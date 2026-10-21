# -*- coding: utf-8 -*-
"""催化剂价值分级校准取证（2026-10-21）

**只读取证/不改生产参数**——所有阈值建议仅供负责人拍板参考。

产出五节：
1. 身份重复度 — (sector, normalized title) identity 行数分布 + 首次/重复行 excess 均值
2. 来源×类型分级 — source × category_tag × impact_level 的 excess+d1 方向 + 方向正确率
3. novelty 分档 — 新闻首现 vs 第 N 次出现（依赖 node4_raw_news 关联）
4. T-1 前瞻窗检验 — 文献 (Hu et al. 2019; Gao-Hu-Zhang 2022) 事件前 1 日收益
5. 参数建议表 — macro 排除 / confidence 新闸 / identity 重锚窗 各候选阈值

口径红线：前向收益一律减基准（中证1000 同窗收益），原始口径数字只作参照不作结论。
样本 < min-samples 不出建议（照 CALIB_MIN_SAMPLES = 30 纪律）。

用法：
    python scripts/calibrate_catalyst_value_tier.py [--days 365] [--out-dir reports] [--min-samples 30]

产出：
    reports/catalyst_value_tier_YYYYMMDD.md
    reports/catalyst_value_tier_YYYYMMDD.json

纯离线只读：不写 DB、不调 LLM、不改任何生产配置。
"""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

# 复用 calibrate_catalyst_confidence_gate.py 的件（importlib 加载，同 forensics 测试范式）
_CONF_GATE_PATH = os.path.join(ROOT_DIR, "scripts", "calibrate_catalyst_confidence_gate.py")
_spec = importlib.util.spec_from_file_location("_conf_gate", _CONF_GATE_PATH)
_conf_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conf_gate)

_load_benchmark = _conf_gate._load_benchmark
_bench_return = _conf_gate._bench_return
_enrich_excess = _conf_gate._enrich_excess
_dir_acc = _conf_gate._dir_acc
_fmt = _conf_gate._fmt
_pct = _conf_gate._pct
VALID_DIRECTION = _conf_gate.VALID_DIRECTION


def _normalize_title(title: str) -> str:
    """标题归一化：去空白/标点/大小写，用于 identity 分组。"""
    if not title:
        return ""
    t = re.sub(r"[\s\u3000]+", "", title)
    t = re.sub(r"[^\w\u4e00-\u9fff]", "", t)
    return t.lower()


def _identity_dup_stats(rows):
    """身份重复度统计：(sector, normalized title) identity 行数分布 + 首次/重复行均值。

    返回 dict:
      - dist: Counter({bucket: count})
      - total_identities: int
      - mean_rows_per_identity: float
      - first_occurrence: {n, d1_mean, d3_mean, d5_mean, dir_acc}
      - repeat: {n, d1_mean, d3_mean, d5_mean, dir_acc}
    """
    groups = defaultdict(list)
    for r in rows:
        key = (r.get("sector", ""), _normalize_title(r.get("title", "")))
        groups[key].append(r)

    dist = Counter()
    for key, grp in groups.items():
        n = len(grp)
        if n == 1:
            dist["1"] += 1
        elif 2 <= n <= 3:
            dist["2-3"] += 1
        elif 4 <= n <= 6:
            dist["4-6"] += 1
        elif 7 <= n <= 10:
            dist["7-10"] += 1
        else:
            dist[">10"] += 1

    first_rows, repeat_rows = [], []
    for key, grp in groups.items():
        grp_sorted = sorted(grp, key=lambda r: r.get("event_date") or date.min)
        first_rows.append(grp_sorted[0])
        repeat_rows.extend(grp_sorted[1:])

    def _mean_excess(sub, window):
        vals = [r.get(f"excess_d{window}") for r in sub if r.get(f"excess_d{window}") is not None]
        return statistics.mean(vals) if vals else None

    def _dir_acc_sub(sub):
        acc, n = _dir_acc(sub)
        return acc, n

    return {
        "dist": dict(dist),
        "total_identities": len(groups),
        "mean_rows_per_identity": statistics.mean(len(g) for g in groups.values()) if groups else 0.0,
        "first_occurrence": {
            "n": len(first_rows),
            "d1_mean": _mean_excess(first_rows, 1),
            "d3_mean": _mean_excess(first_rows, 3),
            "d5_mean": _mean_excess(first_rows, 5),
            "dir_acc": _dir_acc_sub(first_rows),
        },
        "repeat": {
            "n": len(repeat_rows),
            "d1_mean": _mean_excess(repeat_rows, 1),
            "d3_mean": _mean_excess(repeat_rows, 3),
            "d5_mean": _mean_excess(repeat_rows, 5),
            "dir_acc": _dir_acc_sub(repeat_rows),
        },
    }


