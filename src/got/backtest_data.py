# -*- coding: utf-8 -*-
"""回测输入快照读取层（P0 对照底座）：冻结个股行情 + 交易日历。

只读、无网络：`data/_replay/stock_ohlcv_cache.json` 由 `scripts/build_stock_ohlcv_cache.py`
一次性拉取后冻结。回测/回放/四组对照共用同一读取口径，避免「不同来源快照拼真值」
（实施稿 §7.2）。

日期口径：内部一律 ISO `YYYY-MM-DD`；归档目录名 `YYYYMMDD` 必须经 `normalize_day`
转换后再查——直接混用会让 `next_day` 静默落空（P0 曾出现 69 笔订单全部被丢的实现缺陷）。
"""

from __future__ import annotations

import bisect
from datetime import date
from typing import Dict, List, Optional

from src.got.portfolio_sim import Bar


def normalize_day(day: str) -> str:
    """`YYYYMMDD` / `YYYY-MM-DD` → ISO；其它形态原样返回（由调用方校验）。"""
    if len(day) == 8 and day.isdigit():
        return f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return day


class PriceSnapshot:
    """冻结行情快照的只读视图（raw=成交/涨跌停；qfq=收益核算交叉校验）。"""

    def __init__(self, payload: dict):
        self.meta = payload.get("meta") or {}
        self.symbols: Dict[str, dict] = payload.get("symbols") or {}
        self._raw: Dict[str, Dict[str, List[float]]] = {}
        self._qfq: Dict[str, Dict[str, float]] = {}
        self._dates: Dict[str, List[str]] = {}
        for symbol, entry in self.symbols.items():
            raw = {}
            for row in entry.get("raw") or []:
                if len(row) >= 6:
                    raw[row[0]] = row[1:6]
            self._raw[symbol] = raw
            self._dates[symbol] = sorted(raw)
            self._qfq[symbol] = {
                row[0]: float(row[1]) for row in entry.get("qfq_close") or [] if len(row) >= 2
            }
        self.dates: List[str] = sorted({d for dates in self._dates.values() for d in dates})

    # ── 查询 ────────────────────────────────────────────────────────────
    def has_day(self, day: str) -> bool:
        return normalize_day(day) in self.dates

    def bar(self, symbol: str, day: str) -> Optional[Bar]:
        day = normalize_day(day)
        raw = self._raw.get(symbol) or {}
        row = raw.get(day)
        if not row:
            return None
        dates = self._dates[symbol]
        idx = dates.index(day)
        prev_close = float(raw[dates[idx - 1]][3]) if idx > 0 else None
        open_p, high, low, close, volume = row
        return Bar(
            trade_date=date.fromisoformat(day),
            open=float(open_p), high=float(high), low=float(low), close=float(close),
            prev_close=prev_close, volume=float(volume),
        )

    def closes(self, day: str) -> Dict[str, float]:
        day = normalize_day(day)
        out = {}
        for symbol, raw in self._raw.items():
            row = raw.get(day)
            if row:
                out[symbol] = float(row[3])
        return out

    def qfq_closes(self, day: str) -> Dict[str, float]:
        day = normalize_day(day)
        return {s: v[day] for s, v in self._qfq.items() if day in v}

    def next_day(self, day: str) -> Optional[str]:
        """严格晚于 day 的第一个行情日；day 不在日历内也安全（不落首日）。"""
        idx = bisect.bisect_right(self.dates, normalize_day(day))
        return self.dates[idx] if idx < len(self.dates) else None

    def qfq_close(self, symbol: str, day: str) -> Optional[float]:
        return (self._qfq.get(symbol) or {}).get(normalize_day(day))

    def corporate_action_days(self) -> List[str]:
        """raw 与 qfq 日收益率不一致（>0.5pp）的 symbol-day，用于收益口径披露。"""
        hits = []
        for symbol in sorted(self.symbols):
            dates = self._dates[symbol]
            qfq = self._qfq.get(symbol) or {}
            raw = self._raw[symbol]
            for i in range(1, len(dates)):
                d0, d1 = dates[i - 1], dates[i]
                if d0 not in qfq or d1 not in qfq or qfq[d0] == 0 or raw[d0][3] == 0:
                    continue
                raw_r = raw[d1][3] / raw[d0][3] - 1
                qfq_r = qfq[d1] / qfq[d0] - 1
                if abs(qfq_r - raw_r) > 0.005:
                    hits.append(f"{symbol}:{d1}")
        return hits
