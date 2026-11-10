# -*- coding: utf-8 -*-
"""Node 2 相位路由（legacy / enabled）：状态映射、无容量截断、全宇宙分位、单次计权。"""

from __future__ import annotations

import os
from datetime import date
from unittest import TestCase, mock

from src.got.nodes.sector_analysis import SectorAnalysisNode
from src.got.schemas import (
    AmbushCandidate,
    DataQuality,
    LifecycleInfo,
    MainlineGroup,
    SectorContext,
    SectorDetail,
)
from src.got.sector_lifecycle import (
    PHASE_DECLINE,
    PHASE_DORMANT,
    PHASE_EXPANSION,
    PHASE_LAUNCH,
    PHASE_MATURE,
    LifecycleEvidence,
    LifecycleState,
)

D0 = date(2026, 11, 3)


def lc_state(phase: str, age: int = 2, weak_days: int = 0, anchor: date | None = D0,
             data_status: str = "ok") -> LifecycleState:
    return LifecycleState(
        phase=phase,
        anchor_date=anchor,
        phase_age=age,
        phase_dwell=1,
        healthy_days=1 if weak_days == 0 else 0,
        weak_days=weak_days,
        peak_close=100.0,
        last_observation_date=D0,
        transition_reason="phase_advance",
        data_status=data_status,
    )


def ev(**kw) -> LifecycleEvidence:
    base = dict(
        trade_date=D0, bar_complete=True, r5=0.05, r10=0.08, r5_pct=0.9,
        above_ma20=True, high20_dist=-0.005, high20_pos=0.995, strength=80.0,
        close=100.0, rrg_quadrant="leading", overheat_level="normal",
        trend_broken=False, score_drop=0.0,
    )
    base.update(kw)
    return LifecycleEvidence(**base)


def detail(code: str, name: str, state: str, strength: float = 80.0,
           lifecycle: LifecycleInfo | None = None, indicators: dict | None = None,
           accumulation: float | None = None) -> SectorDetail:
    return SectorDetail(
        code=code, name=name, state=state, strength_score=strength,
        lifecycle=lifecycle, indicators=indicators or {},
        accumulation_score=accumulation, tier="A",
    )


def launch_info(score: float = 0.5, age: int = 2) -> LifecycleInfo:
    return LifecycleInfo(
        phase="launch", days_since_launch=age, continuous_phase=PHASE_LAUNCH,
        anchor_date=D0, phase_dwell=age, healthy_days=age, weak_days=0,
        transition_reason="launch_start", data_status="ok",
        time_weight=1.0, launch_score=score,
    )


class TestStateMapping(TestCase):
    node = SectorAnalysisNode(use_mock=True)

    def map(self, state: LifecycleState, **kw) -> str:
        return self.node._apply_lifecycle_state_machine(state, ev(**kw))

    def test_launch_maps_to_active_mainline(self):
        self.assertEqual(self.map(lc_state(PHASE_LAUNCH)), "active_mainline")

    def test_launch_with_unknown_break_maps_to_neutral(self):
        self.assertEqual(self.map(lc_state(PHASE_LAUNCH), trend_broken=None), "neutral")

    def test_launch_exhausted_never_ambush(self):
        self.assertEqual(
            self.map(lc_state(PHASE_LAUNCH), overheat_level="exhausted"), "neutral",
        )

    def test_expansion_maps_to_active(self):
        self.assertEqual(self.map(lc_state(PHASE_EXPANSION, age=6)), "active_mainline")
        self.assertEqual(self.map(lc_state(PHASE_EXPANSION), trend_broken=None), "neutral")

    def test_mature_rrg_weakening_cools(self):
        self.assertEqual(
            self.map(lc_state(PHASE_MATURE, age=12), rrg_quadrant="weakening"),
            "high_position_cooling",
        )
        self.assertEqual(
            self.map(lc_state(PHASE_MATURE, age=12), rrg_quadrant="leading"),
            "active_mainline",
        )

    def test_mature_weak_day_cools_without_phase_change(self):
        self.assertEqual(
            self.map(lc_state(PHASE_MATURE, age=12, weak_days=1)), "high_position_cooling",
        )

    def test_decline_always_exhausted(self):
        self.assertEqual(
            self.map(lc_state(PHASE_DECLINE, age=12), trend_broken=False), "exhausted_mainline",
        )
        self.assertEqual(
            self.map(lc_state(PHASE_DECLINE, age=12), trend_broken=None), "exhausted_mainline",
        )

    def test_defensive_only_covers_mature_and_dormant(self):
        self.assertEqual(self.map(lc_state(PHASE_DORMANT), is_defensive=True), "defensive_rotation")
        self.assertEqual(self.map(lc_state(PHASE_MATURE, age=12), is_defensive=True), "defensive_rotation")
        # launch/expansion 不被 defensive 抢成 neutral
        self.assertEqual(self.map(lc_state(PHASE_LAUNCH), is_defensive=True), "active_mainline")
        self.assertEqual(self.map(lc_state(PHASE_EXPANSION), is_defensive=True), "active_mainline")
        # dormant 未防守 → neutral
        self.assertEqual(self.map(lc_state(PHASE_DORMANT)), "neutral")


