# -*- coding: utf-8 -*-
"""Node 4 两段式（全量标题 LLM 初筛 + 漏读补读扩围，2026-10-22 起）单测。

覆盖：
1. 粗筛 prompt 覆盖**全部**候选标题（无条数上限、不带 snippet、total 计数正确）
2. LLM 失效不降级：空响应 / JSON 失败 / 零选中 / 全越界 → []（宁可当日无事件，不产错事件）
3. 短名单条数上限（GOT_NEWS_TITLE_SHORTLIST_SIZE）与 LLM 输出顺序
4. 漏读补读：排除已吸收、标题归一化去重、不做热度排序/预截断；含当日更早时点快照行，
   按自然日龄标注（`[今日早前]`/`[昨日]`/`[N日前]`），超 `GOT_NEWS_CARRY_MAX` 按最新优先截断
5. DeepInfer 端到端：昨日落库未吸收 → 进粗筛 prompt → 被选中 → 进 ordered_items 并回连 url
6. 回滚键：GOT_NEWS_TITLE_SHORTLIST_ENABLED=false → 旧口径（weight 前 50 条）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.nodes.news_analysis import NewsAnalysisNode

SHORTLIST_PREFIX = "你是 A 股消息面筛选员"
EXTRACTION_PREFIX = "你是严谨的新闻编辑"


def _item(i: int, **kw) -> dict:
    base = {
        "title": f"候选新闻标题{i:03d}",
        "snippet": f"snippet-body-{i}",
        "source": "sina",
        "source_type": "sina",
        "url": f"https://example.com/{i}",
        "published_date": "",
    }
    base.update(kw)
    return base


def _raw_row(
    title: str, appearance_count: int = 1, fetched_date: date | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        title_original=title,
        source="sina",
        url=f"https://example.com/{title}",
        published_at="2026-10-20 09:00:00",
        appearance_count=appearance_count,
        fetched_date=fetched_date or (date.today() - timedelta(days=1)),
    )


def _event_row(title: str) -> SimpleNamespace:
    return SimpleNamespace(
        category="industry",
        title=title,
        significance_score=0.9,
        lifecycle_state="emerging",
        mention_count=2,
        first_seen="2026-10-20",
        get_sources_seen=lambda: ["sina"],
    )


def _fake_db(raw_rows, event_rows):
    """假 DB：Node4RawNewsRecord 走 order_by/limit，Node4EventRecord 走 filter/all。"""
    fake_session = MagicMock()

    def _query(model):
        query = MagicMock()
        if getattr(model, "__name__", "") == "Node4RawNewsRecord":
            query.filter.return_value.order_by.return_value.all.return_value = raw_rows
            (
                query.filter.return_value.order_by.return_value.limit.return_value
                .all.return_value
            ) = raw_rows
        else:
            # 吸收判定按 Node4EventRecord.title 单列查询（返回 Row 元组）
            query.filter.return_value.all.return_value = [(r.title,) for r in event_rows]
            query.filter.return_value.order_by.return_value.all.return_value = event_rows
        return query

    fake_session.query.side_effect = _query
    fake_db = MagicMock()
    fake_db.get_session.return_value.__enter__.return_value = fake_session
    return fake_db


class TestTitleShortlist(unittest.TestCase):
    def setUp(self):
        self.node = NewsAnalysisNode(use_mock=True)

    def _run(self, raw, llm_output):
        captured = {}

        def fake_llm(prompt, **kwargs):
            captured["prompt"] = prompt
            if isinstance(llm_output, str):
                return llm_output
            return json.dumps(llm_output)

        with patch.object(NewsAnalysisNode, "_call_llm", side_effect=fake_llm):
            picked = self.node._shortlist_titles(raw)
        return picked, captured["prompt"]

    def test_shortlist_sees_all_titles(self):
        raw = [_item(i) for i in range(1, 201)]
        picked, prompt = self._run(
            raw, {"selected": [{"index": 180, "reason": "产业级开源发布"}]},
        )
        self.assertIn("1. 候选新闻标题001", prompt)
        self.assertIn("200. 候选新闻标题200", prompt)
        numbered = [
            ln for ln in prompt.splitlines()
            if ln[:1].isdigit() and ". " in ln
        ]
        self.assertEqual(len(numbered), 200, "粗筛必须看到全部 200 条（无条数上限）")
        self.assertEqual([p["title"] for p in picked], ["候选新闻标题180"])

    def test_shortlist_prompt_has_no_snippet(self):
        raw = [_item(i) for i in range(1, 21)]
        _, prompt = self._run(raw, {"selected": []})
        self.assertNotIn("snippet-body", prompt, "粗筛只读标题，不带摘要")

    def test_shortlist_prompt_states_total_count(self):
        raw = [_item(i) for i in range(1, 201)]
        _, prompt = self._run(raw, {"selected": []})
        self.assertIn("共 200 条", prompt)

    def test_shortlist_llm_failure_returns_empty(self):
        raw = [_item(i) for i in range(1, 6)]
        picked, _ = self._run(raw, "")
        self.assertEqual(picked, [], "空响应不得降级到 weight 前 N")

    def test_shortlist_json_failure_returns_empty(self):
        raw = [_item(i) for i in range(1, 6)]
        picked, _ = self._run(raw, "not json")
        self.assertEqual(picked, [])

    def test_shortlist_zero_selection_returns_empty(self):
        raw = [_item(i) for i in range(1, 6)]
        picked, _ = self._run(raw, {"selected": []})
        self.assertEqual(picked, [])

    def test_shortlist_rejects_out_of_range_index(self):
        raw = [_item(i) for i in range(1, 6)]
        picked, _ = self._run(
            raw,
            {"selected": [
                {"index": 999}, {"index": 0}, {"index": -3},
                {"index": "x"}, {"index": 3},
            ]},
        )
        self.assertEqual([p["title"] for p in picked], ["候选新闻标题003"])

        all_invalid, _ = self._run(
            raw, {"selected": [{"index": 999}, {"index": 0}]},
        )
        self.assertEqual(all_invalid, [])

    def test_shortlist_respects_size_cap(self):
        raw = [_item(i) for i in range(1, 31)]
        with patch("src.got.nodes.news_analysis.GOT_NEWS_TITLE_SHORTLIST_SIZE", 12):
            picked, _ = self._run(
                raw, {"selected": [{"index": i} for i in range(1, 21)]},
            )
        self.assertEqual(
            [p["title"] for p in picked],
            [f"候选新闻标题{i:03d}" for i in range(1, 13)],
            "超上限截断，保持 LLM 输出顺序",
        )


class TestCarryPrevTitles(unittest.TestCase):
    def setUp(self):
        self.node = NewsAnalysisNode(use_mock=True)

    def test_carry_prev_titles_excludes_already_absorbed(self):
        raw_rows = [
            _raw_row("已被事件吸收的标题", appearance_count=9),
            _raw_row("DeepSeek开源推理框架DeepInfer登顶Agent Arena开源榜", appearance_count=3),
        ]
        event_rows = [_event_row("已被事件吸收的标题")]
        with patch("src.storage.DatabaseManager", return_value=_fake_db(raw_rows, event_rows)):
            carried = self.node._load_unabsorbed_titles(days=3)
        self.assertEqual(
            [c["title"] for c in carried],
            ["DeepSeek开源推理框架DeepInfer登顶Agent Arena开源榜"],
        )
        self.assertEqual(carried[0]["carried_age_days"], 1, "缺省补读行按昨日计龄")
        self.assertNotIn("carried_from_raw_news", carried[0], "旧布尔标记已由自然日龄取代")

    def test_carry_dedupes_without_pre_truncation(self):
        raw_rows = [
            _raw_row(f"未吸收标题{i:02d}", appearance_count=1)
            for i in range(25)
        ] + [_raw_row("未吸收标题00", appearance_count=99)]  # 同一标题多源重复
        with patch("src.storage.DatabaseManager", return_value=_fake_db(raw_rows, [])):
            carried = self.node._load_unabsorbed_titles(days=3)
        self.assertEqual(len(carried), 25, "不做热度排序/预截断，全量交 LLM 初筛")
        self.assertEqual(
            sum(1 for c in carried if c["title"] == "未吸收标题00"), 1,
            "同一标题多源重复只回灌一次",
        )

    def test_carry_survives_db_failure(self):
        broken = MagicMock()
        broken.get_session.side_effect = RuntimeError("db down")
        with patch("src.storage.DatabaseManager", return_value=broken):
            self.assertEqual(self.node._load_unabsorbed_titles(days=1), [])

    def _shortlist_prompt(self, raw_news, raw_rows, event_rows=()):
        """跑一次 _analyze_with_llm（粗筛零选中），返回粗筛 prompt 文本。"""
        prompts = []

        def fake_llm(prompt, **kwargs):
            prompts.append(prompt)
            if prompt.startswith(SHORTLIST_PREFIX):
                return json.dumps({"selected": []})
            return "{}"

        with patch(
            "src.storage.DatabaseManager",
            return_value=_fake_db(list(raw_rows), list(event_rows)),
        ), \
             patch("src.got.nodes.news_analysis.GOT_NEWS_CARRY_PREV_TITLES", True), \
             patch("src.got.nodes.news_analysis.GOT_NEWS_TITLE_SHORTLIST_ENABLED", True), \
             patch.object(NewsAnalysisNode, "_call_llm", side_effect=fake_llm), \
             patch.object(self.node, "_build_concept_list_str", return_value="AI应用"), \
             patch.object(self.node, "_load_historical_events_for_prompt", return_value=[]):
            asyncio.run(self.node._analyze_with_llm(raw_news))
        return next(p for p in prompts if p.startswith(SHORTLIST_PREFIX))

    def test_carry_includes_today_earlier_snapshot(self):
        """当日更早时点快照行（13:20 GOT 实时窗口已滚出）回灌粗筛池。"""
        raw_rows = [_raw_row("当日早前快讯甲", fetched_date=date.today())]
        with patch("src.storage.DatabaseManager", return_value=_fake_db(raw_rows, [])):
            carried = self.node._load_unabsorbed_titles()
        self.assertEqual(carried[0]["carried_age_days"], 0)

        prompt = self._shortlist_prompt([_item(1)], raw_rows)
        self.assertIn("[今日早前] 当日早前快讯甲", prompt)

    def test_carry_age_label_counts_natural_days(self):
        raw_rows = [_raw_row("两日前的政策快讯", fetched_date=date.today() - timedelta(days=2))]
        with patch("src.storage.DatabaseManager", return_value=_fake_db(raw_rows, [])):
            carried = self.node._load_unabsorbed_titles()
        self.assertEqual(carried[0]["carried_age_days"], 2)

        prompt = self._shortlist_prompt([_item(1)], raw_rows)
        self.assertIn("[2日前] 两日前的政策快讯", prompt)

    def test_carry_pool_cap_truncates_oldest(self):
        raw_rows = [_raw_row(f"快讯{i:02d}", fetched_date=date.today() - timedelta(days=i))
                    for i in range(3)]
        with patch("src.storage.DatabaseManager", return_value=_fake_db(raw_rows, [])), \
             patch("src.got.nodes.news_analysis.GOT_NEWS_CARRY_MAX", 2):
            carried = self.node._load_unabsorbed_titles()
        self.assertEqual(
            [c["title"] for c in carried], ["快讯00", "快讯01"],
            "超上限按查询序（fetched_date desc, id desc）截断，丢最旧行",
        )

    def test_carry_dedupes_same_title_as_today_raw(self):
        """当日实时采集命中的标题不再进池，粗筛 prompt 里只出现一次。"""
        raw_news = [_item(1, title="同题快讯乙"), _item(2)]
        raw_rows = [_raw_row("同题快讯乙", fetched_date=date.today())]
        prompt = self._shortlist_prompt(raw_news, raw_rows)
        self.assertEqual(prompt.count("同题快讯乙"), 1)


class TestDeepInferEndToEnd(unittest.TestCase):
    """核心验收：昨日落库未吸收的 DeepInfer → 进粗筛 prompt → 选中 → 细读回连 url。"""

    def setUp(self):
        self.node = NewsAnalysisNode(use_mock=True)

    def test_deepinfer_scenario_end_to_end(self):
        deepinfer_title = "DeepSeek开源推理框架DeepInfer，V4.1 Flash登顶Agent Arena开源榜"
        raw_news = [_item(1), _item(2)]
        raw_rows = [_raw_row("已被吸收的标题", 9), _raw_row(deepinfer_title, 3)]
        event_rows = [_event_row("已被吸收的标题")]
        prompts = []

        def fake_llm(prompt, **kwargs):
            prompts.append(prompt)
            if prompt.startswith(SHORTLIST_PREFIX):
                index = None
                for line in prompt.splitlines():
                    head, _, rest = line.partition(". ")
                    if head.isdigit() and "DeepInfer" in rest:
                        index = int(head)
                        break
                self.assertIsNotNone(index, "DeepInfer 必须出现在粗筛 prompt")
                return json.dumps({"selected": [{"index": index, "reason": "产业级开源"}]})
            if prompt.startswith(EXTRACTION_PREFIX):
                return json.dumps({"events": [{
                    "title": "DeepInfer 开源事件",
                    "summary": "国产推理框架登顶",
                    "event_type": "industry",
                    "impact_level": "high",
                    "sentiment": "positive",
                    "affected_sectors": ["AI应用"],
                    "source_index": 1,
                }]})
            if "概念" in prompt[:20]:
                return json.dumps({"concepts": []})
            return json.dumps({"risks": []})

        with patch("src.storage.DatabaseManager", return_value=_fake_db(raw_rows, event_rows)), \
             patch("src.got.nodes.news_analysis.GOT_NEWS_CARRY_PREV_TITLES", True), \
             patch("src.got.nodes.news_analysis.GOT_NEWS_TITLE_SHORTLIST_ENABLED", True), \
             patch.object(NewsAnalysisNode, "_call_llm", side_effect=fake_llm), \
             patch.object(self.node, "_build_concept_list_str", return_value="AI应用"), \
             patch.object(self.node, "_load_historical_events_for_prompt", return_value=[]):
            events, _, _ = asyncio.run(self.node._analyze_with_llm(raw_news))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "DeepInfer 开源事件")
        self.assertEqual(
            events[0].source_url, f"https://example.com/{deepinfer_title}",
            "source_index 必须回连到昨日补读条目的真实 url",
        )

    def test_llm_failure_yields_no_events_without_rule_fallback(self):
        raw_news = [_item(1), _item(2)]
        with patch("src.got.nodes.news_analysis.GOT_NEWS_CARRY_PREV_TITLES", False), \
             patch("src.got.nodes.news_analysis.GOT_NEWS_TITLE_SHORTLIST_ENABLED", True), \
             patch.object(NewsAnalysisNode, "_call_llm", return_value=""), \
             patch.object(self.node, "_build_concept_list_str", return_value="AI应用"), \
             patch.object(self.node, "_load_historical_events_for_prompt", return_value=[]):
            events, concepts, _ = asyncio.run(self.node._analyze_with_llm(raw_news))
        self.assertEqual(events, [], "LLM 失效不得回退到 weight 前 N")
        self.assertEqual(concepts, [])

    def test_zero_selection_skips_extraction_call(self):
        """粗筛零选中 → 不向提取 LLM 发空素材（否则可能凭空产出事件）。"""
        raw_news = [_item(1), _item(2)]
        prompts = []

        def fake_llm(prompt, **kwargs):
            prompts.append(prompt)
            if prompt.startswith(SHORTLIST_PREFIX):
                return json.dumps({"selected": []})
            if prompt.startswith(EXTRACTION_PREFIX):
                return json.dumps({"events": [{
                    "title": "不应出现的幻觉事件",
                    "summary": "s",
                    "event_type": "industry",
                    "impact_level": "high",
                    "sentiment": "positive",
                }]})
            return json.dumps({"concepts": [], "risks": []})

        with patch("src.got.nodes.news_analysis.GOT_NEWS_CARRY_PREV_TITLES", False), \
             patch("src.got.nodes.news_analysis.GOT_NEWS_TITLE_SHORTLIST_ENABLED", True), \
             patch.object(NewsAnalysisNode, "_call_llm", side_effect=fake_llm), \
             patch.object(self.node, "_build_concept_list_str", return_value="AI应用"), \
             patch.object(self.node, "_load_historical_events_for_prompt", return_value=[]):
            events, _, _ = asyncio.run(self.node._analyze_with_llm(raw_news))

        self.assertEqual(events, [])
        self.assertFalse(
            any(p.startswith(EXTRACTION_PREFIX) for p in prompts),
            "空素材下不得再调用事件提取",
        )

    def test_rollback_switch_uses_legacy_top_50(self):
        raw_news = [_item(i) for i in range(1, 61)]
        prompts = []

        def fake_llm(prompt, **kwargs):
            prompts.append(prompt)
            return ""

        with patch("src.got.nodes.news_analysis.GOT_NEWS_TITLE_SHORTLIST_ENABLED", False), \
             patch.object(NewsAnalysisNode, "_call_llm", side_effect=fake_llm), \
             patch.object(self.node, "_build_concept_list_str", return_value="AI应用"), \
             patch.object(self.node, "_load_historical_events_for_prompt", return_value=[]), \
             patch.object(
                 self.node, "_load_unabsorbed_titles",
                 side_effect=AssertionError("回滚路径不得补读昨日标题"),
             ) as carry:
            asyncio.run(self.node._analyze_with_llm(raw_news))

        carry.assert_not_called()
        self.assertFalse(any(p.startswith(SHORTLIST_PREFIX) for p in prompts))
        extraction_prompt = next(p for p in prompts if p.startswith(EXTRACTION_PREFIX))
        self.assertIn("\n50. [sina] 候选新闻标题050", extraction_prompt)
        self.assertNotIn("\n51. ", extraction_prompt, "回滚路径仍是 weight 前 50 条")


class TestCategoryQuota(unittest.TestCase):
    """P1-3 短名单类目配额（影子，缺省关）：单一类目超 6 条的部分整组裁掉，空位不回填。"""

    def setUp(self):
        self.node = NewsAnalysisNode(use_mock=True)

    def _raw(self):
        items = [
            {"title": f"算力产业进展第{i}条", "source": "sina", "url": f"https://e/{i}"}
            for i in range(9)
        ]
        items += [
            {"title": f"消费复苏观察第{i}条", "source": "sina", "url": f"https://c/{i}"}
            for i in range(3)
        ]
        return items

    def _run(self, switch):
        raw = self._raw()
        quota = {}

        def fake_llm(prompt, **kwargs):
            quota["prompt"] = prompt
            return json.dumps({
                "selected": [{"index": i, "reason": "产业级"} for i in range(1, 13)],
            })

        with patch.object(NewsAnalysisNode, "_call_llm", side_effect=fake_llm), \
             patch("src.got.nodes.news_analysis.GOT_NEWS_SHORTLIST_QUOTA", switch):
            picked = self.node._shortlist_titles(raw, quota_out=quota)
        return picked, quota

    def test_switch_off_keeps_everything(self):
        picked, quota = self._run(False)
        self.assertEqual(len(picked), 12)
        self.assertNotIn("类目分散", quota["prompt"])
        self.assertNotIn("dropped_by_quota", quota, "开关关 → 不产配额记录（不落影子）")

    def test_switch_on_trims_single_category(self):
        picked, quota = self._run(True)
        self.assertIn("类目分散", quota["prompt"])
        self.assertEqual(len(picked), 9, "9 条算力裁到 6 + 3 条其他")
        self.assertEqual(sum(1 for p in picked if "算力" in p["title"]), 6)
        self.assertEqual(len(quota["dropped_by_quota"]), 3, "超配额部分裁掉，空位不回填")
        self.assertEqual(quota["category_dist"], {"算力": 9, "其他": 3})
        self.assertEqual(len(quota["picked"]), 9)


if __name__ == "__main__":
    unittest.main()
