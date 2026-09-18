# -*- coding: utf-8 -*-
"""GOT 选股胜率优化 4 步（win_rate_optimization_plan.md）回归测试。

覆盖：
- Step 1: Node 6 `_compute_market_regime` 消费 attack_power 双档熔断
- Step 2: `_lifecycle_priority_score` acceleration 按 day 衰减
- Step 3: `_score_candidates` 资金确认硬闸门（capital < 35 不得 buy_trend）
- Step 4: `_score_candidates` 过热降权半幅传导到 composite

每条断言均带反证：关闭对应 env 开关后行为回到旧逻辑。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ─────────────────────────── Step 1 输入构造 ───────────────────────────

def _regime_inputs(
    attack_power,
    tech_score=-0.4,
    adx=20.0,
    market_direction="普涨",
    institutional=0.9,
    news_impact=0.0,
    fear_greed=0.5,
    sentiment_stage="neutral",
    sentiment_momentum=0.0,
):
    """构造 _compute_market_regime 的 5 个 context（MagicMock，仅设被读字段）。

    默认参数下（attack_power 除外）regime_score 计算为：
      无趋势(adx<=25): tech_score*0.2 = -0.08
      attack 偏移:      (attack_power-0.5)*0.2
      普涨:            +0.3
      资金面:           institutional*0.2 = +0.18
      其余维度:         0
    """
    market = MagicMock()
    market.tech_growth.regime_metadata.adx_value = adx
    market.tech_growth.score = tech_score
    market.tech_growth.cross_index_consistency = 0.5
    if attack_power is None:
        market.market_state = None
    else:
        market.market_state.attack_power = attack_power

    sector = MagicMock()
    sector.market_direction = market_direction
    sector.active_mainline = []

    capital = MagicMock()
    capital.institutional_score = institutional
    capital.confidence_multiplier = 1.0

    news = MagicMock()
    news.impact_score = news_impact

    sentiment = MagicMock()
    sentiment.fear_greed_index = fear_greed
    sentiment.sentiment_stage = sentiment_stage
    sentiment.sentiment_momentum = sentiment_momentum

    return market, sector, capital, news, sentiment


class TestStep1AttackPowerCircuitBreaker(unittest.TestCase):
    """Step 1：attack_power 双档熔断（`_compute_market_regime`）。"""

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.compute = AggregatorNode._compute_market_regime

    def test_band2_breaks_bull_to_transition(self):
        """(a) attack_power=0.17 → 档 2 把 bull 压回 transition。"""
        inputs = _regime_inputs(attack_power=0.17)
        with patch.dict(os.environ, {"GOT_WINRATE_STEP1_ENABLED": "1"}):
            regime = self.compute(*inputs)
        self.assertEqual(regime.regime, "transition")

    def test_band2_reverse_without_switch(self):
        """反证：关闭 Step 1 时同输入应判 bull（0.334 > 0.3）。"""
        inputs = _regime_inputs(attack_power=0.17)
        with patch.dict(os.environ, {"GOT_WINRATE_STEP1_ENABLED": "0"}):
            regime = self.compute(*inputs)
        self.assertEqual(regime.regime, "bull")

    def test_band1_breaks_to_bear(self):
        """(b) attack_power=0.10（crash 区间）→ 档 1 强制 bear。"""
        inputs = _regime_inputs(attack_power=0.10)
        with patch.dict(os.environ, {"GOT_WINRATE_STEP1_ENABLED": "1"}):
            regime = self.compute(*inputs)
        self.assertEqual(regime.regime, "bear")

    def test_band1_reverse_without_switch(self):
        """反证：关闭 Step 1 时 attack_power=0.10 只能到 bull，无法到 bear。"""
        inputs = _regime_inputs(attack_power=0.10)
        with patch.dict(os.environ, {"GOT_WINRATE_STEP1_ENABLED": "0"}):
            regime = self.compute(*inputs)
        self.assertEqual(regime.regime, "bull")

    def test_none_attack_power_no_breaker(self):
        """(c) market_state 缺失（attack_power=None）→ 不熔断，与旧逻辑一致（bull）。"""
        inputs = _regime_inputs(attack_power=None)
        with patch.dict(os.environ, {"GOT_WINRATE_STEP1_ENABLED": "1"}):
            regime = self.compute(*inputs)
        # 无 attack 偏移、无熔断：-0.08 + 0.3 + 0.18 = 0.40 → bull
        self.assertEqual(regime.regime, "bull")


# ─────────────────────────── Step 2 ───────────────────────────

class TestStep2AccelerationDayDecay(unittest.TestCase):
    """Step 2：acceleration 生命周期按 day 衰减（`_lifecycle_priority_score`）。"""

    def setUp(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        self.node = PortfolioDecisionNode(use_mock=True)

    def _priority(self, day, env):
        # composite=0.0 + sentiment_momentum=0.0 → final = 0.6 * priority
        with patch.dict(os.environ, env):
            final = self.node._lifecycle_priority_score("acceleration", day, 0.0, 0.0)
        return final / 0.6

    def test_day_early(self):
        self.assertEqual(self._priority(1, {"GOT_WINRATE_STEP2_ENABLED": "1"}), 75)

    def test_day_mid(self):
        self.assertEqual(self._priority(5, {"GOT_WINRATE_STEP2_ENABLED": "1"}), 65)

    def test_day_late(self):
        self.assertEqual(self._priority(14, {"GOT_WINRATE_STEP2_ENABLED": "1"}), 50)

    def test_reverse_disabled_keeps_70(self):
        """反证：关闭 Step 2 时 day 14 仍得旧固定值 70。"""
        self.assertEqual(self._priority(14, {"GOT_WINRATE_STEP2_ENABLED": "0"}), 70)

    def test_day_none_keeps_70(self):
        """day 未知（None）→ 回退固定 70（即使开关开启）。"""
        self.assertEqual(self._priority(None, {"GOT_WINRATE_STEP2_ENABLED": "1"}), 70)


# ─────────────────────── Step 3 / Step 4 公共装置 ───────────────────────

def _make_capital():
    from src.got.schemas import CapitalContext, CapitalEvidence, DataQuality
    return CapitalContext(
        status="normal",
        institutional_attitude="neutral",
        institutional_score=0.0,
        confidence_multiplier=1.0,
        evidence=CapitalEvidence(),
        reasoning=None,
        data_quality=DataQuality(),
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


def _make_sector_ctx():
    from src.got.schemas import SectorContext
    return SectorContext(
        active_mainline=[],
        ambush=[],
        near_miss_ambush=[],
        strength_lookup={},
    )


def _make_unified():
    from src.got.schemas import UnifiedContext
    return UnifiedContext(
        status="normal",
        unified_signal="neutral",
        overall_confidence=0.5,
        final_position_limit=0.5,
        confirmed_sectors=[],
        ambush_pool=[],
    )


def _make_stock(code="300001", name="测试股", sector_name="半导体", **kw):
    from src.services.stock_screener import ScreenerStock
    defaults = dict(
        sector_score=50.0,
        capital_score=50.0,
        capital_available=True,
        news_score=50.0,
        news_available=True,
        tech_wave_score=50.0,
        risk_score_safe=50.0,
    )
    defaults.update(kw)
    return ScreenerStock(code=code, name=name, sector_name=sector_name, **defaults)


def _intel(lifecycle_phase="launch", lifecycle_day=1):
    return {
        "classification": "actionable",
        "capital_check": "neutral",
        "news_check": "neutral",
        "sentiment_check": "low",
        "lifecycle_phase": lifecycle_phase,
        "lifecycle_day": lifecycle_day,
    }


class _ScoreCandidatesHarness(unittest.IsolatedAsyncioTestCase):
    """驱动 `_score_candidates` 循环体：mock 掉数据抓取与板块情报辅助方法。"""

    async def _run(self, stock, intel, overheat, env):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode(use_mock=True)
        node._build_sector_intel_map = lambda uc: {stock.sector_name: intel}
        node._adjust_score_by_sector_intel = lambda base, name, m: base
        node._compute_overheat_penalty = lambda name, m, uc: overheat
        node._collect_shadow_t1 = lambda finalists: None

        async def _fake_verify(finalists, sector_intel_map):
            return finalists

        node._verify_candidate_sectors = _fake_verify

        screener_mock = MagicMock()
        screener_mock._extract_got_universe.return_value = [{"code": stock.code}]
        screener_mock._filter_got_finalists.return_value = [stock]

        market = MagicMock()
        market.model_dump.return_value = {}

        with patch(
            "src.services.stock_screener.StockScreenerPipeline",
            return_value=screener_mock,
        ), patch.dict(os.environ, env):
            results = await node._score_candidates(
                _make_sector_ctx(), _make_capital(), _make_news(), market,
                _make_unified(), _make_sentiment(),
            )
        self.assertEqual(len(results), 1)
        return results[0]


class TestStep3CapitalGate(_ScoreCandidatesHarness):
    """Step 3：资金确认硬闸门（capital < 35 不得 buy_trend）。"""

    async def test_low_capital_downgraded_to_watch(self):
        stock = _make_stock(capital_score=20.0, capital_available=True)
        res = await self._run(stock, _intel("launch", 1), overheat=1.0,
                              env={"GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "watch")

    async def test_low_capital_reverse_without_switch(self):
        """反证：关闭 Step 3 时同输入应得 buy_trend。"""
        stock = _make_stock(capital_score=20.0, capital_available=True)
        res = await self._run(stock, _intel("launch", 1), overheat=1.0,
                              env={"GOT_WINRATE_STEP3_ENABLED": "0"})
        self.assertEqual(res.action_recommendation, "buy_trend")

    async def test_high_capital_keeps_buy_trend(self):
        stock = _make_stock(capital_score=64.0, capital_available=True)
        res = await self._run(stock, _intel("launch", 1), overheat=1.0,
                              env={"GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "buy_trend")

    async def test_capital_unavailable_not_gated(self):
        """资金不可用（capital_available=False）→ 不触发闸门，仍 buy_trend。

        生产中 ScreenerStock.capital_score 为 float（缺失时默认 0.0），
        “资金缺失”的真实信号是 capital_available=False，走 `not stock.capital_available` 分支。
        """
        stock = _make_stock(capital_score=0.0, capital_available=False)
        res = await self._run(stock, _intel("launch", 1), overheat=1.0,
                              env={"GOT_WINRATE_STEP3_ENABLED": "1"})
        self.assertEqual(res.action_recommendation, "buy_trend")


class TestStep4OverheatComposite(_ScoreCandidatesHarness):
    """Step 4：过热降权半幅传导到 composite。"""

    async def test_composite_half_penalty(self):
        stock = _make_stock()
        on = await self._run(stock, _intel("mature", 5), overheat=0.75,
                             env={"GOT_WINRATE_STEP4_ENABLED": "1"})
        off = await self._run(stock, _intel("mature", 5), overheat=0.75,
                              env={"GOT_WINRATE_STEP4_ENABLED": "0"})
        # 半幅系数 = 0.5 + 0.5 * 0.75 = 0.875
        self.assertLess(on.composite_score, off.composite_score)
        self.assertAlmostEqual(on.composite_score, off.composite_score * 0.875, places=4)

    async def test_no_overheat_no_effect(self):
        """overheat_penalty=1.0 时 Step 4 不改变 composite。"""
        stock = _make_stock()
        on = await self._run(stock, _intel("mature", 5), overheat=1.0,
                             env={"GOT_WINRATE_STEP4_ENABLED": "1"})
        off = await self._run(stock, _intel("mature", 5), overheat=1.0,
                              env={"GOT_WINRATE_STEP4_ENABLED": "0"})
        self.assertAlmostEqual(on.composite_score, off.composite_score, places=6)


if __name__ == "__main__":
    unittest.main()
