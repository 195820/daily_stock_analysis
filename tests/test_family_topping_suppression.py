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

    async def test_off_family_name_null_baseline(self):
        """C-3 off 态基线：ENABLED=0 时 unified_scoring[].family_name 恒 None（逐字节不变）。"""
        _node, res = await self._run(_make_stock(), _intel_fg(family="医药"), [_fs()],
                                     {**_STEP3, "GOT_FAMILY_TOPPING_GATE_ENABLED": "0"})
        self.assertIsNone(res.family_name)

    async def test_on_family_name_present(self):
        """C-3 对照：ENABLED=1 时 family_name 取 frame_group（层B join key 生效）。"""
        _node, res = await self._run(_make_stock(), _intel_fg(family="医药"), [_fs()],
                                     {**_STEP3, **_ON, "GOT_FAMILY_TOPPING_MODE": "shadow"})
        self.assertEqual(res.family_name, "医药")

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


# ══════════════════ FXG 家族级见顶上游排除闸门（commit 2）══════════════════
def _fs_full(name="医药", aggravated=True, days_suppressed=2, days_healthy=0,
            members=("创新药",)):
    """带 members/days_healthy 的 FamilySuppression（FXG 触发/豁免判据用）。"""
    return FamilySuppression(name=name, aggravated=aggravated,
                             days_suppressed=days_suppressed, days_healthy=days_healthy,
                             members=list(members))


def _intel_fxg(lifecycle_phase="acceleration", news_check="neutral",
               capital_check="neutral", family="医药"):
    """sector_intel_map 值（FXG 正向豁免判据读 lifecycle_phase/news_check/capital_check）。"""
    return {"classification": "actionable", "capital_check": capital_check,
            "news_check": news_check, "sentiment_check": "low",
            "lifecycle_phase": lifecycle_phase, "lifecycle_day": 1,
            "momentum_state": "thrust", "rs_mom_decay_2d": 0.01, "frame_group": family}


def _launch_aligned(family="医药"):
    """正向三条件齐备（launch + catalyzed + aligned）→ 应豁免。"""
    return _intel_fxg(lifecycle_phase="launch", news_check="catalyzed",
                      capital_check="aligned", family=family)


