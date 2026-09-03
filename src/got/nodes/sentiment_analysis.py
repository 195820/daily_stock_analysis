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
import pathlib
from typing import Any, Dict, List, Optional, Tuple

from data_provider.base import DataFetcherManager
from src.got.schemas import (
    SentimentContext,
    SentimentStageState,
    HotStock,
    DataQuality,
)
from src.got.persistence import (
    load_previous_stage_state,
    load_sentiment_score_history,
)

logger = logging.getLogger(__name__)

# =============================================================================
# 雪球热榜参数
# =============================================================================

XUEQIU_TOP_N = 50  # 过滤后取前 50 只热门股（覆盖全行业主要板块）
XUEQIU_TIMEOUT = 90  # 雪球 API 较慢，单次超时 90 秒

# 行业关键词（覆盖 A 股主要板块，宽松过滤以反映散户真实关注分布）
TECH_GROWTH_KEYWORDS = frozenset({
    # AI / 科技
    "AI", "人工智能", "芯片", "半导体", "算力", "光模块", "服务器",
    "大模型", "智算", "GPU", "存储", "液冷", "数据要素",
    # 机器人
    "机器人", "减速器", "伺服", "传感", "控制器", "具身", "人形",
    # 软件 / 信创
    "软件", "信创", "鸿蒙", "操作系统", "数据库", "中间件", "网络安全",
    "云计算", "SaaS", "数字",
    # 新能源
    "新能源", "储能", "光伏", "锂电", "氢能", "风电", "电动车", "电池",
    # 军工 / 航天
    "军工", "航天", "卫星", "雷达", "电子对抗", "国防", "船舶",
    # 通信
    "5G", "6G", "通信", "光纤", "基站",
    # 科技制造
    "PCB", "面板", "显示", "LED", "光学", "激光",
    # 消费
    "白酒", "食品", "饮料", "乳业", "啤酒", "家电",
    # 医药 / 医疗
    "医药", "医疗", "生物", "疫苗", "创新药", "中药", "CXO",
    # 金融
    "券商", "银行", "保险", "证券", "金融",
    # 地产 / 基建
    "地产", "房地产", "物业", "基建", "建材", "水泥", "钢铁",
    # 资源 / 有色
    "黄金", "有色", "稀土", "铜", "铝", "煤炭", "石油", "能源",
    # 汽车
    "汽车", "整车", "零部件",
    # 农业
    "农业", "种业", "养殖", "猪", "饲料",
    # 其他热门
    "旅游", "酒店", "传媒", "游戏", "教育",
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

        # Step 1: 并发采集数据（6 源）
        (em_data, breadth_data, nga_data, search_data,
         xueqiu_data, detail_data, data_quality) = await self._fetch_all_data()

        # Step 2: 计算情绪评分
        sentiment_score = self._calculate_sentiment_score(
            em_data, breadth_data, nga_data, search_data, xueqiu_data, detail_data,
        )
        sentiment_score = max(-1.0, min(1.0, sentiment_score))

        # Step 3: 反身性阶段分类（用东财关注指数替代雪球关注度）
        stage, signal, confidence = self._classify_reflexivity(
            sentiment_score, em_data,
        )

        # Step 4: 整体判断
        overall = self._score_to_sentiment(sentiment_score)
        forum_mood = self._score_to_mood(sentiment_score)

        # Step 5: 热门股提取（优先东财千股千评，降级到雪球）
        hot_stocks = self._extract_hot_stocks(em_data, xueqiu_data)

        # Step 6: 市场温度 + 散户关注度
        market_temp = self._calculate_market_temperature(breadth_data)
        retail_attention = self._calculate_retail_attention(em_data, xueqiu_data)

        # Step 7: 消息面偏离
        news_drift = self._classify_news_drift(search_data)

        # Step 8: 情绪分歧度（从东财数据计算）
        dispersion_info = self._compute_sentiment_dispersion(em_data)

        # Step 8b: 时序动量（与历史 EMA 对比）
        sentiment_momentum = self._compute_sentiment_momentum(sentiment_score)

        # Step 8c: v2.1 情绪阶段状态机 + AR(1) 持续性
        # Rollback 开关：DISABLE_SENTIMENT_STAGE_STATE=true 回退
        import os as _os
        if _os.environ.get("DISABLE_SENTIMENT_STAGE_STATE", "").lower() == "true":
            logger.info("[Node 5] DISABLE_SENTIMENT_STAGE_STATE=true，跳过阶段状态机")
            stage_state = None
            persistence_regime = None
        else:
            stage_state = self._apply_stage_state_machine(stage)
            persistence_regime = self._compute_ar1_persistence()

        # Step 9: LLM reasoning + hot_topics
        reasoning, hot_topics = await self._generate_reasoning(
            overall, sentiment_score, stage, forum_mood,
            hot_stocks, breadth_data, search_data, nga_data,
            dispersion_info=dispersion_info,
        )

        # 情绪评分 → 恐贪指数（0=极度恐惧, 1=极度贪婪）
        fear_greed = (sentiment_score + 1) / 2  # [-1,1] → [0,1]

        has_partial_failure = any(
            f in data_quality.fetch_failed_fields
            for f in ["eastmoney_comment", "breadth", "search", "nga"]
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

        # 设置新增字段
        result.fear_greed_index = fear_greed
        result.sentiment_dispersion = dispersion_info.get("dispersion", 0.0)
        result.contrarian_signal = dispersion_info.get("contrarian_signal", False)
        result.sentiment_momentum = sentiment_momentum
        # v2.1 阶段状态机 + 持续性
        result.stage_state = stage_state
        result.persistence_regime = persistence_regime
        # 阶段状态机的反向信号合并到 contrarian_signal（OR 逻辑）
        if stage_state and stage_state.contrarian_alert:
            result.contrarian_signal = True
        logger.info(
            "[Node 5] 完成 — status=%s, score=%.2f, stage=%s, signal=%s, "
            "dispersion=%.2f, momentum=%+.3f, contrarian=%s, "
            "stage_state(dwell=%d, dir=%s, alert=%s), persistence=%s",
            status, sentiment_score, stage, signal,
            dispersion_info.get("dispersion", 0.0),
            sentiment_momentum,
            result.contrarian_signal,
            stage_state.dwell_days if stage_state else 0,
            stage_state.momentum_direction if stage_state else "?",
            stage_state.contrarian_alert if stage_state else False,
            persistence_regime,
        )
        return result

    # =================================================================
    # Step 1: 并发数据采集
    # =================================================================

    async def _fetch_all_data(
        self,
    ) -> Tuple[Dict, Dict, Dict, Dict, Dict, Dict, DataQuality]:
        """并发采集 6 类数据：东财千股千评 + 市场宽度 + NGA + 搜索 + 雪球 + 评论详情。

        Phase 1: 5 源并发（东财/宽度/NGA/搜索/雪球）
        Phase 2: 东财评论详情采样（依赖 Phase 1 的东财数据）
        """
        # Phase 1: 并发采集 5 个独立数据源
        em_task = asyncio.create_task(self._fetch_eastmoney_comment())
        breadth_task = asyncio.create_task(self._fetch_breadth())
        nga_task = asyncio.create_task(self._fetch_nga_forum())
        search_task = asyncio.create_task(self._fetch_sentiment_search())
        xueqiu_task = asyncio.create_task(self._fetch_xueqiu())

        em_data, breadth_data, nga_data, search_data, xueqiu_data = await asyncio.gather(
            em_task, breadth_task, nga_task, search_task, xueqiu_task,
            return_exceptions=True,
        )

        # 异常降级为空 dict
        if isinstance(em_data, Exception):
            logger.error("[Node 5] 东财千股千评采集失败: %s", em_data)
            em_data = {}
        if isinstance(breadth_data, Exception):
            logger.error("[Node 5] 市场宽度采集失败: %s", breadth_data)
            breadth_data = {}
        if isinstance(nga_data, Exception):
            logger.error("[Node 5] NGA 采集失败: %s", nga_data)
            nga_data = {}
        if isinstance(search_data, Exception):
            logger.error("[Node 5] 搜索舆情采集失败: %s", search_data)
            search_data = {}
        if isinstance(xueqiu_data, Exception):
            logger.error("[Node 5] 雪球采集失败: %s", xueqiu_data)
            xueqiu_data = {}

        # Phase 2: 东财评论详情采样（依赖 em_data）
        detail_data = await self._fetch_comment_details(em_data)

        # DataQuality 追踪
        available, missing, failed = [], [], []
        sources = [
            ("eastmoney_comment", em_data),
            ("comment_detail", detail_data),
            ("breadth", breadth_data),
            ("nga", nga_data),
            ("search", search_data),
            ("xueqiu", xueqiu_data),
        ]
        for name, data in sources:
            if data:
                available.append(f"{name}_data")
            else:
                missing.append(f"{name}_data")

        data_quality = DataQuality(
            available_fields=available,
            missing_fields=missing,
            fetch_failed_fields=failed,
        )
        return em_data, breadth_data, nga_data, search_data, xueqiu_data, detail_data, data_quality

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

        try:
            follow, deal, tweet = await asyncio.wait_for(
                asyncio.gather(
                    _fetch_one("stock_hot_follow_xq", "follow"),
                    _fetch_one("stock_hot_deal_xq", "deal"),
                    _fetch_one("stock_hot_tweet_xq", "tweet"),
                ),
                timeout=XUEQIU_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("[Node 5] 雪球热榜整体超时 (%ds)，返回空", XUEQIU_TIMEOUT)
            follow, deal, tweet = [], [], []

        return {
            "follow": follow,
            "deal": deal,
            "tweet": tweet,
            "total_count": len(follow) + len(deal) + len(tweet),
        }

    async def _fetch_eastmoney_comment(self) -> Dict[str, Any]:
        """获取东财千股千评全市场数据（datacenter-web 端点，非 push2）。

        返回 5190 只股票的综合得分/机构参与度/关注指数/排名等。
        实测耗时 ~3s，数据当日更新。
        """
        import akshare as ak

        def _fetch():
            return ak.stock_comment_em()

        try:
            df = await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.warning("[Node 5] 东财千股千评获取失败: %s", exc)
            return {}

        if df is None or df.empty:
            return {}

        logger.info("[Node 5] 东财千股千评: %d 只股票", len(df))

        return {
            "df": df,
            "count": len(df),
            "trade_date": str(df["交易日"].iloc[0]) if "交易日" in df.columns else "",
        }

    # 评论详情采样参数
    COMMENT_DETAIL_SAMPLE_N = 15  # 采样股数（控制耗时：~15s）
    COMMENT_DETAIL_TIMEOUT = 60   # 详情采样总超时

    async def _fetch_comment_details(self, em_data: Dict) -> Dict[str, Any]:
        """采样热门股的东财评论详情（参与意愿/关注度/机构参与度历史）。

        从 em_data 中按关注指数取 top N，并发调用 detail API。
        端点：datacenter-web.eastmoney.com（非 push2）。
        """
        if not em_data or "df" not in em_data:
            return {}

        import akshare as ak

        df = em_data["df"]
        if "关注指数" not in df.columns:
            return {}

        # 按关注指数取 top N
        top_stocks = df.nlargest(self.COMMENT_DETAIL_SAMPLE_N, "关注指数")
        symbols = top_stocks["代码"].astype(str).tolist()
        names = top_stocks["名称"].astype(str).tolist()

        async def _fetch_one_detail(symbol: str, name: str) -> Dict:
            """获取单只股票的 4 个 detail 维度。"""
            result = {"symbol": symbol, "name": name}
            apis = {
                "desire": ak.stock_comment_detail_scrd_desire_em,
                "focus": ak.stock_comment_detail_scrd_focus_em,
                "institutional": ak.stock_comment_detail_zlkp_jgcyd_em,
                "score_history": ak.stock_comment_detail_zhpj_lspf_em,
            }
            for key, func in apis.items():
                try:
                    detail_df = await asyncio.to_thread(func, symbol=symbol)
                    if detail_df is not None and not detail_df.empty:
                        result[key] = detail_df
                except Exception as exc:
                    logger.debug("[Node 5] %s detail.%s 失败: %s", symbol, key, exc)
            return result

        try:
            details = await asyncio.wait_for(
                asyncio.gather(
                    *[_fetch_one_detail(s, n) for s, n in zip(symbols, names)],
                    return_exceptions=True,
                ),
                timeout=self.COMMENT_DETAIL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("[Node 5] 评论详情采样超时 (%ds)", self.COMMENT_DETAIL_TIMEOUT)
            return {}

        # 过滤异常结果
        valid = [d for d in details if isinstance(d, dict) and d.get("symbol")]
        logger.info("[Node 5] 评论详情采样: %d/%d 成功", len(valid), len(symbols))

        return {"details": valid, "sample_count": len(valid)}

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

    async def _fetch_nga_forum(self) -> Dict[str, Any]:
        """获取 NGA 论坛舆情数据（ ForumSentimentService ）。"""
        from src.config import Config
        from src.services.forum_sentiment import ForumSentimentService
        from src.config import get_config

        config = get_config()
        if not config.forum_sentiment_enabled:
            return {}

        service = ForumSentimentService(config)
        if not service.is_available:
            logger.debug("[Node 5] NGA 舆情不可用（缺少 Cookie 配置）")
            return {}

        def _fetch():
            return service.fetch_hot_stocks()

        try:
            result = await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.warning("[Node 5] NGA 舆情采集异常: %s", exc)
            return {}

        if not result.hot_stocks and result.thread_count == 0:
            status = result.source_status.get("nga_dashidai", "")
            if "error" in status or "unconfigured" in status:
                return {}

        return {
            "market_sentiment": result.market_sentiment,
            "hot_stocks": [
                {
                    "code": s.code,
                    "name": s.name,
                    "heat_score": s.heat_score,
                    "sentiment_score": s.sentiment_score,
                    "mention_count": s.mention_count,
                }
                for s in result.hot_stocks[:20]
            ],
            "thread_count": result.thread_count,
            "hot_topics": [t.get("topic", "") for t in result.hot_topics[:5]],
        }

    # =================================================================
    # Step 2: 情绪评分（6 维置信度加权）
    # =================================================================

    # 各维度基础权重 + 置信度（参考 fffinstill Fear & Greed Index）
    _DIMENSION_CONFIG = {
        "eastmoney":  {"weight": 0.25, "confidence": 0.90},
        "breadth":    {"weight": 0.15, "confidence": 0.85},
        "nga":        {"weight": 0.20, "confidence": 0.80},
        "news":       {"weight": 0.15, "confidence": 0.70},
        "detail":     {"weight": 0.10, "confidence": 0.60},
        "momentum":   {"weight": 0.15, "confidence": 0.80},
    }

    @classmethod
    def _calculate_sentiment_score(
        cls,
        em_data: Dict,
        breadth: Dict,
        nga: Dict,
        search: Dict,
        xueqiu: Dict,
        detail_data: Dict,
    ) -> float:
        """6 维置信度加权情绪评分（-1 ~ 1）。

        缺失维度时有效权重自动重分配（乘以置信度后归一化）。
        """
        scores: list[float] = []
        eff_weights: list[float] = []

        # D1: 东财千股千评 — 全市场综合得分均值归一化
        if em_data and "df" in em_data:
            df = em_data["df"]
            if "综合得分" in df.columns:
                avg_score = df["综合得分"].mean()
                em_score = (avg_score - 50) / 50  # 50→0, 100→1, 0→-1
                scores.append(max(-1.0, min(1.0, em_score)))
                cfg = cls._DIMENSION_CONFIG["eastmoney"]
                eff_weights.append(cfg["weight"] * cfg["confidence"])

        # D2: 市场宽度 — 涨跌比 + 涨停跌停比
        if breadth:
            breadth_score = breadth.get("breadth_score", 0.0)
            limit_up = breadth.get("limit_up", 0)
            limit_down = breadth.get("limit_down", 0)
            limit_total = limit_up + limit_down
            if limit_total > 0:
                limit_score = (limit_up - limit_down) / limit_total
            else:
                limit_score = 0.0
            combined = 0.6 * breadth_score + 0.4 * limit_score
            scores.append(max(-1.0, min(1.0, combined)))
            cfg = cls._DIMENSION_CONFIG["breadth"]
            eff_weights.append(cfg["weight"] * cfg["confidence"])

        # D3: NGA 论坛舆情 — market_sentiment 已在 [-1, 1]
        if nga and nga.get("market_sentiment") is not None:
            scores.append(nga["market_sentiment"])
            cfg = cls._DIMENSION_CONFIG["nga"]
            eff_weights.append(cfg["weight"] * cfg["confidence"])

        # D4: 搜索舆情 — 关键词匹配（LLM 分类在 Step 3 增强）
        if search and search.get("articles"):
            search_score = cls._keyword_sentiment_score(search["articles"])
            scores.append(search_score)
            cfg = cls._DIMENSION_CONFIG["news"]
            eff_weights.append(cfg["weight"] * cfg["confidence"])

        # D5: 散户行为代理 — 参与意愿变化均值
        if detail_data and detail_data.get("details"):
            desire_score = cls._detail_desire_score(detail_data["details"])
            if desire_score is not None:
                scores.append(desire_score)
                cfg = cls._DIMENSION_CONFIG["detail"]
                eff_weights.append(cfg["weight"] * cfg["confidence"])

        if not scores:
            return 0.0

        total_w = sum(eff_weights)
        if total_w == 0:
            return 0.0
        return sum(s * w for s, w in zip(scores, eff_weights)) / total_w

    @staticmethod
    def _keyword_sentiment_score(articles: List[Dict]) -> float:
        """关键词情感评分（改进版：扩展词库 + 否定检测）。"""
        positive_words = frozenset({
            "看涨", "看多", "反弹", "突破", "利好", "放量", "强势",
            "大涨", "新高", "牛市", "加仓", "爆发", "拉升", "金叉",
            "多头", "量价齐升", "超预期", "底部放量", "主力吸筹",
        })
        negative_words = frozenset({
            "看空", "看跌", "暴跌", "利空", "减持", "套牢", "割肉",
            "大跌", "新低", "熊市", "崩盘", "出逃", "恐慌", "跳水",
            "砸盘", "死叉", "量价背离", "主力出货", "爆雷",
        })
        # 否定词：翻转其后紧跟的情感词方向
        negation_words = frozenset({"不", "没", "难以", "不再", "未"})

        pos_count = 0
        neg_count = 0
        for article in articles:
            text = article.get("title", "") + article.get("snippet", "")
            for pw in positive_words:
                if pw in text:
                    idx = text.index(pw)
                    prefix = text[max(0, idx - 3):idx]
                    if any(nw in prefix for nw in negation_words):
                        neg_count += 1
                    else:
                        pos_count += 1
            for nw in negative_words:
                if nw in text:
                    idx = text.index(nw)
                    prefix = text[max(0, idx - 3):idx]
                    if any(neg in prefix for neg in negation_words):
                        pos_count += 1
                    else:
                        neg_count += 1

        total = pos_count + neg_count
        if total == 0:
            return 0.0
        return (pos_count - neg_count) / total

    @staticmethod
    def _detail_desire_score(details: List[Dict]) -> Optional[float]:
        """从东财 detail 数据中提取参与意愿变化均值，归一化到 [-1, 1]。"""
        changes = []
        for d in details:
            desire_df = d.get("desire")
            if desire_df is not None and not desire_df.empty:
                # 取最新一行的"参与意愿变化"
                if "参与意愿变化" in desire_df.columns:
                    latest_change = desire_df["参与意愿变化"].iloc[-1]
                    try:
                        changes.append(float(latest_change))
                    except (ValueError, TypeError):
                        pass
        if not changes:
            return None
        avg_change = sum(changes) / len(changes)
        # 参与意愿变化范围约 -50~+50，归一化到 [-1, 1]
        return max(-1.0, min(1.0, avg_change / 30))

    # =================================================================
    # Step 3: 反身性 5 阶段分类（设计文档 P2-2）
    # =================================================================

    @staticmethod
    def _classify_reflexivity(
        score: float, em_data: Dict,
    ) -> Tuple[str, str, float]:
        """反身性阶段分类。用东财关注指数衡量散户狂热程度。"""
        # 散户关注度比率（东财关注指数均值 / 100 归一化）
        attention_ratio = 0.0
        if em_data and "df" in em_data:
            df = em_data["df"]
            if "关注指数" in df.columns:
                avg_attention = df["关注指数"].mean()
                attention_ratio = min(avg_attention / 80, 1.0)  # 80+ 为极高关注

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
    def _extract_hot_stocks(em_data: Dict, xueqiu: Dict) -> List[HotStock]:
        """提取热门股（优先东财千股千评，降级到雪球）。

        东财数据带综合得分 → 真实情绪判断（非硬编码）。
        """
        # 优先：东财千股千评（按关注指数排序，综合得分判断情绪）
        if em_data and "df" in em_data:
            df = em_data["df"]
            if "关注指数" in df.columns and "综合得分" in df.columns:
                top = df.nlargest(XUEQIU_TOP_N, "关注指数")
                hot_stocks = []
                for _, row in top.iterrows():
                    score = float(row.get("综合得分", 50) or 50)
                    if score >= 70:
                        sentiment = "positive"
                    elif score <= 30:
                        sentiment = "negative"
                    else:
                        sentiment = "neutral"
                    hot_stocks.append(HotStock(
                        stock_code=str(row.get("代码", "")),
                        stock_name=str(row.get("名称", "")),
                        mention_count=int(float(row.get("关注指数", 0) or 0) * 1000),
                        sentiment=sentiment,
                        associated_sectors=[],
                    ))
                if hot_stocks:
                    return hot_stocks

        # 降级：雪球 3 榜合并
        stock_map: Dict[str, Dict] = {}
        for dim in ["tweet", "deal", "follow"]:
            for stock in xueqiu.get(dim, []):
                code = stock["stock_code"]
                if code not in stock_map:
                    stock_map[code] = {
                        "stock_code": code,
                        "stock_name": stock["stock_name"],
                        "total_attention": 0,
                    }
                stock_map[code]["total_attention"] += stock["attention"]

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
                sentiment="neutral",  # 雪球无情绪数据，不再硬编码 positive
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
    def _calculate_retail_attention(em_data: Dict, xueqiu: Dict) -> int:
        """散户关注度（东财关注指数总量，降级到雪球讨论量）。"""
        if em_data and "df" in em_data:
            df = em_data["df"]
            if "关注指数" in df.columns:
                return int(df["关注指数"].sum())
        # 降级：雪球
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

    @staticmethod
    def _compute_sentiment_dispersion(em_data: Dict) -> Dict[str, Any]:
        """情绪分歧度（参考国金证券研究：一致看多 → 见顶信号）。

        分歧度 = 综合得分的变异系数（std/mean）。
        分歧度低 = 一致看多/看空 → 反向信号。
        """
        if not em_data or "df" not in em_data:
            return {"dispersion": 0.0, "up_down_gap": 0.0, "contrarian_signal": False}

        df = em_data["df"]
        if "综合得分" not in df.columns or "涨跌幅" not in df.columns:
            return {"dispersion": 0.0, "up_down_gap": 0.0, "contrarian_signal": False}

        scores = df["综合得分"].dropna()
        if len(scores) < 10 or scores.mean() == 0:
            return {"dispersion": 0.0, "up_down_gap": 0.0, "contrarian_signal": False}

        dispersion = scores.std() / abs(scores.mean())

        # 涨跌股得分差异
        up_mask = df["涨跌幅"] > 0
        down_mask = df["涨跌幅"] < 0
        up_avg = df.loc[up_mask, "综合得分"].mean() if up_mask.any() else 50
        down_avg = df.loc[down_mask, "综合得分"].mean() if down_mask.any() else 50
        up_down_gap = up_avg - down_avg

        # 一致看多（高分+低分歧）或一致看空（低分+低分歧）→ 反向信号
        # 注：dispersion/mean_score 均为 numpy.float64，比较与 and 的结果会是
        # numpy.bool_ 而非 Python bool。pydantic 严格序列化拒绝 numpy 标量
        # （0902 实盘 Phase 2 因此整体失败：Unable to serialize unknown type:
        # <class 'numpy.bool'>），故在此边界处显式转 bool。
        mean_score = scores.mean()
        contrarian = bool(
            dispersion < 0.35
            and (mean_score > 70 or mean_score < 30)
        )

        return {
            "dispersion": round(float(dispersion), 3),
            "up_down_gap": round(float(up_down_gap), 2),
            "contrarian_signal": contrarian,
            "mean_score": round(float(mean_score), 2),
        }

    @staticmethod
    def _compute_sentiment_momentum(current_score: float) -> float:
        """时序动量：加载近期 sentiment.json，计算当前 score 与 EMA(5) 的偏差。

        返回偏差值（正=情绪升温, 负=情绪降温）。
        无历史数据时返回 0.0。
        """
        import json as _json

        daily_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "data" / "got_daily"
        if not daily_dir.exists():
            return 0.0

        # 按日期倒序加载最近 10 天的 sentiment_score
        scores = []
        for date_dir in sorted(daily_dir.iterdir(), reverse=True)[:10]:
            if not date_dir.is_dir():
                continue
            sent_file = date_dir / "sentiment.json"
            if not sent_file.exists():
                continue
            try:
                with open(sent_file, encoding="utf-8") as f:
                    data = _json.load(f)
                score = data.get("sentiment_score")
                if score is not None:
                    scores.append(float(score))
            except Exception:
                pass

        if len(scores) < 2:
            return 0.0

        # EMA(5) 计算
        alpha = 2.0 / (min(len(scores), 5) + 1)
        ema = scores[0]  # 最近一天
        for s in scores[1:]:
            ema = alpha * s + (1 - alpha) * ema

        return round(current_score - ema, 3)

    # =================================================================
    # v2.1 情绪阶段状态机 + AR(1) 持续性 + 反向信号
    # =================================================================

    # 阶段序数（用于判断升温/降温方向）
    _STAGE_ORDER = {
        "capitulation": 0,
        "pessimism": 1,
        "neutral": 2,
        "optimism": 3,
        "euphoria": 4,
    }

    @staticmethod
    def _apply_stage_state_machine(
        raw_stage: str,
    ) -> SentimentStageState:
        """情绪阶段状态机（跨日记忆）。

        逻辑（与 Node 6 Regime 状态机对齐）：
        - raw == previous → dwell+1（阶段持续）
        - raw != previous → 切换，dwell=1，记录 previous
        - 冷启动（无归档）→ current=raw, dwell=1

        额外输出：
        - momentum_direction：基于近 3 日阶段变化方向
        - stage_transitions：近 10 日阶段序列（升序）
        - contrarian_alert：反向信号（见 _compute_stage_contrarian_alert）
        """
        prev_state = load_previous_stage_state()

        if prev_state is None:
            # 冷启动
            return SentimentStageState(
                current_stage=raw_stage,
                dwell_days=1,
                previous_stage=None,
                momentum_direction="stable",
                stage_transitions=[raw_stage],
                contrarian_alert=False,
            )

        prev_stage = prev_state["current_stage"]
        prev_dwell = prev_state["dwell_days"]
        prev_transitions = prev_state.get("stage_transitions", [])

        if raw_stage == prev_stage:
            # 持续：dwell+1，清除 previous
            new_dwell = prev_dwell + 1
            new_previous = prev_stage
        else:
            # 切换：dwell=1
            new_dwell = 1
            new_previous = prev_stage

        # 构建近 10 日阶段序列
        new_transitions = list(prev_transitions[-9:]) + [raw_stage]

        # 动量方向：看最近 3 日阶段变化
        recent = new_transitions[-3:] if len(new_transitions) >= 3 else new_transitions
        momentum = SentimentAnalysisNode._compute_momentum_direction(recent)

        # 反向信号
        contrarian = SentimentAnalysisNode._compute_stage_contrarian_alert(
            current_stage=raw_stage,
            dwell_days=new_dwell,
            momentum_direction=momentum,
            stage_transitions=new_transitions,
        )

        return SentimentStageState(
            current_stage=raw_stage,
            dwell_days=new_dwell,
            previous_stage=new_previous,
            momentum_direction=momentum,
            stage_transitions=new_transitions,
            contrarian_alert=contrarian,
        )

    @staticmethod
    def _compute_momentum_direction(recent_stages: list[str]) -> str:
        """从近 N 日阶段序列判断动量方向。

        warming：阶段序数单调上升（至少 2 次上升）
        cooling：阶段序数单调下降（至少 2 次下降）
        stable：其他

        Returns:
            "warming" / "cooling" / "stable"
        """
        if len(recent_stages) < 2:
            return "stable"

        order = SentimentAnalysisNode._STAGE_ORDER
        nums = [order.get(s, 2) for s in recent_stages]

        # 计算连续上升/下降次数
        rising = sum(1 for i in range(1, len(nums)) if nums[i] > nums[i - 1])
        falling = sum(1 for i in range(1, len(nums)) if nums[i] < nums[i - 1])

        if rising >= 2 and rising > falling:
            return "warming"
        if falling >= 2 and falling > rising:
            return "cooling"
        return "stable"

    @staticmethod
    def _compute_stage_contrarian_alert(
        current_stage: str,
        dwell_days: int,
        momentum_direction: str,
        stage_transitions: list[str],
    ) -> bool:
        """反向信号触发条件（学术依据：Han 2022 + Da FEARS）。

        规则：
        1. 连续 3 天升温（momentum_direction == "warming" 且最近 3 日阶段上升）
           → contrarian_sell（散户狂热 = 顶部预警）
        2. 连续 3 天降温（momentum_direction == "cooling" 且最近 3 日阶段下降）
           → contrarian_buy（恐慌耗尽 = 底部预警）
        3. euphoria 持续 > 5 天 → contrarian_sell（过度贪婪）
        4. capitulation 持续 > 3 天 → contrarian_buy（恐慌耗尽）
        5. 高频切换（10 日 ≥ 5 次变化）→ 震荡标记，不触发反向
        """
        # 高频切换 → 不触发反向（市场犹豫不决）
        if len(stage_transitions) >= 5:
            changes = sum(
                1 for i in range(1, len(stage_transitions))
                if stage_transitions[i] != stage_transitions[i - 1]
            )
            if changes >= 5:
                return False

        # 规则 3: euphoria 持续 > 5 天
        if current_stage == "euphoria" and dwell_days > 5:
            return True

        # 规则 4: capitulation 持续 > 3 天
        if current_stage == "capitulation" and dwell_days > 3:
            return True

        # 规则 1 & 2: 连续 3 天升温/降温
        if momentum_direction == "warming" and len(stage_transitions) >= 3:
            order = SentimentAnalysisNode._STAGE_ORDER
            last3 = [order.get(s, 2) for s in stage_transitions[-3:]]
            if last3[0] < last3[1] < last3[2]:
                return True

        if momentum_direction == "cooling" and len(stage_transitions) >= 3:
            order = SentimentAnalysisNode._STAGE_ORDER
            last3 = [order.get(s, 2) for s in stage_transitions[-3:]]
            if last3[0] > last3[1] > last3[2]:
                return True

        return False

    @staticmethod
    def _compute_ar1_persistence() -> Optional[str]:
        """计算近 20 日 sentiment_score 的 AR(1) 系数，判断情绪持续性。

        AR(1) = Corr(score_t, score_{t-1})

        学术依据（Han et al. 2022）：
        - AR(1) > 0.7 → high_persistence（trending regime，异象显著）
        - AR(1) < 0.3 → low_persistence（mean-reverting，反转策略有效）
        - 0.3 ~ 0.7 → medium（中间态，不做特殊处理）

        Returns:
            "high" / "low" / "medium" / None（数据不足）
        """
        history = load_sentiment_score_history(days=20)
        if len(history) < 20:
            logger.info(
                "[Node 5] AR(1) 数据不足（%d/20 日），标 unknown", len(history)
            )
            return None

        scores = [s for _, s in history]

        # 计算 AR(1) 系数：简单用 lag-1 自相关
        n = len(scores)
        mean_val = sum(scores) / n
        numerator = sum(
            (scores[t] - mean_val) * (scores[t - 1] - mean_val)
            for t in range(1, n)
        )
        denominator = sum((s - mean_val) ** 2 for s in scores)

        if denominator == 0:
            # 常数序列 → 完全持续
            return "high"

        ar1 = numerator / denominator
        logger.info("[Node 5] AR(1) = %.3f（%d 日历史）", ar1, n)

        if ar1 > 0.7:
            return "high"
        if ar1 < 0.3:
            return "low"
        return "medium"

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
        nga: Dict,
        dispersion_info: Optional[Dict] = None,
    ) -> Tuple[str, List[str]]:
        """LLM 生成 reasoning + hot_topics，失败时规则降级。"""
        # 构建摘要
        top_stocks_text = "、".join(
            f"{s.stock_name}({s.sentiment[0].upper()},{s.mention_count})"
            for s in hot_stocks[:8]
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

        nga_text = ""
        if nga:
            nga_stocks = nga.get("hot_stocks", [])
            if nga_stocks:
                nga_text = "、".join(
                    f"{s['name']}({s['mention_count']}提)" for s in nga_stocks[:8]
                )
            nga_sentiment = nga.get("market_sentiment", 0)
            nga_topic_list = nga.get("hot_topics", [])
            if nga_topic_list:
                nga_text += f"；热门话题：{'、'.join(nga_topic_list[:3])}"
            nga_text += f"；论坛情绪：{nga_sentiment:+.2f}"

        dispersion_text = ""
        if dispersion_info:
            disp = dispersion_info.get("dispersion", 0)
            gap = dispersion_info.get("up_down_gap", 0)
            contrarian = dispersion_info.get("contrarian_signal", False)
            dispersion_text = (
                f"情绪分歧度：{disp:.2f}（低=一致/高=分歧），"
                f"涨跌股情绪差：{gap:+.1f}"
            )
            if contrarian:
                dispersion_text += "⚠️ 一致看多/看空，反向信号触发"

        prompt = (
            "你是散户情绪分析师。根据以下数据，撰写一段简洁的散户情绪分析报告。\n\n"
            f"情绪评分：{score:.2f}（{overall}）\n"
            f"反身性阶段：{stage}\n"
            f"论坛情绪：{mood}\n"
            f"{dispersion_text}\n\n"
            f"热门股（P=正面/N=负面/N=中性）：{top_stocks_text}\n"
            f"市场宽度：{breadth_text or '无数据'}\n"
            f"舆情搜索：{search_text or '无数据'}\n"
            f"NGA论坛：{nga_text or '无数据'}\n\n"
            "请输出：\n"
            "1. 一段150字以内的情绪分析文本（注意分歧度和反向信号）\n"
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
