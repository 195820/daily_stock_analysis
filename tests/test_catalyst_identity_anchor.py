# -*- coding: utf-8 -*-
"""
催化剂身份锚定（2026-10-21）：global_map 同一真实事件的多次新闻命中在 CATALYST_WINDOW_DAYS
内只锚定一次，杜绝滚动重锚生成新 event_id 导致 P6 样本被重复行稀释（Tetlock 2011 stale news 实证）。

七用例覆盖：身份复用 / anchor_count 递增 / 超窗重锚 / macro 评分入口排除 /
跨日 event_id 幂等 / store=None 禁用钩子 / 增量路径过期锚定不产新事件；
另含 calibration 脚本纯函数回归（无 DB）。
"""

import os
import sys
import types
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.catalyst_calendar import (
    CatalystCalendarBuilder,
    _identity_key,
    _make_event_id,
)
from src.got.schemas import NewsEvent


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


class _FakeIdentityStore:
    """内存身份存储，实现与 _IdentityAnchorStore 完全相同的契约（无 DB）。"""

    def __init__(self, rows=None):
        self.rows = {k: dict(v) for k, v in (rows or {}).items()}
        self.touches = []
        self.upserts = []

    def load(self, key):
        return self.rows.get(key)

    def touch(self, key):
        self.touches.append(key)
        if key not in self.rows:
            return -1
        self.rows[key]["anchor_count"] += 1
        return self.rows[key]["anchor_count"]

    def upsert(self, key, news_date, catalyst_date, source_news):
        self.upserts.append((key, news_date, catalyst_date, source_news))
        self.rows[key] = {
            "first_anchor_date": news_date,
            "catalyst_date": catalyst_date,
            "anchor_count": 1,
        }
        return True


def _builder_with_store(store, concepts=("CPO",)):
    builder = CatalystCalendarBuilder()
    builder._concept_set = set(concepts)
    builder._identity_store = store
    return builder


# 英伟达财报 mapping 的 global_event 字符串（与 catalyst_config.py GLOBAL_TO_A_SHARE_MAP 一致）
_NV_GLOBAL_EVENT = "英伟达财报"


class TestIdentityAnchorReuse(unittest.TestCase):
    """用例 1：身份已锚定且未超窗 → 永不再滚动，event_date/event_id 与首日一致"""

    def test_second_day_same_identity_reuses_anchor(self):
        ikey = _identity_key(_NV_GLOBAL_EVENT)
        store = _FakeIdentityStore({
            ikey: {
                "first_anchor_date": date(2026, 10, 16),
                "catalyst_date": date(2026, 10, 16),
                "anchor_count": 1,
            }
        })
        builder = _builder_with_store(store)

        start, end = date(2026, 10, 14), date(2026, 11, 4)
        events = builder._map_global_events(
            [_news("英伟达财报发布", "2026-10-20")], start, end
        )

        cpo_events = [e for e in events if e.sector == "CPO"]
        self.assertEqual(len(cpo_events), 1)
        # 关键断言：event_date 复用 10-16（不是 fresh 10-21）
        # stale = (10-20 - 10-16).days = 4 < 14 → 不重锚
        self.assertEqual(cpo_events[0].event_date, date(2026, 10, 16))
        self.assertEqual(
            cpo_events[0].event_id,
            _make_event_id("global_map", "global", "CPO", date(2026, 10, 16)),
        )


class TestIdentityAnchorCountIncrements(unittest.TestCase):
    """用例 2：身份复用时 anchor_count 递增，_identity_hits 同步记录"""

    def test_identity_anchor_count_increments(self):
        ikey = _identity_key(_NV_GLOBAL_EVENT)
        store = _FakeIdentityStore({
            ikey: {
                "first_anchor_date": date(2026, 10, 16),
                "catalyst_date": date(2026, 10, 16),
                "anchor_count": 1,
            }
        })
        builder = _builder_with_store(store)

        start, end = date(2026, 10, 14), date(2026, 11, 4)
        events = builder._map_global_events(
            [_news("英伟达财报发布", "2026-10-20")], start, end
        )
        self.assertGreaterEqual(len([e for e in events if e.sector == "CPO"]), 1)

        self.assertEqual(store.touches, [ikey])
        self.assertEqual(store.rows[ikey]["anchor_count"], 2)
        self.assertEqual(builder._identity_hits[ikey], 2)


