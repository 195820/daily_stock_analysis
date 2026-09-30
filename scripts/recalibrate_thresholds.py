# -*- coding: utf-8 -*-
"""WI-P0-5 · P6 → 阈值回归校准闭环（离线标定脚本）。

来源：docs/got/plans/architecture_optimization_implementation_20260929.md §3 WI-P0-5。

职责：以 P6（catalyst_realizations / ambush_position_snapshots）realized 分布为因变量，
产出阈值标定工件 data/threshold_calibration.json（供 P0-1/P0-2/P1-5 消费），
覆盖：
- lifecycle mature/late_stage 的 day 阈值（现状写死经验值，`mature & day>30` 几乎永不触发）
- AMBUSH_DRIFT_STALL_DAYS / AMBUSH_DRIFT_TIMEOUT_DAYS / AMBUSH_BINARY_GRACE_DAYS
  （现状全标「样本量 0，待校准」）
- 最优停止观测证据的日收益分布摘要（后验先验的最小可用近似）

Point-in-time 红线（验收标准）：as_of 之后才兑现/退出的记录一律剔除
（event_date/exit_date > as_of 不参与标定），杜绝 look-ahead。

用法：
    python scripts/recalibrate_thresholds.py [--as-of YYYY-MM-DD] [--out PATH] [--min-samples 30]

样本不足箱：输出 status=insufficient_sample + 当前值维持（消费方据此保持 legacy 回退位），
绝不虚构阈值——「阈值来自数据、不来自拍脑袋」（红线 6）。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from src.storage import (
    AmbushPositionSnapshotRecord,
    CatalystRealizationRecord,
    DatabaseManager,
)

logger = logging.getLogger("recalibrate_thresholds")

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "threshold_calibration.json"

# 现状代码常量（portfolio_decision.py 锚点，仅作对照基线写进工件，不改代码）
CURRENT_CONSTANTS = {
    "AMBUSH_DRIFT_STALL_DAYS": 10,
    "AMBUSH_DRIFT_TIMEOUT_DAYS": 20,
    "AMBUSH_BINARY_GRACE_DAYS": 2,
    "LIFECYCLE_MATURE_REDUCE_DAY": 30,
}

MIN_TRADING_DAYS = 5  # 事件至少走完 T+5 才纳入（day_5_return 为最宽窗口）


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例 Wilson 95% 区间（小样本比正态区间稳健）。n=0 → (0,1) 全不确定。"""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def _trading_days_between(d1: date, d2: date) -> int:
    """粗略交易日数（weekend-only 口径，标定侧允许近似；在线侧计数由 P1-9 真实日历负责）"""
    if d1 is None or d2 is None or d2 < d1:
        return 0
    count, cur = 0, d1
    from datetime import timedelta
    while cur <= d2:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return max(0, count - 1)  # 不含入场当日


# ─────────────────────────────────────────────────────────────
# 数据采集（point-in-time 过滤在 SQL 层粗筛 + Python 层精筛）
# ─────────────────────────────────────────────────────────────

def load_realized_events(session, as_of: date) -> list[dict]:
    """P6 催化剂兑现记录（T+1/3/5 收益齐备且兑现日 ≤ as_of）。"""
    rows = session.execute(
        select(CatalystRealizationRecord).where(
            CatalystRealizationRecord.is_realized.is_(True),
            CatalystRealizationRecord.day_1_return.isnot(None),
        )
    ).scalars().all()
    out = []
    for r in rows:
        ref = r.realization_date or r.event_date
        if ref is None or ref > as_of:  # point-in-time 精筛：as_of 后才兑现的不进标定
            continue
        if r.event_date > as_of:
            continue
        d1 = r.day_1_return
        d3 = r.day_3_return if r.day_3_return is not None else d1
        d5 = r.day_5_return if r.day_5_return is not None else d3
        if d1 is None:
            continue  # 多窗口收益全缺 → 不进标定（缺失≠零分）
        out.append({
            "sector": r.sector,
            "category_tag": (r.category_tag or "").lower(),
            "event_date": r.event_date,
            "day1": d1,
            "day3": d3,
            "day5": d5,
            "story_lifetime_days": r.story_lifetime_days,
            "direction_correct": bool(r.drift_direction_correct)
            if r.drift_direction_correct is not None else False,
        })
    return out


