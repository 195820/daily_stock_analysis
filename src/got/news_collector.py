"""Node 4 新闻快照定时采集（GOT 执行之外，零 LLM）。

复用 NewsAnalysisNode 的采集+持久化路径，只采滚动新闻线
（新浪/CLS/华尔街见闻/东财/NewsNow），跳过 NGA/同花顺热榜与 Intelligence 热榜——
后三者不是按小时失效的时效源，高频抓取只会增加反爬风险，仍由每日 GOT 全量采集。
落库行由 GOT 运行时 _load_unabsorbed_titles 补读。

P0-1：快照同样落热榜元数据（`platform`/`platforms`/`rank`/`heat`/`snapshot_count`）——
NewsNow 8 平台不在跳过清单内，每小时快照即形成「盘中在榜轨迹」（当日多快照的
snapshot_count + 名次变化）。同花顺热榜仍跳过（反爬先例），其轨迹只随每日 GOT 采集一次。
"""
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 快照跳过的源：论坛/热榜/情报热榜（非时效源，反爬风险高）
SNAPSHOT_SKIP_SOURCES = ("nga_forum", "ths_hot")


async def collect_news_snapshot() -> Dict[str, Any]:
    """单次快照：5 个滚动新闻源采集 → node4_raw_news 落库（零 LLM）。

    Returns:
        {"raw": int, "persisted": int, "updated": int,
         "available_fields": list[str], "missing_fields": list[str]}
    Raises:
        采集整体异常向上抛（main.py 分支转退出码 1）。
    """
    from src.got.nodes.news_analysis import NewsAnalysisNode

    node = NewsAnalysisNode(use_mock=False)
    raw_news, raw_pool, data_quality = await node._fetch_news(
        skip_sources=SNAPSHOT_SKIP_SOURCES,
        include_intelligence=False,
    )
    if not raw_news:
        logger.warning("[news-collect] 未获取到任何新闻（全部源失败）")
        return {"raw": 0, "persisted": 0, "updated": 0,
                "available_fields": [], "missing_fields": []}
    # 落库用未截断的全量池（含 NewsNow 热榜：无发布时间 → 不在 merged 的 weight 前 150）
    persisted, updated = node._persist_raw_news(raw_pool)
    return {
        "raw": len(raw_pool),
        "persisted": persisted,
        "updated": updated,
        "available_fields": data_quality.available_fields,
        "missing_fields": data_quality.missing_fields,
    }
