# -*- coding: utf-8 -*-
"""
Node 6 单元测试：埋伏 E14 出场催化宽限影子（2026-10-16 补登 #6，零决策改动观测线）。

覆盖（T1-T8）：
1. E14 转出（前日 ambush→今日 neutral）+ 窗口内合格催化 → grace_would_hold=True
2. 无催化事件 → 候选仍记录，grace_would_hold=False
3. 超窗催化（>10 交易日）→ 不触发 hold
4. negative 方向事件被排除（qualified_event_count=0）
5. E12b 飞刀（mom<0.2 且 strength<25）不属宽限对象
6. 零决策不变式：返回 None、输入对象零改动（日历事件列表原样）
7. DB 失败 → warning + 空样本落盘（grace_candidates_count=0，漏记不误记）
8. 过去事件（td=0）排除 + 非 ambush→neutral 转出不产候选
"""

import json
import os
import shutil
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.got.nodes.aggregator as ag


# 2000-01-03 是周一（weekend-only 口径下交易日计数可推）
_FIXED_TODAY = date(2000, 1, 3)
_TEST_DAY_DIR = Path("data/got_daily/20000103")
_SHADOW_FILE = _TEST_DAY_DIR / "shadow_ambush_exit_grace.json"


def _row(code, name, state, prev_date, dwell=1, mom=0.4, strength=40.0):
    return SimpleNamespace(
        sector_code=code, sector_name=name, tracking_date=prev_date,
        state=state, dwell_days=dwell, rrg_momentum_ema=mom, strength_score=strength,
    )


def _event(event_id, title, event_date, sector="低空经济", confidence=0.8,
           direction="positive", affected_sectors=None):
    from src.got.schemas import CatalystEvent
    return CatalystEvent(
        event_id=event_id, event_date=event_date, event_type="narrative",
        title=title, sector=sector, confidence=confidence, direction=direction,
        consensus_expectation="预期", source="llm_narrative",
        affected_sectors=affected_sectors or [sector],
    )


def _fake_db(prev_rows, today_rows):
    """构造 DatabaseManager 替身：get_session() → query 链两次 .all()（先前日、后当日）。"""
    db = MagicMock()
    session = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.all.side_effect = [prev_rows, today_rows]
    session.query.return_value = q
    cm = MagicMock()
    cm.__enter__.return_value = session
    cm.__exit__.return_value = False
    db.get_session.return_value = cm
    return db


def _news_with_events(events):
    if not events:
        return SimpleNamespace(catalyst_calendar=None)
    from src.got.schemas import CatalystCalendar
    return SimpleNamespace(catalyst_calendar=CatalystCalendar(target_date=event_date_max(events), events=events))


def event_date_max(events):
    return max((e.event_date for e in events), default=_FIXED_TODAY)


class _RecordingDB:
    """get_session 抛异常的 DB 替身（T7：DB 失败 → 空样本落盘）。"""

    def get_session(self):
        raise RuntimeError("db down")