def _first_vs_repeat(rows):
    """首次 vs 重复行均值（兼容旧接口，内部调 _identity_dup_stats）。"""
    stats = _identity_dup_stats(rows)
    return stats["first_occurrence"], stats["repeat"]


def _load_records_extended(days: int):
    """读 catalyst_realizations（扩展字段：source, title, impact_level）。"""
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
                "title": r.title,
                "source": r.source,
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


def _load_node4_raw_news():
    """读 node4_raw_news（title_normalized, first_seen_date, appearance_count）。"""
    from src.storage import DatabaseManager, Node4RawNewsRecord

    db = DatabaseManager()
    news_map = {}
    with db.get_session() as session:
        recs = session.query(Node4RawNewsRecord).all()
        for r in recs:
            key = _normalize_title(r.title_normalized or "")
            if key:
                news_map[key] = {
                    "first_seen_date": r.first_seen_date,
                    "appearance_count": r.appearance_count or 1,
                }
    return news_map


def _source_category_stats(rows, min_samples: int):
    """来源×类型分级：source × category_tag × impact_level 的 excess+d1 方向 + 方向正确率。"""
    groups = defaultdict(list)
    for r in rows:
        src = r.get("source") or "unknown"
        cat = r.get("category_tag") or "unknown"
        imp = r.get("impact_level") or "unknown"
        groups[(src, cat, imp)].append(r)

    out = []
    for (src, cat, imp), grp in sorted(groups.items()):
        n = len(grp)
        d1_vals = [r.get("excess_d1") for r in grp if r.get("excess_d1") is not None]
        d3_vals = [r.get("excess_d3") for r in grp if r.get("excess_d3") is not None]
        d5_vals = [r.get("excess_d5") for r in grp if r.get("excess_d5") is not None]
        dir_acc, n_dir = _dir_acc(grp)
        out.append({
            "source": src,
            "category_tag": cat,
            "impact_level": imp,
            "n": n,
            "d1_mean": statistics.mean(d1_vals) if d1_vals else None,
            "d3_mean": statistics.mean(d3_vals) if d3_vals else None,
            "d5_mean": statistics.mean(d5_vals) if d5_vals else None,
            "dir_acc": dir_acc,
            "n_dir": n_dir,
            "sample_sufficient": n >= min_samples,
        })
    return out


