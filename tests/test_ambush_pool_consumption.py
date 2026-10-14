# -*- coding: utf-8 -*-
"""
埋伏池消费链回归测试（0827 半导体案例）。

背景：v3.3.1 引入蓄水池升格机制后，升格板块可能已移出 Node 2 的
sector.ambush 名单，但下游消费侧（mini GOT 候选 / Phase 2 actionable /
universe 提取）未同步，导致正式池板块在选股中不可见——0827 半导体
是首个真实案例（正式池有、confirmed_sectors 无、Phase 2 无票）。

覆盖：
1. _build_sector_intel 补入池外板块 → SectorIntel 存在且分类为 actionable
2. 池外板块命中 Node 2 near_miss detail → 复用真实技术分（非默认 50）
3. 池外板块无任何 Node 2 数据 → 仍能产出 intel（降级不崩溃）
4. 池中板块已在 sector.ambush 时不重复入候选
5. _resolve_pool_sector_detail 回退链：near_miss → strength_lookup 合成 → None
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _make_capital():
    from src.got.schemas import CapitalContext, CapitalEvidence, DataQuality
    return CapitalContext(
        status="normal",
        institutional_attitude="neutral",
        institutional_score=0.0,
        evidence=CapitalEvidence(),
        reasoning=None,
        data_quality=DataQuality(),
    )


def _make_sentiment():
    from src.got.schemas import SentimentContext, DataQuality
    return SentimentContext(
        overall_sentiment="neutral",
        sentiment_score=0.0,
        sentiment_stage="neutral",
        trading_signal="neutral",
        signal_confidence=0.5,
        data_quality=DataQuality(),
        stage_state=None,
        persistence_regime=None,
    )


def _make_news():
    from src.got.schemas import NewsContext, DataQuality
    return NewsContext(
        overall_impact="neutral",
        major_events=[],
        concept_suggestions=[],
        impact_score=0.0,
        reasoning=None,
        data_quality=DataQuality(),
    )


def _make_detail(name, score, state="neutral", code=""):
    from src.got.schemas import SectorDetail
    return SectorDetail(code=code, name=name, state=state, strength_score=score)


def _make_pool_candidate(name="半导体", score=0.46):
    from src.got.schemas import AmbushCandidate
    return AmbushCandidate(
        sector_name=name,
        sector_code="BK0001",
        catalyst_score=score,
        category_tag="drift",
        events=["英伟达财报超预期"],
        gate_notes="蓄水池升格(medium×0.8)",
        accum_strength="medium",
    )


def _make_sector_ctx(ambush=None, near_miss=None, strength_lookup=None):
    from src.got.schemas import SectorContext
    return SectorContext(
        active_mainline=[],
        ambush=ambush or [],
        near_miss_ambush=near_miss or [],
        strength_lookup=strength_lookup or {},
    )


class TestAmbushPoolConsumption(unittest.TestCase):
    """埋伏正式池板块（含蓄水池升格）必须进入 mini GOT 候选并获得 actionable 分类。"""

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)

    def test_promoted_sector_outside_node2_list_gets_actionable_intel(self):
        """0827 场景复刻：半导体在池中但不在 sector.ambush（仅 near_miss）。"""
        sector = _make_sector_ctx(
            near_miss=[_make_detail("半导体", 49.9)],
            strength_lookup={"半导体": 49.9},
        )
        pool = [_make_pool_candidate("半导体")]

        intels = self.node._build_sector_intel(
            sector, _make_capital(), _make_news(), _make_sentiment(),
            ambush_pool=pool, ambush_reservoir=[],
        )
        # run() 中的独立步骤：埋伏通道结果回写 SectorIntel
        self.node._enrich_ambush_sector_intel(intels, pool, [])

        by_name = {i.sector_name: i for i in intels}
        self.assertIn("半导体", by_name, "升格板块必须补入 mini GOT 候选")
        intel = by_name["半导体"]
        self.assertEqual(intel.sector_role, "ambush")
        self.assertEqual(intel.classification, "actionable",
                         "入池板块必须映射为 actionable（选股通行证）")
        self.assertEqual(intel.tech_score, 49.9, "应复用 near_miss detail 的真实强度分")
        self.assertEqual(intel.ambush_catalyst_score, 0.46,
                         "_enrich_ambush_sector_intel 应回写催化剂分")

    def test_promoted_sector_without_any_node2_data_still_survives(self):
        """既不在任何 Node 2 列表、也不在 strength_lookup → 降级但不崩溃。"""
        sector = _make_sector_ctx()
        pool = [_make_pool_candidate("神秘板块")]

        intels = self.node._build_sector_intel(
            sector, _make_capital(), _make_news(), _make_sentiment(),
            ambush_pool=pool, ambush_reservoir=[],
        )

        by_name = {i.sector_name: i for i in intels}
        self.assertIn("神秘板块", by_name)
        self.assertEqual(by_name["神秘板块"].classification, "actionable")
        self.assertEqual(by_name["神秘板块"].tech_score, 50.0, "无数据走默认技术分")

    def test_pool_sector_already_in_ambush_list_not_duplicated(self):
        """直通路径：板块同时在 sector.ambush 和池中 → 只产出一条 intel。"""
        sector = _make_sector_ctx(ambush=[_make_detail("白酒", 55.0, state="ambush")])
        pool = [_make_pool_candidate("白酒", 0.5)]

        intels = self.node._build_sector_intel(
            sector, _make_capital(), _make_news(), _make_sentiment(),
            ambush_pool=pool, ambush_reservoir=[],
        )

        baijiu = [i for i in intels if i.sector_name == "白酒"]
        self.assertEqual(len(baijiu), 1)
        self.assertEqual(baijiu[0].classification, "actionable")


class TestPhase2UniverseInjection(unittest.IsolatedAsyncioTestCase):
    """Phase 2 _score_candidates 必须把池中非 Node 2 名单板块注入选股 universe。"""

    async def test_promoted_sector_injected_into_universe_source(self):
        """0827 场景：半导体不在 confirmed 也不在 Node 2 ambush → 注入后可被 _extract_got_universe 看到。"""
        from unittest.mock import MagicMock, patch
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        from src.got.schemas import UnifiedContext

        sector = _make_sector_ctx(
            near_miss=[_make_detail("半导体", 49.9)],
            strength_lookup={"半导体": 49.9},
        )
        pool = [_make_pool_candidate("半导体")]
        unified = UnifiedContext(
            status="normal",
            unified_signal="neutral",
            overall_confidence=0.5,
            final_position_limit=0.5,
            confirmed_sectors=[],  # 复刻 0827：confirmed 中查无此板块
            ambush_pool=pool,
        )
        market = MagicMock()
        market.model_dump.return_value = {}

        captured = {}

        def fake_extract(got_context):
            captured["got_context"] = got_context
            return []

        screener_mock = MagicMock()
        screener_mock._extract_got_universe.side_effect = fake_extract
        screener_mock._filter_got_finalists.return_value = []

        node = PortfolioDecisionNode(use_mock=True)
        with patch(
            "src.services.stock_screener.StockScreenerPipeline",
            return_value=screener_mock,
        ):
            results = await node._score_candidates(
                sector, _make_capital(), _make_news(), market,
                unified, _make_sentiment(),
            )

        self.assertEqual(results, [])  # universe 为空 → 提前返回，不影响捕获验证
        sector_ctx = captured["got_context"]["sector_context"]
        ambush_entries = sector_ctx["ambush"]
        names = [s.get("name") for s in ambush_entries]
        self.assertIn("半导体", names, "升格板块必须注入 universe 源列表")
        entry = next(s for s in ambush_entries if s.get("name") == "半导体")
        self.assertEqual(entry["strength_score"], 49.9,
                         "注入项应带 strength_lookup 查表强度")

    async def test_node2_list_sector_not_double_injected(self):
        """直通路径：板块已在 Node 2 ambush 名单且在池中 → 不重复注入。"""
        from unittest.mock import MagicMock, patch
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        from src.got.schemas import UnifiedContext, SectorIntel

        sector = _make_sector_ctx(
            ambush=[_make_detail("白酒", 55.0, state="ambush")],
            strength_lookup={"白酒": 55.0},
        )
        pool = [_make_pool_candidate("白酒", 0.5)]
        intel = SectorIntel(
            sector_name="白酒",
            sector_code="",
            sector_type="concept",
            sector_role="ambush",
            tech_score=55.0,
            tech_detail="",
            capital_check="neutral",
            capital_detail="",
            news_check="neutral",
            news_detail="",
            sentiment_check="low",
            sentiment_detail="",
            composite_score=55.0,
            confidence=0.5,
            classification="actionable",
            reasoning="",
        )
        unified = UnifiedContext(
            status="normal",
            unified_signal="neutral",
            overall_confidence=0.5,
            final_position_limit=0.5,
            confirmed_sectors=[intel],
            ambush_pool=pool,
        )
        market = MagicMock()
        market.model_dump.return_value = {}

        captured = {}

        def fake_extract(got_context):
            captured["got_context"] = got_context
            return []

        screener_mock = MagicMock()
        screener_mock._extract_got_universe.side_effect = fake_extract
        screener_mock._filter_got_finalists.return_value = []

        node = PortfolioDecisionNode(use_mock=True)
        with patch(
            "src.services.stock_screener.StockScreenerPipeline",
            return_value=screener_mock,
        ):
            await node._score_candidates(
                sector, _make_capital(), _make_news(), market,
                unified, _make_sentiment(),
            )

        ambush_entries = captured["got_context"]["sector_context"]["ambush"]
        baijiu = [s for s in ambush_entries if s.get("name") == "白酒"]
        self.assertEqual(len(baijiu), 1)


class TestResolvePoolSectorDetail(unittest.TestCase):
    """_resolve_pool_sector_detail 回退链。"""

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.resolve = AggregatorNode._resolve_pool_sector_detail

    def test_near_miss_detail_preferred(self):
        sector = _make_sector_ctx(
            near_miss=[_make_detail("半导体", 49.9, code="BK0001")],
            strength_lookup={"半导体": 49.9},
        )
        detail = self.resolve(sector, _make_pool_candidate("半导体"))
        self.assertIsNotNone(detail)
        self.assertEqual(detail.strength_score, 49.9)

    def test_fallback_to_strength_lookup_synthesis(self):
        sector = _make_sector_ctx(strength_lookup={"半导体": 49.9})
        detail = self.resolve(sector, _make_pool_candidate("半导体"))
        self.assertIsNotNone(detail)
        self.assertEqual(detail.state, "ambush")
        self.assertEqual(detail.strength_score, 49.9)
        self.assertEqual(detail.code, "BK0001", "合成 detail 应带池候选的板块代码")

    def test_returns_none_when_no_data(self):
        sector = _make_sector_ctx()
        self.assertIsNone(self.resolve(sector, _make_pool_candidate("半导体")))


if __name__ == "__main__":
    unittest.main()
