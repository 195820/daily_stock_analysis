# -*- coding: utf-8 -*-
"""催化剂日历架构修正（2026-10-22）测试

覆盖四块改动（方案 local://node4-ambush-events-plan.md）：
1. Node 4 当日事件进埋伏通道（第 2 步）：准入 / 负面排除 / 日历优先 / 开关回退 / 别名查找
2. 置信度硬闸 → 排序权重（第 3 步）：0.6 档准入、0.58 仍拦、提升路径仍 0.7
3. P6 兑现登记（第 4 步）：Node 4 主催化候选登记 source="node4_event"
4. 跨日发酵原始新闻入 narrative prompt（第 6 步）+ 产业级事件召回（第 7 步，DeepInfer 反例）
"""
import asyncio
import json
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.got.catalyst_calendar import CatalystCalendarBuilder
from src.got.nodes.aggregator import AggregatorNode
from src.got.nodes.news_analysis import NewsAnalysisNode
from src.got.schemas import (
    AmbushCandidate,
    AmbushReservoirEntry,
    CapitalContext,
    CapitalEvidence,
    CatalystCalendar,
    CatalystEvent,
    MarketRegime,
    NewsContext,
    NewsEvent,
    SectorContext,
    SectorDetail,
    SectorIntel,
    SentimentContext,
)
from src.got.catalyst_config import (
    AMBUSH_ACCUM_MULTIPLIER,
    CATALYST_CONFIDENCE_GATE,
    INDUSTRY_RECALL_MAX,
)


# ─────────────────────────────────────────────────────────────
# Fixture 工厂（与 tests/test_ambush_nomination.py 同范式）
# ─────────────────────────────────────────────────────────────

TODAY = date.today()


def _make_capital(flow_map):
    ev = CapitalEvidence(sector_flow={
        "score": 0.1,
        "sector_flow_all": flow_map,
        "sector_flow_all_3d": {},
        "top_concepts": [],
        "top_industries": [],
        "mainline_bonus": False,
        "timestamp": "mock",
    })
    return CapitalContext(
        status="normal", institutional_attitude="neutral", institutional_score=0.0, evidence=ev,
    )


def _make_sector(names, strength=60.0):
    return SectorContext(ambush=[
        SectorDetail(code=f"BK_{i:03d}", name=n, state="ambush", strength_score=strength)
        for i, n in enumerate(names)
    ])


def _make_news(events=None, calendar_events=None, calendar_none=False):
    if calendar_none:
        calendar = None
    else:
        calendar = CatalystCalendar(target_date=TODAY, events=list(calendar_events or []))
    return NewsContext(
        overall_impact="neutral",
        impact_score=0.0,
        major_events=list(events or []),
        catalyst_calendar=calendar,
    )


def _make_sentiment():
    return SentimentContext(
        overall_sentiment="neutral",
        sentiment_score=0.0,
        sentiment_stage="neutral",
        signal_confidence=0.5,
    )


def _make_calendar_event(sector, confidence=0.9, impact=2, days_ahead=7, direction="positive"):
    return CatalystEvent(
        event_id=f"cal_{sector}_{confidence}",
        event_date=TODAY + timedelta(days=days_ahead),
        event_type="narrative",
        title=f"{sector}催化事件",
        sector=sector,
        impact_level=impact,
        direction=direction,
        confidence=confidence,
        source="llm_narrative",
    )


def _make_news_event(sectors, impact="high", sentiment="positive", weight=0.8, title="稀土出口管制落地"):
    return NewsEvent(
        event_type="industry",
        title=title,
        summary="测试事件",
        impact_level=impact,
        sentiment=sentiment,
        affected_sectors=list(sectors),
        catalyst_weight=weight,
    )


class _ChannelBase(unittest.TestCase):
    """隔离归档/文件系统与 LLM：只测埋伏通道本体。"""

    def setUp(self):
        self.node = AggregatorNode.__new__(AggregatorNode)
        self.node._run_date = TODAY
        self.prompts: list[str] = []

    def _run_channel(self, sector, capital, news, sentiment, *, register_p6=True):
        async def fake_llm(prompt, **kwargs):
            self.prompts.append(prompt)
            return "[]"

        with patch("src.got.persistence.load_capital_flow_series", return_value=({}, [])), \
             patch.object(AggregatorNode, "_load_previous_reservoir", return_value={}), \
             patch.object(AggregatorNode, "_load_capital_flow_aliases", return_value={}), \
             patch.object(AggregatorNode, "_load_prev_glue_relations_index", return_value={}), \
             patch.object(self.node, "_call_llm_async", new=fake_llm):
            if register_p6:
                with patch.object(AggregatorNode, "_register_node4_realization") as p6_mock:
                    result = asyncio.run(
                        self.node._build_ambush_channel(sector, capital, news, sentiment)
                    )
                self.p6_mock = p6_mock
                return result
            return asyncio.run(
                self.node._build_ambush_channel(sector, capital, news, sentiment)
            )


