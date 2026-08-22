"""
催化剂日历构建器

板块级前瞻日历：财报/解禁/宏观（规则映射）+ 叙事型催化（LLM 增强）+ 全球→A股映射。
输出 CatalystCalendar，嵌入 NewsContext 供 Node 6 埋伏通道消费。

刷新节奏：每 5 交易日全量重建 + 每交易日增量更新。
"""

import asyncio
import hashlib
import json
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from src.got.schemas import CatalystEvent, CatalystCalendar, NewsEvent
from src.got.catalyst_config import (
    GLOBAL_TO_A_SHARE_MAP,
    CATALYST_WINDOW_DAYS,
    CATALYST_CONFIDENCE_GATE,
    CATALYST_FULL_REFRESH_INTERVAL,
    IMPACT_WEIGHT,
    MACRO_CALENDAR_RULES,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 工具函数
# =============================================================================

def _flatten_concept_universe() -> Set[str]:
    """展开 CONCEPT_UNIVERSE 为板块名集合（用于匹配过滤）"""
    from src.got.nodes.sector_analysis import CONCEPT_UNIVERSE
    names = set()
    for subcats in CONCEPT_UNIVERSE.values():
        for board_list in subcats.values():
            names.update(board_list)
    return names


def _make_event_id(source: str, event_type: str, sector: str, event_date: date) -> str:
    """确定性 event_id：同 source+type+sector+date → 同 ID → UPSERT 去重"""
    raw = f"{source}|{event_type}|{sector}|{event_date.isoformat()}"
    return hashlib.md5(raw.encode()).hexdigest()


def _trade_days_offset(base: date, days: int) -> date:
    """粗略交易日偏移（跳过周末，不处理节假日）"""
    result = base
    added = 0
    while added < days:
        result += timedelta(days=1)
        if result.weekday() < 5:
            added += 1
    return result


def _count_trade_days(start: date, end: date) -> int:
    """两日期间的交易日数（跳过周末）"""
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


class CatalystCalendarBuilder:
    """催化剂日历构建器

    使用方式：
        builder = CatalystCalendarBuilder()
        calendar = await builder.build_calendar(target_date=date.today())
    """

    def __init__(self):
        self._concept_set: Optional[Set[str]] = None
        self._stock_board_cache: Dict[str, List[str]] = {}
        self._llm = None

    @property
    def concept_set(self) -> Set[str]:
        if self._concept_set is None:
            self._concept_set = _flatten_concept_universe()
        return self._concept_set

    @property
    def llm(self):
        if self._llm is None:
            from src.agent.llm_adapter import LLMToolAdapter
            self._llm = LLMToolAdapter()
        return self._llm

    # =================================================================
    # 主入口
    # =================================================================

    async def build_calendar(self, target_date: Optional[date] = None) -> CatalystCalendar:
        """构建催化剂日历（全量或增量）"""
        target_date = target_date or date.today()
        end_date = target_date + timedelta(days=CATALYST_WINDOW_DAYS)

        try:
            full_refresh = self._should_full_refresh(target_date)
            mode = "full" if full_refresh else "incremental"

            if full_refresh:
                events = await self._full_build(target_date, end_date)
            else:
                events = await self._incremental_update(target_date, end_date)

            # 按日期排序
            events.sort(key=lambda e: e.event_date)

            # 持久化
            self._save_events(events, target_date)
            self._update_refresh_tracker(mode, target_date)

            # 统计
            trade_days = self._get_trade_days_since_refresh(target_date)

            calendar = CatalystCalendar(
                status="normal" if events else "degraded",
                target_date=target_date,
                window_days=CATALYST_WINDOW_DAYS,
                events=events,
                last_full_refresh=target_date if full_refresh else self._get_last_refresh(),
                refresh_mode=mode,
                total_trading_days_since_refresh=trade_days,
            )

            logger.info(
                "[CatalystCalendar] 构建完成: %d 事件, mode=%s, window=%s~%s",
                len(events), mode, target_date, end_date,
            )
            return calendar

        except Exception as exc:
            logger.error("[CatalystCalendar] 构建失败: %s", exc, exc_info=True)
            return CatalystCalendar(
                status="failed",
                target_date=target_date,
                window_days=CATALYST_WINDOW_DAYS,
            )

    # =================================================================
    # 刷新节奏
    # =================================================================

    def _should_full_refresh(self, target_date: date) -> bool:
        """距上次全量刷新 ≥ 5 交易日 → 全量"""
        last = self._get_last_refresh()
        if last is None:
            return True
        trade_days = _count_trade_days(last, target_date)
        return trade_days >= CATALYST_FULL_REFRESH_INTERVAL

    def _get_last_refresh(self) -> Optional[date]:
        from src.storage import DatabaseManager, CatalystRefreshTracker
        db = DatabaseManager()
        with db.get_session() as session:
            row = session.query(CatalystRefreshTracker).first()
            return row.last_full_refresh if row else None

    def _get_trade_days_since_refresh(self, target_date: date) -> int:
        last = self._get_last_refresh()
        if last is None:
            return 0
        return _count_trade_days(last, target_date)

    def _update_refresh_tracker(self, mode: str, target_date: date) -> None:
        from src.storage import DatabaseManager, CatalystRefreshTracker
        db = DatabaseManager()
        with db.get_session() as session:
            row = session.query(CatalystRefreshTracker).first()
            if row is None:
                row = CatalystRefreshTracker(
                    id=1,
                    last_full_refresh=target_date,
                    trading_days_since_refresh=0,
                )
                session.add(row)
            else:
                if mode == "full":
                    row.last_full_refresh = target_date
                    row.trading_days_since_refresh = 0
                else:
                    row.trading_days_since_refresh += 1
            session.commit()

    # =================================================================
    # 全量构建
    # =================================================================

    async def _full_build(self, start: date, end: date) -> List[CatalystEvent]:
        """全量构建：结构化(财报+解禁+宏观) + 全球映射 + LLM 叙事"""
        all_events: List[CatalystEvent] = []

        # ── 结构化数据（并发获取） ──
        structured = await asyncio.gather(
            self._fetch_earnings_calendar(start, end),
            self._fetch_lockup_calendar(start, end),
            return_exceptions=True,
        )

        earnings_raw = structured[0] if not isinstance(structured[0], Exception) else []
        lockup_raw = structured[1] if not isinstance(structured[1], Exception) else []

        if isinstance(structured[0], Exception):
            logger.warning("[CatalystCalendar] 财报日历获取失败: %s", structured[0])
        if isinstance(structured[1], Exception):
            logger.warning("[CatalystCalendar] 解禁日历获取失败: %s", structured[1])

        # 宏观日历（纯规则，无 IO）
        macro_raw = self._generate_macro_calendar(start, end)

        # ── 批量映射：stock → sectors（并发，一次完成） ──
        all_stocks = earnings_raw + lockup_raw
        stock_sector_map = await self._batch_map_stocks(all_stocks, max_concurrent=8)

        for raw in earnings_raw:
            mapped = self._map_structured_event_fast(raw, "earnings", stock_sector_map)
            all_events.extend(mapped)

        for raw in lockup_raw:
            mapped = self._map_structured_event_fast(raw, "lockup", stock_sector_map)
            all_events.extend(mapped)

        # 聚合同板块同日期的多只股票事件（合并标题）
        all_events = self._aggregate_structured_events(all_events)

        # 宏观事件直接生成（板块已在规则中指定）
        for raw in macro_raw:
            for sector in raw.get("affected_sectors", []):
                if sector in self.concept_set:
                    all_events.append(CatalystEvent(
                        event_id=_make_event_id("macro_rule", "macro", sector, raw["event_date"]),
                        event_date=raw["event_date"],
                        event_type="macro",
                        title=raw["title"],
                        sector=sector,
                        impact_level=raw.get("impact_level", 2),
                        direction=raw.get("direction", "binary"),
                        confidence=1.0,
                        consensus_expectation="宏观经济数据发布，影响待确认",
                        source="macro_rule",
                        category_tag=raw.get("category_tag", "binary"),
                    ))

        # ── 全球映射 + LLM 叙事（消费 Node 4 历史事件） ──
        news_events = self._load_recent_news_events(days=7)

        global_events = self._map_global_events(news_events, start, end)
        all_events.extend(global_events)

        narrative = await self._llm_extract_narrative_catalysts(news_events, start, end)
        all_events.extend(narrative)

        # 去重：仅对叙事/全球事件（同 sector+date 保留最高 impact）
        # 结构化/宏观事件已在聚合阶段处理，不参与全局去重
        structured_macro = [e for e in all_events if e.source in ("macro_rule",) or e.source.startswith("akshare_")]
        dedup_pool = [e for e in all_events if e.source in ("llm_narrative", "global_map")]
        dedup_pool = self._deduplicate(dedup_pool)
        all_events = structured_macro + dedup_pool

        logger.info(
            "[CatalystCalendar] 全量构建: earnings=%d lockup=%d macro=%d global=%d narrative=%d → 去重后 %d",
            len(earnings_raw), len(lockup_raw), len(macro_raw),
            len(global_events), len(narrative), len(all_events),
        )
        return all_events

    # =================================================================
    # 增量更新
    # =================================================================

    async def _incremental_update(self, start: date, end: date) -> List[CatalystEvent]:
        """增量：加载 DB 有效事件 + 新增（全球映射 + LLM 叙事）"""
        # 加载 DB 中仍有效的旧事件
        existing = self._load_active_events(start)
        existing_ids = {e.event_id for e in existing}

        new_events: List[CatalystEvent] = []

        # 全球映射增量（近 1 天新闻）
        news_events = self._load_recent_news_events(days=1)
        global_events = self._map_global_events(news_events, start, end)
        for e in global_events:
            if e.event_id not in existing_ids:
                new_events.append(e)

        # LLM 叙事增量
        narrative = await self._llm_extract_narrative_catalysts(news_events, start, end)
        for e in narrative:
            if e.event_id not in existing_ids:
                new_events.append(e)

        # 合并
        all_events = existing + new_events
        all_events = self._deduplicate(all_events)

        logger.info(
            "[CatalystCalendar] 增量更新: existing=%d, new=%d → 合计 %d",
            len(existing), len(new_events), len(all_events),
        )
        return all_events

    # =================================================================
    # 结构化数据获取
    # =================================================================

    async def _fetch_earnings_calendar(self, start: date, end: date) -> List[Dict]:
        """获取财报披露日历

        优先 stock_yjyg_em（业绩预告，含披露日期），降级 stock_report_disclosure。
        过滤 [start, end] 窗口内的记录。
        """
        def _fetch() -> List[Dict]:
            import akshare as ak
            import pandas as pd

            # 尝试 stock_yjyg_em — 需要指定季度
            results = []
            for quarter in self._get_relevant_quarters(start, end):
                try:
                    df = ak.stock_yjyg_em(date=quarter)
                    if df is None or df.empty:
                        continue

                    # 最后一列是披露日期
                    date_col = df.columns[-1]
                    code_col = None
                    name_col = None
                    for col in df.columns:
                        col_str = str(col)
                        if "代码" in col_str or "code" in col_str.lower():
                            code_col = col
                        elif "简称" in col_str or "名称" in col_str or "name" in col_str.lower():
                            name_col = col

                    # 解析日期并过滤
                    df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
                    window = df[(df[date_col] >= str(start)) & (df[date_col] <= str(end))]

                    for _, row in window.iterrows():
                        try:
                            ed = row[date_col].date() if pd.notna(row[date_col]) else None
                            if ed is None:
                                continue
                            results.append({
                                "stock_code": str(row.get(code_col, "")) if code_col else "",
                                "stock_name": str(row.get(name_col, "")) if name_col else "",
                                "event_date": ed,
                                "report_type": "earnings",
                            })
                        except (ValueError, TypeError, AttributeError):
                            continue
                except Exception as exc:
                    logger.debug("[CatalystCalendar] stock_yjyg_em(%s) 失败: %s", quarter, exc)

            # 降级：stock_report_disclosure
            if not results:
                try:
                    df = ak.stock_report_disclosure()
                    if df is not None and not df.empty:
                        # 日期列：尝试多个候选
                        date_col = None
                        for col in df.columns:
                            col_str = str(col)
                            if "披露" in col_str or "首次" in col_str:
                                date_col = col
                                break

                        if date_col:
                            code_col = df.columns[0] if len(df.columns) > 0 else None
                            name_col = df.columns[1] if len(df.columns) > 1 else None

                            for _, row in df.iterrows():
                                try:
                                    raw_date = str(row[date_col])[:10]
                                    ed = date.fromisoformat(raw_date)
                                    if start <= ed <= end:
                                        results.append({
                                            "stock_code": str(row.get(code_col, "")) if code_col else "",
                                            "stock_name": str(row.get(name_col, "")) if name_col else "",
                                            "event_date": ed,
                                            "report_type": "earnings",
                                        })
                                except (ValueError, TypeError):
                                    continue
                except Exception as exc:
                    logger.debug("[CatalystCalendar] stock_report_disclosure 降级失败: %s", exc)

            # 去重：同 stock_code+event_date 只保留一条
            seen = set()
            deduped = []
            for r in results:
                key = (r.get("stock_code", ""), r.get("event_date"))
                if key not in seen:
                    seen.add(key)
                    deduped.append(r)

            return deduped

        return await asyncio.to_thread(_fetch)

    @staticmethod
    def _get_relevant_quarters(start: date, end: date) -> List[str]:
        """根据窗口日期范围，生成需要查询的季度列表"""
        quarters = set()
        current = start
        while current <= end:
            year = current.year
            month = current.month
            if month <= 3:
                quarters.add(f"{year}0331")
                quarters.add(f"{year}0331")  # Q4 年报
            elif month <= 6:
                quarters.add(f"{year}0331")  # Q4 年报（4月补披）
                quarters.add(f"{year}0630")  # Q1 季报
            elif month <= 9:
                quarters.add(f"{year}0630")  # Q2 中报
            else:
                quarters.add(f"{year}0930")  # Q3 季报
            # 跨年
            if month <= 4:
                quarters.add(f"{year - 1}0930")
                quarters.add(f"{year - 1}1231")
            current += timedelta(days=90)
        return sorted(quarters)

    async def _fetch_lockup_calendar(self, start: date, end: date) -> List[Dict]:
        """获取限售解禁日历

        akshare stock_restricted_release_detail_em(start_date, end_date)
        """
        def _fetch():
            import akshare as ak
            try:
                df = ak.stock_restricted_release_detail_em(
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
            except Exception as exc:
                logger.warning("[CatalystCalendar] 限售解禁 API 失败: %s", exc)
                return []

            if df is None or df.empty:
                return []

            # 列定位：按位置（列名编码不稳定）
            # 预期结构：序号, 股票代码, 股票简称, 解禁时间, ...
            code_col = df.columns[1] if len(df.columns) > 1 else None
            name_col = df.columns[2] if len(df.columns) > 2 else None
            date_col = df.columns[3] if len(df.columns) > 3 else None

            results = []
            for _, row in df.iterrows():
                try:
                    raw_date = str(row[date_col])[:10] if date_col else ""
                    ed = date.fromisoformat(raw_date)
                    results.append({
                        "stock_code": str(row[code_col]) if code_col else "",
                        "stock_name": str(row[name_col]) if name_col else "",
                        "event_date": ed,
                        "report_type": "lockup",
                    })
                except (ValueError, TypeError):
                    continue

            return results

        return await asyncio.to_thread(_fetch)

    def _generate_macro_calendar(self, start: date, end: date) -> List[Dict]:
        """宏观日历：规则内置（PMI/CPI/社融/LPR）"""
        results = []

        # 遍历窗口内的每个月
        current = start
        while current <= end:
            year, month = current.year, current.month

            for rule in MACRO_CALENDAR_RULES:
                event_date = self._resolve_macro_date(year, month, rule["day_rule"])
                if event_date and start <= event_date <= end:
                    results.append({
                        "event_date": event_date,
                        "title": rule["title"],
                        "impact_level": rule.get("impact_level", 2),
                        "affected_sectors": rule.get("affected_sectors", []),
                        "direction": rule.get("direction", "binary"),
                        "category_tag": rule.get("category_tag", "binary"),
                    })

            # 跳到下个月
            if month == 12:
                current = date(year + 1, 1, 1)
            else:
                current = date(year, month + 1, 1)

        return results

    @staticmethod
    def _resolve_macro_date(year: int, month: int, day_rule: str) -> Optional[date]:
        """解析宏观事件日期规则"""
        import calendar as cal_mod
        if day_rule == "month_end":
            last_day = cal_mod.monthrange(year, month)[1]
            return date(year, month, last_day)
        elif day_rule == "mid_month":
            return date(year, month, 10)
        elif day_rule == "mid_month_late":
            return date(year, month, 13)
        elif day_rule == "day_20":
            return date(year, month, 20)
        return None

    # =================================================================
    # 事件→板块映射
    # =================================================================

    async def _map_structured_event(
        self, raw: Dict, event_type: str
    ) -> List[CatalystEvent]:
        """stock_code → 所属板块列表 → 与 CONCEPT_UNIVERSE 交集（逐只，已废弃）"""
        stock_code = raw.get("stock_code", "")
        if not stock_code:
            return []
        sectors = await self._map_stock_to_sectors(stock_code)
        return self._build_structured_events(raw, event_type, sectors)

    def _map_structured_event_fast(
        self, raw: Dict, event_type: str, stock_sector_map: Dict[str, List[str]]
    ) -> List[CatalystEvent]:
        """使用预计算的批量映射生成事件"""
        stock_code = raw.get("stock_code", "")
        if not stock_code:
            return []
        clean = stock_code.replace("SH", "").replace("SZ", "").replace("BJ", "")
        sectors = stock_sector_map.get(clean, [])
        return self._build_structured_events(raw, event_type, sectors)

    def _build_structured_events(
        self, raw: Dict, event_type: str, sectors: List[str]
    ) -> List[CatalystEvent]:
        """根据已映射的板块列表生成 CatalystEvent"""
        if not sectors:
            return []

        event_date = raw["event_date"]
        stock_name = raw.get("stock_name", "")
        stock_code = raw.get("stock_code", "")
        source = f"akshare_{event_type}"
        title = (
            f"{stock_name}({stock_code}) {'财报披露' if event_type == 'earnings' else '限售解禁'}"
        )

        direction = "negative" if event_type == "lockup" else "drift"
        category = "fade" if event_type == "lockup" else "drift"
        impact = 2  # 个股级事件默认 ★★

        return [
            CatalystEvent(
                event_id=_make_event_id(source, event_type, sector, event_date),
                event_date=event_date,
                event_type=event_type,
                title=title,
                sector=sector,
                impact_level=impact,
                direction=direction,
                confidence=0.95,  # 结构化数据高置信
                consensus_expectation="财报/解禁为确定性事件，影响待市场消化",
                source=source,
                category_tag=category,
            )
            for sector in sectors
        ]

    async def _map_stock_to_sectors(self, stock_code: str) -> List[str]:
        """stock_code → 所属概念板块列表（只返回 CONCEPT_UNIVERSE 内的）

        性能优化：并发获取 + 缓存。
        """
        clean = stock_code.replace("SH", "").replace("SZ", "").replace("BJ", "")
        if not clean or len(clean) != 6:
            return []

        if clean in self._stock_board_cache:
            boards = self._stock_board_cache[clean]
        else:
            boards = await self._fetch_stock_boards(clean)
            self._stock_board_cache[clean] = boards

        matched = [b for b in boards if b in self.concept_set]
        return matched

    async def _batch_map_stocks(self, stocks: List[Dict], max_concurrent: int = 8) -> Dict[str, List[str]]:
        """批量映射多只股票→板块（并发控制 + 缓存复用）

        Args:
            stocks: [{"stock_code": "000001", ...}, ...]
            max_concurrent: 最大并发数

        Returns:
            {stock_code: [sector_name, ...]}
        """
        sem = asyncio.Semaphore(max_concurrent)
        mapping: Dict[str, List[str]] = {}

        async def _map_one(stock: Dict):
            code = stock.get("stock_code", "")
            if not code or code in mapping:
                return
            async with sem:
                sectors = await self._map_stock_to_sectors(code)
                mapping[code] = sectors

        await asyncio.gather(*[_map_one(s) for s in stocks])
        return mapping

    async def _fetch_stock_boards(self, stock_code: str) -> List[str]:
        """通过 DataFetcherManager 查询股票所属板块"""
        def _fetch():
            from data_provider.base import DataFetcherManager
            manager = DataFetcherManager()
            boards = manager.get_belong_boards(stock_code)
            return [
                b.get("name", "") or b.get("board_name", "")
                for b in (boards or [])
            ]

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            logger.debug("[CatalystCalendar] 查询 %s 所属板块失败: %s", stock_code, exc)
            return []

    # =================================================================
    # 全球事件映射
    # =================================================================

    def _map_global_events(
        self, news_events: List[NewsEvent], start: date, end: date
    ) -> List[CatalystEvent]:
        """匹配 GLOBAL_TO_A_SHARE_MAP → 生成映射后的 CatalystEvent"""
        results = []

        for news in news_events:
            title_lower = news.title.lower()

            for mapping in GLOBAL_TO_A_SHARE_MAP:
                keywords = mapping["global_keywords"]
                if not any(kw.lower() in title_lower for kw in keywords):
                    continue

                # 命中 → 展开板块
                # 处理油价异动的正反向映射
                sectors_positive = mapping.get("a_share_sectors", [])
                sectors_negative = mapping.get("a_share_sectors_negative", [])

                # 估算事件日期（新闻日期 + 1~3 交易日传导）
                news_date = date.fromisoformat(news.first_seen) if news.first_seen else date.today()
                catalyst_date = min(_trade_days_offset(news_date, 1), end)

                if catalyst_date < start:
                    continue

                # 正向映射
                for sector_hint in sectors_positive:
                    matched = self._match_sector_hint(sector_hint)
                    for sector in matched:
                        results.append(CatalystEvent(
                            event_id=_make_event_id("global_map", "global", sector, catalyst_date),
                            event_date=catalyst_date,
                            event_type="global",
                            title=f"[全球] {mapping['global_event']}",
                            sector=sector,
                            impact_level=mapping["base_impact_level"],
                            direction=mapping.get("direction", "binary"),
                            confidence=0.8,
                            consensus_expectation=f"全球事件传导: {mapping['global_event']}",
                            source="global_map",
                            category_tag=mapping.get("category_tag", "binary"),
                            is_global=True,
                        ))

                # 反向映射（利空板块）
                for sector_hint in sectors_negative:
                    matched = self._match_sector_hint(sector_hint)
                    for sector in matched:
                        results.append(CatalystEvent(
                            event_id=_make_event_id("global_map", "global", sector, catalyst_date),
                            event_date=catalyst_date,
                            event_type="global",
                            title=f"[全球·利空] {mapping['global_event']}",
                            sector=sector,
                            impact_level=mapping["base_impact_level"],
                            direction="negative",
                            confidence=0.8,
                            consensus_expectation=f"全球事件利空传导: {mapping['global_event']}",
                            source="global_map",
                            category_tag=mapping.get("category_tag", "binary"),
                            is_global=True,
                        ))

        return results

    def _match_sector_hint(self, sector_hint: str) -> List[str]:
        """板块关键词 → CONCEPT_UNIVERSE 中匹配的板块名"""
        # 精确匹配
        if sector_hint in self.concept_set:
            return [sector_hint]

        # 模糊匹配：板块名包含关键词
        matched = [name for name in self.concept_set if sector_hint in name]
        return matched if matched else []

    # =================================================================
    # LLM 叙事型催化剂提取
    # =================================================================

    async def _llm_extract_narrative_catalysts(
        self, news_events: List[NewsEvent], start: date, end: date
    ) -> List[CatalystEvent]:
        """从 Node 4 历史事件中筛选前瞻性事件，LLM 判断板块映射与置信度"""
        # 筛选有价值的输入事件（emerging/fermenting/peak 状态）
        candidates = [
            e for e in news_events
            if e.lifecycle_state in ("emerging", "fermenting", "peak")
            and e.impact_level in ("high", "medium")
        ]

        if not candidates:
            return []

        # 构建 prompt
        concept_list = sorted(self.concept_set)
        events_summary = "\n".join(
            f"- [{e.lifecycle_state}] {e.title} (影响={e.impact_level}, "
            f"情感={e.sentiment}, 首次={e.first_seen}, 提及={e.mention_count})"
            for e in candidates[:30]  # 限制输入量
        )

        prompt = f"""你是一个 A 股市场催化剂分析师。基于以下近期新闻事件，识别未来 {CATALYST_WINDOW_DAYS} 天内可能发生的催化剂事件。

## 候选新闻事件
{events_summary}

## 合法板块列表（只允许映射到以下板块）
{json.dumps(concept_list, ensure_ascii=False)}

## 输出要求
输出 JSON 数组，每项为一个催化剂事件：
```json
[
  {{
    "date": "YYYY-MM-DD",
    "title": "事件描述",
    "sector": "板块名（必须在合法列表中）",
    "impact_level": 2,
    "direction": "positive/negative/binary/drift/fade",
    "confidence": 0.8,
    "consensus_expectation": "市场对此事件的共识预期是什么",
    "category_tag": "drift/fade/binary",
    "source_news": "来源于哪条新闻的标题"
  }}
]
```

## 严格约束
1. date 必须是 {start} ~ {end} 之间的具体日期
2. confidence 必须给出（0-1），不确定则给低分（<0.7 的不入评分但会标"参考"）
3. consensus_expectation 必填——"市场预期什么？"，说不出则 confidence 给 0.5
4. category_tag 必须标注：drift（量化基本面，事件后继续漂移）/ fade（软叙事，事件日即退）/ binary（高不确定二元事件）
5. sector 必须来自合法列表，不编造板块名
6. 宁缺毋滥：没有高置信催化剂就输出空数组
7. 只输出 JSON，不要其他文字"""

        try:
            response = await asyncio.to_thread(
                self.llm.call_text,
                messages=[
                    {"role": "system", "content": "你是 A 股催化剂分析师，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=3000,
            )

            if not response or not response.content:
                logger.warning("[CatalystCalendar] LLM 叙事提取返回空")
                return []

            return self._parse_llm_narrative(response.content, start, end)

        except Exception as exc:
            logger.warning("[CatalystCalendar] LLM 叙事提取失败: %s", exc)
            return []

    def _parse_llm_narrative(self, text: str, start: date, end: date) -> List[CatalystEvent]:
        """解析 LLM 输出的 JSON，过滤非法条目"""
        # 兼容 markdown code block
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            items = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("[CatalystCalendar] LLM 输出 JSON 解析失败")
            return []

        if not isinstance(items, list):
            return []

        results = []
        for item in items:
            try:
                event_date = date.fromisoformat(str(item["date"])[:10])
                sector = item.get("sector", "")

                if sector not in self.concept_set:
                    continue
                if not (start <= event_date <= end):
                    continue

                confidence = float(item.get("confidence", 0.5))
                impact = int(item.get("impact_level", 1))
                impact = max(1, min(3, impact))

                results.append(CatalystEvent(
                    event_id=_make_event_id("llm_narrative", "narrative", sector, event_date),
                    event_date=event_date,
                    event_type="narrative",
                    title=item.get("title", "叙事型催化"),
                    sector=sector,
                    impact_level=impact,
                    direction=item.get("direction", "positive"),
                    confidence=confidence,
                    consensus_expectation=item.get("consensus_expectation", ""),
                    source="llm_narrative",
                    category_tag=item.get("category_tag", ""),
                ))
            except (ValueError, KeyError, TypeError) as exc:
                logger.debug("[CatalystCalendar] 跳过非法 LLM 条目: %s", exc)
                continue

        return results

    # =================================================================
    # 持久化
    # =================================================================

    def _save_events(self, events: List[CatalystEvent], target_date: date) -> None:
        """UPSERT 事件到 catalyst_events 表"""
        if not events:
            return

        from src.storage import DatabaseManager, CatalystEventRecord
        db = DatabaseManager()
        expires = target_date + timedelta(days=CATALYST_WINDOW_DAYS)

        with db.get_session() as session:
            for event in events:
                existing = session.get(CatalystEventRecord, event.event_id)
                if existing:
                    # 更新已有记录
                    existing.event_date = event.event_date
                    existing.title = event.title
                    existing.impact_level = event.impact_level
                    existing.confidence = event.confidence
                    existing.consensus_expectation = event.consensus_expectation
                    existing.expires_at = expires
                else:
                    record = CatalystEventRecord(
                        event_id=event.event_id,
                        event_date=event.event_date,
                        event_type=event.event_type,
                        title=event.title,
                        sector=event.sector,
                        impact_level=event.impact_level,
                        direction=event.direction,
                        confidence=event.confidence,
                        consensus_expectation=event.consensus_expectation,
                        source=event.source,
                        category_tag=event.category_tag,
                        is_global=event.is_global,
                        expires_at=expires,
                    )
                    session.add(record)

            session.commit()

        logger.debug("[CatalystCalendar] 持久化 %d 事件", len(events))

        # P6: 同步注册到兑现归档表
        try:
            from src.got.catalyst_realization import CatalystRealizationTracker
            tracker = CatalystRealizationTracker()
            for event in events:
                tracker.register_event(event, source_type="forward")
        except Exception as exc:
            logger.debug("[CatalystCalendar] P6 注册失败（已降级）: %s", exc)

    def _load_active_events(self, target_date: date) -> List[CatalystEvent]:
        """加载 expires_at >= target_date 的有效事件"""
        from src.storage import DatabaseManager, CatalystEventRecord
        db = DatabaseManager()

        with db.get_session() as session:
            records = (
                session.query(CatalystEventRecord)
                .filter(CatalystEventRecord.expires_at >= target_date)
                .all()
            )

        return [
            CatalystEvent(
                event_id=r.event_id,
                event_date=r.event_date,
                event_type=r.event_type,
                title=r.title,
                sector=r.sector,
                impact_level=r.impact_level,
                direction=r.direction,
                confidence=r.confidence,
                consensus_expectation=r.consensus_expectation or "",
                source=r.source,
                category_tag=r.category_tag or "",
                is_global=r.is_global or False,
            )
            for r in records
        ]

    # =================================================================
    # 辅助
    # =================================================================

    def _load_recent_news_events(self, days: int = 7) -> List[NewsEvent]:
        """从 Node4EventRecord 加载最近 N 天的事件（供全球映射和 LLM 消费）"""
        from src.storage import DatabaseManager, Node4EventRecord
        db = DatabaseManager()
        cutoff = date.today() - timedelta(days=days)

        with db.get_session() as session:
            records = (
                session.query(Node4EventRecord)
                .filter(
                    Node4EventRecord.last_seen >= cutoff,
                    Node4EventRecord.lifecycle_state.in_(["emerging", "fermenting", "peak"]),
                )
                .all()
            )

        return [
            NewsEvent(
                event_type=r.category or "industry",
                title=r.title,
                summary=r.title,
                impact_level="high" if (r.significance_score or 0) > 0.5 else "medium",
                sentiment="neutral",
                lifecycle_state=r.lifecycle_state,
                mention_count=r.mention_count,
                first_seen=str(r.first_seen) if r.first_seen else None,
                source_count=len(r.get_sources_seen()),
            )
            for r in records
        ]

    @staticmethod
    def _deduplicate(events: List[CatalystEvent]) -> List[CatalystEvent]:
        """同 sector+date 保留 impact_level 最高的事件（仅用于非结构化事件）"""
        best: Dict[Tuple[str, date], CatalystEvent] = {}
        for event in events:
            key = (event.sector, event.event_date)
            if key not in best or event.impact_level > best[key].impact_level:
                best[key] = event
        return list(best.values())

    @staticmethod
    def _aggregate_structured_events(events: List[CatalystEvent]) -> List[CatalystEvent]:
        """聚合同板块同日期的结构化事件（财报/解禁），合并标题列出多只股票"""
        from collections import defaultdict
        groups: Dict[Tuple[str, str, date], List[CatalystEvent]] = defaultdict(list)
        non_structured = []

        for e in events:
            if e.source.startswith("akshare_"):
                key = (e.source, e.sector, e.event_date)
                groups[key].append(e)
            else:
                non_structured.append(e)

        aggregated = []
        for (source, sector, event_date), group in groups.items():
            if len(group) == 1:
                aggregated.append(group[0])
            else:
                # 合并标题：列出所有股票名
                stock_names = [e.title for e in group]
                merged_title = "；".join(stock_names[:3])
                if len(stock_names) > 3:
                    merged_title += f" 等{len(stock_names)}只"

                representative = group[0]
                aggregated.append(CatalystEvent(
                    event_id=representative.event_id,
                    event_date=event_date,
                    event_type=representative.event_type,
                    title=merged_title,
                    sector=sector,
                    impact_level=representative.impact_level,
                    direction=representative.direction,
                    confidence=representative.confidence,
                    consensus_expectation=representative.consensus_expectation,
                    source=source,
                    category_tag=representative.category_tag,
                ))

        return aggregated + non_structured
