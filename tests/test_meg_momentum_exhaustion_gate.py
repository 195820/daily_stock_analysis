# -*- coding: utf-8 -*-
"""MEG 动能衰竭门（GOT_MOM_EXHAUST_*）影子模式测试（2026-10-09）

覆盖 §7.3/§7.4/§7.5/§7.7 三层：
- 层1 信号：SectorAnalysisNode._compute_momentum_state 三态 + 数据不足豁免 + env 阈值
- 层2 action 门：acceleration+snap+buy_trend → shadow 留痕不改 action / veto 降 watch / off 零 diff
- 层3 仓位缩放：acceleration+snap → veto 降 base 到 caution 档 / shadow 仅留痕 / off 零 diff

回测证真（§7.8.1，2026-10-08）：snap-thrust T+5 横截面超额差 -2.54%（Welch t=-3.28, p=0.001），
立案案例 880952 芯片 9/23 命中 -9.41%。
影子规范：缺省 GOT_MOM_EXHAUST_GATE_ENABLED=0 → 逐字节不变；MODE=shadow → 仅留痕不改决策。
层2 夹具与 test_bear_entry_gate 同构（驱动真实 _score_candidates）；regime=transition 隔离 bear 门。
"""
import json
import os
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.got.schemas import (
    CapitalContext, CapitalEvidence, DataQuality, NewsContext, SentimentContext,
    SectorContext, UnifiedContext, SectorIntel, LifecycleInfo,
)


# ────────────────────────── 共用夹具（与 test_bear_entry_gate 同构）──────────────────────────
def _make_capital():
    return CapitalContext(status="normal", institutional_attitude="neutral",
                          institutional_score=0.0,
                          evidence=CapitalEvidence(), reasoning=None, data_quality=DataQuality())


def _make_news():
    return NewsContext(overall_impact="neutral", major_events=[], concept_suggestions=[],
                       impact_score=0.0, reasoning=None, data_quality=DataQuality())


def _make_sentiment():
    return SentimentContext(overall_sentiment="neutral", sentiment_score=0.0,
                            sentiment_stage="neutral", signal_confidence=0.5, data_quality=DataQuality(),
                            stage_state=None, persistence_regime=None)


def _make_sector_ctx():
    return SectorContext(active_mainline=[], ambush=[], near_miss_ambush=[], strength_lookup={})


def _make_unified(confirmed_sectors=None, ambush_pool=None):
    return UnifiedContext(status="normal", unified_signal="neutral", overall_confidence=0.5,
                          final_position_limit=0.5, confirmed_sectors=confirmed_sectors or [],
                          ambush_pool=ambush_pool or [])


def _make_stock(code="300001", name="测试股", sector_name="半导体", **kw):
    from src.services.stock_screener import ScreenerStock
    defaults = dict(sector_score=90.0, capital_score=90.0, capital_available=True,
                    news_score=90.0, news_available=True, tech_wave_score=90.0,
                    risk_score_safe=80.0)
    defaults.update(kw)
    return ScreenerStock(code=code, name=name, sector_name=sector_name, **defaults)


def _intel(lifecycle_phase="acceleration", momentum_state="snap", rs_mom_decay_2d=-0.44,
           lifecycle_day=1):
    """sector_intel_map 值（dict）——MEG 层2 门读 momentum_state / rs_mom_decay_2d。"""
    return {"classification": "actionable", "capital_check": "neutral",
            "news_check": "neutral", "sentiment_check": "low",
            "lifecycle_phase": lifecycle_phase, "lifecycle_day": lifecycle_day,
            "momentum_state": momentum_state, "rs_mom_decay_2d": rs_mom_decay_2d}


def _market_with_regime(regime):
    m = MagicMock()
    m.model_dump.return_value = {}
    m.market_state = types.SimpleNamespace(regime=regime) if regime is not None else None
    return m


