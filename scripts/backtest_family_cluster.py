# -*- coding: utf-8 -*-
"""家族簇（K 线聚类 → 家族 identity）历史回测：链式重放归档日，比对规则层产线差异。

口径
----
- 归档目录复制到临时树，按日期升序**链式**重放 Node 6（前一日重放产物当作后一日的前日归档），
  因此跨日 Jaccard 继承与 FTS latch 走的是新代码的真实链条；
- 对照基线 = 归档 `unified.json`（旧代码当日产物），只覆盖 Node 6 层字段（Phase 2 不在链上）；
- A6 复核默认关闭（`--review-mode live` 会真调 LLM，成本 ≈ 簇数/日，仅做抽样核验时用）；
- 全流程不改真实归档（临时树 + `persistence._ARCHIVE_DIR` 重定向），仅 replay 的 glue 旁挂产物
  按既有行为落 `data/got_daily/<day>/_replay/`（已被 gitignore）。

产物
----
`data/got_daily/backtest_family_cluster_<start>_<end>.md`（逐日对照表 + 汇总），stdout 打印汇总。

用法
----
    python scripts/backtest_family_cluster.py --start 20260928 --end 20261030
    python scripts/backtest_family_cluster.py --start 20261016 --end 20261021 --review-mode shadow
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.got import family_cluster, persistence  # noqa: E402
from src.got.archive import load_daily_contexts  # noqa: E402
from src.got.nodes.aggregator import AggregatorNode  # noqa: E402

OTHER = family_cluster.OTHER_FAMILY


def _parse_day(raw: str) -> date:
    return datetime.strptime(raw, "%Y%m%d").date()


def _family_of(unified: dict) -> dict[str, str]:
    """板块名 → endurance 家族键（tiers 结构，旧归档同字段）。"""
    mapping: dict[str, str] = {}
    for entry in unified.get("mainline_endurance") or []:
        for members in (entry.get("tiers") or {}).values():
            for board in members or []:
                mapping[board] = entry.get("name", "")
    return mapping


def _actionable_mainline(unified: dict) -> list[str]:
    return [
        s.get("sector_name", "")
        for s in unified.get("confirmed_sectors") or []
        if s.get("classification") == "actionable" and s.get("sector_role") == "mainline"
    ]


def _suppression_rows(unified: dict) -> list[tuple]:
    return [
        (e.get("name", ""), int(e.get("days_suppressed") or 0), tuple(e.get("members") or []))
        for e in unified.get("family_suppression") or []
    ]


def _other_rate(boards: list[str], lookup: dict[str, str]) -> float | None:
    if not boards:
        return None
    return round(sum(1 for b in boards if (lookup.get(b) or "") == OTHER) / len(boards), 3)


def _short(value: tuple, limit: int = 3) -> str:
    name, days, members = value
    shown = "/".join(members[:limit]) + ("…" if len(members) > limit else "")
    return f"{name}(d{days}:{shown})"


async def _replay_chain(days: list[Path], review_mode: str) -> list[dict]:
    """按日期升序链式重放，返回逐日指标（临时树内 unified.json 同步回写）。"""
    rows: list[dict] = []
    for index, day_dir in enumerate(days, 1):
        run_date = _parse_day(day_dir.name)
        original = json.loads((day_dir / "unified.json").read_text(encoding="utf-8"))
        sector = json.loads((day_dir / "sector.json").read_text(encoding="utf-8"))
        lookup = sector.get("frame_group_lookup") or {}

        contexts = load_daily_contexts(run_date)
        node = AggregatorNode(use_mock=False)
        unified = await node.run(
            market=contexts["market"],
            sector=contexts["sector"],
            capital=contexts["capital"],
            news=contexts["news"],
            sentiment=contexts["sentiment"],
            replay_mode=True,
            run_date=run_date,
        )
        payload = json.loads(unified.model_dump_json())
        (day_dir / "unified.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8",
        )

        old_boards = _actionable_mainline(original)
        new_boards = _actionable_mainline(payload)
        shadow = payload.get("family_cluster_shadow") or {}
        old_map = _family_of(original)
        new_map = _family_of(payload)
        changed = {
            board: (old_map.get(board), new_map.get(board))
            for board in set(old_map) | set(new_map)
            if old_map.get(board) != new_map.get(board)
        }
        rows.append({
            "date": day_dir.name,
            "actionable_old": len(old_boards),
            "actionable_new": len(new_boards),
            "before_other_rate": _other_rate(old_boards, lookup),
            "after_other_rate": (shadow.get("coverage_after") or {}).get("其他_rate"),
            "cluster_id_rate": (shadow.get("coverage_after") or {}).get("cluster_id_rate"),
            "endurance_old": len(original.get("mainline_endurance") or []),
            "endurance_new": len(payload.get("mainline_endurance") or []),
            "fs_old": _suppression_rows(original),
            "fs_new": _suppression_rows(payload),
            "changed_keys": len(changed),
            "changed_from_other": sum(1 for old, _ in changed.values() if old == OTHER),
            "changed_samples": [
                f"{b}: {old or '∅'}→{new}" for b, (old, new) in sorted(changed.items())
            ][:4],
            "clusters": (shadow.get("review_stats") or {}).get("total"),
            "vote_clusters": (shadow.get("review_stats") or {}).get("vote_clusters"),
        })
        print(
            f"[{index}/{len(days)}] {day_dir.name} 其他率 "
            f"{rows[-1]['before_other_rate']}→{rows[-1]['after_other_rate']} "
            f"| 改键 {rows[-1]['changed_keys']}（离『其他』{rows[-1]['changed_from_other']}）"
        )
    return rows


def _render(rows: list[dict], *, start: str, end: str) -> str:
    lines = [
        f"# 家族簇历史回测（{start}–{end}）",
        "",
        "口径：链式重放 Node 6（临时归档树），基线=旧代码归档 `unified.json`；"
        "只覆盖 Node 6 字段，Phase 2 不在链上；A6 复核按运行时开关。",
        "",
        "| 日期 | actionable 主线 | 其他率 before | 其他率 after | cluster_id 占比 | "
        "endurance 族数 旧→新 | FTS 台账 旧 | FTS 台账 新 | 改键数（离其他） |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['date']} | {row['actionable_old']}→{row['actionable_new']} | "
            f"{row['before_other_rate']} | {row['after_other_rate']} | {row['cluster_id_rate']} | "
            f"{row['endurance_old']}→{row['endurance_new']} | "
            f"{'; '.join(_short(v) for v in row['fs_old']) or '—'} | "
            f"{'; '.join(_short(v) for v in row['fs_new']) or '—'} | "
            f"{row['changed_keys']}（{row['changed_from_other']}） |"
        )
    lines.append("")
    total_actionable = sum(r["actionable_old"] for r in rows)
    before_other = sum(
        round((r["before_other_rate"] or 0) * r["actionable_old"]) for r in rows
    )
    after_other = sum(
        round((r["after_other_rate"] or 0) * r["actionable_new"]) for r in rows
    )
    lines += [
        "## 汇总",
        "",
        f"- 交易日：{len(rows)}；actionable 主线板块合计：{total_actionable}",
        f"- `其他` 板块数：{before_other} → {after_other}"
        f"（{before_other / total_actionable:.1%} → {after_other / total_actionable:.1%}）"
        if total_actionable else "- 无样本",
        f"- endurance 家族数：{sum(r['endurance_old'] for r in rows)} → "
        f"{sum(r['endurance_new'] for r in rows)}",
        f"- 分组键变更：{sum(r['changed_keys'] for r in rows)} 个板块-日，"
        f"其中原键为「其他」{sum(r['changed_from_other'] for r in rows)} 个",
        "",
        "## 改键样例（每日前 4 条）",
        "",
    ]
    for row in rows:
        if row["changed_samples"]:
            lines.append(f"- {row['date']}：" + "；".join(row["changed_samples"]))
    lines.append("")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="家族簇历史回测（链式重放）")
    parser.add_argument("--start", required=True, help="起始日 YYYYMMDD（含）")
    parser.add_argument("--end", required=True, help="结束日 YYYYMMDD（含）")
    parser.add_argument(
        "--review-mode", default="off",
        choices=list(family_cluster.FAMILY_CLUSTER_MODES),
        help="A6 复核模式（缺省 off：纯规则层回测，零 LLM）",
    )
    parser.add_argument("--out", default="", help="报告输出路径（缺省 data/got_daily/backtest_family_cluster_*.md）")
    args = parser.parse_args()

    start, end = _parse_day(args.start), _parse_day(args.end)
    candidates = [
        d for d in persistence.list_archive_dirs(reverse=False)
        if start <= _parse_day(d.name) <= end
        and (d / "sector.json").exists()
        and (d / "unified.json").exists()
    ]
    if not candidates:
        print("无可用归档日（需 sector.json + unified.json）")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for day_dir in candidates:
            shutil.copytree(day_dir, root / day_dir.name)
        persistence._ARCHIVE_DIR = root
        os.environ["GOT_FAMILY_CLUSTER_ENABLED"] = "1"
        os.environ["GOT_FAMILY_CLUSTER_REVIEW"] = args.review_mode
        rows = await _replay_chain([root / d.name for d in candidates], args.review_mode)

    report = _render(rows, start=args.start, end=args.end)
    out_path = (
        Path(args.out) if args.out
        else Path("data/got_daily") / f"backtest_family_cluster_{args.start}_{args.end}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report + "\n", encoding="utf-8")
    print("\n" + report)
    print(f"报告已写入：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
