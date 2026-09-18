# -*- coding: utf-8 -*-
"""Phase 2 买入分配：整手取整归零必须可见（F-3，2026-09-14 审核）。

`quantity = int(per_stock_budget / price / 100) * 100` 对高价股取整后为 0，
原实现的 `if quantity > 0:` 没有 else ⇒ 候选被静默淘汰、预算不回填。
0914 实测：方邦股份 ¥145.36，100 股需 ¥14,536，而单只预算仅约 ¥11.7k。
"""
from __future__ import annotations

import logging

import pytest

from src.got.nodes.portfolio_decision import PortfolioDecisionNode
from src.got.schemas import PortfolioRiskAssessment, UnifiedScoringResult

EQUITY = 100_000.0
# 买入预算 30,000 / 3 只候选 = 单只 ¥10,000 ⇒ 方邦（100 股需 ¥14,536）取整归零
TOTAL_CASH = 30_000.0
PRICES = {"688020": 145.36, "002040": 9.58, "000070": 16.64, "603773": 25.0, "688598": 30.0}


def _candidate(symbol: str, name: str, composite_score: float) -> UnifiedScoringResult:
    return UnifiedScoringResult(
        symbol=symbol,
        name=name,
        is_holding=False,
        composite_score=composite_score,
        dimension_scores={},
        action_recommendation="buy_trend",
        confidence=0.7,
        reasoning="test",
    )


def _risk() -> PortfolioRiskAssessment:
    return PortfolioRiskAssessment(
        position_limit=0.3, current_position_ratio=0.0, constraint_reasoning="test"
    )


def _portfolio(total_cash: float = TOTAL_CASH) -> dict:
    return {
        "accounts": [],
        "total_equity": EQUITY,
        "total_market_value": 0.0,
        "total_cash": total_cash,
    }


@pytest.fixture
def node(monkeypatch: pytest.MonkeyPatch) -> PortfolioDecisionNode:
    decision_node = PortfolioDecisionNode(use_mock=False)
    monkeypatch.setattr(
        decision_node, "_get_realtime_price", lambda symbol: PRICES.get(symbol, 0.0)
    )
    return decision_node


def test_lot_rounded_to_zero_is_logged_and_excluded(
    node: PortfolioDecisionNode, caplog: pytest.LogCaptureFixture
) -> None:
    candidates = [
        _candidate("688020", "方邦股份", 74.2),
        _candidate("002040", "南京港", 74.0),
        _candidate("000070", "特发信息", 70.7),
    ]

    with caplog.at_level(logging.WARNING):
        allocations, _vetoed = node._allocate(candidates, _risk(), _portfolio())

    assert {a.symbol for a in allocations} == {"002040", "000070"}
    assert any("超出单只预算" in record.message for record in caplog.records), (
        "整手归零必须留痕，否则候选静默消失"
    )


def test_affordable_candidate_buys_whole_lots(node: PortfolioDecisionNode) -> None:
    """回归护栏：预算充裕时高价股正常买入，未被守卫子句误伤"""
    candidates = [_candidate("688020", "方邦股份", 74.2)]

    allocations, _vetoed = node._allocate(
        candidates, _risk(), _portfolio(total_cash=EQUITY)
    )

    assert [a.symbol for a in allocations] == ["688020"]
    # 修复后：无 sector_name 的候选走 unknown 分支（乘数 0.55）
    # buy_budget = min(position_limit×equity, cash) = min(30_000, 100_000) = 30_000
    # 1 只候选 → base_budget = 30_000，×0.55 = 16_500
    # 16_500 / 145.36 / 100 = 1.13 手 → 取整 1 手 → 100 股
    assert allocations[0].target_quantity == 100  # unknown 乘数 0.55：16.5k/145.36 → 1 手


def test_cleared_sector_gets_conservative_budget_branch(node: PortfolioDecisionNode) -> None:
    """板块验证清空的候选（sector_name=None）走 unknown 分支：预算乘数 0.55 而非主线 1.0"""
    cleared = _candidate("603773", "沃格光电", 83.7)   # sector_name 未传 → None
    tagged = _candidate("688598", "金博股份", 76.9)
    tagged.sector_name = "半导体"

    allocations, _vetoed = node._allocate([cleared, tagged], _risk(), _portfolio())

    by_symbol = {a.symbol: a for a in allocations}
    assert by_symbol["603773"].sector_limit_detail["branch"] == "unknown"
    assert by_symbol["603773"].sector_limit_detail["sector_role"] == "unknown"