# ────────────────────────── 层1：_compute_momentum_state 纯函数三态 ──────────────────────────
class TestComputeMomentumState(unittest.TestCase):
    """层1 信号：decay_2d=(ema[t]-ema[t-2])/ema[t-2]，与回测口径一致。"""

    @staticmethod
    def _rrg(ema_t):
        return types.SimpleNamespace(rs_momentum=ema_t)

    @staticmethod
    def _prev(traj):
        return {"momentum_trajectory": traj}

    def _call(self, ema_t, traj, env=None):
        from src.got.nodes.sector_analysis import SectorAnalysisNode
        with patch.dict(os.environ, env or {}):
            return SectorAnalysisNode._compute_momentum_state(self._rrg(ema_t), self._prev(traj))

    def test_snap_two_day_break(self):
        # ema[t]=0.5, ema[t-2]=traj[-2]=0.9 → decay=-0.444 ≤ -0.30 → snap
        state, decay = self._call(0.5, [1.0, 0.9, 0.8])
        self.assertEqual(state, "snap")
        self.assertAlmostEqual(decay, -0.444, places=3)

    def test_stall_below_prev_but_not_break(self):
        # ema[t]=0.85 < ema[t-1]=traj[-1]=0.86，decay=-0.034 > -0.30 → stall
        state, decay = self._call(0.85, [0.9, 0.88, 0.86])
        self.assertEqual(state, "stall")
        self.assertAlmostEqual(decay, -0.034, places=3)

    def test_thrust_advancing(self):
        # ema[t]=0.9 ≥ ema[t-1]=0.8，decay=+0.286 → thrust
        state, decay = self._call(0.9, [0.6, 0.7, 0.8])
        self.assertEqual(state, "thrust")
        self.assertAlmostEqual(decay, 0.286, places=3)

    def test_none_when_traj_too_short(self):
        # 无 t-2 → 数据不足豁免（下游门自动不触发 → 零行为变化）
        self.assertEqual(self._call(0.5, [0.8]), (None, None))

    def test_none_when_rrg_none(self):
        from src.got.nodes.sector_analysis import SectorAnalysisNode
        self.assertEqual(SectorAnalysisNode._compute_momentum_state(None, self._prev([0.9, 0.8])),
                         (None, None))

    def test_none_when_ema_t2_zero(self):
        # ema[t-2]=traj[-2]=0.0 → 除零守卫 → 豁免
        self.assertEqual(self._call(0.5, [0.0, 0.5]), (None, None))

    def test_env_threshold_flips_stall_to_snap(self):
        # decay=-0.118：缺省阈值 -0.30 → stall；收紧到 -0.10 → snap
        ema_t, traj = 0.75, [0.9, 0.85, 0.8]
        self.assertEqual(self._call(ema_t, traj)[0], "stall")
        self.assertEqual(self._call(ema_t, traj, {"GOT_MOM_EXHAUST_DECAY": "-0.10"})[0], "snap")


# ────────────────────────── 层2：acceleration+snap → buy_trend 降级 ──────────────────────────
class _MegGateHarness(unittest.IsolatedAsyncioTestCase):

    async def _run(self, stock, intel, env, regime="transition"):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode(use_mock=True)
        node._build_sector_intel_map = lambda uc: {stock.sector_name: intel}
        node._adjust_score_by_sector_intel = lambda base, name, mm: base
        node._compute_overheat_penalty = lambda name, mm, uc: 1.0
        node._collect_shadow_t1 = lambda finalists: None

        async def _fake_verify(finalists, sector_intel_map):
            return finalists
        node._verify_candidate_sectors = _fake_verify

        screener_mock = MagicMock()
        screener_mock._extract_got_universe.return_value = [{"code": stock.code}]
        screener_mock._filter_got_finalists.return_value = [stock]

        with patch("src.services.stock_screener.StockScreenerPipeline", return_value=screener_mock), \
             patch.dict(os.environ, env):
            results = await node._score_candidates(
                _make_sector_ctx(), _make_capital(), _make_news(),
                _market_with_regime(regime), _make_unified(), _make_sentiment(),
            )
        self.assertEqual(len(results), 1)
        return node, results[0]


_STEP3 = {"GOT_WINRATE_STEP3_ENABLED": "1"}


