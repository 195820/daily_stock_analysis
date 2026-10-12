# -*- coding: utf-8 -*-
"""批次 A（竞品补强）新闻侧出处接地单测。

覆盖三步确定性回连（禁 LLM 造 URL）：
- _format_news_for_prompt 返回 (text, ordered_items)，序号 1..N 与 ordered_items 严格对齐
- 素材行含 [#idx | published_date] 标记
- _parse_events 按 source_index 回填真实 url/timestamp；越界/缺失→None
- **EVENT_EXTRACTION_PROMPT 两态均可 .format()**（生产链路直接调用它；2026-10-12
  曾因示例 JSON 收尾大括号少转义一层，off 态也当场抛 ValueError 崩掉整条 Phase 1）
"""
from __future__ import annotations

import json

from src.got.nodes.news_analysis import EVENT_EXTRACTION_PROMPT, NewsAnalysisNode


def _items():
    return [
        {"title": "新闻甲", "snippet": "s1", "source": "cls", "source_type": "cls",
         "url": "https://cls/a1", "published_date": "2026-10-11 09:00:00", "level": "A"},
        {"title": "新闻乙", "snippet": "s2", "source": "sina", "source_type": "sina",
         "url": "https://sina/b2", "published_date": "2026-10-11 10:30:00"},
        {"title": "新闻丙", "snippet": "s3", "source": "caixin", "source_type": "caixin",
         "url": "", "published_date": ""},
    ]


def test_format_returns_aligned_items():
    text, ordered = NewsAnalysisNode._format_news_for_prompt(_items(), gated=True)
    assert len(ordered) == 3
    assert "[#1" in text and "[#2" in text and "[#3" in text
    # 发布时间进入素材行（供时效判断）
    assert "2026-10-11 09:00:00" in text
    # 序号 1-based 对齐：ordered[idx-1] 即该行对应的原始 item
    assert ordered[0]["url"] == "https://cls/a1"
    assert ordered[2]["url"] == ""


def test_format_off_is_original_no_markers():
    # gated=False（缺省）：素材行逐字同改造前，无 [#idx] 标记（零 prompt 变更）
    text, ordered = NewsAnalysisNode._format_news_for_prompt(_items())
    assert "[#1" not in text
    assert "1. [财联社-A] 新闻甲" in text
    assert len(ordered) == 3  # 仍返回有序列表，供回连（无 source_index 时回连 None）


def test_parse_events_回连_by_source_index():
    items = _items()
    result = json.dumps({"events": [
        {"title": "事件X", "impact_level": "high", "sentiment": "positive", "source_index": 2},
    ]}, ensure_ascii=False)
    events = NewsAnalysisNode._parse_events(result, items)
    assert len(events) == 1
    # source_index=2 → ordered[1]（sina 乙）
    assert events[0].source_url == "https://sina/b2"
    assert events[0].timestamp == "2026-10-11 10:30:00"


def test_parse_events_out_of_range_index_none():
    items = _items()
    result = json.dumps({"events": [
        {"title": "事件Y", "impact_level": "medium", "sentiment": "neutral", "source_index": 99},
    ]}, ensure_ascii=False)
    ev = NewsAnalysisNode._parse_events(result, items)[0]
    assert ev.source_url is None and ev.timestamp is None


def test_parse_events_no_index_backward_compatible():
    items = _items()
    result = json.dumps({"events": [
        {"title": "事件Z", "impact_level": "medium", "sentiment": "neutral"},
    ]}, ensure_ascii=False)
    ev = NewsAnalysisNode._parse_events(result, items)[0]
    assert ev.source_url is None and ev.timestamp is None


def test_parse_events_empty_url_stays_none():
    """回连到 url 为空的 item（如丙/cls 源无 url）→ None，不写空串伪值。"""
    items = _items()
    result = json.dumps({"events": [
        {"title": "事件W", "impact_level": "low", "sentiment": "neutral", "source_index": 3},
    ]}, ensure_ascii=False)
    ev = NewsAnalysisNode._parse_events(result, items)[0]
    assert ev.source_url is None  # 丙的 url 为空串 → None
    assert ev.timestamp is None


_PROMPT_BASE = dict(concept_list="CL", news_data="ND", historical_context="HC")


def _example_json(rendered: str) -> str:
    return rendered[rendered.index('{"events"'):]


def test_event_prompt_off_state_formats_and_matches_pre_batch():
    """缺省态（GOT_NEWS_EVIDENCE_GATING=false）必须能 format，且渲染逐字同改造前。"""
    rendered = EVENT_EXTRACTION_PROMPT.format(src_index_req="", src_index_example="", **_PROMPT_BASE)
    assert "source_index" not in rendered
    assert _example_json(rendered) == (
        '{"events": [{"title": "...", "summary": "...", "event_type": "...", '
        '"impact_level": "...", "sentiment": "...", "affected_sectors": ["..."], '
        '"affected_stocks": ["..."], "source_type": "professional"}]}'
    )
    json.loads(_example_json(rendered))  # 示例 JSON 自身可解析，不是残缺串


def test_event_prompt_on_state_formats_and_keeps_valid_json():
    """接地态插 source_index 字段后模板仍可 format，示例 JSON 仍解析通过。"""
    rendered = EVENT_EXTRACTION_PROMPT.format(
        src_index_req="\n   - source_index: ...",
        src_index_example=', "source_index": 3',
        **_PROMPT_BASE,
    )
    assert "source_index" in rendered
    json.loads(_example_json(rendered))
