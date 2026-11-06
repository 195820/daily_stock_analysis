# -*- coding: utf-8 -*-
"""Node 2 early_watch：只看不买的早段观察清单。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

from src.got.nodes.sector_analysis import SectorAnalysisNode


def sec(code, name, strength, state="neutral", quadrant="leading", top=False):
    return SimpleNamespace(
        code=code, name=name, strength_score=strength, state=state,
        today_change=1.0, tier="A", rrg={"quadrant": quadrant},
    )


def result(active=(), ambush=(), near=(), top=()):
    return SimpleNamespace(
        active_mainline=[SimpleNamespace(members=list(active))] if active else [],
        ambush=list(ambush), near_miss_ambush=list(near), top_by_score=list(top),
    )


def raw(code, closes):
    return {"code": code, "klines": [{"close": c} for c in closes]}


class TestEarlyWatch(TestCase):
    def test_top_only_and_unrouted_are_kept(self):
        rows = SectorAnalysisNode._build_early_watch(
            [sec("880710", "种业", 81.8, state="exhausted_mainline", top=True)],
            result(top=[SimpleNamespace(code="880710")]),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "top_only_high_strength")
        unrouted = SectorAnalysisNode._build_early_watch(
            [sec("880904", "机器人概", 83.4)], result(),
        )
        self.assertEqual(unrouted[0]["reason"], "unrouted_high_strength")

    def test_active_ambush_members_are_excluded(self):
        rows = SectorAnalysisNode._build_early_watch(
            [sec("880001", "已入主线", 90)], result(active=[SimpleNamespace(code="880001")])
        )
        self.assertEqual(rows, [])
        rows = SectorAnalysisNode._build_early_watch(
            [sec("880002", "已入埋伏", 90)], result(ambush=[SimpleNamespace(code="880002")])
        )
        self.assertEqual(rows, [])

    def test_ambush_state_not_in_output_is_kept(self):
        rows = SectorAnalysisNode._build_early_watch(
            [sec("880692", "短剧游戏", 84.4, state="ambush", top=True)],
            result(top=[SimpleNamespace(code="880692")]),
        )
        self.assertEqual(rows[0]["code"], "880692")

    def test_trend_launch_is_kept_before_strength_80(self):
        # 种业 08-03 形态：5/10 日向上、站上 MA20、贴 20 日高、5 日分位高
        closes = [100 + i * 1.0 for i in range(40)]
        rows = SectorAnalysisNode._build_early_watch(
            [sec("880710", "种业", 60.0)], result(),
            [raw("880710", closes)] + [raw(f"88{i:04d}", [100.0] * 40) for i in range(900, 904)],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "trend_launch")
        self.assertGreater(rows[0]["r5"], 0)
        self.assertGreaterEqual(rows[0]["pct5"], 0.8)

    def test_flat_or_below_ma20_not_trend_launch(self):
        flat = [100.0] * 40
        rows = SectorAnalysisNode._build_early_watch(
            [sec("880001", "横盘", 60.0)], result(), [raw("880001", flat)],
        )
        self.assertEqual(rows, [])
        down = [100 - i * 0.5 for i in range(40)]
        rows = SectorAnalysisNode._build_early_watch(
            [sec("880002", "下行", 60.0)], result(), [raw("880002", down)],
        )
        self.assertEqual(rows, [])

    def test_strength_below_80_and_cap(self):
        rows = SectorAnalysisNode._build_early_watch(
            [sec("880003", "弱", 79.9)], result(),
        )
        self.assertEqual(rows, [])
        many = [sec(f"88{i:04d}", f"B{i}", 80 + i * 0.1) for i in range(30)]
        rows = SectorAnalysisNode._build_early_watch(many, result())
        self.assertEqual(len(rows), 20)
        self.assertGreaterEqual(rows[0]["strength"], rows[-1]["strength"])


if __name__ == "__main__":
    import unittest
    unittest.main()
