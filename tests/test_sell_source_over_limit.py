# -*- coding: utf-8 -*-
"""P1-4 / OPEN-1 W2 卖出来源（组合层超限减仓）单测（排期批 4.1，2026-09-28）。

三态开关（仿 ENABLED+SHADOW 双开关惯例，本项风险最高，先观察再转正）：
- **生效**（`GOT_SELL_SOURCE_ENABLED=1`）：Step 1 个股无卖出但组合仍超上限时，Step 1.5 按综合分
  升序削最弱持仓回落至上限（补 W1「超限无减仓路径」）；家族宽限（`sector_drift_grace=True`）
  持仓跳过（防 688598 式误卖）；确曾超限时 buy_budget 归零抑制「超限反而继续买入」。
- **影子观察**（`ENABLED=0` + `GOT_SELL_SOURCE_SHADOW=1`，**默认**）：只算「会削谁/多少/是否撞宽限」
  落 `node._sell_source_shadow` 留痕，**不写 allocation、不改 sell_proceeds/over_limit_forced**
  → 决策逐字节不变，风险为零却有真读数。
- **全关**（`ENABLED=0` + `SHADOW=0`）：Step 1.5 整体跳过，逐字节还原改动前。
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.got.nodes.portfolio_decision import PortfolioDecisionNode
from src.got.schemas import PortfolioRiskAssessment, UnifiedScoringResult

# 目标仓位上限 0.6 × equity 100_000 = 60_000；持仓市值 80_000 → 超限 20_000
TOTAL_EQUITY = 100_000.0
POS_A = {"symbol": "000001", "quantity": 1000, "last_price": 30.0, "unrealized_pnl_pct": 5.0}   # 市值 30_000，弱（40）
POS_B = {"symbol": "000002", "quantity": 1000, "last_price": 50.0, "unrealized_pnl_pct": -2.0}  # 市值 50_000，强（80）


def _holding(
    symbol: str, name: str, composite: float, *, grace: bool = False,
    action: str = "hold",
) -> UnifiedScoringResult:
    return UnifiedScoringResult(
        symbol=symbol,
        name=name,
        is_holding=True,
        composite_score=composite,
        dimension_scores={},
        action_recommendation=action,  # 默认 hold：个股不触发卖出 → 逼出组合层减仓来源
        confidence=0.6,
        reasoning="test-hold",
        sector_drift_grace=grace,
    )


def _candidate(symbol: str, name: str, composite: float) -> UnifiedScoringResult:
    return UnifiedScoringResult(
        symbol=symbol,
        name=name,
        is_holding=False,
        composite_score=composite,
        dimension_scores={},
        action_recommendation="buy_trend",
        confidence=0.7,
        reasoning="test-buy",
    )


def _risk(position_limit: float = 0.6) -> PortfolioRiskAssessment:
    return PortfolioRiskAssessment(
        position_limit=position_limit,
        current_position_ratio=0.8,
        constraint_reasoning="test",
    )


def _portfolio(positions: list, total_cash: float = 0.0) -> dict:
    mv = sum(p["quantity"] * p["last_price"] for p in positions)
    return {
        "accounts": [{"positions": positions}],
        "total_equity": TOTAL_EQUITY,
        "total_market_value": mv,
        "total_cash": total_cash,
    }


@pytest.fixture
def node(monkeypatch: pytest.MonkeyPatch) -> PortfolioDecisionNode:
    decision_node = PortfolioDecisionNode(use_mock=False)
    monkeypatch.setattr(decision_node, "_get_realtime_price", lambda symbol: 20.0)
    return decision_node


# ── 生效模式（ENABLED=1）：真减仓 ──

def test_over_limit_trims_weakest_when_enabled(node: PortfolioDecisionNode) -> None:
    """生效：超限削最弱持仓（A 综合分 40 先削），减仓数量=覆盖缺口的最小整手。"""
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "1"}):
        allocations, _vetoed = node._allocate(
            [_holding("000001", "弱A", 40.0), _holding("000002", "强B", 80.0)],
            _risk(),
            _portfolio([POS_A, POS_B]),
        )

    sells = [a for a in allocations if a.action == "sell"]
    assert {s.symbol for s in sells} == {"000001"}, "只应削最弱持仓 A"
    trim = sells[0]
    # 缺口 20_000 / 价 30 = 666.7 股 → 向上取整到 700 股（¥21_000 ≥ 缺口，封顶不超持仓）
    assert trim.quantity_delta == -700
    assert trim.current_quantity == 1000
    assert trim.target_quantity == 300
    assert "组合超限减仓" in trim.reasoning
    # 生效模式同样落留痕，mode=enabled
    assert node._sell_source_shadow is not None
    assert node._sell_source_shadow["mode"] == "enabled"


def test_grace_protected_holding_skipped(node: PortfolioDecisionNode) -> None:
    """生效：家族宽限持仓跳过减仓（防 688598 式误卖），转削非宽限的强持仓 B。"""
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "1"}):
        allocations, _vetoed = node._allocate(
            [_holding("000001", "弱A", 40.0, grace=True), _holding("000002", "强B", 80.0)],
            _risk(),
            _portfolio([POS_A, POS_B]),
        )

    sells = [a for a in allocations if a.action == "sell"]
    assert {s.symbol for s in sells} == {"000002"}, "宽限的 A 不得被削，改削 B"
    # 缺口 20_000 / 价 50 = 400 股（¥20_000 恰好压回上限）
    assert sells[0].quantity_delta == -400
    assert node._sell_source_shadow["grace_skipped_symbols"] == ["000001"]


def test_over_limit_forced_suppresses_rebuy(node: PortfolioDecisionNode) -> None:
    """W1 闭环：削至上限后 buy_budget 归零，即便存在买入候选也不回补。"""
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "1"}):
        allocations, vetoed = node._allocate(
            [
                _holding("000001", "弱A", 40.0, grace=True),  # 宽限，跳过
                _holding("000002", "强B", 80.0),             # 削 400 股恰好压回上限
                _candidate("300003", "新C", 90.0),           # 有买入候选也不给预算
            ],
            _risk(),
            _portfolio([POS_A, POS_B]),
        )

    buys = [a for a in allocations if a.action == "buy"]
    assert buys == [], "超限减仓后不得用减仓款回补"
    assert "300003" in {v.symbol for v in vetoed}, "被预算归零否决的候选须显式透出"


# ── 评审修复边界用例（2026-09-28）：重复削减 / 零股 / 整手向上取整超配 ──

def test_no_duplicate_sell_for_step1_handled_symbols(node: PortfolioDecisionNode) -> None:
    """修复#1：Step 1 已 sell 的标的不得被 Step 1.5 二次开卖（削减量只按 remaining_qty 计）。

    场景：A（弱、action=sell，市值 30_000）Step 1 全清释放 30_000，但 MV 80_000-30_000=50_000
    仍超上限 40_000（limit 0.4）→ Step 1.5 应只削 B，且 A 在 allocations 仅出现一次。
    旧缺陷：候选不排除已卖标的、按原始股数二次削 A → 卖 2000 股实持 1000 股 + proceeds 虚增。
    """
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "1"}):
        allocations, _vetoed = node._allocate(
            [_holding("000001", "弱A", 40.0, action="sell"), _holding("000002", "强B", 80.0)],
            _risk(position_limit=0.4),
            _portfolio([POS_A, POS_B]),
        )

    sells = [a for a in allocations if a.action == "sell"]
    assert [s.symbol for s in sells] == ["000001", "000002"], "A 仅 Step 1 一张卖单，无重复"
    step1_a = sells[0]
    assert step1_a.quantity_delta == -1000 and step1_a.target_quantity == 0
    trim_b = sells[1]
    assert "组合超限减仓" in trim_b.reasoning, "超限缺口应改削尚未处理的 B"
    # 缺口 10_000 / 价 50 = 200 股，target 基于剩余敞口 1000 股
    assert trim_b.quantity_delta == -200
    assert trim_b.current_quantity == 1000 and trim_b.target_quantity == 800
    # 影子留痕同算法：planned_trims 同样不得包含已清仓的 A
    assert [t["symbol"] for t in node._sell_source_shadow["planned_trims"]] == ["000002"]


def test_reduce_then_over_limit_uses_remaining_quantity(node: PortfolioDecisionNode) -> None:
    """修复#1：Step 1 已 reduce 的标的，Step 1.5 只能再削未处理余量，不得越及已卖部分。

    场景：A（弱、action=reduce）Step 1 削 500 股（int(1000*0.5/100)*100）余 500；
    超限缺口 25_000（MV 80_000-15_000-40_000）→ 先削 A 剩余 500×30=15_000，再削 B 覆盖尾差
    10_000/50=200 股。旧缺陷：二次削 A 按原始 1000 股计 → target_quantity 变负。
    """
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "1"}):
        allocations, _vetoed = node._allocate(
            [_holding("000001", "弱A", 40.0, action="reduce"), _holding("000002", "强B", 80.0)],
            _risk(position_limit=0.4),
            _portfolio([POS_A, POS_B]),
        )

    sells = [a for a in allocations if a.action == "sell"]
    assert [s.symbol for s in sells] == ["000001", "000001", "000002"]
    assert sells[0].quantity_delta == -500, "Step 1 reduce 50%"
    assert sells[1].quantity_delta == -500 and sells[1].target_quantity == 0, "Step 1.5 只能削余量 500"
    assert sells[2].quantity_delta == -200 and sells[2].target_quantity == 800


def test_odd_lot_holding_cleared_not_negative(node: PortfolioDecisionNode) -> None:
    """修复#2：零股持仓（<100 股）按整笔清仓，不得被 max(100,…) 抬回一手致负股数。

    场景：A 仅持 50 股@30（送股/历史零股），最弱先削 → reduce=min(700, 50)=50 清仓；
    旧缺陷：max(100, min(700,50))=100 → target_quantity=50-100=-50、tgt_value 负值落盘。
    """
    pos_odd = {"symbol": "000001", "quantity": 50, "last_price": 30.0, "unrealized_pnl_pct": 1.0}
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "1"}):
        allocations, _vetoed = node._allocate(
            [_holding("000001", "零股A", 40.0), _holding("000002", "强B", 80.0)],
            _risk(position_limit=0.3),  # target 30_000，MV 51_500 → 超限 21_500
            _portfolio([pos_odd, POS_B]),
        )

    sells = [a for a in allocations if a.action == "sell"]
    trim_a = next(s for s in sells if s.symbol == "000001")
    assert trim_a.quantity_delta == -50, "零股按整笔清仓 50 股"
    assert trim_a.target_quantity == 0, "不得为负"
    assert all(a.target_quantity >= 0 for a in allocations), "任何 allocation 不得出现负股数"
    # 残余缺口 21_500-1_500=20_000 → 改削 B 400 股
    trim_b = next(s for s in sells if s.symbol == "000002")
    assert trim_b.quantity_delta == -400


def test_ceil_overshoot_still_suppresses_rebuy(node: PortfolioDecisionNode) -> None:
    """修复#3：整手向上取常态留下小正 gap，over_limit_forced 前置短路仍须归零预算不回补。

    场景：缺口 20_000 / 价 30 → 削700 股 = 21_000（削过头 1_000）→ position_gap=+1_000>0。
    旧缺陷：elif 被 gap>0 抢先 → buy_budget=min(1_000, cash)>0 拿减仓款回补；
    既有 test_over_limit_forced_suppresses_rebuy 恰好设计了整除无尾差的理想分支未能暴露。
    """
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "1"}):
        allocations, vetoed = node._allocate(
            [
                _holding("000001", "弱A", 40.0),
                _holding("000002", "强B", 80.0),
                _candidate("300003", "新C", 90.0),
            ],
            _risk(),
            _portfolio([POS_A, POS_B], total_cash=5_000.0),
        )

    sells = [a for a in allocations if a.action == "sell"]
    assert sells and sells[0].symbol == "000001" and sells[0].quantity_delta == -700
    # 削 21_000 后 MV 59_000 < target 60_000 → 旧逻辑会用 gap 买入，前置短路后必须归零
    assert [a for a in allocations if a.action == "buy"] == [], "尾差 gap 不得被回补"
    assert "300003" in {v.symbol for v in vetoed}, "被归零否决的候选须显式透出"


# ── 影子模式（ENABLED=0 + SHADOW=1，默认）：跑+留痕，不生效 ──

def test_shadow_records_observation_without_effect(node: PortfolioDecisionNode) -> None:
    """影子：超限只记留痕（会削谁/多少），不写 allocation、不改决策。"""
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "0", "GOT_SELL_SOURCE_SHADOW": "1"}):
        allocations, _vetoed = node._allocate(
            [_holding("000001", "弱A", 40.0), _holding("000002", "强B", 80.0)],
            _risk(),
            _portfolio([POS_A, POS_B]),
        )

    # 决策零改动：不产出任何减仓 allocation
    assert allocations == [], "影子模式不得写入任何 allocation"
    # 但留痕存在，且算出「本会削最弱 A 700 股」
    shadow = node._sell_source_shadow
    assert shadow is not None and shadow["mode"] == "shadow"
    assert shadow["excess_value"] == pytest.approx(20_000.0)
    assert [t["symbol"] for t in shadow["planned_trims"]] == ["000001"]
    assert shadow["planned_trims"][0]["reduce_quantity"] == 700


def test_shadow_respects_grace_in_record(node: PortfolioDecisionNode) -> None:
    """影子：宽限持仓计入 grace_skipped、不进减仓计划（观察期即可见误卖防护效果）。"""
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "0", "GOT_SELL_SOURCE_SHADOW": "1"}):
        allocations, _ = node._allocate(
            [_holding("000001", "弱A", 40.0, grace=True), _holding("000002", "强B", 80.0)],
            _risk(),
            _portfolio([POS_A, POS_B]),
        )

    assert allocations == []
    shadow = node._sell_source_shadow
    assert shadow["grace_skipped_symbols"] == ["000001"]
    assert [t["symbol"] for t in shadow["planned_trims"]] == ["000002"]


# ── 全关（ENABLED=0 + SHADOW=0）：逐字节还原 ──

def test_full_off_restores_old_behavior(node: PortfolioDecisionNode) -> None:
    """两开关皆关：Step 1.5 整体跳过，无减仓、无留痕，逐字节还原改动前。"""
    with patch.dict(os.environ, {"GOT_SELL_SOURCE_ENABLED": "0", "GOT_SELL_SOURCE_SHADOW": "0"}):
        allocations, vetoed = node._allocate(
            [_holding("000001", "弱A", 40.0), _holding("000002", "强B", 80.0)],
            _risk(),
            _portfolio([POS_A, POS_B]),
        )

    assert allocations == [], "关闭时不得产生任何 allocation"
    assert vetoed == [], "无买入候选时否决列表为空"
    assert node._sell_source_shadow is None, "两开关皆关时不留痕"


def test_default_env_is_shadow_on(node: PortfolioDecisionNode) -> None:
    """默认态守卫：未设 env → 生效关、影子开（代码每日跑检测出读数，但不落决策）。"""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("GOT_SELL_SOURCE_ENABLED", None)
        os.environ.pop("GOT_SELL_SOURCE_SHADOW", None)
        allocations, _ = node._allocate(
            [_holding("000001", "弱A", 40.0), _holding("000002", "强B", 80.0)],
            _risk(),
            _portfolio([POS_A, POS_B]),
        )

    # 默认不生效（无真减仓）……
    assert allocations == [], "默认（未设 env）不得触发真减仓"
    # ……但默认在影子观察（留痕可见），代码挣得其位
    assert node._sell_source_shadow is not None
    assert node._sell_source_shadow["mode"] == "shadow"
