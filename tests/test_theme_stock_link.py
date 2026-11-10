# -*- coding: utf-8 -*-
"""P3-a 个股—主题回链单测：精确匹配、占位行分母、三类可观测失真、无候选扩权。"""

from __future__ import annotations

import unittest

from src.got.theme_ledger import Observation, SetupSignal
from src.got.theme_stock_link import (
    LINK_EXACT,
    attach_forward,
    attach_forward_net,
    forward_excess,
    forward_net,
    forward_session,
    summarize_forward,
    LINK_PLACEHOLDER,
    LINK_QUERY_BOARD,
    LINK_UNMATCHED,
    build_links,
    summarize,
)
from src.got.theme_ledger import Episode

CAL = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26",
       "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]


def _obs(day="2026-10-29", code="880001", name="板块A", strength=85.0, *, ambush=False,
         broken=None, top=True) -> Observation:
    return Observation(
        trade_date=day, board_code=code, board_name=name, theme_id=code,
        strength=strength, state="ambush" if ambush else "neutral",
        in_top_output=top, in_ambush_output=ambush,
        trend_broken=broken, trend_status="ok" if broken is not None else "unknown",
    )


def _ep(code="880001", name="板块A", start="2026-10-20") -> Episode:
    return Episode(episode_id=f"{code}-{start}", theme_id=code, board_code=code,
                   board_name=name, start_date=start, peak_strength=85.0, peak_date=start)


def _setup(code="880001", status="armed", variant="none", day="2026-10-29") -> SetupSignal:
    return SetupSignal(
        setup_id=f"{code}-{variant}-{day}", board_code=code, board_name="板块A",
        theme_id=code, episode_id=f"{code}-2026-10-20", fired_date=day,
        setup_type=variant, age_days=1, fund_state="known", status=status,
    )


def _entry(**over):
    base = {
        "symbol": "600000", "name": "测试股", "sector_name": "板块A",
        "sector_type": "active_mainline", "is_finalist": False,
        "sector_query_board": "板块A",
    }
    base.update(over)
    return base


