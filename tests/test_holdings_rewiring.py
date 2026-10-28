# -*- coding: utf-8 -*-
"""持仓模块三原则接线（H1/H2/H3）回归。

覆盖：
- H1 供数：HoldingsContext 构建、板块名解析、Node 3 资金流索引、Node 6 持仓专属情报
- H2 裁决归位：`decision_plan.assess_holding` 分支表（含 sector_confirmed 守卫）
- H3 历史回查：`lookup_decision_history` 三表 join + n<3 不出比率 + 催化剂双锚

判定函数为纯函数，夹具直接构造 dataclass；DB 相关用 DATABASE_PATH 隔离临时库。
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.config import Config
from src.got.decision_plan import (
    HoldingAssessmentFacts,
    assess_holding,
    build_history_shadow,
    build_verdict_shadow,
    lookup_decision_history,
)
from src.got.schemas import HoldingBrief, HoldingsContext, SectorContext, SectorDetail
from src.storage import (
    AmbushPositionSnapshotRecord,
    CatalystEventRecord,
    DatabaseManager,
    HoldingThesisRecord,
    TradePlanRecord,
)


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "holdings_rewiring.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


# =============================================================================
# H2：assess_holding 分支表
# =============================================================================

def _facts(**overrides) -> HoldingAssessmentFacts:
    base = dict(
        symbol="600519",
        sector_name="半导体",
        sector_strength=70.0,
        sector_role="mainline",
        lifecycle_phase="launch",
        lifecycle_day=5,
        lifecycle_action="hold",
        lifecycle_reason="鱼身",
    )
    base.update(overrides)
    return HoldingAssessmentFacts(**base)


def test_assess_no_sector_without_grace_sells_and_marks_broken():
    verdict = assess_holding(_facts(sector_name="", all_belong_boards=[]))
    assert verdict.action == "sell"
    assert verdict.reason_code == "no_sector"
    assert verdict.thesis_broken is True


def test_assess_no_sector_with_grace_holds():
    facts = _facts(
        sector_name="",
        all_belong_boards=["军工"],
        grace_resolver=lambda drift: ("hold", True),
    )
    verdict = assess_holding(facts)
    assert (verdict.action, verdict.reason_code) == ("hold", "no_sector_grace")
    assert verdict.grace_applied is True
    assert verdict.thesis_broken is False


def test_assess_thesis_drift_grace_vs_sell():
    grace_facts = _facts(thesis_drift=True, thesis_family="AI", grace_resolver=lambda d: ("reduce", True))
    verdict = assess_holding(grace_facts)
    assert verdict.reason_code == "thesis_drift_grace"
    assert verdict.action == "reduce"
    assert "AI" in verdict.reason_text

    sell_facts = _facts(thesis_drift=True, thesis_family="AI", grace_resolver=lambda d: ("sell", False))
    verdict = assess_holding(sell_facts)
    assert verdict.reason_code == "thesis_drift"
    assert verdict.thesis_broken is True


@pytest.mark.parametrize(
    "strength,expected_action,expected_code",
    [
        (39.9, "sell", "lifecycle_none_weak"),
        (40.0, "reduce", "lifecycle_none_cooling"),
        (59.9, "reduce", "lifecycle_none_cooling"),
        (60.0, "hold", "lifecycle_none_strong"),
    ],
)
def test_assess_lifecycle_none_strength_ladder(strength, expected_action, expected_code):
    verdict = assess_holding(_facts(lifecycle_phase=None, sector_strength=strength))
    assert (verdict.action, verdict.reason_code) == (expected_action, expected_code)


def test_assess_sector_confirmed_guard_only_when_enabled():
    facts = _facts(lifecycle_phase=None, sector_strength=55.0, sector_confirmed=True)

    legacy = assess_holding(facts, guard_enabled=False)
    assert (legacy.action, legacy.reason_code) == ("reduce", "lifecycle_none_cooling")
    assert legacy.sector_confirmed_guard is False

    v2 = assess_holding(facts, guard_enabled=True)
    assert (v2.action, v2.reason_code) == ("hold", "sector_confirmed")
    assert v2.sector_confirmed_guard is True


def test_assess_guard_not_applied_when_sector_not_confirmed():
    facts = _facts(lifecycle_phase=None, sector_strength=55.0, sector_confirmed=False)
    assert assess_holding(facts, guard_enabled=True).reason_code == "lifecycle_none_cooling"


def test_assess_strength_and_cooling_branches():
    assert assess_holding(_facts(sector_strength=49.0)).reason_code == "strength_lt50"
    cooling = assess_holding(_facts(sector_role="cooling", sector_strength=55.0))
    assert (cooling.action, cooling.reason_code) == ("hold", "cooling_hold")


def test_assess_other_role_grace_and_break():
    grace = _facts(sector_role="neutral", grace_resolver=lambda d: ("hold", True))
    assert assess_holding(grace).reason_code == "role_other_grace"

    broken = _facts(sector_role="neutral", grace_resolver=lambda d: ("sell", False))
    verdict = assess_holding(broken)
    assert verdict.reason_code == "role_other"
    assert verdict.thesis_broken is True


def test_assess_p4_branches_snapshot_anchor():
    exit_facts = _facts(
        sector_role="ambush",
        ambush_exit={"signal": "fade_sell", "reason": "催化消退", "category_tag": "fade"},
    )
    verdict = assess_holding(exit_facts)
    assert (verdict.action, verdict.reason_code) == ("sell", "p4_exit")
    assert "fade_sell" in verdict.reason_text

    hold_facts = _facts(
        sector_role="ambush",
        ambush_exit={"signal": None, "reason": "", "category_tag": "drift"},
    )
    verdict = assess_holding(hold_facts)
    assert (verdict.action, verdict.reason_code) == ("hold", "p4_hold")


def test_assess_p4_exit_precedes_lifecycle_missing_ladder():
    """埋伏板块 lifecycle 恒为 None：退出信号必须抢在 40/60 降温档之前（原链死分支修复）。"""
    facts = _facts(
        sector_role="ambush",
        lifecycle_phase=None,
        sector_strength=55.0,
        sector_confirmed=True,
        ambush_exit={"signal": "drift_exhausted", "reason": "动能耗尽", "category_tag": "drift"},
    )
    for guard in (True, False):
        verdict = assess_holding(facts, guard_enabled=guard)
        assert (verdict.action, verdict.reason_code) == ("sell", "p4_exit")
        assert verdict.evidence["p4_anchor"] == "snapshot"


def test_assess_p4_without_signal_keeps_legacy_ladder():
    """无退出信号 → 不抢前，仍按 lifecycle 缺失 + 强度档（逐字节等价旧行为）。"""
    facts = _facts(
        sector_role="ambush",
        lifecycle_phase=None,
        sector_strength=55.0,
        sector_confirmed=False,
        ambush_exit={"signal": None, "reason": "", "category_tag": "drift"},
    )
    assert assess_holding(facts).reason_code == "lifecycle_none_cooling"


def test_assess_p4_thesis_catalyst_double_anchor_gated_by_history_mode():
    facts = _facts(
        sector_role="ambush",
        ambush_exit=None,
        history={"catalyst": {"status": "elapsed"}},
    )
    # 影子态：历史只作证据，仍走生命周期分支
    shadow = assess_holding(facts, history_enabled=False)
    assert shadow.reason_code == "lifecycle_signal"
    assert shadow.evidence["history"]["catalyst_status"] == "elapsed"

    # enabled：thesis 催化剂锚已到期 → 不需要 snapshot 也能退出
    enabled = assess_holding(facts, history_enabled=True)
    assert (enabled.action, enabled.reason_code) == ("sell", "p4_exit")
    assert enabled.evidence["p4_anchor"] == "thesis"


def test_assess_lifecycle_fallback_branch():
    verdict = assess_holding(_facts(lifecycle_action="reduce", lifecycle_reason="成熟期"))
    assert (verdict.action, verdict.reason_code) == ("reduce", "lifecycle_signal")
    assert verdict.evidence["lifecycle_reason"] == "成熟期"


def test_grace_resolver_not_called_on_plain_branch():
    calls: list[str] = []

    def _resolver(drift):  # pragma: no cover - 断言不会被调用
        calls.append(drift)
        return "hold", True

    assess_holding(_facts(grace_resolver=_resolver))
    assert calls == []


def test_build_verdict_shadow_counts_changes():
    rows = [
        {"symbol": "A", "changed": True},
        {"symbol": "B", "changed": False},
    ]
    shadow = build_verdict_shadow(rows, mode="shadow", holdings_seen=2)
    assert shadow["changed_count"] == 1
    assert shadow["changed_symbols"] == ["A"]
    assert shadow["holdings_seen"] == 2


# =============================================================================
# H1：供数（context builder / 解析 / 索引 / 情报）
# =============================================================================

def test_build_holdings_context_merges_accounts_and_theses(isolated_db):
    from src.got.holdings_context import build_holdings_context

    with isolated_db.get_session() as session:
        session.add(HoldingThesisRecord(
            symbol="600519", sector_name="白酒", family_name="消费",
            entry_date=date(2026, 10, 20), entry_price=1500.0, source="got_buy",
            catalyst_event_id="evt-1", is_open=True,
        ))
        session.commit()

    portfolio = {
        "accounts": [
            {"positions": [
                {"symbol": "600519", "name": "贵州茅台", "quantity": 100,
                 "avg_cost": 1500.0, "last_price": 1600.0, "unrealized_pnl_pct": 6.7},
                {"symbol": "000001", "name": "平安银行", "quantity": 200,
                 "avg_cost": 10.0, "last_price": 9.5, "unrealized_pnl_pct": -5.0},
            ]},
            {"positions": [
                {"symbol": "600519", "name": "贵州茅台", "quantity": 100},
            ]},
        ],
    }
    ctx = build_holdings_context(as_of=date(2026, 10, 27), portfolio=portfolio)

    assert ctx.status == "normal"
    assert ctx.symbols == ["600519", "000001"]        # 全账户去重
    assert ctx.sector_wishes == ["白酒"]
    assert ctx.catalyst_ids == ["evt-1"]
    assert ctx.entry_source_counts == {"thesis": 1, "ambush": 0, "none": 1}
    by_symbol = {p.symbol: p for p in ctx.positions}
    assert by_symbol["600519"].sector_name == "白酒"
    assert by_symbol["000001"].entry_source == "none"


def test_build_holdings_context_empty_accounts():
    from src.got.holdings_context import build_holdings_context

    ctx = build_holdings_context(portfolio={"accounts": []})
    assert ctx.status == "empty"
    assert ctx.positions == []


def test_build_holdings_context_marks_ambush_entry_source(isolated_db):
    from src.got.holdings_context import build_holdings_context

    with isolated_db.get_session() as session:
        session.add(AmbushPositionSnapshotRecord(
            symbol="300750", sector_name="锂电", category_tag="drift",
            entry_date=date(2026, 10, 22), entry_price=200.0, is_open=True,
        ))
        session.commit()

    ctx = build_holdings_context(
        portfolio={"accounts": [{"positions": [{"symbol": "300750", "quantity": 100}]}]}
    )
    assert ctx.entry_source_counts["ambush"] == 1
    assert ctx.sector_wishes == []   # 无 thesis → 无板块诉求


def test_match_board_code_resolution_levels():
    from src.got.nodes.sector_analysis import SectorAnalysisNode

    mapping = {
        "半导体": "880001",
        "半导体概念": "880001b",
        "光伏设备": "880002",
        "银行": "880003",
        "银行保险": "880004",
    }
    assert SectorAnalysisNode._match_board_code("半导体", mapping) == "880001"
    assert SectorAnalysisNode._match_board_code("半导体概念", mapping) == "880001b"
    assert SectorAnalysisNode._match_board_code("光伏", {"光伏概念": "880002"}) == "880002"
    # 子串命中多个 → 放弃（不猜）
    assert SectorAnalysisNode._match_board_code("银行", {"银行A": "1", "银行B": "2"}) is None
    assert SectorAnalysisNode._match_board_code("茅台", mapping) is None


def test_build_holdings_flow_index_alias_match_and_miss():
    from src.got.nodes.capital_analysis import CapitalAnalysisNode

    holdings = HoldingsContext(
        sector_wishes=["白酒", "半导体", "银行"],
        positions=[HoldingBrief(symbol="600519")],
    )
    evidence = {
        "sector_flow_all": {"白酒": 10.0, "半导体概念": 5.0},
        "sector_flow_all_3d": {"白酒": 8.0, "半导体概念": 4.0},
        "sector_flow_all_5d": {"白酒": 6.0},
    }
    index = CapitalAnalysisNode._build_holdings_flow_index(evidence, holdings)

    assert index["hit_count"] == 2
    assert index["index"]["白酒"]["matched_key"] == "白酒"
    assert index["index"]["半导体"]["matched_key"] == "半导体概念"   # 别名变体
    assert index["index"]["半导体"]["flow"] == 5.0
    assert index["index"]["银行"]["matched"] is False
    assert index["miss"] == ["银行"]


def test_build_holdings_flow_index_none_without_wishes():
    from src.got.nodes.capital_analysis import CapitalAnalysisNode

    assert CapitalAnalysisNode._build_holdings_flow_index({}, None) is None
    assert CapitalAnalysisNode._build_holdings_flow_index({}, HoldingsContext()) is None


def _sector_detail(name: str, strength: float = 66.0, phase: str = "acceleration") -> SectorDetail:
    from src.got.schemas import LifecycleInfo

    return SectorDetail(
        code="880999",
        name=name,
        state="neutral",
        strength_score=strength,
        lifecycle=LifecycleInfo(phase=phase, days_since_launch=9),
    )


def test_build_holdings_intel_isolated_from_confirmed():
    from src.got.schemas import SectorIntel
    from src.got.nodes.aggregator import AggregatorNode

    node = AggregatorNode(use_mock=True)
    sector = SectorContext(
        holdings_sectors=[_sector_detail("白酒"), _sector_detail("半导体")],
        strength_lookup={"白酒": 66.0, "半导体": 66.0, "银行": 52.0},
    )
    confirmed = [SectorIntel(sector_name="半导体", sector_role="mainline", classification="actionable")]
    holdings = HoldingsContext(sector_wishes=["白酒", "半导体", "银行"])

    intel = node._build_holdings_intel(sector, confirmed, holdings)
    names = {i.sector_name for i in intel}

    # 已被 confirmed 覆盖的半导体不重复；白酒来自 holdings_sectors；银行只有强度
    assert names == {"白酒", "银行"}
    for item in intel:
        assert item.sector_role == "holding"
        assert item.classification == "watch"
    by_name = {i.sector_name: i for i in intel}
    assert by_name["白酒"].lifecycle.phase == "acceleration"
    assert by_name["白酒"].board_state == "neutral"
    assert by_name["银行"].lifecycle is None
    assert by_name["银行"].confidence == 0.3


def test_build_holdings_intel_without_wishes_returns_empty():
    from src.got.nodes.aggregator import AggregatorNode

    node = AggregatorNode(use_mock=True)
    assert node._build_holdings_intel(SectorContext(), [], None) == []


def test_sector_intel_map_uses_holdings_intel_but_confirmed_wins():
    from src.got.schemas import SectorIntel, UnifiedContext
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode

    node = PortfolioDecisionNode(use_mock=True)
    unified = UnifiedContext(
        status="normal", unified_signal="neutral", overall_confidence=0.5,
        final_position_limit=0.5,
        confirmed_sectors=[
            SectorIntel(sector_name="半导体", sector_role="mainline", classification="actionable"),
        ],
        holdings_intel=[
            SectorIntel(sector_name="白酒", sector_role="holding", classification="watch",
                        lifecycle=None),
            SectorIntel(sector_name="半导体", sector_role="holding", classification="watch"),
        ],
    )
    intel_map = node._build_sector_intel_map(unified)
    assert intel_map["白酒"]["sector_role"] == "holding"
    # 重叠键以 confirmed 为准（分类口径不被持仓中性档篡改）
    assert intel_map["半导体"]["sector_role"] == "mainline"
    assert intel_map["半导体"]["classification"] == "actionable"


def test_adjust_score_skips_holding_role():
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode

    node = PortfolioDecisionNode(use_mock=True)
    intel_map = {
        "白酒": {
            "classification": "watch", "sector_role": "holding",
            "capital_check": "divergent", "news_check": "negative", "sentiment_check": "missing",
        }
    }
    # 未验证维度不得加减分 → 原值返回
    assert node._adjust_score_by_sector_intel(70.0, "白酒", intel_map) == 70.0


# =============================================================================
# H3：lookup_decision_history
# =============================================================================

def _add_plan(session, symbol: str, plan_date: date, status: str) -> None:
    session.add(TradePlanRecord(
        plan_date=plan_date, symbol=symbol, name="X", lane="mainline",
        action="buy", target_quantity=100.0, target_value=10000.0,
        execution_status=status,
    ))


def test_lookup_decision_history_joins_three_tables(isolated_db):
    as_of = date(2026, 10, 27)
    with isolated_db.get_session() as session:
        session.add(HoldingThesisRecord(
            symbol="600519", sector_name="白酒", family_name="消费",
            entry_date=date(2026, 10, 20), entry_price=1500.0, source="got_buy",
            composite_at_entry=72.0, catalyst_event_id="evt-9", is_open=True,
            lifecycle_state="intact",
        ))
        session.add(CatalystEventRecord(
            event_id="evt-9", event_date=date(2026, 10, 21), event_type="earnings",
            title="季报", sector="白酒", source="llm_narrative",
            expires_at=date(2026, 10, 24),
        ))
        _add_plan(session, "600519", date(2026, 10, 23), "executed")
        _add_plan(session, "600519", date(2026, 10, 24), "partial")
        _add_plan(session, "600519", date(2026, 10, 25), "expired")
        session.commit()

    history = lookup_decision_history(["600519"], as_of=as_of)

    thesis = history["600519"]["thesis"]
    assert thesis["sector_name"] == "白酒"
    assert thesis["composite_at_entry"] == 72.0
    assert thesis["lifecycle_state"] == "intact"

    catalyst = history["600519"]["catalyst"]
    assert catalyst["event_id"] == "evt-9"
    assert catalyst["status"] == "expired"      # expires_at < as_of

    stats = history["600519"]["plan_stats"]
    assert stats["sampled"] == 3
    assert stats["executed"] == 1 and stats["partial"] == 1 and stats["expired"] == 1
    # (1 + 0.5*1) / 3
    assert stats["executed_ratio"] == 0.5

    shadow = build_history_shadow(history, mode="shadow")
    assert shadow["rows"][0]["catalyst_status"] == "expired"
    assert shadow["rows"][0]["executed_ratio"] == 0.5


def test_lookup_decision_history_small_sample_has_no_ratio(isolated_db):
    with isolated_db.get_session() as session:
        _add_plan(session, "600519", date(2026, 10, 23), "executed")
        _add_plan(session, "600519", date(2026, 10, 24), "executed")
        session.commit()

    stats = lookup_decision_history(["600519"], as_of=date(2026, 10, 27))["600519"]["plan_stats"]
    assert stats["sampled"] == 2
    assert stats["executed_ratio"] is None
    assert "样本" in stats["note"]


def test_lookup_decision_history_catalyst_status_today_and_upcoming(isolated_db):
    as_of = date(2026, 10, 27)
    with isolated_db.get_session() as session:
        session.add(HoldingThesisRecord(
            symbol="000001", sector_name="银行", family_name="金融",
            entry_date=date(2026, 10, 20), entry_price=10.0, source="got_buy",
            catalyst_event_id="evt-today", is_open=True,
        ))
        session.add(HoldingThesisRecord(
            symbol="000002", sector_name="地产", family_name="地产",
            entry_date=date(2026, 10, 20), entry_price=10.0, source="got_buy",
            catalyst_event_id="evt-future", is_open=True,
        ))
        session.add(CatalystEventRecord(
            event_id="evt-today", event_date=as_of, event_type="macro",
            title="LPR", sector="银行", source="macro_rule",
            expires_at=as_of + timedelta(days=3),
        ))
        session.add(CatalystEventRecord(
            event_id="evt-future", event_date=as_of + timedelta(days=2),
            event_type="macro", title="PMI", sector="地产", source="macro_rule",
            expires_at=as_of + timedelta(days=5),
        ))
        session.commit()

    history = lookup_decision_history(["000001", "000002"], as_of=as_of)
    assert history["000001"]["catalyst"]["status"] == "today"
    assert history["000002"]["catalyst"]["status"] == "upcoming"


def test_lookup_decision_history_ambush_snapshot(isolated_db):
    with isolated_db.get_session() as session:
        session.add(AmbushPositionSnapshotRecord(
            symbol="300750", sector_name="锂电", category_tag="drift",
            catalyst_event_id="evt-x", entry_date=date(2026, 10, 22),
            entry_price=200.0, is_open=True,
        ))
        session.commit()

    row = lookup_decision_history(["300750"], as_of=date(2026, 10, 27))["300750"]
    assert row["ambush_snap"]["category_tag"] == "drift"
    assert row["thesis"] is None


def test_lookup_decision_history_empty_symbols():
    assert lookup_decision_history([], as_of=date(2026, 10, 27)) == {}


def test_holdings_switches_default_enabled():
    """2026-10-27 用户裁决：跳过影子纪律直接转正（缺省 enabled），开关保留为 kill-switch。"""
    from src.got import catalyst_config

    assert catalyst_config.GOT_HOLDINGS_VERDICT_V2 in ("off", "shadow", "enabled")
    assert catalyst_config.GOT_HOLDINGS_HISTORY_FEED in ("off", "shadow", "enabled")
    assert catalyst_config.GOT_HOLDINGS_VERDICT_V2 == "enabled"
    assert catalyst_config.GOT_HOLDINGS_HISTORY_FEED == "enabled"
