# -*- coding: utf-8 -*-
"""GATE-1 可成交性投影层 + WI-P1-9 真实交易日历 + WI-P1-7(a) 词表重叠自检。

对应实施报告 docs/got/plans/architecture_optimization_implementation_20260929.md：
- GATE-1 验收：跌停一字/T+1/涨停封板（含一字与盘中封板）三态用例 + 行情不可用不拦截不抛异常 +
  开关全关逐字节不变红线
- WI-P1-9 验收：节假日计数正确；日历异常降级 weekend-only 不崩
- WI-P1-7(a) 验收：去重后词表无跨族重叠
"""
from __future__ import annotations

from datetime import date

import pytest

from src.got.nodes.portfolio_decision import PortfolioDecisionNode
from src.got.schemas import PortfolioRiskAssessment, UnifiedScoringResult
from src.got.tradability import (
    TradabilityResult,
    check_tradability,
    limit_pct_for_code,
)

EQUITY = 100_000.0


# ─────────────────────────────────────────────────────────────
# tradability 纯函数层
# ─────────────────────────────────────────────────────────────

def test_limit_pct_by_board() -> None:
    assert limit_pct_for_code("600123") == 10.0
    assert limit_pct_for_code("300750") == 20.0
    assert limit_pct_for_code("688981") == 20.0
    assert limit_pct_for_code("920001") == 30.0
    assert limit_pct_for_code("AAPL") == 0.0  # 非 A 股：不适用 → 调用方放行


def test_sell_blocked_at_limit_down_one_word() -> None:
    res = check_tradability(
        action="sell", quote_price=9.00, pre_close=10.0,
        quote_low=9.00, quote_high=9.00, limit_pct=10.0,
    )
    assert not res.executable and res.blocked_reason == "limit_down"


def test_sell_allowed_when_board_opened_intraday() -> None:
    """现价跌停但盘中曾开板（high 高于跌停价）→ 不拦截（少拦优于错拦）"""
    res = check_tradability(
        action="sell", quote_price=9.00, pre_close=10.0,
        quote_low=9.00, quote_high=9.40, limit_pct=10.0,
    )
    assert res.executable and res.blocked_reason is None


def test_sell_blocked_by_t_plus_1() -> None:
    today = date(2026, 9, 29)
    res = check_tradability(
        action="sell", quote_price=10.2, pre_close=10.0, limit_pct=10.0,
        entry_date=today, today=today,
    )
    assert not res.executable and res.blocked_reason == "t_plus_1"
    # 昨日建仓 → T+1 解禁，可卖
    res2 = check_tradability(
        action="sell", quote_price=10.2, pre_close=10.0, limit_pct=10.0,
        entry_date=date(2026, 9, 28), today=today,
    )
    assert res2.executable and res2.blocked_reason is None


def test_t_plus_1_blocked_even_when_quote_missing() -> None:
    """CR#2 回归：T+1 不依赖行情——即使现价/昨收缺失（本应走 quote_unavailable 放行），
    当日建仓的卖单仍必须被拦为 t_plus_1（修正前会被行情充分性检查旁路）。"""
    today = date(2026, 9, 29)
    res = check_tradability(
        action="sell", quote_price=None, pre_close=None,
        entry_date=today, today=today,
    )
    assert not res.executable and res.blocked_reason == "t_plus_1"


def test_buy_blocked_at_limit_up_one_word() -> None:
    res = check_tradability(
        action="buy", quote_price=11.00, pre_close=10.0,
        quote_low=11.00, quote_high=11.00, limit_pct=10.0,
    )
    assert not res.executable and res.blocked_reason == "limit_up"


def test_buy_blocked_when_limit_up_after_intraday_open() -> None:
    """现价触板但盘中曾开板（low < 涨停）→ 仍不可买（2026-10-27 002093 实盘）"""
    res = check_tradability(
        action="buy", quote_price=11.00, pre_close=10.0,
        quote_low=10.50, quote_high=11.00, limit_pct=10.0,
    )
    assert not res.executable and res.blocked_reason == "limit_up"


def test_buy_allowed_below_limit_up() -> None:
    """现价未触板 → 放行（不误拦开板后回落的票）"""
    res = check_tradability(
        action="buy", quote_price=10.90, pre_close=10.0,
        quote_low=10.50, quote_high=11.00, limit_pct=10.0,
    )
    assert res.executable and res.blocked_reason is None


