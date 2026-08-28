# -*- coding: utf-8 -*-
"""
Phase 6 单元测试：跨节点时间线共振 + 数据新鲜度 + 生产监控。

覆盖：
1. _detect_cross_node_temporal_signals 4 种模式
2. 几何平均置信度（单组件不触发）
3. _validate_data_freshness
4. save_monitoring / load_monitoring
5. UnifiedContext.cross_node_signals Schema 向后兼容
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _make_regime(**kwargs):
    from src.got.schemas import MarketRegime
    defaults = {
        "regime": "transition",
        "risk_budget": 0.6,
        "dwell_days": 1,
        "transition_count": 0,
        "transition_pending": None,
        "fear_greed": 0.5,
        "sentiment_stage": "neutral",
    }
    defaults.update(kwargs)
    return MarketRegime(**defaults)


def _make_capital(**kwargs):
    from src.got.schemas import CapitalContext, CapitalEvidence, DataQuality
    evidence = kwargs.pop("evidence", None) or CapitalEvidence()
    defaults = {
        "status": "normal",
        "institutional_attitude": "neutral",
        "institutional_score": 0.0,
        "confidence_multiplier": 1.0,
        "evidence": evidence,
        "reasoning": None,
        "data_quality": DataQuality(),
    }
    defaults.update(kwargs)
    return CapitalContext(**defaults)


def _make_sentiment(**kwargs):
    from src.got.schemas import SentimentContext, DataQuality
    defaults = {
        "overall_sentiment": "neutral",
        "sentiment_score": 0.0,
        "sentiment_stage": "neutral",
        "trading_signal": "neutral",
        "signal_confidence": 0.5,
        "data_quality": DataQuality(),
        "stage_state": None,
        "persistence_regime": None,
    }
    defaults.update(kwargs)
    return SentimentContext(**defaults)


def _make_news():
    from src.got.schemas import NewsContext, DataQuality
    return NewsContext(
        overall_impact="neutral",
        major_events=[],
        concept_suggestions=[],
        impact_score=0.0,
        reasoning=None,
        data_quality=DataQuality(),
    )


class TestCrossNodeSignals(unittest.TestCase):
    """测试 _detect_cross_node_temporal_signals 4 种共振模式。"""

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)
        self._detect = AggregatorNode._detect_cross_node_temporal_signals

    def _make_capital_with_flow(self, flow_1d=None, flow_5d=None):
        from src.got.schemas import CapitalContext, CapitalEvidence, DataQuality
        sector_flow = {}
        if flow_1d is not None:
            sector_flow["sector_flow_all"] = flow_1d
        if flow_5d is not None:
            sector_flow["sector_flow_all_5d"] = flow_5d
        evidence = CapitalEvidence(sector_flow=sector_flow)
        return CapitalContext(
            status="normal",
            institutional_attitude="neutral",
            institutional_score=0.0,
            confidence_multiplier=1.0,
            evidence=evidence,
            reasoning=None,
            data_quality=DataQuality(),
        )

    def test_bull_confirmation(self):
        """regime 刚切牛 + 资金加速度 > 0.15 → bull_confirmation。"""
        from src.got.schemas import SentimentStageState
        regime = _make_regime(regime="bull", dwell_days=1)
        capital = self._make_capital_with_flow(
            flow_1d={"A": 10.0, "B": 5.0},
            flow_5d={"A": 15.0, "B": 8.0},  # 5d avg > 1d → acceleration > 0
        )
        sentiment = _make_sentiment()
        news = _make_news()

        signals = self._detect(regime, capital, sentiment, news)
        bull_sigs = [s for s in signals if s.signal_type == "bull_confirmation"]
        self.assertEqual(len(bull_sigs), 1)
        self.assertEqual(bull_sigs[0].suggested_action, "strong_buy")
        self.assertGreater(bull_sigs[0].confidence, 0)

    def test_sentiment_top(self):
        """反向卖出 + 资金撤退 → sentiment_top。"""
        from src.got.schemas import SentimentStageState
        stage_state = SentimentStageState(
            current_stage="euphoria",
            dwell_days=6,
            previous_stage="optimism",
            momentum_direction="cooling",
            contrarian_alert=True,
        )
        regime = _make_regime(regime="bear", dwell_days=3)
        capital = self._make_capital_with_flow(
            flow_1d={"A": 10.0},
            flow_5d={"A": 5.0},  # 5d avg < 1d → acceleration < 0
        )
        sentiment = _make_sentiment(
            sentiment_stage="euphoria",
            stage_state=stage_state,
        )
        news = _make_news()

        signals = self._detect(regime, capital, sentiment, news)
        top_sigs = [s for s in signals if s.signal_type == "sentiment_top"]
        self.assertEqual(len(top_sigs), 1)
        self.assertEqual(top_sigs[0].suggested_action, "sell")

    def test_range_reversal(self):
        """高震荡 + 低持续性 → range_reversal。"""
        regime = _make_regime(regime="transition", transition_count=7)
        capital = self._make_capital_with_flow()
        sentiment = _make_sentiment(persistence_regime="low")
        news = _make_news()

        signals = self._detect(regime, capital, sentiment, news)
        reversal_sigs = [s for s in signals if s.signal_type == "range_reversal"]
        self.assertEqual(len(reversal_sigs), 1)
        self.assertEqual(reversal_sigs[0].suggested_action, "short_term_reversal")

    def test_no_signal_when_conditions_not_met(self):
        """条件不满足 → 无信号。"""
        regime = _make_regime(regime="bull", dwell_days=10)  # 非刚切
        capital = self._make_capital_with_flow()
        sentiment = _make_sentiment()
        news = _make_news()

        signals = self._detect(regime, capital, sentiment, news)
        # 无 bull_confirmation（dwell=10 > 2）
        bull_sigs = [s for s in signals if s.signal_type == "bull_confirmation"]
        self.assertEqual(len(bull_sigs), 0)

    def test_geometric_mean_single_component_low_confidence(self):
        """单组件极端值 → 几何平均拉低，不触发信号。"""
        # bull_confirmation 需要 dwell<=2 且 acceleration>0.15
        # 如果只有 regime 确认但加速度很小（0.01），几何平均后置信度低
        regime = _make_regime(regime="bull", dwell_days=1)
        capital = self._make_capital_with_flow(
            flow_1d={"A": 10.0},
            flow_5d={"A": 10.1},  # acceleration ≈ 0.01，极小
        )
        sentiment = _make_sentiment()
        news = _make_news()

        signals = self._detect(regime, capital, sentiment, news)
        # 加速度 0.01 < 0.15 阈值，不触发 bull_confirmation
        bull_sigs = [s for s in signals if s.signal_type == "bull_confirmation"]
        self.assertEqual(len(bull_sigs), 0)


class TestDataFreshness(unittest.TestCase):
    """测试 _validate_data_freshness。"""

    def test_fresh_data(self):
        from src.got.nodes.capital_analysis import _validate_data_freshness
        # 最后一行是最新日期
        df = pd.DataFrame({"日期": ["2026-08-26", "2026-08-27"], "value": [1, 2]})
        result = _validate_data_freshness(df, "2026-08-27")
        self.assertTrue(result["is_fresh"])
        self.assertFalse(result["stale_data"])

    def test_stale_data(self):
        from src.got.nodes.capital_analysis import _validate_data_freshness
        df = pd.DataFrame({"日期": ["2026-08-25"], "value": [1]})
        result = _validate_data_freshness(df, "2026-08-27")
        self.assertFalse(result["is_fresh"])
        self.assertTrue(result["stale_data"])

    def test_no_date_column(self):
        from src.got.nodes.capital_analysis import _validate_data_freshness
        df = pd.DataFrame({"value": [1, 2, 3]})
        result = _validate_data_freshness(df, "2026-08-27")
        self.assertTrue(result["is_fresh"])  # 无法验证时假设有效

    def test_empty_dataframe(self):
        from src.got.nodes.capital_analysis import _validate_data_freshness
        result = _validate_data_freshness(pd.DataFrame(), "2026-08-27")
        self.assertTrue(result["is_fresh"])

    def test_time_column(self):
        from src.got.nodes.capital_analysis import _validate_data_freshness
        df = pd.DataFrame({"时间": ["2026-08-27 14:30:00"], "value": [1]})
        result = _validate_data_freshness(df, "2026-08-27")
        self.assertTrue(result["is_fresh"])


class TestMonitoring(unittest.TestCase):
    """测试 save_monitoring / load_monitoring。"""

    def setUp(self):
        # 临时目录
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch(
            "src.got.persistence._MONITORING_DIR",
            Path(self._tmpdir),
        )
        self._patcher.start()
        self._patcher2 = patch(
            "src.got.persistence._MONITORING_FILE",
            Path(self._tmpdir) / "monitoring.json",
        )
        self._patcher2.start()

    def tearDown(self):
        self._patcher.stop()
        self._patcher2.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_load(self):
        from src.got.persistence import load_monitoring
        result = load_monitoring()
        self.assertIsNone(result["last_successful_run"])
        self.assertEqual(result["anomalies"], [])

    def test_save_and_load(self):
        from src.got.persistence import save_monitoring, load_monitoring
        result = save_monitoring(
            regime_dwell=5,
            regime_switch_count_20d=2,
            flow_5d_non_null_rate=0.95,
            flow_5d_total_boards=50,
            mainline_consistency_avg=3.0,
            contrarian_alert_today=True,
            persistence_regime="high",
            cross_node_signal_counts={"bull_confirmation": 1},
        )
        self.assertEqual(result["last_successful_run"] is not None, True)
        self.assertEqual(result["node1"]["regime_dwell_days_history"][-1], 5)

        loaded = load_monitoring()
        self.assertEqual(loaded["node1"]["regime_dwell_days_history"][-1], 5)

    def test_anomaly_critical(self):
        from src.got.persistence import save_monitoring
        result = save_monitoring(
            regime_dwell=1,
            regime_switch_count_20d=2,
            flow_5d_non_null_rate=0.5,  # < 0.8 → critical
            flow_5d_total_boards=50,  # ≥10 冷启动保护通过
            mainline_consistency_avg=0.0,
            contrarian_alert_today=False,
            persistence_regime=None,
            cross_node_signal_counts={},
        )
        critical = [a for a in result["anomalies"] if a["level"] == "critical"]
        self.assertTrue(len(critical) >= 1)
        self.assertEqual(result["node3"]["status"], "critical")

    def test_anomaly_warning(self):
        from src.got.persistence import save_monitoring
        result = save_monitoring(
            regime_dwell=1,
            regime_switch_count_20d=10,  # > 8 → warning
            flow_5d_non_null_rate=0.95,
            flow_5d_total_boards=50,
            mainline_consistency_avg=0.0,
            contrarian_alert_today=False,
            persistence_regime=None,
            cross_node_signal_counts={},
        )
        warnings = [a for a in result["anomalies"] if a["level"] == "warning"]
        self.assertTrue(len(warnings) >= 1)


class TestUnifiedContextBackwardCompat(unittest.TestCase):
    """测试 UnifiedContext.cross_node_signals 向后兼容。"""

    def test_default_empty_list(self):
        from src.got.schemas import UnifiedContext, DataQuality
        ctx = UnifiedContext(
            status="normal",
            unified_signal="neutral",
            overall_confidence=0.5,
            final_position_limit=0.5,
            data_quality=DataQuality(),
        )
        self.assertEqual(ctx.cross_node_signals, [])

    def test_cross_node_signal_schema(self):
        from src.got.schemas import CrossNodeTemporalSignal
        sig = CrossNodeTemporalSignal(
            signal_type="bull_confirmation",
            confidence=0.75,
            component_nodes=["node6_regime", "node3_capital"],
            component_evidence={"regime": "bull", "dwell_days": 1},
            reasoning="测试信号",
            suggested_action="strong_buy",
        )
        self.assertEqual(sig.signal_type, "bull_confirmation")
        self.assertEqual(sig.confidence, 0.75)


if __name__ == "__main__":
    unittest.main()
