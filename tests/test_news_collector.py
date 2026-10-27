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
from unittest.mock import AsyncMock, patch

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
        quality = DataQuality(available_fields=["sina_feed"], missing_fields=["cls_telegraph"])
        stats, persist = self._collect((raw, quality), persist_return=(1, 1))
        self.assertEqual(stats["raw"], 2)
        self.assertEqual(stats["persisted"], 1)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["available_fields"], ["sina_feed"])
        self.assertEqual(stats["missing_fields"], ["cls_telegraph"])
        persist.assert_called_once_with(raw)

    def test_empty_fetch_returns_zero_counts(self):
        stats, persist = self._collect(([], DataQuality()))
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
            return [], {}

        with patch(
            "src.got.nodes.news_analysis.fetch_all_news", side_effect=fake_fetch_all,
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


if __name__ == "__main__":
    unittest.main()
