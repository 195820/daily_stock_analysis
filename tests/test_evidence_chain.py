# -*- coding: utf-8 -*-
"""批次 A（竞品补强）证据链与出处单测（一期只降不升，零决策变化）。

覆盖计划 §A4 要点：
- source 字面量映射表键 == schemas.CatalystEvent.source 声明枚举（防"接无源枚举=死代码" + 防漏配）
- 按 source 静态映射 tier（macro/akshare→confirmed，global/narrative→reported）
- 乘数一期零变化：confirmed == reported == 1.0
- 防倒挂档位序：rumor < reported（为二期预留）
- _compute_catalyst_score 对 rumor 事件按乘数降权、confirmed/reported 不变
"""
from __future__ import annotations

from datetime import date

from src.got.schemas import CatalystEvent
from src.got.catalyst_config import (
    CATALYST_SOURCE_TIER,
    CATALYST_TIER_ORDER,
    EVIDENCE_TIER_MULTIPLIER,
)
from src.got.catalyst_calendar import resolve_macro_fanout_sectors
from src.got.nodes.aggregator import AggregatorNode

# schemas.CatalystEvent.source 声明的完整枚举（与字段 description 逐字对齐）
DECLARED_SOURCES = {"akshare_earnings", "akshare_lockup", "macro_rule", "llm_narrative", "global_map"}


def _ev(source: str, tier: str, *, impact: int = 3, conf: float = 1.0) -> CatalystEvent:
    return CatalystEvent(
        event_id=f"{source}-1",
        event_date=date(2026, 10, 11),
        event_type="macro" if source == "macro_rule" else "global",
        title="测试事件",
        sector="半导体",
        impact_level=impact,
        confidence=conf,
        source=source,
        evidence_tier=tier,
    )


def test_map_keys_equal_declared_sources():
    """映射表键必须与生产声明的 source 枚举完全一致——既防无源死键，也防漏配走 fallback。"""
    assert set(CATALYST_SOURCE_TIER.keys()) == DECLARED_SOURCES


def test_tier_resolution_by_source():
    assert CATALYST_SOURCE_TIER["macro_rule"] == "confirmed"
    assert CATALYST_SOURCE_TIER["akshare_earnings"] == "confirmed"
    assert CATALYST_SOURCE_TIER["akshare_lockup"] == "confirmed"
    assert CATALYST_SOURCE_TIER["global_map"] == "reported"
    assert CATALYST_SOURCE_TIER["llm_narrative"] == "reported"


def test_multiplier_phase1_zero_change():
    """一期只降不升：confirmed 与 reported 乘数均为 1.0（主力事件不换池）。"""
    assert EVIDENCE_TIER_MULTIPLIER["confirmed"] == 1.0
    assert EVIDENCE_TIER_MULTIPLIER["reported"] == 1.0


def test_tier_order_no_inversion():
    """防倒挂：rumor 乘数 < reported==confirmed（档位序 confirmed>reported>rumor 在乘数上单调）。"""
    assert EVIDENCE_TIER_MULTIPLIER["rumor"] < EVIDENCE_TIER_MULTIPLIER["reported"]
    assert CATALYST_TIER_ORDER == ("confirmed", "reported", "rumor")


def test_compute_score_rumor_discounted():
    """同事件，rumor 相对 reported 按乘数降权；confirmed 与 reported 得分一致（一期零变化）。"""
    today = date(2026, 10, 11)
    _, _, _, _ = AggregatorNode._compute_catalyst_score([_ev("macro_rule", "reported")], today)
    reported = AggregatorNode._compute_catalyst_score([_ev("macro_rule", "reported")], today)[0]
    confirmed = AggregatorNode._compute_catalyst_score([_ev("macro_rule", "confirmed")], today)[0]
    rumor = AggregatorNode._compute_catalyst_score([_ev("llm_narrative", "rumor")], today)[0]

    assert confirmed == reported  # 一期 both ×1.0
    assert rumor == round(reported * EVIDENCE_TIER_MULTIPLIER["rumor"], 3) or rumor < reported


def test_compute_score_empty_events():
    score, top, tag, titles = AggregatorNode._compute_catalyst_score([], date(2026, 10, 11))
    assert score == 0.0 and top is None


def test_macro_fanout_gate_off_preserves_legacy():
    """W1：GOT_MACRO_FANOUT_ENABLED=false 时仅旧白名单板块扇出（零决策面变更）。"""
    concept_set = {"券商", "银行", "房地产", "有色金属", "白酒"}
    raw = ["券商", "周期", "有色金属", "白酒"]  # 周期为伪类目，不在 concept_set
    off = resolve_macro_fanout_sectors(raw, concept_set, fanout_enabled=False)
    assert off == ["券商"]  # 有色/白酒被门控排除，行为同项一前
    on = resolve_macro_fanout_sectors(raw, concept_set, fanout_enabled=True)
    assert on == ["券商", "有色金属", "白酒"]  # 启用后扩扇出，周期仍因不命中被丢弃
