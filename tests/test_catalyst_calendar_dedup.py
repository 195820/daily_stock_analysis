# -*- coding: utf-8 -*-
"""
催化剂日历修复单元测试（2026-08-28）

覆盖：
1. _map_global_events 跨日去重 — 同一真实事件的多日新闻只产一条，锚定最早日期
2. _is_echo_event — 全方向回声检测（移除 negative 限制，阈值 0.7）
3. _is_past_echo — 时态守卫：过去时源新闻 + 无未来标记的 title 被判事后改写
4. _has_unsourced_entity — 实体一致性：头衔+人名不可溯源 → 判定幻觉
5. _parse_llm_narrative — 端到端：positive 回声 / 实体幻觉被丢弃，合法前瞻保留
6. _cleanup_stale_events — 全量重建清理（mock DB）
"""

import json
import os
import sys
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.catalyst_calendar import CatalystCalendarBuilder
from src.got.schemas import CatalystEvent, NewsEvent


def _news(title: str, first_seen: str) -> NewsEvent:
    return NewsEvent(
        event_type="global",
        title=title,
        summary=title,
        impact_level="high",
        sentiment="neutral",
        lifecycle_state="fermenting",
        mention_count=3,
        first_seen=first_seen,
    )


def _builder(concepts) -> CatalystCalendarBuilder:
    builder = CatalystCalendarBuilder()
    builder._concept_set = set(concepts)  # 绕过 CONCEPT_UNIVERSE 加载
    builder._identity_store = None  # 单测禁用真实 DB 身份锚定（2026-10-21）
    return builder


class TestGlobalMapCrossDayDedup(unittest.TestCase):
    """B: 同一真实事件的多日跟踪新闻只生成一条事件（锚定最早日期）"""

    def test_multi_day_news_single_event_anchored_earliest(self):
        builder = _builder({"CPO"})
        news_events = [
            _news("英伟达发布二季度财报超预期", "2026-08-28"),
            _news("英伟达财报发布，半导体产业链走强", "2026-08-26"),
        ]
        events = builder._map_global_events(news_events, date(2026, 8, 26), date(2026, 9, 9))
        cpo_events = [e for e in events if e.sector == "CPO"]
        self.assertEqual(len(cpo_events), 1, "多日跟踪新闻应聚合为单条事件")
        # 锚定最早新闻日期 08-26（周三）+ 1 交易日 = 08-27
        self.assertEqual(cpo_events[0].event_date, date(2026, 8, 27))

    def test_event_id_idempotent_across_news_sets(self):
        builder = _builder({"CPO"})
        # 首日新闻 + 次日反应新闻：反应新闻被守卫滤除后锚定不变 → event_id 相同（UPSERT 幂等）
        e_full = builder._map_global_events(
            [_news("英伟达财报发布", "2026-08-26"), _news("英伟达市值暴涨", "2026-08-27")],
            date(2026, 8, 26), date(2026, 9, 9),
        )
        e_first_only = builder._map_global_events(
            [_news("英伟达财报发布", "2026-08-26")],
            date(2026, 8, 26), date(2026, 9, 9),
        )
        self.assertEqual(
            [e.event_id for e in e_full],
            [e.event_id for e in e_first_only],
        )


class TestGlobalMapReactionGuard(unittest.TestCase):
    """global_map 反应新闻守卫：事后反应报道不再 +1 锚定新催化剂日期（2026-08-31）"""

    def test_reaction_news_produces_no_event(self):
        builder = _builder({"CPO"})
        events = builder._map_global_events(
            [_news("英伟达市值暴涨3万亿美元", "2026-08-28")],
            date(2026, 8, 28), date(2026, 9, 11),
        )
        self.assertEqual(events, [], "纯反应新闻不应生成任何催化剂事件")

    def test_forward_news_keeps_plus_one_anchor_and_source_news(self):
        builder = _builder({"CPO"})
        events = builder._map_global_events(
            [_news("英伟达将于周五发布二季度财报", "2026-08-26")],
            date(2026, 8, 26), date(2026, 9, 9),
        )
        self.assertEqual(len(events), 1)
        # 08-26（周三）+ 1 交易日 = 08-27；锚定新闻标题写入 source_news 供溯源
        self.assertEqual(events[0].event_date, date(2026, 8, 27))
        self.assertEqual(events[0].source_news, "英伟达将于周五发布二季度财报")

    def test_mixed_news_anchors_earliest_forward(self):
        builder = _builder({"CPO"})
        events = builder._map_global_events(
            [
                _news("英伟达财报发布", "2026-08-27"),
                _news("英伟达市值暴涨3万亿美元", "2026-08-28"),
            ],
            date(2026, 8, 27), date(2026, 9, 10),
        )
        self.assertEqual(len(events), 1)
        # 反应新闻被滤除，锚定事前新闻 08-27（周四）+ 1 交易日 = 08-28（周五）
        self.assertEqual(events[0].event_date, date(2026, 8, 28))
        self.assertEqual(events[0].source_news, "英伟达财报发布")


