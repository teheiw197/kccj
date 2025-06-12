from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
import json
import os
import asyncio
from datetime import datetime, timedelta
import re
from dateutil import parser
from enum import Enum
from typing import List, Dict, Any
import httpx
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11 import Event
from nonebot.adapters.onebot.v11 import Bot
from nonebot.typing import T_State
from nonebot import on_message, on_command
from nonebot.rule import to_me
from nonebot.permission import SUPERUSER
from .ai_service import SiliconFlowService
from .parser import parse_word, parse_xlsx, parse_image, parse_text_schedule
import aiohttp

# ========== 数据结构 ==========
class Course:
    def __init__(self, day, time, name, teacher, location, weeks):
        self.day = day
        self.time = time
        self.name = name
        self.teacher = teacher
        self.location = location
        self.weeks = weeks
    def to_dict(self):
        return self.__dict__

class UserState(Enum):
    WAIT_SCHEDULE = 1
    PARSING = 2
    WAIT_CONFIRM = 3
    ACTIVE = 4

# ========== 主插件注册 ==========
@register(
    "kccj",
    "teheiw197",
    "智能课程提醒插件，内置SiliconFlow大模型API",
    "1.3.0",
    "https://github.com/teheiw197/kccj"
)
class KCCJPlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.data_dir = os.path.join("data", "course_reminder")
        os.makedirs(self.data_dir, exist_ok=True)
        self.user_state = {}  # user_id: UserState
        self.reminder_tasks = {}
        asyncio.create_task(self.reminder_service())

    # ========== 消息类型处理 ==========
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent, *args, **kwargs):
        user_id = event.get_sender_id()
        if self.has_media(event):
            await self.send_msg(event, self.get_media_tip())
            event.stop_event()
            return
        # 进入课程表解析流程
        await self.parse_course_schedule(event)

    def has_media(self, event: AstrMessageEvent) -> bool:
        return any(getattr(seg, 'type', None) in ("image", "file") for seg in event.message_obj.message)

    def get_media_tip(self):
        template = (
            "【姓名同学学年学期课程安排】\n\n"
            "📚 基本信息\n\n"
            "• 学校：XX大学（没有则不显示）\n"
            "• 班级：XX班（没有则不显示）\n"
            "• 专业：XX专业（没有则不显示）\n"
            "• 学院：XX学院（没有则不显示）\n\n"
            "🗓️ 每周课程详情\n星期X\n\n"
            "• 上课时间（节次和时间）：\n课程名称\n教师：老师姓名\n上课地点：教室/场地\n周次：具体周次\n\n"
            "示例：\n星期一\n上课时间：第1-2节（08:00-09:40）\n课程名称：如何找到富婆\n教师：飘逸\n上课地点150123\n周次：1-16周\n\n"
            "周末：无课程。\n\n"
            "🌙 晚间课程\n\n• 上课时间（节次和时间）：\n课程名称\n教师：老师姓名\n上课地点：教室/场地\n周次：具体周次\n\n"
            "📌 重要备注\n\n• 备注内容1\n• 备注内容2\n\n请留意课程周次及教室安排，合理规划学习时间！"
        )
        return (
            "抱歉，我无法识别图片和文件。请复制下方【课程消息模板】发送给豆包：\n" + template
        )

    # ========== 课程表解析 ==========
    async def parse_course_schedule(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        text = event.message_str
        courses = self._parse_schedule(text)
        if not courses:
            await self.send_msg(event, "课程表解析失败，请检查格式。")
            self.user_state[user_id] = UserState.WAIT_SCHEDULE
            return
        # 格式化并返回用户确认
        confirm_text = self.format_courses_for_confirm(courses)
        await self.send_msg(event, confirm_text)
        self.save_user_data(user_id, courses)
        self.user_state[user_id] = UserState.WAIT_CONFIRM

    def _parse_schedule(self, text: str) -> List[Course]:
        # 简单正则解析，支持自定义扩展
        course_pattern = re.compile(
            r"星期([一二三四五六日])[^\n]*\n上课时间：([^\n]+)\n课程名称：([^\n]+)\n教师：([^\n]+)\n上课地点：([^\n]+)\n周次：([^\n]+)",
            re.MULTILINE
        )
        courses = []
        for match in course_pattern.finditer(text):
            day, time, name, teacher, location, weeks = match.groups()
            courses.append(Course(day, time, name, teacher, location, weeks))
        return courses

    def format_courses_for_confirm(self, courses: List[Course]) -> str:
        lines = ["已为您解析出如下课程信息，请确认："]
        for c in courses:
            lines.append(f"星期{c.day} {c.time} {c.name} {c.teacher} {c.location} {c.weeks}")
        lines.append("\n回复'确认'保存，回复'取消'放弃。")
        return "\n".join(lines)

    # ========== 提醒服务管理 ==========
    async def reminder_service(self):
        while True:
            now = datetime.now()
            for user_id, courses in self.load_all_user_data().items():
                # 只对已激活用户提醒
                if self.user_state.get(user_id) != UserState.ACTIVE:
                    continue
                for c in courses:
                    remind_time = self.calculate_remind_time(c)
                    if remind_time and now >= remind_time and not self.is_task_sent(user_id, c):
                        await self.send_reminder(user_id, c)
                        self.mark_task_sent(user_id, c)
            # 每天23:00发送次日课程汇总
            if now.hour == 23 and now.minute == 0:
                for user_id, courses in self.load_all_user_data().items():
                    if self.user_state.get(user_id) == UserState.ACTIVE:
                        preview_msg = self.format_daily_preview(courses)
                        if preview_msg:
                            await self.context.send_message(
                                user_id,
                                [{"type": "plain", "text": preview_msg}]
                            )
                            await self.context.send_message(
                                user_id,
                                [{"type": "plain", "text": "是否开启明日课程提醒？回复'是'开启提醒。"}]
                            )
            await asyncio.sleep(60)

    def calculate_remind_time(self, course: Course):
        advance = self.config.get("remind_advance_minutes", 30)
        # 这里只做骨架，需结合具体时间格式实现
        return None

    def is_task_sent(self, user_id, course):
        return False

    def mark_task_sent(self, user_id, course):
        pass

    async def send_reminder(self, user_id, course: Course):
        try:
            msg = (
                "同学你好，待会有课哦\n"
                f"上课时间（节次和时间）：{course.time}\n"
                f"课程名称：{course.name}\n"
                f"教师：{course.teacher}\n"
                f"上课地点：{course.location}"
            )
            await self.context.send_message(
                user_id,
                [{"type": "plain", "text": msg}]
            )
        except Exception as e:
            logger.error(f"send_reminder error: {e}")

    def format_daily_preview(self, courses: List[Course]) -> str:
        # 只预览明天的课程
        tomorrow = datetime.now() + timedelta(days=1)
        weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
        tomorrow_weekday = weekday_map[tomorrow.weekday()]
        lines = [f"明天（星期{tomorrow_weekday}）课程预览："]
        found = False
        for c in courses:
            if c.day == tomorrow_weekday:
                found = True
                lines.append(f"{c.time} {c.name} {c.teacher} {c.location} {c.weeks}")
        return "\n".join(lines) if found else ""

    # ========== 持久化存储 ==========
    def save_user_data(self, user_id, courses: List[Course]):
        path = os.path.join(self.data_dir, f"user_{user_id}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([c.to_dict() for c in courses], f, ensure_ascii=False, indent=2)

    def load_user_data(self, user_id) -> List[Course]:
        path = os.path.join(self.data_dir, f"user_{user_id}.json")
        if not os.path.exists(path):
            return []
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [Course(**c) for c in data]

    def load_all_user_data(self) -> Dict[str, List[Course]]:
        result = {}
        for fname in os.listdir(self.data_dir):
            if fname.startswith("user_") and fname.endswith(".json"):
                user_id = fname[5:-5]
                result[user_id] = self.load_user_data(user_id)
        return result

    # ========== 指令 ==========
    @filter.command("reminder")
    async def reminder_command(self, event: AstrMessageEvent, subcmd: str = None, *args, **kwargs):
        user_id = event.get_sender_id()
        if subcmd == "start":
            self.user_state[user_id] = UserState.WAIT_SCHEDULE
            await self.send_msg(event, "请发送你的课程表文本。")
        elif subcmd == "test":
            test_msg = (
                "【课程提醒测试】\n"
                "上课时间：第1-2节（08:00-09:40）\n"
                "课程名称：如何找到富婆\n"
                "教师：飘逸\n"
                "上课地点150123"
            )
            yield event.plain_result(test_msg)
        elif subcmd == "stop":
            self.user_state[user_id] = UserState.WAIT_SCHEDULE
            await self.send_msg(event, "已暂停所有提醒服务，课程表数据保留。")
        elif subcmd == "update":
            self.user_state[user_id] = UserState.WAIT_SCHEDULE
            await self.send_msg(event, "请重新发送你的课程表文本。")
        else:
            await self.send_msg(event, "用法：/reminder start|test|stop|update")

    async def send_msg(self, event: AstrMessageEvent, text: str):
        try:
            await self.context.send_message(
                event.unified_msg_origin,
                [{"type": "plain", "text": text}]
            )
        except Exception as e:
            logger.error(f"send_msg error: {e}")

    def get_config(self, key, default=None):
        """安全地获取配置值"""
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    @filter.command("help")
    async def help_command(self, event: AstrMessageEvent):
        '''显示插件帮助信息'''
        try:
            help_text = (
                "kccj 智能课程提醒插件\n"
                "- 发送课程表文本，自动AI解析\n"
                "- 支持课前提醒、每日预览\n"
                "- 支持群聊/私聊自动适配\n"
                "- 指令：/help 查看帮助\n"
            )
            yield event.plain_result(help_text)
        except Exception as e:
            logger.error(f"help_command error: {e}")
            yield event.plain_result("帮助信息获取失败，请联系管理员。")

    @filter.command("test")
    async def test_command(self, event: AstrMessageEvent):
        """发送测试提醒"""
        user_id = event.get_sender_id()
        if user_id not in self.user_state:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        test_msg = """上课时间：第1-2节（08:00-09:40）
课程名称：如何找到富婆
教师：飘逸
上课地点150123"""
        yield event.plain_result("这是一条测试提醒消息：\n\n" + test_msg)

    @filter.command("preview")
    async def preview_command(self, event: AstrMessageEvent, *args, **kwargs):
        """预览明天的课程"""
        user_id = event.get_sender_id()
        if user_id not in self.user_state:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        preview_msg = self.format_daily_preview(self.load_user_data(user_id))
        if preview_msg:
            yield event.plain_result(preview_msg)
        else:
            yield event.plain_result("明天没有课程安排。")

    @filter.command("status")
    async def status_command(self, event: AstrMessageEvent, *args, **kwargs):
        """查看当前提醒状态"""
        user_id = event.get_sender_id()
        if user_id not in self.user_state:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        state = self.user_state[user_id]
        yield event.plain_result(f"当前提醒状态：{state}")

    @filter.command("stop")
    async def stop_command(self, event: AstrMessageEvent, *args, **kwargs):
        """停止课程提醒"""
        user_id = event.get_sender_id()
        if user_id not in self.user_state:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        if user_id in self.reminder_tasks:
            self.reminder_tasks[user_id].cancel()
            del self.reminder_tasks[user_id]
            yield event.plain_result("已停止课程提醒。")
        else:
            yield event.plain_result("课程提醒已经停止。")

    @filter.command("start")
    async def start_command(self, event: AstrMessageEvent, *args, **kwargs):
        """开启课程提醒"""
        user_id = event.get_sender_id()
        if user_id not in self.user_state:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return
        if self.user_state[user_id] == UserState.ACTIVE:
            yield event.plain_result("课程提醒已开启。"); return
        self.user_state[user_id] = UserState.ACTIVE
        self.save_user_data(user_id, self.load_user_data(user_id))
        yield event.plain_result("已开启课程提醒。")

    @filter.command("clear")
    async def clear_command(self, event: AstrMessageEvent, *args, **kwargs):
        """清除课程数据"""
        user_id = event.get_sender_id()
        if user_id in self.user_state:
            del self.user_state[user_id]
            if user_id in self.reminder_tasks:
                self.reminder_tasks[user_id].cancel()
                del self.reminder_tasks[user_id]
            self.save_user_data(user_id, [])
            yield event.plain_result("已清除课程数据。")
        else:
            yield event.plain_result("您还没有设置课程表。")

    async def start_reminder_task(self, unified_msg_origin, course_info):
        """启动提醒任务"""
        if unified_msg_origin in self.reminder_tasks:
            self.reminder_tasks[unified_msg_origin].cancel()
        
        async def reminder_task():
            while True:
                now = datetime.now()
                # 检查是否需要发送提醒
                for day, courses in course_info["weekly_courses"].items():
                    # 检查是否周末
                    if day in ["六", "日"] and not self.get_config('notification_settings.enable_weekend_reminder', True):
                        continue
                        
                    for course in courses:
                        # 解析上课时间
                        time_match = re.match(r"第(\d+)-(\d+)节（(\d+):(\d+)-(\d+):(\d+)）", course["time"])
                        if time_match:
                            start_hour = int(time_match.group(3))
                            start_minute = int(time_match.group(4))
                            
                            # 计算提醒时间
                            reminder_time = now.replace(hour=start_hour, minute=start_minute) - timedelta(minutes=self.get_config('reminder_settings.reminder_time', 30))
                            
                            if now.hour == reminder_time.hour and now.minute == reminder_time.minute:
                                # 发送提醒
                                reminder_msg = self.get_config(
                                    'message_templates.reminder_template',
                                    "【课程提醒】\n上课时间：{time}\n课程名称：{name}\n教师：{teacher}\n上课地点：{location}"
                                ).format(
                                    time=course["time"],
                                    name=course["name"],
                                    teacher=course["teacher"],
                                    location=course["location"]
                                )
                                await self.context.send_message(unified_msg_origin, [{"type": "plain", "text": reminder_msg}])
                
                # 检查晚间课程
                if self.get_config('notification_settings.enable_evening_reminder', True):
                    for course in course_info["evening_courses"]:
                        time_match = re.match(r"第(\d+)-(\d+)节（(\d+):(\d+)-(\d+):(\d+)）", course["time"])
                        if time_match:
                            start_hour = int(time_match.group(3))
                            start_minute = int(time_match.group(4))
                            
                            reminder_time = now.replace(hour=start_hour, minute=start_minute) - timedelta(minutes=self.get_config('reminder_settings.reminder_time', 30))
                            
                            if now.hour == reminder_time.hour and now.minute == reminder_time.minute:
                                reminder_msg = self.get_config(
                                    'message_templates.reminder_template',
                                    "【课程提醒】\n上课时间：{time}\n课程名称：{name}\n教师：{teacher}\n上课地点：{location}"
                                ).format(
                                    time=course["time"],
                                    name=course["name"],
                                    teacher=course["teacher"],
                                    location=course["location"]
                                )
                                await self.context.send_message(unified_msg_origin, [{"type": "plain", "text": reminder_msg}])
                
                await asyncio.sleep(60)  # 每分钟检查一次
        
        self.reminder_tasks[unified_msg_origin] = asyncio.create_task(reminder_task())

    async def daily_preview_task(self):
        """每日预览任务"""
        while True:
            now = datetime.now()
            preview_time = datetime.strptime(self.get_config('reminder_settings.daily_preview_time', '23:00'), "%H:%M").time()
            
            if (now.hour == preview_time.hour and 
                now.minute == preview_time.minute and 
                self.get_config('reminder_settings.enable_daily_preview', True)):
                # 发送每日预览
                for user_id, course_info in self.user_state.items():
                    if course_info == UserState.ACTIVE:
                        preview_msg = self.format_daily_preview(self.load_user_data(user_id))
                        if preview_msg:
                            # 发送预览消息
                            await self.context.send_message(user_id, [{"type": "plain", "text": preview_msg}])
                            # 询问是否开启明日提醒
                            await self.context.send_message(user_id, [{"type": "plain", "text": "是否开启明日课程提醒？回复'是'开启提醒。"}])
            await asyncio.sleep(60)  # 每分钟检查一次

    async def terminate(self):
        '''插件被卸载/停用时调用，安全取消所有异步任务并保存数据'''
        try:
            for task in getattr(self, 'reminder_tasks', {}).values():
                if task and not task.done():
                    task.cancel()
            for user_id, state in self.user_state.items():
                if state == UserState.ACTIVE:
                    self.save_user_data(user_id, self.load_user_data(user_id))
            logger.info("kccj插件已安全终止并保存数据。")
        except Exception as e:
            logger.error(f"terminate error: {e}")

    @filter.command("testremind")
    async def test_remind_command(self, event: AstrMessageEvent, *args, **kwargs):
        '''课程提醒测试指令'''
        try:
            test_msg = (
                "【课程提醒测试】\n"
                "上课时间：第1-2节（08:00-09:40）\n"
                "课程名称：如何找到富婆\n"
                "教师：飘逸\n"
                "上课地点150123"
            )
            yield event.plain_result(test_msg)
        except Exception as e:
            logger.error(f"test_remind_command error: {e}")
            yield event.plain_result("课程提醒测试失败，请联系管理员。")

# 初始化插件
nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

# 配置
CONFIG = {
    "api_key": "sk-zxtmadhtngzchfjeuoasxfyjbvxnvunyqgyrusdwentlbjxo",
    "api_base": "https://api.siliconflow.cn/v1",
    "model": "deepseek-ai/DeepSeek-V3",
    "remind_advance_minutes": 30,
    "daily_summary_hour": 23,
    "daily_summary_minute": 0
}

# 初始化 AI 服务
ai_service = SiliconFlowService(
    api_key=CONFIG["api_key"],
    api_base=CONFIG["api_base"],
    model=CONFIG["model"]
)

# 数据存储
DATA_DIR = "data/plugins/kccj/data"
os.makedirs(DATA_DIR, exist_ok=True)

def get_user_data_path(user_id: str) -> str:
    return os.path.join(DATA_DIR, f"{user_id}.json")

def load_user_data(user_id: str) -> Dict:
    file_path = get_user_data_path(user_id)
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载用户数据失败: {str(e)}")
    return {"courses": [], "reminder_enabled": False}

def save_user_data(user_id: str, data: Dict):
    file_path = get_user_data_path(user_id)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存用户数据失败: {str(e)}")

# 消息处理器
@on_message()
async def handle_message(bot: Bot, event: Event, state: T_State):
    user_id = str(event.get_user_id())
    
    # 检查是否为图片/文件消息
    if any(seg.type in ["image", "file"] for seg in event.get_message()):
        # 获取文件信息
        file_seg = next(seg for seg in event.get_message() if seg.type in ["image", "file"])
        file_url = file_seg.data.get("url", "")
        file_name = file_seg.data.get("name", "")
        
        if not file_url:
            await bot.send(event, Message([MessageSegment.text("无法获取文件，请重试。")]))
            return
            
        # 下载文件
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    if resp.status != 200:
                        await bot.send(event, Message([MessageSegment.text("文件下载失败，请重试。")]))
                        return
                    file_data = await resp.read()
        except Exception as e:
            logger.error(f"下载文件失败: {str(e)}")
            await bot.send(event, Message([MessageSegment.text("文件下载失败，请重试。")]))
            return
            
        # 保存文件
        ext = os.path.splitext(file_name)[-1].lower()
        save_path = os.path.join(DATA_DIR, f"{user_id}{ext}")
        try:
            with open(save_path, "wb") as f:
                f.write(file_data)
        except Exception as e:
            logger.error(f"保存文件失败: {str(e)}")
            await bot.send(event, Message([MessageSegment.text("文件保存失败，请重试。")]))
            return
            
        # 根据文件类型解析
        try:
            if ext in [".docx", ".doc"]:
                courses = parse_word(save_path)
            elif ext in [".xlsx", ".xls"]:
                courses = parse_xlsx(save_path)
            elif ext in [".jpg", ".jpeg", ".png", ".bmp"]:
                courses = await parse_image(save_path, CONFIG.get("ocr_api_url", ""), CONFIG.get("ocr_api_key", ""))
            else:
                await bot.send(event, Message([MessageSegment.text("暂不支持该文件类型，请发送Word、Excel或图片格式的课程表。")]))
                return
                
            if not courses:
                await bot.send(event, Message([MessageSegment.text("未能从文件中识别出课程信息，请检查文件格式是否正确。")]))
                return
                
            # 保存课程数据
            user_data = load_user_data(user_id)
            user_data["courses"] = courses
            save_user_data(user_id, user_data)
            
            # 生成确认消息
            confirm_msg = "已解析到以下课程：\n\n"
            for course in courses:
                confirm_msg += f"{course['weekday']} {course['time']} {course['course']}\n"
                confirm_msg += f"教室：{course['classroom']} 教师：{course['teacher']}\n\n"
            confirm_msg += "是否开启课程提醒？回复'是'开启提醒。"
            
            await bot.send(event, Message([MessageSegment.text(confirm_msg)]))
            
        except Exception as e:
            logger.error(f"解析文件失败: {str(e)}")
            await bot.send(event, Message([MessageSegment.text("解析文件失败，请检查文件格式是否正确。")]))
        finally:
            # 清理临时文件
            try:
                os.remove(save_path)
            except:
                pass
        return

    # 处理文本消息
    text = event.get_plaintext().strip()
    if not text:
        return

    # 尝试解析课程表
    courses = parse_text_schedule(text)
    if not courses:
        await bot.send(event, Message([
            MessageSegment.text("抱歉，我无法解析课程表。\n"),
            MessageSegment.text("请确保课程表格式正确，包含：星期、时间、课程名称、教室、教师等信息。")
        ]))
        return

    # 保存课程数据
    user_data = load_user_data(user_id)
    user_data["courses"] = courses
    save_user_data(user_id, user_data)

    # 生成确认消息
    confirm_msg = "已解析到以下课程：\n\n"
    for course in courses:
        confirm_msg += f"{course['weekday']} {course['time']} {course['course']}\n"
        confirm_msg += f"教室：{course['classroom']} 教师：{course['teacher']}\n\n"
    confirm_msg += "是否开启课程提醒？回复'是'开启提醒。"

    await bot.send(event, Message([MessageSegment.text(confirm_msg)]))

# 确认开启提醒
@on_message()
async def handle_confirmation(bot: Bot, event: Event, state: T_State):
    user_id = str(event.get_user_id())
    text = event.get_plaintext().strip()
    
    if text != "是":
        return

    user_data = load_user_data(user_id)
    if not user_data["courses"]:
        await bot.send(event, Message([MessageSegment.text("请先发送课程表。")]))
        return

    user_data["reminder_enabled"] = True
    save_user_data(user_id, user_data)

    # 启动提醒服务
    asyncio.create_task(start_reminder_service(bot, user_id))

    await bot.send(event, Message([MessageSegment.text("已开启课程提醒服务！")]))

# 提醒服务
async def start_reminder_service(bot: Bot, user_id: str):
    while True:
        try:
            user_data = load_user_data(user_id)
            if not user_data["reminder_enabled"]:
                break

            now = datetime.now()
            
            # 检查是否需要发送每日汇总
            if now.hour == CONFIG["daily_summary_hour"] and now.minute == CONFIG["daily_summary_minute"]:
                await send_daily_summary(bot, user_id, user_data["courses"])
            
            # 检查是否需要发送课程提醒
            for course in user_data["courses"]:
                if should_send_reminder(course):
                    reminder_msg = await ai_service.generate_reminder_message(course)
                    await bot.send_private_msg(user_id=user_id, message=Message([MessageSegment.text(reminder_msg)]))
                    # 添加延时，避免重复提醒
                    await asyncio.sleep(60)

            await asyncio.sleep(60)  # 每分钟检查一次
        except Exception as e:
            logger.error(f"提醒服务发生错误: {str(e)}")
            await asyncio.sleep(60)

def should_send_reminder(course: Dict[str, Any]) -> bool:
    """
    判断是否需要发送课程提醒
    """
    try:
        now = datetime.now()
        
        # 解析星期几
        weekday_map = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6}
        course_weekday = weekday_map.get(course["weekday"])
        if course_weekday is None:
            return False
            
        # 如果今天不是课程日，不发送提醒
        if now.weekday() != course_weekday:
            return False
            
        # 解析上课时间
        time_str = course["time"]
        if "节" in time_str:
            # 处理"1-2节"这样的格式
            start_section = int(time_str.split("-")[0])
        else:
            # 处理"8:00-9:40"这样的格式
            start_time = datetime.strptime(time_str.split("-")[0], "%H:%M").time()
            current_time = now.time()
            time_diff = datetime.combine(now.date(), start_time) - datetime.combine(now.date(), current_time)
            return 0 <= time_diff.total_seconds() <= CONFIG["remind_advance_minutes"] * 60
            
        # 根据节次判断时间
        section_times = {
            1: "8:00", 2: "8:55", 3: "10:00", 4: "10:55",
            5: "14:00", 6: "14:55", 7: "16:00", 8: "16:55",
            9: "19:00", 10: "19:55", 11: "20:50"
        }
        
        if start_section not in section_times:
            return False
            
        start_time = datetime.strptime(section_times[start_section], "%H:%M").time()
        current_time = now.time()
        time_diff = datetime.combine(now.date(), start_time) - datetime.combine(now.date(), current_time)
        
        # 在提前提醒时间内发送提醒
        return 0 <= time_diff.total_seconds() <= CONFIG["remind_advance_minutes"] * 60
        
    except Exception as e:
        logger.error(f"判断提醒时间时发生错误: {str(e)}")
        return False

async def send_daily_summary(bot: Bot, user_id: str, courses: List[Dict[str, Any]]):
    """
    发送每日课程汇总
    """
    try:
        now = datetime.now()
        tomorrow = now + timedelta(days=1)
        tomorrow_weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][tomorrow.weekday()]
        
        # 筛选明天的课程
        tomorrow_courses = [c for c in courses if c["weekday"] == tomorrow_weekday]
        
        if not tomorrow_courses:
            summary_msg = "明天没有课程安排，可以好好休息啦！😊"
        else:
            summary_msg = "📚 明日课程安排：\n\n"
            for course in tomorrow_courses:
                summary_msg += f"⏰ {course['time']} {course['course']}\n"
                summary_msg += f"📍 教室：{course['classroom']}\n"
                summary_msg += f"👨‍🏫 教师：{course['teacher']}\n\n"
            
            summary_msg += "是否需要开启明日课程提醒？回复'是'开启提醒。"
        
        await bot.send_private_msg(user_id=user_id, message=Message([MessageSegment.text(summary_msg)]))
        
    except Exception as e:
        logger.error(f"发送每日汇总时发生错误: {str(e)}")

