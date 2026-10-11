# -*- coding: utf-8 -*-
"""批次 F：回测纪律纯函数单测（run card 强制 + 过拟合三检，全离线）。"""
from __future__ import annotations

from src.got.backtest_discipline import (
    benchmark_excess,
    build_run_card,
    moving_block_bootstrap_ci,
    walk_forward_splits,
)


def test_run_card_valid_requires_pit():
    ok = build_run_card(sample_window="2026-01~09", benchmark="沪深300",
                        point_in_time="close_after_1500")
    assert ok["_valid"] is True
    assert ok["scoring_formula_version"]
    assert ok["git_commit"]
    bad = build_run_card(sample_window="", benchmark="", point_in_time="unknown")
    assert bad["_valid"] is False


def test_run_card_rejects_fill_policy():
    card = build_run_card(sample_window="w", benchmark="b", point_in_time="replay",
                          missing_value_policy="fill")
    assert card["_valid"] is False  # 缺失值伪造填补被拒（Vibe-Trading 纪律）


def test_benchmark_excess():
    assert benchmark_excess([3, 2, 1], [1, 1, 1]) == [2, 1, 0]
    # 截断到较短长度
    assert benchmark_excess([5, 5, 5, 5], [1, 1]) == [4, 4]


def test_bootstrap_ci_deterministic_and_orders():
    a = moving_block_bootstrap_ci([1.0, 2.0, 3.0, 4.0, 5.0] * 6, seed=7)
    b = moving_block_bootstrap_ci([1.0, 2.0, 3.0, 4.0, 5.0] * 6, seed=7)
    assert a == b                     # 同 seed 复现
    assert a[0] <= a[1]               # 下界 ≤ 上界
    assert a[0] > 0                   # 全正收益 → CI 下界为正
    assert moving_block_bootstrap_ci([]) == (0.0, 0.0)


def test_walk_forward_splits_count():
    wins = list(walk_forward_splits(10, train=4, test=2, step=2))
    assert wins[0] == ((0, 4), (4, 6))
    assert all(te[1] <= 10 for _, te in wins)
    # 不足一窗 → 不产出（不虚构切分）
    assert list(walk_forward_splits(5, train=4, test=2)) == []
    assert list(walk_forward_splits(100, train=0, test=5)) == []
