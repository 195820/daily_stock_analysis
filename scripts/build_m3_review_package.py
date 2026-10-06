#!/usr/bin/env python
"""OPEN-C2 · M3 人工终审评审包（glue 家族归属误配率），离线只读诊断脚本。

规格出处：
- 【glue 计划】§8.1/§8.2（`docs/got/archive/agent_glue_plan_0903.md`）：M3＝误配率
  「人工抽查 glue 归属中『无实质归属关系』的占比」，红线 **≤15%**，转正需**连续 5 日达标**，
  样本口径原文是「每日候选全量，≤10 分钟/日」，且明确写了 **M3 无法自动化**（ground truth
  是市场共识判断，没有可查询的真值表）。
- 【C 层计划】§7.2 **OPEN-C2**（`docs/got/archive/got_layer_c_plan_0911.md`）：原文只写了
  时长、没有流程定义；建议默认口径 =「分层抽 10 个（『其他』回收 5 + 冲突 3 + 随机 2），
  把判据写成三档（无关系 / 弱关系 / 强关系），只把『无关系』计入」。
- 现行登记：`docs/got/verification.md` §6（M3 / OPEN-C2 两行）、`docs/got/backlog.md` §3.3。

本脚本把「怎么抽 / 判据是什么 / 谁判 / 怎么计分」固化成可复现流程，产出**待填**评审包
（md 人读表 + csv 回填模板 + json 抽样留痕），**不代判、不改生产代码、不触网、不写 DB**；
判定一律归负责人。

口径可比性（重要，别自欺）：
- `--sample-mode stratified`（缺省，OPEN-C2 建议口径）对「回收/冲突」两类**对抗性超采**，
  其误配率是偏高的初筛读数，**不能**直接当 §8.2 的转正依据；
- `--sample-mode full`（§8.1 原文「每日候选全量」）才是转正口径；分层初筛只作日常预警。
  计分结果会写明本次是哪一口径。

抽样与类别判定全部复用既有审核工具 `scripts/export_glue_family_audit.py`
（`discover_days` / `load_day` / `classify` / `flag_cross_day_instability`），不另写第二套口径；
置信区间复用 `scripts/calibrate_optimal_stop.py::wilson_interval`。

用法：
    python scripts/build_m3_review_package.py                     # 最近 5 个有效归档日，分层抽 10/日
    python scripts/build_m3_review_package.py --days 20260928 20260929
    python scripts/build_m3_review_package.py --sample-mode full  # §8.1 转正口径
    python scripts/build_m3_review_package.py --score data/glue_family_audit/m3_review_20261006.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))

import export_glue_family_audit as audit_tool  # noqa: E402  复用审核工具的行装载与类别判定

OUTPUT_DIR = audit_tool.OUTPUT_DIR          # data/glue_family_audit（已 gitignore）
RED_LINE_PCT = 15.0                         # 【glue 计划】§8.1 M3 红线
STREAK_DAYS_REQUIRED = 5                    # §8.2「连续 5 日达标」

# OPEN-C2 三档判据 + 一档「无法判定」（不参与计分，只披露，防把「没依据」算成「没错」）
GRADE_STRONG = "强关系"
GRADE_WEAK = "弱关系"
GRADE_NONE = "无关系"
GRADE_UNKNOWN = "无法判定"
GRADES = (GRADE_STRONG, GRADE_WEAK, GRADE_NONE, GRADE_UNKNOWN)
# 与审核工具 HTML 页的 对/错/存疑 三键位对齐，两套判读可互认
GRADE_TO_HTML_BTN = {GRADE_STRONG: "ok", GRADE_NONE: "bad", GRADE_WEAK: "unsure", GRADE_UNKNOWN: "unsure"}

STRATA = (
    # (层名, 抽取数默认, 类别池, 语义)
    ("recovered", 5, ("recovered",), "『其他』回收：规则侧落『其他』、glue 却给了家族 → 最可能是幻觉"),
    ("overridden", 3, ("overridden",), "冲突：规则侧与 glue 给了不同家族 → 最可能是错判"),
    ("random", 2, ("same",), "随机对照：两侧一致，防止只审难点导致读数偏高"),
)

# 【glue 计划】§8.1：M3 的分母是「glue 给出了归属」的行。弃权（glue 给『无归属』）与
# 双空不入分母（它们没有「归属」可判误配），另作为「glue 弃权率」披露。
NON_M3_CATEGORIES = ("dropped", "both_none")

CSV_COLUMNS = [
    "idx", "day", "code", "name", "category", "rule_family", "glue_family",
    "glue_strength", "glue_confidence", "grade", "note",
]


# ─────────────────────────────────────────────────────────────
# 1) 装载（复用审核工具）
# ─────────────────────────────────────────────────────────────

def resolve_days(days: Optional[list[str]], recent: int, variant: str = "") -> list[str]:
    """显式日优先；否则取最近 `recent` 个「同时有 unified + glue + news」的归档日。"""
    found = audit_tool.discover_days(sorted(days) if days else None, variant)
    if not found:
        return []
    return found if days else found[-recent:]


def load_rows(days: list[str], variant: str = "") -> list[dict]:
    rows: list[dict] = []
    for day in days:
        rows.extend(audit_tool.load_day(day, variant))
    audit_tool.flag_cross_day_instability(rows)   # 同板跨日不同家族 → 打标（人工判读要看）
    return rows


# ─────────────────────────────────────────────────────────────
# 2) 抽样
# ─────────────────────────────────────────────────────────────

def stratify(rows: list[dict], n_recovered: int, n_overridden: int, n_random: int,
             seed: int) -> dict[str, Any]:
    """OPEN-C2 分层抽样：回收 5 + 冲突 3 + 随机 2 = 10/日。

    三条硬约束：
    1. 确定性（同 seed 同归档 → 同一批样本，可复核、可回填续判）；
    2. **池不足时如实记缺口，绝不跨层补齐**（拿「一致」样本填「冲突」的空缺会把读数稀释）；
    3. 按日分层（每日一轮抽查，逐日计分），日内池先按 (day, code) 排序再抽样。
    """
    picked: list[dict] = []
    pools: dict[str, int] = {}
    shortfalls: dict[str, dict[str, int]] = {}
    asked = {"recovered": n_recovered, "overridden": n_overridden, "random": n_random}

    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)

    for day in sorted(by_day):
        day_rows = sorted(by_day[day], key=lambda r: (str(r.get("code")), str(r.get("name"))))
        rng = random.Random(f"{seed}|{day}")          # 逐日独立子流：加一天不影响其他日样本
        pools[f"{day}·unattributed"] = sum(
            1 for r in day_rows if r["category"] in NON_M3_CATEGORIES
        )
        for name, _default, cats, _desc in STRATA:
            pool = [r for r in day_rows if r["category"] in cats]
            k = min(asked[name], len(pool))
            take = rng.sample(pool, k) if k else []
            picked.extend(take)
            pools[f"{day}·{name}"] = len(pool)
            if len(pool) < asked[name]:
                shortfalls[f"{day}·{name}"] = {
                    "asked": asked[name], "available": len(pool), "taken": k,
                }
    picked.sort(key=lambda r: (r["day"], str(r.get("code"))))
    return {"picked": picked, "pools": pools, "shortfalls": shortfalls, "asked": asked}


def pick_full(rows: list[dict]) -> dict[str, Any]:
    """§8.1 转正口径：当日候选全量（不做分层超采，读数无偏但耗时更长）。"""
    return {"picked": sorted(rows, key=lambda r: (r["day"], str(r.get("code")))),
            "pools": {}, "shortfalls": {}, "asked": {}}


# ─────────────────────────────────────────────────────────────
# 3) 判据与计分
# ─────────────────────────────────────────────────────────────

def normalize_grade(value: Any) -> Optional[str]:
    """回填表里的判定列归一：容错简写，未知值 → None（计入「未判定」而不是蒙对）。"""
    s = str(value or "").strip()
    if not s:
        return None
    for g in GRADES:
        if s == g:
            return g
    alias = {
        "强": GRADE_STRONG, "真实": GRADE_STRONG, "对": GRADE_STRONG, "ok": GRADE_STRONG,
        "弱": GRADE_WEAK, "牵强": GRADE_WEAK, "存疑": GRADE_WEAK,
        "无": GRADE_NONE, "错": GRADE_NONE, "bad": GRADE_NONE, "无关": GRADE_NONE,
        "无法判": GRADE_UNKNOWN, "不确定": GRADE_UNKNOWN,
    }
    return alias.get(s)


def score_records(records: list[dict], red_line_pct: float = RED_LINE_PCT) -> dict[str, Any]:
    """按日计分：误配率＝「无关系」/（强+弱+无）。

    - 「无法判定」与空值**既不计分子也不计分母**，单独披露（记 0 会假装达标）
    - 类别为 `dropped`/`both_none`（glue 未给归属）的行不入 M3 分母（§8.1 定义），
      即使被填了判定也归到 `excluded_unattributed` 计数
    - 「弱关系」不计分子（OPEN-C2 原文），但必须披露占比 —— 弱关系堆量是 prompt 收紧信号
    - 逐日判 `mis<=red_line`；`streak` 取**末尾连续**达标天数（§8.2 语义）；
      当日一条都没判 = 「无数据日」，既不算达标也不算破线，直接从连续序列里排除并披露
    """
    from collections import Counter

    by_day: dict[str, list[dict]] = {}
    for r in records:
        by_day.setdefault(str(r.get("day")), []).append(r)

    def in_denominator(r: dict) -> bool:
        return str(r.get("category") or "") not in NON_M3_CATEGORIES

    days_out: list[dict] = []
    for day in sorted(by_day):
        c = Counter()
        day_rows = by_day[day]
        for r in day_rows:
            if not in_denominator(r):
                c["非分母"] += 1
                continue
            g = normalize_grade(r.get("grade"))
            c[g or "未判定"] += 1
        judged = c[GRADE_STRONG] + c[GRADE_WEAK] + c[GRADE_NONE]
        mis = c[GRADE_NONE] / judged * 100 if judged else None
        days_out.append({
            "day": day,
            "sampled": len(day_rows),
            "excluded_unattributed": c["非分母"],
            "judged": judged,
            "unjudged": c["未判定"],
            "undecidable": c[GRADE_UNKNOWN],
            GRADE_STRONG: c[GRADE_STRONG],
            GRADE_WEAK: c[GRADE_WEAK],
            GRADE_NONE: c[GRADE_NONE],
            "misfire_pct": round(mis, 1) if mis is not None else None,
            "pass": (mis is not None and mis <= red_line_pct),
        })

    total = Counter()
    for d in days_out:
        for k in (GRADE_STRONG, GRADE_WEAK, GRADE_NONE, "judged", "unjudged",
                  "undecidable", "excluded_unattributed"):
            total[k] += d[k]
    pooled_pct = total[GRADE_NONE] / total["judged"] * 100 if total["judged"] else None
    lo, hi = None, None
    if total["judged"]:
        from calibrate_optimal_stop import wilson_interval
        lo, hi = wilson_interval(total[GRADE_NONE], total["judged"])

    streak = 0
    seq = [d for d in days_out if d["judged"] > 0]      # 未判满的日不进入达标/破线判定
    for d in reversed(seq):
        if not d["pass"]:
            break
        streak += 1
    longest = best = 0
    for d in seq:
        best = best + 1 if d["pass"] else 0
        longest = max(longest, best)

    return {
        "red_line_pct": red_line_pct,
        "days": days_out,
        "pooled": {
            "judged": total["judged"],
            "unjudged": total["unjudged"],
            "undecidable": total["undecidable"],
            "excluded_unattributed": total["excluded_unattributed"],
            GRADE_STRONG: total[GRADE_STRONG],
            GRADE_WEAK: total[GRADE_WEAK],
            GRADE_NONE: total[GRADE_NONE],
            "misfire_pct": round(pooled_pct, 1) if pooled_pct is not None else None,
            "wilson95": [round(lo, 4), round(hi, 4)] if lo is not None else None,
        },
        "streak_days_met": streak,
        "longest_streak_days": longest,
        "days_required": STREAK_DAYS_REQUIRED,
        "days_with_data": len(seq),
        "days_without_data": len(days_out) - len(seq),
        "status": ("met" if streak >= STREAK_DAYS_REQUIRED else
                   "not_met" if any(not d["pass"] for d in seq) else
                   "pending_more_days"),
    }


# ─────────────────────────────────────────────────────────────
# 4) 评审包渲染
# ─────────────────────────────────────────────────────────────

def process_note(sample_mode: str) -> str:
    return f"""## 〇、流程定义（OPEN-C2 一次性写清，之后照此执行）

