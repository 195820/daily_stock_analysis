# -*- coding: utf-8 -*-
"""持仓主板块人工覆盖（2026-10-28 润建股份被光伏顶替的修复）。"""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.holding_sector_overrides import all_overrides, get_holding_sector_override
from src.got.nodes.portfolio_decision import PortfolioDecisionNode


class TestHoldingSectorOverride(unittest.TestCase):
    def test_default_override_map(self):
        self.assertEqual(get_holding_sector_override("002929"), "东数西算")
        self.assertEqual(get_holding_sector_override("SH002929"), "东数西算")
        self.assertEqual(get_holding_sector_override("600519"), "")

    def test_env_override_merges(self):
        old = os.environ.get("GOT_HOLDING_SECTOR_OVERRIDES")
        os.environ["GOT_HOLDING_SECTOR_OVERRIDES"] = "600519=白酒, 002929=云计算"
        try:
            merged = all_overrides()
            self.assertEqual(merged["600519"], "白酒")
            self.assertEqual(get_holding_sector_override("002929"), "云计算")  # 环境变量优先
        finally:
            if old is None:
                os.environ.pop("GOT_HOLDING_SECTOR_OVERRIDES", None)
            else:
                os.environ["GOT_HOLDING_SECTOR_OVERRIDES"] = old

    def test_find_stock_sector_uses_override_strength(self):
        node = PortfolioDecisionNode(use_mock=True)
        info = node._find_stock_sector(
            "002929", None,
            strength_lookup={"东数西算": 46.4, "光伏": 35.0},
            allow_override=True,
        )
        self.assertEqual(info["sector_name"], "东数西算")
        self.assertAlmostEqual(info["strength"], 46.4, places=3)
        self.assertTrue(info["is_override"])
        self.assertEqual(info["sector_role"], "holding_override")

    def test_override_not_applied_without_flag(self):
        node = PortfolioDecisionNode(use_mock=True)
        node._reverse_lookup_stock_sector = lambda *a, **k: []
        info = node._find_stock_sector(
            "002929", SimpleNamespace(confirmed_sectors=[]),
            strength_lookup={"东数西算": 46.4},
            allow_override=False,
        )
        self.assertEqual(info["sector_name"], "")

    def test_thesis_drift_skipped_for_override(self):
        node = PortfolioDecisionNode(use_mock=True)
        info = {
            "sector_name": "东数西算",
            "sector_type": "concept",
            "strength": 46.4,
            "is_override": True,
        }
        thesis = SimpleNamespace(sector_name="光伏", family_name="新能源")
        out = node._find_stock_sector_with_thesis(
            info, "002929", None, {}, {"002929": thesis},
        )
        self.assertEqual(out["sector_name"], "东数西算")
        self.assertNotIn("thesis_drift", out)
        self.assertEqual(out["identity_sector_name"], "光伏")

    def test_thesis_identity_is_fixed_when_current_sector_differs(self):
        node = PortfolioDecisionNode(use_mock=True)
        info = {
            "sector_name": "新能源车",
            "sector_type": "mainline",
            "strength": 72.2,
        }
        thesis = SimpleNamespace(sector_name="动力电池", family_name="其他")
        out = node._find_stock_sector_with_thesis(
            info, "002009", None, {}, {"002009": thesis},
        )
        # 当日匹配仍是新能源车；买入身份固定为 thesis 的动力电池，不再被重选
        self.assertEqual(out["sector_name"], "新能源车")
        self.assertEqual(out["identity_sector_name"], "动力电池")
        self.assertTrue(out["thesis_drift"])


if __name__ == "__main__":
    unittest.main()
