# -*- coding: utf-8 -*-
"""P0-1 热榜元数据落库 + attention 影子聚合单测。

覆盖：
1. `NewsAnalysisNode._attention_meta`：单值 platform / 合并 platforms / rank/heat 规整
2. `_persist_raw_news`：命中行 snapshot_count +1、rank 取更靠前、platform 补空不覆盖、
   `appearance_count`（按日）不被同日二次运行推高
3. `build_attention_shadow`：同题多平台 → platform_count/multi_platform_count；
   无热榜行 → 空 items 不报错，且落盘字段齐全
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.attention_shadow import build_attention_shadow
from src.got.nodes.news_analysis import NewsAnalysisNode


class _Row:
    """node4_raw_news 行替身（字段与 ORM 列同名）。"""

    def __init__(self, **kw):
        self.title_normalized = "某产业政策落地"
        self.title_original = "某产业政策落地"
        self.source = "微博"
        self.last_seen_date = date.today()
        self.appearance_count = 1
        self.appearance_count_run_based = 1
        self.snapshot_count = 1
        self.url = None
        self.published_at = None
        self.url_canonical = None
        self.platform = "weibo"
        self.platforms = "weibo"
        self.rank = 5
        self.heat = None
        for k, v in kw.items():
            setattr(self, k, v)


def _session(existing=None, similar_rows=()):
    """假 session：URL/标题精确查询走 first()，相似度兜底走 all()。"""
    session = MagicMock()
    query = session.query.return_value
    query.filter.return_value.first.return_value = existing
    query.filter.return_value.all.return_value = list(similar_rows)
    return session


class TestAttentionMeta(unittest.TestCase):
    def test_merges_platform_set_and_normalizes(self):
        meta = NewsAnalysisNode._attention_meta({
            "platform": "weibo",
            "platforms": ["weibo", "baidu"],
            "rank": 3,
            "heat": 0,
        })
        self.assertEqual(meta["platform"], "weibo")
        self.assertEqual(meta["platforms"], "baidu,weibo")
        self.assertEqual(meta["rank"], 3)
        self.assertIsNone(meta["heat"])

    def test_no_hot_metadata(self):
        meta = NewsAnalysisNode._attention_meta({"title": "普通快讯"})
        self.assertIsNone(meta["platform"])
        self.assertIsNone(meta["platforms"])
        self.assertIsNone(meta["rank"])
        self.assertIsNone(meta["heat"])

    def test_missing_order_rank_is_null(self):
        """THS `order` 缺省 0 → 名次未知存 NULL（不把 0 当第一名）。"""
        self.assertIsNone(NewsAnalysisNode._attention_meta({"platform": "ths", "rank": 0})["rank"])


class TestPersistHotMetadata(unittest.TestCase):
    def _persist(self, session, item):
        db = MagicMock()
        db.get_session.return_value.__enter__.return_value = session
        with patch("src.got.nodes.news_analysis.DatabaseManager", return_value=db):
            return NewsAnalysisNode(use_mock=True)._persist_raw_news([item])

    def test_existing_row_increments_snapshot_and_keeps_best_rank(self):
        row = _Row(rank=5, snapshot_count=3, heat=None)
        item = {
            "title": "某产业政策落地",
            "source": "微博",
            "url": "https://a.com/x",
            "platform": "baidu",
            "platforms": ["baidu"],
            "rank": 2,
            "heat": 88.5,
        }
        persisted, updated = self._persist(_session(existing=row), item)

        self.assertEqual((persisted, updated), (0, 1))
        self.assertEqual(row.snapshot_count, 4)
        self.assertEqual(row.rank, 2, "名次取更靠前（更小）")
        self.assertEqual(row.appearance_count, 1, "同日重复不推高按日 appearance_count")
        self.assertEqual(row.platform, "weibo", "已有 platform 不被覆盖")
        self.assertEqual(row.platforms, "baidu,weibo")
        self.assertEqual(row.heat, 88.5)

    def test_new_row_writes_metadata(self):
        session = _session(existing=None, similar_rows=[])
        item = {
            "title": "某产业政策落地",
            "source": "微博",
            "url": "https://a.com/x?utm_source=weibo",
            "platform": "weibo",
            "platforms": ["weibo", "zhihu"],
            "rank": 1,
            "heat": 12.0,
        }
        persisted, updated = self._persist(session, item)

        self.assertEqual((persisted, updated), (1, 0))
        record = session.add.call_args[0][0]
        self.assertEqual(record.platform, "weibo")
        self.assertEqual(record.platforms, "weibo,zhihu")
        self.assertEqual(record.rank, 1)
        self.assertEqual(record.heat, 12.0)
        self.assertEqual(record.snapshot_count, 1)
        self.assertEqual(record.url_canonical, "https://a.com/x")


class TestBuildAttentionShadow(unittest.TestCase):
    def _build(self, rows):
        day_dir = Path(tempfile.mkdtemp())
        db = MagicMock()
        db.get_session.return_value.__enter__.return_value.query.return_value.filter.return_value.all.return_value = rows
        with patch("src.storage.DatabaseManager", return_value=db):
            record = build_attention_shadow(day_dir, today=date(2026, 10, 28))
        out = day_dir / "shadow_attention.json"
        return record, out

    def test_multi_platform_aggregation(self):
        rows = [
            _Row(title_normalized="同题甲", title_original="同题甲", platform="weibo", platforms="weibo", rank=2, snapshot_count=3),
            _Row(title_normalized="同题甲", title_original="同题甲", platform="baidu", platforms="baidu", rank=7, snapshot_count=2),
            _Row(title_normalized="单平台乙", title_original="单平台乙", platform="zhihu", platforms="zhihu", rank=1),
        ]
        record, out = self._build(rows)

        self.assertTrue(out.exists())
        self.assertEqual(record["multi_platform_count"], 1)
        first = record["items"][0]
        self.assertEqual(first["title"], "同题甲")
        self.assertEqual(first["platform_count"], 2)
        self.assertEqual(first["platforms"], ["baidu", "weibo"])
        self.assertEqual(first["best_rank"], 2)
        self.assertEqual(first["snapshot_count"], 3)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["date"], "20261028")

    def test_merged_platforms_column_counts_once(self):
        """去重合并后的单行（platforms="weibo,zhihu"）同样算多平台扩散。"""
        rows = [_Row(title_normalized="同题甲", title_original="同题甲",
                     platform="weibo", platforms="weibo,zhihu", rank=3)]
        record, _ = self._build(rows)
        self.assertEqual(record["items"][0]["platform_count"], 2)
        self.assertEqual(record["multi_platform_count"], 1)

    def test_no_hot_rows_is_empty_not_error(self):
        record, out = self._build([])
        self.assertEqual(record["items"], [])
        self.assertEqual(record["multi_platform_count"], 0)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
