# -*- coding: utf-8 -*-
"""
Phase 1 单元测试：Node 3 资金面多窗口 + 吸筹闸门三档 + Schema 向后兼容。

覆盖：
1. load_capital_flow_series 归档回读
2. _score_sector_flow 多窗口扩展字段
3. _score_dde 累计/连续字段
4. _check_accumulation_gate 四档判定
5. AmbushCandidate accum_strength 向后兼容
"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestLoadCapitalFlowSeries(unittest.TestCase):
    """测试 load_capital_flow_series 归档回读。"""

    def test_returns_sector_and_dde(self):
        """返回 (sector_series, dde_series) 元组。"""
        from src.got.persistence import load_capital_flow_series
        sector_series, dde_series = load_capital_flow_series(days=5)
        self.assertIsInstance(sector_series, dict)
        self.assertIsInstance(dde_series, list)

    def test_sector_series_structure(self):
        """板块序列值为 [(日期, 净流入), ...] 格式。"""
        from src.got.persistence import load_capital_flow_series
        sector_series, _ = load_capital_flow_series(days=5)
        for sector_name, series in sector_series.items():
            self.assertIsInstance(sector_name, str)
            self.assertIsInstance(series, list)
            for date_str, flow in series:
                self.assertIsInstance(date_str, str)
                self.assertEqual(len(date_str), 8)  # YYYYMMDD
                self.assertIsInstance(flow, float)

    def test_days_parameter_limits_results(self):
        """days 参数限制返回的天数。"""
        from src.got.persistence import load_capital_flow_series
        series_3, _ = load_capital_flow_series(days=3)
        series_10, _ = load_capital_flow_series(days=10)
        # 10 天应该覆盖至少和 3 天一样多的数据
        max_len_3 = max((len(s) for s in series_3.values()), default=0)
        max_len_10 = max((len(s) for s in series_10.values()), default=0)
        self.assertGreaterEqual(max_len_10, max_len_3)

    def test_dates_ascending(self):
        """返回的序列按日期升序排列。"""
        from src.got.persistence import load_capital_flow_series
        sector_series, _ = load_capital_flow_series(days=10)
        for series in sector_series.values():
            if len(series) >= 2:
                dates = [d for d, _ in series]
                self.assertEqual(dates, sorted(dates))


class TestScoreSectorFlowMultiday(unittest.TestCase):
    """测试 _score_sector_flow 多窗口扩展字段。"""

    def _make_data(self):
        import pandas as pd
        concept_df = pd.DataFrame({
            "名称": ["AI视频", "半导体", "银行"],
            "今日净额": [42.0, 25.5, -10.0],
        })
        industry_df = pd.DataFrame({
            "名称": ["种植业", "金融"],
            "今日净额": [1.8, -5.0],
        })
        return {"concept": concept_df, "industry": industry_df, "timestamp": "mock"}

    def test_returns_multiday_fields(self):
        """返回 dict 包含 sector_flow_all_3d / 5d / mainline_consistency_days / acceleration。"""
        from src.got.nodes.capital_analysis import CapitalAnalysisNode
        node = CapitalAnalysisNode()
        result = node._score_sector_flow(self._make_data(), sector_context=None)
        self.assertIsNotNone(result)
        score, ev = result
        self.assertIn("sector_flow_all_3d", ev)
        self.assertIn("sector_flow_all_5d", ev)
        self.assertIn("mainline_consistency_days", ev)
        self.assertIn("acceleration", ev)

    def test_sector_flow_all_3d_is_dict(self):
        """sector_flow_all_3d 为板块名→日均(亿)映射。"""
        from src.got.nodes.capital_analysis import CapitalAnalysisNode
        node = CapitalAnalysisNode()
        _, ev = node._score_sector_flow(self._make_data(), sector_context=None)
        self.assertIsInstance(ev["sector_flow_all_3d"], dict)
        for name, val in ev["sector_flow_all_3d"].items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(val, float)

    def test_mainline_consistency_zero_without_context(self):
        """无 sector_context 时 mainline_consistency_days = 空 dict（#2 修复：按板块 dict 口径）。"""
        from src.got.nodes.capital_analysis import CapitalAnalysisNode
        node = CapitalAnalysisNode()
        _, ev = node._score_sector_flow(self._make_data(), sector_context=None)
        self.assertEqual(ev["mainline_consistency_days"], {})


class TestScoreDdeMultiday(unittest.TestCase):
    """测试 _score_dde 累计/连续字段。"""

    def _make_dde_data(self, total_net=-1.04e8):
        return {
            "total_net_amount": total_net,
            "stock_count": 5000,
            "snapshots": [
                {"code": "000001", "big_net_amount": 1e7, "dde_big_order": 0.5},
                {"code": "000002", "big_net_amount": -2e7, "dde_big_order": -1.0},
            ],
            "timestamp": "mock",
        }

    def test_returns_cumulative_fields(self):
        """返回 dict 包含 cumulative_3d / cumulative_5d / consecutive_positive_days。"""
        from src.got.nodes.capital_analysis import CapitalAnalysisNode
        node = CapitalAnalysisNode()
        _, ev = node._score_dde(self._make_dde_data())
        self.assertIn("cumulative_3d", ev)
        self.assertIn("cumulative_5d", ev)
        self.assertIn("consecutive_positive_days", ev)

    def test_consecutive_positive_days_is_int(self):
        """consecutive_positive_days 为非负整数。"""
        from src.got.nodes.capital_analysis import CapitalAnalysisNode
        node = CapitalAnalysisNode()
        _, ev = node._score_dde(self._make_dde_data())
        self.assertIsInstance(ev["consecutive_positive_days"], int)
        self.assertGreaterEqual(ev["consecutive_positive_days"], 0)


