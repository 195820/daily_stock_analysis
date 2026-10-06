# -*- coding: utf-8 -*-
"""P2-4 · 解禁 risk_events 通道取证脚本的纯函数回归（2026-10-06）

只测无副作用计算件：日线读取、事件日锚定（镜像生产 ±3 日回退）、T+N 窗口、
扇出/板块日去重、与生产落库值对账、符号检验、减基准汇总、方向判定
（negative/positive/no_signal/insufficient）、与埋伏快照的可接线性、解冻结论。

脚本本身**不建通道、不接在线决策**（WI-P2-4 依赖 GATE-1/P0-1，§11.2 本轮不启动），
所以这里测的是「取证口径有没有被代码钉住」，不是接线行为。
"""
import importlib.util
import os
import sqlite3
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPEC_PATH = os.path.join(ROOT, "scripts", "audit_lockup_risk_events.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("lockup_risk_events", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _ev(sector, day, title="甲股(600000) 限售解禁", **kw):
    row = {"sector": sector, "event_date": day, "title": title,
           "impact_level": 2, "confidence": 0.95}
    row.update(kw)
    return row


def _win(board=None, bench=None, stored=None, rec="match"):
    exc = round(board - bench, 4) if board is not None and bench is not None else None
    return {"board_pct": board, "bench_pct": bench, "excess_pct": exc,
            "db_stored": stored, "reconcile": rec}


def _row(mod, sector, day, w1, w3, w5, code="880330"):
    return {"sector": sector, "event_date": day, "board_code": code,
            "code_match": "exact", "stock_codes": ["600000"], "status": "ok",
            "windows": {1: w1, 3: w3, 5: w5}}


# ── 日线读取与锚定 ──────────────────────────────────────────
def test_norm_day_accepts_both_calendars(mod):
    assert mod._norm_day("20260819") == "2026-08-19"
    assert mod._norm_day("2026-08-19") == "2026-08-19"
    assert mod._norm_day("2026-08-19 00:00:00") == "2026-08-19"


def test_read_price_series_missing_and_bad_columns(mod, tmp_path):
    assert mod.read_price_series(tmp_path / "nope.csv") == []
    bad = tmp_path / "bad.csv"
    bad.write_text("date,open\n20260819,1\n", encoding="utf-8")   # 无 close 列
    assert mod.read_price_series(bad) == []
    good = tmp_path / "good.csv"
    good.write_text("date,close\n20260820,11.0\n20260819,10.0\n", encoding="utf-8")
    assert mod.read_price_series(good) == [("2026-08-19", 10.0), ("2026-08-20", 11.0)]


def test_find_anchor_index_exact_then_forward_fallback(mod):
    series = [("2026-08-19", 10.0), ("2026-08-20", 11.0), ("2026-08-21", 12.0)]
    assert mod.find_anchor_index(series, date(2026, 8, 20)) == 1
    assert mod.find_anchor_index(series, date(2026, 8, 22)) == 2       # ±3 日内回退（先 + 后 −）
    assert mod.find_anchor_index(series, date(2026, 8, 26)) is None     # 距最近 bar 5 天 > ±3
    assert mod.find_anchor_index(series, date(2026, 8, 18)) == 0        # +1 命中 08-19
    assert mod.find_anchor_index([], date(2026, 8, 19)) is None


def test_anchor_window_returns_bounds_and_bad_base(mod):
    series = [("2026-08-19", 10.0), ("2026-08-20", 11.0)]
    got = mod.anchor_window_returns(series, 0, offsets=(1, 3, 5))
    assert got[1] == pytest.approx(10.0)
    assert got[3] is None and got[5] is None                              # 越界不编数
    assert mod.anchor_window_returns(series, 9) == {1: None, 3: None, 5: None}
    zero = [("2026-08-19", 0.0)]
    assert all(v is None for v in mod.anchor_window_returns(zero, 0).values())


# ── 扇出与去重 ──────────────────────────────────────────────
def test_dedupe_board_day_merges_same_sector_same_date(mod):
    rows = [_ev("半导体", "2026-08-19", "甲股(600000) 限售解禁", day_1_return=1.0),
            _ev("半导体", "2026-08-19", "乙股(600001) 限售解禁", day_1_return=3.0),
            _ev("半导体", "2026-08-20", "乙股(600001) 限售解禁")]
    samples = mod.dedupe_board_day(rows)
    assert len(samples) == 2
    first = samples[0]
    assert first["sector"] == "半导体" and first["event_date"] == "2026-08-19"
    assert len(first["titles"]) == 2 and first["stock_codes"] == ["600000", "600001"]
    assert first["db_day_returns"][1] == 1.0          # 同板同日取首条落库值（板块移动同源）


def test_fanout_stats_counts_stock_events(mod):
    rows = [_ev("半导体", "2026-08-19", "甲股(600000) 限售解禁"),
            _ev("汽车芯片", "2026-08-19", "甲股(600000) 限售解禁"),
            _ev("半导体", "2026-08-20", "乙股(600001) 限售解禁")]
    got = mod.fanout_stats(rows)
    assert got["raw_rows"] == 3 and got["distinct_stock_events"] == 2
    assert got["distinct_sectors"] == 2
    assert got["boards_per_stock_event_max"] == 2
    assert got["boards_per_stock_event_mean"] == 1.5
    assert mod.stock_code_of("无代码标题") is None


# ── 对账与统计 ──────────────────────────────────────────────
def test_reconcile_match_diff_na(mod):
    assert mod.reconcile(1.0, 1.02) == "match"
    assert mod.reconcile(1.0, 1.20) == "diff"
    assert mod.reconcile(None, 1.0) == "na" and mod.reconcile(1.0, None) == "na"


def test_sign_test_exact_binomial(mod):
    got = mod.sign_test([-1.0] * 10)
    assert got["negative"] == 10 and got["positive"] == 0 and got["neg_share"] == 1.0
    assert got["p_two_sided"] == pytest.approx(2 / 1024, abs=1e-6)   # 精确单侧尾 ×2
    mixed = mod.sign_test([-1.0, 1.0, -1.0, 1.0])
    assert mixed["p_two_sided"] == 1.0 and mixed["neg_share"] == 0.5
    assert mod.sign_test([0.0, 0.0])["p_two_sided"] is None      # 全零不做判定


def test_summarize_window_uses_only_comparable_cells(mod):
    rows = [
        _row(mod, "A", "2026-08-19", _win(-1.0, 0.5, -1.5), _win(None, 0.5), _win(1.0, None)),
        _row(mod, "B", "2026-08-20", _win(2.0, 0.5, 1.5, rec="diff"), _win(None, None), _win(None, None)),
    ]
    w1 = mod.summarize_window(rows, 1)
    assert w1["n_board"] == 2 and w1["n_excess"] == 2
    assert w1["excess_mean_pct"] == 0.0                            # (-1.5+1.5)/2
    assert w1["board_mean_pct"] == 0.5                             # (-1+2)/2，未减基准
    assert w1["reconcile"]["match"] == 1 and w1["reconcile"]["diff"] == 1
    assert w1["reconcile_comparable"] == 2
    assert w1["sign_test"]["neg_share"] == 0.5
    lo, hi = w1["share_below_bench_wilson95"]
    assert lo < 0.5 < hi
    w3 = mod.summarize_window(rows, 3)
    assert w3["n_excess"] == 0 and w3["excess_mean_pct"] is None
    assert w3["sign_test"] is None and w3["share_below_bench_wilson95"] is None


# ── 方向判定 ────────────────────────────────────────────────
def _wins(mod, board_vals, bench_vals):
    return [mod.summarize_window(
        [_row(mod, f"S{i}", f"2026-08-{19 + i}", _win(b, m, round(b - m, 4)),
              _win(None, None), _win(None, None))
         for i, (b, m) in enumerate(zip(board_vals, bench_vals))], 1)
    ]


def test_judge_evidence_four_states(mod):
    small = mod.judge_evidence(_wins(mod, [-1.0, 1.0], [0.0, 0.0]))
    assert small["verdict"] == "insufficient_sample"
    assert small["significant_negative"] is False and small["significant_positive"] is False

    flat = mod.judge_evidence(_wins(mod, [-1.0, 1.0, -1.0, 1.0, -1.0, 1.0], [0.0] * 6))
    assert flat["verdict"] == "no_signal" and flat["direction"] == "flat"

    neg = mod.judge_evidence(_wins(mod, [-3.0, -2.0, -4.0, -1.0, -2.5, -3.5], [0.0] * 6))
    assert neg["verdict"] == "significant_negative" and neg["significant_negative"] is True

    pos = mod.judge_evidence(_wins(mod, [3.0, 2.0, 4.0, 1.0, 2.5, 3.5], [0.0] * 6))
    assert pos["verdict"] == "significant_positive" and pos["significant_positive"] is True


def test_judge_evidence_prefers_primary_window_5(mod):
    rows = [_row(mod, f"S{i}", f"2026-08-{19 + i}",
                  _win(1.0, 0.0, 1.0), _win(1.0, 0.0, 1.0), _win(-5.0, 0.0, -5.0))
            for i in range(6)]
    wins = [mod.summarize_window(rows, off) for off in (1, 3, 5)]
    got = mod.judge_evidence(wins, primary_td=5)
    assert got["primary_window"] == 5 and got["verdict"] == "significant_negative"
    fallback = mod.judge_evidence([wins[0]], primary_td=5)
    assert fallback["primary_window"] == 1                      # 主窗不可用则退到可用窗


# ── 可接线性 ────────────────────────────────────────────────
def test_near_position_overlap_counts_within_window(mod):
    series = [(f"2026-08-{d}", 10.0) for d in range(17, 31, 2)]        # 0817,0819,...
    samples = [{"sector": "半导体", "event_date": "2026-08-25", "stock_codes": ["600000"]}]
    codes = {"半导体": ("880491", "exact")}
    boards = {"880491": series}
    ambush = [{"symbol": "300708", "sector_name": "半导体", "entry_date_iso": "2026-08-21"}]
    got = mod.near_position_overlap(samples, ambush, boards, codes, near_td=5)
    assert got["overlap_count"] == 1 and got["overlap"][0]["gap_td"] == 2

    far = mod.near_position_overlap(samples, ambush, boards, codes, near_td=1)
    assert far["overlap_count"] == 0
    no_code = mod.near_position_overlap(
        samples, ambush, boards, {"半导体": (None, "failed")}, near_td=5)
    assert no_code["overlap_count"] == 0 and no_code["sectors_without_kline"] == ["半导体"]


def test_match_census_tags_fuzzy_and_failed(mod):
    codes = {"A": ("880491", "exact"), "B": ("880492", "alias"),
             "C": ("880493", "fuzzy:半导体"), "D": (None, "failed")}
    got = mod.match_census(codes)
    assert got == {"exact": 1, "alias": 1, "fuzzy": 1, "failed": 1}


# ── 解冻结论 ────────────────────────────────────────────────
def test_channel_verdict_blocks_all_three_gaps(mod):
    ev = {"significant_negative": False, "significant_positive": False,
          "verdict": "no_signal"}
    got = mod.channel_verdict(
        {"catalyst_events": {"by_type": {}}}, [_ev("半导体", "2026-08-19")],
        date(2026, 10, 6), ev, {"overlap_count": 0, "near_td": 5})
    assert got["unlock_ready"] is False
    assert len(got["preconditions"]) == 3
    assert got["lockup_last_event_date"] == "2026-08-19"
    assert got["online_calendar_lockup_rows"] == 0


def test_channel_verdict_flags_reversed_direction_not_sample_size(mod):
    ev = {"significant_negative": False, "significant_positive": True,
          "verdict": "significant_positive"}
    got = mod.channel_verdict(
        {"catalyst_events": {"by_type": {"lockup": 5}}}, [_ev("半导体", "2026-08-19")],
        date(2026, 10, 6), ev, {"overlap_count": 3, "near_td": 5})
    assert got["unlock_ready"] is False
    assert len(got["preconditions"]) == 1
    assert "显著跑赢" in got["preconditions"][0]


def test_channel_verdict_ready_when_all_gates_pass(mod):
    ev = {"significant_negative": True, "significant_positive": False,
          "verdict": "significant_negative"}
    got = mod.channel_verdict(
        {"catalyst_events": {"by_type": {"lockup": 5}}}, [_ev("半导体", "2026-08-19")],
        date(2026, 10, 6), ev, {"overlap_count": 3, "near_td": 5})
    assert got["unlock_ready"] is True and got["preconditions"] == []


# ── DB 只读与端到端降级 ─────────────────────────────────────
def _make_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE catalyst_realizations (event_id TEXT, event_type TEXT, sector TEXT,"
        " title TEXT, event_date TEXT, impact_level INT, confidence REAL,"
        " day_1_return REAL, day_3_return REAL, day_5_return REAL);"
        "CREATE TABLE catalyst_events (event_type TEXT, event_date TEXT);"
        "CREATE TABLE ambush_position_snapshots (symbol TEXT, sector_name TEXT, entry_date TEXT);"
        "CREATE TABLE portfolio_positions (symbol TEXT);"
    )
    con.execute("INSERT INTO catalyst_realizations VALUES ('e1','lockup','半导体',"
                "'甲股(600000) 限售解禁','2026-08-19',2,0.95,NULL,NULL,NULL)")
    con.execute("INSERT INTO ambush_position_snapshots VALUES ('300708','半导体','2026-08-17')")
    con.commit()
    con.close()


def test_open_readonly_rejects_writes(mod, tmp_path):
    _make_db(str(tmp_path / "x.db"))
    con = mod.open_readonly(tmp_path / "x.db")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO portfolio_positions VALUES ('600000')")
    con.close()


def test_run_audit_degrades_honestly_without_klines(mod, tmp_path):
    db = tmp_path / "x.db"
    _make_db(str(db))
    art = mod.run_audit(db, tmp_path / "empty_kline_dir", tmp_path / "no_bench.csv",
                        date(2026, 10, 6))
    assert art["q1_data_ground"]["raw_rows"] == 1
    assert art["q2_evidence"]["verdict"]["verdict"] == "insufficient_sample"
    assert art["q2_evidence"]["windows"][0]["n_excess"] == 0
    assert art["q3_joinability"]["overlap_count"] == 0
    assert art["verdict"]["unlock_ready"] is False
    assert art["sources"]["benchmark_loaded"] is False
    # 只读：跑完后持仓表仍为空
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0] == 0
    con.close()