# ─────────────────────────────────────────────────────────────
# 第 2 步：Node 4 事件进埋伏通道
# ─────────────────────────────────────────────────────────────

class TestNode4AmbushBranch(_ChannelBase):

    def test_node4_event_enters_ambush_pool(self):
        """日历为空 + Node 4 高影响正面事件 → 板块进正式池（此前只有 no_catalyst 一条路）。"""
        name = "稀土"
        news = _make_news(
            events=[_make_news_event([name])], calendar_none=True,
        )
        pool, reservoir, _ = self._run_channel(
            _make_sector([name]), _make_capital({name: 5.0}), news, _make_sentiment(),
        )
        self.assertEqual([c.sector_name for c in pool], [name])
        self.assertIn("Node4事件(", pool[0].gate_notes)
        self.assertGreater(pool[0].catalyst_score, 0.0)
        self.assertEqual(pool[0].category_tag, "fade")
        self.assertEqual(pool[0].top_event_date, TODAY,
                         "Node 4 事件即当日事件；留 None 会让 P4 fade 分支不可达")
        self.assertEqual(reservoir, [], "有 Node 4 催化时不应进蓄水池")

        # P4 出口可达性：fade 型按「事件日次日卖」退出（此前 top_event_date=None 恒不可达）
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        decision = PortfolioDecisionNode.__new__(PortfolioDecisionNode)
        snapshot = SimpleNamespace(
            entry_date=TODAY,
            catalyst_event_date=pool[0].top_event_date,
            category_tag=pool[0].category_tag,
            entry_price=10.0,
        )
        signal_today, _ = decision._ambush_exit_signal(snapshot, TODAY, 10.5)
        self.assertIsNone(signal_today, "事件日当天不卖")
        signal_next, reason = decision._ambush_exit_signal(
            snapshot, TODAY + timedelta(days=1), 10.5,
        )
        self.assertEqual(signal_next, "fade_sell", reason)

    def test_node4_event_alias_variant_lookup(self):
        """L2 别名变体：事件扇出用「半导体概念」也能命中「半导体」候选。"""
        name = "半导体"
        news = _make_news(
            events=[_make_news_event(["半导体概念"], title="某厂新产线投产")],
            calendar_none=True,
        )
        pool, _, _ = self._run_channel(
            _make_sector([name]), _make_capital({name: 5.0}), news, _make_sentiment(),
        )
        self.assertEqual([c.sector_name for c in pool], [name])

    def test_node4_negative_event_excluded(self):
        """负面情绪事件不进埋伏（G1 语义：利空不构成埋伏理由）。"""
        name = "稀土"
        news = _make_news(
            events=[_make_news_event([name], sentiment="negative", weight=0.4)],
            calendar_none=True,
        )
        pool, reservoir, _ = self._run_channel(
            _make_sector([name]), _make_capital({name: 5.0}), news, _make_sentiment(),
        )
        self.assertEqual(pool, [])
        self.assertEqual(len(reservoir), 1)
        self.assertEqual(reservoir[0].reason, "no_catalyst")

    def test_calendar_still_wins_when_both_present(self):
        """两类都命中 → 日历算分（有日期前瞻），Node 4 只补留痕，不改分。"""
        name = "稀土"
        cal = _make_calendar_event(name, confidence=0.8)
        news = _make_news(
            events=[_make_news_event([name])],
            calendar_events=[cal],
        )
        pool, _, _ = self._run_channel(
            _make_sector([name]), _make_capital({name: 5.0}), news, _make_sentiment(),
        )
        self.assertEqual(len(pool), 1)
        raw, _, _, _ = AggregatorNode._compute_catalyst_score([cal], TODAY)
        expected = round(raw * AMBUSH_ACCUM_MULTIPLIER.get("weak", 1.0), 3)
        self.assertAlmostEqual(pool[0].catalyst_score, expected, places=6)
        self.assertIn("闸门全通过", pool[0].gate_notes)
        self.assertIn("Node4事件(", pool[0].gate_notes)

    def test_switch_off_restores_calendar_only(self):
        """开关关闭 → 逐字节回现状：Node 4 事件不进池，走 no_catalyst。"""
        name = "稀土"
        news = _make_news(events=[_make_news_event([name])], calendar_none=True)
        with patch("src.got.nodes.aggregator.GOT_NODE4_AMBUSH_EVENTS_ENABLED", False):
            pool, reservoir, _ = self._run_channel(
                _make_sector([name]), _make_capital({name: 5.0}), news, _make_sentiment(),
            )
        self.assertEqual(pool, [])
        self.assertEqual(reservoir[0].reason, "no_catalyst")


