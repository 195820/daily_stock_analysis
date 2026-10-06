#!/usr/bin/env python
"""业绩预告方向取证：`stock_yjyg_em` 的「预告类型」是否真的能区分利好/利空（个股级）。

来源：2026-10-06 WI-P2-4 重定后暴露的**活 bug**（负责人裁决「减持/业绩预亏怎么做」引出）。
代码事实链（截至本脚本编写时，逐行核对）：
  1. `catalyst_calendar.py:409` 调 `ak.stock_yjyg_em(date=quarter)`，该接口返回固定 11 列
     （序号/股票代码/股票简称/预测指标/业绩变动/预测数值/业绩变动幅度/业绩变动原因/
      预告类型/上年同期值/公告日期），**「预告类型」列就在里面**；
  2. 但 `:414` 只取 `df.columns[-1]`（侥幸是「公告日期」）、`:433-438` 只落
     stock_code/stock_name/event_date/report_type，**预告类型整列丢弃**；
  3. `:646` 于是只能给所有 earnings 事件打 `direction="drift"`；
  4. `stock_screener.py:2729-2737`（P5 个股桶）只对 `direction=="negative"` continue，
     否则 `score=100` 置顶 → **首亏/预减的股票正在被当最强催化剂顶到最前**；
     `aggregator.py:2146-2151` 的 `qualified_events` 同理（条件 `direction != "negative"`）
     → 预亏事件还在给整个板块当「合格催化」。

**本脚本不改生产代码、不建通道、不接在线决策**，只回答两件取证问题：

  Q1 取值面 —— 历史窗口内「预告类型」的真实取值集合与频次（不猜枚举值，跑出来）。
  Q2 方向面 —— 按「预告类型」分组的**个股级** T+1/3/5 收益减基准是否显著为负。
      度量对象是**个股自身**，不是板块扇出——这是 WI-P2-4 那一课的直接应用：
      解禁那次因为只能按板块取证，读数对不上命题层次，结论被迫标「不适用」。
  Q3 影响面 —— 样本期内被现有链路错误置顶的事件条数（把 bug 的量级钉出来）。

口径纪律（禁第二套口径）：统计件**不复制、直接 import** 解禁取证脚本
`scripts/audit_lockup_risk_events.py` 的 `_norm_day` / `find_anchor_index` /
`anchor_window_returns` / `sign_test` / `wilson_interval` 与 `WINDOWS` / `SIGN_ALPHA`，
基准同为中证 1000（`.review_tmp/bt_cache/index_sh000852.csv`，与 GATE-2 标定同文件）。
判定阈值同 `judge_evidence`（可判窗口 n≥5、主判窗 T+5、α=0.05），只是标签中性化。

数据来源与触网说明（**与解禁取证不同，本脚本必须触网**）：
  - 事件面：`ak.stock_yjyg_em` 按报告期取（生产件 `_fetch_earnings_calendar` 丢列，不可复用，
    故此处直调 akshare；这是取证专用，不构成新的生产数据通路）。原始返回落缓存，重跑不重拉。
  - 价格面：优先 `data_provider.DataFetcherManager.get_daily_data`（项目标准多源件，
    EM 路 `adjust="qfq"` 前复权），失败回退 DB `stock_daily`（离线、覆盖极小）；
    逐股缓存到 `.review_tmp/earnings_forecast_cache/kline/`。
  - 基准与 DB：本地只读（DB 以 `mode=ro` 打开，绝不写）。

用法：
    python scripts/audit_earnings_forecast_direction.py --events-only        # 只看 Q1 取值面
    python scripts/audit_earnings_forecast_direction.py                       # 全量（触网取价格）
    python scripts/audit_earnings_forecast_direction.py --no-fetch --max-events 300
        [--as-of YYYY-MM-DD] [--quarters 20251231,20260331] [--min-type-n 20] [--json-only]

产物：data/earnings_forecast_evidence.json + 控制台摘要。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

DEFAULT_DB = ROOT_DIR / "data" / "stock_analysis.db"
DEFAULT_BENCHMARK = ROOT_DIR / ".review_tmp" / "bt_cache" / "index_sh000852.csv"
DEFAULT_CACHE = ROOT_DIR / ".review_tmp" / "earnings_forecast_cache"
DEFAULT_OUT = ROOT_DIR / "data" / "earnings_forecast_evidence.json"

MIN_JUDGEABLE_WINDOW_N = 5    # 与解禁取证 judge_evidence 同阈值
MIN_TYPE_N = 30               # 单类型可判方向的最小可算事件数（低于此只报数不判方向）
KLINE_PRE_DAYS = 10
KLINE_POST_DAYS = 30
MAX_WINDOW_DAYS = 60          # 单次向数据源请求的日历跨度上限（见 bucket_spans）


# ─────────────────────────────────────────────────────────────
# 0) 口径复用：直接 import 解禁取证件，失败即终止（绝不自写第二套）
# ─────────────────────────────────────────────────────────────

def load_shared_kouji() -> object:
    spec = importlib.util.spec_from_file_location(
        "audit_lockup_risk_events", ROOT_DIR / "scripts" / "audit_lockup_risk_events.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载共享口径件 scripts/audit_lockup_risk_events.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for need in ("_norm_day", "find_anchor_index", "anchor_window_returns", "sign_test",
                 "wilson_interval", "WINDOWS", "SIGN_ALPHA"):
        if not hasattr(mod, need):
            raise RuntimeError(f"共享口径件缺少 {need}，取证不可进行（宁可失败也不另立口径）")
    return mod


K = load_shared_kouji()


# ─────────────────────────────────────────────────────────────
# 1) 事件面：yjyg 取数 + 缓存
# ─────────────────────────────────────────────────────────────

COL_CANDIDATES = {
    "stock_code": ("股票代码", "股票代码", "代码"),
    "stock_name": ("股票简称", "股票名称", "简称"),
    "forecast_type": ("预告类型", "预测类型"),
    "change_pct": ("业绩变动幅度", "变动幅度"),
    "change_text": ("业绩变动",),
    "reason": ("业绩变动原因",),
    "indicator": ("预测指标",),
    "announce_date": ("公告日期", "披露日期"),
}

POSITIONAL_FALLBACK = {  # ak.stock_yjyg_em 最终 select 的固定列序（0-based）
    "stock_code": 1, "stock_name": 2, "indicator": 3, "change_text": 4,
    "change_pct": 6, "reason": 7, "forecast_type": 8, "announce_date": 10,
}


def _pick(df, key: str) -> Tuple[Optional[str], str]:
    """按候选列名取列；取不到则回退 akshare 固定列序，并如实标注回退。"""
    for c in COL_CANDIDATES[key]:
        if c in df.columns:
            return c, "by_name"
    idx = POSITIONAL_FALLBACK.get(key)
    if idx is not None and len(df.columns) > idx:
        return df.columns[idx], "positional_fallback"
    return None, "missing"


def default_quarters(as_of: date, lookback: int = 4) -> List[str]:
    """as_of 往前 lookback 个报告期（业绩预告集中在 1/4/7/10 月）。"""
    ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    out: List[str] = []
    y = as_of.year
    for _ in range(lookback + 2):
        for m, d in ends:
            s = f"{y}{m:02d}{d:02d}"
            if date(y, m, d) < as_of:
                out.append(s)
        y -= 1
    return sorted(set(out))[-lookback:]


def fetch_events(quarters: List[str], cache_dir: Path, refresh: bool
                 ) -> Tuple[List[dict], List[dict], Optional[str]]:
    """逐报告期取业绩预告原始行 → 结构化事件；缓存 JSON 便于离线重跑。"""
    rows: List[dict] = []
    metas: List[dict] = []
    err: Optional[str] = None
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        return [], [], f"akshare 导入失败: {exc!r}"

    for q in quarters:
        cp = cache_dir / f"yjyg_{q}.json"
        raw = None
        if cp.exists() and not refresh:
            try:
                raw = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                raw = None
        if raw is None:
            try:
                df = ak.stock_yjyg_em(date=q)
            except Exception as exc:  # noqa: BLE001 - 网络失败须如实上报而非编数
                err = f"ak.stock_yjyg_em({q}) 失败: {exc!r}"
                metas.append({"quarter": q, "status": "fetch_failed", "error": err})
                continue
            if df is None or df.empty:
                metas.append({"quarter": q, "status": "empty", "n": 0})
                continue
            cols = [str(c) for c in df.columns]
            picked: Dict[str, Tuple[Optional[int], str]] = {}
            for key in COL_CANDIDATES:
                col, how = _pick(df, key)
                picked[key] = (cols.index(col) if col in cols else None, how)
            recs = []
            for _, r in df.iterrows():
                g = lambda k: ("" if picked[k][0] is None else
                               "" if r.iloc[picked[k][0]] is None else str(r.iloc[picked[k][0]]))
                recs.append({
                    "quarter": q,
                    "stock_code": g("stock_code").strip(),
                    "stock_name": g("stock_name").strip(),
                    "forecast_type": g("forecast_type").strip(),
                    "change_pct": g("change_pct").strip(),
                    "change_text": g("change_text").strip()[:60],
                    "reason": g("reason").strip()[:80],
                    "indicator": g("indicator").strip(),
                    "announce_date": K._norm_day(g("announce_date")),
                })
            raw = {"columns": cols,
                   "col_how": {k: v[1] for k, v in picked.items()},
                   "rows": recs}
            cp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            metas.append({"quarter": q, "status": "fetched", "n": len(recs)})
        else:
            metas.append({"quarter": q, "status": "cache", "n": len(raw.get("rows", []))})
        rows.extend(raw.get("rows", []))
        if raw.get("col_how", {}).get("forecast_type") == "positional_fallback":
            metas.append({"quarter": q, "warning": "「预告类型」按 akshare 固定列序回退取用"})
    return rows, metas, err


PROFIT_PRIORITY = ("归属于上市公司股东的净利润", "扣除非经常性损益后的净利润", "净利润")


def dedupe_events(raw_rows: List[dict], as_of: date) -> Tuple[List[dict], dict]:
    """(code, 公告日) → 一条「业绩预告公告」，canonical 行按**净利润口径**选。

    同一公告日往往同时披露 营业收入 / 归母净利 / 扣非净利 / 每股收益 多条预告，
    且实测约 1/3 的 (股,日) 各指标类型不一致（如营收「略增」+ 净利「首亏」）。
    「业绩预亏」命题讲的是利润，故取净利润口径行为准；覆盖率、冲突数、
    无净利口径行数全部进产物，不做静默取舍。
    """
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for r in raw_rows:
        code, day = r.get("stock_code", ""), r.get("announce_date", "")
        if not code or not day or len(day) != 10:
            continue
        groups.setdefault((code, day), []).append(r)

    events: List[dict] = []
    conflicts = no_profit = 0
    for (_code, _day), rs in groups.items():
        types = {x.get("forecast_type") or "(空)" for x in rs}
        if len(types) > 1:
            conflicts += 1
        chosen = rs[0]
        for want in PROFIT_PRIORITY:
            hit = next((x for x in rs if want in (x.get("indicator") or "")), None)
            if hit is not None:
                chosen = hit
                break
        on_profit = any(w in (chosen.get("indicator") or "") for w in PROFIT_PRIORITY)
        if not on_profit:
            no_profit += 1
        e = dict(chosen)
        e.update({"indicator_rows": len(rs), "type_conflict": len(types) > 1,
                  "profit_basis": on_profit})
        events.append(e)

    too_recent = 0
    last_day = (as_of - timedelta(days=max(K.WINDOWS))).isoformat()
    kept = []
    for e in events:
        if e["announce_date"] > last_day:
            too_recent += 1
            continue
        kept.append(e)
    kept.sort(key=lambda x: (x["announce_date"], x["stock_code"]))
    stats = {
        "raw_rows": len(raw_rows),
        "stock_day_groups": len(events),
        "type_conflict_groups": conflicts,
        "type_conflict_share": round(conflicts / len(events), 4) if events else None,
        "groups_without_profit_indicator": no_profit,
        "profit_basis_share": round((len(events) - no_profit) / len(events), 4) if events else None,
        "dropped_too_recent": too_recent,
        "usable_events": len(kept),
        "canonical_rule": "同 (股,日) 多指标 → 取净利润口径首条（归母 > 扣非 > 净利润），"
                          "无净利口径行则取首条并标 profit_basis=False",
    }
    return kept, stats


def stratified_sample(events: List[dict], budget: int,
                      floor: int) -> Tuple[List[dict], Dict[str, int]]:
    """按「预告类型」分层：每类保底 floor 条（不足则全取），余额按可补名额比例分。

    为何不用等比抽样：预增/首亏 会吃饱样本，而 略减/续盈/不确定 只剩几条
    → 恰恰是需要方证的负面小类型没有统计功效。类内仍按**等距下标**取，
    保持时段均匀（避开单 regime 集中）。预算不够保底时按类型规模等比缩。
    """
    by_type: Dict[str, List[dict]] = {}
    for e in events:
        by_type.setdefault(e.get("forecast_type") or "(空)", []).append(e)
    alloc = {t: min(len(rs), floor) for t, rs in by_type.items()}
    if sum(alloc.values()) >= budget:
        scale = budget / max(1, sum(alloc.values()))
        alloc = {t: max(1, int(n * scale)) for t, n in alloc.items()}
    else:
        rest = budget - sum(alloc.values())
        room = {t: len(rs) - alloc[t] for t, rs in by_type.items()}
        total_room = sum(room.values()) or 1
        for t, r in room.items():
            alloc[t] += min(r, int(round(rest * r / total_room)))

    picked: List[dict] = []
    for t, rs in by_type.items():
        n = min(alloc[t], len(rs))
        if n >= len(rs):
            picked.extend(rs)
        else:
            step = len(rs) / n
            picked.extend(rs[min(int(i * step), len(rs) - 1)] for i in range(n))
    picked.sort(key=lambda x: (x["announce_date"], x["stock_code"]))
    return picked, dict(sorted(alloc.items(), key=lambda kv: -kv[1]))


def type_census(events: List[dict]) -> Dict[str, dict]:
    """Q1：真实取值集合与频次（含变动幅度分布，供人工核对映射）。"""
    out: Dict[str, dict] = {}
    for e in events:
        t = e.get("forecast_type") or "(空)"
        b = out.setdefault(t, {"n_events": 0, "stocks": set(), "change_pcts": []})
        b["n_events"] += 1
        b["stocks"].add(e["stock_code"])
        try:
            b["change_pcts"].append(float(e.get("change_pct") or ""))
        except (TypeError, ValueError):
            pass
    res = {}
    for t, b in out.items():
        cps = b["change_pcts"]
        res[t] = {
            "n_events": b["n_events"],
            "n_stocks": len(b["stocks"]),
            "change_pct_median": round(statistics.median(cps), 2) if cps else None,
            "change_pct_range": [round(min(cps), 2), round(max(cps), 2)] if cps else None,
        }
    return dict(sorted(res.items(), key=lambda kv: -kv[1]["n_events"]))


# ─────────────────────────────────────────────────────────────
# 2) 价格面：个股日 K（缓存优先 → 项目多源件 → DB 只读）
# ─────────────────────────────────────────────────────────────

def _cache_path(kline_dir: Path, code: str) -> Path:
    return kline_dir / f"{code}.json"


def _cache_hit(p: Path, start: date, end: date) -> Optional[List[Tuple[str, float]]]:
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if blob.get("start", "9999") > start.isoformat() or blob.get("end", "0000") < end.isoformat():
        return None  # 缓存区间不覆盖本次请求
    return [(K._norm_day(d), float(c)) for d, c in blob.get("rows", [])]


def _rows_from_df(df) -> List[Tuple[str, float]]:
    date_col = next((c for c in ("date", "日期", "trade_date") if c in getattr(df, "columns", [])), None)
    close_col = next((c for c in ("close", "收盘") if c in getattr(df, "columns", [])), None)
    if not date_col or not close_col:
        return []
    out = []
    for _, r in df.iterrows():
        try:
            out.append((K._norm_day(str(r[date_col])), float(r[close_col])))
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda t: t[0])


# ── 覆盖校验（本轮实测发现的取数层缺陷）──
# 治理前的现象：manager.get_daily_data 是「首个非空即返回」，不校区间忠实性。
# 实测：Mootdx 对 000977/300763/301068 请求 2026-06-28~2026-08-07（约 28 交易日）
# **只返区间末日 1 行**，当成功收下 → 公告日永远锚不到；397 条里 126 条因此报
# anchor_out_of_range（其中 95 条挤在 2026-07，即中报预告高峰）。那不是停牌也不是新股，
# 是取数层对请求区间不忠实。修在取证侧：覆盖不够就**逐源重取、取覆盖最全的一路**。
COVERAGE_MIN_ROWS = 12
COVERAGE_SLACK_DAYS = 10  # 新股/真停牌天然拿满整窗，给 10 日历天容忍


def coverage_short(rows: List[Tuple[str, float]], start: date, end: date) -> bool:
    """实得序列不够覆盖请求区间 → True（拿不准就重取，不把残缺当数据）。

    ⚠️ 传入的必须是**已截到请求区间内**的行（见 _in_range）：拿一个更宽的超集序列来判，
    首尾天然在区间外，会永远“通过”——本轮实测返现过这个假通过（反而少算 11 条）。
    """
    if len(rows) < COVERAGE_MIN_ROWS:
        return True
    first, last = rows[0][0], rows[-1][0]
    return (first > (start + timedelta(days=COVERAGE_SLACK_DAYS)).isoformat()
            or last < (end - timedelta(days=COVERAGE_SLACK_DAYS)).isoformat())


def _in_range(rows: List[Tuple[str, float]], start: date, end: date) -> List[Tuple[str, float]]:
    """只留请求区间内的行（部分源会超送，不能拿去算后续窗口）。"""
    lo, hi = start.isoformat(), end.isoformat()
    return [r for r in rows if lo <= r[0] <= hi]


def _fetch_one(fetcher, code: str, start: date, end: date):
    try:
        return fetcher.get_daily_data(stock_code=code,
                                      start_date=start.isoformat(),
                                      end_date=end.isoformat())
    except TypeError:  # 各源签名不同（位置参数/额外 days）
        return fetcher.get_daily_data(code, start.isoformat(), end.isoformat())


def _fetch_best_series(code: str, start: date, end: date, state: dict
                       ) -> Tuple[List[Tuple[str, float]], str]:
    """逐数据源取同一段区间，取**区间内行数最多**的一路（不是首个非空、也不是总行数最多）。

    两个口径陷阱实测踩过：
    - Mootdx 会把整段区间缩成区间末日 1 行；
    - Tencent 不守请求区间（要 06-28~08-07 它给 06-26~09-30），按「总行数」计分就会选到
      不覆盖目标旧窗口的近期数据。
    故计分只看落在 [start, end] **内**的行数。不够则换下一路；覆盖够了立即停（不给
    Pytdx 那种 30s 连接超时代码走完全部源的代价）；某源连续 3 次异常或空返 → 拉黑，不再浪费时间。
    全部失败也不猜价，原样返区间内行数最多的那路并如实计入后续锚定失败。
    """
    lo, hi = start.isoformat(), end.isoformat()
    best: List[Tuple[str, float]] = []
    best_inside = -1
    best_src = ""
    dead = state.setdefault("dead_sources", {})
    for f in list(state.get("fetchers") or []):
        name = getattr(f, "name", type(f).__name__)
        if dead.get(name, 0) >= 3:
            continue
        try:
            rows = _rows_from_df(_fetch_one(f, code, start, end))
        except Exception as exc:  # noqa: BLE001
            dead[name] = dead.get(name, 0) + 1
            state.setdefault("fetch_errors", []).append(
                f"{code}[{name}]: {type(exc).__name__}"[:120])
            continue
        if not rows:
            dead[name] = dead.get(name, 0) + 1
            continue
        inside = sum(1 for d, _ in rows if lo <= d <= hi)
        if inside > best_inside:
            best, best_inside, best_src = rows, inside, f"multi:{name}"
        if not coverage_short(_in_range(rows, start, end), start, end):
            break  # 本级已足够覆盖区间，不再试更低的源
    best = _in_range(best, start, end) if best else best
    if best:
        retry = state.setdefault("coverage_retry", {})
        retry["resolved"] = retry.get("resolved", 0) + 1
        retry[f"won_by_{best_src}"] = retry.get(f"won_by_{best_src}", 0) + 1
    else:
        retry = state.setdefault("coverage_retry", {})
        retry["unresolved"] = retry.get("unresolved", 0) + 1
    return best, best_src


def load_stock_series(code: str, start: date, end: date, kline_dir: Path,
                      db: Optional[Path], state: dict,
                      cache_key: Optional[str] = None
                      ) -> Tuple[List[Tuple[str, float]], str]:
    """单只票的 (日期, 收盘) 序列 + 来源标注；任何一层失败都如实记录，不猜价。

    cache_key：同一 code 的不同公告时段分档不互覆盖（按 (code, 窗口起点) 命名）。
    命中的旧同 code 超集缓存只要**区间内**行数达标就继续用（零额外网费）。
    """
    cp = _cache_path(kline_dir, cache_key or code)
    hit = _cache_hit(cp, start, end)
    if hit is None and cache_key:  # 同 code 的超集旧档能盖住本窗口就复用
        hit = _cache_hit(_cache_path(kline_dir, code), start, end)
    if hit is not None:
        if not (state.get("refetch_insufficient")
                and coverage_short(_in_range(hit, start, end), start, end)):
            state["source_census"]["cache"] = state["source_census"].get("cache", 0) + 1
            return hit, "cache"
        state.setdefault("coverage_retry", {})["cache_rejected"] = \
            state.setdefault("coverage_retry", {}).get("cache_rejected", 0) + 1

    rows, source = [], ""
    if state["manager"] is not None:
        if state.get("refetch_insufficient"):
            rows, source = _fetch_best_series(code, start, end, state)
        else:
            try:
                df, src = state["manager"].get_daily_data(
                    code, start_date=start.isoformat(), end_date=end.isoformat())
                rows = _rows_from_df(df)
                source = f"fetcher:{src}"
            except Exception as exc:  # noqa: BLE001
                state.setdefault("fetch_errors", []).append(f"{code}: {exc!r}"[:160])
                rows, source = [], "fetcher_failed"
    if not rows and db:
        rows = _rows_from_db(db, code, start, end)
        source = "db:stock_daily" if rows else "none"
    if rows:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"code": code, "start": start.isoformat(),
                                  "end": end.isoformat(), "source": source,
                                  "rows": rows}, ensure_ascii=False), encoding="utf-8")
    state["source_census"][source or "none"] = state["source_census"].get(source or "none", 0) + 1
    return rows, source


def _rows_from_db(db: Path, code: str, start: date, end: date) -> List[Tuple[str, float]]:
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        cur = con.execute(
            "SELECT date, close FROM stock_daily WHERE code=? AND date BETWEEN ? AND ? "
            "ORDER BY date", (code, start.isoformat(), end.isoformat()))
        return [(K._norm_day(str(d)), float(c)) for d, c in cur.fetchall() if c is not None]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def bucket_spans(events: List[dict]) -> List[Tuple[str, date, date]]:
    """按公告时段把同一只票切成若干**窄窗**（每窗含前后置天数 ≤ MAX_WINDOW_DAYS）。

    为什么不是一股一次宽窗：`get_daily_data` 首个非空即返回、不校验区间忠实性，
    实测 Mootdx 会把长区间缩成区间末日 1 行 → 397 条样本里 126 条锚定越界（其中 95 条
    挤在 2026-07 中报预告高峰）。窄窗落在源能忠实返回的跨度内；缓存按
    `(code, 窗口起点)` 分档，重跑只为真正缺覆盖的那一档付费。
    """
    by_code: Dict[str, List[date]] = {}
    for e in events:
        by_code.setdefault(e["stock_code"], []).append(date.fromisoformat(e["announce_date"]))
    buckets: List[Tuple[str, date, date]] = []
    for code, ds in by_code.items():
        days = sorted(set(ds))
        lo = hi = days[0]
        for d in days[1:]:
            if (d - lo).days + KLINE_PRE_DAYS + KLINE_POST_DAYS > MAX_WINDOW_DAYS:
                buckets.append((code, lo, hi))
                lo = d
            hi = d
        buckets.append((code, lo, hi))
    return sorted(buckets)


# ─────────────────────────────────────────────────────────────
# 3) T+N 减基准（个股自身，非板块）
# ─────────────────────────────────────────────────────────────

def attach_returns(events: List[dict], series_by_code: Dict[str, List[Tuple[str, float]]],
                   benchmark: List[Tuple[str, float]]) -> Tuple[List[dict], Dict[str, int]]:
    status: Dict[str, int] = {}
    out: List[dict] = []
    for e in events:
        row = dict(e)
        series = series_by_code.get(e["stock_code"], [])
        if not series:
            row["status"] = "no_kline"
        else:
            ev = date.fromisoformat(e["announce_date"])
            base = K.find_anchor_index(series, ev)
            if base is None:
                row["status"] = "anchor_out_of_range"
            else:
                sret = K.anchor_window_returns(series, base)
                bref: Dict[int, Optional[float]] = {o: None for o in K.WINDOWS}
                if benchmark:
                    bi = K.find_anchor_index(benchmark, ev)
                    if bi is not None:
                        bref = K.anchor_window_returns(benchmark, bi)
                row["windows"] = {}
                for off in K.WINDOWS:
                    raw, bm = sret.get(off), bref.get(off)
                    row["windows"][off] = {
                        "stock_pct": raw,
                        "bench_pct": bm,
                        "excess_pct": round(raw - bm, 4) if raw is not None and bm is not None else None,
                    }
                row["status"] = "ok"
        status[row["status"]] = status.get(row["status"], 0) + 1
        out.append(row)
    return out, status


# ── Q2b 合并组（功效设备，不是标签来源）──
# 逐类型分层把 5414 条公告摊成 11 格，每格可算样本掉到 20-30 条（价格面损耗约 1/3：
# 北交所无 K 线 / 锚定越界），**没有任何一格能达到可判门槛** —— 这是设计缺陷不是市场结论。
# 合并组把「预亏是不是利空」这一**真正的命题**按字面语义并格，用牺牲分辨率换统计功效；
# 逐类型读数保留作交叉核对（字面归组若错，并格会互相稀释，故两者必须同时披露）。
COHORT_NEGATIVE = ("首亏", "续亏", "增亏", "预减", "略减")
COHORT_POSITIVE = ("预增", "扭亏", "减亏", "略增", "续盈")


def cohort_split(by_type: Dict[str, List[dict]]) -> Tuple[Dict[str, List[dict]], List[str]]:
    """按字面语义并格；未进任一组（如「不确定」/akshare 新增枚举）的类型**如实列出**，不静默吞掉。"""
    groups: Dict[str, List[dict]] = {
        "负面合并组": [r for t in COHORT_NEGATIVE for r in by_type.get(t, [])],
        "正面合并组": [r for t in COHORT_POSITIVE for r in by_type.get(t, [])],
    }
    covered = set(COHORT_NEGATIVE) | set(COHORT_POSITIVE)
    leftovers = sorted(t for t in by_type if t not in covered)
    return groups, leftovers


def summarize_type(rows: List[dict], ftype: str, min_n: int) -> dict:
    """Q2 主读数：单类型逐窗 裸收益 / 超额 / 精确符号检验 / Wilson。"""
    windows = []
    for off in K.WINDOWS:
        raws = [r["windows"][off]["stock_pct"] for r in rows
                if r.get("windows", {}).get(off, {}).get("stock_pct") is not None]
        excs = [r["windows"][off]["excess_pct"] for r in rows
                if r.get("windows", {}).get(off, {}).get("excess_pct") is not None]
        w = {"window_td": off, "n_stock": len(raws),
             "stock_mean_pct": round(statistics.mean(raws), 4) if raws else None,
             "n_excess": len(excs),
             "excess_mean_pct": round(statistics.mean(excs), 4) if excs else None,
             "excess_median_pct": round(statistics.median(excs), 4) if excs else None,
             "sign_test": K.sign_test(excs) if excs else None}
        if excs:
            st = w["sign_test"]
            lo, hi = K.wilson_interval(st["negative"], st["negative"] + st["positive"])
            w["share_below_bench_wilson95"] = [round(lo, 4), round(hi, 4)]
        else:
            w["share_below_bench_wilson95"] = None
        windows.append(w)

    usable = [w for w in windows if w["n_excess"] >= MIN_JUDGEABLE_WINDOW_N]
    primary = None
    for w in usable:
        if w["window_td"] == 5:
            primary = w
            break
    primary = primary or (max(usable, key=lambda w: w["n_excess"]) if usable else None)
    if primary is None:
        verdict, reason = "insufficient_sample", f"减基准样本 <{MIN_JUDGEABLE_WINDOW_N}（n=0 或缺价）"
        p = None
    else:
        st = primary["sign_test"]
        p, mean = st["p_two_sided"], primary["excess_mean_pct"]
        sig = p is not None and p < K.SIGN_ALPHA
        if sig and mean < 0:
            verdict = "significant_negative"
            reason = f"T+{primary['window_td']} 个股超额均值 {mean}%（n={primary['n_excess']}, p={p}）显著跑输 → 支持判 negative"
        elif sig and mean > 0:
            verdict = "significant_positive"
            reason = f"T+{primary['window_td']} 个股超额均值 {mean}%（n={primary['n_excess']}, p={p}）显著跑赢 → 不支持判 negative"
        else:
            verdict = "no_signal"
            reason = f"T+{primary['window_td']} 超额均值 {mean}%（n={primary['n_excess']}, p={p}）不显著 → 方向未知"
    n_ok = sum(1 for r in rows if r["status"] == "ok")
    if n_ok < min_n:
        verdict = "inconclusive_small_n"
        reason += f"；可算样本 {n_ok} < 类型门槛 {min_n}，不得进方向映射"
    return {"forecast_type": ftype, "n_events": len(rows), "n_priced": n_ok,
            "windows": windows, "verdict": verdict, "p_two_sided": p,
            "excess_mean_pct": primary["excess_mean_pct"] if primary else None,
            "reason": reason}


def blast_radius(rows: List[dict], per_type: Dict[str, dict],
                 census_all: Dict[str, dict]) -> dict:
    """Q3：按取证判出的 negative 类型，现有链路每天在错误置顶多少条。

    量级用**全量**取值面（不按抽样条数外推，抽样只影响 Q2 的统计功效），
    样本内可算条数另列，两者都披露。
    """
    neg_types = {t for t, s in per_type.items() if s["verdict"] == "significant_negative"}
    full = sum(census_all.get(t, {}).get("n_events", 0) for t in neg_types)
    in_sample = sum(1 for r in rows if r.get("forecast_type") in neg_types)
    return {
        "population_negative_events": full,
        "population_negative_stocks": sum(census_all.get(t, {}).get("n_stocks", 0)
                                          for t in neg_types),
        "sample_negative_events_priced": in_sample,
        "population_events_total": sum(c["n_events"] for c in census_all.values()),
        "negative_share_of_population": (round(full / sum(c["n_events"] for c in census_all.values()), 4)
                                         if census_all else None),
        "negative_verdict_types": sorted(neg_types),
        "mechanism": "direction='drift' → stock_screener.py:2729 不 continue → score=100 置顶；"
                     "aggregator.py:2146-2151 qualified_events 亦不排斥（条件只 != 'negative'）",
        "note": "仅量级；逐类型是否真该判 negative 以 q2_direction_by_type 为准，"
                "未判出方向（inconclusive/no_signal）的类型不计入错误置顶",
    }


def suggest_map(per_type: Dict[str, dict]) -> dict:
    """建议映射只来自读数；n 不足或字面语义与读数冲突一律标 unresolved。"""
    ok, unresolved = {}, {}
    for t, s in per_type.items():
        if s["verdict"] == "significant_negative":
            ok[t] = "negative"
        elif s["verdict"] == "significant_positive":
            ok[t] = "positive"
        else:
            unresolved[t] = f"{s['verdict']}（n_events={s['n_events']}, n_priced={s['n_priced']}）"
    return {"data_driven": ok, "unresolved": unresolved,
            "rule": "方向只由个股级 T+5 减基准符号检验给出；不按类型名称字面含义预设映射（字面可能反，如「减亏」）"}


def overall_verdict(census: Dict[str, dict], per_type: Dict[str, dict],
                    metas: List[dict], fetch_err: Optional[str]) -> dict:
    priced = sum(s["n_priced"] for s in per_type.values())
    neg = [t for t, s in per_type.items() if s["verdict"] == "significant_negative"]
    ready = bool(neg) and priced >= 50 and not fetch_err
    pre = []
    if fetch_err:
        pre.append(f"Q1 事件取数失败 → 无事件地基：{fetch_err}")
    if priced < 50:
        pre.append(f"Q2 可算个股收益样本仅 {priced} 条（<50）→ 方向读数不足以支撑接线")
    if not neg:
        pre.append("Q2 没有任何「预告类型」显著跑输基准 → 现有 bug 的量级需重估"
                   "（注意：这不等于「预亏不是利空」，只代表本样本未检出）")
    return {"ready_to_fix_direction": ready, "n_priced_events": priced,
            "negative_types": sorted(neg), "preconditions": pre,
            "scope_note": "取证只回答「哪些类型可判 negative」；改生产 direction 属在线决策变更，"
                          "须另开 WI 并三态开关 + shadow 读数，本脚本不代做"}


# ─────────────────────────────────────────────────────────────
# 4) CLI
# ─────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> dict:
    as_of = (datetime.strptime(args.as_of, "%Y-%m-%d").date()
             if args.as_of else date.today())
    quarters = ([q.strip() for q in args.quarters.split(",") if q.strip()]
                if args.quarters else default_quarters(as_of))
    cache = Path(args.cache_dir)

    raw_rows, metas, fetch_err = ([], [], None)
    if not args.no_fetch or not list(cache.glob("yjyg_*.json")):
        raw_rows, metas, fetch_err = fetch_events(quarters, cache, args.refresh_events)
    else:
        for q in quarters:
            cp = cache / f"yjyg_{q}.json"
            if cp.exists():
                blob = json.loads(cp.read_text(encoding="utf-8"))
                raw_rows.extend(blob.get("rows", []))
                metas.append({"quarter": q, "status": "cache", "n": len(blob.get("rows", []))})

    events_all, dstats = dedupe_events(raw_rows, as_of)
    sample = events_all
    if args.max_events and len(events_all) > args.max_events:
        sample, alloc = stratified_sample(events_all, args.max_events, args.per_type_floor)
        dstats["priced_sample"] = {
            "taken": len(sample),
            "per_type_alloc": alloc,
            "method": f"按预告类型分层：每类保底 {args.per_type_floor} 条（不足全取），"
                      f"余额按可补名额比例分；类内等距下标取样保时段均匀",
        }

    artifact = {
        "kind": "earnings_forecast_direction_evidence",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "purpose": "业绩预告「预告类型」能否区分利好/利空（个股级 T+N 减基准）——"
                   "修 earnings direction 反向 bug 的前置取证",
        "sources": {
            "quarters": quarters, "event_cache": str(cache),
            "benchmark": str(Path(args.benchmark)), "db": str(Path(args.db)),
            "shared_kouji": "scripts/audit_lockup_risk_events.py（import 复用，非复制）",
            "windows_td": list(K.WINDOWS), "alpha": K.SIGN_ALPHA,
            "min_type_n": args.min_type_n,
        },
        "q1_fetch": {"metas": metas, "fetch_error": fetch_err},
        "q1_dedupe": dstats,
        "q1_type_census": type_census(events_all),
    }

    if args.events_only:
        artifact["verdict"] = {"events_only": True,
                               "note": "仅取值面；价格面需去掉 --events-only 重跑（会触网取个股日K）"}
        return artifact

    kline_dir = cache / "kline"
    kline_dir.mkdir(parents=True, exist_ok=True)
    state = {"source_census": {}, "fetch_errors": [], "manager": None,
             "fetchers": [], "refetch_insufficient": bool(args.refetch_insufficient)}
    if not args.no_fetch:
        try:
            from data_provider import DataFetcherManager

            state["manager"] = DataFetcherManager()
            # 逐源重取用：manager 自己的链路是「首个非空即返回」，不校区间忠实性
            state["fetchers"] = state["manager"]._get_fetchers_snapshot()
        except Exception as exc:  # noqa: BLE001
            state["manager_error"] = repr(exc)[:200]

    buckets = bucket_spans(sample)
    benchmark = K.read_price_series(Path(args.benchmark))
    series_by_code: Dict[str, List[Tuple[str, float]]] = {}
    for code, lo, hi in buckets:
        s, _ = load_stock_series(code, lo - timedelta(days=KLINE_PRE_DAYS),
                                 hi + timedelta(days=KLINE_POST_DAYS),
                                 kline_dir, Path(args.db), state,
                                 cache_key=f"{code}_{lo.isoformat()}")
        if s:
            merged: Dict[str, float] = dict(series_by_code.get(code, []))
            merged.update(dict(s))
            series_by_code[code] = sorted(merged.items())

    rows, status = attach_returns(sample, series_by_code, benchmark)
    by_type: Dict[str, List[dict]] = {}
    for r in rows:
        by_type.setdefault(r.get("forecast_type") or "(空)", []).append(r)
    per_type = {t: summarize_type(rs, t, args.min_type_n) for t, rs in by_type.items()}
    cohorts, cohort_leftovers = cohort_split(by_type)
    per_cohort = {t: summarize_type(rs, t, args.min_cohort_n) for t, rs in cohorts.items()}

    artifact["q2_price"] = {
        "kline_source_census": state["source_census"],
        "kline_windows": {"codes": len({b[0] for b in buckets}), "buckets": len(buckets),
                          "max_window_days": MAX_WINDOW_DAYS},
        "manager_error": state.get("manager_error"),
        "benchmark_loaded": bool(benchmark),
        "fetch_errors_sample": state.get("fetch_errors", [])[:20],
        "event_status": status,
        "coverage_retry": dict(state.get("coverage_retry") or {}),
        "dead_sources": dict(state.get("dead_sources") or {}),
        "refetch_insufficient": bool(args.refetch_insufficient),
    }
    artifact["q2_direction_by_type"] = per_type
    artifact["q2b_direction_by_cohort"] = {
        "readings": per_cohort,
        "definition": {"负面合并组": list(COHORT_NEGATIVE), "正面合并组": list(COHORT_POSITIVE)},
        "types_not_in_any_cohort": cohort_leftovers,
        "note": "合并组是按「预告类型」字面语义并格的**功效设备**：逐类型分层后每格可算样本仅 20-30 条、"
                "全部达不到门槛，并格才能检验「负面类型整体是否跑输基准」。它不产生标签 —— "
                "字面归组错了就会互相稀释，所以标签仍以逐类型读数为准、两边同时披露",
    }
    artifact["q3_blast_radius"] = blast_radius(rows, per_type, artifact["q1_type_census"])
    artifact["suggested_direction_map"] = suggest_map(per_type)
    artifact["verdict"] = overall_verdict(artifact["q1_type_census"], per_type, metas, fetch_err)
    artifact["caveats"] = [
        "度量对象=个股自身（不是板块扇出）：这是 WI-P2-4 那轮教训的直接应用，读数可对上命题层次",
        "超额=个股 qfq 收盘收益 − 中证1000 同日收益；未做行业/风格中性，个股 beta 与板块溢价都计入残差",
        "锚定沿用生产约定（公告日收盘→T+N 收盘，±3 日回退）；业绩预告多为盘后公告，T+1 才是首个完整反应日",
        "「预告类型」取值是报告期全市场口径，同一股票同一报告期可能有多条（不同预测指标）→ 已按 (code,公告日) 去重并披露冲突数",
        "抽样为「按预告类型分层 + 类内等距」：小类型（略减/续盈/不确定）保底后可判性优于等比抽样；"
        "若要全量，去掉 --max-events 并接受逐股拉取的网费",
        "报告期跨度决定了 regime 覆盖面：只取 1-2 个报告期会把结论绑在单一市场上",
        "若字面语义与个股读数相反（名称是「预减」但跑赢），以读数为准并标 unresolved —— "
        "字面可靠不可靠本身就是本脚本要回答的问题",
        "合并组只提高功效、不提供标签：同一股票多次公告在不同窗重复计入，符号检验视其为独立事件（逐类型层同此口径，不另立）",
        "多重比较未修正：11 个类型×3 个窗口都在同一零假设下做双侧符号检验，α=0.05 下出现 1～2 个「显著」属预期随机"
        "（本轮唯一显著的「减亏 p=0.047」不单独作为接线依据）",
        "逐源重取的拉黑阈值是「同一源累计 3 次异常/空返就永久跳过」：对接口抖动偏严格。本轮七个源跑完全部累到 3 次，"
        "其中 Efinance/Tencent 在达上限前共拿下 resolved=106 档（64+42），仍有 48 档无数据（对应 no_kline=44，主为北交所），"
        "所以本阈值本轮未伤到读数，但它**不是无害的**，重跑时若 unresolved 升高先查这里",
        "本脚本不判「预亏是否该卖出/否决」：架构现状里个股级负面事件的净效果只有「加分归零」，"
        "否决闸门 aggregator.py:2227/3211 只吃 sector_events 且需 impact>=3",
    ]
    return artifact


def print_summary(a: dict) -> None:
    print("=" * 72)
    print(f"业绩预告方向取证　as_of={a['as_of']}　报告期={a['sources']['quarters']}")
    print("=" * 72)
    f = a["q1_fetch"]
    print("[Q1 取数]", [(m.get("quarter"), m.get("status"), m.get("n")) for m in f["metas"]])
    if f["fetch_error"]:
        print(f"  ⚠️ {f['fetch_error']}")
    print(f"  取数：原始 {a['q1_dedupe']['raw_rows']} → (股,日) {a['q1_dedupe']['stock_day_groups']}"
          f" → 可用 {a['q1_dedupe']['usable_events']}"
          f"（指标类型冲突 {a['q1_dedupe']['type_conflict_groups']}"
          f"={a['q1_dedupe']['type_conflict_share']}，"
          f"净利润口径覆盖 {a['q1_dedupe']['profit_basis_share']}，"
          f"太新剔除 {a['q1_dedupe']['dropped_too_recent']}）")
    if a['q1_dedupe'].get('priced_sample'):
        print(f"  价格面抽样：{a['q1_dedupe']['priced_sample']['taken']} 条"
              f"（{a['q1_dedupe']['priced_sample']['method']}）")
    print("[Q1 预告类型取值面]")
    for t, c in list(a["q1_type_census"].items())[:20]:
        print(f"  {t:<8} 事件={c['n_events']:<6} 个股={c['n_stocks']:<6} 幅度中位={c['change_pct_median']}")
    if a.get("q2_price"):
        print("[Q2 价格面]", a["q2_price"]["kline_source_census"], "状态", a["q2_price"]["event_status"])
        print("[Q2 分类型方向读数]")
        for t, s in sorted(a["q2_direction_by_type"].items(), key=lambda kv: -kv[1]["n_events"]):
            w = next((x for x in s["windows"] if x["window_td"] == 5), s["windows"][-1])
            st = w["sign_test"] or {}
            print(f"  {t:<8} n={s['n_events']:<5} 可算={s['n_priced']:<5} "
                  f"T+5超额={w['excess_mean_pct']}% 跑输占比={st.get('neg_share')} "
                  f"p={st.get('p_two_sided')} → {s['verdict']}")
        br = a["q3_blast_radius"]
        print(f"[Q3 影响面] negative 类型={br['negative_verdict_types']} "
              f"全量被错误置顶={br['population_negative_events']} 条/"
              f"{br['population_negative_stocks']} 股（占全量 "
              f"{br['negative_share_of_population']}）")
        print("[建议映射（仅来自读数）]", a["suggested_direction_map"]["data_driven"])
        print("[未定]", a["suggested_direction_map"]["unresolved"])
        v = a["verdict"]
        print(f"[结论] ready_to_fix_direction={v['ready_to_fix_direction']} 可算样本={v['n_priced_events']}")
        for p in v["preconditions"]:
            print(f"  - {p}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="业绩预告「预告类型」方向取证（个股级 T+N 减基准）")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--quarters", default=None, help="逗号分隔报告期，默认往前 4 个报告期")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    ap.add_argument("--refresh-events", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="只用缓存，不触网（价格面仍可读缓存/DB）")
    ap.add_argument("--events-only", action="store_true", help="只出 Q1 取值面，不取价格")
    ap.add_argument("--max-events", type=int, default=400)
    ap.add_argument("--per-type-floor", type=int, default=40,
                    help="分层抽样：每个预告类型保底可算条数")
    ap.add_argument("--min-type-n", type=int, default=MIN_TYPE_N)
    ap.add_argument("--min-cohort-n", type=int, default=60,
                    help="合并组可判样本门槛（低于此不进读数结论）")
    ap.add_argument("--refetch-insufficient", action="store_true",
                    help="对覆盖不足的 code 绕开「首个非空即返回」、逐源重取覆盖最全的一路（会触网）")
    ap.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--json-only", action="store_true")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    artifact = run(args)
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
