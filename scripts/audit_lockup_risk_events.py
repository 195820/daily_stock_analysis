#!/usr/bin/env python
"""P2-4 · 解禁（lockup）利空事件 risk_events 通道：解冻前置取证（离线只读）。

来源：docs/got/plans/architecture_optimization_implementation_20260929.md §6 WI-P2-4
（O3b「利空事件 risk_events 接入退出」）+ §11.2（本轮不启动：依赖 GATE-1/P0-1）。
现状代码事实：`catalyst_calendar.py` 解禁被抓取（`_fetch_lockup_calendar`）却在
`_full_build`/`_build_structured_events` 明确丢弃，注释写「未来由独立 risk_events 通道
消费」，而该通道不存在。

**本脚本不建通道、不接在线决策、不改生产代码**，只回答「这个通道现在值不值得立」：

  Q1 数据地基 —— 解禁事件在本地离线的可得性：历史样本量、个股→板块扇出结构、
      入库断流点（丢弃生效后 `catalyst_events` 是否还有 lockup）。
  Q2 负向证据强度 —— 解禁板块 T+1/3/5 板块收益**减基准**后是否显著为负。
      生产落库的 `day_N_return`/`actual_return` 是**未减基准的裸板块收益**
      （catalyst_realization._compute_multi_window_returns），而【总计划】§1.1 红线
      要求一切收益读数减基准；不减基准就判「解禁是否利空」会把大盘 beta 当信号。
  Q3 可接线性 —— 解禁事件与埋伏/持仓快照的时间交集（「解禁临近持仓」样本数）。
      P0-1 退出后验要消费的就是这一格；空格 = 接线没有观测地基。

口径纪律（禁第二套口径）：
  - 板块名→板码：复用生产 `CatalystRealizationTracker._get_board_code`（三级解析；
    `object.__new__` 跳过 `__init__`，不建 DB 连接）
  - 收益锚定：与生产 `_find_event_date_index` + `_compute_multi_window_returns` 同锚
    （事件日收盘 → T+N 收盘，按该板码自身 K 线交易日序列，精确匹配失败回退 ±3 日），
    并逐条与 DB 已落库的 day_1/day_3/day_5 **对账**（reconcile），对不上就如实报覆盖率
  - 基准：`.review_tmp/bt_cache/index_sh000852.csv`（中证1000）——与 GATE-2 标定同文件
    同口径；缺失时超额节诚实返回 n=0，不影响裸收益读数
  - 比例类置信区间：复用 `calibrate_optimal_stop.wilson_interval`
  - 板块×事件日去重：同板同日多只解禁股会重复计同一次板块移动，统计口径必须先聚合

数据来源（全部本地；默认不触网；DB 只以 `mode=ro` 打开，绝不写）：
  - `data/stock_analysis.db`：`catalyst_realizations`（含 lockup 历史残留）、
    `catalyst_events`（在线日历，验证断流）、`ambush_position_snapshots`、`portfolio_positions`
  - `.review_tmp/bt880_cache/{code}.csv`（板块日线：date=YYYYMMDD,close）
  - `.review_tmp/bt_cache/index_sh000852.csv`（基准指数日线：date=YYYY-MM-DD,close）

用法：
    python scripts/audit_lockup_risk_events.py [--as-of YYYY-MM-DD] [--near-td 5]
        [--db PATH] [--kline-dir PATH] [--benchmark PATH] [--out PATH] [--json-only]

产物：data/lockup_risk_evidence.json + 控制台摘要。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))

DEFAULT_DB = ROOT_DIR / "data" / "stock_analysis.db"
DEFAULT_KLINE_DIR = ROOT_DIR / ".review_tmp" / "bt880_cache"
DEFAULT_BENCHMARK = ROOT_DIR / ".review_tmp" / "bt_cache" / "index_sh000852.csv"
DEFAULT_OUT = ROOT_DIR / "data" / "lockup_risk_evidence.json"

WINDOWS: Tuple[int, ...] = (1, 3, 5)
RECON_TOL_PCT = 0.05          # 与生产落库值对账容差（百分点）
SIGN_ALPHA = 0.05             # 符号检验显著性水平
CODE_RE = re.compile(r"\((\d{6})\)")


def wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """复用 GATE-2 标定件的 Wilson 区间（同一口径，勿另写）。"""
    from calibrate_optimal_stop import wilson_interval as _wi

    return _wi(successes, n, z)


# ─────────────────────────────────────────────────────────────
# 1) 生产件复用：板块名 → 板码
# ─────────────────────────────────────────────────────────────

def production_board_code_resolver() -> Tuple[object, str]:
    """拿到生产 `_get_board_code`，不建 DB / 不发网络。

    Returns:
        (resolver, note)：resolver 是 CatalystRealizationTracker 实例（未 __init__），
        note 为降级原因（导入失败时调用方须如实披露而不是自写模糊匹配）。
    """
    try:
        from src.got.catalyst_realization import CatalystRealizationTracker

        tracker = object.__new__(CatalystRealizationTracker)
        return tracker, ""
    except Exception as exc:  # noqa: BLE001 - 取证脚本降级优先于崩溃
        return None, f"生产件导入失败: {exc!r}"


def resolve_board_codes(
    resolver: Optional[object], sector_names: Iterable[str]
) -> Dict[str, Tuple[Optional[str], str]]:
    """sector → (board_code, match_source)，逐条走生产三级解析。"""
    out: Dict[str, Tuple[Optional[str], str]] = {}
    for name in sorted(set(sector_names)):
        if resolver is None:
            out[name] = (None, "resolver_unavailable")
            continue
        try:
            code, source = resolver._get_board_code(name)  # noqa: SLF001 - 故意复用生产件
        except Exception as exc:  # noqa: BLE001
            code, source = (None, f"error:{exc!r}")
        out[name] = (code, source)
    return out


# ─────────────────────────────────────────────────────────────
# 2) 本地日线（板块 + 基准）
# ─────────────────────────────────────────────────────────────

def _norm_day(raw: str) -> str:
    """YYYYMMDD / YYYY-MM-DD → YYYY-MM-DD（统一锚定用）。"""
    s = str(raw).strip()[:10]
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def read_price_series(path: Path, date_col: str = "date") -> List[Tuple[str, float]]:
    """读 CSV → [(ISO 日期, 收盘)]，按日期升序；文件缺失/无列返回 []。

    只依赖 `date`/`close` 两列，不引入 pandas（与既有取证脚本一致的轻口径）。
    """
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return []
    header = [c.strip() for c in lines[0].split(",")]
    try:
        di, ci = header.index(date_col), header.index("close")
    except ValueError:
        return []
    series: List[Tuple[str, float]] = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) <= max(di, ci):
            continue
        try:
            series.append((_norm_day(parts[di]), float(parts[ci])))
        except (TypeError, ValueError):
            continue
    series.sort(key=lambda t: t[0])
    return series


def find_anchor_index(
    series: List[Tuple[str, float]], event_date: date, tolerance_days: int = 3
) -> Optional[int]:
    """事件日锚定（镜像生产 `_find_event_date_index`：精确优先，再 ±1..±3 回退）。

    回退顺序与生产一致：delta=1..3，每个 delta 先 +后 −。
    """
    if not series:
        return None
    by_day = {d: i for i, (d, _) in enumerate(series)}
    target = event_date.isoformat()
    if target in by_day:
        return by_day[target]
    for delta in range(1, tolerance_days + 1):
        for sign in (1, -1):
            alt = (event_date + timedelta(days=sign * delta)).isoformat()
            if alt in by_day:
                return by_day[alt]
    return None


def anchor_window_returns(
    series: List[Tuple[str, float]],
    base_idx: int,
    offsets: Tuple[int, ...] = WINDOWS,
) -> Dict[int, Optional[float]]:
    """T+N = close[base+N]/close[base] − 1（%，板块/指数通用，事件日收盘为基准）。

    base 下标由 `find_anchor_index` 给出；窗口越界返回 None（与生产同构）。
    """
    out: Dict[int, Optional[float]] = {}
    if base_idx < 0 or base_idx >= len(series):
        return {o: None for o in offsets}
    base_price = series[base_idx][1]
    if base_price <= 0:
        return {o: None for o in offsets}
    for off in offsets:
        tgt = base_idx + off
        if tgt >= len(series):
            out[off] = None
            continue
        out[off] = round((series[tgt][1] - base_price) / base_price * 100, 4)
    return out


# ─────────────────────────────────────────────────────────────
# 3) DB 只读装载
# ─────────────────────────────────────────────────────────────

def open_readonly(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_lockup_events(con: sqlite3.Connection, as_of: date) -> List[dict]:
    """历史 lockup 兑现记录（≤ as_of，point-in-time）。"""
    rows = con.execute(
        "SELECT * FROM catalyst_realizations WHERE event_type='lockup' "
        "AND event_date <= ? ORDER BY event_date, sector",
        (as_of.isoformat(),),
    ).fetchall()
    return [dict(r) for r in rows]


def load_event_type_census(con: sqlite3.Connection) -> Dict[str, Dict[str, int]]:
    """两张表的 event_type 计数：证明在线日历已断流、只残留在历史兑现表。"""
    census: Dict[str, Dict[str, int]] = {}
    for table in ("catalyst_events", "catalyst_realizations"):
        try:
            got = con.execute(
                f"SELECT event_type, COUNT(*) c, MAX(event_date) mx FROM {table} "
                "GROUP BY event_type"
            ).fetchall()
        except sqlite3.Error:
            continue
        census[table] = {
            "by_type": {str(r["event_type"]): r["c"] for r in got},
            "max_event_date": {str(r["event_type"]): str(r["mx"]) for r in got},
        }
    return census


def load_ambush_snapshots(con: sqlite3.Connection, as_of: date) -> List[dict]:
    try:
        rows = con.execute("SELECT * FROM ambush_position_snapshots ORDER BY entry_date").fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        d = dict(r)
        entry = _norm_day(str(d.get("entry_date") or ""))
        if entry and entry > as_of.isoformat():
            continue
        d["entry_date_iso"] = entry
        out.append(d)
    return out


def count_open_positions(con: sqlite3.Connection) -> int:
    try:
        return con.execute("SELECT COUNT(*) c FROM portfolio_positions").fetchone()["c"]
    except sqlite3.Error:
        return 0


# ─────────────────────────────────────────────────────────────
# 4) 样本构造（扇出 / 板块日去重）
# ─────────────────────────────────────────────────────────────

def stock_code_of(title: str) -> Optional[str]:
    m = CODE_RE.search(str(title or ""))
    return m.group(1) if m else None


def dedupe_board_day(events: List[dict]) -> List[dict]:
    """(sector, event_date) 聚合：同板同日多只解禁股只算一次板块移动。

    返回按 (event_date, sector) 排序的样本，附 fanout 计数与个股清单，
    统计用「板块日样本」，披露用原始行数——两者都必须在产物里可见。
    """
    buckets: Dict[Tuple[str, str], dict] = {}
    for ev in events:
        sector = str(ev.get("sector") or "")
        day = _norm_day(str(ev.get("event_date") or ""))
        if not sector or not day:
            continue
        key = (sector, day)
        b = buckets.get(key)
        if b is None:
            b = {
                "sector": sector,
                "event_date": day,
                "titles": [],
                "stock_codes": [],
                "impact_levels": [],
                "confidences": [],
                "db_day_returns": {},
            }
            buckets[key] = b
        b["titles"].append(str(ev.get("title") or ""))
        code = stock_code_of(ev.get("title"))
        if code:
            b["stock_codes"].append(code)
        b["impact_levels"].append(ev.get("impact_level"))
        b["confidences"].append(ev.get("confidence"))
        for off in WINDOWS:
            val = ev.get(f"day_{off}_return")
            if val is not None:
                prev = b["db_day_returns"].get(off)
                if prev is None:
                    b["db_day_returns"][off] = float(val)
    return [buckets[k] for k in sorted(buckets)]


def fanout_stats(events: List[dict]) -> dict:
    """个股级事件数 vs 事件×板块行数：解禁是个股事件扇出成板块，扇出倍数须披露。"""
    per_stock: Dict[Tuple[str, str], int] = {}
    for ev in events:
        code = stock_code_of(ev.get("title")) or (str(ev.get("title") or ""))
        key = (code, _norm_day(str(ev.get("event_date") or "")))
        per_stock[key] = per_stock.get(key, 0) + 1
    mult = sorted(per_stock.values(), reverse=True)
    return {
        "raw_rows": len(events),
        "distinct_stock_events": len(per_stock),
        "distinct_sectors": len({str(e.get("sector") or "") for e in events}),
        "boards_per_stock_event_mean": round(sum(mult) / len(mult), 2) if mult else 0.0,
        "boards_per_stock_event_max": mult[0] if mult else 0,
    }


# ─────────────────────────────────────────────────────────────
# 5) 超额收益 + 与生产落库值对账
# ─────────────────────────────────────────────────────────────

def reconcile(mine: Optional[float], stored: Optional[float], tol: float = RECON_TOL_PCT) -> str:
    """同锚重算值 vs 生产落库值：match / diff / na（不可比）。"""
    if mine is None or stored is None:
        return "na"
    return "match" if abs(mine - stored) <= tol else "diff"


def build_board_day_returns(
    samples: List[dict],
    codes: Dict[str, Tuple[Optional[str], str]],
    board_series: Dict[str, List[Tuple[str, float]]],
    benchmark: List[Tuple[str, float]],
) -> List[dict]:
    """逐板块日样本：裸板块 T+N + 基准 T+N + 超额 + 对账标签。"""
    enriched: List[dict] = []
    for s in samples:
        code, match_source = codes.get(s["sector"], (None, "missing"))
        row = dict(s)
        row.update({"board_code": code, "code_match": match_source, "windows": {}})
        series = board_series.get(code or "", [])
        ev_date = date.fromisoformat(s["event_date"])
        if not series:
            row["status"] = "no_kline" if code else ("no_board_code" if match_source != "resolver_unavailable" else "resolver_unavailable")
            enriched.append(row)
            continue
        base = find_anchor_index(series, ev_date)
        if base is None:
            row["status"] = "anchor_out_of_range"
            enriched.append(row)
            continue
        brows = anchor_window_returns(series, base)
        bidx: Dict[int, Optional[float]] = {o: None for o in WINDOWS}
        if benchmark:
            bbase = find_anchor_index(benchmark, ev_date)
            if bbase is not None:
                bidx = anchor_window_returns(benchmark, bbase)
        for off in WINDOWS:
            raw = brows.get(off)
            bm = bidx.get(off)
            stored = s["db_day_returns"].get(off)
            row["windows"][off] = {
                "board_pct": raw,
                "bench_pct": bm,
                "excess_pct": round(raw - bm, 4) if raw is not None and bm is not None else None,
                "db_stored": stored,
                "reconcile": reconcile(raw, stored),
            }
        row["status"] = "ok"
        row["anchor_shift_days"] = abs(
            (date.fromisoformat(series[base][0]) - ev_date).days
        )
        enriched.append(row)
    return enriched


def sign_test(values: List[float], zero_is: str = "drop") -> dict:
    """精确双侧符号检验（H0: P(超额<0)=0.5）。零值按 zero_is 归类并披露。"""
    neg = sum(1 for v in values if v < 0)
    pos = sum(1 for v in values if v > 0)
    zeros = len(values) - neg - pos
    n = neg + pos
    out = {
        "n": len(values), "negative": neg, "positive": pos, "zero": zeros,
        "neg_share": round(neg / n, 4) if n else None,
        "p_two_sided": None,
    }
    if n:
        k = min(neg, pos)
        tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
        out["p_two_sided"] = round(min(1.0, 2 * tail), 6)
    return out


def summarize_window(rows: List[dict], off: int) -> dict:
    """单窗口 pooled 读数：裸收益 / 超额 / 符号检验 / Wilson 区间。"""
    raws = [
        r["windows"][off]["board_pct"]
        for r in rows
        if r.get("windows", {}).get(off, {}).get("board_pct") is not None
    ]
    excs = [
        r["windows"][off]["excess_pct"]
        for r in rows
        if r.get("windows", {}).get(off, {}).get("excess_pct") is not None
    ]
    rec = {}
    for tag in ("match", "diff", "na"):
        rec[tag] = sum(
            1 for r in rows
            if r.get("windows", {}).get(off, {}).get("reconcile") == tag
        )
    res = {
        "window_td": off,
        "n_board": len(raws),
        "board_mean_pct": round(statistics.mean(raws), 4) if raws else None,
        "board_median_pct": round(statistics.median(raws), 4) if raws else None,
        "n_excess": len(excs),
        "excess_mean_pct": round(statistics.mean(excs), 4) if excs else None,
        "excess_median_pct": round(statistics.median(excs), 4) if excs else None,
        "reconcile": rec,
        "reconcile_comparable": rec["match"] + rec["diff"],
        "sign_test": sign_test(excs) if excs else None,
    }
    if excs:
        neg = res["sign_test"]["negative"]
        n = res["sign_test"]["negative"] + res["sign_test"]["positive"]
        lo, hi = wilson_interval(neg, n)
        res["share_below_bench_wilson95"] = [round(lo, 4), round(hi, 4)]
    else:
        res["share_below_bench_wilson95"] = None
    return res


def per_sector_summary(rows: List[dict], off: int = 5) -> List[dict]:
    """板块级最负/最正极值（供人工看是不是被少数板块主导）。"""
    items = [
        {
            "sector": r["sector"],
            "board_code": r.get("board_code"),
            "event_date": r["event_date"],
            "excess_pct": r["windows"][off]["excess_pct"],
            "board_pct": r["windows"][off]["board_pct"],
            "n_stocks": len(r.get("stock_codes") or []),
        }
        for r in rows
        if r.get("windows", {}).get(off, {}).get("excess_pct") is not None
    ]
    items.sort(key=lambda x: x["excess_pct"])
    return {"worst5": items[:5], "best5": items[-5:]} if items else {"worst5": [], "best5": []}


# ─────────────────────────────────────────────────────────────
# 6) Q3：解禁与持仓的可接线性
# ─────────────────────────────────────────────────────────────

def near_position_overlap(
    samples: List[dict],
    ambush_rows: List[dict],
    board_series: Dict[str, List[Tuple[str, float]]],
    codes: Dict[str, Tuple[Optional[str], str]],
    near_td: int = 5,
) -> dict:
    """「持仓入场后 near_td 个交易日内该板块有解禁」的样本计数。

    埋伏快照按 sector_name 对齐（同板码才算）；`portfolio_positions` 无板块字段，
    只能报总仓位数并如实说明口径缺口，不做名字猜测。
    """
    hits: List[dict] = []
    unmatched_names: set[str] = set()
    for a in ambush_rows:
        sector = str(a.get("sector_name") or "")
        entry = a.get("entry_date_iso")
        if not sector or not entry:
            continue
        code = codes.get(sector, (None, ""))[0]
        for s in samples:
            if s["sector"] != sector:
                continue
            series = board_series.get(code or "", [])
            if not series:
                unmatched_names.add(sector)
                continue
            base = find_anchor_index(series, date.fromisoformat(entry))
            if base is None:
                continue
            lock_idx = find_anchor_index(series, date.fromisoformat(s["event_date"]))
            if lock_idx is None:
                continue
            if 0 <= lock_idx - base <= near_td:
                hits.append({
                    "symbol": a.get("symbol"),
                    "sector": sector,
                    "entry_date": entry,
                    "lockup_event_date": s["event_date"],
                    "gap_td": lock_idx - base,
                })
    return {
        "near_td": near_td,
        "ambush_rows": len(ambush_rows),
        "lockup_board_days": len(samples),
        "overlap_count": len(hits),
        "overlap": hits,
        "sectors_without_kline": sorted(unmatched_names),
        "note": "portfolio_positions 无板块字段，持仓侧只报总数（口径缺口，不做名称猜测）",
    }


# ─────────────────────────────────────────────────────────────
# 7) 结论判定（解冻评估）
# ─────────────────────────────────────────────────────────────

def judge_evidence(windows: List[dict], primary_td: int = 5) -> dict:
    """Q2 判定：解禁板块减基准后是跑输（利空成立）/ 跑赢（方向反指）/ 无信号。

    主判据取 `primary_td`（默认 T+5，与退出决策尺度对齐）；该窗不可用时退到
    样本最多的窗。显著性 = 精确符号检验 p < SIGN_ALPHA，方向看超额均值符号。
    """
    usable = [w for w in windows if w["n_excess"] >= 5]
    if not usable:
        return {
            "verdict": "insufficient_sample",
            "direction": "unknown",
            "significant_negative": False,
            "significant_positive": False,
            "reason": "减基准超额样本 <5（板码/K线/基准任一缺失），无统计地基",
            "primary_window": None,
        }
    by_td = {w["window_td"]: w for w in usable}
    primary = by_td.get(primary_td) or max(usable, key=lambda w: w["n_excess"])
    st = primary["sign_test"] or {}
    p = st.get("p_two_sided")
    mean = primary["excess_mean_pct"]
    sig = p is not None and p < SIGN_ALPHA
    if sig and mean is not None and mean < 0:
        verdict, direction = "significant_negative", "negative"
        tail = "→ 解禁板块显著跑输基准，负向证据成立"
    elif sig and mean is not None and mean > 0:
        verdict, direction = "significant_positive", "positive"
        tail = "→ 解禁板块**显著跑赢**基准：原设计前提（解禁=利空）在现有样本上方向相反，直接接线会系统性反向操作"
    else:
        verdict, direction = "no_signal", "flat"
        tail = "→ 未显著：接线等于给噪声加开关"
    return {
        "verdict": verdict,
        "direction": direction,
        "significant_negative": verdict == "significant_negative",
        "significant_positive": verdict == "significant_positive",
        "primary_window": primary["window_td"],
        "excess_mean_pct": mean,
        "p_two_sided": p,
        "windows_significant": {
            str(w["window_td"]): (w["sign_test"] or {}).get("p_two_sided") for w in windows
        },
        "reason": (
            f"T+{primary['window_td']} 超额均值 {mean}%（n={primary['n_excess']}，"
            f"符号检验 p={p}）{tail}"
        ),
    }


def channel_verdict(
    census: dict, lockup_rows: List[dict], as_of: date, evidence: dict, overlap: dict
) -> dict:
    """三问汇总 → unlock_ready / not_ready + 前置清单（结论全部可追溯到读数）。"""
    max_lockup_day = max((_norm_day(str(r.get("event_date") or "")) for r in lockup_rows), default="")
    online_lockup = (
        census.get("catalyst_events", {}).get("by_type", {}).get("lockup", 0) or 0
    )
    preconditions: List[str] = []
    if not lockup_rows:
        preconditions.append("Q1 本地无任何 lockup 历史样本 → 通道取值面未知")
    if online_lockup == 0:
        preconditions.append(
            "Q1 `catalyst_events` 里 lockup=0（丢弃已生效）→ 通道要先得有在线数据源："
            "恢复 `_fetch_lockup_calendar` 的入库或新建独立 risk_events 落库"
        )
    if evidence["significant_positive"]:
        preconditions.append(
            "Q2 解禁板块减基准后**显著跑赢** → 不是样本不够的问题：【架构计划】WI-P2-4 把解禁当"
            "负向退出证据的前提在现有票池上不成立，解冻前必须先重定风险方向（或证明该正向是"
            "扇出/小样本假象），否则接线=反向操作"
        )
    elif not evidence["significant_negative"]:
        preconditions.append(
            "Q2 解禁板块减基准后未显著跑输 → 需先重取更大解禁样本（≥30 板块日）再判负向强度"
        )
    if overlap["overlap_count"] == 0:
        preconditions.append(
            f"Q3 「入场后 ≤{overlap['near_td']} 交易日同板解禁」样本=0 → P0-1 退出后验无从抬升，"
            "需 ambush 快照积累（且解禁入库恢复）后才可测"
        )
    ready = not preconditions
    return {
        "as_of": as_of.isoformat(),
        "lockup_history_rows": len(lockup_rows),
        "lockup_last_event_date": max_lockup_day,
        "online_calendar_lockup_rows": online_lockup,
        "q2_verdict": evidence["verdict"],
        "q3_overlap_count": overlap["overlap_count"],
        "unlock_ready": ready,
        "preconditions": preconditions,
    }


# ─────────────────────────────────────────────────────────────
# 8) 产物 / CLI
# ─────────────────────────────────────────────────────────────

def run_audit(
    db_path: Path,
    kline_dir: Path,
    benchmark_path: Path,
    as_of: date,
    near_td: int = 5,
) -> dict:
    con = open_readonly(db_path)
    try:
        lockup_rows = load_lockup_events(con, as_of)
        census = load_event_type_census(con)
        ambush_rows = load_ambush_orders_safe(con, as_of)
        open_pos = count_open_positions(con)
    finally:
        con.close()

    resolver, resolver_note = production_board_code_resolver()
    samples = dedupe_board_day(lockup_rows)
    codes = resolve_board_codes(resolver, {s["sector"] for s in samples})

    board_series: Dict[str, List[Tuple[str, float]]] = {}
    for code in {c for c, _src in codes.values() if c}:
        p = kline_dir / f"{code}.csv"
        if p.exists():
            board_series[code] = read_price_series(p)
    benchmark = read_price_series(benchmark_path)

    rows = build_board_day_returns(samples, codes, board_series, benchmark)
    windows = [summarize_window(rows, off) for off in WINDOWS]
    evidence = judge_evidence(windows)
    overlap = near_position_overlap(samples, ambush_rows, board_series, codes, near_td)
    overlap["open_positions"] = open_pos

    status_census: Dict[str, int] = {}
    for r in rows:
        status_census[r["status"]] = status_census.get(r["status"], 0) + 1

    artifact = {
        "kind": "lockup_risk_evidence",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "purpose": "P2-4 risk_events 通道的解冻前置取证（离线只读，不建通道、不接在线决策）",
        "sources": {
            "db": str(db_path),
            "kline_dir": str(kline_dir),
            "benchmark": str(benchmark_path),
            "benchmark_loaded": bool(benchmark),
            "resolver_note": resolver_note,
            "windows_td": list(WINDOWS),
        },
        "q1_data_ground": {
            "raw_rows": len(lockup_rows),
            "fanout": fanout_stats(lockup_rows),
            "board_day_samples": len(samples),
            "event_date_range": [
                min((s["event_date"] for s in samples), default=None),
                max((s["event_date"] for s in samples), default=None),
            ],
            "census": census,
            "sample_status": status_census,
            "code_match_census": match_census(codes),
        },
        "q2_evidence": {
            "note": "生产落库 day_N 是未减基准的裸板块收益；本节同锚重算并减基准",
            "windows": windows,
            "extremes": per_sector_summary(rows),
            "verdict": evidence,
        },
        "q3_joinability": overlap,
        "verdict": channel_verdict(census, lockup_rows, as_of, evidence, overlap),
        "caveats": [
            "样本窗 880 缓存仅覆盖 20260528→20260930，且是当时回测选中的 142 个板块（非全市场）",
            "超额基准只用中证1000（与 GATE-2 同口径）；未做行业中性，板块自身 beta 会计入残差",
            "同板同日多只解禁股已聚合为 1 个板块日样本；扇出倍数在 q1_data_ground.fanout 披露",
            "对账列（reconcile）是同锚重算值与生产落库值的逐条比对：diff 主要源于当时解析到的板码／K 线截止与现在不同（家族词表、别名表已修法），diff 占比高则本表读数不可当生产口径用",
            "na = 生产当日未落 day_N（事件太新或未走完窗口），不计入对账分母",
        ],
    }
    return artifact


def load_ambush_orders_safe(con: sqlite3.Connection, as_of: date) -> List[dict]:
    """表不存在/无权限时诚实返回空（取证脚本不因单表失败而整体崩）。"""
    try:
        return load_ambush_snapshots(con, as_of)
    except sqlite3.Error:
        return []


def match_census(codes: Dict[str, Tuple[Optional[str], str]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for _name, (code, source) in codes.items():
        tag = "failed" if not code else ("fuzzy" if str(source).startswith("fuzzy") else str(source))
        out[tag] = out.get(tag, 0) + 1
    return out


def print_summary(artifact: dict) -> None:
    q1, q2, q3, v = (
        artifact["q1_data_ground"],
        artifact["q2_evidence"],
        artifact["q3_joinability"],
        artifact["verdict"],
    )
    print("=" * 68)
    print(f"P2-4 解禁 risk_events 取证　as_of={artifact['as_of']}")
    print("=" * 68)
    print("[Q1 数据地基]")
    print(f"  原始行={q1['raw_rows']}  板块日样本={q1['board_day_samples']}  "
          f"独立个股事件={q1['fanout']['distinct_stock_events']}  "
          f"扇出倍数(均值/最大)={q1['fanout']['boards_per_stock_event_mean']}/{q1['fanout']['boards_per_stock_event_max']}")
    print(f"  事件日区间={q1['event_date_range']}  覆盖板块数={q1['fanout']['distinct_sectors']}")
    print(f"  在线日历 lockup={v['online_calendar_lockup_rows']}（断流核对）")
    print(f"  样本状态={q1['sample_status']}")
    print("[Q2 负向证据（减基准）]")
    for w in q2["windows"]:
        st = w["sign_test"] or {}
        print(f"  T+{w['window_td']}: 板块裸均值={w['board_mean_pct']}%  "
              f"超额均值={w['excess_mean_pct']}%  n={w['n_excess']}  "
              f"跑输占比={st.get('neg_share')}  p={st.get('p_two_sided')}  "
              f"对账 match/diff/na={w['reconcile']['match']}/{w['reconcile']['diff']}/{w['reconcile']['na']}"
              f"（可比 {w['reconcile_comparable']}）")
    print(f"  判定：{q2['verdict']['verdict']} — {q2['verdict']['reason']}")
    print("[Q3 可接线性]")
    print(f"  埋伏快照={q3['ambush_rows']} 条  持仓={q3.get('open_positions')} 条  "
          f"≤{q3['near_td']}td 同板解禁命中={q3['overlap_count']}")
    print("[结论]")
    print(f"  unlock_ready={v['unlock_ready']}")
    for c in v["preconditions"]:
        print(f"    - {c}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="P2-4 解禁 risk_events 通道解冻前置取证（离线只读）")
    ap.add_argument("--as-of", dest="as_of", default=None, help="YYYY-MM-DD，默认今天")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--kline-dir", default=str(DEFAULT_KLINE_DIR))
    ap.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK))
    ap.add_argument("--near-td", type=int, default=5, help="「临近」窗口（交易日）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--json-only", action="store_true", help="只写工件不打印摘要")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    as_of = (
        datetime.strptime(args.as_of, "%Y-%m-%d").date()
        if args.as_of
        else date.today()
    )
    artifact = run_audit(
        Path(args.db), Path(args.kline_dir), Path(args.benchmark), as_of, args.near_td
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[写入] {out}")
    if not args.json_only:
        print_summary(artifact)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
