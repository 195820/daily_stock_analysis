# -*- coding: utf-8 -*-
"""埋伏通道 relations 补挂测试（A/B 根因修复，2026-09-30）

覆盖：
1. 截断名/白名单外板块：名称查表落空 → relations 按 board_code 补挂 → 入池
   （索引当日 in-memory glue 优先，前日归档仅作独立调用回退）
2. 护栏：relation 回连事件 confidence<0.7 不入合格集；direction=negative 不补挂
3. relation 指向已过期事件（不在今日日历）→ 丢弃 + relations_hit_all_missed 留痕
4. 幂等：同日同输入两跑，池/蓄水池/漏斗逐字节一致
5. 开关回滚：AMBUSH_RELATIONS_WIRING=False → 行为与接线前逐字节一致
6. 已命中不补挂（纯增量）；升格分支消费 _rel_idx_cache
7. _build_relations_index：当日 glue 属性对象/缺失降级 → 索引正确/空索引 no-op
fixture 复用 tests/test_node6_ambush_funnel.py 的工厂模式。
"""
import asyncio
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from src.got.nodes import aggregator as agg_mod
from src.got.nodes.aggregator import AggregatorNode
from src.got.schemas import (
    SectorContext, CapitalContext, CapitalEvidence, NewsContext, SentimentContext,
    SectorDetail, CatalystEvent, CatalystCalendar, AmbushReservoirEntry, GlueRelation,
)


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
                direction="positive", title="测试催化事件", event_id=None):
    return CatalystEvent(
        event_id=event_id or f"t_{sector}_{days_ahead}_{direction}",
        event_date=date.today() + timedelta(days=days_ahead),
        event_type="narrative",
        title=title,
        sector=sector,
        impact_level=impact,
        direction=direction,
        confidence=confidence,
        source="llm_narrative",
    )


def _write_prev_glue(glue_root, relations):
    """在最近一份前日归档目录写 agent_glue.json（list_archive_dirs before=今日 会取到它）。"""
    day_dir = glue_root / "20260928"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "agent_glue.json").write_text(
        json.dumps({"relations": relations, "status": "ok"}, ensure_ascii=False),
        encoding="utf-8",
    )


