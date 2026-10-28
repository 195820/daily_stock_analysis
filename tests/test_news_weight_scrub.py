# -*- coding: utf-8 -*-
"""weight 热度项删除（2026-10-22）回归测试。

背景：`_calculate_weight` 旧口径 = 来源 30% + 时效 25% + **热度 20%** + 多源 25%。
热度项把「涨停/暴涨/利好…」当事件价值，实测一条个股涨停播报（22.4）压过产业级开源
发布/全球气候异常（各 11.6）1.9 倍。事件价值判定已改由 LLM 全量初筛负责
（`news_analysis._shortlist_titles`），weight 只保留信息源质量三项。
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got import news_feeds
from src.got.news_feeds import _calculate_weight


def _item(title: str, source_type: str = "sina", source_count: int = 1) -> dict:
    return {
        "title": title,
        "source_type": source_type,
        "published_date": "",
        "source_count": source_count,
    }


class TestNewsWeightScrub(unittest.TestCase):
    def test_hot_keywords_removed(self):
        self.assertFalse(
            hasattr(news_feeds, "_HOT_KEYWORDS"),
            "热度关键词表应随热度项一并删除（唯一消费者已不存在）",
        )

    def test_weight_ignores_hot_words(self):
        hot = _calculate_weight(_item("A股涨停潮：多股涨停"))
        plain = _calculate_weight(_item("某公司发布年度报告"))
        self.assertEqual(hot, plain, "热度词不得再抬分")

    def test_weight_still_ranks_by_provenance(self):
        multi = _calculate_weight(_item("同一标题", source_count=3))
        single = _calculate_weight(_item("同一标题", source_count=1))
        self.assertGreater(multi, single, "多源确认仍应更高")

        wscn = _calculate_weight(_item("同一标题", source_type="wallstreetcn"))
        sina = _calculate_weight(_item("同一标题", source_type="sina"))
        self.assertGreater(wscn, sina, "来源质量仍应参与排序")

    def test_weight_no_longer_inverts_importance(self):
        hot_three = _calculate_weight(_item("A股涨停潮：多股涨停", "ths_hot", 3))
        hot_single = _calculate_weight(_item("A股涨停潮：多股涨停", "sina", 1))
        self.assertLess(hot_single, hot_three, "多源确认对同一标题仍应加分")

        # 旧口径 22.4 vs 11.6 = 1.93 倍；删除热度项后仅剩来源/多源质量差异
        industry = _calculate_weight(
            _item("DeepSeek开源推理框架DeepInfer登顶Agent Arena开源榜", "sina", 1)
        )
        self.assertLess(
            hot_three / industry, 1.6,
            "涨停播报不得再以 ~1.9 倍压过产业级事件（剩余差异只来自信息源质量）",
        )
        self.assertGreater(hot_three / industry, 1.0)


if __name__ == "__main__":
    unittest.main()
