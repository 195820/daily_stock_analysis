# -*- coding: utf-8 -*-
"""工作流 B：Node1 熊市收手闸门（GOT_BEAR_ENTRY_GATE）测试（2026-10-01）

覆盖 §7.1：
- bear 日主线 buy_trend/buy_dip → watch（veto）
- ambush 来源豁免：bear 日埋伏股仍保留 buy*
- transition / bull 日行为完全不变（bull 中性回归）
- 开关关闭（GOT_BEAR_ENTRY_GATE=0）零 diff
- F3：market_state 缺失 / regime 空 → 闸门不触发
- dip soft 模式：仅乘性降权、保留 buy_dip
- 命中计数 self._bear_gate_hits

驱动 _score_candidates 循环体，夹具与 test_winrate_optimization 同构。
"""
import asyncio
import json
import os
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.test_fxg_veto_baseline_pair import _FULL, _usr
from src.got.schemas import (
    CapitalContext, CapitalEvidence, DataQuality, NewsContext, SentimentContext,
    SectorContext, UnifiedContext, AmbushCandidate,
)


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


def _make_unified(ambush_pool=None):
    return UnifiedContext(status="normal", unified_signal="neutral", overall_confidence=0.5,
                          final_position_limit=0.5, confirmed_sectors=[],
                          ambush_pool=ambush_pool or [])


def _make_stock(code="300001", name="测试股", sector_name="半导体", **kw):
    from src.services.stock_screener import ScreenerStock
    defaults = dict(sector_score=90.0, capital_score=90.0, capital_available=True,
                    news_score=90.0, news_available=True, tech_wave_score=90.0,
                    risk_score_safe=80.0)
    defaults.update(kw)
    return ScreenerStock(code=code, name=name, sector_name=sector_name, **defaults)


def _intel(lifecycle_phase="launch", lifecycle_day=1):
    return {"classification": "actionable", "capital_check": "neutral",
            "news_check": "neutral", "sentiment_check": "low",
            "lifecycle_phase": lifecycle_phase, "lifecycle_day": lifecycle_day}


def _market_with_regime(regime):
    """构造 market_context：market_state.regime=指定值（None 模拟归档无标签）。"""
    m = MagicMock()
    m.model_dump.return_value = {}
    m.market_state = types.SimpleNamespace(regime=regime) if regime is not None else None
    return m


class _BearGateHarness(unittest.IsolatedAsyncioTestCase):

    async def _run(self, stock, intel, regime, env, ambush_pool=None, phase="launch",
                   lane_shadow=None):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode(use_mock=True)
        node._build_sector_intel_map = lambda uc: {stock.sector_name: intel}
        node._adjust_score_by_sector_intel = lambda base, name, mm: base
        node._compute_overheat_penalty = lambda name, mm, uc: 1.0
        node._collect_shadow_t1 = lambda finalists: None
        # F-1 污染防线：stub universe 是 [{"code": ...}] 残缺形态，GOT_FAMILY_TOPPING_GATE_ENABLED=1
        # （.env 已置 1）时 4098 分支会把 stub 写进 repo cwd data/got_daily/<today>/candidate_pool.json，
        # 覆盖生产归档（1013/1014/1015 三日实锤）。打桩 = test_family_topping_suppression 同款先例。
        node._save_candidate_pool_snapshot = lambda *a, **k: None

        async def _fake_verify(finalists, sector_intel_map):
            return finalists
        node._verify_candidate_sectors = _fake_verify

        screener_mock = MagicMock()
        screener_mock._extract_got_universe.return_value = [{"code": stock.code}]
        screener_mock._filter_got_finalists.return_value = [stock]
        if lane_shadow is not None:
            screener_mock._ambush_lane_shadow = lane_shadow

        with patch("src.services.stock_screener.StockScreenerPipeline", return_value=screener_mock), \
             patch.dict(os.environ, env):
            results = await node._score_candidates(
                _make_sector_ctx(), _make_capital(), _make_news(),
                _market_with_regime(regime), _make_unified(ambush_pool), _make_sentiment(),
            )
        self.assertEqual(len(results), 1)
        return node, results[0]


