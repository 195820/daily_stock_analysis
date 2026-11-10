# -*- coding: utf-8 -*-
"""推荐票 T+N 存活回测：取每日报告「候选股评分 Top N」，按当日收盘价买入，看 T+N 收盘是否为正。

口径
----
- 推荐票 = `trading_recommendations.md` 的「候选股评分 Top N」（默认 5，按报告顺序）；
- 买入价 = 报告当日收盘价（不用盘中快照价，避免 bar_complete=false 失真）；
- 存活 = T+N 收盘价 > 买入价；未到 T+N 的日期单独列出，不计入存活率；
- 数据源 = DataFetcherManager 日线（本机 mootdx 优先），缺数据的票单独标 N/A。

用法
----
    python scripts/backtest_recommendation_survival.py --start 20261019 --end 20261103
    python scripts/backtest_recommendation_survival.py --start 20261019 --end 20261103 --horizon 3
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_provider.base import DataFetcherManager  # noqa: E402
from src.got import persistence  # noqa: E402

_PICK_RE = re.compile(
    r"###\s*\d+\.\s*(.+?)\s*\((\d{6})\)\s*\n(.*?)(?=###\s*\d+\.|##\s|\Z)", re.S,
)
_SCORE_RE = re.compile(r"综合评分[:：]\*{0,2}\s*([\d.]+)")
_TOP_SECTION_RE = re.compile(r"##\s*候选股评分\s*Top\s*(\d+)", re.S)


def _parse_day(md_path: Path) -> list[dict]:
    text = md_path.read_text(encoding="utf-8")
    start = _TOP_SECTION_RE.search(text)
    if not start:
        return []
    body = text[start.start():]
    picks = []
    for index, (name, code, block) in enumerate(_PICK_RE.findall(body), 1):
        score = _SCORE_RE.search(block)
        picks.append({
            "rank": index,
            "name": name.strip(),
            "code": code,
            "score": float(score.group(1)) if score else None,
        })
    return picks


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y%m%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description="推荐票 T+N 存活回测")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    start, end = _parse_date(args.start), _parse_date(args.end)
    days = [
        d for d in persistence.list_archive_dirs(reverse=False)
        if start <= _parse_date(d.name) <= end
        and (d / "trading_recommendations.md").exists()
    ]
    if not days:
        print("无可用报告")
        return 1

    manager = DataFetcherManager()
    price_cache: dict[str, dict[str, float]] = {}
    order_cache: dict[str, list[str]] = {}

    def closes(code: str):
        if code not in price_cache:
            try:
                df, _src = manager.get_daily_data(code, days=90)
                df = df.sort_values("date")
                series = {
                    str(row["date"])[:10]: float(row["close"])
                    for _, row in df.iterrows()
                }
                price_cache[code] = series
                order_cache[code] = sorted(series)
            except Exception as exc:  # noqa: BLE001
                print(f"  price fail {code}: {exc}")
                price_cache[code] = {}
                order_cache[code] = []
        return price_cache[code], order_cache[code]

    rows = []
    for day_dir in days:
        report_date = f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:]}"
        picks = _parse_day(day_dir / "trading_recommendations.md")[: args.top]
        results = []
        for pick in picks:
            series, ordered = closes(pick["code"])
            ref = series.get(report_date)
            if ref is None or report_date not in series:
                results.append({**pick, "ref": ref, "exit": None, "ret": None, "pending": True})
                continue
            idx = ordered.index(report_date)
            target_idx = idx + args.horizon
            if target_idx >= len(ordered):
                results.append({**pick, "ref": ref, "exit": None, "ret": None, "pending": True})
                continue
            exit_close = series[ordered[target_idx]]
            results.append({
                **pick, "ref": ref, "exit": exit_close,
                "ret": round(exit_close / ref - 1, 4), "pending": False,
                "exit_date": ordered[target_idx],
            })
        rows.append({"date": report_date, "picks": results})

    def _matured(day):
        return [p for p in day["picks"] if not p.get("pending") and p.get("ret") is not None]

    lines = [
        f"# 推荐票 T+{args.horizon} 存活回测（{args.start}–{args.end}，每日 Top {args.top}）",
        "",
        "买入=推荐日收盘；存活=T+N 收盘 > 买入价；未到 T+N 的日期不计入存活率。",
        "",
        "| 日期 | 已到期 | 存活 | 存活率 | 均值 | 最好 | 最差 | 当日推荐（T+N 收益） |",
        "|---|---|---|---|---|---|---|---|",
    ]
    total = survivors = 0
    rets: list[float] = []
    for day in rows:
        matured = _matured(day)
        pending = len(day["picks"]) - len(matured)
        if not matured:
            lines.append(f"| {day['date']} | 0 | — | — | — | — | — | 未到 T+{args.horizon}（{len(day['picks'])} 只） |")
            continue
        wins = [p for p in matured if p["ret"] > 0]
        day_rets = [p["ret"] for p in matured]
        total += len(matured)
        survivors += len(wins)
        rets.extend(day_rets)
        detail = "；".join(
            f"{p['name']}({p['ret']:+.1%})" for p in matured
        )
        if pending:
            detail += f"；未到期 {pending} 只"
        lines.append(
            f"| {day['date']} | {len(matured)} | {len(wins)} | {len(wins)/len(matured):.0%} | "
            f"{sum(day_rets)/len(day_rets):+.1%} | {max(day_rets):+.1%} | {min(day_rets):+.1%} | {detail} |"
        )

    lines += [
        "",
        "## 汇总（仅已到期样本）",
        "",
        f"- 样本：{total} 只；存活 {survivors} 只（**{survivors/total:.1%}**）" if total else "- 无已到期样本",
        f"- 平均 T+{args.horizon} 收益：{sum(rets)/len(rets):+.2%}；中位 {sorted(rets)[len(rets)//2]:+.2%}" if rets else "",
        "",
    ]
    report = "\n".join(line for line in lines if line is not None)
    out = Path(args.out) if args.out else Path(
        f"data/got_daily/backtest_recommendation_survival_T{args.horizon}_{args.start}_{args.end}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\n报告已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
