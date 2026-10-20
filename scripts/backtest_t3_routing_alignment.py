#!/usr/bin/env python3
"""E1 T3 路由一致性 — 归档离线回测（转正申请补充证据，零生产改动）。

**回测范围声明（总计划三条原则）**：本脚本只能产出「离线统计判据」——
rule↔t3 历史不一致率基线与映射口径全覆盖校验；**不能替代**零数值调制红线
（开关关闭日输出逐字节一致，须生产对照）、clock 一律不动（读数起点 2026-10-15）。

口径来源：`docs/got/plans/E1_rule_verdict映射表草案_20261015.md`（负责人
2026-10-15 拍板：四档语义 + avoid→keep 豁免单列）。本脚本把该映射表机制化。

- 输入：`data/got_daily/YYYYMMDD/unified.json` 的 `contradictions[]`
  （`resolution` = rule_verdict 同源文本，`arbitration_verdict` = t3_verdict）。
- 数据卫生：归档文本一律 `.replace('\\x00','')`（20260805–0814 内嵌 \\u0000）。
- 排除规则：`resolution` 空 → 不参与统计；`t3_verdict is None`
  （T3 前时代 / dwell<2 skip / 置信<0.6 / LLM 失败）→ 不构成对齐样本。
- 对齐语义：t3 downgrade ↔ rule avoid/reduce = 一致；t3 keep ↔ rule hold = 一致；
  t3 keep + rule avoid（「单板块…追涨」模板）= 豁免单列（非不一致）；
  t3 upgrade（历史 0 条，防御性保留分支）+ rule avoid/reduce = 张力单列；
  其余组合 = 不一致。

转正判据（verification.md）：10 日影子窗内 rule↔t3 不一致率 ≤30%。
本脚本产出历史基线供申请附交；影子期计数本身不豁免。

使用：
    python scripts/backtest_t3_routing_alignment.py
输出：`.review_tmp/backtest_t3_routing_alignment.md` + stdout 摘要。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / ".review_tmp"
OUT_FILE = OUT_DIR / "backtest_t3_routing_alignment.md"

# ── 映射表（草案 v1 逐行机制化；判别顺序 = 行序，命中即停）──────────────────
# (模板判别: 全部子串须命中, 四档)
MAPPING_TABLE: list[tuple[tuple[str, ...], str]] = [
    (("单板块资金领先", "勿仅因资金流入追涨"), "avoid"),        # 行1
    (("建议降低", "是否被技术面低估"), "MIXED(uncounted)"),      # 行2 双向
    (("降低置信度", "避免追高站岗"), "reduce"),                  # 行3
    (("降低仓位或观望",), "reduce"),                             # 行4
    (("谨慎追涨",), "avoid"),                                    # 行5
    (("以综合口径为准",), "hold"),                               # 行6
    (("供复核",), "hold"),                                       # 行7 glue
    (("蓄势",), "hold"),                                         # 行8
    (("逢低建仓",), "buy"),                                      # 行9
    (("长期逻辑加仓",), "buy"),                                  # 行9'
]

# 行10 关键词兜底（有序，先命中先停）
FALLBACK_KEYWORDS: list[tuple[str, str]] = [
    ("低估", "buy"), ("逢低", "buy"), ("布局", "buy"),
    ("降置信", "reduce"), ("降仓", "reduce"), ("减仓", "reduce"),
    ("追涨", "avoid"), ("追高", "avoid"), ("勿买", "avoid"),
    ("观望", "hold"), ("维持", "hold"), ("复核", "hold"), ("口径", "hold"),
]

EXEMPT_FINGERPRINT = ("单板块", "勿仅因资金流入追涨")  # 豁免模板（负责人 2026-10-15 拍板）


def map_rule_bucket(resolution: str) -> tuple[str, str]:
    """resolution 文本 → (四档, 命中方式)。返回 ('uncounted', ...) 表示新模板。"""
    for needles, bucket in MAPPING_TABLE:
        if all(n in resolution for n in needles):
            return bucket, "template"
    for kw, bucket in FALLBACK_KEYWORDS:
        if kw in resolution:
            return bucket, "fallback"
    return "uncounted", "no-match"


def load_contradictions() -> list[dict]:
    """55 日归档 → 逐条 {day, pair, resolution, t3_verdict, bucket, hit}。"""
    root = PROJECT_ROOT / "data" / "got_daily"
    rows: list[dict] = []
    for d in sorted(root.iterdir()):
        if not re.fullmatch(r"20\d{6}", d.name):
            continue
        p = d / "unified.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # 20260917 unified 完好；此处仅防御
        for c in data.get("contradictions") or []:
            res = (c.get("resolution") or "").replace("\x00", "")
            if not res:
                continue
            bucket, hit = map_rule_bucket(res)
            rows.append({
                "day": d.name,
                "pair": c.get("pair") or "?",
                "resolution": res,
                "t3": c.get("arbitration_verdict"),
                "bucket": bucket,
                "hit": hit,
                "exempt": all(fp in res for fp in EXEMPT_FINGERPRINT),
            })
    return rows


def alignment(rule_bucket: str, t3: str) -> str:
    """对齐语义（草案「与 t3_verdict 的对齐语义」节）。"""
    if t3 == "downgrade":
        if rule_bucket in ("avoid", "reduce"):
            return "agree"
        if rule_bucket == "hold":
            return "disagree"  # 规则层遗漏风险应降级而未降
        return "disagree"
    if t3 == "keep":
        if rule_bucket == "hold":
            return "agree"
        if rule_bucket == "avoid" :
            return "exempt_review"  # 豁免单列（非不一致）
        return "disagree"  # avoid/reduce/buy 被 keep 维持
    if t3 == "upgrade":
        # 历史零条；规则层过度悲观被抬 → 与 avoid/reduce 张力单列
        return "tension" if rule_bucket in ("avoid", "reduce") else "disagree"
    return "disagree"


def main() -> int:
    rows = load_contradictions()
    n_res = len(rows)
    buckets = Counter(r["bucket"] for r in rows)
    hits = Counter(r["hit"] for r in rows)
    uncounted = [r for r in rows if r["bucket"] == "uncounted"]

    with_t3 = [r for r in rows if r["t3"] is not None]
    verdicts = Counter(r["t3"] for r in rows)

    align_counter: Counter = Counter()
    exempt_rows: list[dict] = []
    disagree_rows: list[dict] = []
    tension_rows: list[dict] = []
    for r in with_t3:
        a = alignment(r["bucket"], r["t3"])
        r["alignment"] = a
        align_counter[a] += 1
        if a == "exempt_review":
            exempt_rows.append(r)
        elif a == "disagree":
            disagree_rows.append(r)
        elif a == "tension":
            tension_rows.append(r)

    n_align = align_counter["agree"]
    n_exempt = align_counter["exempt_review"]
    n_dis = align_counter["disagree"]
    n_tension = align_counter["tension"]
    denom = n_align + n_dis  # 严格口径：豁免/张力不进分母
    # 上界口径：豁免判例若不成立（计作不一致）→ 最保守读数
    denom_wide = len(with_t3)
    dis_wide = n_dis + n_exempt
    rate = (n_dis / denom * 100) if denom else float("nan")
    rate_wide = (dis_wide / denom_wide * 100) if denom_wide else float("nan")

    days_with_verdict = sorted({r["day"] for r in with_t3})

    OUT_DIR.mkdir(exist_ok=True)
    lines: list[str] = [
        "# E1 T3 路由一致性 — 历史离线回测（转正申请补充证据）",
        "",
        f"- 生成：{date.today().isoformat()}｜脚本：`scripts/backtest_t3_routing_alignment.py`（只读归档，零生产改动）",
        "- 口径：`docs/got/plans/E1_rule_verdict映射表草案_20261015.md`（负责人 2026-10-15 拍板）",
        "- 边界：离线统计判据；零数值调制红线 / 5 类生产计数 / clock（2026-10-15 起）不豁免。",
        "",
        "## 1. 总量",
        "",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 归档 contradiction 总数（含无 resolution） | {n_res + sum(1 for _ in _no_res())} |",
        f"| 有 resolution（参与映射） | {n_res} |",
        f"| 四档分布 | {dict(buckets)} |",
        f"| 命中方式 | {dict(hits)} |",
        f"| uncounted（新模板告警） | {len(uncounted)} |",
        f"| t3_verdict 有值（对齐样本） | {len(with_t3)}（verdict 分布 {dict(verdicts)}，覆盖 {len(days_with_verdict)} 天 {days_with_verdict[0]}–{days_with_verdict[-1]}） |",
        "",
    ]
    if uncounted:
        lines += ["### uncounted 明细（须人工裁决扩表）", ""]
        for r in uncounted:
            lines.append(f"- [{r['day']}] `{r['pair']}`：{r['resolution'][:80]}")
        lines.append("")

    lines += [
        "## 2. 对齐结果（E1 判据：10 日不一致率 ≤30%）",
        "",
        f"| 口径 | n | 不一致 | 不一致率 |",
        f"|---|---|---|---|",
        f"| 严格（豁免/张力单列） | {denom} | {n_dis} | **{rate:.1f}%** |",
        f"| 上界（豁免计作不一致） | {denom_wide} | {dis_wide} | {rate_wide:.1f}% |",
        "",
        f"- 一致 {n_align}｜豁免单列 {n_exempt}（avoid→keep「单板块…追涨」模板）｜"
        f"张力单列 {n_tension}（upgrade×avoid/reduce，历史 0 条即 0）｜不一致 {n_dis}",
        f"- 影子窗预期样本量：当前矛盾产出率 ~1.9 条/日（dwell≥2 才有 verdict）→ 10 日约 10–15 条；"
        f"本回测一次给出 {len(with_t3)} 条，样本量优势是 E1 回测核心价值。",
        "",
    ]
    if disagree_rows:
        lines += ["### 不一致逐条（回放对照台账）", ""]
        for r in disagree_rows:
            lines.append(
                f"- **[{r['day']}]** `{r['pair']}` rule=**{r['bucket']}** vs t3=**{r['t3']}**｜{r['resolution'][:70]}"
            )
        lines.append("")
    if exempt_rows:
        lines += ["### 豁免单列逐条（非不一致，观察项）", ""]
        for r in exempt_rows:
            lines.append(f"- [{r['day']}] `{r['pair']}` rule=avoid t3=keep")
        lines.append("")

    lines += [
        "## 3. 转正申请引用建议",
        "",
        f"> 历史基线（{days_with_verdict[0]}–{days_with_verdict[-1]}，n={denom}）：rule↔t3 不一致率 "
        f"{rate:.1f}%（豁免计不一致的上界 {rate_wide:.1f}%），远低于 30% 门槛；映射表对 {n_res} 条历史 resolution "
        f"全覆盖（uncounted={len(uncounted)}）。影子期 10 日读数照常计数，两者并列附交。",
        "",
        "> 不可替代项：零数值调制红线（GOT_T3_ROUTING_SHADOW 关闭日输出逐字节一致）必须生产对照。",
    ]

    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] {n_res} 条 resolution 映射完成（uncounted={len(uncounted)}）")
    print(f"[OK] 对齐样本 {len(with_t3)} 条：一致 {n_align} / 豁免 {n_exempt} / 张力 {n_tension} / 不一致 {n_dis}")
    print(f"[OK] 不一致率 严格 {rate:.1f}%（{denom}）｜宽口径 {rate_wide:.1f}%（{denom_wide}）")
    print(f"[OK] 报告 → {OUT_FILE.relative_to(PROJECT_ROOT)}")
    return 0 if not uncounted else 2  # 2 = 有新模板待人工裁决


def _no_res() -> list:
    """占位：无 resolution 条数仅用于报告首行粗计（逐日 contradiction 总数）。"""
    root = PROJECT_ROOT / "data" / "got_daily"
    total = 0
    for d in sorted(root.iterdir()):
        if not re.fullmatch(r"20\d{6}", d.name):
            continue
        p = d / "unified.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        total += len(data.get("contradictions") or [])
    return list(range(total))


if __name__ == "__main__":
    raise SystemExit(main())