def test_missing_quote_never_blocks() -> None:
    """降级红线：行情缺失 → 放行 + quote_unavailable 告警标签"""
    res = check_tradability(action="sell", quote_price=None, pre_close=None)
    assert res.executable and res.blocked_reason == "quote_unavailable"
    res2 = check_tradability(action="sell", quote_price=10.0, pre_close=None)
    assert res2.executable and res2.blocked_reason == "quote_unavailable"


# ─────────────────────────────────────────────────────────────
# _allocate 接线：三态开关行为
# ─────────────────────────────────────────────────────────────

def _holding(symbol="601999", name="测试持仓", action="sell") -> UnifiedScoringResult:
    return UnifiedScoringResult(
        symbol=symbol, name=name, is_holding=True, composite_score=30.0,
        dimension_scores={}, action_recommendation=action, confidence=0.8,
        reasoning="板块衰竭，建议清仓",
    )


def _buy_candidate(symbol="002040", name="测试候选", score=80.0) -> UnifiedScoringResult:
    return UnifiedScoringResult(
        symbol=symbol, name=name, is_holding=False, composite_score=score,
        dimension_scores={}, action_recommendation="buy_trend", confidence=0.7,
        reasoning="test",
    )


def _risk() -> PortfolioRiskAssessment:
    return PortfolioRiskAssessment(
        position_limit=0.3, current_position_ratio=0.2, constraint_reasoning="test"
    )


def _portfolio_with_position() -> dict:
    return {
        "accounts": [{
            "positions": [{
                "symbol": "601999", "quantity": 2000, "last_price": 10.0,
                "unrealized_pnl_pct": 5.0,
            }],
        }],
        "total_equity": EQUITY,
        "total_market_value": 20_000.0,
        "total_cash": 5_000.0,
    }


_LIMIT_DOWN_Q = {"price": 9.00, "pre_close": 10.0, "high": 9.00, "low": 9.00}
_NORMAL_Q = {"price": 10.2, "pre_close": 10.0, "high": 10.5, "low": 9.9}
_LIMIT_UP_Q = {"price": 11.00, "pre_close": 10.0, "high": 11.00, "low": 11.00}


@pytest.fixture
def node(monkeypatch: pytest.MonkeyPatch) -> PortfolioDecisionNode:
    n = PortfolioDecisionNode(use_mock=False)
    monkeypatch.setattr(n, "_get_realtime_price", lambda symbol: 11.0)
    return n


def _patch_quote(node, monkeypatch, quotes: dict, entry_date=None) -> None:
    """行情/建仓日注入：_allocate 内部为函数级 import，patch 源模块属性即生效"""
    import src.got.tradability as tb
    monkeypatch.setattr(tb, "fetch_tradability_inputs", lambda s: dict(quotes.get(s, {})))
    monkeypatch.setattr(tb, "get_last_entry_date", lambda s, t: entry_date)


def test_gate_off_keeps_legacy_byte_identical(node, monkeypatch) -> None:
    """开关全关（缺省 off）：跌停持仓照常产生成交卖单，不打标、不拦截、无留痕"""
    monkeypatch.delenv("GOT_TRADABILITY_GATE", raising=False)
    _patch_quote(node, monkeypatch, {"601999": _LIMIT_DOWN_Q})
    allocations, _ = node._allocate([_holding()], _risk(), _portfolio_with_position())
    sells = [a for a in allocations if a.symbol == "601999"]
    assert len(sells) == 1
    assert sells[0].quantity_delta == -2000
    assert sells[0].pending_exit is False and sells[0].blocked_reason is None
    assert node._tradability_pending is None


def test_shadow_flags_without_changing_decision(node, monkeypatch) -> None:
    """shadow：跌停卖单打标 pending_exit + limit_down，但数量/预算逐字节不变"""
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "shadow")
    _patch_quote(node, monkeypatch, {"601999": _LIMIT_DOWN_Q})
    allocations, _ = node._allocate([_holding()], _risk(), _portfolio_with_position())
    sells = [a for a in allocations if a.symbol == "601999"]
    assert len(sells) == 1
    assert sells[0].quantity_delta == -2000  # 决策不变
    assert sells[0].pending_exit is True
    assert sells[0].blocked_reason == "limit_down"
    assert node._tradability_pending["mode"] == "shadow"
    checked = node._tradability_pending["checked_exits"]
    assert len(checked) == 1
    assert checked[0]["executable"] is False
    assert checked[0]["blocked_reason"] == "limit_down"


