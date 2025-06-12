# kccj 课程提醒插件

> 仓库地址：[https://github.com/teheiw197/kccj](https://github.com/teheiw197/kccj)

## 简介

kccj 是一款基于 AstrBot 的课程提醒插件，支持课程表文本解析、自动提醒、每日预览等功能，帮助用户高效管理课程时间。

## 功能特性
- 课程表文本自动解析
- 课前30分钟自动提醒
- 每日23:00发送次日课程预览
- 支持自定义提醒时间、消息模板
- 支持命令：/help /test /preview /status /stop /start /clear
- 仅支持文本课程表，图片/文件会提示转为文本

## 安装方法
1. 将本插件目录 `kccj` 放入 AstrBot 的 `data/plugins/` 目录下。
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 启动 AstrBot，插件会自动加载。

## 使用说明
- 发送课程表文本，插件会自动解析并确认。
- 支持以下命令：
  - `/help` 显示帮助
  - `/test` 发送测试提醒
  - `/preview` 预览明天课程
  - `/status` 查看提醒状态
  - `/stop` 停止提醒
  - `/start` 开启提醒
  - `/clear` 清除课程数据
- 发送图片/文件时会提示转为文本。

## 配置说明
- 插件支持 WebUI 配置提醒时间、每日预览、消息模板等。
- 配置文件：`_conf_schema.json`，可自定义提醒行为。

## 贡献方式
欢迎提交 issue 和 PR！

## 作者
- teheiw197

## 开源协议
MIT License 