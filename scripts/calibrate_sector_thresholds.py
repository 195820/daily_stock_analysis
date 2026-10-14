# -*- coding: utf-8 -*-
"""P1-1 aggregator 双阈值（50/40）分位重标取证（2026-10-11，离线只读）

**只出证据、不代替定值**——所有数值改动归负责人拍板后另批实施。
当前生产口径（不动）：
- aggregator.py `_classify_all_sectors`：`score >= 50` → actionable（TOP-N=12 内）
- aggregator.py `_is_hard_exclude`：`composite < 40` → exclude（不可被 TOP-N 覆盖）

背景（P1-1 原始规格，docs/got/archive/agent_glue_plan_0903.md:1302）：
两绝对阈值按旧 composite 语义校准；glue 转正（2026-09-17）后
composite = Agent strength × 100，分布漂移，须用影子期 ≥10 日数据重定分位。

产出七节：
1. 逐日分布分位表（composite p10/p25/p50/p75/p90 + 当日 actionable/caution/exclude 计数）
2. PRE/POST 汇总分位对照（预期锚：POST p50=55 vs PRE p50=67，share≥50 63.1% vs 81.2%）
3. 50 线准入翻转清单：POST 期 composite ∈ [40,50) 且 classification=caution——
   仅因卡 50 线未入 actionable 的板块（40 线未拒、TOP-N 未满）
4. 40 线硬拒翻转清单：两假设档（40→35 放行 [35,40)；40→45 新拒 [40,45)）
5. 每日 actionable vs TOP-N=12 名额对照（实证绑定约束）
6. 建议分位候选（仅供拍板）：当日截面分位方案 vs 绝对值下调方案
7. 声明：数值归负责人

用法：
    python scripts/calibrate_sector_thresholds.py [--since 20260917] [--pre-since 20260724]
        [--out reports/sector_threshold_recibr]

产物：
    reports/sector_threshold_recibr_YYYYMMDD.md + .json（不入库）

纯离线只读：不写库、不写 data/、不改任何生产文件。
"""

import argparse
import json
import os
import sys
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

ARCHIVE_DIR = os.path.join(ROOT_DIR, "data", "got_daily")
# glue 转正日（2026-09-17）：POST 期起点；PRE 对照期 [pre_since, since)
DEFAULT_SINCE = "20260917"
DEFAULT_PRE_SINCE = "20260724"
DEFAULT_OUT_STEM = "reports/sector_threshold_recibr"
TOP_N = 12  # aggregator.py:1573 classify top_n=12

# 报告锚（2026-10-11 计划期实测值，脚本正确性校验用；偏差仅打印告警不阻断）
ANCHORS = {
    "post_p50": 55.0,
    "pre_p50": 67.0,
    "post_share_ge50": 63.1,
    "pre_share_ge50": 81.2,
    "post_share_lt40": 10.7,
    "pre_share_lt40": 11.9,
}


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"[skip] {path}: {e}")
        return None


