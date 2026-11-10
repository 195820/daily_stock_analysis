# -*- coding: utf-8 -*-
"""P2 路由复评影子层（只读归档事实，不产生任何交易资格）。

职责（实施稿 §5.1–§5.3）
------------------------
给定「账本 setup + 当日归档闸门事实」，回答四个问题：
1. 该板块是否仍在 observation（账本给出）；
2. 是否因强度/位置/催化被排序靠后（归档 strength/break 字段）；
3. 是否曾经申请 setup（账本给出）；
4. **哪一层容量或闸门阻止它继续向下游传递**（本模块的 `classify_blocking_layer`）。

口径边界（红线）
----------------
- 只**读取**归档闸门事实（`sector.json` 的 break_signals/overheat/crowded、
  `unified.json` 的 market_regime、`portfolio_decision.json` 的仓位比/上限），
  不重算 Node 2/6 的评分，不调用任何预算/组合优化；
- 输出只有「阻断层/变体路由/原因」三类审计字段，**没有 buy 字段**，
  任何下游不得把它当候选资格；
- E16 变体（B/C/D）复用 E16 定向反事实的规则常量与判据，供交叉核对；
  与账本解耦，不随账本转正（实施稿 §5.1）。

E16 规则来源：`scripts/audit_node2_state_counterfactual.py` 的
`broken/entry_ok/quadrant/ambush_score/cf_b/cf_c/cf_d`（同常量 AMBUSH_FLOOR=0.30、
LEADING_EXEMPT_STRENGTH=80）；本模块为纯函数复刻，`tests/test_theme_routing_shadow.py`
对两者做同输入一致性校验，规则改动必须两处同步。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

AMBUSH_FLOOR = 0.30
LEADING_EXEMPT_STRENGTH = 80.0
OVERHEAT_LEVELS = {"warning", "overheated", "exhausted"}
OUTPUT_BUCKETS = ("active_mainline", "ambush", "near_miss_ambush", "top_by_score")

BLOCKING_LAYERS = (
    "visible",
    "top_only",
    "unresolved_no_detail",
    "trend_broken",
    "overheat",
    "regime_blocked",
    "crowding",
    "capacity",
    "score_rank",
)


def _item_get(item: Any, *names: str) -> Any:
    for name in names:
        if isinstance(item, Mapping):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        if value not in (None, ""):
            return value
    return None


def expand_bucket_items(payload: Mapping[str, Any], bucket: str) -> List[dict]:
    """展开主线组的 members；其余桶原样返回。"""
    out: List[dict] = []
    for item in payload.get(bucket) or []:
        members = item.get("members") if isinstance(item, Mapping) else None
        if members and not item.get("code"):
            out.extend(m for m in members if isinstance(m, Mapping))
        else:
            out.append(item)
    return out


def detail_index(payload: Mapping[str, Any]) -> Dict[str, dict]:
    """name → 板块 detail（所有输出桶，含主线组 members）。"""
    out: Dict[str, dict] = {}
    for bucket in (
        "active_mainline", "exhausted_mainline", "ambush", "near_miss_ambush",
        "defensive_rotation", "cooling_down", "top_by_score", "industry_top",
    ):
        for item in expand_bucket_items(payload, bucket):
            name = str(item.get("name") or item.get("group_name") or "").strip()
            if name and name not in out:
                out[name] = dict(item)
    return out


def bucket_membership(payload: Mapping[str, Any], code: str, name: str) -> List[str]:
    """该板块当天实际出现在哪些输出桶（归档事实，不推断）。"""
    hits: List[str] = []
    for bucket in OUTPUT_BUCKETS:
        for item in expand_bucket_items(payload, bucket):
            if item.get("code") == code or item.get("name") == name:
                hits.append(bucket)
                break
    return hits


# ── E16 变体判据（与 counterfactual 脚本同口径）─────────────────────────
def broken(det: Mapping[str, Any]) -> bool:
    bs = det.get("break_signals") or {}
    price5 = bs.get("price_chg_5d")
    above = bs.get("above_ma20")
    high20 = bs.get("high20_pos")
    overheat = bs.get("overheat_level")
    score_drop = bs.get("score_drop")
    return bool(
        (price5 is not None and price5 <= 0)
        or (not above)
        or (overheat == "exhausted" and (high20 or 0) < 0.60)
        or ((score_drop or 0) >= 20)
    )


def entry_ok(det: Mapping[str, Any]) -> bool:
    strength = float(det.get("strength_score") or 0)
    if det.get("tier") in ("S", "A"):
        return strength > 60
    return strength >= 80


def quadrant(det: Mapping[str, Any]) -> Optional[str]:
    return (det.get("rrg") or {}).get("quadrant")


def ambush_score(det: Mapping[str, Any]) -> float:
    """板块的吸筹/埋伏分（仅归因用，不得反哺路由）。

    P2 前置底座（2026-11-05）：enabled 下旧 6 维分改记在 `accumulation_score`，
    `ambush_score_v2` 不再产出 → 优先读新字段，legacy 缺该字段才回退旧键。
    """
    indicators = det.get("indicators") or {}
    value = det.get("accumulation_score")
    if value is None:
        value = indicators.get("accumulation_score")
    if value is None:
        value = indicators.get("ambush_score_v2")
    return float(value or 0.0)


def cf_active(det: Mapping[str, Any]) -> str:
    return "active_mainline" if (
        not broken(det) and quadrant(det) == "leading" and entry_ok(det)
    ) else "neutral"


def cf_b(det: Mapping[str, Any]) -> str:
    if ambush_score(det) >= AMBUSH_FLOOR:
        return "ambush"
    return cf_active(det)


def cf_c(det: Mapping[str, Any]) -> str:
    if ambush_score(det) >= AMBUSH_FLOOR and not (
        float(det.get("strength_score") or 0) >= LEADING_EXEMPT_STRENGTH
        and quadrant(det) == "leading"
    ):
        return "ambush"
    return cf_active(det)


def cf_d(det: Mapping[str, Any]) -> str:
    return cf_active(det)


# ── 闸门事实与阻断层 ────────────────────────────────────────────────────
@dataclass(frozen=True)
class GateFacts:
    code: str
    name: str
    buckets: Tuple[str, ...]
    det_present: bool
    state: str
    strength: Optional[float]
    broken: Optional[bool]
    entry_ok: Optional[bool]
    quadrant: Optional[str]
    ambush_score: Optional[float]
    overheat_level: str
    overheat_severity: Optional[float]
    regime: str
    frame_group: str
    crowded: bool
    position_ratio: Optional[float]
    position_limit: Optional[float]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "board_code": self.code,
            "board_name": self.name,
            "archived_buckets": list(self.buckets),
            "det_present": self.det_present,
            "state": self.state,
            "strength": None if self.strength is None else round(float(self.strength), 2),
            "broken": self.broken,
            "entry_ok": self.entry_ok,
            "quadrant": self.quadrant,
            "ambush_score": None if self.ambush_score is None else round(self.ambush_score, 3),
            "overheat_level": self.overheat_level,
            "overheat_severity": (
                None if self.overheat_severity is None else round(self.overheat_severity, 3)
            ),
            "regime": self.regime,
            "frame_group": self.frame_group,
            "crowded": self.crowded,
            "position_ratio": (
                None if self.position_ratio is None else round(self.position_ratio, 4)
            ),
            "position_limit": (
                None if self.position_limit is None else round(self.position_limit, 4)
            ),
        }


def collect_gate_facts(
    *,
    code: str,
    name: str,
    payload: Mapping[str, Any],
    unified: Optional[Mapping[str, Any]],
    risk: Optional[Mapping[str, Any]],
    details: Optional[Mapping[str, dict]] = None,
) -> GateFacts:
    details = details if details is not None else detail_index(payload)
    det = details.get(name)
    break_signals = (det or {}).get("break_signals") or {}
    overheat_lookup = payload.get("overheat_lookup") or {}
    severity = (det or {}).get("overheat_severity")
    if severity is None:
        severity = overheat_lookup.get(name)
    regime_obj = (unified or {}).get("market_regime") or {}
    regime = str(regime_obj.get("regime") or "") if isinstance(regime_obj, Mapping) else str(regime_obj)
    crowded_list = payload.get("crowded_families") or []
    frame_group = str((det or {}).get("frame_group") or "")
    # risk 可能是整份 portfolio_decision.json（字段在 risk_assessment 下）或已拍平
    risk_obj = risk or {}
    if isinstance(risk_obj.get("risk_assessment"), Mapping):
        risk_obj = risk_obj["risk_assessment"]
    ratio = risk_obj.get("current_position_ratio")
    limit = risk_obj.get("position_limit")
    return GateFacts(
        code=code,
        name=name,
        buckets=tuple(bucket_membership(payload, code, name)),
        det_present=det is not None,
        state=str((det or {}).get("state") or ""),
        strength=(det or {}).get("strength_score"),
        broken=broken(det) if det else None,
        entry_ok=entry_ok(det) if det else None,
        quadrant=quadrant(det) if det else None,
        ambush_score=ambush_score(det) if det else None,
        overheat_level=str(break_signals.get("overheat_level") or ""),
        overheat_severity=severity,
        regime=regime,
        frame_group=frame_group,
        crowded=bool(frame_group and frame_group in {str(x) for x in crowded_list}),
        position_ratio=float(ratio) if isinstance(ratio, (int, float)) else None,
        position_limit=float(limit) if isinstance(limit, (int, float)) else None,
    )


TRADABLE_BUCKETS = {"active_mainline", "ambush", "near_miss_ambush"}
TOP_ONLY_BUCKETS = {"top_by_score", "industry_top"}


def classify_blocking_layer(facts: GateFacts) -> Tuple[str, str]:
    """返回 (blocking_layer, reason)。顺序固定，见模块 docstring 与测试。

    `top_only` 与 `visible` 必须分开：`top_by_score` 是展示/排序桶、不创建候选
    （验证报告 §3 明确「top 桶不创建候选」，10-29 短剧正落在该桶）。
    """
    if any(b in TRADABLE_BUCKETS for b in facts.buckets):
        return "visible", f"archived_buckets={','.join(facts.buckets)}"
    if facts.buckets and all(b in TOP_ONLY_BUCKETS for b in facts.buckets):
        return "top_only", (
            f"archived_buckets={','.join(facts.buckets)}（不创建候选，下游仍不可交易）"
        )
    if facts.buckets:
        return "visible", f"archived_buckets={','.join(facts.buckets)}"
    if not facts.det_present:
        return "unresolved_no_detail", "该板块不在任何输出桶 detail，无法审计闸门（不推断）"
    if facts.broken:
        return "trend_broken", "break_signals 判 broken（5d≤0 / 跌破MA20 / 衰竭 / 分数骤降）"
    if facts.overheat_level in OVERHEAT_LEVELS:
        return "overheat", f"overheat_level={facts.overheat_level}"
    if facts.regime == "bear":
        return "regime_blocked", "market_regime=bear（组合层限制）"
    if facts.crowded:
        return "crowding", f"frame_group={facts.frame_group} 在 crowded_families"
    # 组合预算（position_ratio ≥ limit）是 Phase 2 执行层约束，不是板块掉出 Phase 1
    # 输出桶的原因；只留在 gate_verdicts 供对照，不进首要阻断层，避免掩盖板块级归因。
    if facts.state in ("ambush", "active_mainline"):
        return "capacity", f"内部 state={facts.state} 但不在对应输出桶（容量/排序截断）"
    return "score_rank", "强度/排序未达输出门槛"


GATE_ORDER = ("trend", "overheat", "regime", "crowding", "budget", "capacity")


def gate_verdicts(facts: GateFacts) -> Dict[str, Dict[str, str]]:
    """逐闸门判定（板块层，三态 pass/fail/unknown）。

    事实来源固定为归档输出（`gate_state_source=archived`）：本函数只把阈值/规则
    套在归档字段上，不重算 Node 2/6。缺证据一律 `unknown`，不当作 pass。
    """
    if not facts.det_present:
        return {
            gate: {"state": "unknown", "detail": "该板块不在任何输出桶 detail"}
            for gate in GATE_ORDER
        }
    tradable = [b for b in facts.buckets if b in TRADABLE_BUCKETS]
    verdicts: Dict[str, Dict[str, str]] = {
        "trend": (
            {"state": "fail", "detail": "break_signals 判 broken"}
            if facts.broken else {"state": "pass", "detail": "break_signals 未破位"}
        ),
        "overheat": (
            {"state": "fail", "detail": f"overheat_level={facts.overheat_level}"}
            if facts.overheat_level in OVERHEAT_LEVELS
            else {"state": "pass", "detail": f"overheat_level={facts.overheat_level or 'normal'}"}
        ),
        "regime": (
            {"state": "fail", "detail": "market_regime=bear"}
            if facts.regime == "bear"
            else {"state": "pass", "detail": f"market_regime={facts.regime or 'unknown'}"}
        ),
        "crowding": (
            {"state": "fail", "detail": f"frame_group={facts.frame_group} 拥挤"}
            if facts.crowded else {"state": "pass", "detail": "家族未拥挤"}
        ),
        "budget": {"state": "unknown", "detail": "缺仓位比/上限"},
        "capacity": {"state": "n/a", "detail": f"内部 state={facts.state or 'unknown'} 非候选态"},
    }
    if facts.position_ratio is not None and facts.position_limit is not None:
        verdicts["budget"] = (
            {"state": "fail", "detail": f"{facts.position_ratio:.3f} ≥ {facts.position_limit:.3f}"}
            if facts.position_ratio >= facts.position_limit
            else {"state": "pass", "detail": f"{facts.position_ratio:.3f} < {facts.position_limit:.3f}"}
        )
    if tradable:
        verdicts["capacity"] = {"state": "pass", "detail": f"输出桶={','.join(tradable)}"}
    elif facts.state in ("ambush", "active_mainline"):
        verdicts["capacity"] = {
            "state": "fail",
            "detail": f"内部 state={facts.state} 但不在对应输出桶（容量/排序截断）",
        }
    return verdicts


def first_failing_gate(verdicts: Mapping[str, Mapping[str, str]]) -> Optional[str]:
    for gate in GATE_ORDER:
        if (verdicts.get(gate) or {}).get("state") == "fail":
            return gate
    return None


def shadow_row(
    *,
    trade_date: str,
    setup: Any,
    facts: GateFacts,
    layer: str,
    reason: str,
) -> Dict[str, Any]:
    """单条 setup 的影子审计记录（无 buy 字段，不可作为候选资格）。"""
    return {
        "trade_date": trade_date,
        "setup_id": getattr(setup, "setup_id", ""),
        "board_code": getattr(setup, "board_code", facts.code),
        "board_name": getattr(setup, "board_name", facts.name),
        "variant": getattr(setup, "setup_type", ""),
        "age_days": getattr(setup, "age_days", None),
        "setup_status": getattr(setup, "status", ""),
        "setup_applied": getattr(setup, "status", "") == "armed",
        "blocking_layer": layer,
        "reason": reason,
        "e16_alt_routes": {},
        "gates": facts.as_dict(),
        "gate_state_source": "archived",
        "gate_verdicts": gate_verdicts(facts),
    }


def build_rows(
    *,
    trade_date: str,
    setups: Sequence[Any],
    observations: Sequence[Any],
    payload: Mapping[str, Any],
    unified: Optional[Mapping[str, Any]],
    risk: Optional[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """把当日 4 变体 setup 逐条审计；observation 只用于补齐无 setup 的板块事实。"""
    details = detail_index(payload)
    obs_by_code = {o.board_code: o for o in observations if o.trade_date == trade_date}
    rows: List[Dict[str, Any]] = []
    for setup in setups:
        if getattr(setup, "fired_date", "") != trade_date:
            continue
        obs = obs_by_code.get(setup.board_code)
        name = getattr(setup, "board_name", "") or (obs.board_name if obs else "")
        facts = collect_gate_facts(
            code=setup.board_code, name=name, payload=payload,
            unified=unified, risk=risk, details=details,
        )
        # blocking_layer 描述「归档路由把该板块停在哪一层」，与 setup 是否申请复评分开记：
        # 未 armed 的板块没有「被闸门拒绝」可言，由 setup_applied 标记，避免污染拒绝统计。
        layer, reason = classify_blocking_layer(facts)
        row = shadow_row(
            trade_date=trade_date, setup=setup, facts=facts, layer=layer, reason=reason
        )
        det = details.get(name)
        if det:
            row["e16_alt_routes"] = {
                "B": cf_b(det), "C": cf_c(det), "D": cf_d(det),
            }
        rows.append(row)
    return rows


def observation_reminders(
    *,
    trade_date: str,
    episodes: Sequence[Any],
    observations: Sequence[Any],
    payload: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """open/pending 段但当天不在任何输出桶的板块 → 复评提醒（只读）。"""
    out: List[Dict[str, Any]] = []
    obs_by_code = {o.board_code: o for o in observations if o.trade_date == trade_date}
    for ep in episodes:
        if ep.status == "closed":
            continue
        buckets = bucket_membership(payload, ep.board_code, ep.board_name)
        if buckets:
            continue
        obs = obs_by_code.get(ep.board_code)
        out.append({
            "trade_date": trade_date,
            "board_code": ep.board_code,
            "board_name": ep.board_name,
            "episode_id": ep.episode_id,
            "episode_status": ep.status,
            "peak_strength": round(ep.peak_strength, 2),
            "strength": None if obs is None or obs.strength is None else round(obs.strength, 2),
            "state": obs.state if obs else "",
            "gaps": ep.gaps,
            "note": "observation 保留身份，但当日不在任何输出桶（下游不可见）",
        })
    out.sort(key=lambda r: (r["board_code"], r["trade_date"]))
    return out


def summarize(rows: Sequence[Mapping[str, Any]], reminders: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_variant_layer: Dict[str, int] = {}
    by_layer: Dict[str, int] = {}
    by_variant_status: Dict[str, int] = {}
    by_layer_armed: Dict[str, int] = {}
    by_gate_fail: Dict[str, int] = {}
    gate_unknown: Dict[str, int] = {}
    for row in rows:
        layer = str(row["blocking_layer"])
        variant = str(row["variant"])
        by_layer[layer] = by_layer.get(layer, 0) + 1
        by_variant_layer[f"{variant}:{layer}"] = by_variant_layer.get(f"{variant}:{layer}", 0) + 1
        key = f"{variant}:{row['setup_status']}"
        by_variant_status[key] = by_variant_status.get(key, 0) + 1
        if row.get("setup_applied"):
            by_layer_armed[layer] = by_layer_armed.get(layer, 0) + 1
            for gate, verdict in (row.get("gate_verdicts") or {}).items():
                state = (verdict or {}).get("state")
                if state == "fail":
                    by_gate_fail[gate] = by_gate_fail.get(gate, 0) + 1
                elif state == "unknown":
                    gate_unknown[gate] = gate_unknown.get(gate, 0) + 1
    return {
        "rows": len(rows),
        "by_layer": dict(sorted(by_layer.items())),
        "by_layer_armed": dict(sorted(by_layer_armed.items())),
        "by_gate_fail_armed": dict(sorted(by_gate_fail.items())),
        "by_gate_unknown_armed": dict(sorted(gate_unknown.items())),
        "by_variant_layer": dict(sorted(by_variant_layer.items())),
        "by_variant_status": dict(sorted(by_variant_status.items())),
        "observation_reminders": len(reminders),
        "reminder_boards": sorted({str(r["board_name"]) for r in reminders}),
    }