# ─────────────────────────────────────────────────────────────
# 第 3 步：置信度硬闸 → 排序权重
# ─────────────────────────────────────────────────────────────

class TestConfidenceGateLow(_ChannelBase):

    def test_gate_low_admits_060_narrative(self):
        """0.62 的叙事事件既过准入、也触发确定性提升（2026-10-23 升格门对齐 0.6）。"""
        name = "稀土"
        ev = _make_calendar_event(name, confidence=0.62)
        news = _make_news(calendar_events=[ev])
        pool, _, _ = self._run_channel(
            _make_sector([name]), _make_capital({name: 5.0}), news, _make_sentiment(),
        )
        self.assertEqual([c.sector_name for c in pool], [name], "0.62 应过低档准入")

        # 提升门与准入闸对齐（回测三档无统计差异，0.7 硬门无依据）
        intel = SectorIntel(sector_name=name, sector_role="ambush", classification="caution")
        promoted = self.node._deterministic_catalyst_promotion(
            [intel], pool, self.node._group_events_by_sector([ev]),
        )
        self.assertEqual(promoted, 1, "0.62 应触发确定性提升（门槛 0.6）")
        self.assertEqual(intel.classification, "actionable")

        # 对照：0.75 事件 → 提升仍然生效（门槛下调未破坏高置信路径）
        ev_high = _make_calendar_event(name, confidence=0.75)
        intel_high = SectorIntel(sector_name=name, sector_role="ambush", classification="caution")
        promoted_high = self.node._deterministic_catalyst_promotion(
            [intel_high], pool, self.node._group_events_by_sector([ev_high]),
        )
        self.assertEqual(promoted_high, 1)

    def test_gate_low_still_blocks_below_060(self):
        """0.58 仍被拦 → 证明 0.6 是闸门而非全放。"""
        name = "稀土"
        news = _make_news(calendar_events=[_make_calendar_event(name, confidence=0.58)])
        pool, reservoir, _ = self._run_channel(
            _make_sector([name]), _make_capital({name: 5.0}), news, _make_sentiment(),
        )
        self.assertEqual(pool, [])
        self.assertEqual(reservoir[0].reason, "no_catalyst")


# ─────────────────────────────────────────────────────────────
# 第 4 步：P6 兑现登记
# ─────────────────────────────────────────────────────────────

class TestNode4P6Registration(_ChannelBase):

    def test_p6_registered_for_node4_candidate(self):
        """Node 4 事件单独作主催化 → register_event 收到 source=node4_event 的 CatalystEvent。"""
        name = "稀土"
        news = _make_news(events=[_make_news_event([name])], calendar_none=True)
        with patch("src.got.catalyst_realization.CatalystRealizationTracker") as tracker_cls:
            pool, _, _ = self._run_channel(
                _make_sector([name]), _make_capital({name: 5.0}), news, _make_sentiment(),
                register_p6=False,
            )
        self.assertEqual([c.sector_name for c in pool], [name])
        register = tracker_cls.return_value.register_event
        register.assert_called_once()
        event = register.call_args[0][0]
        self.assertEqual(event.source, "node4_event")
        self.assertEqual(event.sector, name)
        self.assertEqual(event.event_date, TODAY)
        self.assertEqual(event.direction, "positive")
        self.assertEqual(event.impact_level, 3, "high → ★★★")
        self.assertTrue(event.title.startswith("[Node4]"))
        # 同日重跑幂等：同 source/type/sector/date → 同 event_id
        from src.got.catalyst_calendar import _make_event_id
        self.assertEqual(event.event_id, _make_event_id("node4_event", "narrative", name, TODAY))


