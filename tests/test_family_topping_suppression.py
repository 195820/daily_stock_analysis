# -*- coding: utf-8 -*-
"""家族见顶持续压制门（FTS, GOT_FAMILY_TOPPING_*）WI-A 测试：schema + Node6 latch 合成。

覆盖：
- FamilySuppression / UnifiedContext schema 缺省值（off 态字段为空）
- _synthesize_family_suppression 跨日 latch 三态转移：新建 / 持续 / 健康迟滞退出 / 上限释放
- aggravated 判据（warning_signals 含"破位信号"）
- env 配置（COOLDOWN / EXIT_HEALTHY / MODE）

与 MEG 正交：MEG 看单日板块 decay 断裂（snap），FTS 看家族级 endurance_band=topping_risk + 跨日 latch。
Phase2 消费（层A 缩仓 / 层B 硬降）见 WI-B 测试。
"""
import os
import unittest
from unittest.mock import patch

from src.got.schemas import MainlineEndurance, FamilySuppression, UnifiedContext
from src.got.nodes.aggregator import AggregatorNode


def _endurance(name, band="topping_risk", warning_signals=None, tiers=None):
    return MainlineEndurance(
        name=name, wave_days=4, endurance_band=band,
        warning_signals=warning_signals or [], tiers=tiers or {},
    )


def _prev(name, days_suppressed=0, days_healthy=0, aggravated=False, band="topping_risk"):
    """前日 unified.json 里 family_suppression 条目的 dict 形态。"""
    return {"name": name, "endurance_band": band, "aggravated": aggravated,
            "warning_signals": [], "members": [], "days_suppressed": days_suppressed,
            "days_healthy": days_healthy}


# ────────────────────────── schema 缺省值 ──────────────────────────
class TestFamilySuppressionSchema(unittest.TestCase):
    def test_defaults(self):
        fs = FamilySuppression(name="医药")
        self.assertEqual(fs.endurance_band, "topping_risk")
        self.assertFalse(fs.aggravated)
        self.assertEqual(fs.days_suppressed, 0)
        self.assertEqual(fs.days_healthy, 0)
        self.assertEqual(fs.mode, "shadow")
        self.assertEqual(fs.members, [])
        self.assertEqual(fs.warning_signals, [])

    def test_unified_context_default_empty(self):
        """UnifiedContext 未显式设置 → family_suppression 为空（off 态不产台账）。"""
        uc = UnifiedContext(status="normal", unified_signal="neutral",
                            overall_confidence=0.5, final_position_limit=0.5)
        self.assertEqual(uc.family_suppression, [])


# ────────────────────────── Node6 latch 合成 ──────────────────────────
class TestSynthesizeFamilySuppression(unittest.TestCase):
    def _call(self, endurance, prev_map, env=None):
        with patch.dict(os.environ, env or {}):
            return AggregatorNode._synthesize_family_suppression(endurance, prev_map)

    # ── 进入 ──
    def test_new_topping_enters_suppression(self):
        """今日 topping + 前日未压 → 新建 days_suppressed=0, days_healthy=0。"""
        out = self._call([_endurance("医药", tiers={"mid": ["医药商业"], "new": ["创新药"]})], {})
        self.assertEqual(len(out), 1)
        fs = out[0]
        self.assertEqual(fs.name, "医药")
        self.assertEqual(fs.days_suppressed, 0)
        self.assertEqual(fs.days_healthy, 0)
        self.assertEqual(set(fs.members), {"医药商业", "创新药"})

    def test_non_topping_not_suppressed(self):
        """inertia / divergence 家族不进压制台账。"""
        out = self._call([_endurance("金融", band="divergence"),
                          _endurance("其他", band="inertia")], {})
        self.assertEqual(out, [])

    # ── aggravated 判据 ──
    def test_aggravated_when_break_signal(self):
        out = self._call([_endurance("医药", warning_signals=[
            "资金面未确认（rejected）", "破位信号（跌幅>3%或趋势下行）"])], {})
        self.assertTrue(out[0].aggravated)

    def test_not_aggravated_without_break(self):
        out = self._call([_endurance("医药", warning_signals=[
            "无近期催化剂", "资金面未确认（rejected）"])], {})
        self.assertFalse(out[0].aggravated)

    # ── 持续 ──
    def test_persist_increments_days_suppressed(self):
        """今日 topping + 前日已压 → days_suppressed+=1, days_healthy 归零。"""
        out = self._call([_endurance("医药")], {"医药": _prev("医药", days_suppressed=2)})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].days_suppressed, 3)
        self.assertEqual(out[0].days_healthy, 0)

    # ── 健康迟滞退出 ──
    def test_healthy_first_day_keeps_latch(self):
        """今日非 topping + 前日已压（days_healthy=0）→ days_healthy=1 < 2 → 仍压制。"""
        out = self._call([_endurance("医药", band="divergence")],
                         {"医药": _prev("医药", days_suppressed=1, days_healthy=0)})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].days_healthy, 1)
        self.assertEqual(out[0].days_suppressed, 2)

    def test_healthy_exit_after_threshold(self):
        """连续健康达 EXIT_HEALTHY(2) → 释放。"""
        out = self._call([_endurance("医药", band="divergence")],
                         {"医药": _prev("医药", days_suppressed=1, days_healthy=1)})
        self.assertEqual(out, [])

    def test_env_exit_healthy_configurable(self):
        """EXIT_HEALTHY=1 → 单个健康日即释放。"""
        out = self._call([_endurance("医药", band="divergence")],
                         {"医药": _prev("医药", days_suppressed=1, days_healthy=0)},
                         {"GOT_FAMILY_TOPPING_EXIT_HEALTHY": "1"})
        self.assertEqual(out, [])

    # ── 上限释放（防振荡永久锁）──
    def test_cooldown_release(self):
        """days_suppressed 达 COOLDOWN 上限 → 即便健康日不足也释放。"""
        out = self._call([_endurance("医药", band="divergence")],
                         {"医药": _prev("医药", days_suppressed=5, days_healthy=0)},
                         {"GOT_FAMILY_TOPPING_COOLDOWN": "5"})
        self.assertEqual(out, [])

    def test_cooldown_not_yet_reached_keeps(self):
        """days_suppressed 未达 COOLDOWN 且健康日不足 → 保持压制。"""
        out = self._call([_endurance("医药", band="divergence")],
                         {"医药": _prev("医药", days_suppressed=3, days_healthy=0)},
                         {"GOT_FAMILY_TOPPING_COOLDOWN": "5"})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].days_suppressed, 4)

    # ── 家族跌出 active_mainline（今日不在 endurance）仍走 latch ──
    def test_family_dropped_still_latched(self):
        out = self._call([], {"医药": _prev("医药", days_suppressed=1, days_healthy=0)})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].name, "医药")
        self.assertEqual(out[0].days_healthy, 1)

    # ── 模式快照 ──
    def test_mode_snapshot(self):
        out = self._call([_endurance("医药")], {}, {"GOT_FAMILY_TOPPING_MODE": "veto"})
        self.assertEqual(out[0].mode, "veto")


if __name__ == "__main__":
    unittest.main()
