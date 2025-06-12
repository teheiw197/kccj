import aiohttp
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class SiliconFlowService:
    def __init__(self, api_key: str, api_base: str = "https://api.siliconflow.cn/v1", model: str = "deepseek-ai/DeepSeek-V3"):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    async def chat_completion(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> Optional[str]:
        """
        调用 SiliconFlow 聊天完成 API
        """
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": False,
                    "max_tokens": 1024
                }
                
                async with session.post(
                    f"{self.api_base}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=30
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result["choices"][0]["message"]["content"]
                    else:
                        error_text = await response.text()
                        logger.error(f"API调用失败: {response.status} - {error_text}")
                        return None
        except Exception as e:
            logger.error(f"调用AI服务时发生错误: {str(e)}")
            return None

    async def parse_course_schedule(self, text: str) -> Optional[List[Dict[str, Any]]]:
        """
        使用 AI 解析课程表文本
        """
        prompt = f"""请帮我解析以下课程表文本，提取每节课的以下信息：
1. 星期几
2. 上课时间（第几节）
3. 课程名称
4. 教室
5. 教师姓名

请以JSON格式返回，格式如下：
[
    {{
        "weekday": "周一",
        "time": "1-2节",
        "course": "高等数学",
        "classroom": "教1-201",
        "teacher": "张三"
    }}
]

课程表文本：
{text}

请确保返回的是合法的JSON格式。如果无法解析某些信息，请将对应字段设为空字符串。"""

        messages = [
            {"role": "system", "content": "你是一个专业的课程表解析助手，请准确提取课程信息。如果某些信息无法确定，请将对应字段设为空字符串。"},
            {"role": "user", "content": prompt}
        ]

        response = await self.chat_completion(messages, temperature=0.3)
        if not response:
            return None

        try:
            # 尝试从响应中提取JSON部分
            json_str = response.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].strip()
            
            courses = json.loads(json_str)
            
            # 验证课程数据
            for course in courses:
                if not isinstance(course, dict):
                    raise ValueError("课程数据格式错误")
                required_fields = ["weekday", "time", "course", "classroom", "teacher"]
                for field in required_fields:
                    if field not in course:
                        course[field] = ""
                    elif not isinstance(course[field], str):
                        course[field] = str(course[field])
            
            return courses
        except Exception as e:
            logger.error(f"解析AI响应时发生错误: {str(e)}")
            return None

    async def generate_reminder_message(self, course: Dict[str, Any]) -> str:
        """
        生成课程提醒消息
        """
        prompt = f"""请生成一条友好的课程提醒消息，包含以下课程信息：
- 课程：{course['course']}
- 时间：{course['weekday']} {course['time']}
- 教室：{course['classroom']}
- 教师：{course['teacher']}

要求：
1. 语气友好自然
2. 包含emoji表情
3. 突出重要信息
4. 长度适中
5. 提醒用户提前到达教室"""

        messages = [
            {"role": "system", "content": "你是一个贴心的课程提醒助手，请生成友好的提醒消息。"},
            {"role": "user", "content": prompt}
        ]

        response = await self.chat_completion(messages, temperature=0.7)
        return response if response else "课程提醒：请准时上课！" 