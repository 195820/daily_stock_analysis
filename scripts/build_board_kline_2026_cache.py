#!/usr/bin/env python
"""拉取板块 2026 全年 K 线缓存（含 up_count/down_count，供 Node 2 replay）。

缓存键为板块 code；字段与 get_board_kline 对齐：
  dates/open/high/low/close/volume/amount/up_count/down_count
幂等：已有且 >=200 行的 code 跳过；失败 code 记录，不阻断。

用法：
  python scripts/build_board_kline_2026_cache.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from data_provider.base import DataFetcherManager  # noqa: E402

DB = ROOT / "data" / "stock_analysis.db"
CACHE = ROOT / "data" / "got_daily" / "_cache" / "board_kline_2026_cache.json"
FIELDS = ["open", "high", "low", "close", "volume", "amount", "up_count", "down_count"]


def load_codes() -> list[tuple[str, str]]:
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select distinct sector_code, sector_name from node2_sector_trends "
            "where sector_code is not null and sector_code != ''"
        ).fetchall()
    finally:
        con.close()
    return [(c, (n or "").strip("\x00")) for c, n in rows]


def main() -> int:
    codes = load_codes()
    cache: dict[str, dict] = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    todo = [(c, n) for c, n in codes if len((cache.get(c) or {}).get("dates") or []) < 200]
    print(f"codes={len(codes)} cached_ok={len(codes)-len(todo)} todo={len(todo)}", flush=True)

    manager = DataFetcherManager()
    failed: list[str] = []
    for i, (code, name) in enumerate(todo, 1):
        try:
            df = manager.get_board_kline(code, "concept", 300)
            if df is None or len(df) < 30:
                failed.append(code)
                continue
            df = df.sort_index()
            rec = {"name": name, "dates": [str(d)[:10] for d in df.index]}
            for f in FIELDS:
                rec[f] = [float(x) if x == x else None for x in df[f].tolist()] if f in df.columns else []
            cache[code] = rec
        except Exception as exc:  # noqa: BLE001
            failed.append(code)
            print(f"  fail {code} {name}: {type(exc).__name__} {exc}", flush=True)
        if i % 20 == 0 or i == len(todo):
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(todo)} ok={len(cache)} failed={len(failed)}", flush=True)

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"saved {CACHE} boards={len(cache)} failed={len(failed)}", flush=True)
    if failed:
        print("failed codes:", ",".join(failed[:30]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
