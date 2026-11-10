# -*- coding: utf-8 -*-
"""P3-a 个股—主题证据回链（只读审计，不扩候选资格）。

实施稿 §6（第三层：主题到个股的转换）与 §4.3（身份复用现有对象）。

职责
----
把当日 `candidate_pool.universe` 的每只票回链到：
`board_code → 当日 observation → open episode/age → 四变体 setup → 输出桶可见性`，
并计量三类失真的**可观测部分**：

| 失真 | 本模块能测的 | 仍缺 |
|---|---|---|
| 强板块居中跟风 | 板块强度 ≥80 且非 finalist（代理，非个股相对强度） | 个股板块内相对强度/分支受益证据 |
| 弱埋伏接跌 | ambush 道 + 板块破位/强度<50/无 armed setup | 个股自身趋势与可成交性 |
| 后置验证无补选 | 段 age>5 的“晚段入场” | 验证拒绝后的名额释放与递补 |

口径红线
--------
- 只读归档 `candidate_pool.json` + 账本快照事实；**不产生任何候选资格、不改漏斗**；
- 空占位行（symbol 与 name 均为空，如 10-12 的 53 行）单列 `placeholder_row`，
  不计入映射率分母——归档噪声不得伪装成「映射失败」；
- 名称回链只做精确匹配 + `sector_query_board` 精确匹配；不做模糊/LLM 推断，
  匹配不上一律 `unmatched` 并保留原名。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

LATE_ENTRY_AGE = 5
STRONG_BOARD = 80.0
WEAK_BOARD = 50.0
WEAK_BOARD_FLOOR = 40.0

LINK_EXACT = "exact"
LINK_QUERY_BOARD = "query_board"
LINK_UNMATCHED = "unmatched"
LINK_PLACEHOLDER = "placeholder_row"


@dataclass(frozen=True)
class CandidateLink:
    trade_date: str
    symbol: str
    name: str
    sector_name: str
    lane: str
    lane_raw: str
    lane_source: str
    is_finalist: bool
    board_code: str
    board_name: str
    link_status: str
    strength: Optional[float]
    state: str
    in_active_output: bool
    in_ambush_output: bool
    in_near_miss_output: bool
    in_top_output: bool
    trend_broken: Optional[bool]
    episode_id: str
    age_days: Optional[int]
    episode_status: str
    setups: Dict[str, str]
    flags: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "symbol": self.symbol,
            "name": self.name,
            "sector_name": self.sector_name,
            "lane": self.lane,
            "lane_raw": self.lane_raw,
            "lane_source": self.lane_source,
            "is_finalist": self.is_finalist,
            "board_code": self.board_code,
            "board_name": self.board_name,
            "link_status": self.link_status,
            "strength": None if self.strength is None else round(float(self.strength), 2),
            "state": self.state,
            "in_active_output": self.in_active_output,
            "in_ambush_output": self.in_ambush_output,
            "in_near_miss_output": self.in_near_miss_output,
            "in_top_output": self.in_top_output,
            "trend_broken": self.trend_broken,
            "episode_id": self.episode_id,
            "age_days": self.age_days,
            "episode_status": self.episode_status,
            "setups": dict(self.setups),
            "flags": list(self.flags),
        }


def _is_placeholder(entry: Mapping[str, Any]) -> bool:
    return not str(entry.get("symbol") or "").strip() and not str(entry.get("name") or "").strip()


LANE_FROM_TYPE = {
    "active_mainline": "mainline",
    "ambush": "ambush",
    "near_miss_ambush": "near_miss",
    "top_by_score": "top_only",
}


def _lane(entry: Mapping[str, Any], obs: Any = None) -> Tuple[str, str, str]:
    """返回 (lane, lane_raw, lane_source)。

    `sector_type` 是 2026-10-20 起才写入的字段（10-16 等早期归档为空）；缺失时按当日
    输出桶成员推断（ambush→ambush、active→mainline、near_miss→near_miss、top→top_only），
    并标记 `lane_source=bucket_fallback`，禁止把「字段缺失」记成 unknown 道别。
    """
    raw = str(entry.get("sector_type") or "").strip().lower()
    if raw in LANE_FROM_TYPE:
        return LANE_FROM_TYPE[raw], raw, "sector_type"
    if "ambush" in raw:
        return "ambush", raw, "sector_type"
    if "mainline" in raw or "active" in raw:
        return "mainline", raw, "sector_type"
    if obs is not None:
        if obs.in_ambush_output:
            return "ambush", raw, "bucket_fallback"
        if obs.in_active_output:
            return "mainline", raw, "bucket_fallback"
        if obs.in_near_miss_output:
            return "near_miss", raw, "bucket_fallback"
        if obs.in_top_output:
            return "top_only", raw, "bucket_fallback"
    return "unknown", raw, "missing"


def build_links(
    *,
    trade_date: str,
    candidates: Sequence[Mapping[str, Any]],
    observations: Sequence[Any],
    episodes: Sequence[Any],
    setups: Sequence[Any],
    calendar: Sequence[str],
) -> List[CandidateLink]:
    """逐票回链；只做精确名称匹配，不推断。"""
    obs_by_name: Dict[str, Any] = {}
    for obs in observations:
        if obs.trade_date == trade_date and obs.board_name:
            obs_by_name[obs.board_name] = obs
    ep_by_code: Dict[str, Any] = {}
    for ep in episodes:
        if ep.start_date <= trade_date and (ep.end_date is None or ep.end_date >= trade_date):
            ep_by_code.setdefault(ep.board_code, ep)
    setups_by_board: Dict[str, Dict[str, str]] = {}
    for setup in setups:
        if setup.fired_date == trade_date:
            setups_by_board.setdefault(setup.board_code, {})[setup.setup_type] = setup.status
    cal_index = {d: i for i, d in enumerate(calendar)}

    out: List[CandidateLink] = []
    for entry in candidates:
        if _is_placeholder(entry):
            out.append(CandidateLink(
                trade_date=trade_date, symbol="", name="", sector_name="", lane="unknown",
                lane_raw="", lane_source="missing", is_finalist=False, board_code="", board_name="", link_status=LINK_PLACEHOLDER,
                strength=None, state="", in_active_output=False, in_ambush_output=False,
                in_near_miss_output=False, in_top_output=False, trend_broken=None,
                episode_id="", age_days=None, episode_status="", setups={}, flags=("placeholder_row",),
            ))
            continue

        sector_name = str(entry.get("sector_name") or "").strip()
        query_board = str(entry.get("sector_query_board") or "").strip()
        obs = obs_by_name.get(sector_name) if sector_name else None
        status = LINK_EXACT if obs else ""
        if obs is None and query_board:
            obs = obs_by_name.get(query_board)
            status = LINK_QUERY_BOARD if obs else ""
        if obs is None:
            status = LINK_UNMATCHED

        ep = ep_by_code.get(obs.board_code) if obs else None
        age = None
        if ep and cal_index.get(trade_date) is not None and cal_index.get(ep.start_date) is not None:
            age = cal_index[trade_date] - cal_index[ep.start_date] + 1
        setups = setups_by_board.get(obs.board_code, {}) if obs else {}
        lane, lane_raw, lane_source = _lane(entry, obs)
        finalist = bool(entry.get("is_finalist"))
        strength = obs.strength if obs else None

        flags: List[str] = []
        if status == LINK_UNMATCHED:
            flags.append("unmapped")
        if obs is not None and not any((
            obs.in_active_output, obs.in_ambush_output,
            obs.in_near_miss_output, obs.in_top_output,
        )):
            flags.append("board_invisible")
        if obs is not None and obs.trend_broken is True:
            flags.append("board_trend_broken")
        # 埋伏道本身就是低强度早段池；只有「破位」或「极弱（<40）」才算弱埋伏接跌风险，
        # 否则 100% 命中会让标记失去区分度（ambush 全道平均强度约 45）。
        if lane == "ambush" and obs is not None:
            if obs.trend_broken is True:
                flags.append("ambush_broken")
            if strength is not None and strength < WEAK_BOARD_FLOOR:
                flags.append("ambush_deep_weak")
            if "ambush_broken" in flags or "ambush_deep_weak" in flags:
                flags.append("weak_ambush_board")
        if age is not None and age > LATE_ENTRY_AGE:
            flags.append("late_entry")
        if strength is not None and strength >= STRONG_BOARD and not finalist:
            flags.append("strong_board_follower")
        out.append(CandidateLink(
            trade_date=trade_date,
            symbol=str(entry.get("symbol") or ""),
            name=str(entry.get("name") or ""),
            sector_name=sector_name,
            lane=lane,
            lane_raw=lane_raw,
            lane_source=lane_source,
            is_finalist=finalist,
            board_code=obs.board_code if obs else "",
            board_name=obs.board_name if obs else "",
            link_status=status,
            strength=strength,
            state=obs.state if obs else "",
            in_active_output=bool(obs.in_active_output) if obs else False,
            in_ambush_output=bool(obs.in_ambush_output) if obs else False,
            in_near_miss_output=bool(obs.in_near_miss_output) if obs else False,
            in_top_output=bool(obs.in_top_output) if obs else False,
            trend_broken=obs.trend_broken if obs else None,
            episode_id=ep.episode_id if ep else "",
            age_days=age,
            episode_status=ep.status if ep else "",
            setups=setups,
            flags=tuple(flags),
        ))
    return out


FORWARD_HORIZONS = (3, 5, 10, 15)


def forward_session(calendar: Sequence[str], trade_date: str, horizon: int) -> Optional[str]:
    """交易日历上的 T+h；越界/日期不在日历 → None（不得借用更晚数据）。"""
    if trade_date not in calendar:
        return None
    idx = calendar.index(trade_date) + horizon
    return calendar[idx] if 0 <= idx < len(calendar) else None


def forward_excess(
    *,
    trade_date: str,
    horizon: int,
    calendar: Sequence[str],
    stock_closes: Mapping[str, float],
    board_closes: Mapping[str, float],
) -> Dict[str, Any]:
    """个股 T+h 收益 − 所属板块 T+h 收益（收盘到收盘，评估用，非决策输入）。"""
    end = forward_session(calendar, trade_date, horizon)
    if end is None:
        return {"status": "unresolved_no_future", "horizon": horizon}
    s0, s1 = stock_closes.get(trade_date), stock_closes.get(end)
    b0, b1 = board_closes.get(trade_date), board_closes.get(end)
    if None in (s0, s1, b0, b1) or not s0 or not b0:
        return {"status": "unresolved_price", "horizon": horizon, "end_date": end}
    stock_ret = s1 / s0 - 1.0
    board_ret = b1 / b0 - 1.0
    return {
        "status": "ok",
        "horizon": horizon,
        "end_date": end,
        "stock_return": round(stock_ret, 6),
        "board_return": round(board_ret, 6),
        "excess": round(stock_ret - board_ret, 6),
    }


def forward_net(
    *,
    trade_date: str,
    horizon: int,
    calendar: Sequence[str],
    symbol: str,
    stock_bars: Mapping[str, Mapping[str, Any]],
    cost: Any = None,
) -> Dict[str, Any]:
    """候选级含成本前向净收益（次日开盘买 → T+h 收盘卖，仅评估）。

    `stock_bars[trade_date]` 形如
    `{"open","high","low","close","volume","prev_close"}`（键名固定，杜绝行内偏移错位）。
    入场日 = 决策日的**下一交易日**开盘；`horizon>=2` 保证不违反 T+1。
    涨停/停牌/缺前收 → `unresolved_entry`；不做仓位/资金约束。
    费用按费率计（不含最低佣金，避免用任意头寸规模制造假费用）。
    """
    from src.got.portfolio_sim import CostModel
    from src.got.tradability import check_tradability, limit_pct_for_code

    if horizon < 2:
        raise ValueError("forward_net horizon 必须 >=2（T+1 约束）")
    cost = cost or CostModel()
    if trade_date not in calendar:
        return {"status": "unresolved_no_calendar", "horizon": horizon}
    idx = calendar.index(trade_date)
    if idx + horizon >= len(calendar):
        return {"status": "unresolved_no_future", "horizon": horizon}
    entry_day = calendar[idx + 1]
    exit_day = calendar[idx + horizon]
    entry = stock_bars.get(entry_day)
    exit_bar = stock_bars.get(exit_day)
    if not entry or not exit_bar:
        return {"status": "unresolved_price", "horizon": horizon}
    volume = entry.get("volume")
    if volume is not None and float(volume) <= 0:
        return {"status": "unresolved_entry", "horizon": horizon, "reason": "suspended"}
    prev_close = entry.get("prev_close")
    if not prev_close:
        return {"status": "unresolved_entry", "horizon": horizon, "reason": "no_prev_close"}
    verdict = check_tradability(
        action="buy",
        quote_price=float(entry["open"]),
        pre_close=float(prev_close),
        quote_low=float(entry["low"]),
        quote_high=float(entry["high"]),
        limit_pct=limit_pct_for_code(symbol, ""),
        entry_date=None,
        today=None,
    )
    if not verdict.executable:
        return {"status": "unresolved_entry", "horizon": horizon,
                "reason": verdict.blocked_reason or "limit_up"}
    entry_price = cost.buy_price(float(entry["open"]))
    exit_price = cost.sell_price(float(exit_bar["close"]))
    buy_rate = cost.commission_rate + cost.transfer_fee_rate
    sell_rate = cost.commission_rate + cost.transfer_fee_rate + cost.stamp_duty_rate
    net = (exit_price * (1 - sell_rate)) / (entry_price * (1 + buy_rate)) - 1.0
    return {
        "status": "ok",
        "horizon": horizon,
        "entry_date": entry_day,
        "exit_date": exit_day,
        "entry_price": round(entry_price, 4),
        "exit_price": round(exit_price, 4),
        "net_return": round(net, 6),
        "return_basis": "next_open_entry + rates(no_min_commission) + slippage",
    }


def attach_forward_net(
    rows: Sequence[Mapping[str, Any]],
    *,
    calendar: Sequence[str],
    stock_bars: Mapping[str, Mapping[str, Mapping[str, Any]]],
    horizons: Sequence[int] = FORWARD_HORIZONS,
    cost: Any = None,
) -> List[Dict[str, Any]]:
    """给回链行追加 `forward_net`（仅评估；缺价/不可买 → unresolved）。"""
    out: List[Dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "")
        item = dict(row)
        net: Dict[str, Any] = {}
        for h in horizons:
            net[str(h)] = forward_net(
                trade_date=str(row["trade_date"]), horizon=h, calendar=calendar,
                symbol=symbol, stock_bars=stock_bars.get(symbol) or {}, cost=cost,
            )
        item["forward_net"] = net
        out.append(item)
    return out


def attach_forward(
    rows: Sequence[Mapping[str, Any]],
    *,
    calendar: Sequence[str],
    stock_closes: Mapping[str, Mapping[str, float]],
    board_closes: Mapping[str, Mapping[str, float]],
    horizons: Sequence[int] = FORWARD_HORIZONS,
) -> List[Dict[str, Any]]:
    """给回链行追加 `forward`（仅用于事后评估；无价格→unresolved，不插值）。"""
    out: List[Dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "")
        board = str(row.get("board_code") or "")
        item = dict(row)
        fwd: Dict[str, Any] = {}
        for h in horizons:
            fwd[str(h)] = forward_excess(
                trade_date=str(row["trade_date"]), horizon=h, calendar=calendar,
                stock_closes=stock_closes.get(symbol) or {},
                board_closes=board_closes.get(board) or {},
            )
        item["forward"] = fwd
        out.append(item)
    return out


def _mean_ci(values: Sequence[float], by_date: Mapping[str, Sequence[float]]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "positive_rate": None, "ci95": None}
    from src.got.backtest_discipline import date_cluster_bootstrap_ci

    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 6),
        "positive_rate": round(sum(1 for v in values if v > 0) / len(values), 4),
        "ci95": list(date_cluster_bootstrap_ci(by_date)),
    }


def summarize_forward(rows: Sequence[Mapping[str, Any]], horizons: Sequence[int] = FORWARD_HORIZONS) -> Dict[str, Any]:
    """按 horizon × 分组给出超额均值/胜率/日期簇 CI（仅评估）。"""
    out: Dict[str, Any] = {}
    for h in horizons:
        key = str(h)
        buckets: Dict[str, List[Mapping[str, Any]]] = {"all": []}
        for row in rows:
            fwd = (row.get("forward") or {}).get(key) or {}
            if fwd.get("status") != "ok":
                continue
            buckets["all"].append(row)
            buckets.setdefault(str(row.get("lane")), []).append(row)
            if "weak_ambush_board" in (row.get("flags") or []):
                buckets.setdefault("weak_ambush_board", []).append(row)
            if "strong_board_follower" in (row.get("flags") or []):
                buckets.setdefault("strong_board_follower", []).append(row)
            buckets.setdefault("finalist" if row.get("is_finalist") else "non_finalist", []).append(row)

        out[key] = {}
        for name, subset in buckets.items():
            values = [row["forward"][key]["excess"] for row in subset]
            by_date: Dict[str, List[float]] = {}
            for row in subset:
                by_date.setdefault(str(row["trade_date"]), []).append(row["forward"][key]["excess"])
            out[key][name] = _mean_ci(values, by_date)
    return out


def summarize_net(rows: Sequence[Mapping[str, Any]], horizons: Sequence[int] = FORWARD_HORIZONS) -> Dict[str, Any]:
    """按 horizon × 分组汇总候选级**含成本净收益**（胜率/均值/日期簇 CI）。"""
    out: Dict[str, Any] = {}
    for h in horizons:
        key = str(h)
        buckets: Dict[str, List[Mapping[str, Any]]] = {"all": []}
        for row in rows:
            net = (row.get("forward_net") or {}).get(key) or {}
            if net.get("status") != "ok":
                continue
            buckets["all"].append(row)
            buckets.setdefault(str(row.get("lane")), []).append(row)
            if "weak_ambush_board" in (row.get("flags") or []):
                buckets.setdefault("weak_ambush_board", []).append(row)
            if "strong_board_follower" in (row.get("flags") or []):
                buckets.setdefault("strong_board_follower", []).append(row)
            buckets.setdefault("finalist" if row.get("is_finalist") else "non_finalist", []).append(row)
        out[key] = {}
        for name, subset in buckets.items():
            values = [row["forward_net"][key]["net_return"] for row in subset]
            by_date: Dict[str, List[float]] = {}
            for row in subset:
                by_date.setdefault(str(row["trade_date"]), []).append(
                    row["forward_net"][key]["net_return"]
                )
            out[key][name] = _mean_ci(values, by_date)
            if values:
                out[key][name]["win_rate"] = round(
                    sum(1 for v in values if v > 0) / len(values), 4
                )
    return out


def summarize(links: Sequence[CandidateLink]) -> Dict[str, Any]:
    placeholders = [x for x in links if x.link_status == LINK_PLACEHOLDER]
    real = [x for x in links if x.link_status != LINK_PLACEHOLDER]
    linked = [x for x in real if x.link_status in (LINK_EXACT, LINK_QUERY_BOARD)]
    flag_counts: Dict[str, int] = {}
    for link in real:
        for flag in link.flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    lanes: Dict[str, Dict[str, Any]] = {}
    for lane in ("mainline", "ambush", "near_miss", "top_only", "unknown"):
        subset = [x for x in real if x.lane == lane]
        if not subset:
            continue
        lanes[lane] = {
            "count": len(subset),
            "linked": sum(1 for x in subset if x.board_code),
            "finalists": sum(1 for x in subset if x.is_finalist),
            "weak_ambush_board": sum(1 for x in subset if "weak_ambush_board" in x.flags),
            "avg_strength": (
                round(sum(x.strength for x in subset if x.strength is not None)
                      / max(1, sum(1 for x in subset if x.strength is not None)), 2)
            ),
            "lane_sources": dict(sorted(
                (src, sum(1 for x in subset if x.lane_source == src))
                for src in {x.lane_source for x in subset}
            )),
        }
    ages = [x.age_days for x in real if x.age_days is not None]
    age_buckets = {
        "age<=2": sum(1 for a in ages if a <= 2),
        "age_3-5": sum(1 for a in ages if 3 <= a <= 5),
        "age>5": sum(1 for a in ages if a > LATE_ENTRY_AGE),
        "unknown": sum(1 for x in real if x.age_days is None),
    }
    unmatched_names = sorted({x.sector_name for x in real if x.link_status == LINK_UNMATCHED})
    invisible_boards = sorted({x.board_name for x in real if "board_invisible" in x.flags})
    invisible_detail: List[Dict[str, Any]] = []
    seen_invisible: Dict[Tuple[str, str], int] = {}
    for link in real:
        if "board_invisible" in link.flags:
            key = (link.trade_date, link.sector_name)
            seen_invisible[key] = seen_invisible.get(key, 0) + 1
    for (day, sector), count in sorted(seen_invisible.items()):
        invisible_detail.append({"trade_date": day, "sector_name": sector, "count": count})
    return {
        "candidates_total": len(links),
        "placeholder_rows": len(placeholders),
        "candidates_real": len(real),
        "linked": len(linked),
        "linked_rate": round(len(linked) / len(real), 4) if real else None,
        "finalists": sum(1 for x in real if x.is_finalist),
        "flags": dict(sorted(flag_counts.items())),
        "lanes": lanes,
        "age_buckets": age_buckets,
        "unmatched_names": unmatched_names[:30],
        "invisible_boards": invisible_boards[:30],
        "invisible_detail": invisible_detail[:30],
        "unmatched_count": len(unmatched_names),
        "not_measured": [
            "板块内同侪相对排名（每板块每日仅 1–3 只候选，排名为噪声，主动不做）",
            "分支受益证据链（事件→窄分支→个股）与验证拒绝后的名额释放/递补（需 P3-b 漏斗重放）",
        ],
    }