class TestEchoDetection(unittest.TestCase):
    """D1: 全方向回声检测（不再限定 negative）"""

    def test_positive_echo_detected(self):
        self.assertTrue(CatalystCalendarBuilder._is_echo_event(
            "百度集团完成香港双重主要上市",
            "百度集团完成香港双重主要上市，纳入港股通预期升温",
            "positive",
        ))

    def test_negative_echo_still_detected(self):
        self.assertTrue(CatalystCalendarBuilder._is_echo_event(
            "美股存储板块重挫",
            "美股存储板块重挫，A 股相关板块或承压",
            "negative",
        ))

    def test_distinct_title_not_echo(self):
        self.assertFalse(CatalystCalendarBuilder._is_echo_event(
            "制裁生效后 9-15 落地第二批清单",
            "美国宣布第一批制裁清单",
            "binary",
        ))

    def test_no_source_news_not_echo(self):
        self.assertFalse(CatalystCalendarBuilder._is_echo_event(
            "任意标题", None, "positive",
        ))


class TestTenseGuard(unittest.TestCase):
    """D3: 过去时源新闻 + 无未来标记 title → 事后改写回声"""

    def test_past_source_without_future_marker_dropped(self):
        self.assertTrue(CatalystCalendarBuilder._is_past_echo(
            "英伟达单日市值暴增后，A股算力产业链业绩预期上修",
            "英伟达单日市值暴增2000亿美元",
        ))

    def test_past_source_with_future_marker_retained(self):
        self.assertFalse(CatalystCalendarBuilder._is_past_echo(
            "制裁落地后，预计 9-15 公布第二批清单",
            "第一批制裁清单已落地",
        ))

    def test_non_past_source_retained(self):
        self.assertFalse(CatalystCalendarBuilder._is_past_echo(
            "百度港股通纳入进程受关注",
            "分析师讨论百度港股通纳入进程",
        ))

    def test_no_source_news_retained(self):
        self.assertFalse(CatalystCalendarBuilder._is_past_echo("任意标题", None))


class TestEntityGuard(unittest.TestCase):
    """D4: 头衔+人名必须可溯源到 source_news"""

    def test_unsourced_person_name_flagged(self):
        self.assertTrue(CatalystCalendarBuilder._has_unsourced_entity(
            "美联储主席鲍威尔讲话，市场关注降息路径",
            "美联储理事沃什定于周五发表公开演讲",
        ))

    def test_sourced_person_name_retained(self):
        self.assertFalse(CatalystCalendarBuilder._has_unsourced_entity(
            "美联储主席鲍威尔讲话，市场关注降息路径",
            "美联储主席鲍威尔将在杰克逊霍尔年会发表讲话",
        ))

    def test_no_title_entity_retained(self):
        self.assertFalse(CatalystCalendarBuilder._has_unsourced_entity(
            "美联储9月议息会议临近",
            "市场关注美联储9月议息会议",
        ))

    def test_no_source_news_retained(self):
        self.assertFalse(CatalystCalendarBuilder._has_unsourced_entity(
            "美联储主席鲍威尔讲话", None,
        ))


