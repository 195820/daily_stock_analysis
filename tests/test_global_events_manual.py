# -*- coding: utf-8 -*-
"""global_events_manual（一次性全球大事件单列通道，2026-10-22）单测。

治理规则：固定事件目录（GLOBAL_TO_A_SHARE_MAP）只收可复现、有日历节奏的事件；
一次性大事件单列登记到本模块，字段逐项取人工值（不回退 map 缺省），有效期缺省 7 天。
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.catalyst_calendar import CatalystCalendarBuilder, _make_event_id
from src.got.global_events_manual import active_manual_events


def _entry(**overrides):
    entry = {
        "event_id": "manual_20261020_deepinfer",
        "title": "[全球·单列] DeepInfer 开源推理框架",
        "occurred_date": date(2026, 10, 20),
        "sectors": ["AI应用"],
        "direction": "positive",
        "category_tag": "fade",
        "impact_level": 1,
        "confidence": 0.55,
        "a_share_linkage": "国产推理框架开源 → 国产算力/推理部署链催化",
        "source_news": "DeepSeek开源推理框架DeepInfer，V4.1 Flash登顶Agent Arena开源榜",
        "valid_days": 7,
    }
    entry.update(overrides)
    return entry


def _builder(concepts):
    builder = CatalystCalendarBuilder()
    builder._concept_set = set(concepts)
    builder._identity_store = None
    return builder


class TestActiveManualEvents(unittest.TestCase):
    def test_active_manual_events_respects_valid_days(self):
        entries = [
            _entry(event_id="yesterday", occurred_date=date(2026, 10, 20)),
            _entry(event_id="today", occurred_date=date(2026, 10, 21)),
            _entry(event_id="expired", occurred_date=date(2026, 10, 14)),  # +7d = 10-21，不含
        ]
        with patch("src.got.global_events_manual.MANUAL_GLOBAL_EVENTS", entries):
            active = active_manual_events(date(2026, 10, 21))
        self.assertEqual([e["event_id"] for e in active], ["yesterday", "today"])

    def test_expired_manual_event_produces_nothing(self):
        entries = [_entry(occurred_date=date(2026, 10, 10))]
        builder = _builder({"AI应用"})
        with patch("src.got.global_events_manual.MANUAL_GLOBAL_EVENTS", entries):
            self.assertEqual(active_manual_events(date(2026, 10, 21)), [])
            self.assertEqual(
                builder._manual_global_events(date(2026, 10, 21), date(2026, 11, 4)),
                [],
            )


class TestManualEventConversion(unittest.TestCase):
    def test_manual_event_id_stable_and_prefixed(self):
        builder = _builder({"AI应用"})
        with patch("src.got.global_events_manual.MANUAL_GLOBAL_EVENTS", [_entry()]):
            first = builder._manual_global_events(date(2026, 10, 21), date(2026, 11, 4))
            second = builder._manual_global_events(date(2026, 10, 21), date(2026, 11, 4))
        self.assertEqual(len(first), 1)
        expected = _make_event_id("global_manual", "global", "AI应用", date(2026, 10, 21))
        self.assertEqual(first[0].event_id, expected)
        self.assertEqual(first[0].event_id, second[0].event_id, "同 sector+date 幂等")
        self.assertNotEqual(
            first[0].event_id,
            _make_event_id("global_map", "global", "AI应用", date(2026, 10, 21)),
            "单列事件不得与关键词映射撞 ID",
        )

    def test_manual_event_does_not_inherit_map_defaults(self):
        builder = _builder({"AI应用"})
        with patch("src.got.global_events_manual.MANUAL_GLOBAL_EVENTS", [_entry()]):
            events = builder._manual_global_events(date(2026, 10, 21), date(2026, 11, 4))
        event = events[0]
        self.assertEqual(event.source, "global_manual")
        self.assertTrue(event.is_global)
        self.assertEqual(event.impact_level, 1, "impact_level 取人工登记值（map #4 缺省是 2）")
        self.assertEqual(event.confidence, 0.55, "confidence 取人工值（map 缺省 0.8）")
        self.assertEqual(event.title, "[全球·单列] DeepInfer 开源推理框架")
        self.assertEqual(
            event.source_news,
            "DeepSeek开源推理框架DeepInfer，V4.1 Flash登顶Agent Arena开源榜",
        )
        self.assertEqual(event.consensus_expectation, "国产推理框架开源 → 国产算力/推理部署链催化")
        self.assertEqual(event.event_date, date(2026, 10, 21), "occurred_date + 1 交易日")

    def test_unmatched_sector_produces_nothing(self):
        builder = _builder({"券商"})
        with patch("src.got.global_events_manual.MANUAL_GLOBAL_EVENTS", [_entry()]):
            self.assertEqual(
                builder._manual_global_events(date(2026, 10, 21), date(2026, 11, 4)), [],
            )


class TestCleanupIncludesManual(unittest.TestCase):
    def test_cleanup_stale_events_includes_global_manual(self):
        builder = _builder({"AI应用"})
        with patch("src.got.global_events_manual.MANUAL_GLOBAL_EVENTS", [_entry()]):
            events = builder._manual_global_events(date(2026, 10, 21), date(2026, 11, 4))

        fake_session = MagicMock()
        fake_session.query.return_value.filter.return_value.delete.return_value = 0
        fake_db = MagicMock()
        fake_db.get_session.return_value.__enter__.return_value = fake_session
        with patch("src.storage.DatabaseManager", return_value=fake_db):
            builder._cleanup_stale_events(events, date(2026, 10, 21))

        expressions = " ".join(
            str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
            for call in fake_session.query.return_value.filter.call_args_list
        )
        self.assertIn("global_manual", expressions,
                      "produced_sources 必须含 global_manual，否则单列事件过期后永久残留")

    def test_cleanup_always_cleans_deterministic_sources(self):
        """global_map/global_manual 本次产出 0 也必须清残留（2026-10-21 实测）。

        full 重建 global=0 时旧 `global_map` 行仍在库（expires 未到、event_date=当日），
        同一交易日再跑走增量 `_load_active_events` 会把它们原样载回日历。
        """
        from src.got.schemas import CatalystEvent

        builder = _builder({"券商"})
        macro_event = CatalystEvent(
            event_id="macro1", event_date=date(2026, 10, 21), event_type="macro",
            title="官方制造业PMI", sector="券商", source="macro_rule",
        )
        fake_session = MagicMock()
        fake_session.query.return_value.filter.return_value.delete.return_value = 0
        fake_db = MagicMock()
        fake_db.get_session.return_value.__enter__.return_value = fake_session
        with patch("src.storage.DatabaseManager", return_value=fake_db):
            builder._cleanup_stale_events([macro_event], date(2026, 10, 21))

        expressions = " ".join(
            str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
            for call in fake_session.query.return_value.filter.call_args_list
        )
        self.assertIn("global_map", expressions)
        self.assertIn("global_manual", expressions)


if __name__ == "__main__":
    unittest.main()
