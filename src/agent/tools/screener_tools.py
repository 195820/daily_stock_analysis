# -*- coding: utf-8 -*-
"""
===================================
选股 Agent Tools
===================================

提供选股相关工具，供 Agent 调用：
- get_forum_sentiment: 查询股票舆情
- get_sector_rotation: 获取板块轮动分析
- get_sector_constituents: 查询板块成分股
"""

import logging

from src.agent.tools.registry import ToolParameter, ToolDefinition

logger = logging.getLogger(__name__)


# ============================================================
# get_forum_sentiment
# ============================================================

def _handle_get_forum_sentiment(stock_code: str) -> dict:
    """查询股票在论坛的舆情数据。"""
    try:
        from src.services.forum_sentiment import ForumSentimentService
        service = ForumSentimentService()
        result = service.get_stock_sentiment(stock_code)

        if not result:
            return {
                "stock_code": stock_code,
                "found": False,
                "message": f"未找到 {stock_code} 的舆情数据",
            }

        return {
            "stock_code": stock_code,
            "found": True,
            "data": result,
        }
    except Exception as e:
        logger.error(f"查询舆情失败 {stock_code}: {e}")
        return {"stock_code": stock_code, "error": str(e)}


get_forum_sentiment_tool = ToolDefinition(
    name="get_forum_sentiment",
    description="查询指定股票在 NGA 大时代论坛的舆情数据，包含热度、情绪倾向、提及次数等。"
                "用于判断市场关注度和散户情绪。",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="股票代码，如 '600519', '000858'",
            required=True,
        ),
    ],
    handler=_handle_get_forum_sentiment,
    category="sentiment",
)


# ============================================================
# get_sector_rotation
# ============================================================

def _handle_get_sector_rotation(top_n: int = 10, board_type: str = "concept") -> dict:
    """获取板块轮动分析。"""
    try:
        from src.services.sector_rotation import SectorRotationService
        service = SectorRotationService()
        result = service.get_rotation_analysis(top_n=top_n)

        if not result:
            return {"error": "板块轮动分析数据不可用"}

        return {
            "analysis_date": result.get("analysis_date", ""),
            "strong_sectors": result.get("strong_sectors", []),
            "rising_sectors": result.get("rising_sectors", []),
            "declining_sectors": result.get("declining_sectors", []),
            "market_phase": result.get("market_phase", ""),
            "summary": result.get("summary", ""),
        }
    except Exception as e:
        logger.error(f"板块轮动分析失败: {e}")
        return {"error": str(e)}


get_sector_rotation_tool = ToolDefinition(
    name="get_sector_rotation",
    description="获取板块轮动分析，包含强势板块、崛起板块、衰退板块。"
                "用于判断当前市场热点和资金流向。",
    parameters=[
        ToolParameter(
            name="top_n",
            type="integer",
            description="返回板块数量（默认 10）",
            required=False,
            default=10,
        ),
        ToolParameter(
            name="board_type",
            type="string",
            description="板块类型: 'concept' 概念板块, 'industry' 行业板块（默认 concept）",
            required=False,
            default="concept",
            enum=["concept", "industry"],
        ),
    ],
    handler=_handle_get_sector_rotation,
    category="market",
)


# ============================================================
# get_sector_constituents
# ============================================================

def _handle_get_sector_constituents(board_code: str, top_n: int = 10) -> dict:
    """查询指定板块的成分股列表。"""
    try:
        from src.services.sector_rotation import SectorRotationService
        service = SectorRotationService()
        stocks = service.get_sector_stocks(board_code, top_n=top_n)

        if not stocks:
            return {
                "board_code": board_code,
                "found": False,
                "message": f"未找到板块 {board_code} 的成分股数据",
            }

        return {
            "board_code": board_code,
            "found": True,
            "count": len(stocks),
            "stocks": stocks,
        }
    except Exception as e:
        logger.error(f"查询板块成分股失败 {board_code}: {e}")
        return {"board_code": board_code, "error": str(e)}


get_sector_constituents_tool = ToolDefinition(
    name="get_sector_constituents",
    description="查询指定板块的成分股列表（按涨跌幅降序）。"
                "用于查看板块内个股表现，识别龙头股。",
    parameters=[
        ToolParameter(
            name="board_code",
            type="string",
            description="板块代码，如 'BK0477'（锂电池）、'BK0655'（人工智能）",
            required=True,
        ),
        ToolParameter(
            name="top_n",
            type="integer",
            description="返回股票数量（默认 10）",
            required=False,
            default=10,
        ),
    ],
    handler=_handle_get_sector_constituents,
    category="market",
)


# ============================================================
# get_screener_results
# ============================================================

def _handle_get_screener_results(
    run_date: str = "",
    min_score: float = 60.0,
    limit: int = 20,
) -> dict:
    """获取选股结果。"""
    try:
        from src.repositories.screener_repository import ScreenerRepository
        repo = ScreenerRepository()

        if run_date:
            records = repo.get_results_by_date(run_date, min_score=min_score, limit=limit)
        else:
            records = repo.get_latest_results(limit=limit)

        if not records:
            return {
                "found": False,
                "message": "暂无选股结果",
                "stocks": [],
            }

        stocks = []
        for r in records:
            import json
            stocks.append({
                "code": r.stock_code,
                "name": r.stock_name,
                "score": r.composite_score,
                "sector": r.sector,
                "sector_strength": r.sector_strength,
                "forum_heat": r.forum_heat,
                "strategy_score": r.strategy_score,
                "recommendation": r.recommendation,
                "ai_reasoning": r.ai_reasoning[:80] if r.ai_reasoning else "",
                "risk_tags": json.loads(r.risk_tags_json) if r.risk_tags_json else [],
            })

        return {
            "found": True,
            "run_date": run_date or records[0].run_date,
            "count": len(stocks),
            "stocks": stocks,
        }
    except Exception as e:
        logger.error(f"获取选股结果失败: {e}")
        return {"error": str(e)}


get_screener_results_tool = ToolDefinition(
    name="get_screener_results",
    description="获取每日智能选股结果，包含综合评分、推荐级别、AI 点评等。"
                "不传日期则返回最新一次选股结果。",
    parameters=[
        ToolParameter(
            name="run_date",
            type="string",
            description="选股日期 YYYY-MM-DD（留空则返回最新结果）",
            required=False,
            default="",
        ),
        ToolParameter(
            name="min_score",
            type="number",
            description="最低评分过滤（默认 60）",
            required=False,
            default=60.0,
        ),
        ToolParameter(
            name="limit",
            type="integer",
            description="返回数量限制（默认 20）",
            required=False,
            default=20,
        ),
    ],
    handler=_handle_get_screener_results,
    category="data",
)


# ============================================================
# 导出
# ============================================================

ALL_SCREENER_TOOLS = [
    get_forum_sentiment_tool,
    get_sector_rotation_tool,
    get_sector_constituents_tool,
    get_screener_results_tool,
]
