#!/usr/bin/env python
"""读取 Node 2 全年 replay 状态，计算板块 T+3/T+5 收益与胜率。

收益 = 板块指数 close(T+h)/close(T)-1；超额 = 同日 replay 宇宙均值。
胜率 = 同日超额 > 0 的 board-day 占比；CI = 日期簇 bootstrap。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from audit_theme_episode_forward_returns import agg, boot_diff, boot_mean, fmt_pct  # noqa: E402
from src.got.board_filter import is_noise_board  # noqa: E402

CACHE = ROOT / "data" / "got_daily" / "_cache" / "board_kline_2026_cache.json"
REPLAY = ROOT / "data" / "got_daily" / "_cache" / "node2_replay_2026.json"


def main() -> int:
    rows = json.loads(REPLAY.read_text(encoding="utf-8"))
    rows = [r for r in rows if not is_noise_board(str(r.get("name") or ""))]
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    idx = {code: {d: i for i, d in enumerate(rec.get("dates") or [])} for code, rec in raw.items()}

    dropped = 0
    for r in rows:
        r["d"] = r["date"]
        r["r3"] = r["r5"] = None
        rec = raw.get(r["code"]) or {}
        closes = rec.get("close") or []
        i = idx.get(r["code"], {}).get(r["date"])
        if i is None or i >= len(closes):
            continue
        if i + 3 < len(closes) and closes[i]:
            v = closes[i + 3] / closes[i] - 1
            if abs(v) <= 0.20:
                r["r3"] = v
            else:
                dropped += 1
        if i + 5 < len(closes) and closes[i]:
            v = closes[i + 5] / closes[i] - 1
            if abs(v) <= 0.20:
                r["r5"] = v
            else:
                dropped += 1

    for h in (3, 5):
        by_date = defaultdict(list)
        for r in rows:
            v = r.get(f"r{h}")
            if v is not None:
                by_date[r["date"]].append(v)
        means = {d: sum(v) / len(v) for d, v in by_date.items() if v}
        for r in rows:
            v = r.get(f"r{h}")
            r[f"x{h}"] = (v - means[r["date"]]) if (v is not None and r["date"] in means) else None

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["state"]].append(r)

    print(f"replay rows={len(rows)} 状态数={len(groups)}")
    print(f"清洗: 排除含'停板'板块; |r|>20% 置空 {dropped} 个 horizon")
    print(f"{'state':<22}{'n':>7}{'dates':>7}{'T+3':>9}{'胜率':>7}{'95%CI':>22}"
          f"{'T+5':>9}{'胜率':>7}{'95%CI':>22}")
    for state in sorted(groups, key=lambda s: -len(groups[s])):
        rs = groups[state]
        line = f"{state:<22}{len(rs):>7}"
        for h in (3, 5):
            vals = [r[f"x{h}"] for r in rs if r.get(f"x{h}") is not None]
            a = agg(rs, h)
            _, lo, hi = boot_mean(a, 1000)
            win = sum(1 for v in vals if v > 0) / len(vals) if vals else 0.0
            if h == 3:
                line += f"{len(a):>7}{fmt_pct(sum(vals)/len(vals) if vals else None):>9}{win:>7.0%}"
                line += f"  [{fmt_pct(lo)}, {fmt_pct(hi)}]"
            else:
                line += f"{fmt_pct(sum(vals)/len(vals) if vals else None):>9}{win:>7.0%}"
                line += f"  [{fmt_pct(lo)}, {fmt_pct(hi)}]"
        print(line)

    print("\n== 关键对比 ==")
    for label, a, b in [
        ("active − neutral", "active_mainline", "neutral"),
        ("ambush − neutral", "ambush", "neutral"),
        ("active − ambush", "active_mainline", "ambush"),
        ("cooling − neutral", "high_position_cooling", "neutral"),
        ("exhausted − neutral", "exhausted_mainline", "neutral"),
    ]:
        ra, rb = groups.get(a, []), groups.get(b, [])
        if not ra or not rb:
            continue
        line = f"  {label:<24}"
        for h in (3, 5):
            d, lo, hi = boot_diff(agg(ra, h), agg(rb, h), 1000)
            line += f"T+{h} {fmt_pct(d):>8} [{fmt_pct(lo)}, {fmt_pct(hi)}]  "
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