def test_enabled_defers_blocked_sell(node, monkeypatch) -> None:
    """enabled：跌停一字卖单不进当日成交集合，释放资金不计入预算"""
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "enabled")
    _patch_quote(node, monkeypatch, {"601999": _LIMIT_DOWN_Q})
    allocations, _ = node._allocate([_holding()], _risk(), _portfolio_with_position())
    assert [a for a in allocations if a.symbol == "601999" and a.quantity_delta != 0] == []
    pendings = node._tradability_pending["pending_exits"]
    assert len(pendings) == 1
    assert pendings[0]["reason"] == "limit_down" and pendings[0]["intent"] == "sell"


def test_enabled_blocks_t_plus_1(node, monkeypatch) -> None:
    """enabled：当日新建仓 + sell 信号 → t_plus_1 拦截顺延"""
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "enabled")
    _patch_quote(node, monkeypatch, {"601999": _NORMAL_Q}, entry_date=date.today())
    allocations, _ = node._allocate([_holding()], _risk(), _portfolio_with_position())
    assert [a for a in allocations if a.symbol == "601999" and a.quantity_delta != 0] == []
    pendings = node._tradability_pending["pending_exits"]
    assert pendings[0]["reason"] == "t_plus_1"


def test_quote_unavailable_never_blocks_enabled(node, monkeypatch) -> None:
    """enabled + 行情不可用：不拦截、不抛异常、不标 pending_exit（CR#3），仅留告警标签"""
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "enabled")
    _patch_quote(node, monkeypatch, {})  # 行情取不到
    allocations, _ = node._allocate([_holding()], _risk(), _portfolio_with_position())
    sells = [a for a in allocations if a.symbol == "601999"]
    assert len(sells) == 1 and sells[0].quantity_delta == -2000  # 放行
    assert sells[0].pending_exit is False  # CR#3：降级放行不标 pending_exit
    assert sells[0].blocked_reason == "quote_unavailable"  # 仅留原因供观测
    # 卖单照常执行 → 不进顺延集合
    assert node._tradability_pending["pending_exits"] == []


def test_enabled_blocks_limit_up_buy(node, monkeypatch) -> None:
    """enabled：涨停封板买入候选禁入成交集合"""
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "enabled")
    _patch_quote(node, monkeypatch, {"002040": _LIMIT_UP_Q})
    allocations, _ = node._allocate([_buy_candidate()], _risk(), {
        "accounts": [], "total_equity": EQUITY,
        "total_market_value": 0.0, "total_cash": 30_000.0,
    })
    assert [a for a in allocations if a.action == "buy"] == []
    pendings = node._tradability_pending["pending_exits"]
    assert pendings[0]["reason"] == "limit_up" and pendings[0]["intent"] == "buy_blocked"


def test_shadow_flags_limit_up_buy_but_keeps_it(node, monkeypatch) -> None:
    """shadow：涨停买入仅打标，allocation 仍按旧路径产生"""
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "shadow")
    _patch_quote(node, monkeypatch, {"002040": _LIMIT_UP_Q})
    allocations, _ = node._allocate([_buy_candidate()], _risk(), {
        "accounts": [], "total_equity": EQUITY,
        "total_market_value": 0.0, "total_cash": 30_000.0,
    })
    buys = [a for a in allocations if a.action == "buy"]
    assert len(buys) == 1
    assert buys[0].pending_exit is True and buys[0].blocked_reason == "limit_up"


# ─────────────────────────────────────────────────────────────
# WI-P1-9 真实交易日历
# ─────────────────────────────────────────────────────────────

def test_count_trade_days_weekend_only_default(monkeypatch) -> None:
    from src.got.catalyst_calendar import _count_trade_days
    monkeypatch.delenv("GOT_REAL_TRADE_CAL", raising=False)
    # 2026-09-28(周一) ~ 2026-10-09(周五)：weekend-only = 10 个工作日
    assert _count_trade_days(date(2026, 9, 28), date(2026, 10, 9)) == 10


def test_count_trade_days_real_calendar_skips_holidays(monkeypatch) -> None:
    import src.got.catalyst_calendar as cc
    monkeypatch.setenv("GOT_REAL_TRADE_CAL", "1")
    holidays = {date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)}
    monkeypatch.setattr(
        cc, "_is_cn_trading_day",
        lambda d: d.weekday() < 5 and d not in holidays,
    )
    # 同样区间剔除 4 个节假日 → 6 个交易日
    assert cc._count_trade_days(date(2026, 9, 28), date(2026, 10, 9)) == 6


