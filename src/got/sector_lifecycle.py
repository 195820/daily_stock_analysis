"""Node 2 连续行情相位状态机（既有 P2 实施稿的前置底座，2026-11-05）。

与 `node2_sector_trends.state` 的分工（两套对象，禁止互相顶替）：

- `LifecycleState.phase` = **行情相位**，全量板块可用：
  `dormant / launch / expansion / mature / decline`。锚点（anchor_date）= 趋势启动日，
  跨旧 state 字符串（ambush → active_mainline → high_position_cooling）继承，
  只有「硬破位」或「连续 3 个真实弱观测」才终止；日内 RRG 变化只改风险状态。
- `state` = **交易/风险状态**（`ambush / active_mainline / high_position_cooling /
  exhausted_mainline / defensive_rotation / neutral`），每日由
  `SectorAnalysisNode._apply_lifecycle_state_machine` 从相位 + 当日风险证据重新映射。

为什么不复用既有计数器：`theme_ledger` 的 80/75 episode、`mainline_days`（处于主线风险
状态的天数）、`wave_days`（波次回撤结构）语义都不是「从启动趋势开始的行情年龄」，
改名复用会把风险状态天数当成行情年龄，故新建本模块。

时间权重只在此处定义一次（`lifecycle_weight`）；Node 6 与 Phase 2 不再另乘
`_wave_coeff` / 旧 phase ±分，避免同一板块年龄被重复降权。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Sequence

# =============================================================================
# 相位与边界（v1 口径，测试显式冻结，不通过环境变量临时调参）
# =============================================================================

PHASE_DORMANT = "dormant"
PHASE_LAUNCH = "launch"
PHASE_EXPANSION = "expansion"
PHASE_MATURE = "mature"
PHASE_DECLINE = "decline"

LAUNCH_END = 3            # launch: 1–3 日
EXPANSION_END = 10        # expansion: 4–10 日
MATURE_FLOOR = 11         # mature: ≥11 日（不设年龄硬死线，只按权重衰减）

# 启动定义（2026-11-10 起为 P1 口径，来源 `scripts/search_node2_launch_params.py`）：
# P1 = 站上 MA20 ∧ 距 20 日高 −10%~−5% ∧ 5 日动量 2%~5% ∧ vol20 截面分位 top30%。
# 旧结构口径（站上 MA20 + MA20 上行 + 5/10 日同向）在生产从未输出：581 日事件研究
# day1 T+5 超额 −0.12pp（t=−1.16）、5 日组合 −0.29pp/日（t=−0.93）。P1 的多重检验
# 存活情况见搜索脚本的 BH-FDR 节；未存活时按用户裁决先接线并归档标注。
LAUNCH_DIST_MIN = -0.10           # 距 20 日高下限（更远 = 远离结构位）
LAUNCH_DIST_MAX = -0.05           # 距 20 日高上限（更近 = 追高）
LAUNCH_R5_MIN = 0.02              # 5 日动量下限
LAUNCH_R5_MAX = 0.05              # 5 日动量上限（更高 = 已启动/追涨）
LAUNCH_VOL20_PCT_MIN = 0.70       # vol20 截面分位下限（0–1）

MA20_SLOPE_LOOKBACK = 5           # MA20 斜率比较窗口（交易日）
DECLINE_WEAK_DAYS = 3             # 连续弱观测确认 → decline
DECLINE_COOLDOWN_OBS = 2          # decline 后至少 2 个观测日才允许重新启动

# 时间权重系数（分段线性）
EXPANSION_WEIGHT_STEP = 0.02
MATURE_WEIGHT_BASE = 0.85
MATURE_WEIGHT_STEP = 0.03
MATURE_WEIGHT_FLOOR = 0.50
DECLINE_WEIGHT = 0.50

# legacy bootstrap：只有这三个旧 state 具备「已在行情中」的语义
LEGACY_MAINLINE_STATES = frozenset(
    {"active_mainline", "high_position_cooling", "exhausted_mainline"}
)

# transition_reason 受控词表（归档比对用，禁止自由文本）
REASON_MISSING_BAR = "missing_bar"
REASON_HARD_BREAK = "hard_break"
REASON_LEGACY_BOOTSTRAP = "legacy_bootstrap"
REASON_LAUNCH_START = "launch_start"
REASON_WEAK_HOLD = "weak_hold"
REASON_WEAK_CONFIRM_DECLINE = "weak_confirm_decline"
REASON_PHASE_ADVANCE = "phase_advance"
REASON_REENTRY = "reentry"
REASON_BOOTSTRAP_UNKNOWN = "bootstrap_unknown"

REASONS = frozenset({
    REASON_MISSING_BAR, REASON_HARD_BREAK, REASON_LEGACY_BOOTSTRAP,
    REASON_LAUNCH_START, REASON_WEAK_HOLD, REASON_WEAK_CONFIRM_DECLINE,
    REASON_PHASE_ADVANCE, REASON_REENTRY, REASON_BOOTSTRAP_UNKNOWN,
})

# 连续相位 → 下游既有 phase 词表（只做这一层适配，下游不得再读原始新相位）
_ADAPTED_PHASE_MAP = {
    PHASE_LAUNCH: "launch",
    PHASE_EXPANSION: "acceleration",
    PHASE_MATURE: "mature",
    PHASE_DECLINE: "mature_exhausted",
    PHASE_DORMANT: None,
}

_VALID_MODES = ("legacy", "enabled")


def get_lifecycle_mode() -> str:
    """Node 2 生命周期模式开关：`legacy`（缺省）/ `enabled`。

    环境变量 `GOT_NODE2_LIFECYCLE`；非法值（含离线回测专用的 `ab` 标记）一律回退
    `legacy`——生产进程只认这两个值，不新增 Node2 shadow/`ab` 生产态。
    """
    mode = (os.getenv("GOT_NODE2_LIFECYCLE", "legacy") or "").strip().lower()
    return mode if mode in _VALID_MODES else "legacy"


def map_lifecycle_phase(phase: Optional[str]) -> Optional[str]:
    """连续相位 → 下游既有 `LifecycleInfo.phase` 词表。

    `launch→launch`、`expansion→acceleration`、`mature→mature`、
    `decline→mature_exhausted`、`dormant→None`（不产出可交易生命周期）。
    """
    if phase is None:
        return None
    return _ADAPTED_PHASE_MAP.get(phase)


# =============================================================================
# 数据对象
# =============================================================================

@dataclass(frozen=True)
class LifecycleEvidence:
    """单日相位证据（不可变；全部来自当日已算出的标量，不重复检测）。"""

    trade_date: date
    bar_complete: bool
    r5: Optional[float] = None
    r10: Optional[float] = None
    r5_pct: Optional[float] = None
    above_ma20: Optional[bool] = None
    ma20_slope: Optional[float] = None   # MA20 5 日斜率（ma20 / ma20[-5] - 1）
    high20_dist: Optional[float] = None
    high20_pos: Optional[float] = None      # 与 _compute_break_signals 同口径
    vol20_pct: Optional[float] = None       # 20 日收益波动率的截面分位（0–1）
    strength: Optional[float] = None
    close: Optional[float] = None
    rrg_quadrant: Optional[str] = None
    overheat_level: str = "normal"
    trend_broken: Optional[bool] = None
    launch_score: Optional[float] = None    # 输入不完整时 None（不按 0 分拒绝已有段）
    score_drop: Optional[float] = None
    is_defensive: bool = False
    legacy_state: Optional[str] = None             # 只读 bootstrap 输入
    legacy_wave_start: Optional[date] = None
    legacy_mainline_days: Optional[int] = None


@dataclass(frozen=True)
class LifecycleState:
    """连续相位状态（不可变；持久化列见 `node2_sector_trends.lifecycle_*`）。"""

    phase: str
    anchor_date: Optional[date]
    phase_age: Optional[int]    # 锚日含当日=1，按交易日历
    phase_dwell: int            # 当前相位内真实观测数（缺档不推进）
    healthy_days: int
    weak_days: int
    peak_close: Optional[float]
    last_observation_date: Optional[date]
    transition_reason: str
    data_status: str            # ok / unknown


def dormant_state(reason: str = REASON_WEAK_HOLD, data_status: str = "ok") -> LifecycleState:
    """无段状态（未启动或已熄灭）。"""
    return LifecycleState(
        phase=PHASE_DORMANT,
        anchor_date=None,
        phase_age=None,
        phase_dwell=0,
        healthy_days=0,
        weak_days=0,
        peak_close=None,
        last_observation_date=None,
        transition_reason=reason,
        data_status=data_status,
    )


# =============================================================================
# 纯函数：破位 / 启动评分 / 启动条件
# =============================================================================

def is_trend_broken(
    r5: Optional[float],
    above_ma20: Optional[bool],
    high20_pos: Optional[float],
    overheat_level: Optional[str],
    score_drop: Optional[float],
) -> Optional[bool]:
    """趋势破坏判据（legacy `_apply_state_machine` 的 broken OR 链同口径）。

    `r5<=0 or above_ma20 is False or (exhausted 且 high20_pos<0.60) or score_drop>=20`。
    任一条件成立 → True；否则任一必要字段缺失 → None（不算退出）；全齐且不成立 → False。
    """
    if (r5 is not None and r5 <= 0) \
            or (above_ma20 is False) \
            or (overheat_level == "exhausted" and high20_pos is not None and high20_pos < 0.60) \
            or (score_drop is not None and score_drop >= 20):
        return True
    if r5 is None or above_ma20 is None or high20_pos is None \
            or overheat_level is None or score_drop is None:
        return None
    return False


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_launch_score(
    r5_pct: Optional[float],
    r10: Optional[float],
    high20_dist: Optional[float],
) -> Optional[float]:
    """启动诊断分（容量排序用，不另造收益因子）。

    `0.5*r5_pct + 0.25*clip((r10+.10)/.20) + 0.25*clip((high20_dist+.02)/.02)`，
    任一输入为 None → None。
    """
    if r5_pct is None or r10 is None or high20_dist is None:
        return None
    score = (
        0.5 * _clip01(r5_pct)
        + 0.25 * _clip01((r10 + 0.10) / 0.20)
        + 0.25 * _clip01((high20_dist + 0.02) / 0.02)
    )
    return round(_clip01(score), 4)


def can_start(evidence: LifecycleEvidence) -> bool:
    """启动条件 = P1：站上 MA20 ∧ 距高 −10%~−5% ∧ r5 2%~5% ∧ vol20 分位 top30%。

    与「健康延续」（`_is_healthy`）分离：延续只要求结构向上，不看距高/波动分位。
    任一必要字段缺失 → 不启动（不把缺数据读成启动信号）。
    """
    if not evidence.bar_complete:
        return False
    if evidence.trend_broken is not False:
        return False
    if evidence.overheat_level == "exhausted":
        return False
    if evidence.above_ma20 is not True:
        return False
    if evidence.r5 is None or not (LAUNCH_R5_MIN <= evidence.r5 <= LAUNCH_R5_MAX):
        return False
    if evidence.high20_dist is None or not (
        LAUNCH_DIST_MIN <= evidence.high20_dist <= LAUNCH_DIST_MAX
    ):
        return False
    if evidence.vol20_pct is None or evidence.vol20_pct < LAUNCH_VOL20_PCT_MIN:
        return False
    return True


def _is_healthy(evidence: LifecycleEvidence) -> bool:
    """健康延续：站上 MA20 + 5/10 日向上 + MA20 未走平 + 未过热衰竭。

    不再约束距 20 日高——距高是拟合型阈值，见文件头注释。
    """
    return bool(
        evidence.above_ma20 is True
        and evidence.r5 is not None and evidence.r5 > 0
        and evidence.r10 is not None and evidence.r10 > 0
        and evidence.ma20_slope is not None and evidence.ma20_slope > 0
        and evidence.overheat_level != "exhausted"
    )


def _phase_for_age(age: int) -> str:
    if age <= LAUNCH_END:
        return PHASE_LAUNCH
    if age <= EXPANSION_END:
        return PHASE_EXPANSION
    return PHASE_MATURE


# =============================================================================
# 日历/年龄
# =============================================================================

def _calendar_index(calendar: Optional[Sequence[date]]) -> dict[date, int]:
    if not calendar:
        return {}
    return {d: i for i, d in enumerate(calendar)}


def _phase_age(anchor: Optional[date], trade_date: date, index: dict[date, int]) -> Optional[int]:
    """`phase_age = index(trade_date) - index(anchor) + 1`（与观测是否缺档无关）。"""
    if anchor is None or trade_date not in index or anchor not in index:
        return None
    return index[trade_date] - index[anchor] + 1


def _anchor_from_age(age: int, trade_date: date, calendar: Sequence[date]) -> Optional[date]:
    """按年龄反算锚日（bootstrap 用）；日历不足时钳到最早一日。"""
    idx = _calendar_index(calendar)
    if trade_date not in idx:
        return None
    pos = idx[trade_date] - (max(1, age) - 1)
    if pos < 0:
        pos = 0
    return calendar[pos]


def _bootstrap_age(evidence: LifecycleEvidence, calendar: Sequence[date]) -> tuple[int, Optional[date]]:
    """legacy bootstrap 年龄：wave_start 到当日的交易日数，缺失时用 mainline_days 兜底。

    Returns: (age, adjusted_anchor)。age 至少 1。
    """
    index = _calendar_index(calendar)
    trade_date = evidence.trade_date
    if evidence.legacy_wave_start is not None and trade_date in index:
        age = _phase_age(evidence.legacy_wave_start, trade_date, index)
        if age is not None and age >= 1:
            return age, evidence.legacy_wave_start
    fallback = max(int(evidence.legacy_mainline_days or 0), MATURE_FLOOR)
    return fallback, _anchor_from_age(fallback, trade_date, calendar)


# =============================================================================
# 状态转移
# =============================================================================

def transition_lifecycle(
    previous: Optional[LifecycleState],
    evidence: LifecycleEvidence,
    calendar: Sequence[date],
) -> LifecycleState:
    """单板块单日相位转移（纯函数，同日重放逐字段相同）。

    优先级：无 bar → legacy bootstrap（仅 previous 为空）→ 硬破位 → 启动/冷却 → 健康推进。
    """
    cal = list(calendar or [])
    index = _calendar_index(cal)
    trade_date = evidence.trade_date
    in_calendar = trade_date in index

    # ── 1) 无有效当日 bar：保持，只推进日历年龄 ──
    if not evidence.bar_complete:
        if previous is None:
            return dormant_state(REASON_BOOTSTRAP_UNKNOWN, data_status="unknown")
        return replace(
            previous,
            phase_age=_phase_age(previous.anchor_date, trade_date, index),
            transition_reason=REASON_MISSING_BAR,
            data_status="unknown",
        )

    prev_phase = previous.phase if previous is not None else PHASE_DORMANT

    # ── 2) 无新相位历史的 legacy bootstrap（先于硬破位：首次启用要能从旧状态恢复锚日）──
    # 只有 `previous is None` 才走；已有新相位历史时一律以硬破位/弱确认为准。
    # 旧状态 exhausted_mainline → phase=decline（healthy=0/weak=0），与硬破位同终态，
    # 但保留了 wave_start 锚日，使首日被路由的板块也有 LifecycleInfo。
    if previous is None and evidence.legacy_state in LEGACY_MAINLINE_STATES:
        age, anchor = _bootstrap_age(evidence, cal)
        if evidence.trend_broken is True or evidence.legacy_state == "exhausted_mainline":
            # 首日即破位：保留 bootstrap 锚日，但相位必须是 decline（不能因旧状态是
            # active_mainline 就当成健康中段）
            phase = PHASE_DECLINE
            healthy, weak = 0, 0
        else:
            phase = _phase_for_age(age)
            if evidence.legacy_state == "high_position_cooling":
                if phase == PHASE_LAUNCH:
                    phase = PHASE_EXPANSION
                age = max(age, 4)
                anchor = _anchor_from_age(age, trade_date, cal)
                healthy, weak = 0, 1
            else:
                healthy, weak = 1, 0
        return LifecycleState(
            phase=phase,
            anchor_date=anchor,
            phase_age=age,
            phase_dwell=1,
            healthy_days=healthy,
            weak_days=weak,
            peak_close=evidence.close,
            last_observation_date=trade_date,
            transition_reason=REASON_LEGACY_BOOTSTRAP,
            data_status="ok" if in_calendar else "unknown",
        )

    # ── 3) 硬破位优先（过热/拥挤不豁免年龄，也不因年龄豁免风险）──
    if evidence.trend_broken is True:
        anchor = previous.anchor_date if previous is not None else None
        peak = previous.peak_close if previous is not None else evidence.close
        age = _phase_age(anchor, trade_date, index)
        return LifecycleState(
            phase=PHASE_DECLINE,
            anchor_date=anchor,
            phase_age=age,
            phase_dwell=1,
            healthy_days=0,
            weak_days=0,
            peak_close=peak,
            last_observation_date=trade_date,
            transition_reason=REASON_HARD_BREAK,
            data_status="ok" if (age is not None or anchor is None) else "unknown",
        )

    launch_ok = can_start(evidence)

    # ── 4) 无段启动 / decline 冷却 / dormant 保持 ──
    if previous is None or prev_phase == PHASE_DORMANT:
        if launch_ok:
            return LifecycleState(
                phase=PHASE_LAUNCH,
                anchor_date=trade_date,
                phase_age=1,
                phase_dwell=1,
                healthy_days=1,
                weak_days=0,
                peak_close=evidence.close,
                last_observation_date=trade_date,
                transition_reason=REASON_LAUNCH_START,
                data_status="ok" if in_calendar else "unknown",
            )
        prev_healthy = previous.healthy_days if previous is not None else 0
        prev_weak = previous.weak_days if previous is not None else 0
        return LifecycleState(
            phase=PHASE_DORMANT,
            anchor_date=None,
            phase_age=None,
            phase_dwell=0,
            healthy_days=prev_healthy,
            weak_days=prev_weak,
            peak_close=previous.peak_close if previous is not None else None,
            last_observation_date=(
                previous.last_observation_date if previous is not None else None
            ),
            transition_reason=REASON_WEAK_HOLD,
            data_status="ok" if in_calendar else "unknown",
        )

    if prev_phase == PHASE_DECLINE:
        if previous.phase_dwell >= DECLINE_COOLDOWN_OBS and launch_ok:
            return LifecycleState(
                phase=PHASE_LAUNCH,
                anchor_date=trade_date,
                phase_age=1,
                phase_dwell=1,
                healthy_days=1,
                weak_days=0,
                peak_close=evidence.close,
                last_observation_date=trade_date,
                transition_reason=REASON_REENTRY,
                data_status="ok" if in_calendar else "unknown",
            )
        # 冷却不足或条件不成立：保持 decline，累计观测
        age = _phase_age(previous.anchor_date, trade_date, index)
        return replace(
            previous,
            phase=PHASE_DECLINE,
            phase_age=age,
            phase_dwell=previous.phase_dwell + 1,
            last_observation_date=trade_date,
            transition_reason=REASON_WEAK_HOLD,
            data_status="ok" if (age is not None or previous.anchor_date is None) else "unknown",
        )

    # ── 5) 已开段（launch/expansion/mature）健康推进 ──
    healthy_today = _is_healthy(evidence)
    if healthy_today:
        healthy_days = previous.healthy_days + 1
        weak_days = 0
    else:
        weak_days = previous.weak_days + 1
        healthy_days = 0

    peak = previous.peak_close
    if evidence.close is not None:
        peak = evidence.close if peak is None else max(peak, evidence.close)

    age = _phase_age(previous.anchor_date, trade_date, index)
    if age is None and previous.anchor_date is not None:
        # 锚日/当日不在同一日历（历史缺档）：不猜 anchor，标记 unknown
        data_status = "unknown"
    else:
        data_status = "ok"

    # ── 6) 相位推进 / 连续弱确认 ──
    if weak_days >= DECLINE_WEAK_DAYS:
        phase = PHASE_DECLINE
        reason = REASON_WEAK_CONFIRM_DECLINE
    elif healthy_today:
        phase = _phase_for_age(age if age is not None else previous.phase_age or 1)
        reason = REASON_PHASE_ADVANCE
    else:
        # 单日/双日弱信号：保持相位，不按年龄晋级（避免横盘一天就重建行情）
        phase = prev_phase
        reason = REASON_WEAK_HOLD

    phase_dwell = 1 if phase != prev_phase else previous.phase_dwell + 1

    return LifecycleState(
        phase=phase,
        anchor_date=previous.anchor_date,
        phase_age=age,
        phase_dwell=phase_dwell,
        healthy_days=healthy_days,
        weak_days=weak_days,
        peak_close=peak,
        last_observation_date=trade_date,
        transition_reason=reason,
        data_status=data_status,
    )


def phase_age_inputs(
    previous: Optional[LifecycleState],
    evidence: LifecycleEvidence,
    calendar: Sequence[date],
) -> tuple[str, Optional[int]]:
    """过热 D8 的时间输入：(previous 连续相位, 当日年龄)。

    年龄只由 anchor 与交易日历决定（首次启用时用 legacy wave_start / mainline_days 兜底），
    与当日是否健康无关——时间输入不得反过来依赖当日风险判定，否则循环
    （`overheat` 是 `break_signals` 的输入，`break_signals` 是相位转移的输入）。
    """
    if previous is None and evidence.bar_complete \
            and evidence.legacy_state in LEGACY_MAINLINE_STATES:
        age, _anchor = _bootstrap_age(evidence, calendar)
        if evidence.legacy_state == "exhausted_mainline":
            return PHASE_DECLINE, age
        phase = _phase_for_age(age)
        if evidence.legacy_state == "high_position_cooling":
            phase = PHASE_EXPANSION if phase == PHASE_LAUNCH else phase
            age = max(age, 4)
        return phase, age
    if previous is None:
        return PHASE_DORMANT, None
    age = _phase_age(previous.anchor_date, evidence.trade_date, _calendar_index(calendar))
    if age is None:
        # 日历不可定位：不猜 anchor，沿用上一观测年龄（state 会标 data_status=unknown）
        age = previous.phase_age
    return previous.phase, age


# =============================================================================
# 时间权重
# =============================================================================
def lifecycle_weight(state: Optional[LifecycleState]) -> float:
    """相位时间权重（代入前把 phase_age 钳到相位下限，绝不产生 >1.00 的权重）。

    launch 1.00 / expansion 1.00-0.02×(age-4) / mature max(0.50, 0.85-0.03×(age-11)) /
    decline 0.50（仅诊断排序，风险状态仍禁止新开仓）/ dormant 或 unknown 1.00（缺失不算利空）。
    """
    if state is None:
        return 1.0
    if state.data_status == "unknown":
        return 1.0
    phase = state.phase
    age = state.phase_age
    if phase == PHASE_LAUNCH:
        return 1.0
    if phase == PHASE_EXPANSION:
        a = max(4, age if age is not None else 4)
        return round(max(0.0, min(1.0, 1.00 - EXPANSION_WEIGHT_STEP * (a - 4))), 4)
    if phase == PHASE_MATURE:
        a = max(MATURE_FLOOR, age if age is not None else MATURE_FLOOR)
        return round(
            max(MATURE_WEIGHT_FLOOR, min(1.0, MATURE_WEIGHT_BASE - MATURE_WEIGHT_STEP * (a - MATURE_FLOOR))),
            4,
        )
    if phase == PHASE_DECLINE:
        return DECLINE_WEIGHT
    return 1.0


# =============================================================================
# 持久化读取
# =============================================================================

def lifecycle_state_from_prev_trend(prev: Optional[dict]) -> Optional[LifecycleState]:
    """从 `prev_trend` 记录构造 `LifecycleState`。

    只有 `lifecycle_anchor_date / lifecycle_phase_age / lifecycle_phase_dwell` 三者
    均非空才构造；否则视为首次启用（返回 None → 走 legacy bootstrap）。
    """
    if not prev:
        return None
    anchor = prev.get("lifecycle_anchor_date")
    age = prev.get("lifecycle_phase_age")
    dwell = prev.get("lifecycle_phase_dwell")
    phase = prev.get("lifecycle_continuous_phase")
    if anchor is None or age is None or dwell is None or not phase:
        return None
    peak = prev.get("lifecycle_peak_close")
    return LifecycleState(
        phase=str(phase),
        anchor_date=anchor,
        phase_age=int(age),
        phase_dwell=int(dwell),
        healthy_days=int(prev.get("lifecycle_healthy_days") or 0),
        weak_days=int(prev.get("lifecycle_weak_days") or 0),
        peak_close=float(peak) if peak is not None else None,
        last_observation_date=prev.get("lifecycle_last_observation_date"),
        transition_reason=str(prev.get("lifecycle_transition_reason") or ""),
        data_status=str(prev.get("lifecycle_data_status") or "ok"),
    )


def build_lifecycle_evidence(
    trade_date: date,
    *,
    bar_complete: bool,
    trend_window: Optional[dict] = None,
    r5_pct: Optional[float] = None,
    vol20_pct: Optional[float] = None,
    strength: Optional[float] = None,
    rrg_quadrant: Optional[str] = None,
    overheat_level: str = "normal",
    break_signals: Optional[dict] = None,
    score_drop: Optional[float] = None,
    is_defensive: bool = False,
    legacy_state: Optional[str] = None,
    legacy_wave_start: Optional[date] = None,
    legacy_mainline_days: Optional[int] = None,
) -> LifecycleEvidence:
    """把 `_measure_trend_window` / `_compute_break_signals` 的标量装配成证据对象。

    `trend_broken` 由 `is_trend_broken(...)` 唯一口径派生，`_determine_state_v3`
    与 lifecycle 共用同一布尔结果，禁止两套定义。
    """
    tw = trend_window or {}
    bs = break_signals or {}
    r5 = tw.get("r5") if tw else None
    r10 = tw.get("r10") if tw else None
    high20_dist = tw.get("high20_dist") if tw else None
    close = tw.get("close") if tw else None
    above_ma20 = bs.get("above_ma20")
    ma20_slope = tw.get("ma20_slope") if tw else None
    high20_pos = bs.get("high20_pos")
    if overheat_level is None:
        overheat_level = bs.get("overheat_level") or "normal"
    elif bs.get("overheat_level") is not None:
        overheat_level = bs["overheat_level"]
    if score_drop is None and bs.get("score_drop") is not None:
        score_drop = bs["score_drop"]
    if r5 is None and bs.get("price_chg_5d") is not None:
        r5 = bs["price_chg_5d"]
    # 破位判据必须用 `_compute_break_signals` 的 price_chg_5d（close[-1]/close[-5]-1，
    # 与 legacy 状态机同口径），不能改用趋势测量的 r5（close[-1]/close[-6]-1）：
    # 两者相差一个交易日，混用会让同一板块的 broken 判定与 legacy 不一致。
    broken_r5 = bs.get("price_chg_5d")
    if broken_r5 is None:
        broken_r5 = r5
    broken = is_trend_broken(broken_r5, above_ma20, high20_pos, overheat_level, score_drop)
    return LifecycleEvidence(
        trade_date=trade_date,
        bar_complete=bool(bar_complete),
        r5=r5,
        r10=r10,
        r5_pct=r5_pct,
        above_ma20=above_ma20,
        ma20_slope=ma20_slope,
        high20_dist=high20_dist,
        high20_pos=high20_pos,
        vol20_pct=vol20_pct,
        strength=strength,
        close=close,
        rrg_quadrant=rrg_quadrant,
        overheat_level=overheat_level,
        trend_broken=broken,
        launch_score=compute_launch_score(r5_pct, r10, high20_dist),
        score_drop=score_drop,
        is_defensive=is_defensive,
        legacy_state=legacy_state,
        legacy_wave_start=legacy_wave_start,
        legacy_mainline_days=legacy_mainline_days,
    )
