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
from pathlib import Path
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.got.schemas import (
    NewsContext,
    NewsEvent,
    DataQuality,
)
from src.got.news_feeds import fetch_all_news_pool, filter_carried_pool
from src.got.news_url import canonicalize_url
from src.got.news_retrieval import (
    news_category,
    normalize_title,
    read_news,
    title_similarity,
)
from src.got.catalyst_config import (
    GOT_NEWS_EVIDENCE_GATING,
    GOT_ATTENTION_SHADOW,
    GOT_NEWS_FULLTEXT_ENABLED,
    GOT_NEWS_FULLTEXT_MAX_CHARS,
    GOT_LOW_SECTOR_RESCUE,
    GOT_NEWS_READING_ARCH,
    GOT_NEWS_TITLE_SHORTLIST_ENABLED,
    GOT_NEWS_TITLE_SHORTLIST_SIZE,
    GOT_NEWS_SHORTLIST_QUOTA,
    GOT_NEWS_CARRY_PREV_TITLES,
    GOT_NEWS_CARRY_DAYS,
    GOT_NEWS_CARRY_MAX,
    GOT_INDUSTRY_RECALL_ENABLED,
    INDUSTRY_RECALL_KEYWORDS,
    INDUSTRY_RECALL_MAX,
    LOW_RESCUE_TEMPERATURE,
    LOW_RESCUE_MAX_TOKENS,
)
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

# 标题粗筛（阶段 1）：输出上限依据 ≤12 条 × ~40 token/条
TITLE_SHORTLIST_TEMPERATURE = 0.2
TITLE_SHORTLIST_MAX_TOKENS = 1500
# 回滚路径（GOT_NEWS_TITLE_SHORTLIST_ENABLED=false）沿用的旧截断条数
LEGACY_PROMPT_MAX_ITEMS = 50
# 类目配额（P1-3）：同一粗类目在短名单中的上限（12 条上限的 50%，拍板值非数据标定；
# 影子期收 category_dist 实际分布后再调）
SHORTLIST_CATEGORY_QUOTA = 6


# =============================================================================
# Prompt 模板
# =============================================================================

TITLE_SHORTLIST_PROMPT = """你是 A 股消息面筛选员。下面是今日全部候选新闻标题（已按编号列出，共 {total} 条）。

{titles}

任务：挑出对**中国经济与 A 股板块**可能产生实质影响的条目，最多 {limit} 条。
按"影响深度"判断，不按标题炸裂程度，也不按公司规模：
- 挑：能改变供需格局/政策方向/技术路线/资金成本的事件——财报与业绩指引、产业政策与监管、重大技术发布（开源/量产/突破）、产业链供需与价格异动、地缘与大宗商品、宏观数据与央行操作、大额资本动作、影响行业的公司级事件。
- 不挑：单纯行情播报（"XX涨超X%""多股涨停"）、论坛口水、无主体的评论转载、软文、与 A 股无传导链的境外社会新闻（娱乐/体育/治安/生活类）、标题无法判断主体的条目。
- 境外宏观/政治/地缘事件（选举、关税、制裁、央行/财政政策、冲突等）不按"境外新闻"排除：只要存在关税/汇率/利率/大宗商品/风险偏好/产业链传导链，必须保留；无法映射单一板块时仍可入选，reason 写明传导链。
- 关键区分：一条"某小公司三季报"若只影响自身，影响低于"改变全行业供需/政策的产业级事件"；反之若该公司是产业链关键环节，则影响深远。
- [今日早前]/[昨日]/[N日前] 前缀的是此前已出现但未分析的条目（数字为距今天的自然日数；跨周末/假日时其市场时效以重新开盘计，不按日龄折减），若具实质影响同样要挑。
- 每条标题最多被挑一次；挑中的要给一句话理由（引用标题里的实体与影响链条）。{quota_req}
输出 JSON：{{"selected":[{{"index":3,"reason":"..."}}]}}
index 必须是上面出现过的编号。只输出 JSON。"""

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
   - source_type: 事件的主要信息来源类型——"professional"（专业媒体/通讯社）或 "retail_forum"（散户论坛/社区讨论）。如果事件主要由[NGA论坛]等论坛标签的新闻首先提及或讨论，标记为"retail_forum"{src_index_req}
   - onset_days: 估计该事件对板块产生可观测影响的滞后期，单位交易日，1-10 的整数；无法判断填 null（不要猜）
2. 不要遗漏重要事件，也不要重复
3. 同一事件被多家媒体报道时，合并为一个事件
4. 如果当前新闻中的事件与历史事件相关（同一主题或因果关系），请在 summary 中标注"延续自：[历史事件标题]"
5. 严格按 importance 排序，high 在前
6. 素材中带 [产业] 前缀的条目是按产业链动作词表（开源/量产/涨价/出口管制…）补召回的，可能不在初筛短名单里；若具实质影响同样要提取
7. 境外宏观/政治/地缘事件（选举、关税、制裁、央行/财政政策、冲突等）若存在关税/汇率/利率/大宗商品/风险偏好/产业链传导链，必须提取；无法映射单一板块时 affected_sectors 可留空（不要为凑板块强行映射），但 summary 必须写清传导链

输出 JSON 格式（不要 markdown 代码块）：
{{"events": [{{"title": "...", "summary": "...", "event_type": "...", "impact_level": "...", "sentiment": "...", "affected_sectors": ["..."], "affected_stocks": ["..."], "source_type": "professional", "onset_days": 3{src_index_example}}}]}}"""

LOW_RESCUE_PROMPT = """你是 A 股板块研究员。以下事件被一审判为对大盘「low（轻微影响）」——大盘口径没错，但其中部分事件对**特定板块**可能是「high（重大影响）」。你的任务是逐条判定每个事件**对其映射板块**的影响等级。

判定核心（一条主线）：该事件对映射板块是**共同冲击**还是**份额重分配**？
共同冲击 = 改变板块整体的共同基本面——行业需求扩张/收缩、技术路线验证或证伪、政策管辖变化、供应链成本变化；板块内多数公司同向受益/受损。
份额重分配 = 只改变板块内部竞争格局——竞对受损、个别公司拿单/抢份额；板块整体不动。份额重分配不是板块级利好，sector_impact 封顶 medium。

当前板块精选概念列表：{concept_list}

候选事件（一审 low，已有板块映射）：
{events_json}

## 三维判定标准

1. **冲击共性**：事件是否作用于映射板块的共同基本面（行业需求、技术路线、政策管辖、供应链成本）？竞对破产、竞对丢单等零和事件即使直接作用于板块内公司，也只是份额重分配，判 low。
2. **板块占比**：板块内是否有相当数量或较大权重的公司把事件主题作为主要收入来源或核心叙事？个别公司沾边撑不起板块级影响。
3. **增量信息**（两级）：
   硬规则——首报 vs 追进：事件是否为该进展的**首次披露**？同一事实的追进报道、重复确认、已兑现回声无增量信息，判 low（与催化剂日历 D1 回声守卫同口径）。
   先验——量化 vs 软故事：含具体数字/日期/量级（订单金额、产能、税率、交付节点）的量化事件市场欠反应、漂移可观；发布会、人物言论、宏观评论等软故事易过度反应。软故事存疑时在 high/medium 之间降档，不硬排除。

## sector_impact 分级

**high**：维度 1（共同冲击）与维度 2（板块占比）同时满足，且为首报、非回声。事件是板块级需求/技术/政策叙事的增量催化。
**medium**：板块相关但不满足全部条件——共同冲击但板块占比不足；或板块占比足但属份额重分配；或首报但软故事存疑。
**low**：份额重分配、追进/回声、或对板块仅情绪面间接传导（与大盘口径无实质差异）。

## Few-shot

事件：「特斯拉 Roadster 完成首次飞行演示」，板块：[汽车零部件, 飞行汽车]
→ eVTOL 技术可行性首次实证，改变低空经济板块整体商用化进程预期（维度1 共同冲击✓）；板块核心叙事即商用化进展（维度2✓）；演示当日实际发生=进展实证首报、非预告重申（维度3✓）→ sector_impact=high

事件：「某新能源公司获 10GWh 储能大单」，板块：[储能]
→ 订单仅个别公司受益，不改板块整体供需格局（维度1 份额重分配✗）→ sector_impact=low

事件：「某省发布促消费若干措施」，板块：[零售]
→ 政策非直接作用于零售具体业务（维度1✗）；板块叙事多元不以此为核（维度2✗）→ sector_impact=low

## 输出

