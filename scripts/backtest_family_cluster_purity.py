# -*- coding: utf-8 -*-
"""家族聚类纯度对照：raw 收益相关 vs 残差收益相关（重建 theme_families 后比对）。

口径
----
- 对每个归档日，取其 `sector.json.frame_group_lookup` 的板块全集，拉板块 K 线（截到当日，
  无前视），用生产函数 `SectorAnalysisNode._build_theme_families` 分别以 raw / residual 基重建簇；
- 纯度 = 簇内「受控家族（frame_group，非其他）多数派占比」，只统计 ≥2 个具名成员的簇，
  按成员加权；mixed = 簇内出现 ≥2 个不同受控家族；
- 另看 actionable 主线里落「其他」的板块落在多大的簇里（>10 成员 = 大盘 blob 嫌疑）。
- K 线按 code 缓存到 `data/got_daily/_cache/board_kline_cache.json`，复跑只补缺失。

用法
----
    python scripts/backtest_family_cluster_purity.py --start 20260928 --end 20261030
    python scripts/backtest_family_cluster_purity.py --start 20261023 --end 20261023 --window 40
    python scripts/backtest_family_cluster_purity.py --start 20261023 --end 20261023 --detail
        → data/got_daily/cluster_detail_<start>_<end>/<day>_{raw,residual}.md（逐簇成员明细）
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_provider.base import DataFetcherManager  # noqa: E402
from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY  # noqa: E402
from src.got import persistence  # noqa: E402
from src.got.nodes.sector_analysis import SectorAnalysisNode  # noqa: E402
from src.got.schemas import SectorDetail  # noqa: E402

OTHER = "其他"
CACHE = Path("data/got_daily/_cache/board_kline_cache.json")


def _parse(raw: str) -> date:
    return datetime.strptime(raw, "%Y%m%d").date()


def _registry() -> dict[str, str]:
    out = {}
    for code, info in TDX_BOARD_REGISTRY.items():
        name = str(info.get("name") or "").replace("\x00", "").strip()
        if name:
            out.setdefault(name, code)
    return out


def _day_boards(day_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """→ (lookup=name→frame_group, name→code)。"""
    sec = json.loads((day_dir / "sector.json").read_text(encoding="utf-8"))
    lookup = dict(sec.get("frame_group_lookup") or {})
    codes = {str(k).replace("\x00", "").strip(): str(v) for k, v in _registry().items()}
    for group in sec.get("active_mainline") or []:
        for member in group.get("members") or []:
            name = str(member.get("name") or "").replace("\x00", "").strip()
            if name and member.get("code"):
                codes[name] = str(member["code"])
    for bucket in ("cooling_down", "exhausted_mainline", "top_by_score", "ambush"):
        for detail in sec.get(bucket) or []:
            name = str(detail.get("name") or "").replace("\x00", "").strip()
            if name and detail.get("code"):
                codes[name] = str(detail["code"])
    return lookup, codes


def _load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _ensure_klines(codes: dict[str, str], cache: dict) -> None:
    missing = [c for c in set(codes.values()) if not (cache.get(c) or {}).get("dates")]
    if not missing:
        return
    manager = DataFetcherManager()
    for index, code in enumerate(missing, 1):
        try:
            df = manager.get_board_kline(code, "concept", 200)
        except Exception:
            df = None
        if df is None or len(df) < 30:
            cache[code] = {"dates": [], "closes": []}
        else:
            df = df.sort_values("date")
            cache[code] = {
                "dates": [str(d) for d in df["date"].tolist()],
                "closes": [float(c) for c in df["close"].tolist()],
            }
        if index % 20 == 0 or index == len(missing):
            print(f"  kline {index}/{len(missing)}")


def _stub_inputs(names: list[str], codes: dict[str, str], cache: dict, day: str):
    analyzed, raw = [], []
    for name in names:
        code = codes.get(name)
        bars = cache.get(code or "")
        if not bars or not bars["dates"]:
            continue
        closes = [
            c for d, c in zip(bars["dates"], bars["closes"]) if d[:10] <= day
        ][-160:]
        if len(closes) < 30:
            continue
        analyzed.append(SectorDetail(
            code=code, name=name, state="active_mainline", strength_score=50.0,
        ))
        raw.append({"code": code, "klines": [{"close": c} for c in closes]})
    return analyzed, raw


def _metrics(families, lookup: dict[str, str]) -> dict:
    clusters = [[str(m) for m in (f.member_boards or [])] for f in families]
    sizes = [len(c) for c in clusters] or [0]
    named_total = matched = 0
    mixed = 0
    evaluated = 0
    for members in clusters:
        labels = [
            (lookup.get(m) or "") for m in members
            if (lookup.get(m) or "") not in ("", OTHER)
        ]
        if len(labels) < 2:
            continue
        counts: dict[str, int] = {}
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        evaluated += 1
        named_total += len(labels)
        matched += max(counts.values())
        if len(counts) >= 2:
            mixed += 1
    return {
        "clusters": len([s for s in sizes if s]),
        "max_size": max(sizes),
        "median_size": statistics.median(sizes),
        "big_share": round(sum(s for s in sizes if s > 10) / max(1, sum(sizes)), 3),
        "purity": round(matched / named_total, 3) if named_total else None,
        "named_members": named_total,
        "evaluated_clusters": evaluated,
        "mixed_clusters": mixed,
        "sizes": sizes,
    }


def _other_in_big(families, lookup: dict[str, str], other_boards: list[str]) -> tuple[int, float]:
    size_of = {m: len(f.member_boards or []) for f in families for m in (f.member_boards or [])}
    sizes = [size_of.get(b, 0) for b in other_boards]
    if not sizes:
        return 0, 0.0
    return sum(1 for s in sizes if s > 10), round(sum(1 for s in sizes if s > 10) / len(sizes), 3)


def _audit_day(day: str, families, lookup: dict[str, str], actionable: list[str]) -> str:
    """单日 residual 簇明细：按大小降序，成员标注 frame_group。"""
    clusters = sorted(
        ([str(m) for m in (f.member_boards or [])] for f in families),
        key=len, reverse=True,
    )
    other_set = {b for b in actionable if (lookup.get(b) or "") == OTHER}
    lines = [f"## {day}", "",
             "| # | 大小 | 纯度 | 家族分布 | 成员 |",
             "|---|---|---|---|---|"]
    for idx, members in enumerate(clusters, 1):
        labels = [(lookup.get(m) or OTHER) for m in members]
        named = [x for x in labels if x != OTHER]
        counts: dict[str, int] = {}
        for x in named:
            counts[x] = counts.get(x, 0) + 1
        purity = round(max(counts.values()) / len(named), 2) if named else None
        dist = " / ".join(
            f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
        ) or "—"
        untagged = len(labels) - len(named)
        if untagged:
            dist += f" / 其他×{untagged}"
        flagged = []
        if len(members) > 10:
            flagged.append("blob")
        if any(b in other_set for b in members):
            flagged.append("含其他(主线)")
        member_text = "、".join(
            f"{m}[{lookup.get(m) or OTHER}]" for m in members
        )
        lines.append(
            f"| {idx}{'（' + '/'.join(flagged) + '）' if flagged else ''} | {len(members)} | "
            f"{purity if purity is not None else '—'} | {dist} | {member_text} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_detail(day: str, families_by_basis: dict, lookup: dict, args) -> None:
    """逐簇明细：成员按 frame_group 分组，标簇大小/多数派/纯度，供人工核对聚簇。"""
    out_dir = Path(
        args.out.replace(".md", "_detail") if args.out else
        f"data/got_daily/cluster_detail_{args.start}_{args.end}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    for basis, families in families_by_basis.items():
        lines = [
            f"# {day} 聚簇明细 · {basis} · 窗口 {args.window} 日",
            "",
            "读法：每簇先看大小（>10 = 大盘 blob 嫌疑）、再看成员按 frame_group 分组是否同族；"
            "「其他」成员是词表漏报，靠簇身份兜底。",
            "",
        ]
        ordered = sorted(families, key=lambda f: len(f.member_boards or []), reverse=True)
        for index, family in enumerate(ordered, 1):
            members = [str(m) for m in (family.member_boards or [])]
            groups: dict[str, list[str]] = {}
            for name in members:
                label = (lookup.get(name) or "").strip() or OTHER
                groups.setdefault(label, []).append(name)
            labels = [g for g in groups if g != OTHER]
            counts = sorted(((g, len(v)) for g, v in groups.items() if g != OTHER),
                            key=lambda kv: kv[1], reverse=True)
            purity = None
            if len(members) >= 2 and labels:
                named = sum(len(groups[g]) for g in labels)
                purity = round(counts[0][1] / named, 2)
            lines.append(
                f"## 簇 {index}（{len(members)} 成员"
                + (f"，纯度 {purity}" if purity is not None else "")
                + (f"，多数派 {counts[0][0]}" if counts else "")
                + "）"
            )
            for label, names in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                lines.append(f"- **{label}**（{len(names)}）：{'、'.join(names)}")
            lines.append("")
        path = out_dir / f"{day}_{basis}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  明细：{path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="家族聚类纯度对照（raw vs residual）")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--window", type=int, default=60)
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--detail", action="store_true",
        help="额外落逐簇明细（成员按 frame_group 分组，raw/residual 各一份 md）",
    )
    parser.add_argument("--audit", action="store_true", help="附逐日簇明细（residual 基）")
    parser.add_argument("--audit-out", default="")
    args = parser.parse_args()

    start, end = _parse(args.start), _parse(args.end)
    days = [
        d for d in persistence.list_archive_dirs(reverse=False)
        if start <= _parse(d.name) <= end
        and (d / "sector.json").exists() and (d / "unified.json").exists()
    ]
    if not days:
        print("无可用归档日")
        return 1

    audit_sections: list[str] = []
    cache = _load_cache()
    day_meta = {}
    all_codes: dict[str, str] = {}
    for day_dir in days:
        lookup, codes = _day_boards(day_dir)
        day_meta[day_dir.name] = (lookup, codes)
        all_codes.update({k: v for k, v in codes.items() if k in lookup})
    print(f"日数 {len(days)}，板块名 {len(all_codes)}，拉取/复用 K 线…")
    _ensure_klines(all_codes, cache)
    _save_cache(cache)

    node = SectorAnalysisNode(use_mock=True)
    rows = []
    for day_dir in days:
        lookup, codes = day_meta[day_dir.name]
        names = [n for n in lookup if n in codes]
        analyzed, raw = _stub_inputs(names, codes, cache, day_dir.name)
        if len(analyzed) < 3:
            print(f"[{day_dir.name}] 可用板块不足，跳过")
            continue
        unified = json.loads((day_dir / "unified.json").read_text(encoding="utf-8"))
        actionable = [
            s.get("sector_name", "") for s in unified.get("confirmed_sectors") or []
            if s.get("classification") == "actionable" and s.get("sector_role") == "mainline"
        ]
        other_boards = [b for b in actionable if (lookup.get(b) or "") == OTHER]
        row = {"date": day_dir.name, "universe": len(analyzed), "other_actionable": len(other_boards)}
        detail_families = {}
        for basis in ("raw", "residual"):
            os.environ["GOT_FAMILY_CLUSTER_BASIS"] = basis
            os.environ["GOT_FAMILY_CLUSTER_WINDOW"] = str(args.window)
            families = node._build_theme_families(list(analyzed), list(raw))
            detail_families[basis] = families
            metrics = _metrics(families, lookup)
            metrics["other_in_big"], metrics["other_in_big_share"] = _other_in_big(
                families, lookup, other_boards,
            )
            row[basis] = metrics
        rows.append(row)
        if args.detail:
            _write_detail(day_dir.name, detail_families, lookup, args)
        if args.audit:
            audit_sections.append(_audit_day(day_dir.name, families, lookup, actionable))
        r, s = row["raw"], row["residual"]
        print(
            f"[{row['date']}] raw: {r['clusters']}簇 max{r['max_size']} purity {r['purity']} "
            f"| resid: {s['clusters']}簇 max{s['max_size']} purity {s['purity']} "
            f"| 其他 {row['other_actionable']}（blob {r['other_in_big']}→{s['other_in_big']}）"
        )

    if not rows:
        return 1

    lines = [
        f"# 家族聚类纯度对照（raw vs residual，{args.start}–{args.end}，窗口 {args.window} 日）",
        "",
        "纯度 = 簇内受控家族多数派占比（仅统计 ≥2 个具名成员的簇，按成员加权）；"
        "big_share = 成员落在 >10 成员簇的比例；其他(blob) = actionable 主线中落「其他」且处在 >10 成员簇的板块数。",
        "",
        "| 日期 | 板块数 | 簇数 raw→resid | 最大簇 raw→resid | 纯度 raw→resid | "
        "big_share raw→resid | 其他(blob) raw→resid |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        r, s = row["raw"], row["residual"]
        lines.append(
            f"| {row['date']} | {row['universe']} | {r['clusters']}→{s['clusters']} | "
            f"{r['max_size']}→{s['max_size']} | {r['purity']}→{s['purity']} | "
            f"{r['big_share']}→{s['big_share']} | "
            f"{r['other_in_big']}/{row['other_actionable']}→{s['other_in_big']}/{row['other_actionable']} |"
        )

    def _avg(key: str, basis: str):
        vals = [row[basis][key] for row in rows if row[basis][key] is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    lines += [
        "",
        "## 汇总（日均）",
        "",
        f"- 纯度：raw {_avg('purity','raw')} → residual {_avg('purity','residual')}",
        f"- 最大簇：raw {round(sum(r['raw']['max_size'] for r in rows)/len(rows),1)} → "
        f"residual {round(sum(r['residual']['max_size'] for r in rows)/len(rows),1)}",
        f"- big_share：raw {_avg('big_share','raw')} → residual {_avg('big_share','residual')}",
        f"- 混合簇数（日均）：raw {_avg('mixed_clusters','raw')} → residual {_avg('mixed_clusters','residual')}",
        f"- 参与纯度统计的簇数（日均）：raw {_avg('evaluated_clusters','raw')} → "
        f"residual {_avg('evaluated_clusters','residual')}；"
        f"具名成员（日均）：raw {round(sum(r['raw']['named_members'] for r in rows)/len(rows),1)} → "
        f"residual {round(sum(r['residual']['named_members'] for r in rows)/len(rows),1)}",
        f"- 其他板块落 blob：raw {sum(r['raw']['other_in_big'] for r in rows)}/"
        f"{sum(r['other_actionable'] for r in rows)} → residual "
        f"{sum(r['residual']['other_in_big'] for r in rows)}/{sum(r['other_actionable'] for r in rows)}",
        "",
        "注：簇由当日 `frame_group_lookup` 全集重建（K 线截到当日、无前视；未复刻生产候选集裁剪，"
        "簇大小与生产会有出入）；纯度只看已命名成员（「其他」不计分子分母）。",
        "",
    ]
    report = "\n".join(lines)
    out = Path(args.out) if args.out else Path(
        f"data/got_daily/purity_family_cluster_{args.start}_{args.end}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    if args.audit:
        audit_text = "\n".join(audit_sections)
        audit_path = Path(args.audit_out) if args.audit_out else Path(
            f"data/got_daily/cluster_audit_{args.start}_{args.end}.md"
        )
        audit_path.write_text(
            f"# 家族簇逐日明细（residual 基，{args.start}–{args.end}）\n\n" + audit_text + "\n",
            encoding="utf-8",
        )
        print(f"簇明细已写入：{audit_path}")
    print("\n" + report)
    print(f"报告已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