class _WiringTestBase(unittest.TestCase):

    def setUp(self):
        self.node = AggregatorNode.__new__(AggregatorNode)
        self.node._replay_mode = False
        # 临时归档根：agent_glue.json 写入其中，避免触碰生产 data/got_daily
        self.tmp_archive_root = Path(tempfile.mkdtemp(prefix="glue_arch_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_archive_root, ignore_errors=True)

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


class TestSupplement(_WiringTestBase):

    def test_truncated_name_supplemented_and_pooled(self):
        """截断名"人形机器"+日历桶名"人形机器人"（legacy 落空）→ relations 补挂入池。"""
        name, code = "人形机器", "880703"
        ev = _make_event("人形机器人", event_id="ev1")  # 桶键与查表键不同 → legacy miss
        sector = SectorContext(
            active_mainline=[],
            ambush=[SectorDetail(code=code, name=name, state="ambush", strength_score=50.0)],
        )
        rel = [{"event_id": "ev1", "board_code": code, "strength": 0.75,
                "direction": "positive", "confidence": 0.8}]
        with patch.object(agg_mod, "GLUE_ARCHIVE_ROOT", self.tmp_archive_root), \
             patch.object(agg_mod, "AMBUSH_RELATIONS_WIRING", True):
            _write_prev_glue(self.tmp_archive_root, rel)
            pool, reservoir, _ = self._run_channel(
                sector, _make_capital({name: 2.0}), _make_news([ev]), _make_sentiment(),
            )

        self.assertEqual(1, len(pool))
        self.assertEqual(name, pool[0].sector_name)
        # B 公式分：impact2=0.6 × proximity(7天>阈值→1.0) × conf 0.9 × weak 0.6 = 0.324
        self.assertAlmostEqual(0.324, round(pool[0].catalyst_score, 3), places=3)
        stats = self._funnel()["relations_supplement"]
        self.assertEqual("on", stats["wiring"])
        self.assertEqual(1, stats["legacy_miss_total"])
        self.assertEqual(1, stats["relations_matched"])
        self.assertEqual(1, stats["supplemented_events"])
        self.assertEqual(0, stats["legacy_miss_and_no_relations_hit"])

    def test_low_confidence_backjoined_event_not_qualified(self):
        """回连事件 confidence 0.5 < 门槛 0.7 → 过闸但不入池，落蓄水池 no_catalyst。"""
        name, code = "人形机器", "880703"
        ev = _make_event("人形机器人", confidence=0.5, event_id="ev2")
        sector = SectorContext(
            active_mainline=[],
            ambush=[SectorDetail(code=code, name=name, state="ambush", strength_score=50.0)],
        )
        rel = [{"event_id": "ev2", "board_code": code, "direction": "positive"}]
        with patch.object(agg_mod, "GLUE_ARCHIVE_ROOT", self.tmp_archive_root), \
             patch.object(agg_mod, "AMBUSH_RELATIONS_WIRING", True):
            _write_prev_glue(self.tmp_archive_root, rel)
            pool, reservoir, _ = self._run_channel(
                sector, _make_capital({name: 2.0}), _make_news([ev]), _make_sentiment(),
            )

        self.assertEqual(0, len(pool))
        self.assertTrue(any(r.sector_name == name and r.reason == "no_catalyst"
                            for r in reservoir))

    def test_negative_relation_direction_not_supplemented(self):
        """relation 自报 direction=negative → 不补挂（利空线不松弛），计 all_missed。"""
        name, code = "人形机器", "880703"
        ev = _make_event("人形机器人", direction="negative", event_id="ev3")
        sector = SectorContext(
            active_mainline=[],
            ambush=[SectorDetail(code=code, name=name, state="ambush", strength_score=50.0)],
        )
        rel = [{"event_id": "ev3", "board_code": code, "direction": "negative"}]
        with patch.object(agg_mod, "GLUE_ARCHIVE_ROOT", self.tmp_archive_root), \
             patch.object(agg_mod, "AMBUSH_RELATIONS_WIRING", True):
            _write_prev_glue(self.tmp_archive_root, rel)
            pool, reservoir, _ = self._run_channel(
                sector, _make_capital({name: 2.0}), _make_news([ev]), _make_sentiment(),
            )

        self.assertEqual(0, len(pool))
        stats = self._funnel()["relations_supplement"]
        self.assertEqual(1, stats["relations_matched"])
        self.assertEqual(1, stats["relations_hit_all_missed"])
        self.assertEqual(0, stats["supplemented_events"])

    def test_expired_relation_event_dropped(self):
        """relation 指向已不在今日日历的事件 → 丢弃，不虚构过期催化。"""
        stats = {k: 0 for k in (
            "legacy_miss_total", "relations_matched",
            "legacy_miss_and_no_relations_hit", "relations_hit_all_missed",
            "supplemented_events")}
        hits = AggregatorNode._supplement_from_relations(
            [], "880703",
            {"880703": [{"event_id": "gone", "direction": "positive"}]},
            [_make_event("别的板", event_id="today_only")],
            stats,
        )
        self.assertEqual([], hits)
        self.assertEqual(1, stats["relations_hit_all_missed"])
        self.assertEqual(0, stats["supplemented_events"])

    def test_legacy_hit_not_supplemented_pure_increment(self):
        """名称查表已命中 → 补挂恒 no-op，不重复不覆盖。"""
        ev_legacy = _make_event("光刻机", event_id="evL")
        ev_other = _make_event("光刻机", days_ahead=8, event_id="evR")
        stats = {k: 0 for k in (
            "legacy_miss_total", "relations_matched",
            "legacy_miss_and_no_relations_hit", "relations_hit_all_missed",
            "supplemented_events")}
        out = AggregatorNode._supplement_from_relations(
            [ev_legacy], "BK001",
            {"BK001": [{"event_id": "evR", "direction": "positive"}]},
            [ev_legacy, ev_other],
            stats,
        )
        self.assertEqual([ev_legacy], out)
        self.assertEqual(0, stats["legacy_miss_total"])

    def test_idempotent_same_day_rerun(self):
        """同日同输入两跑：池/蓄水池/漏斗逐字节一致（守验收红线）。"""
        name, code = "人形机器", "880703"
        ev = _make_event("人形机器人", event_id="ev1")
        sector = SectorContext(
            active_mainline=[],
            ambush=[SectorDetail(code=code, name=name, state="ambush", strength_score=50.0)],
        )
        rel = [{"event_id": "ev1", "board_code": code, "direction": "positive"}]
        with patch.object(agg_mod, "GLUE_ARCHIVE_ROOT", self.tmp_archive_root), \
             patch.object(agg_mod, "AMBUSH_RELATIONS_WIRING", True):
            _write_prev_glue(self.tmp_archive_root, rel)
            p1, r1, v1 = self._run_channel(
                sector, _make_capital({name: 2.0}), _make_news([ev]), _make_sentiment())
            f1 = self._funnel()
            p2, r2, v2 = self._run_channel(
                sector, _make_capital({name: 2.0}), _make_news([ev]), _make_sentiment())
            f2 = self._funnel()

        dump = lambda p, r, v: json.dumps(  # noqa: E731
            [x.model_dump(mode="json") for x in p + r] + list(v),
            ensure_ascii=False, sort_keys=True, default=str)
        self.assertEqual(dump(p1, r1, v1), dump(p2, r2, v2))
        self.assertEqual(json.dumps(f1, sort_keys=True, default=str),
                         json.dumps(f2, sort_keys=True, default=str))


class TestSwitchRollback(_WiringTestBase):

    def test_switch_off_keeps_legacy_behavior(self):
        """AMBUSH_RELATIONS_WIRING=False → 与接线前一致：落空板块回蓄水池 no_catalyst。"""
        name, code = "人形机器", "880703"
        ev = _make_event("人形机器人", event_id="ev1")
        sector = SectorContext(
            active_mainline=[],
            ambush=[SectorDetail(code=code, name=name, state="ambush", strength_score=50.0)],
        )
        rel = [{"event_id": "ev1", "board_code": code, "direction": "positive"}]
        with patch.object(agg_mod, "GLUE_ARCHIVE_ROOT", self.tmp_archive_root), \
             patch.object(agg_mod, "AMBUSH_RELATIONS_WIRING", False):
            _write_prev_glue(self.tmp_archive_root, rel)
            pool, reservoir, _ = self._run_channel(
                sector, _make_capital({name: 2.0}), _make_news([ev]), _make_sentiment(),
            )

        self.assertEqual(0, len(pool))
        self.assertTrue(any(r.sector_name == name and r.reason == "no_catalyst"
                            for r in reservoir))
        stats = self._funnel()["relations_supplement"]
        self.assertEqual("off", stats["wiring"])
        self.assertEqual(0, stats["legacy_miss_total"])


class TestBuildRelationsIndex(unittest.TestCase):
    """当日 in-memory glue → 索引（run() 前置写入 _rel_idx_cache 的生产主路径）。"""

    def test_glue_relation_objects_indexed(self):
        glue = type("G", (), {"relations": [
            GlueRelation(event_id="e1", board_code="880703", strength=0.7,
                         direction="positive", confidence=0.8),
            GlueRelation(event_id="e2", board_code="880703", strength=0.6,
                         direction="negative", confidence=0.9),
            GlueRelation(event_id="e3", board_code="", strength=0.5),
        ]})()
        idx = AggregatorNode._build_relations_index(glue)
        self.assertEqual(
            {"880703": [
                {"event_id": "e1", "direction": "positive"},
                {"event_id": "e2", "direction": "negative"},
            ]},
            idx,
        )

    def test_missing_or_empty_glue_yields_empty_index(self):
        self.assertEqual({}, AggregatorNode._build_relations_index(None))
        self.assertEqual({}, AggregatorNode._build_relations_index(
            type("G", (), {"relations": None})()))
        self.assertEqual({}, AggregatorNode._build_relations_index(
            type("G", (), {})()))

    def test_channel_consumes_preset_cache_without_archive(self):
        """缓存预写（模拟当日 glue）时通道直接补挂，无需任何归档。"""
        name, code = "人形机器", "880703"
        ev = _make_event("人形机器人", event_id="ev1")
        sector = SectorContext(
            active_mainline=[],
            ambush=[SectorDetail(code=code, name=name, state="ambush", strength_score=50.0)],
        )
        node = AggregatorNode.__new__(AggregatorNode)
        node._replay_mode = False
        node._rel_idx_cache = {code: [{"event_id": "ev1", "direction": "positive"}]}

        async def fake_llm(prompt, **kwargs):
            return "[]"

        with patch.object(agg_mod, "AMBUSH_RELATIONS_WIRING", True), \
             patch("src.got.persistence.load_capital_flow_series", return_value=({}, [])), \
             patch.object(AggregatorNode, "_load_previous_reservoir", return_value={}), \
             patch.object(AggregatorNode, "_load_capital_flow_aliases", return_value={}), \
             patch.object(node, "_call_llm_async", new=fake_llm):
            pool, _, _ = asyncio.run(
                node._build_ambush_channel(
                    sector, _make_capital({name: 2.0}), _make_news([ev]), _make_sentiment()
                )
            )
            stats = node._ambush_gate_funnel["relations_supplement"]
        self.assertEqual(1, len(pool))
        self.assertEqual(1, stats["supplemented_events"])


class TestPromotionBranch(unittest.TestCase):
    """升格分支消费 _rel_idx_cache：蓄水池截断名板块经补挂拿到催化 → 重走闸门。"""

    def setUp(self):
        self.node = AggregatorNode.__new__(AggregatorNode)
        self.node._replay_mode = False
        self.tmp_archive_root = Path(tempfile.mkdtemp(prefix="glue_arch_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_archive_root, ignore_errors=True)

    def test_promotion_uses_relations_index(self):
        name, code = "人形机器", "880703"
        ev = _make_event("人形机器人", event_id="ev1")
        entry = AmbushReservoirEntry(
            sector_name=name, sector_code=code, reason="no_catalyst",
            entered_date=date.today() - timedelta(days=3),
            days_in_reservoir=3, entered_strength=45.0,
        )
        sector = SectorContext(
            active_mainline=[],
            strength_lookup={name: 55.0},  # 强度回升：45 → 55
        )
        self.node._rel_idx_cache = {
            code: [{"event_id": "ev1", "direction": "positive"}]
        }

        with patch("src.got.persistence.load_capital_flow_series",
                   return_value=({}, [])), \
             patch.object(AggregatorNode, "_load_capital_flow_aliases", return_value={}):
            # _check_reservoir_promotion 为同步方法（提名 LLM 仅在当日候选支路）
            promoted, remaining = self.node._check_reservoir_promotion(
                [entry], sector, _make_news([ev]),
                _make_capital({name: 2.0}), _make_sentiment(), date.today(),
            )

        self.assertEqual(1, len(promoted))
        self.assertEqual(name, promoted[0].sector_name)
        self.assertIn("蓄水池升格", promoted[0].gate_notes)


if __name__ == "__main__":
    unittest.main()
