# -*- coding: utf-8 -*-
"""连续行情相位状态机（src/got/sector_lifecycle.py）行为测试。

锁定：启动完整条件、相位边界与精确时间权重、锚点继承、弱确认/硬破位退出、
decline 冷却、缺档只推进日历年龄、同日重放幂等、defensive 覆盖优先级。
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest import TestCase

from src.got.sector_lifecycle import (
    PHASE_DECLINE,
    PHASE_DORMANT,
    PHASE_EXPANSION,
    PHASE_LAUNCH,
    PHASE_MATURE,
    LifecycleEvidence,
    LifecycleState,
    build_lifecycle_evidence,
    can_start,
    compute_launch_score,
    get_lifecycle_mode,
    is_trend_broken,
    lifecycle_state_from_prev_trend,
    lifecycle_weight,
    map_lifecycle_phase,
    phase_age_inputs,
    transition_lifecycle,
)


def trading_calendar(start: date = date(2026, 7, 1), n: int = 40) -> list[date]:
    """工作日序列（测试用固定日历，不依赖 exchange_calendars）。"""
    days: list[date] = []
    cur = start
    while len(days) < n:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


CAL = trading_calendar()


def evidence(
    i: int,
    *,
    r5: float | None = 0.03,
    r10: float = 0.08,
    dist: float | None = -0.07,
    above: bool = True,
    r5_pct: float | None = 0.85,
    vol20_pct: float | None = 0.9,
    overheat: str = "normal",
    drop: float = 0.0,
    bar: bool = True,
    close: float | None = None,
    quadrant: str = "improving",
    legacy_state: str | None = None,
    legacy_wave_start: date | None = None,
    legacy_mainline_days: int | None = None,
    is_defensive: bool = False,
    slope: float | None = 0.01,
) -> LifecycleEvidence:
    return build_lifecycle_evidence(
        CAL[i],
        bar_complete=bar,
        trend_window={
            "close": close if close is not None else 100.0 + i,
            "r5": r5,
            "r10": r10,
            "high20_dist": dist,
            "above_ma20": above,
            "ma20_slope": slope,
        },
        r5_pct=r5_pct,
        vol20_pct=vol20_pct,
        strength=60.0,
        rrg_quadrant=quadrant,
        overheat_level=overheat,
        break_signals={
            "price_chg_5d": r5,
            "above_ma20": above,
            "high20_pos": None if dist is None else 1.0 + dist,
            "overheat_level": overheat,
            "score_drop": drop,
        },
        score_drop=drop,
        is_defensive=is_defensive,
        legacy_state=legacy_state,
        legacy_wave_start=legacy_wave_start,
        legacy_mainline_days=legacy_mainline_days,
    )


def weak_evidence(i: int, **kw) -> LifecycleEvidence:
    """弱势但未破位（r5 仍 >0、站上 MA20，仅 r10 转负）。"""
    return evidence(i, r5=0.001, r10=-0.01, **kw)


class TestLaunchConditions(TestCase):
    def test_full_condition_starts_launch(self):
        ev = evidence(0)
        self.assertTrue(can_start(ev))
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_LAUNCH)
        self.assertEqual(st.phase_age, 1)
        self.assertEqual(st.phase_dwell, 1)
        self.assertEqual(st.healthy_days, 1)
        self.assertEqual(st.anchor_date, CAL[0])
        self.assertEqual(st.transition_reason, "launch_start")
        self.assertEqual(st.data_status, "ok")

    def test_p1_boundary_conditions(self):
        """启动 = P1：站上 MA20 ∧ 距高 −10%~−5% ∧ r5 2%~5% ∧ vol20 分位 top30%。

        分位（r5_pct）与 MA20 斜率不参与启动——它们是诊断/延续输入。
        """
        # 距高边界（闭区间）
        self.assertTrue(can_start(evidence(0, dist=-0.10)))
        self.assertTrue(can_start(evidence(0, dist=-0.05)))
        self.assertFalse(can_start(evidence(0, dist=-0.11)))
        self.assertFalse(can_start(evidence(0, dist=-0.04)))
        # 5 日动量边界（闭区间）
        self.assertTrue(can_start(evidence(0, r5=0.02)))
        self.assertTrue(can_start(evidence(0, r5=0.05)))
        self.assertFalse(can_start(evidence(0, r5=0.019)))
        self.assertFalse(can_start(evidence(0, r5=0.051)))
        # vol20 截面分位门槛
        self.assertTrue(can_start(evidence(0, vol20_pct=0.70)))
        self.assertFalse(can_start(evidence(0, vol20_pct=0.69)))
        self.assertFalse(can_start(evidence(0, vol20_pct=None)))
        # r5_pct 不参与
        self.assertTrue(can_start(evidence(0, r5_pct=0.10)))
        self.assertTrue(can_start(evidence(0, r5_pct=None)))
        # MA20 斜率不参与
        self.assertTrue(can_start(evidence(0, slope=-0.01)))
        self.assertTrue(can_start(evidence(0, slope=None)))
        # 结构闸门仍在
        self.assertFalse(can_start(evidence(0, above=False)))
        self.assertFalse(can_start(evidence(0, overheat="exhausted")))

    def test_missing_data_does_not_start(self):
        for kwargs in (
            {"r5": None},
            {"dist": None},
            {"vol20_pct": None},
            {"bar": False},
            {"r5": -0.001},
            {"above": False},
            {"overheat": "exhausted"},
        ):
            with self.subTest(**kwargs):
                self.assertFalse(can_start(evidence(0, **kwargs)))

    def test_trend_broken_unknown_does_not_start(self):
        """trend_broken is None（缺必要字段）不算可交易启动。"""
        ev = build_lifecycle_evidence(
            CAL[0],
            bar_complete=True,
            trend_window={"r5": 0.05, "r10": 0.08, "high20_dist": -0.005, "close": 100.0},
            r5_pct=0.9,
            break_signals={"above_ma20": True, "high20_pos": 0.99, "overheat_level": "normal"},
        )
        self.assertIsNone(ev.trend_broken)
        self.assertFalse(can_start(ev))
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_DORMANT)

    def test_launch_score_formula_and_none(self):
        self.assertIsNone(compute_launch_score(None, 0.08, -0.005))
        self.assertIsNone(compute_launch_score(0.9, None, -0.005))
        score = compute_launch_score(0.9, 0.10, -0.02)
        # 0.5*0.9 + 0.25*clip((0.10+0.10)/0.20)=0.25 + 0.25*clip(0/0.02)=0
        self.assertAlmostEqual(score, 0.45 + 0.25, places=4)


class TestPhaseBoundaries(TestCase):
    def test_twenty_day_sequence_and_weights(self):
        st = None
        phases: list[str] = []
        weights: dict[int, float] = {}
        for i in range(20):
            st = transition_lifecycle(st, evidence(i, close=100.0 + 2 * i), CAL)
            phases.append(st.phase)
            weights[st.phase_age] = lifecycle_weight(st)
        self.assertEqual(phases[:3], [PHASE_LAUNCH] * 3)
        self.assertEqual(phases[3:10], [PHASE_EXPANSION] * 7)
        self.assertEqual(phases[10:], [PHASE_MATURE] * 10)
        # 第 20 日不因年龄自动 decline
        self.assertEqual(st.phase, PHASE_MATURE)
        self.assertEqual(weights[4], 1.00)
        self.assertEqual(weights[8], 0.92)
        self.assertEqual(weights[10], 0.88)
        self.assertEqual(weights[11], 0.85)
        self.assertEqual(weights[15], 0.73)
        self.assertEqual(weights[20], 0.58)
        self.assertEqual(lifecycle_weight(LifecycleState(
            phase=PHASE_MATURE, anchor_date=CAL[0], phase_age=23, phase_dwell=1,
            healthy_days=0, weak_days=0, peak_close=None, last_observation_date=None,
            transition_reason="phase_advance", data_status="ok",
        )), 0.50)
        self.assertEqual(lifecycle_weight(LifecycleState(
            phase=PHASE_MATURE, anchor_date=CAL[0], phase_age=25, phase_dwell=1,
            healthy_days=0, weak_days=0, peak_close=None, last_observation_date=None,
            transition_reason="phase_advance", data_status="ok",
        )), 0.50)

    def test_unknown_or_dormant_weight_is_one(self):
        unknown = LifecycleState(
            phase=PHASE_MATURE, anchor_date=CAL[0], phase_age=25, phase_dwell=1,
            healthy_days=0, weak_days=0, peak_close=None, last_observation_date=None,
            transition_reason="missing_bar", data_status="unknown",
        )
        self.assertEqual(lifecycle_weight(unknown), 1.0)
        self.assertEqual(lifecycle_weight(None), 1.0)
        self.assertEqual(lifecycle_weight(LifecycleState(
            phase=PHASE_DORMANT, anchor_date=None, phase_age=None, phase_dwell=0,
            healthy_days=0, weak_days=0, peak_close=None, last_observation_date=None,
            transition_reason="weak_hold", data_status="ok",
        )), 1.0)

    def test_boostrap_age_never_exceeds_one_weight(self):
        """bootstrap 给超大 age 也不得产生 >1.00 的权重。"""
        ev = evidence(20, legacy_state="active_mainline", legacy_mainline_days=99)
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_MATURE)
        self.assertLessEqual(lifecycle_weight(st), 1.0)


class TestAnchorInheritance(TestCase):
    def test_anchor_survives_phase_advance_and_rrg_swings(self):
        st = None
        anchors = set()
        for i in range(12):
            st = transition_lifecycle(
                st,
                evidence(i, quadrant="leading" if i % 2 == 0 else "lagging"),
                CAL,
            )
            anchors.add(st.anchor_date)
        self.assertEqual(anchors, {CAL[0]})
        self.assertEqual(st.phase_age, 12)
        self.assertEqual(st.phase, PHASE_MATURE)

    def test_single_weak_day_does_not_rebuild(self):
        st = None
        for i in range(4):
            st = transition_lifecycle(st, evidence(i), CAL)
        anchor = st.anchor_date
        age = st.phase_age
        st = transition_lifecycle(st, weak_evidence(4), CAL)
        self.assertEqual(st.anchor_date, anchor)
        self.assertEqual(st.phase, PHASE_EXPANSION)
        self.assertEqual(st.weak_days, 1)
        self.assertEqual(st.phase_dwell, 2)
        self.assertGreater(st.phase_age, age)
        self.assertEqual(st.transition_reason, "weak_hold")

    def test_three_real_weak_observations_decline(self):
        st = None
        for i in range(4):
            st = transition_lifecycle(st, evidence(i), CAL)
        for k in range(2):
            st = transition_lifecycle(st, weak_evidence(4 + k), CAL)
        self.assertEqual(st.phase, PHASE_EXPANSION)
        self.assertEqual(st.weak_days, 2)
        st = transition_lifecycle(st, weak_evidence(6), CAL)
        self.assertEqual(st.phase, PHASE_DECLINE)
        self.assertEqual(st.transition_reason, "weak_confirm_decline")
        self.assertEqual(st.phase_dwell, 1)
        self.assertEqual(st.anchor_date, CAL[0])

    def test_hard_break_is_immediate(self):
        st = None
        for i in range(3):
            st = transition_lifecycle(st, evidence(i), CAL)
        st = transition_lifecycle(st, evidence(3, r5=-0.02, above=False), CAL)
        self.assertEqual(st.phase, PHASE_DECLINE)
        self.assertEqual(st.transition_reason, "hard_break")
        self.assertEqual(st.healthy_days, 0)
        self.assertEqual(st.weak_days, 0)
        self.assertEqual(st.anchor_date, CAL[0])

    def test_decline_needs_two_observation_days_before_reentry(self):
        st = None
        for i in range(3):
            st = transition_lifecycle(st, evidence(i), CAL)
        st = transition_lifecycle(st, evidence(3, r5=-0.02, above=False), CAL)
        self.assertEqual(st.phase, PHASE_DECLINE)
        self.assertEqual(st.phase_dwell, 1)
        # 冷却第 1 个观测日：启动条件成立也不得开新段
        st = transition_lifecycle(st, evidence(4), CAL)
        self.assertEqual(st.phase, PHASE_DECLINE)
        self.assertEqual(st.phase_dwell, 2)
        self.assertEqual(st.transition_reason, "weak_hold")
        # 第 2 个观测日：允许重置
        st = transition_lifecycle(st, evidence(5), CAL)
        self.assertEqual(st.phase, PHASE_LAUNCH)
        self.assertEqual(st.transition_reason, "reentry")
        self.assertEqual(st.anchor_date, CAL[5])
        self.assertEqual(st.phase_age, 1)


class TestMissingBarAndIdempotency(TestCase):
    def test_missing_bar_advances_age_only(self):
        st = None
        for i in range(5):
            st = transition_lifecycle(st, evidence(i), CAL)
        before = st
        st = transition_lifecycle(st, evidence(5, bar=False), CAL)
        self.assertEqual(st.phase, before.phase)
        self.assertEqual(st.anchor_date, before.anchor_date)
        self.assertEqual(st.peak_close, before.peak_close)
        self.assertEqual(st.phase_dwell, before.phase_dwell)
        self.assertEqual(st.healthy_days, before.healthy_days)
        self.assertEqual(st.weak_days, before.weak_days)
        self.assertEqual(st.phase_age, before.phase_age + 1)
        self.assertEqual(st.data_status, "unknown")
        self.assertEqual(st.transition_reason, "missing_bar")
        # 缺档后继续缺档也不得推进观测计数
        st2 = transition_lifecycle(st, evidence(6, bar=False), CAL)
        self.assertEqual(st2.phase_dwell, before.phase_dwell)
        self.assertEqual(st2.phase_age, before.phase_age + 2)

    def test_missing_bar_without_history_is_unknown_dormant(self):
        st = transition_lifecycle(None, evidence(0, bar=False), CAL)
        self.assertEqual(st.phase, PHASE_DORMANT)
        self.assertIsNone(st.anchor_date)
        self.assertEqual(st.data_status, "unknown")
        self.assertEqual(st.transition_reason, "bootstrap_unknown")

    def test_same_day_replay_is_field_identical(self):
        history = [evidence(i) for i in range(6)]
        first = None
        for ev in history:
            first = transition_lifecycle(first, ev, CAL)
        second = None
        for ev in history:
            second = transition_lifecycle(second, ev, CAL)
        self.assertEqual(first, second)

    def test_date_outside_calendar_is_unknown(self):
        off_cal = date(2026, 7, 4)  # 周六：不在固定交易日历内
        ev = LifecycleEvidence(trade_date=off_cal, bar_complete=True,
                               r5=0.03, r10=0.08, r5_pct=0.9, above_ma20=True,
                               ma20_slope=0.01, vol20_pct=0.9,
                               high20_dist=-0.07, high20_pos=0.93, close=100.0,
                               overheat_level="normal", trend_broken=False)
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_LAUNCH)
        # 日期不在 calendar → 不猜锚，标记 unknown（当日新锚仍是已知事实，age=1）
        self.assertEqual(st.data_status, "unknown")
        self.assertEqual(st.phase_age, 1)


class TestBootstrap(TestCase):
    def test_active_mainline_bootstraps_age_from_wave_start(self):
        ev = evidence(20, legacy_state="active_mainline", legacy_wave_start=CAL[10])
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_MATURE)
        self.assertEqual(st.phase_age, 11)
        self.assertEqual(st.anchor_date, CAL[10])
        self.assertEqual(st.healthy_days, 1)
        self.assertEqual(st.weak_days, 0)
        self.assertEqual(st.transition_reason, "legacy_bootstrap")

    def test_high_position_cooling_forces_expansion_at_least_four(self):
        # 有效 wave_start 给出 young age=3 → launch 被强制为 expansion 且 age=max(3,4)
        ev = evidence(20, legacy_state="high_position_cooling", legacy_wave_start=CAL[18])
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_EXPANSION)
        self.assertEqual(st.phase_age, 4)
        self.assertEqual(st.anchor_date, CAL[17])
        self.assertEqual(st.healthy_days, 0)
        self.assertEqual(st.weak_days, 1)

    def test_active_mainline_bootstrap_broken_maps_to_decline_with_anchor(self):
        """首日即破位：保留 wave_start 锚日，但相位必须是 decline。"""
        ev = evidence(20, legacy_state="active_mainline", legacy_wave_start=CAL[10],
                      r5=-0.02, above=False)
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_DECLINE)
        self.assertEqual(st.anchor_date, CAL[10])
        self.assertEqual(st.healthy_days, 0)
        self.assertEqual(st.transition_reason, "legacy_bootstrap")

    def test_broken_first_day_without_legacy_mainline_state_has_no_anchor(self):
        """无旧主线状态又当日破位：decline 但无锚（不猜 anchor）。"""
        ev = evidence(0, legacy_state="neutral", r5=-0.02, above=False)
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_DECLINE)
        self.assertIsNone(st.anchor_date)
        self.assertEqual(st.transition_reason, "hard_break")

    def test_exhausted_mainline_maps_to_decline(self):
        ev = evidence(20, legacy_state="exhausted_mainline", legacy_wave_start=CAL[5])
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_DECLINE)
        self.assertEqual(st.phase_age, 16)
        self.assertEqual(st.healthy_days, 0)

    def test_ambush_legacy_state_does_not_bootstrap(self):
        """ambush/neutral/defensive 不 bootstrap：按正常启动规则（当日条件成立 → launch）。"""
        ev = evidence(0, legacy_state="ambush")
        st = transition_lifecycle(None, ev, CAL)
        self.assertEqual(st.phase, PHASE_LAUNCH)
        self.assertEqual(st.transition_reason, "launch_start")

    def test_phase_age_inputs_for_previous_anchor(self):
        st = None
        for i in range(4):
            st = transition_lifecycle(st, evidence(i), CAL)
        prev_phase, age = phase_age_inputs(st, evidence(4), CAL)
        self.assertEqual(prev_phase, PHASE_EXPANSION)
        self.assertEqual(age, 5)

    def test_lifecycle_state_from_prev_trend_requires_three_fields(self):
        self.assertIsNone(lifecycle_state_from_prev_trend(None))
        self.assertIsNone(lifecycle_state_from_prev_trend({}))
        self.assertIsNone(lifecycle_state_from_prev_trend({
            "lifecycle_continuous_phase": "launch",
            "lifecycle_anchor_date": CAL[0],
            "lifecycle_phase_age": 2,
            "lifecycle_phase_dwell": None,
        }))
        st = lifecycle_state_from_prev_trend({
            "lifecycle_continuous_phase": "launch",
            "lifecycle_anchor_date": CAL[0],
            "lifecycle_phase_age": 2,
            "lifecycle_phase_dwell": 2,
            "lifecycle_time_weight": 1.0,
        })
        self.assertIsInstance(st, LifecycleState)
        self.assertEqual(st.phase, PHASE_LAUNCH)
        self.assertEqual(st.phase_age, 2)


class TestTrendBrokenAndMapping(TestCase):
    def test_is_trend_broken_rules(self):
        self.assertTrue(is_trend_broken(-0.01, True, 0.99, "normal", 0.0))
        self.assertTrue(is_trend_broken(0.01, False, 0.99, "normal", 0.0))
        self.assertTrue(is_trend_broken(0.01, True, 0.5, "exhausted", 0.0))
        self.assertTrue(is_trend_broken(0.01, True, 0.9, "normal", 20.0))
        self.assertFalse(is_trend_broken(0.01, True, 0.9, "normal", 0.0))
        # 必要字段缺失 → None（不算退出）
        self.assertIsNone(is_trend_broken(0.01, True, 0.9, "normal", None))
        self.assertIsNone(is_trend_broken(None, True, 0.9, "normal", 0.0))

    def test_high20_pos_derived_from_dist(self):
        """high20_pos = 1+high20_dist，破位判据不在两处重复换算。"""
        ev = build_lifecycle_evidence(
            CAL[0], bar_complete=True,
            trend_window={"r5": 0.01, "r10": 0.02, "high20_dist": -0.41, "close": 10.0},
            r5_pct=0.9,
            break_signals={"above_ma20": True, "high20_pos": 0.59,
                           "overheat_level": "exhausted", "score_drop": 0.0},
        )
        self.assertTrue(ev.trend_broken)  # exhausted 且 high20_pos<0.60

    def test_map_lifecycle_phase(self):
        self.assertEqual(map_lifecycle_phase(PHASE_LAUNCH), "launch")
        self.assertEqual(map_lifecycle_phase(PHASE_EXPANSION), "acceleration")
        self.assertEqual(map_lifecycle_phase(PHASE_MATURE), "mature")
        self.assertEqual(map_lifecycle_phase(PHASE_DECLINE), "mature_exhausted")
        self.assertIsNone(map_lifecycle_phase(PHASE_DORMANT))


class TestModeSwitch(TestCase):
    def test_invalid_mode_falls_back_to_legacy(self):
        import os
        from unittest import mock

        for value, expected in (
            (None, "legacy"), ("legacy", "legacy"), ("enabled", "enabled"),
            ("ENABLED", "enabled"), ("ab", "legacy"), ("shadow", "legacy"), ("", "legacy"),
        ):
            with self.subTest(value=value):
                env = {} if value is None else {"GOT_NODE2_LIFECYCLE": value}
                with mock.patch.dict(os.environ, env, clear=False):
                    if value is None:
                        os.environ.pop("GOT_NODE2_LIFECYCLE", None)
                    self.assertEqual(get_lifecycle_mode(), expected)


if __name__ == "__main__":
    import unittest

    unittest.main()