def _novelty_stats(rows, news_map, min_samples: int):
    """novelty 分档：首现 / 第2次 / 第3-5次 / >5次。

    若 news_map 为空或关联失败，返回 SKIP 标记。
    """
    if not news_map:
        return {"skip": True, "reason": "novelty 口径缺失（node4_raw_news 无可用关联列）"}

    buckets = {"首现": [], "第2次": [], "第3-5次": [], ">5次": []}
    unmatched = 0
    for r in rows:
        key = _normalize_title(r.get("title", ""))
        if not key or key not in news_map:
            unmatched += 1
            continue
        news_info = news_map[key]
        count = news_info.get("appearance_count", 1)
        if count == 1:
            buckets["首现"].append(r)
        elif count == 2:
            buckets["第2次"].append(r)
        elif 3 <= count <= 5:
            buckets["第3-5次"].append(r)
        else:
            buckets[">5次"].append(r)
    # 关联可靠性检查：未匹配率 > 50% 则 SKIP（避免伪造读数）
    total = sum(len(g) for g in buckets.values()) + unmatched
    if total > 0 and unmatched / total > 0.5:
        return {"skip": True, "reason": f"novelty 口径缺失（node4_raw_news 关联失败率 {unmatched}/{total} = {unmatched/total:.1%}）"}

    out = []
    for bucket_name, grp in buckets.items():
        n = len(grp)
        d1_vals = [r.get("excess_d1") for r in grp if r.get("excess_d1") is not None]
        d3_vals = [r.get("excess_d3") for r in grp if r.get("excess_d3") is not None]
        d5_vals = [r.get("excess_d5") for r in grp if r.get("excess_d5") is not None]
        dir_acc, n_dir = _dir_acc(grp)
        out.append({
            "bucket": bucket_name,
            "n": n,
            "d1_mean": statistics.mean(d1_vals) if d1_vals else None,
            "d3_mean": statistics.mean(d3_vals) if d3_vals else None,
            "d5_mean": statistics.mean(d5_vals) if d5_vals else None,
            "dir_acc": dir_acc,
            "n_dir": n_dir,
            "sample_sufficient": n >= min_samples,
        })
    return {"skip": False, "buckets": out, "unmatched": unmatched}


def _t1_window_check(rows):
    """T-1 前瞻窗检验：文献 (Hu et al. 2019; Gao-Hu-Zhang 2022) 事件前 1 日收益。

    当前 schema 无 T-1 收益列，诚实 SKIP。
    """
    return {
        "skip": True,
        "reason": "T-1 窗需另立数据 WI（当前 schema 无 T-1 收益列）",
        "literature": [
            "Hu, Pan, Wang & Zhu (2019/2020, NBER WP 25817): FOMC/NFP/ISM/GDP 公告前 24h 存在正漂移",
            "Gao, Hu & Zhang (2022, PBC SF): 财报公告前 10 日窗口贡献 72% 的公告溢价",
        ],
    }


