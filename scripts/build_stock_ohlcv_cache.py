#!/usr/bin/env python
"""P0 冻结行情快照：回测用个股日线（未复权 raw + 前复权 qfq 收盘）。

口径（实施稿 §7.1/§7.2）
------------------------
- `raw`（MootdxFetcher，未复权）：成交价、整手金额、涨跌停/停牌判定，与生产可成交性同口径；
- `qfq`（EfinanceFetcher，前复权）：收益核算交叉校验（除权除息日 raw 收益会偏低估）；
- 快照写入 `data/_replay/stock_ohlcv_cache.json`，回测只读快照，不联网 → 同一输入可复现；
- 拉取失败按 symbol 记 `errors`，计数留痕，不填充、不插值。

用法
----
    python scripts/build_stock_ohlcv_cache.py --start 20260601 --end 20261105 --workers 4
    python scripts/build_stock_ohlcv_cache.py --force          # 全量重拉
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OUT = ROOT / "data" / "_replay" / "stock_ohlcv_cache.json"
DAILY = ROOT / "data" / "got_daily"

_ARCHIVE_SUFFIXES = ("_backup", "_pre_fix")


def archive_days(start: date, end: date) -> list[Path]:
    out = []
    for path in sorted(DAILY.iterdir()):
        if not path.is_dir() or len(path.name) != 8 or not path.name.isdigit():
            continue
        if any(path.name.endswith(sfx) for sfx in _ARCHIVE_SUFFIXES):
            continue
        try:
            day = datetime.strptime(path.name, "%Y%m%d").date()
        except ValueError:
            continue
        if start <= day <= end:
            out.append(path)
    return out


def collect_symbols(days: list[Path]) -> dict[str, str]:
    """从归档 allocation + candidate_pool 收集 symbol→name（缺失记空名，不猜）。"""
    symbols: dict[str, str] = {}
    for day_dir in days:
        path = day_dir / "portfolio_decision.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                payload = {}
            for item in payload.get("allocation") or []:
                symbol = str(item.get("symbol") or "").strip()
                if symbol:
                    symbols.setdefault(symbol, str(item.get("name") or ""))
        pool_path = day_dir / "candidate_pool.json"
        if pool_path.exists():
            try:
                pool = json.loads(pool_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pool = {}
            for item in pool.get("universe") or []:
                symbol = str(item.get("symbol") or "").strip()
                if symbol:
                    symbols.setdefault(symbol, str(item.get("name") or ""))
    return symbols


def _rows(df) -> list[list]:
    rows = []
    for _, row in df.iterrows():
        raw_date = str(row.get("date"))[:10]
        close = row.get("close")
        if not raw_date or close is None:
            continue
        rows.append([
            raw_date,
            float(row.get("open") or 0.0),
            float(row.get("high") or 0.0),
            float(row.get("low") or 0.0),
            float(close),
            float(row.get("volume") or 0.0),
        ])
    rows.sort(key=lambda r: r[0])
    return rows


def fetch_symbol(symbol: str, start: date, end: date) -> dict:
    entry: dict = {"raw": [], "qfq_close": [], "raw_source": "", "qfq_source": "", "errors": []}
    try:
        from data_provider.mootdx_fetcher import MootdxFetcher
        df = MootdxFetcher().get_daily_data(symbol, start_date=start.isoformat(), end_date=end.isoformat())
        entry["raw"] = _rows(df)
        entry["raw_source"] = "MootdxFetcher"
    except Exception as exc:  # noqa: BLE001
        entry["errors"].append(f"raw:{type(exc).__name__}:{exc}")
    try:
        from data_provider.efinance_fetcher import EfinanceFetcher
        df = EfinanceFetcher().get_daily_data(symbol, start_date=start.isoformat(), end_date=end.isoformat())
        entry["qfq_close"] = [[r[0], r[4]] for r in _rows(df)]
        entry["qfq_source"] = "EfinanceFetcher"
    except Exception as exc:  # noqa: BLE001
        entry["errors"].append(f"qfq:{type(exc).__name__}:{exc}")
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description="构建回测用个股行情冻结快照")
    ap.add_argument("--start", default="20260601")
    ap.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end = datetime.strptime(args.end, "%Y%m%d").date()
    days = archive_days(start, end)
    symbols = collect_symbols(days)
    print(f"归档日 {len(days)} 个，allocation 标的 {len(symbols)} 只")

    cache: dict = {"meta": {}, "symbols": {}}
    if OUT.exists() and not args.force:
        cache = json.loads(OUT.read_text(encoding="utf-8"))
    known = cache.get("symbols", {})

    todo = [s for s in sorted(symbols) if s not in known]
    if args.force:
        todo = sorted(symbols)
    print(f"待拉取 {len(todo)} 只（已有 {len(known)} 只）")

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch_symbol, s, start, end): s for s in todo}
        for idx, fut in enumerate(as_completed(futures), 1):
            symbol = futures[fut]
            results[symbol] = fut.result()
            rows = len(results[symbol]["raw"])
            err = " | ".join(results[symbol]["errors"])
            print(f"[{idx}/{len(todo)}] {symbol} raw={rows} {err}")

    for symbol, entry in results.items():
        entry["name"] = symbols.get(symbol, "")
        known[symbol] = entry

    payload = {
        "meta": {
            "built_at": datetime.now().isoformat(timespec="seconds"),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "raw_source": "MootdxFetcher(未复权)",
            "qfq_source": "EfinanceFetcher(前复权close)",
            "adjust_policy": "raw=成交/涨跌停；qfq=收益核算交叉校验",
            "symbols": len(known),
            "failed_raw": sorted(s for s, e in known.items() if not e.get("raw")),
            "missing_qfq": sorted(s for s, e in known.items() if not e.get("qfq_close")),
        },
        "symbols": {s: known[s] for s in sorted(known)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"快照写入 {OUT}（{OUT.stat().st_size/1024:.0f} KB，{len(known)} 只）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
