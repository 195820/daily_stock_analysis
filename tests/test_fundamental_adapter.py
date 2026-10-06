# -*- coding: utf-8 -*-
"""
Tests for fundamental adapter helpers.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from importlib.util import find_spec
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.fundamental_adapter import (  # noqa: E402
    REPORT_TABLE_MAX_ENTRIES,
    AkshareFundamentalAdapter,
    _build_dividend_payload,
    _extract_latest_row,
    _filter_rows_by_code,
    _parse_dividend_plan_to_per_share,
    _recent_report_periods,
)


def _yjyg_table(rows) -> pd.DataFrame:
    """复刻 ak.stock_yjyg_em(date=...) 的固定列序（akshare 1.18.91 实测）。"""
    return pd.DataFrame(
        [
            {
                "序号": i + 1,
                "股票代码": r["code"],
                "股票简称": r["name"],
                "预测指标": "归属于上市公司股东的净利润",
                "业绩变动": r["text"],
                "业绩变动幅度": r.get("pct"),
                "业绩变动原因": "略",
                "预告类型": r["type"],
                "上年同期值": -18320000.0,
                "公告日期": "2026-08-19",
            }
            for i, r in enumerate(rows)
        ]
    )


class _FakeAk:
    """按 (接口, 报告期) 返回预置表并记录调用，不触网。"""

    def __init__(self, tables):
        self.tables = tables
        self.calls = []

    def _get(self, func, date):
        self.calls.append((func, date))
        value = self.tables.get((func, date))
        if isinstance(value, Exception):
            raise value
        return pd.DataFrame() if value is None else value

    def stock_yjyg_em(self, date="20200331"):
        return self._get("stock_yjyg_em", date)

    def stock_yjbb_em(self, date="20200331"):
        return self._get("stock_yjbb_em", date)

    def stock_yjkb_em(self, date="20211231"):
        return self._get("stock_yjkb_em", date)


class TestFundamentalAdapter(unittest.TestCase):
    def test_parse_dividend_plan_to_per_share_supports_cn_patterns(self) -> None:
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("10派3元(含税)"), 0.3, places=6)
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("每10股派发2.5元"), 0.25, places=6)
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("每股派0.8元"), 0.8, places=6)
        self.assertIsNone(_parse_dividend_plan_to_per_share("仅送股，不现金分红"))

    def test_extract_latest_row_returns_none_when_code_mismatch(self) -> None:
        df = pd.DataFrame(
            {
                "股票代码": ["600000", "000001"],
                "值": [1, 2],
            }
        )
        row = _extract_latest_row(df, "600519")
        self.assertIsNone(row)

    def test_extract_latest_row_fallback_when_no_code_column(self) -> None:
        df = pd.DataFrame({"值": [1, 2]})
        row = _extract_latest_row(df, "600519")
        self.assertIsNotNone(row)
        self.assertEqual(row["值"], 1)

    def test_dragon_tiger_no_match_with_code_column_is_ok(self) -> None:
        adapter = AkshareFundamentalAdapter()
        df = pd.DataFrame(
            {
                "股票代码": ["600000"],
                "日期": ["2026-01-01"],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(df, "stock_lhb_stock_statistic_em", [])):
            result = adapter.get_dragon_tiger_flag("600519")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["is_on_list"])
        self.assertEqual(result["recent_count"], 0)

    def test_dragon_tiger_match_is_ok(self) -> None:
        adapter = AkshareFundamentalAdapter()
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "日期": [today],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(df, "stock_lhb_stock_statistic_em", [])):
            result = adapter.get_dragon_tiger_flag("600519")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["is_on_list"])
        self.assertGreaterEqual(result["recent_count"], 1)

    def test_fundamental_bundle_includes_financial_report_and_dividend_payload(self) -> None:
        adapter = AkshareFundamentalAdapter()
        now = datetime.now()
        within_ttm = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        future_day = (now + timedelta(days=10)).strftime("%Y-%m-%d")
        old_day = (now - timedelta(days=500)).strftime("%Y-%m-%d")
        fin_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "报告期": [within_ttm],
                "营业总收入": [1000.0],
                "归母净利润": [300.0],
                "经营活动产生的现金流量净额": [500.0],
                "净资产收益率": [18.2],
                "营业收入同比": [12.0],
                "净利润同比": [9.5],
            }
        )
        forecast_df = _yjyg_table(
            [{"code": "600519", "name": "贵州茅台", "text": "预计净利润盈利:100万元", "type": "预增", "pct": 10.0}]
        )
        quick_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "每股收益": [50.0],
                "营业收入-营业收入": [80000000000.0],
                "营业收入-去年同期": [70000000000.0],
                "净利润-净利润": [30000000000.0],
                "净利润-去年同期": [27000000000.0],
                "净利润-同比增长": [11.1],
                "所处行业": ["白酒"],
                "公告日期": ["2026-08-15"],
            }
        )
        dividend_df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519", "600519", "600519"],
                "除息日": [within_ttm, within_ttm, future_day, old_day],
                "分配方案": ["10派3元(含税)", "10派3元(含税)", "10派5元", "10派1元"],
            }
        )

        # 业绩预告/快报现在走 _fetch_report_rows（按报告期取全市场表），
        # 其余块仍走 _call_df_candidates（fin → dividend → institution → top10）
        with patch.object(
            adapter,
            "_call_df_candidates",
            side_effect=[
                (fin_df, "stock_financial_abstract", []),
                (dividend_df, "stock_fhps_detail_em", []),
                (None, None, []),
                (None, None, []),
            ],
        ) as df_calls, patch.object(
            adapter,
            "_fetch_report_rows",
            side_effect=[
                (forecast_df, "stock_yjyg_em"),
                (quick_df, "stock_yjkb_em"),
            ],
        ) as report_calls:
            result = adapter.get_fundamental_bundle("600519")

        # 预告链路只包 yjyg（2026-10-06 取证：降级源 yjbb 会把「公告日期」当摘要），
        # 快报链路只包 yjkb；两者都不得再传 symbol。
        self.assertEqual(
            [c.args[0] for c in report_calls.call_args_list],
            [("stock_yjyg_em",), ("stock_yjkb_em",)],
        )
        self.assertEqual(df_calls.call_count, 4)

        financial_report = result["earnings"].get("financial_report", {})
        self.assertEqual(financial_report.get("report_date"), within_ttm)
        self.assertEqual(financial_report.get("revenue"), 1000.0)
        self.assertEqual(financial_report.get("net_profit_parent"), 300.0)
        self.assertEqual(financial_report.get("operating_cash_flow"), 500.0)
        self.assertEqual(financial_report.get("roe"), 18.2)
        self.assertEqual(
            result["earnings"].get("forecast_summary"), "预计净利润盈利:100万元"
        )
        # 「预告类型」以前整列丢弃，现在必须出现在 earnings 块里（风险判定的直接依据）
        self.assertEqual(result["earnings"].get("forecast_type"), "预增")
        # 快报表里没有摘要文本列 → 必须落结构化数值，不得把公告日期当摘要
        self.assertNotIn("quick_report_summary", result["earnings"])
        self.assertEqual(
            result["earnings"].get("quick_report"),
            {
                "revenue": 80000000000.0,
                "net_profit": 30000000000.0,
                "net_profit_yoy": 11.1,
                "announce_date": "2026-08-15",
            },
        )

        dividend_payload = result["earnings"].get("dividend", {})
        events = dividend_payload.get("events", [])
        self.assertEqual(len(events), 2)  # duplicate + future day filtered
        self.assertEqual(dividend_payload.get("ttm_event_count"), 1)
        self.assertAlmostEqual(dividend_payload.get("ttm_cash_dividend_per_share"), 0.3, places=6)

    def test_build_dividend_payload_returns_empty_when_code_not_matched(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "除息日": [now],
                "分配方案": ["10派3元(含税)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload, {})

    def test_build_dividend_payload_skips_after_tax_plan(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "除息日": [now],
                "分配方案": ["10派3元(税后)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload, {})

    def test_build_dividend_payload_ttm_window_boundary(self) -> None:
        now = datetime.now()
        day_365 = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        day_366 = (now - timedelta(days=366)).strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519"],
                "除息日": [day_365, day_366],
                "分配方案": ["10派3元(含税)", "10派5元(含税)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload.get("ttm_event_count"), 1)
        self.assertAlmostEqual(payload.get("ttm_cash_dividend_per_share"), 0.3, places=6)


class TestReportPeriodTables(unittest.TestCase):
    """业绩预告/快报/报表＝「按报告期返回全市场表」的接口。

    回归本体的 bug：akshare 这三个签名只收 date、不收 symbol，旧候选用
    {"symbol": code} 调用必然 TypeError → 退到空参 → 拿到 2020Q1/2021年报 陈旧
    报告期再按代码过滤 → 预亏通路长期为空。本类同订三件事：
    只传 date、逐报告期新→旧、全市场表进程内缓存（逐股不重拉）。
    """

    def setUp(self) -> None:
        self.adapter = AkshareFundamentalAdapter()

    def test_recent_report_periods_newest_first_and_only_ended(self) -> None:
        self.assertEqual(
            _recent_report_periods(datetime(2026, 10, 6)), ["20260930", "20260630"]
        )
        # 1 月中：年报预告窗口（报告期为上一年 12-31），不行到将来
        self.assertEqual(
            _recent_report_periods(datetime(2026, 1, 15)), ["20251231", "20250930"]
        )
        self.assertEqual(
            _recent_report_periods(datetime(2026, 10, 6), count=3),
            ["20260930", "20260630", "20260331"],
        )

    def test_filter_rows_by_code_strict_never_falls_back_to_whole_table(self) -> None:
        df = pd.DataFrame({"股票简称": ["别的股"], "预告类型": ["预增"]})
        self.assertTrue(_filter_rows_by_code(df, "600519", strict=True).empty)
        # 默认口径不变（历史调用方依赖「无代码列返回原表」）
        self.assertIs(_filter_rows_by_code(df, "600519"), df)

    def test_get_report_table_passes_date_only_and_caches_per_period(self) -> None:
        fake = _FakeAk({("stock_yjyg_em", "20260630"): _yjyg_table(
            [{"code": "600187", "name": "*ST国中", "text": "预计盈利", "type": "扭亏", "pct": 115.83}]
        )})
        errors: list = []
        first = self.adapter._get_report_table(fake, "stock_yjyg_em", "20260630", errors)
        second = self.adapter._get_report_table(fake, "stock_yjyg_em", "20260630", errors)
        self.assertEqual(fake.calls, [("stock_yjyg_em", "20260630")])  # 第2次走缓存
        self.assertIs(first, second)
        self.assertEqual(errors, [])
        self.assertIsNotNone(_filter_rows_by_code(second, "600187", strict=True))

    def test_get_report_table_records_error_and_treats_empty_table_as_miss(self) -> None:
        fake = _FakeAk({("stock_yjyg_em", "20260930"): TypeError("boom")})
        errors: list = []
        self.assertIsNone(self.adapter._get_report_table(fake, "stock_yjyg_em", "20260930", errors))
        self.assertEqual(errors, ["stock_yjyg_em:TypeError"])
        # 空表不写缓存（否则一次瞬时性空返会把该报告期钉住 TTL 时长）
        self.assertEqual(self.adapter._report_tables, {})
        self.assertIsNone(self.adapter._get_report_table(fake, "stock_yjkb_em", "20260630", errors))

    def test_get_report_table_evicts_oldest_beyond_max_entries(self) -> None:
        seeded = {
            ("stock_yjyg_em", f"P{i}"): (float(i), _yjyg_table(
                [{"code": "600187", "name": "x", "text": "t", "type": "首亏"}]
            ))
            for i in range(REPORT_TABLE_MAX_ENTRIES)
        }
        self.adapter._report_tables = dict(seeded)
        fake = _FakeAk({("stock_yjyg_em", "NEW"): _yjyg_table(
            [{"code": "600187", "name": "x", "text": "t", "type": "首亏"}]
        )})
        self.adapter._get_report_table(fake, "stock_yjyg_em", "NEW", [])
        self.assertEqual(len(self.adapter._report_tables), REPORT_TABLE_MAX_ENTRIES)
        self.assertNotIn(("stock_yjyg_em", "P0"), self.adapter._report_tables)
        self.assertIn(("stock_yjyg_em", "NEW"), self.adapter._report_tables)

    def test_fetch_report_rows_prefers_newest_period_then_fallback_source(self) -> None:
        table = _yjyg_table([{"code": "600187", "name": "*ST国中", "text": "预计盈利", "type": "扭亏"}])
        fake = _FakeAk({
            ("stock_yjyg_em", "20260930"): pd.DataFrame(),   # 新期未披露
            ("stock_yjyg_em", "20260630"): table,            # 命中
            ("stock_yjbb_em", "20260930"): table,            # 不应被访问
        })
        errors: list = []
        with patch("data_provider.fundamental_adapter._recent_report_periods",
                   return_value=["20260930", "20260630"]), \
                patch.dict(sys.modules, {"akshare": fake}):
            rows, source = self.adapter._fetch_report_rows(
                ("stock_yjyg_em", "stock_yjbb_em"), "600187", errors
            )
        self.assertEqual(source, "stock_yjyg_em")
        self.assertEqual(rows["预告类型"].tolist(), ["扭亏"])
        self.assertEqual(fake.calls, [("stock_yjyg_em", "20260930"), ("stock_yjyg_em", "20260630")])

    def test_fetch_report_rows_falls_back_to_secondary_source(self) -> None:
        bb = pd.DataFrame([{"股票代码": "600187", "净利润": 1.0}])
        fake = _FakeAk({("stock_yjbb_em", "20260630"): bb})
        with patch("data_provider.fundamental_adapter._recent_report_periods",
                   return_value=["20260630"]), patch.dict(sys.modules, {"akshare": fake}):
            rows, source = self.adapter._fetch_report_rows(
                ("stock_yjyg_em", "stock_yjbb_em"), "600187", []
            )
        self.assertEqual(source, "stock_yjbb_em")
        self.assertEqual(len(rows), 1)

    def test_fetch_report_rows_returns_none_when_stock_absent_everywhere(self) -> None:
        fake = _FakeAk({("stock_yjyg_em", "20260630"): _yjyg_table(
            [{"code": "000001", "name": "x", "text": "t", "type": "预减"}]
        )})
        errors: list = []
        with patch("data_provider.fundamental_adapter._recent_report_periods",
                   return_value=["20260630"]), patch.dict(sys.modules, {"akshare": fake}):
            rows, source = self.adapter._fetch_report_rows(("stock_yjyg_em",), "600187", errors)
        self.assertIsNone(rows)
        self.assertIsNone(source)
        # 拿到过表、只是本股无行 → 这是「该股没有预告」的正常缺失，不得计为错误
        self.assertEqual(errors, [])

    def test_unpublished_period_error_is_dropped_when_older_period_hits(self) -> None:
        """未公布的报告期在 akshare 内部报 TypeError（实测）：命中旧期就不能再上报它。

        否则每次调用都带一条假错，会把整个 earnings 块误标成 failed。
        """
        quick = pd.DataFrame([{"股票代码": "600187", "净利润-净利润": 1.0}])
        fake = _FakeAk({
            ("stock_yjkb_em", "20260930"): TypeError("'NoneType' object is not subscriptable"),
            ("stock_yjkb_em", "20260630"): quick,
        })
        errors: list = []
        with patch("data_provider.fundamental_adapter._recent_report_periods",
                   return_value=["20260930", "20260630"]), patch.dict(sys.modules, {"akshare": fake}):
            rows, source = self.adapter._fetch_report_rows(("stock_yjkb_em",), "600187", errors)
        self.assertEqual(source, "stock_yjkb_em")
        self.assertEqual(errors, [])

    def test_total_miss_reports_each_error_only_once(self) -> None:
        fake = _FakeAk({
            ("stock_yjkb_em", "20260930"): TypeError("a"),
            ("stock_yjkb_em", "20260630"): TypeError("b"),
        })
        errors: list = []
        with patch("data_provider.fundamental_adapter._recent_report_periods",
                   return_value=["20260930", "20260630"]), patch.dict(sys.modules, {"akshare": fake}):
            rows, source = self.adapter._fetch_report_rows(("stock_yjkb_em",), "600187", errors)
        self.assertIsNone(rows)
        self.assertEqual(errors, ["stock_yjkb_em:TypeError"])

    def test_source_never_passes_symbol_to_period_table_apis(self) -> None:
        """静态守卫：防止有人再把 symbol 候选加回业绩类接口。"""
        import re

        from data_provider import fundamental_adapter as module

        with open(module.__file__, encoding="utf-8") as fh:
            src = fh.read()
        for func in ("stock_yjyg_em", "stock_yjbb_em", "stock_yjkb_em"):
            self.assertEqual(
                re.findall(rf'"{func}",\s*\{{\s*"symbol"', src), [],
                msg=f"{func} 只收 date，出现 symbol 候选即为回归",
            )

    @unittest.skipIf(find_spec("akshare") is None, "akshare 未安装")
    def test_installed_akshare_period_apis_take_only_date(self) -> None:
        """本 WI 的取证根据：真实签名里没有 symbol。"""
        import inspect

        import akshare as ak

        for func in ("stock_yjyg_em", "stock_yjbb_em", "stock_yjkb_em"):
            params = inspect.signature(getattr(ak, func)).parameters
            self.assertIn("date", params, msg=func)
            self.assertNotIn("symbol", params, msg=func)


if __name__ == "__main__":
    unittest.main()
