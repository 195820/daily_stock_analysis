# -*- coding: utf-8 -*-
"""Consolidation behavior tests: shared relations, backfill, event identity."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

from src.got.glue_relations import apply_glue_relations, relation_signal_map
from src.got.nodes.aggregator import AggregatorNode
from src.got.nodes.news_analysis import NewsAnalysisNode
from src.got.schemas import (
    CatalystEvent,
    CatalystCalendar,
    GlueRelation,
    NewsContext,
    SemanticGlueResult,
    SentimentContext,
)


def _major_event(title, event_id=None):
    return SimpleNamespace(
        event_id=event_id, title=title, summary=title,
        impact_level="high", sentiment="positive",
        affected_sectors=["半导体"], affected_stocks=[],
    )


def _candidates():
    detail = SimpleNamespace(code="TD_半导体", strength_score=70.0)
    return [("半导体", detail, {"role": "mainline", "tier": "A"})]


def test_major_event_id_prefers_persisted_identity():
    assert AggregatorNode._major_event_id(_major_event("x", "abc123")) == "abc123"
    fallback = AggregatorNode._major_event_id(_major_event("x"))
    assert fallback.startswith("major_")


def test_news_id_is_stable_and_rescue_independent():
    node = NewsAnalysisNode(use_mock=True)
    as_of = date(2026, 10, 29)
    first = node._generate_event_id("标题 A", "cls", as_of)
    second = node._generate_event_id("标题  A", "cls", as_of)
    assert first == second  # normalize_title 去空白
    assert first != node._generate_event_id("标题 A", "cls", date(2026, 10, 30))

    events = [
        SimpleNamespace(event_id=None, title="事件一", source_type="cls"),
        SimpleNamespace(event_id="keep", title="事件二", source_type="cls"),
    ]
    node._assign_stable_event_ids(events, as_of)
    assert events[0].event_id == node._generate_event_id("事件一", "cls", as_of)
    assert events[1].event_id == "keep"


def test_validate_glue_superset_and_closure():
    node = AggregatorNode(use_mock=True)
    parsed = {
        "evidence_ids": ["ev_a"],
        "relations": [{
            "event_id": "e1", "board_code": "TD_半导体", "strength": 0.8,
            "direction": "negative", "propagation_path": ["设备"],
            "confidence": 0.9, "evidence_ids": ["ev_a"],
        }],
        "strengths": [{
            "board_code": "TD_半导体", "strength": 0.7, "confidence": 0.8,
            "evidence": "资金流入", "evidence_ids": ["ev_a"],
        }],
        "families": [],
        "concept_suggestions": [{"concept_name": "具身智能", "evidence": "两源", "mention_frequency": 2}],
        "key_risks": ["海外需求下修"],
        "hot_topics": ["具身智能"],
        "narrative": "半导体强，风险在外需",
        "cross_sector_risks": ["风格切换"],
    }
    result = node._validate_glue(parsed, _candidates(), allowed_evidence_ids={"ev_a"})
    assert result.relations[0].direction == "negative"
    assert result.relations[0].evidence_ids == ["ev_a"]
    assert result.concept_suggestions[0]["concept_name"] == "具身智能"
    assert result.key_risks == ["海外需求下修"]
    assert result.cross_sector_risks == ["风格切换"]
    assert result.hot_topics == ["具身智能"]
    assert result.narrative

    forged = dict(parsed)
    forged["relations"] = [dict(parsed["relations"][0], evidence_ids=["ev_fake"])]
    try:
        node._validate_glue(forged, _candidates(), allowed_evidence_ids={"ev_a"})
    except ValueError as exc:
        assert "证据闭包" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("伪造 evidence_id 必须失败")


def test_apply_glue_relations_keeps_direction_and_unmapped():
    event = CatalystEvent(
        event_id="e1", event_date=date(2026, 10, 30), event_type="narrative",
        title="t", sector="半导体", source="llm_narrative",
        affected_sectors=["半导体"],
    )
    other = CatalystEvent(
        event_id="e2", event_date=date(2026, 10, 30), event_type="narrative",
        title="t2", sector="银行", source="global_map", affected_sectors=["银行"],
    )
    macro = CatalystEvent(
        event_id="m1", event_date=date(2026, 10, 30), event_type="macro",
        title="PMI", sector="有色", source="macro_rule", affected_sectors=["有色"],
    )
    news = NewsContext(
        overall_impact="neutral", impact_score=0.0,
        catalyst_calendar=CatalystCalendar(target_date=date(2026, 10, 29), events=[event, other, macro]),
    )
    glue = SemanticGlueResult(relations=[
        GlueRelation(event_id="e1", board_code="TD_半导体", strength=0.7,
                     direction="negative", propagation_path=["x"], confidence=0.8),
        GlueRelation(event_id="e2", board_code="TD_未知", strength=0.9,
                     direction="positive", propagation_path=["y"], confidence=0.9),
    ])
    stats = apply_glue_relations(news, glue, _candidates(), replay=True)
    assert event.affected_sectors == ["半导体"]
    assert event.direction == "positive"  # A6 不改事件方向语义
    assert other.affected_sectors == ["银行"]  # 无合法 relation → 保留原扇出
    assert macro.affected_sectors == ["有色"]
    assert stats["unmapped_relations"] == 1


def test_apply_glue_relations_writes_mapped_sector():
    event = CatalystEvent(
        event_id="e1", event_date=date(2026, 10, 30), event_type="narrative",
        title="t", sector="半导体", source="llm_narrative", affected_sectors=[],
    )
    news = NewsContext(
        overall_impact="neutral", impact_score=0.0,
        catalyst_calendar=CatalystCalendar(target_date=date(2026, 10, 29), events=[event]),
    )
    glue = SemanticGlueResult(relations=[
        GlueRelation(event_id="e1", board_code="TD_半导体", strength=0.7,
                     propagation_path=["x"], confidence=0.8),
    ])
    apply_glue_relations(news, glue, _candidates(), replay=True)
    assert event.affected_sectors == ["半导体"]


def test_node4_analysis_has_no_concept_or_risk_llm(monkeypatch):
    node = NewsAnalysisNode(use_mock=True)
    calls = []

    def fake_call(prompt, *, temperature, max_tokens, task_id="node4.llm", max_retries=3):
        calls.append(task_id)
        if task_id == "node4.title_shortlist":
            return json.dumps({"selected": [{"index": 1}]})
        return json.dumps({"events": []})

    monkeypatch.setattr(node, "_call_llm", fake_call)
    asyncio.run(node._analyze_with_llm([{"title": "news", "snippet": "s"}]))
    assert calls == ["node4.title_shortlist", "node4.event_extraction"]
    assert not any("concept" in c or "risk" in c for c in calls)


def test_backfill_semantic_aggregates():
    node = AggregatorNode(use_mock=True)
    news = NewsContext(overall_impact="positive", impact_score=0.3)
    sentiment = SentimentContext(
        overall_sentiment="neutral", sentiment_score=0.0, sentiment_stage="neutral",
    )
    glue = SemanticGlueResult(
        concept_suggestions=[{"concept_name": "具身智能"}],
        key_risks=["外需下修"],
        hot_topics=["具身智能"],
    )
    node._backfill_semantic_aggregates(news, sentiment, glue)
    assert news.concept_suggestions[0]["concept_name"] == "具身智能"
    assert news.key_risks == ["外需下修"]
    assert sentiment.hot_topics == ["具身智能"]
    assert "具身智能" in news.reasoning


def test_logical_narrative_dedupe_keeps_earliest():
    from src.got.catalyst_calendar import CatalystCalendarBuilder

    builder = CatalystCalendarBuilder()
    base = date(2026, 10, 29)
    e1 = CatalystEvent(
        event_id="a", event_date=base, event_type="narrative", title="t1",
        sector="半导体", source="llm_narrative", source_news="同一新闻",
    )
    e2 = CatalystEvent(
        event_id="b", event_date=date(2026, 10, 30), event_type="narrative", title="t1",
        sector="半导体", source="llm_narrative", source_news="同一新闻",
    )
    other = CatalystEvent(
        event_id="c", event_date=date(2026, 10, 30), event_type="narrative", title="t2",
        sector="半导体", source="llm_narrative", source_news="另一新闻",
    )
    kept = builder._dedupe_logical_narrative_events([e1, e2, other], base)
    assert [e.event_date for e in kept if e.event_id in ("a", "b")] == [base]
    assert any(e.event_id == "c" for e in kept)


def test_macro_alignment_keeps_deterministic_fanout():
    from src.got.catalyst_calendar import CatalystCalendarBuilder

    builder = CatalystCalendarBuilder()
    macro = CatalystEvent(
        event_id="m", event_date=date(2026, 10, 30), event_type="macro",
        title="PMI", sector="有色", source="macro_rule", affected_sectors=["有色"],
    )
    narrative = CatalystEvent(
        event_id="n", event_date=date(2026, 10, 30), event_type="narrative",
        title="n", sector="半导体", source="llm_narrative", affected_sectors=[],
    )
    result = builder._align_macro_rule_events([macro, narrative], {"有色", "贵金属"})
    assert "有色" in macro.affected_sectors
    assert narrative.affected_sectors == []  # 叙事扇出归 A6，不在此处解释


def test_screener_uses_relations_and_flags_missing(monkeypatch):
    from src.services.stock_screener import StockScreenerPipeline

    pipeline = StockScreenerPipeline.__new__(StockScreenerPipeline)
    monkeypatch.setattr(pipeline, "_get_all_sector_names", lambda ctx: {"半导体", "银行", "煤炭"})
    news = {"major_events": [{"title": "t", "affected_sectors": ["煤炭"], "sentiment": "positive", "impact_level": "high"}]}
    got = {"semantic_relations": {
        "半导体": {"signal": 0.5}, "银行": {"signal": -0.4},
    }}
    candidates = [
        {"code": "1", "sector": "半导体"},
        {"code": "2", "sector": "银行"},
        {"code": "3", "sector": "煤炭"},
    ]
    scores, avail = pipeline._score_news(candidates, news, {}, got_context=got)
    assert scores["1"] == 70.0
    assert scores["2"] == 16.0
    assert scores["3"] == 100.0  # fallback 子串匹配仍给分
    assert avail["1"] is True and avail["2"] is True
    assert avail["3"] is False
    assert candidates[2]["news_relation_missing"] is True


def test_reasoning_renders_a6_shared_semantics():
    from src.got.archive import _render_a6_glue_formal

    glue = {
        "status": "ok",
        "strengths": [{"board_code": "880471", "strength": 0.78, "news_read": "medium",
                        "capital_read": "high", "sentiment_read": "high"}],
        "relations": [{
            "event_id": "e1", "board_code": "880471", "direction": "neutral",
            "strength": 0.45, "confidence": 0.55,
            "propagation_path": ["金融法", "银行"], "evidence_ids": ["ev_1", "ev_2"],
        }],
        "families": [{"board_code": "880471", "family": "金融"}],
        "narrative": "银行资金面最强",
        "concept_suggestions": [{"concept_name": "算力用电", "evidence": "能源局", "mention_frequency": 3}],
        "key_risks": ["合规成本上升"],
        "cross_sector_risks": ["题材持续性风险"],
        "hot_topics": ["算力用电"],
    }
    lines = _render_a6_glue_formal(
        glue, glue_consumed=True, glue_status="ok",
        code_to_name={"880471": "银行"}, event_titles={"e1": "金融法草案二审"},
    )
    text = "\n".join(lines)
    assert "A6 事件→板块关系" in text and "金融法草案二审" in text
    assert "9/9" not in text and "1/1 条 relation 带 evidence_ids" in text
    assert "A6 共享语义产物" in text and "算力用电" in text
    assert "合规成本上升" in text and "题材持续性风险" in text
