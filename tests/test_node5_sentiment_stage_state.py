# -*- coding: utf-8 -*-
"""
Phase 3 单元测试：Node 5 情绪阶段状态机。

覆盖：
1. load_previous_stage_state 归档回读
2. load_sentiment_score_history 历史序列
3. _apply_stage_state_machine 4 种场景（持续/切换/冷启动）
4. _compute_momentum_direction 3 种方向
5. _compute_stage_contrarian_alert 5 种规则
6. _compute_ar1_persistence 3 种持续性
7. SentimentStageState / SentimentContext 向后兼容
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestLoadPreviousStageState(unittest.TestCase):
    """测试 load_previous_stage_state 归档回读。"""

    def test_returns_none_when_no_archive(self):
        """无归档时返回 None。"""
        from src.got.persistence import load_previous_stage_state
        with patch("src.got.persistence._list_archive_dirs", return_value=[]):
            result = load_previous_stage_state()
        self.assertIsNone(result)

    def test_returns_stage_fields(self):
        """返回阶段状态机字段。"""
        from src.got.persistence import load_previous_stage_state
        mock_data = {
            "stage_state": {
                "current_stage": "optimism",
                "dwell_days": 3,
                "previous_stage": "neutral",
                "momentum_direction": "warming",
                "stage_transitions": ["neutral", "optimism", "optimism"],
                "contrarian_alert": False,
            }
        }
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_sentiment_json", return_value=mock_data):
            mock_dirs.return_value = ["/fake/20260827"]
            result = load_previous_stage_state()
        self.assertIsNotNone(result)
        self.assertEqual(result["current_stage"], "optimism")
        self.assertEqual(result["dwell_days"], 3)
        self.assertEqual(result["momentum_direction"], "warming")

    def test_skips_missing_stage_state(self):
        """跳过无 stage_state 字段的归档。"""
        from src.got.persistence import load_previous_stage_state
        mock_no_stage = {"sentiment_score": 0.3}
        mock_with_stage = {"stage_state": {"current_stage": "euphoria", "dwell_days": 2}}
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_sentiment_json") as mock_load:
            mock_dirs.return_value = ["/fake/20260827", "/fake/20260826"]
            mock_load.side_effect = [mock_no_stage, mock_with_stage]
            result = load_previous_stage_state()
        self.assertIsNotNone(result)
        self.assertEqual(result["current_stage"], "euphoria")


class TestLoadSentimentScoreHistory(unittest.TestCase):
    """测试 load_sentiment_score_history 历史序列。"""

    def test_returns_empty_when_no_archive(self):
        """无归档时返回空列表。"""
        from src.got.persistence import load_sentiment_score_history
        with patch("src.got.persistence._list_archive_dirs", return_value=[]):
            result = load_sentiment_score_history(days=20)
        self.assertEqual(result, [])

    def test_returns_ascending_order(self):
        """返回的序列按日期升序排列。"""
        from pathlib import Path
        from src.got.persistence import load_sentiment_score_history
        mock_data = {"sentiment_score": 0.5}
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_sentiment_json", return_value=mock_data):
            mock_dirs.return_value = [Path("/fake/20260827"), Path("/fake/20260826")]
            result = load_sentiment_score_history(days=20)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][0], "20260826")
        self.assertEqual(result[1][0], "20260827")


class TestApplyStageStateMachine(unittest.TestCase):
    """测试 _apply_stage_state_machine 场景。"""

    def test_continuation_dwell_increments(self):
        """raw == previous → dwell+1。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        prev_state = {
            "current_stage": "optimism",
            "dwell_days": 3,
            "previous_stage": "neutral",
            "momentum_direction": "warming",
            "stage_transitions": ["neutral", "optimism", "optimism"],
            "contrarian_alert": False,
        }
        with patch("src.got.nodes.sentiment_analysis.load_previous_stage_state", return_value=prev_state):
            result = SentimentAnalysisNode._apply_stage_state_machine("optimism")
        self.assertEqual(result.current_stage, "optimism")
        self.assertEqual(result.dwell_days, 4)

    def test_switch_dwell_resets(self):
        """raw != previous → dwell=1。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        prev_state = {
            "current_stage": "optimism",
            "dwell_days": 3,
            "previous_stage": "neutral",
            "momentum_direction": "stable",
            "stage_transitions": ["neutral", "optimism"],
            "contrarian_alert": False,
        }
        with patch("src.got.nodes.sentiment_analysis.load_previous_stage_state", return_value=prev_state):
            result = SentimentAnalysisNode._apply_stage_state_machine("pessimism")
        self.assertEqual(result.current_stage, "pessimism")
        self.assertEqual(result.dwell_days, 1)
        self.assertEqual(result.previous_stage, "optimism")

    def test_cold_start(self):
        """冷启动：无归档 → current=raw, dwell=1。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        with patch("src.got.nodes.sentiment_analysis.load_previous_stage_state", return_value=None):
            result = SentimentAnalysisNode._apply_stage_state_machine("neutral")
        self.assertEqual(result.current_stage, "neutral")
        self.assertEqual(result.dwell_days, 1)
        self.assertIsNone(result.previous_stage)


