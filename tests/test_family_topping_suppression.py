# -*- coding: utf-8 -*-
"""家族见顶持续压制门（FTS, GOT_FAMILY_TOPPING_*）WI-A 测试：schema + Node6 latch 合成。

覆盖：
- FamilySuppression / UnifiedContext schema 缺省值（off 态字段为空）
- _synthesize_family_suppression 跨日 latch 三态转移：新建 / 持续 / 健康迟滞退出 / 上限释放
- aggravated 判据（破位 + ≥MIN_CONFIRMS 个正交硬确认项：资金rejected/严重过热/老波/拥挤）
- env 配置（COOLDOWN / EXIT_HEALTHY / MODE）

与 MEG 正交：MEG 看单日板块 decay 断裂（snap），FTS 看家族级 endurance_band=topping_risk + 跨日 latch。
Phase2 消费（层A 缩仓 / 层B 硬降）见 WI-B 测试。
"""
import os
import json
import types
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.got.schemas import (
    MainlineEndurance, FamilySuppression, UnifiedContext,
    CapitalContext, CapitalEvidence, DataQuality, NewsContext, SentimentContext,
    SectorContext, SectorIntel, LifecycleInfo,
)
from src.got.nodes.aggregator import AggregatorNode


def _endurance(name, band="topping_risk", warning_signals=None, tiers=None,
               capital_stance="missing", max_overheat=0.0, crowded=False, wave_days=4):
    return MainlineEndurance(
        name=name, wave_days=wave_days, endurance_band=band,
        warning_signals=warning_signals or [], tiers=tiers or {},
        capital_stance=capital_stance, max_overheat=max_overheat, crowded=crowded,
    )


def _prev(name, days_suppressed=0, days_healthy=0, aggravated=False, band="topping_risk"):
    """前日 unified.json 里 family_suppression 条目的 dict 形态。"""
    return {"name": name, "endurance_band": band, "aggravated": aggravated,
            "warning_signals": [], "members": [], "days_suppressed": days_suppressed,
            "days_healthy": days_healthy}


# ──────────────── WI-B 共用夹具（与 test_meg_momentum_exhaustion_gate 同构）────────────────
def _make_capital():
    return CapitalContext(status="normal", institutional_attitude="neutral",
                          institutional_score=0.0, confidence_multiplier=1.0,
                          evidence=CapitalEvidence(), reasoning=None, data_quality=DataQuality())


def _make_news():
    return NewsContext(overall_impact="neutral", major_events=[], concept_suggestions=[],
                       impact_score=0.0, reasoning=None, data_quality=DataQuality())


def _make_sentiment():
    return SentimentContext(overall_sentiment="neutral", sentiment_score=0.0,
                            sentiment_stage="neutral", trading_signal="neutral",
                            signal_confidence=0.5, data_quality=DataQuality(),
                            stage_state=None, persistence_regime=None)


def _make_sector_ctx():
    return SectorContext(active_mainline=[], ambush=[], near_miss_ambush=[], strength_lookup={})


def _make_unified(confirmed_sectors=None, family_suppression=None):
    return UnifiedContext(status="normal", unified_signal="neutral", overall_confidence=0.5,
                          final_position_limit=0.5, confirmed_sectors=confirmed_sectors or [],
                          family_suppression=family_suppression or [])


def _make_stock(code="600513", name="联环药业", sector_name="创新药", **kw):
    from src.services.stock_screener import ScreenerStock
    defaults = dict(sector_score=90.0, capital_score=90.0, capital_available=True,
                    news_score=90.0, news_available=True, tech_wave_score=90.0,
                    risk_score_safe=80.0)
    defaults.update(kw)
    return ScreenerStock(code=code, name=name, sector_name=sector_name, **defaults)


def _intel_fg(family="医药", lifecycle_phase="acceleration", momentum_state="thrust"):
    """sector_intel_map 值（dict）——FTS 层B 读 frame_group 做 join；momentum_state=thrust 隔离 MEG。"""
    return {"classification": "actionable", "capital_check": "neutral",
            "news_check": "neutral", "sentiment_check": "low",
            "lifecycle_phase": lifecycle_phase, "lifecycle_day": 1,
            "momentum_state": momentum_state, "rs_mom_decay_2d": 0.01,
            "frame_group": family}