class TestMegActionGate(_MegGateHarness):

    async def test_off_zero_diff(self):
        """缺省 ENABLED=0：acceleration+snap 仍 buy_trend，reasoning 无 MEG 痕迹，不落 shadow_meg。"""
        stock = _make_stock()
        node, res = await self._run(stock, _intel(), {**_STEP3, "GOT_MOM_EXHAUST_GATE_ENABLED": "0"})
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertNotIn("MEG", res.reasoning)
        self.assertEqual(node._meg_shadow, {})   # off 态无留痕 → 不写 shadow_meg.json

    async def test_shadow_flags_without_changing_action(self):
        """shadow：留痕（reasoning 有 MEG影子）但 action 逐字节不变（仍 buy_trend）。"""
        stock = _make_stock()
        node, res = await self._run(stock, _intel(),
                                 {**_STEP3, "GOT_MOM_EXHAUST_GATE_ENABLED": "1",
                                  "GOT_MOM_EXHAUST_MODE": "shadow"})
        self.assertEqual(res.action_recommendation, "buy_trend")   # ← 不改 action
        self.assertIn("MEG影子", res.reasoning)                    # ← 留痕
        self.assertEqual(node._meg_shadow["mode"], "shadow")        # ← 影子留痕汇总
        self.assertEqual(len(node._meg_shadow["flagged_stocks"]), 1)
        self.assertEqual(node._meg_shadow["flagged_stocks"][0]["symbol"], stock.code)

    async def test_veto_downgrades_to_watch(self):
        """veto：acceleration+snap+buy_trend → watch，reasoning 记录断裂幅度。"""
        stock = _make_stock()
        node, res = await self._run(stock, _intel(),
                                 {**_STEP3, "GOT_MOM_EXHAUST_GATE_ENABLED": "1",
                                  "GOT_MOM_EXHAUST_MODE": "veto"})
        self.assertEqual(res.action_recommendation, "watch")
        self.assertIn("MEG动能衰竭门", res.reasoning)
        self.assertEqual(node._meg_shadow["mode"], "veto")
        self.assertEqual(node._meg_shadow["flagged_stocks"][0]["action_wouldbe"], "watch")

    async def test_non_snap_unchanged(self):
        """thrust（非 snap）：即便 veto 也不触发（只拦断裂）。"""
        stock = _make_stock()
        _, res = await self._run(stock, _intel(momentum_state="thrust", rs_mom_decay_2d=0.28),
                                 {**_STEP3, "GOT_MOM_EXHAUST_GATE_ENABLED": "1",
                                  "GOT_MOM_EXHAUST_MODE": "veto"})
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertNotIn("MEG", res.reasoning)

    async def test_none_state_unchanged(self):
        """momentum_state=None（数据不足豁免）：veto 也不触发。"""
        stock = _make_stock()
        _, res = await self._run(stock, _intel(momentum_state=None, rs_mom_decay_2d=None),
                                 {**_STEP3, "GOT_MOM_EXHAUST_GATE_ENABLED": "1",
                                  "GOT_MOM_EXHAUST_MODE": "veto"})
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertNotIn("MEG", res.reasoning)

    async def test_non_acceleration_unchanged(self):
        """launch（非 acceleration）+snap：veto 不触发（门限 acceleration 末期）。"""
        stock = _make_stock()
        _, res = await self._run(stock, _intel(lifecycle_phase="launch"),
                                 {**_STEP3, "GOT_MOM_EXHAUST_GATE_ENABLED": "1",
                                  "GOT_MOM_EXHAUST_MODE": "veto"})
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertNotIn("MEG", res.reasoning)


