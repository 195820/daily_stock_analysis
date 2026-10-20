#!/usr/bin/env python3
"""F1 埋伏道一致性校验（_build_ambush_lane_validation）— 归档离线回测。

**回测范围声明**：只读归档消费者，零生产改动、零 LLM。产出口径：
- 三规则历史触发分布与 7 归档日窗口内结果追踪（rule-2 early 触发 → 离开率）；
- 对照组（reservoir 1–4 天条目）同窗离开率 → **区分度读数**（非原始命中率，
  减基准纪律：埋伏票本身高周转，离开率高不构成规则有效性证据）；
- rule-2 high（days≥7）可达性检查（0.7×RESERVOIR_EXPIRY_DAYS=7 vs 历史最大驻留）。

规则语义（aggregator.py:2842 `_build_ambush_lane_validation` 直读复用）：
1. stagnation_ambush：ambush_pool 候选 ∈ cooling_down ∪ industry_top（`.name`）；
2. catalyst_expiry_watch：reservoir `days_in_reservoir` ≥7 → high（升格/除名提案），
   ≥5（elif）→ low 早期预警；
3. family_divergence：reservoir 条目名与 strength 榜前 3 家族互为子串且
   strength < reservoir 中位数 → medium。

追踪口径：对每个 rule-2 early 触发（day, sector）扫其后 ≤7 个**归档日**
（跳缺日如 0911/0912/1010），首次该 sector 不在 ambush_reservoir = 离开；
离开前出现在 ambush_pool[] = 晋升，否则移除。

使用：
    D:/ProgramData/miniconda3/envs/daily_stock_analysis/python.exe scripts/backtest_ambush_lane_validation.py
输出：`.review_tmp/backtest_ambush_lane_validation.md` + stdout 摘要。
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("GOT_AMBUSH_VALIDATION_SHADOW", "1")  # 函数模块级开关，须在 import 前置位

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.got.nodes.aggregator import AggregatorNode  # noqa: E402
from src.got.catalyst_config import RESERVOIR_EXPIRY_DAYS  # noqa: E402

OUT_DIR = PROJECT_ROOT / ".review_tmp"
OUT_FILE = OUT_DIR / "backtest_ambush_lane_validation.md"
EARLY_WINDOW = 7  # 触发后追踪窗（归档日）


def load_day(day_dir: Path) -> dict | None:
    """单日归档 → (pool, reservoir, sector-namespace) 或 None。"""
    up = day_dir / "unified.json"
    sp = day_dir / "sector.json"
    if not up.exists() or not sp.exists():
        return None
    try:
        u = json.loads(up.read_text(encoding="utf-8"))
        s = json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    pool = [SimpleNamespace(sector_name=e.get("sector_name") or "") for e in (u.get("ambush_pool") or [])]
    reservoir = [
        SimpleNamespace(
            sector_name=e.get("sector_name") or "",
            days_in_reservoir=int(e.get("days_in_reservoir") or 0),
            entered_strength=float(e.get("entered_strength") or 0),
        )
        for e in (u.get("ambush_reservoir") or [])
    ]
    sector = SimpleNamespace(
        cooling_down=[SimpleNamespace(name=x.get("name") or "") for x in (s.get("cooling_down") or [])],
        industry_top=[SimpleNamespace(name=x.get("name") or "") for x in (s.get("industry_top") or [])],
        theme_families=[
            SimpleNamespace(family_name=x.get("family_name") or "", family_score=float(x.get("family_score") or 0))
            for x in (s.get("theme_families") or [])
        ],
        strength_lookup=s.get("strength_lookup") or {},
    )
    return {"pool": pool, "reservoir": reservoir, "sector": sector,
            "pool_names": {e.sector_name for e in pool},
            "reservoir_names": {e.sector_name for e in reservoir}}


def main() -> int:
    root = PROJECT_ROOT / "data" / "got_daily"
    day_dirs = [d for d in sorted(root.iterdir()) if re.fullmatch(r"20\d{6}", d.name)]
    days: list[str] = []
    data: dict[str, dict] = {}
    for d in day_dirs:
        loaded = load_day(d)
        if loaded is not None:
            days.append(d.name)
            data[d.name] = loaded

    rule_counter: Counter = Counter()          # (rule, severity)
    rule1_rows: list[dict] = []
    rule3_rows: list[dict] = []
    rule2_high: list[dict] = []
    rule2_early: list[dict] = []               # (day, sector, days_in_reservoir)
    max_dwell = 0

    for day in days:
        dt = data[day]
        for e in dt["reservoir"]:
            max_dwell = max(max_dwell, e.days_in_reservoir)
        props = AggregatorNode._build_ambush_lane_validation(dt["pool"], dt["reservoir"], dt["sector"]) or []
        for p in props:
            rule_counter[(p["rule"], p.get("severity"))] += 1
            if p["rule"] == "stagnation_ambush":
                rule1_rows.append({"day": day, **p})
            elif p["rule"] == "family_divergence":
                rule3_rows.append({"day": day, **p})
            elif p["rule"] == "catalyst_expiry_watch":
                if p.get("severity") == "high":
                    rule2_high.append({"day": day, **p})
                else:
                    rule2_early.append({"day": day, "sector": p["sector"],
                                        "days": p["detail"]["days_in_reservoir"]})

    # rule-2 early 触发 → ≤7 归档日窗口离开追踪
    idx = {d: i for i, d in enumerate(days)}
    n_left = n_promoted = n_removed = n_stay = 0
    early_ledger: list[str] = []
    for t in rule2_early:
        i0 = idx[t["day"]]
        outcome = "未离开（窗内仍驻留）"
        for j in range(i0 + 1, min(i0 + 1 + EARLY_WINDOW, len(days))):
            later = data[days[j]]
            if t["sector"] not in later["reservoir_names"]:
                if t["sector"] in later["pool_names"] or any(
                    t["sector"] in data[days[k]]["pool_names"]
                    for k in range(i0 + 1, j + 1)
                ):
                    outcome = f"晋升（{days[j]} 前）"
                    n_promoted += 1
                else:
                    outcome = f"移除（{days[j]} 缺席）"
                    n_removed += 1
                n_left += 1
                break
        else:
            n_stay += 1
        early_ledger.append(f"- [{t['day']}] `{t['sector']}` 驻留 {t['days']} 天 → {outcome}")

    # 对照组：reservoir 1–4 天条目（day, entry 观测），同窗离开率
    ctrl_n = ctrl_left = 0
    for i0, day in enumerate(days):
        for e in data[day]["reservoir"]:
            if not (1 <= e.days_in_reservoir <= 4):
                continue
            ctrl_n += 1
            for j in range(i0 + 1, min(i0 + 1 + EARLY_WINDOW, len(days))):
                if e.sector_name not in data[days[j]]["reservoir_names"]:
                    ctrl_left += 1
                    break
    ctrl_rate = ctrl_left / ctrl_n * 100 if ctrl_n else float("nan")
    early_total = len(rule2_early)
    early_rate = n_left / early_total * 100 if early_total else float("nan")
    lift = early_rate - ctrl_rate

    high_reachable = max_dwell >= 7  # 0.7 × RESERVOIR_EXPIRY_DAYS

    OUT_DIR.mkdir(exist_ok=True)
    lines = [
        "# F1 埋伏道一致性校验 — 归档离线回测台账",
        "",
        f"- 生成：{date.today().isoformat()}｜规则源：`aggregator.py:2842 _build_ambush_lane_validation` 直读复用"
        f"（GOT_AMBUSH_VALIDATION_SHADOW=1，RESERVOIR_EXPIRY_DAYS={RESERVOIR_EXPIRY_DAYS}）",
        f"- 样本：{len(days)} 归档日（{days[0]}–{days[-1]}；缺 unified/sector.json 的日子自动跳过）",
        "",
        "## 三规则触发分布",
        "",
        f"| 规则 | severity | 触发数 |",
        f"|---|---|---|",
    ]
    for (rule, sev), n in sorted(rule_counter.items()):
        lines.append(f"| {rule} | {sev} | {n} |")
    lines += [
        "",
        f"最大驻留 = {max_dwell} 天 → rule-2 high（≥7）{'可达' if high_reachable else '历史不可达（max < 0.7×10），仅报告阈值语义'}",
        "",
        "## rule-2 early（days≥5 预警）→ ≤7 归档日结果追踪",
        "",
        f"- 触发 {early_total} 次：离开 {n_left}（晋升 {n_promoted} + 移除 {n_removed}）｜未离开 {n_stay}"
        f" → 窗口离开率 **{early_rate:.1f}%**",
        "",
    ]
    lines += early_ledger if early_ledger else ["- 零触发"]
    lines += [
        "",
        "## 对照组与区分度（减基准纪律）",
        "",
        f"- 对照组：reservoir `days_in_reservoir∈[1,4]` 条目（day×entry 观测 n={ctrl_n}），同窗离开率 **{ctrl_rate:.1f}%**",
        f"- 区分度 = early 触发组 − 对照组 = **{lift:+.1f}pp**"
        + ("（差异微弱：埋伏票本身高周转，规则未提供超出基纪律的识别力，如实报告）" if abs(lift) < 15 else ""),
        "",
        "## 其余规则逐条",
        "",
    ]
    lines += [f"- [{r['day']}] stagnation_ambush `{r['sector']}` cooling={r['detail']['in_cooling']} industry_top={r['detail']['in_industry_top']}"
              for r in rule1_rows] or ["- rule-1 stagnation_ambush：0 触发（pool 与 cooling/industry_top 零交集）"]
    lines += [f"- [{r['day']}] family_divergence `{r['sector']}` family={r['detail']['family']} "
              f"strength {r['detail']['entry_strength']} < median {r['detail']['median_strength']}"
              for r in rule3_rows] or ["- rule-3 family_divergence：0 触发"]
    lines += [f"- [{r['day']}] rule-2 high `{r['sector']}` days={r['detail']['days_in_reservoir']}"
              for r in rule2_high] or ["- rule-2 high（days≥7）：0 触发"]
    lines += [
        "",
        "## 判读（plan §3.3 对照）",
        "",
        f"> rule-2 high 历史不可达（max 驻留 {max_dwell} < 7）：阈值语义在历史分布上仅由 early 层承载，"
        f"high 层是防御性兜底；rule-1 零触发说明 pool 准入闸门已挡住停滞板块（校验层冗余但无害）；"
        f"rule-2 early 窗口离开率 {early_rate:.1f}% vs 对照组 {ctrl_rate:.1f}%（{lift:+.1f}pp）——"
        f"**区分度读数以对照组为准**，不做原始命中率背书。判据④零决策红线与 shadow 窗口计数照常生产执行。",
    ]
    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")

    print(f"[OK] {len(days)} 归档日（{days[0]}–{days[-1]}），规则分布 {dict(rule_counter)}")
    print(f"[OK] rule-2 early {early_total} 次 → 离开 {n_left}（晋 {n_promoted}/移 {n_removed}）率 {early_rate:.1f}%")
    print(f"[OK] 对照组 n={ctrl_n} 离开率 {ctrl_rate:.1f}% → 区分度 {lift:+.1f}pp")
    print(f"[OK] max dwell={max_dwell} → rule-2 high 触发 {len(rule2_high)}")
    print(f"[OK] 报告 → {OUT_FILE.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
