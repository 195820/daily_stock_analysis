# -*- coding: utf-8 -*-
"""ths_screener 问财 JSON 解析回归测试。

背景（2026-10-12 故障）：问财把「近5个交易日的dde大单净量合计」在响应里
物化为「区间dde大单净量总和[YYYYMMDD-YYYYMMDD]」措辞，contains 匹配失配
→ dde_5d_sum 静默落 0 → ambush 5 日分组空转，全埋伏板块被单日口径错杀。
本文件锁定：新旧两种列名措辞都必须解析出 5 日值。
"""
from __future__ import annotations

import unittest

from src.services.ths_screener import THSScreenerResult, THSScreenerService


def _row(code: str, name: str, dde_5d_key: str, dde_5d_val) -> dict:
    return {
        "股票代码": f"{code}.SH" if code.startswith("6") else f"{code}.SZ",
        "股票简称": name,
        "最新价": 10.0,
        "最新涨跌幅": "1.23",
        "dde大单净量[20261012]": "0.154",
        dde_5d_key: dde_5d_val,
        "换手率": "2.5",
        "成交量": "123456",
        "所属板块": "",
        "行业简称": "",
    }


def _parse(rows: list):
    svc = THSScreenerService.__new__(THSScreenerService)  # 不触碰实例状态
    result = THSScreenerResult(query="test")
    svc._parse_json_api({"data": {"data": rows}}, result)
    return result.stocks


class TestParseDde5dSum(unittest.TestCase):

    def test_new_interval_column_name_parsed(self):
        """新措辞「区间dde大单净量总和[区间]」必须解析出 5 日值（2026-10-12 实测键名）"""
        stocks = _parse([_row(
            "600988", "赤峰黄金",
            "区间dde大单净量总和[20260929-20261012]", 0.486,
        )])
        self.assertEqual(len(stocks), 1)
        self.assertAlmostEqual(stocks[0].dde_5d_sum, 0.486)
        self.assertAlmostEqual(stocks[0].dde_net_pct, 0.154)  # 单日列不受影响

    def test_legacy_total_column_name_parsed(self):
        """旧措辞「近5个交易日的dde大单净量合计:xxx」保持兼容"""
        stocks = _parse([_row(
            "601069", "西部黄金",
            "近5个交易日的dde大单净量合计:20261012", 0.463,
        )])
        self.assertAlmostEqual(stocks[0].dde_5d_sum, 0.463)

    def test_missing_5d_column_stays_zero(self):
        """主线查询未请求 5 日列 → 维持默认 0（THSStock 契约）"""
        stocks = _parse([_row("000975", "山金国际", "无关列", 1.0)])
        self.assertEqual(stocks[0].dde_5d_sum, 0.0)

    def test_distinct_from_single_day_columns(self):
        """单日/净流入/净额等相近列名不得误当 5 日合计"""
        row = _row("002345", "潮宏基", "无关列", 1.0)
        row["dde大单净流入量[20261012]"] = "999"
        row["最新dde大单净额"] = "888"
        stocks = _parse([row])
        self.assertEqual(stocks[0].dde_5d_sum, 0.0)

    def test_float_passthrough_no_unit_suffix(self):
        """实测新列值为 float（无亿/万后缀），str 数值同样安全解析"""
        stocks = _parse([_row(
            "301265", "华新科技",
            "区间dde大单净量总和[20260929-20261012]", 1.886,
        )])
        self.assertAlmostEqual(stocks[0].dde_5d_sum, 1.886)


if __name__ == "__main__":
    unittest.main()