class TestAmbushExitGraceShadow(unittest.TestCase):
    """埋伏 E14 出场催化宽限影子。"""

    def setUp(self):
        if _TEST_DAY_DIR.is_dir():
            shutil.rmtree(_TEST_DAY_DIR)
        elif _TEST_DAY_DIR.exists():
            _TEST_DAY_DIR.unlink()
        self.node = ag.AggregatorNode.__new__(ag.AggregatorNode)
        self.node._run_date = _FIXED_TODAY

    def tearDown(self):
        if _TEST_DAY_DIR.is_dir():
            shutil.rmtree(_TEST_DAY_DIR)
        elif _TEST_DAY_DIR.exists():
            _TEST_DAY_DIR.unlink()

    def _run_shadow(self, prev_rows, today_rows, events, news=None):
        """统一驱动：patch DB + 固定日期，调 _compute_ambush_exit_grace_shadow。"""
        news = news or _news_with_events(events)
        with patch("src.storage.DatabaseManager",
                   return_value=_fake_db(prev_rows, today_rows)):
            self.node._compute_ambush_exit_grace_shadow(None, news)
        return json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))

    # T1 ─────────────────────────────────────────────────────────
    def test_t1_e14_exit_with_in_window_catalyst_holds(self):
        """前日 ambush→今日 neutral + 4 交易日内合格催化 → grace_would_hold=True。"""
        prev = [_row("BK01", "低空经济", "ambush", date(2000, 1, 1), dwell=3)]
        today = [_row("BK01", "低空经济", "neutral", _FIXED_TODAY, mom=0.4, strength=40.0)]
        events = [_event("E1", "低空经济展会", date(2000, 1, 6))]
        record = self._run_shadow(prev, today, events)
        self.assertEqual(record["grace_candidates_count"], 1)
        r = record["records"][0]
        self.assertEqual(r["sector_code"], "BK01")
        self.assertEqual(r["prev_state"], "ambush")
        self.assertEqual(r["today_state"], "neutral")
        self.assertTrue(r["e14_only"])
        self.assertTrue(r["grace_would_hold"])
        self.assertEqual(r["nearest_event"]["trade_days_away"], 4)
        self.assertEqual(r["nearest_event"]["event_id"], "E1")
        self.assertEqual(record["window_days"], 10)

    # T2 ─────────────────────────────────────────────────────────
    def test_t2_no_events_candidate_still_recorded(self):
        """无催化事件：候选仍记录（观测转出本身），grace_would_hold=False。"""
        prev = [_row("BK01", "低空经济", "ambush", date(2000, 1, 1))]
        today = [_row("BK01", "低空经济", "neutral", _FIXED_TODAY)]
        record = self._run_shadow(prev, today, [])
        self.assertEqual(record["grace_candidates_count"], 1)
        r = record["records"][0]
        self.assertFalse(r["grace_would_hold"])
        self.assertIsNone(r["nearest_event"])
        self.assertEqual(r["qualified_event_count"], 0)

    # T3 ─────────────────────────────────────────────────────────
    def test_t3_out_of_window_catalyst_not_hold(self):
        """超窗催化（21 交易日后）→ 不触发 hold。"""
        prev = [_row("BK01", "低空经济", "ambush", date(2000, 1, 1))]
        today = [_row("BK01", "低空经济", "neutral", _FIXED_TODAY)]
        events = [_event("E1", "远期事件", date(2000, 1, 31))]
        record = self._run_shadow(prev, today, events)
        r = record["records"][0]
        self.assertEqual(r["qualified_event_count"], 1)  # 事件合格但不入窗
        self.assertFalse(r["grace_would_hold"])
        self.assertIsNone(r["nearest_event"])

    # T4 ─────────────────────────────────────────────────────────
    def test_t4_negative_direction_excluded(self):
        """negative 方向事件被排除（宽限只看非负面催化）。"""
        prev = [_row("BK01", "低空经济", "ambush", date(2000, 1, 1))]
        today = [_row("BK01", "低空经济", "neutral", _FIXED_TODAY)]
        events = [_event("E1", "利空事件", date(2000, 1, 6), direction="negative")]
        record = self._run_shadow(prev, today, events)
        r = record["records"][0]
        self.assertEqual(r["qualified_event_count"], 0)
        self.assertFalse(r["grace_would_hold"])

    # T5 ─────────────────────────────────────────────────────────
    def test_t5_e12b_knife_excluded(self):
        """E12b 飞刀（mom<0.2 且 strength<25）硬退出，不属宽限对象。"""
        prev = [_row("BK01", "低空经济", "ambush", date(2000, 1, 1))]
        today = [_row("BK01", "低空经济", "neutral", _FIXED_TODAY, mom=0.1, strength=20.0)]
        events = [_event("E1", "展会", date(2000, 1, 6))]
        record = self._run_shadow(prev, today, events)
        self.assertEqual(record["grace_candidates_count"], 0)
        self.assertEqual(record["records"], [])

    # T6 ─────────────────────────────────────────────────────────
    def test_t6_zero_decision_invariant(self):
        """零决策不变式：返回 None；输入对象零改动（事件列表原样、无属性新增）。"""
        prev = [_row("BK01", "低空经济", "ambush", date(2000, 1, 1))]
        today = [_row("BK01", "低空经济", "neutral", _FIXED_TODAY)]
        events = [_event("E1", "展会", date(2000, 1, 6))]
        news = _news_with_events(events)
        events_snapshot = list(events)
        ret = self.node._compute_ambush_exit_grace_shadow(
            None, news) if False else None
        # 直接调用（DB patch 下），校验返回值与输入不变性
        with patch("src.storage.DatabaseManager",
                   return_value=_fake_db(prev, today)):
            result = self.node._compute_ambush_exit_grace_shadow(None, news)
        self.assertIsNone(result)
        self.assertEqual(news.catalyst_calendar.events, events_snapshot)
        self.assertIs(news.catalyst_calendar.events[0], events[0])
        self.assertFalse(hasattr(news, "_grace_touched"))

    # T7 ─────────────────────────────────────────────────────────
    def test_t7_db_failure_empty_sample_written(self):
        """DB 失败 → warning + 空样本落盘（grace_candidates_count=0，漏记不误记）。"""
        with patch("src.storage.DatabaseManager", return_value=_RecordingDB()):
            self.node._compute_ambush_exit_grace_shadow(None, _news_with_events([]))
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        self.assertEqual(record["grace_candidates_count"], 0)
        self.assertEqual(record["records"], [])
        self.assertTrue(record["switch"])  # 有效样本标记：占位判定 gate（archive 计数用）

    # T8 ─────────────────────────────────────────────────────────
    def test_t8_past_event_and_non_e14_transition_excluded(self):
        """过去事件（td=0）排除；非 ambush→neutral 转出不产候选。"""
        prev = [
            _row("BK01", "低空经济", "ambush", date(2000, 1, 1)),
            _row("BK02", "券商", "ambush", date(2000, 1, 1)),
            _row("BK03", "半导体", "neutral", date(2000, 1, 1)),  # 前日非 ambush
        ]
        today = [
            _row("BK01", "低空经济", "neutral", _FIXED_TODAY),
            _row("BK02", "券商", "neutral", _FIXED_TODAY),
            _row("BK03", "半导体", "neutral", _FIXED_TODAY),  # 前日 neutral→今日 neutral 非转出
        ]
        events = [_event("E1", "昨日事件", date(1999, 12, 31), sector="低空经济")]
        record = self._run_shadow(prev, today, events)
        codes = {r["sector_code"] for r in record["records"]}
        self.assertEqual(codes, {"BK01", "BK02"})  # BK03 前日非 ambush
        bk01 = next(r for r in record["records"] if r["sector_code"] == "BK01")
        self.assertFalse(bk01["grace_would_hold"])  # 过去事件不入窗
        self.assertIsNone(bk01["nearest_event"])


if __name__ == "__main__":
    unittest.main()
