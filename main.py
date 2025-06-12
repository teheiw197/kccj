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

@register("kccj", "teheiw197", "课程提醒插件", "1.0.0")
class KCCJPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.course_data = {}
        self.reminder_tasks = {}
        self.data_file = os.path.join("data", "plugins", "kccj", "course_data.json")
        self.load_data()
        asyncio.create_task(self.daily_preview_task())

    def load_data(self):
        """加载课程数据"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.course_data = json.load(f)
        except Exception as e:
            logger.error(f"加载课程数据失败: {str(e)}")
            self.course_data = {}

    def save_data(self):
        """保存课程数据"""
        try:
            os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.course_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存课程数据失败: {str(e)}")

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
            self.save_data()
            yield event.plain_result("已清除课程数据。")
        else:
            yield event.plain_result("您还没有设置课程表。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        """处理所有消息"""
        # 检查消息是否包含图片或文件
        has_image = any(comp.type == "image" for comp in event.message_obj.message)
        has_file = any(comp.type == "file" for comp in event.message_obj.message)
        
        if has_image or has_file:
            template = """【课程消息模板】

📚 基本信息

• 学校：XX大学（没有则不显示）

• 班级：XX班（没有则不显示）

• 专业：XX专业（没有则不显示）

• 学院：XX学院（没有则不显示）

🗓️ 每周课程详情
星期X

• 上课时间（节次和时间）：
课程名称
教师：老师姓名
上课地点：教室/场地
周次：具体周次

示例：
星期一
上课时间：第1-2节（08:00-09:40）
课程名称：如何找到富婆
教师：飘逸
上课地点150123
周次：1-16周

周末：无课程。

🌙 晚间课程

• 上课时间（节次和时间）：
课程名称
教师：老师姓名
上课地点：教室/场地
周次：具体周次

📌 重要备注

• 备注内容1

• 备注内容2

请留意课程周次及教室安排，合理规划学习时间！"""
            yield event.plain_result("抱歉,我无法识别图片和文件。因为作者穷,请您复制下方【课程消息模板】去豆包,将课程表图片或者文件和课程消息模板发送给豆包,让它生成后,再来发送给我。\n\n" + template)
            return

        # 处理文本消息
        message = event.message_str.strip()
        if not message:
            return

        # 解析课程信息
        try:
            course_info = self.parse_course_info(message)
            if course_info:
                user_id = event.get_sender_id()
                self.course_data[user_id] = course_info
                self.save_data()
                
                # 发送确认消息
                confirm_msg = "已解析您的课程信息,请确认是否正确:\n\n" + self.format_course_info(course_info)
                yield event.plain_result(confirm_msg)
                
                # 启动提醒任务
                if self.get_config('notification_settings.enable_reminder', True):
                    await self.start_reminder_task(event.unified_msg_origin, course_info)
        except Exception as e:
            logger.error(f"处理课程信息失败: {str(e)}")
            yield event.plain_result("抱歉,解析课程信息失败,请检查格式是否正确。")

    def parse_course_info(self, text):
        """解析课程信息"""
        course_info = {
            "basic_info": {},
            "weekly_courses": {},
            "evening_courses": [],
            "remarks": []
        }
        
        # 解析基本信息
        basic_info_pattern = r"•\s*([^：]+)：([^\n]+)"
        basic_info_matches = re.finditer(basic_info_pattern, text)
        for match in basic_info_matches:
            key = match.group(1).strip()
            value = match.group(2).strip()
            if value != "（没有则不显示）":
                course_info["basic_info"][key] = value

        # 解析每周课程
        weekly_pattern = r"星期([一二三四五六日])\n(.*?)(?=星期|$)"
        weekly_matches = re.finditer(weekly_pattern, text, re.DOTALL)
        for match in weekly_matches:
            day = match.group(1)
            courses_text = match.group(2)
            
            # 解析具体课程
            course_pattern = r"上课时间：([^\n]+)\n课程名称：([^\n]+)\n教师：([^\n]+)\n上课地点：([^\n]+)\n周次：([^\n]+)"
            course_matches = re.finditer(course_pattern, courses_text)
            
            day_courses = []
            for course_match in course_matches:
                course = {
                    "time": course_match.group(1),
                    "name": course_match.group(2),
                    "teacher": course_match.group(3),
                    "location": course_match.group(4),
                    "weeks": course_match.group(5)
                }
                day_courses.append(course)
            
            if day_courses:
                course_info["weekly_courses"][day] = day_courses

        # 解析晚间课程
        evening_pattern = r"上课时间：([^\n]+)\n课程名称：([^\n]+)\n教师：([^\n]+)\n上课地点：([^\n]+)\n周次：([^\n]+)"
        evening_matches = re.finditer(evening_pattern, text)
        for match in evening_matches:
            course = {
                "time": match.group(1),
                "name": match.group(2),
                "teacher": match.group(3),
                "location": match.group(4),
                "weeks": match.group(5)
            }
            course_info["evening_courses"].append(course)

        # 解析备注
        remark_pattern = r"•\s*([^\n]+)"
        remark_matches = re.finditer(remark_pattern, text)
        for match in remark_matches:
            remark = match.group(1).strip()
            if remark and not remark.startswith("备注内容"):
                course_info["remarks"].append(remark)

        return course_info

    def format_course_info(self, course_info):
        """格式化课程信息用于显示"""
        result = []
        
        # 格式化基本信息
        if course_info["basic_info"]:
            result.append("📚 基本信息")
            for key, value in course_info["basic_info"].items():
                result.append(f"• {key}：{value}")
            result.append("")

        # 格式化每周课程
        if course_info["weekly_courses"]:
            result.append("🗓️ 每周课程详情")
            for day, courses in course_info["weekly_courses"].items():
                result.append(f"星期{day}")
                for course in courses:
                    result.append(
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
                    result.append("")
            result.append("")

        # 格式化晚间课程
        if course_info["evening_courses"]:
            result.append("🌙 晚间课程")
            for course in course_info["evening_courses"]:
                result.append(
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
                result.append("")
            result.append("")

        # 格式化备注
        if course_info["remarks"]:
            result.append("📌 重要备注")
            for remark in course_info["remarks"]:
                result.append(f"• {remark}")

        return "\n".join(result)

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
        self.save_data() 