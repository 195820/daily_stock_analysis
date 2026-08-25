"""
Node 4: 消息面分析节点

职责：采集和分析影响市场的政策、行业、国际新闻。

设计理念：
- LLM 是核心分析引擎：新闻是非结构化数据
- 3 并发 LLM 调用：事件提取 / 概念发现 / 风险总结（P1-4）
- 多源确认：同一事件 ≥3 家独立媒体（P1-5）
- 阈值过滤 + 显著度加权：low-impact 不参与评分（v0.3）

数据来源：4 源推送型采集（新浪文章流/CLS 电报/华尔街见闻/东财快讯）
"""

import asyncio
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from src.got.schemas import (
    NewsContext,
    NewsEvent,
    DataQuality,
)
from src.got.news_feeds import fetch_all_news
from src.storage import DatabaseManager, Node4EventRecord, Node4RawNewsRecord

logger = logging.getLogger(__name__)

# =============================================================================
# 搜索查询词（3 类 × 2 条 = 6 并发）
# =============================================================================

# 多源确认：title 相似度阈值
TITLE_SIMILARITY_THRESHOLD = 0.45

# 评分权重
IMPACT_WEIGHTS = {"high": 2.0, "medium": 1.0, "low": 0.0}
SOURCE_MULTIPLIER_THRESHOLD = 3
SOURCE_MULTIPLIER_BONUS = 1.5

# LLM 参数
EVENT_EXTRACTION_TEMPERATURE = 0.2
EVENT_EXTRACTION_MAX_TOKENS = 4000
CONCEPT_DISCOVERY_TEMPERATURE = 0.5
CONCEPT_DISCOVERY_MAX_TOKENS = 800
RISK_SUMMARY_TEMPERATURE = 0.3
RISK_SUMMARY_MAX_TOKENS = 300
REASONING_TEMPERATURE = 0.3
REASONING_MAX_TOKENS = 400


# =============================================================================
# Prompt 模板
# =============================================================================

EVENT_EXTRACTION_PROMPT = """你是严谨的新闻编辑。从以下新闻中提取影响A股的重要事件。

当前科技成长方向的板块精选概念列表：{concept_list}

{historical_context}

新闻数据：
{news_data}

要求：
1. 每个事件必须包含以下字段：
   - title: 事件标题（简明扼要）
   - summary: 2-3句摘要
   - event_type: "policy"（政策）/ "industry"（行业）/ "global"（国际）
   - impact_level: "high"（重大影响）/ "medium"（中等影响）/ "low"（轻微影响）
   - sentiment: "positive"（利好）/ "negative"（利空）/ "neutral"（中性）
   - affected_sectors: 受影响板块列表（尽量映射到概念列表中的概念）
   - affected_stocks: 事件中明确提到的具体个股名称或代码列表（如"中际旭创"、"300750"）。没有提到具体个股则留空[]
   - source_type: 事件的主要信息来源类型——"professional"（专业媒体/通讯社）或 "retail_forum"（散户论坛/社区讨论）。如果事件主要由[NGA论坛]等论坛标签的新闻首先提及或讨论，标记为"retail_forum"
2. 不要遗漏重要事件，也不要重复
3. 同一事件被多家媒体报道时，合并为一个事件
4. 如果当前新闻中的事件与历史事件相关（同一主题或因果关系），请在 summary 中标注"延续自：[历史事件标题]"
5. 严格按 importance 排序，high 在前

输出 JSON 格式（不要 markdown 代码块）：
{{"events": [{{"title": "...", "summary": "...", "event_type": "...", "impact_level": "...", "sentiment": "...", "affected_sectors": ["..."], "affected_stocks": ["..."], "source_type": "professional"}}]}}"""

CONCEPT_DISCOVERY_PROMPT = """你是敏锐的市场分析师，善于发现新兴投资概念。

当前已有的概念列表：{current_concepts}

{historical_context}

最新新闻：
{news_data}

要求：
1. 从新闻中发现新闻中反复出现的、尚未在概念列表中的新投资概念/主题
2. 每个概念需提供：concept_name（概念名）、evidence（证据，1-2句）、mention_frequency（新闻中被提及的次数估计）
3. 只输出真正有价值的新概念，不要凑数
4. 如果没有新概念，返回空列表
5. 如果某个概念在历史事件中已经连续多天出现（fermenting 或 peak 状态），请特别关注并标注"持续发酵中"

输出 JSON 格式（不要 markdown 代码块）：
{{"concepts": [{{"concept_name": "...", "evidence": "...", "mention_frequency": 3}}]}}"""

