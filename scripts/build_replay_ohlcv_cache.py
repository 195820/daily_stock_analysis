#!/usr/bin/env python
"""构建 Node 2 重放用的板块 OHLCV 缓存（只读，不写生产）。

输出
----
data/_replay/board_ohlcv_cache.json
  {code: {name, dates, open, high, low, close, volume, amount, up_count, down_count}}

说明
----
- 宇宙 = 归档 sector.json 的 strength_lookup 名称（09-02 起，可配）
- 名称→代码来自 node2_sector_trends，缺的从归档桶 detail 补
- get_board_kline 返回完整 OHLCV + up_count/down_count，可重建 breadth_health
- 现有 board_kline_cache.json 只有 dates/closes，不能用于 Node 2 重放

用法
----
  python scripts/build_replay_ohlcv_cache.py --limit 20 --workers 4
  python scripts/build_replay_ohlcv_cache.py --workers 8
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from audit_theme_episode_ledger import iter_days, parse_day  # noqa: E402

DAILY = ROOT / "data" / "got_daily"
OUT = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
DB = ROOT / "data" / "stock_analysis.db"


def load_name_code() -> dict[str, str]:
    out: dict[str, str] = {}
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select sector_name, sector_code, count(*) c "
            "from node2_sector_trends group by sector_name, sector_code"
        ).fetchall()
    finally:
        con.close()
    best: dict[str, tuple[str, int]] = {}
    for name, code, cnt in rows:
        name = (name or "").strip("\x00")
        if not name:
            continue
        if name not in best or cnt > best[name][1]:
            best[name] = (code, int(cnt))
    for name, (code, _) in best.items():
        out[name] = code
    return out


def load_universe(start: date, end: date) -> tuple[set[str], dict[str, str]]:
    names: set[str] = set()
    archive_code: dict[str, str] = {}
    for d in iter_days(start, end):
        p = DAILY / d.strftime("%Y%m%d") / "sector.json"
        if not p.exists():
            continue
        j = json.loads(p.read_text(encoding="utf-8"))
        names.update((n or "").strip("\x00") for n in (j.get("strength_lookup") or {}))
        for key in ("active_mainline", "cooling_down", "exhausted_mainline",
                    "ambush", "near_miss_ambush", "defensive_rotation",
                    "top_by_score", "industry_top"):
            items = j.get(key) or []
            if key == "active_mainline":
                items = [m for g in items for m in g.get("members", [])]
            for m in items:
                name = (m.get("name") or "").strip("\x00")
                code = m.get("code")
                if name and code:
                    archive_code.setdefault(name, code)
    return names, archive_code


def main() -> int:
    ap = argparse.ArgumentParser(description="构建重放用板块 OHLCV 缓存")
    ap.add_argument("--start", default="20260902")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--days", type=int, default=200)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 个（冒烟）")
    args = ap.parse_args()

    start, end = parse_day(args.start), parse_day(args.end)
    names, archive_code = load_universe(start, end)
    db_code = load_name_code()
    code_name: dict[str, str] = {}
    missing: list[str] = []
    for name in sorted(names):
        code = archive_code.get(name) or db_code.get(name)
        if not code:
            missing.append(name)
            continue
        code_name[code] = name
    print(f"宇宙: {len(names)} 名称，已映射 {len(code_name)} 代码，缺代码 {len(missing)}")
    if missing:
        print("  缺代码样例:", missing[:10])

    from data_provider.base import DataFetcherManager
    manager = DataFetcherManager()
    codes = sorted(code_name)
    if args.limit:
        codes = codes[: args.limit]
    print(f"抓取: {len(codes)} 个板块 × {args.days} 天，workers={args.workers}")

    cache: dict[str, dict] = {}
    failed: list[str] = []

    def fetch(code: str):
        df = manager.get_board_kline(code, "concept", args.days)
        if df is None or df.empty:
            return code, None
        df = df.sort_values("date")
        payload = {
            "name": code_name[code],
            "dates": [str(x) for x in df["date"].tolist()],
            "open": [float(x) for x in df["open"].tolist()],
            "high": [float(x) for x in df["high"].tolist()],
            "low": [float(x) for x in df["low"].tolist()],
            "close": [float(x) for x in df["close"].tolist()],
            "volume": [float(x) for x in df["volume"].tolist()],
            "amount": [float(x) for x in df["amount"].tolist()],
            "up_count": [float(x) for x in df["up_count"].tolist()],
            "down_count": [float(x) for x in df["down_count"].tolist()],
        }
        return code, payload

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, code): code for code in codes}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            try:
                _, payload = fut.result()
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{code}:{exc}")
                continue
            if payload is None:
                failed.append(f"{code}:empty")
            else:
                cache[code] = payload
            if i % 20 == 0 or i == len(codes):
                print(f"  {i}/{len(codes)} ok={len(cache)} fail={len(failed)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"写入 {OUT}：{len(cache)} 板块，失败 {len(failed)}")
    if failed:
        print("  失败样例:", failed[:10])
    if cache:
        any_code = next(iter(cache))
        print(f"  样例 {any_code}: {len(cache[any_code]['dates'])} 天 "
              f"{cache[any_code]['dates'][0]} → {cache[any_code]['dates'][-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