class TestAmbushRouting(TestCase):
    node = SectorAnalysisNode(use_mock=True)

    def _backstop_board(self, code: str, name: str, dist: float = -0.06) -> SectorDetail:
        return detail(
            code, name, "active_mainline", strength=70.0,
            lifecycle=launch_info(score=0.5),
            indicators={"pos_close": 100.0 * (1 + dist), "pos_high20": 100.0},
            accumulation=0.6,
        )

    def test_pullback_launch_enters_mainline_not_ambush(self):
        board = self._backstop_board("880001", "回调兜底")
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "enabled"}):
            out = self.node._build_mainline_output(SectorContext(), [board], attack_power=0.5)
        self.assertEqual(
            [m.code for g in out.active_mainline for m in g.members], ["880001"],
        )
        self.assertEqual(out.ambush, [])

    def test_ambush_uses_accumulation_score_when_v2_missing(self):
        """enabled 只写 accumulation_score；ambush 排序必须回落，否则恒 0、Track2 永远空。"""
        scored = detail("880002", "低位吸筹", "neutral", strength=50.0, accumulation=0.6)
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "enabled"}):
            out = self.node._select_ambush_by_score([scored], set())
        self.assertEqual([s.code for s in out], ["880002"])

    def test_ambush_excludes_mainline_even_with_high_accumulation(self):
        main = detail("880003", "主线", "active_mainline", strength=70.0, accumulation=0.9)
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "enabled"}):
            out = self.node._select_ambush_by_score([main], set())
        self.assertEqual(out, [])

    def test_legacy_still_caps_track1_at_16(self):
        boards = [
            detail(f"88{i:04d}", f"埋伏{i}", "ambush", strength=50.0 + i,
                   indicators={"ambush_score_v2": 0.5})
            for i in range(25)
        ]
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "legacy"}):
            result = self.node._build_mainline_output(SectorContext(), boards, attack_power=0.5)
        self.assertEqual(len(result.ambush), SectorAnalysisNode.AMBUSH_TRACK1_MAX)
        self.assertEqual(len(result.ambush), 16)

    def test_launch_watch_marks_mainline_output(self):
        board = self._backstop_board("880004", "兜底")
        board.entry_role = "backstop"
        group = MainlineGroup(
            group_name="兜底", group_score=70.0, tier="A",
            mainline_quality="weak", members=[board],
        )
        result = SectorContext(active_mainline=[group])
        rows = self.node._build_launch_watch([board], result)
        self.assertTrue(rows[0]["in_output"])
        self.assertIsNone(rows[0]["dropped_reason"])
        self.assertEqual(rows[0]["entry_role"], "backstop")

    def test_launch_watch_dropped_reason_capacity(self):
        board = self._backstop_board("880005", "兜底未入组")
        rows = self.node._build_launch_watch([board], SectorContext())
        self.assertFalse(rows[0]["in_output"])
        self.assertEqual(rows[0]["dropped_reason"], "mainline_capacity")

    def test_launch_watch_dropped_reason_entry_dist(self):
        board = self._backstop_board("880006", "贴高兜底", dist=-0.01)
        rows = self.node._build_launch_watch([board], SectorContext())
        self.assertEqual(rows[0]["dropped_reason"], "mainline_entry_dist")

    def test_accumulation_watch_sorted_and_capped(self):
        boards = [
            detail(f"88{i:04d}", f"吸筹{i}", "neutral", accumulation=0.1 + i * 0.01)
            for i in range(20)
        ]
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "enabled"}):
            rows = self.node._build_accumulation_watch(boards)
        self.assertEqual(len(rows), 16)
        self.assertGreater(rows[0]["ambush_score_v2"], rows[-1]["ambush_score_v2"])
        self.assertEqual(rows[0]["code"], "880019")


