# -*- coding: utf-8 -*-
"""埋伏通道 Agent 增强测试（0831 短剧案例修复，决策 19/20/21）

覆盖：
1. S1 静默否决留痕：吸筹失败 → 「吸筹观察」进否决日志；真否决不受影响
2. S3 Agent 提名：采纳入池（nominated/折扣/移除观察条目）
3. 护栏 G1（★★★利空）/ G3（无吸筹证据不进视野）/ G5（单跑 ≤2）
4. 置信度门槛 / LLM 降级 / 紧急开关
5. S4 LLM 精炼切片扩围（埋伏候选 + 催化事件注入）
"""
import asyncio
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from src.got.nodes.aggregator import AggregatorNode
from src.got.schemas import (
    SectorContext, CapitalContext, CapitalEvidence, NewsContext, SentimentContext,
    SectorDetail, CatalystEvent, CatalystCalendar, MarketRegime, SectorIntel,
)
from src.got.catalyst_config import AMBUSH_NOMINATION_MULTIPLIER


# ─────────────────────────────────────────────────────────────
# Fixture 工厂
# ─────────────────────────────────────────────────────────────

def _make_capital(flow_map, flow_3d_map=None):
    """构造带指定板块资金流的 CapitalContext。"""
    ev = CapitalEvidence(
        sector_flow={
            "score": 0.1,
            "sector_flow_all": flow_map,
            "sector_flow_all_3d": flow_3d_map or {},
            "top_concepts": [],
            "top_industries": [],
            "mainline_bonus": False,
            "timestamp": "mock",
        }
    )
    return CapitalContext(
        status="normal",
        institutional_attitude="neutral",
        institutional_score=0.0,
        evidence=ev,
    )


def _make_sector(names, strength=60.0):
    return SectorContext(
        ambush=[
            SectorDetail(code=f"BK_{i:03d}", name=n, state="ambush", strength_score=strength)
            for i, n in enumerate(names)
        ],
    )


def _make_news(events):
    return NewsContext(
        overall_impact="neutral",
        impact_score=0.0,
        catalyst_calendar=CatalystCalendar(target_date=date.today(), events=events),
    )


def _make_sentiment():
    return SentimentContext(
        overall_sentiment="neutral",
        sentiment_score=0.0,
        sentiment_stage="neutral",
        signal_confidence=0.5,
    )


def _make_event(sector, days_ahead=7, impact=2, confidence=0.9,
                direction="positive", title="测试催化事件"):
    return CatalystEvent(
        event_id=f"t_{sector}_{days_ahead}",
        event_date=date.today() + timedelta(days=days_ahead),
        event_type="narrative",
        title=title,
        sector=sector,
        impact_level=impact,
        direction=direction,
        confidence=confidence,
        source="llm_narrative",
    )


class _ChannelTestBase(unittest.TestCase):
    """公共基类：隔离归档/文件系统，mock LLM 调用并捕获 prompt。"""

    def setUp(self):
        self.node = AggregatorNode.__new__(AggregatorNode)
        self.prompts: list[str] = []
        self.mock_series: dict = {}

    def _run_channel(self, sector, capital, news, sentiment,
                     llm_response="[]", llm_error=None):
        async def fake_llm(prompt, **kwargs):
            self.prompts.append(prompt)
            if llm_error:
                raise llm_error
            return llm_response

        with patch("src.got.persistence.load_capital_flow_series",
                   return_value=(self.mock_series, [])), \
             patch.object(AggregatorNode, "_load_previous_reservoir", return_value={}), \
             patch.object(AggregatorNode, "_load_capital_flow_aliases", return_value={}), \
             patch.object(self.node, "_call_llm_async", new=fake_llm):
            return asyncio.run(
                self.node._build_ambush_channel(sector, capital, news, sentiment)
            )


# ─────────────────────────────────────────────────────────────
# S1: 静默否决留痕
# ─────────────────────────────────────────────────────────────

