# -*- coding: utf-8 -*-
"""GOT 编排器交易日检查。

背景（F-1，2026-09-14 审核）：`_check_trading_day` 曾 `import TradingCalendar`，
而 `src/core/trading_calendar.py` 只有模块级 `is_market_open()`、**不存在该类** ⇒
恒抛 ImportError 被 `except ImportError` 静默吞掉，交易日检查从未生效
（周末/节假日照跑，日志仅一行 warning）。
"""
from __future__ import annotations

from datetime import date

import pytest

from src.got.orchestrator import GOTOrchestrator, NonTradingDayError

NON_TRADING_SATURDAY = date(2026, 9, 12)
NON_TRADING_SUNDAY = date(2026, 9, 13)
TRADING_MONDAY = date(2026, 9, 14)


def _bare_orchestrator() -> GOTOrchestrator:
    """绕过 __init__：待测方法不依赖实例状态，无需构造 6 个节点（省 1.4s）"""
    return GOTOrchestrator.__new__(GOTOrchestrator)


@pytest.mark.parametrize("non_trading_day", [NON_TRADING_SATURDAY, NON_TRADING_SUNDAY])
def test_non_trading_day_raises(non_trading_day: date) -> None:
    with pytest.raises(NonTradingDayError):
        _bare_orchestrator()._check_trading_day(non_trading_day)


def test_trading_day_passes() -> None:
    assert _bare_orchestrator()._check_trading_day(TRADING_MONDAY) is None


def test_no_import_failure_warning(caplog: pytest.LogCaptureFixture) -> None:
    """回归护栏：修复前该分支恒被 ImportError 命中，检查形同虚设"""
    with caplog.at_level("WARNING"):
        with pytest.raises(NonTradingDayError):
            _bare_orchestrator()._check_trading_day(NON_TRADING_SATURDAY)
    assert "无法导入交易日历" not in caplog.text
