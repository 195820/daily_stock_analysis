# -*- coding: utf-8 -*-
"""4d/D-3 前置：候选池归零率 funnel_stats 观测单测（批次 5 §4.1，2026-09-28）。

覆盖纯观测计算：零改动选股决策，仅确保板块级归零事件首次可落盘。
- 归零板块识别（entering 有、picked 无）
- zero_pick_rate 计算
- round_survival_counts 取自 _last_funnel_trace + final_out
- ScreenerRunResult.funnel_stats 默认 None（观测字段不进判定）
"""
from __future__ import annotations

from src.services.stock_screener import ScreenerRunResult, ScreenerStock, StockScreenerPipeline


def _pipeline(trace=None):
    p = object.__new__(StockScreenerPipeline)
    if trace is not None:
        p._last_funnel_trace = trace
    return p


def _stock(code, sector):
    return ScreenerStock(code=code, name=code, score=1.0, sector_name=sector, sector_type="active_mainline")


def test_zero_pick_sector_detected():
    universe = [
        {"code": "600001", "sector": "AI算力", "sector_type": "active_mainline"},
        {"code": "600002", "sector": "AI算力", "sector_type": "active_mainline"},
        {"code": "600003", "sector": "白酒", "sector_type": "active_mainline"},  # 该板块全被漏斗滤掉
    ]
    finalists = [_stock("600001", "AI算力")]
    stats = _pipeline()._compute_funnel_stats(universe, finalists)
    assert stats["actionable_sector_count"] == 2
    assert stats["picked_sector_count"] == 1
    assert stats["zero_pick_sectors"] == ["白酒"]
    assert stats["zero_pick_rate"] == 0.5


def test_round_survival_from_trace():
    universe = [{"code": "600001", "sector": "AI算力", "sector_type": "active_mainline"}]
    finalists = [_stock("600001", "AI算力")]
    stats = _pipeline(trace={"r0_in": 10, "r0": 8, "r4": 3})._compute_funnel_stats(universe, finalists)
    rsc = stats["round_survival_counts"]
    assert rsc["r0_in"] == 10 and rsc["r4"] == 3
    assert rsc["final_out"] == 1  # 追加出口数


def test_empty_universe_no_division_error():
    stats = _pipeline()._compute_funnel_stats([], [])
    assert stats["actionable_sector_count"] == 0
    assert stats["zero_pick_rate"] == 0.0
    assert stats["zero_pick_sectors"] == []


def test_result_field_defaults_none():
    r = ScreenerRunResult(run_date="2026-09-28", run_time="15:00:00", strategy_name="got_mode")
    assert r.funnel_stats is None
