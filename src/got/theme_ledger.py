# -*- coding: utf-8 -*-
"""P1 主题观察账本：状态无关的 board observation / episode / setup / theme_watch。

实施稿 §4（观察底座）与设计稿 `主题账本设计_20261105.md` §3–§6。

不变量（改动前先读）
--------------------
1. **单写者**：只有本模块的 builder 写 observation/episode/setup；Node 2/6/Phase 2 只读。
2. **不回流**：`state` 只作观测特征，**不参与分段/确认/过期**（与 `wave_days` 的核心区别）。
3. **时间切片**：快照只含 `<= trade_date` 的事实；`age` 按交易日历计，缺档日只增 age、
   不推进确认计数（`pending_break` 仅由真实观测推进）。
4. **权限**：observation 仅进观察集合/诊断/复评提醒；setup 只表示「值得重评交易资格」，
   不得被任何路径映射为 buy；不改 composite、不改仓位。
5. **幂等**：按主键 upsert；同日重跑覆盖不追加；episode/setup 可由 observation 全量重建。

目录
----
`Observation` / `Episode` / `SetupSignal` → `build_observations` → `derive_episodes` →
`derive_setups` → `build_theme_watch` → `build_snapshot`；IO 侧 `ThemeLedgerStore` 与
`build_and_persist` 只做读写，不改变上述纯函数语义。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

LEDGER_VERSION = "theme_ledger/v1"
MODES = ("off", "shadow", "enabled")
DEFAULT_MODE = "off"

# 观察段先验（验证报告 §2/§5.2：80/75/3 是待验证先验，不是已证结论）
ENTER_STRENGTH = 80.0
EXIT_STRENGTH = 75.0
CONFIRM_OBSERVATIONS = 3

# setup 变体（实施稿 §5.3：四种口径独立比较，不用单一公式裁定）
SETUP_VARIANTS = ("fund_1d", "fund_3d", "fund_any", "none")
SETUP_MAX_AGE = 2
SETUP_EXPIRY_SESSIONS = 5
SETUP_PEAK_DRAWDOWN_LIMIT = 10.0

# 独立观察容量：不使用 ambush 16 / near-miss 10 / 蓄水池 5 与 15
THEME_WATCH_CAPACITY = 20


# ── 对象 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Observation:
    """单板块-日观测：必须能还原当时事实（实施稿 §4.1）。"""

    trade_date: str
    board_code: str
    board_name: str
    theme_id: str
    strength: Optional[float]
    state: str                     # 内部真实 state（仅特征，不参与分段）
    in_active_output: bool = False
    in_ambush_output: bool = False
    in_near_miss_output: bool = False
    in_top_output: bool = False
    fund_1d: Optional[float] = None
    fund_3d: Optional[float] = None
    fund_status: str = "unknown"   # known / unknown
    news_event_ids: Tuple[str, ...] = ()
    catalyst_event_ids: Tuple[str, ...] = ()
    heat_rank: Optional[int] = None
    ma20: Optional[float] = None
    close: Optional[float] = None
    ret_5d: Optional[float] = None
    trend_broken: Optional[bool] = None
    trend_status: str = "unknown"  # ok / unknown
    source: str = "real"           # real / synthetic_gap
    bar_complete: bool = True
    run_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "board_code": self.board_code,
            "board_name": self.board_name,
            "theme_id": self.theme_id,
            "strength": None if self.strength is None else round(float(self.strength), 2),
            "state": self.state,
            "in_active_output": self.in_active_output,
            "in_ambush_output": self.in_ambush_output,
            "in_near_miss_output": self.in_near_miss_output,
            "in_top_output": self.in_top_output,
            "fund_1d": None if self.fund_1d is None else round(float(self.fund_1d), 4),
            "fund_3d": None if self.fund_3d is None else round(float(self.fund_3d), 4),
            "fund_status": self.fund_status,
            "news_event_ids": list(self.news_event_ids),
            "catalyst_event_ids": list(self.catalyst_event_ids),
            "heat_rank": self.heat_rank,
            "ma20": None if self.ma20 is None else round(float(self.ma20), 4),
            "close": None if self.close is None else round(float(self.close), 4),
            "ret_5d": None if self.ret_5d is None else round(float(self.ret_5d), 4),
            "trend_broken": self.trend_broken,
            "trend_status": self.trend_status,
            "source": self.source,
            "bar_complete": self.bar_complete,
            "run_id": self.run_id,
        }


@dataclass
class Episode:
    """观察段：语义固定为「持续观察」，不是交易信号。"""

    episode_id: str
    theme_id: str
    board_code: str
    board_name: str
    start_date: str
    end_date: Optional[str] = None
    break_date: Optional[str] = None
    confirm_date: Optional[str] = None
    peak_strength: float = 0.0
    peak_date: str = ""
    status: str = "open"          # open / pending_break / closed
    pending_break_days: int = 0
    last_observation_date: str = ""
    observations: int = 0
    gaps: int = 0
    break_reason: str = ""

    def age_as_of(self, calendar: Sequence[str], as_of: str) -> Optional[int]:
        """含首尾的交易日 session 数：开段当日 age=1（与验证报告口径一致）。"""
        if self.start_date not in calendar or as_of not in calendar:
            return None
        return calendar.index(as_of) - calendar.index(self.start_date) + 1

    def as_dict(self, calendar: Optional[Sequence[str]] = None, as_of: str = "") -> Dict[str, Any]:
        age = self.age_as_of(calendar, as_of) if calendar and as_of else None
        return {
            "episode_id": self.episode_id,
            "theme_id": self.theme_id,
            "board_code": self.board_code,
            "board_name": self.board_name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "break_date": self.break_date,
            "confirm_date": self.confirm_date,
            "peak_strength": round(float(self.peak_strength), 2),
            "peak_date": self.peak_date,
            "status": self.status,
            "pending_break_days": self.pending_break_days,
            "last_observation_date": self.last_observation_date,
            "observations": self.observations,
            "gaps": self.gaps,
            "break_reason": self.break_reason,
            "age_days": age,
        }


@dataclass(frozen=True)
class SetupSignal:
    """交易资格复评申请：armed ≠ buy，必须重走全部闸门。"""

    setup_id: str
    board_code: str
    board_name: str
    theme_id: str
    episode_id: str
    fired_date: str
    setup_type: str
    age_days: int
    fund_state: str                # 观测级：该日是否有任一资金证据（known/unknown）
    status: str                    # armed / unarmed / unknown / invalidated
    variant_fund_state: str = "unknown"   # 变体级：该变体所需资金证据是否可得
    expiry_date: Optional[str] = None
    invalid_reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "board_code": self.board_code,
            "board_name": self.board_name,
            "theme_id": self.theme_id,
            "episode_id": self.episode_id,
            "fired_date": self.fired_date,
            "setup_type": self.setup_type,
            "age_days": self.age_days,
            "fund_state": self.fund_state,
            "variant_fund_state": self.variant_fund_state,
            "status": self.status,
            "expiry_date": self.expiry_date,
            "invalid_reason": self.invalid_reason,
        }


# ── 观测构造 ────────────────────────────────────────────────────────────
def _name_key(name: str) -> str:
    return (name or "").strip()


def build_observations(
    trade_date: str,
    *,
    state_rows: Sequence[Mapping[str, Any]],
    membership: Mapping[str, Mapping[str, bool]],
    fund_map: Optional[Mapping[str, Tuple[Optional[float], Optional[float]]]] = None,
    news_refs: Optional[Mapping[str, Sequence[str]]] = None,
    catalyst_refs: Optional[Mapping[str, Sequence[str]]] = None,
    heat_map: Optional[Mapping[str, int]] = None,
    trend_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
    run_id: str = "",
    bar_complete: bool = True,
) -> Tuple[List[Observation], Dict[str, Any]]:
    """把当日 DB 状态行 + 输出桶成员 + 证据引用组装成观测（纯函数）。

    只对**有稳定 board_code** 的对象落观测；仅有名称的输出桶条目计入
    `unmapped_output_boards`，不猜身份（实施稿 §4.3）。
    """
    fund_map = fund_map or {}
    news_refs = news_refs or {}
    catalyst_refs = catalyst_refs or {}
    heat_map = heat_map or {}
    trend_map = trend_map or {}

    observations: List[Observation] = []
    seen: set[str] = set()
    for row in state_rows:
        code = str(row.get("sector_code") or "").strip()
        if not code:
            continue
        name = _name_key(str(row.get("sector_name") or ""))
        flags = membership.get(code) or membership.get(name) or {}
        fund_1d, fund_3d = fund_map.get(code, fund_map.get(name, (None, None)))
        trend = trend_map.get(code) or trend_map.get(name) or {}
        observations.append(Observation(
            trade_date=trade_date,
            board_code=code,
            board_name=name,
            theme_id=code,
            strength=row.get("strength_score"),
            state=str(row.get("state") or ""),
            in_active_output=bool(flags.get("active")),
            in_ambush_output=bool(flags.get("ambush")),
            in_near_miss_output=bool(flags.get("near_miss")),
            in_top_output=bool(flags.get("top")),
            fund_1d=fund_1d,
            fund_3d=fund_3d,
            fund_status="known" if (fund_1d is not None or fund_3d is not None) else "unknown",
            news_event_ids=tuple(news_refs.get(name, ())),
            catalyst_event_ids=tuple(catalyst_refs.get(name, ())),
            heat_rank=heat_map.get(code, heat_map.get(name)),
            ma20=trend.get("ma20"),
            close=trend.get("close"),
            ret_5d=trend.get("ret_5d"),
            trend_broken=trend.get("trend_broken"),
            trend_status=str(trend.get("status") or "unknown"),
            source="real",
            bar_complete=bar_complete,
            run_id=run_id,
        ))
        seen.add(code)
        seen.add(name)

    unmapped = sorted({
        name for name, flags in membership.items()
        if name and name not in seen and any(flags.values())
    })
    diagnostics = {
        "state_rows": len(state_rows),
        "observations": len(observations),
        "unmapped_output_boards": unmapped[:50],
        "unmapped_output_count": len(unmapped),
        "fund_known": sum(1 for o in observations if o.fund_status == "known"),
        "trend_known": sum(1 for o in observations if o.trend_status == "ok"),
    }
    return observations, diagnostics


# ── 分段 ────────────────────────────────────────────────────────────────
def derive_episodes(
    observations: Sequence[Observation],
    calendar: Sequence[str],
    *,
    enter: float = ENTER_STRENGTH,
    exit_threshold: float = EXIT_STRENGTH,
    confirm_observations: int = CONFIRM_OBSERVATIONS,
) -> Tuple[List[Episode], Dict[Tuple[str, str], Dict[str, Any]]]:
    """状态无关的观察段派生。

    返回 (episodes, 观测状态索引)。索引键 `(board_code, trade_date)`，值为
    `{episode_id, age_days, status, peak_strength}`，供 setup/theme_watch 使用。

    语义：
    - 首次 `strength>=enter` 开段；
    - 首次 `<exit_threshold` 只进 pending-break，段身份不变；
    - 连续 `confirm_observations` 个**真实观测**低于阈值才确认关闭；
    - 缺档日不产生退出确认，但 age 按交易日历继续增长。
    """
    cal_index = {d: i for i, d in enumerate(calendar)}
    by_board: Dict[str, List[Observation]] = {}
    for obs in observations:
        if obs.source != "real":
            continue
        by_board.setdefault(obs.board_code, []).append(obs)

    episodes: List[Episode] = []
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for code, rows in sorted(by_board.items()):
        rows.sort(key=lambda o: (o.trade_date,))
        current: Optional[Episode] = None
        last_seen: Optional[str] = None
        for obs in rows:
            if last_seen is not None and current is not None and obs.trade_date in cal_index and last_seen in cal_index:
                current.gaps += max(0, cal_index[obs.trade_date] - cal_index[last_seen] - 1)
            strength = obs.strength
            if strength is None:
                continue
            if current is None:
                if strength >= enter:
                    current = Episode(
                        episode_id=f"{code}-{obs.trade_date}",
                        theme_id=obs.theme_id,
                        board_code=code,
                        board_name=obs.board_name,
                        start_date=obs.trade_date,
                        peak_strength=float(strength),
                        peak_date=obs.trade_date,
                    )
                    episodes.append(current)
                    current.observations += 1
                    current.last_observation_date = obs.trade_date
                    index[(code, obs.trade_date)] = {
                        "episode_id": current.episode_id,
                        "age_days": 1,
                        "status": "open",
                        "peak_strength": current.peak_strength,
                    }
                last_seen = obs.trade_date
                continue

            current.observations += 1
            current.last_observation_date = obs.trade_date
            if strength > current.peak_strength:
                current.peak_strength = float(strength)
                current.peak_date = obs.trade_date

            if strength < exit_threshold:
                current.pending_break_days += 1
                if current.break_date is None:
                    current.break_date = obs.trade_date
                current.status = "pending_break"
                if current.pending_break_days >= confirm_observations:
                    current.status = "closed"
                    current.confirm_date = obs.trade_date
                    # 段尾 = 破位前最后一个真实观测
                    earlier = [r.trade_date for r in rows if r.trade_date < current.break_date]
                    current.end_date = earlier[-1] if earlier else current.start_date
                    current.break_reason = "drawdown"
                    index[(code, obs.trade_date)] = {
                        "episode_id": current.episode_id, "age_days": None,
                        "status": "closed", "peak_strength": current.peak_strength,
                    }
                    current = None
                    last_seen = obs.trade_date
                    continue
            else:
                if current.pending_break_days:
                    # 回到阈值上方：pending-break 解除，段继续
                    current.pending_break_days = 0
                    current.break_date = None
                    current.status = "open"

            if obs.trade_date not in cal_index or current.start_date not in cal_index:
                # 日期不在日历内（离线混源/DB-only 日期）：不写 age 索引，避免 age 坍缩成 1
                last_seen = obs.trade_date
                continue
            age = cal_index[obs.trade_date] - cal_index[current.start_date] + 1
            index[(code, obs.trade_date)] = {
                "episode_id": current.episode_id,
                "age_days": int(age),
                "status": current.status,
                "peak_strength": current.peak_strength,
            }
            last_seen = obs.trade_date

    return episodes, index


# ── setup ───────────────────────────────────────────────────────────────
def _fund_state(fund_1d: Optional[float], fund_3d: Optional[float]) -> str:
    return "known" if (fund_1d is not None or fund_3d is not None) else "unknown"


def _fund_condition(obs: Observation, variant: str) -> Optional[bool]:
    if variant == "none":
        return True
    values = []
    if variant == "fund_1d":
        values = [obs.fund_1d]
    elif variant == "fund_3d":
        values = [obs.fund_3d]
    elif variant == "fund_any":
        values = [obs.fund_1d, obs.fund_3d]
    else:
        raise ValueError(f"未知 setup 变体: {variant}")
    known = [v for v in values if v is not None]
    if not known:
        return None   # UNKNOWN：不并入 armed/unarmed
    return any(v > 0 for v in known)


def derive_setups(
    observations: Sequence[Observation],
    episode_index: Mapping[Tuple[str, str], Mapping[str, Any]],
    calendar: Sequence[str],
    *,
    variants: Sequence[str] = SETUP_VARIANTS,
    max_age: int = SETUP_MAX_AGE,
    expiry_sessions: int = SETUP_EXPIRY_SESSIONS,
    drawdown_limit: float = SETUP_PEAK_DRAWDOWN_LIMIT,
) -> List[SetupSignal]:
    """四变体 setup：只申请复评资格，不代表可买入。

    - 早段 = 观察段 age<=max_age；
    - 资金缺失 → `unknown`（既不 armed 也不 unarmed）；`fund_state` 是**观测级**
      （该日有无任一资金证据），`variant_fund_state` 是**变体级**（该变体所需字段是否可得），
      两者分开，四变体并排比较时不会因变体不同而翻转观测级状态；
    - 趋势破坏 / 自峰值回撤超限 → `invalidated`，拒绝原因落盘。

    注意 `peak_drawdown` 的语义：峰值取**观察段累计峰值**（episode meta），
    不是当日日内回撤。早段板块若前几日冲高后回落 ≥10%，当日即判 invalidated；
    P2 闸门重走必须沿用同一语义，不得改写成 intraday drawdown。
    """
    cal_index = {d: i for i, d in enumerate(calendar)}
    out: List[SetupSignal] = []
    for obs in observations:
        key = (obs.board_code, obs.trade_date)
        meta = episode_index.get(key)
        if not meta:
            continue
        age = meta.get("age_days")
        if age is None or age > max_age or age < 0:
            continue
        expiry_idx = cal_index.get(obs.trade_date)
        expiry = calendar[expiry_idx + expiry_sessions] if (
            expiry_idx is not None and expiry_idx + expiry_sessions < len(calendar)
        ) else None
        peak = float(meta.get("peak_strength") or 0.0)
        drawdown = ((peak - (obs.strength or peak)) / peak * 100) if peak > 0 else 0.0
        obs_fund_state = _fund_state(obs.fund_1d, obs.fund_3d)
        for variant in variants:
            condition = _fund_condition(obs, variant)
            variant_fund_state = "unknown" if condition is None else "known"
            invalid_reason = ""
            if obs.trend_broken is True:
                invalid_reason = "trend_broken"
            elif drawdown >= drawdown_limit:
                invalid_reason = "peak_drawdown"
            if invalid_reason:
                status = "invalidated"
            elif condition is None:
                status = "unknown"
            elif condition:
                status = "armed"
            else:
                status = "unarmed"
            out.append(SetupSignal(
                setup_id=f"{meta['episode_id']}-{variant}-{obs.trade_date}",
                board_code=obs.board_code,
                board_name=obs.board_name,
                theme_id=obs.theme_id,
                episode_id=str(meta["episode_id"]),
                fired_date=obs.trade_date,
                setup_type=variant,
                age_days=int(age),
                fund_state=obs_fund_state,
                variant_fund_state=variant_fund_state,
                status=status,
                expiry_date=expiry,
                invalid_reason=invalid_reason,
            ))
    return out


# ── 观察集合 ────────────────────────────────────────────────────────────
def build_theme_watch(
    episodes: Sequence[Episode],
    setups: Sequence[SetupSignal],
    observations: Sequence[Observation],
    calendar: Sequence[str],
    as_of: str,
    *,
    capacity: int = THEME_WATCH_CAPACITY,
) -> Dict[str, Any]:
    """独立 observation 集合（自有容量/排序/驱逐留痕）。

    只读 open/pending_break 段；容量截断逐条记录原因，不与 ambush/near-miss/
    蓄水池共享名额（实施稿 §3、§5.2）。
    """
    obs_by_board = {o.board_code: o for o in observations if o.trade_date == as_of}
    setups_by_board: Dict[str, Dict[str, str]] = {}
    for setup in setups:
        if setup.fired_date == as_of:
            setups_by_board.setdefault(setup.board_code, {})[setup.setup_type] = setup.status

    entries = []
    for ep in episodes:
        if ep.status == "closed":
            continue
        obs = obs_by_board.get(ep.board_code)
        latest = obs or next(
            (o for o in reversed(list(observations)) if o.board_code == ep.board_code), None
        )
        age = ep.age_as_of(calendar, as_of)
        entries.append({
            "board_code": ep.board_code,
            "board_name": ep.board_name,
            "theme_id": ep.theme_id,
            "episode_id": ep.episode_id,
            "status": ep.status,
            "age_days": age,
            "peak_strength": round(ep.peak_strength, 2),
            "observations": ep.observations,
            "gaps": ep.gaps,
            "strength": None if latest is None or latest.strength is None else round(latest.strength, 2),
            "state": latest.state if latest else "",
            "in_active_output": bool(latest.in_active_output) if latest else False,
            "in_ambush_output": bool(latest.in_ambush_output) if latest else False,
            "in_near_miss_output": bool(latest.in_near_miss_output) if latest else False,
            "in_top_output": bool(latest.in_top_output) if latest else False,
            "strength_as_of": latest.trade_date if latest else None,
            "setup": setups_by_board.get(ep.board_code, {}),
        })

    entries.sort(key=lambda e: (
        e["age_days"] if e["age_days"] is not None else 10**6,
        -e["peak_strength"],
        e["board_code"],
    ))
    kept = entries[:capacity]
    dropped = [
        {"board_code": e["board_code"], "board_name": e["board_name"], "reason": "capacity"}
        for e in entries[capacity:]
    ]
    return {
        "as_of": as_of,
        "capacity": capacity,
        "observation_count": len(entries),
        "watch_count": len(kept),
        "entries": kept,
        "dropped": dropped,
        "setup_counts": _count_setups(setups, as_of),
    }


def _count_setups(setups: Sequence[SetupSignal], as_of: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for setup in setups:
        if setup.fired_date != as_of:
            continue
        key = f"{setup.setup_type}:{setup.status}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


# ── 快照 ────────────────────────────────────────────────────────────────
def build_snapshot(
    trade_date: str,
    *,
    observations: Sequence[Observation],
    episodes: Sequence[Episode],
    setups: Sequence[SetupSignal],
    watch: Mapping[str, Any],
    calendar: Sequence[str],
    mode: str,
    run_id: str = "",
    diagnostics: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    obs_rows = [o.as_dict() for o in observations if o.trade_date == trade_date]
    version_src = json.dumps(obs_rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "version": LEDGER_VERSION,
        "trade_date": trade_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "run_id": run_id,
        "snapshot_version": hashlib.sha256(version_src.encode("utf-8")).hexdigest()[:16],
        "calendar_len": len(calendar),
        "observations": obs_rows,
        "episodes": [e.as_dict(calendar, trade_date) for e in episodes
                     if e.start_date <= trade_date],
        "setups": [s.as_dict() for s in setups if s.fired_date == trade_date],
        "theme_watch": dict(watch),
        "diagnostics": dict(diagnostics or {}),
        "invariants": {
            "state_used_for_segmentation": False,
            "observation_can_create_buy": False,
            "setup_can_create_buy": False,
            "reuses_ambush_capacity": False,
        },
    }


def snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    canonical = {k: v for k, v in snapshot.items() if k != "generated_at"}
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── 运行时输入适配 ──────────────────────────────────────────────────────
def _item_get(item: Any, *names: str) -> Any:
    """dict / pydantic 双形态取值：归档 JSON 是 dict，运行时是 SectorDetail。"""
    for name in names:
        if isinstance(item, Mapping):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        if value not in (None, ""):
            return value
    return None


def _item_code(item: Any) -> str:
    return str(_item_get(item, "code", "sector_code") or "").strip()


def _item_name(item: Any) -> str:
    return str(_item_get(item, "name", "sector_name", "group_name") or "").strip()


def _item_state(item: Any) -> str:
    return str(_item_get(item, "state", "board_state") or "").strip()


def _iter_items(items: Iterable[Any]) -> Iterable[Any]:
    """展开 MainlineGroup.members（主线组把板块藏在 members 里）。"""
    for item in items or []:
        members = _item_get(item, "members") or []
        if members and not _item_code(item):
            for member in members:
                yield member
        else:
            yield item


def _bucket_codes(items: Iterable[Any]) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for item in _iter_items(items):
        code = _item_code(item)
        name = _item_name(item)
        if code:
            out[code] = True
        if name:
            out[name] = True
    return out


def membership_from_sector_ctx(sector_ctx: Any) -> Dict[str, Dict[str, bool]]:
    if sector_ctx is None:
        return {}
    buckets = {
        "active": ("active_mainline", "exhausted_mainline"),
        "ambush": ("ambush",),
        "near_miss": ("near_miss_ambush",),
        "top": ("top_by_score",),
    }
    membership: Dict[str, Dict[str, bool]] = {}
    for flag, names in buckets.items():
        for field_name in names:
            for key in _bucket_codes(getattr(sector_ctx, field_name, []) or []):
                membership.setdefault(key, {})[flag] = True
    return membership


def _evidence_mapping(evidence: Any, key: str) -> Dict[str, float]:
    """从 CapitalContext.evidence 提取 name→数值（结构容忍 dict/list，失败返回空）。"""
    raw = evidence.get(key) if isinstance(evidence, Mapping) else getattr(evidence, key, None)
    if isinstance(raw, Mapping) and raw.get("sector_flow_all"):
        raw = raw.get("sector_flow_all")
    out: Dict[str, float] = {}
    if isinstance(raw, Mapping):
        for name, value in raw.items():
            try:
                out[str(name)] = float(value)
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, Mapping):
                name = item.get("sector_name") or item.get("name")
                value = item.get("value", item.get("flow"))
                try:
                    if name is not None and value is not None:
                        out[str(name)] = float(value)
                except (TypeError, ValueError):
                    continue
    return out


def fund_map_from_capital(capital_ctx: Any) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """板块资金：`evidence.sector_flow.sector_flow_all`（名称→亿元，正=净流入）。

    单位亿元与符号约定记入快照 diagnostics；setup 只用符号，不做量纲换算。
    """
    if capital_ctx is None:
        return {}
    evidence = getattr(capital_ctx, "evidence", None)
    flow = _evidence_mapping(evidence, "sector_flow")
    all_flows = _evidence_mapping(evidence, "sector_flow_all")
    three_day = _evidence_mapping(evidence, "sector_flow_3d")
    one_day = all_flows or flow
    keys = set(one_day) | set(three_day)
    return {k: (one_day.get(k), three_day.get(k)) for k in keys}


def _event_field(event: Any, name: str) -> Any:
    if isinstance(event, Mapping):
        return event.get(name)
    return getattr(event, name, None)


def event_refs_from_news(news_ctx: Any) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """新闻 / 催化剂事件 → 板块证据引用（按 affected_sectors 名称）。"""
    news: Dict[str, List[str]] = {}
    catalyst: Dict[str, List[str]] = {}
    if news_ctx is None:
        return news, catalyst
    for event in getattr(news_ctx, "major_events", []) or []:
        event_id = _event_field(event, "event_id") or _event_field(event, "id") or ""
        for sector in _event_field(event, "affected_sectors") or []:
            key = str(sector).strip()
            if key and event_id:
                news.setdefault(key, []).append(str(event_id))
    cal = getattr(news_ctx, "catalyst_calendar", None)
    events = getattr(cal, "events", None) if cal is not None else None
    for event in events or []:
        event_id = _event_field(event, "event_id") or ""
        for sector in _event_field(event, "affected_sectors") or []:
            key = str(sector).strip()
            if key and event_id:
                catalyst.setdefault(key, []).append(str(event_id))
    return news, catalyst


def state_rows_from_sector_ctx(sector_ctx: Any) -> List[Dict[str, Any]]:
    """降级路径：DB 未落当日行时，从 SectorContext 还原板块 state/strength（同一计算、同日事实）。

    只覆盖出现在输出桶里的板块（非桶板块在 context 中无稳定 code，不能猜身份）；
    返回行数即覆盖率上限，缺口由 `diagnostics.unmapped_output_count` 与调用方计数披露。
    """
    if sector_ctx is None:
        return []
    strengths = getattr(sector_ctx, "strength_lookup", None) or {}
    fields = (
        "active_mainline", "exhausted_mainline", "ambush", "near_miss_ambush",
        "defensive_rotation", "cooling_down", "top_by_score", "industry_top",
    )
    rows: Dict[str, Dict[str, Any]] = {}
    for field_name in fields:
        for item in _iter_items(getattr(sector_ctx, field_name, []) or []):
            code = _item_code(item)
            name = _item_name(item)
            if not code or not name or code in rows:
                continue
            rows[code] = {
                "sector_code": code,
                "sector_name": name,
                "strength_score": strengths.get(name),
                "state": _item_state(item),
            }
    return sorted(rows.values(), key=lambda r: r["sector_code"])


def trend_map_from_cache(
    codes: Sequence[str],
    *,
    as_of: str,
    cache_path: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """从板指 OHLCV 快照取 MA20/位置/5 日收益；缺失一律 status=unknown（不插值）。"""
    path = cache_path or Path("data/_replay/board_ohlcv_cache.json")
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[theme_ledger] 板指快照读取失败: %s", exc)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for code in codes:
        entry = payload.get(code) or {}
        dates = entry.get("dates") or []
        closes = entry.get("close") or []
        if not dates or not closes or as_of not in dates:
            continue
        idx = dates.index(as_of)
        if idx < 20:
            continue
        window = closes[max(0, idx - 19): idx + 1]
        ma20 = sum(window) / len(window)
        close = float(closes[idx])
        ret_5d = (close / float(closes[idx - 5]) - 1) if idx >= 5 and closes[idx - 5] else None
        out[code] = {
            "ma20": ma20,
            "close": close,
            "ret_5d": ret_5d,
            "trend_broken": bool(ret_5d is not None and ret_5d < -0.03 and close < ma20),
            "status": "ok",
        }
    return out


# ── 存储 ────────────────────────────────────────────────────────────────
class ThemeLedgerStore:
    """SQLite 单写者存储（幂等 upsert；重建可覆盖）。"""

    def __init__(self, db: Any = None):
        if db is None:
            from src.storage import DatabaseManager
            db = DatabaseManager()
        self.db = db

    def persist_day(
        self,
        observations: Sequence[Observation],
        episodes: Sequence[Episode],
        setups: Sequence[SetupSignal],
        trade_date: str,
    ) -> Dict[str, int]:
        from src.storage import ThemeEpisodeRecord, ThemeObservationRecord, ThemeSetupRecord

        day = date.fromisoformat(trade_date)
        session = self.db.get_session()
        written = {"observations": 0, "episodes": 0, "setups": 0}
        try:
            session.query(ThemeObservationRecord).filter(
                ThemeObservationRecord.trade_date == day
            ).delete(synchronize_session=False)
            for obs in observations:
                if obs.trade_date != trade_date:
                    continue
                session.add(ThemeObservationRecord(
                    board_code=obs.board_code,
                    trade_date=day,
                    theme_id=obs.theme_id,
                    board_name=obs.board_name,
                    strength=obs.strength,
                    state=obs.state,
                    in_active_output=obs.in_active_output,
                    in_ambush_output=obs.in_ambush_output,
                    in_near_miss_output=obs.in_near_miss_output,
                    in_top_output=obs.in_top_output,
                    fund_1d=obs.fund_1d,
                    fund_3d=obs.fund_3d,
                    fund_status=obs.fund_status,
                    news_event_ids=list(obs.news_event_ids),
                    catalyst_event_ids=list(obs.catalyst_event_ids),
                    heat_rank=obs.heat_rank,
                    ma20=obs.ma20,
                    close=obs.close,
                    ret_5d=obs.ret_5d,
                    trend_broken=obs.trend_broken,
                    trend_status=obs.trend_status,
                    source=obs.source,
                    bar_complete=obs.bar_complete,
                    run_id=obs.run_id,
                ))
                written["observations"] += 1
            for ep in episodes:
                if ep.start_date > trade_date:
                    continue
                session.merge(ThemeEpisodeRecord(
                    episode_id=ep.episode_id,
                    theme_id=ep.theme_id,
                    board_code=ep.board_code,
                    board_name=ep.board_name,
                    start_date=date.fromisoformat(ep.start_date),
                    end_date=date.fromisoformat(ep.end_date) if ep.end_date else None,
                    break_date=date.fromisoformat(ep.break_date) if ep.break_date else None,
                    confirm_date=date.fromisoformat(ep.confirm_date) if ep.confirm_date else None,
                    peak_strength=ep.peak_strength,
                    peak_date=date.fromisoformat(ep.peak_date) if ep.peak_date else None,
                    status=ep.status,
                    pending_break_days=ep.pending_break_days,
                    last_observation_date=(
                        date.fromisoformat(ep.last_observation_date)
                        if ep.last_observation_date else None
                    ),
                    observations=ep.observations,
                    gaps=ep.gaps,
                    break_reason=ep.break_reason,
                ))
                written["episodes"] += 1
            for setup in setups:
                if setup.fired_date > trade_date:
                    continue
                session.merge(ThemeSetupRecord(
                    setup_id=setup.setup_id,
                    theme_id=setup.theme_id,
                    board_code=setup.board_code,
                    board_name=setup.board_name,
                    episode_id=setup.episode_id,
                    fired_date=date.fromisoformat(setup.fired_date),
                    setup_type=setup.setup_type,
                    age_days=setup.age_days,
                    fund_state=setup.fund_state,
                    variant_fund_state=setup.variant_fund_state,
                    status=setup.status,
                    expiry_date=date.fromisoformat(setup.expiry_date) if setup.expiry_date else None,
                    invalid_reason=setup.invalid_reason,
                ))
                written["setups"] += 1
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return written

    def purge_derived(self) -> Dict[str, int]:
        """清空派生投影（episode/setup）；observation 是事实，保留可重建。"""
        from src.storage import ThemeEpisodeRecord, ThemeSetupRecord

        session = self.db.get_session()
        try:
            episodes = session.query(ThemeEpisodeRecord).delete(synchronize_session=False)
            setups = session.query(ThemeSetupRecord).delete(synchronize_session=False)
            session.commit()
            return {"episodes_deleted": int(episodes or 0), "setups_deleted": int(setups or 0)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load_observations(self, trade_date: str) -> List[Dict[str, Any]]:
        from src.storage import ThemeObservationRecord
        day = date.fromisoformat(trade_date)
        session = self.db.get_session()
        try:
            rows = session.query(ThemeObservationRecord).filter(
                ThemeObservationRecord.trade_date == day
            ).order_by(ThemeObservationRecord.board_code).all()
            return [
                {
                    "trade_date": r.trade_date.isoformat(),
                    "board_code": r.board_code,
                    "board_name": r.board_name,
                    "theme_id": r.theme_id or r.board_code,
                    "strength": r.strength,
                    "state": r.state,
                    "in_active_output": bool(r.in_active_output),
                    "in_ambush_output": bool(r.in_ambush_output),
                    "in_near_miss_output": bool(r.in_near_miss_output),
                    "in_top_output": bool(r.in_top_output),
                    "fund_1d": r.fund_1d,
                    "fund_3d": r.fund_3d,
                    "fund_status": r.fund_status,
                    "news_event_ids": list(r.news_event_ids or []),
                    "catalyst_event_ids": list(r.catalyst_event_ids or []),
                    "heat_rank": r.heat_rank,
                    "ma20": r.ma20,
                    "close": r.close,
                    "ret_5d": r.ret_5d,
                    "trend_broken": r.trend_broken,
                    "trend_status": r.trend_status,
                    "source": r.source,
                    "bar_complete": bool(r.bar_complete),
                    "run_id": r.run_id,
                }
                for r in rows
            ]
        finally:
            session.close()


# ── 运行时入口 ──────────────────────────────────────────────────────────
def get_mode() -> str:
    mode = os.getenv("GOT_THEME_LEDGER", DEFAULT_MODE).strip().lower()
    return mode if mode in MODES else DEFAULT_MODE


def snapshot_path(trade_date: date, base_dir: Optional[Path] = None) -> Path:
    stamp = trade_date.strftime("%Y%m%d")
    root = Path(base_dir) if base_dir is not None else Path("data") / "got_daily"
    return root / stamp / "theme_ledger.json"


def load_calendar(
    start: Optional[str] = None,
    end: Optional[str] = None,
    *,
    db: Any = None,
    base_dir: Optional[Path] = None,
) -> List[str]:
    """交易日历：DB 内 Node 2 追踪日 ∪ 归档目录日（ISO，升序）。

    `db`/`base_dir` 仅用于测试隔离（生产缺省走 DatabaseManager 与 data/got_daily）。
    """
    days: set[str] = set()
    try:
        from src.storage import DatabaseManager
        from sqlalchemy import text
        session = (db or DatabaseManager()).get_session()
        try:
            rows = session.execute(text(
                "SELECT DISTINCT tracking_date FROM node2_sector_trends"
            )).fetchall()
            for (value,) in rows:
                if value is None:
                    continue
                days.add(value.isoformat() if hasattr(value, "isoformat") else str(value)[:10])
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[theme_ledger] 日历读取失败（DB）: %s", exc)
    archive = Path(base_dir) if base_dir is not None else Path("data") / "got_daily"
    if archive.exists():
        for path in archive.iterdir():
            if path.is_dir() and len(path.name) == 8 and path.name.isdigit():
                days.add(f"{path.name[:4]}-{path.name[4:6]}-{path.name[6:]}")
    out = sorted(d for d in days if (not start or d >= start) and (not end or d <= end))
    return out


def build_and_persist(
    trade_date: date,
    *,
    sector_ctx: Any = None,
    capital_ctx: Any = None,
    news_ctx: Any = None,
    run_id: str = "",
    bar_complete: bool = True,
    persist: bool = True,
    db: Any = None,
    base_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """运行时 builder：Step 2.6 调用；任何异常由调用方 fail-open 忽略。

    `state_rows` 取 DB 中 Node 2 当日已落的行（Node 2 在本步骤之前完成）；
    `membership` 取 SectorContext 输出桶；资金/新闻/趋势缺失如实标 unknown。
    """
    from src.storage import DatabaseManager, Node2SectorTrendRecord

    day = trade_date.isoformat()
    ledger_db = db or DatabaseManager()
    session = ledger_db.get_session()
    try:
        rows = session.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.tracking_date == trade_date
        ).all()
        state_rows = [
            {
                "sector_code": r.sector_code,
                "sector_name": r.sector_name,
                "strength_score": r.strength_score,
                "state": r.state,
            }
            for r in rows
        ]
    finally:
        session.close()

    state_source = "db"
    if not state_rows:
        state_rows = state_rows_from_sector_ctx(sector_ctx)
        state_source = "sector_context" if state_rows else "empty"

    membership = membership_from_sector_ctx(sector_ctx)
    fund_map = fund_map_from_capital(capital_ctx)
    news_refs, catalyst_refs = event_refs_from_news(news_ctx)

    calendar = load_calendar(db=ledger_db, base_dir=base_dir)
    if day not in calendar:
        calendar = sorted(set(calendar) | {day})

    # 趋势字段：只为已开段板块取（限量），缺失 status=unknown
    open_codes: List[str] = []
    prev = load_snapshot(trade_date, offset_days=1, calendar=calendar, base_dir=base_dir)
    for ep in (prev or {}).get("episodes", []) or []:
        if ep.get("status") != "closed":
            open_codes.append(str(ep.get("board_code")))
    trend_map = trend_map_from_cache(open_codes, as_of=day) if open_codes else {}

    observations, diagnostics = build_observations(
        day,
        state_rows=state_rows,
        membership=membership,
        fund_map=fund_map,
        news_refs=news_refs,
        catalyst_refs=catalyst_refs,
        trend_map=trend_map,
        run_id=run_id,
        bar_complete=bar_complete,
    )
    diagnostics["state_source"] = state_source
    diagnostics["db_state_rows"] = 0 if state_source != "db" else diagnostics.get("state_rows", 0)
    history = _load_observation_history(day, calendar, observations, db=ledger_db)
    diagnostics["history_observations"] = len(history)
    episodes, index = derive_episodes(history, calendar)
    setups = derive_setups(history, index, calendar)
    watch = build_theme_watch(episodes, setups, history, calendar, day)
    snapshot = build_snapshot(
        day,
        observations=history,
        episodes=episodes,
        setups=setups,
        watch=watch,
        calendar=calendar,
        mode=get_mode(),
        run_id=run_id,
        diagnostics=diagnostics,
    )
    if persist:
        ThemeLedgerStore(db=ledger_db).persist_day(history, episodes, setups, day)
        path = snapshot_path(trade_date, base_dir=base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def _load_observation_history(
    day: str,
    calendar: Sequence[str],
    today: Sequence[Observation],
    *,
    lookback_sessions: int = 250,
    db: Any = None,
) -> List[Observation]:
    """加载 as-of 历史观测（只读 <= day），保证分段是「当时事实」的重建。"""
    if day not in calendar:
        return list(today)
    idx = calendar.index(day)
    start = calendar[max(0, idx - lookback_sessions)]
    try:
        from src.storage import DatabaseManager, ThemeEpisodeRecord

        _session = (db or DatabaseManager()).get_session()
        try:
            earliest = _session.query(ThemeEpisodeRecord.start_date).filter(
                ThemeEpisodeRecord.status != "closed",
                ThemeEpisodeRecord.start_date <= date.fromisoformat(day),
            ).order_by(ThemeEpisodeRecord.start_date).first()
            if earliest and earliest[0] and earliest[0].isoformat() < start:
                start = earliest[0].isoformat()
        finally:
            _session.close()
    except Exception:  # noqa: BLE001 - 读取失败按固定窗口降级
        pass
    history: List[Observation] = []
    try:
        from src.storage import DatabaseManager
        from src.storage import ThemeObservationRecord
        session = (db or DatabaseManager()).get_session()
        try:
            rows = session.query(ThemeObservationRecord).filter(
                ThemeObservationRecord.trade_date <= date.fromisoformat(day),
                ThemeObservationRecord.trade_date >= date.fromisoformat(start),
            ).order_by(ThemeObservationRecord.trade_date, ThemeObservationRecord.board_code).all()
            history = [_obs_from_record(r) for r in rows]
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[theme_ledger] 历史观测读取失败: %s", exc)
    today_codes = {o.board_code for o in today}
    history = [o for o in history if not (o.trade_date == day and o.board_code in today_codes)]
    history.extend(today)
    history.sort(key=lambda o: (o.trade_date, o.board_code))
    return history


def _obs_from_record(record: Any) -> Observation:
    return Observation(
        trade_date=record.trade_date.isoformat(),
        board_code=record.board_code,
        board_name=record.board_name,
        theme_id=record.theme_id or record.board_code,
        strength=record.strength,
        state=record.state or "",
        in_active_output=bool(record.in_active_output),
        in_ambush_output=bool(record.in_ambush_output),
        in_near_miss_output=bool(record.in_near_miss_output),
        in_top_output=bool(record.in_top_output),
        fund_1d=record.fund_1d,
        fund_3d=record.fund_3d,
        fund_status=record.fund_status or "unknown",
        ma20=record.ma20,
        close=record.close,
        ret_5d=record.ret_5d,
        trend_broken=record.trend_broken,
        trend_status=record.trend_status or "unknown",
        source=record.source or "real",
        bar_complete=bool(record.bar_complete),
        run_id=record.run_id or "",
    )


WATCH_SHADOW_MAX_ENTRIES = 10


def watch_shadow_payload(snapshot: Mapping[str, Any], *, max_entries: int = WATCH_SHADOW_MAX_ENTRIES) -> Dict[str, Any]:
    """账本快照 → UnifiedContext 紧凑投影（只读；禁止任何交易字段）。"""
    watch = snapshot.get("theme_watch") or {}
    entries = []
    for item in (watch.get("entries") or [])[:max_entries]:
        entries.append({
            "board_code": item.get("board_code"),
            "board_name": item.get("board_name"),
            "episode_id": item.get("episode_id"),
            "age_days": item.get("age_days"),
            "status": item.get("status"),
            "peak_strength": item.get("peak_strength"),
            "strength": item.get("strength"),
            "state": item.get("state"),
            "in_active_output": bool(item.get("in_active_output")),
            "in_ambush_output": bool(item.get("in_ambush_output")),
            "in_near_miss_output": bool(item.get("in_near_miss_output")),
            "in_top_output": bool(item.get("in_top_output")),
            "setup": item.get("setup") or {},
        })
    return {
        "as_of": snapshot.get("trade_date"),
        "mode": snapshot.get("mode"),
        "observation_count": watch.get("observation_count", 0),
        "watch_count": watch.get("watch_count", 0),
        "dropped_count": len(watch.get("dropped") or []),
        "entries": entries,
        "source": "theme_ledger.json",
        "not_a_trade_signal": True,
    }


def attach_theme_watch_shadow(
    unified: Any,
    trade_date: date,
    *,
    base_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """把当日账本快照投影挂到 UnifiedContext（trace-only，fail-open）。

    开关 off 或快照缺失 → 返回 None 且不改动 unified；任何异常都不得阻断 Node 6。
    """
    if get_mode() == "off":
        return None
    try:
        snapshot = load_snapshot(trade_date, base_dir=base_dir)
        if not snapshot:
            return None
        payload = watch_shadow_payload(snapshot)
        unified.theme_watch_shadow = payload
        return payload
    except Exception as exc:  # noqa: BLE001 - 观察层失败不得影响聚合
        logger.warning("[theme_ledger] 观察提醒挂载失败（已忽略）: %s", exc)
        return None


def load_snapshot(
    trade_date: date,
    *,
    offset_days: int = 0,
    calendar: Optional[Sequence[str]] = None,
    base_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """读 `<= trade_date` 的最近一份快照（默认读 T 当日；offset_days=1 读 T-1）。"""
    cal = list(calendar or load_calendar())
    day = trade_date.isoformat()
    if day not in cal:
        cal = sorted(set(cal) | {day})
    idx = cal.index(day) - max(0, offset_days)
    for candidate in reversed(cal[: idx + 1]):
        path = snapshot_path(date.fromisoformat(candidate), base_dir=base_dir)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[theme_ledger] 快照损坏 %s: %s", path, exc)
                return None
    return None
