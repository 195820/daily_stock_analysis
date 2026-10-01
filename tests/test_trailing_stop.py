# -*- coding: utf-8 -*-
"""工作流 C：持仓延续性管理（移动止盈 + 天数断点 + 观测字段）测试（2026-10-01）

覆盖 §7.1 工作流 C：
- 移动止盈矩阵（激活/未激活 × 回撤 8%/12% × 硬止损/exhausted/过热优先级不变）
- 无快照存量仓近似 HWM 路径
- K 线缺失 → 该 symbol 不入结果（上层两字段 None、动作退回现逻辑 = no-op 保证）
- mature 减仓 day 边界（缺省 30 / 可配 14）
- UnifiedScoringResult 新增 days_held / drawdown_from_hwm 序列化断言

_lifecycle_sell_signal / _batch_fetch_hwm 均在函数体内局部 import catalyst_config / 服务类，
故 patch 模块属性 / 服务类即可生效。
"""
import types
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

import src.got.catalyst_config as catalyst_config
from src.got.schemas import UnifiedScoringResult


def _node():
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode
    return PortfolioDecisionNode(use_mock=True)


def _sell(node, pnl=0.05, phase="acceleration", day=3, overheat=None,
          stop=-0.07, hwm_gain=None, drawdown=None):
    return node._lifecycle_sell_signal(
        pnl, phase, day, overheat, stop,
        hwm_gain_pct=hwm_gain, drawdown_from_hwm=drawdown,
    )


class TestTrailingStop(unittest.TestCase):
    """C-B1 移动止盈触发矩阵 + 强制卖出优先级。"""

    def test_disabled_by_default_noop(self):
        """开关关闭（缺省）：即使回撤巨大也不触发追踪，退回现逻辑。"""
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", False):
            action, reason = _sell(node, pnl=0.05, phase="acceleration",
                                   hwm_gain=0.30, drawdown=0.20)
        self.assertEqual(action, "hold")
        self.assertNotIn("移动止盈", reason)

    def test_not_armed_below_arm_pct(self):
        """曾盈利 < ARM(0.10)：未激活，维持现状（hold）。"""
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", True), \
             patch.object(catalyst_config, "TRAILING_ARM_PCT", 0.10), \
             patch.object(catalyst_config, "TRAILING_DRAWDOWN_REDUCE", 0.08), \
             patch.object(catalyst_config, "TRAILING_DRAWDOWN_SELL", 0.12):
            action, reason = _sell(node, pnl=0.05, phase="acceleration",
                                   hwm_gain=0.05, drawdown=0.20)
        self.assertEqual(action, "hold")

    def test_drawdown_reduce(self):
        """激活后回撤 ≥8% <12% → reduce 减半锁盈。"""
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", True), \
             patch.object(catalyst_config, "TRAILING_ARM_PCT", 0.10), \
             patch.object(catalyst_config, "TRAILING_DRAWDOWN_REDUCE", 0.08), \
             patch.object(catalyst_config, "TRAILING_DRAWDOWN_SELL", 0.12):
            action, reason = _sell(node, pnl=0.10, phase="acceleration",
                                   hwm_gain=0.25, drawdown=0.09)
        self.assertEqual(action, "reduce")
        self.assertIn("移动止盈", reason)

    def test_drawdown_sell(self):
        """激活后回撤 ≥12% → sell 清仓。"""
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", True), \
             patch.object(catalyst_config, "TRAILING_ARM_PCT", 0.10), \
             patch.object(catalyst_config, "TRAILING_DRAWDOWN_REDUCE", 0.08), \
             patch.object(catalyst_config, "TRAILING_DRAWDOWN_SELL", 0.12):
            action, reason = _sell(node, pnl=0.10, phase="acceleration",
                                   hwm_gain=0.30, drawdown=0.15)
        self.assertEqual(action, "sell")
        self.assertIn("移动止盈", reason)

    def test_hard_stop_priority_over_trailing(self):
        """硬止损优先：pnl < 止损线时先返回止损，不进入追踪分支。"""
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", True):
            action, reason = _sell(node, pnl=-0.08, stop=-0.07,
                                   hwm_gain=0.30, drawdown=0.15)
        self.assertEqual(action, "sell")
        self.assertIn("触发止损线", reason)
        self.assertNotIn("移动止盈", reason)

    def test_exhausted_priority_over_trailing(self):
        """exhausted 强制卖出优先于追踪。"""
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", True):
            action, reason = _sell(node, pnl=0.10, phase="mature_exhausted",
                                   hwm_gain=0.30, drawdown=0.09)
        self.assertEqual(action, "sell")
        self.assertIn("衰竭阶段", reason)

    def test_none_inputs_noop(self):
        """hwm_gain/drawdown 任一为 None（K 线缺失）→ 追踪 no-op，退回现逻辑。"""
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", True):
            action, _ = _sell(node, pnl=0.05, phase="acceleration",
                              hwm_gain=None, drawdown=0.30)
        self.assertEqual(action, "hold")