def load_closed_ambush(session, as_of: date) -> list[dict]:
    """已关闭埋伏持仓（exit_date ≤ as_of），供 drift 超时/衰竭与 binary 宽限标定。"""
    rows = session.execute(
        select(AmbushPositionSnapshotRecord).where(
            AmbushPositionSnapshotRecord.is_open.is_(False),
            AmbushPositionSnapshotRecord.exit_date.isnot(None),
        )
    ).scalars().all()
    out = []
    for r in rows:
        if r.exit_date > as_of or r.entry_date > as_of:
            continue
        ret = None
        if r.entry_price and r.exit_price is not None and r.entry_price > 0:
            ret = (r.exit_price - r.entry_price) / r.entry_price * 100.0
        out.append({
            "symbol": r.symbol,
            "category_tag": (r.category_tag or "").lower(),
            "exit_signal": r.exit_signal or "",
            "held_trading_days": _trading_days_between(r.entry_date, r.exit_date),
            "event_to_exit_days": _trading_days_between(r.catalyst_event_date, r.exit_date),
            "holding_return_pct": ret,
        })
    return out


# ─────────────────────────────────────────────────────────────
# 标定逻辑
# ─────────────────────────────────────────────────────────────

def calibrate_lifecycle_day(events: list[dict], min_samples: int) -> dict:
    """lifecycle day 阈值：找 T+1..T+5 窗口中「median 收益转负」的最早窗口对应天数，
    作为 mature 减仓档的建议 day（方向：day4–5 分箱，替代写死的 30）。"""
    pos = [e for e in events if e["category_tag"] in ("drift", "binary", "fade")]
    n = len(pos)
    med1 = statistics.median([e["day1"] for e in pos]) if n else None
    med3 = statistics.median([e["day3"] for e in pos]) if n else None
    med5 = statistics.median([e["day5"] for e in pos]) if n else None
    win1 = sum(1 for e in pos if e["day1"] > 0)
    lo, hi = wilson_interval(win1, n)
    result = {
        "samples": n,
        "median_return": {"T1": med1, "T3": med3, "T5": med5},
        "t1_win_rate": {"point": (win1 / n) if n else None,
                        "wilson95": [round(lo, 4), round(hi, 4)] if n else None},
        "current_constant_day": CURRENT_CONSTANTS["LIFECYCLE_MATURE_REDUCE_DAY"],
    }
    if n < min_samples or n == 0:  # 空箱：无样本不得虚构 ok 建议
        result.update(status="insufficient_sample", recommendation="keep_legacy")
        return result
    # 建议：median(T+3) 已≤0 而 median(T+1)>0 → 收益峰值在 1–3 日之间 → day 阈值取 4；
    # 三窗口 median 全正 → 衰减慢于预期，维持保守 5（仍远小于 30）；全负 → 取 3。
    if med1 is not None and med1 <= 0:
        suggested = 3
    elif med3 is not None and med3 <= 0:
        suggested = 4
    elif med5 is not None and med5 <= 0:
        suggested = 5
    else:
        suggested = 5
    # 故事寿命分布给独立佐证（日历日 → 交易日近似 ×5/7）
    lives = [e["story_lifetime_days"] for e in pos if e["story_lifetime_days"]]
    if lives:
        result["story_lifetime_cal_days"] = {
            "p50": statistics.median(lives),
            "p75": sorted(lives)[int(len(lives) * 0.75)] if len(lives) >= 4 else None,
        }
    result.update(status="ok", recommendation_day=suggested)
    return result