class TestMainlineEntryConstraint(TestCase):
    """主线入口位置约束：贴高板块不进新主线，字段缺失 fail-open。"""

    node = SectorAnalysisNode(use_mock=True)

    def _board(self, code: str, name: str, close: float, high20: float) -> SectorDetail:
        return detail(code, name, "active_mainline", strength=90.0,
                      indicators={"pos_close": close, "pos_high20": high20})

    def test_at_high_excluded_pullback_kept(self):
        high = self._board("880001", "贴高", 99.5, 100.0)      # dist = -0.5%
        pull = self._board("880002", "回调", 94.0, 100.0)      # dist = -6.0%
        with mock.patch.dict(os.environ, {"GOT_MAINLINE_ENTRY_MAX_DIST": "-0.02"}):
            out = self.node._build_mainline_output(SectorContext(), [high, pull], attack_power=0.5)
        names = [m.name for g in out.active_mainline for m in g.members]
        self.assertEqual(names, ["回调"])

    def test_missing_position_fields_fail_open(self):
        board = detail("880003", "缺字段", "active_mainline", strength=90.0, indicators={})
        with mock.patch.dict(os.environ, {"GOT_MAINLINE_ENTRY_MAX_DIST": "-0.02"}):
            out = self.node._build_mainline_output(SectorContext(), [board], attack_power=0.5)
        self.assertEqual(len(out.active_mainline), 1)

    def test_off_disables_constraint(self):
        high = self._board("880004", "贴高", 99.9, 100.0)
        with mock.patch.dict(os.environ, {"GOT_MAINLINE_ENTRY_MAX_DIST": "off"}):
            out = self.node._build_mainline_output(SectorContext(), [high], attack_power=0.5)
        self.assertEqual(len(out.active_mainline), 1)

    def test_all_at_high_yields_empty_mainline(self):
        boards = [self._board(f"88001{i}", f"贴高{i}", 99.0, 100.0) for i in range(3)]
        with mock.patch.dict(os.environ, {"GOT_MAINLINE_ENTRY_MAX_DIST": "-0.02"}):
            out = self.node._build_mainline_output(SectorContext(), boards, attack_power=0.5)
        self.assertEqual(out.active_mainline, [])


class TestUniversePercentile(TestCase):
    node = SectorAnalysisNode(use_mock=True)

    def test_percentile_uses_full_universe(self):
        boards = [{"code": f"{i:06d}", "name": f"B{i}"} for i in range(60)]
        quick = {
            f"{i:06d}": {"change_1d": 0.0, "change_3d": 0.01, "change_5d": i / 100,
                         "has_crash": False}
            for i in range(60)
        }
        pct_map, dq = self.node._r5_percentile_map(quick, boards)
        self.assertEqual(dq["data_status"], "ok")
        self.assertEqual(dq["quick_valid_count"], 60)
        self.assertAlmostEqual(pct_map["000059"], 1.0, places=6)
        self.assertAlmostEqual(pct_map["000000"], 1 / 60, places=6)

    def test_small_universe_is_unavailable(self):
        boards = [{"code": f"{i:06d}", "name": f"B{i}"} for i in range(10)]
        quick = {
            f"{i:06d}": {"change_1d": 0.0, "change_3d": 0.01, "change_5d": 0.01,
                         "has_crash": False}
            for i in range(10)
        }
        pct_map, dq = self.node._r5_percentile_map(quick, boards)
        self.assertEqual(pct_map, {})
        self.assertEqual(dq["data_status"], "unavailable")
        self.assertIn("quick_universe_below_min", dq["unavailable_reasons"])

    def test_high_missing_ratio_is_unavailable(self):
        boards = [{"code": f"{i:06d}", "name": f"B{i}"} for i in range(100)]
        quick = {
            f"{i:06d}": ({"change_1d": 0.0, "change_3d": 0.01, "change_5d": 0.01,
                          "has_crash": False} if i < 60 else {"change_1d": 0.0})
            for i in range(100)
        }
        pct_map, dq = self.node._r5_percentile_map(quick, boards)
        self.assertEqual(pct_map, {})
        self.assertIn("quick_missing_ratio_exceeded", dq["unavailable_reasons"])

    def test_launch_channel_is_budget_bounded(self):
        # 70 个板块全部 3/5 日同向 → 70 个候选；分位不再截断，只有抓取预算（120）
        boards = [{"code": f"{i:06d}", "name": f"B{i}"} for i in range(70)]
        quick = {}
        for i in range(70):
            change_5d = 0.50 if i >= 30 else 0.01
            quick[f"{i:06d}"] = {
                "change_1d": 0.0, "change_3d": 0.01, "change_5d": change_5d,
                "has_crash": False,
            }
        quality = DataQuality()
        selected, dq = self.node._discover_launch_boards(boards, quick, quality)
        self.assertEqual(len(selected), 70)
        self.assertEqual(dq["launch_candidates"], 70)
        self.assertEqual(selected[0]["code"], "000030")  # 按 change_5d 降序

    def test_noise_filter_and_crash_excluded(self):
        boards = [{"code": f"{i:06d}", "name": f"B{i}"} for i in range(60)]
        quick = {
            f"{i:06d}": {"change_1d": 0.0, "change_3d": 0.01, "change_5d": 0.2,
                         "has_crash": False}
            for i in range(60)
        }
        quick["000000"]["has_crash"] = True
        quick["000001"]["change_3d"] = -0.01
        quality = DataQuality()
        selected, _ = self.node._discover_launch_boards(boards, quick, quality)
        codes = {b["code"] for b in selected}
        self.assertNotIn("000000", codes)
        self.assertNotIn("000001", codes)


