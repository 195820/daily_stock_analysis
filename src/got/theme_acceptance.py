# -*- coding: utf-8 -*-
"""P4 验收判定器（实施稿 §2.1/§9）：双主指标 + 样本门槛 + 回撤约束。

纯函数：输入两份 `backtest_portfolio_baseline.py` 产出的 arm payload，输出逐条判据与
最终状态。**不产生收益结论**——样本不足一律 `insufficient_sample`；无条件通过才 `pass`。

判据（与实施稿逐条对应）
------------------------
1. 样本：两臂均 `sample_days>=60` 且闭合往返 `>=100`；不足 → `insufficient_sample`
2. 净收益胜率：变体 − 基线 >0 且日期簇 bootstrap 95% CI 下界 >0
3. 组合累计净收益率：逐日收益差 >0 且日期簇 bootstrap 95% CI 下界 >0
4. 最大回撤：变体相对基线恶化 ≤1pp
5. 集中度：单日贡献占比作为**披露项**（不设门槛）
6. 风控零绕过：无法从回测 payload 验证 → `not_verifiable`，需人工签署

任何一条 `fail` → `fail`；任一 `unresolved` → `unresolved`；样本不足优先返回
`insufficient_sample`（不得用点估计顶替）。`disclosure` 只披露、不参与聚合。

**设计约束**：`risk_gate_no_bypass` 恒为 `not_verifiable`，因此机械判据全过时最终状态是
`pass_pending_attestation`，**不存在自动 `pass`**——这是刻意设计（风控零绕过必须人工签署），
不要为了「能 pass」而删除该判据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.got.backtest_discipline import date_cluster_bootstrap_ci

MIN_SAMPLE_DAYS = 60
MIN_CLOSED_TRADES = 100
MAX_MDD_WORSENING = 0.01
MIN_COMMON_DATES = 10

PASS = "pass"
FAIL = "fail"
DISCLOSURE = "disclosure"
INSUFFICIENT = "insufficient_sample"
UNRESOLVED = "unresolved"
NOT_VERIFIABLE = "not_verifiable"


@dataclass(frozen=True)
class Criterion:
    name: str
    status: str
    value: Any = None
    threshold: Any = None
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "value": self.value,
            "threshold": self.threshold,
            "detail": self.detail,
        }


def _closed_count(arm: Mapping[str, Any]) -> int:
    return int(((arm.get("metrics") or {}).get("closed_trades") or {}).get("count") or 0)


def _sample_days(arm: Mapping[str, Any]) -> int:
    return int(((arm.get("metrics") or {}).get("sample_days")) or 0)


def _equity_by_date(arm: Mapping[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in arm.get("equity_curve") or []:
        if len(row) >= 2:
            out[str(row[0])] = float(row[1])
    return out


def _daily_returns(equity: Mapping[str, float]) -> Dict[str, float]:
    dates = sorted(equity)
    return {
        dates[i]: equity[dates[i]] / equity[dates[i - 1]] - 1.0
        for i in range(1, len(dates))
        if equity[dates[i - 1]]
    }


def _win_by_date(arm: Mapping[str, Any]) -> Dict[str, float]:
    return _win_by_date_with_dropped(arm)[0]


def _win_by_date_with_dropped(arm: Mapping[str, Any]) -> tuple[Dict[str, float], int]:
    """按平仓日聚合胜率；缺 exit_date 的成交**剔除并计数**（不静默丢失）。"""
    buckets: Dict[str, List[float]] = {}
    dropped = 0
    for trade in arm.get("closed_trades") or []:
        day = str(trade.get("exit_date") or "")
        if not day:
            dropped += 1
            continue
        buckets.setdefault(day, []).append(1.0 if float(trade.get("net_return") or 0) > 0 else 0.0)
    return {d: sum(v) / len(v) for d, v in buckets.items() if v}, dropped


def _paired_diff(
    a: Mapping[str, float], b: Mapping[str, float]
) -> Dict[str, float]:
    """b − a，只取两臂都有的日期（缺日不插值）。"""
    return {d: b[d] - a[d] for d in sorted(set(a) & set(b))}


def _diff_criterion(
    *,
    name: str,
    baseline: Mapping[str, float],
    variant: Mapping[str, float],
    threshold_detail: str,
    detail: str = "",
) -> Criterion:
    diff = _paired_diff(baseline, variant)
    if len(diff) < MIN_COMMON_DATES:
        return Criterion(
            name=name, status=INSUFFICIENT, value=None,
            threshold=threshold_detail,
            detail=(detail + "；" if detail else "") + f"common_dates={len(diff)}<{MIN_COMMON_DATES}",
        )
    mean = sum(diff.values()) / len(diff)
    lo, hi = date_cluster_bootstrap_ci({d: [v] for d, v in diff.items()})
    ok = mean > 0 and lo > 0
    return Criterion(
        name=name,
        status=PASS if ok else FAIL,
        value={"mean": round(mean, 6), "ci95": [lo, hi], "common_dates": len(diff)},
        threshold=threshold_detail,
        detail=(detail + "；" if detail else "") + "变体−基线",
    )


SCHEMA_FIELDS = ("metrics", "equity_curve", "closed_trades", "meta")
COMPARED_FIELDS = (
    "metrics.sample_days", "metrics.max_drawdown", "metrics.closed_trades.count",
    "closed_trades[].exit_date", "closed_trades[].net_return",
    "equity_curve[][0]", "equity_curve[][1]",
    "meta.cost_model", "meta.initial_cash", "meta.window",
)


def _schema_check(baseline: Mapping[str, Any], variant: Mapping[str, Any]) -> Criterion:
    """两臂必须来自同一 runner schema / 同一成本与初始资金，否则比较无效。"""
    missing = {
        "baseline": [k for k in SCHEMA_FIELDS if k not in baseline],
        "variant": [k for k in SCHEMA_FIELDS if k not in variant],
    }
    if any(missing.values()):
        return Criterion(
            name="payload_schema", status=UNRESOLVED, value=missing,
            threshold="两臂均含 " + ",".join(SCHEMA_FIELDS),
            detail="缺字段，拒绝比较",
        )
    base_meta = baseline.get("meta") or {}
    var_meta = variant.get("meta") or {}
    diffs = {
        key: [base_meta.get(key), var_meta.get(key)]
        for key in ("cost_model", "initial_cash", "window")
        if base_meta.get(key) != var_meta.get(key)
    }
    if diffs:
        return Criterion(
            name="payload_schema", status=UNRESOLVED, value=diffs,
            threshold="成本模型/初始资金/窗口一致",
            detail="meta 不一致（不同 runner 版本或参数），比较无效",
        )
    return Criterion(
        name="payload_schema", status=PASS,
        value=list(COMPARED_FIELDS), threshold="同 schema + 同成本/初始资金/窗口",
        detail="比较字段清单见 value",
    )


def evaluate_arms(
    baseline: Mapping[str, Any], variant: Mapping[str, Any]
) -> Dict[str, Any]:
    """逐条判据 + 最终状态；不抛异常，异常输入落 unresolved。"""
    criteria: List[Criterion] = []
    try:
        criteria.append(_schema_check(baseline, variant))
        base_days, var_days = _sample_days(baseline), _sample_days(variant)
        base_closed, var_closed = _closed_count(baseline), _closed_count(variant)
        criteria.append(Criterion(
            name="sample_days",
            status=PASS if min(base_days, var_days) >= MIN_SAMPLE_DAYS else INSUFFICIENT,
            value={"baseline": base_days, "variant": var_days},
            threshold=f">={MIN_SAMPLE_DAYS}",
            detail="两臂均需满足",
        ))
        criteria.append(Criterion(
            name="closed_trades",
            status=PASS if min(base_closed, var_closed) >= MIN_CLOSED_TRADES else INSUFFICIENT,
            value={"baseline": base_closed, "variant": var_closed},
            threshold=f">={MIN_CLOSED_TRADES}",
            detail="两臂均需满足",
        ))

        base_eq, var_eq = _equity_by_date(baseline), _equity_by_date(variant)
        if set(base_eq) != set(var_eq):
            criteria.append(Criterion(
                name="equity_alignment", status=UNRESOLVED,
                value={"baseline_days": len(base_eq), "variant_days": len(var_eq)},
                threshold="日期集合一致",
                detail="两臂净值日期不一致，禁止拼接比较",
            ))
        else:
            criteria.append(Criterion(
                name="equity_alignment", status=PASS,
                value=len(base_eq), threshold="日期集合一致",
            ))
            base_win, base_dropped = _win_by_date_with_dropped(baseline)
            var_win, var_dropped = _win_by_date_with_dropped(variant)
            criteria.append(_diff_criterion(
                name="win_rate_diff",
                baseline=base_win,
                variant=var_win,
                threshold_detail="差值>0 且 CI 下界>0",
                detail=f"缺 exit_date 剔除：baseline={base_dropped} / variant={var_dropped}",
            ))
            criteria.append(_diff_criterion(
                name="net_return_diff",
                baseline=_daily_returns(base_eq),
                variant=_daily_returns(var_eq),
                threshold_detail="差值>0 且 CI 下界>0",
            ))
            base_mdd = float((baseline.get("metrics") or {}).get("max_drawdown") or 0)
            var_mdd = float((variant.get("metrics") or {}).get("max_drawdown") or 0)
            criteria.append(Criterion(
                name="max_drawdown",
                status=PASS if (var_mdd - base_mdd) <= MAX_MDD_WORSENING else FAIL,
                value={"baseline": base_mdd, "variant": var_mdd,
                       "delta": round(var_mdd - base_mdd, 6)},
                threshold=f"恶化<={MAX_MDD_WORSENING:.2f}",
            ))
            base_ret = _daily_returns(base_eq)
            diff = _paired_diff(base_ret, _daily_returns(var_eq))
            total = sum(diff.values())
            top_share = (max((abs(v) for v in diff.values()), default=0.0) / abs(total)
                         if total else None)
            criteria.append(Criterion(
                name="single_day_concentration",
                status=DISCLOSURE if top_share is not None else DISCLOSURE,
                value=None if top_share is None else round(top_share, 4),
                threshold="披露项（不设门槛、不参与聚合）",
                detail="单日绝对贡献 / 累计差值；>0.5 需人工复核",
            ))
        criteria.append(Criterion(
            name="risk_gate_no_bypass",
            status=NOT_VERIFIABLE,
            threshold="人工签署",
            detail="回测 payload 无法证明风控零绕过，需 P5 审计产物 + 人工确认",
        ))
    except Exception as exc:  # noqa: BLE001 - 判定器不得因脏数据崩溃
        criteria.append(Criterion(
            name="evaluator", status=UNRESOLVED, detail=f"{type(exc).__name__}: {exc}"
        ))

    statuses = {c.status for c in criteria} - {DISCLOSURE}
    if INSUFFICIENT in statuses:
        final = INSUFFICIENT
    elif UNRESOLVED in statuses:
        # 数据/schema 不可比时，fail 也不可信 → 优先 unresolved
        final = UNRESOLVED
    elif FAIL in statuses:
        final = FAIL
    elif NOT_VERIFIABLE in statuses:
        # 机械判据全过，但风控零绕过无法由回测证明 → 待人工签署，不自动 pass
        final = "pass_pending_attestation"
    else:
        final = PASS

    return {
        "status": final,
        "criteria": [c.as_dict() for c in criteria],
        "baseline_meta": (baseline.get("meta") or {}).get("window"),
        "variant_meta": (variant.get("meta") or {}).get("window"),
        "compared_fields": list(COMPARED_FIELDS),
        "note": "本判定器不产生收益结论；insufficient_sample 时禁止转正。",
    }


def render_md(report: Mapping[str, Any]) -> str:
    lines = [
        f"# P4 验收判定：`{report['status']}`",
        "",
        "> 判定器只执行实施稿 §2.1/§9 的机械判据；不替代人工风控签署，不产生收益结论。",
        "",
        "| 判据 | 状态 | 值 | 门槛 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for c in report.get("criteria") or []:
        lines.append(
            f"| {c['name']} | {c['status']} | {c.get('value')} | "
            f"{c.get('threshold')} | {c.get('detail', '')} |"
        )
    lines += ["", report.get("note", ""), ""]
    return "\n".join(lines)