class TestParseNarrativePipeline(unittest.TestCase):
    """端到端：_parse_llm_narrative 各守卫联动"""

    def test_echo_entity_dropped_and_forward_event_kept(self):
        builder = _builder({"半导体", "银行"})
        items = [
            {   # positive 回声：title 逐字复述源新闻（且源新闻为盘中报道）→ 丢弃
                "date": "2026-09-01",
                "title": "百度集团完成香港双重主要上市",
                "sector": "半导体",
                "impact_level": 2,
                "direction": "positive",
                "confidence": 0.8,
                "consensus_expectation": "纳入港股通",
                "category_tag": "drift",
                "source_news": "百度集团完成香港双重主要上市，早盘涨超4%",
            },
            {   # 实体幻觉：鲍威尔未出现于源新闻（实际是沃什）→ 丢弃
                "date": "2026-09-03",
                "title": "美联储主席鲍威尔讲话，市场关注降息路径",
                "sector": "银行",
                "impact_level": 2,
                "direction": "binary",
                "confidence": 0.8,
                "consensus_expectation": "降息预期",
                "category_tag": "binary",
                "source_news": "美联储理事沃什定于周五发表公开演讲",
            },
            {   # 合法前瞻事件 → 保留（2026-09-04 为周五，非周末）
                "date": "2026-09-04",
                "title": "第二批出口管制清单预计公布",
                "sector": "半导体",
                "impact_level": 2,
                "direction": "binary",
                "confidence": 0.8,
                "consensus_expectation": "管制范围扩大",
                "category_tag": "binary",
                "source_news": "知情人士称第二批出口管制清单将于近期公布",
            },
        ]
        events = builder._parse_llm_narrative(
            json.dumps(items, ensure_ascii=False),
            date(2026, 8, 31), date(2026, 9, 11),
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "第二批出口管制清单预计公布")
        self.assertEqual(events[0].sector, "半导体")


class TestCleanupStaleEvents(unittest.TestCase):
    """C: 全量重建时清理过期记录 + 未再生成的非结构化残留"""

    def test_cleanup_issues_two_deletes_and_commit(self):
        builder = CatalystCalendarBuilder()
        kept = CatalystEvent(
            event_id="kept_id",
            event_date=date(2026, 9, 1),
            event_type="global",
            title="[全球] 测试事件",
            sector="CPO",
            source="global_map",
        )

        fake_session = MagicMock()
        fake_db = MagicMock()
        fake_db.get_session.return_value.__enter__.return_value = fake_session

        with patch("src.storage.DatabaseManager", return_value=fake_db):
            builder._cleanup_stale_events([kept], date(2026, 8, 28))

        # 一次过期清理 + 一次残留清理
        self.assertEqual(fake_session.query.call_count, 2)
        self.assertEqual(fake_session.commit.call_count, 1)


class TestSaveEventsExpiryAnchoring(unittest.TestCase):
    """缺陷②：到期锤定首传日 + drift 尾巴 3 交易日，UPDATE 不续期"""

    def _event(self, eid: str, category_tag: str) -> CatalystEvent:
        return CatalystEvent(
            event_id=eid,
            event_date=date(2026, 9, 1),
            event_type="global",
            title="[全球] 测试事件",
            sector="CPO",
            source="global_map",
            category_tag=category_tag,
        )

    def _fake_db_first_is_none(self):
        fake_session = MagicMock()
        fake_session.query.return_value.filter.return_value.first.return_value = None
        fake_db = MagicMock()
        fake_db.get_session.return_value.__enter__.return_value = fake_session
        return fake_db, fake_session

    def test_insert_drift_uses_3_trade_day_tail(self):
        from src.got.catalyst_calendar import _trade_days_offset
        builder = CatalystCalendarBuilder()
        target = date(2026, 8, 28)
        fake_db, fake_session = self._fake_db_first_is_none()
        with patch("src.storage.DatabaseManager", return_value=fake_db), \
             patch("src.got.catalyst_realization.CatalystRealizationTracker"):
            builder._save_events([self._event("drift_id", "drift")], target)
        added = fake_session.add.call_args[0][0]
        self.assertEqual(added.expires_at, _trade_days_offset(target, 3))

    def test_insert_non_drift_uses_14_day_window(self):
        from datetime import timedelta
        builder = CatalystCalendarBuilder()
        target = date(2026, 8, 28)
        fake_db, fake_session = self._fake_db_first_is_none()
        with patch("src.storage.DatabaseManager", return_value=fake_db), \
             patch("src.got.catalyst_realization.CatalystRealizationTracker"):
            builder._save_events([self._event("bin_id", "binary")], target)
        added = fake_session.add.call_args[0][0]
        self.assertEqual(added.expires_at, target + timedelta(days=14))

    def test_update_does_not_refresh_expiry(self):
        """已存在事件走 UPDATE 分支：expires_at 保留首次入库值，不被续期。"""
        builder = CatalystCalendarBuilder()
        target = date(2026, 9, 25)
        original_expiry = date(2026, 9, 2)  # 首传时锤定的旧到期日
        existing = MagicMock()
        existing.expires_at = original_expiry
        fake_session = MagicMock()
        fake_session.query.return_value.filter.return_value.first.return_value = existing
        fake_db = MagicMock()
        fake_db.get_session.return_value.__enter__.return_value = fake_session
        with patch("src.storage.DatabaseManager", return_value=fake_db), \
             patch("src.got.catalyst_realization.CatalystRealizationTracker"):
            builder._save_events([self._event("drift_id", "drift")], target)
        self.assertEqual(existing.expires_at, original_expiry)  # 未被刷新
        fake_session.add.assert_not_called()  # 走 UPDATE、不新建


