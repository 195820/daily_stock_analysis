# -*- coding: utf-8 -*-
"""业绩预告方向取证脚本（scripts/audit_earnings_forecast_direction.py）的纯函数回归。

只测无副作用计算件，并且**逐条钉住 2026-10-06 取证时真实踩过的坑**：
- 统计口径必须 import 解禁取证件，不自立第二套口径；
- 覆盖判定必须用「区间内切片」：拿整段超集序列去判会在「区间内其实残缺」时假通过
  （本轮实测的负结果——加了校验反而 n_priced 249→233，根因就是这条）；
- 逐源重取的计分只看区间内行数（实测 Tencent 会超送区间外数据，按总行数计分会选到
  根本不覆盖目标旧窗口的近期行情）；
- 合并组按字面语义并格，但未进组的类型必须如实列出（不静默吞掉 akshare 新增枚举）；
- 缓存按 (code, 窗口起点) 分档，同 code 的超集旧档在区间内达标时仍复用（零额外网费）；
- 同股同日多指标时 canonical 行取净利润口径（营收「略增」不得盖掉净利「首亏」）。

脚本本身**不改生产代码、不接在线决策**，所以这里测的是「取证口径有没有被代码钉住」，
不是接线行为。
"""
import importlib.util
import os
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPEC_PATH = os.path.join(ROOT, "scripts", "audit_earnings_forecast_direction.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("earnings_forecast_direction", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── 1) 口径复用 ──────────────────────────────────────────────

def test_statistics_are_imported_not_duplicated(mod):
    """减基准/符号检验必须来自解禁取证件，且阈值与 GATE-2 标定同口径。"""
    for need in ("_norm_day", "find_anchor_index", "anchor_window_returns",
                 "sign_test", "wilson_interval", "WINDOWS", "SIGN_ALPHA"):
        assert hasattr(mod.K, need), f"共享口径件缺少 {need}"
    assert tuple(mod.K.WINDOWS) == (1, 3, 5)
    assert mod.K.SIGN_ALPHA == 0.05


# ── 2) 覆盖校验：必须只看区间内（本轮负结果的根因）────────────

def test_in_range_only_keeps_requested_window(mod):
    rows = [("2026-06-26", 1.0), ("2026-07-01", 2.0),
            ("2026-08-07", 3.0), ("2026-09-30", 4.0)]
    got = mod._in_range(rows, date(2026, 7, 1), date(2026, 8, 7))
    assert [d for d, _ in got] == ["2026-07-01", "2026-08-07"]


def test_coverage_short_rejects_superset_false_pass(mod):
    """整段序列首尾「盖得住」区间，但区间内一条都没有 → 必须判为覆盖不足。

    反例就是本轮踩过的那版实现：拿超集序列直接判，first≤start+slack 且 last≥end-slack
    会**永远通过**，于是残缺数据被当成可用证据。
    """
    start, end = date(2026, 7, 1), date(2026, 7, 31)
    superset = [(d, 1.0) for d in
                (["2026-06-%02d" % i for i in range(1, 13)]
                 + ["2026-08-%02d" % i for i in range(20, 31)])]
    assert len(superset) >= mod.COVERAGE_MIN_ROWS
    assert mod.coverage_short(superset, start, end) is False      # 旧口径：假通过
    inside = mod._in_range(superset, start, end)
    assert inside == []
    assert mod.coverage_short(inside, start, end) is True          # 新口径：判不足并触发重取


def test_coverage_short_accepts_dense_in_range_slice(mod):
    start, end = date(2026, 7, 1), date(2026, 7, 31)
    rows = [("2026-05-01", 1.0)] + [("2026-07-%02d" % i, 2.0) for i in range(1, 31)]
    assert mod.coverage_short(mod._in_range(rows, start, end), start, end) is False


def test_coverage_short_flags_sparse_series(mod):
    """区间内只剩 1 行（实测 Mootdx 把整段区间缩成区间末日 1 行）→ 必须判不足。"""
    start, end = date(2026, 6, 28), date(2026, 8, 7)
    one = [("2026-08-07", 77.99)]
    assert mod.coverage_short(mod._in_range(one, start, end), start, end) is True


# ── 3) 逐源重取：计分只看区间内行数 ──────────────────────────

def test_fetch_best_series_prefers_in_range_coverage_over_total_rows(mod, monkeypatch):
    """超送区间外数据的源（Tencent 型）不得靠「总行数多」胜出。"""
    monkeypatch.setattr(mod, "_rows_from_df", lambda df: df)  # 测试里直接喂行，绕开 pandas
    start, end = date(2026, 7, 1), date(2026, 7, 31)
    sparse = [("2026-07-15", 1.0)]
    wide_but_off = [("2026-09-%02d" % i, 2.0) for i in range(1, 25)]      # 区间外 24 行
    good = [("2026-07-%02d" % i, 3.0) for i in range(1, 25)]              # 区间内 24 行

    def mk(name, rows):
        return SimpleNamespace(name=name,
                               get_daily_data=lambda stock_code=None, start_date=None,
                                                end_date=None, **kw: rows)

    state = {"fetchers": [mk("OffShelf", wide_but_off), mk("Covered", good),
                          mk("Sparse", sparse)],
             "refetch_insufficient": True}
    rows, source = mod._fetch_best_series("600000", start, end, state)
    assert source == "multi:Covered"
    assert all(start.isoformat() <= d <= end.isoformat() for d, _ in rows)  # 返回前裁剪
    assert state["coverage_retry"]["resolved"] == 1


def test_fetch_best_series_early_stops_when_first_source_covers(mod, monkeypatch):
    """覆盖够了就早停：不给 Pytdx 那种 30s 连接超时代码走完全源（本轮实测代价）。"""
    monkeypatch.setattr(mod, "_rows_from_df", lambda df: df)
    calls = []

    def mk(name, rows):
        def _get(stock_code=None, start_date=None, end_date=None, **kw):
            calls.append(name)
            return rows
        return SimpleNamespace(name=name, get_daily_data=_get)

    good = [("2026-07-%02d" % i, 1.0) for i in range(1, 31)]
    state = {"fetchers": [mk("First", good), mk("Second", good)],
             "refetch_insufficient": True}
    mod._fetch_best_series("600000", date(2026, 7, 1), date(2026, 7, 31), state)
    assert calls == ["First"]


def test_fetch_best_series_blacklists_broken_source(mod, monkeypatch):
    """同一源累计异常达上限后被跳过，不再逐只票付超时代价。"""
    monkeypatch.setattr(mod, "_rows_from_df", lambda df: df)

    def boom(stock_code=None, start_date=None, end_date=None, **kw):
        raise RuntimeError("connect timeout")

    good = [("2026-07-%02d" % i, 1.0) for i in range(1, 31)]

    def mk_ok(name):
        return SimpleNamespace(name=name,
                               get_daily_data=lambda stock_code=None, start_date=None,
                                                end_date=None, **kw: good)

    dead = SimpleNamespace(name="Dead", get_daily_data=boom)
    state = {"fetchers": [dead, mk_ok("Ok")], "refetch_insufficient": True}
    for i in range(5):
        mod._fetch_best_series("60%04d" % i, date(2026, 7, 1), date(2026, 7, 31), state)
    assert state["dead_sources"]["Dead"] >= 3


# ── 4) 合并组：并格提高功效，但不得静默吞类型 ────────────────

def test_cohort_split_lists_types_not_in_any_cohort(mod):
    by_type = {t: [{"stock_code": "600000", "forecast_type": t}] for t in
               list(mod.COHORT_NEGATIVE) + list(mod.COHORT_POSITIVE)
               + ["不确定", "akshare 新增枚举"]}
    groups, leftovers = mod.cohort_split(by_type)
    assert len(groups["负面合并组"]) == len(mod.COHORT_NEGATIVE)
    assert len(groups["正面合并组"]) == len(mod.COHORT_POSITIVE)
    assert leftovers == ["akshare 新增枚举", "不确定"]      # 如实列出，不静默吞掉


def test_cohort_membership_is_literal_semantics(mod):
    """并格依据是字面语义，且必须写死可核对（负面组不含「扭亏/减亏」这类反转名）。"""
    assert set(mod.COHORT_NEGATIVE) == {"首亏", "续亏", "增亏", "预减", "略减"}
    assert "扭亏" in mod.COHORT_POSITIVE and "减亏" in mod.COHORT_POSITIVE
    assert not (set(mod.COHORT_NEGATIVE) & set(mod.COHORT_POSITIVE))


# ── 5) 窄窗分档：每档跨度受 MAX_WINDOW_DAYS 约束 ─────────────

def test_bucket_spans_caps_each_window_width(mod):
    events = [{"stock_code": "600000", "announce_date": d} for d in
              ("2026-01-15", "2026-04-20", "2026-07-10")]
    buckets = mod.bucket_spans(events)
    assert len(buckets) == 3                       # 跨半年不会被合成一档宽窗
    for _code, lo, hi in buckets:
        span = (hi - lo).days + mod.KLINE_PRE_DAYS + mod.KLINE_POST_DAYS
        assert span <= mod.MAX_WINDOW_DAYS


def test_bucket_spans_keeps_same_month_events_in_one_bucket(mod):
    events = [{"stock_code": code, "announce_date": "2026-07-%02d" % day}
              for code, day in (("600000", 1), ("600000", 5), ("000001", 20))]
    buckets = mod.bucket_spans(events)
    assert [c for c, _, _ in buckets] == ["000001", "600000"]
    assert buckets[1][1:] == (date(2026, 7, 1), date(2026, 7, 5))   # 同股同日段合并


# ── 6) 缓存按 (code, 窗口起点) 分档 ─────────────────────────

def _fake_state(**kw):
    state = {"source_census": {}, "fetch_errors": [], "manager": None,
             "fetchers": [], "refetch_insufficient": True}
    state.update(kw)
    return state


def test_superset_cache_is_reused_when_in_range_is_ok(mod, tmp_path):
    kline = tmp_path / "kline"
    kline.mkdir()
    start, end = date(2026, 7, 1), date(2026, 7, 31)
    rows = [("2026-06-01", 1.0)] + [("2026-07-%02d" % i, 2.0) for i in range(1, 31)]
    (kline / "600000.json").write_text(
        '{"code":"600000","start":"2026-06-01","end":"2026-08-31","source":"old",'
        '"rows":[%s]}' % ",".join('["%s",%s]' % (d, v) for d, v in rows),
        encoding="utf-8")
    state = _fake_state()
    got, source = mod.load_stock_series("600000", start, end, kline, None, state,
                                        cache_key="600000_2026-07-01")
    assert source == "cache" and len(got) == len(rows)             # 超集旧档仍可复用


def test_deficient_cache_is_rejected_and_not_counted_as_data(mod, tmp_path):
    """旧档区间内只剩 1 行 → 计 cache_rejected，且**不得当成功返回**（无源时如实为空）。"""
    kline = tmp_path / "kline"
    kline.mkdir()
    (kline / "600000.json").write_text(
        '{"code":"600000","start":"2026-05-01","end":"2026-09-30","source":"old",'
        '"rows":[["2026-08-07",77.99]]}', encoding="utf-8")
    state = _fake_state()
    got, source = mod.load_stock_series("600000", date(2026, 6, 28), date(2026, 8, 7),
                                        kline, None, state, cache_key="600000_2026-06-28")
    assert got == [] and source in ("", "none")
    assert state["coverage_retry"]["cache_rejected"] == 1


# ── 7) canonical 行取净利润口径 ─────────────────────────────

def test_dedupe_prefers_profit_indicator_row(mod):
    as_of = date(2026, 10, 6)
    raw = [
        {"stock_code": "600000", "stock_name": "甲股", "announce_date": "2026-08-20",
         "indicator": "营业收入", "forecast_type": "略增", "change_pct": "8.0"},
        {"stock_code": "600000", "stock_name": "甲股", "announce_date": "2026-08-20",
         "indicator": "归属于上市公司股东的净利润", "forecast_type": "首亏",
         "change_pct": "-120.0"},
    ]
    events, stats = mod.dedupe_events(raw, as_of)
    assert stats["stock_day_groups"] == 1
    assert stats["type_conflict_groups"] == 1              # 32.45% 那种冲突要被抓出来
    assert events[0]["forecast_type"] == "首亏"             # 净利润口径胜出，不被营收「略增」盖掉
    assert events[0]["profit_basis"] is True


def test_dedupe_marks_rows_without_profit_basis(mod):
    raw = [{"stock_code": "000001", "announce_date": "2026-08-20",
            "indicator": "营业收入", "forecast_type": "略增"}]
    events, stats = mod.dedupe_events(raw, date(2026, 10, 6))
    assert stats["groups_without_profit_indicator"] == 1
    assert events[0]["profit_basis"] is False               # 不静默取舍，如实标注


def test_dedupe_drops_events_without_priced_window(mod):
    """公告日太新（T+N 还没走完）必须剔除，不得拿未完成窗口参与读数。"""
    as_of = date(2026, 10, 6)
    fresh = (as_of - timedelta(days=1)).isoformat()
    raw = [{"stock_code": "600001", "announce_date": fresh,
            "indicator": "归属于上市公司股东的净利润", "forecast_type": "预增"}]
    events, stats = mod.dedupe_events(raw, as_of)
    assert stats["dropped_too_recent"] == 1 and events == []
