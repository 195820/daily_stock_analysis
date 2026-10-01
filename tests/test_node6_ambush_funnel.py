# -*- coding: utf-8 -*-
"""D-P2 埋伏闸门漏斗 skipped_reason 留痕测试（2026-09-26）

覆盖：
1. 死闸未评估留痕：hot_sectors 为空时，全通过路径的 G2/G3 记 skipped 而非 evaluated
2. 资金流缺失候选：G4 记 skipped(flow_data_missing)，不再虚计 evaluated/rejected
3. 数据在位对照：ths_hot_sectors 非空时 G2/G3 正常计 evaluated
4. 决策路径零变更：funnel 改动不影响 pool/reservoir/veto_log 三元组
5. 同日重跑幂等：同输入两跑，funnel 与三元组逐字节一致（守幂等验收红线）

fixture 复用 tests/test_ambush_nomination.py 的工厂模式。
"""
import asyncio
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from src.got.nodes.aggregator import AggregatorNode
from src.got.schemas import (
    SectorContext, CapitalContext, CapitalEvidence, NewsContext, SentimentContext,
    SectorDetail, CatalystEvent, CatalystCalendar, SectorIntel, AmbushCandidate,
)
from src.got.catalyst_config import AMBUSH_ACCUM_MULTIPLIER


# ─────────────────────────────────────────────────────────────
# Fixture 工厂（与 test_ambush_nomination 同构）
# ─────────────────────────────────────────────────────────────

def _make_capital(flow_map, flow_3d_map=None):
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
        confidence_multiplier=1.0,
        evidence=ev,
    )


def _make_sector(names, hot_names=None, strength=60.0):
    return SectorContext(
        ambush=[
            SectorDetail(code=f"BK_{i:03d}", name=n, state="ambush", strength_score=strength)
            for i, n in enumerate(names)
        ],
        ths_hot_sectors={h: 1 for h in (hot_names or [])},
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
        trading_signal="neutral",
        signal_confidence=0.5,
    )


def _make_event(sector, days_ahead=7, impact=2, confidence=0.9,
                direction="positive", title="测试催化事件"):
    return CatalystEvent(
        event_id=f"t_{sector}_{days_ahead}_{direction}",
        event_date=date.today() + timedelta(days=days_ahead),
        event_type="narrative",
        title=title,
        sector=sector,
        impact_level=impact,
        direction=direction,
        confidence=confidence,
        source="llm_narrative",
    )


class _FunnelTestBase(unittest.TestCase):

    def setUp(self):
        self.node = AggregatorNode.__new__(AggregatorNode)
        self.node._replay_mode = False

    def _run_channel(self, sector, capital, news, sentiment):
        async def fake_llm(prompt, **kwargs):
            return "[]"

        with patch("src.got.persistence.load_capital_flow_series",
                   return_value=({}, [])), \
             patch.object(AggregatorNode, "_load_previous_reservoir", return_value={}), \
             patch.object(AggregatorNode, "_load_capital_flow_aliases", return_value={}), \
             patch.object(self.node, "_call_llm_async", new=fake_llm):
            return asyncio.run(
                self.node._build_ambush_channel(sector, capital, news, sentiment)
            )

    def _funnel(self):
        return self.node._ambush_gate_funnel


class TestDeadGateSkipped(_FunnelTestBase):

    def test_g2_g3_skipped_not_evaluated_when_no_hot_sectors(self):
        """hot_sectors 为空 → 全通过路径的 G2/G3 记 skipped，不虚计 evaluated。"""
        name = "光刻机"
        capital = _make_capital({name: 2.0})  # 当日正流入 → G4 weak 通过
        news = _make_news([_make_event(name)])
        pool, _, _ = self._run_channel(
            _make_sector([name]), capital, news, _make_sentiment(),
        )
        funnel = self._funnel()
        self.assertTrue(any(c.sector_name == name for c in pool), "候选应入池")
        self.assertEqual(funnel["G2_防买顶"]["evaluated"], 0)
        self.assertEqual(funnel["G3_拥挤度"]["evaluated"], 0)
        self.assertEqual(funnel["G2_防买顶"]["skipped"], 1)
        self.assertEqual(funnel["G3_拥挤度"]["skipped"], 1)
        self.assertEqual(funnel["G2_防买顶"]["skipped_reason"], "data_unavailable(THS热度为空)")
        self.assertEqual(funnel["G3_拥挤度"]["skipped_reason"], "data_unavailable(THS热度为空)")
        # G4 有数据且通过：正常计 evaluated/accepted
        self.assertEqual(funnel["G4_吸筹"]["evaluated"], 1)
        self.assertEqual(funnel["G4_吸筹"]["accepted"], 1)
        self.assertEqual(funnel["G4_吸筹"]["skipped"], 0)
        self.assertIsNone(funnel["G4_吸筹"]["skipped_reason"])
        self.assertEqual(funnel["final_pool"], 1)

    def test_hot_sectors_present_counts_evaluated(self):
        """数据在位对照：ths_hot_sectors 非空 → G2/G3 计 evaluated，无 skipped。"""
        name = "光刻机"
        capital = _make_capital({name: 2.0})
        news = _make_news([_make_event(name)])
        pool, _, _ = self._run_channel(
            _make_sector([name], hot_names=["消费板块"]), capital, news, _make_sentiment(),
        )
        funnel = self._funnel()
        self.assertTrue(any(c.sector_name == name for c in pool))
        self.assertEqual(funnel["G2_防买顶"]["evaluated"], 1)
        self.assertEqual(funnel["G3_拥挤度"]["evaluated"], 1)
        self.assertEqual(funnel["G2_防买顶"]["skipped"], 0)
        self.assertEqual(funnel["G3_拥挤度"]["skipped"], 0)
        self.assertIsNone(funnel["G2_防买顶"]["skipped_reason"])


