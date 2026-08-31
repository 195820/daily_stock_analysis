"""Node 2 板块生命周期状态机单元测试（2026-08-17 文献校准）。

覆盖：
1. _apply_state_machine 转移矩阵可达性（主线→埋伏非法边、高位降温过渡）
2. _is_ambush_candidate 埋伏新判据（排除黄金 08-17 类"高位残留"）
3. _ema_smooth 3 日 EMA 平滑
4. _compute_rrg 含 EMA 平滑的象限判定
5. _state_to_signal 回归（含 high_position_cooling）
6. SectorContext Schema 含 cooling_down 槽位

运行：python -m unittest tests.test_sector_state_machine -v
"""
import unittest
import pandas as pd
from datetime import date

from src.got.nodes.sector_analysis import SectorAnalysisNode
from src.got.schemas import SectorContext, SectorDetail, RRGQuadrant


# =================================================================
# 1. _apply_state_machine 转移矩阵
# =================================================================
class TestApplyStateMachine(unittest.TestCase):
    """规则版马尔可夫转移矩阵可达性测试。"""

    # 默认合成证据（未破位、neutral RRG）
    @staticmethod
    def _evidence(**overrides):
        base = dict(
            prev_state="neutral", prev_dwell=0,
            q="lagging", rs_momentum=0.3, strength_score=50.0,
            price_chg_5d=0.02, above_ma20=True, high20_pos=0.85,
            overheat_level="normal", today_change=1.0,
            score_drop=0.0,
            is_ambush=False, is_defensive=False,
            exit_pressure=0, tier="B",
        )
        base.update(overrides)
        return base

    def test_coldstart_neutral_by_default(self):
        """冷启动且无启动信号 → neutral。"""
        e = self._evidence(prev_state=None, q="lagging", strength_score=40.0)
        assert SectorAnalysisNode._apply_state_machine(**e) == "neutral"

    def test_coldstart_leading_strong_enters_active(self):
        """冷启动 + leading + score>60 + 未破位 → active_mainline（首次进入，S/A 级）。"""
        e = self._evidence(prev_state=None, q="leading", strength_score=65.0,
                           price_chg_5d=0.03, above_ma20=True, tier="A")
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_active_stays_active_when_healthy(self):
        """主线 + 健康证据（leading + score>55）→ 驻留 active。"""
        e = self._evidence(prev_state="active_mainline", prev_dwell=3,
                           q="leading", strength_score=70.0,
                           price_chg_5d=0.02, above_ma20=True)
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_active_to_cooling_on_weakening_not_broken(self):
        """主线 + weakening + 未破位 → high_position_cooling（核心路径，禁止直接到 ambush）。"""
        e = self._evidence(prev_state="active_mainline", q="weakening",
                           strength_score=62.0, price_chg_5d=0.01,
                           above_ma20=True, overheat_level="normal")
        assert SectorAnalysisNode._apply_state_machine(**e) == "high_position_cooling"

    def test_active_to_exhausted_on_broken(self):
        """主线 + 破位 → exhausted_mainline（当天踢）。"""
        e = self._evidence(prev_state="active_mainline", q="weakening",
                           price_chg_5d=-0.03, above_ma20=True)
        assert SectorAnalysisNode._apply_state_machine(**e) == "exhausted_mainline"

    def test_active_never_directly_to_ambush(self):
        """主线 + 黄金 08-17 类证据（weakening+ratio高+score78）→ 不得跳到 ambush。"""
        e = self._evidence(prev_state="active_mainline",
                           q="weakening", rs_momentum=0.0,
                           strength_score=78.0, price_chg_5d=0.01,
                           above_ma20=True, today_change=2.99,
                           score_drop=-10.0,
                           is_ambush=False, is_defensive=False)
        result = SectorAnalysisNode._apply_state_machine(**e)
        assert result in ("high_position_cooling", "neutral")
        assert result != "ambush", "主线必须经冷却/衰竭退出，不得直跳 ambush"

    def test_cooling_dwell1_no_upgrade(self):
        """冷却 + dwell=1（未到驻留确认）→ 即使修复也留在 cooling。"""
        e = self._evidence(prev_state="high_position_cooling", prev_dwell=1,
                           q="improving", strength_score=60.0,
                           price_chg_5d=0.01, above_ma20=True)
        assert SectorAnalysisNode._apply_state_machine(**e) == "high_position_cooling"

    def test_cooling_dwell2_upgrades_to_active(self):
        """冷却 + dwell>=2 + 修复确认（leading/improving + score>55 + 5d>0）→ active。"""
        e = self._evidence(prev_state="high_position_cooling", prev_dwell=2,
                           q="improving", strength_score=60.0,
                           price_chg_5d=0.01, above_ma20=True)
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_cooling_extinguishes_on_low_score(self):
        """冷却 + score<45 + 未破位 → neutral（低分熄灭，非破位）。"""
        e = self._evidence(prev_state="high_position_cooling", prev_dwell=3,
                           q="weakening", strength_score=38.0,
                           price_chg_5d=0.005,  # 未破位（5日涨幅仍正）
                           above_ma20=True, score_drop=0.0)
        assert SectorAnalysisNode._apply_state_machine(**e) == "neutral"

    def test_exhausted_requires_2day_confirm_to_reenter(self):
        """衰竭 + 修复条件满足但 dwell<2 → 继续观察，不得直接回 active。"""
        e = self._evidence(prev_state="exhausted_mainline", prev_dwell=1,
                           q="leading", strength_score=65.0,
                           price_chg_5d=0.02, today_change=1.0,
                           above_ma20=True, score_drop=0.0)
        assert SectorAnalysisNode._apply_state_machine(**e) == "exhausted_mainline"

    def test_exhausted_2day_confirm_reenters_active(self):
        """衰竭 + dwell>=2 + leading + score>60 + 5d>0 + today>0 → active。"""
        e = self._evidence(prev_state="exhausted_mainline", prev_dwell=2,
                           q="leading", strength_score=65.0,
                           price_chg_5d=0.02, today_change=1.0,
                           above_ma20=True, score_drop=0.0)
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_exhausted_after_3days_extinguishes(self):
        """衰竭 + dwell>=3 + 持续弱势(score<50) → neutral。"""
        e = self._evidence(prev_state="exhausted_mainline", prev_dwell=3,
                           q="lagging", strength_score=42.0,
                           price_chg_5d=-0.02, above_ma20=False)
        assert SectorAnalysisNode._apply_state_machine(**e) == "neutral"

    def test_ambush_to_active_on_breakout(self):
        """埋伏 + leading 突破 + score>60 + 驻留>=1 → active。"""
        e = self._evidence(prev_state="ambush", prev_dwell=1,
                           q="leading", rs_momentum=0.6,
                           strength_score=65.0, is_ambush=False)
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_ambush_to_neutral_on_momentum_drop(self):
        """埋伏 + momentum<0.5 连续 + 驻留>=2 → neutral（出埋伏）。"""
        e = self._evidence(prev_state="ambush", prev_dwell=2,
                           q="lagging", rs_momentum=0.3,
                           strength_score=48.0, is_ambush=False)
        assert SectorAnalysisNode._apply_state_machine(**e) == "neutral"

    def test_ambush_stays_when_conditions_hold(self):
        """埋伏 + 条件仍满足 → 驻留 ambush。"""
        e = self._evidence(prev_state="ambush", prev_dwell=2,
                           q="improving", rs_momentum=0.55,
                           strength_score=55.0, is_ambush=True)
        assert SectorAnalysisNode._apply_state_machine(**e) == "ambush"

    def test_hi2_surge_bypasses_dwell(self):
        """硬触发 HI2：单日涨>8% + leading + score>70 → 当日 active（豁免驻留）。"""
        e = self._evidence(prev_state="neutral", prev_dwell=0,
                           q="leading", strength_score=75.0,
                           today_change=9.5)
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_hi1_score_crash_immediate_demotion(self):
        """硬触发 HI1：单日 score 崩≥20 → 立即降级（broken 判定含 score_drop≥20）。"""
        # active → exhausted（score_drop>=20 触发 broken）
        e = self._evidence(prev_state="active_mainline", prev_dwell=2,
                           q="lagging", strength_score=30.0,
                           price_chg_5d=-0.01, above_ma20=False,
                           score_drop=25.0)
        assert SectorAnalysisNode._apply_state_machine(**e) == "exhausted_mainline"

    # ── 退出压力驱逐（2026-08-31 吸收原框逻辑）──

    def test_exit_pressure_SA_tolerance_3(self):
        """S/A 级退出压力容忍 3 天：pressure=3 → exhausted，pressure=2 → 驻留。"""
        # pressure=2 < tolerance(3) → 驻留（需 q=leading 才触发驻留分支）
        e = self._evidence(
            prev_state="active_mainline", prev_dwell=5,
            q="leading", strength_score=60.0,
            price_chg_5d=0.01, above_ma20=True,
            tier="S", exit_pressure=2,
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"
        # pressure=3 >= tolerance(3) → 衰竭（压力驱逐优先于驻留）
        e = self._evidence(
            prev_state="active_mainline", prev_dwell=5,
            q="leading", strength_score=60.0,
            price_chg_5d=0.01, above_ma20=True,
            tier="S", exit_pressure=3,
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "exhausted_mainline"

    def test_exit_pressure_B_tolerance_1(self):
        """B 级退出压力容忍 1 天：pressure=1 → exhausted。"""
        e = self._evidence(
            prev_state="active_mainline", prev_dwell=3,
            q="weakening", strength_score=55.0,
            price_chg_5d=0.01, above_ma20=True,
            tier="B", exit_pressure=1,
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "exhausted_mainline"

    def test_exit_pressure_priority_after_broken(self):
        """退出压力驱逐优先级：在 broken 判定之后。破位优先于压力驱逐。"""
        e = self._evidence(
            prev_state="active_mainline", prev_dwell=3,
            q="lagging", strength_score=40.0,
            price_chg_5d=-0.03, above_ma20=False,
            tier="S", exit_pressure=5,
        )
        # broken=True → exhausted（破位优先）
        assert SectorAnalysisNode._apply_state_machine(**e) == "exhausted_mainline"

    # ── 入口门槛分档（2026-08-31 吸收原框入框门槛）──

    def test_entry_threshold_SA_61_enters(self):
        """S/A 级入口：score=61 > 60 → active_mainline。"""
        e = self._evidence(
            prev_state=None, q="leading", strength_score=61.0,
            price_chg_5d=0.02, above_ma20=True, tier="S",
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_entry_threshold_B_80_enters(self):
        """B 级入口：score=80 恰好进入（>= 对齐原框 "score < min_score 才拒绝" 语义）。"""
        e = self._evidence(
            prev_state=None, q="leading", strength_score=80.0,
            price_chg_5d=0.02, above_ma20=True, tier="B",
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_entry_threshold_SA_60_rejected(self):
        """S/A 级入口：score=60 不进（严格 >60）。"""
        e = self._evidence(
            prev_state=None, q="leading", strength_score=60.0,
            price_chg_5d=0.02, above_ma20=True, tier="A",
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "neutral"

    def test_entry_threshold_B_79_rejected(self):
        """B 级入口：score=79 < 80 → neutral。"""
        e = self._evidence(
            prev_state=None, q="leading", strength_score=79.0,
            price_chg_5d=0.02, above_ma20=True, tier="B",
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "neutral"


# =================================================================
# 2. _ema_smooth 平滑
# =================================================================
class TestEmaSmooth(unittest.TestCase):
    """3 日 EMA 平滑（N=3, alpha≈0.5）。"""

    def test_first_observation_no_prev(self):
        """无前值时取原始值。"""
        assert SectorAnalysisNode._ema_smooth(None, 0.8) == 0.8

    def test_smoothing_convergence(self):
        """喂序列 [0.8, 0.2, 0.7, 0.75]，EMA 应逐步趋近原始序列。"""
        raws = [0.8, 0.2, 0.7, 0.75]
        prev = None
        emas = []
        for r in raws:
            prev = SectorAnalysisNode._ema_smooth(prev, r)
            emas.append(prev)
        # 第 1 日 = 0.8
        assert emas[0] == 0.8
        # 第 2 日：0.5*0.2 + 0.5*0.8 = 0.5
        assert abs(emas[1] - 0.5) < 1e-9
        # 第 3 日：0.5*0.7 + 0.5*0.5 = 0.6
        assert abs(emas[2] - 0.6) < 1e-9
        # 第 4 日：0.5*0.75 + 0.5*0.6 = 0.675
        assert abs(emas[3] - 0.675) < 1e-9

    def test_absorbs_daily_noise(self):
        """单日剧烈下跌不一次性翻转（黄金 08-13 类"截面挤压"）。"""
        # 从 0.9 平稳 → 单日跌到 0.4（被截面挤压）
        ema_prev = 0.9
        ema_after = SectorAnalysisNode._ema_smooth(ema_prev, 0.4)
        # EMA 后 = 0.5*0.4 + 0.5*0.9 = 0.65，仍在 0.5 以上 → 象限不翻转
        assert ema_after > 0.5


# =================================================================
# 3. _compute_rrg 含 EMA
# =================================================================
class TestComputeRRG(unittest.TestCase):
    """RRG 计算含 EMA 平滑，原始值保留 rs_ratio_raw/rs_momentum_raw。"""

    def test_returns_raw_and_smoothed(self):
        """EMA 字段平滑、raw 字段保留原始值。"""
        prev_trend = {
            "strength_trajectory": [50, 55, 60, 65, 70],  # momentum>0
            "rrg_ratio_ema": 0.45,
            "rrg_momentum_ema": 0.40,
        }
        # strength_score=60 在截面中位（below 2/5 = 0.4）
        all_scores = [40, 50, 60, 70, 80]
        rrg = SectorAnalysisNode._compute_rrg(60.0, prev_trend, all_scores)
        # rs_ratio_raw 是原始截面百分位（below=2, len=5 → 2/5=0.4）
        assert rrg.rs_ratio_raw == 0.4
        # rs_ratio 是 EMA 平滑：0.5*0.4 + 0.5*0.45 = 0.425
        assert abs(rrg.rs_ratio - 0.425) < 1e-9
        # rs_momentum_raw = 0.5 + (70-50)/5/10 = 0.5 + 0.4 = 0.9
        assert abs(rrg.rs_momentum_raw - 0.9) < 1e-9
        # rs_momentum EMA = 0.5*0.9 + 0.5*0.4 = 0.65
        assert abs(rrg.rs_momentum - 0.65) < 1e-9

    def test_first_observation_no_prev_trend(self):
        """无前日趋势时 EMA = 原始值。"""
        all_scores = [40, 50, 60, 70, 80]
        rrg = SectorAnalysisNode._compute_rrg(60.0, None, all_scores)
        assert rrg.rs_ratio == rrg.rs_ratio_raw
        assert rrg.rs_momentum == 0.5  # 无历史默认中性
        assert rrg.rs_momentum_raw == 0.5


# =================================================================
# 4. _state_to_signal 回归
# =================================================================
class TestStateToSignal(unittest.TestCase):
    def test_known_states(self):
        assert SectorAnalysisNode._state_to_signal("active_mainline") == "bullish"
        assert SectorAnalysisNode._state_to_signal("exhausted_mainline") == "bearish"
        assert SectorAnalysisNode._state_to_signal("ambush") == "neutral"
        assert SectorAnalysisNode._state_to_signal("defensive_rotation") == "neutral"

    def test_cooling_is_neutral(self):
        """2026-08-17 新增：high_position_cooling → neutral（对外信号保持三值）。"""
        assert SectorAnalysisNode._state_to_signal("high_position_cooling") == "neutral"

    def test_unknown_falls_back_to_neutral(self):
        assert SectorAnalysisNode._state_to_signal("unknown_state") == "neutral"


# =================================================================
# 5. Schema 回归
# =================================================================
class TestSchemaCoolingDown(unittest.TestCase):
    def test_sector_context_has_cooling_down(self):
        """SectorContext 含 cooling_down 字段，默认空列表。"""
        ctx = SectorContext()
        assert hasattr(ctx, "cooling_down")
        assert ctx.cooling_down == []

    def test_sector_detail_accepts_cooling_state(self):
        """SectorDetail.state 可接受 high_position_cooling。"""
        detail = SectorDetail(
            code="880328",
            name="黄金",
            state="high_position_cooling",
            strength_score=78.0,
        )
        assert detail.state == "high_position_cooling"

    def test_rrg_quadrant_has_raw_fields(self):
        """RRGQuadrant 含 rs_ratio_raw/rs_momentum_raw（诊断用）。"""
        rrg = RRGQuadrant(
            rs_ratio=0.5, rs_momentum=0.5, quadrant="leading",
            rs_ratio_raw=0.6, rs_momentum_raw=0.4,
        )
        assert rrg.rs_ratio_raw == 0.6
        assert rrg.rs_momentum_raw == 0.4


# =================================================================
# 6. exit_pressure 计算逻辑（_compute_exit_pressure 直测）
# =================================================================
class TestComputeExitPressure(unittest.TestCase):
    """验证 _compute_exit_pressure 累计规则（吸收原框 consecutive_low_days）。

    累计规则：
    - 仅前日为主线（active_mainline）才累计，其余状态归 0（等价原框只跟踪框内成员）
    - strength_score < 50 → prev + 1；score >= 50 → 归 0（叠加前）
    - today_change < -3.0 → 额外 +2；< -1.5 → 额外 +1（T-0 止损）
    """

    def test_low_score_accumulates_from_prev(self):
        """前日主线 + 今日低分 → prev+1（连续 3 天低分累计到 3）。"""
        ep = SectorAnalysisNode._compute_exit_pressure("active_mainline", 0, 48.0, 0.5)
        assert ep == 1
        ep = SectorAnalysisNode._compute_exit_pressure("active_mainline", ep, 47.0, 0.5)
        assert ep == 2
        ep = SectorAnalysisNode._compute_exit_pressure("active_mainline", ep, 46.0, 0.5)
        assert ep == 3

    def test_recovery_resets_pressure(self):
        """前日主线 + 今日 score>=50 → 归 0（即使带小额下跌）。"""
        ep = SectorAnalysisNode._compute_exit_pressure("active_mainline", 2, 55.0, -1.0)
        assert ep == 0

    def test_recovery_with_small_drop_adds_one(self):
        """score>=50 归 0 后叠加 T-0 -2.0% → 1。"""
        ep = SectorAnalysisNode._compute_exit_pressure("active_mainline", 2, 55.0, -2.0)
        assert ep == 1

    def test_t0_crash_on_healthy_score(self):
        """score>=50 归 0 + 单日暴跌 -3.5% → +2（T-0 硬止损）。"""
        ep = SectorAnalysisNode._compute_exit_pressure("active_mainline", 0, 65.0, -3.5)
        assert ep == 2

    def test_low_score_plus_crash_stacks(self):
        """score<50 (+1) + today<-3.0 (+2) = prev+3。"""
        ep = SectorAnalysisNode._compute_exit_pressure("active_mainline", 0, 45.0, -3.5)
        assert ep == 3

    def test_non_mainline_prev_no_accumulation(self):
        """前日非主线（neutral/ambush/cooling）→ 不累计，归 0（等价原框只跟踪框内成员）。"""
        for prev in (None, "neutral", "ambush", "high_position_cooling",
                     "exhausted_mainline", "defensive_rotation"):
            ep = SectorAnalysisNode._compute_exit_pressure(prev, 2, 40.0, -4.0)
            assert ep == 0, f"prev={prev} 不应累计退出压力"


# =================================================================
# 6b. exit_pressure 驱逐边集成（经 _apply_state_machine）
# =================================================================
class TestExitPressureEviction(unittest.TestCase):
    """验证压力值传入状态机后的驱逐行为（与累计规则端到端衔接）。"""

    def test_exit_pressure_combines_low_score_and_crash(self):
        """score<50 (+1) + today<-3.0 (+2) = 累计 +3。

        通过 _apply_state_machine 间接验证：B 级 pressure=3 → exhausted。
        """
        e = TestApplyStateMachine._evidence(
            prev_state="active_mainline", prev_dwell=2,
            q="weakening", strength_score=45.0,
            price_chg_5d=0.01, above_ma20=True,
            tier="B", exit_pressure=3,  # 模拟：score<50(+1) + crash(-3.5%→+2) = 3
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "exhausted_mainline"

    def test_exit_pressure_reset_on_recovery(self):
        """score>=50 → 压力归零（即使有 T-0 小额下跌）。

        模拟：score=60(>=50 → reset=0) + today=-1.0(< -1.5? no) = 0
        """
        e = TestApplyStateMachine._evidence(
            prev_state="active_mainline", prev_dwell=5,
            q="leading", strength_score=60.0,
            price_chg_5d=0.02, above_ma20=True,
            tier="S", exit_pressure=0,  # score>=50 → reset
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "active_mainline"

    def test_exit_pressure_t0_crash_only(self):
        """score>=50 (reset=0) + today<-3.0 (+2) = pressure=2。

        B 级容忍 1 天 → pressure=2 >= 1 → exhausted。
        """
        e = TestApplyStateMachine._evidence(
            prev_state="active_mainline", prev_dwell=3,
            q="leading", strength_score=65.0,
            price_chg_5d=0.02, above_ma20=True,
            tier="B", exit_pressure=2,  # reset=0 + crash=+2
        )
        assert SectorAnalysisNode._apply_state_machine(**e) == "exhausted_mainline"


# =================================================================
# 7. SectorDetail exit_pressure_days 字段
# =================================================================
class TestSectorDetailExitPressure(unittest.TestCase):
    def test_exit_pressure_days_field(self):
        """SectorDetail 接受 exit_pressure_days 字段。"""
        detail = SectorDetail(
            code="880328",
            name="黄金",
            state="active_mainline",
            strength_score=78.0,
            exit_pressure_days=2,
        )
        assert detail.exit_pressure_days == 2

    def test_exit_pressure_days_default_none(self):
        """exit_pressure_days 默认 None。"""
        detail = SectorDetail(
            code="880328",
            name="黄金",
            state="neutral",
            strength_score=50.0,
        )
        assert detail.exit_pressure_days is None
