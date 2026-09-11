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
import re
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
from src.got.board_aliases import normalize_sector_name as _normalize_sector_name

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
    # 增加 Node 2 板块名别名（东财板块名 vs CONCEPT_UNIVERSE 命名差异）
    # Node 2 使用东财原始板块名（如"半导体"），CONCEPT_UNIVERSE 中可能是"半导体概念"
    # 双向映射：两个名字都加入集合，确保任一方都能匹配
    aliases = {
        "半导体概念": "半导体", "芯片概念": "芯片",
        "PCB概念": "PCB", "CPO概念": "CPO",
        "EDA概念": "EDA", "OLED概念": "OLED",
        "算力概念": "算力", "液冷概念": "液冷",
        "数据中心概念": "数据中心",
    }
    # 双向：原集合有"半导体概念"→加"半导体"；若有"半导体"→加"半导体概念"
    all_names = set(names)
    for formal, alias in aliases.items():
        if formal in all_names:
            names.add(alias)
        if alias in all_names:
            names.add(formal)
    return names


# 板块名规范化映射统一使用公共别名表（同花顺口径 → Node 2 东财口径），
# _normalize_sector_name 由 src.got.board_aliases 提供（见顶部 import）


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

    async def build_calendar(
        self, target_date: Optional[date] = None, force_full: bool = False
    ) -> CatalystCalendar:
        """构建催化剂日历（全量或增量）

        force_full=True 跳过刷新节奏判断强制全量重建（守卫修复验证/残留清理场景）。
        """
        target_date = target_date or date.today()
        end_date = target_date + timedelta(days=CATALYST_WINDOW_DAYS)

        try:
            full_refresh = force_full or self._should_full_refresh(target_date)
            mode = "full" if full_refresh else "incremental"

            if full_refresh:
                events = await self._full_build(target_date, end_date)
            else:
                events = await self._incremental_update(target_date, end_date)

            # 按日期排序
            events.sort(key=lambda e: e.event_date)

            # 持久化（全量模式先清理本次未再生成的残留记录）
            if full_refresh:
                self._cleanup_stale_events(events, target_date)
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
        """全量构建：结构化(财报+宏观) + 全球映射 + LLM 叙事（限售解禁不纳入）"""
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
        # lockup 不入催化剂日历（利空风险事件），仅映射 earnings 股票
        all_stocks = earnings_raw
        stock_sector_map = await self._batch_map_stocks(all_stocks, max_concurrent=8)

        for raw in earnings_raw:
            mapped = self._map_structured_event_fast(raw, "earnings", stock_sector_map)
            all_events.extend(mapped)

        # 限售解禁不纳入催化剂日历（利空风险事件，非正面催化信号）
        # 未来由独立 risk_events 通道消费

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

        # T5: 事件→板块语义对齐层（为叙事/全球事件补充多板块扇出）
        # 候选域 = CONCEPT_UNIVERSE + 蓄水池板块（A2 最想喂饱的对象）
        all_events = await self._align_events_to_sectors(all_events, self._build_sector_universe())

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

        # T5: 增量路径同样跑语义对齐（只处理 affected_sectors 为空的新事件；
        # 存量事件从 DB 加载时已回填，不会重复对齐）——否则扇出一周仅全量日生效
        new_events = await self._align_events_to_sectors(
            new_events, self._build_sector_universe()
        )

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
        # 限售解禁不纳入催化剂日历（利空风险，非催化正面信号）
        if event_type == "lockup":
            return []

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
                event_id=_make_event_id(source, event_type, _normalize_sector_name(sector), event_date),
                event_date=event_date,
                event_type=event_type,
                title=title,
                sector=_normalize_sector_name(sector),
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
        """匹配 GLOBAL_TO_A_SHARE_MAP → 生成映射后的 CatalystEvent

        跨日去重（2026-08-28）：同一真实事件的多日跟踪新闻（如英伟达财报发布、
        次日市值暴涨）若各自按 first_seen 偏移，会生成不同 catalyst_date → 不同
        event_id → 日历出现跨日重复。此处先按 mapping 聚合命中，锚定最早新闻日期，
        每个 (mapping, sector) 只生成一条事件；event_id 幂等，UPSERT 自然去重。

        反应新闻守卫（2026-08-31）：+1 交易日偏移的前提是事前新闻；事后反应报道
        （市值暴涨等）再 +1 属传导链双重计算，聚合前先过滤（复用 D3 时态标记口径）。
        """
        # 聚合命中：key = global_event 名 → (mapping, 最早新闻日期, 锚定新闻标题)
        hits: Dict[str, Tuple[Dict[str, Any], date, str]] = {}
        for news in news_events:
            # 事后反应报道不产生新的催化剂日期（如财报已发布后的市值暴涨报道）
            if self._is_reaction_news(news.title):
                logger.debug(
                    "[CatalystCalendar] 全球映射反应新闻跳过: %s", news.title[:40]
                )
                continue
            title_lower = news.title.lower()
            for mapping in GLOBAL_TO_A_SHARE_MAP:
                keywords = mapping["global_keywords"]
                if not any(kw.lower() in title_lower for kw in keywords):
                    continue
                news_date = date.fromisoformat(news.first_seen) if news.first_seen else date.today()
                key = mapping["global_event"]
                if key not in hits or news_date < hits[key][1]:
                    hits[key] = (mapping, news_date, news.title)

        results = []
        for _key, (mapping, news_date, anchor_title) in hits.items():
            # 估算事件日期（新闻日期 + 1 交易日传导）
            catalyst_date = min(_trade_days_offset(news_date, 1), end)
            if catalyst_date < start:
                continue

            # 处理油价异动的正反向映射
            sectors_positive = mapping.get("a_share_sectors", [])
            sectors_negative = mapping.get("a_share_sectors_negative", [])

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
                        source_news=anchor_title,
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
                        source_news=anchor_title,
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

        today = start  # start 即 target_date（今日）
        prompt = f"""你是一个 A 股市场催化剂分析师。你的任务是识别未来 {CATALYST_WINDOW_DAYS} 天内**尚未发生**的催化剂事件。

**今天是 {today}**。你需要输出 {start} ~ {end} 之间**真正前瞻性的事件**。

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

## 时态判断（核心）

**禁止输出已发生事件的回声**。区分两种情况：

| 情况 | 示例 | 是否允许输出 |
|------|------|--------------|
| **事件本身已发生**（禁止） | "美股存储板块重挫"、"某公司暴跌"、"政策已落地" | ❌ 禁止 |
| **已发生事件的后续影响**（允许，需指出具体未来节点） | "制裁生效后 9-15 落地第二批清单"、"财报发布后 9-10 分析师下调评级" | ✅ 允许，date 必须是具体未来节点 |

**判断标准**：
- 如果候选新闻描述的是**已经发生的事后状态**（"重挫/暴跌/崩盘/已落地/已公布"），且你无法指出一个**具体的未来触发节点**，则不要输出。
- 如果候选新闻描述的是**未来将发生的事**（"即将发布/下周落地/预计公布"），则可以输出。
- 如果候选新闻是**盘中/事后报道**（含"早盘/收盘/涨超/暴增/完成"等表述），禁止直接改写成前瞻事件，除非能指出具体的未来触发节点。

## Few-shot 示例

**✅ 正确示例（前瞻性事件）**：
```json
{{
  "date": "2026-09-05",
  "title": "英伟达 Q2 财报发布（盘后）",
  "sector": "半导体",
  "impact_level": 3,
  "direction": "binary",
  "confidence": 0.9,
  "consensus_expectation": "市场预期 HBM 收入环比 +50%，若 miss 则 A 股算力板块承压",
  "category_tag": "binary",
  "source_news": "英伟达将于 9-5 盘后发布 Q2 财报"
}}
```

**❌ 错误示例（已发生事件回声，禁止输出）**：
```json
{{
  "date": "2026-08-29",
  "title": "美股存储光通信板块重挫，A 股相关板块承压",
  "sector": "存储芯片",
  "impact_level": 2,
  "direction": "negative",
  "confidence": 0.7,
  "consensus_expectation": "美股重挫传导",
  "category_tag": "drift",
  "source_news": "美股存储光通信板块重挫，A 股相关板块或承压"
}}
```
**错误原因**：source_news 和 title 几乎逐字重复，描述的是**已经发生的重挫**，不是未来事件。8-29 是周六，更加不可能发生美股事件。

## 严格约束
1. date 必须是 {start} ~ {end} 之间的具体日期，且**不能是周末**（周六/周日 A 股不开盘）
2. confidence 必须给出（0-1），不确定则给低分（<0.7 的不入评分但会标"参考"）
3. consensus_expectation 必填——"市场预期什么？"，说不出则 confidence 给 0.5
4. category_tag 必须标注：drift（量化基本面，事件后继续漂移）/ fade（软叙事，事件日即退）/ binary（高不确定二元事件）
5. sector 必须来自合法列表，不编造板块名
6. **宁缺毋滥**：没有高置信前瞻性催化剂就输出空数组
7. **不要输出已发生事件的回声**（参见时态判断表格）
8. 只输出 JSON，不要其他文字"""

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

            events = self._parse_llm_narrative(response.content, start, end)

            # T2: 叙事校验者（D1/D2 结构守卫之后，语义层校验）
            events = await self._critic_verify_narrative(events, news_events)

            return events

        except Exception as exc:
            logger.warning("[CatalystCalendar] LLM 叙事提取失败: %s", exc)
            return []

    def _parse_llm_narrative(self, text: str, start: date, end: date) -> List[CatalystEvent]:
        """解析 LLM 输出的 JSON，过滤非法条目

        过滤规则：
        1. 板块合法性检查
        2. 日期范围检查
        3. D2 周末守卫：event_date 落在周末直接丢弃
        4. D1 回声检测：title 与 source_news 高度相似（全方向） → 已发生事件回声，丢弃
        5. D3 时态守卫：source_news 为过去时报道且 title 无未来标记 → 事后改写，丢弃
        6. D4 实体一致性：title 中头衔+人名未出现于 source_news → LLM 幻觉，丢弃
        """
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
                # 规范化板块名（"半导体概念"→"半导体"等，与 Node 2 对齐）
                canonical_sector = _normalize_sector_name(sector)

                if sector not in self.concept_set and canonical_sector not in self.concept_set:
                    continue
                if not (start <= event_date <= end):
                    continue

                # D2 周末守卫：日期落在周末直接丢弃
                if event_date.weekday() >= 5:
                    logger.debug(
                        "[CatalystCalendar] 周末守卫丢弃: %s (date=%s, weekday=%d)",
                        item.get("title", "")[:30], event_date, event_date.weekday()
                    )
                    continue

                confidence = float(item.get("confidence", 0.5))
                impact = int(item.get("impact_level", 1))
                impact = max(1, min(3, impact))

                title = item.get("title", "叙事型催化")
                direction = item.get("direction", "positive")
                source_news = item.get("source_news")

                # D1 回声检测：title 与 source_news 高度相似（全方向，2026-08-28 移除 negative 限制）
                if self._is_echo_event(title, source_news, direction):
                    logger.debug(
                        "[CatalystCalendar] 回声检测丢弃: title=%s, source_news=%s",
                        title[:30], (source_news or "")[:30]
                    )
                    continue

                # D3 时态守卫：源新闻为过去时报道且 title 未指向未来节点 → 事后改写回声
                if self._is_past_echo(title, source_news):
                    logger.debug(
                        "[CatalystCalendar] 时态守卫丢弃: title=%s, source_news=%s",
                        title[:30], (source_news or "")[:30]
                    )
                    continue

                # D4 实体一致性：头衔+人名必须可溯源到 source_news（拦截鲍威尔式幻觉）
                if self._has_unsourced_entity(title, source_news):
                    logger.debug(
                        "[CatalystCalendar] 实体一致性丢弃: title=%s, source_news=%s",
                        title[:30], (source_news or "")[:30]
                    )
                    continue

                results.append(CatalystEvent(
                    event_id=_make_event_id("llm_narrative", "narrative", canonical_sector, event_date),
                    event_date=event_date,
                    event_type="narrative",
                    title=title,
                    sector=canonical_sector,
                    impact_level=impact,
                    direction=direction,
                    confidence=confidence,
                    consensus_expectation=item.get("consensus_expectation", ""),
                    source="llm_narrative",
                    category_tag=item.get("category_tag", ""),
                    source_news=source_news,
                ))
            except (ValueError, KeyError, TypeError) as exc:
                logger.debug("[CatalystCalendar] 跳过非法 LLM 条目: %s", exc)
                continue

        return results

    # =================================================================
    # T5: 事件→板块语义对齐层
    # =================================================================

    _SECTOR_ALIGN_PROMPT = """你是 A 股板块映射专家。给定一条催化剂事件和候选板块列表，判断该事件影响哪些板块。

## 规则
1. 只从候选列表中选择，不编造板块名
2. 每个事件可选 0-N 个板块（0 表示无明确板块影响）
3. 输出 JSON 数组，每个元素: {{"event_id": "...", "affected_sectors": ["板块1", "板块2"]}}
4. 若事件无明确板块影响，affected_sectors 留空数组

## 事件列表
{events_json}

## 候选板块（只从中选择）
{sector_list}

只输出 JSON，不要其他文字。"""

    def _build_sector_universe(self) -> Set[str]:
        """T5 候选域：CONCEPT_UNIVERSE 白名单 + 蓄水池板块（计划要求：蓄水池+主线+白名单）

        蓄水池是 A2 晋升最想喂饱的对象，必须在候选域内，否则 T5 无法把事件对齐到蓄水池名字。
        """
        universe = set(self.concept_set)
        universe.update(self._load_reservoir_sector_names())
        return universe

    @staticmethod
    def _load_reservoir_sector_names() -> Set[str]:
        """从最新 GOT 归档加载蓄水池板块名（失败降级空集，不阻塞）"""
        from pathlib import Path
        names: Set[str] = set()
        try:
            # A-P0-1 统一入口，排除当天
            from src.got.archive import list_archive_dirs
            date_dirs = list_archive_dirs(reverse=True, before=date.today())
            for d in date_dirs:
                unified_path = d / "unified.json"
                if not unified_path.exists():
                    continue
                with open(unified_path, encoding="utf-8") as f:
                    data = json.load(f)
                for e in data.get("ambush_reservoir", []):
                    if e.get("sector_name"):
                        names.add(e["sector_name"])
                break  # 只取最近一份归档
        except Exception as exc:
            logger.debug("[T5对齐] 蓄水池板块加载失败，降级: %s", exc)
        return names

    async def _align_events_to_sectors(
        self, events: List[CatalystEvent], sector_universe: Set[str]
    ) -> List[CatalystEvent]:
        """T5: LLM 语义对齐 — 为叙事/全球事件补充 affected_sectors 多板块扇出

        一次批量调用：逐事件输出 affected_sectors（0-N 个）。
        影子模式（SECTOR_ALIGN_SHADOW=true）：只记录对比日志，不修改事件。

        仅处理 source in (llm_narrative, global_map) 的事件。
        结构化/宏观事件走既有规则映射，不受影响。
        """
        from src.got.catalyst_config import SECTOR_ALIGN_SHADOW

        # 筛选需要对齐的事件（叙事 + 全球，且 affected_sectors 为空）
        targets = [
            e for e in events
            if e.source in ("llm_narrative", "global_map")
            and not e.affected_sectors
        ]
        if not targets:
            return events

        # 构建板块列表（限制 150 个，防 prompt 过长）。
        # 蓄水池板块优先保留（它们是晋升路径最想对齐的目标），其余按字典序补齐；
        # prompt 与输出校验使用同一份列表，避免"校验允许的名字 LLM 看不到"的自相矛盾。
        reservoir = self._load_reservoir_sector_names()
        prioritized = sorted(n for n in sector_universe if n in reservoir)
        rest = sorted(n for n in sector_universe if n not in reservoir)
        sector_list = (prioritized + rest)[:150]
        allowed_sectors = set(sector_list)

        # 构建事件摘要（只传关键信息）
        events_json = json.dumps(
            [
                {
                    "event_id": e.event_id,
                    "title": e.title[:80],
                    "sector": e.sector,
                    "event_type": e.event_type,
                }
                for e in targets[:30]  # 最多 30 个事件一批
            ],
            ensure_ascii=False,
        )

        prompt = self._SECTOR_ALIGN_PROMPT.format(
            events_json=events_json,
            sector_list=", ".join(sector_list),
        )

        # R5: 串行 + 空响应重试（与 T1/T3 对齐）
        response = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    self.llm.call_text,
                    messages=[
                        {"role": "system", "content": "你是板块映射专家，只输出 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=2000,
                )
                if response and response.content:
                    break
            except Exception as exc:
                logger.debug("[T5对齐] 第 %d 次调用失败: %s", attempt + 1, exc)
                response = None
        try:
            if not response or not response.content:
                logger.warning("[T5对齐] LLM 返回空（已重试 3 次），降级跳过")
                return events

            # 解析 LLM 输出
            cleaned = response.content.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            mappings = json.loads(cleaned)

            if not isinstance(mappings, list):
                logger.warning("[T5对齐] LLM 输出非数组，降级跳过")
                return events

            # 建立 event_id → affected_sectors 映射
            id_to_sectors: Dict[str, List[str]] = {}
            for m in mappings:
                eid = m.get("event_id", "")
                sectors = m.get("affected_sectors", [])
                if eid and isinstance(sectors, list):
                    # 只保留在候选列表中的板块（与 prompt 同源，不产生幻觉名）
                    id_to_sectors[eid] = [s for s in sectors if s in allowed_sectors]

            # 应用对齐结果（或影子模式只记日志）
            aligned_count = 0
            multi_sector_count = 0
            for event in targets:
                new_sectors = id_to_sectors.get(event.event_id, [])
                if not new_sectors:
                    continue
                aligned_count += 1
                if len(new_sectors) > 1:
                    multi_sector_count += 1

                if SECTOR_ALIGN_SHADOW:
                    # 影子模式：只记日志
                    if new_sectors != [event.sector]:
                        logger.info(
                            "[T5影子] %s: 原 sector=%s → 对齐后 %s",
                            event.event_id[:16], event.sector, new_sectors
                        )
                else:
                    # 正式模式：写入 affected_sectors
                    event.affected_sectors = new_sectors

            logger.info(
                "[T5对齐] %d 个事件对齐，%d 个多板块扇出（影子=%s）",
                aligned_count, multi_sector_count, SECTOR_ALIGN_SHADOW,
            )

        except Exception as exc:
            logger.warning("[T5对齐] LLM 调用失败，降级跳过: %s", exc)

        return events

    # =================================================================
    # T2: 叙事事件校验者（反 Sycophancy 批判角色）
    # =================================================================

    _CRITIC_PROMPT = """你是催化剂事件的批判性审查员。你的角色是质疑和验证，不是赞同。

给定一组 LLM 生成的催化剂事件和原始新闻素材，逐事件判断是否应保留。

## 检查清单
对每个事件检查以下 4 项：
1. **时态**：事件描述的是未来将发生的事，还是已发生的事后描述？（已发生 → drop）
2. **方向**：direction 标注是否与事件内容一致？（矛盾 → drop）
3. **板块映射**：sector 选择是否合理？（明显错位 → drop）
4. **证据支撑**：原始新闻素材中是否有支撑该事件的证据？（纯推测 → drop）

## 原始新闻素材
{source_news_summary}

## 待审事件
{events_json}

## 输出格式
JSON 数组: [{{"event_id": "...", "verdict": "keep/drop", "reason": "一句话理由", "checks": {{"temporal": "ok/fail", "direction": "ok/fail", "sector": "ok/fail", "evidence": "ok/fail"}}}}]

只输出 JSON，不要其他文字。"""

    async def _critic_verify_narrative(
        self, events: List[CatalystEvent], source_news: List["NewsEvent"]
    ) -> List[CatalystEvent]:
        """T2: 批判者校验 — 对 LLM 叙事事件做反 Sycophancy 审查

        影子模式（NARRATIVE_CRITIC_SHADOW=true）：只记录 drop 日志，不实际丢弃。
        正式模式：drop 的事件从结果中移除。

        D1/D2/D3/D4 结构守卫已在前置过滤，此方法是语义层补充。
        """
        from src.got.catalyst_config import NARRATIVE_CRITIC_SHADOW

        if not events:
            return events

        # 构建原始新闻摘要（只取标题+情感）
        source_summary = "\n".join(
            f"- [{n.sentiment}] {n.title[:60]}"
            for n in source_news[:20]
        ) if source_news else "（无原始新闻素材）"

        # 构建事件摘要
        events_json = json.dumps(
            [
                {
                    "event_id": e.event_id,
                    "title": e.title[:80],
                    "sector": e.sector,
                    "direction": e.direction,
                    "confidence": e.confidence,
                    "source_news": (e.source_news or "")[:60],
                }
                for e in events[:20]  # 最多 20 个一批
            ],
            ensure_ascii=False,
        )

        prompt = self._CRITIC_PROMPT.format(
            source_news_summary=source_summary,
            events_json=events_json,
        )

        # R5: 串行 + 空响应重试（与 T1/T3/T5 对齐）
        response = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    self.llm.call_text,
                    messages=[
                        {"role": "system", "content": "你是批判性审查员，只输出 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=2000,
                )
                if response and response.content:
                    break
            except Exception as exc:
                logger.debug("[T2校验] 第 %d 次调用失败: %s", attempt + 1, exc)
                response = None
        try:
            if not response or not response.content:
                logger.warning("[T2校验] LLM 返回空（已重试 3 次），降级保留全部事件")
                return events

            cleaned = response.content.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            verdicts = json.loads(cleaned)

            if not isinstance(verdicts, list):
                logger.warning("[T2校验] LLM 输出非数组，降级保留全部")
                return events

            # 建立 event_id → verdict 映射
            drop_ids: set = set()
            for v in verdicts:
                if v.get("verdict") == "drop":
                    drop_ids.add(v.get("event_id", ""))
                    reason = v.get("reason", "未说明")
                    checks = v.get("checks", {})
                    failed = [k for k, val in checks.items() if val == "fail"]
                    logger.info(
                        "[T2校验%s] DROP %s: %s (失败项: %s)",
                        "影子" if NARRATIVE_CRITIC_SHADOW else "",
                        v.get("event_id", "?")[:16],
                        reason[:40],
                        ", ".join(failed) if failed else "未标注",
                    )

            if NARRATIVE_CRITIC_SHADOW:
                # 影子模式：只记日志，不实际丢弃
                return events
            else:
                # 正式模式：移除被 drop 的事件
                kept = [e for e in events if e.event_id not in drop_ids]
                dropped = len(events) - len(kept)
                if dropped > 0:
                    logger.info("[T2校验] 批判者丢弃 %d/%d 个事件", dropped, len(events))
                return kept

        except Exception as exc:
            logger.warning("[T2校验] LLM 调用失败，降级保留全部: %s", exc)
            return events

    @staticmethod
    def _is_echo_event(title: str, source_news: Optional[str], direction: str) -> bool:
        """判断是否为已发生事件的回声（D1）

        条件：
        1. source_news 存在
        2. title 与 source_news 高度相似（字符级 Jaccard >= 0.7 或互相包含）

        2026-08-28 起移除 direction=="negative" 前置限制（positive 回声同样漏检过），
        阈值提高到 0.7 避免误杀合理引用源新闻标题的事件。
        direction 参数保留仅为签名兼容，不再参与判定。
        """
        if not source_news:
            return False

        # 互相包含：一个完全包含另一个
        if source_news in title or title in source_news:
            return True

        # 字符级 Jaccard 相似度
        title_chars = set(title)
        source_chars = set(source_news)
        if not title_chars or not source_chars:
            return False

        intersection = title_chars & source_chars
        union = title_chars | source_chars
        jaccard = len(intersection) / len(union) if union else 0

        return jaccard >= 0.7

    # D3 时态守卫标记词：出现于 source_news 即视为事后/盘中报道；
    # title 必须含未来标记才保留（宁缺毋滥）
    _PAST_TENSE_MARKERS = (
        "已完成", "已落地", "已公布", "已生效",
        "涨超", "暴增", "暴涨", "重挫", "大跌",
        "早盘", "收盘", "完成", "落地",
    )
    _FUTURE_TENSE_MARKERS = (
        "将", "即将", "预计", "定于", "计划", "下周", "待", "有望", "拟",
    )

    @classmethod
    def _is_past_echo(cls, title: str, source_news: Optional[str]) -> bool:
        """时态守卫（D3）：源新闻为过去时报道且 title 无未来标记 → 事后改写回声"""
        if not source_news:
            return False
        if not any(marker in source_news for marker in cls._PAST_TENSE_MARKERS):
            return False
        return not any(marker in title for marker in cls._FUTURE_TENSE_MARKERS)

    @classmethod
    def _is_reaction_news(cls, title: str) -> bool:
        """全球映射守卫：含过去时标记且无未来标记 → 事后反应报道（复用 D3 标记口径）

        global_map 的 +1 交易日偏移面向事前新闻（今日宣布 → A 股次日反应）；
        反应报道（"市值暴涨"等）是对已兑现事件的传导，再锚定 +1 会双重计算
        传导链（如 08-27 财报 → 08-28 暴涨新闻被错锚成 08-31 的前瞻催化剂）。
        """
        if not title:
            return False
        if not any(marker in title for marker in cls._PAST_TENSE_MARKERS):
            return False
        return not any(marker in title for marker in cls._FUTURE_TENSE_MARKERS)

    # D4 实体一致性：头衔后紧邻的 2-3 字中文人名（中文头衔通常前置：美联储主席鲍威尔）
    _TITLE_ENTITY_RE = re.compile(r"(?:主席|行长|总裁|首席执行官|CEO|财长|部长)([\u4e00-\u9fa5]{2,3})")

    @classmethod
    def _has_unsourced_entity(cls, title: str, source_news: Optional[str]) -> bool:
        """实体一致性（D4）：title 提取的头衔+人名未出现于 source_news → 判定 LLM 幻觉"""
        if not source_news:
            return False
        for match in cls._TITLE_ENTITY_RE.finditer(title):
            if match.group(1) not in source_news:
                return True
        return False

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
                # T5 扇出序列化（空列表 → NULL，表示未对齐）
                sectors_payload = (
                    json.dumps(event.affected_sectors, ensure_ascii=False)
                    if event.affected_sectors else None
                )
                existing = session.get(CatalystEventRecord, event.event_id)
                if existing:
                    # 更新已有记录
                    existing.event_date = event.event_date
                    existing.title = event.title
                    existing.impact_level = event.impact_level
                    existing.confidence = event.confidence
                    existing.consensus_expectation = event.consensus_expectation
                    existing.source_news = event.source_news
                    existing.affected_sectors = sectors_payload
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
                        source_news=event.source_news,
                        affected_sectors=sectors_payload,
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

    def _cleanup_stale_events(self, events: List[CatalystEvent], target_date: date) -> None:
        """全量重建清理：删除过期记录 + 本次未再生成的 global_map/llm_narrative 残留

        来源降级护栏（2026-08-31）：某来源本次产出为 0 可能是降级（如 LLM 模块缺失）
        而非"无事件"，此时删除该来源存量会误伤有效日历；只清理本次有产出的来源。
        结构化/宏观事件（akshare_*/macro_rule）event_id 幂等，不在此主动删除。
        """
        from src.storage import DatabaseManager, CatalystEventRecord
        db = DatabaseManager()
        valid_ids = {e.event_id for e in events}

        # 本次有产出的非结构化来源（未产出来源的存量跳过清理）
        produced_sources = sorted({
            e.source for e in events if e.source in ("global_map", "llm_narrative")
        })

        # R4 护栏：重建结果为空（数据源全面降级）时不清理残留，
        # 否则 ~event_id.in_(空集) 恒真，会把全部 global_map/llm_narrative 删光；
        # 过期记录仍正常清理（与旧行为一致）
        skip_stale_cleanup = not valid_ids
        if skip_stale_cleanup:
            logger.warning(
                "[CatalystCalendar] 全量重建结果为空，跳过残留清理（防误删全表）"
            )

        with db.get_session() as session:
            expired_count = session.query(CatalystEventRecord).filter(
                CatalystEventRecord.expires_at < target_date
            ).delete(synchronize_session=False)

            if skip_stale_cleanup or not produced_sources:
                stale_count = 0
            else:
                stale_count = session.query(CatalystEventRecord).filter(
                    CatalystEventRecord.source.in_(produced_sources),
                    ~CatalystEventRecord.event_id.in_(valid_ids),
                ).delete(synchronize_session=False)
            session.commit()

        logger.info(
            "[CatalystCalendar] 全量重建清理: 过期=%d, 残留=%d",
            expired_count, stale_count,
        )

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
                source_news=r.source_news,
                # T5 扇出回填：旧记录无该列/空值 → 空列表，增量路径不会重复对齐已有值的事件；
                # 对齐层只处理 affected_sectors 为空的事件，因此旧记录仍可被后续全量对齐补齐。
                affected_sectors=(
                    json.loads(r.affected_sectors)
                    if getattr(r, "affected_sectors", None) else []
                ),
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
