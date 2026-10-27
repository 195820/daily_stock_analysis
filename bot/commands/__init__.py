# -*- coding: utf-8 -*-
"""
===================================
命令处理器模块
===================================

包含所有机器人命令的实现。
"""

from bot.commands.base import BotCommand

# PEP 562 懒导出：命令实现里 analyze/ask 等要 import data_provider（pandas/akshare，
# +116 MB RSS）。常驻监听进程 `import bot.dispatcher` 时只需要 base 基类；
# 真正分发到某条 /command 时（get_dispatcher -> ALL_COMMANDS）才拉起对应命令模块。
_COMMANDS = {
    "HelpCommand": ("bot.commands.help", "HelpCommand"),
    "StatusCommand": ("bot.commands.status", "StatusCommand"),
    "AnalyzeCommand": ("bot.commands.analyze", "AnalyzeCommand"),
    "MarketCommand": ("bot.commands.market", "MarketCommand"),
    "BatchCommand": ("bot.commands.batch", "BatchCommand"),
    "AskCommand": ("bot.commands.ask", "AskCommand"),
    "ChatCommand": ("bot.commands.chat", "ChatCommand"),
    "ResearchCommand": ("bot.commands.research", "ResearchCommand"),
    "StrategiesCommand": ("bot.commands.strategies", "StrategiesCommand"),
    "HistoryCommand": ("bot.commands.history", "HistoryCommand"),
    "PortfolioCommand": ("bot.commands.portfolio", "PortfolioCommand"),
}

# All available commands (for auto-registration)。
# 兼容旧「模块级实体列表」：这里返回按 __all__ 顺序 import 的类列表，
# 只在 get_dispatcher() 首次调用时被访问一次。
_ORDER = [
    "HelpCommand", "StatusCommand", "AnalyzeCommand", "MarketCommand",
    "BatchCommand", "AskCommand", "ChatCommand", "ResearchCommand",
    "StrategiesCommand", "HistoryCommand", "PortfolioCommand",
]

__all__ = [
    "BotCommand",
    *_ORDER,
    "ALL_COMMANDS",
]


def __getattr__(name):
    import importlib

    if name == "ALL_COMMANDS":
        classes = []
        for cls_name in _ORDER:
            module_name, attr = _COMMANDS[cls_name]
            classes.append(getattr(importlib.import_module(module_name), attr))
        globals()[name] = classes  # 缓存，避免重复 import 查找
        return classes

    entry = _COMMANDS.get(name) or (("bot.commands.base", "BotCommand") if name == "BotCommand" else None)
    if entry is None:
        raise AttributeError(f"module 'bot.commands' has no attribute {name!r}")
    module = importlib.import_module(entry[0])
    value = getattr(module, entry[1])
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals()) + __all__)
