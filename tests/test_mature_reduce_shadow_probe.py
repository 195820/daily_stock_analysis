# -*- coding: utf-8 -*-
"""C-B2 `mature_reduce_shadow` 三态语义守卫测试（A1 #3，2026-10-12）。

背景：该字段原为裸 list，`[]` 同时对应三种完全不同的情况——① 无账户 ② 有账户但无持仓
③ 有持仓但无标的落入 (14, 现阈值] 成熟窗口——读影子判据时无法归因（与同日发现的
antibias「零产物零告警」同谱系）。现改为自描述 dict：

    {hits: [...], holdings_seen: int, skipped_reason: Optional[str]}
    · 字段=null                 → 本次 Phase 2 根本没调 _score_holdings
    · skipped_reason=...        → 跑了但无可观测对象，hits 必空
    · holdings_seen>0 且 hits 空 → 真读数（确实没有到期持仓）

⚠️ 持仓模块大改（真相源 / 入场锚 / 退出分层）时本三态须一并返工，见
   docs/got/plans/持仓模块重构_待分析与待实施清单_20261010.md §三 WI-P-6。

测试策略：只测盘点留痕的状态机与其写入时机，不测逐持仓评分（那属其它用例的范围）。
上下文构建类 helper 全部桩掉 ⇒ 不依赖 Node 6/2 输入、不触网。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.got.nodes.portfolio_decision import PortfolioDecisionNode
from src.got.schemas import (
    CapitalContext,
    DataQuality,
    NewsContext,
    PortfolioDecision,
    PortfolioRiskAssessment,
    UnifiedScoringResult,
)

_PROBE_KEYS = {"hits", "holdings_seen", "skipped_reason"}

# _score_holdings 进入逐持仓循环前会调用的上下文/取数 helper，全部桩掉
_PRE_LOOP_PATCHES = (
    "_build_sector_strength_map", "_build_sector_intel_map",
    "_build_extended_lifecycle_map", "_extract_capital_overall",
    "_extract_news_overall", "_batch_fetch_tech_scores",
    "_load_ambush_snaphots", "_load_holding_theses",
    "_build_family_anchor_map", "_batch_fetch_hwm",
)


def _patched(node, extra=None):
    """进入循环前的 helper 桩；extra 为 {方法名: 返回值}，未列出的返回 {}。"""
    patches = []
    for name in _PRE_LOOP_PATCHES:
        ret = (extra or {}).get(name, {} if not name.startswith("_extract") else 0.0)
        patches.append(patch.object(node, name, return_value=ret))
    return patches


def _capital_ctx() -> CapitalContext:
    """最小可用：循环前段有一处 `capital_context.model_dump()`，不能传 None。"""
    return CapitalContext(
        status="normal",
        institutional_attitude="neutral",
        institutional_score=0.1,
        confidence_multiplier=1.0,
    )


def _news_ctx() -> NewsContext:
    return NewsContext(overall_impact="neutral", impact_score=0.0)


def _score(node, portfolio):
    with_patch = _patched(node)
    for p in with_patch:
        p.start()
    try:
        return node._score_holdings(
            portfolio, None, None, _capital_ctx(), _news_ctx(), None,  # type: ignore[arg-type]
        )
    finally:
        for p in with_patch:
            p.stop()


class _Sentinel(Exception):
    """用于在循环体第一行中断，以验证盘点字段是在逐持仓评分之前写入的。"""


def test_missing_accounts_marks_no_accounts():
    """accounts 缺失/为空 → skipped_reason=no_accounts，hits 必空。"""
    node = PortfolioDecisionNode()
    _score(node, {"accounts": []})
    probe = node._mature_reduce_shadow_hits
    assert probe.keys() == _PROBE_KEYS, "盘点结构变更须同步 verification.md 五线表与 schemas 描述"
    assert probe["skipped_reason"] == "no_accounts"
    assert probe["hits"] == [] and probe["holdings_seen"] == 0


def test_empty_positions_marks_no_holdings_not_true_zero():
    """有账户、零持仓 → no_holdings：不得被读成「今天没有到期持仓」这条真读数。"""
    node = PortfolioDecisionNode()
    _score(node, {"accounts": [{"positions": []}]})
    probe = node._mature_reduce_shadow_hits
    assert probe["skipped_reason"] == "no_holdings"
    assert probe["holdings_seen"] == 0 and probe["hits"] == []


def test_holdings_seen_recorded_before_per_position_scoring():
    """有持仓 → holdings_seen 计数、skipped_reason 保持 None（真读数的前置）。

    循环体第一行（_resolve_holding_name）抛哨兵中断，证明盘点字段在逐持仓评分之前
    就已写好——不依赖任何行情/LLM，也不受循环内逻辑影响。
    """
    node = PortfolioDecisionNode()
    with patch.object(node, "_resolve_holding_name", side_effect=_Sentinel()):
        patches = _patched(node)
        for p in patches:
            p.start()
        try:
            with pytest.raises(_Sentinel):
                node._score_holdings(
                    {"accounts": [{"positions": [{"symbol": "600000", "name": "测试"}]}]},
                    None, None, _capital_ctx(), _news_ctx(), None,  # type: ignore[arg-type]
                )
        finally:
            for p in patches:
                p.stop()
    probe = node._mature_reduce_shadow_hits
    assert probe["holdings_seen"] == 1
    assert probe["skipped_reason"] is None, "有持仓时不得标 skipped_reason，否则真读数会被当成跳过"


def test_never_called_yields_null_distinct_from_empty():
    """第三态：未跑 _score_holdings 时落盘端取到 None（原样保留，不伪造成 []）。

    锁 portfolio_decision.py 落盘处 `getattr(self, "_mature_reduce_shadow_hits", None)`
    依赖的前提：属性不存在 ⇒ None。
    """
    node = PortfolioDecisionNode()
    assert getattr(node, "_mature_reduce_shadow_hits", None) is None


def test_schema_accepts_dict_and_defaults_none():
    """类型变更守卫：Optional[dict] 能收三态结构，且默认 None。"""
    base = dict(
        status="normal",
        risk_assessment=PortfolioRiskAssessment(
            position_limit=0.5, current_position_ratio=0.2, constraint_reasoning="测试约束",
        ),
        unified_scoring=[UnifiedScoringResult(
            symbol="600030", name="中信证券", is_holding=False, composite_score=72.0,
            dimension_scores={"capital": 30.0, "news": 10.0, "sector": 12.0,
                              "tech": 10.0, "risk": 10.0},
            action_recommendation="hold", confidence=0.6, reasoning="测试",
        )],
        allocation=[], summary="测试总结", warnings=[], reasoning="测试推理",
        data_quality=DataQuality(),
    )
    assert PortfolioDecision(**base).mature_reduce_shadow is None
    decision = PortfolioDecision(**base, mature_reduce_shadow={
        "hits": [{"symbol": "600000", "sector": "银行", "lifecycle_day": 20,
                  "action_kept": "hold"}],
        "holdings_seen": 3, "skipped_reason": None,
    })
    assert decision.mature_reduce_shadow["hits"][0]["symbol"] == "600000"
    assert decision.mature_reduce_shadow["holdings_seen"] == 3
