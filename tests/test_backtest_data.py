# -*- coding: utf-8 -*-
"""冻结行情快照读取层单测：日期口径、缺口、前收、qfq 对齐。

回归重点：归档目录名 `YYYYMMDD` 与快照 ISO 日期混用曾导致订单全部静默丢弃
（`next_day` 返回 None），本项目不允许该类错误再次无声发生。
"""

from __future__ import annotations

import unittest
from datetime import date

from src.got.backtest_data import PriceSnapshot, normalize_day


def payload() -> dict:
    rows = [
        ["2026-08-03", 10.0, 10.5, 9.8, 10.2, 1000.0],
        ["2026-08-05", 10.3, 10.6, 10.0, 10.4, 900.0],
        ["2026-08-06", 10.4, 10.8, 10.1, 10.7, 800.0],
    ]
    return {
        "meta": {"built_at": "2026-11-05T10:00:00"},
        "symbols": {
            "600000": {
                "name": "测试",
                "raw": rows,
                "qfq_close": [["2026-08-03", 9.0], ["2026-08-05", 9.2], ["2026-08-06", 9.5]],
            }
        },
    }


class TestSnapshot(unittest.TestCase):
    def setUp(self) -> None:
        self.snap = PriceSnapshot(payload())

    def test_normalize_day(self):
        self.assertEqual(normalize_day("20260805"), "2026-08-05")
        self.assertEqual(normalize_day("2026-08-05"), "2026-08-05")

    def test_next_day_accepts_both_formats_and_respects_gaps(self):
        # 08-04 在快照中缺档（停牌/未采集）→ 必须跳到 08-05，不能落首日
        self.assertEqual(self.snap.next_day("20260803"), "2026-08-05")
        self.assertEqual(self.snap.next_day("2026-08-03"), "2026-08-05")
        # 缺档日本身不在日历 → 仍取严格更晚的日期
        self.assertEqual(self.snap.next_day("20260804"), "2026-08-05")

    def test_next_day_end_of_calendar(self):
        self.assertIsNone(self.snap.next_day("20260806"))

    def test_bar_prev_close_and_missing(self):
        bar = self.snap.bar("600000", "20260805")
        self.assertEqual(bar.trade_date, date(2026, 8, 5))
        self.assertAlmostEqual(bar.prev_close, 10.2)   # 08-03 收盘
        self.assertAlmostEqual(bar.open, 10.3)
        self.assertIsNone(self.snap.bar("600000", "20260804"))
        self.assertIsNone(self.snap.bar("999999", "20260805"))

    def test_first_bar_has_no_prev_close(self):
        self.assertIsNone(self.snap.bar("600000", "20260803").prev_close)

    def test_qfq_closes_and_corporate_action_detection(self):
        self.assertAlmostEqual(self.snap.qfq_close("600000", "20260805"), 9.2)
        # 构造一次除权：raw 收益与 qfq 收益差 >0.5pp
        sharp = payload()
        sharp["symbols"]["600000"]["raw"][1][4] = 8.0     # 08-05 收盘从 10.4 改成 8.0
        hits = PriceSnapshot(sharp).corporate_action_days()
        self.assertIn("600000:2026-08-05", hits)
        # 08-06 的 raw 收益率也被同一处改动污染 → 同日簇检测会一并标记，属预期
        self.assertIn("600000:2026-08-06", hits)


if __name__ == "__main__":
    unittest.main()
