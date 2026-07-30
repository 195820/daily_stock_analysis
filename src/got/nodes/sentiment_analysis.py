"""
Node 5: 情绪面分析节点

职责：采集散户情绪数据（雪球热门榜 + 市场宽度 + 搜索舆情），
     量化散户情绪，输出反身性信号。

设计理念：
- 聚焦散户情绪：与资金面（机构行为）形成对照
- 反身性阶段分类：5阶段（euphoria/optimism/neutral/pessimism/capitulation）（P2-2）
- 极端情绪为反向信号：散户狂热=顶部，散户恐慌=底部

数据来源：
- akshare 雪球热门榜（关注/交易/讨论 3 榜）
- akshare 市场宽度（涨跌家数、涨停跌停）
- SearchService 搜索舆情关键词
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from data_provider.base import DataFetcherManager
from src.got.schemas import (
    SentimentContext,
    HotStock,
    DataQuality,
)

logger = logging.getLogger(__name__)

# =============================================================================
# 雪球热榜参数
# =============================================================================

XUEQIU_TOP_N = 30  # 过滤后取前 30 只热门科技股
XUEQIU_TIMEOUT = 90  # 雪球 API 较慢，单次超时 90 秒

# 科技成长方向关键词（过滤雪球全量热榜，聚焦项目定位的科技成长赛道）
TECH_GROWTH_KEYWORDS = frozenset({
    # AI 产业链
    "AI", "人工智能", "芯片", "半导体", "算力", "光模块", "服务器",
    "大模型", "智算", "GPU", "存储", "算力", "液冷", "数据要素",
    # 机器人
    "机器人", "减速器", "伺服", "传感", "控制器", "具身", "人形",
    # 软件/信创
    "软件", "信创", "鸿蒙", "操作系统", "数据库", "中间件", "网络安全",
    "云计算", "SaaS", "数字",
    # 新能源（科技关联）
    "新能源", "储能", "光伏", "锂电", "氢能", "风电", "电动车",
    # 军工电子
    "军工", "航天", "卫星", "雷达", "电子对抗",
    # 通信
    "5G", "6G", "通信", "光纤", "基站",
    # 科技制造
    "PCB", "面板", "显示", "LED", "光学", "激光",
})

# =============================================================================
# 情绪搜索查询词
# =============================================================================

SENTIMENT_SEARCH_QUERIES = [
    "A股 今日 散户 情绪 恐贪",
    "股市 市场情绪 多空 资金",
    "股吧 热门讨论 散户",
]

# =============================================================================
# 反身性 5 阶段阈值（设计文档 P2-2，初始值，运行 3 个月后回测调整）
# =============================================================================

EUPHORIA_SCORE_THRESHOLD = 0.5
EUPHORIA_ATTENTION_THRESHOLD = 0.75
OPTIMISM_SCORE_THRESHOLD = 0.2
PESSIMISM_SCORE_THRESHOLD = -0.2
CAPITULATION_SCORE_THRESHOLD = -0.5

# 信号置信度
CONTRARIAN_CONFIDENCE = 0.7
TREND_CONFIDENCE = 0.5
NEUTRAL_CONFIDENCE = 0.3


class SentimentAnalysisNode:
    """情绪面分析节点"""

    def __init__(self, use_mock: bool = True):
        self.use_mock = use_mock
        self._fetcher_manager: Optional[DataFetcherManager] = None

    @property
    def fetcher(self) -> DataFetcherManager:
        """懒加载 DataFetcherManager。"""
        if self._fetcher_manager is None:
            self._fetcher_manager = DataFetcherManager()
        return self._fetcher_manager

    async def run(self) -> SentimentContext:
        """执行情绪面分析"""
        if self.use_mock:
            logger.info("[Node 5] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 5] 开始真实数据情绪面分析")

        # Step 1: 并发采集数据
        xueqiu_data, breadth_data, search_data, data_quality = await self._fetch_all_data()

        # Step 2: 计算情绪评分
        sentiment_score = self._calculate_sentiment_score(
            xueqiu_data, breadth_data, search_data,
        )
        sentiment_score = max(-1.0, min(1.0, sentiment_score))

        # Step 3: 反身性阶段分类
        stage, signal, confidence = self._classify_reflexivity(
            sentiment_score, xueqiu_data,
        )

        # Step 4: 整体判断
        overall = self._score_to_sentiment(sentiment_score)
        forum_mood = self._score_to_mood(sentiment_score)

        # Step 5: 热门股提取
        hot_stocks = self._extract_hot_stocks(xueqiu_data)

        # Step 6: 市场温度 + 散户关注度
        market_temp = self._calculate_market_temperature(breadth_data)
        retail_attention = self._calculate_retail_attention(xueqiu_data)

        # Step 7: 消息面偏离
        news_drift = self._classify_news_drift(search_data)

        # Step 8: LLM reasoning + hot_topics
        reasoning, hot_topics = await self._generate_reasoning(
            overall, sentiment_score, stage, forum_mood,
            hot_stocks, breadth_data, search_data,
        )

        has_partial_failure = any(
            f in data_quality.fetch_failed_fields
            for f in ["xueqiu", "breadth", "search"]
        )
        status = "degraded" if has_partial_failure else "normal"

        result = SentimentContext(
            status=status,
            overall_sentiment=overall,
            sentiment_score=sentiment_score,
            sentiment_stage=stage,
            trading_signal=signal,
            signal_confidence=confidence,
            forum_mood=forum_mood,
            hot_stocks=hot_stocks,
            hot_topics=hot_topics,
            retail_attention_count=retail_attention,
            market_temperature=market_temp,
            news_drift=news_drift,
            reasoning=reasoning,
            data_quality=data_quality,
        )

        logger.info(
            "[Node 5] 完成 — status=%s, score=%.2f, stage=%s, signal=%s",
            status, sentiment_score, stage, signal,
        )
        return result

    # =================================================================
    # Step 1: 并发数据采集
    # =================================================================

    async def _fetch_all_data(
        self,
    ) -> Tuple[Dict, Dict, Dict, DataQuality]:
        """并发采集 3 类数据：雪球热榜 + 市场宽度 + 搜索舆情。"""
        xueqiu_task = asyncio.create_task(self._fetch_xueqiu())
        breadth_task = asyncio.create_task(self._fetch_breadth())
        search_task = asyncio.create_task(self._fetch_sentiment_search())

        xueqiu_data, breadth_data, search_data = await asyncio.gather(
            xueqiu_task, breadth_task, search_task, return_exceptions=True,
        )

        available = []
        missing = []
        failed = []

        for name, data in [("xueqiu", xueqiu_data), ("breadth", breadth_data), ("search", search_data)]:
            if isinstance(data, Exception):
                logger.error("[Node 5] %s 采集失败: %s", name, data)
                failed.append(name)
            elif data:
                available.append(f"{name}_data")
            else:
                missing.append(f"{name}_data")

        # 异常降级为空 dict
        if isinstance(xueqiu_data, Exception):
            xueqiu_data = {}
        if isinstance(breadth_data, Exception):
            breadth_data = {}
        if isinstance(search_data, Exception):
            search_data = {}

        data_quality = DataQuality(
            available_fields=available,
            missing_fields=missing,
            fetch_failed_fields=failed,
        )
        return xueqiu_data, breadth_data, search_data, data_quality

    async def _fetch_xueqiu(self) -> Dict[str, Any]:
        """获取雪球 3 个热榜（关注/交易/讨论），取 top N。"""
        import akshare as ak

        async def _fetch_one(func_name: str, dim: str) -> List[Dict]:
            try:
                func = getattr(ak, func_name)
                df = await asyncio.to_thread(func, symbol="最热门")
                if df is None or df.empty:
                    return []

                # 科技成长过滤：股票简称匹配关键词
                def _is_tech(name: str) -> bool:
                    return any(kw in name for kw in TECH_GROWTH_KEYWORDS)

                mask = df["股票简称"].astype(str).apply(_is_tech)
                filtered = df[mask]

                records = []
                for _, row in filtered.head(XUEQIU_TOP_N).iterrows():
                    code_raw = str(row.get("股票代码", "")).strip()
                    code = code_raw.replace("SH", "").replace("SZ", "").replace("sh", "").replace("sz", "")
                    records.append({
                        "stock_code": code,
                        "stock_name": str(row.get("股票简称", "")).strip(),
                        "attention": int(row.get("关注", 0)),
                        "price": float(row.get("最新价", 0) or 0),
                        "dimension": dim,
                    })
                return records
            except Exception as exc:
                logger.warning("[Node 5] 雪球 %s 失败: %s", func_name, exc)
                return []

        follow, deal, tweet = await asyncio.gather(
            _fetch_one("stock_hot_follow_xq", "follow"),
            _fetch_one("stock_hot_deal_xq", "deal"),
            _fetch_one("stock_hot_tweet_xq", "tweet"),
        )

        return {
            "follow": follow,
            "deal": deal,
            "tweet": tweet,
            "total_count": len(follow) + len(deal) + len(tweet),
        }

    async def _fetch_breadth(self) -> Dict[str, Any]:
        """获取市场宽度数据（涨跌家数、涨停跌停）。

        通过 DataFetcherManager 代理（支持多源降级）。
        """
        def _fetch():
            return self.fetcher.get_market_breadth()

        try:
            breadth = await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.warning("[Node 5] 市场宽度获取失败: %s", exc)
            return {}

        if not breadth:
            return {}

        # 计算派生指标
        total = breadth.get('up_count', 0) + breadth.get('down_count', 0) + breadth.get('flat_count', 0)
        up_count = breadth.get('up_count', 0)
        down_count = breadth.get('down_count', 0)

        return {
            "total": total,
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": breadth.get('flat_count', 0),
            "limit_up": breadth.get('limit_up_count', 0),
            "limit_down": breadth.get('limit_down_count', 0),
            "up_ratio": up_count / max(total, 1),
            "down_ratio": down_count / max(total, 1),
            "avg_change": 0.0,  # Manager 不提供，后续可扩展
            "breadth_score": (up_count - down_count) / max(total, 1),
        }

    async def _fetch_sentiment_search(self) -> Dict[str, Any]:
        """搜索舆情关键词，获取市场情绪定性描述。"""
        from src.search_service import get_search_service

        service = get_search_service()
        if not service.is_available:
            return {"articles": [], "available": False}

        all_articles = []

        async def _search_one(query: str):
            try:
                response = await asyncio.to_thread(
                    service.search_market_news, query, 5, 3,
                )
                if response.success and response.results:
                    return [
                        {"title": r.title, "snippet": r.snippet, "source": r.source}
                        for r in response.results
                    ]
            except Exception as exc:
                logger.debug("[Node 5] 舆情搜索 '%s' 失败: %s", query, exc)
            return []

        results = await asyncio.gather(
            *[_search_one(q) for q in SENTIMENT_SEARCH_QUERIES],
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, list):
                all_articles.extend(r)

        return {"articles": all_articles, "available": bool(all_articles)}

    # =================================================================
    # Step 2: 情绪评分
    # =================================================================

    @staticmethod
    def _calculate_sentiment_score(
        xueqiu: Dict, breadth: Dict, search: Dict,
    ) -> float:
        """综合 3 个维度计算情绪评分（-1 ~ 1）。"""
        scores = []
        weights = []

        # 维度 1: 市场宽度（权重 0.50）— 最可靠
        if breadth:
            breadth_score = breadth.get("breadth_score", 0.0)
            scores.append(breadth_score)
            weights.append(0.50)

        # 维度 2: 雪球讨论热度偏移（权重 0.25）
        if xueqiu and xueqiu.get("tweet"):
            tweet_stocks = xueqiu["tweet"]
            avg_attention = (
                sum(s["attention"] for s in tweet_stocks) / len(tweet_stocks)
                if tweet_stocks else 0
            )
            # 热度越高 → 情绪越亢奋（正向偏移）
            if avg_attention > 50000:
                attention_score = 0.3
            elif avg_attention > 10000:
                attention_score = 0.1
            elif avg_attention > 1000:
                attention_score = 0.0
            else:
                attention_score = -0.2
            scores.append(attention_score)
            weights.append(0.25)

        # 维度 3: 搜索舆情（权重 0.25）
        if search and search.get("articles"):
            articles = search["articles"]
            # 简单关键词统计
            positive_words = frozenset({
                "看涨", "看多", "反弹", "突破", "利好", "放量", "强势",
                "大涨", "新高", "牛市", "加仓", "爆发", "强势",
            })
            negative_words = frozenset({
                "看空", "看跌", "暴跌", "利空", "减持", "套牢", "割肉",
                "大跌", "新低", "熊市", "崩盘", "出逃", "恐慌",
            })
            pos_count = 0
            neg_count = 0
            for article in articles:
                text = article.get("title", "") + article.get("snippet", "")
                pos_count += sum(1 for w in positive_words if w in text)
                neg_count += sum(1 for w in negative_words if w in text)
            total = pos_count + neg_count
            if total > 0:
                search_score = (pos_count - neg_count) / total
            else:
                search_score = 0.0
            scores.append(search_score)
            weights.append(0.25)

        if not scores:
            return 0.0

        total_weight = sum(weights)
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return weighted_sum / total_weight

    # =================================================================
    # Step 3: 反身性 5 阶段分类（设计文档 P2-2）
    # =================================================================

    @staticmethod
    def _classify_reflexivity(
        score: float, xueqiu: Dict,
    ) -> Tuple[str, str, float]:
        """
        反身性阶段分类。

        Returns:
            (sentiment_stage, trading_signal, signal_confidence)
        """
        # 计算散户关注度比率（用于判断极端情绪）
        attention_ratio = 0.0
        if xueqiu and xueqiu.get("tweet"):
            tweet_stocks = xueqiu["tweet"]
            avg_attention = (
                sum(s["attention"] for s in tweet_stocks) / len(tweet_stocks)
                if tweet_stocks else 0
            )
            # 归一化：>100k 为极高关注
            attention_ratio = min(avg_attention / 100000, 1.0)

        # 5 阶段分类
        if score > EUPHORIA_SCORE_THRESHOLD and attention_ratio > EUPHORIA_ATTENTION_THRESHOLD:
            return "euphoria", "contrarian_sell", CONTRARIAN_CONFIDENCE

        if score > OPTIMISM_SCORE_THRESHOLD:
            return "optimism", "trend_following", TREND_CONFIDENCE

        if abs(score) <= OPTIMISM_SCORE_THRESHOLD:
            return "neutral", "neutral", NEUTRAL_CONFIDENCE

        if score < CAPITULATION_SCORE_THRESHOLD and attention_ratio > 0.5:
            return "capitulation", "contrarian_buy", CONTRARIAN_CONFIDENCE

        if score < PESSIMISM_SCORE_THRESHOLD:
            return "pessimism", "trend_following", TREND_CONFIDENCE

        return "neutral", "neutral", NEUTRAL_CONFIDENCE

    # =================================================================
    # 辅助函数
    # =================================================================

    @staticmethod
    def _score_to_sentiment(score: float) -> str:
        if score > 0.3:
            return "bullish"
        if score < -0.3:
            return "bearish"
        return "neutral"

    @staticmethod
    def _score_to_mood(score: float) -> str:
        if score > 0.6:
            return "极度乐观"
        if score > 0.3:
            return "乐观"
        if score > 0.1:
            return "偏乐观"
        if score > -0.1:
            return "中性"
        if score > -0.3:
            return "偏悲观"
        if score > -0.6:
            return "悲观"
        return "极度悲观"

    @staticmethod
    def _extract_hot_stocks(xueqiu: Dict) -> List[HotStock]:
        """从雪球 3 榜合并提取热门股，按讨论热度排序。"""
        stock_map: Dict[str, Dict] = {}

        for dim in ["tweet", "deal", "follow"]:
            for stock in xueqiu.get(dim, []):
                code = stock["stock_code"]
                if code not in stock_map:
                    stock_map[code] = {
                        "stock_code": code,
                        "stock_name": stock["stock_name"],
                        "total_attention": 0,
                        "dimensions": set(),
                    }
                stock_map[code]["total_attention"] += stock["attention"]
                stock_map[code]["dimensions"].add(dim)

        sorted_stocks = sorted(
            stock_map.values(),
            key=lambda s: s["total_attention"],
            reverse=True,
        )[:XUEQIU_TOP_N]

        return [
            HotStock(
                stock_code=s["stock_code"],
                stock_name=s["stock_name"],
                mention_count=s["total_attention"],
                sentiment="positive",
                associated_sectors=[],
            )
            for s in sorted_stocks
        ]

    @staticmethod
    def _calculate_market_temperature(breadth: Dict) -> float:
        """市场温度：0=冰点, 1=沸点。"""
        if not breadth:
            return 0.5

        up_ratio = breadth.get("up_ratio", 0.5)
        limit_up = breadth.get("limit_up", 0)
        limit_down = breadth.get("limit_down", 0)

        # 综合涨跌比 + 涨停跌停比
        breadth_component = up_ratio
        limit_component = min(limit_up / max(limit_up + limit_down, 1), 1.0)

        temp = 0.6 * breadth_component + 0.4 * limit_component
        return max(0.0, min(1.0, temp))

    @staticmethod
    def _calculate_retail_attention(xueqiu: Dict) -> int:
        """散户关注度（雪球讨论总量）。"""
        tweet = xueqiu.get("tweet", [])
        return sum(s["attention"] for s in tweet)

    @staticmethod
    def _classify_news_drift(search: Dict) -> str:
        """消息面偏离方向。"""
        articles = search.get("articles", [])
        if not articles:
            return "neutral"

        positive_words = frozenset({"利好", "反弹", "突破", "看多", "强势", "大涨"})
        negative_words = frozenset({"利空", "暴跌", "看空", "减持", "大跌", "恐慌"})

        pos = 0
        neg = 0
        for a in articles:
            text = a.get("title", "") + a.get("snippet", "")
            pos += sum(1 for w in positive_words if w in text)
            neg += sum(1 for w in negative_words if w in text)

        if pos > neg * 1.5:
            return "positive"
        if neg > pos * 1.5:
            return "negative"
        return "neutral"

    # =================================================================
    # LLM reasoning
    # =================================================================

    async def _generate_reasoning(
        self,
        overall: str,
        score: float,
        stage: str,
        mood: str,
        hot_stocks: List[HotStock],
        breadth: Dict,
        search: Dict,
    ) -> Tuple[str, List[str]]:
        """LLM 生成 reasoning + hot_topics，失败时规则降级。"""
        # 构建摘要
        top_stocks_text = "、".join(
            f"{s.stock_name}({s.mention_count})" for s in hot_stocks[:8]
        ) or "无"

        breadth_text = ""
        if breadth:
            breadth_text = (
                f"涨跌比 {breadth.get('up_count', 0)}:{breadth.get('down_count', 0)}，"
                f"涨停 {breadth.get('limit_up', 0)} 跌停 {breadth.get('limit_down', 0)}"
            )

        search_text = ""
        articles = search.get("articles", [])
        if articles:
            search_text = "；".join(
                a.get("title", "") for a in articles[:5]
            )

        prompt = (
            "你是散户情绪分析师。根据以下数据，撰写一段简洁的散户情绪分析报告。\n\n"
            f"情绪评分：{score:.2f}（{overall}）\n"
            f"反身性阶段：{stage}\n"
            f"论坛情绪：{mood}\n\n"
            f"雪球热门股：{top_stocks_text}\n"
            f"市场宽度：{breadth_text or '无数据'}\n"
            f"舆情搜索：{search_text or '无数据'}\n\n"
            "请输出：\n"
            "1. 一段150字以内的情绪分析文本\n"
            "2. 2-3个热门话题关键词（用JSON格式返回）\n\n"
            "输出格式（不要 markdown 代码块）：\n"
            '{"reasoning": "分析文本...", "hot_topics": ["话题1", "话题2"]}'
        )

        try:
            result = await asyncio.to_thread(
                self._call_llm, prompt, temperature=0.3, max_tokens=600,
            )
            if result:
                import json
                import re
                cleaned = re.sub(r"```(?:json)?\s*", "", result).strip()
                try:
                    data = json.loads(cleaned)
                except json.JSONDecodeError:
                    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group())
                        except json.JSONDecodeError:
                            data = None
                    else:
                        data = None

                if data:
                    reasoning = data.get("reasoning", result)
                    hot_topics = data.get("hot_topics", [])
                    return reasoning, hot_topics
        except Exception as exc:
            logger.warning("[Node 5] reasoning LLM 失败: %s", exc)

        # 规则降级
        return self._fallback_reasoning(overall, score, stage, mood, hot_stocks, breadth), []

    @staticmethod
    def _call_llm(prompt: str, *, temperature: float, max_tokens: int, max_retries: int = 3) -> str:
        """同步 LLM 调用，含空响应重试。"""
        from src.agent.llm_adapter import LLMToolAdapter

        adapter = LLMToolAdapter()
        messages = [{"role": "user", "content": prompt}]

        for attempt in range(max_retries + 1):
            response = adapter.call_text(
                messages, temperature=temperature, max_tokens=max_tokens,
            )
            if response and response.content and response.content.strip():
                return response.content.strip()
            if attempt < max_retries:
                logger.debug("[Node 5] LLM 空响应，重试 %d/%d", attempt + 1, max_retries)
        return ""

    @staticmethod
    def _fallback_reasoning(
        overall: str, score: float, stage: str, mood: str,
        hot_stocks: List[HotStock], breadth: Dict,
    ) -> str:
        """规则降级 reasoning。"""
        parts = [f"散户情绪{mood}（评分 {score:.2f}），反身性阶段：{stage}。"]

        if hot_stocks:
            names = "、".join(s.stock_name for s in hot_stocks[:5])
            parts.append(f"热门股：{names}。")

        if breadth:
            parts.append(
                f"涨跌比 {breadth.get('up_count', 0)}:{breadth.get('down_count', 0)}。"
            )

        if stage == "euphoria":
            parts.append("散户极度狂热，注意反向卖出信号。")
        elif stage == "capitulation":
            parts.append("散户恐慌出清，关注反向买入机会。")

        return "".join(parts)

    # =================================================================
    # Mock 输出
    # =================================================================

    def _mock_output(self) -> SentimentContext:
        """Mock 输出（温和乐观）"""
        return SentimentContext(
            status="normal",
            overall_sentiment="bullish",
            sentiment_score=0.42,
            sentiment_stage="optimism",
            trading_signal="trend_following",
            signal_confidence=0.5,
            forum_mood="乐观",
            hot_stocks=[
                HotStock(
                    stock_code="002935",
                    stock_name="天奥电子",
                    mention_count=28,
                    sentiment="positive",
                    associated_sectors=["semiconductor", "ai_chips"],
                ),
                HotStock(
                    stock_code="300377",
                    stock_name="赢时胜",
                    mention_count=22,
                    sentiment="positive",
                    associated_sectors=["ai_applications"],
                ),
            ],
            hot_topics=["人形机器人量产", "AI芯片国产替代", "降息预期"],
            retail_attention_count=50000,
            market_temperature=0.65,
            news_drift="positive",
            reasoning="[Mock] 散户情绪温和乐观，热门股集中在AI和机器人板块。无极端情绪信号。",
            data_quality=DataQuality(
                available_fields=["xueqiu_data", "breadth_data", "search_data"],
                missing_fields=[],
            ),
        )
