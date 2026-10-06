# -*- coding: utf-8 -*-
"""GATE-2 最优停止标定脚本纯函数回归（2026-10-06）

只测无副作用计算件（归档日过滤、sector.json 下钻、主线段切分、θ 与超额、
分层收缩、基准取窗）——脚本本体只读归档 + 写一个 JSON 工件，判读归负责人。
`flatten_sector_entries` 一条是真实踩坑回归：`active_mainline` 顶层是家族分组，
不下钻 members 就会把全部活跃主线整批丢掉，只剩下 exhausted 板块，θ 被系统性
拉向第 1 天、pooled 累计收益从 +2.9% 变成 −1.9%。
"""
import importlib.util
import os
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPEC_PATH = os.path.join(ROOT, "scripts", "calibrate_optimal_stop.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("optimal_stop_calib", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _bench(closes):
    """(closes, days, order) 三元组：按 YYYYMMDD 升序。"""
    days = sorted(closes)
    return {"closes": closes, "days": days, "order": {d: i for i, d in enumerate(days)}}


# ── 归档日过滤 ──────────────────────────────────────────────
def test_list_archive_days_excludes_snapshot_dirs(mod, tmp_path):
    for name in ("20260805", "20260805_backup", "20260817_pre_fix", "_cache", "20260930"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "unified.json").write_text("{}", encoding="utf-8")
    got = mod.list_archive_days(tmp_path, date(2026, 9, 30))
    assert got == ["20260805", "20260930"]          # 快照目录同日重复计入会污染面板
    assert mod.list_archive_days(tmp_path, date(2026, 8, 6)) == ["20260805"]  # as_of 截断


# ── sector.json 下钻（真实踩坑回归）─────────────────────────
def test_flatten_sector_entries_drills_into_family_groups(mod):
    sdata = {
        "active_mainline": [{"group_name": "地产链", "tier": "A", "members": [
            {"code": "880483", "name": "全国地产", "today_change": 3.1,
             "frame_group": "其他", "mainline_days": 24, "state": "active_mainline"}]}],
        "exhausted_mainline": [{"code": "880681", "name": "减肥药", "today_change": 2.81,
                                "frame_group": "医药", "mainline_days": 3}],
        "industry_top": [{"code": "880363", "name": "安防服务", "today_change": -1.0}],
    }
    out = mod.flatten_sector_entries(sdata, ("active_mainline", "exhausted_mainline",
                                             "industry_top"))
    assert set(out) == {"880483", "880681", "880363"}
    assert out["880483"]["ret_pct"] == 3.1
    # 顶层条目没有 state 时用列表名兜底（exhausted 与 active 语义不同，不能混）
    assert out["880681"]["state"] == "exhausted_mainline"
    assert out["880483"]["state"] == "active_mainline"


# ── 主线段切分 ──────────────────────────────────────────────
def _panel_of(code, days):
    return {(d, code): {"day": d, "code": code, "name": code, "ret_pct": None,
                        "frame_group": None, "mainline_days": None,
                        "state": "active_mainline", "role_source": "unified_role",
                        "has_sector_entry": True} for d in days}


def test_build_segments_splits_on_gap_and_honours_gap_tolerance(mod):
    cal = ["20260914", "20260915", "20260916", "20260917", "20260918",
           "20260921", "20260922", "20260923", "20260924"]
    panel = _panel_of("880001", cal[:5] + cal[6:])   # 缺 20260921：掉出主线一天
    segs = mod.build_segments(panel, cal, max_gap_days=5, min_len=2)
    assert [s["days"] for s in segs] == [cal[:5], cal[6:]]
    # 周末/长假：相邻归档日序号连续但日历日跨度大 → ≤max_gap_days 不切，超过才切
    long_holiday = ["20260924", "20260928"]          # 中秋国庆前，隔 4 天
    panel2 = _panel_of("880002", long_holiday)
    assert len(mod.build_segments(panel2, cal + ["20260928"], 5, 2)) == 1
    far = ["20260924", "20260930"]
    assert mod.build_segments(_panel_of("880003", far), cal + far, 5, 2) == []


# ── θ（最优停止点）与超额 ───────────────────────────────────
def test_segment_stats_theta_and_excess_alignment(mod):
    days = ["20260914", "20260915", "20260916", "20260917"]
    rets = {"20260914": 5.0, "20260915": 4.0, "20260916": -3.0, "20260917": 1.0}
    panel = {}
    for d in days:
        panel[(d, "880001")] = {"day": d, "code": "880001", "name": "测试板",
                                "ret_pct": rets[d], "frame_group": "医药",
                                "mainline_days": 4, "state": "active_mainline",
                                "role_source": "unified_role", "has_sector_entry": True}
    bench = _bench({"20260914": 100.0, "20260915": 101.0, "20260916": 100.0,
                    "20260917": 100.0, "20260918": 101.0})
    st = mod.segment_stats({"code": "880001", "days": days}, panel,
                           {"20260914": "bull"}, {"880001": "医药"}, bench)
    # 累计：5.0 → 9.2 → 5.924 → 6.983 → 峰值在第 2 天，回落 2.22pp
    assert st["theta_day"] == 2 and st["n_days"] == 4
    assert st["giveback_pct"] == pytest.approx(2.2168, abs=1e-3)
    assert st["peaked_before_end"] is True
    # 日超额 = 板块涨跌 − 基准同日涨跌（基准 14 日无前一日 → 该日剔除，故子列只 3 个）
    assert st["excess_pct"] == pytest.approx([3.0, -2.0099, 1.0])
    assert st["theta_excess_day"] == 1      # 超额峰值在子序列第 1 个（与绝对序列错位 1 日，见 caveat）
    assert st["cum_excess_pct"] == pytest.approx(1.9391, abs=1e-3)
    assert st["giveback_excess_pct"] == pytest.approx(1.0609, abs=1e-3)
    assert st["family"] == "医药" and st["regime"] == "bull"
    # 不足两个收益读数 → None（缺失≠零分，不拿单点冒充一段）
    thin = {(d, "880009"): dict(panel[("20260914", "880001")], code="880009",
                                ret_pct=None) for d in ("20260914", "20260915")}
    assert mod.segment_stats({"code": "880009", "days": list(thin)}, thin, {}, {}, bench) is None


def test_segment_stats_without_benchmark_keeps_absolute_only(mod):
    days = ["20260914", "20260915"]
    panel = {(d, "880001"): {"day": d, "code": "880001", "name": None,
                             "ret_pct": 1.0 if d == days[0] else -0.5,
                             "frame_group": None, "mainline_days": None,
                             "state": "active_mainline", "role_source": "unified_role",
                             "has_sector_entry": True} for d in days}
    st = mod.segment_stats({"code": "880001", "days": days}, panel, {}, {})
    assert st["cum_excess_pct"] is None and st["theta_excess_day"] is None
    assert st["mu_daily_pct"] == pytest.approx(0.25)


# ── 先验分布 / 分层收缩 ─────────────────────────────────────
def test_prior_cdf_theta_monotonic_and_skips_missing(mod):
    segs = [{"theta_day": 1, "theta_excess_day": None},
            {"theta_day": 3, "theta_excess_day": 2},
            {"theta_day": 11, "theta_excess_day": 2}]
    cdf = mod.prior_cdf_theta(segs, "theta_day")
    assert cdf == sorted(cdf) and len(cdf) == mod.THETA_MAX_DAY
    assert cdf[0] == pytest.approx(1 / 3, abs=1e-4)      # θ=1 只有 1 段
    assert cdf[-1] == pytest.approx(2 / 3, abs=1e-4)     # θ=11 超出枚举上限 → 末位不强制为 1
    cdf_ex = mod.prior_cdf_theta(segs, "theta_excess_day")
    assert cdf_ex[0] == 0.0 and cdf_ex[1] == 1.0               # None 段不入分布
    assert mod.prior_cdf_theta([{"theta_day": None}], "theta_day") == []


def test_shrink_falls_back_to_pooled_for_small_bins(mod):
    # w = n/(n+k)：n=2,k=8 → 0.2；全局值主导
    assert mod.shrink_scalar(5.0, 1.0, 2, 8) == pytest.approx(1.8)
    assert mod.shrink_scalar(None, 1.0, 2, 8) == 1.0        # 箱内无读数 → 直接全局
    assert mod.shrink_scalar(2.0, None, 2, 8) == 2.0
    assert mod.shrink_scalar(None, None, 0, 8) is None      # 两端都无 → None，不虚构 0
    cdf = mod.shrink_cdf([0.0, 0.5, 1.0], [0.2, 0.6, 1.0], 2, 8)
    assert cdf == sorted(cdf) and len(cdf) == 3             # 混合仍是 CDF
    assert cdf[0] == pytest.approx(0.16)
    assert mod.shrink_cdf([], [0.2, 0.6, 1.0], 1, 8) == [0.2, 0.6, 1.0]


def test_build_bins_never_empty_and_status_graded(mod):
    def _seg(family, regime, rets):
        return {"code": family, "name": family, "start": "20260914", "end": "20260917",
                "n_days": len(rets), "n_days_span": len(rets), "n_days_observed": len(rets),
                "family": family, "regime": regime, "ret_pct": rets, "excess_pct": rets,
                "cum_ret_pct": sum(rets), "mu_daily_pct": sum(rets) / len(rets),
                "sigma_daily_pct": 1.0, "cum_excess_pct": sum(rets),
                "mu_daily_excess_pct": sum(rets) / len(rets),
                "theta_day": 1, "theta_ret_pct": rets[0], "giveback_pct": 0.0,
                "peaked_before_end": len(rets) > 1, "theta_excess_day": 1,
                "giveback_excess_pct": 0.0, "selfreport_mainline_days": len(rets)}
    segs = ([_seg("AI硬件", "bull", [2.0, -1.0]) for _ in range(20)]
            + [_seg("农业", "bear", [1.0, 1.0])])
    pooled = mod.summarize(segs, "pooled")
    bins = mod.build_bins(segs, pooled, min_samples=12, k=8)
    assert len(bins) == 2                                   # 有段就有箱，不得为空
    big = next(b for b in bins if b["family"] == "AI硬件")
    small = next(b for b in bins if b["family"] == "农业")
    assert big["status"] == "ok" and big["shrink_weight"] == pytest.approx(0.7143, abs=1e-3)
    assert small["status"] == "shrunk" and small["shrink_weight"] == pytest.approx(0.1111, abs=1e-3)
    # 小样本箱被拉向 pooled：农业裸 μ=+1.0，收缩后必须低于 1.0
    assert small["mu_daily_pct"] < 1.0 and small["mu_daily_pct"] > pooled["mu_daily_pct"]["mean"]
    assert small["raw"]["mu_daily_pct"]["mean"] == pytest.approx(1.0)   # 裸值仍可审阅
    assert "p_peak_by_day" not in small["raw"]                  # 体积控制：裸箱只留 scalar
    assert mod.summarize([], "empty")["status"] == "empty"


# ── 基准取窗 ────────────────────────────────────────────────
def test_bench_window_helpers(mod):
    b = _bench({"20260914": 100.0, "20260915": 104.0, "20260916": 106.0,
                "20260917": 112.0, "20260918": 112.0})
    assert mod.bench_ret_on(b["closes"], b["days"], b["order"], "20260915") == pytest.approx(4.0)
    assert mod.bench_ret_on(b["closes"], b["days"], b["order"], "20260914") is None  # 无前一日
    assert mod.bench_ret_on(b["closes"], b["days"], b["order"], "20260920") is None  # 不在日历
    assert mod.bench_fwd(b["closes"], b["days"], b["order"], "20260915", 2) == pytest.approx(7.6923, abs=1e-3)
    assert mod.bench_fwd(b["closes"], b["days"], b["order"], "20260917", 2) is None  # 尾部不足
    assert mod.load_benchmark(__import__("pathlib").Path("nope.csv"))[0] == {}


def test_wilson_interval_edge_cases(mod):
    assert mod.wilson_interval(0, 0) == (0.0, 1.0)          # 无样本 = 全不确定
    lo, hi = mod.wilson_interval(5, 5)
    assert lo > 0.4 and hi == 1.0
