# -*- coding: utf-8 -*-
"""Node 6 run() 级冒烟测试（批次 5 审查修复 #2）。

背景：批次 5 审查发现 aggregator.run() 内裸 `os.getenv` 因缺模块级 `import os`
而 NameError 崩溃，但 25 个新测试全为静态方法级，全仓库无 run() 级测试，
导致该缺陷在生产合入且域回归 86 passed 未捕获。本文件补上该盲区：
以最小合法 context 走完 `AggregatorNode.run(replay_mode=True)`，
断言产出 UnifiedContext 且批次 5 影子字段非空。

replay_mode=True：跳过状态机/持久化/EMA/监控/glue 落盘副作用，零外部 IO。
"""

from src.got.schemas import (
    CapitalContext,
    KeyLevels,
    MarketContext,
    NewsContext,
    RegimeMetadata,
    SentimentContext,
    SectorContext,
    Signal,
    StyleSignal,
    TechGrowthSignal,
    TimeframeGOTResult,
    TimeframeSignals,
)


def _market_ctx() -> MarketContext:
    sig = Signal(trend="bullish", score=0.5)
    signals = TimeframeSignals(short_term=sig, medium_term=sig, long_term=sig)
    style = StyleSignal(trend="bullish", score=0.5, signals=signals)
    tech = TechGrowthSignal(
        trend="bullish",
        score=0.6,
        signals=signals,
        timeframe_got=TimeframeGOTResult(unified_trend="bullish", unified_score=0.6),
        regime_metadata=RegimeMetadata(
            adx_value=25.0, regime="trending", volatility_regime="normal",
        ),
    )
    return MarketContext(
        tech_growth=tech,
        mid_small_cap=style,
        large_cap=style,
        position_limit=0.6,
        confidence=0.7,
        key_levels=KeyLevels(),
    )


def _capital_ctx() -> CapitalContext:
    return CapitalContext(
        status="normal",
        institutional_attitude="neutral",
        institutional_score=0.1,
        confidence_multiplier=1.0,
    )


def _news_ctx() -> NewsContext:
    return NewsContext(overall_impact="neutral", impact_score=0.0)


def _sentiment_ctx() -> SentimentContext:
    return SentimentContext(
        overall_sentiment="neutral",
        sentiment_score=0.0,
        sentiment_stage="recovery",
        trading_signal="hold",
        signal_confidence=0.5,
    )


def test_run_smoke_produces_unified_context_with_batch5_shadow():
    """run() 冒烟：批次 5 审查修复 #2 的回归锚点。

    修复前：run() 在 L326/L397 裸 os.getenv 因缺模块级 import os 抛 NameError，
    本测试是仓库首个覆盖 run() 主路径的测试，修复前必失败（NameError），
    修复后必须产出含 3b/4b 影子字段的 UnifiedContext。
    """
    import asyncio

    from src.got.nodes.aggregator import AggregatorNode

    # use_mock=False：默认 True 会在 run() 入口短路返回 _mock_output()，走不到批次 5 段
    node = AggregatorNode(use_mock=False)
    unified = asyncio.run(node.run(
        _market_ctx(),
        SectorContext(),
        _capital_ctx(),
        _news_ctx(),
        _sentiment_ctx(),
        replay_mode=True,
    ))

    # 主产物健全性
    assert unified.status in ("normal", "manual_review_required")
    assert 0.0 <= unified.overall_confidence <= 1.0
    assert 0.0 <= unified.final_position_limit <= 1.0

    # 批次 5 影子观测（默认 GOT_DISAGREEMENT_SHADOW=1 / GOT_MARKET_THEME_SHADOW=1）
    assert unified.conflict_summary_shadow is not None, "3b 分歧摘要影子字段不应为空"
    assert "conflict_type" in unified.conflict_summary_shadow
    assert unified.market_theme_shadow is not None, "4b 市场结构影子字段不应为空"
    assert "status" in unified.market_theme_shadow