class TestForwardWindowLoad(unittest.TestCase):
    """2026-10-21：`_load_active_events` 必须剔除已过事件日的记录。

    实测病灶：1021 归档 22 条里 4 条 event_date 早于当日（社融 10-13 ×2 / LPR 10-20 ×2），
    增量路径按 expires_at 载入后每日原样重发——日历是前瞻窗口，已过事件归 P6 兑现追踪。
    """

    @staticmethod
    def _row(event_date, expires_at, event_id):
        from types import SimpleNamespace

        return SimpleNamespace(
            event_id=event_id, event_date=event_date, event_type="macro",
            title="t", sector="券商", impact_level=2, direction="binary",
            confidence=1.0, consensus_expectation="", source="macro_rule",
            category_tag="binary", evidence_tier="reported", source_url=None,
            is_global=False, source_news=None, affected_sectors=None,
            expires_at=expires_at,
        )

    def test_past_dated_rows_excluded_from_load(self):
        target = date(2026, 10, 21)
        rows = [
            self._row(date(2026, 10, 13), date(2026, 10, 22), "past-1"),
            self._row(date(2026, 10, 20), date(2026, 10, 22), "past-2"),
            self._row(date(2026, 10, 21), date(2026, 10, 23), "today"),
            self._row(date(2026, 10, 31), date(2026, 11, 14), "future"),
        ]
        fake_session = MagicMock()
        fake_session.query.return_value.filter.return_value.all.return_value = rows
        fake_db = MagicMock()
        fake_db.get_session.return_value.__enter__.return_value = fake_session

        with patch("src.storage.DatabaseManager", return_value=fake_db):
            events = CatalystCalendarBuilder()._load_active_events(target)

        self.assertEqual(
            [e.event_date for e in events], [date(2026, 10, 21), date(2026, 10, 31)],
            "事件日当天保留（day-0 催化），已过事件日剔除",
        )
        self.assertEqual([e.event_id for e in events], ["today", "future"])


class TestGlobalMapStaleNewsDoesNotPoisonAnchor(unittest.TestCase):
    """2026-10-21：仍在发酵的旧跟踪行不得钉死聚合锚。

    病灶：hits 取「最早 first_seen」，而新闻窗口按 last_seen 选行——一条 10-16 的
    财报跟踪行（last_seen 10-21 仍在窗口）会让 10-22 的新财报锚定到 10-19 < start，
    整条 mapping 被 continue 丢弃，新事件永远上不来（实测复现：10-16+10-22 → 0 事件）。
    """

    def test_fresh_news_anchors_even_when_old_tracking_news_present(self):
        builder = _builder({"CPO"})
        events = builder._map_global_events(
            [
                _news("英伟达财报发布，超预期", "2026-10-16"),
                _news("英伟达财报指引上调", "2026-10-22"),
            ],
            date(2026, 10, 22), date(2026, 11, 5),
        )
        self.assertEqual(len(events), 1, "旧跟踪行不得吞掉今天的新事件")
        self.assertEqual(events[0].event_date, date(2026, 10, 23))
        self.assertEqual(events[0].source_news, "英伟达财报指引上调")

    def test_out_of_window_only_news_still_produces_nothing(self):
        builder = _builder({"CPO"})
        events = builder._map_global_events(
            [_news("英伟达财报发布，超预期", "2026-10-16")],
            date(2026, 10, 22), date(2026, 11, 5),
        )
        self.assertEqual(events, [], "纯旧新闻不产事件（与过滤前一致）")


if __name__ == "__main__":
    unittest.main()
