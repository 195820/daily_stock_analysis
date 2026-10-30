# -*- coding: utf-8 -*-
"""Narrow semantic agents: identity / move attribution / read-only constraints."""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from src.got.agent_runtime import GOTAgentRuntime
from src.got.narrative_agents import (
    AttributionItem,
    NarrativeContext,
    run_identity_agent,
    run_move_attribution,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode

    monkeypatch.setattr(
        PortfolioDecisionNode, "_build_stock_sector_map", lambda self, ctx: None,
    )
    monkeypatch.setattr("src.got.narrative_agents._daily_return_pct", lambda symbol: None)


class _News:
    def __init__(self, events):
        self.major_events = events


def _event(title, sectors=("汽车零部件",)):
    return SimpleNamespace(
        event_id=f"e-{title}", title=title, summary=title,
        affected_sectors=list(sectors), affected_stocks=["002009"],
        sentiment="positive", impact_level="high",
    )


def _sector_ctx():
    detail = SimpleNamespace(name="汽车零部件", code="TD_汽车零部件", strength_score=70.0)
    return SimpleNamespace(
        active_mainline=[SimpleNamespace(group_name="g", members=[detail])],
        ambush=[], exhausted_mainline=[], defensive_rotation=[], industry_top=[],
    )


def _runtime_with(output_builder):
    rt = GOTAgentRuntime(date(2026, 10, 29), adapter=object(), archive_root=None, replay=True)

    async def fake_call_json(**kwargs):
        refs = kwargs.get("evidence_refs") or []
        payload = output_builder(refs)
        return SimpleNamespace(
            status="ok", output=payload, step_id=f"{kwargs['task_id']}:001",
            error=None, task_id=kwargs["task_id"],
        )

    rt.call_json = fake_call_json
    return rt


@pytest.mark.asyncio
async def test_identity_002009_conflicting_themes_coexist():
    def builder(refs):
        news_id = next(r.evidence_id for r in refs if r.kind == "news")
        market_id = next(r.evidence_id for r in refs if r.kind == "market")
        return [
            {
                "symbol": "002009", "theme": "具身智能/物理AI", "layer": "software_model",
                "confidence": 0.8, "novelty": True,
                "evidence_ids": [news_id, market_id],
                "conflicts": ["与动力电池身份并存"], "abstain": False,
            },
            {
                "symbol": "002009", "theme": "动力电池", "layer": "hardware",
                "confidence": 0.6, "novelty": False,
                "evidence_ids": [market_id], "conflicts": [], "abstain": False,
            },
        ]

    rt = _runtime_with(builder)
    ctx = await run_identity_agent(
        [{"symbol": "002009", "sector": "汽车零部件"}],
        _sector_ctx(), _News([_event("天奇股份布局具身智能")]),
        [], rt, date(2026, 10, 29),
    )
    assert ctx.status == "ok"
    themes = {i.theme for i in ctx.identities}
    assert "具身智能/物理AI" in themes and "动力电池" in themes
    embodied = next(i for i in ctx.identities if i.theme == "具身智能/物理AI")
    assert embodied.layer == "software_model"
    assert embodied.novelty is True
    assert embodied.conflicts


@pytest.mark.asyncio
async def test_identity_single_source_novelty_downgraded_and_missing_evidence_abstains():
    def builder(refs):
        news_id = next(r.evidence_id for r in refs if r.kind == "news")
        return [
            {
                "symbol": "002009", "theme": "具身智能", "layer": "software_model",
                "confidence": 0.7, "novelty": True,
                "evidence_ids": [news_id], "conflicts": [], "abstain": False,
            },
        ]

    rt = _runtime_with(builder)
    ctx = await run_identity_agent(
        [{"symbol": "002009", "sector": "汽车零部件"}, {"symbol": "600000", "sector": ""}],
        _sector_ctx(), _News([_event("新闻A")]),
        [], rt, date(2026, 10, 29),
    )
    item = next(i for i in ctx.identities if i.symbol == "002009")
    assert item.novelty is False, "单来源证据不得标 novelty"
    missing = next(i for i in ctx.identities if i.symbol == "600000")
    assert missing.abstain is True


@pytest.mark.asyncio
async def test_attribution_without_evidence_or_return_is_unexplained():
    def builder(refs):
        return [
            {"symbol": "600000", "bucket": "sector_beta", "theme": "银行",
             "confidence": 0.8, "evidence_ids": [], "residual": 0.2},
        ]

    rt = _runtime_with(builder)
    ctx = await run_move_attribution(
        [{"symbol": "600000", "sector": "银行"}],
        SimpleNamespace(), _News([]), SimpleNamespace(), SimpleNamespace(),
        {}, rt, date(2026, 10, 29), capital=None,
    )
    item = ctx.attributions[0]
    assert item.bucket == "unexplained"
    assert item.evidence_ids == []
    assert item.return_pct is None


def test_t1h_prompt_contains_same_attribution():
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode

    node = PortfolioDecisionNode()
    node._move_attributions = {
        "600000": AttributionItem(
            symbol="600000", date="2026-10-29", return_pct=1.2,
            bucket="sector_beta", theme="银行", confidence=0.7,
            evidence_ids=["ev_1"], residual=0.1,
        )
    }
    holding = SimpleNamespace(
        symbol="600000", name="浦发银行", sector_name="银行",
        composite_score=66.0, lifecycle_phase="mature", lifecycle_day=3,
        ambush_position_type=None, ambush_exit_signal=None, ambush_exit_reason=None,
    )
    prompt = node._build_holding_review_prompt(
        holding, "bull", SimpleNamespace(major_events=[]), {"positions": []},
    )
    assert "sector_beta" in prompt and "ev_1" in prompt
    # 归因只读：不改评分/止损/分配
    assert holding.composite_score == 66.0


def test_replay_memory_writes_zero(tmp_path, monkeypatch):
    from src.got.semantic_memory import SemanticMemory

    memory = SemanticMemory(date(2026, 10, 29), replay=True)
    identity = SimpleNamespace(
        symbol="600000", theme="银行", layer="other", confidence=0.5,
        price_beta=None, novelty=False, evidence_ids=[],
    )
    attr = AttributionItem(symbol="600000", bucket="unexplained")
    assert memory.record_identities([identity]) == 0
    assert memory.record_attributions([attr]) == 0
    assert memory.record_realizations()["written"] == 0
