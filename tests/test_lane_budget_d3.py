# -*- coding: utf-8 -*-
"""D3 分道预算接线（2026-10-20 生效）。

生效语义（GOT_LANE_BUDGET_ENABLED=1，缺省）：
    per_stock_budget(c) = buy_budget × w(lane(c)) / Σ w(lane(top3))
    Σ per_stock_budget = buy_budget        ← 消除「等分×角色打折」的预算蒸发

三态对照（B = buy_budget = 30,000，单价一律 ¥10.0，一手 = ¥1,000）：
| 场景 | D3=1 每只预算 | 股数 | D3=0（旧路径）每只预算 | 股数 |
|---|---|---|---|---|
| 2 主线 + 1 埋伏 | 11,764.7 / 11,764.7 / 6,470.6 | 1100/1100/600 | 10,000 / 10,000 / 5,500 | 1000/1000/500 |
| 3 纯埋伏 | 10,000 ×3 | 1000 ×3 | 5,500 ×3 | 500 ×3 |
| 3 纯主线 | 10,000 ×3 | 1000 ×3 | 10,000 ×3 | 1000 ×3 |
"""
from __future__ import annotations

import importlib
import os

import pytest

from src.got.nodes.portfolio_decision import PortfolioDecisionNode, _stype_to_role
from src.got.schemas import (
    PortfolioRiskAssessment,
    SectorIntel,
    UnifiedContext,
    UnifiedScoringResult,
)

EQUITY = 100_000.0
CASH = 100_000.0
POSITION_LIMIT = 0.3
B = EQUITY * POSITION_LIMIT          # buy_budget = 30,000
PRICE = 10.0                          # 一手 = ¥1,000，便于按预算精确反算股数

MAINLINE_SECTOR = "主线板块"
AMBUSH_SECTOR = "埋伏板块"


def _candidate(symbol: str, sector_name: str, sector_type: str,
               score: float = 70.0) -> UnifiedScoringResult:
    return UnifiedScoringResult(
        symbol=symbol, name=f"票{symbol}", is_holding=False,
        composite_score=score, dimension_scores={},
        action_recommendation="buy_trend", confidence=0.7, reasoning="test",
        sector_name=sector_name, sector_type=sector_type,
    )


def _risk() -> PortfolioRiskAssessment:
    return PortfolioRiskAssessment(
        position_limit=POSITION_LIMIT, current_position_ratio=0.0,
        constraint_reasoning="test",
    )


def _portfolio() -> dict:
    return {
        "accounts": [], "total_equity": EQUITY,
        "total_market_value": 0.0, "total_cash": CASH,
    }


def _unified(roles: dict[str, str]) -> UnifiedContext:
    return UnifiedContext(
        status="normal", unified_signal="neutral", overall_confidence=0.5,
        final_position_limit=0.5,
        confirmed_sectors=[
            SectorIntel(sector_name=name, sector_role=role) for name, role in roles.items()
        ],
        ambush_pool=[],
    )


@pytest.fixture
def node(monkeypatch: pytest.MonkeyPatch) -> PortfolioDecisionNode:
    # 单测不走 GATE-1 实时行情（.env 置 shadow 时会逐票拉东财/akshare，网络慢时整套挂起）
    monkeypatch.setenv("GOT_TRADABILITY_GATE", "off")
    decision_node = PortfolioDecisionNode(use_mock=False)
    monkeypatch.setattr(
        decision_node, "_get_realtime_price", lambda symbol: PRICE
    )
    return decision_node


def _run(node: PortfolioDecisionNode, candidates, unified, enabled: str):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GOT_LANE_BUDGET_ENABLED", enabled)
        allocations, _vetoed = node._allocate(
            candidates, _risk(), _portfolio(), unified_context=unified
        )
    return {a.symbol: a for a in allocations}


# ─────────────────────── 1. 混合道：预算蒸发消除 ───────────────────────

