#!/usr/bin/env python
"""GATE-2 · 最优停止的 pooled/分层先验标定（离线标定脚本）。

来源：docs/got/plans/architecture_optimization_implementation_20260929.md §2 GATE-2。

职责：把「主线段」的历史日收益转成 WI-P0-1（概率化最优停止引擎）可用的**先验查表**，
落 `data/optimal_stop_priors.json`。做法是跨板块 pooled + 分层（板块家族 × regime），
小样本箱按 hierarchical shrinkage 向全局箱回退——单持仓日线观测点 ≤7 无法逐持仓估
后验（§10「不在单持仓尺度做在线贝叶斯 MCMC」），故一切估计都在离线这里完成。

数据来源（全部本地、默认不触网）：
- `data/got_daily/YYYYMMDD/unified.json` → `confirmed_sectors[].sector_role`（主线判据）
  + `market_regime.regime`（分箱维度）
- `data/got_daily/YYYYMMDD/sector.json` → 板块面板 `today_change`（该板块当日涨跌幅 %，
  即生产当日所见，天然 point-in-time）+ `frame_group` / `mainline_days`
- 可选 `--kline-dir`（默认 `.review_tmp/bt880_cache`，缺失即降级）只用于**段后**续涨
  fwd1/3/5（面板里板块出池后就没有读数了，只能靠日线补），不参与段内估计
- `--benchmark`（默认 `.review_tmp/bt_cache/index_sh000852.csv`，中证1000）用于减基准
  超额列（【总计划】§1.1 口径红线）；文件缺失时超额节诚实返回 n=0，不影响绝对值读数

Point-in-time 红线（§7 专项）：
- 只读 `--as-of` 之前（含）的归档日；段内收益全部来自当日归档快照，不看未来
- 唯一使用「未来信息」的是 `frame_group` 映射（板块→家族是静态分类学，非价格前视），
  且取 as_of 之前最近一次已知值；工件 caveats 已披露

用法：
    python scripts/calibrate_optimal_stop.py [--as-of YYYY-MM-DD] [--out PATH]
        [--min-len 2] [--max-gap-days 5] [--min-samples 12] [--shrink-k 8]
        [--kline-dir PATH|--no-kline] [--wave-jsonl PATH]

验收标准（§2 GATE-2）：分箱样本量计数如实输出；小样本箱 shrinkage 回退全局箱；
pooled 箱不得为空（空则 status=insufficient_sample 且消费方必须退回 legacy）。
本脚本只读归档、只写一个 JSON 工件，不碰 DB、不碰生产常量、不碰在线决策。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

logger = logging.getLogger("calibrate_optimal_stop")

ARCHIVE_DIR = ROOT_DIR / "data" / "got_daily"
DEFAULT_OUT = ROOT_DIR / "data" / "optimal_stop_priors.json"
DEFAULT_KLINE_DIR = ROOT_DIR / ".review_tmp" / "bt880_cache"
DEFAULT_BENCH_CSV = ROOT_DIR / ".review_tmp" / "bt_cache" / "index_sh000852.csv"
DEFAULT_WAVE_JSONL = ROOT_DIR / "data" / "wave_calibration.jsonl"

DAY_RE = re.compile(r"^\d{8}$")
# 段内 θ（最优停止日）只枚举到这些天，之后归入尾部箱（主线寿命 93.5%<7 天）
THETA_MAX_DAY = 10
UNKNOWN = "unknown"


def _to_date(day: str) -> date:
    return datetime.strptime(day, "%Y%m%d").date()


# ─────────────────────────────────────────────────────────────
# 1) 归档面板加载
# ─────────────────────────────────────────────────────────────

def list_archive_days(archive_dir: Path, as_of: date) -> list[str]:
    """合规归档日 = 目录名严格 8 位数字 且 ≤ as_of。

    必须排除 `20260805_backup` / `20260817_pre_fix` / `_cache` 这类快照目录，
    否则同一天的板块会被重复计入两次（实测存在）。
    """
    days = []
    if not archive_dir.exists():
        return days
    for p in archive_dir.iterdir():
        if not p.is_dir() or not DAY_RE.match(p.name):
            continue
        if _to_date(p.name) > as_of:
            continue
        if (p / "unified.json").exists():
            days.append(p.name)
    return sorted(days)


def _json_or_none(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("[GATE-2] 归档解析失败 %s: %s", path, exc)
        return None


def load_panel(archive_dir: Path, days: list[str], include_exhausted: bool = False) -> tuple[dict, dict]:
    """(day, code) → 面板行；以及 day → regime。

    主线判据（两个源取并集，缺一会漏）：
    1. `unified.confirmed_sectors[].sector_role == "mainline"`——Node6 收口后「已确认主线」，
       是 P0-1 持仓对象的真实语义（20260807 起才落该字段）；
    2. `sector.json.active_mainline[].members[].state == "active_mainline"`——Node2 侧活跃主线。
       注意 `active_mainline` 顶层是**家族分组**（group_name/members），板块在 members 里。
    `exhausted_mainline`（衰竭）默认**不算**主线段——它已被判退出，计入会把 θ 系统性拉向
    第 1 天（初版实测踩到此坑：pooled cum_ret p50 由 +0.x 变成 −1.9%），需要时用
    `--include-exhausted` 做敏感性对照。
    """
    panel: dict[tuple[str, str], dict] = {}
    regime_by_day: dict[str, str] = {}
    sector_lists = ("active_mainline", "exhausted_mainline", "ambush",
                    "near_miss_ambush", "defensive_rotation", "cooling_down",
                    "top_by_score", "industry_top")
    mainline_states = {"active_mainline"} | ({"exhausted_mainline"} if include_exhausted else set())

    for day in days:
        udata = _json_or_none(archive_dir / day / "unified.json") or {}
        regime_by_day[day] = (udata.get("market_regime") or {}).get("regime") or UNKNOWN
        unified_rows = {s["sector_code"]: s for s in (udata.get("confirmed_sectors") or [])
                        if s.get("sector_code")}
        sdata = _json_or_none(archive_dir / day / "sector.json") or {}
        sector_rows = flatten_sector_entries(sdata, sector_lists)
        for code in set(unified_rows) | set(sector_rows):
            u = unified_rows.get(code) or {}
            srow = sector_rows.get(code) or {}
            by_role = u.get("sector_role") == "mainline"
            by_state = (srow.get("state") or "") in mainline_states
            if not (by_role or by_state):
                continue
            panel[(day, code)] = {
                "day": day,
                "code": code,
                "name": srow.get("name") or u.get("sector_name"),
                "ret_pct": srow.get("ret_pct"),
                "frame_group": srow.get("frame_group") or u.get("frame_group"),
                "mainline_days": srow.get("mainline_days") or u.get("mainline_days"),
                "state": srow.get("state") or (u.get("sector_role") or UNKNOWN),
                "role_source": "unified_role" if by_role else "sector_state",
                "has_sector_entry": bool(srow),
            }
    return panel, regime_by_day


def flatten_sector_entries(sdata: dict, sector_lists) -> dict[str, dict]:
    """sector.json → {code: 面板字段}。`active_mainline` 顶层是家族分组（group_name +
    members），板块藏在 members 里；其余列表是平铺板块条目。不下钻就整批丢主线。"""
    out: dict[str, dict] = {}
    for key in sector_lists:
        for item in sdata.get(key) or []:
            if not isinstance(item, dict):
                continue
            candidates = item.get("members") if isinstance(item.get("members"), list) else [item]
            for e in candidates:
                code = (e or {}).get("code")
                if not code:
                    continue
                out[code] = {
                    "name": e.get("name"),
                    "ret_pct": e.get("today_change"),
                    "frame_group": e.get("frame_group"),
                    "mainline_days": e.get("mainline_days"),
                    "state": e.get("state") or key,
                }
    return out


def build_family_map(panel: dict, as_of: date) -> dict[str, str]:
    """sector_code → frame_group（家族）。家族是板块的静态分类属性，取 as_of 之前
    最后一次出现的非空值（不引入任何价格未来信息）。"""
    latest: dict[str, tuple[str, str]] = {}
    for (day, code), row in panel.items():
        fg = row.get("frame_group")
        if not fg or _to_date(day) > as_of:
            continue
        if code not in latest or day >= latest[code][0]:
            latest[code] = (day, fg)
    return {code: fg for code, (_, fg) in latest.items()}


def load_benchmark(path: Path) -> tuple[dict[str, float], list[str], dict[str, int]]:
    """基准指数日线（中证1000，【总计划】§1.1 口径红线：前向读数一律减基准）。
    返回 (close by YYYYMMDD, 排序日列, day→序号)。文件缺失 → 空表，超额节诚实降级不可得。"""
    if not path or not path.exists():
        return {}, [], {}
    closes: dict[str, float] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return {}, [], {}
    header = (lines[0].lower() if lines else "")
    date_col, close_col = 0, 1
    names = [c.strip() for c in header.split(",")]
    for i, name in enumerate(names):
        if name in ("date", "\u65e5\u671f", "trade_date"):
            date_col = i
        if name in ("close", "\u6536\u76d8", "收盘"):
            close_col = i
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(date_col, close_col):
            continue
        d = parts[date_col].strip().replace("-", "").replace("/", "")
        if not DAY_RE.match(d):
            continue
        try:
            closes[d] = float(parts[close_col])
        except ValueError:
            pass
    ordered_days = sorted(closes)
    return closes, ordered_days, {d: i for i, d in enumerate(ordered_days)}


def bench_ret_on(bench: dict[str, float], days: list[str], order: dict[str, int],
                 day: str) -> float | None:
    """基准在该交易日的涨跌幅（%）：与板块当日同口径（当日 close / 前一交易日 close）。
    该日不在基准日历上 → None，调用侧剔除该日，不拿 0 顶替。"""
    i = order.get(day)
    if i is None or i < 1 or day not in bench:
        return None
    prev_close = bench.get(days[i - 1])
    if not prev_close or prev_close <= 0:
        return None
    return (bench[day] / prev_close - 1.0) * 100.0


def bench_fwd(bench: dict[str, float], days: list[str], order: dict[str, int],
              day: str, h: int) -> float | None:
    """基准自 day 起 +h 交易日的累计收益（%），用于段后超额。尾部不足 → None。"""
    i = order.get(day)
    if i is None or day not in bench or i + h >= len(days):
        return None
    base = bench[days[i]]
    if base <= 0:
        return None
    return (bench[days[i + h]] / base - 1.0) * 100.0


def load_kline_closes(kline_dir: Path) -> dict[str, dict[str, float]]:
    """板块日线缓存 → {code: {YYYYMMDD: close}}。目录不存在返回 {}（诚实降级，不触网）。
    列位由表头决定（本仓缓存为 date,open,close,…，仅用于段外窗口）。"""
    out: dict[str, dict[str, float]] = {}
    if not kline_dir or not kline_dir.exists():
        return out
    for f in kline_dir.glob("*.csv"):
        closes: dict[str, float] = {}
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        names = [c.strip().lower() for c in lines[0].split(",")]
        close_i = names.index("close") if "close" in names else 2
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) <= close_i:
                continue
            d = parts[0].strip()
            if DAY_RE.match(d):
                try:
                    closes[d] = float(parts[close_i])
                except ValueError:
                    pass
        if closes:
            out[f.stem] = closes
    return out


# ─────────────────────────────────────────────────────────────
# 2) 主线段切分
# ─────────────────────────────────────────────────────────────

def build_segments(panel: dict, calendar: list[str], max_gap_days: int,
                   min_len: int) -> list[dict]:
    """按 (calendar 序号) 把每个板块的主线日切成连续段。

    连续性用**交易日历序号**判断（不是简单数组相邻）：两个主线日序号差 >1 表示中间
    有断档（掉出主线/未跑管道），即段尾。`max_gap_days` 为日历天容差：跨周末/长假的
    相邻交易日允许 ≤max_gap_days 个日历日，超过才判为真断档。
    """
    cal_idx = {d: i for i, d in enumerate(calendar)}
    by_code: dict[str, list[str]] = {}
    for (day, code) in panel:
        by_code.setdefault(code, []).append(day)

    segs: list[dict] = []
    for code, day_list in by_code.items():
        run: list[str] = []
        for day in sorted(day_list):
            if run:
                prev = run[-1]
                ci, pi = cal_idx.get(day), cal_idx.get(prev)
                if ci is not None and pi is not None and ci - pi > 1:
                    segs.append({"code": code, "days": run})
                    run = []
                elif (_to_date(day) - _to_date(prev)).days > max_gap_days:
                    segs.append({"code": code, "days": run})
                    run = []
            run.append(day)
        if run:
            segs.append({"code": code, "days": run})
    return [s for s in segs if len(s["days"]) >= min_len]


def segment_stats(seg: dict, panel: dict, regime_by_day: dict, family_map: dict,
                  bench: dict | None = None) -> dict | None:
    """单个主线段的收益统计 + 段内累计收益峰值日 θ（=最优停止点，事后视角）。

    θ 给两套：绝对（板块自身涨幅，回答「哪一天该落袋」）与超额（减中证1000 同日，
    回答「哪一天不再跑赢」）——后者是【总计划】§1.1 口径红线要求的，也是 P1-1 Kelly 的输入。
    """
    bench = bench or {"closes": {}, "days": [], "order": {}}
    days = seg["days"]
    rows = [panel[(d, seg["code"])] for d in days if (d, seg["code"]) in panel]
    rets = [r["ret_pct"] for r in rows if r["ret_pct"] is not None]
    if len(rets) < 2:
        return None  # 缺失≠零分：不足以刻画一段收益就不进标定
    mu = statistics.mean(rets)
    sigma = statistics.stdev(rets) if len(rets) > 1 else 0.0
    cum, cum_series = 1.0, []
    for r in rets:
        cum *= 1.0 + r / 100.0
        cum_series.append((cum - 1.0) * 100.0)
    theta = max(range(len(cum_series)), key=lambda i: cum_series[i]) + 1  # 1-based
    start_day, end_day = rows[0]["day"], rows[-1]["day"]

    excess = []
    for r in rows:
        if r["ret_pct"] is None:
            continue
        b = bench_ret_on(bench["closes"], bench["days"], bench["order"], r["day"])
        if b is not None:
            excess.append(r["ret_pct"] - b)
    ex_series: list[float] = []
    acc = 1.0
    for x in excess:
        acc *= 1.0 + x / 100.0
        ex_series.append((acc - 1.0) * 100.0)
    theta_excess = (max(range(len(ex_series)), key=lambda i: ex_series[i]) + 1
                    if len(ex_series) >= 2 else None)

    return {
        "code": seg["code"],
        "name": rows[-1].get("name"),
        "start": start_day,
        "end": end_day,
        "n_days": len(rets),
        "n_days_span": len(days),
        "n_days_observed": len(rows),
        "family": family_map.get(seg["code"], UNKNOWN),
        "regime": regime_by_day.get(start_day, UNKNOWN),
        "ret_pct": rets,
        "excess_pct": excess,
        "cum_ret_pct": round(cum_series[-1], 4),
        "mu_daily_pct": round(mu, 4),
        "sigma_daily_pct": round(sigma, 4),
        "theta_day": theta,
        "theta_ret_pct": round(cum_series[theta - 1], 4),
        "giveback_pct": round(cum_series[theta - 1] - cum_series[-1], 4),
        "peaked_before_end": theta < len(rets),
        "cum_excess_pct": round(ex_series[-1], 4) if ex_series else None,
        "mu_daily_excess_pct": round(statistics.mean(excess), 4) if excess else None,
        "theta_excess_day": theta_excess,
        "giveback_excess_pct": (round(ex_series[theta_excess - 1] - ex_series[-1], 4)
                                if theta_excess and ex_series else None),
        "selfreport_mainline_days": rows[-1].get("mainline_days"),
    }


# ─────────────────────────────────────────────────────────────
# 3) 分布 / pooled 汇总
# ─────────────────────────────────────────────────────────────

def _p(values: list[float]) -> dict:
    """分位摘要；空输入 → 全 None（消费方据此回退，不虚构 0）。"""
    if not values:
        return {"p25": None, "p50": None, "p75": None, "mean": None, "n": 0}
    s = sorted(values)
    q = lambda pp: s[min(len(s) - 1, int(math.floor(len(s) * pp)))]
    return {"p25": round(q(0.25), 4), "p50": round(q(0.50), 4),
            "p75": round(q(0.75), 4), "mean": round(statistics.mean(s), 4), "n": len(s)}


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def prior_cdf_theta(segments: list[dict], key: str = "theta_day") -> list[float]:
    """P(θ ≤ t)，t=1..THETA_MAX_DAY。

    这是 P0-1 冷启动的**无观测先验**：新持仓进入主线第 t 天、尚无破位/背离证据时，
    「这段还能涨到第几天」的基准分布。θ>THETA_MAX_DAY 的段**不**计入末位（故末位可 <1，
    剩下的质量就是「仍在线上」，尾部计数另见 p_peak_by_day 的 >上限 桶）。混合两个 CDF
    仍是 CDF，故分层箱可逐元素收缩。θ 缺失（超额无读数）的段不入该分布。
    """
    thetas = [s[key] for s in segments if s.get(key)]
    if not thetas:
        return []
    n = len(thetas)
    return [round(sum(1 for t in thetas if t <= day) / n, 4)
            for day in range(1, THETA_MAX_DAY + 1)]


def edge_next_day(segments: list[dict], field: str = "ret_pct",
                  label: str = "after_day") -> list[dict]:
    """第 t 天之后「再多持有一天」的期望收益（段内实测，pooled）。

    P0-1 的直接判据：edge<0 的 t 就是数据说话「该走」的位置；样本不足的天如实给 n。
    """
    out = []
    for t in range(1, THETA_MAX_DAY + 1):
        obs = [s[field][t] for s in segments if len(s.get(field) or []) > t]
        out.append({label: t, "n": len(obs),
                    "mean_ret_pct": round(statistics.mean(obs), 4) if obs else None,
                    "win_rate": round(sum(1 for x in obs if x > 0) / len(obs), 4) if obs else None})
    return out


def p_peak_by_day(segments: list[dict]) -> list[dict]:
    n = len(segments)
    if not n:
        return []
    out = []
    for t in range(1, THETA_MAX_DAY + 1):
        c = sum(1 for s in segments if s["theta_day"] == t)
        out.append({"day": t, "count": c, "share": round(c / n, 4)})
    tail = sum(1 for s in segments if s["theta_day"] > THETA_MAX_DAY)
    out.append({"day": f">{THETA_MAX_DAY}", "count": tail, "share": round(tail / n, 4)})
    return out


def summarize(segments: list[dict], label: str) -> dict:
    if not segments:
        return {"label": label, "segments": 0, "sector_days": 0, "status": "empty"}
    peaked = sum(1 for s in segments if s["peaked_before_end"])
    lo, hi = wilson_interval(peaked, len(segments))
    mus = [s["mu_daily_pct"] for s in segments]
    sigmas = [s["sigma_daily_pct"] for s in segments]
    ex_mus = [s["mu_daily_excess_pct"] for s in segments if s["mu_daily_excess_pct"] is not None]
    return {
        "label": label,
        "segments": len(segments),
        "sector_days": sum(s["n_days"] for s in segments),
        "unique_sectors": len({s["code"] for s in segments}),
        "cum_ret_pct": _p([s["cum_ret_pct"] for s in segments]),
        "mu_daily_pct": _p(mus),
        "sigma_daily_pct": _p(sigmas),
        "cum_excess_pct": _p([s["cum_excess_pct"] for s in segments
                              if s["cum_excess_pct"] is not None]),
        "mu_daily_excess_pct": _p(ex_mus),
        "theta_day": _p([float(s["theta_day"]) for s in segments]),
        "theta_excess_day": _p([float(s["theta_excess_day"]) for s in segments
                                if s["theta_excess_day"]]),
        "giveback_pct": _p([s["giveback_pct"] for s in segments]),
        "peak_beats_end_rate": {"point": round(peaked / len(segments), 4),
                                "wilson95": [round(lo, 4), round(hi, 4)]},
        "prior_cdf_theta": prior_cdf_theta(segments, "theta_day"),
        "prior_cdf_theta_excess": prior_cdf_theta(segments, "theta_excess_day"),
        "p_peak_by_day": p_peak_by_day(segments),
    }


def shrink_scalar(bin_val, pooled_val, n: int, k: int):
    """hierarchical shrinkage：w = n/(n+k)，w·箱内值 + (1−w)·全局值。

    箱内值为 None（该箱无该指标）→ 直接给全局值；全局也 None → None（不虚构 0）。
    """
    if bin_val is None:
        return pooled_val if pooled_val is not None else None
    if pooled_val is None:
        return bin_val
    w = n / (n + k) if (n + k) > 0 else 1.0
    return round(w * bin_val + (1.0 - w) * pooled_val, 4)


def shrink_cdf(bin_cdf: list[float], pooled_cdf: list[float], n: int, k: int) -> list[float]:
    if not bin_cdf:
        return list(pooled_cdf or [])
    if not pooled_cdf:
        return bin_cdf
    w = n / (n + k) if (n + k) > 0 else 1.0
    return [round(w * a + (1.0 - w) * b, 4) for a, b in zip(bin_cdf, pooled_cdf)]


def build_bins(segments: list[dict], pooled: dict, min_samples: int, k: int) -> list[dict]:
    """板块家族 × regime 分箱；样本 < min_samples 的箱如实标 shrunk/insufficient，
    数值一律经 shrinkage 回退全局——**箱不得为空**（有段就有 pooled 兜底值）。"""
    groups: dict[tuple[str, str], list[dict]] = {}
    for s in segments:
        groups.setdefault((s["family"], s["regime"]), []).append(s)
    bins = []
    for (family, regime), segs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        n = len(segs)
        raw = summarize(segs, f"{family} × {regime}")
        w = n / (n + k) if (n + k) > 0 else 1.0
        pp = raw["peak_beats_end_rate"]["point"]
        bins.append({
            "family": family,
            "regime": regime,
            "segments": n,
            "sector_days": raw["sector_days"],
            "unique_sectors": raw["unique_sectors"],
            "shrink_weight": round(w, 4),
            "status": "ok" if n >= min_samples else "shrunk",
            "mu_daily_pct": shrink_scalar(raw["mu_daily_pct"]["mean"],
                                          pooled["mu_daily_pct"]["mean"], n, k),
            "mu_daily_excess_pct": shrink_scalar(raw["mu_daily_excess_pct"]["mean"],
                                                 pooled["mu_daily_excess_pct"]["mean"], n, k),
            "sigma_daily_pct": shrink_scalar(raw["sigma_daily_pct"]["mean"],
                                             pooled["sigma_daily_pct"]["mean"], n, k),
            "theta_p50_days": shrink_scalar(raw["theta_day"]["p50"],
                                            pooled["theta_day"]["p50"], n, k),
            "cum_ret_p50_pct": shrink_scalar(raw["cum_ret_pct"]["p50"],
                                             pooled["cum_ret_pct"]["p50"], n, k),
            "cum_excess_p50_pct": shrink_scalar(raw["cum_excess_pct"]["p50"],
                                                pooled["cum_excess_pct"]["p50"], n, k),
            "giveback_p50_pct": shrink_scalar(raw["giveback_pct"]["p50"],
                                              pooled["giveback_pct"]["p50"], n, k),
            "peak_beats_end_rate": shrink_scalar(pp, pooled["peak_beats_end_rate"]["point"], n, k),
            "prior_cdf_theta": shrink_cdf(raw["prior_cdf_theta"], pooled["prior_cdf_theta"], n, k),
            "prior_cdf_theta_excess": shrink_cdf(raw["prior_cdf_theta_excess"],
                                                 pooled["prior_cdf_theta_excess"], n, k),
            # 裸值（未收缩）只留 scalar，便于审阅收缩幅度；p_peak_by_day 只在 pooled 给，控制工件体积
            "raw": {k: v for k, v in raw.items()
                    if k in ("segments", "cum_ret_pct", "mu_daily_pct", "sigma_daily_pct",
                             "mu_daily_excess_pct", "cum_excess_pct", "theta_day",
                             "theta_excess_day", "giveback_pct", "peak_beats_end_rate")},
        })
    return bins


# ─────────────────────────────────────────────────────────────
# 4) 段后续涨（可选 K 线）/ wave_calibration 交叉核对
# ─────────────────────────────────────────────────────────────

def attach_post_segment_returns(segments: list[dict], closes_by_code: dict,
                               bench: dict | None = None) -> dict:
    """段结束日 → +1/+3/+5 交易日的板块指数收益（%），供 P0-1 判断「清仓后是否踏空」。

    只在提供日线缓存时可得；缺失板块计入分母外（覆盖率如实输出）。同时给减基准超额。
    """
    bench = bench or {"closes": {}, "days": [], "order": {}}
    filled = {1: [], 3: [], 5: []}
    filled_ex = {1: [], 3: [], 5: []}
    hit = miss = 0
    for s in segments:
        closes = closes_by_code.get(s["code"])
        if not closes:
            miss += 1
            continue
        cal = sorted(closes)
        idx = {d: i for i, d in enumerate(cal)}
        end_i = idx.get(s["end"])
        if end_i is None:
            miss += 1
            continue
        base = closes[cal[end_i]]
        got_any = False
        for h in (1, 3, 5):
            if end_i + h < len(cal) and base > 0:
                v = (closes[cal[end_i + h]] / base - 1.0) * 100.0
                filled[h].append(v)
                s[f"fwd{h}_pct"] = round(v, 4)
                b = bench_fwd(bench["closes"], bench["days"], bench["order"], s["end"], h)
                if b is not None:
                    filled_ex[h].append(v - b)
                    s[f"fwd{h}_excess_pct"] = round(v - b, 4)
                got_any = True
        hit += 1 if got_any else 0
        miss += 0 if got_any else 1
    return {
        "kline_sectors_available": len(closes_by_code),
        "segments_with_kline": hit,
        "segments_without_kline": miss,
        "fwd_after_segment": {f"fwd{h}": _p(v) for h, v in filled.items()},
        "fwd_after_segment_excess": {f"fwd{h}": _p(v) for h, v in filled_ex.items()},
    }


def wave_crosscheck(path: Path, segments: list[dict]) -> dict | None:
    """与 data/wave_calibration.jsonl（生产自报 wave_days）对表：
    验证「主线寿命 93.5%<7 天」在本面板上是否同向，属旁证非输入。"""
    if not path or not path.exists() or not segments:
        return None
    vals = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            wd = rec.get("wave_days")
            if isinstance(wd, (int, float)):
                vals.append(float(wd))
    except OSError:
        return None
    if not vals:
        return None
    mine = [float(s["n_days"]) for s in segments]
    under7 = lambda xs: round(sum(1 for x in xs if x < 7) / len(xs), 4)
    return {
        "wave_calibration_rows": len(vals),
        "wave_days_p50": round(statistics.median(vals), 2),
        "wave_days_share_lt7": under7(vals),
        "segment_len_p50": round(statistics.median(mine), 2),
        "segment_len_share_lt7": under7(mine),
        "verdict": "同向" if abs(under7(vals) - under7(mine)) <= 0.25 else "背离（需人工核对口径）",
    }


# ─────────────────────────────────────────────────────────────
# 5) 工件装配
# ─────────────────────────────────────────────────────────────

def build_artifact(as_of: date, args) -> tuple[dict, list[dict]]:
    archive = Path(args.archive_dir)
    days = list_archive_days(archive, as_of)
    if not days:
        raise SystemExit(f"[GATE-2] 归档为空：{archive}（as_of={as_of}）——无数据不产出工件")
    panel, regime_by_day = load_panel(archive, days, include_exhausted=args.include_exhausted)
    family_map = build_family_map(panel, as_of)
    b_closes, b_days, b_order = load_benchmark(Path(args.benchmark))
    bench = {"closes": b_closes, "days": b_days, "order": b_order}
    calendar = days  # 归档日序列即交易日历子集（生产每个交易日跑一次）
    raw_segs = build_segments(panel, calendar, args.max_gap_days, args.min_len)
    segments = [x for x in (segment_stats(s, panel, regime_by_day, family_map, bench)
                            for s in raw_segs) if x]

    # 右删失：段尾落在归档最后 N 天的段，它们的真实峰值可能在 as_of 之后，现在被硬生生
    # 截断 → θ 系统性偏小（与「追高就该走」同伪象）。默认剔除并如实计数。
    tail_days = set(days[-args.censor_tail_days:]) if args.censor_tail_days > 0 else set()
    censored = [s for s in segments if s["end"] in tail_days]
    segments = [s for s in segments if s["end"] not in tail_days]

    closes_by_code = {}
    if not args.no_kline:
        closes_by_code = load_kline_closes(Path(args.kline_dir))
    post = attach_post_segment_returns(segments, closes_by_code, bench) if closes_by_code else {
        "kline_sectors_available": 0, "segments_with_kline": 0,
        "segments_without_kline": len(segments),
        "fwd_after_segment": {"note": "未提供/未找到板块日线缓存，段后续涨不可得（默认不触网）"},
        "fwd_after_segment_excess": {"note": "同上"},
    }

    pooled = summarize(segments, "pooled(all families × all regimes)")
    pooled["status"] = "ok" if pooled.get("segments", 0) >= args.min_samples else "insufficient_sample"
    pooled["edge_next_day_pct"] = edge_next_day(segments, "ret_pct", "after_day")
    pooled["edge_next_day_excess_pct"] = edge_next_day(segments, "excess_pct", "after_day")
    pooled["benchmark"] = {"file": str(args.benchmark), "days": len(b_days),
                           "available": bool(b_days)}
    bins = build_bins(segments, pooled, args.min_samples, args.shrink_k) if segments else []

    role_split = {"unified_role": 0, "sector_state": 0}
    for row in panel.values():
        role_split[row["role_source"]] = role_split.get(row["role_source"], 0) + 1
    ret_missing = sum(1 for row in panel.values() if row["ret_pct"] is None)
    no_sector_entry = sum(1 for row in panel.values() if not row["has_sector_entry"])
    selfrep = [(s["selfreport_mainline_days"], s["n_days"]) for s in segments
               if s["selfreport_mainline_days"]]
    selfrep_diff = [abs(a - b) for a, b in selfrep]

    artifact = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "as_of": as_of.isoformat(),
            "source": "docs/got/plans/architecture_optimization_implementation_20260929.md §2 GATE-2",
            "archive_days": len(days),
            "archive_range": [days[0], days[-1]],
            "regime_days_missing": sum(1 for v in regime_by_day.values() if v == UNKNOWN),
            "mainline_sector_days": len(panel),
            "mainline_role_source_split": role_split,
            "today_change_missing_rows": ret_missing,
            "mainline_rows_without_sector_entry": no_sector_entry,
            "segments_total": len(raw_segs),
            "segments_usable": len(segments),
            "segments_right_censored_dropped": len(censored),
            "censor_tail_days": args.censor_tail_days,
            "min_segment_len": args.min_len,
            "include_exhausted": bool(args.include_exhausted),
            "max_gap_calendar_days": args.max_gap_days,
            "min_samples_per_bin": args.min_samples,
            "shrink_k": args.shrink_k,
            "theta_max_day": THETA_MAX_DAY,
            "point_in_time": ("只读 as_of 之前归档；段内收益取当日归档 today_change（生产所见），"
                              "不看未来价格；唯一跨期字段是板块→家族静态映射（见 caveats）"),
            "family_unresolved_segments": sum(1 for s in segments if s["family"] == UNKNOWN),
            "benchmark_available": bool(b_days),
            "segments_with_excess": sum(1 for s in segments if s["cum_excess_pct"] is not None),
            "selfreport_mainline_days_check": {
                "n": len(selfrep_diff),
                "abs_diff_p50": round(statistics.median(selfrep_diff), 2) if selfrep_diff else None,
                "note": "实测段长 vs 生产自报 mainline_days 的中位绝对差（口径应接近，偏差大说明切段判据漂了）",
            },
        },
        "pooled": pooled,
        "bins_family_x_regime": bins,
        "post_segment_projection": post,
        "wave_calibration_crosscheck": wave_crosscheck(Path(args.wave_jsonl), segments),
        "consumer_contract": {
            "P0-1": ("清仓后验的**先验**：新主线持仓第 t 日无观测时用 pooled.prior_cdf_theta；"
                     "拿到持仓板块家族 + 当日 regime 后查 bins_family_x_regime 同名字段（"
                     "两值都已 shrinkage，直接当作先验用，不必再回退）"),
            "P0-1/excess": ("同一位置另给 prior_cdf_theta_excess（减中证1000 后不再跑赢的日子）；"
                            "退出决策看绝对值，「是否还值得占额度」看超额值，两套都不得自己混用"),
            "P0-1/reject": ("bin.status=shrunk 且 shrink_weight<0.3 时视为弱箱：与 pooled 混合再取，"
                            "绝不把单箱裸值当后验"),
            "P0-1/edge": ("pooled.edge_next_day_pct / edge_next_day_excess_pct 给出「第 t 日后再持有"
                          "一天」的实测期望，为观测证据权重（破位/背离之外）提供基线"),
            "P1-1": ("Kelly/vol-target 用 pooled.mu_daily_excess_pct / sigma_daily_pct 的 pooled 期望"
                     "（【总计划】§1.1 禁裸绝对收益），禁止用单持仓点估计（§10 已否决）"),
            "empty": ("pooled.status=insufficient_sample 时消费方必须保持 legacy 查表分支，"
                      "禁止拿空工件上线"),
        },
        "caveats": [
            "右删失处理：段尾在归档最后 --censor-tail-days 天的段已剔除（计数见 meta）——它们的 θ 只能看到截至日，留着会把峰值拉早",
            "幸存者偏差：面板只含「被生产判为主线」的板块-日，θ/收益分布 conditioned on 选股判据本身",
            "判据漂移：早期归档（20260807 前）无 sector_role，主线判定退回 sector.json state；wave/冷却规则 v3.5 后才稳定，跨期段不完全同质",
            "样本窗口仅 %d 个交易日、%d 段，家族×regime 多数箱必然 shrunk——先验可用但不可当精确最优点" % (len(days), len(segments)),
            "today_change 是板块指数涨跌幅，未扣任何可成交性（涨跌停/流动性属 GATE-1），θ 不等于可执行退出日",
            "板块→家族映射取 as_of 前最近已知值：属静态分类学而非价格前视，但早期归档缺该字段，未解析箱记 unknown",
            "段后续涨依赖本地板块日线缓存（默认不触网）；覆盖率见 post_segment_projection，缺缓存时该节不可得",
            "regime 早期归档为 None → 计入 unknown 箱，bull/bear/transition 的对比只在 20260805 之后有意义",
            "主线日在 sector.json 里查不到条目时（ret_pct=None）仍计为段内一天，累计收益只连乘可见读数，θ 会有 ±1 日偏位（缺失计数见 meta）",
            "超额为日频简单差（板块涨跌% − 基准涨跌%）后连乘，未用几何对数差；excess 子列只包含基准有读数的日，与绝对子列可能少1天，θ_excess 因此只能看作近似对齐",
        ],
    }
    return artifact, segments


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GATE-2 最优停止 pooled/分层先验标定（离线）")
    ap.add_argument("--as-of", type=str, default=None, help="point-in-time 截止日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--out", type=str, default=str(DEFAULT_OUT))
    ap.add_argument("--archive-dir", type=str, default=str(ARCHIVE_DIR))
    ap.add_argument("--min-len", type=int, default=2, help="主线段最小交易日数")
    ap.add_argument("--max-gap-days", type=int, default=5, help="段内允许的相邻主线日最大日历天间隔（容周末/长假）")
    ap.add_argument("--min-samples", type=int, default=12, help="分箱达阈值（不足则 shrunk）")
    ap.add_argument("--censor-tail-days", type=int, default=1,
                    help="剔除段尾落在归档最后 N 天的右删失段（0=不剔除，θ 会偏小）")
    ap.add_argument("--shrink-k", type=int, default=8, help="shrinkage 强度：w=n/(n+k)")
    ap.add_argument("--kline-dir", type=str, default=str(DEFAULT_KLINE_DIR),
                    help="板块日线缓存目录（仅用于段后续涨；缺失即降级，不触网）")
    ap.add_argument("--benchmark", type=str, default=str(DEFAULT_BENCH_CSV),
                    help="基准指数日线 CSV（中证1000），用于减基准超额列")
    ap.add_argument("--no-kline", action="store_true", help="完全跳过段后续涨估计")
    ap.add_argument("--wave-jsonl", type=str, default=str(DEFAULT_WAVE_JSONL))
    ap.add_argument("--include-exhausted", action="store_true",
                    help="敏感性对照：把 exhausted_mainline 也当主线段（默认排除，见 load_panel 注释）")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    artifact, segments = build_artifact(as_of, args)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")

    pooled = artifact["pooled"]
    ok_bins = sum(1 for b in artifact["bins_family_x_regime"] if b["status"] == "ok")
    logger.info(
        "[GATE-2] 工件已写出 %s（归档日=%d, 主线段=%d/%d 可用, pooled=%s, "
        "θ_p50=%s, 家族×regime 箱=%d 其中达标=%d, 段外K线覆盖=%d/%d）",
        out_path, artifact["meta"]["archive_days"], len(segments),
        artifact["meta"]["segments_total"], pooled["status"],
        pooled["theta_day"]["p50"], len(artifact["bins_family_x_regime"]), ok_bins,
        artifact["post_segment_projection"]["segments_with_kline"], len(segments),
    )
    if pooled["status"] == "insufficient_sample":
        logger.warning("[GATE-2] pooled 样本不足——消费方（P0-1/P1-1）必须保持 legacy 分支")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