class TestFlowMissingSkipped(_FunnelTestBase):

    def test_g4_skipped_when_flow_data_missing(self):
        """资金流缺失候选 → G4 记 skipped(flow_data_missing)，不再计 evaluated/rejected。"""
        name = "量子计算"
        capital = _make_capital({})  # sector_flow_all 空但 evidence 存在 → "未找到板块资金流数据" missing
        news = _make_news([_make_event(name)])
        pool, reservoir, veto_log = self._run_channel(
            _make_sector([name]), capital, news, _make_sentiment(),
        )
        funnel = self._funnel()
        self.assertEqual(funnel["G4_吸筹"]["evaluated"], 0)
        self.assertEqual(funnel["G4_吸筹"]["rejected"], 0)
        self.assertEqual(funnel["G4_吸筹"]["skipped"], 1)
        self.assertEqual(funnel["G4_吸筹"]["skipped_reason"], "flow_data_missing")
        # 决策路径不变：缺失候选仍走蓄水池观察（非否决）
        self.assertFalse(any(c.sector_name == name for c in pool))
        self.assertTrue(any(r.sector_name == name and r.reason == "data_missing" for r in reservoir))
        self.assertFalse(any(v.startswith(f"{name}") for v in veto_log))


class TestDecisionPathUnchanged(_FunnelTestBase):

    def test_negative_veto_funnel_counts_and_no_pool_entry(self):
        """★★★利空否决路径：G1 计 evaluated/rejected，池与蓄水池均空（与改前一致）。"""
        name = "太空养猪"
        capital = _make_capital({name: 2.0})
        news = _make_news([
            _make_event(name, impact=3, direction="negative", title="重大处罚"),
            _make_event(name),
        ])
        pool, reservoir, veto_log = self._run_channel(
            _make_sector([name]), capital, news, _make_sentiment(),
        )
        funnel = self._funnel()
        self.assertEqual(funnel["G1_利空"]["evaluated"], 1)
        self.assertEqual(funnel["G1_利空"]["rejected"], 1)
        self.assertEqual(funnel["G2_防买顶"]["evaluated"], 0)
        self.assertEqual(funnel["G2_防买顶"]["skipped"], 0)  # G1 拦截后不再逐闸计数
        self.assertEqual(funnel["final_pool"], 0)
        self.assertFalse(any(c.sector_name == name for c in pool))
        self.assertEqual(len(reservoir), 0)
        self.assertTrue(any(v.startswith(f"{name}: ★★★利空否决") for v in veto_log))

    def test_same_day_rerun_idempotent(self):
        """同输入两跑：funnel 与三元组逐字节一致（守同日重跑幂等验收）。"""
        import json

        name_a, name_b = "光刻机", "量子计算"
        capital = _make_capital({name_a: 2.0})  # name_b 无资金流 → missing 分支也覆盖
        news = _make_news([_make_event(name_a), _make_event(name_b)])

        def once():
            sector = _make_sector([name_a, name_b])
            pool, reservoir, veto_log = self._run_channel(
                sector, capital, news, _make_sentiment(),
            )
            return (
                json.dumps(self._funnel(), ensure_ascii=False, sort_keys=True),
                json.dumps([c.sector_name for c in pool], ensure_ascii=False),
                json.dumps([r.sector_name for r in reservoir], ensure_ascii=False),
                json.dumps(veto_log, ensure_ascii=False),
            )

        first = once()
        second = once()
        self.assertEqual(first, second, "同日重跑 funnel/决策三元组必须逐字节一致")