# ────────────────────────── 层3：acceleration+snap → base 降到 caution 档 ──────────────────────────
class TestMegSizing(unittest.TestCase):
    """层3 仓位缩放：mainline+transition 下 base 0.40→0.12（veto），shadow/off 保持 0.40。

    limit = round(base × regime_m(0.65) × role_m(mainline 1.0) × conf(1.0), 3)
      base=0.40 → 0.26 ；base=0.12 → 0.078
    """

    def _limit(self, env, momentum_state="snap", phase="acceleration",
               role="mainline", classification="actionable"):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        si = SectorIntel(sector_name="半导体", sector_role=role, classification=classification,
                         confidence=1.0, momentum_state=momentum_state,
                         lifecycle=LifecycleInfo(phase=phase))
        with patch.dict(os.environ, env):
            ra = node._assess_risk(_make_unified(confirmed_sectors=[si]), {})
        return ra.sector_exposure_limits.get("半导体")

    def test_off_base_unchanged(self):
        self.assertAlmostEqual(self._limit({"GOT_MOM_EXHAUST_GATE_ENABLED": "0"}), 0.26, places=3)

    def test_shadow_keeps_base(self):
        """shadow：留痕（日志）但 base 不缩放 → 与 off 逐字节一致。"""
        lim = self._limit({"GOT_MOM_EXHAUST_GATE_ENABLED": "1", "GOT_MOM_EXHAUST_MODE": "shadow"})
        self.assertAlmostEqual(lim, 0.26, places=3)

    def test_veto_reduces_base(self):
        lim = self._limit({"GOT_MOM_EXHAUST_GATE_ENABLED": "1", "GOT_MOM_EXHAUST_MODE": "veto"})
        self.assertAlmostEqual(lim, 0.078, places=3)

    def test_non_snap_keeps_base(self):
        lim = self._limit({"GOT_MOM_EXHAUST_GATE_ENABLED": "1", "GOT_MOM_EXHAUST_MODE": "veto"},
                          momentum_state="thrust")
        self.assertAlmostEqual(lim, 0.26, places=3)

    def test_non_acceleration_keeps_base(self):
        lim = self._limit({"GOT_MOM_EXHAUST_GATE_ENABLED": "1", "GOT_MOM_EXHAUST_MODE": "veto"},
                          phase="launch")
        self.assertAlmostEqual(lim, 0.26, places=3)

    def test_sizing_subtoggle_off(self):
        """SIZING=0：即便 GATE_ENABLED=1+veto，层3 也不缩放（层2/层3 独立开关）。"""
        lim = self._limit({"GOT_MOM_EXHAUST_GATE_ENABLED": "1", "GOT_MOM_EXHAUST_MODE": "veto",
                           "GOT_MOM_EXHAUST_SIZING": "0"})
        self.assertAlmostEqual(lim, 0.26, places=3)

    def test_veto_no_downscale_when_already_caution(self):
        """caution 分类 base=0.12 已等于 caution 档 → base>_caution_base 为假，不再缩（幂等）。"""
        lim = self._limit({"GOT_MOM_EXHAUST_GATE_ENABLED": "1", "GOT_MOM_EXHAUST_MODE": "veto"},
                          classification="caution")
        self.assertAlmostEqual(lim, 0.078, places=3)   # 0.12 × 0.65 = 0.078，与 veto 后一致


# ────────────────────────── 影子留痕落盘 + reasoning.md 渲染（第6条影子线）──────────────────────────
class TestMegShadowArtifact(unittest.TestCase):
    """shadow_meg.json 仅 ENABLED 时写；archive 渲染缺产物时返回 []（off 态逐字节不变）。"""

    _MEG = {
        "line": "meg", "executed": True, "mode": "shadow", "sizing": True,
        "decay_th": "-0.30", "evaluated": 7, "mode_note": "",
        "flagged_stocks": [{"symbol": "300001", "name": "测试股", "sector": "半导体",
                            "decay_2d": -0.44, "mode": "shadow",
                            "action_before": "buy_trend", "action_wouldbe": "watch"}],
    }

    def test_save_writes_meg_json(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        node._shadow_t1 = {}
        node._shadow_t1h = {}
        node._meg_shadow = dict(self._MEG)
        node._meg_sizing_flags = [{"sector_name": "半导体", "decay_2d": -0.44,
                                   "role": "mainline", "base_before": 0.4,
                                   "base_wouldbe": 0.12, "mode": "shadow"}]
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                node._save_shadow_records()
                p = Path("data/got_daily") / date.today().strftime("%Y%m%d") / "shadow_meg.json"
                self.assertTrue(p.exists())
                rec = json.loads(p.read_text(encoding="utf-8"))
                self.assertEqual(rec["action_hits"], 1)
                self.assertEqual(rec["sizing_hits"], 1)
                self.assertEqual(rec["flagged_stocks"][0]["symbol"], "300001")
                self.assertEqual(rec["flagged_sectors"][0]["sector_name"], "半导体")
            finally:
                os.chdir(old)

    def test_off_writes_nothing(self):
        """全影子字段为空 → _save_shadow_records 不落任何文件（逐字节不变）。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                node._save_shadow_records()
                self.assertFalse(Path("data/got_daily").exists())
            finally:
                os.chdir(old)

    def test_render_present(self):
        from src.got import archive
        meg = dict(self._MEG, mode="veto", flagged_sectors=[])
        lines = archive._render_shadow_meg(meg)
        self.assertTrue(any("MEG 动能衰竭门" in l for l in lines))
        self.assertTrue(any("buy_trend→watch" in l for l in lines))

    def test_render_absent_returns_empty(self):
        from src.got import archive
        self.assertEqual(archive._render_shadow_meg(None), [])
        self.assertEqual(archive._render_shadow_meg({}), [])


if __name__ == "__main__":
    unittest.main()