class TestLink(unittest.TestCase):
    def test_exact_and_query_board_and_unmatched(self):
        obs = [_obs()]
        links = build_links(
            trade_date="2026-10-29", calendar=CAL,
            candidates=[
                _entry(symbol="600001", name="A", sector_name="板块A"),
                _entry(symbol="600002", name="B", sector_name="", sector_query_board="板块A"),
                _entry(symbol="600003", name="C", sector_name="不存在板块",
                       sector_query_board="也不存在"),
            ],
            observations=obs, episodes=[_ep()], setups=[_setup()],
        )
        self.assertEqual([x.link_status for x in links],
                         [LINK_EXACT, LINK_QUERY_BOARD, LINK_UNMATCHED])
        self.assertEqual(links[0].board_code, "880001")
        self.assertEqual(links[0].episode_id, "880001-2026-10-20")
        self.assertEqual(links[0].age_days, 8)
        self.assertEqual(links[0].setups, {"none": "armed"})
        self.assertIn("unmapped", links[2].flags)

    def test_placeholder_rows_excluded_from_rates(self):
        links = build_links(
            trade_date="2026-10-29", calendar=CAL,
            candidates=[_entry(), {"symbol": "", "name": ""}],
            observations=[_obs()], episodes=[_ep()], setups=[_setup()],
        )
        self.assertEqual(links[1].link_status, LINK_PLACEHOLDER)
        summary = summarize(links)
        self.assertEqual(summary["placeholder_rows"], 1)
        self.assertEqual(summary["candidates_real"], 1)
        self.assertEqual(summary["linked_rate"], 1.0)

    def test_weak_ambush_board_flags(self):
        # ambush 道 + 破位 + 无 armed setup → weak_ambush_board
        links = build_links(
            trade_date="2026-10-29", calendar=CAL,
            candidates=[_entry(sector_type="ambush")],
            observations=[_obs(ambush=True, broken=True)],
            episodes=[_ep()], setups=[_setup(status="invalidated")],
        )
        flags = links[0].flags
        self.assertIn("weak_ambush_board", flags)
        self.assertIn("board_trend_broken", flags)
        self.assertIn("ambush_broken", flags)
        summary = summarize(links)
        self.assertEqual(summary["lanes"]["ambush"]["weak_ambush_board"], 1)

    def test_late_entry_and_strong_follower_flags(self):
        links = build_links(
            trade_date="2026-10-29", calendar=CAL,
            candidates=[_entry(is_finalist=False)],
            observations=[_obs(strength=88.0)],
            episodes=[_ep(start="2026-10-20")], setups=[_setup()],
        )
        self.assertIn("late_entry", links[0].flags)
        self.assertIn("strong_board_follower", links[0].flags)

    def test_lane_falls_back_to_bucket_when_sector_type_missing(self):
        links = build_links(
            trade_date="2026-10-29", calendar=CAL,
            candidates=[_entry(sector_type="")],
            observations=[_obs(ambush=True)],
            episodes=[_ep()], setups=[_setup()],
        )
        self.assertEqual(links[0].lane, "ambush")
        self.assertEqual(links[0].lane_source, "bucket_fallback")
        self.assertEqual(links[0].lane_raw, "")

    def test_board_invisible_flag(self):
        links = build_links(
            trade_date="2026-10-29", calendar=CAL,
            candidates=[_entry()],
            observations=[_obs(top=False, ambush=False)],
            episodes=[_ep()], setups=[_setup()],
        )
        self.assertIn("board_invisible", links[0].flags)

    def test_summary_has_no_trade_keys(self):
        links = build_links(
            trade_date="2026-10-29", calendar=CAL,
            candidates=[_entry()], observations=[_obs()],
            episodes=[_ep()], setups=[_setup()],
        )
        summary = summarize(links)

        def keys(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    yield key
                    yield from keys(value)
            elif isinstance(obj, list):
                for value in obj:
                    yield from keys(value)

        forbidden = {"buy", "sell", "quantity", "weight", "action"}
        self.assertFalse(forbidden & set(keys(summary)))

    def test_no_trade_fields_in_output(self):
        links = build_links(
            trade_date="2026-10-29", calendar=CAL,
            candidates=[_entry()], observations=[_obs()],
            episodes=[_ep()], setups=[_setup()],
        )
        row = links[0].as_dict()
        for forbidden in ("buy", "sell", "quantity", "weight", "action"):
            self.assertNotIn(forbidden, row)


class TestForward(unittest.TestCase):
    CAL_FWD = ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"]

    def test_forward_session_bounds(self):
        self.assertEqual(forward_session(self.CAL_FWD, "2026-10-27", 3), "2026-10-30")
        self.assertIsNone(forward_session(self.CAL_FWD, "2026-11-02", 3))
        self.assertIsNone(forward_session(self.CAL_FWD, "2026-10-31", 1))

    def test_forward_excess_ok_and_unresolved(self):
        stock = {"2026-10-29": 10.0, "2026-11-03": 11.0}
        board = {"2026-10-29": 100.0, "2026-11-03": 102.0}
        ok = forward_excess(trade_date="2026-10-29", horizon=3, calendar=self.CAL_FWD,
                            stock_closes=stock, board_closes=board)
        self.assertEqual(ok["status"], "ok")
        self.assertAlmostEqual(ok["stock_return"], 0.1, places=6)
        self.assertAlmostEqual(ok["board_return"], 0.02, places=6)
        self.assertAlmostEqual(ok["excess"], 0.08, places=6)
        missing = forward_excess(trade_date="2026-10-29", horizon=3, calendar=self.CAL_FWD,
                                 stock_closes=stock, board_closes={})
        self.assertEqual(missing["status"], "unresolved_price")
        future = forward_excess(trade_date="2026-11-02", horizon=3, calendar=self.CAL_FWD,
                                stock_closes=stock, board_closes=board)
        self.assertEqual(future["status"], "unresolved_no_future")

    def test_forward_net_bar_layout_and_limit_up(self):
        cal = ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
               "2026-11-02", "2026-11-03"]
        bars = {
            "2026-10-28": {"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
                           "volume": 1000, "prev_close": 9.8},
            "2026-10-29": {"open": 10.1, "high": 10.3, "low": 10.0, "close": 10.2,
                           "volume": 900, "prev_close": 10.1},
            "2026-10-30": {"open": 10.2, "high": 10.4, "low": 10.1, "close": 10.4,
                           "volume": 800, "prev_close": 10.2},
            "2026-11-02": {"open": 10.4, "high": 10.6, "low": 10.3, "close": 10.5,
                           "volume": 750, "prev_close": 10.4},
            "2026-11-03": {"open": 10.4, "high": 10.6, "low": 10.3, "close": 10.6,
                           "volume": 700, "prev_close": 10.4},
        }
        ok = forward_net(trade_date="2026-10-27", horizon=3, calendar=cal,
                         symbol="600000", stock_bars=bars)
        self.assertEqual(ok["status"], "ok")
        self.assertEqual(ok["entry_date"], "2026-10-28")
        self.assertEqual(ok["exit_date"], "2026-10-30")
        self.assertGreater(ok["net_return"], 0)
        # 次日开盘即涨停（前收 9.8 → 10.78）→ 不可买
        limit_bars = {**bars, "2026-10-28": {"open": 10.78, "high": 10.78, "low": 10.78,
                                             "close": 10.78, "volume": 1000, "prev_close": 9.8}}
        blocked = forward_net(trade_date="2026-10-27", horizon=3, calendar=cal,
                              symbol="600000", stock_bars=limit_bars)
        self.assertEqual(blocked["status"], "unresolved_entry")
        self.assertEqual(blocked["reason"], "limit_up")
        with self.assertRaises(ValueError):
            forward_net(trade_date="2026-10-27", horizon=1, calendar=cal,
                        symbol="600000", stock_bars=bars)

    def test_attach_forward_and_summary(self):
        rows = [
            {"trade_date": "2026-10-29", "symbol": "600000", "board_code": "880001",
             "lane": "mainline", "flags": [], "is_finalist": True},
            {"trade_date": "2026-10-29", "symbol": "600001", "board_code": "880001",
             "lane": "ambush", "flags": ["weak_ambush_board"], "is_finalist": False},
        ]
        stock = {"600000": {"2026-10-29": 10.0, "2026-11-03": 11.0},
                 "600001": {"2026-10-29": 10.0, "2026-11-03": 9.5}}
        board = {"880001": {"2026-10-29": 100.0, "2026-11-03": 102.0}}
        out = attach_forward(rows, calendar=self.CAL_FWD, stock_closes=stock,
                             board_closes=board, horizons=(3,))
        self.assertEqual(out[0]["forward"]["3"]["status"], "ok")
        summary = summarize_forward(out, horizons=(3,))
        self.assertEqual(summary["3"]["all"]["count"], 2)
        self.assertAlmostEqual(summary["3"]["all"]["positive_rate"], 0.5, places=6)
        self.assertIn("weak_ambush_board", summary["3"])
        self.assertEqual(summary["3"]["weak_ambush_board"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