class TestStaleIdentityReanchor(unittest.TestCase):
    """用例 3：首次锚定超窗（>= CATALYST_WINDOW_DAYS）→ 新锚定日期生效，anchor_count 重置 1"""

    def test_stale_identity_past_window_reanchors(self):
        ikey = _identity_key(_NV_GLOBAL_EVENT)
        store = _FakeIdentityStore({
            ikey: {
                "first_anchor_date": date(2026, 9, 1),
                "catalyst_date": date(2026, 9, 2),
                "anchor_count": 5,
            }
        })
        builder = _builder_with_store(store)

        start, end = date(2026, 10, 14), date(2026, 11, 4)
        events = builder._map_global_events(
            [_news("英伟达财报发布", "2026-10-20")], start, end
        )

        cpo_events = [e for e in events if e.sector == "CPO"]
        self.assertEqual(len(cpo_events), 1)
        # stale = (10-20 - 9-1).days = 49 >= 14 → 重锚 → fresh_anchor = 10-21
        self.assertEqual(cpo_events[0].event_date, date(2026, 10, 21))
        self.assertEqual(len(store.upserts), 1)
        self.assertEqual(store.upserts[0][2], date(2026, 10, 21))  # catalyst_date
        self.assertEqual(store.rows[ikey]["anchor_count"], 1)


class TestMacroExcludedFromGrouping(unittest.TestCase):
    """用例 4：AggregatorNode._exclude_macro_regime_ref 按开关过滤 macro_rule"""

    def test_macro_excluded_from_grouping(self):
        from src.got.nodes.aggregator import AggregatorNode

        macro = types.SimpleNamespace(source="macro_rule")
        global_map = types.SimpleNamespace(source="global_map")
        mixed = [macro, global_map]

        # 开关 true → 过滤掉 macro_rule
        with unittest.mock.patch(
            "src.got.nodes.aggregator.CATALYST_MACRO_AS_REGIME_REF", True
        ):
            result = AggregatorNode._exclude_macro_regime_ref(mixed)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0], global_map)

        # 开关 false → 原样返回（同一 list 对象）
        with unittest.mock.patch(
            "src.got.nodes.aggregator.CATALYST_MACRO_AS_REGIME_REF", False
        ):
            result = AggregatorNode._exclude_macro_regime_ref(mixed)
        self.assertIs(result, mixed)


class TestEventIdStableAcrossDays(unittest.TestCase):
    """用例 5：P6 零新增行保证 —— 同一身份跨日新闻生成相同 event_id 集合"""

    def test_map_global_events_id_stable_across_days(self):
        store = _FakeIdentityStore()
        builder = _builder_with_store(store)
        start, end = date(2026, 10, 14), date(2026, 11, 4)

        # 第一跑：10-20 新闻 → store 空 → fresh_anchor=10-21，首次 upsert
        run1 = builder._map_global_events(
            [_news("英伟达财报发布", "2026-10-20")], start, end
        )
        run1_cpo = [e for e in run1 if e.sector == "CPO"]
        self.assertEqual(len(run1_cpo), 1)

        # 第二跑：10-21 新闻（同身份，新一天）→ stale=(10-21-10-20).days=1<14 → 复用
        run2 = builder._map_global_events(
            [_news("英伟达财报发布", "2026-10-21")], start, end
        )
        run2_cpo = [e for e in run2 if e.sector == "CPO"]
        self.assertEqual(len(run2_cpo), 1)

        self.assertEqual(
            [e.event_id for e in run2_cpo],
            [e.event_id for e in run1_cpo],
        )
        self.assertEqual(run2_cpo[0].event_date, run1_cpo[0].event_date)


class TestStoreNoneDisablesAnchoring(unittest.TestCase):
    """用例 6：_identity_store = None 禁用锚定，第二跑按 fresh 日期锚（无异常）"""

    def test_store_none_disables_anchoring(self):
        builder = _builder_with_store(_FakeIdentityStore())
        builder._identity_store = None
        start, end = date(2026, 10, 14), date(2026, 11, 4)

        # 第一跑：10-20 新闻 → fresh_anchor=10-21
        run1 = builder._map_global_events(
            [_news("英伟达财报发布", "2026-10-20")], start, end
        )
        run1_cpo = [e for e in run1 if e.sector == "CPO"]
        self.assertEqual(len(run1_cpo), 1)
        self.assertEqual(run1_cpo[0].event_date, date(2026, 10, 21))

        # 第二跑：10-21 新闻 → fresh_anchor=10-22（无身份复用，日期前进）
        run2 = builder._map_global_events(
            [_news("英伟达财报发布", "2026-10-21")], start, end
        )
        run2_cpo = [e for e in run2 if e.sector == "CPO"]
        self.assertEqual(len(run2_cpo), 1)
        self.assertEqual(run2_cpo[0].event_date, date(2026, 10, 22))


