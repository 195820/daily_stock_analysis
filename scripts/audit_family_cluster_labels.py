# -*- coding: utf-8 -*-
"""A6 家族标签离线抽样审计（live 回写前置）。

对归档日的簇（按当前生产聚类基重建：残差 + 大簇切分）跑一次 A6 复核，产出
`a6_label` / `vote_label` / `would_rewrite` 明细供人工审核；**不写归档、不改任何生产状态**。

用法
----
    python scripts/audit_family_cluster_labels.py --dates 20261021,20261023,20261030
    python scripts/audit_family_cluster_labels.py --dates 20261023 --max-per-day 10

产物：data/got_daily/audit_family_cluster_labels_<dates>.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_family_cluster_purity as purity  # noqa: E402
from src.got import family_cluster  # noqa: E402
from src.got.archive import load_daily_contexts  # noqa: E402
from src.got.nodes.aggregator import AggregatorNode  # noqa: E402
from src.got.nodes.sector_analysis import SectorAnalysisNode  # noqa: E402

OTHER = family_cluster.OTHER_FAMILY


def _day_inputs(day: str, cache: dict):
    day_dir = Path("data/got_daily") / day
    lookup, codes = purity._day_boards(day_dir)
    names = [n for n in lookup if n in codes]
    analyzed, raw = purity._stub_inputs(names, codes, cache, day)
    return day_dir, lookup, analyzed, raw


async def _run_day(day: str, cache: dict, max_clusters: int) -> dict:
    day_dir, lookup, analyzed, raw = _day_inputs(day, cache)
    if len(analyzed) < 3:
        return {"date": day, "error": "板块不足"}
    # 生产聚类基（残差 + 大簇切分，默认值即 .env 口径）
    node2 = SectorAnalysisNode(use_mock=True)
    families = node2._build_theme_families(analyzed, raw)

    unified = json.loads((day_dir / "unified.json").read_text(encoding="utf-8"))
    actionable = [
        s.get("sector_name", "") for s in unified.get("confirmed_sectors") or []
        if s.get("classification") == "actionable" and s.get("sector_role") == "mainline"
    ]
    other_actionable = {b for b in actionable if (lookup.get(b) or "") in ("", OTHER)}

    contexts = load_daily_contexts(datetime.strptime(day, "%Y%m%d").date())
    node6 = AggregatorNode(use_mock=False)
    node6._run_date = datetime.strptime(day, "%Y%m%d").date()
    evidence = node6._build_family_evidence_index(contexts["news"])
    metrics = node6._family_board_metrics(contexts["sector"])

    # 抽样：一半名额给「含可回写其他成员」的簇（审计靶子），另一半按大小取其余簇，
    # 避免整张表都是其他簇造成抽样偏差。
    def _has_other(family):
        members = list(getattr(family, "member_boards", None) or [])
        return any(m in other_actionable for m in members)

    ordered = sorted(
        families,
        key=lambda f: (-len(getattr(f, "member_boards", None) or []),),
    )
    targets = [f for f in ordered if _has_other(f)]
    others = [f for f in ordered if not _has_other(f)]
    half = max(1, max_clusters // 2)
    picked = targets[:half] + others[: max(0, max_clusters - min(len(targets), half))]
    records = await family_cluster.build_family_clusters(
        picked, lookup,
        datetime.strptime(day, "%Y%m%d").date(),
        0.34, "shadow", None, {}, "", evidence,
        actionable_boards=actionable,
        board_metrics=metrics,
        llm_call=node6._call_llm_async,
    )
    return {
        "date": day,
        "lookup": lookup,
        "other_actionable": sorted(other_actionable),
        "total_clusters": len(families),
        "reviewed": records,
    }


def _render(results: list[dict], dates: str) -> str:
    lines = [
        f"# A6 家族标签离线抽样审计（{dates}）",
        "",
        "口径：簇按当前生产聚类基重建（残差 60 日 + 大簇 0.88 切分），**REVIEW=shadow** 只出标签不写回；",
        "`改写` = 切 live 后会被改 `frame_group` 的 actionable「其他」成员（已具名板块永不改判）。",
        "审核重点：`a6` 是否明显错族、`a6` 与 `vote` 分歧时谁对、`改写` 名单是否合理。",
        "",
    ]
    total = reviewed = nulls = errors = rejected = vote_total = agree = rewrite_boards = 0
    for result in results:
        if result.get("error"):
            lines.append(f"## {result['date']} — 跳过：{result['error']}")
            continue
        lookup = result["lookup"]
        lines.append(f"## {result['date']}（抽样 {len(result['reviewed'])}/{result['total_clusters']} 簇）")
        lines.append("")
        lines.append("| 簇 | 成员数 | vote | a6 | 改写(其他成员) | 成员（按 frame_group） |")
        lines.append("|---|---|---|---|---|---|")
        for rec in result["reviewed"]:
            total += 1
            if rec.review_status != "skipped":
                reviewed += 1
            if rec.review_status == "error":
                errors += 1
            if rec.review_status == "rejected":
                rejected += 1
            if rec.a6_label is None:
                nulls += 1
            if rec.vote_label:
                vote_total += 1
                if rec.a6_label == rec.vote_label:
                    agree += 1
            members = list(rec.member_boards or [])
            rewrite = sorted(rec.would_rewrite or {})
            rewrite_boards += len(rewrite)
            groups: dict[str, list[str]] = {}
            for name in members:
                groups.setdefault((lookup.get(name) or "").strip() or OTHER, []).append(name)
            member_text = "；".join(
                f"{label}({len(v)}): {'、'.join(v)}"
                for label, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))
            )
            a6_text = rec.a6_label or {
                "rejected": "拒绝", "error": "失败", "skipped": "未复核",
            }.get(rec.review_status, "—")
            lines.append(
                f"| {rec.cluster_id} | {len(members)} | {rec.vote_label or '—'} | "
                f"{a6_text} | {'、'.join(rewrite) or '—'} | {member_text} |"
            )
        lines.append("")
    lines += [
        "## 汇总",
        "",
        f"- 抽样簇 {total}；已复核 {reviewed}；A6 拒绝（判「其他」）{rejected}；"
        f"复核异常 {errors}；无标签合计 {nulls}",
        f"- vote 簇 {vote_total}，a6==vote {agree}"
        + (f"（{agree / vote_total:.0%}）" if vote_total else ""),
        f"- 切 live 后会改写的 actionable「其他」板块：{rewrite_boards} 个板块-簇",
        "",
    ]
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="A6 家族标签离线抽样审计")
    parser.add_argument("--dates", required=True, help="逗号分隔 YYYYMMDD")
    parser.add_argument("--max-per-day", type=int, default=20)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    cache = purity._load_cache()
    all_codes: dict[str, str] = {}
    for day in args.dates.split(","):
        _, codes = purity._day_boards(Path("data/got_daily") / day)
        all_codes.update(codes)
    purity._ensure_klines(all_codes, cache)
    purity._save_cache(cache)

    results = []
    for day in args.dates.split(","):
        print(f"[{day}] 重建簇并复核…")
        results.append(await _run_day(day, cache, args.max_per_day))

    report = _render(results, args.dates)
    out = Path(args.out) if args.out else Path(
        f"data/got_daily/audit_family_cluster_labels_{args.dates.replace(',', '_')}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    print("\n" + report)
    print(f"报告已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