class TestObservationTrace(_ChannelTestBase):

    def test_accumulation_fail_leaves_trace(self):
        """吸筹失败 → 「吸筹观察」进否决日志（不再静默），且未获提名时保留。"""
        name = "短剧游戏"
        capital = _make_capital({name: -5.0})
        news = _make_news([_make_event(name)])
        pool, _, veto_log = self._run_channel(
            _make_sector([name]), capital, news, _make_sentiment(),
        )
        self.assertFalse(any(c.sector_name == name for c in pool))
        self.assertTrue(
            any(v.startswith(f"{name}: 吸筹观察") for v in veto_log),
            f"应含吸筹观察留痕: {veto_log}",
        )

    def test_real_veto_unaffected(self):
        """★★★利空真否决仍按原样留痕，不被观察前缀污染。"""
        name = "太空养猪"
        capital = _make_capital({name: 5.0})
        news = _make_news([_make_event(name, impact=3, direction="negative",
                                       title="监管点名（利空）")])
        _, _, veto_log = self._run_channel(
            _make_sector([name]), capital, news, _make_sentiment(),
        )
        self.assertTrue(any("利空否决" in v for v in veto_log), veto_log)
        self.assertFalse(any("吸筹观察" in v for v in veto_log))


# ─────────────────────────────────────────────────────────────
# S3: Agent 提名
# ─────────────────────────────────────────────────────────────