class TestMatureReduceDay(unittest.TestCase):
    """C-B2 mature 减仓天数断点可配（缺省 30，不改生产）。"""

    def test_default_30_boundary(self):
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", False), \
             patch.object(catalyst_config, "LIFECYCLE_MATURE_REDUCE_DAY", 30):
            # day=30：不 >30 → mature 且 pnl 不 >10% → hold
            a30, _ = _sell(node, pnl=0.05, phase="mature", day=30)
            # day=31：>30 → reduce
            a31, _ = _sell(node, pnl=0.05, phase="mature", day=31)
        self.assertEqual(a30, "hold")
        self.assertEqual(a31, "reduce")

    def test_configurable_14(self):
        node = _node()
        with patch.object(catalyst_config, "LIFECYCLE_TRAILING_STOP_ENABLED", False), \
             patch.object(catalyst_config, "LIFECYCLE_MATURE_REDUCE_DAY", 14):
            a15, reason = _sell(node, pnl=0.05, phase="mature", day=15)
        self.assertEqual(a15, "reduce")
        self.assertIn("成熟期已 15 天", reason)


class TestBatchFetchHwm(unittest.TestCase):
    """C-B1 _batch_fetch_hwm：HWM/回撤/近似 HWM/K 线缺失。"""

    def _fake_df(self, closes):
        # 降序（最新在前），date 为 ISO 字符串
        n = len(closes)
        dates = [(date.today() - timedelta(days=i)).isoformat() for i in range(n)]
        return pd.DataFrame({"date": dates, "close": [float(c) for c in closes]})

    def _patch_service(self, df_by_code):
        """patch TechnicalIndicatorService，_get_history 按 code 返回预设 df。"""
        inst = MagicMock()
        inst._get_history.side_effect = lambda code, days=60: df_by_code.get(code)
        cm_service = patch("src.services.technical_indicator.TechnicalIndicatorService",
                           return_value=inst)
        cm_cfg = patch("src.config.get_config", return_value=MagicMock())
        return cm_service, cm_cfg

    def test_hwm_entry_in_window(self):
        node = _node()
        entry = date.today() - timedelta(days=5)
        # 最新收盘 11.0（降序首位），期间最高 13.0
        df = self._fake_df([11.0, 12.0, 13.0, 10.0, 10.5, 11.2, 11.5])
        positions = [{"symbol": "300001", "unrealized_pnl_pct": 10.0}]
        thesis = {"300001": types.SimpleNamespace(entry_date=entry, entry_price=10.0)}
        cs, cc = self._patch_service({"300001": df})
        with cs, cc:
            res = node._batch_fetch_hwm(positions, thesis, {})
        self.assertIn("300001", res)
        info = res["300001"]
        self.assertFalse(info["approx"])
        self.assertAlmostEqual(info["current"], 11.0, places=6)
        self.assertAlmostEqual(info["hwm"], 13.0, places=6)
        self.assertAlmostEqual(info["drawdown"], (13.0 - 11.0) / 13.0, places=6)
        # 成本用 entry_price=10 → hwm_gain = 13/10 - 1 = 0.30
        self.assertAlmostEqual(info["hwm_gain"], 0.30, places=6)

    def test_no_entry_date_approx(self):
        node = _node()
        df = self._fake_df([11.0, 12.0, 13.0, 10.0])
        positions = [{"symbol": "300002", "unrealized_pnl_pct": 10.0}]
        cs, cc = self._patch_service({"300002": df})
        with cs, cc:
            res = node._batch_fetch_hwm(positions, {}, {})
        info = res["300002"]
        self.assertTrue(info["approx"])
        self.assertIsNone(info["entry_date"])
        # 近似：成本 current/(1+pnl)=11/1.1=10 → hwm_gain=13/10-1=0.30
        self.assertAlmostEqual(info["hwm_gain"], 0.30, places=6)

    def test_kline_missing_excluded(self):
        """K 线缺失（None）→ 该 symbol 不入结果（上层字段 None、动作退回现逻辑）。"""
        node = _node()
        positions = [{"symbol": "300003", "unrealized_pnl_pct": 5.0}]
        cs, cc = self._patch_service({"300003": None})
        with cs, cc:
            res = node._batch_fetch_hwm(positions, {}, {})
        self.assertNotIn("300003", res)
        self.assertEqual(res, {})

    def test_service_init_failure_returns_empty(self):
        node = _node()
        with patch("src.services.technical_indicator.TechnicalIndicatorService",
                   side_effect=RuntimeError("boom")), \
             patch("src.config.get_config", return_value=MagicMock()):
            res = node._batch_fetch_hwm([{"symbol": "300001"}], {}, {})
        self.assertEqual(res, {})


class TestUnifiedScoringSerialization(unittest.TestCase):
    """C-B3 新增观测字段序列化 + 默认 None。"""

    def _base_kwargs(self, **kw):
        d = dict(symbol="300001", name="测试", is_holding=True, composite_score=50.0,
                 dimension_scores={}, action_recommendation="hold", confidence=0.6,
                 reasoning="x")
        d.update(kw)
        return d

    def test_defaults_none(self):
        r = UnifiedScoringResult(**self._base_kwargs())
        self.assertIsNone(r.days_held)
        self.assertIsNone(r.drawdown_from_hwm)

    def test_populated_and_dump(self):
        r = UnifiedScoringResult(**self._base_kwargs(days_held=6, drawdown_from_hwm=0.09))
        dumped = r.model_dump(mode="python")
        self.assertEqual(dumped["days_held"], 6)
        self.assertAlmostEqual(dumped["drawdown_from_hwm"], 0.09, places=6)


if __name__ == "__main__":
    unittest.main()