class TestBearEntryGate(_BearGateHarness):

    async def test_bear_mainline_buy_trend_downgraded(self):
        stock = _make_stock(sector_name="半导体")
        node, res = await self._run(stock, _intel("launch", 1), "bear",
                                    {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "watch")
        self.assertIn("Node1=bear 收手闸门: buy_trend→watch", res.reasoning)
        self.assertEqual(node._bear_gate_hits, 1)

    async def test_bear_ambush_exempt(self):
        """埋伏来源（_bypass_ambush_sectors）在 bear 日仍保留 buy*（§3.3 豁免）。"""
        pool = [AmbushCandidate(sector_name="半导体", category_tag="drift", catalyst_score=1.0)]
        stock = _make_stock(sector_name="半导体")
        node, res = await self._run(stock, _intel("launch", 1), "bear",
                                    {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"},
                                    ambush_pool=pool)
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertEqual(res.pool_source, "bypass_ambush")
        self.assertEqual(node._bear_gate_hits, 0)

    async def test_bear_lane_ambush_exempt_independent_of_active(self):
        """2026-10-13 转正：lane 过闸板块票 bear 日豁免，不依赖 GOT_AMBUSH_SPLIT_ACTIVE。"""
        stock = _make_stock(sector_name="半导体", sector_type="ambush")
        lane_shadow = {"lane_finalists": [{"code": "600999", "sector": "半导体"}]}
        node, res = await self._run(stock, _intel("launch", 1), "bear",
                                    {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1",
                                     "GOT_AMBUSH_SPLIT_ACTIVE": "0"},
                                    lane_shadow=lane_shadow)
        self.assertEqual(res.action_recommendation, "buy_trend")
        self.assertEqual(node._bear_gate_hits, 0)
        self.assertNotIn("收手闸门", res.reasoning)
        self.assertEqual(node._ambush_lane_trace["bear_gate_watch"], [])

    async def test_bear_lane_trace_trend_branch(self):
        """未豁免 ambush 型票 bear 日 buy_trend→watch 记 bear_gate_watch（trend 分支）。"""
        stock = _make_stock(sector_name="半导体", sector_type="ambush")
        node, res = await self._run(stock, _intel("launch", 1), "bear",
                                    {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "watch")
        self.assertEqual(node._bear_gate_hits, 1)
        self.assertEqual(node._ambush_lane_trace["bear_gate_watch"], [stock.code])

    async def test_bear_lane_trace_dip_branch(self):
        """buy_dip veto 分支对称补记 bear_gate_watch（2026-10-13 缺口修复）。"""
        stock = _make_stock(sector_name="半导体", sector_type="ambush")
        node, res = await self._run(stock, _intel("mature", 3), "bear",
                                    {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1",
                                     "GOT_BEAR_DIP_MODE": "veto"}, phase="mature")
        self.assertEqual(res.action_recommendation, "watch")
        self.assertEqual(node._bear_gate_hits, 1)
        self.assertEqual(node._ambush_lane_trace["bear_gate_watch"], [stock.code])

    async def test_transition_unchanged(self):
        stock = _make_stock()
        _, res = await self._run(stock, _intel("launch", 1), "transition",
                                 {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "buy_trend")

    async def test_bull_neutral_unchanged(self):
        """bull 日行为完全不变（§6 bull 中性回归）。"""
        stock = _make_stock()
        _, res = await self._run(stock, _intel("launch", 1), "bull",
                                 {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "buy_trend")

    async def test_gate_off_zero_diff(self):
        stock = _make_stock()
        _, res = await self._run(stock, _intel("launch", 1), "bear",
                                 {"GOT_BEAR_ENTRY_GATE": "0", "GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "buy_trend")

    async def test_no_market_state_not_triggered(self):
        """F3：market_state 缺失 → 闸门不触发（避免无标签日误杀）。"""
        stock = _make_stock()
        _, res = await self._run(stock, _intel("launch", 1), None,
                                 {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "buy_trend")

    async def test_bear_buy_dip_veto(self):
        """bear 日 buy_dip（mature + 高 composite）在 veto 模式降 watch。"""
        stock = _make_stock(sector_name="半导体")
        _, res = await self._run(stock, _intel("mature", 3), "bear",
                                 {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1",
                                  "GOT_BEAR_DIP_MODE": "veto"}, phase="mature")
        self.assertEqual(res.action_recommendation, "watch")
        self.assertIn("buy_dip→watch", res.reasoning)

    async def test_bear_buy_dip_soft_keeps_action_reduces_score(self):
        """soft 模式：保留 buy_dip，但 composite 被乘性降权（对照 veto 严格更松）。"""
        stock = _make_stock(sector_name="半导体")
        _, soft = await self._run(stock, _intel("mature", 3), "bear",
                                  {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1",
                                   "GOT_BEAR_DIP_MODE": "soft", "GOT_BEAR_DIP_SOFT_FACTOR": "0.8"})
        _, clean = await self._run(stock, _intel("mature", 3), "transition",
                                   {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(soft.action_recommendation, "buy_dip")
        self.assertIn("软降权", soft.reasoning)
        self.assertLess(soft.composite_score, clean.composite_score)
        self.assertAlmostEqual(soft.composite_score, round(clean.composite_score * 0.8, 4), places=3)

    # ── backlog §1.H 修法② tombstone（2026-10-14）：每轮无条件组装留痕 dict ──

    async def test_tombstone_assembled_on_zero_hits(self):
        """非 bear 日 0 命中 → 仍组装 dict（hits:0、executed:False），盲日可辨识。"""
        stock = _make_stock()
        node, _ = await self._run(stock, _intel("launch", 1), "transition",
                                  {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        rec = node._bear_gate_shadow
        self.assertIsInstance(rec, dict)
        self.assertEqual(rec["hits"], 0)
        self.assertFalse(rec["executed"])
        self.assertEqual(rec["flagged_stocks"], [])
        self.assertEqual(rec["node1_regime"], "transition")

    async def test_tombstone_carries_flagged_on_hits(self):
        """bear 日命中 → dict 含逐单 flagged_stocks 与双 regime 口径字段。"""
        stock = _make_stock()
        node, res = await self._run(stock, _intel("launch", 1), "bear",
                                    {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "watch")
        rec = node._bear_gate_shadow
        self.assertIsInstance(rec, dict)
        self.assertEqual(rec["hits"], 1)
        self.assertTrue(rec["executed"])
        self.assertEqual(rec["node1_regime"], "bear")
        self.assertEqual(rec["flagged_stocks"][0]["symbol"], stock.code)
        self.assertEqual(rec["flagged_stocks"][0]["action_after"], "watch")

    async def test_tombstone_regime_fields_distinct(self):
        """node1_regime 记触发口径（raw），regime 记 Node6 unified 展示口径——两字段并存不混用。"""
        stock = _make_stock()
        node, _ = await self._run(stock, _intel("launch", 1), "bear",
                                  {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        rec = node._bear_gate_shadow
        self.assertEqual(rec["node1_regime"], "bear")       # 触发口径
        self.assertIsNone(rec["regime"])                     # 夹具 UnifiedContext 无 market_regime

    async def test_tombstone_overwrites_stale_on_save(self):
        """0 命中日落盘 tombstone 覆盖旧命中文件，消除 stale 归因歧义。"""
        import json
        import tempfile
        from pathlib import Path
        from datetime import date

        stock = _make_stock()
        node, _ = await self._run(stock, _intel("launch", 1), "transition",
                                  {"GOT_BEAR_ENTRY_GATE": "1", "GOT_WINRATE_STEP3_ENABLED": "1"})
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                day_dir = Path("data/got_daily") / date.today().strftime("%Y%m%d")
                day_dir.mkdir(parents=True, exist_ok=True)
                stale = {"line": "bear_gate", "executed": True, "hits": 9, "flagged_stocks": []}
                (day_dir / "shadow_bear_gate.json").write_text(
                    json.dumps(stale, ensure_ascii=False), encoding="utf-8")
                node._save_shadow_records()
                rec = json.loads(
                    (day_dir / "shadow_bear_gate.json").read_text(encoding="utf-8"))
                self.assertEqual(rec["hits"], 0)
                self.assertFalse(rec["executed"])
            finally:
                os.chdir(old)


class TestFxgBaselineBearGateSubblock(unittest.IsolatedAsyncioTestCase):
    """基线实例不走 run()/_save_shadow_records，其 bear gate 拦单观测随 fxg_baseline_scoring.json
    以 bear_gate 子块留痕（backlog §1.H 基线侧盲区，2026-10-14）。"""

    async def _call(self, score_ret):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        async def _fake_score(self_node, sector_ctx, capital_ctx, news_ctx, market_ctx,
                              unified, sentiment_context=None):
            # 真 _score_candidates 末尾无条件组装 tombstone（见 test_tombstone_*）；桩直接落同形 dict
            self_node._bear_gate_shadow = {"line": "bear_gate", "executed": False,
                                           "node1_regime": None, "regime": None,
                                           "dip_mode": "veto", "hits": 0, "flagged_stocks": []}
            return score_ret

        with patch.object(PortfolioDecisionNode, "_score_candidates", _fake_score), \
             patch.dict(os.environ, dict(_FULL)):
            old = os.getcwd()
            with tempfile.TemporaryDirectory() as d:
                os.chdir(d)
                try:
                    from src.got.orchestrator import GOTOrchestrator
                    await GOTOrchestrator.__new__(GOTOrchestrator)._maybe_capture_fxg_baseline(
                        MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                        MagicMock(), MagicMock(), date(2026, 10, 10))
                    p = Path("data/got_daily/20261010") / "fxg_baseline_scoring.json"
                    rec = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
                finally:
                    os.chdir(old)
        return rec

    async def test_bear_gate_subblock_present(self):
        """基线跑后 _bear_gate_shadow 为 dict（tombstone 组装）→ 子块随产物落盘。"""
        rec = await self._call([_usr()])
        self.assertIsNotNone(rec["bear_gate"])
        self.assertEqual(rec["bear_gate"]["line"], "bear_gate")
        self.assertIn("hits", rec["bear_gate"])

    async def test_bear_gate_subblock_none_when_stubbed(self):
        """桩测未组装（_bear_gate_shadow 保持 list）→ 子块为 None，不炸不造数据。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        async def _fake_score_poke(self_node, *a, **k):
            return [_usr()]

        with patch.object(PortfolioDecisionNode, "_score_candidates", _fake_score_poke), \
             patch.dict(os.environ, dict(_FULL)):
            old = os.getcwd()
            with tempfile.TemporaryDirectory() as d:
                os.chdir(d)
                try:
                    from src.got.orchestrator import GOTOrchestrator
                    await GOTOrchestrator.__new__(GOTOrchestrator)._maybe_capture_fxg_baseline(
                        MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                        MagicMock(), MagicMock(), date(2026, 10, 10))
                    p = Path("data/got_daily/20261010") / "fxg_baseline_scoring.json"
                    rec = json.loads(p.read_text(encoding="utf-8"))
                finally:
                    os.chdir(old)
        # _fake_score_poke 未动 _bear_gate_shadow → __init__ 缺省 [] → 子块 None
        self.assertIsNone(rec["bear_gate"])


class TestAmbushRealScores(_BearGateHarness):
    """P0-4：埋伏票 composite 使用真实 tech/risk；回滚键恢复 rank×100 / 100。"""

    async def test_ambush_real_scores_used_in_composite(self):
        stock = _make_stock(
            sector_name="半导体", sector_type="ambush",
            ambush_rank_score=0.42, tech_wave_score=70.0, risk_score_safe=60.0,
        )
        _, res = await self._run(
            stock, _intel(), None,
            {"GOT_AMBUSH_SPLIT_ACTIVE": "1", "GOT_AMBUSH_REAL_SCORES": "1"},
        )
        self.assertEqual(res.dimension_scores["tech"], 70.0)
        self.assertEqual(res.dimension_scores["risk"], 60.0)

    async def test_ambush_real_scores_rollback(self):
        stock = _make_stock(
            sector_name="半导体", sector_type="ambush",
            ambush_rank_score=0.42, tech_wave_score=70.0, risk_score_safe=60.0,
        )
        _, res = await self._run(
            stock, _intel(), None,
            {"GOT_AMBUSH_SPLIT_ACTIVE": "1", "GOT_AMBUSH_REAL_SCORES": "0"},
        )
        self.assertEqual(res.dimension_scores["tech"], 42.0)
        self.assertEqual(res.dimension_scores["risk"], 100.0)


if __name__ == "__main__":
    unittest.main()