| 项 | 口径 |
|---|---|
| 谁判 | **负责人**（本脚本只搬运事实、不代判：M3 的 ground truth 是市场共识，无可查询真值表） |
| 时长预算 | ≤10 分钟/日（分层初筛）；全量口径按 §8.1 原文「每日候选全量」 |
| 抽样 | {'当日候选全量（§8.1 转正口径）' if sample_mode == 'full' else 'OPEN-C2 分层：『其他』回收 5 + 冲突 3 + 随机 2 = 10（按日独立子流，同 seed 可复现）'} |
| 判据三档 | **强关系**＝该事件/消息面与该板块所属家族存在真实需求或基本面传导；**弱关系**＝传导链存在但间接/牵强（同族不同驱动、跨链二级传导）；**无关系**＝两者无实质传导 |
| 第四档 | **无法判定**＝当日证据不足以判（消息缺失/板块名歧义）。不计分子也不计分母，单独披露 |
| 计入规则 | 误配率 = 「无关系」/（强 + 弱 + 无）。**只有「无关系」计入分子**（OPEN-C2 原文） |
| 分母口径 | §8.1 定义 M3 针对「glue **给出了归属**」的行；glue 弃权/双空（`无归属`）不入分母，另作「glue 弃权率」披露 |
| 红线 | 单日误配率 ≤ 15%（10 样本 → 最多 1 条无关系；2 条即 20% 破线） |
| 转正 | **连续 {STREAK_DAYS_REQUIRED} 个抽查日**逐日达标，且必须用 `--sample-mode full` 口径复跑（分层样本对难点超采，读数偏高，只作预警初筛） |
| 回填 | 把本表 `grade` 列填成 强关系/弱关系/无关系/无法判定 之一（csv 或 HTML 页 对/错/存疑 键位 1/2/3 均可），再跑 `--score` |
"""


def render_md(pkg: dict[str, Any]) -> str:
    rows = pkg["rows"]
    lines: list[str] = [
        f"# M3 家族归属人工终审评审包（{pkg['sample_mode']} 口径，seed={pkg['seed']}）",
        "",
        f"- 生成：{pkg['generated_at']}　归档日：{'、'.join(pkg['days'])}　样本：{len(rows)} 行",
        f"- 数据源：`{audit_tool.ARCHIVE_ROOT / 'YYYYMMDD' / '{unified,agent_glue,news}.json'}`（生产当日归档，天然 point-in-time）",
        f"- 审核工具同源：`scripts/export_glue_family_audit.py`（类别判定复用其 `classify`，不另写口径）",
        "",
        process_note(pkg["sample_mode"]),
    ]
    if pkg["shortfalls"]:
        lines += ["", "### ⚠️ 抽样缺口（池不足，未跨层补齐）", ""]
        for key, v in sorted(pkg["shortfalls"].items()):
            lines.append(f"- {key}：要求 {v['asked']}，可用 {v['available']}，实取 {v['taken']}")
    if pkg["pools"]:
        lines += ["", "### 池规模（逐日逐层）", "", "| 日·层 | 池大小 |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(pkg["pools"].items())]

    lines += ["", "## 一、一览索引（判定列留空，负责人填）", "",
              "| # | 日期 | 板块 | code | 类别 | 规则侧家族 | glue 侧家族 | str | conf | 判定 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, start=1):
        lines.append(
            f"| {i} | {r['day']} | {r['name']} | {r['code']} | {audit_tool.CATEGORY_LABEL.get(r['category'], r['category'])} "
            f"| {r['rule_family']} | {r['glue_family']} | {_num(r['glue_strength'])} | {_num(r['glue_confidence'])} | ＿＿＿＿ |"
        )

    lines += ["", "## 二、逐条完整展开（判读要看全证据，别只看家族名）", ""]
    for i, r in enumerate(rows, start=1):
        pretty = f"{r['day'][:4]}-{r['day'][4:6]}-{r['day'][6:]}"
        unstable = r.get("unstable_with") or []
        lines += [
            f"### [{i}] {pretty} · {r['name']}（{r['code']}）　"
            f"规则侧 `{r['rule_family']}` → glue 侧 `{r['glue_family']}`"
            f"（{audit_tool.CATEGORY_LABEL.get(r['category'], r['category'])}）",
            "",
            f"- **glue 自述依据**：{r['glue_evidence'] or '（无）'}",
            f"- **当日消息面证据**：",
        ]
        if r["news_evidence"]:
            lines += [f"    {j}. {ev}" for j, ev in enumerate(r["news_evidence"], start=1)]
        else:
            lines += ["    （当日无相关消息证据 —— 家族归属若只靠板块名字面语义，倾向判「无关系」）"]
        if unstable:
            lines.append(f"- **⚠️ 跨日不一致**：同板块其他日拿到 {unstable}")
        lines += [
            f"- **判定**：＿＿＿＿＿＿（{GRADE_STRONG} / {GRADE_WEAK} / {GRADE_NONE} / {GRADE_UNKNOWN}）"
            f"　备注：＿＿＿＿＿＿＿＿＿＿",
            "",
            "---",
            "",
        ]
    lines += [
        "## 三、计分与判读（回填后自动产出，勿手改本节以上内容以外的列）",
        "",
        "```",
        f"python scripts/build_m3_review_package.py --score {pkg['csv_path']}",
        "```",
        "",
        f"红线 {RED_LINE_PCT}%、需连续 {STREAK_DAYS_REQUIRED} 日达标；"
        "分层口径的读数不得直接写入转正结论（见 §〇 可比性）。",
        "",
    ]
    return "\n".join(lines)


def _num(v: Any) -> str:
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def csv_rows(pkg_rows: list[dict]) -> list[dict]:
    out = []
    for i, r in enumerate(pkg_rows, start=1):
        out.append({
            "idx": i,
            "day": r["day"],
            "code": r["code"],
            "name": r["name"],
            "category": r["category"],
            "rule_family": r["rule_family"],
            "glue_family": r["glue_family"],
            "glue_strength": _num(r["glue_strength"]),
            "glue_confidence": _num(r["glue_confidence"]),
            "grade": "",                      # 判定列留空给负责人
            "note": "",
        })
    return out


def write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(records)


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


# ─────────────────────────────────────────────────────────────
# 5) CLI
# ─────────────────────────────────────────────────────────────

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OPEN-C2 · M3 人工终审评审包（离线只读，判定归负责人）")
    p.add_argument("--days", nargs="*", help="归档日 YYYYMMDD，缺省按 --recent 自动发现")
    p.add_argument("--recent", type=int, default=5, help="自动发现最近 N 个有效归档日（glue 齐全）")
    p.add_argument("--variant", default="", help="审核工具的回放变体目录（如 pipeline_l1l3），默认生产归档")
    p.add_argument("--sample-mode", choices=("stratified", "full"), default="stratified",
                   help="stratified=OPEN-C2 分层初筛（偏高读数）；full=§8.1 转正口径")
    p.add_argument("--seed", type=int, default=42, help="分层抽样种子（逐日独立子流，可复现）")
    p.add_argument("--n-recovered", type=int, default=STRATA[0][1], help="『其他』回收层抽取数")
    p.add_argument("--n-overridden", type=int, default=STRATA[1][1], help="冲突层抽取数")
    p.add_argument("--n-random", type=int, default=STRATA[2][1], help="随机对照层抽取数")
    p.add_argument("--out-dir", default=str(OUTPUT_DIR))
    p.add_argument("--score", help="回填后的 csv 路径：只计分，不产评审包")
    p.add_argument("--tag", default="", help="产物文件名后缀，缺省用生成日")
    return p.parse_args(argv)


def build_package(args: argparse.Namespace, stamp: str) -> int:
    days = resolve_days(args.days, args.recent, args.variant)
    if not days:
        print("[FAIL] 无同时含 unified.json + agent_glue.json + news.json 的归档日"
              + (f"（variant={args.variant}）" if args.variant else ""), file=sys.stderr)
        return 1
    rows = load_rows(days, args.variant)
    if not rows:
        print("[FAIL] 审核工具未产出任何候选行", file=sys.stderr)
        return 1

    if args.sample_mode == "full":
        sel = pick_full(rows)
    else:
        sel = stratify(rows, args.n_recovered, args.n_overridden, args.n_random, args.seed)

    out_dir = Path(args.out_dir)
    tag = args.tag or stamp
    md_path = out_dir / f"m3_review_{tag}.md"
    csv_path = out_dir / f"m3_review_{tag}.csv"
    json_path = out_dir / f"m3_review_{tag}.json"

    picked = sel["picked"]
    pkg = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "sample_mode": args.sample_mode,
        "seed": args.seed,
        "rows": picked,
        "pools": sel["pools"],
        "shortfalls": sel["shortfalls"],
        "csv_path": str(csv_path),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_md(pkg), encoding="utf-8")
    records = csv_rows(picked)
    write_csv(csv_path, records)
    json_path.write_text(json.dumps({
        "generated_at": pkg["generated_at"],
        "days": days,
        "sample_mode": args.sample_mode,
        "seed": args.seed,
        "asked": sel["asked"],
        "pools": sel["pools"],
        "shortfalls": sel["shortfalls"],
        "picked": [{"day": r["day"], "code": r["code"], "name": r["name"],
                    "category": r["category"]} for r in picked],
        "grade_scale": list(GRADES),
        "html_btn_map": GRADE_TO_HTML_BTN,
        "red_line_pct": RED_LINE_PCT,
        "days_required": STREAK_DAYS_REQUIRED,
        "files": {"md": str(md_path), "csv": str(csv_path)},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    from collections import Counter
    cats = Counter(r["category"] for r in picked)
    print(f"归档日 {len(days)} 天（{'、'.join(days)}），候选 {len(rows)} 行 → 抽样 {len(picked)} 行"
          f"（口径={args.sample_mode}，seed={args.seed}）")
    print("样本类别分布：" + "、".join(
        f"{audit_tool.CATEGORY_LABEL.get(k, k)}={v}" for k, v in sorted(cats.items())))
    if sel["shortfalls"]:
        print("⚠️ 分层缺口（未跨层补齐，如实披露）：" +
              "；".join(f"{k} 要求{v['asked']}/可用{v['available']}/实取{v['taken']}"
                        for k, v in sorted(sel["shortfalls"].items())))
    print(f"评审包：{md_path}")
    print(f"回填表：{csv_path}")
    print(f"抽样留痕：{json_path}")
    print("判定归负责人（本脚本不代判）；回填后跑 --score 计分")
    return 0


def score_package(path: str) -> int:
    csv_path = Path(path)
    if not csv_path.exists():
        print(f"[FAIL] 回填表不存在: {csv_path}", file=sys.stderr)
        return 1
    records = read_csv(csv_path)
    if not records:
        print("[FAIL] 回填表为空", file=sys.stderr)
        return 1
    res = score_records(records)
    meta_path = csv_path.with_suffix(".json")
    mode = ""
    if meta_path.exists():
        mode = json.loads(meta_path.read_text(encoding="utf-8")).get("sample_mode", "")
    print(f"== M3 计分（口径={mode or '未知'}，红线 {res['red_line_pct']}%，需连续 {res['days_required']} 日）==")
    for d in res["days"]:
        print(f"{d['day']}: 样本{d['sampled']} 已判{d['judged']} "
              f"{GRADE_STRONG}{d[GRADE_STRONG]}/{GRADE_WEAK}{d[GRADE_WEAK]}/{GRADE_NONE}{d[GRADE_NONE]} "
              f"误配率={d['misfire_pct'] if d['misfire_pct'] is not None else '—'}% "
              + (f"{'达标' if d['pass'] else '不达标'}" if d["judged"] else "未判满（不计达标/破线）")
              + (f" 无法判定{d['undecidable']}" if d["undecidable"] else "")
              + (f" 未判{d['unjudged']}" if d["unjudged"] else "")
              + (f" 非分母(glue弃权){d['excluded_unattributed']}" if d["excluded_unattributed"] else ""))
    p = res["pooled"]
    print(f"合并：已判 {p['judged']}（{GRADE_NONE} {p[GRADE_NONE]} / {GRADE_WEAK} {p[GRADE_WEAK]} / "
          f"{GRADE_STRONG} {p[GRADE_STRONG]}）→ 误配率 {p['misfire_pct'] if p['misfire_pct'] is not None else '—'}% "
          f"Wilson95={p['wilson95']}")
    print(f"末尾连续达标 {res['streak_days_met']} 日（历史最长 {res['longest_streak_days']}）"
          f"→ status={res['status']}；有效抽查日 {res['days_with_data']}，无数据日 {res['days_without_data']}")
    if mode == "stratified":
        print("⚠️ 分层初筛口径：对难点超采、读数偏高，转正须用 --sample-mode full 复跑（§8.1）")
    out = csv_path.with_name(csv_path.stem + "_score.json")
    out.write_text(json.dumps({"sample_mode": mode, **res}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"计分工件：{out}")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    stamp = date.today().strftime("%Y%m%d")
    if args.score:
        return score_package(args.score)
    return build_package(args, stamp)


if __name__ == "__main__":
    raise SystemExit(main())
