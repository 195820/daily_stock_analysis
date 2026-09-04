# -*- coding: utf-8 -*-
"""
Phase 2 §3 单元测试：TencentFetcher。

覆盖：
1. symbol 转换（_to_tencent_symbol）
2. K 线解析（mock response）
3. 数据标准化（STANDARD_COLUMNS）
4. 边界场景（非 A 股代码 / 空响应 / 无效 JSON）
5. 5 个 Node 1 指数端到端获取
6. DataFetcherManager 注册验证
"""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.tencent_fetcher import TencentFetcher, _to_tencent_symbol
from data_provider.base import DataFetchError, DataFetcherManager


class TestSymbolConversion(unittest.TestCase):
    """测试 _to_tencent_symbol 转换规则。"""

    def test_sh_suffix(self):
        self.assertEqual(_to_tencent_symbol("600519.SH"), "sh600519")
        self.assertEqual(_to_tencent_symbol("000001.SH"), "sh000001")
        self.assertEqual(_to_tencent_symbol("000300.SH"), "sh000300")

    def test_sz_suffix(self):
        self.assertEqual(_to_tencent_symbol("000001.SZ"), "sz000001")
        self.assertEqual(_to_tencent_symbol("399006.SZ"), "sz399006")

    def test_bj_suffix(self):
        self.assertEqual(_to_tencent_symbol("920748.BJ"), "bj920748")

    def test_ss_alias(self):
        """SS 后缀（Yahoo Finance 格式）→ sh。"""
        self.assertEqual(_to_tencent_symbol("600519.SS"), "sh600519")

    def test_prefix_format(self):
        """sh600519 前缀格式 → 保持。"""
        self.assertEqual(_to_tencent_symbol("sh600519"), "sh600519")
        self.assertEqual(_to_tencent_symbol("sz399006"), "sz399006")

    def test_bare_code_sh(self):
        """裸码 6/9 开头推断为上海。"""
        self.assertEqual(_to_tencent_symbol("600519"), "sh600519")
        self.assertEqual(_to_tencent_symbol("920748"), "sh920748")

    def test_bare_code_sz(self):
        """裸码非 6/9 开头推断为深圳。"""
        self.assertEqual(_to_tencent_symbol("000001"), "sz000001")
        self.assertEqual(_to_tencent_symbol("399006"), "sz399006")

    def test_unsupported_returns_none(self):
        """非 A 股代码返回 None。"""
        self.assertIsNone(_to_tencent_symbol("AAPL"))
        self.assertIsNone(_to_tencent_symbol("HK00700"))
        self.assertIsNone(_to_tencent_symbol(""))
        self.assertIsNone(_to_tencent_symbol("csi930955"))


class TestKlineResponseParsing(unittest.TestCase):
    """测试 K 线响应解析（mock HTTP）。"""

    def setUp(self):
        self.fetcher = TencentFetcher()

    def _mock_response(self, symbol, bars):
        """构造 mock HTTP 响应。"""
        payload = {"data": {symbol: {"qfqday": bars}}}
        return json.dumps(payload).encode("utf-8")

    @patch("data_provider.tencent_fetcher.urllib.request.urlopen")
    def test_parse_qfqday(self, mock_urlopen):
        """解析 qfqday 字段。"""
        mock_urlopen.return_value.read.return_value = self._mock_response(
            "sh600519",
            [
                ["2026-09-01", "1800.00", "1820.00", "1830.00", "1790.00", "1000000.00"],
                ["2026-09-02", "1820.00", "1850.00", "1860.00", "1810.00", "1200000.00"],
            ],
        )
        df = self.fetcher._fetch_raw_data("600519.SH", "2026-09-01", "2026-09-02")
        self.assertEqual(len(df), 2)
        self.assertAlmostEqual(df.iloc[0]["close"], 1820.0)
        self.assertEqual(df.iloc[1]["volume"], 1200000)

    @patch("data_provider.tencent_fetcher.urllib.request.urlopen")
    def test_parse_day_fallback(self, mock_urlopen):
        """qfqday 不存在时回退到 day 字段（指数无前复权）。"""
        payload = {"data": {"sh000001": {"day": [
            ["2026-09-03", "3952.790", "3942.090", "3968.110", "3930.450", "496990189.000"],
        ]}}}
        mock_urlopen.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        df = self.fetcher._fetch_raw_data("000001.SH", "2026-09-03", "2026-09-03")
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]["close"], 3942.09)

    @patch("data_provider.tencent_fetcher.urllib.request.urlopen")
    def test_invalid_json_raises_error(self, mock_urlopen):
        """无效 JSON 抛 DataFetchError。"""
        mock_urlopen.return_value.read.return_value = b"not json"
        with self.assertRaises(DataFetchError):
            self.fetcher._fetch_raw_data("600519.SH", "2026-09-01", "2026-09-02")

    @patch("data_provider.tencent_fetcher.urllib.request.urlopen")
    def test_empty_data_raises_error(self, mock_urlopen):
        """空数据响应抛 DataFetchError。"""
        mock_urlopen.return_value.read.return_value = json.dumps(
            {"data": {"sh600519": {}}}
        ).encode("utf-8")
        with self.assertRaises(DataFetchError):
            self.fetcher._fetch_raw_data("600519.SH", "2026-09-01", "2026-09-02")

    def test_unsupported_symbol_raises_error(self):
        """不支持的 symbol 抛 DataFetchError。"""
        with self.assertRaises(DataFetchError):
            self.fetcher._fetch_raw_data("AAPL", "2026-09-01", "2026-09-02")


