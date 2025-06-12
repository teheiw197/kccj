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

# ========== 状态机定义 ==========
class CourseState(Enum):
    PENDING = "待确认"
    CONFIRMED = "已确认"
    CANCELLED = "已取消"

# ========== 主插件注册 ==========
@register(
    "kccj",
    "teheiw197",
    "智能课程提醒插件，支持AI解析与定时提醒",
    "1.2.0",
    "https://github.com/teheiw197/kccj"
)
class KCCJPlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.data_file = os.path.join("data", "plugins", "kccj", "course_data.json")
        self.task_db_file = os.path.join("data", "plugins", "kccj", "task_db.json")
        self.course_data = self.load_json(self.data_file)
        self.task_db = self.load_json(self.task_db_file)
        self.reminder_tasks = {}
        asyncio.create_task(self.reminder_scheduler())

    # ========== 数据存储 ========== 
    def load_json(self, path):
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    def save_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ========== 消息处理分流 ==========
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        # 检测图片/文件，自动分流到豆包
        if self.has_media(event):
            await self.invoke_doubao_assist(event)
            event.stop_event()
            return
        # 文本预处理
        text = self.preprocess_text(event.message_str)
        if not text:
            return
        # AI解析与多轮追问
        course_list = await self.multi_round_parse(text)
        if not course_list:
            await event.send("抱歉，未能成功解析课程表，请检查格式或稍后重试。")
            return
        # 数据校验
        valid_courses = [c for c in course_list if self.validate_course(c)]
        if not valid_courses:
            await event.send("解析结果不完整或有误，请补充关键信息。")
            return
        # 状态机与用户确认
        user_id = event.get_sender_id()
        self.course_data[user_id] = {
            "state": CourseState.PENDING.value,
            "course_data": valid_courses,
            "create_time": datetime.now().isoformat()
        }
        self.save_json(self.data_file, self.course_data)
        await event.send(f"已为您解析出如下课程信息，请确认：\n{json.dumps(valid_courses, ensure_ascii=False, indent=2)}\n回复"确认"保存，回复"取消"放弃。")

    def has_media(self, event: AstrMessageEvent) -> bool:
        # 检查消息链是否包含图片或文件
        return any(getattr(seg, 'type', None) in ("image", "file") for seg in event.message_obj.message)

    def preprocess_text(self, text: str) -> str:
        # 文本预处理：去除多余空格、合并换行
        return re.sub(r"\s+", " ", text.strip())

    # ========== AI解析与多轮追问 ==========
    async def multi_round_parse(self, text: str) -> List[Dict[str, Any]]:
        BASE_PROMPT = "你是课程表解析专家，请从以下文本中提取课程信息，输出JSON数组：\n必须包含字段：课程名称、星期几、上课时间、周次\n可选字段：教师、地点\n时间格式示例：第1-2节（08:00-09:40）\n周次格式示例：1-16周\n\n文本内容：{text}"
        FOLLOW_UP_PROMPT = "上次解析缺少[课程名称/时间/周次]，请重新提取：{text}"
        max_retries = self.config.get("max_ai_retries", 2)
        for round in range(max_retries+1):
            prompt = BASE_PROMPT.format(text=text) if round == 0 else FOLLOW_UP_PROMPT.format(text=text)
            result = await self.call_llm(prompt)
            if self.validate_result(result):
                return result
        return []

    async def call_llm(self, prompt: str) -> List[Dict[str, Any]]:
        provider = self.config.get("ai_provider", "siliconflow")
        if provider == "siliconflow":
            return await self.invoke_siliconflow_llm(prompt)
        elif provider == "doubao":
            return await self.invoke_doubao_llm(prompt)
        elif provider == "openai":
            return await self.invoke_openai_llm(prompt)
        else:
            return []

    async def invoke_siliconflow_llm(self, prompt: str) -> List[Dict[str, Any]]:
        api_key = self.config.get("siliconflow_api_key", "sk-zxtmadhtngzchfjeuoasxfyjbvxnvunyqgyrusdwentlbjxo")
        base_url = "https://api.siliconflow.cn/v1"
        model = "deepseek-ai/DeepSeek-V3"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }
        async with httpx.AsyncClient(base_url=base_url) as client:
            try:
                resp = await client.post("/chat/completions", json=payload, headers=headers, timeout=30)
                data = resp.json()
                # 假设返回格式与OpenAI兼容
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                # 解析为JSON数组
                import json as _json
                try:
                    return _json.loads(content)
                except Exception:
                    return []
            except Exception as e:
                logger.error(f"SiliconFlow API调用失败: {e}")
                return []

    async def invoke_doubao_llm(self, prompt: str) -> List[Dict[str, Any]]:
        # 豆包 LLM API 调用骨架
        api_key = self.config.get("doubao_api_key", "")
        if not api_key:
            return []
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": "doubao-pro", "prompt": prompt, "format": "json"}
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post("https://api.doubao.com/v1/chat/completions", json=payload, headers=headers, timeout=30)
                data = resp.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", [])
            except Exception as e:
                logger.error(f"豆包API调用失败: {e}")
                return []

    async def invoke_openai_llm(self, prompt: str) -> List[Dict[str, Any]]:
        # OpenAI LLM API 调用骨架（需补充openai库调用）
        return []

    async def invoke_doubao_assist(self, event: AstrMessageEvent):
        # 豆包图片/文件协同接口骨架
        await event.send("检测到图片/文件，已自动转交豆包AI解析，请稍候……")
        # 这里可补充图片转URL、调用豆包OCR等逻辑

    def validate_result(self, result) -> bool:
        # 验证AI返回的结果是否为课程列表且包含必填字段
        if not isinstance(result, list):
            return False
        for course in result:
            if not self.validate_course(course):
                return False
        return True

    def validate_course(self, course: dict) -> bool:
        required_fields = {"课程名称", "星期几", "上课时间", "周次"}
        if not required_fields.issubset(course.keys()):
            return False
        if not re.match(r"第\d+-\d+节（\d{2}:\d{2}-\d{2}:\d{2}）", course["上课时间"]):
            return False
        if not re.match(r"\d+-\d+周", course["周次"]):
            return False
        return True

    # ========== 定时提醒引擎骨架 ==========
    async def reminder_scheduler(self):
        while True:
            now = datetime.now()
            for user_id, user_info in self.course_data.items():
                if user_info.get("state") != CourseState.CONFIRMED.value:
                    continue
                for course in user_info.get("course_data", []):
                    # 计算提醒时间
                    remind_time = self.calculate_remind_time(course)
                    if remind_time and now >= remind_time and not self.is_task_sent(user_id, course):
                        await self.send_reminder(user_id, course)
                        self.mark_task_sent(user_id, course)
            await asyncio.sleep(30)

    def calculate_remind_time(self, course: dict):
        # 解析上课时间，返回提醒时间（课前N分钟）
        advance = self.config.get("remind_advance_minutes", 30)
        # 这里只做骨架，需结合具体时间格式实现
        return None

    def is_task_sent(self, user_id, course):
        # 判断任务是否已发送
        return False

    def mark_task_sent(self, user_id, course):
        # 标记任务为已发送
        pass

    async def send_reminder(self, user_id, course):
        # 发送提醒消息
        await self.context.send_message(user_id, [{"type": "plain", "text": f"【课程提醒】即将上课：{course}"}])

    def get_config(self, key, default=None):
        """安全地获取配置值"""
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    @filter.command("help")
    async def help_command(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """课程提醒插件使用说明：

1. 发送课程表
直接发送课程表文本即可，格式需要符合模板要求。

2. 命令列表：
/help - 显示此帮助信息
/test - 发送一条测试提醒
/preview - 预览明天的课程
/status - 查看当前提醒状态
/stop - 停止课程提醒
/start - 开启课程提醒
/clear - 清除课程数据

3. 注意事项：
- 目前仅支持文本格式的课程表
- 如果发送图片或文件，会提示使用豆包生成课程表文本
- 课程提醒会在每节课前30分钟发送
- 每天晚上23:00会发送第二天的课程预览"""
        yield event.plain_result(help_text)

    @filter.command("test")
    async def test_command(self, event: AstrMessageEvent):
        """发送测试提醒"""
        user_id = event.get_sender_id()
        if user_id not in self.course_data:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        test_msg = """上课时间：第1-2节（08:00-09:40）
课程名称：如何找到富婆
教师：飘逸
上课地点150123"""
        yield event.plain_result("这是一条测试提醒消息：\n\n" + test_msg)

    @filter.command("preview")
    async def preview_command(self, event: AstrMessageEvent):
        """预览明天的课程"""
        user_id = event.get_sender_id()
        if user_id not in self.course_data:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        preview_msg = self.format_daily_preview(self.course_data[user_id])
        if preview_msg:
            yield event.plain_result(preview_msg)
        else:
            yield event.plain_result("明天没有课程安排。")

    @filter.command("status")
    async def status_command(self, event: AstrMessageEvent):
        """查看当前提醒状态"""
        user_id = event.get_sender_id()
        if user_id not in self.course_data:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        status = "提醒状态：\n"
        status += f"• 课程提醒：{'开启' if self.get_config('notification_settings.enable_reminder', True) else '关闭'}\n"
        status += f"• 周末提醒：{'开启' if self.get_config('notification_settings.enable_weekend_reminder', True) else '关闭'}\n"
        status += f"• 晚间课程提醒：{'开启' if self.get_config('notification_settings.enable_evening_reminder', True) else '关闭'}\n"
        status += f"• 每日预览：{'开启' if self.get_config('reminder_settings.enable_daily_preview', True) else '关闭'}\n"
        status += f"• 提醒时间：课前{self.get_config('reminder_settings.reminder_time', 30)}分钟\n"
        status += f"• 预览时间：{self.get_config('reminder_settings.daily_preview_time', '23:00')}"
        
        yield event.plain_result(status)

    @filter.command("stop")
    async def stop_command(self, event: AstrMessageEvent):
        """停止课程提醒"""
        user_id = event.get_sender_id()
        if user_id not in self.course_data:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        if user_id in self.reminder_tasks:
            self.reminder_tasks[user_id].cancel()
            del self.reminder_tasks[user_id]
            yield event.plain_result("已停止课程提醒。")
        else:
            yield event.plain_result("课程提醒已经停止。")

    @filter.command("start")
    async def start_command(self, event: AstrMessageEvent):
        """开启课程提醒"""
        user_id = event.get_sender_id()
        if user_id not in self.course_data:
            yield event.plain_result("您还没有设置课程表，请先发送课程表。")
            return

        if user_id in self.reminder_tasks:
            yield event.plain_result("课程提醒已经在运行中。")
            return

        await self.start_reminder_task(event.unified_msg_origin, self.course_data[user_id])
        yield event.plain_result("已开启课程提醒。")

    @filter.command("clear")
    async def clear_command(self, event: AstrMessageEvent):
        """清除课程数据"""
        user_id = event.get_sender_id()
        if user_id in self.course_data:
            del self.course_data[user_id]
            if user_id in self.reminder_tasks:
                self.reminder_tasks[user_id].cancel()
                del self.reminder_tasks[user_id]
            self.save_json(self.data_file, self.course_data)
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
                for user_id, course_info in self.course_data.items():
                    preview_msg = self.format_daily_preview(course_info)
                    if preview_msg:
                        # 发送预览消息
                        await self.context.send_message(user_id, [{"type": "plain", "text": preview_msg}])
                        # 询问是否开启明日提醒
                        await self.context.send_message(user_id, [{"type": "plain", "text": "是否开启明日课程提醒？回复'是'开启提醒。"}])
            await asyncio.sleep(60)  # 每分钟检查一次

    def format_daily_preview(self, course_info):
        """格式化每日预览信息"""
        tomorrow = datetime.now() + timedelta(days=1)
        weekday_map = {0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"}
        tomorrow_weekday = weekday_map[tomorrow.weekday()]
        
        if tomorrow_weekday in course_info["weekly_courses"]:
            courses = course_info["weekly_courses"][tomorrow_weekday]
            courses_text = []
            for course in courses:
                courses_text.append(
                    self.get_config(
                        'message_templates.course_template',
                        "上课时间：{time}\n课程名称：{name}\n教师：{teacher}\n上课地点：{location}"
                    ).format(
                        time=course["time"],
                        name=course["name"],
                        teacher=course["teacher"],
                        location=course["location"]
                    )
                )
            
            return self.get_config(
                'message_templates.preview_template',
                "【明日课程预览】\n星期{weekday}的课程安排：\n\n{courses}"
            ).format(
                weekday=tomorrow_weekday,
                courses="\n".join(courses_text)
            )
        return ""

    async def terminate(self):
        """插件终止时清理资源"""
        for task in self.reminder_tasks.values():
            task.cancel()
        self.save_json(self.data_file, self.course_data) 