def test_mixed_lane_budget_is_fully_deployed(node: PortfolioDecisionNode) -> None:
    candidates = [
        _candidate("600001", MAINLINE_SECTOR, "active_mainline", 80.0),
        _candidate("600002", MAINLINE_SECTOR, "active_mainline", 79.0),
        _candidate("600003", AMBUSH_SECTOR, "ambush", 78.0),
    ]
    unified = _unified({MAINLINE_SECTOR: "mainline", AMBUSH_SECTOR: "ambush"})

    alloc = _run(node, candidates, unified, "1")

    # 埋伏票必须真的被买到（D4 union + ACTIVE 转正的端到端前提）
    assert set(alloc) == {"600001", "600002", "600003"}

    # 2 主线 + 1 埋伏 → Σw = 2.55；主线 B×1/2.55、埋伏 B×0.55/2.55
    assert alloc["600001"].target_quantity == 1100
    assert alloc["600002"].target_quantity == 1100
    assert alloc["600003"].target_quantity == 600
    assert alloc["600003"].sector_limit_detail["lane"] == "ambush"
    assert alloc["600003"].sector_limit_detail["lane_weight"] == pytest.approx(0.55)
    assert alloc["600003"].sector_limit_detail["lane_budget_applied"] is True

    # 预算全额投放（整手取整误差 < 一手金额）
    total = sum(a.target_value for a in alloc.values())
    assert abs(total - B) < 3 * PRICE * 100
    # 分道比 ≈ 1:0.55（股数比 1100:600 已含取整，用预算反算校验）
    assert alloc["600001"].target_value / alloc["600003"].target_value == pytest.approx(
        1.0 / 0.55, rel=0.12
    )


# ─────────────────────── 2/3. 纯道：与等分一致 ───────────────────────

def test_pure_ambush_lane_gets_equal_share(node: PortfolioDecisionNode) -> None:
    """3 只纯埋伏：D3 下各 B/3（旧路径 0.55×B/3 —— 预算蒸发 45%）"""
    candidates = [
        _candidate(f"60000{i}", AMBUSH_SECTOR, "ambush", 80.0 - i) for i in range(1, 4)
    ]
    unified = _unified({AMBUSH_SECTOR: "ambush"})

    alloc = _run(node, candidates, unified, "1")

    assert {a.target_quantity for a in alloc.values()} == {1000}
    for a in alloc.values():
        assert a.sector_limit_detail["lane"] == "ambush"
        assert a.target_value == pytest.approx(B / 3, abs=PRICE * 100)


def test_pure_mainline_lane_matches_equal_split(node: PortfolioDecisionNode) -> None:
    candidates = [
        _candidate(f"60001{i}", MAINLINE_SECTOR, "active_mainline", 80.0 - i)
        for i in range(1, 4)
    ]
    unified = _unified({MAINLINE_SECTOR: "mainline"})

    alloc = _run(node, candidates, unified, "1")

    assert {a.target_quantity for a in alloc.values()} == {1000}
    assert {a.sector_limit_detail["lane"] for a in alloc.values()} == {"mainline"}


# ─────────────────────── 4. kill-switch：逐字节回归旧路径 ───────────────────────

