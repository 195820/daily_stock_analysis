# -*- coding: utf-8 -*-
"""T1 审查层 reject 前向收益回测（T+3 / T+5）。

口径
----
- 样本 = `data/got_daily/YYYYMMDD/shadow_t1.json` 的 `rejects[]`；
- 基准价 = 归档日收盘价（T1 在收盘后跑，次日才可交易；这里与推荐存活回测同口径，
  先回答“被拒票后来涨没涨”，不引入次日开盘/滑点）；
- T+N = 归档日之后第 N 个交易日收盘；
- 拒绝精度 = T+N 收益 < 0 的占比（拒绝后下跌 = 拒对了）；未到期样本不计入。

用法
----
    python scripts/backtest_t1_rejects.py --start 20260915 --end 20261103
    python scripts/backtest_t1_rejects.py --start 20260915 --end 20261103 --horizons 3,5
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_provider.base import DataFetcherManager  # noqa: E402
from src.got import persistence  # noqa: E402


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y%m%d").date()


def _iso(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _load_rejects(day_dir: Path) -> list[dict]:
    path = day_dir / "shadow_t1.json"
    if not path.exists():
        return []
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not record.get("executed"):
        return []
    return [
        r for r in (record.get("rejects") or [])
        if isinstance(r, dict) and str(r.get("symbol", "")).strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="T1 reject T+N 前向收益回测")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--horizons", default="3,5")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    start, end = _parse_date(args.start), _parse_date(args.end)
    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    if not horizons or any(h <= 0 for h in horizons):
        print("horizons 必须是正整数")
        return 2

    days = [
        d for d in persistence.list_archive_dirs(reverse=False)
        if start <= _parse_date(d.name) <= end and (d / "shadow_t1.json").exists()
    ]
    if not days:
        print("无可用 shadow_t1.json")
        return 1

    manager = DataFetcherManager()
    price_cache: dict[str, dict[str, float]] = {}
    order_cache: dict[str, list[str]] = {}

    def closes(code: str):
        if code not in price_cache:
            try:
                df, _src = manager.get_daily_data(code, days=180)
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

    rows: list[dict] = []
    for day_dir in days:
        report_date = _iso(day_dir.name)
        for reject in _load_rejects(day_dir):
            code = str(reject["symbol"]).strip()
            series, ordered = closes(code)
            ref = series.get(report_date)
            row = {
                "date": report_date,
                "symbol": code,
                "name": str(reject.get("name", "")),
                "reason": str(reject.get("reason", ""))[:80],
                "ref": ref,
                "pending": False,
            }
            if ref is None or report_date not in series:
                row["pending"] = True
                row["missing"] = "no_ref_close"
                rows.append(row)
                continue
            idx = ordered.index(report_date)
            for horizon in horizons:
                target = idx + horizon
                key = f"t{horizon}"
                if target >= len(ordered):
                    row[key] = None
                    row["pending"] = True
                else:
                    exit_close = series[ordered[target]]
                    row[key] = round(exit_close / ref - 1, 4)
                    row[f"{key}_date"] = ordered[target]
            rows.append(row)

    def matured(horizon: int) -> list[dict]:
        return [r for r in rows if r.get(f"t{horizon}") is not None]

    lines = [
        f"# T1 reject T+N 前向收益回测（{args.start}–{args.end}）",
        "",
        "基准=归档日收盘；T+N=之后第 N 个交易日收盘；拒绝精度=T+N 收益<0 占比（拒对了）。",
        "未到 T+N / 无基准价的样本不计入汇总。",
        "",
        "| 日期 | 代码 | 名称 | 基准 | " + " | ".join(f"T+{h}" for h in horizons) + " |",
        "|---|---|---|---|" + "|".join("---" for _ in horizons) + "|",
    ]
    for row in rows:
        values = []
        for horizon in horizons:
            ret = row.get(f"t{horizon}")
            values.append("—" if ret is None else f"{ret:+.1%}")
        ref = "—" if row["ref"] is None else f"{row['ref']:.2f}"
        lines.append(
            f"| {row['date']} | {row['symbol']} | {row['name']} | {ref} | "
            + " | ".join(values) + " |"
        )

    lines += ["", "## 汇总（仅已到期）", ""]
    for horizon in horizons:
        sample = matured(horizon)
        if not sample:
            lines.append(f"- T+{horizon}：无已到期样本")
            continue
        rets = sorted(r[f"t{horizon}"] for r in sample)
        neg = sum(1 for r in rets if r < 0)
        mean = sum(rets) / len(rets)
        median = rets[len(rets) // 2]
        lines.append(
            f"- T+{horizon}：n={len(rets)}，拒绝精度（<0）={neg}/{len(rets)} "
            f"（{neg / len(rets):.1%}），均值 {mean:+.2%}，中位 {median:+.2%}"
        )
    lines.append("")

    report = "\n".join(lines)
    out = Path(args.out) if args.out else Path(
        f"data/got_daily/backtest_t1_rejects_{args.start}_{args.end}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"报告已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