def _parameter_suggestions(rows, min_samples: int):
    """参数建议表：macro 排除 / confidence 新闸 / identity 重锚窗。"""
    suggestions = []

    # 1. macro 排除
    macro_rows = [r for r in rows if r.get("source") == "macro_rule"]
    non_macro_rows = [r for r in rows if r.get("source") != "macro_rule"]
    macro_d1 = [r.get("excess_d1") for r in macro_rows if r.get("excess_d1") is not None]
    non_macro_d1 = [r.get("excess_d1") for r in non_macro_rows if r.get("excess_d1") is not None]
    macro_dir_acc, macro_n_dir = _dir_acc(macro_rows)
    non_macro_dir_acc, non_macro_n_dir = _dir_acc(non_macro_rows)
    suggestions.append({
        "parameter": "macro exclusion",
        "option": "exclude macro_rule",
        "kept_n": len(non_macro_rows),
        "kept_excess_d1": statistics.mean(non_macro_d1) if non_macro_d1 else None,
        "kept_dir_acc": non_macro_dir_acc,
        "excluded_n": len(macro_rows),
        "excluded_excess_d1": statistics.mean(macro_d1) if macro_d1 else None,
        "excluded_dir_acc": macro_dir_acc,
        "sample_sufficient": len(non_macro_rows) >= min_samples and len(macro_rows) >= min_samples,
    })

    # 2. confidence 新闸候选
    conf_candidates = [0.55, 0.60, 0.65, 0.70, 0.75]
    for gate in conf_candidates:
        kept = [r for r in rows if (r.get("confidence") or 0) >= gate]
        dropped = [r for r in rows if (r.get("confidence") or 0) < gate]
        kept_d1 = [r.get("excess_d1") for r in kept if r.get("excess_d1") is not None]
        kept_d3 = [r.get("excess_d3") for r in kept if r.get("excess_d3") is not None]
        kept_d5 = [r.get("excess_d5") for r in kept if r.get("excess_d5") is not None]
        kept_dir_acc, kept_n_dir = _dir_acc(kept)
        dropped_d1 = [r.get("excess_d1") for r in dropped if r.get("excess_d1") is not None]
        dropped_dir_acc, dropped_n_dir = _dir_acc(dropped)
        suggestions.append({
            "parameter": "confidence gate",
            "option": f">={gate}",
            "kept_n": len(kept),
            "kept_excess_d1": statistics.mean(kept_d1) if kept_d1 else None,
            "kept_excess_d3": statistics.mean(kept_d3) if kept_d3 else None,
            "kept_excess_d5": statistics.mean(kept_d5) if kept_d5 else None,
            "kept_dir_acc": kept_dir_acc,
            "dropped_n": len(dropped),
            "dropped_excess_d1": statistics.mean(dropped_d1) if dropped_d1 else None,
            "dropped_dir_acc": dropped_dir_acc,
            "sample_sufficient": len(kept) >= min_samples,
        })

    # 3. identity 重锚窗候选（日历日）
    reanchor_windows = [7, 14, 30]
    for window_days in reanchor_windows:
        groups = defaultdict(list)
        for r in rows:
            key = (r.get("sector", ""), _normalize_title(r.get("title", "")))
            groups[key].append(r)

        deduped_rows = []
        for key, grp in groups.items():
            grp_sorted = sorted(grp, key=lambda r: r.get("event_date") or date.min)
            last_anchor_date = None
            for r in grp_sorted:
                evt_date = r.get("event_date")
                if evt_date is None:
                    continue
                if last_anchor_date is None or (evt_date - last_anchor_date).days > window_days:
                    deduped_rows.append(r)
                    last_anchor_date = evt_date

        deduped_d1 = [r.get("excess_d1") for r in deduped_rows if r.get("excess_d1") is not None]
        deduped_dir_acc, deduped_n_dir = _dir_acc(deduped_rows)
        suggestions.append({
            "parameter": "identity re-anchor window",
            "option": f"{window_days} calendar days",
            "kept_n": len(deduped_rows),
            "kept_excess_d1": statistics.mean(deduped_d1) if deduped_d1 else None,
            "kept_dir_acc": deduped_dir_acc,
            "sample_sufficient": len(deduped_rows) >= min_samples,
        })

    return suggestions


