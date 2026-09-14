# -*- coding: utf-8 -*-
"""
Phase 2 单元测试：Node 6 Regime 状态机。

覆盖：
1. load_previous_regime_state 归档回读
2. load_regime_state_history 历史序列
3. _apply_regime_state_machine 4 种场景（持续/确认切换/待确认/冷启动）
4. _compute_position_from_regime dwell 感知缩放
5. MarketRegime Schema 向后兼容
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestLoadPreviousRegimeState(unittest.TestCase):
    """测试 load_previous_regime_state 归档回读。"""

    def test_returns_none_when_no_archive(self):
        """无归档时返回 None。"""
        from src.got.persistence import load_previous_regime_state
        with patch("src.got.persistence._list_archive_dirs", return_value=[]):
            result = load_previous_regime_state()
        self.assertIsNone(result)

    def test_returns_regime_fields(self):
        """返回 regime 状态机字段。"""
        from src.got.persistence import load_previous_regime_state
        mock_data = {
            "market_regime": {
                "regime": "bull",
                "dwell_days": 5,
                "previous_regime": "transition",
                "transition_count": 2,
                "transition_pending": None,
            }
        }
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_unified_json", return_value=mock_data):
            mock_dirs.return_value = ["/fake/20260827"]
            result = load_previous_regime_state()
        self.assertIsNotNone(result)
        self.assertEqual(result["regime"], "bull")
        self.assertEqual(result["dwell_days"], 5)
        self.assertEqual(result["transition_count"], 2)

    def test_skips_missing_regime_field(self):
        """跳过无 regime 字段的归档。"""
        from src.got.persistence import load_previous_regime_state
        mock_data_no_regime = {"market_regime": {}}
        mock_data_with_regime = {"market_regime": {"regime": "bear"}}
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_unified_json") as mock_load:
            mock_dirs.return_value = ["/fake/20260827", "/fake/20260826"]
            mock_load.side_effect = [mock_data_no_regime, mock_data_with_regime]
            result = load_previous_regime_state()
        self.assertIsNotNone(result)
        self.assertEqual(result["regime"], "bear")


class TestLoadRegimeStateHistory(unittest.TestCase):
    """测试 load_regime_state_history 历史序列。"""

    def test_returns_empty_when_no_archive(self):
        """无归档时返回空列表。"""
        from src.got.persistence import load_regime_state_history
        with patch("src.got.persistence._list_archive_dirs", return_value=[]):
            result = load_regime_state_history(days=20)
        self.assertEqual(result, [])

    def test_returns_ascending_order(self):
        """返回的序列按日期升序排列。"""
        from pathlib import Path
        from src.got.persistence import load_regime_state_history
        mock_data_1 = {"market_regime": {"regime": "bull"}}
        mock_data_2 = {"market_regime": {"regime": "transition"}}
        mock_data_3 = {"market_regime": {"regime": "bear"}}
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_unified_json") as mock_load:
            # 返回的是按日期降序（Path 对象）
            mock_dirs.return_value = [Path("/fake/20260827"), Path("/fake/20260826"), Path("/fake/20260825")]
            mock_load.side_effect = [mock_data_1, mock_data_2, mock_data_3]
            result = load_regime_state_history(days=20)
        self.assertEqual(len(result), 3)
        # 应该是升序（从旧到新）
        self.assertEqual(result[0][1], "bear")
        self.assertEqual(result[1][1], "transition")
        self.assertEqual(result[2][1], "bull")


class TestApplyRegimeStateMachine(unittest.TestCase):
    """测试 _apply_regime_state_machine 4 种场景。"""

    def _make_regime(self, regime="bull", **kwargs):
        from src.got.schemas import MarketRegime
        return MarketRegime(
            regime=regime,
            risk_budget=0.6,
            **kwargs,
        )

    def test_continuation_dwell_increments(self):
        """raw == previous → dwell+1，清除 pending。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("bull")
        prev_state = {
            "regime": "bull",
            "dwell_days": 3,
            "previous_regime": "transition",
            "transition_count": 1,
            "transition_pending": None,
        }
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=[
                 ("20260825", "transition"), ("20260826", "bull"), ("20260827", "bull")
             ]):
            result = AggregatorNode._apply_regime_state_machine(regime)
        self.assertEqual(result.regime, "bull")
        self.assertEqual(result.dwell_days, 4)
        self.assertIsNone(result.transition_pending)

    def test_confirmed_switch(self):
        """raw != previous 且 raw == transition_pending → 确认切换，dwell=1。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("bear")
        prev_state = {
            "regime": "bull",
            "dwell_days": 5,
            "previous_regime": "transition",
            "transition_count": 1,
            "transition_pending": "bear",  # pending 与 raw 相同
        }
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=[
                 ("20260825", "transition"), ("20260826", "bull"), ("20260827", "bear")
             ]):
            result = AggregatorNode._apply_regime_state_machine(regime)
        self.assertEqual(result.regime, "bear")
        self.assertEqual(result.dwell_days, 1)
        self.assertIsNone(result.transition_pending)
        # R2：情形 2 是唯一会让 previous_regime != regime 的路径，必须在此钉住。
        # 否则 R2「出现 previous_regime != regime 的归档日 ≥1 天」只能靠等真实市场切换
        # （自 08-27 接线以来 0 次触发），零切换无法区分「市场没切换」与「情形不可达」。
        # 见 docs/got_backlog.md §9 #3 / §9.1。
        self.assertEqual(result.previous_regime, "bull")

    def test_pending_first_appearance(self):
        """raw != previous 且 raw != transition_pending → 保持 previous，设新 pending。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("bear")
        prev_state = {
            "regime": "bull",
            "dwell_days": 3,
            "previous_regime": "transition",
            "transition_count": 1,
            "transition_pending": None,
        }
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=[
                 ("20260825", "transition"), ("20260826", "bull"), ("20260827", "bear")
             ]):
            result = AggregatorNode._apply_regime_state_machine(regime)
        # 保持 previous（bull），设新 pending（bear）
        self.assertEqual(result.regime, "bull")
        self.assertEqual(result.dwell_days, 4)  # dwell 继续+1
        self.assertEqual(result.transition_pending, "bear")

    def test_cold_start_no_archive(self):
        """冷启动：无归档 → current=raw, dwell=1。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("transition")
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=None), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=[]):
            result = AggregatorNode._apply_regime_state_machine(regime)
        self.assertEqual(result.regime, "transition")
        self.assertEqual(result.dwell_days, 1)
        self.assertIsNone(result.previous_regime)
        self.assertEqual(result.transition_count, 0)

    def test_transition_count_from_history(self):
        """transition_count 从 20 日历史中统计切换次数。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("bull")
        prev_state = {
            "regime": "bull",
            "dwell_days": 1,
            "previous_regime": "bear",
            "transition_count": 0,
            "transition_pending": None,
        }
        # 模拟 5 日历史：transition → bear → bull → transition → bull = 4 次切换
        history = [
            ("20260821", "transition"),
            ("20260822", "bear"),
            ("20260825", "bull"),
            ("20260826", "transition"),
            ("20260827", "bull"),
        ]
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=history):
            result = AggregatorNode._apply_regime_state_machine(regime)
        self.assertEqual(result.transition_count, 4)


