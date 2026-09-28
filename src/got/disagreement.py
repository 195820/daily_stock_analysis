# -*- coding: utf-8 -*-
"""Phase 3b — 分歧摘要 + RiskOverridePlan（批次 5 §3 / remote_integration_plan.md §8）

**本地基座 + 远端分类学移植**（09-01 核查修正：不从远端整抄 `disagreement.py`）：
- 复用 `src/agent/protocols.AgentOpinion` / `normalize_decision_signal`，不新造数据结构；
- 加权共识对齐 `src/agent/skills/aggregator.SkillAggregator` 的「信号分制 + 权重归一」；
- 权重来源＝各节点先验常量表，`AgentMemory` 校准为**可选注入**（`memory=None` 退化为等权——
  避免被 §14.1 A3 休眠项阻塞）；
- 移植 11 类冲突分类学 `classify_conflict_type` + `RiskOverridePlan`（§8.2）。

⚠️ 分类学完整性说明：`remote_integration_plan.md` §8.2 表仅**显式列出 7 类**
（`risk_override` / `mixed_directional_signals` / `aligned_bullish` / `aligned_bearish` /
`aligned_neutral` / `partial_bullish_with_degraded_inputs` / `degraded_only`），其余以「...」省略、
远端原文本仓不存在。本模块按同一 {方向多数 × degraded × risk} 维度**补全余 4 类**
（`bullish_with_risk_caution` / `mixed_with_degraded_inputs` /
`partial_bearish_with_degraded_inputs` / `neutral_with_degraded_inputs`），
标 TODO 待远端原文核对。**默认影子（`GOT_DISAGREEMENT_SHADOW=1`）只落观测、不改任何决策**；
enabled 转正归负责人 + 需影子期读数（计划 §9.3）。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from src.agent.protocols import AgentOpinion, normalize_decision_signal

logger = logging.getLogger(__name__)

# ── 信号分制（对齐 SkillAggregator._SIGNAL_SCORES，1~5 分制）──
_SIGNAL_SCORES: Dict[str, float] = {
    "strong_buy": 5.0,
    "buy": 4.0,
    "hold": 3.0,
    "sell": 2.0,
    "strong_sell": 1.0,
}
_SCORE_TO_SIGNAL = [
    (4.5, "strong_buy"),
    (3.5, "buy"),
    (2.5, "hold"),
    (1.5, "sell"),
    (0.0, "strong_sell"),
]

# ── 各节点先验权重常量表（TODO：AgentMemory 校准后作为 base × 校准因子；memory=None 退化等权）──
_SOURCE_PRIOR_WEIGHTS: Dict[str, float] = {
    "regime": 1.5,         # 市场环境（总纲，权重最高）
    "family_rank": 1.2,    # 家族梯队（Node 2 主线）
    "capital": 1.3,        # 资金面（Node 3）
    "catalyst_gate": 1.0,  # 催化剂门控（Node 4）
    "sentiment": 0.8,      # 情绪（Node 5）
    "sell_source": 1.0,    # 组合层卖出来源（风控向）
}

# ── 11 类冲突 → 决策路径提示（§8.2 decision_path_hint）──
_DECISION_PATH_HINTS: Dict[str, str] = {
    "risk_override": "优先风控，限制/暂停买入",
    "mixed_directional_signals": "跨节点方向分歧，需逐条解释后择重",
    "aligned_bullish": "一致看多，确认价格/风险后执行",
    "aligned_bearish": "一致看空，保持防守控制",
    "aligned_neutral": "一致中性，持仓观望",
    "partial_bullish_with_degraded_inputs": "看多但数据降级，声明数据限制后谨慎执行",
    "degraded_only": "仅数据降级/输入不足，声明数据限制，不主动开新仓",
    # ── 以下 4 类为 §8.2「...」省略部分的同维度补全（TODO 待远端原文核对）──
    "bullish_with_risk_caution": "看多但存在非否决风险警示，减仓/设严止损后执行",
    "mixed_with_degraded_inputs": "方向分歧叠加数据降级，优先补数据再仲裁",
    "partial_bearish_with_degraded_inputs": "看空但数据降级，声明限制后防守",
    "neutral_with_degraded_inputs": "中性叠加数据降级，观望为主",
}

_ALL_CONFLICT_TYPES: tuple = tuple(_DECISION_PATH_HINTS.keys())


@dataclass
class RiskOverridePlan:
    """风控覆盖计划（§8.2 远端设计，移植 + GOT 适配）。"""

    veto_buy: bool = False           # 否决买入（→ Phase 2 buy_budget=0）
    adjustment: str = ""             # "veto" | "downgrade_one" | "downgrade_two" | ""
    target_signal: str = ""          # 降级后信号
    reason: str = ""                 # "risk_veto" | "bearish_consensus" | ...

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# 信号映射（UnifiedContext / 节点对象 → AgentOpinion[]）
# =============================================================================

def _opinion(source: str, signal: str, confidence: float, *, degraded: bool = False,
             risk_caution: bool = False, reasoning: str = "") -> AgentOpinion:
    # 归一到 buy/hold/sell 稳定枚举（复用远端 protocols.normalize_decision_signal）
    return AgentOpinion(
        agent_name=source,
        signal=normalize_decision_signal(signal, default="hold"),
        confidence=max(0.0, min(1.0, float(confidence))),
        reasoning=reasoning,
        raw_data={"degraded": degraded, "risk_caution": risk_caution},
    )


def build_opinions(
    *,
    regime: Any = None,
    sector: Any = None,
    sector_intels: Optional[List[Any]] = None,
    capital: Any = None,
    sentiment: Any = None,
    sell_source: Optional[Dict[str, Any]] = None,
) -> List[AgentOpinion]:
    """把各节点信号映射为 `AgentOpinion[]`（缺失输入 → 该源标 degraded，不静默丢弃）。

    各映射为先验占位（TODO：随样本校准），保证**给定输入输出确定**、可测。
    """
    opinions: List[AgentOpinion] = []

    # ── regime（Node 1 总纲）──
    if regime is not None:
        rg = getattr(regime, "regime", None)
        sig = {"bull": "buy", "bear": "sell"}.get(rg, "hold")
        conf = float(getattr(regime, "cross_index_consistency", 0.5) or 0.5)
        opinions.append(_opinion("regime", sig, conf, reasoning=f"regime={rg}"))
    else:
        opinions.append(_opinion("regime", "hold", 0.0, degraded=True, reasoning="regime 缺失"))

    # ── family_rank（Node 2 主线梯队）──
    if sector is not None:
        active = getattr(sector, "active_mainline", None) or []
        sig = "buy" if len(active) > 0 else "hold"
        conf = min(1.0, len(active) / 3.0) if active else 0.3
        opinions.append(_opinion("family_rank", sig, conf, reasoning=f"active_mainline={len(active)}"))
    else:
        opinions.append(_opinion("family_rank", "hold", 0.0, degraded=True, reasoning="sector 缺失"))

    # ── capital（Node 3 资金面，institutional_score 约 [-1,1]）──
    if capital is not None and getattr(capital, "institutional_score", None) is not None:
        score = float(capital.institutional_score or 0.0)
        sig = "buy" if score >= 0.3 else ("sell" if score <= -0.3 else "hold")
        opinions.append(_opinion("capital", sig, abs(score), reasoning=f"institutional={score:+.2f}"))
    else:
        opinions.append(_opinion("capital", "hold", 0.0, degraded=True, reasoning="资金面缺失"))

    # ── catalyst_gate（Node 4，actionable 板块可用性）──
    if sector_intels is not None:
        actionable = sum(1 for s in sector_intels if getattr(s, "classification", "") == "actionable")
        sig = "buy" if actionable > 0 else "hold"
        conf = min(1.0, actionable / 3.0) if actionable else 0.3
        opinions.append(_opinion("catalyst_gate", sig, conf, reasoning=f"actionable={actionable}"))
    else:
        opinions.append(_opinion("catalyst_gate", "hold", 0.0, degraded=True, reasoning="板块情报缺失"))

    # ── sentiment（Node 5 情绪，过度贪婪转为 risk_caution）──
    if sentiment is not None and getattr(sentiment, "fear_greed", None) is not None:
        fg = float(sentiment.fear_greed or 0.5)
        caution = fg >= 0.8  # 极端贪婪 → 非否决风险警示
        opinions.append(_opinion(
            "sentiment", "hold", 0.3, risk_caution=caution,
            reasoning=f"fear_greed={fg:.2f}",
        ))
    else:
        opinions.append(_opinion("sentiment", "hold", 0.0, degraded=True, reasoning="情绪面缺失"))

    # ── sell_source（组合层风控，仅 PD 上下文有；有减仓 → sell + risk_caution）──
    if sell_source:
        trims = sell_source.get("planned_trims") or []
        mode = sell_source.get("mode", "")
        opinions.append(_opinion(
            "sell_source", "sell" if trims else "hold", 0.6 if trims else 0.2,
            risk_caution=bool(trims),
            reasoning=f"sell_source mode={mode} trims={len(trims)}",
        ))

    return opinions


# =============================================================================
# 加权共识 + 权重解析
# =============================================================================

def compute_signal_weights(opinions: List[AgentOpinion], memory: Any = None) -> Dict[str, float]:
    """解析各 opinion 权重。

    `memory=None` → **退化等权**（每项 1.0，对齐 §14.1 A3 休眠项不阻塞）；
    否则用先验常量表 `_SOURCE_PRIOR_WEIGHTS` 作 base，乘 memory 校准因子（缺省 1.0）。
    TODO：`AgentMemory` 校准接线（远端 §8.4-1 / §14），本批仅落地接口位。
    """
    if memory is None:
        return {op.agent_name: 1.0 for op in opinions}
    weights: Dict[str, float] = {}
    calib = getattr(memory, "compute_skill_weights", None)
    perf = calib([op.agent_name for op in opinions]) if callable(calib) else {}
    for op in opinions:
        base = _SOURCE_PRIOR_WEIGHTS.get(op.agent_name, 1.0)
        weights[op.agent_name] = base * float(perf.get(op.agent_name, 1.0) or 1.0)
    return weights


def weighted_consensus(
    opinions: List[AgentOpinion], weights: Dict[str, float]
) -> Dict[str, Any]:
    """信号分制加权共识（对齐 SkillAggregator.aggregate），返回 score/signal/confidence。"""
    if not opinions:
        return {"weighted_score": 3.0, "signal": "hold", "confidence": 0.0}
    total_w = sum(weights.get(op.agent_name, 1.0) for op in opinions) or 1.0
    w_score = sum(_SIGNAL_SCORES.get(op.signal, 3.0) * weights.get(op.agent_name, 1.0) for op in opinions) / total_w
    w_conf = sum(op.confidence * weights.get(op.agent_name, 1.0) for op in opinions) / total_w
    final = "strong_sell"
    for threshold, sig in _SCORE_TO_SIGNAL:
        if w_score >= threshold:
            final = sig
            break
    return {"weighted_score": round(w_score, 3), "signal": final, "confidence": round(min(1.0, w_conf), 3)}


# =============================================================================
# 冲突分类学（11 类，§8.2）
# =============================================================================

def _direction(op: AgentOpinion) -> str:
    score = _SIGNAL_SCORES.get(op.signal, 3.0)
    if score > 3.0:
        return "bull"
    if score < 3.0:
        return "bear"
    return "neutral"


def classify_conflict_type(
    opinions: List[AgentOpinion], *, risk_override_present: bool = False
) -> Dict[str, str]:
    """返回 {"conflict_type", "decision_path_hint"}（11 类，优先级级联，互斥）。"""
    if risk_override_present:
        return {"conflict_type": "risk_override", "decision_path_hint": _DECISION_PATH_HINTS["risk_override"]}

    degraded_present = any(op.raw_data.get("degraded") for op in opinions)
    soft_risk = any(op.raw_data.get("risk_caution") for op in opinions)
    valid = [op for op in opinions if not op.raw_data.get("degraded")]

    if not valid:
        return {"conflict_type": "degraded_only", "decision_path_hint": _DECISION_PATH_HINTS["degraded_only"]}

    dirs = [_direction(op) for op in valid]
    has_bull, has_bear = "bull" in dirs, "bear" in dirs

    if has_bull and has_bear:
        ct = "mixed_with_degraded_inputs" if degraded_present else "mixed_directional_signals"
    elif has_bull:
        if degraded_present:
            ct = "partial_bullish_with_degraded_inputs"
        elif soft_risk:
            ct = "bullish_with_risk_caution"
        else:
            ct = "aligned_bullish"
    elif has_bear:
        ct = "partial_bearish_with_degraded_inputs" if degraded_present else "aligned_bearish"
    else:
        ct = "neutral_with_degraded_inputs" if degraded_present else "aligned_neutral"

    return {"conflict_type": ct, "decision_path_hint": _DECISION_PATH_HINTS[ct]}


def derive_risk_override_plan(
    conflict_type: str, *, risk_override_present: bool = False
) -> RiskOverridePlan:
    """把冲突类型转为 RiskOverridePlan（§8.3：风控否决 > 既有降级规则）。"""
    if conflict_type == "risk_override" or risk_override_present:
        return RiskOverridePlan(
            veto_buy=True, adjustment="veto", target_signal="hold", reason="risk_veto",
        )
    if conflict_type in ("aligned_bearish", "partial_bearish_with_degraded_inputs"):
        return RiskOverridePlan(adjustment="downgrade_one", target_signal="hold", reason="bearish_consensus")
    if conflict_type in ("mixed_directional_signals", "mixed_with_degraded_inputs"):
        return RiskOverridePlan(adjustment="downgrade_one", target_signal="hold", reason="directional_conflict")
    if conflict_type == "bullish_with_risk_caution":
        return RiskOverridePlan(adjustment="downgrade_one", target_signal="hold", reason="risk_caution")
    # aligned_bullish / aligned_neutral / neutral_with_degraded_inputs /
    # partial_bullish_with_degraded_inputs / degraded_only → 不覆盖（仅声明，不改预算）
    return RiskOverridePlan()


# =============================================================================
# 分歧摘要（§8.2 build_agent_disagreement_summary）
# =============================================================================

def build_disagreement_summary(
    opinions: List[AgentOpinion],
    *,
    risk_override_present: bool = False,
    memory: Any = None,
) -> Dict[str, Any]:
    """产出 §8.2 结构：bullish/bearish/neutral agents + conflict_type + hint + risk_control。"""
    weights = compute_signal_weights(opinions, memory=memory)
    consensus = weighted_consensus(opinions, weights)
    cls = classify_conflict_type(opinions, risk_override_present=risk_override_present)
    plan = derive_risk_override_plan(cls["conflict_type"], risk_override_present=risk_override_present)

    def _pack(op: AgentOpinion) -> Dict[str, Any]:
        return {"agent_name": op.agent_name, "signal": op.signal, "confidence": round(op.confidence, 3)}

    return {
        "bullish_agents": [_pack(op) for op in opinions if _direction(op) == "bull"],
        "bearish_agents": [_pack(op) for op in opinions if _direction(op) == "bear"],
        "neutral_agents": [_pack(op) for op in opinions if _direction(op) == "neutral"],
        "conflict_type": cls["conflict_type"],
        "decision_path_hint": cls["decision_path_hint"],
        "risk_override_present": risk_override_present,
        "consensus": consensus,
        "risk_control": plan.to_dict(),
        "weights_equal": memory is None,
    }
