# -*- coding: utf-8 -*-
"""
Phase 2 §4 单元测试：指数注册表 + 身份解析。

覆盖：
1. IndexEntry / AnalysisTarget 数据结构
2. IndexRegistry 加载与查找（by canonical / suffixed / bare / alias / name）
3. parse_analysis_target 消歧（裸码→股票，带后缀→指数，中文名→指数）
4. matches_index_code 跨格式匹配
5. 冲突消歧：000001 是股票（裸码）还是指数（带后缀）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.index_registry import (
    IndexEntry,
    AnalysisTarget,
    IndexRegistry,
    parse_analysis_target,
    matches_index_code,
    get_registry,
)


class TestIndexEntry(unittest.TestCase):
    """IndexEntry 数据结构。"""

    def test_suffixed_code(self):
        entry = IndexEntry(
            bare_code="000001", exchange="SH",
            canonical_id="sh000001", display_name="上证指数",
        )
        self.assertEqual(entry.suffixed_code, "000001.SH")


class TestIndexRegistry(unittest.TestCase):
    """IndexRegistry 加载与查找。"""

    def setUp(self):
        self.registry = get_registry()

    def test_load_not_empty(self):
        """注册表加载了 ≥5 个指数。"""
        self.assertGreaterEqual(len(self.registry.all_entries()), 5)

    def test_get_by_canonical(self):
        entry = self.registry.get_by_canonical("sh000001")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.display_name, "上证指数")

    def test_get_by_suffixed(self):
        entry = self.registry.get_by_suffixed("399006.SZ")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.bare_code, "399006")
        self.assertEqual(entry.exchange, "SZ")

    def test_get_by_alias(self):
        entry = self.registry.get_by_alias("szzs")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.canonical_id, "sh000001")

    def test_get_by_name(self):
        entry = self.registry.get_by_name("沪深300")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.canonical_id, "sh000300")

    def test_missing_returns_none(self):
        self.assertIsNone(self.registry.get_by_canonical("sh999999"))
        self.assertIsNone(self.registry.get_by_suffixed("999999.SH"))


class TestParseAnalysisTarget(unittest.TestCase):
    """parse_analysis_target 消歧。"""

    def test_bare_code_defaults_to_stock(self):
        """裸码 000001 → 股票（平安银行），消除歧义。"""
        t = parse_analysis_target("000001")
        self.assertEqual(t.asset_type, "stock")
        self.assertEqual(t.canonical_id, "000001.SZ")

    def test_suffixed_code_resolves_to_index(self):
        """带后缀 000001.SH → 指数（上证指数）。"""
        t = parse_analysis_target("000001.SH")
        self.assertEqual(t.asset_type, "index")
        self.assertEqual(t.canonical_id, "sh000001")
        self.assertIsNotNone(t.matched_index)

    def test_prefix_code_resolves_to_index(self):
        """带前缀 sh000001 → 指数。"""
        t = parse_analysis_target("sh000001")
        self.assertEqual(t.asset_type, "index")

    def test_chinese_name_resolves_to_index(self):
        """中文名 '上证指数' → 指数。"""
        t = parse_analysis_target("上证指数")
        self.assertEqual(t.asset_type, "index")
        self.assertEqual(t.canonical_id, "sh000001")

    def test_alias_resolves_to_index(self):
        """别名 'szzs' → 指数。"""
        t = parse_analysis_target("szzs")
        self.assertEqual(t.asset_type, "index")

    def test_sse_index(self):
        """沪深300 → 指数。"""
        t = parse_analysis_target("000300.SH")
        self.assertEqual(t.asset_type, "index")
        self.assertEqual(t.canonical_id, "sh000300")

    def test_sz_index(self):
        """创业板指 → 指数。"""
        t = parse_analysis_target("399006.SZ")
        self.assertEqual(t.asset_type, "index")
        self.assertEqual(t.canonical_id, "sz399006")

    def test_bare_600519_is_stock(self):
        """600519 → 股票（SH）。"""
        t = parse_analysis_target("600519")
        self.assertEqual(t.asset_type, "stock")
        self.assertIn("SH", t.canonical_id)

    def test_us_ticker(self):
        """AAPL → 美股。"""
        t = parse_analysis_target("AAPL")
        self.assertEqual(t.asset_type, "stock")
        self.assertEqual(t.canonical_id, "AAPL")

    def test_empty_input(self):
        t = parse_analysis_target("")
        self.assertEqual(t.asset_type, "unsupported")

    def test_unknown_code(self):
        t = parse_analysis_target("xyz123!!!")
        self.assertEqual(t.asset_type, "unsupported")


class TestMatchesIndexCode(unittest.TestCase):
    """matches_index_code 跨格式匹配。"""

    def test_sh_prefix_matches_suffixed(self):
        self.assertTrue(matches_index_code("sh000001", "000001.SH"))

    def test_suffixed_matches_sh_prefix(self):
        self.assertTrue(matches_index_code("000001.SH", "sh000001"))

    def test_bare_matches_suffixed(self):
        """裸码 000001 来自指数列表时按上证指数解释，匹配 000001.SH。

        matches_index_code 专用于指数匹配语境（idx_code 来自 overview.indices），
        与 parse_analysis_target 的「裸码→股票」消歧规则是不同语境。
        """
        self.assertTrue(matches_index_code("000001", "000001.SH"))

    def test_sz_prefix_matches(self):
        self.assertTrue(matches_index_code("sz399006", "399006.SZ"))

    def test_ss_alias_matches_sh(self):
        """SS 后缀（Yahoo Finance）等价于 SH。"""
        self.assertTrue(matches_index_code("sh000001", "000001.SS"))

    def test_different_indices_no_match(self):
        self.assertFalse(matches_index_code("sh000001", "sh000300"))

    def test_case_insensitive(self):
        self.assertTrue(matches_index_code("SH000001", "000001.sh"))


class TestConflictResolution(unittest.TestCase):
    """000001 双身份消歧的核心测试。"""

    def test_same_input_different_meaning(self):
        """同一代码 000001，裸码=股票，带后缀=指数。"""
        as_stock = parse_analysis_target("000001")
        as_index = parse_analysis_target("000001.SH")

        self.assertEqual(as_stock.asset_type, "stock")
        self.assertEqual(as_index.asset_type, "index")
        # 两者的 canonical_id 不同
        self.assertNotEqual(as_stock.canonical_id, as_index.canonical_id)


if __name__ == "__main__":
    unittest.main()
