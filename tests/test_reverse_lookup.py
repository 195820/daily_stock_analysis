# -*- coding: utf-8 -*-
"""
_reverse_lookup_stock_sector 三级匹配单元测试（2026-08-28）

覆盖：
1. 蓄水池板块经别名归一化后命中（半导体概念 → 半导体），不再落入行业板 fallback
2. 埋伏正式池板块命中
3. confirmed_sectors 经别名归一化命中
4. 三级均未命中 → fallback 且 is_fallback=True
"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.nodes.portfolio_decision import PortfolioDecisionNode
from src.got.schemas import (
    AmbushCandidate,
    AmbushReservoirEntry,
    SectorIntel,
    UnifiedContext,
)


def _unified(**kwargs) -> UnifiedContext:
    base = dict(
        status="normal",
        unified_signal="neutral",
        overall_confidence=0.5,
        final_position_limit=0.5,
    )
    base.update(kwargs)
    return UnifiedContext(**base)


class TestReverseLookupThreeTier(unittest.TestCase):

    def setUp(self):
        self.node = PortfolioDecisionNode()

    def _call(self, boards, unified, **kwargs):
        with patch("data_provider.base.DataFetcherManager") as mock_mgr:
            mock_mgr.return_value.get_belong_boards.return_value = boards
            return self.node._reverse_lookup_stock_sector("002158", unified, **kwargs)

    def test_reservoir_board_matched_via_alias(self):
        """半导体落蓄水池 + efinance 返回同花顺口径"半导体概念" → 应命中并返回东财口径名"""
        unified = _unified(ambush_reservoir=[
            AmbushReservoirEntry(
                sector_name="半导体", reason="no_catalyst", entered_date=date(2026, 8, 28),
            ),
        ])
        boards = [
            {"name": "机械设备"},      # 行业板（旧逻辑的 fallback 结果）
            {"name": "半导体概念"},    # 同花顺口径，归一化后应命中蓄水池
            {"name": "液冷概念"},
        ]
        result = self._call(boards, unified, strength_lookup={"半导体": 55.4})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sector_name"], "半导体")
        self.assertEqual(result[0]["strength"], 55.4)
        self.assertEqual(result[0]["sector_role"], "ambush")
        self.assertEqual(result[0]["ambush_source"], "reservoir")
        self.assertFalse(result[0].get("is_fallback", False))

    def test_ambush_pool_matched(self):
        unified = _unified(ambush_pool=[
            AmbushCandidate(sector_name="液冷", catalyst_score=1.0, category_tag="drift"),
        ])
        boards = [{"name": "液冷概念"}]
        result = self._call(boards, unified)
        self.assertEqual(result[0]["sector_name"], "液冷")
        self.assertEqual(result[0]["strength"], 50.0)  # 无强度数据 → 中性分
        self.assertEqual(result[0]["ambush_source"], "ambush")

    def test_confirmed_sector_matched_via_alias(self):
        unified = _unified(confirmed_sectors=[
            SectorIntel(sector_name="半导体", composite_score=70.0),
        ])
        boards = [{"name": "半导体概念"}]
        result = self._call(boards, unified)
        self.assertEqual(result[0]["sector_name"], "半导体")
        self.assertEqual(result[0]["strength"], 70.0)
        self.assertEqual(result[0]["sector_role"], "mainline")

    def test_no_match_falls_back(self):
        unified = _unified()
        boards = [{"name": "机械设备"}, {"name": "专精特新"}]
        result = self._call(boards, unified)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["is_fallback"])
        self.assertEqual(result[0]["sector_name"], "机械设备")
        self.assertEqual(result[0]["sector_type"], "belong_board")

    def test_no_boards_returns_empty(self):
        result = self._call([], _unified())
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