# ─────────────────────────────────────────────────────────────
# 第 6 步：跨日发酵原始新闻入 narrative prompt
# ─────────────────────────────────────────────────────────────

def _fermenting_record(title, count=3, span=2):
    return SimpleNamespace(
        title_original=title,
        appearance_count=count,
        first_seen_date=TODAY - timedelta(days=span - 1),
        last_seen_date=TODAY,
    )


class TestFermentingPrompt(unittest.TestCase):

    def _builder(self):
        builder = CatalystCalendarBuilder.__new__(CatalystCalendarBuilder)
        builder._concept_set = {"稀土"}
        builder._critic_drop_ids = set()
        builder._llm = MagicMock()
        builder._llm.call_text.return_value = SimpleNamespace(content="[]")
        return builder

    def _run(self, news_events):
        builder = self._builder()
        start, end = TODAY, TODAY + timedelta(days=14)
        asyncio.run(builder._llm_extract_narrative_catalysts(news_events, start, end))
        call = builder._llm.call_text.call_args
        return call.kwargs["messages"][1]["content"]

    @staticmethod
    def _candidate():
        return NewsEvent(
            event_type="industry", title="某地气象局发布高温预警", summary="",
            impact_level="high", sentiment="positive",
            lifecycle_state="emerging", first_seen=str(TODAY), mention_count=2,
        )

    def test_fermenting_raw_news_in_narrative_prompt(self):
        rows = [_fermenting_record("超强厄尔尼诺持续发酵", count=3),
                _fermenting_record("种植链再掀涨停潮", count=5, span=4)]
        with patch.object(NewsAnalysisNode, "_detect_fermenting_events", return_value=rows):
            prompt_text = self._run([self._candidate()])
        self.assertIn("跨日发酵信号", prompt_text)
        self.assertIn("超强厄尔尼诺持续发酵", prompt_text)
        self.assertIn("种植链再掀涨停潮", prompt_text)
        self.assertIn("出现 5 次，跨 4 天", prompt_text)
        self.assertLess(
            prompt_text.index("种植链再掀涨停潮"), prompt_text.index("超强厄尔尼诺持续发酵"),
            "应按 appearance_count 降序",
        )

    def test_fermenting_switch_off_removes_section(self):
        rows = [_fermenting_record("超强厄尔尼诺持续发酵")]
        with patch.object(NewsAnalysisNode, "_detect_fermenting_events", return_value=rows), \
             patch("src.got.catalyst_calendar.GOT_NARRATIVE_FERMENTING_INPUT", False):
            prompt_text = self._run([self._candidate()])
        self.assertNotIn("跨日发酵信号", prompt_text)

    def test_fermenting_stale_and_bare_titles_excluded(self):
        """调用侧边界：陈旧行（真库最老 8-24）与裸实体标题（「中际旭创」）不得进 prompt。"""
        rows = [
            _fermenting_record("超强厄尔尼诺持续发酵", count=3),
            _fermenting_record("中际旭创", count=29),
            SimpleNamespace(
                title_original="长文拆解某某能源", appearance_count=45,
                first_seen_date=TODAY - timedelta(days=60),
                last_seen_date=TODAY - timedelta(days=30),
            ),
        ]
        with patch.object(NewsAnalysisNode, "_detect_fermenting_events", return_value=rows):
            prompt_text = self._run([self._candidate()])
        self.assertIn("超强厄尔尼诺持续发酵", prompt_text)
        self.assertNotIn("中际旭创", prompt_text)
        self.assertNotIn("长文拆解某某能源", prompt_text)

    def test_fermenting_output_still_guarded(self):
        """新来源事件同样受 D2 周末守卫约束：周六 date 直接丢弃。"""
        builder = self._builder()
        start = TODAY
        end = TODAY + timedelta(days=30)
        saturday = next(
            start + timedelta(days=i) for i in range(31)
            if (start + timedelta(days=i)).weekday() == 5
        )
        payload = json.dumps([{
            "date": saturday.isoformat(),
            "title": f"{saturday} 政策落地",
            "sector": "稀土",
            "impact_level": 2,
            "direction": "positive",
            "confidence": 0.8,
            "consensus_expectation": "预期",
            "category_tag": "drift",
            "source_news": "超强厄尔尼诺持续发酵",
        }], ensure_ascii=False)
        self.assertEqual(builder._parse_llm_narrative(payload, start, end), [])