def test_kill_switch_restores_role_multiplier_path(node: PortfolioDecisionNode) -> None:
    """GOT_LANE_BUDGET_ENABLED=0 → 等分×ROLE_MULTIPLIER：2 主线 + 1 埋伏 = 0.85×B"""
    candidates = [
        _candidate("600001", MAINLINE_SECTOR, "active_mainline", 80.0),
        _candidate("600002", MAINLINE_SECTOR, "active_mainline", 79.0),
        _candidate("600003", AMBUSH_SECTOR, "ambush", 78.0),
    ]
    unified = _unified({MAINLINE_SECTOR: "mainline", AMBUSH_SECTOR: "ambush"})

    alloc = _run(node, candidates, unified, "0")

    assert alloc["600001"].target_quantity == 1000      # 10,000 / 10
    assert alloc["600002"].target_quantity == 1000
    assert alloc["600003"].target_quantity == 500       # 5,500 / 10（0.55 折后）
    assert alloc["600003"].sector_limit_detail["branch"] == "ambush"   # 旧路径按 sector_role
    # =0 回滚路径的 allocation detail 不得新增任何 D3 键（逐字节回旧口径；
    # C4 trace 因此仍按 sector_role 归集，恢复旧 lane_budget_split_shadow 语义）
    assert "lane" not in alloc["600003"].sector_limit_detail
    assert "lane_weight" not in alloc["600003"].sector_limit_detail
    assert "lane_budget_applied" not in alloc["600003"].sector_limit_detail
    assert sum(a.target_value for a in alloc.values()) == pytest.approx(
        0.85 * B, abs=3 * PRICE * 100
    )
    # C4 trace 在 =0 路径仍按 sector_role 归集（不因 D3 键而改道），且开关读数同步为 False
    shadow = {row["lane"]: row for row in (node._lane_budget_split_shadow or [])}
    assert set(shadow) == {"mainline", "ambush"}
    assert shadow["mainline"]["n_positions"] == 2
    assert shadow["ambush"]["n_positions"] == 1
    assert all(row["lane_budget_applied"] is False for row in shadow.values())


# ─────────────────────── 5. 身份未验证：不享道权重 ───────────────────────

def test_unverified_sector_keeps_conservative_unknown_lane(
    node: PortfolioDecisionNode,
) -> None:
    """sector_name 为空（板块身份未验证）→ lane 落 unknown（0.55），不吃 D3 道权重"""
    candidates = [
        _candidate("600001", MAINLINE_SECTOR, "active_mainline", 80.0),
        _candidate("600004", None, "active_mainline", 79.0),
    ]
    unified = _unified({MAINLINE_SECTOR: "mainline"})

    alloc = _run(node, candidates, unified, "1")

    # Σw = 1.0 + 0.55 = 1.55 → 主线 19,354.8（1900 股）、未验证 10,645.2（1000 股）
    assert alloc["600004"].sector_limit_detail["lane"] == "unknown"
    assert alloc["600004"].sector_limit_detail["branch"] == "unknown"
    assert alloc["600001"].target_quantity > alloc["600004"].target_quantity


# ─────────────────────── 6. 开关缺省值 + 映射函数 ───────────────────────

def test_switch_defaults_are_on() -> None:
    """两开关缺省为 1（2026-10-20 用户裁决跳过影子纪律）：清 env 后重载模块直读"""
    import src.got.catalyst_config as cc

    keys = ("GOT_LANE_BUDGET_ENABLED", "GOT_AMBUSH_SPLIT_ACTIVE")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        reloaded = importlib.reload(cc)
        assert reloaded.GOT_LANE_BUDGET_ENABLED is True
        assert reloaded.GOT_AMBUSH_SPLIT_ACTIVE is True
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
        importlib.reload(cc)


def test_stype_to_role_mapping() -> None:
    assert _stype_to_role("active_mainline") == "mainline"
    assert _stype_to_role("ambush") == "ambush"
    assert _stype_to_role("") == "unknown"
    assert _stype_to_role("thesis_relay") == "unknown"
    # 裸 mainline = schema 缺省值（sector_type 未被 passthrough 填充）→ 身份缺失，按 miss 处理
    assert _stype_to_role("mainline") == "unknown"


def test_default_sector_type_is_conservative_unknown_lane(
    node: PortfolioDecisionNode,
) -> None:
    """sector_type 缺失（落 schema 缺省 'mainline'）的票不享主线道权重 → unknown 0.55"""
    candidates = [
        _candidate("600001", MAINLINE_SECTOR, "active_mainline", 80.0),
        _candidate("600005", MAINLINE_SECTOR, "mainline", 79.0),   # schema 缺省值
    ]

    alloc = _run(node, candidates, _unified({MAINLINE_SECTOR: "mainline"}), "1")

    # Σw = 1.0 + 0.55；缺省值的票落 unknown，预算低于确认主线票
    assert alloc["600005"].sector_limit_detail["lane"] == "unknown"
    assert alloc["600005"].target_quantity < alloc["600001"].target_quantity