# ────────────────── _compute_fxg_exclusions 纯函数 ──────────────────
class TestComputeFxgExclusions(unittest.TestCase):
    def setUp(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        self.node = PortfolioDecisionNode(use_mock=True)

    def _call(self, family_suppression, intel_map, actionable_names=None, env=None):
        uc = _make_unified(family_suppression=family_suppression)
        with patch.dict(os.environ, env or {}):
            return self.node._compute_fxg_exclusions(uc, intel_map, actionable_names)

    def test_trigger_excludes_all_members(self):
        """aggravated + days_suppressed>=2 + days_healthy==0 → 整族 members 入 excluded。"""
        excl, exem, audit = self._call(
            [_fs_full(members=["创新药", "医药商业"])],
            {"创新药": _intel_fxg(), "医药商业": _intel_fxg()}, {"创新药", "医药商业"})
        self.assertEqual(excl, {"创新药", "医药商业"})
        self.assertEqual(exem, set())
        self.assertEqual(len(audit), 2)
        self.assertTrue(all(a["aggravated"] and a["days_suppressed"] == 2 for a in audit))

    def test_days_suppressed_below_n_not_excluded(self):
        """days_suppressed<2（早段）→ 不剔（留给层B 软处理）。"""
        excl, _, _ = self._call([_fs_full(days_suppressed=1)], {"创新药": _intel_fxg()}, {"创新药"})
        self.assertEqual(excl, set())

    def test_days_healthy_nonzero_not_excluded(self):
        """W-1：days_healthy>0（恢复期 latch）→ 不做家族级硬剔。"""
        excl, _, _ = self._call([_fs_full(days_suppressed=3, days_healthy=1)],
                                {"创新药": _intel_fxg()}, {"创新药"})
        self.assertEqual(excl, set())

    def test_non_aggravated_not_excluded(self):
        """非 aggravated（仅软压制）→ 不进 FXG 剔除。"""
        excl, _, _ = self._call([_fs_full(aggravated=False)], {"创新药": _intel_fxg()}, {"创新药"})
        self.assertEqual(excl, set())

    def test_min_days_configurable_to_zero(self):
        """MIN_DAYS=0 → 见顶首日即生效。"""
        excl, _, _ = self._call([_fs_full(days_suppressed=0)], {"创新药": _intel_fxg()},
                                {"创新药"}, {"GOT_FAMILY_TOPPING_EXCLUDE_MIN_DAYS": "0"})
        self.assertEqual(excl, {"创新药"})

    def test_positive_evidence_exempt(self):
        """正向三条件（launch+catalyzed+aligned）→ 豁免不剔。"""
        excl, exem, audit = self._call([_fs_full(members=["创新药"])],
                                       {"创新药": _launch_aligned()}, {"创新药"})
        self.assertEqual(excl, set())
        self.assertEqual(exem, {"创新药"})
        self.assertTrue(audit[0]["exempted"])

    def test_partial_evidence_not_exempt(self):
        """缺 capital_check=aligned（launch+catalyzed 但资金未 aligned）→ 照剔不豁免。"""
        intel = _intel_fxg(lifecycle_phase="launch", news_check="catalyzed", capital_check="neutral")
        excl, exem, _ = self._call([_fs_full(members=["创新药"])], {"创新药": intel}, {"创新药"})
        self.assertEqual(excl, {"创新药"})
        self.assertEqual(exem, set())

    def test_glue_mispinned_healthy_sector_exempt(self):
        """glue 误钉健康板块走同一豁免通道（launch+catalyzed+aligned）。"""
        excl, exem, _ = self._call([_fs_full(members=["误钉板块"])],
                                   {"误钉板块": _launch_aligned()}, {"误钉板块"})
        self.assertEqual(excl, set())
        self.assertEqual(exem, {"误钉板块"})

    def test_mixed_members_partial_exempt(self):
        """同族一板块豁免、一板块剔（成员级粒度）。"""
        excl, exem, _ = self._call([_fs_full(members=["创新药", "医药商业"])],
                                   {"创新药": _launch_aligned(), "医药商业": _intel_fxg()},
                                   {"创新药", "医药商业"})
        self.assertEqual(excl, {"医药商业"})
        self.assertEqual(exem, {"创新药"})

    def test_missing_intel_not_exempt(self):
        """members 板块不在 sector_intel_map（C-1 上提后可取到豁免字段；缺则不豁免）→ 照剔。"""
        excl, exem, _ = self._call([_fs_full(members=["创新药"])], {}, {"创新药"})
        self.assertEqual(excl, {"创新药"})
        self.assertEqual(exem, set())

    def test_s1_namespace_mismatch_warns_but_excludes(self):
        """S-1：members 板块不在 actionable_names → 记 warning，但仍入 excluded。"""
        with self.assertLogs("src.got.nodes.portfolio_decision", level="WARNING") as cm:
            excl, _, _ = self._call([_fs_full(members=["创新药"])],
                                    {"创新药": _intel_fxg()}, {"半导体"})
        self.assertEqual(excl, {"创新药"})
        self.assertTrue(any("命名空间错配" in m for m in cm.output))

    def test_exception_degrades_to_empty(self):
        """任何异常（days_suppressed 非数值）→ 降级空集，绝不阻塞 Phase2。"""
        bad = types.SimpleNamespace(aggravated=True, days_suppressed="abc",
                                    days_healthy=0, name="医药", members=["创新药"])
        bad_uc = types.SimpleNamespace(family_suppression=[bad])
        excl, exem, audit = self.node._compute_fxg_exclusions(
            bad_uc, {"创新药": _intel_fxg()}, {"创新药"})
        self.assertEqual((excl, exem, audit), (set(), set(), []))


# ────────────────── FXG Phase2：actionable 补入后剔除 + ambush 旁路守卫 ──────────────────
class _FxgPhase2Harness(unittest.IsolatedAsyncioTestCase):
    """固定场景：家族 医药（aggravated, days_suppressed=2, members=[创新药]）经 ambush_pool
    补入 actionable；半导体为存活候选。观测 captured['ambush']（旁路终值）+ node._fxg_shadow。"""

    async def _run(self, env):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        from src.got.schemas import SectorIntel, AmbushCandidate, LifecycleInfo

        node = PortfolioDecisionNode(use_mock=True)
        intel_map = {"创新药": _intel_fxg(), "半导体": _intel_fxg(family="半导体")}
        node._build_sector_intel_map = lambda uc: intel_map
        node._adjust_score_by_sector_intel = lambda base, name, mm: base
        node._compute_overheat_penalty = lambda name, mm, uc: 1.0
        node._collect_shadow_t1 = lambda finalists: None
        node._save_candidate_pool_snapshot = lambda *a, **k: None

        async def _fake_verify(finalists, sim):
            return finalists
        node._verify_candidate_sectors = _fake_verify

        stock = _make_stock(code="600000", name="浦发银行", sector_name="半导体")
        captured = {}

        def _fake_extract(got_context):
            sc = got_context["sector_context"]
            captured["ambush"] = sorted(s.get("name") for s in sc["ambush"])
            return [{"code": stock.code}]

        screener_mock = MagicMock()
        screener_mock._extract_got_universe = _fake_extract
        screener_mock._filter_got_finalists = lambda uni, ctx, n: [stock]

        confirmed = [
            SectorIntel(sector_name="创新药", classification="actionable",
                        confidence=1.0, frame_group="医药", lifecycle=LifecycleInfo(phase="mature")),
            SectorIntel(sector_name="半导体", classification="actionable",
                        confidence=1.0, frame_group="半导体", lifecycle=LifecycleInfo(phase="acceleration")),
        ]
        unified = UnifiedContext(status="normal", unified_signal="neutral", overall_confidence=0.5,
                                 final_position_limit=0.5, confirmed_sectors=confirmed,
                                 ambush_pool=[AmbushCandidate(sector_name="创新药", catalyst_score=1.0,
                                                              category_tag="drift")],
                                 family_suppression=[_fs_full(members=["创新药"])])
        with patch("src.services.stock_screener.StockScreenerPipeline", return_value=screener_mock), \
             patch.dict(os.environ, env):
            results = await node._score_candidates(
                _make_sector_ctx(), _make_capital(), _make_news(),
                _market_with_regime("transition"), unified, _make_sentiment(),
            )
        return node, captured, results


class TestFxgPhase2Gate(_FxgPhase2Harness):

    async def test_parent_off_no_shadow_no_bypass_guard(self):
        """ENABLED=0：即便 EXCLUDE_ENABLED=1 也不进 FXG → _fxg_shadow 空、旁路不守卫（逐字节不变）。"""
        node, cap, _ = await self._run({**_STEP3, "GOT_FAMILY_TOPPING_GATE_ENABLED": "0",
                                        "GOT_FAMILY_TOPPING_EXCLUDE_ENABLED": "1"})
        self.assertEqual(node._fxg_shadow, {})
        self.assertIn("创新药", cap["ambush"])

    async def test_exclude_disabled_no_fxg_subblock(self):
        """S-2：EXCLUDE_ENABLED=0 → _fxg_shadow 空（不产 fxg 子块）。"""
        node, cap, _ = await self._run({**_STEP3, **_ON, "GOT_FAMILY_TOPPING_EXCLUDE_ENABLED": "0"})
        self.assertEqual(node._fxg_shadow, {})
        self.assertIn("创新药", cap["ambush"])

    async def test_shadow_records_but_keeps_pool(self):
        """shadow：_fxg_shadow 记本应剔，但池不变（旁路仍补入被剔板块）。"""
        node, cap, _ = await self._run({**_STEP3, **_ON, "GOT_FAMILY_TOPPING_EXCLUDE_ENABLED": "1",
                                        "GOT_FAMILY_TOPPING_EXCLUDE_MODE": "shadow"})
        self.assertEqual(node._fxg_shadow["mode"], "shadow")
        self.assertEqual({e["sector"] for e in node._fxg_shadow["excluded_sectors"]}, {"创新药"})
        self.assertIn("创新药", cap["ambush"])

    async def test_veto_subtracts_and_blocks_bypass(self):
        """C-2：veto → 减法在 ambush 补入之后 + 旁路守卫拦住 → 被剔板块不得回流选股池。"""
        node, cap, _ = await self._run({**_STEP3, **_ON, "GOT_FAMILY_TOPPING_EXCLUDE_ENABLED": "1",
                                        "GOT_FAMILY_TOPPING_EXCLUDE_MODE": "veto"})
        self.assertEqual(node._fxg_shadow["mode"], "veto")
        self.assertNotIn("创新药", cap["ambush"])
        self.assertEqual({e["sector"] for e in node._fxg_shadow["excluded_sectors"]}, {"创新药"})


# ────────────────── FXG 影子台账落盘 + 候选池快照 + archive 渲染 ──────────────────
_FTS_RECORD = {
    "line": "fts", "executed": True, "mode": "shadow", "sizing": True,
    "suppressed_families": [{"name": "医药", "aggravated": True,
                             "days_suppressed": 2, "days_healthy": 0}],
    "evaluated": 3, "flagged_stocks": [],
}
_FXG_RECORD = {
    "line": "fxg", "executed": True, "mode": "veto", "min_days": 2,
    "excluded_sectors": [{"sector": "创新药", "family": "医药", "exempted": False,
                          "days_suppressed": 2, "aggravated": True}],
    "exempted_sectors": [{"sector": "医药商业", "family": "医药", "exempted": True,
                          "reason": "launch+catalyzed+aligned"}],
    "evaluated": 3,
}


class TestFxgShadowArtifact(unittest.TestCase):
    def _write_and_read(self, fxg_shadow):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        node._shadow_t1 = {}
        node._shadow_t1h = {}
        node._meg_shadow = {}
        node._fts_shadow = dict(_FTS_RECORD)
        node._fxg_shadow = dict(fxg_shadow) if fxg_shadow else {}
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                node._save_shadow_records()
                p = Path("data/got_daily") / date.today().strftime("%Y%m%d") / "shadow_fts.json"
                tmp_left = list(p.parent.glob("*.tmp")) if p.parent.exists() else []
                rec = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
            finally:
                os.chdir(old)
        return rec, tmp_left

    def test_fxg_subblock_merged(self):
        """EXCLUDE_ENABLED=1（_fxg_shadow 非空）→ shadow_fts.json 带 fxg 子块。"""
        rec, tmp_left = self._write_and_read(_FXG_RECORD)
        self.assertEqual(rec["fxg"]["mode"], "veto")
        self.assertEqual(rec["fxg"]["excluded_sectors"][0]["sector"], "创新药")
        self.assertEqual(rec["fxg"]["exempted_sectors"][0]["reason"], "launch+catalyzed+aligned")
        self.assertEqual(tmp_left, [])  # 原子写不留 .tmp

    def test_no_fxg_subblock_when_off(self):
        """S-2：_fxg_shadow 空 → shadow_fts.json 不含 fxg 键（off 态逐字节不变）。"""
        rec, _ = self._write_and_read({})
        self.assertNotIn("fxg", rec)

    def test_candidate_pool_snapshot(self):
        """F-1：candidate_pool.json 落 universe（含 family/is_finalist）。

        输入必须复刻生产契约（F-1 教训：传错形态的测试=防线空转）：
        universe = List[Dict]（_extract_got_universe 返回，板块键 sector），
        finalists = List[ScreenerStock]（_filter_got_finalists 返回）。
        """
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        finalist = _make_stock(code="600513", name="联环药业", sector_name="创新药")
        universe_dict = {"code": "600513", "name": "联环药业", "sector": "创新药",
                         "sector_type": "active_mainline", "sector_score": 90}
        other_dict = {"code": "000001", "name": "平安银行", "sector": "银行",
                      "sector_type": "ambush", "sector_score": 60}
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                node._save_candidate_pool_snapshot(
                    [universe_dict, other_dict], [finalist],
                    {"创新药": _intel_fxg(), "银行": _intel_fxg(family="金融")})
                p = Path("data/got_daily") / date.today().strftime("%Y%m%d") / "candidate_pool.json"
                rec = json.loads(p.read_text(encoding="utf-8"))
            finally:
                os.chdir(old)
        self.assertEqual(rec["line"], "candidate_pool")
        self.assertEqual(rec["universe_size"], 2)
        self.assertEqual(rec["finalist_size"], 1)
        row0, row1 = rec["universe"]
        self.assertEqual(row0["symbol"], "600513")
        self.assertEqual(row0["name"], "联环药业")
        self.assertEqual(row0["sector_name"], "创新药")
        self.assertEqual(row0["family"], "医药")
        self.assertEqual(row0["sector_score"], 90)
        self.assertTrue(row0["is_finalist"])
        # 非 finalist 行：真实 code 不在 finalist 集合（F-1 缺陷时恒 False）
        self.assertEqual(row1["symbol"], "000001")
        self.assertEqual(row1["sector_name"], "银行")
        self.assertFalse(row1["is_finalist"])

    def test_render_fxg_section(self):
        """archive：fts 带 fxg 子块 → reasoning 出 FXG 段（剔/豁免板块）。"""
        from src.got import archive
        fts = dict(_FTS_RECORD, fxg=dict(_FXG_RECORD))
        lines = archive._render_shadow_fts(fts)
        self.assertTrue(any("FXG 家族级上游排除" in l for l in lines))
        self.assertTrue(any("剔除板块" in l and "创新药" in l for l in lines))
        self.assertTrue(any("豁免板块" in l and "医药商业" in l for l in lines))

    def test_render_no_fxg_section_when_absent(self):
        """archive：无 fxg 子块 → reasoning 不含 FXG 段（off 态逐字节不变）。"""
        from src.got import archive
        lines = archive._render_shadow_fts(dict(_FTS_RECORD))
        self.assertFalse(any("FXG" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