def test_cleared_sector_dimension_excluded_from_composite() -> None:
    """sector_available=False 时，sector 维度权重归一化剔除，composite 仅含其余 4 维"""
    from src.services.stock_screener import SCORING_WEIGHTS, weighted_score_with_redistribution

    scores = {"capital": 100.0, "news": 91.0, "sector": 94.1, "tech": 46.0, "risk": 100.0}
    avail = {"capital": True, "news": True, "sector": False, "tech": True, "risk": True}
    composite, actual_weights = weighted_score_with_redistribution(scores, avail, SCORING_WEIGHTS)

    assert actual_weights.get("sector", 0.0) == 0.0          # sector 剔除
    # (100×0.2 + 91×0.2 + 46×0.333 + 100×0.267) = 80.2（sector 剔除后权重归一化）
    assert composite == pytest.approx(80.2, abs=0.2)


def test_strip_sector_reasoning() -> None:
    """_strip_sector_reasoning 剥离板块强度段落，替换为验证未通过提示"""
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode

    text = (
        "1. **资金面** (15.0/15): \n"
        "   - 资金面评分 100/100\n"
        "2. **消息面** (13.7/15): \n"
        "   - 消息面评分 91/100\n"
        "3. **板块强度** (23.5/25): \n"
        "   - 属于active_mainline板块「存储芯片」\n"
        "   - 板块强度 94 分\n"
        "4. **技术面** (11.5/25): \n"
        "   - 波段技术指标评分 46/100\n"
        "5. **风险** (20.0/20): \n"
        "   - 安全评分 100/100"
    )
    stripped = PortfolioDecisionNode._strip_sector_reasoning(text)
    assert "存储芯片" not in stripped
    assert "板块验证未通过" in stripped
    assert "4. **技术面**" in stripped   # 后续段落保留


# ── Fix 3: 漂移宽限单测 ──

def test_drift_action_grace_hold() -> None:
    """家族锚点 >=60 → hold + grace_applied=True"""
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode
    from datetime import date

    node = PortfolioDecisionNode()
    family_anchor = {
        "AI 硬件": {
            'sector_name': '先进封装',
            'composite_score': 92.0,
            'sector_role': 'mainline',
            'capital_check': 'aligned',
            'news_check': 'catalyzed',
            'sentiment_check': 'high',
        }
    }
    action, grace = node._drift_action_with_grace(
        "688598", "半导体", 33.0,
        {"半导体": "AI 硬件"}, family_anchor,
        thesis=None, today=date.today(),
    )
    assert action == "hold", f"期望 hold，实际{action}"
    assert grace == True, f"期望 grace=True，实际{grace}"


def test_drift_action_grace_reduce_then_sell() -> None:
    """家族锚点 [40,60) + drift_watch_since=昨天 → sell；drift_watch_since=None → reduce"""
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode
    from datetime import date, timedelta

    node = PortfolioDecisionNode()
    family_anchor = {
        "周期资源": {
            'sector_name': '石墨烯',
            'composite_score': 50.0,
            'sector_role': 'caution',
            'capital_check': 'neutral',
            'news_check': 'neutral',
            'sentiment_check': 'medium',
        }
    }

    # 首次 → reduce
    action, grace = node._drift_action_with_grace(
        "688598", "石墨烯", 33.0,
        {"石墨烯": "周期资源"}, family_anchor,
        thesis=None, today=date.today(),
    )
    assert action == "reduce", f"首次期望 reduce，实际{action}"
    assert grace == True, f"首次期望 grace=True，实际{grace}"

    # 模拟已观察跨日（thesis.drift_watch_since=昨天）
    class Thesis:
        drift_watch_since = date.today() - timedelta(days=1)
        drift_family = "周期资源"
    action, grace = node._drift_action_with_grace(
        "688598", "石墨烯", 33.0,
        {"石墨烯": "周期资源"}, family_anchor,
        thesis=Thesis(), today=date.today(),
    )
    assert action == "sell", f"跨日期望 sell，实际{action}"
    assert grace == False, f"跨日期望 grace=False，实际{grace}"


def test_drift_action_deep_break_sells() -> None:
    """家族锚点 <40 → sell（不宽限）"""
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode
    from datetime import date

    node = PortfolioDecisionNode()
    family_anchor = {
        "冷门行业": {
            'sector_name': '传统机械',
            'composite_score': 30.0,
            'sector_role': 'neutral',
            'capital_check': 'divergent',
            'news_check': 'negative',
            'sentiment_check': 'low',
        }
    }
    action, grace = node._drift_action_with_grace(
        "688598", "传统机械", 25.0,
        {"传统机械": "冷门行业"}, family_anchor,
        thesis=None, today=date.today(),
    )
    assert action == "sell", f"深度破位期望 sell，实际{action}"
    assert grace == False, f"深度破位期望 grace=False，实际{grace}"
