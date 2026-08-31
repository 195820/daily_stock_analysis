# -*- coding: utf-8 -*-
"""
Phase 4 单元测试：Node 4 催化剂兑现轨迹扩展（PEAD 多窗口分析）。

覆盖：
1. _compute_multi_window_returns — T+1/3/5 收益计算
2. _determine_pead_direction — aligned / counter / neutral 判定
3. _find_event_date_index — 精确匹配 + 回退查找
4. _compute_sector_return — 修正后的 API 调用
5. check_and_record_realization — 集成多窗口 + PEAD 字段写入
6. get_pead_analysis_report — 分组统计报告
7. CatalystRealizationRecord 新字段向后兼容
"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _make_kline_df(rows):
    """构造板块 K 线 DataFrame。

    rows: [(date_str, open, close), ...]
    """
    return pd.DataFrame(rows, columns=["date", "open", "close"])


class TestComputeMultiWindowReturns(unittest.TestCase):
    """测试 _compute_multi_window_returns 多窗口收益计算。"""

    def setUp(self):
        from src.got.catalyst_realization import CatalystRealizationTracker
        self.tracker = CatalystRealizationTracker()

    def test_all_three_windows(self):
        """T+1/3/5 均有数据时全部返回。"""
        kline = _make_kline_df([
            ("2026-08-01", 100, 102),  # event_date (base)
            ("2026-08-04", 102, 105),  # T+1
            ("2026-08-05", 105, 104),  # T+2
            ("2026-08-06", 104, 108),  # T+3
            ("2026-08-07", 108, 107),  # T+4
            ("2026-08-08", 107, 110),  # T+5
        ])

        with patch.object(self.tracker, "_get_board_code", return_value=("880001", "exact")), \
             patch.object(self.tracker, "_fetch_board_kline", return_value=kline):
            result = self.tracker._compute_multi_window_returns("半导体", date(2026, 8, 1))

        self.assertIsNotNone(result["day_1"])
        self.assertIsNotNone(result["day_3"])
        self.assertIsNotNone(result["day_5"])
        # T+1: (105-102)/102*100 ≈ 2.94%
        self.assertAlmostEqual(result["day_1"], (105 - 102) / 102 * 100, places=2)
        # T+3: (108-102)/102*100 ≈ 5.88%
        self.assertAlmostEqual(result["day_3"], (108 - 102) / 102 * 100, places=2)
        # T+5: (110-102)/102*100 ≈ 7.84%
        self.assertAlmostEqual(result["day_5"], (110 - 102) / 102 * 100, places=2)

    def test_partial_windows(self):
        """K 线仅 3 行 → 只有 T+1，T+3/T+5 为 None。"""
        kline = _make_kline_df([
            ("2026-08-01", 100, 100),
            ("2026-08-04", 100, 105),
            ("2026-08-05", 105, 103),
        ])

        with patch.object(self.tracker, "_get_board_code", return_value=("880001", "exact")), \
             patch.object(self.tracker, "_fetch_board_kline", return_value=kline):
            result = self.tracker._compute_multi_window_returns("半导体", date(2026, 8, 1))

        self.assertIsNotNone(result["day_1"])
        self.assertIsNone(result["day_3"])
        self.assertIsNone(result["day_5"])
        self.assertEqual(result["max_window"], 1)

    def test_no_board_code(self):
        """板块代码查不到 → 全 None。"""
        with patch.object(self.tracker, "_get_board_code", return_value=(None, "unresolved")):
            result = self.tracker._compute_multi_window_returns("未知板块", date(2026, 8, 1))

        self.assertIsNone(result["day_1"])
        self.assertIsNone(result["max_window"])

    def test_event_date_not_found(self):
        """K 线中无 event_date → 全 None。"""
        kline = _make_kline_df([
            ("2026-07-01", 100, 102),
            ("2026-07-02", 102, 104),
        ])

        with patch.object(self.tracker, "_get_board_code", return_value=("880001", "exact")), \
             patch.object(self.tracker, "_fetch_board_kline", return_value=kline):
            result = self.tracker._compute_multi_window_returns("半导体", date(2026, 8, 1))

        self.assertIsNone(result["day_1"])

    def test_max_window_calculation(self):
        """max_window 应标记最大绝对收益出现的窗口。"""
        kline = _make_kline_df([
            ("2026-08-01", 100, 100),  # base
            ("2026-08-04", 100, 103),  # T+1: +3%
            ("2026-08-05", 103, 102),
            ("2026-08-06", 102, 97),   # T+3: -3%
            ("2026-08-07", 97, 96),
            ("2026-08-08", 96, 95),    # T+5: -5%
        ])

        with patch.object(self.tracker, "_get_board_code", return_value=("880001", "exact")), \
             patch.object(self.tracker, "_fetch_board_kline", return_value=kline):
            result = self.tracker._compute_multi_window_returns("半导体", date(2026, 8, 1))

        # T+5 绝对收益最大 (-5%)
        self.assertEqual(result["max_window"], 5)
        self.assertAlmostEqual(result["max_pct"], (95 - 100) / 100 * 100, places=2)

    def test_empty_kline(self):
        """K 线为空 → 全 None。"""
        with patch.object(self.tracker, "_get_board_code", return_value=("880001", "exact")), \
             patch.object(self.tracker, "_fetch_board_kline", return_value=pd.DataFrame()):
            result = self.tracker._compute_multi_window_returns("半导体", date(2026, 8, 1))

        self.assertIsNone(result["day_1"])


class TestDeterminePeadDirection(unittest.TestCase):
    """测试 _determine_pead_direction 方向判定。"""

    def setUp(self):
        from src.got.catalyst_realization import CatalystRealizationTracker
        self.tracker = CatalystRealizationTracker()

    def test_aligned_positive(self):
        """positive 事件 + 多数窗口正收益 → aligned。"""
        result = self.tracker._determine_pead_direction(
            "positive", {"day_1": 2.0, "day_3": 3.5, "day_5": 5.0}
        )
        self.assertEqual(result, "aligned")

    def test_counter_positive(self):
        """positive 事件 + 全部窗口负收益 → counter。"""
        result = self.tracker._determine_pead_direction(
            "positive", {"day_1": -1.0, "day_3": -2.0, "day_5": -3.0}
        )
        self.assertEqual(result, "counter")

    def test_neutral_mixed(self):
        """positive 事件 + 1正2负 → neutral（未达多数也未全反）。"""
        result = self.tracker._determine_pead_direction(
            "positive", {"day_1": 2.0, "day_3": -1.0, "day_5": -0.5}
        )
        self.assertEqual(result, "neutral")

    def test_aligned_negative(self):
        """negative 事件 + 多数窗口负收益 → aligned。"""
        result = self.tracker._determine_pead_direction(
            "negative", {"day_1": -2.0, "day_3": -1.5, "day_5": -3.0}
        )
        self.assertEqual(result, "aligned")

    def test_binary_aligned(self):
        """binary 事件 + 大幅波动（|r|>1%）→ aligned。"""
        result = self.tracker._determine_pead_direction(
            "binary", {"day_1": 3.0, "day_3": -2.5, "day_5": 4.0}
        )
        self.assertEqual(result, "aligned")

    def test_binary_counter(self):
        """binary 事件 + 全部小幅波动（|r|<1%）→ counter。"""
        result = self.tracker._determine_pead_direction(
            "binary", {"day_1": 0.5, "day_3": -0.3, "day_5": 0.8}
        )
        self.assertEqual(result, "counter")

    def test_no_data(self):
        """全部 None → neutral。"""
        result = self.tracker._determine_pead_direction(
            "positive", {"day_1": None, "day_3": None, "day_5": None}
        )
        self.assertEqual(result, "neutral")

    def test_partial_data_aligned(self):
        """只有 1 个窗口有数据且方向一致 → aligned。"""
        result = self.tracker._determine_pead_direction(
            "positive", {"day_1": 2.0, "day_3": None, "day_5": None}
        )
        self.assertEqual(result, "aligned")


class TestFindEventDateIndex(unittest.TestCase):
    """测试 _find_event_date_index 日期查找。"""

    def test_exact_string_match(self):
        """日期列是字符串，精确匹配。"""
        from src.got.catalyst_realization import CatalystRealizationTracker
        df = _make_kline_df([
            ("2026-08-01", 100, 100),
            ("2026-08-04", 100, 105),
            ("2026-08-05", 105, 103),
        ])
        idx = CatalystRealizationTracker._find_event_date_index(df, date(2026, 8, 4))
        self.assertEqual(idx, 1)

    def test_fallback_nearby(self):
        """精确匹配失败，回退到 ±1 天。"""
        from src.got.catalyst_realization import CatalystRealizationTracker
        # event_date 是周末，K 线只有周五
        df = _make_kline_df([
            ("2026-07-31", 100, 100),  # 周五
            ("2026-08-03", 100, 105),  # 周一
        ])
        # 8/1 是周六，应回退到 7/31 或 8/3
        idx = CatalystRealizationTracker._find_event_date_index(df, date(2026, 8, 1))
        self.assertIsNotNone(idx)

    def test_not_found(self):
        """超出回退范围 → None。"""
        from src.got.catalyst_realization import CatalystRealizationTracker
        df = _make_kline_df([
            ("2026-07-01", 100, 100),
        ])
        idx = CatalystRealizationTracker._find_event_date_index(df, date(2026, 8, 1))
        self.assertIsNone(idx)


class TestCheckAndRecordRealization(unittest.TestCase):
    """测试 check_and_record_realization 集成多窗口 + PEAD。"""

    def _make_mock_record(self):
        """构造 mock CatalystRealizationRecord"""
        record = MagicMock()
        record.event_id = "test_event_001"
        record.sector = "半导体"
        record.event_date = date(2026, 8, 1)
        record.direction = "positive"
        record.is_realized = False
        return record

    def test_writes_multi_window_fields(self):
        """兑现时写入 day_1/3/5_return + max_return_window + pead_direction。"""
        from src.got.catalyst_realization import CatalystRealizationTracker
        tracker = CatalystRealizationTracker()

        mock_record = self._make_mock_record()
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = [mock_record]
        tracker.db.get_session = MagicMock()
        tracker.db.get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        tracker.db.get_session.return_value.__exit__ = MagicMock(return_value=False)

        window_returns = {"day_1": 2.0, "day_3": 3.5, "day_5": 5.0, "max_window": 5, "max_pct": 5.0}
        with patch.object(tracker, "_compute_multi_window_returns", return_value=window_returns):
            count = tracker.check_and_record_realization(target_date=date(2026, 8, 10))

        self.assertEqual(count, 1)
        self.assertTrue(mock_record.is_realized)
        self.assertEqual(mock_record.day_1_return, 2.0)
        self.assertEqual(mock_record.day_3_return, 3.5)
        self.assertEqual(mock_record.day_5_return, 5.0)
        self.assertEqual(mock_record.max_return_window, 5)
        self.assertEqual(mock_record.max_return_pct, 5.0)
        self.assertEqual(mock_record.pead_direction, "aligned")

    def test_skips_when_no_window_data(self):
        """多窗口全无数据 → 跳过，不标记兑现。"""
        from src.got.catalyst_realization import CatalystRealizationTracker
        tracker = CatalystRealizationTracker()

        mock_record = self._make_mock_record()
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = [mock_record]
        tracker.db.get_session = MagicMock()
        tracker.db.get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        tracker.db.get_session.return_value.__exit__ = MagicMock(return_value=False)

        no_data = {"day_1": None, "day_3": None, "day_5": None, "max_window": None, "max_pct": None}
        with patch.object(tracker, "_compute_multi_window_returns", return_value=no_data):
            count = tracker.check_and_record_realization(target_date=date(2026, 8, 10))

        self.assertEqual(count, 0)
        self.assertFalse(mock_record.is_realized)


class TestGetPeadAnalysisReport(unittest.TestCase):
    """测试 get_pead_analysis_report 分组统计。"""

    def test_empty_returns_zero_report(self):
        """无已兑现事件 → 全零报告。"""
        from src.got.catalyst_realization import CatalystRealizationTracker
        tracker = CatalystRealizationTracker()

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []
        tracker.db.get_session = MagicMock()
        tracker.db.get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        tracker.db.get_session.return_value.__exit__ = MagicMock(return_value=False)

        report = tracker.get_pead_analysis_report()
        self.assertEqual(report["total_realized"], 0)
        self.assertEqual(report["overall"]["avg_day_1"], 0)

    def test_grouped_stats(self):
        """多事件按 event_type 分组统计。"""
        from src.got.catalyst_realization import CatalystRealizationTracker
        tracker = CatalystRealizationTracker()

        records = []
        for i, (etype, d1, d3, d5, pead) in enumerate([
            ("earnings", 2.0, 3.0, 5.0, "aligned"),
            ("earnings", 1.0, 2.0, 4.0, "aligned"),
            ("narrative", -1.0, -2.0, -3.0, "counter"),
        ]):
            r = MagicMock()
            r.event_type = etype
            r.category_tag = "drift"
            r.day_1_return = d1
            r.day_3_return = d3
            r.day_5_return = d5
            r.max_return_window = 5
            r.max_return_pct = d5
            r.pead_direction = pead
            records.append(r)

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = records
        tracker.db.get_session = MagicMock()
        tracker.db.get_session.return_value.__enter__ = MagicMock(return_value=mock_session)
        tracker.db.get_session.return_value.__exit__ = MagicMock(return_value=False)

        report = tracker.get_pead_analysis_report()
        self.assertEqual(report["total_realized"], 3)

        # earnings 分组：2 个事件，avg_day_1 = 1.5
        earnings = report["by_event_type"]["earnings"]
        self.assertEqual(earnings["count"], 2)
        self.assertAlmostEqual(earnings["avg_day_1"], 1.5)
        self.assertAlmostEqual(earnings["aligned_rate"], 1.0)

        # narrative 分组：1 个事件，aligned_rate = 0
        narrative = report["by_event_type"]["narrative"]
        self.assertEqual(narrative["count"], 1)
        self.assertAlmostEqual(narrative["aligned_rate"], 0.0)

        # category_tag 分组
        self.assertIn("drift", report["by_category_tag"])


class TestCatalystRealizationRecordBackwardCompat(unittest.TestCase):
    """测试 CatalystRealizationRecord 新字段向后兼容。"""

    def test_new_columns_exist(self):
        """新字段在 ORM 模型上存在。"""
        from src.storage import CatalystRealizationRecord
        self.assertTrue(hasattr(CatalystRealizationRecord, "day_1_return"))
        self.assertTrue(hasattr(CatalystRealizationRecord, "day_3_return"))
        self.assertTrue(hasattr(CatalystRealizationRecord, "day_5_return"))
        self.assertTrue(hasattr(CatalystRealizationRecord, "max_return_window"))
        self.assertTrue(hasattr(CatalystRealizationRecord, "max_return_pct"))
        self.assertTrue(hasattr(CatalystRealizationRecord, "pead_direction"))

    def test_new_columns_nullable(self):
        """新字段均 nullable（默认 None），旧记录不受影响。"""
        from src.storage import CatalystRealizationRecord
        for col_name in ("day_1_return", "day_3_return", "day_5_return",
                         "max_return_window", "max_return_pct", "pead_direction"):
            col = CatalystRealizationRecord.__table__.columns[col_name]
            self.assertTrue(col.nullable, f"{col_name} should be nullable")


if __name__ == "__main__":
    unittest.main()
