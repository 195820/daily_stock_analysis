# -*- coding: utf-8 -*-
"""P1-1 CATALYST_CONFIDENCE_GATE 分位重标取证表（2026-10-06，第 1 批 #1）

**只出证据、不代替定值**——门槛取值归负责人拍板（【批次5详设】拍板④ /
backlog §5 Phase 5c 4.4）。当前 `catalyst_config.py` 注释即标
「TODO: 待校准 (当前样本量: 0)」，实际 `catalyst_realizations` 已有兑现样本，
本脚本把它转成分位质量表。

产出六节：
1. 样本就绪度——glue_consumed 交易日数（<10 则标注未到期）、兑现样本数、
   **direction 语义污染计数**（污染会导致方向准确率伪低，见下）
2. confidence 分箱质量表（兑现率 / 净化方向正确率 / 前向收益 / **减基准超额**）
3. 门槛模拟表（候选阈值 → 保留样本与池化质量 + 区分度/倒挂趋势判定）
4. 归档埋伏漏斗 + 蓄水池 reason 分布（量化「合格催化缺失」是否为归零主因，
   2026-10-06 离线回放的关键发现）
5. 分类质量（category_tag / source_type / event_type）
6. 留给负责人的决策位（含本次新发现的 direction 语义缺陷待拍板）

口径红线（【总计划】§1.1）：前向收益一律减基准（中证1000 同窗收益），
原始口径数字只作参照不作结论。

用法：
    python scripts/calibrate_catalyst_confidence_gate.py [--days 180]
        [--candidates 0.5,0.55,0.6,0.65,0.7,0.75,0.8] [--window actual|1|3|5]

产出：
    reports/catalyst_confidence_gate_forensics_YYYYMMDD.md
    reports/catalyst_confidence_gate_forensics_YYYYMMDD.json

纯离线只读：不写 DB、不调 LLM、不改任何生产配置。
"""

import argparse
import io
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

# 中证1000 日收盘缓存（回测审核期产出，只读；缺失时减基准列降级为 N/A 而非崩溃）
BENCH_CSV = os.path.join(ROOT_DIR, ".review_tmp", "bt_cache", "index_sh000852.csv")
ARCHIVE_DIR = os.path.join(ROOT_DIR, "data", "got_daily")
VALID_DIRECTION = {"positive", "negative", "binary"}  # 污染过滤见报告§1 注
GLUE_MIN_TRADE_DAYS = 10      # backlog §5 5c：影子/样本满 10 交易日方可判读
CALIB_MIN_SAMPLES = 30        # 与 calibrate_catalyst_params.py 同门槛

DEFAULT_BUCKETS = [0.0, 0.5, 0.6, 0.65, 0.7, 0.75, 0.8, 0.9, 1.01]


def _fmt(v, nd=2, suffix=""):
    return "N/A" if v is None else f"{v:.{nd}f}{suffix}"


def _pct(v, nd=0):
    """比例（0~1 小数）转百分比字符串；传已乘 100 的值会翻倍，调用侧统一给小数。"""
    return "N/A" if v is None else f"{v * 100:.{nd}f}%"


