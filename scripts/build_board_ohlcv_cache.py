"""重建板块 OHLCV 缓存（mootdx 单源，800 日请求 → 服务器上限约 581 根）。

输出 `data/_replay/board_ohlcv_cache.json`：
`{code: {name, dates[], open[], high[], low[], close[], volume[], amount[], up_count[], down_count[]}}`

已有缓存里的板块清单与名称是唯一真源；本脚本只负责把历史拉深，
拉取失败时保留旧序列，不删除板块。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from data_provider.base import DataFetcherManager  # noqa: E402

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
DAYS = 800
FIELDS = ("open", "high", "low", "close", "volume", "amount", "up_count", "down_count")


def main() -> int:
    old = json.loads(CACHE.read_text(encoding="utf-8"))
    mgr = DataFetcherManager()
    out: dict[str, dict] = {}
    ok = kept = 0
    t0 = time.time()
    for i, (code, prev) in enumerate(sorted(old.items()), 1):
        df = None
        for _ in range(3):
            try:
                df = mgr.get_board_kline(code, "concept", DAYS)
            except Exception:
                df = None
            if df is not None and len(df) > len(prev.get("dates") or []):
                break
            time.sleep(0.3)
        if df is None or len(df) < len(prev.get("dates") or []):
            out[code] = prev
            kept += 1
        else:
            rec = {"name": prev.get("name"), "dates": [str(d)[:10] for d in df["date"]]}
            for f in FIELDS:
                if f in df:
                    rec[f] = [float(x) if x == x else None for x in df[f]]
            out[code] = rec
            ok += 1
        if i % 50 == 0:
            print(f"{i}/{len(old)} ok={ok} kept={kept} {time.time()-t0:.0f}s", flush=True)
    CACHE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"done ok={ok} kept={kept} bars={len(next(iter(out.values()))['dates'])} "
          f"{time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