def test_trade_days_offset_real_calendar(monkeypatch) -> None:
    import src.got.catalyst_calendar as cc
    monkeypatch.setenv("GOT_REAL_TRADE_CAL", "1")
    holidays = {date(2026, 10, 1), date(2026, 10, 2)}
    monkeypatch.setattr(
        cc, "_is_cn_trading_day",
        lambda d: d.weekday() < 5 and d not in holidays,
    )
    # 2026-09-30(周三) +1 交易日：跳过 10-01/10-02 节假日与周末 → 10-05(周一)也是假 → 10-06(假) → 10-07?
    holidays |= {date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)}
    assert cc._trade_days_offset(date(2026, 9, 30), 1) == date(2026, 10, 8)


def test_calendar_library_unavailable_never_counts_weekend(monkeypatch) -> None:
    """CR#1 真实场景：exchange-calendars 缺失时 is_market_open **fail-open 恒返回 True不抛**（非异常），
    weekday 硬约束必须将周末判 False（修正前会被误计为交易日）。"""
    import src.got.catalyst_calendar as cc
    monkeypatch.setenv("GOT_REAL_TRADE_CAL", "1")

    # 模拟库不可用：is_market_open 对所有日期都 fail-open 返回 True（与真实 trading_calendar 一致）
    import src.core.trading_calendar as tc
    monkeypatch.setattr(tc, "is_market_open", lambda market, d: True)
    # 周末：即使 is_market_open 恒 True，叠加 weekday 硬约束后必为 False
    assert cc._is_cn_trading_day(date(2026, 10, 3)) is False  # 周六
    assert cc._is_cn_trading_day(date(2026, 10, 4)) is False  # 周日
    # 工作日：fail-open → 当作交易日（仅节假日维度无法识别，符合降级契约）
    assert cc._is_cn_trading_day(date(2026, 10, 5)) is True  # 周一


def test_calendar_failure_degrades_to_weekend_only(monkeypatch) -> None:
    """日历抛异常 → 降级 weekend-only + 不抛异常（except 分支）。"""
    import src.got.catalyst_calendar as cc
    monkeypatch.setenv("GOT_REAL_TRADE_CAL", "1")

    def _boom(market: str, d: date) -> bool:
        raise RuntimeError("calendar down")

    import src.core.trading_calendar as tc
    monkeypatch.setattr(tc, "is_market_open", _boom)
    # 工作日异常降级→ weekday<5 判 True；周末由前置 weekday 硬短路直接 False
    assert cc._is_cn_trading_day(date(2026, 10, 5)) is True  # 周一（降级）
    assert cc._is_cn_trading_day(date(2026, 10, 3)) is False  # 周六


# ─────────────────────────────────────────────────────────────
# WI-P1-7(a) 词表重叠自检
# ─────────────────────────────────────────────────────────────

def test_no_cross_family_overlap_after_dedup() -> None:
    from src.got.family_keywords import _audit_keyword_overlaps
    overlaps = _audit_keyword_overlaps()
    assert overlaps == [], f"WI-P1-7(a) 后仍存跨族重叠: {overlaps}"


def test_huandian_resolves_to_ad_family() -> None:
    """「换电」显式归智能驾驶（唯一登记），新能源侧死配置已清除"""
    from src.got.family_keywords import FAMILY_KEYWORDS
    owners = [fam for fam, kws in FAMILY_KEYWORDS if "换电" in kws]
    assert owners == ["智能驾驶"]


def test_dikong_resolves_to_military_family() -> None:
    """M3 终审收口（2026-10-06）：「低空经济」走规则侧，不再留给 glue 自由裁量。

    背景：glue 任务2 只对规则侧落「其他」的板块有家族裁量权（aggregator.py 提示词
    明写「仅对落『其他』的板块」）。“低空经济”连续两个抽查日被 glue 判成
    「智能驾驶」（负责人判「无关系」），收口到军工后规则侧直接命中，误配源头切断。
    """
    from src.got.family_keywords import FAMILY_KEYWORDS
    from src.got.nodes.sector_analysis import _build_frame_group

    owners = [fam for fam, kws in FAMILY_KEYWORDS if "低空" in kws]
    assert owners == ["军工"], f"「低空」应唯一登记于军工，实际: {owners}"
    # 真产函数级别断言（不重新实现首命中规则），防止声明序漂移
    assert _build_frame_group("低空经济") == "军工"
    # 回归护栏：不得再落「其他」（那是 glue 裁量权的入口）
    assert _build_frame_group("低空经济") != "其他"
    # 智能驾驶族不得沾“低空”——否则本轮 M3 误配会重新引入
    ad_keywords = dict(FAMILY_KEYWORDS)["智能驾驶"]
    assert not [k for k in ad_keywords if "低空" in k]


