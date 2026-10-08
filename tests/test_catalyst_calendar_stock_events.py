# -*- coding: utf-8 -*-
"""个股级结构化事件（财报披露/限售解禁）不入催化剂日历（2026-10-08 收口）

背景：1008 全流程实测中财报通道「死而复生」（0825–0930 因取数失真恒为零），
7 条 drift 财报挤占 22 格日历，个股扇出板块层与 WI-P2-4 移除解禁的三条理由
逐字同案。本测试把「个股级事件不入历」钉成契约，对齐
test_lockup_risk_events.py 的 online_calendar_lockup_rows == 0 先例。
"""
import asyncio
import inspect
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.catalyst_calendar import CatalystCalendarBuilder  # noqa: E402
from src.got.schemas import CatalystEvent  # noqa: E402

_WINDOW = (date(2026, 10, 8), date(2026, 10, 22))


def _make_event(**kw):
    base = dict(
        event_id="t-1",
        event_date=_WINDOW[0],
        event_type="earnings",
        title="某股(600000) 财报披露",
        sector="半导体",
        impact_level=2,
        direction="drift",
        confidence=0.95,
        source="akshare_earnings",
        category_tag="drift",
    )
    base.update(kw)
    return CatalystEvent(**base)


def _stub_io(builder, sectors=("半导体",), legacy=()):
    """切断全量构建的全部 IO 边：宏观规则桩 + 新闻/全球/叙事/对齐置空。"""
    builder._generate_macro_calendar = lambda s, e: [{
        "event_date": s,
        "title": "测试宏观事件",
        "impact_level": 2,
        "affected_sectors": list(sectors),
        "direction": "binary",
        "category_tag": "binary",
    }]
    builder._load_recent_news_events = lambda days: []
    builder._map_global_events = lambda news, s, e: []
    builder._align_events_to_sectors = lambda evs, uni: asyncio.sleep(0, result=evs)
    builder._load_active_events = lambda start: list(legacy)

    async def _no_narrative(news, s, e):
        return []

    builder._llm_extract_narrative_catalysts = _no_narrative


# ── 在线构建：不产生任何 akshare_*（财报/解禁）事件 ──────────
def test_full_build_emits_no_stock_level_events():
    builder = CatalystCalendarBuilder()
    _stub_io(builder)
    events = asyncio.run(builder._full_build(*_WINDOW))
    assert events, "宏观桩事件应入历（防止整条路径静默归零的假通过）"
    assert all(not e.source.startswith("akshare_") for e in events)
    assert {e.source for e in events} == {"macro_rule"}


def test_full_build_source_contract_solidified():
    """_full_build 源码不得残留个股取数/映射入口（防止复活走回老路）。"""
    src = inspect.getsource(CatalystCalendarBuilder._full_build)
    for gone in ("earnings", "lockup", "_map_structured_event",
                 "_batch_map_stocks", "_aggregate_structured_events"):
        assert gone not in src, f"_full_build 残留 {gone!r}"


# ── 构建器不再暴露个股→板块映射 API（防止旁路复活）─────────
def test_builder_has_no_stock_sector_mapping_api():
    for gone in ("_fetch_earnings_calendar", "_fetch_lockup_calendar",
                 "_map_structured_event", "_map_structured_event_fast",
                 "_build_structured_events", "_map_stock_to_sectors",
                 "_batch_map_stocks", "_fetch_stock_boards",
                 "_aggregate_structured_events", "_get_relevant_quarters"):
        assert not hasattr(CatalystCalendarBuilder, gone), f"{gone} 应已退役"


# ── 全量重建不回填存量个股事件（混合更新语义：存量只随 expires_at 出历）──
def test_full_build_does_not_reload_legacy_stock_events():
    legacy = _make_event(
        expires_at=(_WINDOW[0] + timedelta(days=5)).isoformat(),
    )
    builder = CatalystCalendarBuilder()
    _stub_io(builder, legacy=[legacy])
    events = asyncio.run(builder._full_build(*_WINDOW))
    assert all(e.source != "akshare_earnings" for e in events)


def test_incremental_still_carries_legacy_until_expiry():
    """登记既有语义：增量日 DB 存量财报仍出现直到 expires_at（与 lockup 先例一致），
    验证修复必须看全量重建日。"""
    legacy = _make_event(
        expires_at=(_WINDOW[0] + timedelta(days=5)).isoformat(),
    )
    builder = CatalystCalendarBuilder()
    _stub_io(builder, legacy=[legacy])
    events = asyncio.run(builder._incremental_update(*_WINDOW))
    assert any(e.source == "akshare_earnings" for e in events)