RISK_SUMMARY_PROMPT = """你是专业的市场研究员。请阅读以下新闻摘要，列出 3-5 个投资者需要关注的不确定性因素。

新闻摘要：
{news_data}

要求：
1. 每个因素用一句话概括
2. 重点关注：政策变化、外部环境、行业竞争格局等方面
3. 语言客观中立

输出 JSON 格式（不要 markdown 代码块）：
{{"risks": ["因素1", "因素2", "因素3"]}}"""

REASONING_PROMPT = """你是资深市场策略师。根据以下消息面分析结果，撰写一段简洁的分析报告。

消息面分析结果：
- 整体影响：{overall_impact}（评分：{impact_score:.2f}）
- 重要事件（{event_count}条）：
{events_text}
- 新概念建议：{concepts_text}
- 关键风险：{risks_text}

请输出：
1. 一段200字以内的分析文本，解释当前消息面的整体格局
2. 重点说明哪些事件对市场影响最大
3. 提示需要关注的风险和机会

输出格式：纯文本（不要JSON）"""


class NewsAnalysisNode:
    """消息面分析节点"""

    def __init__(self, use_mock: bool = True):
        self.use_mock = use_mock

    async def run(self) -> NewsContext:
        """执行消息面分析"""
        if self.use_mock:
            logger.info("[Node 4] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 4] 开始真实数据消息面分析")

        # Step 1: 并发采集新闻
        raw_news, data_quality = await self._fetch_news()

        # Step 1.5: 原始新闻持久化（P1-1: 跨日发酵检测基础）
        self._persist_raw_news(raw_news)

        if not raw_news:
            logger.warning("[Node 4] 未获取到任何新闻数据")
            return NewsContext(
                status="failed",
                overall_impact="neutral",
                impact_score=0.0,
                reasoning="新闻采集全部失败，无法分析",
                data_quality=data_quality,
            )

        has_partial_failure = any(
            f in data_quality.fetch_failed_fields
            for f in ["cls_telegraph", "akshare_news", "search_news"]
        )

        logger.info("[Node 4] 采集到 %d 条原始新闻", len(raw_news))

        # Step 2: 3 并发 LLM 分析
        events, concepts, risks = await self._analyze_with_llm(raw_news)

        # Step 3: 多源确认
        events = self._apply_source_count(events, raw_news)

        # 过滤 low-impact 事件（不参与 major_events 输出）
        events = [e for e in events if e.impact_level != "low"]

        # Step 3.5: 事件持久化（跨日记忆）
        self._save_events_to_db(events, raw_news)

        # Step 4: 评分计算
        impact_score = self._calculate_impact_score(events)
        overall_impact = self._score_to_impact(impact_score)

        # Step 5: reasoning 生成
        reasoning = await self._generate_reasoning(
            overall_impact, impact_score, events, concepts, risks,
        )

        status = "degraded" if has_partial_failure else "normal"

        result = NewsContext(
            status=status,
            overall_impact=overall_impact,
            impact_score=impact_score,
            major_events=events,
            concept_suggestions=concepts,
            key_risks=risks,
            reasoning=reasoning,
            data_quality=data_quality,
        )

        # ── 催化剂日历（P1） ──
        try:
            from src.got.catalyst_calendar import CatalystCalendarBuilder
            builder = CatalystCalendarBuilder()
            calendar = await builder.build_calendar()
            result.catalyst_calendar = calendar
            logger.info(
                "[Node 4] 催化剂日历: %d 事件, mode=%s",
                len(calendar.events), calendar.refresh_mode,
            )
        except Exception as exc:
            logger.warning("[Node 4] 催化剂日历构建失败（已降级）: %s", exc)

        logger.info(
            "[Node 4] 完成 — status=%s, impact=%s(%.2f), events=%d",
            status, overall_impact, impact_score, len(events),
        )
        return result

    # =================================================================
    # Step 1: 新闻采集
    # =================================================================

    async def _fetch_news(self) -> Tuple[List[Dict[str, Any]], DataQuality]:
        """4 源并发采集新闻（新浪/CLS/华尔街见闻/东财），返回合并结果 + DataQuality。"""
        merged, stats = await asyncio.to_thread(fetch_all_news)

        # 构建 DataQuality
        available = [name for name, count in stats.items() if count > 0]
        missing = [name for name, count in stats.items() if count == 0]

        source_map = {
            "sina": "sina_feed",
            "cls": "cls_telegraph",
            "wallstreetcn": "wallstreetcn",
            "eastmoney": "eastmoney_kuaixun",
            "newsnow": "newsnow_hot",
            "nga_forum": "nga_forum",
        }
        available = [source_map.get(s, s) for s in available]
        missing = [source_map.get(s, s) for s in missing]

        data_quality = DataQuality(
            available_fields=available,
            missing_fields=missing,
        )

        total = sum(stats.values())
        logger.info(
            "[Node 4] 5 源采集完成: sina=%d cls=%d wallstreetcn=%d eastmoney=%d newsnow=%d → 合并 %d 条",
            stats.get("sina", 0), stats.get("cls", 0),
            stats.get("wallstreetcn", 0), stats.get("eastmoney", 0),
            stats.get("newsnow", 0), len(merged),
        )
        return merged, data_quality

    # =================================================================
    # Step 2: 3 并发 LLM 分析
    # =================================================================

    async def _analyze_with_llm(
        self, raw_news: List[Dict],
    ) -> Tuple[List[NewsEvent], List[Dict], List[str]]:
        """3 次 LLM 调用：事件提取 → 概念发现 → 风险总结（串行，避免 API 并发空响应）。"""
        news_text = self._format_news_for_prompt(raw_news)
        concept_list_str = "科技成长（AI、半导体、机器人、新能源）"

        # 加载历史事件用于 LLM prompt 注入（跨日记忆）
        historical_events = self._load_historical_events_for_prompt(days=7)
        historical_context = self._format_historical_context_for_prompt(historical_events)

        # 事件提取（注入历史上下文）
        events_result = await asyncio.to_thread(
            self._call_llm,
            EVENT_EXTRACTION_PROMPT.format(
                concept_list=concept_list_str,
                news_data=news_text,
                historical_context=historical_context,
            ),
            temperature=EVENT_EXTRACTION_TEMPERATURE,
            max_tokens=EVENT_EXTRACTION_MAX_TOKENS,
        )
        events = self._parse_events(events_result)

        # 概念发现（注入历史上下文）
        concepts_result = await asyncio.to_thread(
            self._call_llm,
            CONCEPT_DISCOVERY_PROMPT.format(
                current_concepts=concept_list_str,
                news_data=news_text,
                historical_context=historical_context,
            ),
            temperature=CONCEPT_DISCOVERY_TEMPERATURE,
            max_tokens=CONCEPT_DISCOVERY_MAX_TOKENS,
        )
        concepts = self._parse_concepts(concepts_result)

        # 风险总结（可从事件中规则兜底）
        risks_result = await asyncio.to_thread(
            self._call_llm,
            RISK_SUMMARY_PROMPT.format(news_data=news_text),
            temperature=RISK_SUMMARY_TEMPERATURE,
            max_tokens=RISK_SUMMARY_MAX_TOKENS,
        )
        risks = self._parse_risks(risks_result, events)

        for name, result in zip(
            ["事件提取", "概念发现", "风险总结"],
            [events_result, concepts_result, risks_result],
        ):
            if not result:
                logger.warning("[Node 4] LLM %s 返回空", name)

        return events, concepts, risks

    # NGA 保底：确保至少 5 条论坛帖子进入 LLM 输入（散户视角不应被专业媒体完全淹没）
    _NGA_MIN_FOR_LLM = 5

    @staticmethod
    def _format_news_for_prompt(news_items: List[Dict], max_items: int = 50) -> str:
        """将新闻列表格式化为 LLM prompt 文本（含来源类型标注 + NGA 保底）。"""
        top_items = list(news_items[:max_items])

        # NGA 保底：如果 top N 里没有论坛帖子，从后续条目中补入
        nga_in_top = sum(1 for item in top_items if item.get("source_type") == "nga_forum")
        if nga_in_top < NewsAnalysisNode._NGA_MIN_FOR_LLM:
            nga_remaining = [
                item for item in news_items[max_items:]
                if item.get("source_type") == "nga_forum"
            ]
            needed = NewsAnalysisNode._NGA_MIN_FOR_LLM - nga_in_top
            for nga_item in nga_remaining[:needed]:
                # 替换 top_items 末尾最低权重的非 NGA 条目
                for j in range(len(top_items) - 1, -1, -1):
                    if top_items[j].get("source_type") != "nga_forum":
                        top_items[j] = nga_item
                        break

        lines = []
        for i, item in enumerate(top_items, 1):
            source = item.get("source", "未知")
            source_type = item.get("source_type", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")[:150]
            level = item.get("level", "")

            # 来源标注：[财联社-A] / [东财] / [央视] / [搜索]
            if source_type == "cls" and level:
                tag = f"财联社-{level}"
            elif source_type == "cls":
                tag = "财联社"
            elif source_type == "eastmoney":
                tag = "东财"
            elif source_type == "cctv":
                tag = "央视"
            elif source_type == "caixin":
                tag = "财新"
            else:
                tag = source or "搜索"

            lines.append(f"{i}. [{tag}] {title}\n   {snippet}")
        return "\n".join(lines)

    @staticmethod
    def _call_llm(prompt: str, *, temperature: float, max_tokens: int, max_retries: int = 3) -> str:
        """同步 LLM 调用（在 asyncio.to_thread 中执行），含空响应重试。"""
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
                logger.debug("[Node 4] LLM 空响应，重试 %d/%d", attempt + 1, max_retries)
        return ""

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        """从 LLM 输出中提取 JSON（兼容 markdown 代码块）。"""
        if not text:
            return None
        # 去除 markdown 代码块标记
        cleaned = re.sub(r"```(?:json)?\s*", "", text)
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试提取第一个 { ... } 块
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            # 处理截断 JSON：补齐未闭合的括号
            repaired = NewsAnalysisNode._repair_truncated_json(cleaned)
            if repaired:
                return repaired
        return None

    @staticmethod
    def _repair_truncated_json(text: str) -> Optional[Dict]:
        """尝试修复被 max_tokens 截断的 JSON（补齐未闭合括号）。"""
        if not text or "{" not in text:
            return None

        # 从第一个 { 开始
        start = text.index("{")
        truncated = text[start:]

        # 移除尾部不完整的键值对（以逗号或不完整字符串结尾）
        truncated = re.sub(r',\s*"[^"]*"?$', "", truncated)
        truncated = re.sub(r',\s*\}$', "}", truncated)

        # 统计未闭合的括号，从后往前补
        stack = []
        in_string = False
        escape_next = False
        for ch in truncated:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in ("{", "["):
                stack.append(ch)
            elif ch == "}" and stack and stack[-1] == "{":
                stack.pop()
            elif ch == "]" and stack and stack[-1] == "[":
                stack.pop()

        # 如果在字符串中截断，先关闭字符串
        if in_string:
            truncated += '"'

        # 补齐括号
        for opener in reversed(stack):
            truncated += "}" if opener == "{" else "]"

        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _parse_events(result) -> List[NewsEvent]:
        """解析事件提取 LLM 输出。"""
        if isinstance(result, Exception) or not result:
            return []

        data = NewsAnalysisNode._extract_json(result)
        if not data or "events" not in data:
            logger.warning("[Node 4] 事件提取 JSON 解析失败，原始输出前200字: %s", result[:200])
            return []

        events = []
        for item in data["events"]:
            try:
                events.append(NewsEvent(
                    event_type=item.get("event_type", "industry"),
                    title=item.get("title", ""),
                    summary=item.get("summary", ""),
                    impact_level=item.get("impact_level", "medium"),
                    sentiment=item.get("sentiment", "neutral"),
                    affected_sectors=item.get("affected_sectors", []),
                    affected_stocks=item.get("affected_stocks", []),
                    source_type=item.get("source_type", "professional"),
                    source_count=1,
                ))
            except Exception as exc:
                logger.debug("[Node 4] 事件解析失败: %s — item=%s", exc, item)
        return events

    @staticmethod
    def _parse_concepts(result) -> List[Dict]:
        """解析概念发现 LLM 输出。"""
        if isinstance(result, Exception) or not result:
            return []

        data = NewsAnalysisNode._extract_json(result)
        if not data or "concepts" not in data:
            logger.warning("[Node 4] 概念发现 JSON 解析失败，原始输出前200字: %s", result[:200])
            return []

        return [
            {
                "concept_name": c.get("concept_name", ""),
                "evidence": c.get("evidence", ""),
                "mention_frequency": c.get("mention_frequency", 1),
            }
            for c in data["concepts"]
            if c.get("concept_name")
        ]

    @staticmethod
    def _parse_risks(result, events: Optional[List[NewsEvent]] = None) -> List[str]:
        """解析风险总结 LLM 输出，失败时从负面事件中提取。"""
        # 优先用 LLM 结果
        if not isinstance(result, Exception) and result:
            data = NewsAnalysisNode._extract_json(result)
            if data and "risks" in data:
                parsed = [r for r in data["risks"] if r]
                if parsed:
                    return parsed

        # 规则降级：从负面事件中提取风险
        if events:
            negative_events = [
                e for e in events if e.sentiment == "negative"
            ]
            if negative_events:
                return [
                    f"{e.title}（{e.summary[:40]}）"
                    for e in negative_events[:3]
                ]

        return ["新闻分析未能提取风险"]

    # =================================================================
    # Step 3: 多源确认
    # =================================================================

    @staticmethod
    def _apply_source_count(
        events: List[NewsEvent], raw_news: List[Dict],
    ) -> List[NewsEvent]:
        """跨源合并：统计每个事件被多少独立来源报道。

        LLM 事件提取已合并同一事件的多源报道（见 prompt 第 3 条），
        但输出中 source_count 默认为 1。本方法反向匹配：
        将每条原始新闻映射到最相似的 LLM 事件，然后按事件统计独立来源数。
        """
        if not events or not raw_news:
            return events

        # Step 1: 每条原始新闻 → 最佳匹配的 LLM 事件
        event_source_map: dict[int, set[str]] = {i: set() for i in range(len(events))}

        for item in raw_news:
            item_title = item.get("title", "")
            if not item_title:
                continue
            source = item.get("source_type", "unknown")

            best_idx = -1
            best_ratio = 0.0
            for i, event in enumerate(events):
                ratio = SequenceMatcher(None, item_title, event.title).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_idx = i

            # 阈值 0.35：LLM 生成的事件标题是对多条新闻的概括，与单条标题相似度较低
            if best_idx >= 0 and best_ratio >= 0.35:
                event_source_map[best_idx].add(source)

        # Step 2: 设置 source_count 和 source_type
        for i, event in enumerate(events):
            sources = event_source_map[i]
            event.source_count = max(len(sources), 1)
            # source_type：保留 LLM 的 retail_forum 标注，仅对 professional 做字符串归因
            if sources and event.source_type == "professional":
                event.source_type = max(sources, key=lambda s: sum(
                    1 for item in raw_news if item.get("source_type") == s
                ))

        return events

    # =================================================================
    # Step 3.5: 事件持久化（跨日记忆）
    # =================================================================

    def _save_events_to_db(self, events: List[NewsEvent], raw_news: List[Dict]) -> None:
        """保存事件到 SQLite，支持跨日累积。

        去重策略：标题相似度 ≥0.6 视为同一事件，复用 _deduplicate_news() 逻辑。
        同一事件多次出现时：mention_count += 1，更新 last_seen，追加 sources_seen。
        """
        if not events:
            return

        try:
            db = DatabaseManager()
            today = date.today()

            with db.get_session() as session:
                # 加载最近 7 天的历史事件用于去重
                historical_events = self._load_historical_events_from_db(session, days=7)

                for event in events:
                    # 在历史事件中查找相似事件
                    matched_event_id = None
                    for hist_event in historical_events:
                        similarity = SequenceMatcher(None, event.title, hist_event.title).ratio()
                        if similarity >= 0.6:
                            matched_event_id = hist_event.event_id
                            break

                    if matched_event_id:
                        # 更新已有事件
                        db_event = session.query(Node4EventRecord).filter_by(event_id=matched_event_id).first()
                        if db_event:
                            db_event.mention_count += 1
                            db_event.last_seen = today
                            # 追加新来源
                            existing_sources = db_event.get_sources_seen()
                            if event.source_type and event.source_type not in existing_sources:
                                existing_sources.append(event.source_type)
                                db_event.set_sources_seen(existing_sources)
                            # 追加情感值到轨迹
                            sentiment_value = self._sentiment_to_float(event.sentiment)
                            trajectory = db_event.get_sentiment_trajectory()
                            trajectory.append(sentiment_value)
                            db_event.set_sentiment_trajectory(trajectory[-7:])  # 保留最近 7 天
                            db_event.updated_at = datetime.utcnow()
                    else:
                        # 创建新事件
                        event_id = self._generate_event_id(event.title, today)
                        db_event = Node4EventRecord(
                            event_id=event_id,
                            title=event.title,
                            source=event.source_type or "unknown",
                            first_seen=today,
                            last_seen=today,
                            mention_count=1,
                            lifecycle_state="emerging",
                            category=event.event_type,
                            significance_score=self._impact_to_score(event.impact_level),
                        )
                        db_event.set_sources_seen([event.source_type] if event.source_type else [])
                        db_event.set_sentiment_trajectory([self._sentiment_to_float(event.sentiment)])
                        session.add(db_event)

                # 更新所有事件的生命周期状态
                self._update_lifecycle_states(session)
                session.commit()

            logger.info("[Node 4] 事件持久化完成: %d 个事件", len(events))

        except Exception as e:
            logger.error("[Node 4] 事件持久化失败: %s", e, exc_info=True)

    # ── P1-1: 原始新闻持久化（跨日发酵检测）──

    @staticmethod
    def _normalize_title(title: str) -> str:
        """标题归一化：去标点/统一小写/压缩空白，用于精确匹配去重。"""
        import re
        t = title.strip().lower()
        t = re.sub(r'[^\w一-鿿]', '', t)  # 保留中文+字母数字
        t = re.sub(r'\s+', '', t)
        return t[:200]  # 截断到 200 字符

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        """标题相似度（Jaccard on character bigrams）。0~1。"""
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        bigrams_a = {a[i:i+2] for i in range(len(a)-1)}
        bigrams_b = {b[i:i+2] for i in range(len(b)-1)}
        if not bigrams_a or not bigrams_b:
            return 0.0
        intersection = bigrams_a & bigrams_b
        union = bigrams_a | bigrams_b
        return len(intersection) / len(union)

    def _persist_raw_news(self, raw_news: List[Dict]) -> None:
        """原始新闻持久化（P1-1）：精确匹配 + 相似度兜底去重。

        每次 _fetch_news() 后调用，将全量原始数据写入 node4_raw_news 表。
        同一新闻跨日重复出现时：appearance_count += 1, last_seen_date 更新。
        """
        if not raw_news:
            return

        today = date.today()
        try:
            db = DatabaseManager()
            with db.get_session() as session:
                persisted = 0
                updated = 0
                for item in raw_news:
                    title = item.get("title", "").strip()
                    source = item.get("source", "unknown")
                    if not title:
                        continue

                    title_norm = self._normalize_title(title)

                    # Step 1: 精确匹配（同标题 + 同来源）
                    existing = session.query(Node4RawNewsRecord).filter(
                        Node4RawNewsRecord.title_normalized == title_norm,
                        Node4RawNewsRecord.source == source,
                    ).first()

                    # Step 2: 精确未命中 → 同源近 3 日记录做相似度兜底
                    if not existing:
                        recent = session.query(Node4RawNewsRecord).filter(
                            Node4RawNewsRecord.source == source,
                            Node4RawNewsRecord.fetched_date >= today - timedelta(days=3),
                            Node4RawNewsRecord.lifecycle_state == 'active',
                        ).all()
                        for rec in recent:
                            if self._title_similarity(title_norm, rec.title_normalized) >= 0.45:
                                existing = rec
                                break

                    if existing:
                        existing.appearance_count += 1
                        existing.last_seen_date = today
                        updated += 1
                    else:
                        session.add(Node4RawNewsRecord(
                            title_normalized=title_norm,
                            title_original=title,
                            source=source,
                            fetched_date=today,
                            first_seen_date=today,
                            last_seen_date=today,
                            appearance_count=1,
                            lifecycle_state='active',
                        ))
                        persisted += 1

                session.commit()
                if persisted or updated:
                    logger.info(
                        f"[Node 4] 原始新闻持久化: 新增={persisted}, 更新={updated}"
                    )
        except Exception as e:
            logger.warning(f"[Node 4] 原始新闻持久化失败: {e}")

    def _detect_fermenting_events(self) -> List[Node4RawNewsRecord]:
        """检测发酵中的原始新闻：跨日出现 ≥3 次 AND 跨度 ≥2 天。

        参考国金证券舆情研究：中热度事件持续出现 → 正在发酵 → 注入 LLM prompt。
        """
        from sqlalchemy import func
        try:
            db = DatabaseManager()
            with db.get_session() as session:
                return session.query(Node4RawNewsRecord).filter(
                    Node4RawNewsRecord.appearance_count >= 3,
                    Node4RawNewsRecord.lifecycle_state == 'active',
                    func.julianday(Node4RawNewsRecord.last_seen_date)
                    - func.julianday(Node4RawNewsRecord.first_seen_date) >= 2,
                ).all()
        except Exception as e:
            logger.warning(f"[Node 4] 发酵检测失败: {e}")
            return []

    def _load_historical_events_from_db(self, session, days: int = 7) -> List[Node4EventRecord]:
        """从 SQLite 加载最近 N 天的事件（用于去重和 LLM prompt 注入）。"""
        cutoff_date = date.today() - timedelta(days=days)
        return session.query(Node4EventRecord).filter(
            Node4EventRecord.last_seen >= cutoff_date
        ).order_by(Node4EventRecord.mention_count.desc()).all()

    def _load_historical_events_for_prompt(self, days: int = 7) -> List[Dict[str, Any]]:
        """加载历史事件用于 LLM prompt 注入。返回字典列表，便于格式化。"""
        try:
            db = DatabaseManager()
            with db.get_session() as session:
                events = self._load_historical_events_from_db(session, days)
                return [
                    {
                        "event_id": e.event_id,
                        "title": e.title,
                        "lifecycle_state": e.lifecycle_state,
                        "mention_count": e.mention_count,
                        "days_span": (e.last_seen - e.first_seen).days + 1,
                        "source_count": len(e.get_sources_seen()),
                        "sentiment_avg": sum(e.get_sentiment_trajectory()) / len(e.get_sentiment_trajectory()) if e.get_sentiment_trajectory() else 0,
                        "category": e.category,
                    }
                    for e in events
                ]
        except Exception as e:
            logger.error("[Node 4] 加载历史事件失败: %s", e, exc_info=True)
            return []

    def _update_lifecycle_states(self, session) -> None:
        """批量更新事件的生命周期状态。

        状态机：
        - emerging: first_seen == today 且 mention_count == 1
        - fermenting: mention_count >= 3 且跨 >=2 天
        - peak: mention_count >= 10 且跨 >=3 个独立来源
        - fading: last_seen < today 且 mention_count 较峰值下降 >50%
        - archived: last_seen < today - 7 days
        """
        today = date.today()
        events = session.query(Node4EventRecord).all()

        for event in events:
            days_span = (event.last_seen - event.first_seen).days + 1
            source_count = len(event.get_sources_seen())

            # archived: 7 天未出现
            if event.last_seen < today - timedelta(days=7):
                event.lifecycle_state = "archived"
            # peak: 高热度 + 多来源
            elif event.mention_count >= 10 and source_count >= 3 and days_span >= 3:
                event.lifecycle_state = "peak"
            # fermenting: 中等热度 + 跨日
            elif event.mention_count >= 3 and days_span >= 2:
                event.lifecycle_state = "fermenting"
            # emerging: 新事件
            elif event.first_seen == today and event.mention_count == 1:
                event.lifecycle_state = "emerging"
            # fading: 热度下降（简化判定：last_seen < today）
            elif event.last_seen < today:
                event.lifecycle_state = "fading"

    def _generate_event_id(self, title: str, today: date) -> str:
        """生成事件 ID（基于标题和日期的哈希）。"""
        content = f"{title}|{today.isoformat()}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _sentiment_to_float(sentiment: str) -> float:
        """将情感标签转换为数值：positive=1.0, neutral=0.0, negative=-1.0。"""
        mapping = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
        return mapping.get(sentiment, 0.0)

    @staticmethod
    def _impact_to_score(impact_level: str) -> float:
        """将影响级别转换为数值分数。"""
        mapping = {"high": 1.0, "medium": 0.5, "low": 0.0}
        return mapping.get(impact_level, 0.0)

    def _format_historical_context_for_prompt(self, historical_events: List[Dict[str, Any]]) -> str:
        """格式化历史事件用于 LLM prompt 注入。"""
        if not historical_events:
            return ""

        # 按生命周期状态分组
        fermenting = [e for e in historical_events if e["lifecycle_state"] == "fermenting"]
        peak = [e for e in historical_events if e["lifecycle_state"] == "peak"]
        emerging = [e for e in historical_events if e["lifecycle_state"] == "emerging"]

        lines = ["历史事件（最近 7 天）："]

        if peak:
            lines.append("【峰值事件】（高热度，多来源确认）：")
            for e in peak[:3]:  # 最多 3 个
                lines.append(f"  - {e['title']}（出现 {e['mention_count']} 次，{e['source_count']} 个来源，跨 {e['days_span']} 天）")

        if fermenting:
            lines.append("【发酵事件】（连续多天出现，正在升温）：")
            for e in fermenting[:3]:
                lines.append(f"  - {e['title']}（出现 {e['mention_count']} 次，跨 {e['days_span']} 天，{e['source_count']} 个来源）")

        if emerging:
            lines.append("【新兴事件】（今日首次出现）：")
            for e in emerging[:2]:
                lines.append(f"  - {e['title']}（{e['category']}）")

        if not (peak or fermenting or emerging):
            return ""

        lines.append("\n请判断当前新闻中的事件是否与上述历史事件相关，并在 summary 中标注。")
        return "\n".join(lines)

    # =================================================================
    # Step 4: 评分计算
    # =================================================================

    @staticmethod
    def _calculate_impact_score(events: List[NewsEvent]) -> float:
        """阈值过滤 + 显著度加权评分。

        low-impact 不参与评分。
        medium/high 按权重加权。
        source_count ≥ 3 的事件额外 1.5x 加成。
        """
        scored_events = [
            e for e in events if e.impact_level in ("high", "medium")
        ]
        if not scored_events:
            return 0.0

        sentiment_map = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}

        weighted_sum = 0.0
        weight_total = 0.0

        for event in scored_events:
            base_weight = IMPACT_WEIGHTS.get(event.impact_level, 0.0)
            source_mult = (
                SOURCE_MULTIPLIER_BONUS
                if event.source_count >= SOURCE_MULTIPLIER_THRESHOLD
                else 1.0
            )
            sentiment_val = sentiment_map.get(event.sentiment, 0.0)
            effective_weight = base_weight * source_mult

            weighted_sum += sentiment_val * effective_weight
            weight_total += effective_weight

        if weight_total == 0:
            return 0.0

        score = weighted_sum / weight_total
        return max(-1.0, min(1.0, score))

    @staticmethod
    def _score_to_impact(score: float) -> str:
        if score > 0.15:
            return "positive"
        if score < -0.15:
            return "negative"
        return "neutral"

    # =================================================================
    # Step 5: reasoning 生成
    # =================================================================

    async def _generate_reasoning(
        self,
        overall_impact: str,
        impact_score: float,
        events: List[NewsEvent],
        concepts: List[Dict],
        risks: List[str],
    ) -> str:
        """LLM 生成 reasoning 文本，失败时降级为规则拼接。"""
        events_text = "\n".join(
            f"  - [{e.impact_level}/{e.sentiment}] {e.title}"
            for e in events[:8]
        ) or "  无"
        concepts_text = "、".join(
            c.get("concept_name", "") for c in concepts[:5]
        ) or "无"
        risks_text = "；".join(risks[:5]) if risks else "无"

        prompt = REASONING_PROMPT.format(
            overall_impact=overall_impact,
            impact_score=impact_score,
            event_count=len(events),
            events_text=events_text,
            concepts_text=concepts_text,
            risks_text=risks_text,
        )

        try:
            result = await asyncio.to_thread(
                self._call_llm,
                prompt,
                temperature=REASONING_TEMPERATURE,
                max_tokens=REASONING_MAX_TOKENS,
            )
            if result:
                return result
        except Exception as exc:
            logger.warning("[Node 4] reasoning LLM 生成失败: %s", exc)

        return self._fallback_reasoning(
            overall_impact, impact_score, events, concepts, risks,
        )

    @staticmethod
    def _fallback_reasoning(
        overall_impact: str,
        impact_score: float,
        events: List[NewsEvent],
        concepts: List[Dict],
        risks: List[str],
    ) -> str:
        """规则降级 reasoning。"""
        parts = [f"消息面整体{overall_impact}（评分 {impact_score:.2f}）。"]

        high_events = [e for e in events if e.impact_level == "high"]
        if high_events:
            titles = "、".join(e.title for e in high_events[:3])
            parts.append(f"高影响事件: {titles}。")

        positive = [e for e in events if e.sentiment == "positive"]
        negative = [e for e in events if e.sentiment == "negative"]
        if positive and not negative:
            parts.append("消息面以利好为主。")
        elif negative and not positive:
            parts.append("消息面以利空为主，注意风险。")

        if concepts:
            names = "、".join(c.get("concept_name", "") for c in concepts[:3])
            parts.append(f"新概念方向: {names}。")

        if risks:
            parts.append(f"风险提示: {'；'.join(risks[:3])}。")

        return "".join(parts)

    # =================================================================
    # Mock 输出（use_mock=True）
    # =================================================================

    def _mock_output(self) -> NewsContext:
        """Mock 输出（中性偏多）"""
        return NewsContext(
            status="normal",
            overall_impact="positive",
            impact_score=0.35,
            major_events=[
                NewsEvent(
                    event_type="policy",
                    title="工信部发布人形机器人创新发展指导意见",
                    summary="明确2026年产量突破10万台目标，支持关键技术攻关",
                    impact_level="high",
                    sentiment="positive",
                    affected_sectors=["humanoid_robots", "reducers", "sensors"],
                    timestamp="2026-07-22T09:00:00",
                    source_count=5,
                ),
                NewsEvent(
                    event_type="industry",
                    title="英伟达发布新一代AI芯片Blackwell Ultra",
                    summary="性能提升2倍，功耗降低30%，已获主要云厂商订单",
                    impact_level="high",
                    sentiment="positive",
                    affected_sectors=["ai_chips", "server_hardware"],
                    timestamp="2026-07-22T06:00:00",
                    source_count=8,
                ),
                NewsEvent(
                    event_type="global",
                    title="美联储暗示9月可能降息",
                    summary="鲍威尔称通胀持续回落，劳动力市场降温",
                    impact_level="medium",
                    sentiment="positive",
                    affected_sectors=[],
                    timestamp="2026-07-21T22:00:00",
                    source_count=4,
                ),
            ],
            concept_suggestions=[
                {
                    "concept_name": "算电协同",
                    "evidence": "AI数据中心电力需求激增，多地出台配套政策",
                    "mention_frequency": 12,
                },
            ],
            key_risks=[
                "中美科技摩擦升级风险",
                "半导体出口管制可能收紧",
            ],
            reasoning="[Mock] 政策面+产业面双重利好，关注人形机器人和AI芯片主线。",
            data_quality=DataQuality(
                available_fields=["policy_news", "industry_news", "global_news"],
                missing_fields=[],
            ),
        )
