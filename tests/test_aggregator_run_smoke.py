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
    )


def _news_ctx() -> NewsContext:
    return NewsContext(overall_impact="neutral", impact_score=0.0)


def _sentiment_ctx() -> SentimentContext:
    return SentimentContext(
        overall_sentiment="neutral",
        sentiment_score=0.0,
        sentiment_stage="recovery",
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


def test_run_rejects_a6_disabled():
    """run() 级语义锁：`GOT_A6_ENABLED=false` 是 **fail-fast，不是回滚**（2026-10-12）。

    背景：`catalyst_config.py` 与【backlog】§4.3 曾四处声称“设 false 即可回到影子期
    『归档不消费』”。逐行核代码不成立：`aggregator.run()` 入口对 `not GOT_A6_ENABLED`
    直接 raise RuntimeError；`AGENT_SEMANTIC_GLUE_ENABLED=false` 同一句式 raise；
    `AGENT_SEMANTIC_GLUE_SHADOW=true` 只改日志与 `[SHADOW]` 标记、不拦 Pass 1.5 消费。
    且规则侧三件套已随 A6 阶段 2（2026-09-26）退役 ⇒ 已无“影子期行为”可回。

    另 `test_a6_wiring.py::TestC3` 直接调 `_build_sector_intel` 而绕开了本守卫，所以它绿
    并不能证明可回滚——本用例负责把“真实入口行为”钉住：若日后有人静默去掉 raise 或反向
    开放热回退，本用例先响，强制他回去同步 §4.3 / §8 #15 与回滚叙述。
    """
    import asyncio

    import pytest

    from src.got.nodes.aggregator import AggregatorNode

    node = AggregatorNode(use_mock=False)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.got.nodes.aggregator.GOT_A6_ENABLED", False)
        with pytest.raises(RuntimeError, match="GOT_A6_ENABLED"):
            asyncio.run(node.run(
                _market_ctx(),
                SectorContext(),
                _capital_ctx(),
                _news_ctx(),
                _sentiment_ctx(),
                replay_mode=True,
            ))