def _market_with_regime(regime):
    m = MagicMock()
    m.model_dump.return_value = {}
    m.market_state = types.SimpleNamespace(regime=regime) if regime is not None else None
    return m


def _fs(name="医药", aggravated=True, days_suppressed=0):
    return FamilySuppression(name=name, aggravated=aggravated, days_suppressed=days_suppressed)


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

    # ── aggravated 判据（破位 + ≥1 硬确认）──
    def test_aggravated_break_plus_capital_rejected(self):
        """破位 + 资金 rejected → 1 硬确认 → aggravated（保留 20261009 医药立案）。"""
        out = self._call([_endurance("医药", warning_signals=[
            "资金面未确认（rejected）", "破位信号（跌幅>3%或趋势下行）"],
            capital_stance="rejected")], {})
        self.assertTrue(out[0].aggravated)

    def test_break_without_confirm_not_aggravated(self):
        """关键修正：仅破位、无任何正交硬确认 → 不 aggravated（旧判据会误判为加重）。"""
        out = self._call([_endurance("医药", warning_signals=[
            "无近期催化剂", "破位信号（跌幅>3%或趋势下行）"],
            capital_stance="missing")], {})
        self.assertFalse(out[0].aggravated)

    def test_aggravated_break_plus_severe_overheat(self):
        """破位 + 严重过热≥0.5 → aggravated。"""
        out = self._call([_endurance("医药", warning_signals=[
            "破位信号（跌幅>3%或趋势下行）", "严重过热（0.62）"],
            max_overheat=0.62)], {})
        self.assertTrue(out[0].aggravated)

    def test_aggravated_break_plus_old_wave(self):
        """破位 + 老波 wave_days≥6 → aggravated。"""
        out = self._call([_endurance("医药", warning_signals=[
            "破位信号（跌幅>3%或趋势下行）", "老波（wave_days=7）"],
            wave_days=7)], {})
        self.assertTrue(out[0].aggravated)

    def test_aggravated_break_plus_crowded(self):
        """破位 + 家族拥挤 → aggravated。"""
        out = self._call([_endurance("医药", warning_signals=[
            "破位信号（跌幅>3%或趋势下行）", "家族拥挤（≥3板块同族）"],
            crowded=True)], {})
        self.assertTrue(out[0].aggravated)

    def test_not_aggravated_without_break(self):
        """破位是必要条件：有硬确认但无破位 → 不 aggravated。"""
        out = self._call([_endurance("医药", warning_signals=[
            "资金面未确认（rejected）", "严重过热（0.70）"],
            capital_stance="rejected", max_overheat=0.70)], {})
        self.assertFalse(out[0].aggravated)

    def test_min_confirms_2_requires_two_confirms(self):
        """MIN_CONFIRMS=2：破位+单确认不加重，破位+双确认才加重。"""
        env = {"GOT_FAMILY_TOPPING_AGGRAVATED_MIN_CONFIRMS": "2"}
        single = self._call([_endurance("医药", warning_signals=[
            "破位信号（跌幅>3%或趋势下行）"], capital_stance="rejected")], {}, env)
        self.assertFalse(single[0].aggravated)
        double = self._call([_endurance("医药", warning_signals=[
            "破位信号（跌幅>3%或趋势下行）"],
            capital_stance="rejected", crowded=True)], {}, env)
        self.assertTrue(double[0].aggravated)

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


# ────────────────── WI-B 层B：家族被压制+aggravated → buy 硬降级 ──────────────────
class _FtsGateHarness(unittest.IsolatedAsyncioTestCase):

    async def _run(self, stock, intel, family_suppression, env, regime="transition"):
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

        unified = _make_unified(family_suppression=family_suppression)
        with patch("src.services.stock_screener.StockScreenerPipeline", return_value=screener_mock), \
             patch.dict(os.environ, env):
            results = await node._score_candidates(
                _make_sector_ctx(), _make_capital(), _make_news(),
                _market_with_regime(regime), unified, _make_sentiment(),
            )
        self.assertEqual(len(results), 1)
        return node, results[0]