class TestG4FreezeRelease(_FunnelTestBase):
    """工作流 A：G4 资金闸门冻结放行（GOT_AMBUSH_G4_FREEZE=1）。"""

    def test_missing_released_and_counted_as_frozen_not_evaluated(self):
        """开关开 + missing 候选 → 入池 + frozen_released=1，不虚计 evaluated/accepted/skipped。"""
        name = "电气设备"
        capital = _make_capital({})  # 无资金流数据 → missing
        news = _make_news([_make_event(name)])
        with patch("src.got.nodes.aggregator.AMBUSH_G4_FREEZE", True):
            pool, reservoir, _ = self._run_channel(
                _make_sector([name]), capital, news, _make_sentiment(),
            )
        funnel = self._funnel()
        self.assertTrue(any(c.sector_name == name for c in pool), "冻结期 missing 应入池")
        self.assertEqual(funnel["G4_吸筹"]["frozen_released"], 1)
        self.assertEqual(funnel["G4_吸筹"]["evaluated"], 0)
        self.assertEqual(funnel["G4_吸筹"]["accepted"], 0)
        self.assertEqual(funnel["G4_吸筹"]["skipped"], 0)
        self.assertEqual(funnel["final_pool"], 1)
        # 不新增 data_missing 蓄水池条目（被①接管）
        self.assertFalse(any(r.sector_name == name and r.reason == "data_missing" for r in reservoir))
        cand = next(c for c in pool if c.sector_name == name)
        self.assertTrue(cand.gate_notes.startswith("G4冻结放行"), f"gate_notes 应标冻结: {cand.gate_notes}")
        self.assertEqual(cand.accum_strength, "missing", "数据诚实：保留真实 accum_strength 不改写")

    def test_freeze_off_preserves_reservoir_diversion(self):
        """开关关（缺省）→ 旧行为不变：missing 走蓄水池 data_missing，G4 计 skipped。"""
        name = "水务"
        capital = _make_capital({})
        news = _make_news([_make_event(name)])
        with patch("src.got.nodes.aggregator.AMBUSH_G4_FREEZE", False):
            pool, reservoir, _ = self._run_channel(
                _make_sector([name]), capital, news, _make_sentiment(),
            )
        funnel = self._funnel()
        self.assertFalse(any(c.sector_name == name for c in pool), "开关关不应入池")
        self.assertEqual(funnel["G4_吸筹"]["skipped"], 1)
        self.assertEqual(funnel["G4_吸筹"]["frozen_released"], 0)
        self.assertTrue(any(r.sector_name == name and r.reason == "data_missing" for r in reservoir))

    def test_run_ambush_gates_freeze_missing_semantics(self):
        """直调 _run_ambush_gates：freeze_missing=True 放行 / =False 静默（兼守护升格支路传 False 不冻结）。"""
        capital = _make_capital({})
        on = self.node._run_ambush_gates("半导体", [], capital, [], date.today(), freeze_missing=True)
        self.assertTrue(on[0], "freeze_missing=True 应视同过闸")
        self.assertEqual(on[3], "missing")
        off = self.node._run_ambush_gates("半导体", [], capital, [], date.today(), freeze_missing=False)
        self.assertFalse(off[0], "freeze_missing=False（升格支路）应维持旧行为")

    def test_multiplier_missing_strictly_below_weak(self):
        """防倒挂：乘数表 missing ×0.5 严格低于 weak（同催化分下冻结条目不高于有弱证据者）。"""
        self.assertEqual(AMBUSH_ACCUM_MULTIPLIER["missing"], 0.5)
        self.assertLess(AMBUSH_ACCUM_MULTIPLIER["missing"], AMBUSH_ACCUM_MULTIPLIER["weak"])


class TestDeterministicPromotionFreeze(_FunnelTestBase):
    """工作流 A ③：确定性提升条件 B 对 missing 的放宽（仅开关开时生效）。"""

    def _build_args(self, name):
        sector = SectorIntel(sector_name=name, sector_role="ambush", classification="caution", reasoning="")
        pool_entry = AmbushCandidate(
            sector_name=name, category_tag="drift", catalyst_score=1.0, accum_strength="missing",
        )
        events_by_sector = {name: [_make_event(name)]}
        return [sector], [pool_entry], events_by_sector

    def test_missing_promoted_when_freeze_on_with_marker(self):
        name = "先进封装"
        sectors, pool, ev = self._build_args(name)
        with patch("src.got.nodes.aggregator.AMBUSH_G4_FREEZE", True):
            n = self.node._deterministic_catalyst_promotion(sectors, pool, ev)
        self.assertEqual(n, 1)
        self.assertEqual(sectors[0].classification, "actionable")
        self.assertIn("[冻结期提升-无资金准据]", sectors[0].reasoning)

    def test_missing_not_promoted_when_freeze_off(self):
        name = "先进封装"
        sectors, pool, ev = self._build_args(name)
        with patch("src.got.nodes.aggregator.AMBUSH_G4_FREEZE", False):
            n = self.node._deterministic_catalyst_promotion(sectors, pool, ev)
        self.assertEqual(n, 0)
        self.assertEqual(sectors[0].classification, "caution")


if __name__ == "__main__":
    unittest.main()