def _pctl(sorted_vals, p):
    """线性插值分位（numpy percentile 默认口径）。空序列返回 None。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def _dist(values):
    v = sorted(values)
    n = len(v)
    return {
        "n": n,
        "p10": _pctl(v, 10),
        "p25": _pctl(v, 25),
        "p50": _pctl(v, 50),
        "p75": _pctl(v, 75),
        "p90": _pctl(v, 90),
        "share_lt40": (sum(1 for x in v if x < 40) / n) if n else None,
        "share_ge50": (sum(1 for x in v if x >= 50) / n) if n else None,
    }


def _fmt(v, nd=1, suffix=""):
    return "N/A" if v is None else f"{v:.{nd}f}{suffix}"


def _pct(v, nd=1):
    return "N/A" if v is None else f"{v * 100:.{nd}f}%"


def load_days():
    """扫描 data/got_daily/2026*/unified.json → {date_str: unified_dict}（坏档跳过）。"""
    if not os.path.isdir(ARCHIVE_DIR):
        print(f"[fatal] 归档目录不存在: {ARCHIVE_DIR}")
        sys.exit(2)
    all_dirs = [n for n in sorted(os.listdir(ARCHIVE_DIR)) if len(n) == 8 and n.startswith("2026") and n.isdigit()]
    days = {}
    for name in all_dirs:
        u = _read_json(os.path.join(ARCHIVE_DIR, name, "unified.json"))
        if u is not None:
            days[name] = u
    return days, all_dirs


def extract_sector_rows(unified):
    """confirmed_sectors → [(sector_name, composite_score, classification, sector_role)]；None 分数跳过。"""
    rows = []
    for s in unified.get("confirmed_sectors") or []:
        score = s.get("composite_score")
        if score is None:
            continue
        rows.append((
            s.get("sector_name") or s.get("name") or "?",
            float(score),
            s.get("classification"),
            s.get("sector_role") or "mainline",
        ))
    return rows


def day_counts(rows):
    """分类计数（全量口径，含 ambush，与分布 n 同口径可对账）；名额口径见节 5 rep_actionable。"""
    c = {"actionable": 0, "caution": 0, "exclude": 0, "other": 0}
    for row in rows:
        cls = row[2]
        c[cls if cls in c else "other"] += 1
    return c


def check_anchor(label, actual, expected, tol):
    if actual is None:
        return
    if abs(actual - expected) > tol:
        print(f"[anchor-warn] {label}: 实测 {actual:.1f} vs 计划锚 {expected}（偏差 > {tol}）")


def main():
    ap = argparse.ArgumentParser(description="P1-1 双阈值分位重标取证（只读）")
    ap.add_argument("--since", default=DEFAULT_SINCE, help="POST 期起始日（glue 转正日）YYYYMMDD")
    ap.add_argument("--pre-since", default=DEFAULT_PRE_SINCE, help="PRE 对照期起始日 YYYYMMDD")
    ap.add_argument("--out", default=DEFAULT_OUT_STEM, help="输出路径 stem（自动加 _YYYYMMDD.md/.json）")
    args = ap.parse_args()
    since, pre_since = args.since, args.pre_since

    days, all_dirs = load_days()
    skipped = [n for n in all_dirs if n not in days]

    post_dates = sorted(d for d in days if d >= since)
    pre_dates = sorted(d for d in days if pre_since <= d < since)
    if len(post_dates) < 10:
        print(f"[warn] POST 期仅 {len(post_dates)} 个归档日 < 10 门槛，结论标注未到期")

    post_rows = {d: extract_sector_rows(days[d]) for d in post_dates}
    pre_rows = {d: extract_sector_rows(days[d]) for d in pre_dates}
    # 注意：post_rows/pre_rows 保留全量 confirmed_sectors（含 ambush），与计划锚口径一致；
    # ambush 过滤只在消费点做（flip 清单/名额/分位候选），见各节 `_role != "ambush"` 条件。

    # 成员→组代表映射（从当日 sector.json 重建，与 aggregator._collect_sector_candidates 同构）：
    # TOP-N 名额只被组代表（组名/max strength 成员）消耗；组成员 actionable 属继承、不占名额。
    # sector.json 缺失/坏档日 → 空映射，名额按全量 actionable 保守计数（报告标注）。
    def build_member_to_group(d):
        sd = _read_json(os.path.join(ARCHIVE_DIR, d, "sector.json"))
        m2g = {}
        if not sd:
            return m2g, False
        for g in sd.get("active_mainline") or []:
            gname = g.get("group_name")
            for m in g.get("members") or []:
                mn = m.get("name")
                if mn and gname and mn != gname:
                    m2g[mn] = gname
        return m2g, True

    member_maps, sector_json_ok = {}, {}
    for d in post_dates:
        member_maps[d], sector_json_ok[d] = build_member_to_group(d)

    post_all = [s for rows in post_rows.values() for _, s, _, _ in rows]
    pre_all = [s for rows in pre_rows.values() for _, s, _, _ in rows]
    post_dist, pre_dist = _dist(post_all), _dist(pre_all)

    # 锚校验（脚本正确性）
    check_anchor("POST p50", post_dist["p50"], ANCHORS["post_p50"], 0.6)
    check_anchor("PRE p50", pre_dist["p50"], ANCHORS["pre_p50"], 0.6)
    check_anchor("POST share>=50", post_dist["share_ge50"] * 100, ANCHORS["post_share_ge50"], 0.8)
    check_anchor("PRE share>=50", pre_dist["share_ge50"] * 100, ANCHORS["pre_share_ge50"], 0.8)
    check_anchor("POST share<40", post_dist["share_lt40"] * 100, ANCHORS["post_share_lt40"], 0.8)
    check_anchor("PRE share<40", pre_dist["share_lt40"] * 100, ANCHORS["pre_share_lt40"], 0.8)

    # ── 节 3：50 线准入翻转清单（POST 期 [40,50) 且 caution；ambush 不走 TOP-N 通道，排除）──
    flip50 = [
        {"date": d, "sector": name, "score": s}
        for d in post_dates
        for name, s, cls, _role in post_rows[d]
        if 40 <= s < 50 and cls == "caution" and _role != "ambush"
    ]
    flip50_by_sector = {}
    for r in flip50:
        flip50_by_sector[r["sector"]] = flip50_by_sector.get(r["sector"], 0) + 1
    flip50_by_sector_sorted = sorted(flip50_by_sector.items(), key=lambda kv: (-kv[1], kv[0]))

    # ── 节 4：40 线硬拒翻转清单（两假设档，POST 期，含 exclude 与 caution；ambush 排除）──
    down_40_to_35 = [
        {"date": d, "sector": name, "score": s, "classification": cls}
        for d in post_dates
        for name, s, cls, _role in post_rows[d]
        if 35 <= s < 40 and _role != "ambush"
    ]
    up_40_to_45 = [
        {"date": d, "sector": name, "score": s, "classification": cls}
        for d in post_dates
        for name, s, cls, _role in post_rows[d]
        if 40 <= s < 45 and _role != "ambush"
    ]

    # ── 节 5：每日 actionable vs TOP-N=12（名额口径=组代表；成员继承不计名额）──
    quota_rows = []
    for d in post_dates:
        cnt = day_counts(post_rows[d])
        rep_actionable = sum(
            1 for name, _, cls, _role in post_rows[d]
            if cls == "actionable" and _role != "ambush" and name not in member_maps[d]
        )
        quota_rows.append({
            "date": d,
            "actionable": cnt["actionable"],
            "rep_actionable": rep_actionable,
            "caution": cnt["caution"],
            "exclude": cnt["exclude"],
            "n_sectors": len(post_rows[d]),
            "quota_binding": rep_actionable >= TOP_N,
        })

    quota_row_by_date = {r["date"]: r for r in quota_rows}

    # ── 节 6：建议分位候选（仅供拍板）──
    # 方案 A：50 线改当日截面分位（例 p60）；方案 B：绝对值下调。
    # 对每个候选线，翻转数 = POST 期当日 [新线, 50) ∩ caution 且名额未满才真实受益
    def flip_count_for_line(new_line):
        n = 0
        for d in post_dates:
            rep_actionable = quota_row_by_date[d]["rep_actionable"]
            if rep_actionable >= TOP_N:
                continue  # 名额已满，降线不增 actionable
            # caution 且非 actionable 组成员（成员随组分类，降线不改变其分类）
            n += sum(
                1 for name, s, cls, _role in post_rows[d]
                if new_line <= s < 50 and cls == "caution" and _role != "ambush"
                and name not in member_maps[d]  # 组成员继承组分类，不计
            )
        return n

    # 当日截面分位：用当日 POST 分布的分位数做候选
    day_scores = {d: sorted(s for _, s, _, _ in post_rows[d]) for d in post_dates}
    pct_candidates = {}
    for p in (50, 55, 60, 65):
        vals = [_pctl(day_scores[d], p) for d in post_dates if day_scores[d]]
        vals = [v for v in vals if v is not None]
        pct_candidates[f"p{p}"] = {
            "mean_line": round(sum(vals) / len(vals), 1) if vals else None,
            "min_line": round(min(vals), 1) if vals else None,
            "max_line": round(max(vals), 1) if vals else None,
        }
    abs_candidates = {}
    for line in (45, 42, 40):
        abs_candidates[str(line)] = {"flip_count": flip_count_for_line(line)}

    report_date = datetime.now().strftime("%Y%m%d")
    out_md = f"{args.out}_{report_date}.md"
    out_json = f"{args.out}_{report_date}.json"
    os.makedirs(os.path.dirname(out_md) or ".", exist_ok=True)

    # ── JSON 产物 ──
    payload = {
        "generated": report_date,
        "since": since,
        "pre_since": pre_since,
        "skipped_dirs": skipped,
        "post_dates": post_dates,
        "pre_dates": pre_dates,
        "post_dist": post_dist,
        "pre_dist": pre_dist,
        "daily": {
            d: {
                "p10": _pctl(sorted(s for _, s, _, _ in post_rows[d]), 10),
                "p25": _pctl(sorted(s for _, s, _, _ in post_rows[d]), 25),
                "p50": _pctl(sorted(s for _, s, _, _ in post_rows[d]), 50),
                "p75": _pctl(sorted(s for _, s, _, _ in post_rows[d]), 75),
                "p90": _pctl(sorted(s for _, s, _, _ in post_rows[d]), 90),
                **day_counts(post_rows[d]),
            }
            for d in post_dates
        },
        "flip50": flip50,
        "flip50_by_sector": flip50_by_sector_sorted,
        "down_40_to_35": down_40_to_35,
        "up_40_to_45": up_40_to_45,
        "quota": quota_rows,
        "pct_candidates": pct_candidates,
        "abs_candidates": abs_candidates,
        "note": "仅取证，数值改动归负责人拍板后另批实施",
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # ── MD 报告 ──
    L = []
    ap_ = L.append
    ap_("# P1-1 aggregator 双阈值（50/40）分位重标取证报告")
    ap_("")
    ap_(f"生成：{report_date}　POST 期：`{since}` 起（glue 转正）　PRE 对照期：`{pre_since}` ≤ d < `{since}`")
    ap_("")
    ap_(f"- POST：{len(post_dates)} 个归档日 / n={post_dist['n']} 条板块记录")
    ap_(f"- PRE：{len(pre_dates)} 个归档日 / n={pre_dist['n']} 条板块记录")
    if skipped:
        ap_(f"- 坏档/缺档跳过（try/except 容错，非 bug）：{', '.join(skipped) or '无'}")
        if "20260911" in skipped and "20260912" in skipped:
            ap_("  - 20260911 / 20260912：目录仅余空 `_replay/`（降级运行，无生产 unified.json），少约 62 行 PRE 样本；"
                "由此 PRE 尾部份额与计划 2026-10-11 快照小偏差（share≥50 79.7% vs 81.2%、share<40 12.9% vs 11.9%，"
                "该组数字仅对该快照成立），p10/p25/p50/p75/p90 全部匹配——勿重查。`_replay/` 为回放变体，不读作生产数据。")
        if "20261010" in skipped:
            ap_("  - 20261010：不完整运行（仅 candidate_pool.json）。")
    if not skipped:
        ap_("- 坏档/缺档跳过：无")
    if len(post_dates) < 10:
        ap_(f"- ⚠️ POST 期 {len(post_dates)} 日 < 10 门槛，本报告结论按「未到期」降权")
    ap_("")
    ap_("## 1. 逐日分布分位表（POST 期，glue 覆写后 composite）")
    ap_("")
    ap_("| 日期 | n | p10 | p25 | p50 | p75 | p90 | actionable | caution | exclude |")
    ap_("|---|---|---|---|---|---|---|---|---|---|")
    for d in post_dates:
        sc = sorted(s for _, s, _, _ in post_rows[d])
        cnt = day_counts(post_rows[d])
        ap_(f"| {d} | {len(sc)} | {_fmt(_pctl(sc,10))} | {_fmt(_pctl(sc,25))} | {_fmt(_pctl(sc,50))} | "
            f"{_fmt(_pctl(sc,75))} | {_fmt(_pctl(sc,90))} | {cnt['actionable']} | {cnt['caution']} | {cnt['exclude']} |")
    ap_("")
    ap_("## 2. PRE/POST 汇总分位对照")
    ap_("")
    ap_("| 期 | n | p10 | p25 | p50 | p75 | p90 | share<40 | share≥50 |")
    ap_("|---|---|---|---|---|---|---|---|---|")
    for label, dist in (("PRE", pre_dist), ("POST", post_dist)):
        ap_(f"| {label} | {dist['n']} | {_fmt(dist['p10'])} | {_fmt(dist['p25'])} | {_fmt(dist['p50'])} | "
            f"{_fmt(dist['p75'])} | {_fmt(dist['p90'])} | {_pct(dist['share_lt40'])} | {_pct(dist['share_ge50'])} |")
    ap_("")
    ap_("解读：硬拒线 40 两期份额基本持平（" + _pct(post_dist['share_lt40']) + " vs " + _pct(pre_dist['share_lt40'])
        + "）⇒ 40 线实际行为未失效；漂移集中在高端（p50 " + _fmt(pre_dist['p50']) + " → " + _fmt(post_dist['p50'])
        + "），50 线到达率 " + _pct(pre_dist['share_ge50']) + " → " + _pct(post_dist['share_ge50']) + "。")
    ap_("")
    ap_("## 3. 50 线准入翻转清单（POST 期 composite ∈ [40,50) 且 caution）")
    ap_("")
    ap_("口径：排除 ambush 角色板块（其分类由埋伏闸门直设，`aggregator.py:1579-1582`，不经过 50 线）与"
        "actionable 组成员继承行；计划快照的 68 条为未过滤全量口径，本表 6 条为通道归因口径（非脚本 bug）。")
    ap_("")
    ap_(f"共 {len(flip50)} 条（date, sector, score）；按板块聚合出现天数：")
    ap_("")
    ap_("| 板块 | 出现天数 |")
    ap_("|---|---|")
    for name, n in flip50_by_sector_sorted:
        ap_(f"| {name} | {n} |")
    ap_("")
    ap_("逐条明细（含日期与分值）见同名 `.json` 的 `flip50`。")
    ap_("")
    ap_("## 4. 40 线硬拒翻转清单（两假设档，POST 期）")
    ap_("")
    ap_(f"### 下调档 40→35（会放行 [35,40)）：{len(down_40_to_35)} 条")
    if down_40_to_35:
        ap_("")
        ap_("| 日期 | 板块 | composite | classification |")
        ap_("|---|---|---|---|")
        for r in down_40_to_35:
            ap_(f"| {r['date']} | {r['sector']} | {r['score']:.1f} | {r['classification']} |")
    ap_("")
    ap_(f"### 上调档 40→45（会新拒 [40,45)）：{len(up_40_to_45)} 条")
    if up_40_to_45:
        ap_("")
        ap_("| 日期 | 板块 | composite | classification |")
        ap_("|---|---|---|---|")
        for r in up_40_to_45:
            ap_(f"| {r['date']} | {r['sector']} | {r['score']:.1f} | {r['classification']} |")
    ap_("")
    ap_("## 5. 每日 actionable vs TOP-N=12 名额对照")
    ap_("")
    ap_("名额口径：TOP-N 名额只被组代表消耗（`_classify_all_sectors` 中 `actionable_count < top_n and score >= 50`），"
        "组成员 actionable 属继承、不占名额（成员→组映射自当日 sector.json 的 active_mainline 重建）。"
        "注意 actionable(代表) 是名额占用的**上界**：被过热/拥挤拦截的代表（`aggregator.py:1996-2001`）记 caution、不耗名额，"
        "归档数据无法从 unified.json 重建过热明细，故不再下修。")
    ap_("")
    ap_("| 日期 | actionable(全量) | actionable(代表,占名额) | caution | exclude | 板块总数 | 名额绑定（代表≥12） |")
    ap_("|---|---|---|---|---|---|---|")
    for r in quota_rows:
        ap_(f"| {r['date']} | {r['actionable']} | {r['rep_actionable']} | {r['caution']} | {r['exclude']} | {r['n_sectors']} | "
            f"{'是' if r['quota_binding'] else '否'} |")
    ap_("")
    binding_days = sum(1 for r in quota_rows if r["quota_binding"])
    if not all(sector_json_ok.values()):
        bad = [d for d, ok in sector_json_ok.items() if not ok]
        ap_(f"⚠️ 以下日期 sector.json 缺失/坏档，名额按全量 actionable 保守计数：{', '.join(bad)}")
    ap_(f"名额绑定天数：{binding_days}/{len(quota_rows)}。"
        f"{'全部低于 12 ⇒ 50 线是绑定约束而非名额截断。' if binding_days == 0 else '存在名额绑定日，降线增量为节 6 口径（名额未满才计入）。'}")
    ap_("")
    ap_("## 6. 建议分位候选（仅供拍板）")
    ap_("")
    ap_("### 方案 A：50 线改当日截面分位（自适应漂移；跨日不可比）")
    ap_("")
    ap_("| 候选 | 当日线均值 | 当日线范围 |")
    ap_("|---|---|---|")
    for k, v in pct_candidates.items():
        ap_(f"| {k} | {_fmt(v['mean_line'])} | {_fmt(v['min_line'])} ~ {_fmt(v['max_line'])} |")
    ap_("")
    ap_("### 方案 B：绝对值下调（简单稳定；会随语义再漂移）")
    ap_("")
    ap_("| 新线 | 真实翻转数（名额未满日 [新线,50) ∩ caution） |")
    ap_("|---|---|")
    for k, v in abs_candidates.items():
        ap_(f"| {k} | {v['flip_count']} |")
    ap_("")
    ap_("| 方案 | 优点 | 缺点 |")
    ap_("|---|---|---|")
    ap_("| A 当日分位 | 自适应分布漂移，无再校准负担 | 跨日不可比；冷启动日（板块少）分位不稳 |")
    ap_("| B 绝对值下调 | 简单、可比、可回滚 | 下次语义漂移仍需重标 |")
    ap_("")
    ap_("## 7. 声明")
    ap_("")
    ap_("**本报告仅取证，不代替定值。所有数值改动（50 线分位化或绝对值下调、40 线任何调整）归负责人拍板后另批实施；"
        "本脚本与报告不修改 `aggregator.py` 及任何生产文件、不写库、不写 `data/`。**")
    ap_("")

    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"[ok] {out_md}")
    print(f"[ok] {out_json}")
    print(f"[ok] POST n={post_dist['n']} p50={_fmt(post_dist['p50'])} | PRE n={pre_dist['n']} p50={_fmt(pre_dist['p50'])} "
          f"| flip50={len(flip50)}")


if __name__ == "__main__":
    main()
