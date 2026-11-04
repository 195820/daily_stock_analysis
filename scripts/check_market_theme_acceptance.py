# -*- coding: utf-8 -*-
"""4b 市场结构（market_theme_shadow）转正验收。

两段口径：
1. 文档硬门槛（verification.md 2026-10-15）：非空日 ≥10、status=ok ≥90%、
   ENABLED=0 时 `market_theme` 真值必须为 null（违约 0）。
2. 语义一致性（描述性，不单独作为硬门槛）：top5_concepts 与同日/次日
   `sector.json` 的 active_mainline 成员、strength_lookup 的重合/强度相关；
   同时报告噪声板块（昨ST连板/昨日涨停等）占比。

用法：
    python scripts/check_market_theme_acceptance.py --start 20260928 --end 20261103
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.got import persistence  # noqa: E402
from src.got.board_filter import is_noise_board  # noqa: E402


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y%m%d").date()


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for rank, idx in enumerate(order, start=1):
        ranks[idx] = float(rank)
    return ranks


def _pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(x) != len(y):
        return None
    mx, my = sum(x) / len(x), sum(y) / len(y)
    vx = sum((v - mx) ** 2 for v in x)
    vy = sum((v - my) ** 2 for v in y)
    if vx <= 0 or vy <= 0:
        return None
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return cov / math.sqrt(vx * vy)


def _mainline_boards(sector: dict) -> set[str]:
    names: set[str] = set()
    for group in sector.get("active_mainline") or []:
        if not isinstance(group, dict):
            continue
        if group.get("group_name"):
            names.add(str(group["group_name"]))
        for member in group.get("members") or []:
            if isinstance(member, dict) and member.get("name"):
                names.add(str(member["name"]))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="4b 市场结构验收")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    start, end = _parse_date(args.start), _parse_date(args.end)
    days = [
        d for d in persistence.list_archive_dirs(reverse=False)
        if start <= _parse_date(d.name) <= end and (d / "unified.json").exists()
    ]
    if not days:
        print("无可用 unified.json")
        return 1

    # 预载 sector，供同日/次日 mainline 对照
    sectors = {
        d.name: _load(d / "sector.json")
        for d in days
        if (d / "sector.json").exists()
    }
    ordered_names = [d.name for d in days]
    next_name = {
        name: ordered_names[i + 1]
        for i, name in enumerate(ordered_names)
        if i + 1 < len(ordered_names)
    }

    rows: list[dict] = []
    all_strength_means: list[float] = []
    for day_dir in days:
        unified = _load(day_dir / "unified.json")
        shadow = unified.get("market_theme_shadow")
        if not isinstance(shadow, dict):
            continue
        top5 = [
            str(item.get("name"))
            for item in (shadow.get("top5_concepts") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        if not top5:
            continue
        sector = sectors.get(day_dir.name, {})
        strength = sector.get("strength_lookup") or {}
        strength = {str(k): float(v) for k, v in strength.items() if isinstance(v, (int, float))}
        mainline = _mainline_boards(sector)
        next_sector = sectors.get(next_name.get(day_dir.name, ""), {})
        next_mainline = _mainline_boards(next_sector)
        next_strength = next_sector.get("strength_lookup") or {}
        next_strength = {
            str(k): float(v) for k, v in next_strength.items() if isinstance(v, (int, float))
        }

        clean = [name for name in top5 if not is_noise_board(name)]
        noise = [name for name in top5 if is_noise_board(name)]
        matched = [name for name in clean if name in strength]
        same_main = [name for name in clean if name in mainline]
        next_main = [name for name in clean if name in next_mainline]

        # 同日 top5 涨跌幅 vs strength_score 的相关性（只用能匹配的 clean 项）
        pairs = [
            (float(item.get("change_pct", 0.0)), strength[str(item.get("name"))])
            for item in (shadow.get("top5_concepts") or [])
            if isinstance(item, dict)
            and item.get("name") in strength
            and not is_noise_board(str(item.get("name")))
        ]
        corr = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
        if strength:
            all_strength_means.append(sum(strength.values()) / len(strength))
        rows.append({
            "date": f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:]}",
            "status": str(shadow.get("status", "")),
            "top5": top5,
            "noise": noise,
            "matched": len(matched),
            "same_main": len(same_main),
            "next_main": len(next_main),
            "top5_strength": (
                sum(strength[n] for n in matched) / len(matched) if matched else None
            ),
            "corr": corr,
            "true_value_null": unified.get("market_theme") is None,
        })

    total = len(rows)
    ok_days = sum(1 for r in rows if r["status"] == "ok")
    violations = sum(1 for r in rows if not r["true_value_null"])
    clean_total = sum(len(r["top5"]) - len(r["noise"]) for r in rows)
    noise_total = sum(len(r["noise"]) for r in rows)
    matched_total = sum(r["matched"] for r in rows)
    same_total = sum(r["same_main"] for r in rows)
    next_total = sum(r["next_main"] for r in rows)
    corrs = [r["corr"] for r in rows if r["corr"] is not None]
    top_strength = [r["top5_strength"] for r in rows if r["top5_strength"] is not None]

    hard_pass = total >= 10 and (ok_days / total >= 0.90 if total else False) and violations == 0

    lines = [
        f"# 4b 市场结构验收（{args.start}–{args.end}）",
        "",
        "## 文档硬门槛",
        "",
        f"- 非空日：**{total}**（要求 ≥10）",
        f"- status=ok：**{ok_days}/{total}**（{ok_days / total:.1%}，要求 ≥90%）" if total else "- status=ok：无样本",
        f"- `market_theme` 真值违约：**{violations}**（ENABLED=0 时必须为 0）",
        f"- 硬门槛：**{'PASS' if hard_pass else 'FAIL'}**",
        "",
        "## 语义一致性（描述性）",
        "",
        f"- 噪声板块：**{noise_total}/{noise_total + clean_total}**（{noise_total / (noise_total + clean_total):.1%}）" if (noise_total + clean_total) else "- 噪声板块：无样本",
        f"- clean top5 能匹配 strength_lookup：**{matched_total}/{clean_total}**（{matched_total / clean_total:.1%}）" if clean_total else "",
        f"- 与同日 active_mainline 重合：**{same_total}/{clean_total}**（{same_total / clean_total:.1%}）" if clean_total else "",
        f"- 与次日 active_mainline 重合：**{next_total}/{clean_total}**（{next_total / clean_total:.1%}）" if clean_total else "",
        f"- 同日 top5 涨跌幅 vs strength_score：n={len(corrs)}，均值 {statistics.mean(corrs):+.3f}，中位 {statistics.median(corrs):+.3f}" if corrs else "- 同日相关性：样本不足",
        f"- top5 平均 strength：{statistics.mean(top_strength):.1f}；全市场平均 strength：{statistics.mean(all_strength_means):.1f}" if top_strength and all_strength_means else "",
        "",
        "## 逐日",
        "",
        "| 日期 | status | top5 | 噪声 | 匹配 strength | 同日主线 | 次日主线 | top5 strength | corr | 真值 null |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ts = "—" if r["top5_strength"] is None else f"{r['top5_strength']:.1f}"
        cc = "—" if r["corr"] is None else f"{r['corr']:+.3f}"
        lines.append(
            f"| {r['date']} | {r['status']} | {'/'.join(r['top5'])} | "
            f"{'/'.join(r['noise']) or '—'} | {r['matched']} | {r['same_main']} | "
            f"{r['next_main']} | {ts} | {cc} | "
            f"{'Y' if r['true_value_null'] else 'N'} |"
        )

    report = "\n".join(line for line in lines if line != "")
    out = Path(args.out) if args.out else Path(
        f"data/got_daily/market_theme_acceptance_{args.start}_{args.end}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\n报告已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