class TestAccumulationGateTiers(unittest.TestCase):
    """测试 _check_accumulation_gate 四档判定。"""

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)

    def _make_capital(self, sector_flow_all, sector_flow_all_3d=None):
        """构造带指定板块资金的 CapitalContext mock。"""
        from src.got.schemas import CapitalContext, CapitalEvidence
        ev = CapitalEvidence(
            sector_flow={
                "score": 0.1,
                "sector_flow_all": sector_flow_all,
                "sector_flow_all_3d": sector_flow_all_3d or {},
                "top_concepts": [],
                "top_industries": [],
                "mainline_bonus": False,
                "timestamp": "mock",
            }
        )
        return CapitalContext(
            status="normal",
            institutional_attitude="neutral",
            institutional_score=0.0,
            confidence_multiplier=1.0,
            evidence=ev,
        )

    def test_weak_today_positive_only(self):
        """当日正流入但无 3d 数据 → weak。"""
        from src.got.nodes.aggregator import AggregatorNode
        capital = self._make_capital({"半导体": 10.0})
        with patch("src.got.persistence.load_capital_flow_series", return_value=({}, [])):
            passed, detail, strength, _evidence = self.node._check_accumulation_gate("半导体", capital)
        self.assertTrue(passed)
        self.assertEqual(strength, "weak")

    def test_medium_3d_positive(self):
        """3d 日均正流入 → medium。"""
        from src.got.nodes.aggregator import AggregatorNode
        capital = self._make_capital(
            {"半导体": 10.0},
            sector_flow_all_3d={"半导体": 5.0},
        )
        # Mock archive to return no consecutive data (so falls to medium)
        mock_series = {"半导体": [("20260825", -3.0), ("20260826", 8.0), ("20260827", 10.0)]}
        with patch("src.got.persistence.load_capital_flow_series", return_value=(mock_series, [])):
            passed, detail, strength, _evidence = self.node._check_accumulation_gate("半导体", capital)
        self.assertTrue(passed)
        self.assertEqual(strength, "medium")

    def test_strong_consecutive_3_days(self):
        """连续 3 日正流入 → strong。"""
        from src.got.nodes.aggregator import AggregatorNode
        capital = self._make_capital(
            {"半导体": 10.0},
            sector_flow_all_3d={"半导体": 5.0},
        )
        mock_series = {"半导体": [("20260825", 3.0), ("20260826", 8.0), ("20260827", 10.0)]}
        with patch("src.got.persistence.load_capital_flow_series", return_value=(mock_series, [])):
            passed, detail, strength, _evidence = self.node._check_accumulation_gate("半导体", capital)
        self.assertTrue(passed)
        self.assertEqual(strength, "strong")
        self.assertIn("3", detail)  # 应含连续天数

    def test_negative_today_fails(self):
        """当日负流入 → 闸门失败。"""
        from src.got.nodes.aggregator import AggregatorNode
        capital = self._make_capital({"半导体": -10.0})
        passed, detail, strength, _evidence = self.node._check_accumulation_gate("半导体", capital)
        self.assertFalse(passed)

    def test_missing_sector_flow_fails(self):
        """资金面数据缺失 → 闸门失败 + missing 三态（区分无信号与坏信号，调用方应转观察而非否决）。"""
        from src.got.nodes.aggregator import AggregatorNode
        from src.got.schemas import CapitalContext, CapitalEvidence
        capital = CapitalContext(
            status="failed",
            institutional_attitude="neutral",
            institutional_score=0.0,
            confidence_multiplier=0.0,
            evidence=CapitalEvidence(),
        )
        passed, detail, strength, _evidence = self.node._check_accumulation_gate("半导体", capital)
        self.assertFalse(passed)
        self.assertEqual(strength, "missing")

    def test_unknown_sector_returns_missing(self):
        """板块不在资金流覆盖且无别名映射 → missing 而非 legacy。"""
        capital = self._make_capital({"半导体": -10.0})
        passed, detail, strength, _evidence = self.node._check_accumulation_gate("量子面料", capital)
        self.assertFalse(passed)
        self.assertEqual(strength, "missing")


class TestAmbushCandidateBackwardCompat(unittest.TestCase):
    """测试 AmbushCandidate accum_strength 向后兼容。"""

    def test_default_is_legacy(self):
        """默认值为 legacy。"""
        from src.got.schemas import AmbushCandidate
        c = AmbushCandidate(sector_name="测试", catalyst_score=0.5, category_tag="drift")
        self.assertEqual(c.accum_strength, "legacy")

    def test_explicit_value(self):
        """显式传入生效。"""
        from src.got.schemas import AmbushCandidate
        c = AmbushCandidate(
            sector_name="测试", catalyst_score=0.5, category_tag="drift",
            accum_strength="strong",
        )
        self.assertEqual(c.accum_strength, "strong")

    def test_old_json_compat(self):
        """旧 JSON 无 accum_strength → 默认 legacy。"""
        import json
        from src.got.schemas import AmbushCandidate
        old_json = json.loads(
            '{"sector_name": "测试", "catalyst_score": 0.5, "category_tag": "drift"}'
        )
        c = AmbushCandidate.model_validate(old_json)
        self.assertEqual(c.accum_strength, "legacy")


if __name__ == "__main__":
    unittest.main()
