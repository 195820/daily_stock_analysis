# -*- coding: utf-8 -*-
"""Node 4 分层粗筛（事件簇索引 → 分片泛读 → 全局去重/MMR → 终审）单测。

覆盖（对应计划 §Verification A）：
1. 100 条（80 独立主题 + 20 同题）→ 81 簇；所有主题都进分片；候选映射无未知 cluster_id
2. 同簇重复候选只占 1 个终审名额；高热度主题与单源产业事件都被保留
3. 同主题 5 条 importance=3 + 5 个独立主题 → MMR 主题上限 3，不只剩首个主题
4. embedding 抛错 → index_mode=jaccard_fallback；空池返回 [] 且不调 LLM
5. 单片 LLM 失败只跳该片；终审空响应 → MMR 次序前 12（非 weight/原始顺序）
6. evidence_index 越界 / 伪造 cluster_id / 同片重复簇 → 丢弃，无人造标题进细读
7. 终审后条目保留原始 title/snippet/url/published_date，并追加簇溯源字段；
   `_parse_events()` 按 ordered_items 回连真实 URL
8. 娱乐热榜过滤只作用于粗筛池，实时 merged_news 条数与顺序不变
9. GOT_NEWS_READING_ARCH=flat 仍走 `_shortlist_titles`；cluster 走新模块；
   GOT_NEWS_TITLE_SHORTLIST_ENABLED=false 保留 weight 前 50 旧路径（不补读/不调新模块）
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got import news_retrieval as nr
from src.got.nodes import news_analysis as na
from src.got.nodes.news_analysis import NewsAnalysisNode

SHARD_PREFIX = "你是 A 股消息面泛读员"
REVIEW_PREFIX = "你是 A 股消息面终审员"
_CLUSTER_ID_RE = re.compile(r"\[([0-9a-f]{12})\]")


def _item(i: int, **kw) -> dict:
    base = {
        "title": f"候选新闻标题{i:03d}",
        "snippet": f"snippet-body-{i}",
        "source": "cls",
        "source_type": "cls",
        "url": f"https://example.com/{i}",
        "published_date": "2026-10-28 09:00:00",
    }
    base.update(kw)
    return base


def _one_hot(index: int, dim: int) -> list:
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


def _cluster_ids(prompt: str) -> list:
    return list(dict.fromkeys(_CLUSTER_ID_RE.findall(prompt)))


def _fake_vectors(mapping: dict):
    async def _vectors(titles, run_date, *, stats=None):
        return [mapping[title] for title in titles]
    return _vectors


def _fake_llm(shard_handler, review_handler):
    def _call(prompt, **kwargs):
        if prompt.startswith(SHARD_PREFIX):
            return shard_handler(prompt)
        if prompt.startswith(REVIEW_PREFIX):
            return review_handler(prompt)
        raise AssertionError(f"未预期的 prompt: {prompt[:40]}")
    return _call


def _shard_candidates(prompt: str, importance: int = 3) -> str:
    ids = _cluster_ids(prompt)
    return json.dumps({
        "candidates": [
            {
                "cluster_id": cid,
                "topic": f"主题{i}",
                "importance": importance,
                "freshness": 2,
                "reason": "产业级事件",
                "evidence_index": 1,
            }
            for i, cid in enumerate(ids)
        ],
    })


def _empty_result() -> str:
    return json.dumps({"events": [], "concepts": [], "risks": []})


class _NodeCase(unittest.TestCase):
    def setUp(self):
        self.node = NewsAnalysisNode(use_mock=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.multiple(
            nr,
            GOT_NEWS_TRACE_ROOT=self._tmp.name,
            GOT_NEWS_EMBED_CACHE_ROOT=self._tmp.name,
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class TestClusterIndex(_NodeCase):
    def test_dedupes_same_topic_and_covers_all_shards(self):
        dim = 81
        items = [_item(i, title=f"独立主题{i:02d}产业动态") for i in range(80)]
        for i in range(20):
            items.append(_item(100 + i, title=f"DeepSeek开源推理框架DeepInfer更新{i}",
                               source="sina", source_type="sina"))
        vectors = [_one_hot(i, dim) for i in range(80)] + [_one_hot(80, dim)] * 20

        clusters = nr.index_news_clusters(items, vectors=vectors)
        self.assertEqual(len(clusters), 81, "20 条同题必须只占 1 簇")

        shards = nr.make_reading_shards(clusters)
        sharded = [c.cluster_id for shard in shards for c in shard]
        self.assertEqual(sorted(sharded), sorted(c.cluster_id for c in clusters))
        self.assertTrue(any(len(shard) > 1 for shard in shards), "独立主题应被分到多片")

        candidates = [
            {"cluster_id": c.cluster_id, "topic": "", "importance": 1, "freshness": 0}
            for c in clusters
        ]
        drop: dict = {}
        merged = nr.merge_shard_candidates(candidates, clusters, drop_out=drop)
        self.assertEqual(len(merged), len(clusters))
        self.assertEqual(drop["unknown_cluster"], 0)
        known = {c.cluster_id for c in clusters}
        self.assertTrue(all(record["cluster_id"] in known for record in merged))

    def test_same_cluster_never_takes_two_review_slots(self):
        dim = 2
        items = [
            _item(1, title="固态电池量产线投产", source="财联社", source_type="cls"),
            _item(2, title="DeepSeek开源推理框架DeepInfer登顶", source="sina", source_type="sina"),
        ]
        clusters = nr.index_news_clusters(items, vectors=[_one_hot(0, dim), _one_hot(1, dim)])
        self.assertEqual(len(clusters), 2)
        hot, deep = clusters[0], clusters[1]

        candidates = [
            {"cluster_id": hot.cluster_id, "topic": "固态电池", "importance": 3,
             "freshness": 2, "source_quality": 1.0, "title": hot.representative["title"]},
            {"cluster_id": hot.cluster_id, "topic": "固态电池", "importance": 2,
             "freshness": 1, "source_quality": 1.0, "title": hot.representative["title"]},
            {"cluster_id": deep.cluster_id, "topic": "国产推理框架", "importance": 3,
             "freshness": 2, "source_quality": 0.6, "title": deep.representative["title"]},
        ]
        merged = nr.merge_shard_candidates(candidates, clusters)
        self.assertEqual(len(merged), 2, "同一簇只能留 1 条候选")
        self.assertEqual(len({r["cluster_id"] for r in merged}), 2)

        finalists = nr.diversify_candidates(merged, limit=2)
        self.assertEqual(len(finalists), 2)
        self.assertEqual(len({f["cluster_id"] for f in finalists}), 2)
        self.assertIn(deep.cluster_id, {f["cluster_id"] for f in finalists},
                      "单源 DeepInfer 类产业事件不得被高热度主题挤掉")

    def test_topic_cap_keeps_diverse_candidates(self):
        candidates = [
            {"cluster_id": f"a{i:012x}", "topic": "AI算力", "importance": 3,
             "freshness": 2, "source_quality": 1.0, "title": f"AI算力事件变体{i}"}
            for i in range(5)
        ] + [
            {"cluster_id": f"b{i:012x}", "topic": topic, "importance": 2,
             "freshness": 1, "source_quality": 1.0, "title": f"{topic}进展"}
            for i, topic in enumerate(["固态电池", "稀土出口", "光伏组件", "白酒消费", "券商并购"])
        ]
        finalists = nr.diversify_candidates(candidates, limit=10)
        topics = [f["topic_text"] for f in finalists]
        self.assertEqual(topics.count("AI算力"), 3, "同一主题最多 3 条")
        self.assertEqual(len(finalists), 8, "5 个独立主题必须全部保留")
        self.assertGreater(len(set(topics)), 1, "MMR 不能只输出首个主题")


class TestDegradation(_NodeCase):
    def test_embedding_failure_falls_back_to_jaccard(self):
        items = [
            _item(1, title="DeepSeek开源推理框架DeepInfer发布"),
            _item(2, title="DeepSeek开源推理框架DeepInfer正式上线"),
            _item(3, title="固态电池量产线投产"),
            _item(4, title="锂电材料涨价"),
        ]

        async def _boom(texts):
            raise RuntimeError("embedding quota exceeded")

        with patch.object(nr, "embed_texts", side_effect=_boom), \
                patch.object(NewsAnalysisNode, "_call_llm",
                             side_effect=_fake_llm(_shard_candidates,
                                                   lambda p: json.dumps({"selected": []}))):
            shortlist, trace = asyncio.run(nr.read_news(self.node, items))

        self.assertEqual(trace["index_mode"], "jaccard_fallback")
        self.assertLess(trace["cluster_count"], len(items), "同题标题应聚成一簇")
        self.assertEqual(trace["cluster_count"], 3)
        self.assertEqual(shortlist, [], "终审合法空数组 = 当日无重要事件")

    def test_empty_pool_returns_empty_without_prompt(self):
        with patch.object(NewsAnalysisNode, "_call_llm") as mocked:
            shortlist, trace = asyncio.run(nr.read_news(self.node, []))
        self.assertEqual(shortlist, [])
        self.assertEqual(trace["cluster_count"], 0)
        mocked.assert_not_called()


class TestShardAndReview(_NodeCase):
    def _items(self, count: int) -> list:
        return [_item(i, title=f"独立主题{i:03d}产业动态") for i in range(count)]

    def test_failed_shard_is_skipped_and_review_failure_falls_back_to_mmr(self):
        items = self._items(61)
        vectors = [_one_hot(i, 61) for i in range(61)]

        def shard_handler(prompt):
            if "第 2/2 批" in prompt:
                return ""  # 该片空响应 → 旧候选全丢，但其余片照常
            ids = _cluster_ids(prompt)
            return json.dumps({
                "candidates": [
                    {
                        "cluster_id": cid,
                        "topic": f"主题{i}",
                        # 第 10 条给最高价值 → MMR 次序应把它排到最前（片上限 20 条）
                        "importance": 3 if i == 9 else 1,
                        "freshness": 2,
                        "evidence_index": 1,
                    }
                    for i, cid in enumerate(ids)
                ],
            })

        with patch.object(nr, "_embed_title_vectors", new=_fake_vectors(
            {item["title"]: vec for item, vec in zip(items, vectors)}
        )), patch.object(NewsAnalysisNode, "_call_llm",
                         side_effect=_fake_llm(shard_handler, lambda prompt: "")):
            shortlist, trace = asyncio.run(nr.read_news(self.node, items))

        self.assertEqual(trace["shard_count"], 2)
        self.assertEqual(trace["llm_failures"], 1)
        self.assertEqual([s["status"] for s in trace["shards"]], ["ok", "failed"])
        self.assertEqual(trace["review_status"], "fallback_mmr")
        self.assertEqual(len(shortlist), 12)
        # MMR 按价值分排序：最高分条目在前，而不是候选池原始顺序（标题 000 先）
        self.assertEqual(shortlist[0]["title"], items[9]["title"])

    def test_invalid_candidates_dropped(self):
        items = [
            _item(1, title="固态电池量产线投产"),
            _item(2, title="锂电材料涨价"),
        ]
        clusters = nr.index_news_clusters(items, vectors=[_one_hot(0, 2), _one_hot(1, 2)])
        valid = clusters[0].cluster_id

        payload = json.dumps({
            "candidates": [
                {"cluster_id": valid, "topic": "固态电池", "importance": 3,
                 "freshness": 2, "evidence_index": 1},
                {"cluster_id": "deadbeef0000", "topic": "伪造", "importance": 3,
                 "freshness": 2, "evidence_index": 1},
                {"cluster_id": valid, "topic": "固态电池", "importance": 2,
                 "freshness": 1, "evidence_index": 99},
                {"cluster_id": valid, "topic": "固态电池", "importance": 1,
                 "freshness": 0, "evidence_index": 1},
            ],
        })
        with patch.object(NewsAnalysisNode, "_call_llm", return_value=payload):
            result = nr.score_shard(self.node, clusters)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["cluster_id"], valid)
        self.assertEqual(result["dropped_invalid"], 3)

    def test_llm_exceptions_degrade_without_raising(self):
        items = [
            _item(1, title="固态电池量产线投产"),
            _item(2, title="锂电材料涨价"),
        ]
        clusters = nr.index_news_clusters(items, vectors=[_one_hot(0, 2), _one_hot(1, 2)])

        with patch.object(NewsAnalysisNode, "_call_llm", side_effect=RuntimeError("boom")):
            shard_result = nr.score_shard(self.node, clusters)
        self.assertEqual(shard_result["status"], "failed")
        self.assertEqual(shard_result["candidates"], [])

        def _call(prompt, **kwargs):
            if prompt.startswith(SHARD_PREFIX):
                return _shard_candidates(prompt)
            raise RuntimeError("review down")

        with patch.object(nr, "_embed_title_vectors", new=_fake_vectors(
            {item["title"]: _one_hot(i, 2) for i, item in enumerate(items)}
        )), patch.object(NewsAnalysisNode, "_call_llm", side_effect=_call):
            shortlist, trace = asyncio.run(nr.read_news(self.node, items))
        self.assertEqual(trace["review_status"], "fallback_mmr")
        self.assertEqual(len(shortlist), 2)

    def test_selected_item_keeps_real_fields_and_parse_events_reconnects_url(self):
        item = _item(
            7,
            title="固态电池量产线正式投产",
            snippet="某电池厂固态产线投产，年产能 5GWh。",
            url="https://real.example.com/news/7",
            published_date="2026-10-28 10:30:00",
        )
        vectors = [_one_hot(0, 1)]

        def shard_handler(prompt):
            return _shard_candidates(prompt)

        def review_handler(prompt):
            cid = _cluster_ids(prompt)[0]
            return json.dumps({"selected": [{"cluster_id": cid, "reason": "产业落地"}]})

        with patch.object(nr, "_embed_title_vectors", new=_fake_vectors(
            {item["title"]: vectors[0]}
        )), patch.object(NewsAnalysisNode, "_call_llm",
                         side_effect=_fake_llm(shard_handler, review_handler)):
            shortlist, trace = asyncio.run(nr.read_news(self.node, [item]))

        self.assertEqual(len(shortlist), 1)
        picked = shortlist[0]
        self.assertEqual(picked["title"], item["title"])
        self.assertEqual(picked["snippet"], item["snippet"])
        self.assertEqual(picked["url"], item["url"])
        self.assertEqual(picked["published_date"], item["published_date"])
        self.assertTrue(picked["news_cluster_id"])
        self.assertIn(item["title"], picked["member_titles"])
        self.assertEqual(trace["review_status"], "ok")

        _, ordered = NewsAnalysisNode._format_news_for_prompt(shortlist, gated=True)
        events = NewsAnalysisNode._parse_events(json.dumps({
            "events": [{
                "title": "固态电池投产",
                "summary": "产线投产",
                "event_type": "industry",
                "impact_level": "high",
                "sentiment": "positive",
                "affected_sectors": ["电池"],
                "source_index": 1,
            }],
        }), ordered)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_url, "https://real.example.com/news/7")
        self.assertEqual(events[0].timestamp, "2026-10-28 10:30:00")


class TestCarriedPoolFilter(unittest.TestCase):
    def test_filters_entertainment_hotlists_only(self):
        from src.got.news_feeds import filter_carried_pool

        pool = [
            {"title": "娱乐1", "source": "微博"},
            {"title": "娱乐2", "source": "百度热搜"},
            {"title": "娱乐3", "source": "今日头条"},
            {"title": "娱乐4", "source": "抖音"},
            {"title": "娱乐5", "source": "B站热搜"},
            {"title": "娱乐6", "source": "知乎"},
            {"title": "情报1", "source": "intelligence_newsnow-weibo"},
            {"title": "情报2", "source": "intelligence_newsnow-baidu"},
            {"title": "产业1", "source": "同花顺热榜", "source_type": "ths_hot"},
            {"title": "产业2", "source": "财联社热门"},
            {"title": "产业3", "source": "intelligence_newsnow-cls-hot"},
            {"title": "产业4", "source": "intelligence_rss-x"},
            {"title": "产业5", "source": "澎湃新闻"},
            {"title": "产业6", "source": "财联社", "source_type": "cls"},
        ]
        snapshot = list(pool)
        kept = filter_carried_pool(pool)

        self.assertEqual(len(pool), len(snapshot), "不得改动调用方列表")
        self.assertEqual(pool, snapshot, "实时 merged_news 的条数与顺序不变")
        self.assertEqual([k["title"] for k in kept], [
            "产业1", "产业2", "产业3", "产业4", "产业5", "产业6",
        ])


class TestAnalyzeWithLlmRouting(_NodeCase):
    def _stack(self, *, shortlist_enabled: bool, carry: bool, arch: str):
        stack = ExitStack()
        stack.enter_context(patch.object(
            na, "GOT_NEWS_TITLE_SHORTLIST_ENABLED", shortlist_enabled,
        ))
        stack.enter_context(patch.object(na, "GOT_NEWS_CARRY_PREV_TITLES", carry))
        stack.enter_context(patch.object(na, "GOT_NEWS_READING_ARCH", arch))
        stack.enter_context(patch.object(
            NewsAnalysisNode, "_load_historical_events_for_prompt", return_value=[],
        ))
        stack.enter_context(patch.object(
            NewsAnalysisNode, "_recall_industry_candidates", return_value=[],
        ))
        stack.enter_context(patch.object(
            NewsAnalysisNode, "_call_llm", return_value=_empty_result(),
        ))
        return stack

    def test_flat_arch_keeps_shortlist_path(self):
        raw = [_item(i) for i in range(5)]
        with self._stack(shortlist_enabled=True, carry=False, arch="flat") as stack:
            shortlist = stack.enter_context(
                patch.object(NewsAnalysisNode, "_shortlist_titles", return_value=[])
            )
            carry = stack.enter_context(
                patch.object(NewsAnalysisNode, "_load_unabsorbed_titles")
            )
            mocked_read = stack.enter_context(
                patch.object(na, "read_news", new=AsyncMock(return_value=([], {})))
            )
            asyncio.run(self.node._analyze_with_llm(raw))
        shortlist.assert_called_once()
        mocked_read.assert_not_called()
        carry.assert_not_called()

    def test_cluster_arch_uses_new_module(self):
        raw = [_item(i) for i in range(5)]
        chosen = _item(1, title="固态电池量产线投产")
        with self._stack(shortlist_enabled=True, carry=False, arch="cluster") as stack:
            shortlist = stack.enter_context(patch.object(NewsAnalysisNode, "_shortlist_titles"))
            mocked_read = stack.enter_context(patch.object(
                na, "read_news", new=AsyncMock(return_value=(
                    [chosen], {"cluster_count": 1, "shard_count": 1,
                               "finalist_count": 1, "review_status": "ok"},
                )),
            ))
            events, _, _ = asyncio.run(self.node._analyze_with_llm(raw))
        shortlist.assert_not_called()
        mocked_read.assert_awaited_once()
        self.assertEqual(events, [])

    def test_disabled_shortlist_keeps_legacy_path(self):
        raw = [_item(i) for i in range(5)]
        with self._stack(shortlist_enabled=False, carry=True, arch="cluster") as stack:
            shortlist = stack.enter_context(patch.object(NewsAnalysisNode, "_shortlist_titles"))
            carry = stack.enter_context(
                patch.object(NewsAnalysisNode, "_load_unabsorbed_titles")
            )
            mocked_read = stack.enter_context(
                patch.object(na, "read_news", new=AsyncMock(return_value=([], {})))
            )
            asyncio.run(self.node._analyze_with_llm(raw))
        shortlist.assert_not_called()
        mocked_read.assert_not_called()
        carry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