class TestComputeMomentumDirection(unittest.TestCase):
    """测试 _compute_momentum_direction。"""

    def test_warming(self):
        """连续上升 → warming。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        result = SentimentAnalysisNode._compute_momentum_direction(["neutral", "optimism", "euphoria"])
        self.assertEqual(result, "warming")

    def test_cooling(self):
        """连续下降 → cooling。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        result = SentimentAnalysisNode._compute_momentum_direction(["optimism", "neutral", "pessimism"])
        self.assertEqual(result, "cooling")

    def test_stable(self):
        """无趋势 → stable。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        result = SentimentAnalysisNode._compute_momentum_direction(["neutral", "optimism", "neutral"])
        self.assertEqual(result, "stable")


class TestComputeStageContrarianAlert(unittest.TestCase):
    """测试 _compute_stage_contrarian_alert 5 种规则。"""

    def test_euphoria_long_dwell(self):
        """euphoria 持续 > 5 天 → contrarian_sell。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        result = SentimentAnalysisNode._compute_stage_contrarian_alert(
            current_stage="euphoria",
            dwell_days=6,
            momentum_direction="stable",
            stage_transitions=["euphoria"] * 6,
        )
        self.assertTrue(result)

    def test_capitulation_long_dwell(self):
        """capitulation 持续 > 3 天 → contrarian_buy。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        result = SentimentAnalysisNode._compute_stage_contrarian_alert(
            current_stage="capitulation",
            dwell_days=4,
            momentum_direction="stable",
            stage_transitions=["capitulation"] * 4,
        )
        self.assertTrue(result)

    def test_warming_sequence(self):
        """连续 3 天升温 → contrarian_sell。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        result = SentimentAnalysisNode._compute_stage_contrarian_alert(
            current_stage="euphoria",
            dwell_days=1,
            momentum_direction="warming",
            stage_transitions=["neutral", "optimism", "euphoria"],
        )
        self.assertTrue(result)

    def test_cooling_sequence(self):
        """连续 3 天降温 → contrarian_buy。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        result = SentimentAnalysisNode._compute_stage_contrarian_alert(
            current_stage="capitulation",
            dwell_days=1,
            momentum_direction="cooling",
            stage_transitions=["optimism", "pessimism", "capitulation"],
        )
        self.assertTrue(result)

    def test_high_frequency_switch_no_alert(self):
        """高频切换（≥5 次）→ 不触发反向。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        result = SentimentAnalysisNode._compute_stage_contrarian_alert(
            current_stage="euphoria",
            dwell_days=6,
            momentum_direction="warming",
            stage_transitions=["neutral", "optimism", "neutral", "optimism", "neutral", "euphoria"],
        )
        self.assertFalse(result)


class TestComputeAR1Persistence(unittest.TestCase):
    """测试 _compute_ar1_persistence。"""

    def test_high_persistence(self):
        """AR(1) > 0.7 → high。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        # 构造高自相关序列（x_t = 0.08 + 0.85·x_{t-1} 递推，20 个观测值，
        # 满足修复后 ≥20 样本门槛）
        history = [
            ("20260724", 0.0), ("20260725", 0.08), ("20260728", 0.148),
            ("20260729", 0.2058), ("20260730", 0.2549), ("20260731", 0.2967),
            ("20260801", 0.3322), ("20260804", 0.3624), ("20260805", 0.388),
            ("20260806", 0.4098), ("20260807", 0.4283), ("20260808", 0.4441),
            ("20260811", 0.4575), ("20260812", 0.4689), ("20260813", 0.4786),
            ("20260814", 0.4868), ("20260815", 0.4938), ("20260818", 0.4997),
            ("20260819", 0.5047), ("20260820", 0.509),
        ]
        with patch("src.got.nodes.sentiment_analysis.load_sentiment_score_history", return_value=history):
            result = SentimentAnalysisNode._compute_ar1_persistence()
        self.assertEqual(result, "high")

    def test_low_persistence(self):
        """AR(1) < 0.3 → low。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        # 构造弱自相关序列（正负交替，20 个观测值，满足修复后 ≥20 样本门槛）
        history = [
            ("20260801", 0.5), ("20260804", -0.3), ("20260805", 0.4),
            ("20260806", -0.2), ("20260807", 0.3), ("20260808", -0.25),
            ("20260811", 0.45), ("20260812", -0.35), ("20260813", 0.5),
            ("20260814", -0.3), ("20260815", 0.4), ("20260818", -0.2),
            ("20260819", 0.3), ("20260820", -0.25), ("20260821", 0.45),
            ("20260822", -0.35), ("20260825", 0.5), ("20260826", -0.3),
            ("20260827", 0.4), ("20260828", -0.2),
        ]
        with patch("src.got.nodes.sentiment_analysis.load_sentiment_score_history", return_value=history):
            result = SentimentAnalysisNode._compute_ar1_persistence()
        self.assertEqual(result, "low")

    def test_insufficient_data(self):
        """数据不足 → None。"""
        from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
        with patch("src.got.nodes.sentiment_analysis.load_sentiment_score_history", return_value=[]):
            result = SentimentAnalysisNode._compute_ar1_persistence()
        self.assertIsNone(result)


class TestSentimentStageStateBackwardCompat(unittest.TestCase):
    """测试 SentimentStageState / SentimentContext 向后兼容。"""

    def test_default_values(self):
        """默认值：dwell_days=1, momentum_direction=stable, contrarian_alert=False。"""
        from src.got.schemas import SentimentStageState
        stage = SentimentStageState(current_stage="neutral")
        self.assertEqual(stage.dwell_days, 1)
        self.assertEqual(stage.momentum_direction, "stable")
        self.assertFalse(stage.contrarian_alert)
        self.assertEqual(stage.stage_transitions, [])

    def test_old_json_compat(self):
        """旧 JSON 无 stage_state/persistence_regime → 默认值 None。"""
        import json
        from src.got.schemas import SentimentContext
        old_json = json.loads(
            '{"overall_sentiment": "neutral", "sentiment_score": 0.1, '
            '"sentiment_stage": "neutral", "trading_signal": "neutral", '
            '"signal_confidence": 0.3}'
        )
        ctx = SentimentContext.model_validate(old_json)
        self.assertIsNone(ctx.stage_state)
        self.assertIsNone(ctx.persistence_regime)


if __name__ == "__main__":
    unittest.main()
