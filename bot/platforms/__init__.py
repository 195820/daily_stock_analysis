# -*- coding: utf-8 -*-
"""
===================================
平台适配器模块
===================================

包含各平台的 Webhook 处理和消息解析逻辑。

支持两种接入模式：
1. Webhook 模式：需要公网 IP，配置回调 URL
2. Stream 模式：无需公网 IP，通过 WebSocket 长连接（钉钉、飞书支持）
"""

from bot.platforms.base import BotPlatform

# 所有可用平台（Webhook 模式）。
# PEP 562 惰性构建：`from bot.platforms import ALL_PLATFORMS`（bot/handler.py:18）
# 在 webhook 模式处理请求时才 import 平台实现；监听入口 import 本包时不再
# 连带拉起 dingtalk webhook 客户端。缓存后二次访问零开销。
__all__ = [
    "BotPlatform",
    "ALL_PLATFORMS",
    # 钉钉 Stream 模式
    "DingtalkStreamClient",
    "DingtalkStreamHandler",
    "get_dingtalk_stream_client",
    "start_dingtalk_stream_background",
    "DINGTALK_STREAM_AVAILABLE",
    # 飞书 Stream 模式
    "FeishuStreamClient",
    "FeishuStreamHandler",
    "FeishuReplyClient",
    "get_feishu_stream_client",
    "start_feishu_stream_background",
    "FEISHU_SDK_AVAILABLE",
]

_LAZY = {
    "DingtalkPlatform": ("bot.platforms.dingtalk", "DingtalkPlatform"),
    "DingtalkStreamClient": ("bot.platforms.dingtalk_stream", "DingtalkStreamClient"),
    "DingtalkStreamHandler": ("bot.platforms.dingtalk_stream", "DingtalkStreamHandler"),
    "get_dingtalk_stream_client": ("bot.platforms.dingtalk_stream", "get_dingtalk_stream_client"),
    "start_dingtalk_stream_background": ("bot.platforms.dingtalk_stream", "start_dingtalk_stream_background"),
    "DINGTALK_STREAM_AVAILABLE": ("bot.platforms.dingtalk_stream", "DINGTALK_STREAM_AVAILABLE"),
    "FeishuStreamClient": ("bot.platforms.feishu_stream", "FeishuStreamClient"),
    "FeishuStreamHandler": ("bot.platforms.feishu_stream", "FeishuStreamHandler"),
    "FeishuReplyClient": ("bot.platforms.feishu_stream", "FeishuReplyClient"),
    "get_feishu_stream_client": ("bot.platforms.feishu_stream", "get_feishu_stream_client"),
    "start_feishu_stream_background": ("bot.platforms.feishu_stream", "start_feishu_stream_background"),
    "FEISHU_SDK_AVAILABLE": ("bot.platforms.feishu_stream", "FEISHU_SDK_AVAILABLE"),
}


def __getattr__(name):
    import importlib

    if name == "ALL_PLATFORMS":
        from bot.platforms.dingtalk import DingtalkPlatform

        value = {"dingtalk": DingtalkPlatform}
        globals()[name] = value
        return value

    entry = _LAZY.get(name)
    if entry is None:
        raise AttributeError(f"module 'bot.platforms' has no attribute {name!r}")
    module = importlib.import_module(entry[0])
    value = getattr(module, entry[1])
    globals()[name] = value  # 缓存，二次访问不再走 __getattr__
    return value


def __dir__():
    return sorted(list(globals()) + __all__)
