# 课程提醒插件 (kccj)

一个基于 NoneBot2 的课程提醒插件，支持课程表解析、定时提醒、每日汇总等功能。

## 功能特点

- 支持文本格式课程表解析
- 自动识别课程时间、地点、教师等信息
- 课前30分钟自动提醒
- 每日23:00发送次日课程预览
- 支持测试提醒功能
- 支持多用户数据隔离
- 集成 SiliconFlow AI 大模型

## 使用方法

1. 发送课程表文本，格式示例：
```
周一 1-2节 高等数学 教1-201 张三
周二 3-4节 大学英语 教2-101 李四
```

2. 确认课程信息无误后，回复"是"开启提醒服务

3. 可用指令：
- `/test_reminder` - 测试提醒功能
- `/stop_reminder` - 停止提醒服务
- `/update_schedule` - 更新课程表

## 配置说明

插件配置文件位于 `data/plugins/kccj/config.json`，可配置项：

- `api_key`: SiliconFlow API密钥
- `api_base`: API基础URL
- `model`: 使用的模型名称
- `remind_advance_minutes`: 提前提醒时间（分钟）
- `daily_summary_hour`: 每日汇总时间（小时）
- `daily_summary_minute`: 每日汇总时间（分钟）

## 注意事项

1. 请确保课程表格式正确，包含必要信息
2. 图片/文件格式的课程表需要先转换为文本
3. 建议使用豆包OCR等工具进行转换
4. 提醒服务需要保持机器人在线

## 依赖要求

- Python 3.8+
- NoneBot2
- aiohttp
- python-dateutil

## 安装方法

1. 将插件目录复制到 `data/plugins/` 下
2. 安装依赖：`pip install -r requirements.txt`
3. 配置 API 密钥等信息
4. 重启机器人即可使用 