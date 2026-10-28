# -*- coding: utf-8 -*-
"""`has_partial_failure` 修复回归（2026-10-28）。

旧检查读 `data_quality.fetch_failed_fields`（恒空）且比对已不存在的源名
（akshare_news/search_news）→ `status` 永不 degraded。改判 `missing_fields`
里的 4 条高价值线（cls_telegraph/wallstreetcn/eastmoney_kuaixun/newsnow_hot）。

覆盖：高价值线缺失 → degraded；全源在线 → normal；非高价值线缺失（sina_feed）→ 不降级。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.nodes.news_analysis import NewsAnalysisNode
from src.got.schemas import DataQuality

MODULE = "src.got.nodes.news_analysis"

_ITEM = {
    "title": "国务院部署新一轮设备更新",
    "snippet": "全文摘要",
    "source": "sina",
    "source_type": "sina",
    "url": "https://example.com/a",
    "published_date": "2026-10-28 09:00:00",
}


def _fake_llm(prompt: str, **kwargs) -> str:
    if prompt.startswith("你是 A 股消息面筛选员"):
        return json.dumps({"selected": [{"index": 1, "reason": "政策级"}]})
    if prompt.startswith("你是严谨的新闻编辑"):
        return json.dumps({"events": []})
    return ""  # 概念/风险/推理：空 → 解析层走规则兜底


class TestPartialFailureStatus(unittest.IsolatedAsyncioTestCase):
    async def _run(self, missing_fields):
        node = NewsAnalysisNode(use_mock=False)
        quality = DataQuality(
            available_fields=["sina_feed"], missing_fields=list(missing_fields),
        )
        calendar = MagicMock()
        calendar.events = []
        calendar.refresh_mode = "incremental"
        builder = MagicMock()
        builder.build_calendar = AsyncMock(return_value=calendar)

        with patch.object(node, "_fetch_news", AsyncMock(return_value=([_ITEM], [_ITEM], quality))), \
             patch.object(node, "_persist_raw_news", return_value=(0, 0)), \
             patch.object(node, "_call_llm", side_effect=_fake_llm), \
             patch.object(node, "_load_unabsorbed_titles", return_value=[]), \
             patch.object(node, "_load_historical_events_for_prompt", return_value=[]), \
             patch.object(node, "_rescue_low_events", AsyncMock(return_value=[])), \
             patch.object(node, "_write_attention_shadow"), \
             patch("src.got.catalyst_calendar.CatalystCalendarBuilder", return_value=builder), \
             patch("src.got.intelligence_service.initialize_builtin_sources", return_value=0):
            return await node.run()

    async def test_missing_high_value_source_is_degraded(self):
        result = await self._run(["cls_telegraph"])
        self.assertEqual(result.status, "degraded")

    async def test_all_sources_online_is_normal(self):
        result = await self._run([])
        self.assertEqual(result.status, "normal")

    async def test_missing_low_value_source_stays_normal(self):
        """sina_feed/intelligence 缺失不降级（滚动线外部源多，单线缺失不等于能力受损）。"""
        result = await self._run(["sina_feed"])
        self.assertEqual(result.status, "normal")


if __name__ == "__main__":
    unittest.main()