class TestIncrementalShapeExpiredAnchorEmitsNoNewEvent(unittest.TestCase):
    """用例 7：增量路径（start=today）过期锚定不产新事件 —— P6 零新增行回归守卫

    场景：身份已锚定（first_anchor=10-16, catalyst_date=10-19），今日=10-20 跑增量。
    stale = (10-20 - 10-16).days = 4 < 14 → 不重锚 → catalyst_date 沿用 10-19。
    但 10-19 < start=10-20 → `if catalyst_date < start: continue` 淘汰 → 无事件产出。
    """

    def test_incremental_shape_expired_anchor_emits_no_new_event(self):
        ikey = _identity_key(_NV_GLOBAL_EVENT)
        store = _FakeIdentityStore({
            ikey: {
                "first_anchor_date": date(2026, 10, 16),
                "catalyst_date": date(2026, 10, 19),
                "anchor_count": 1,
            }
        })
        builder = _builder_with_store(store)

        # 增量路径：start=today=10-20, end=10-20+14=11-3
        start, end = date(2026, 10, 20), date(2026, 11, 3)
        events = builder._map_global_events(
            [_news("英伟达财报发布", "2026-10-20")], start, end
        )

        # 关键断言：无事件产出（过期锚定被 `if catalyst_date < start: continue` 淘汰）
        self.assertEqual(events, [])
        # touch 被调用（身份裁决路径），但无 upsert（不重锚）
        self.assertEqual(store.touches, [ikey])
        self.assertEqual(store.upserts, [])


class TestCalibrationPureHelpers(unittest.TestCase):
    """用例 8：calibration 脚本的 identity 聚合纯函数（无 DB，同 forensics 测试范式）"""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts", "calibrate_catalyst_value_tier.py",
        )
        spec = importlib.util.spec_from_file_location("ccvt_calib_under_test", path)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    @staticmethod
    def _row(sector, title, day, excess_d1, direction_correct):
        return {
            "sector": sector, "title": title, "event_date": day,
            "excess_d1": excess_d1, "excess_d3": None, "excess_d5": None,
            "direction": "positive", "direction_correct": direction_correct,
        }

    def test_identity_dup_stats_groups_and_splits_first_vs_repeat(self):
        rows = [
            self._row("CPO", "英伟达财报发布", date(2026, 10, 1), 1.0, True),
            self._row("CPO", "英伟达 财报发布！", date(2026, 10, 2), 3.0, False),  # 归一化后同身份
            self._row("CPO", "英伟达财报发布", date(2026, 10, 3), 5.0, False),
            self._row("存储芯片", "SK海力士财报发布", date(2026, 10, 1), 2.0, True),
        ]
        stats = self.mod._identity_dup_stats(rows)

        self.assertEqual(stats["total_identities"], 2)
        self.assertEqual(stats["dist"], {"2-3": 1, "1": 1})
        self.assertAlmostEqual(stats["mean_rows_per_identity"], 2.0)
        # 首次行 = 两个身份的最早行；重复行 = 另一身份的 2 行
        self.assertEqual(stats["first_occurrence"]["n"], 2)
        self.assertAlmostEqual(stats["first_occurrence"]["d1_mean"], 1.5)
        self.assertEqual(stats["repeat"]["n"], 2)
        self.assertAlmostEqual(stats["repeat"]["d1_mean"], 4.0)

    def test_first_vs_repeat_matches_identity_stats(self):
        rows = [
            self._row("CPO", "A 事件", date(2026, 10, 1), 1.0, True),
            self._row("CPO", "A 事件", date(2026, 10, 2), 2.0, False),
        ]
        first, repeat = self.mod._first_vs_repeat(rows)
        self.assertEqual(first["n"], 1)
        self.assertAlmostEqual(first["d1_mean"], 1.0)
        self.assertEqual(repeat["n"], 1)
        self.assertAlmostEqual(repeat["d1_mean"], 2.0)

    def test_normalize_title_ignores_whitespace_and_punctuation(self):
        norm = self.mod._normalize_title
        self.assertEqual(norm("英伟达 财报发布！"), norm("英伟达财报发布"))


if __name__ == "__main__":
    unittest.main()
