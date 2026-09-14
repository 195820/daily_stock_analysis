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
PRICES = {"688020": 145.36, "002040": 9.58, "000070": 16.64}


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
    assert allocations[0].target_quantity == 200  # 30,000 / 145.36 → 2 手