# ─────────────────────────────────────────────────────────────
# 第 7 步：产业级事件召回（DeepInfer 反例）
# ─────────────────────────────────────────────────────────────

DEEPINFER_TITLE = "DeepSeek开源推理框架DeepInfer，V4.1 Flash登顶Agent Arena开源榜"


class TestIndustryRecall(unittest.TestCase):

    def setUp(self):
        self.node = NewsAnalysisNode(use_mock=True)

    def test_industry_recall_pulls_deepinfer(self):
        """短名单漏掉的单源产业发布被召回，并进入阶段 2 细读 prompt。"""
        raw_news = [{"title": "某公司发布三季报", "source": "sina", "source_type": "sina"}]
        raw_news += [{"title": f"无关社会新闻{i}", "source": "cls", "source_type": "cls"}
                     for i in range(18)]
        raw_news.append({"title": DEEPINFER_TITLE, "source": "sina",
                         "source_type": "sina", "source_count": 1,
                         "url": "https://example.com/deepinfer"})

        recalled = self.node._recall_industry_candidates(raw_news)
        self.assertEqual([r["title"] for r in recalled], [DEEPINFER_TITLE])
        self.assertTrue(recalled[0].get("recalled_industry"))

        # 端到端：短名单只选无关条目，细读 prompt 仍含 [产业] 行与标题
        prompts: list[str] = []
        responses = iter([
            json.dumps({"selected": [{"index": 1, "reason": "财报"}]}),
            json.dumps({"events": [{
                "title": "DeepSeek 开源 DeepInfer 推理框架", "summary": "国产算力推理链",
                "event_type": "industry", "impact_level": "high", "sentiment": "positive",
                "affected_sectors": ["算力"], "source_index": 2,
            }]}, ensure_ascii=False),
            json.dumps({"concepts": []}),
            json.dumps({"risks": ["无"]}),
        ])

        def fake_call(prompt, **kwargs):
            prompts.append(prompt)
            return next(responses)

        with patch.object(NewsAnalysisNode, "_call_llm", side_effect=fake_call), \
             patch.object(NewsAnalysisNode, "_load_unabsorbed_titles", return_value=[]):
            events, _, _ = asyncio.run(self.node._analyze_with_llm(raw_news))

        self.assertTrue(any("[产业]" in p and "DeepInfer" in p for p in prompts),
                        "细读 prompt 必含 [产业] 前缀与召回标题")
        self.assertTrue(any("DeepInfer" in e.title for e in events),
                        f"召回后应能抽出事件: {[e.title for e in events]}")
        # source_index=2 必须回连到召回条目的 url（证明它确实排在第 2 位、与素材行对齐）
        self.assertEqual(events[0].source_url, "https://example.com/deepinfer")

    def test_industry_recall_respects_max_and_switch(self):
        raw_news = [
            {"title": f"某厂开源推理芯片{i}量产", "source": "sina", "source_type": "sina",
             "source_count": 12 - i}
            for i in range(12)
        ]
        recalled = self.node._recall_industry_candidates(raw_news)
        self.assertEqual(len(recalled), INDUSTRY_RECALL_MAX)
        counts = [r["source_count"] for r in recalled]
        self.assertEqual(counts, sorted(counts, reverse=True), "按 source_count 降序")

        with patch("src.got.nodes.news_analysis.GOT_INDUSTRY_RECALL_ENABLED", False):
            self.assertEqual(self.node._recall_industry_candidates(raw_news), [])

    def test_industry_recall_requires_both_keyword_groups(self):
        only_entity = [{"title": "国产算力需求旺盛", "source": "sina", "source_type": "sina"}]
        only_action = [{"title": "公司宣布开源新项目", "source": "sina", "source_type": "sina"}]
        both = [{"title": "存储芯片厂商宣布扩产DRAM产能", "source": "sina", "source_type": "sina"}]
        self.assertEqual(self.node._recall_industry_candidates(only_entity), [])
        self.assertEqual(self.node._recall_industry_candidates(only_action), [])
        self.assertEqual(len(self.node._recall_industry_candidates(both)), 1)


if __name__ == "__main__":
    unittest.main()
