#!/usr/bin/env python
"""构建重放用板块 15 分钟线缓存（只读）。

覆盖：index_bars(frequency=1, offset=800) ≈ 2026-08-19 → 11-05
用途：盘中运行（run_timestamp < 15:00）时，按 run_timestamp 取最后一根 15m bar，
      聚合成当日 intraday daily bar；收盘后运行仍用日线收盘。

输出：data/_replay/board_intraday_15m_cache.json
  {code: [{datetime, open, high, low, close, vol, amount, up_count, down_count}, ...]}
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

REPLAY_DIR = ROOT / "data" / "_replay"
DAILY_CACHE = REPLAY_DIR / "board_ohlcv_cache.json"
OUT = REPLAY_DIR / "board_intraday_15m_cache.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="构建板块 15m 缓存")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--offset", type=int, default=800)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    daily = json.loads(DAILY_CACHE.read_text(encoding="utf-8"))
    codes = sorted(daily)
    if args.limit:
        codes = codes[: args.limit]
    print(f"板块: {len(codes)}，15m offset={args.offset}，workers={args.workers}")

    from data_provider.mootdx_fetcher import MootdxFetcher
    fetcher = MootdxFetcher()

    def fetch(code: str):
        with fetcher._mootdx_session() as client:
            df = client.index_bars(
                symbol=code, frequency=1, start=0, offset=args.offset,
            )
        if df is None or df.empty:
            return code, None
        if "datetime" in df.columns:
            df = df.reset_index(drop=True)
        else:
            df = df.reset_index().rename(columns={"index": "datetime"})
        df["datetime"] = df["datetime"].astype(str)
        df = df.sort_values("datetime")
        return code, [
            {
                "datetime": str(r["datetime"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "vol": float(r["vol"]),
                "amount": float(r["amount"]),
                "up_count": float(r["up_count"]),
                "down_count": float(r["down_count"]),
            }
            for _, r in df.iterrows()
        ]

    cache: dict[str, list] = {}
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, c): c for c in codes}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            try:
                _, rows = fut.result()
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{code}:{exc}")
                continue
            if rows:
                cache[code] = rows
            else:
                failed.append(f"{code}:empty")
            if i % 20 == 0 or i == len(codes):
                print(f"  {i}/{len(codes)} ok={len(cache)} fail={len(failed)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"写入 {OUT}: {len(cache)} 板块，失败 {len(failed)}")
    if failed:
        print("  失败样例:", failed[:10])
    if cache:
        k = next(iter(cache))
        print(f"  样例 {k}: {len(cache[k])} bars "
              f"{cache[k][0]['datetime']} → {cache[k][-1]['datetime']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