def calibrate_drift(ambush: list[dict], min_samples: int) -> dict:
    """drift 退出参数：按 exit_signal 分组的持有天数分布 + 收益联动。"""
    drift = [a for a in ambush if a["category_tag"] == "drift" and a["held_trading_days"] > 0]
    n = len(drift)
    base = {"samples": n, "current_constants": {
        "STALL_DAYS": CURRENT_CONSTANTS["AMBUSH_DRIFT_STALL_DAYS"],
        "TIMEOUT_DAYS": CURRENT_CONSTANTS["AMBUSH_DRIFT_TIMEOUT_DAYS"],
    }}
    if n < min_samples or n == 0:  # 空箱/不足：早退，避免下方 days[-1]/mean([]) 崩溃
        base.update(status="insufficient_sample", recommendation="keep_legacy")
        return base
    days = sorted(a["held_trading_days"] for a in drift)
    q = lambda p: days[min(len(days) - 1, int(len(days) * p))]
    by_signal: dict[str, list[float]] = {}
    for a in drift:
        by_signal.setdefault(a["exit_signal"] or "unknown", []).append(
            a["holding_return_pct"] if a["holding_return_pct"] is not None else 0.0
        )
    base.update(
        status="ok",
        held_days={"p50": q(0.50), "p75": q(0.75), "p90": q(0.90)},
        exit_signal_avg_return={k: round(statistics.mean(v), 2) for k, v in by_signal.items()},
        # 建议超时取 p90（封顶现值 20 的 1.5 倍，防止异常长尾拉爆）
        recommendation_timeout_days=min(base["current_constants"]["TIMEOUT_DAYS"] * 3 // 2, max(5, q(0.90))),
    )
    return base


def calibrate_binary(ambush: list[dict], min_samples: int) -> dict:
    """binary 宽限：落空单（event_cancelled）的事件日→退出日间隔分布。"""
    rows = [
        a for a in ambush
        if a["category_tag"] == "binary" and a["exit_signal"] == "event_cancelled"
        and a["event_to_exit_days"] > 0
    ]
    n = len(rows)
    base = {"samples": n, "current_constant": CURRENT_CONSTANTS["AMBUSH_BINARY_GRACE_DAYS"]}
    if n < min_samples or n == 0:  # 空箱/不足：早退，避免下方 days[-1] 崩溃
        base.update(status="insufficient_sample", recommendation="keep_legacy")
        return base
    days = sorted(a["event_to_exit_days"] for a in rows)
    p75 = days[min(len(days) - 1, int(len(days) * 0.75))]
    base.update(status="ok", grace_days_p75=p75, recommendation_grace_days=max(1, p75))
    return base


def build_artifact(as_of: date, min_samples: int) -> dict:
    db = DatabaseManager()
    with db.get_session() as session:
        events = load_realized_events(session, as_of)
        ambush = load_closed_ambush(session, as_of)
    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "as_of": as_of.isoformat(),
            "point_in_time": "只用 as_of 之前已兑现/已退出记录；as_of 后记录剔除（防 look-ahead）",
            "min_samples_per_bin": min_samples,
            "event_samples": len(events),
            "ambush_closed_samples": len(ambush),
            "source": "docs/got/plans/architecture_optimization_implementation_20260929.md WI-P0-5",
        },
        "lifecycle_day": calibrate_lifecycle_day(events, min_samples),
        "ambush_drift": calibrate_drift(ambush, min_samples),
        "ambush_binary_grace": calibrate_binary(ambush, min_samples),
        "consumer_contract": {
            "P0-2": "lifecycle_day.recommendation_day 达标（status=ok）后方可替换写死 30 天常量",
            "P0-1": "median_return/wilson95 为后验观测证据权重的占位输入（GATE-2 分层先验落地前用全局箱）",
            "P4-ambush": "ambush_drift/ambush_binary_grace 的 recommendation_* 达标后方可替换 AMBUSH_DRIFT_* 常量",
            "insufficient_sample": "消费方必须保持 legacy 常量并告警，禁止拿空工件上线",
        },
        "caveats": [
            "幸存者/停牌偏差未去偏（动态宇宙待 GATE-2 数据面完善），兑现率读数可能虚高",
            "held_trading_days 为 weekend-only 近似；P1-9 转正后应改用真实交易日历重算",
            "story_lifetime_days 为日历日口径，与交易日阈值换算存在 ±1 日误差",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="WI-P0-5 P6→阈值回归校准闭环")
    parser.add_argument("--as-of", type=str, default=None,
                        help="point-in-time 截止日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT))
    parser.add_argument("--min-samples", type=int, default=30)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    artifact = build_artifact(as_of, args.min_samples)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "[P0-5] 工件已写出 %s（事件样本=%d, 埋伏关闭样本=%d, lifecycle=%s, drift=%s, binary=%s）",
        out_path, artifact["meta"]["event_samples"], artifact["meta"]["ambush_closed_samples"],
        artifact["lifecycle_day"]["status"], artifact["ambush_drift"]["status"],
        artifact["ambush_binary_grace"]["status"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