# ─────────────────────────────────────────────────────────────
# WI-P0-3 / WI-P0-4 shadow 留痕（仅观测，不改决策）
# ─────────────────────────────────────────────────────────────

def _buy_with_sector(symbol, name, score, sector_name):
    c = _buy_candidate(symbol, name, score)
    c.sector_name = sector_name
    return c


def _risk_with_limits(limits):
    return PortfolioRiskAssessment(
        position_limit=0.9, current_position_ratio=0.0,
        constraint_reasoning="test", sector_exposure_limits=limits,
    )


def test_p0_3_exposure_shadow_flags_would_truncate(node, monkeypatch) -> None:
    """P0-3 shadow：敞口超限时记录会截断多少，但买入数量不因 shadow 改变"""
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "off")
    monkeypatch.setenv("GOT_EXPOSURE_ENFORCE", "shadow")
    # 买入预算充足，单只 limit=0.05→上限 ¥5000，但实际买入远超 → 应记截断
    allocations, _ = node._allocate(
        [_buy_with_sector("002040", "南京港", 74.0, "航运")],
        _risk_with_limits({"航运": 0.05}),
        {"accounts": [], "total_equity": EQUITY, "total_market_value": 0.0, "total_cash": 90_000.0},
    )
    buys = [a for a in allocations if a.action == "buy"]
    assert len(buys) == 1  # shadow 不截断，买入仍产生
    ee = node._exposure_enforce_shadow
    assert ee["mode"] == "shadow"
    assert ee["would_truncate_count"] == 1
    assert ee["would_truncate_total_value"] > 0


def test_p0_3_exposure_shadow_off_no_field(node, monkeypatch) -> None:
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "off")
    monkeypatch.setenv("GOT_EXPOSURE_ENFORCE", "off")
    allocations, _ = node._allocate(
        [_buy_with_sector("002040", "南京港", 74.0, "航运")],
        _risk_with_limits({"航运": 0.05}),
        {"accounts": [], "total_equity": EQUITY, "total_market_value": 0.0, "total_cash": 90_000.0},
    )
    assert node._exposure_enforce_shadow is None
    assert len([a for a in allocations if a.action == "buy"]) == 1


def test_p0_4_signal_size_shadow_compares_equal_vs_weighted(node, monkeypatch) -> None:
    """P0-4 shadow：两只不同分候选产生仓位差，但不改变实际 allocation"""
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "off")
    monkeypatch.setenv("GOT_EXPOSURE_ENFORCE", "off")
    monkeypatch.setenv("GOT_SIGNAL_SIZE", "shadow")
    node._get_realtime_price = lambda symbol: 10.0
    allocations, _ = node._allocate(
        [
            _buy_with_sector("002040", "甲", 90.0, "航运"),
            _buy_with_sector("000070", "乙", 60.0, "航运"),
        ],
        _risk_with_limits({}),
        {"accounts": [], "total_equity": EQUITY, "total_market_value": 0.0, "total_cash": 90_000.0},
    )
    ss = node._signal_size_shadow
    assert ss is not None and ss["mode"] == "shadow"
    assert ss["gamma"] == pytest.approx(0.5)
    # 高分候选加权后应多于等分（信号强→多配），低分反之
    items = {i["symbol"]: i for i in ss["items"]}
    assert items["002040"]["delta"] > 0
    assert items["000070"]["delta"] < 0
    # 实际 allocation 仍为等分（shadow 不改仓位）
    buys = [a for a in allocations if a.action == "buy"]
    assert len(buys) == 2


def test_p0_4_signal_size_shadow_off_no_field(node, monkeypatch) -> None:
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "off")
    monkeypatch.setenv("GOT_EXPOSURE_ENFORCE", "off")
    monkeypatch.setenv("GOT_SIGNAL_SIZE", "off")
    node._get_realtime_price = lambda symbol: 10.0
    node._allocate(
        [_buy_with_sector("002040", "甲", 90.0, "航运")],
        _risk_with_limits({}),
        {"accounts": [], "total_equity": EQUITY, "total_market_value": 0.0, "total_cash": 90_000.0},
    )
    assert node._signal_size_shadow is None