# 测试提醒指令
@on_command("test_reminder", permission=SUPERUSER)
async def test_reminder(bot: Bot, event: Event, state: T_State):
    user_id = str(event.get_user_id())
    user_data = load_user_data(user_id)
    
    if not user_data["courses"]:
        await bot.send(event, Message([MessageSegment.text("请先发送课程表。")]))
        return

    # 发送测试提醒
    for course in user_data["courses"]:
        reminder_msg = await ai_service.generate_reminder_message(course)
        await bot.send(event, Message([MessageSegment.text(reminder_msg)]))
        await asyncio.sleep(1)

# 停止提醒指令
@on_command("stop_reminder", permission=SUPERUSER)
async def stop_reminder(bot: Bot, event: Event, state: T_State):
    user_id = str(event.get_user_id())
    user_data = load_user_data(user_id)
    user_data["reminder_enabled"] = False
    save_user_data(user_id, user_data)
    await bot.send(event, Message([MessageSegment.text("已停止课程提醒服务。")]))

# 更新课程表指令
@on_command("update_schedule", permission=SUPERUSER)
async def update_schedule(bot: Bot, event: Event, state: T_State):
    await bot.send(event, Message([MessageSegment.text("请发送新的课程表。")]))

# 添加新的命令处理器
@on_command("schedule", aliases={"课表"})
async def show_schedule(bot: Bot, event: Event, state: T_State):
    """显示完整课程表"""
    user_id = str(event.get_user_id())
    user_data = load_user_data(user_id)
    
    if not user_data["courses"]:
        await bot.send(event, Message([MessageSegment.text("你还没有上传课程表，请发送课程表。")]))
        return
        
    msg = "📚 你的课程表：\n\n"
    for course in user_data["courses"]:
        msg += f"📅 {course['weekday']} {course['time']}\n"
        msg += f"📖 {course['course']}\n"
        msg += f"📍 {course['classroom']}\n"
        msg += f"👨‍🏫 {course['teacher']}\n\n"
    
    await bot.send(event, Message([MessageSegment.text(msg)]))

@on_command("today", aliases={"今日课程"})
async def show_today(bot: Bot, event: Event, state: T_State):
    """显示今日课程"""
    user_id = str(event.get_user_id())
    user_data = load_user_data(user_id)
    
    if not user_data["courses"]:
        await bot.send(event, Message([MessageSegment.text("你还没有上传课程表，请发送课程表。")]))
        return
        
    now = datetime.now()
    today = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    
    today_courses = [c for c in user_data["courses"] if c["weekday"] == today]
    
    if not today_courses:
        msg = f"今天({today})没有课程安排，可以好好休息啦！😊"
    else:
        msg = f"📚 今日({today})课程安排：\n\n"
        for course in today_courses:
            msg += f"⏰ {course['time']} {course['course']}\n"
            msg += f"📍 {course['classroom']}\n"
            msg += f"👨‍🏫 {course['teacher']}\n\n"
    
    await bot.send(event, Message([MessageSegment.text(msg)])) 