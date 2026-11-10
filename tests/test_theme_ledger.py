# -*- coding: utf-8 -*-
"""P1 主题观察账本：分段/确认/缺档/变体/容量/确定性单测。

治理口径（实施稿 §4/§8-P1）：
- state 只作观测特征，不参与分段；
- 缺档日只增 age、不推进确认计数；
- observation 不等于 buy；setup 只有复评资格；
- 同日重跑/重建结果一致。
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from src.got.theme_ledger import (
    Observation,
    build_observations,
    build_snapshot,
    build_theme_watch,
    derive_episodes,
    derive_setups,
    membership_from_sector_ctx,
    snapshot_digest,
    state_rows_from_sector_ctx,
)

CAL = [f"2026-10-{d:02d}" for d in range(8, 32)]


def obs(day: str, strength: float | None, *, code: str = "880001", state: str = "neutral",
        fund_1d: float | None = None, trend_broken: bool | None = None) -> Observation:
    return Observation(
        trade_date=day, board_code=code, board_name="测试板块", theme_id=code,
        strength=strength, state=state, fund_1d=fund_1d,
        fund_status="known" if fund_1d is not None else "unknown",
        trend_broken=trend_broken, trend_status="ok" if trend_broken is not None else "unknown",
    )


class TestEpisodes(unittest.TestCase):
    def test_enter_pending_recover_and_confirm(self):
        rows = [
            obs("2026-10-08", 81.0),
            obs("2026-10-09", 78.0),
            obs("2026-10-10", 74.0),   # pending-break 1
            obs("2026-10-12", 79.0),   # 回到阈值上方 → pending 清零，段继续
            obs("2026-10-13", 74.0),   # pending 1
            obs("2026-10-14", 73.0),   # pending 2
            obs("2026-10-15", 72.0),   # pending 3 → closed
        ]
        episodes, index = derive_episodes(rows, CAL)
        self.assertEqual(len(episodes), 1)
        ep = episodes[0]
        self.assertEqual(ep.start_date, "2026-10-08")
        self.assertEqual(ep.break_date, "2026-10-13")
        self.assertEqual(ep.confirm_date, "2026-10-15")
        self.assertEqual(ep.end_date, "2026-10-12")   # 破位前最后一个观测
        self.assertEqual(ep.status, "closed")
        self.assertEqual(ep.peak_strength, 81.0)
        self.assertEqual(index[("880001", "2026-10-12")]["status"], "open")

    def test_gap_advances_age_but_not_confirm_clock(self):
        rows = [
            obs("2026-10-08", 85.0),
            obs("2026-10-09", 70.0),   # pending 1
            obs("2026-10-12", 70.0),   # 缺 10-10/10-11（非交易日/缺档）→ pending 2
        ]
        episodes, index = derive_episodes(rows, CAL)
        ep = episodes[0]
        self.assertEqual(ep.pending_break_days, 2)
        self.assertEqual(ep.status, "pending_break")
        age = index[("880001", "2026-10-12")]["age_days"]
        self.assertEqual(age, CAL.index("2026-10-12") - CAL.index("2026-10-08") + 1)
        self.assertEqual(ep.gaps, 2)   # 10-10/10-11 在日历中存在但无观测 → 计缺档

    def test_state_does_not_affect_segmentation(self):
        a = [obs("2026-10-08", 81.0, state="neutral"), obs("2026-10-09", 76.0, state="defensive_rotation")]
        b = [obs("2026-10-08", 81.0, state="active_mainline"), obs("2026-10-09", 76.0, state="ambush")]
        ea, ia = derive_episodes(a, CAL)
        eb, ib = derive_episodes(b, CAL)
        self.assertEqual([e.as_dict(CAL, "2026-10-09") for e in ea],
                         [e.as_dict(CAL, "2026-10-09") for e in eb])
        self.assertEqual(ia, ib)

    def test_below_threshold_without_prior_enter_creates_nothing(self):
        episodes, _ = derive_episodes([obs("2026-10-08", 79.9)], CAL)
        self.assertEqual(episodes, [])

    def test_second_episode_after_close(self):
        rows = [
            obs("2026-10-08", 81.0),
            obs("2026-10-09", 70.0), obs("2026-10-10", 70.0), obs("2026-10-12", 70.0),
            obs("2026-10-13", 82.0),
        ]
        episodes, _ = derive_episodes(rows, CAL)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[1].start_date, "2026-10-13")


class TestSetups(unittest.TestCase):
    def setUp(self):
        self.rows = [
            obs("2026-10-08", 85.0, fund_1d=1.0),
            obs("2026-10-09", 84.0, fund_1d=-2.0),
        ]
        self.episodes, self.index = derive_episodes(self.rows, CAL)

    def test_variants_mark_armed_unarmed_and_unknown(self):
        setups = derive_setups(self.rows, self.index, CAL)
        by = {(s.fired_date, s.setup_type): s for s in setups}
        self.assertEqual(by[("2026-10-08", "fund_1d")].status, "armed")
        self.assertEqual(by[("2026-10-09", "fund_1d")].status, "unarmed")
        self.assertEqual(by[("2026-10-08", "none")].status, "armed")
        # fund_3d 在样本中缺失 → unknown，不并入 armed/unarmed
        self.assertEqual(by[("2026-10-08", "fund_3d")].status, "unknown")
        # 观测级 fund_state 不随变体翻转；变体级单独记
        self.assertEqual(by[("2026-10-08", "fund_3d")].fund_state, "known")
        self.assertEqual(by[("2026-10-08", "fund_3d")].variant_fund_state, "unknown")
        self.assertEqual(by[("2026-10-08", "fund_1d")].variant_fund_state, "known")

    def test_trend_broken_invalidates(self):
        rows = [
            obs("2026-10-08", 85.0, fund_1d=1.0, trend_broken=True),
        ]
        episodes, index = derive_episodes(rows, CAL)
        setups = derive_setups(rows, index, CAL)
        self.assertTrue(all(s.status == "invalidated" for s in setups))
        self.assertTrue(all(s.invalid_reason == "trend_broken" for s in setups))

    def test_age_limit_blocks_setup(self):
        rows = [obs("2026-10-08", 85.0, fund_1d=1.0)] + [
            obs(f"2026-10-{d:02d}", 85.0, fund_1d=1.0) for d in range(9, 15)
        ]
        episodes, index = derive_episodes(rows, CAL)
        setups = derive_setups(rows, index, CAL)
        fired = {s.fired_date for s in setups}
        self.assertIn("2026-10-09", fired)      # age=2
        self.assertNotIn("2026-10-10", fired)   # age=3 > max_age

    def test_expiry_uses_trading_calendar(self):
        setups = derive_setups(self.rows, self.index, CAL)
        first = min(setups, key=lambda s: s.fired_date)
        self.assertEqual(first.expiry_date, CAL[CAL.index(first.fired_date) + 5])


class TestWatchAndSnapshot(unittest.TestCase):
    def test_watch_capacity_and_dropped_reasons(self):
        rows = []
        episodes = []
        for i in range(25):
            code = f"88{i:04d}"
            row = obs("2026-10-08", 85.0, code=code)
            rows.append(row)
        episodes, index = derive_episodes(rows, CAL)
        watch = build_theme_watch(episodes, [], rows, CAL, "2026-10-08", capacity=20)
        self.assertEqual(watch["observation_count"], 25)
        self.assertEqual(watch["watch_count"], 20)
        self.assertEqual(len(watch["dropped"]), 5)
        self.assertTrue(all(d["reason"] == "capacity" for d in watch["dropped"]))

    def test_snapshot_only_contains_as_of_rows_and_is_deterministic(self):
        rows = [
            obs("2026-10-08", 85.0, fund_1d=1.0),
            obs("2026-10-09", 84.0, fund_1d=1.0),
        ]
        episodes, index = derive_episodes(rows, CAL)
        setups = derive_setups(rows, index, CAL)
        watch = build_theme_watch(episodes, setups, rows, CAL, "2026-10-08")
        snap = build_snapshot(
            "2026-10-08", observations=rows, episodes=episodes, setups=setups,
            watch=watch, calendar=CAL, mode="shadow", run_id="r1",
        )
        self.assertEqual(len(snap["observations"]), 1)
        self.assertTrue(all(s["fired_date"] == "2026-10-08" for s in snap["setups"]))
        self.assertFalse(snap["invariants"]["state_used_for_segmentation"])
        self.assertFalse(snap["invariants"]["observation_can_create_buy"])
        snap2 = build_snapshot(
            "2026-10-08", observations=rows, episodes=episodes, setups=setups,
            watch=watch, calendar=CAL, mode="shadow", run_id="r1",
        )
        self.assertEqual(snapshot_digest(snap), snapshot_digest(snap2))

    def test_observation_flags_separate_state_from_output_bucket(self):
        rows = [{
            "sector_code": "880999", "sector_name": "短剧游戏",
            "strength_score": 86.0, "state": "ambush",
        }]
        observations, diag = build_observations(
            "2026-10-29", state_rows=rows,
            membership={"880999": {"top": True}},   # 内部 ambush，但不在 ambush 输出桶
        )
        o = observations[0]
        self.assertEqual(o.state, "ambush")
        self.assertTrue(o.in_top_output)
        self.assertFalse(o.in_ambush_output)
        self.assertEqual(diag["observations"], 1)

    def test_unmapped_output_boards_are_counted_not_guessed(self):
        observations, diag = build_observations(
            "2026-10-29", state_rows=[],
            membership={"某无码板块": {"active": True}},
        )
        self.assertEqual(observations, [])
        self.assertEqual(diag["unmapped_output_count"], 1)


class TestStore(unittest.TestCase):
    """存储层回归：同日重跑不追加、JSON 列表 round-trip、as-of 读取无前视。"""

    def setUp(self):
        import tempfile

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from src.storage import Base

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = Path(self._tmp.name) / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        self._session_factory = sessionmaker(bind=engine)
        self._engine = engine

        class _DB:
            def __init__(self, factory):
                self._factory = factory

            def get_session(self):
                return self._factory()

        from src.got.theme_ledger import ThemeLedgerStore

        self.store = ThemeLedgerStore(db=_DB(self._session_factory))

    def tearDown(self):
        self._engine.dispose()

    def _fixture(self):
        rows = [
            obs("2026-10-08", 85.0, fund_1d=1.5),
            obs("2026-10-09", 84.0, fund_1d=1.0),
        ]
        rows[0] = Observation(
            **{**rows[0].__dict__,
               "news_event_ids": ("news:1", "news:2"),
               "catalyst_event_ids": ("cat:9",)}
        )
        episodes, index = derive_episodes(rows, CAL)
        setups = derive_setups(rows, index, CAL)
        return rows, episodes, setups

    def test_persist_day_is_idempotent(self):
        from src.storage import ThemeEpisodeRecord, ThemeObservationRecord, ThemeSetupRecord

        rows, episodes, setups = self._fixture()
        first = self.store.persist_day(rows, episodes, setups, "2026-10-08")
        second = self.store.persist_day(rows, episodes, setups, "2026-10-08")
        self.assertEqual(first["observations"], second["observations"])
        session = self._session_factory()
        try:
            self.assertEqual(session.query(ThemeObservationRecord).count(), 1)
            self.assertEqual(session.query(ThemeEpisodeRecord).count(), 1)
            # 只写 <= 当日：10-09 的 setup 不得提前落库（无前视）
            same_day = [x for x in setups if x.fired_date == "2026-10-08"]
            self.assertEqual(session.query(ThemeSetupRecord).count(), len(same_day))
            self.assertTrue(all(
                r.fired_date.isoformat() <= "2026-10-08"
                for r in session.query(ThemeSetupRecord)
            ))
            keys = {(r.board_code, r.trade_date) for r in session.query(ThemeObservationRecord)}
            self.assertEqual(len(keys), 1)
        finally:
            session.close()

    def test_round_trip_preserves_json_lists(self):
        rows, episodes, setups = self._fixture()
        self.store.persist_day(rows, episodes, setups, "2026-10-08")
        loaded = self.store.load_observations("2026-10-08")
        self.assertEqual(len(loaded), 1)
        item = loaded[0]
        self.assertEqual(item["news_event_ids"], ["news:1", "news:2"])
        self.assertEqual(item["catalyst_event_ids"], ["cat:9"])
        self.assertAlmostEqual(item["fund_1d"], 1.5)

    def test_load_snapshot_offset_never_reads_future(self):
        import json

        from src.got.theme_ledger import load_snapshot

        base = Path(self._tmp.name) / "got_daily"
        for day, marker in (("2026-10-08", "d08"), ("2026-10-09", "d09")):
            path = base / day.replace("-", "") / "theme_ledger.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"trade_date": day, "marker": marker}), encoding="utf-8")
        cal = ["2026-10-08", "2026-10-09"]
        self.assertEqual(
            load_snapshot(date(2026, 10, 9), calendar=cal, base_dir=base)["marker"], "d09"
        )
        self.assertEqual(
            load_snapshot(date(2026, 10, 9), offset_days=1, calendar=cal, base_dir=base)["marker"],
            "d08",
        )
        # 第一日读 T-1：无更早快照 → None（不得回落到未来）
        self.assertIsNone(
            load_snapshot(date(2026, 10, 8), offset_days=1, calendar=cal, base_dir=base)
        )


class TestWatchShadow(unittest.TestCase):
    """P2-b trace 投影：只读、无交易字段、挂载失败 fail-open、渲染不进转正时钟。"""

    def _snapshot(self) -> dict:
        return {
            "trade_date": "2026-10-29",
            "mode": "shadow",
            "theme_watch": {
                "observation_count": 2,
                "watch_count": 2,
                "entries": [
                    {"board_code": "880692", "board_name": "短剧游戏", "episode_id": "e1",
                     "age_days": 2, "status": "open", "peak_strength": 84.4, "strength": 84.4,
                     "state": "ambush", "in_top_output": True,
                     "setup": {"none": "armed"}},
                    {"board_code": "880675", "board_name": "减速器", "episode_id": "e2",
                     "age_days": 4, "status": "open", "peak_strength": 95.4, "strength": 95.4,
                     "state": "neutral", "in_top_output": True, "setup": {}},
                ],
                "dropped": [{"board_code": "x", "reason": "capacity"}],
            },
        }

    def test_payload_has_no_trade_fields_and_caps_entries(self):
        from src.got.theme_ledger import watch_shadow_payload

        snap = self._snapshot()
        snap["theme_watch"]["entries"] = snap["theme_watch"]["entries"] * 20
        payload = watch_shadow_payload(snap, max_entries=5)
        self.assertEqual(len(payload["entries"]), 5)
        self.assertEqual(payload["dropped_count"], 1)
        self.assertTrue(payload["not_a_trade_signal"])
        blob = json.dumps(payload, ensure_ascii=False)
        for forbidden in ('"buy"', '"sell"', '"weight"', '"action"', '"quantity"'):
            self.assertNotIn(forbidden, blob)

    def test_attach_sets_field_and_respects_mode(self):
        import os
        import tempfile

        from src.got.schemas import UnifiedContext
        from src.got.theme_ledger import attach_theme_watch_shadow

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        path = base / "20261029" / "theme_ledger.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self._snapshot(), ensure_ascii=False), encoding="utf-8")

        old = os.environ.get("GOT_THEME_LEDGER")
        try:
            os.environ["GOT_THEME_LEDGER"] = "off"
            unified = UnifiedContext(
                status="normal", unified_signal="neutral",
                overall_confidence=0.6, final_position_limit=0.5,
            )
            self.assertIsNone(attach_theme_watch_shadow(
                unified, date(2026, 10, 29), base_dir=base))
            self.assertIsNone(unified.theme_watch_shadow)

            os.environ["GOT_THEME_LEDGER"] = "shadow"
            payload = attach_theme_watch_shadow(unified, date(2026, 10, 29), base_dir=base)
            self.assertIsNotNone(payload)
            self.assertEqual(unified.theme_watch_shadow["entries"][0]["board_code"], "880692")
            # 除新字段外，其余字段不受影响
            base_ctx = UnifiedContext(
                status="normal", unified_signal="neutral",
                overall_confidence=0.6, final_position_limit=0.5,
            )
            dumped = unified.model_dump()
            base_dump = base_ctx.model_dump()
            # 只允许新字段与带时间戳的 data_quality 不同（其余决策字段必须逐值一致）
            diff = {k for k in dumped if dumped[k] != base_dump.get(k)}
            self.assertTrue(diff <= {"theme_watch_shadow", "data_quality"}, diff)
            self.assertEqual(dumped["status"], base_dump["status"])
            self.assertEqual(dumped["overall_confidence"], base_dump["overall_confidence"])
            self.assertEqual(dumped["final_position_limit"], base_dump["final_position_limit"])
            self.assertEqual(dumped["confirmed_sectors"], base_dump["confirmed_sectors"])
        finally:
            if old is None:
                os.environ.pop("GOT_THEME_LEDGER", None)
            else:
                os.environ["GOT_THEME_LEDGER"] = old

    def test_attach_missing_snapshot_is_fail_open(self):
        import os

        from src.got.schemas import UnifiedContext
        from src.got.theme_ledger import attach_theme_watch_shadow

        old = os.environ.get("GOT_THEME_LEDGER")
        try:
            os.environ["GOT_THEME_LEDGER"] = "shadow"
            unified = UnifiedContext(
                status="normal", unified_signal="neutral",
                overall_confidence=0.6, final_position_limit=0.5,
            )
            self.assertIsNone(attach_theme_watch_shadow(unified, date(2026, 10, 29),
                                                       base_dir=Path("nonexistent_dir")))
            self.assertIsNone(unified.theme_watch_shadow)
        finally:
            if old is None:
                os.environ.pop("GOT_THEME_LEDGER", None)
            else:
                os.environ["GOT_THEME_LEDGER"] = old

    def test_renderer_flags_stale_as_of(self):
        from src.got.archive import _render_shadow_theme_watch

        unified = {"theme_watch_shadow": {
            "as_of": "2026-10-29", "observation_count": 1, "watch_count": 1,
            "dropped_count": 0, "entries": [],
        }}
        self.assertNotIn("展示 T-1", "\n".join(_render_shadow_theme_watch(
            unified, run_date=date(2026, 10, 29))))
        self.assertIn("当日快照缺失", "\n".join(_render_shadow_theme_watch(
            unified, run_date=date(2026, 10, 30))))

    def test_render_real_history_snapshot(self):
        """真实 10-29 快照 → 投影 → 渲染：验证 archive 实际消费的序列化形态。"""
        from src.got.archive import _render_shadow_theme_watch
        from src.got.theme_ledger import watch_shadow_payload

        path = Path("data/got_daily/theme_ledger_history/archive/theme_ledger_20261029.json")
        if not path.exists():
            self.skipTest("缺 10-29 历史快照")
        payload = watch_shadow_payload(json.loads(path.read_text(encoding="utf-8")))
        text = "\n".join(_render_shadow_theme_watch(
            {"theme_watch_shadow": payload}, run_date=date(2026, 10, 29)))
        self.assertIn("短剧游戏", text)
        self.assertIn("不参与评分", text)

    def test_archive_renderer_lists_entries_and_skips_when_absent(self):
        from src.got.archive import _render_shadow_theme_watch

        self.assertEqual(_render_shadow_theme_watch({"theme_watch_shadow": None}), [])
        lines = _render_shadow_theme_watch({"theme_watch_shadow": {
            "as_of": "2026-10-29", "observation_count": 2, "watch_count": 2,
            "dropped_count": 0, "entries": [{"board_name": "短剧游戏", "board_code": "880692",
                                             "age_days": 2, "strength": 84.4, "status": "open",
                                             "in_top_output": True}],
        }})
        text = "\n".join(lines)
        self.assertIn("短剧游戏", text)
        self.assertIn("不参与评分", text)


class TestRuntimeIntegration(unittest.TestCase):
    """生产路径端到端（隔离 DB + 临时归档根）：写库/落快照/重跑幂等/挂载/渲染。"""

    def setUp(self):
        import tempfile

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from src.storage import Base

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_dir = Path(self._tmp.name) / "got_daily"
        engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'it.db'}")
        Base.metadata.create_all(engine)
        self._engine = engine
        factory = sessionmaker(bind=engine)

        class _DB:
            def __init__(self, f):
                self._f = f

            def get_session(self):
                return self._f()

        self.db = _DB(factory)

        from src.storage import Node2SectorTrendRecord

        session = factory()
        try:
            for code, name, strength, state in (
                ("880692", "短剧游戏", 84.4, "ambush"),
                ("880675", "减速器", 95.4, "neutral"),
            ):
                session.add(Node2SectorTrendRecord(
                    sector_code=code, sector_name=name, tracking_date=date(2026, 10, 29),
                    strength_score=strength, state=state, is_mainline=False,
                    mainline_days=0, ambush_days=1, dwell_days=1,
                ))
            session.commit()
        finally:
            session.close()

    def tearDown(self):
        self._engine.dispose()

    def test_build_persist_reload_attach_and_render(self):
        import os

        from src.got.archive import _render_shadow_theme_watch
        from src.got.schemas import UnifiedContext
        from src.got.theme_ledger import (
            attach_theme_watch_shadow,
            build_and_persist,
            load_snapshot,
        )

        old = os.environ.get("GOT_THEME_LEDGER")
        os.environ["GOT_THEME_LEDGER"] = "shadow"
        try:
            snap1 = build_and_persist(
                date(2026, 10, 29), db=self.db, base_dir=self.base_dir, run_id="it-1"
            )
            self.assertIsNotNone(snap1)
            self.assertEqual(len(snap1["observations"]), 2)
            path = self.base_dir / "20261029" / "theme_ledger.json"
            self.assertTrue(path.exists())
            # 短剧：内部 ambush、无输出桶成员（本用例 membership 为空）
            case = next(o for o in snap1["observations"] if o["board_code"] == "880692")
            self.assertEqual(case["state"], "ambush")
            self.assertFalse(case["in_ambush_output"])

            reloaded = load_snapshot(date(2026, 10, 29), base_dir=self.base_dir)
            self.assertEqual(reloaded["trade_date"], "2026-10-29")

            unified = UnifiedContext(
                status="normal", unified_signal="neutral",
                overall_confidence=0.6, final_position_limit=0.5,
            )
            payload = attach_theme_watch_shadow(
                unified, date(2026, 10, 29), base_dir=self.base_dir
            )
            self.assertIsNotNone(payload)
            text = "\n".join(_render_shadow_theme_watch(
                {"theme_watch_shadow": payload}, run_date=date(2026, 10, 29)
            ))
            self.assertIn("短剧游戏", text)

            # 同日重跑：行数不翻倍
            snap2 = build_and_persist(
                date(2026, 10, 29), db=self.db, base_dir=self.base_dir, run_id="it-2"
            )
            self.assertEqual(len(snap2["observations"]), 2)
            from src.storage import ThemeObservationRecord

            session = self.db.get_session()
            try:
                self.assertEqual(session.query(ThemeObservationRecord).count(), 2)
            finally:
                session.close()
        finally:
            if old is None:
                os.environ.pop("GOT_THEME_LEDGER", None)
            else:
                os.environ["GOT_THEME_LEDGER"] = old


class TestRuntimeAdapters(unittest.TestCase):
    """回归：归档 SectorContext 的字段名是 code/name/state，不是 sector_code/board_state。

    该错配曾让观察层在真实归档上静默归零；此处直接吃真实归档 JSON 防止漂移。
    """

    SECTOR_JSON = Path("data/got_daily/20261029/sector.json")

    @unittest.skipUnless(SECTOR_JSON.exists(), "缺 20261029 归档，跳过真实上下文回归")
    def test_archived_context_maps_codes_states_and_buckets(self):
        from src.got.schemas import SectorContext

        ctx = SectorContext.model_validate(
            json.loads(self.SECTOR_JSON.read_text(encoding="utf-8"))
        )
        rows = state_rows_from_sector_ctx(ctx)
        membership = membership_from_sector_ctx(ctx)
        self.assertGreater(len(rows), 20)
        self.assertGreater(len(membership), 20)
        by_code = {r["sector_code"]: r for r in rows}
        self.assertEqual(by_code["880692"]["state"], "ambush")
        self.assertTrue(membership["880692"]["top"])
        self.assertFalse(membership["880692"].get("ambush"))

        observations, _ = build_observations(
            "2026-10-29", state_rows=rows, membership=membership
        )
        case = next(o for o in observations if o.board_code == "880692")
        self.assertEqual(case.state, "ambush")
        self.assertTrue(case.in_top_output)
        self.assertFalse(case.in_ambush_output)
        self.assertAlmostEqual(case.strength, 84.4)


if __name__ == "__main__":
    unittest.main()