class TestComputePositionDwellAware(unittest.TestCase):
    """测试 _compute_position_from_regime dwell 感知缩放。"""

    def _make_regime(self, regime, dwell_days, transition_count=0, risk_budget=0.6):
        from src.got.schemas import MarketRegime
        return MarketRegime(
            regime=regime,
            risk_budget=risk_budget,
            dwell_days=dwell_days,
            transition_count=transition_count,
        )

    def _make_sector_intels(self, count=2, classification="actionable"):
        from src.got.schemas import SectorIntel
        return [
            SectorIntel(
                sector_name=f"板块{i}",
                classification=classification,
                confidence=0.7,
                composite_score=60.0,
            )
            for i in range(count)
        ]

    def test_bull_fresh_lower_than_established(self):
        """bull fresh (dwell<5) 比 established 仓位低。"""
        from src.got.nodes.aggregator import AggregatorNode
        # 使用较高的 risk_budget 避免被截断
        regime_fresh = self._make_regime("bull", dwell_days=2, risk_budget=0.8)
        regime_established = self._make_regime("bull", dwell_days=10, risk_budget=0.8)
        sectors = self._make_sector_intels(3)

        pos_fresh = AggregatorNode._compute_position_from_regime(regime_fresh, sectors)
        pos_established = AggregatorNode._compute_position_from_regime(regime_established, sectors)

        # fresh 应该比 established 低（0.9 vs 1.0 系数）
        self.assertLess(pos_fresh, pos_established)

    def test_bear_fresh_higher_than_established(self):
        """bear fresh (dwell<5) 比 established 仓位高（初期不那么悲观）。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime_fresh = self._make_regime("bear", dwell_days=2, risk_budget=0.8)
        regime_established = self._make_regime("bear", dwell_days=10, risk_budget=0.8)
        sectors = self._make_sector_intels(3)

        pos_fresh = AggregatorNode._compute_position_from_regime(regime_fresh, sectors)
        pos_established = AggregatorNode._compute_position_from_regime(regime_established, sectors)

        self.assertGreater(pos_fresh, pos_established)

    def test_transition_count_penalty(self):
        """transition_count ≥ 5 → × 0.8 震荡惩罚。"""
        from src.got.nodes.aggregator import AggregatorNode
        # 使用较高的 risk_budget 和更多 sectors 避免被截断
        regime_low = self._make_regime("bull", dwell_days=10, transition_count=3, risk_budget=0.8)
        regime_high = self._make_regime("bull", dwell_days=10, transition_count=6, risk_budget=0.8)
        sectors = self._make_sector_intels(5)  # 更多 sectors

        pos_low = AggregatorNode._compute_position_from_regime(regime_low, sectors)
        pos_high = AggregatorNode._compute_position_from_regime(regime_high, sectors)

        # high 应该比 low 低（因为有 0.8 惩罚）
        self.assertLess(pos_high, pos_low)


class TestMarketRegimeBackwardCompat(unittest.TestCase):
    """测试 MarketRegime 新字段向后兼容。"""

    def test_default_values(self):
        """默认值：dwell_days=1, transition_count=0, pending/previous=None。"""
        from src.got.schemas import MarketRegime
        regime = MarketRegime(regime="bull", risk_budget=0.6)
        self.assertEqual(regime.dwell_days, 1)
        self.assertEqual(regime.transition_count, 0)
        self.assertIsNone(regime.previous_regime)
        self.assertIsNone(regime.transition_pending)

    def test_explicit_values(self):
        """显式传入生效。"""
        from src.got.schemas import MarketRegime
        regime = MarketRegime(
            regime="bull",
            risk_budget=0.6,
            dwell_days=5,
            transition_count=3,
            previous_regime="transition",
            transition_pending="bear",
        )
        self.assertEqual(regime.dwell_days, 5)
        self.assertEqual(regime.transition_count, 3)
        self.assertEqual(regime.previous_regime, "transition")
        self.assertEqual(regime.transition_pending, "bear")

    def test_old_json_compat(self):
        """旧 JSON 无新字段 → 默认值。"""
        import json
        from src.got.schemas import MarketRegime
        old_json = json.loads('{"regime": "bull", "risk_budget": 0.6}')
        regime = MarketRegime.model_validate(old_json)
        self.assertEqual(regime.dwell_days, 1)
        self.assertEqual(regime.transition_count, 0)


if __name__ == "__main__":
    unittest.main()