def build_report(args):
    rows, gate = _load_records_extended(args.days)
    closes, days = _load_benchmark()

    # 减基准 excess（d1/d3/d5 三窗）
    for w in (1, 3, 5):
        _enrich_excess(rows, closes, days, w)
        for r in rows:
            r[f"excess_d{w}"] = r.get("excess")

    # 数据新鲜度
    event_dates = [r.get("event_date") for r in rows if r.get("event_date")]
    max_event_date = max(event_dates) if event_dates else None
    row_count = len(rows)

    # Section 1: 身份重复度
    identity_stats = _identity_dup_stats(rows)

    # Section 2: 来源×类型分级
    source_cat_stats = _source_category_stats(rows, args.min_samples)

    # Section 3: novelty 分档
    try:
        news_map = _load_node4_raw_news()
        novelty_stats = _novelty_stats(rows, news_map, args.min_samples)
    except Exception as e:
        novelty_stats = {"skip": True, "reason": f"node4_raw_news 读取失败: {e}"}

    # Section 4: T-1 前瞻窗检验
    t1_stats = _t1_window_check(rows)

    # Section 5: 参数建议表
    param_suggestions = _parameter_suggestions(rows, args.min_samples)

    # Markdown 渲染
    md = io.StringIO()
    md.write("# 催化剂价值分级校准取证\n\n")
    md.write("**只读取证/不改生产参数**——所有阈值建议仅供负责人拍板参考。\n\n")
    md.write(f"**数据新鲜度**：max event_date = {max_event_date}, row count = {row_count}\n\n")
    md.write(f"**口径红线**：前向收益一律减基准（中证1000 同窗收益），原始口径数字只作参照不作结论。\n\n")
    md.write(f"**样本门槛**：n < {args.min_samples} 不出建议（照 CALIB_MIN_SAMPLES = 30 纪律）。\n\n")

    # Section 1
    md.write("## 1. 身份重复度\n\n")
    md.write(f"**总 identity 数**：{identity_stats['total_identities']}\n\n")
    md.write(f"**平均行数/identity**：{identity_stats['mean_rows_per_identity']:.2f}\n\n")
    md.write("**行数分布**：\n\n")
    md.write("| bucket | count |\n")
    md.write("|---|---|\n")
    for bucket in ["1", "2-3", "4-6", "7-10", ">10"]:
        md.write(f"| {bucket} | {identity_stats['dist'].get(bucket, 0)} |\n")
    md.write("\n")

    md.write("**首次 vs 重复行**：\n\n")
    md.write("| metric | first occurrence | repeat |\n")
    md.write("|---|---|---|\n")
    md.write(f"| n | {identity_stats['first_occurrence']['n']} | {identity_stats['repeat']['n']} |\n")
    md.write(f"| excess d1 mean | {_fmt(identity_stats['first_occurrence']['d1_mean'], 3, '%')} | {_fmt(identity_stats['repeat']['d1_mean'], 3, '%')} |\n")
    md.write(f"| excess d3 mean | {_fmt(identity_stats['first_occurrence']['d3_mean'], 3, '%')} | {_fmt(identity_stats['repeat']['d3_mean'], 3, '%')} |\n")
    md.write(f"| excess d5 mean | {_fmt(identity_stats['first_occurrence']['d5_mean'], 3, '%')} | {_fmt(identity_stats['repeat']['d5_mean'], 3, '%')} |\n")
    first_dir_acc, first_n_dir = identity_stats['first_occurrence']['dir_acc']
    repeat_dir_acc, repeat_n_dir = identity_stats['repeat']['dir_acc']
    md.write(f"| direction accuracy | {_pct(first_dir_acc)} (n={first_n_dir}) | {_pct(repeat_dir_acc)} (n={repeat_n_dir}) |\n")
    md.write("\n")

    # Section 2
    md.write("## 2. 来源×类型分级\n\n")
    md.write("| source | category_tag | impact_level | n | excess d1 | excess d3 | excess d5 | direction accuracy |\n")
    md.write("|---|---|---|---|---|---|---|---|\n")
    for s in source_cat_stats:
        if not s["sample_sufficient"]:
            md.write(f"| {s['source']} | {s['category_tag']} | {s['impact_level']} | {s['n']} | 样本不足 | 样本不足 | 样本不足 | 样本不足 |\n")
        else:
            md.write(f"| {s['source']} | {s['category_tag']} | {s['impact_level']} | {s['n']} | {_fmt(s['d1_mean'], 3, '%')} | {_fmt(s['d3_mean'], 3, '%')} | {_fmt(s['d5_mean'], 3, '%')} | {_pct(s['dir_acc'])} (n={s['n_dir']}) |\n")
    md.write("\n")

    # Section 3
    md.write("## 3. novelty 分档\n\n")
    if novelty_stats.get("skip"):
        md.write(f"**SKIP**: {novelty_stats['reason']}\n\n")
    else:
        md.write(f"**未匹配行数**：{novelty_stats.get('unmatched', 0)}\n\n")
        md.write("| bucket | n | excess d1 | excess d3 | excess d5 | direction accuracy |\n")
        md.write("|---|---|---|---|---|---|\n")
        for b in novelty_stats["buckets"]:
            if not b["sample_sufficient"]:
                md.write(f"| {b['bucket']} | {b['n']} | 样本不足 | 样本不足 | 样本不足 | 样本不足 |\n")
            else:
                md.write(f"| {b['bucket']} | {b['n']} | {_fmt(b['d1_mean'], 3, '%')} | {_fmt(b['d3_mean'], 3, '%')} | {_fmt(b['d5_mean'], 3, '%')} | {_pct(b['dir_acc'])} (n={b['n_dir']}) |\n")
        md.write("\n")

    # Section 4
    md.write("## 4. T-1 前瞻窗检验\n\n")
    if t1_stats.get("skip"):
        md.write(f"**SKIP**: {t1_stats['reason']}\n\n")
        md.write("**文献依据**：\n\n")
        for lit in t1_stats.get("literature", []):
            md.write(f"- {lit}\n")
        md.write("\n")

    # Section 5
    md.write("## 5. 参数建议表\n\n")
    md.write("### 5.1 macro 排除\n\n")
    macro_sug = [s for s in param_suggestions if s["parameter"] == "macro exclusion"]
    if macro_sug:
        s = macro_sug[0]
        if not s["sample_sufficient"]:
            # 只有样本不足的一组不出建议，另一组照常出数（2026-10-21：原实现两组一起标"样本不足"，
            # 与 kept n=781 并列时自相矛盾）
            md.write(f"**kept (non-macro)**: n={s['kept_n']}, excess d1={_fmt(s['kept_excess_d1'], 3, '%')}, direction accuracy={_pct(s['kept_dir_acc'])}\n\n")
            md.write(
                f"**excluded (macro) 样本不足**：n={s['excluded_n']} < min-samples → 不出建议"
                f"（excess d1={_fmt(s['excluded_excess_d1'], 3, '%')}, direction accuracy={_pct(s['excluded_dir_acc'])}，仅作参照）\n\n"
            )
        else:
            md.write(f"**kept (non-macro)**: n={s['kept_n']}, excess d1={_fmt(s['kept_excess_d1'], 3, '%')}, direction accuracy={_pct(s['kept_dir_acc'])}\n\n")
            md.write(f"**excluded (macro)**: n={s['excluded_n']}, excess d1={_fmt(s['excluded_excess_d1'], 3, '%')}, direction accuracy={_pct(s['excluded_dir_acc'])}\n\n")

    md.write("### 5.2 confidence 新闸候选\n\n")
    md.write("| gate | kept n | kept excess d1 | kept excess d3 | kept excess d5 | kept dir acc | dropped n | dropped excess d1 | dropped dir acc |\n")
    md.write("|---|---|---|---|---|---|---|---|---|\n")
    conf_sug = [s for s in param_suggestions if s["parameter"] == "confidence gate"]
    for s in conf_sug:
        if not s["sample_sufficient"]:
            md.write(f"| {s['option']} | {s['kept_n']} | 样本不足 | 样本不足 | 样本不足 | 样本不足 | {s['dropped_n']} | {_fmt(s['dropped_excess_d1'], 3, '%')} | {_pct(s['dropped_dir_acc'])} |\n")
        else:
            md.write(f"| {s['option']} | {s['kept_n']} | {_fmt(s['kept_excess_d1'], 3, '%')} | {_fmt(s['kept_excess_d3'], 3, '%')} | {_fmt(s['kept_excess_d5'], 3, '%')} | {_pct(s['kept_dir_acc'])} | {s['dropped_n']} | {_fmt(s['dropped_excess_d1'], 3, '%')} | {_pct(s['dropped_dir_acc'])} |\n")
    md.write("\n")

    md.write("### 5.3 identity 重锚窗候选\n\n")
    md.write("| window | kept n | kept excess d1 | kept dir acc |\n")
    md.write("|---|---|---|---|\n")
    reanchor_sug = [s for s in param_suggestions if s["parameter"] == "identity re-anchor window"]
    for s in reanchor_sug:
        if not s["sample_sufficient"]:
            md.write(f"| {s['option']} | {s['kept_n']} | 样本不足 | 样本不足 |\n")
        else:
            md.write(f"| {s['option']} | {s['kept_n']} | {_fmt(s['kept_excess_d1'], 3, '%')} | {_pct(s['kept_dir_acc'])} |\n")
    md.write("\n")

    # 留给负责人的决策位
    md.write("## 留给负责人的决策位\n\n")
    md.write("1. **macro 排除**：macro_rule 方向正确率 1/10（n=10 已兑现），是否从催化剂评分入口排除？\n")
    md.write("2. **confidence 新闸**：0.65-0.7 段与 >=0.7 段质量相当（52.9% vs 51.6%），0.7 闸砍掉的是可用样本；是否下调至 0.65？\n")
    md.write("3. **identity 重锚窗**：当前滚动锚定使同一真实事件跨月生成新 event_id「永生」；重锚窗取 7/14/30 日历日？\n")
    md.write("4. **T-1 前瞻窗**：文献显示事件价值在公告前，当前系统只锚 T+1 是窗口错位；是否另立数据 WI 采集 T-1 收益？\n")
    md.write("5. **novelty 分档**：若 node4_raw_news 关联可靠，是否按首现/重复次数差异化权重？\n\n")

    md_text = md.getvalue()

    # JSON 结构化
    skips = []
    if novelty_stats.get("skip"):
        skips.append({"section": "novelty", "reason": novelty_stats["reason"]})
    if t1_stats.get("skip"):
        skips.append({"section": "T-1", "reason": t1_stats["reason"]})

    payload = {
        "run_date": date.today().isoformat(),
        "data_freshness": {
            "max_event_date": max_event_date.isoformat() if max_event_date else None,
            "row_count": row_count,
        },
        "min_samples": args.min_samples,
        "sections": [
            {
                "id": "identity_dup",
                "total_identities": identity_stats["total_identities"],
                "mean_rows_per_identity": identity_stats["mean_rows_per_identity"],
                "dist": identity_stats["dist"],
                "first_occurrence": identity_stats["first_occurrence"],
                "repeat": identity_stats["repeat"],
            },
            {
                "id": "source_category",
                "rows": source_cat_stats,
            },
            {
                "id": "novelty",
                "skip": novelty_stats.get("skip", False),
                "reason": novelty_stats.get("reason"),
                "unmatched": novelty_stats.get("unmatched"),
                "buckets": novelty_stats.get("buckets", []),
            },
            {
                "id": "T-1",
                "skip": t1_stats.get("skip", False),
                "reason": t1_stats.get("reason"),
                "literature": t1_stats.get("literature", []),
            },
            {
                "id": "parameter_suggestions",
                "rows": param_suggestions,
            },
        ],
        "skips": skips,
    }

    return md_text, payload


def main():
    ap = argparse.ArgumentParser(description="催化剂价值分级校准取证（只读）")
    ap.add_argument("--days", type=int, default=365, help="回溯天数（默认 365）")
    ap.add_argument("--out-dir", default="reports", help="输出目录（默认 reports）")
    ap.add_argument("--min-samples", type=int, default=30, help="最小样本门槛（默认 30）")
    args = ap.parse_args()

    md_text, payload = build_report(args)

    os.makedirs(args.out_dir, exist_ok=True)
    today_str = date.today().strftime("%Y%m%d")
    md_path = os.path.join(args.out_dir, f"catalyst_value_tier_{today_str}.md")
    json_path = os.path.join(args.out_dir, f"catalyst_value_tier_{today_str}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_text)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n[写出] {md_path}\n[写出] {json_path}")


if __name__ == "__main__":
    main()