输出 JSON 数组，每项：
{{"index": 0, "sector_impact": "high|medium|low", "reason": "判定理由（引用命中的维度，一句话）"}}
只输出 JSON。index 对应候选事件列表的 0-based 序号。宁严勿松：拿不准 high 还是 medium 时给 medium。"""


def _sample_long_text(text: str, limit: int) -> str:
    """超长正文头/中/尾采样（Horizon 策略）：头 50% + 中 25% + 尾 25%。

    ≤limit 原样返回。头/中/尾采样优于纯截断：政策文件的关键结论常在末尾（"自X日起施行"），
    纯头截断会把它切掉。
    """
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    head_len = int(limit * 0.5)
    mid_len = int(limit * 0.25)
    tail_len = limit - head_len - mid_len
    mid_start = max(head_len, (len(text) - mid_len) // 2)
    mid = text[mid_start:mid_start + mid_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    return f"{text[:head_len]}\n…（中略）\n{mid}\n…（中略）\n{tail}"


def _carry_age_prefix(item: Dict) -> str:
    """补读条目的自然日龄前缀（0=当日更早时点 / 1=昨日 / n=n 自然日前）。

    只标注来源与日龄，不改排序也不打影响分（市场时效不按自然日龄折减，
    跨周末/假日由 LLM 结合题材自行判断）。
    """
    age = item.get("carried_age_days")
    if age == 0:
        return "[今日早前] "
    if age:
        return "[昨日] " if age == 1 else f"[{age}日前] "
    return ""


class NewsAnalysisNode:
    """消息面分析节点"""

    def __init__(self, use_mock: bool = True, got_runtime=None):
        self.use_mock = use_mock
        self.got_runtime = got_runtime

    async def run(self, bar_complete: bool = True, as_of: Optional[date] = None) -> NewsContext:
        """执行消息面分析

        Args:
            bar_complete: 数据完整性标记（True=收盘后数据, False=盘中快照）
            as_of: 业务日（回放/补跑必须与归档/Phase 2 同源）；缺省=当日
        """
        business_date = as_of or date.today()
        self._as_of = business_date
        if self.use_mock:
            logger.info("[Node 4] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 4] 开始真实数据消息面分析")

        # §6 Intelligence 源初始化（幂等，重复调用跳过已存在的源）
        try:
            from src.got.intelligence_service import initialize_builtin_sources
            initialize_builtin_sources()
        except Exception as e:
            logger.warning("[Node 4] Intelligence 初始化失败（降级跳过）: %s", e)

        # Step 1: 并发采集新闻
        raw_news, raw_pool, data_quality = await self._fetch_news()

        # Step 1.5: 原始新闻持久化（P1-1: 跨日发酵检测基础）
        # 落库用**未截断**的全量池：热榜条目（无发布时间）排在 weight 尾部，不会进 merged
        self._persist_raw_news(raw_pool, as_of=business_date)

        if not raw_news:
            logger.warning("[Node 4] 未获取到任何新闻数据")
            return NewsContext(
                status="failed",
                overall_impact="neutral",
                impact_score=0.0,
                reasoning="新闻采集全部失败，无法分析",
                data_quality=data_quality,
            )

        # 部分失败判定：`_fetch_news` 只填 available/missing_fields（fetch_failed_fields 恒空，
        # 旧检查里的 cls_telegraph/akshare_news/search_news 已不起作用）→ status 永不 degraded。
        # 只对高价值 4 线降级：sina_feed/intelligence 缺失不降级（滚动线外部源多，
        # 单线缺失不等于分析能力受损；全源为空的 failed 分支已在上方处理）。
        partial_failure_fields = (
            "cls_telegraph", "wallstreetcn", "eastmoney_kuaixun", "newsnow_hot",
        )
        has_partial_failure = any(
            f in data_quality.missing_fields for f in partial_failure_fields
        )

        logger.info("[Node 4] 采集到 %d 条原始新闻", len(raw_news))

        # Step 2: 3 并发 LLM 分析
        events, concepts, risks = await self._analyze_with_llm(raw_news)

        # Step 3: 多源确认
        events = self._apply_source_count(events, raw_news)

        # Step 3.0: 稳定事件身份（normalize_title + 首次来源 + 业务日）
        # 必须在 low rescue 之前生成，救援/升级不改变身份；_save_events_to_db 命中
        # 历史相似事件时复用历史 event_id（禁止用本次运行日重写历史身份）。
        self._assign_stable_event_ids(events, business_date)

        # Step 3.1: low 事件板块级救援（Q1 修复，GOT_LOW_SECTOR_RESCUE=1 缺省直连，用户裁决 2026-10-16）：
        # LLM 按三维判定标准复审 low 事件的板块级影响，sector_impact=high 的事件升级 medium
        # 进链路（DB/NewsContext/impact_score/日历候选池自然吃到），终结 low 无痕蒸发。
        # 复审范围=affected_sectors 非空且非 negative（D2）；sector_impact 全量落影子 JSON（D4）。
        try:
            events = await self._rescue_low_events(events)
        except Exception as exc:  # noqa: BLE001 — 救援旁路：失败降级=保持原过滤行为
            logger.warning("[Node 4] low 事件板块级救援失败（降级为原过滤行为）: %s", exc)
            events = [e for e in events if e.impact_level != "low"]
        else:
            events = [e for e in events if e.impact_level != "low"]

        # Step 3.5: 事件持久化（跨日记忆）
        self._save_events_to_db(events, raw_news, as_of=business_date)

        # Step 4: 评分计算
        impact_score = self._calculate_impact_score(events)
        overall_impact = self._score_to_impact(impact_score)

        # Step 5: reasoning 生成（纯规则；A6 回填 concepts/risks 后重算，见 Node 6）
        reasoning = self._fallback_reasoning(
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
            builder = CatalystCalendarBuilder(got_runtime=getattr(self, "got_runtime", None))
            calendar = await builder.build_calendar(bar_complete=bar_complete)
            result.catalyst_calendar = calendar
            logger.info(
                "[Node 4] 催化剂日历: %d 事件, mode=%s, bar_complete=%s",
                len(calendar.events), calendar.refresh_mode, bar_complete,
            )
        except Exception as exc:
            logger.warning("[Node 4] 催化剂日历构建失败（已降级）: %s", exc)

        # ── P0-1 attention 影子（纯观测，缺省开） ──
        self._write_attention_shadow()

        logger.info(
            "[Node 4] 完成 — status=%s, impact=%s(%.2f), events=%d",
            status, overall_impact, impact_score, len(events),
        )
        return result

    @staticmethod
    def _write_attention_shadow() -> None:
        """P0-1 attention 影子（缺省开）：聚合当日热榜平台扩散 → shadow_attention.json。

        纯观测：不写 `source_count`、不进 prompt、不进 `_detect_fermenting_events`。
        开关 `GOT_ATTENTION_SHADOW=false` → 缺文件不回现状（零产物）。
        归档前直写当日目录，文件名须登记 `archive.PRE_ARCHIVE_ARTIFACTS`，
        否则同日二次归档的原子替换会把它删除（2026-10-30 实锤）。
        """
        if not GOT_ATTENTION_SHADOW:
            return
        try:
            from src.got.attention_shadow import build_attention_shadow
            build_attention_shadow(Path("data/got_daily") / date.today().strftime("%Y%m%d"))
        except Exception as exc:  # noqa: BLE001 — 影子旁挂，失败不阻断主流程
            logger.warning("[Node 4] attention 影子落盘失败（已忽略）: %s", exc)

    # =================================================================
    # Step 1: 新闻采集
    # =================================================================

    async def _fetch_news(
        self,
        skip_sources: Iterable[str] = (),
        include_intelligence: bool = True,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], DataQuality]:
        """7 源串行采集（新浪/CLS/华尔街见闻/东财/NewsNow/NGA/同花顺）+ Intelligence 合并。

        Returns:
            (merged_news, raw_pool, data_quality)
            - merged_news：截断到 MAX_MERGED_NEWS 的 LLM 输入口径（`run` 的分析素材）
            - raw_pool：截断前的去重全量池（`_persist_raw_news` 落库口径）——热榜条目
              （NewsNow/同花顺，无发布时间）结构性地排在 weight 尾部队列，只会出现在这里

        `skip_sources` / `include_intelligence` 供定时快照采集降载：跳过论坛/热榜/情报热榜，
        只留滚动新闻线（CLS 1–3h、东财/见闻/NewsNow 数小时，必须高频采才不会整段丢失）；
        NGA/同花顺热榜/情报热榜非时效源，仍由每日 GOT 全量采集。
        """
        merged, raw_pool, stats = await asyncio.to_thread(fetch_all_news_pool, skip_sources)

        # §6 Intelligence 通道：与 5 源并行，合并去重后送 LLM
        intelligence_items = await self._fetch_intelligence() if include_intelligence else []
        if intelligence_items:
            # 去重合并（基于标题相似度）
            merged = self._merge_with_intelligence(merged, intelligence_items)
            raw_pool = self._merge_with_intelligence(raw_pool, intelligence_items)
            stats["intelligence"] = len(intelligence_items)

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
            "intelligence": "intelligence_service",
        }
        available = [source_map.get(s, s) for s in available]
        missing = [source_map.get(s, s) for s in missing]

        data_quality = DataQuality(
            available_fields=available,
            missing_fields=missing,
        )

        total = sum(stats.values())
        logger.info(
            "[Node 4] 5+1 源采集完成: sina=%d cls=%d wallstreetcn=%d eastmoney=%d newsnow=%d intelligence=%d → 合并 %d 条",
            stats.get("sina", 0), stats.get("cls", 0),
            stats.get("wallstreetcn", 0), stats.get("eastmoney", 0),
            stats.get("newsnow", 0), stats.get("intelligence", 0),
            len(merged),
        )
        return merged, raw_pool, data_quality

    async def _fetch_intelligence(self) -> List[Dict[str, Any]]:
        """§6 Intelligence 情报采集（独立通道）"""
        try:
            from src.got.intelligence_service import fetch_intelligence_items
            return await asyncio.to_thread(fetch_intelligence_items, 1)  # 最近 1 天
        except Exception as e:
            logger.warning("[Node 4] Intelligence 采集失败（降级跳过）: %s", e)
            return []

    def _merge_with_intelligence(
        self,
        existing_news: List[Dict[str, Any]],
        intelligence_items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """合并 Intelligence 情报到现有新闻列表（基于标题相似度去重）"""
        from difflib import SequenceMatcher

        merged = list(existing_news)
        existing_titles = {item.get("title", "") for item in existing_news}

        for intel_item in intelligence_items:
            intel_title = intel_item.get("title", "").strip()
            if not intel_title:
                continue

            # 精确匹配去重
            if intel_title in existing_titles:
                continue

            # 相似度去重（阈值 0.6，比 news_feeds 的 0.45 更严格）
            is_duplicate = False
            for existing_title in existing_titles:
                ratio = SequenceMatcher(None, intel_title, existing_title).ratio()
                if ratio >= 0.6:
                    is_duplicate = True
                    break

            if not is_duplicate:
                merged.append(intel_item)
                existing_titles.add(intel_title)

        return merged

    # =================================================================
    # Step 2: 4 次串行 LLM 分析（标题粗筛 → 事件提取 → 概念发现 → 风险总结）
    # =================================================================

    def _shortlist_titles(
        self, raw_news: List[Dict], quota_out: Optional[Dict[str, Any]] = None,
    ) -> List[Dict]:
        """把当日**全部**候选标题一次性交 LLM 初筛，返回选中条目（输入不设条数上限）。

        事件价值判定交给 LLM：weight 只度量「媒体多热」，不度量「对 A 股的影响深度」
        （实测一条个股涨停播报 22.2 > 产业级开源发布/全球气候异常 14.8），规则打分已被
        证伪（同 Node 6 A6 阶段 2 「规则价值判断不可靠」教训）。粗筛只读标题不带摘要：
        成本低于细读，且覆盖全量不受长上下文位置偏差影响（Lost in the Middle,
        arXiv:2307.03172）。

        `quota_out`：`GOT_NEWS_SHORTLIST_QUOTA=true` 时的影子记录出口（picked/dropped/
        category_dist），开关关闭不动它。

        失效契约（**不降级到规则排序**）：LLM 空响应 / JSON 解析失败 / 有效 index 为 0
        → 返回 []，当日不产事件。用 weight 兜底等于 LLM 失效时主动选择错误事件——
        宁可漏，不可错。
        """
        ordered = [it for it in raw_news if (it.get("title") or "").strip()]
        if not ordered:
            return []

        lines = []
        for i, item in enumerate(ordered, 1):
            prefix = _carry_age_prefix(item)
            lines.append(f"{i}. {prefix}{item['title'].strip()}")
        prompt = TITLE_SHORTLIST_PROMPT.format(
            total=len(ordered),
            limit=GOT_NEWS_TITLE_SHORTLIST_SIZE,
            titles="\n".join(lines),
            quota_req=(
                "\n- 挑选时保持类目分散：同一宏观政策/同一产业主题的条目合计不超过"
                f" {SHORTLIST_CATEGORY_QUOTA} 条（{GOT_NEWS_TITLE_SHORTLIST_SIZE} 条上限内"
                "至少 3 个不同主题方向），除非当日确实只有单一主线。"
            ) if GOT_NEWS_SHORTLIST_QUOTA else "",
        )

        raw_result = self._call_llm(
            prompt,
            temperature=TITLE_SHORTLIST_TEMPERATURE,
            max_tokens=TITLE_SHORTLIST_MAX_TOKENS,
            task_id="node4.title_shortlist",
        )
        if not raw_result:
            logger.warning(
                "[Node 4] 标题粗筛失败，当日不产事件（不降级到规则排序）: LLM 空响应"
            )
            return []

        data = self._extract_json(raw_result)
        selected = data.get("selected") if isinstance(data, dict) else None
        if not isinstance(selected, list):
            logger.warning(
                "[Node 4] 标题粗筛失败，当日不产事件（不降级到规则排序）: "
                "JSON 解析失败（输出前120字: %s）",
                raw_result[:120],
            )
            return []

        picked: List[Dict] = []
        seen = set()
        for entry in selected:
            if len(picked) >= GOT_NEWS_TITLE_SHORTLIST_SIZE:
                break
            if not isinstance(entry, dict):
                continue
            entry_index = entry.get("index")
            if isinstance(entry_index, bool):
                continue
            try:
                idx = int(entry_index)
            except (TypeError, ValueError):
                continue
            if idx in seen or not (1 <= idx <= len(ordered)):
                continue
            seen.add(idx)
            picked.append(ordered[idx - 1])

        if not picked:
            logger.warning(
                "[Node 4] 标题粗筛失败，当日不产事件（不降级到规则排序）: 有效 index 为 0"
            )
            return []
        if GOT_NEWS_SHORTLIST_QUOTA:
            picked, quota_record = self._apply_category_quota(picked)
            if quota_out is not None:
                quota_out.update(quota_record)
            if quota_record["dropped_by_quota"]:
                logger.info(
                    "[Node 4] 类目配额裁剪: 保留 %d 条，裁掉 %d 条（%s）",
                    len(picked), len(quota_record["dropped_by_quota"]),
                    quota_record["category_dist"],
                )
        return picked

    @classmethod
    def _apply_category_quota(cls, picked: List[Dict]) -> Tuple[List[Dict], Dict[str, Any]]:
        """同一粗类目超过 `SHORTLIST_CATEGORY_QUOTA` 的部分整组裁掉（空位不回填）。

        粗类目 = 标题命中的首个 `INDUSTRY_RECALL_KEYWORDS["industry_entity"]` 词，未命中记
        「其他」。词表只做形态归类（与产业召回同一口径，实现见 `news_retrieval.news_category`），
        **不做价值判断**：裁谁留谁完全按 LLM 给出的顺序，只限制单类目占比。
        返回 (裁剪后条目, 影子记录)。
        """
        buckets: Dict[str, List[Dict]] = {}
        for item in picked:
            buckets.setdefault(news_category(item.get("title", "")), []).append(item)

        kept_ids = set()
        dropped: List[str] = []
        for items in buckets.values():
            for item in items[:SHORTLIST_CATEGORY_QUOTA]:
                kept_ids.add(id(item))
            dropped.extend(it.get("title", "") for it in items[SHORTLIST_CATEGORY_QUOTA:])

        kept = [it for it in picked if id(it) in kept_ids]
        return kept, {
            "picked": [it.get("title", "") for it in kept],
            "dropped_by_quota": dropped,
            "category_dist": {cat: len(items) for cat, items in buckets.items()},
        }

    @staticmethod
    def _write_shortlist_quota_shadow(record: Dict[str, Any]) -> None:
        """落 shadow_shortlist_quota.json（tmp+replace 原子写，影子产物同款惯例）。

        归档前直写当日目录，文件名须登记 `archive.PRE_ARCHIVE_ARTIFACTS`。
        """
        try:
            day_dir = Path("data/got_daily") / date.today().strftime("%Y%m%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            out = day_dir / "shadow_shortlist_quota.json"
            tmp = out.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(out)
            logger.info(
                "[Node 4] 短名单配额影子落盘: picked=%d dropped=%d",
                len(record.get("picked", [])), len(record.get("dropped_by_quota", [])),
            )
        except OSError as exc:
            logger.warning(
                "[Node 4] shadow_shortlist_quota.json 落盘失败（已忽略）: %s", exc,
            )

    def _recall_industry_candidates(
        self, raw_news: List[Dict], exclude: Optional[List[Dict]] = None,
    ) -> List[Dict]:
        """产业级事件召回（2026-10-22）：补阶段 1 短名单装不下的产业链信号。

        反例（DeepInfer×算力）：`DeepSeek开源推理框架DeepInfer，V4.1 Flash登顶Agent Arena
        开源榜`（10-20，source_count=1）没有涨停/暴涨/政策类热度词、也不是多源重复，
        788 条压到 12 条时排在第 12 名外 → LLM 从未看到它 → 日历不可能产出国产算力催化。

        词表是**形态过滤**而非价值判断（`INDUSTRY_RECALL_KEYWORDS`）：
        标题**同时**命中 ≥1 供给侧动作词 与 ≥1 产业实体词 → 召回；影响锚词不计入必要
        条件，仅用于日志（最终影响大小仍由 LLM 判，本方法不排序价值）。

        量控：命中数 > INDUSTRY_RECALL_MAX 时按 source_count 降序取前 N（与短名单 12 条
        合计 ≤20 条进细读，控制在原细读成本 2 倍内）。
        开关 GOT_INDUSTRY_RECALL_ENABLED=false → []（逐字节回现状）。
        """
        if not GOT_INDUSTRY_RECALL_ENABLED or not raw_news:
            return []

        supply_actions = INDUSTRY_RECALL_KEYWORDS["supply_action"]
        entities = INDUSTRY_RECALL_KEYWORDS["industry_entity"]
        anchors = INDUSTRY_RECALL_KEYWORDS["impact_anchor"]

        excluded_titles = {
            self._normalize_title(it.get("title", "")) for it in (exclude or [])
        }

        hits: List[Dict] = []
        seen: set[str] = set()
        for item in raw_news:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            norm = self._normalize_title(title)
            if norm in excluded_titles or norm in seen:
                continue
            if not any(kw in title for kw in supply_actions):
                continue
            if not any(kw in title for kw in entities):
                continue
            seen.add(norm)
            recalled = dict(item)  # 浅拷贝：不改调用方 raw_news（概念发现/持久化仍读原对象）
            recalled["recalled_industry"] = True
            recalled["_recall_anchor_hit"] = any(kw in title for kw in anchors)
            hits.append(recalled)

        # source_count 降序（缺失按 1）——与短名单「多源确认优先」口径一致，时间序保持稳定
        hits.sort(key=lambda it: int(it.get("source_count") or 1), reverse=True)
        if len(hits) > INDUSTRY_RECALL_MAX:
            hits = hits[:INDUSTRY_RECALL_MAX]

        if hits:
            logger.info(
                "[Node 4] 产业召回: %d 条（含影响锚 %d 条），上限 %d",
                len(hits), sum(1 for h in hits if h.get("_recall_anchor_hit")),
                INDUSTRY_RECALL_MAX,
            )
            for h in hits[:5]:
                logger.info("[Node 4] 产业召回命中: %s", (h.get("title") or "")[:60])
        return hits

    def _load_unabsorbed_titles(self, days: Optional[int] = None) -> List[Dict]:
        """漏读补读：近 N 个自然日已落库、但尚未被任何事件吸收的原始标题（全量，不做预截断）。

        背景（2026-10-20 实测）：当日 364 条原始新闻，Node 4 只把 weight 前 50 条
        喂给 LLM，《DeepSeek开源推理框架DeepInfer…》从未进入 node4_events，次日
        催化剂日历完全没有它。原始标题已在 node4_raw_news 落库，直接回灌粗筛池。

        **扩围（2026-10-22）**：窗口从「昨日及更早」扩到 **含当日更早时点**（定时快照
        写入的行，`fetched_date <= today`）。GOT 13:20 运行时的实时窗口看不到 09:30 前
        已滚出的短窗源条目，靠本通道补回；自然日龄经 `carried_age_days` 显式曝给 LLM
        （0=当日更早/1=昨日/n=n 自然日前），**不做信息强度数值衰减**——判定权在粗筛，
        跨周末/假日的市场时效不按自然日龄折减。

        **不做热度排序/预截断**（2026-10-22 真库实测教训）：按 `appearance_count`
        降序取前 20 条，被多轮热榜刷高计数的娱乐/社会条目占满，`appearance_count=1`
        的优质单次新闻（实测 DeepInfer）全部挤掉——这正是 weight 热度项的同类误判。
        候选价值只由 LLM 粗筛判定；本函数只做**标题归一化去重**（同一新闻多源重复）。

        排除已吸收：直接按 `Node4EventRecord.title`（`last_seen` 覆盖 N+1 天）做归一化
        比较——不用 `_load_recent_news_events`，后者按 lifecycle 只保留 emerging/
        fermenting/peak，已 fading 的已吸收事件会让对应原稿被重复回灌。归一化复用
        `_normalize_title`，不另写第二套。窗口缺省 `GOT_NEWS_CARRY_DAYS`（3 日，覆盖
        周五→周一）；池超 `GOT_NEWS_CARRY_MAX` 按最新优先截断（LLM 输入预算护栏）。
        查询异常 → []，不阻断主流程。
        """
        days = GOT_NEWS_CARRY_DAYS if days is None else days
        today = date.today()
        try:
            # 局部导入：单测按既有范式 patch("src.storage.DatabaseManager") 即可注入假 session
            from src.storage import DatabaseManager

            db = DatabaseManager()
            with db.get_session() as session:
                rows = (
                    session.query(Node4RawNewsRecord)
                    .filter(
                        Node4RawNewsRecord.fetched_date >= today - timedelta(days=days),
                        # 原为 < today：纳入当日更早时点快照行（GOT 实时窗口已滚出的条目）
                        Node4RawNewsRecord.fetched_date <= today,
                        Node4RawNewsRecord.lifecycle_state == "active",
                    )
                    .order_by(
                        Node4RawNewsRecord.fetched_date.desc(),
                        Node4RawNewsRecord.id.desc(),
                    )
                    .all()
                )
                candidates = [
                    (
                        r.title_original or "", r.source or "", r.url or "",
                        str(r.published_at or ""), r.fetched_date,
                    )
                    for r in rows
                ]
                absorbed_rows = (
                    session.query(Node4EventRecord.title)
                    .filter(
                        Node4EventRecord.last_seen >= today - timedelta(days=days + 1),
                    )
                    .all()
                )
        except Exception as exc:
            logger.warning("[Node 4] 未吸收标题读取失败（跳过补读）: %s", exc)
            return []

        absorbed = {self._normalize_title(title or "") for (title,) in absorbed_rows}
        carried: List[Dict] = []
        seen = set()
        for title, source, url, published_at, fetched_date in candidates:
            norm = self._normalize_title(title)
            if not norm or norm in absorbed or norm in seen:
                continue
            seen.add(norm)
            fetched = fetched_date
            if isinstance(fetched, datetime):
                fetched = fetched.date()
            elif isinstance(fetched, str):
                try:
                    fetched = date.fromisoformat(fetched[:10])
                except ValueError:
                    fetched = None
            # 缺失/不可解析 → 1：保守沿用旧「昨日补读」语义
            age = (today - fetched).days if isinstance(fetched, date) else 1
            carried.append({
                "title": title,
                "snippet": "",
                "source": source,
                "source_type": source,
                "url": url,
                "published_date": published_at,
                "carried_age_days": age,
            })
        if len(carried) > GOT_NEWS_CARRY_MAX:
            logger.warning(
                "[Node 4] 补读池 %d 条超上限 %d，按最新优先截断（非热度筛选）",
                len(carried), GOT_NEWS_CARRY_MAX,
            )
            carried = carried[:GOT_NEWS_CARRY_MAX]
        return carried

    async def _analyze_with_llm(
        self, raw_news: List[Dict],
    ) -> Tuple[List[NewsEvent], List[Dict], List[str]]:
        """4 次 LLM 调用：标题粗筛 → 事件提取 → 概念发现 → 风险总结（串行，避免 API 并发空响应）。"""
        gated = GOT_NEWS_EVIDENCE_GATING
        # 两段式：阶段 1 全量标题初筛，阶段 2 只对选中条目细读抽事件。
        # GOT_NEWS_TITLE_SHORTLIST_ENABLED=false 回滚到旧口径（weight 前 50 条直接喂细读）。
        if GOT_NEWS_TITLE_SHORTLIST_ENABLED:
            carried: List[Dict] = []
            if GOT_NEWS_CARRY_PREV_TITLES:
                carried = self._load_unabsorbed_titles()
                if carried:
                    today_norms = {
                        self._normalize_title(it.get("title", "")) for it in raw_news
                    }
                    carried = [
                        c for c in carried
                        if self._normalize_title(c.get("title", "")) not in today_norms
                    ]
            # 粗筛池降噪（2026-10-29）：娱乐/社会热榜只从 LLM 粗筛入口剔除，
            # `raw_news` 与 DB 行不动（概念发现/风险总结/attention 观测口径不变）。
            pool_for_shortlist = filter_carried_pool(list(raw_news) + carried)

            if GOT_NEWS_READING_ARCH == "cluster":
                shortlist, retrieval_trace = await read_news(self, pool_for_shortlist)
                # 产业召回：exclude 增加簇成员标题，防同簇另一说法再进细读
                exclude_items: List[Dict] = list(shortlist)
                for item in shortlist:
                    exclude_items.extend(
                        {"title": title} for title in item.get("member_titles") or []
                    )
                recalled = self._recall_industry_candidates(
                    pool_for_shortlist, exclude=exclude_items,
                )
                extraction_pool = shortlist + recalled
                logger.info(
                    "[Node 4] 分层粗筛(cluster): 池 %d → 细读 %d 条"
                    "（补读 %d、产业召回 %d；簇 %d / 片 %d / 终审池 %d / %s）",
                    len(pool_for_shortlist), len(extraction_pool), len(carried), len(recalled),
                    retrieval_trace.get("cluster_count", 0),
                    retrieval_trace.get("shard_count", 0),
                    retrieval_trace.get("finalist_count", 0),
                    retrieval_trace.get("review_status"),
                )
            else:
                quota_record: Dict[str, Any] = {}
                shortlist = await asyncio.to_thread(
                    self._shortlist_titles,
                    pool_for_shortlist, quota_out=quota_record,
                )
                if quota_record:
                    self._write_shortlist_quota_shadow(quota_record)
                # 产业级召回（2026-10-22）：短名单之外按「供给侧动作×产业实体」补召回，
                # 与短名单合并去重后一起进阶段 2 细读（DeepInfer 类单源产业发布不再缺席）
                recalled = self._recall_industry_candidates(raw_news, exclude=shortlist)
                extraction_pool = shortlist + recalled
                logger.info(
                    "[Node 4] 标题粗筛: %d → %d 条（含补读 %d、产业召回 %d）",
                    len(pool_for_shortlist), len(extraction_pool), len(carried), len(recalled),
                )
            # 概念/风险不再单独调用 LLM（2026-10-30 运行时收敛）：概念由 Node 6 A6
            # 共享语义产物回填，风险先按负面事件规则兜底、A6 成功后回填。
            if GOT_NEWS_FULLTEXT_ENABLED:
                extraction_pool = await self._enrich_fulltext(extraction_pool)
            news_text, ordered_items = self._format_news_for_prompt(
                extraction_pool, gated=gated, fulltext=GOT_NEWS_FULLTEXT_ENABLED,
            )
        else:
            news_text, ordered_items = self._format_news_for_prompt(
                raw_news, max_items=LEGACY_PROMPT_MAX_ITEMS, gated=gated,
            )
        # A-2 (2026-10-11)：概念列表不再硬编码"科技成长"，改为从 CONCEPT_UNIVERSE
        # 子类标签派生（含周期/资源/消费/机械/医药扩展），使事件提取的
        # affected_sectors 不再只偏科技（glue 埋伏支路的上游事件供给）。
        concept_list_str = self._build_concept_list_str()

        # 加载历史事件用于 LLM prompt 注入（跨日记忆）
        historical_events = self._load_historical_events_for_prompt(days=7)
        historical_context = self._format_historical_context_for_prompt(historical_events)

        # 事件提取（注入历史上下文）
        # 素材为空 = 粗筛无选中（LLM 失效或当日无值得分析条目）→ 直接跳过提取调用，
        # 既不烧钱也不给「空新闻列表」留幻觉空间；概念/风险仍用全量文本独立产出。
        events_result = ""
        if news_text:
            events_result = await asyncio.to_thread(
                self._call_llm,
                EVENT_EXTRACTION_PROMPT.format(
                    concept_list=concept_list_str,
                    news_data=news_text,
                    historical_context=historical_context,
                    src_index_req=(
                        "\n   - source_index: 该事件**出处**对应的新闻编号（新闻数据前 [#n] 的 n，1-based）；"
                        "取最核心来源编号，无法定位则省略（系统按此编号回填真实 url/发布时间，勿自行生成 url）"
                    ) if gated else "",
                    src_index_example=", \"source_index\": 3" if gated else "",
                ),
                temperature=EVENT_EXTRACTION_TEMPERATURE,
                max_tokens=EVENT_EXTRACTION_MAX_TOKENS,
                task_id="node4.event_extraction",
            )
        else:
            logger.warning("[Node 4] 素材为空（粗筛无选中）→ 跳过事件提取，当日不产事件")
        events = self._parse_events(events_result, ordered_items)

        # 概念发现 / 风险总结（2026-10-30 运行时收敛）：独立 LLM 调用已删除。
        # concepts 由 Node 6 A6 共享语义产物回填；risks 先用负面事件规则兜底，
        # A6 成功后同样回填（不伪造 LLM 输出）。
        concepts: List[Dict] = []
        risks = self._parse_risks("", events)

        return events, concepts, risks

    # 保底配额：确保论坛帖（NGA）与情报（Intelligence）进入 LLM 输入，不被权重更高的
    # 专业媒体完全淹没。§6 Intelligence 条目追加在合并列表尾部，若不保底会被 top-N
    # 截断而永远进不了 prompt（C4：防截断）。
    _NGA_MIN_FOR_LLM = 5
    _INTEL_MIN_FOR_LLM = 5
    # 保底保护的来源类型：替换末尾条目时跳过这些，避免保底类型互相挤占
    _RESERVED_SOURCE_TYPES = ("nga_forum", "intelligence")

    @staticmethod
    async def _enrich_fulltext(items: List[Dict]) -> List[Dict]:
        """短名单正文补全（P0-2，`GOT_NEWS_FULLTEXT_ENABLED` 门控）。

        只对 **url 非空且 snippet < 200 字** 的条目抓单页正文（CLS 快讯 url 恒空但
        snippet 已是全文，跳过）。抓取失败/正文为空 → 保持原 snippet；成功时原 snippet
        留存在 `snippet_raw`（影子对照用）。并发 5，单页超时 10s，最坏 ~20s。
        """
        from src.got.news_fulltext import fetch_article_text

        targets = [
            it for it in items
            if (it.get("url") or "").strip() and len((it.get("snippet") or "").strip()) < 200
        ]
        if not targets:
            return items

        sem = asyncio.Semaphore(5)

        async def _fill(item: Dict) -> None:
            async with sem:
                text = await asyncio.to_thread(fetch_article_text, item["url"])
            if text:
                item["snippet_raw"] = item.get("snippet", "")
                item["snippet"] = text

        await asyncio.gather(*(_fill(it) for it in targets))
        filled = sum(1 for it in targets if it.get("snippet_raw"))
        logger.info(
            "[Node 4] 正文补全: %d/%d 条目标补齐（短名单共 %d 条）",
            filled, len(targets), len(items),
        )
        return items

    @staticmethod
    def _format_news_for_prompt(news_items: List[Dict], max_items: Optional[int] = None, *, gated: bool = False,
                                fulltext: bool = False) -> Tuple[str, List[Dict]]:
        """将新闻列表格式化为 LLM prompt 文本（含来源类型标注 + NGA/Intelligence 保底）。

        返回 (文本, 有序条目列表)：有序列表与文本中序号 1..N 严格对齐，供 `_parse_events`
        按 LLM 输出的 source_index **确定性回连**真实 url/发布时间（防幻觉，禁 LLM 造 URL）。
        max_items=None（缺省）：**不截断**，全量入 prompt（两段式的阶段 2 与粗筛后的全量
        辅助调用都走这条）；只保留旧的 50 条截断作为回滚路径的显式传参
        （`LEGACY_PROMPT_MAX_ITEMS`）。
        gated=False（缺省）：素材行逐字同改造前，仅返回列表供回连（实际无 source_index 则回连 None）。
        fulltext=False（缺省）：snippet 截 150 字（逐字节同改造前）；True（`GOT_NEWS_FULLTEXT_ENABLED`）
        → 上限放到 `GOT_NEWS_FULLTEXT_MAX_CHARS` 且超长按头/中/尾采样。
        """
        top_items = list(news_items) if max_items is None else list(news_items[:max_items])

        def _ensure_min(source_type: str, min_count: int) -> None:
            """保底：确保 top_items 中至少有 min_count 条 source_type 条目。

            不足时从 max_items 之后的尾部补入，替换末尾优先级最低的**非保底**条目
            （跳过 nga_forum / intelligence，避免两类保底互相挤占）。
            """
            in_top = sum(1 for it in top_items if it.get("source_type") == source_type)
            if in_top >= min_count:
                return
            remaining = [
                it for it in news_items[max_items:]
                if it.get("source_type") == source_type
            ]
            needed = min_count - in_top
            for cand in remaining[:needed]:
                for j in range(len(top_items) - 1, -1, -1):
                    if top_items[j].get("source_type") not in NewsAnalysisNode._RESERVED_SOURCE_TYPES:
                        top_items[j] = cand
                        break

        # 全量入 prompt 时无需保底（不存在截断线，尾部条目本就在池内）
        if max_items is not None:
            _ensure_min("nga_forum", NewsAnalysisNode._NGA_MIN_FOR_LLM)
            _ensure_min("intelligence", NewsAnalysisNode._INTEL_MIN_FOR_LLM)

        lines = []
        for i, item in enumerate(top_items, 1):
            source = item.get("source", "未知")
            source_type = item.get("source_type", "")
            title = item.get("title", "")
            raw_snippet = item.get("snippet", "") or ""
            if fulltext:
                snippet = _sample_long_text(raw_snippet, GOT_NEWS_FULLTEXT_MAX_CHARS)
            else:
                snippet = raw_snippet[:150]
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

            pub_date = item.get("published_date", "")
            # 补读日龄前缀 + 产业召回前缀（只标注来源与日龄，不改排序/影响判断）
            prefix = _carry_age_prefix(item)
            if item.get("recalled_industry"):
                prefix += "[产业] "
            if gated:
                # A2 素材行前缀：[#idx | 发布时间]——供 LLM 引用 source_index + 时效判断（url 不入 prompt，回连走代码侧）
                head = f"{i}. [#{i}"
                if pub_date:
                    head += f" | {pub_date}"
                head += f"|{tag}] {prefix}{title}\n   {snippet}"
                lines.append(head)
            else:
                # 改造前原行格式（零决策面变更）
                lines.append(f"{i}. [{tag}] {prefix}{title}\n   {snippet}")
        return "\n".join(lines), top_items

    

    def _call_llm(
        self,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        max_retries: int = 3,
        task_id: str = "node4.llm",
    ) -> str:
        """同步 LLM 调用（worker 线程内执行），统一走 run 级 runtime。"""
        from src.got.agent_runtime import ensure_runtime

        runtime = ensure_runtime(self, getattr(self, "_as_of", None))
        step = runtime.call_text_sync(
            task_id=task_id,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            timeout=60.0,
        )
        if step.status == "ok" and isinstance(step.output, str):
            return step.output.strip()
        if step.status == "failed":
            logger.warning("[Node 4] LLM 调用未成功（%s）: %s", task_id, step.error)
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

    # 埋伏通道催化剂权重映射（2026-10-22）：impact_level 为基数，负面事件减半。
    # 非概率：仅用于 ambush 准入过滤与 B 公式排序（替代 CalendarEvent.confidence 语义）。
    _NODE4_IMPACT_WEIGHT = {"high": 0.8, "medium": 0.6, "low": 0.4}

    @classmethod
    def _derive_catalyst_weight(cls, impact_level: str, sentiment: str) -> float:
        """impact_level×sentiment → ambush 专用权重（0~1）。非法输入按 medium/neutral 处理。"""
        base = cls._NODE4_IMPACT_WEIGHT.get(str(impact_level or "").lower(), 0.6)
        if str(sentiment or "").lower() == "negative":
            base *= 0.5
        return round(base, 3)

    @staticmethod
    def _parse_onset_days(raw: Any) -> Optional[int]:
        """LLM onset_days → Optional[int]（1~10）；null/越界/非数值 → None（未知）。"""
        if raw is None or isinstance(raw, bool):
            return None
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return None
        if not (1 <= days <= 10):
            return None
        return days

    @staticmethod
    def _parse_events(result, ordered_items: Optional[List[Dict]] = None) -> List[NewsEvent]:
        """解析事件提取 LLM 输出。

        A2 确定性回连：按 LLM 输出的 source_index（1-based）从 ordered_items 取**真实**
        url/发布时间回填到 NewsEvent.source_url/timestamp；越界/缺失→留空（不伪造）。
        LLM 不输出 url，防幻觉。

        2026-10-22：同时填 `catalyst_weight`（impact×sentiment 确定性映射）与
        `catalyst_onset_days`（LLM 自评滞后期，可空），供 Node 6 埋伏通道消费。
        """
        if isinstance(result, Exception) or not result:
            return []

        data = NewsAnalysisNode._extract_json(result)
        if not data or "events" not in data:
            logger.warning("[Node 4] 事件提取 JSON 解析失败，原始输出前200字: %s", result[:200])
            return []

        events = []
        for item in data["events"]:
            try:
                # 按 source_index 确定性回连真实出处 url/发布时间（越界或缺失→None）
                src_url = None
                src_time = None
                idx = item.get("source_index")
                if ordered_items and isinstance(idx, int) and 1 <= idx <= len(ordered_items):
                    ref = ordered_items[idx - 1] or {}
                    src_url = (ref.get("url") or "").strip() or None
                    src_time = (ref.get("published_date") or "").strip() or None
                impact_level = item.get("impact_level", "medium")
                sentiment = item.get("sentiment", "neutral")
                events.append(NewsEvent(
                    event_type=item.get("event_type", "industry"),
                    title=item.get("title", ""),
                    summary=item.get("summary", ""),
                    impact_level=impact_level,
                    sentiment=sentiment,
                    affected_sectors=item.get("affected_sectors", []),
                    affected_stocks=item.get("affected_stocks", []),
                    source_type=item.get("source_type", "professional"),
                    source_count=1,
                    source_url=src_url,
                    timestamp=src_time,
                    catalyst_weight=NewsAnalysisNode._derive_catalyst_weight(impact_level, sentiment),
                    catalyst_onset_days=NewsAnalysisNode._parse_onset_days(item.get("onset_days")),
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
    # Step 3.1: low 事件板块级救援（Q1，2026-10-16 RCA，用户裁决直连）
    # =================================================================

    async def _rescue_low_events(self, events: List[NewsEvent]) -> List[NewsEvent]:
        """复审「全局 low 但板块 high」事件：affected_sectors 非空且 sentiment != "negative"
        的 low 事件送 LLM 三维判定（冲击共性/板块占比/增量信息，文献接地见
        docs/got/verification.md 补登 #6），sector_impact=high 的事件原地升级 medium
        直连进链路（DB/NewsContext/impact_score/日历候选池自然吃到），终结无痕蒸发。

        sector_impact 全量留痕 shadow_low_events.json（质量观测+回滚审计）。
        降级语义：LLM 失败/解析失败 → 异常上抛由 run() 回退原过滤；开关关 → 不调 LLM
        落 disabled 占位；落盘失败 → 仍返回升级结果（留痕失败≠救援失败）。
        """
        day_dir = Path("data/got_daily") / date.today().strftime("%Y%m%d")
        low_all = [e for e in events if e.impact_level == "low"]

        # 开关关：disabled 占位 JSON（行为开关关=显式留痕，非「缺开关=缺文件」惯例）
        if not GOT_LOW_SECTOR_RESCUE:
            self._write_low_rescue_shadow(day_dir, {
                "date": date.today().strftime("%Y%m%d"),
                "switch": False,
                "status": "disabled",
                "low_count": len(low_all),
                "review_candidates": 0,
                "upgraded": 0,
                "sector_impact_dist": {},
                "events": [
                    {"title": e.title, "affected_sectors": list(e.affected_sectors),
                     "sentiment": e.sentiment, "sector_impact": None,
                     "reason": "开关关闭，未送审", "upgraded_to": None}
                    for e in low_all
                ],
                "note": "GOT_LOW_SECTOR_RESCUE=0 已回滚原过滤行为",
            })
            return events

        # D2: 复审候选 = 板块映射非空 且 非 negative
        candidates = [
            e for e in low_all
            if e.affected_sectors and e.sentiment != "negative"
        ]

        if not candidates:
            self._write_low_rescue_shadow(day_dir, {
                "date": date.today().strftime("%Y%m%d"),
                "switch": True,
                "low_count": len(low_all),
                "review_candidates": 0,
                "upgraded": 0,
                "sector_impact_dist": {},
                "events": [
                    {"title": e.title, "affected_sectors": list(e.affected_sectors),
                     "sentiment": e.sentiment, "sector_impact": None,
                     "reason": "无板块映射或负面事件，未送审", "upgraded_to": None}
                    for e in low_all
                ],
                "note": "无复审候选（有效样本：0 命中≠0 数据）",
            })
            return events

        events_json = json.dumps(
            [
                {"index": i, "title": e.title, "summary": e.summary,
                 "affected_sectors": list(e.affected_sectors), "sentiment": e.sentiment}
                for i, e in enumerate(candidates)
            ],
            ensure_ascii=False,
        )
        # 复用 Node 4 自有 _call_llm（T2 critic 串行形态）；解析不走 _extract_json
        # （其 dict-only 截断修复对数组协议形状错配——会把截断数组静默解析成首事件对象）
        try:
            raw = await asyncio.to_thread(
                self._call_llm,
                LOW_RESCUE_PROMPT.format(
                    concept_list=self._build_concept_list_str(),
                    events_json=events_json,
                ),
                temperature=LOW_RESCUE_TEMPERATURE,
                max_tokens=LOW_RESCUE_MAX_TOKENS,
                task_id="node4.low_rescue",
            )
            verdicts = self._parse_low_rescue_verdicts(raw)
        except Exception as exc:  # noqa: BLE001 — LLM 失败/解析失败：降级不升级，仍落影子（T4/T5 契约）
            logger.warning("[Node 4] low 救援 LLM/解析失败（降级为原过滤行为）: %s", exc)
            self._write_low_rescue_shadow(day_dir, {
                "date": date.today().strftime("%Y%m%d"),
                "switch": True,
                "low_count": len(low_all),
                "review_candidates": len(candidates),
                "upgraded": 0,
                "sector_impact_dist": {},
                "events": [
                    {"title": e.title, "affected_sectors": list(e.affected_sectors),
                     "sentiment": e.sentiment, "sector_impact": None,
                     "reason": "LLM/解析失败，未获判定", "upgraded_to": None}
                    for e in low_all
                ],
                "failure": f"{type(exc).__name__}: {exc}",
                "note": "LLM 复审失败降级：不升级=回滚语义；失败留痕供质量观测",
            })
            return events
        by_index = {
            v["index"]: v for v in verdicts
            if isinstance(v.get("index"), int)
            and 0 <= v["index"] < len(candidates)
            and v.get("sector_impact") in ("high", "medium", "low")
        }

        upgraded = 0
        verdict_by_candidate: Dict[int, Dict[str, Any]] = {}
        for i, e in enumerate(candidates):
            v = by_index.get(i)
            if v is None:
                continue  # LLM 漏判/非法条目：不升级不臆测
            was_upgraded = False
            if v["sector_impact"] == "high" and e.impact_level == "low":
                e.impact_level = "medium"  # 原地升级，全字段保留（source_url/timestamp/affected_stocks 不丢）
                was_upgraded = True
                upgraded += 1
            verdict_by_candidate[id(e)] = {**v, "upgraded_to": "medium" if was_upgraded else None}

        # 全量留痕：low 事件逐条（含未送审），sector_impact_dist 三态分布
        candidate_ids = {id(e) for e in candidates}
        events_out = []
        for e in low_all:
            if id(e) in candidate_ids and id(e) in verdict_by_candidate:
                v = verdict_by_candidate[id(e)]
                events_out.append({
                    "title": e.title,
                    "affected_sectors": list(e.affected_sectors),
                    "sentiment": e.sentiment,
                    "sector_impact": v["sector_impact"],
                    "reason": v.get("reason"),
                    "upgraded_to": v.get("upgraded_to"),
                })
            else:
                events_out.append({
                    "title": e.title,
                    "affected_sectors": list(e.affected_sectors),
                    "sentiment": e.sentiment,
                    "sector_impact": None,
                    "reason": "无板块映射或负面事件，未送审" if id(e) not in candidate_ids
                              else "LLM 未返回该条目判定",
                    "upgraded_to": None,
                })
        dist = {"high": 0, "medium": 0, "low": 0}
        for v in verdict_by_candidate.values():
            dist[v["sector_impact"]] += 1
        self._write_low_rescue_shadow(day_dir, {
            "date": date.today().strftime("%Y%m%d"),
            "switch": True,
            "low_count": len(low_all),
            "review_candidates": len(candidates),
            "upgraded": upgraded,
            "sector_impact_dist": dist,
            "events": events_out,
            "note": "Q1 low 事件板块级救援（直连，用户裁决 2026-10-16）；"
                    "sector_impact 全量留痕（三维判定·文献接地），≥10 交易日质量观测",
        })
        return events

    @staticmethod
    def _parse_low_rescue_verdicts(raw: str) -> List[Dict[str, Any]]:
        """解析救援复审输出（T2 critic 内联惯例：剥 ``` 围栏 → json.loads → list 守卫）。

        数组协议不走 _extract_json：其 dict-only 截断修复会把截断数组静默解析成
        首事件对象（形状错配非报错）。失败 raise，由 run() 降级为原过滤行为。

        单对象归一（2026-10-29/30 实锤）：只有 1 条送审时模型常返回裸对象
        ``{"index": 0, ...}``，旧逻辑按「非数组」整批丢弃（归档留痕为
        「LLM/解析失败，未获判定」）。对象按 ``[obj]`` 归一，标量仍 raise。
        """
        if not raw or not raw.strip():
            raise ValueError("low 救援复审 LLM 空响应")
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        items = json.loads(cleaned)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise ValueError(f"low 救援复审输出非数组: {type(items).__name__}")
        return [v for v in items if isinstance(v, dict)]

    @staticmethod
    def _write_low_rescue_shadow(day_dir: Path, record: Dict[str, Any]) -> None:
        """落 shadow_low_events.json（tmp+replace 原子写，jump/antibias 同款惯例）。"""
        try:
            day_dir.mkdir(parents=True, exist_ok=True)
            out = day_dir / "shadow_low_events.json"
            tmp = out.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(out)
            logger.info(
                "[Node 4] low 救援影子落盘: low=%d candidates=%d upgraded=%d",
                record.get("low_count", 0),
                record.get("review_candidates", 0),
                record.get("upgraded", 0),
            )
        except OSError as exc:
            # 留痕失败≠救援失败：升级结果照常返回，只告警
            logger.warning("[Node 4] shadow_low_events.json 落盘失败（已忽略）: %s", exc)

    # =================================================================
    # Step 3.5: 事件持久化（跨日记忆）
    # =================================================================

    def _save_events_to_db(self, events: List[NewsEvent], raw_news: List[Dict], *, as_of: Optional[date] = None) -> None:
        """保存事件到 SQLite，支持跨日累积。

        去重策略：标题相似度 ≥0.6 视为同一事件，复用 _deduplicate_news() 逻辑。
        同一事件多次出现时：mention_count += 1，更新 last_seen，追加 sources_seen。
        as_of：业务日（回放/补跑不读墙钟日）；命中历史相似事件时复用其 event_id/first_seen。
        """
        if not events:
            return

        try:
            db = DatabaseManager()
            today = as_of or date.today()

            with db.get_session() as session:
                # 加载最近 7 天的历史事件用于去重（以业务日为上界）
                historical_events = self._load_historical_events_from_db(session, days=7, as_of=today)

                for event in events:
                    # 在历史事件中查找相似事件
                    matched_event_id = None
                    db_event = None
                    for hist_event in historical_events:
                        similarity = SequenceMatcher(None, event.title, hist_event.title).ratio()
                        if similarity >= 0.6:
                            matched_event_id = hist_event.event_id
                            db_event = hist_event
                            break

                    if matched_event_id:
                        # 复用历史身份：本次运行日不得重写 event_id/first_seen
                        event.event_id = matched_event_id
                        if getattr(db_event, "first_seen", None):
                            event.first_seen = str(db_event.first_seen)
                        # A-P0-7: mention_count 改为跨日才增（按日去重）
                        last_seen_date = db_event.last_seen
                        if isinstance(last_seen_date, datetime):
                            last_seen_date = last_seen_date.date()
                        elif not isinstance(last_seen_date, date):
                            last_seen_date = today
                        if last_seen_date < today:
                            db_event.mention_count = (db_event.mention_count or 0) + 1
                        # else: 同日重复只刷新 last_seen/来源/轨迹，不增 mention_count

                        # 影子字段：每次运行+1（旧口径，用于对比）
                        db_event.mention_count_run_based = (db_event.mention_count_run_based or 0) + 1

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
                        # 创建新事件（稳定 ID 已在 Step 3.0 生成，缺失时按业务日补）
                        event_id = event.event_id or self._generate_event_id(
                            event.title, event.source_type or "unknown", today,
                        )
                        event.event_id = event_id
                        event.first_seen = event.first_seen or today.isoformat()
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
        """标题归一化：去标点/统一小写/压缩空白（实现见 news_retrieval，全仓唯一口径）。"""
        return normalize_title(title)

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        """标题相似度（Jaccard on character bigrams）。0~1。"""
        return title_similarity(a, b)

    @staticmethod
    def _attention_meta(item: Dict) -> Dict[str, Any]:
        """采集条目的热榜元数据（P0-1）：单值 platform + 合并 platforms 集 + rank/heat 规整。

        `platforms` 是 `_deduplicate` 合并同题多平台后的集合（微博/百度/知乎同题只留一条
        新闻，但平台扩散要可观测）；单值 `platform` 保留首个平台，`platforms` 记录全集。
        """
        platform = item.get("platform") or None
        platforms = {p for p in (item.get("platforms") or []) if p}
        if platform:
            platforms.add(platform)
        rank = item.get("rank")
        rank = int(rank) if isinstance(rank, (int, float)) and rank > 0 else None
        heat = item.get("heat")
        heat = float(heat) if isinstance(heat, (int, float)) and heat else None
        return {
            "platform": platform,
            "platforms": ",".join(sorted(platforms))[:200] or None,
            "rank": rank,
            "heat": heat,
        }

    @classmethod
    def _merge_same_title_rows(cls, raw_news: List[Dict]) -> List[Dict]:
        """本次入参按 `_normalize_title()` 同题归并后再落库（跨源转载不重复写行）。

        主行=首个条目（保留其 source/url/发布时间，URL 指纹与 title 精确匹配都用主行）；
        其余行的热榜元数据（platform/platforms/rank/heat）并入主行。`source_count`
        不参与（平台扩散 ≠ 独立媒体确认，口径由 `_count_cross_source` 独有）。
        只作用于写入路径，不改调用方对象、不改历史行。
        """
        merged: Dict[str, Dict] = {}
        order: List[str] = []
        for item in raw_news:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            norm = cls._normalize_title(title)
            if not norm:
                continue
            main = merged.get(norm)
            if main is None:
                copy = dict(item)
                platforms = {p for p in (copy.get("platforms") or []) if p}
                if copy.get("platform"):
                    platforms.add(copy["platform"])
                if platforms:
                    copy["platforms"] = sorted(platforms)
                merged[norm] = copy
                order.append(norm)
                continue

            platforms = {p for p in (main.get("platforms") or []) if p}
            platforms |= {p for p in (item.get("platforms") or []) if p}
            if item.get("platform"):
                platforms.add(item["platform"])
            if platforms:
                main["platforms"] = sorted(platforms)
            if not main.get("platform") and item.get("platform"):
                main["platform"] = item["platform"]
            ranks = [
                r for r in (main.get("rank"), item.get("rank"))
                if isinstance(r, int) and not isinstance(r, bool) and r > 0
            ]
            if ranks:
                main["rank"] = min(ranks)  # 数值更小 = 榜单更靠前
            heats = [
                h for h in (main.get("heat"), item.get("heat"))
                if isinstance(h, (int, float)) and not isinstance(h, bool) and h
            ]
            if heats:
                main["heat"] = max(heats)
        return [merged[norm] for norm in order]

    def _persist_raw_news(self, raw_news: List[Dict], *, as_of: Optional[date] = None) -> Tuple[int, int]:
        """原始新闻持久化（P1-1）：同题归并 + 精确匹配 + 相似度兜底去重。

        每次 `_fetch_news()` 后调用，将全量原始数据写入 node4_raw_news 表。
        同一新闻跨日重复出现时：appearance_count += 1, last_seen_date 更新。
        本次入参先按 `_normalize_title()` 同题归并（`_merge_same_title_rows`）：
        `intelligence_newsnow-*` 与热榜源的同一新闻只落一行，URL/title 匹配都用主行。

        Returns:
            (persisted, updated)：本次新增行数 / 命中去重后刷新的行数；
            空输入或持久化异常 → (0, 0)（异常已被内部吞掉，不阻断主流程）。
        """
        if not raw_news:
            return 0, 0
        raw_news = self._merge_same_title_rows(raw_news)

        today = as_of or date.today()
        persisted = 0
        updated = 0
        try:
            db = DatabaseManager()
            with db.get_session() as session:
                for item in raw_news:
                    title = item.get("title", "").strip()
                    source = item.get("source", "unknown")
                    if not title:
                        continue
                    url = (item.get("url") or "").strip() or None
                    published_at = (item.get("published_date") or "").strip() or None
                    url_canonical = canonicalize_url(url or "") or None
                    meta = self._attention_meta(item)

                    title_norm = self._normalize_title(title)

                    # Step 0: URL 指纹匹配（同源同链接 → 同一条，换标题也算）
                    existing = None
                    if url_canonical:
                        existing = session.query(Node4RawNewsRecord).filter(
                            Node4RawNewsRecord.url_canonical == url_canonical,
                            Node4RawNewsRecord.source == source,
                        ).first()

                    # Step 1: 精确匹配（同标题 + 同来源）
                    if not existing:
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
                        # A-P0-7: appearance_count 改为跨日才增（按日去重）
                        last_seen_date = existing.last_seen_date
                        if isinstance(last_seen_date, datetime):
                            last_seen_date = last_seen_date.date()
                        elif not isinstance(last_seen_date, date):
                            last_seen_date = today
                        if last_seen_date < today:
                            existing.appearance_count = (existing.appearance_count or 0) + 1
                        # else: 同日重复只刷新 last_seen_date，不增 appearance_count

                        # 影子字段：每次运行+1（旧口径，用于对比）
                        existing.appearance_count_run_based = (existing.appearance_count_run_based or 0) + 1
                        # P0-1：快照/运行命中次数（小时快照 × 在榜时长 → 盘中热度轨迹，纯观测）
                        existing.snapshot_count = (existing.snapshot_count or 0) + 1

                        existing.last_seen_date = today
                        # A1 出处回填：旧行无 url/发布时间而本次采到则补齐（不覆盖已有值）
                        if url and not getattr(existing, "url", None):
                            existing.url = url
                        if published_at and not getattr(existing, "published_at", None):
                            existing.published_at = published_at
                        if url_canonical and not getattr(existing, "url_canonical", None):
                            existing.url_canonical = url_canonical
                        # P0-1 热榜元数据：platform 补空不覆盖、名次取更靠前、热度补空
                        if meta["platform"] and not getattr(existing, "platform", None):
                            existing.platform = meta["platform"]
                        if meta["platforms"]:
                            merged_platforms = {
                                p for p in (getattr(existing, "platforms", None) or "").split(",") if p
                            } | set(meta["platforms"].split(","))
                            existing.platforms = ",".join(sorted(merged_platforms))[:200]
                        if meta["rank"] and (
                            getattr(existing, "rank", None) is None
                            or meta["rank"] < existing.rank
                        ):
                            existing.rank = meta["rank"]
                        if meta["heat"] and not getattr(existing, "heat", None):
                            existing.heat = meta["heat"]
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
                            url=url,
                            published_at=published_at,
                            url_canonical=url_canonical,
                            platform=meta["platform"],
                            platforms=meta["platforms"],
                            rank=meta["rank"],
                            heat=meta["heat"],
                            snapshot_count=1,
                        ))
                        persisted += 1

                session.commit()
                if persisted or updated:
                    logger.info(
                        f"[Node 4] 原始新闻持久化: 新增={persisted}, 更新={updated}"
                    )
        except Exception as e:
            logger.warning(f"[Node 4] 原始新闻持久化失败: {e}")
            return 0, 0
        return persisted, updated

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

    def _load_historical_events_from_db(self, session, days: int = 7, as_of: Optional[date] = None) -> List[Node4EventRecord]:
        """从 SQLite 加载最近 N 天的事件（用于去重和 LLM prompt 注入）。"""
        cutoff_date = (as_of or date.today()) - timedelta(days=days)
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

    def _generate_event_id(self, title: str, source: str, as_of: date) -> str:
        """稳定事件 ID：normalize_title + 首次来源 + 业务日（跨日相似匹配合并后复用）。"""
        content = f"{self._normalize_title(title)}|{source or 'unknown'}|{as_of.isoformat()}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def _assign_stable_event_ids(self, events: List[NewsEvent], as_of: date) -> None:
        """为抽取完成的事件生成稳定身份（low rescue 前调用，救援不改身份）。"""
        for event in events:
            if getattr(event, "event_id", None):
                continue
            event.event_id = self._generate_event_id(
                event.title, event.source_type or "unknown", as_of,
            )

    @staticmethod
    def _build_concept_list_str() -> str:
        """A-2 (2026-10-11)：从 CONCEPT_UNIVERSE 的 _CATEGORY_GROUP 子类标签派生概念列表文案。

        旧硬编码"科技成长（AI、半导体、机器人、新能源）"使事件提取偏向科技，
        周期/消费/资源事件被 concept_set 闸门静默丢弃。本helper 延迟导入
        sector_analysis（news_analysis 顶层 import 它会形成循环依赖：
        sector_analysis 不 import news_analysis，故运行时才解析即安全）。
        失败时降级回旧硬编码文案，保证 Node 4 不因词表异常中断。
        """
        try:
            from src.got.nodes.sector_analysis import _CATEGORY_GROUP

            labels = sorted(set(_CATEGORY_GROUP.values()))
            if labels:
                return "全市场精选概念（" + "、".join(labels) + "）"
        except Exception as exc:
            logger.warning("[news_analysis] 概念列表派生失败，降级硬编码: %s", exc)
        return "科技成长（AI、半导体、机器人、新能源）"


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