class TestSingleTimeWeight(TestCase):
    """时间权重只在 Node 6 composite 计一次；Node 7/个股 risk 不再另乘。"""

    def test_compute_sector_scores_no_phase_bonus_when_enabled(self):
        from src.got.nodes.aggregator import AggregatorNode

        agg = AggregatorNode.__new__(AggregatorNode)
        d = detail("880001", "加速", "active_mainline", strength=70.0,
                   lifecycle=LifecycleInfo(phase="acceleration", days_since_launch=5,
                                           continuous_phase=PHASE_EXPANSION, time_weight=0.98))
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "enabled"}):
            enabled_score, *_ = agg._compute_sector_scores("加速", d)
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "legacy"}):
            legacy_score, *_ = agg._compute_sector_scores("加速", d)
        self.assertAlmostEqual(enabled_score, 70.0, places=4)
        self.assertAlmostEqual(legacy_score, 78.0, places=4)

    def test_time_weight_applied_once_after_glue(self):
        from src.got.nodes.aggregator import AggregatorNode

        d = detail("880001", "成熟", "active_mainline", strength=80.0,
                   lifecycle=LifecycleInfo(phase="mature", days_since_launch=15,
                                           continuous_phase=PHASE_MATURE, time_weight=0.73))
        row = ("成熟", 90.0, "neutral", "neutral", d, "mainline",
               {"role": "mainline", "tier": "A", "quality": "strong", "mainline_days": 15}, {})
        patched = AggregatorNode._apply_lifecycle_time_weight([row])
        self.assertAlmostEqual(patched[0][1], 90.0 * 0.73, places=4)
        self.assertEqual(patched[0][-1]["_lifecycle_time_weight"], 0.73)
        # 整套 enabled 路径的最终分 = strength × time_weight（不含旧 phase ±分）
        agg = AggregatorNode.__new__(AggregatorNode)
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "enabled"}):
            base, *_ = agg._compute_sector_scores("成熟", d)
        self.assertAlmostEqual(base, 80.0, places=4)
        self.assertAlmostEqual(base * d.lifecycle.time_weight, 58.4, places=4)

    def test_classify_ignores_wave_coeff_when_enabled(self):
        from src.got.nodes.aggregator import AggregatorNode

        agg = AggregatorNode.__new__(AggregatorNode)
        old = detail("880001", "老波", "active_mainline", strength=90.0)
        old.wave_days = 10
        new = detail("880002", "新波", "active_mainline", strength=85.0)
        new.wave_days = 0
        cands = [
            ("老波", 90.0, "neutral", "neutral", old, "mainline"),
            ("新波", 85.0, "neutral", "neutral", new, "mainline"),
        ]
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "enabled"}):
            enabled_map = agg._classify_all_sectors(list(cands), top_n=1)
        with mock.patch.dict(os.environ, {"GOT_NODE2_LIFECYCLE": "legacy"}):
            legacy_map = agg._classify_all_sectors(list(cands), top_n=1)
        self.assertEqual(enabled_map.get("老波"), "actionable")
        self.assertEqual(enabled_map.get("新波"), "caution")
        self.assertEqual(legacy_map.get("新波"), "actionable")
        self.assertEqual(legacy_map.get("老波"), "caution")

    def test_missing_lifecycle_weight_is_one(self):
        from src.got.nodes.aggregator import AggregatorNode

        d = detail("880002", "中性", "neutral", strength=60.0)
        row = ("中性", 60.0, "neutral", "neutral", d, "mainline", {}, {})
        patched = AggregatorNode._apply_lifecycle_time_weight([row])
        self.assertAlmostEqual(patched[0][1], 60.0, places=4)


class TestPoolDetailBackfill(TestCase):
    def test_phase_lookup_backfills_synthetic_detail(self):
        from src.got.nodes.aggregator import AggregatorNode

        info = launch_info(score=0.7, age=3)
        sector = SectorContext(
            strength_lookup={"池外启动": 62.0},
            phase_lookup={"池外启动": info.model_dump(mode="json")},
        )
        cand = AmbushCandidate(
            sector_name="池外启动", sector_code="880999",
            catalyst_score=0.0, category_tag="test", accumulation_detail="test",
        )
        resolved = AggregatorNode._resolve_pool_sector_detail(sector, cand)
        self.assertIsNotNone(resolved)
        self.assertIsNotNone(resolved.lifecycle)
        self.assertEqual(resolved.lifecycle.continuous_phase, PHASE_LAUNCH)
        self.assertEqual(resolved.lifecycle.days_since_launch, 3)


if __name__ == "__main__":
    import unittest

    unittest.main()
