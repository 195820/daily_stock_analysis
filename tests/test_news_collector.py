# -*- coding: utf-8 -*-
"""Node 4 新闻快照采集（GOT 之外，零 LLM）单测。

覆盖：正常采集计数、空采集不抛错、只采滚动新闻线（禁采论坛/热榜/Intelligence）、
采集异常向上传播，以及「采集路径禁止调用 LLM」的硬断言。
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.news_collector import collect_news_snapshot
from src.got.nodes.news_analysis import NewsAnalysisNode
from src.got.schemas import DataQuality


class TestNewsCollector(unittest.TestCase):
    def _collect(self, fetch_return, persist_return=(0, 0)):
        with patch.object(
            NewsAnalysisNode, "_fetch_news", new=AsyncMock(return_value=fetch_return),
        ), patch.object(
            NewsAnalysisNode, "_persist_raw_news", return_value=persist_return,
        ) as persist, patch.object(
            NewsAnalysisNode, "_call_llm",
            side_effect=AssertionError("采集路径禁止 LLM"),
        ):
            stats = asyncio.run(collect_news_snapshot())
        return stats, persist

    def test_snapshot_returns_persist_counts(self):
        raw = [{"title": "标题A", "source": "sina"}, {"title": "标题B", "source": "cls"}]
        pool = raw + [{"title": "热榜丙", "source": "微博", "platform": "weibo", "rank": 1}]
        quality = DataQuality(available_fields=["sina_feed"], missing_fields=["cls_telegraph"])
        stats, persist = self._collect((raw, pool, quality), persist_return=(1, 1))
        self.assertEqual(stats["raw"], 3)
        self.assertEqual(stats["persisted"], 1)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["available_fields"], ["sina_feed"])
        self.assertEqual(stats["missing_fields"], ["cls_telegraph"])
        persist.assert_called_once_with(pool)

    def test_empty_fetch_returns_zero_counts(self):
        stats, persist = self._collect(([], [], DataQuality()))
        self.assertEqual(
            stats, {"raw": 0, "persisted": 0, "updated": 0,
                    "available_fields": [], "missing_fields": []},
        )
        persist.assert_not_called()

    def test_snapshot_skips_forum_hotlists_and_intelligence(self):
        """每小时跑的快照只采滚动新闻线；论坛/热榜/情报热榜禁采（反爬 + 非时效源）。"""
        calls = {}

        def fake_fetch_all(skip_sources=()):
            calls["skip"] = tuple(skip_sources)
            return [], [], {}

        with patch(
            "src.got.nodes.news_analysis.fetch_all_news_pool", side_effect=fake_fetch_all,
        ), patch.object(
            NewsAnalysisNode, "_fetch_intelligence",
            new=AsyncMock(side_effect=AssertionError("快照禁采 Intelligence 热榜")),
        ), patch.object(
            NewsAnalysisNode, "_persist_raw_news",
        ) as persist:
            stats = asyncio.run(collect_news_snapshot())

        self.assertEqual(calls["skip"], ("nga_forum", "ths_hot"))
        self.assertEqual(stats["raw"], 0)
        persist.assert_not_called()

    def test_fetch_failure_propagates(self):
        with patch.object(
            NewsAnalysisNode, "_fetch_news",
            new=AsyncMock(side_effect=RuntimeError("all sources down")),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(collect_news_snapshot())


class TestHotListMetadata(unittest.TestCase):
    """P0-1：热榜元数据在采集层透传（platform / rank / heat），不改 source_type。"""

    def test_newsnow_items_carry_platform_and_rank(self):
        from src.got import news_feeds

        resp = MagicMock()
        resp.json.return_value = {
            "items": [
                {"title": "热榜甲", "url": "https://s.n/1"},
                {"title": "热榜乙", "url": "https://s.n/2"},
            ],
        }
        with patch.object(news_feeds.requests, "get", return_value=resp):
            items = news_feeds.fetch_newsnow_hot()

        self.assertEqual(len(items), len(news_feeds._NEWSNOW_PLATFORMS) * 2)
        self.assertTrue(all(it["source_type"] == "newsnow" for it in items))
        self.assertTrue(all(it["platform"] for it in items))
        self.assertEqual([it["rank"] for it in items[:2]], [1, 2], "响应数组顺序即榜单名次")
        self.assertEqual(items[0]["platform"], news_feeds._NEWSNOW_PLATFORMS[0][0])

    def test_ths_items_carry_platform_rank_heat(self):
        from src.got import news_feeds

        topics = [
            {"title": "话题一", "description": "描述", "heat": 900, "order": 2,
             "related_stocks": [], "jump_url": ""},
            {"title": "话题二", "description": "", "order": 0},
        ]
        with patch("src.services.ths_hot_list.fetch_hot_topics", return_value=topics):
            items = news_feeds.fetch_ths_hot_topics()

        self.assertEqual(items[0]["platform"], "ths")
        self.assertEqual(items[0]["rank"], 2)
        self.assertEqual(items[0]["heat"], 900)
        self.assertIsNone(items[1]["rank"], "order 缺失 → 名次未知存 None，不冒充第一名")


if __name__ == "__main__":
    unittest.main()