def _load_benchmark():
    """中证1000 收盘序列 {date: close}（升序交易日列表另返回）。"""
    if not os.path.exists(BENCH_CSV):
        return {}, []
    closes, days = {}, []
    with open(BENCH_CSV, "r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        try:
            i_date, i_close = header.index("date"), header.index("close")
        except ValueError:
            return {}, []
        for line in f:
            parts = line.strip().split(",")
            if len(parts) <= max(i_date, i_close):
                continue
            try:
                d = datetime.strptime(parts[i_date][:10], "%Y-%m-%d").date()
                closes[d] = float(parts[i_close])
                days.append(d)
            except ValueError:
                continue
    days = sorted(set(days))
    return closes, days


def _bench_return(closes, days, start_day: date, n_trading: int):
    """start_day（含）之后 n_trading 个交易日基准收益(%)；数据不足返回 None。"""
    if not closes or not days:
        return None
    idx = [i for i, d in enumerate(days) if d >= start_day]
    if not idx:
        return None
    i = idx[0]
    if i + n_trading >= len(days):
        return None
    base, last = closes.get(days[i]), closes.get(days[i + n_trading])
    if not base or not last:
        return None
    return (last / base - 1.0) * 100.0


def _load_records(days: int):
    """读 catalyst_realizations（只读，不建 ORM 关系、不写回）。"""
    from src.got.catalyst_config import CATALYST_CONFIDENCE_GATE
    from src.storage import CatalystRealizationRecord, DatabaseManager

    cutoff = date.today() - timedelta(days=days)
    db = DatabaseManager()
    rows = []
    with db.get_session() as session:
        recs = (
            session.query(CatalystRealizationRecord)
            .filter(CatalystRealizationRecord.event_date >= cutoff)
            .all()
        )
        for r in recs:
            rows.append({
                "confidence": r.confidence,
                "event_date": r.event_date,
                "sector": r.sector,
                "is_realized": bool(r.is_realized),
                "direction_correct": (
                    bool(r.drift_direction_correct) if r.drift_direction_correct is not None else None
                ),
                "day_1": r.day_1_return,
                "day_3": r.day_3_return,
                "day_5": r.day_5_return,
                "actual_return": r.actual_return,
                "category_tag": r.category_tag,
                "event_type": r.event_type,
                "source_type": r.source_type,
                "impact_level": r.impact_level,
                "direction": r.direction,
            })
    return rows, CATALYST_CONFIDENCE_GATE


def _enrich_excess(rows, closes, days, window):
    """给每条记录补 fwd / bench / excess（同窗基准收益，缺失留 None）。

    window="actual"：跟生产侧 `_check_direction` 同一口径——优先最长可用窗
    （day_5 → day_3 → day_1），基准按**同一 horizon** 取，避免混窗减错窗；
    window=1/3/5：只取该单一窗（样本稀疏但 horizon 统一）。
    """
    for r in rows:
        r["fwd"], r["bench"], r["excess"], r["horizon"] = None, None, None, None
        if isinstance(r["event_date"], str):
            try:
                r["event_date"] = datetime.strptime(r["event_date"][:10], "%Y-%m-%d").date()
            except ValueError:
                continue
        if r["event_date"] is None:
            continue
        if window == "actual":
            for h in (5, 3, 1):
                if r.get(f"day_{h}") is not None:
                    r["fwd"], r["horizon"] = r[f"day_{h}"], h
                    break
            if r["fwd"] is None and r.get("actual_return") is not None:
                r["fwd"], r["horizon"] = r["actual_return"], None
        else:
            r["fwd"], r["horizon"] = r.get(f"day_{window}"), window
        if r["fwd"] is None or r["horizon"] is None:
            continue
        b = _bench_return(closes, days, r["event_date"], r["horizon"])
        r["bench"] = b
        r["excess"] = r["fwd"] - b if b is not None else None
    return rows


def _dir_acc(sub):
    """方向正确率（仅对 direction ∈ 合法枚举的记录取均值）。

    GLOBAL_TO_A_SHARE_MAP 把 direction 填成了漂移类型词（drift/fade/binary），
    `_check_direction` 对非 positive/negative/binary 恒返回 False——
    直接平均会把这批样本计作“全错”，系统性拉低方向准确率。过滤后另报
    n_dir_valid，便于核对覆盖度。
    """
    acc = [
        r["direction_correct"] for r in sub
        if (r.get("direction") or "") in VALID_DIRECTION
        and r["direction_correct"] is not None
    ]
    return ((sum(1 for x in acc if x) / len(acc)) if acc else None), len(acc)


def _bucket_stats(rows, buckets):
    out = []
    for lo, hi in zip(buckets[:-1], buckets[1:]):
        sub = [r for r in rows if r["confidence"] is not None and lo <= r["confidence"] < hi]
        if not sub:
            out.append({"range": f"[{lo:.2f},{hi:.2f})", "n": 0})
            continue
        realized = [r for r in sub if r["is_realized"]]
        dc, n_dc = _dir_acc(realized)
        fwd = [r["fwd"] for r in realized if r.get("fwd") is not None]
        exc = [r["excess"] for r in realized if r.get("excess") is not None]
        out.append({
            "range": f"[{lo:.2f},{hi:.2f})",
            "n": len(sub),
            "n_realized": len(realized),
            "realize_rate": len(realized) / len(sub),
            "dir_acc": dc,
            "n_dir_valid": n_dc,
            "n_fwd": len(fwd),
            "fwd_mean": (sum(fwd) / len(fwd)) if fwd else None,
            "fwd_median": statistics.median(fwd) if fwd else None,
            "n_excess": len(exc),
            "excess_mean": (sum(exc) / len(exc)) if exc else None,
            "excess_median": statistics.median(exc) if exc else None,
            "win_rate_excess": (sum(1 for x in exc if x > 0) / len(exc)) if exc else None,
            "share": len(sub) / len(rows),
        })
    return out


def _gate_sim(rows, gate: float, candidates):
    """候选阈值下的保留集质量（只算已兑现且带前向收益的样本）。"""
    labeled = [r for r in rows if r["is_realized"] and r["confidence"] is not None]
    out = []
    for thr in sorted(candidates):
        kept = [r for r in labeled if r["confidence"] >= thr]
        dc, n_dc = _dir_acc(kept)
        fwd = [r["fwd"] for r in kept if r.get("fwd") is not None]
        exc = [r["excess"] for r in kept if r.get("excess") is not None]
        out.append({
            "threshold": thr,
            "kept": len(kept),
            "kept_share": len(kept) / len(labeled) if labeled else None,
            "rejected": len(labeled) - len(kept),
            "dir_acc": dc,
            "n_dir_valid": n_dc,
            "n_fwd": len(fwd),
            "fwd_mean": (sum(fwd) / len(fwd)) if fwd else None,
            "n_excess": len(exc),
            "excess_mean": (sum(exc) / len(exc)) if exc else None,
            "win_rate_excess": (sum(1 for x in exc if x > 0) / len(exc)) if exc else None,
            "is_current": abs(thr - gate) < 1e-9,
        })
    return out, len(labeled)


def _archive_funnel_scan(max_days: int = 30):
    """近 N 个归档交易日：埋伏漏斗 + 蓄水池 reason 分布（归零主因取证）。"""
    if not os.path.isdir(ARCHIVE_DIR):
        return {}
    dirs = sorted(
        d for d in os.listdir(ARCHIVE_DIR)
        if os.path.isdir(os.path.join(ARCHIVE_DIR, d)) and d.isdigit() and len(d) == 8
    )
    picked = dirs[-max_days:]
    days_with_pool, initial_total, final_total = 0, 0, 0
    reasons, glue_days = Counter(), []
    per_day = []
    for d in picked:
        p = os.path.join(ARCHIVE_DIR, d, "unified.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError):
            continue
        if data.get("glue_consumed"):
            glue_days.append(d)
        funnel = data.get("ambush_gate_funnel") or {}
        ini = funnel.get("initial_candidates")
        fin = funnel.get("final_pool")
        if ini is not None:
            days_with_pool += 1
            initial_total += int(ini)
            final_total += int(fin or 0)
            per_day.append({"date": d, "initial": int(ini), "final": int(fin or 0),
                            "g4": funnel.get("G4_吸筹")})
        for item in data.get("ambush_reservoir") or []:
            reason = (item.get("reason") or "unknown").split(":")[0]
            reasons[reason] += 1
    return {
        "scanned_days": len(picked),
        "funnel_days": days_with_pool,
        "glue_consumed_days": len(glue_days),
        "glue_consumed_dates": glue_days,
        "initial_total": initial_total,
        "final_total": final_total,
        "zero_final_days": sum(1 for x in per_day if x["final"] == 0),
        "reservoir_reasons": dict(reasons),
        "per_day": per_day,
    }


def _group_quality(rows):
    """按 category_tag / source_type 的减基准质量（门槛之外还需分类看）。"""
    out = {}
    for field in ("category_tag", "source_type", "event_type"):
        groups = defaultdict(list)
        for r in rows:
            if not r["is_realized"]:
                continue
            groups[r.get(field) or "unknown"].append(r)
        tbl = []
        for key, sub in sorted(groups.items(), key=lambda z: -len(z[1])):
            exc = [x["excess"] for x in sub if x.get("excess") is not None]
            fwd = [x["fwd"] for x in sub if x.get("fwd") is not None]
            dc, n_dc = _dir_acc(sub)
            tbl.append({
                "key": key,
                "n": len(sub),
                "fwd_mean": (sum(fwd) / len(fwd)) if fwd else None,
                "excess_mean": (sum(exc) / len(exc)) if exc else None,
                "win_rate_excess": (sum(1 for x in exc if x > 0) / len(exc)) if exc else None,
                "dir_acc": dc,
                "n_dir_valid": n_dc,
            })
        out[field] = tbl
    return out


def build_report(args):
    rows, gate = _load_records(args.days)
    closes, days = _load_benchmark()
    rows = _enrich_excess(rows, closes, days, args.window)
    labeled_all = [r for r in rows if r["is_realized"]]
    with_conf = [r for r in rows if r["confidence"] is not None]
    with_excess = [r for r in labeled_all if r.get("excess") is not None]
    win_label = ("生产同口径（优先最长可用窗 day_5→day_3→day_1）"
                 if args.window == "actual" else f"T+{args.window} 单一窗")
    fun = _archive_funnel_scan(args.archive_days)
    buckets = _bucket_stats(with_conf, args.buckets)
    sims, n_labeled = _gate_sim(rows, gate, args.candidates)
    quality = _group_quality(rows)

    glue_ready = fun.get("glue_consumed_days", 0) >= GLUE_MIN_TRADE_DAYS
    labeled_with_conf = [r for r in labeled_all if r["confidence"] is not None]
    sample_ready = len(labeled_with_conf) >= CALIB_MIN_SAMPLES

    L = []
    L.append(f"# CATALYST_CONFIDENCE_GATE 分位重标取证 — {date.today().isoformat()}")
    L.append("")
    L.append(f"> 第 1 批 #1（P1-1）。**本脚本只出证据，门槛取值归负责人拍板**；")
    L.append(f"> 现值 `CATALYST_CONFIDENCE_GATE={gate}`（`src/got/catalyst_config.py`，注释标「待校准」），")
    L.append(f"> 消费点 Node6 埋伏通道 5 处（合格催化过滤 / 确定性升格 / 提名 / 已发酵检查 / Prompt 注入）。")
    L.append(f"> 统计窗口：近 {args.days} 日历天，前向收益={win_label}，"
             f"基准=中证1000（{'已加载' if closes else '缓存缺失，减基准列 N/A'}）；"
             f"超额 = 板块前向 − 同 horizon 基准收益。")
    L.append("")

    L.append("## 1. 样本就绪度")
    L.append("")
    L.append("| 口径 | 数量 | 判定 |")
    L.append("|------|------|------|")
    L.append(f"| 归档事件（窗口内） | {len(rows)} | — |")
    L.append(f"| 已兑现（is_realized） | {len(labeled_all)} | — |")
    L.append(f"| 带 confidence 取值 | {len(with_conf)}（已兑现 "
             f"{len(labeled_with_conf)}） | 有效样本 "
             f"{'≥' if sample_ready else '<'}{CALIB_MIN_SAMPLES} "
             f"{'✅ 可出建议' if sample_ready else '⚠️ 样本不足'} |")
    L.append(f"| 可算减基准超额 | {len(with_excess)} | 覆盖已兑现样本 "
             f"{len(with_excess)/max(len(labeled_all),1):.0%}（T+5 单窗仅 "
             f"{sum(1 for r in labeled_all if r.get('day_5') is not None)} 条、"
             f"T+3 {sum(1 for r in labeled_all if r.get('day_3') is not None)} 条，"
             f"故单窗分析需降窗或走生产同口径） |")
    L.append(f"| glue_consumed 交易日 | {fun.get('glue_consumed_days','?')}"
             f"（扫描 {fun.get('scanned_days','?')} 个归档日） | "
             f"{'✅' if glue_ready else f'⚠️ 未满 {GLUE_MIN_TRADE_DAYS}，判读需再攒'} |")
    contaminated = [r for r in labeled_all if (r.get("direction") or "") not in VALID_DIRECTION]
    clean_acc = _dir_acc(labeled_all)
    if contaminated and clean_acc[0] is not None:
        raw_acc = sum(1 for r in labeled_all if r["direction_correct"]) / max(len(labeled_all), 1)
        L.append("")
        L.append(
            f"> ⚠️ **数据质量陷阱（本表已过滤，但影响其他报表）**：已兑现样本中 "
            f"{len(contaminated)} 条（{len(contaminated)/max(len(labeled_all),1):.0%}）"
            "的 `direction` 存的是漂移类型词（drift/fade），而 `_check_direction` "
            "只认 positive/negative/binary，其余恒判错——源头 `catalyst_config.GLOBAL_TO_A_SHARE_MAP` "
            "的 direction 字段语义错位。原始口径方向准确率被拉到 "
            f"{raw_acc:.0%}，剔除污染后为 **{clean_acc[0]:.0%}**（有效样本 {clean_acc[1]}）。"
        )
        L.append(
            "> → 副作用：`scripts/calibrate_catalyst_params.py` 基于 direction_acc<50% "
            "会给出「降门槛至 0.60」的建议，属该缺陷导致的伪结论，不得采用。"
        )
    L.append("")
    if not glue_ready:
        L.append(f"> 注：backlog §5 5c 的「~10-05 解锁」为日历日估算未扣国庆假期；"
                 f"假期后首个交易日 10-08 起重新起算，实际判读时点以本表满 {GLUE_MIN_TRADE_DAYS} 为准。")
        L.append("")

    L.append(f"## 2. confidence 分箱质量表（{win_label}）")
    L.append("")
    L.append("| 分箱 | n | 已兑现 | 兑现率 | 方向正确率(n有效) | 前向均值(n) | 减基准超额均值(n) | 超额中位 | 超额胜率 | 占比 |")
    L.append("|------|---|--------|--------|------------------|------------|--------------------|----------|----------|------|")
    for b in buckets:
        if not b["n"]:
            L.append(f"| {b['range']} | 0 | — | — | — | — | — | — | — | — |")
            continue
        L.append(
            f"| {b['range']} | {b['n']} | {b['n_realized']} | {b['realize_rate']:.0%} | "
            f"{_pct(b['dir_acc'])} ({b['n_dir_valid']}) | "
            f"{_fmt(b['fwd_mean'])} ({b['n_fwd']}) | "
            f"{_fmt(b['excess_mean'])} ({b['n_excess']}) | {_fmt(b['excess_median'])} | "
            f"{_pct(b['win_rate_excess'])} | "
            f"{b['share']:.0%} |"
        )
    L.append("")
    L.append("> 读法：**减基准超额胜率 > 50% 且超均值 > 0 的最高分箱下沿**，才是门槛可下调的实证区间；")
    L.append("> 若低分箱胜率 ≤ 现分箱，则 0.7 硬闸方向正确、问题在事件覆盖度而非阈值（对应第 3 表）。")
    L.append("")

    L.append("## 3. 门槛模拟（候选阈值 → 保留集质量）")
    L.append("")
    L.append("| 阈值 | 保留 | 保留占比 | 拦截 | 方向正确率(n有效) | 前向均值(n) | 超额均值(n) | 超额胜率 |")
    L.append("|------|------|----------|------|------------------|------------|--------------|----------|")
    for s in sims:
        mark = " **（现值）**" if s["is_current"] else ""
        L.append(
            f"| {s['threshold']:.2f}{mark} | {s['kept']} | "
            f"{_pct(s['kept_share'])} | {s['rejected']} | "
            f"{_pct(s['dir_acc'])} ({s['n_dir_valid']}) | {_fmt(s['fwd_mean'])} ({s['n_fwd']}) | "
            f"{_fmt(s['excess_mean'])} ({s['n_excess']}) | {_pct(s['win_rate_excess'])} |"
        )
    L.append("")
    for key, label in (("win_rate_excess", "减基准超额胜率"), ("dir_acc", "方向正确率（净化）")):
        vals = [s[key] for s in sims if s.get(key) is not None]
        if len(vals) >= 3:
            spread = max(vals) - min(vals)
            if spread <= 0.08:
                verdict = "【无实质区分度】——单调性与幅值都不够，拉高门槛买不到质量"
            elif vals[-1] < vals[0]:
                verdict = ("【倒挂】——阈值越高该指标反而越差，现置信度分值与结果负相关，"
                           "收紧门槛会适得其反")
            else:
                verdict = "【正向有区分度】——可继续看拐点"
            L.append(
                f"> {label}随阈值 {sims[0]['threshold']:.2f}→{sims[-1]['threshold']:.2f} "
                f"从 {vals[0]:.0%} 变到 {vals[-1]:.0%}，极差 {spread:.0%} → {verdict}"
            )
    cur = next((s for s in sims if s["is_current"]), None)
    lo = next((s for s in sims if abs(s["threshold"] - 0.6) < 1e-9), None)
    if cur and lo and n_labeled:
        L.append(
            f"> 0.7→0.6 的边际：多放行 {lo['kept'] - cur['kept']} 条"
            f"（保留占比 {cur['kept_share']:.0%}→{lo['kept_share']:.0%}），"
            f"超额均值 {cur['excess_mean'] if cur['excess_mean'] is None else round(cur['excess_mean'],2)} → "
            f"{lo['excess_mean'] if lo['excess_mean'] is None else round(lo['excess_mean'],2)}。"
            "代码里 A3 影子日志（`[Node 6 闸门影子] 门槛 0.60 会放行`）与此表同源，可交叉核对。"
        )
        L.append("")

    L.append("## 4. 归档埋伏漏斗 / 蓄水池 reason（归零主因取证）")
    L.append("")
    L.append(f"- 扫描归档日：{fun.get('scanned_days', 0)}，其中有漏斗读数 {fun.get('funnel_days', 0)} 日")
    L.append(f"- 候选累计 {fun.get('initial_total', 0)} → 出池 {fun.get('final_total', 0)}，"
             f"出池为 0 的交易日 {fun.get('zero_final_days', 0)} 日")
    reasons = fun.get("reservoir_reasons") or {}
    if reasons:
        L.append("")
        L.append("| 蓄水池 reason | 次数 |")
        L.append("|-----------------|------|")
        for k, v in sorted(reasons.items(), key=lambda z: -z[1]):
            L.append(f"| {k} | {v} |")
    L.append("")
    L.append("> 2026-10-06 离线回放（`--got-replay 20260930`，冻结态）实测：G4 放行 6 只后 "
             "`final_pool` 仍=0，6 只全因「合格催化缺失」沉入蓄水池 → "
             "**归零主闸已从 G4 资金闸转移到催化剂 confidence 门槛**，故本表优先于 G4 阈值重调。")
    L.append("")

    L.append("## 5. 分类质量（阈值之外还需分型看）")
    for field, title in (("category_tag", "category_tag（drift/fade/binary）"),
                         ("source_type", "source_type"),
                         ("event_type", "event_type")):
        tbl = quality.get(field) or []
        if not tbl:
            continue
        L.append("")
        L.append(f"### 按 {title}")
        L.append("")
        L.append("| 分组 | n | 前向均值 | 超额均值 | 超额胜率 | 方向正确率(n有效) |")
        L.append("|------|---|----------|----------|----------|--------------------|")
        for r in tbl:
            L.append(f"| {r['key']} | {r['n']} | {_fmt(r['fwd_mean'])} | {_fmt(r['excess_mean'])} | "
                     f"{_pct(r['win_rate_excess'])} | {_pct(r['dir_acc'])} ({r['n_dir_valid']}) |")
    L.append("")

    L.append("## 6. 留给负责人的决策位")
    L.append("")
    L.append("1. 取值：维持 0.7 / 下调 0.6 / 收紧 0.75（须以第 2、3 表超额胜率为据，"
             "不得用原始前向收益下结论）；")
    L.append("2. 若结论是「事件覆盖度不足而非阈值过严」→ 归 WI-P1-6（闸门评分化）与"
             "事件锚重建（B4-1），不单独动门槛；")
    L.append("3. 改值属配置改动，需同步 `catalyst_config.py:149` 注释与 backlog §5 5c 行销账；"
             "候选池归零回归为必备项（【架构计划】§0.1-3）。")
    if contaminated:
        L.append(
            "4. **先修数据再读数**（本次新发现）：`GLOBAL_TO_A_SHARE_MAP.direction` 存的"
            "是 drift/fade，与 `_check_direction` 的枚举不匹配，使 "
            f"{len(contaminated)} 条已兑现样本被恒判错——需负责人拍板二选一："
            "① 修源头（映射表改 positive/negative/binary，category_tag 已是类型位）；"
            "② 修判定（`_check_direction` 按 drift→正/fade→负 语义补分支）。"
            "两者均改变已有统计读数，属新 WI，不属本门槛标定。"
        )
    L.append("")
    if not sample_ready or not glue_ready:
        blockers = []
        if not sample_ready:
            blockers.append(f"带 confidence 的已兑现样本 {len(labeled_with_conf)} < {CALIB_MIN_SAMPLES}")
        if not glue_ready:
            blockers.append(f"glue_consumed 交易日 {fun.get('glue_consumed_days', 0)} < {GLUE_MIN_TRADE_DAYS}")
        L.append(f"⚠️ **本次仅取证、不建议改值**：" + "；".join(blockers))
        L.append("")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days_calendar": args.days,
        "forward_window": args.window,
        "current_gate": gate,
        "benchmark_available": bool(closes),
        "counts": {
            "records": len(rows),
            "realized": len(labeled_all),
            "with_confidence": len(with_conf),
            "labeled_with_confidence": len(labeled_with_conf),
            "with_excess": len(with_excess),
            "direction_contaminated": len(contaminated),
        },
        "buckets": buckets,
        "gate_simulation": sims,
        "archive_funnel": fun,
        "group_quality": quality,
        "readiness": {
            "sample_ready": sample_ready,
            "glue_ready": glue_ready,
            "glue_min_trade_days": GLUE_MIN_TRADE_DAYS,
        },
    }
    return L, payload


def main():
    ap = argparse.ArgumentParser(description="CATALYST_CONFIDENCE_GATE 分位重标取证（只读）")
    ap.add_argument("--days", type=int, default=180, help="日历天窗口（默认 180）")
    ap.add_argument("--window", default="actual", choices=["actual", "1", "3", "5"],
                    help="前向收益口径：actual=生产同口径（默认，样本最密）/ 1 / 3 / 5 =单窗")
    ap.add_argument("--candidates", default="0.5,0.55,0.6,0.65,0.7,0.75,0.8",
                    help="门槛候选值，逗号分隔")
    ap.add_argument("--buckets", default=",".join(str(x) for x in DEFAULT_BUCKETS),
                    help="分箱边界，逗号分隔")
    ap.add_argument("--archive-days", type=int, default=30, help="扫描最近多少个归档日")
    ap.add_argument("--outdir", default=os.path.join(ROOT_DIR, "reports"))
    args = ap.parse_args()

    args.window = args.window if args.window == "actual" else int(args.window)
    args.candidates = [float(x) for x in args.candidates.split(",") if x.strip()]
    args.buckets = [float(x) for x in args.buckets.split(",") if x.strip()]
    if args.buckets != sorted(args.buckets):
        ap.error("--buckets 需升序")

    lines, payload = build_report(args)

    os.makedirs(args.outdir, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    md_path = os.path.join(args.outdir, f"catalyst_confidence_gate_forensics_{stamp}.md")
    json_path = os.path.join(args.outdir, f"catalyst_confidence_gate_forensics_{stamp}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, default=str)

    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[写出] {md_path}\n[写出] {json_path}")


if __name__ == "__main__":
    main()
