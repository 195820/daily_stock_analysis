# -*- coding: utf-8 -*-
"""
===================================
机器人命令触发系统
===================================

通过 @机器人 或发送命令触发股票分析等功能。
支持飞书、钉钉、企业微信、Telegram 等多平台。

模块结构：
- models.py: 统一的消息/响应模型
- dispatcher.py: 命令分发器
- commands/: 命令处理器
- platforms/: 平台适配器
- handler.py: Webhook 处理器

使用方式：
1. 配置环境变量（各平台的 Token 等）
2. 启动 WebUI 服务
3. 在各平台配置 Webhook URL：
   - 飞书: http://your-server/bot/feishu
   - 钉钉: http://your-server/bot/dingtalk
   - 企业微信: http://your-server/bot/wecom
   - Telegram: http://your-server/bot/telegram

支持的命令：
- /analyze <股票代码>  - 分析指定股票
- /market             - 大盘复盘
- /batch              - 批量分析自选股
- /help               - 显示帮助
- /status             - 系统状态
"""

# PEP 562 懒导出：`import bot`（例如 bot.platforms.feishu_stream 触发）不再
# 连带拉起 dispatcher -> commands -> data_provider.base -> pandas/akshare
# （实测 +116 MB RSS）。命令分发只在真正处理 /command 消息时才需要。
_LAZY = {
    "BotMessage": ("bot.models", "BotMessage"),
    "BotResponse": ("bot.models", "BotResponse"),
    "ChatType": ("bot.models", "ChatType"),
    "WebhookResponse": ("bot.models", "WebhookResponse"),
    "CommandDispatcher": ("bot.dispatcher", "CommandDispatcher"),
    "get_dispatcher": ("bot.dispatcher", "get_dispatcher"),
}

__all__ = list(_LAZY)


def __getattr__(name):
    entry = _LAZY.get(name)
    if entry is None:
        raise AttributeError(f"module 'bot' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(entry[0])
    value = getattr(module, entry[1])
    globals()[name] = value  # 缓存，二次访问不再走 __getattr__
    return value


def __dir__():
    return sorted(list(globals()) + __all__)