class TestDataNormalization(unittest.TestCase):
    """测试数据标准化。"""

    def setUp(self):
        self.fetcher = TencentFetcher()

    def test_standard_columns(self):
        """标准化后输出 STANDARD_COLUMNS 8 列。"""
        import pandas as pd
        raw = pd.DataFrame([
            {"date": "2026-09-01", "open": 100, "close": 105, "high": 110, "low": 95, "volume": 1000},
            {"date": "2026-09-02", "open": 105, "close": 102, "high": 108, "low": 100, "volume": 1200},
        ])
        ndf = self.fetcher._normalize_data(raw, "600519")
        expected_cols = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
        self.assertEqual(list(ndf.columns), expected_cols)

    def test_pct_chg_calculated(self):
        """pct_chg 按收盘价计算。"""
        import pandas as pd
        raw = pd.DataFrame([
            {"date": "2026-09-01", "open": 100, "close": 100, "high": 100, "low": 100, "volume": 1000},
            {"date": "2026-09-02", "open": 100, "close": 110, "high": 110, "low": 100, "volume": 1000},
        ])
        ndf = self.fetcher._normalize_data(raw, "600519")
        self.assertAlmostEqual(ndf.iloc[1]["pct_chg"], 10.0, places=1)

    def test_date_sorted(self):
        """日期升序排列。"""
        import pandas as pd
        raw = pd.DataFrame([
            {"date": "2026-09-03", "open": 1, "close": 1, "high": 1, "low": 1, "volume": 1},
            {"date": "2026-09-01", "open": 1, "close": 1, "high": 1, "low": 1, "volume": 1},
        ])
        ndf = self.fetcher._normalize_data(raw, "600519")
        self.assertEqual(ndf.iloc[0]["date"], "2026-09-01")


class TestNode1IndicesEndToEnd(unittest.TestCase):
    """Node 1 5 个指数端到端获取（需网络）。"""

    EXPECTED_INDICES = {
        "000001.SH": "上证指数",
        "000300.SH": "沪深300",
        "000905.SH": "中证500",
        "399006.SZ": "创业板指",
        "000688.SH": "科创50",
    }

    @unittest.skipIf(
        os.environ.get("SKIP_LIVE_TESTS") == "1",
        "SKIP_LIVE_TESTS=1",
    )
    def test_all_5_indices(self):
        """5 个指数均能获取 ≥200 bars。"""
        fetcher = TencentFetcher()
        for code in self.EXPECTED_INDICES:
            df = fetcher._fetch_raw_data(code, "2026-01-01", "2026-09-03")
            self.assertGreaterEqual(
                len(df), 200,
                f"{code} 应获取 ≥200 bars，实际 {len(df)}",
            )
            ndf = fetcher._normalize_data(df, code)
            self.assertIn("close", ndf.columns)
            self.assertGreater(ndf.iloc[-1]["close"], 0)


class TestDataFetcherManagerIntegration(unittest.TestCase):
    """DataFetcherManager 集成验证。"""

    def test_tencent_registered(self):
        """TencentFetcher 已注册到 manager。"""
        mgr = DataFetcherManager()
        names = [f.name for f in mgr._get_fetchers_snapshot()]
        self.assertIn("TencentFetcher", names)

    def test_priority_order(self):
        """TencentFetcher (P1) 排在 mootdx/efinance(P0) 之后。"""
        mgr = DataFetcherManager()
        fetchers = mgr._get_fetchers_snapshot()
        priorities = {f.name: f.priority for f in fetchers}
        self.assertLessEqual(priorities["TencentFetcher"], 1)

    def test_a_index_skips_mootdx_efinance(self):
        """A 股指数路由跳过 Mootdx/Efinance，Tencent 保留。"""
        mgr = DataFetcherManager()
        fetchers = mgr._get_fetchers_snapshot()
        filtered = mgr._filter_fetchers_for_a_index(fetchers)
        names = [f.name for f in filtered]
        self.assertNotIn("MootdxFetcher", names)
        self.assertNotIn("EfinanceFetcher", names)
        self.assertIn("TencentFetcher", names)


if __name__ == "__main__":
    unittest.main()