_STEP3 = {"GOT_WINRATE_STEP3_ENABLED": "1"}
_ON = {"GOT_FAMILY_TOPPING_GATE_ENABLED": "1"}


class TestFtsActionGate(_FtsGateHarness):

    async def test_off_zero_diff(self):
        """缺省 ENABLED=0：家族被压制+aggravated 仍 buy_trend，reasoning 无 FTS 痕迹，不落 shadow_fts。"""
        node, res = await self._run(_make_stock(), _intel_fg(), [_fs()],
                                    {**_STEP3, "GOT_FAMILY_TOPPING_GATE_ENABLED": "0"})
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertNotIn("FTS", res.reasoning)
        self.assertEqual(node._fts_shadow, {})

    async def test_shadow_flags_without_changing_action(self):
        """shadow：留痕（reasoning 有 FTS影子）但 action 逐字节不变（仍 buy_trend）。"""
        node, res = await self._run(_make_stock(), _intel_fg(), [_fs()],
                                    {**_STEP3, **_ON, "GOT_FAMILY_TOPPING_MODE": "shadow"})
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertIn("FTS影子", res.reasoning)
        self.assertEqual(node._fts_shadow["mode"], "shadow")
        self.assertEqual(len(node._fts_shadow["flagged_stocks"]), 1)
        self.assertEqual(node._fts_shadow["flagged_stocks"][0]["symbol"], "600513")

    async def test_veto_downgrades_to_watch(self):
        """veto：家族被压制+aggravated+buy_trend → watch，reasoning 记录家族与压制天数。"""
        node, res = await self._run(_make_stock(), _intel_fg(), [_fs(days_suppressed=2)],
                                    {**_STEP3, **_ON, "GOT_FAMILY_TOPPING_MODE": "veto"})
        self.assertEqual(res.action_recommendation, "watch")
        self.assertIn("FTS家族见顶压制", res.reasoning)
        self.assertEqual(node._fts_shadow["flagged_stocks"][0]["action_wouldbe"], "watch")

    async def test_non_aggravated_no_action_downgrade(self):
        """非 aggravated（仅软缩仓）：即便 veto，层B 也不硬降 action（仍 buy_trend）。"""
        node, res = await self._run(_make_stock(), _intel_fg(), [_fs(aggravated=False)],
                                    {**_STEP3, **_ON, "GOT_FAMILY_TOPPING_MODE": "veto"})
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertNotIn("FTS", res.reasoning)
        self.assertEqual(node._fts_action_flags, [])

    async def test_family_not_suppressed_unchanged(self):
        """个股家族不在压制台账 → veto 也不触发。"""
        node, res = await self._run(_make_stock(), _intel_fg(family="医药"), [_fs(name="半导体")],
                                    {**_STEP3, **_ON, "GOT_FAMILY_TOPPING_MODE": "veto"})
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertNotIn("FTS", res.reasoning)

    async def test_regime_independent_bull(self):
        """regime 解耦：Node1=bull（bear 门不触）+ MEG off，仅 FTS 就能把 buy_trend 降 watch。"""
        node, res = await self._run(_make_stock(), _intel_fg(), [_fs()],
                                    {**_STEP3, **_ON, "GOT_FAMILY_TOPPING_MODE": "veto"},
                                    regime="bull")
        self.assertEqual(res.action_recommendation, "watch")
        self.assertIn("FTS家族见顶压制", res.reasoning)


