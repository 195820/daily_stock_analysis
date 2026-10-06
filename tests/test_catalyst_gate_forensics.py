# -*- coding: utf-8 -*-
"""P1-1 取证脚本纯函数回归（2026-10-06）

只测无副作用的计算件（基准收益取窗、direction 污染过滤、比例格式化、分箱聚合），
不触 DB、不触归档、不调 LLM——脚本本体是只读取证工具，判读归负责人。
"""
import importlib.util
import os
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPEC_PATH = os.path.join(ROOT, "scripts", "calibrate_catalyst_confidence_gate.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("ccg_forensics", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rec(**kw):
    base = dict(
        confidence=0.7, event_date=date(2026, 8, 19), sector="存储芯片",
        is_realized=True, direction_correct=False, day_1=None, day_3=None,
        day_5=None, actual_return=None, category_tag="drift",
        event_type="global", source_type="forward", impact_level=2,
        direction="positive", fwd=None, bench=None, excess=None, horizon=None,
    )
    base.update(kw)
    return base


def test_bench_return_uses_trading_day_offsets(mod):
    days = [date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21)]
    closes = {d: c for d, c in zip(days, [100.0, 100.0, 104.0, 106.0])}
    # 起点取 ≥event_date 的首个交易日（8-19=100），+2 交易日 → 8-21=106
    assert mod._bench_return(closes, days, date(2026, 8, 19), 2) == pytest.approx(6.0)
    # 尾部不足 n 个交易日 → None（不得用不完整的窗冒充）
    assert mod._bench_return(closes, days, date(2026, 8, 20), 5) is None
    assert mod._bench_return({}, [], date(2026, 8, 19), 2) is None


def test_dir_acc_excludes_direction_contamination(mod):
    rows = [
        _rec(direction="positive", direction_correct=True),
        _rec(direction="negative", direction_correct=True),
        # GLOBAL_TO_A_SHARE_MAP 的 drift/fade 语义错位样本：恒判错，须排除
        _rec(direction="drift", direction_correct=False),
        _rec(direction="fade", direction_correct=False),
    ]
    acc, n = mod._dir_acc(rows)
    assert n == 2 and acc == 1.0
    # 全污染 → (None, 0)，不返回 0% 伪读数
    acc2, n2 = mod._dir_acc([_rec(direction="drift", direction_correct=False)])
    assert acc2 is None and n2 == 0


def test_enrich_excess_actual_mirrors_longest_available_window(mod):
    days = [date(2026, 8, d) for d in (19, 20, 21, 22, 25, 26)]
    closes = {d: 100.0 + i for i, d in enumerate(days)}
    rows = [
        _rec(day_5=-1.0, actual_return=-1.0),                 # 最长可用窗 = 5
        _rec(day_3=2.0, actual_return=2.0),                   # 退化到 3
        _rec(day_1=0.5, actual_return=0.5),                   # 退化到 1
    ]
    out = mod._enrich_excess(rows, closes, days, "actual")
    assert [r["horizon"] for r in out] == [5, 3, 1]
    # 基准取同一 horizon：event_date=days[0]，+5 → days[5]（105）/ +3 → days[3]（103）
    assert out[0]["bench"] == pytest.approx(5.0)
    assert out[0]["excess"] == pytest.approx(-1.0 - 5.0)
    assert out[1]["excess"] == pytest.approx(2.0 - (closes[days[3]] / closes[days[0]] - 1) * 100)
    assert out[2]["excess"] == pytest.approx(0.5 - (closes[days[1]] / closes[days[0]] - 1) * 100)


def test_bucket_and_pct_formatting(mod):
    assert mod._pct(0.733) == "73%"
    assert mod._pct(None) == "N/A"
    rows = [
        _rec(confidence=0.62, fwd=1.0, excess=0.5),
        _rec(confidence=0.72, fwd=2.0, excess=-0.5, day_5=2.0),
        _rec(confidence=0.55, is_realized=False),
    ]
    tbl = mod._bucket_stats(rows, [0.0, 0.5, 0.6, 0.7, 1.01])
    by_range = {b["range"]: b for b in tbl}
    assert by_range["[0.50,0.60)"]["n"] == 1        # 未兑现只计总数
    assert by_range["[0.60,0.70)"]["n_realized"] == 1
    assert by_range["[0.70,1.01)"]["excess_mean"] == pytest.approx(-0.5)
    assert by_range["[0.00,0.50)"]["n"] == 0


def test_gate_simulation_marks_current_threshold(mod):
    rows = [
        _rec(confidence=0.8, fwd=1.0, excess=0.5, day_5=1.0, direction_correct=True),
        _rec(confidence=0.65, fwd=-2.0, excess=-1.0, day_5=-2.0, direction_correct=True),
        _rec(confidence=0.5, is_realized=False),
    ]
    sims, labeled_n = mod._gate_sim(rows, gate=0.7, candidates=[0.5, 0.65, 0.7, 0.8])
    assert labeled_n == 2
    cur = next(s for s in sims if s["is_current"])
    assert cur["threshold"] == 0.7 and cur["kept"] == 1 and cur["rejected"] == 1
    assert cur["dir_acc"] == 1.0   # positive 且 correct=True
    lo = next(s for s in sims if s["threshold"] == 0.5)
    assert lo["kept"] == 2 and lo["excess_mean"] == pytest.approx(-0.25)