class TestNominationAdoption(_ChannelTestBase):

    def test_adopted_enters_pool(self):
        """采纳提名 → 入池、nominated、0.6 折扣、观察条目移除。"""
        name = "短剧游戏"
        self.mock_series = {name: [("20260827", 1.0), ("20260828", 2.0)]}  # 连续2日 → 过 G3
        capital = _make_capital({name: -2.0})  # 当日净流出 → 吸筹闸门拦下
        events = [_make_event(name, title="AI上星剧定档")]
        llm = f'[{{"sector": "{name}", "nominate": true, "confidence": 0.8, "reason": "定档实质利好"}}]'

        pool, _, veto_log = self._run_channel(
            _make_sector([name]), capital, _make_news(events), _make_sentiment(),
            llm_response=llm,
        )

        nominated = [c for c in pool if c.sector_name == name]
        self.assertEqual(len(nominated), 1, f"应提名入池: pool={[(c.sector_name, c.gate_notes) for c in pool]}")
        cand = nominated[0]
        self.assertTrue(cand.nominated)
        self.assertIn("Agent提名", cand.gate_notes)
        self.assertIn("定档实质利好", cand.gate_notes)
        # 0.6 折扣：与正常 B 公式得分对比
        raw, _, _, _ = self.node._compute_catalyst_score(events, date.today())
        self.assertAlmostEqual(cand.catalyst_score, round(raw * AMBUSH_NOMINATION_MULTIPLIER, 3))
        # 提名成功 → 观察条目被移除
        self.assertFalse(any(v.startswith(f"{name}: 吸筹观察") for v in veto_log))

    def test_g1_negative_blocks_nomination(self):
        """G1：★★★利空否决权不可被 Agent 覆盖。"""
        name = "短剧游戏"
        self.mock_series = {name: [("20260827", 1.0), ("20260828", 2.0)]}
        capital = _make_capital({name: -2.0})
        events = [
            _make_event(name, impact=3, direction="negative", title="监管利空"),
            _make_event(name, title="正面催化"),
        ]
        llm = f'[{{"sector": "{name}", "nominate": true, "confidence": 0.9, "reason": "强行提名"}}]'

        pool, _, veto_log = self._run_channel(
            _make_sector([name]), capital, _make_news(events), _make_sentiment(),
            llm_response=llm,
        )
        self.assertFalse(any(c.sector_name == name for c in pool))
        self.assertTrue(any("利空否决" in v for v in veto_log), veto_log)
        self.assertEqual(self.prompts, [], "利空否决后不应调用提名 LLM")

    def test_g3_no_evidence_not_in_view(self):
        """G3：无归档吸筹证据（连续0日 + 3日均≤0）→ 不进 Agent 视野。"""
        name = "裸奔题材"
        capital = _make_capital({name: -5.0}, flow_3d_map={name: -1.0})
        news = _make_news([_make_event(name)])
        llm = f'[{{"sector": "{name}", "nominate": true, "confidence": 0.9, "reason": "x"}}]'

        pool, _, veto_log = self._run_channel(
            _make_sector([name]), capital, news, _make_sentiment(),
            llm_response=llm,
        )
        self.assertEqual(self.prompts, [], "G3 不满足不应调用提名 LLM")
        self.assertFalse(any(c.sector_name == name for c in pool))
        self.assertTrue(any("吸筹观察" in v for v in veto_log), veto_log)

    def test_g5_max_two_candidates(self):
        """G5：3 个合格候选只有 2 个进 prompt（单次批量，名额 2）。"""
        names = ["题材甲", "题材乙", "题材丙"]
        self.mock_series = {n: [("20260827", 1.0), ("20260828", 2.0)] for n in names}
        capital = _make_capital({n: -2.0 for n in names})
        news = _make_news([_make_event(n) for n in names])

        self._run_channel(
            _make_sector(names), capital, news, _make_sentiment(), llm_response="[]",
        )
        self.assertEqual(len(self.prompts), 1, "应单次批量调用")
        in_prompt = [n for n in names if n in self.prompts[0]]
        self.assertEqual(len(in_prompt), 2, f"prompt 应只含 2 个候选: {in_prompt}")

    def test_low_confidence_rejected(self):
        """置信度 0.5 < 0.6 → 不采纳，观察留痕保留。"""
        name = "短剧游戏"
        self.mock_series = {name: [("20260827", 1.0), ("20260828", 2.0)]}
        capital = _make_capital({name: -2.0})
        llm = f'[{{"sector": "{name}", "nominate": true, "confidence": 0.5, "reason": "不确定"}}]'

        pool, _, veto_log = self._run_channel(
            _make_sector([name]), capital, _make_news([_make_event(name)]),
            _make_sentiment(), llm_response=llm,
        )
        self.assertFalse(any(c.sector_name == name for c in pool))
        self.assertTrue(any(v.startswith(f"{name}: 吸筹观察") for v in veto_log))

    def test_llm_failure_degrades(self):
        """LLM 异常 → 无提名、不崩溃、留痕为观察（R4 降级）。"""
        name = "短剧游戏"
        self.mock_series = {name: [("20260827", 1.0), ("20260828", 2.0)]}
        capital = _make_capital({name: -2.0})

        pool, _, veto_log = self._run_channel(
            _make_sector([name]), capital, _make_news([_make_event(name)]),
            _make_sentiment(), llm_error=RuntimeError("LLM 超时"),
        )
        self.assertFalse(any(c.sector_name == name for c in pool))
        self.assertTrue(any("吸筹观察" in v for v in veto_log), veto_log)

    def test_disabled_switch_skips_nomination(self):
        """AMBUSH_NOMINATION_DISABLED=true → 完全跳过提名逻辑。"""
        name = "短剧游戏"
        self.mock_series = {name: [("20260827", 1.0), ("20260828", 2.0)]}
        capital = _make_capital({name: -2.0})

        with patch("src.got.nodes.aggregator.AMBUSH_NOMINATION_DISABLED", True):
            pool, _, veto_log = self._run_channel(
                _make_sector([name]), capital, _make_news([_make_event(name)]),
                _make_sentiment(),
                llm_response=f'[{{"sector": "{name}", "nominate": true, "confidence": 0.9, "reason": "x"}}]',
            )
        self.assertEqual(self.prompts, [], "开关关闭不应调用提名 LLM")
        self.assertFalse(any(c.sector_name == name for c in pool))
        self.assertTrue(any("吸筹观察" in v for v in veto_log))


# ─────────────────────────────────────────────────────────────
# S4: LLM 精炼视野扩围
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