# ────────────────── WI-B 层A：被压制家族 → base 降到 caution 档 ──────────────────
class TestFtsSizing(unittest.TestCase):
    """层A 缩仓：mainline+transition 下 base 0.40→0.12（veto），shadow/off 保持 0.40。

    limit = round(base × regime_m(0.65) × role_m(mainline 1.0) × conf(1.0), 3)
      base=0.40 → 0.26 ；base=0.12 → 0.078
    层A 对所有被压制家族生效（不要求 aggravated）。
    """

    def _limit(self, env, family="医药", suppressed_family="医药", role="mainline",
               classification="actionable", aggravated=False):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        si = SectorIntel(sector_name="创新药", sector_role=role, classification=classification,
                         confidence=1.0, frame_group=family,
                         lifecycle=LifecycleInfo(phase="acceleration"))
        fs = [_fs(name=suppressed_family, aggravated=aggravated)] if suppressed_family else []
        with patch.dict(os.environ, env):
            ra = node._assess_risk(_make_unified(confirmed_sectors=[si], family_suppression=fs), {})
        return ra.sector_exposure_limits.get("创新药"), node

    def test_off_base_unchanged(self):
        lim, _ = self._limit({"GOT_FAMILY_TOPPING_GATE_ENABLED": "0"})
        self.assertAlmostEqual(lim, 0.26, places=3)

    def test_shadow_keeps_base_but_flags(self):
        """shadow：留痕（_fts_sizing_flags）但 base 不缩放 → 与 off 逐字节一致。"""
        lim, node = self._limit({**_ON, "GOT_FAMILY_TOPPING_MODE": "shadow"})
        self.assertAlmostEqual(lim, 0.26, places=3)
        self.assertEqual(len(node._fts_sizing_flags), 1)

    def test_veto_reduces_base(self):
        lim, _ = self._limit({**_ON, "GOT_FAMILY_TOPPING_MODE": "veto"})
        self.assertAlmostEqual(lim, 0.078, places=3)

    def test_non_suppressed_family_keeps_base(self):
        lim, _ = self._limit({**_ON, "GOT_FAMILY_TOPPING_MODE": "veto"}, suppressed_family="半导体")
        self.assertAlmostEqual(lim, 0.26, places=3)

    def test_sizing_subtoggle_off(self):
        """SIZING=0：即便 GATE_ENABLED=1+veto，层A 也不缩放（层A/层B 独立开关）。"""
        lim, _ = self._limit({**_ON, "GOT_FAMILY_TOPPING_MODE": "veto",
                              "GOT_FAMILY_TOPPING_SIZING": "0"})
        self.assertAlmostEqual(lim, 0.26, places=3)


# ────────────────── WI-B 影子留痕落盘 + reasoning.md 渲染 ──────────────────
class TestFtsShadowArtifact(unittest.TestCase):
    """shadow_fts.json 仅 ENABLED 时写；archive 渲染缺产物时返回 []（off 态逐字节不变）。"""

    _FTS = {
        "line": "fts", "executed": True, "mode": "shadow", "sizing": True,
        "suppressed_families": [{"name": "医药", "aggravated": True,
                                 "days_suppressed": 2, "days_healthy": 0}],
        "evaluated": 6,
        "flagged_stocks": [{"symbol": "600513", "name": "联环药业", "sector": "创新药",
                            "family": "医药", "aggravated": True, "mode": "shadow",
                            "days_suppressed": 2, "action_before": "buy_trend",
                            "action_wouldbe": "watch"}],
    }

    def test_save_writes_fts_json(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        node._shadow_t1 = {}
        node._shadow_t1h = {}
        node._fts_shadow = dict(self._FTS)
        node._fts_sizing_flags = [{"sector_name": "创新药", "family": "医药",
                                   "role": "mainline", "base_before": 0.4,
                                   "base_wouldbe": 0.12, "mode": "shadow", "days_suppressed": 2}]
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                node._save_shadow_records()
                p = Path("data/got_daily") / date.today().strftime("%Y%m%d") / "shadow_fts.json"
                self.assertTrue(p.exists())
                rec = json.loads(p.read_text(encoding="utf-8"))
                self.assertEqual(rec["action_hits"], 1)
                self.assertEqual(rec["sizing_hits"], 1)
                self.assertEqual(rec["flagged_stocks"][0]["symbol"], "600513")
                self.assertEqual(rec["flagged_sectors"][0]["sector_name"], "创新药")
            finally:
                os.chdir(old)

    def test_render_present(self):
        from src.got import archive
        fts = dict(self._FTS, mode="veto", flagged_sectors=[])
        lines = archive._render_shadow_fts(fts)
        self.assertTrue(any("FTS 家族见顶持续压制门" in l for l in lines))
        self.assertTrue(any("buy→watch" in l for l in lines))

    def test_render_absent_returns_empty(self):
        from src.got import archive
        self.assertEqual(archive._render_shadow_fts(None), [])
        self.assertEqual(archive._render_shadow_fts({}), [])


if __name__ == "__main__":
    unittest.main()
