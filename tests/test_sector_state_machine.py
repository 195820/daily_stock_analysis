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
        )
        base.update(overrides)
        return base

    def test_coldstart_neutral_by_default(self):
        """冷启动且无启动信号 → neutral。"""
        e = self._evidence(prev_state=None, q="lagging", strength_score=40.0)
        assert SectorAnalysisNode._apply_state_machine(**e) == "neutral"

    def test_coldstart_leading_strong_enters_active(self):
        """冷启动 + leading + score>60 + 未破位 → active_mainline（首次进入）。"""
        e = self._evidence(prev_state=None, q="leading", strength_score=65.0,
                           price_chg_5d=0.03, above_ma20=True)
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
