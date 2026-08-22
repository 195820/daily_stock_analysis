"""
GOT (Graph of Thought) 市场分析架构

Phase 1: 六节点市场分析
- Node 1: 大盘分析（ADX调权/多时间框架 mini GOT）
- Node 2: 板块分析（启动信号/生命周期/主线分级）
- Node 3: 资金面分析（5维机构评分/降级机制）
- Node 4: 消息面分析（3并发LLM/多源确认）
- Node 5: 情绪面分析（反身性5阶段）
- Node 6: 聚合节点 v2（置信度加权聚合 + 条件迭代精炼）
"""

from src.got.schemas import (
    MainlineGroup,
    MarketContext,
    SectorContext,
    CapitalContext,
    NewsContext,
    SentimentContext,
    UnifiedContext,
)
from src.got.orchestrator import GOTOrchestrator

__all__ = [
    "GOTOrchestrator",
    "MainlineGroup",
    "MarketContext",
    "SectorContext",
    "CapitalContext",
    "NewsContext",
    "SentimentContext",
    "UnifiedContext",
]
