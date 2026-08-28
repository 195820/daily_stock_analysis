# -*- coding: utf-8 -*-
"""
催化剂事件兑现追踪器（P6）

职责：
1. 每日检测过期催化剂事件，计算事件后收益，写入归档表
2. 统计兑现率（按 event_type / source_type / category_tag 分组）
3. 统计各类事件的故事寿命（本地重估）

设计原则：
- 与催化剂日历（catalyst_calendar.py）配合：日历产出事件，追踪器记录兑现
- 与持仓快照（AmbushPositionSnapshotRecord）通过 event_id 关联
- 零 LLM 依赖：全部基于规则 + 数据
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func

from src.got.schemas import CatalystEvent
from src.storage import (
    CatalystEventRecord,
    CatalystRealizationRecord,
    DatabaseManager,
)

logger = logging.getLogger(__name__)


class CatalystRealizationTracker:
    """催化剂事件兑现追踪器

    使用方式：
        tracker = CatalystRealizationTracker()
        tracker.check_and_record_realization(target_date=date.today())
        stats = tracker.get_realization_stats()
    """

    def __init__(self):
        self.db = DatabaseManager()

    # =================================================================
    # 核心：兑现检测与记录
    # =================================================================

    def check_and_record_realization(self, target_date: Optional[date] = None) -> int:
        """检测过期事件并记录兑现结果（含 PEAD 多窗口分析）

        逻辑：
        1. 加载所有未兑现事件（is_realized=False 且 event_date < target_date）
        2. 对每个事件，计算板块 T+1/3/5 多窗口收益
        3. 判断漂移方向是否正确
        4. 更新归档表

        Returns:
            int: 本次新兑现的事件数
        """
        target_date = target_date or date.today()
        realized_count = 0

        with self.db.get_session() as session:
            # 加载未兑现事件
            unrealized = (
                session.query(CatalystRealizationRecord)
                .filter(
                    CatalystRealizationRecord.is_realized == False,
                    CatalystRealizationRecord.event_date < target_date,
                )
                .all()
            )

            if not unrealized:
                logger.info("[P6兑现] 无待兑现事件")
                return 0

            logger.info(f"[P6兑现] 检测到 {len(unrealized)} 个待兑现事件")

            for record in unrealized:
                try:
                    # 计算 T+1/3/5 多窗口收益
                    window_returns = self._compute_multi_window_returns(
                        record.sector, record.event_date
                    )

                    # 至少有一个窗口有数据才算兑现
                    has_any_window = any(
                        window_returns.get(k) is not None
                        for k in ("day_1", "day_3", "day_5")
                    )
                    if not has_any_window:
                        logger.debug(f"[P6兑现] {record.event_id} 无法计算多窗口收益，跳过")
                        continue

                    # 计算实际收益（优先用最长可用窗口，显式 None 检查避免 falsy-zero）
                    actual_return = None
                    for key in ("day_5", "day_3", "day_1"):
                        val = window_returns.get(key)
                        if val is not None:
                            actual_return = val
                            break

                    # 判断漂移方向
                    direction_correct = self._check_direction(
                        record.direction, actual_return
                    )

                    # PEAD 方向判定
                    pead_direction = self._determine_pead_direction(
                        record.direction, window_returns
                    )

                    # 计算故事寿命
                    story_lifetime = (target_date - record.event_date).days

                    # 更新记录
                    record.is_realized = True
                    record.realization_date = target_date
                    record.actual_return = actual_return
                    record.drift_direction_correct = direction_correct
                    record.story_lifetime_days = story_lifetime

                    # PEAD 多窗口字段
                    record.day_1_return = window_returns.get("day_1")
                    record.day_3_return = window_returns.get("day_3")
                    record.day_5_return = window_returns.get("day_5")
                    record.max_return_window = window_returns.get("max_window")
                    record.max_return_pct = window_returns.get("max_pct")
                    record.pead_direction = pead_direction

                    realized_count += 1
                    logger.debug(
                        f"[P6兑现] {record.event_id}: "
                        f"T+1={window_returns.get('day_1')}, "
                        f"T+3={window_returns.get('day_3')}, "
                        f"T+5={window_returns.get('day_5')}, "
                        f"pead={pead_direction}"
                    )

                except Exception as exc:
                    logger.warning(f"[P6兑现] {record.event_id} 兑现检测失败: {exc}")

            session.commit()

        logger.info(f"[P6兑现] 本次新兑现 {realized_count} 个事件")
        return realized_count

    def register_event(self, event: CatalystEvent, source_type: str = "forward") -> None:
        """注册新催化剂事件到归档表

        在催化剂日历构建时调用，为每个事件创建一条归档记录。

        Args:
            event: 催化剂事件
            source_type: forward / reverse_discovery / veto / reservoir
        """
        with self.db.get_session() as session:
            # 检查是否已存在
            existing = (
                session.query(CatalystRealizationRecord)
                .filter_by(event_id=event.event_id)
                .first()
            )
            if existing:
                logger.debug(f"[P6注册] {event.event_id} 已存在，跳过")
                return

            record = CatalystRealizationRecord(
                event_id=event.event_id,
                event_type=event.event_type,
                source=event.source,
                source_type=source_type,
                sector=event.sector,
                title=event.title,
                event_date=event.event_date,
                category_tag=event.category_tag,
                impact_level=event.impact_level,
                consensus_expectation=event.consensus_expectation,
                direction=event.direction,
                confidence=event.confidence,
                is_realized=False,
            )
            session.add(record)
            session.commit()
            logger.debug(f"[P6注册] {event.event_id} ({event.event_type}/{event.sector})")

    # =================================================================
    # 统计方法
    # =================================================================

    def get_realization_stats(self, days: int = 90) -> Dict:
        """统计兑现率（按 event_type / source_type / category_tag 分组）

        Args:
            days: 统计窗口（默认 90 天）

        Returns:
            {
                "total_events": int,
                "realized_events": int,
                "realization_rate": float,
                "by_event_type": {type: {total, realized, rate, avg_return}},
                "by_source_type": {type: {total, realized, rate}},
                "by_category_tag": {tag: {total, realized, rate, avg_return}},
                "direction_accuracy": float,  # 漂移方向预判准确率
            }
        """
        cutoff = date.today() - timedelta(days=days)

        with self.db.get_session() as session:
            # 总事件数
            total = (
                session.query(func.count(CatalystRealizationRecord.id))
                .filter(CatalystRealizationRecord.event_date >= cutoff)
                .scalar()
            ) or 0

            # 已兑现事件数
            realized = (
                session.query(func.count(CatalystRealizationRecord.id))
                .filter(
                    CatalystRealizationRecord.event_date >= cutoff,
                    CatalystRealizationRecord.is_realized == True,
                )
                .scalar()
            ) or 0

            # 按 event_type 分组
            by_event_type = self._group_stats(session, cutoff, "event_type")

            # 按 source_type 分组
            by_source_type = self._group_stats(session, cutoff, "source_type")

            # 按 category_tag 分组
            by_category_tag = self._group_stats(session, cutoff, "category_tag")

            # 方向准确率
            direction_correct = (
                session.query(func.count(CatalystRealizationRecord.id))
                .filter(
                    CatalystRealizationRecord.event_date >= cutoff,
                    CatalystRealizationRecord.is_realized == True,
                    CatalystRealizationRecord.drift_direction_correct == True,
                )
                .scalar()
            ) or 0

        return {
            "total_events": total,
            "realized_events": realized,
            "realization_rate": realized / max(total, 1),
            "by_event_type": by_event_type,
            "by_source_type": by_source_type,
            "by_category_tag": by_category_tag,
            "direction_accuracy": direction_correct / max(realized, 1),
        }

    def get_story_lifetimes(self, days: int = 180) -> Dict[str, Dict]:
        """统计各类事件的故事寿命（本地重估）

        Returns:
            {event_type: {avg_lifetime, median_lifetime, min_lifetime, max_lifetime, count}}
        """
        cutoff = date.today() - timedelta(days=days)

        with self.db.get_session() as session:
            records = (
                session.query(CatalystRealizationRecord)
                .filter(
                    CatalystRealizationRecord.event_date >= cutoff,
                    CatalystRealizationRecord.is_realized == True,
                    CatalystRealizationRecord.story_lifetime_days.isnot(None),
                )
                .all()
            )

        # 按 event_type 分组统计
        lifetimes: Dict[str, List[int]] = {}
        for r in records:
            lifetimes.setdefault(r.event_type, []).append(r.story_lifetime_days)

        result = {}
        for etype, days_list in lifetimes.items():
            days_list.sort()
            n = len(days_list)
            result[etype] = {
                "count": n,
                "avg_lifetime": sum(days_list) / n if n else 0,
                "median_lifetime": days_list[n // 2] if n else 0,
                "min_lifetime": days_list[0] if n else 0,
                "max_lifetime": days_list[-1] if n else 0,
            }

        return result

    def get_pead_analysis_report(self, days: int = 180) -> Dict:
        """PEAD 多窗口分析报告（按 event_type / category_tag 分组）

        用于 B 公式校准：统计各类事件的平均多窗口收益、最大收益出现时间、
        漂移方向一致率等。

        Args:
            days: 统计窗口（默认 180 天）

        Returns:
            {
                "total_realized": int,
                "by_event_type": {
                    type: {
                        "count": int,
                        "avg_day_1": float,
                        "avg_day_3": float,
                        "avg_day_5": float,
                        "avg_max_window": float,
                        "avg_max_pct": float,
                        "aligned_rate": float,
                    }
                },
                "by_category_tag": {
                    tag: { same structure }
                },
                "overall": {
                    "avg_day_1": float, "avg_day_3": float, "avg_day_5": float,
                    "avg_max_window": float, "aligned_rate": float,
                },
            }
        """
        cutoff = date.today() - timedelta(days=days)

        with self.db.get_session() as session:
            records = (
                session.query(CatalystRealizationRecord)
                .filter(
                    CatalystRealizationRecord.event_date >= cutoff,
                    CatalystRealizationRecord.is_realized == True,
                    CatalystRealizationRecord.day_1_return.isnot(None),
                )
                .all()
            )

        if not records:
            return {
                "total_realized": 0,
                "by_event_type": {}, "by_category_tag": {},
                "overall": {"avg_day_1": 0, "avg_day_3": 0, "avg_day_5": 0,
                            "avg_max_window": 0, "aligned_rate": 0},
            }

        def _safe_avg(values):
            filtered = [v for v in values if v is not None]
            return sum(filtered) / len(filtered) if filtered else 0.0

        def _build_group(recs):
            d1 = [r.day_1_return for r in recs]
            d3 = [r.day_3_return for r in recs]
            d5 = [r.day_5_return for r in recs]
            mw = [r.max_return_window for r in recs if r.max_return_window is not None]
            mp = [r.max_return_pct for r in recs if r.max_return_pct is not None]
            aligned = sum(1 for r in recs if r.pead_direction == "aligned")
            total = len(recs)
            return {
                "count": total,
                "avg_day_1": round(_safe_avg(d1), 4),
                "avg_day_3": round(_safe_avg(d3), 4),
                "avg_day_5": round(_safe_avg(d5), 4),
                "avg_max_window": round(sum(mw) / len(mw), 2) if mw else 0,
                "avg_max_pct": round(_safe_avg(mp), 4),
                "aligned_rate": round(aligned / total, 4) if total else 0,
            }

        # 按 event_type 分组
        by_type: Dict[str, list] = {}
        for r in records:
            by_type.setdefault(r.event_type, []).append(r)
        by_event_type = {k: _build_group(v) for k, v in by_type.items()}

        # 按 category_tag 分组
        by_tag: Dict[str, list] = {}
        for r in records:
            tag = r.category_tag or "unknown"
            by_tag.setdefault(tag, []).append(r)
        by_category_tag = {k: _build_group(v) for k, v in by_tag.items()}

        # 总体
        overall = _build_group(records)

        logger.info(
            f"[P6 PEAD] 分析报告: {len(records)} 个已兑现事件, "
            f"{len(by_event_type)} 种事件类型, "
            f"aligned_rate={overall['aligned_rate']:.2%}"
        )

        return {
            "total_realized": len(records),
            "by_event_type": by_event_type,
            "by_category_tag": by_category_tag,
            "overall": overall,
        }

    # =================================================================
    # 板块收益计算
    # =================================================================

    def _compute_sector_return(
        self, sector: str, start_date: date, end_date: date
    ) -> Optional[float]:
        """计算板块事件后收益（兼容旧接口）

        使用 DataFetcherManager.get_board_kline 获取板块 K 线，
        计算 start_date → end_date 的涨幅。

        Returns:
            float: 涨幅百分比（%），或 None（数据不可用）
        """
        try:
            board_code = self._get_board_code(sector)
            if not board_code:
                return None

            # 获取足够的 K 线数据（event_date 前后各若干天）
            cal_days = (end_date - start_date).days + 5
            trading_days = min(cal_days * 2, 60)
            df = self._fetch_board_kline(board_code, trading_days)
            if df is None or df.empty:
                return None

            # 按日期筛选 [start_date, end_date]
            df = self._filter_kline_by_date_range(df, start_date, end_date)
            if df is None or len(df) < 2:
                return None

            start_price = float(df.iloc[0]["open"])
            end_price = float(df.iloc[-1]["close"])
            if start_price <= 0:
                return None

            return ((end_price - start_price) / start_price) * 100

        except Exception as exc:
            logger.debug(f"[P6兑现] 计算 {sector} 收益失败: {exc}")
            return None

    def _compute_multi_window_returns(
        self, sector: str, event_date: date,
    ) -> Dict:
        """计算板块 T+1/3/5 多窗口收益

        获取事件日及之后 ~15 个交易日的 K 线，以事件日收盘价为基准，
        分别计算 T+1、T+3、T+5 个交易日的收益率。

        Args:
            sector: 板块名称（用于查找 TDX 代码）
            event_date: 事件日期（基准日）

        Returns:
            {
                "day_1": float | None,   # T+1 收益率 (%)
                "day_3": float | None,   # T+3 收益率 (%)
                "day_5": float | None,   # T+5 收益率 (%)
                "max_window": int | None, # 最大收益出现在第几天 (1/3/5)
                "max_pct": float | None,  # 最大收益率 (%)
            }
        """
        result: Dict = {
            "day_1": None, "day_3": None, "day_5": None,
            "max_window": None, "max_pct": None,
        }

        try:
            board_code = self._get_board_code(sector)
            if not board_code:
                return result

            # 获取 ~60 个交易日的 K 线（覆盖事件日 + 后续窗口，留足余量防僵尸记录）
            df = self._fetch_board_kline(board_code, 60)
            if df is None or df.empty:
                return result

            # 找到 event_date 在 K 线中的位置
            base_idx = self._find_event_date_index(df, event_date)
            if base_idx is None:
                logger.debug(f"[P6兑现] {sector} K线中未找到 {event_date}")
                return result

            base_price = float(df.iloc[base_idx]["close"])
            if base_price <= 0:
                return result

            # 计算各窗口收益
            window_returns: Dict[int, float] = {}
            for trading_offset in (1, 3, 5):
                target_idx = base_idx + trading_offset
                if target_idx >= len(df):
                    continue
                target_price = float(df.iloc[target_idx]["close"])
                ret = ((target_price - base_price) / base_price) * 100
                window_returns[trading_offset] = round(ret, 4)

            result["day_1"] = window_returns.get(1)
            result["day_3"] = window_returns.get(3)
            result["day_5"] = window_returns.get(5)

            # 计算最大收益窗口
            if window_returns:
                best_window = max(window_returns, key=lambda k: abs(window_returns[k]))
                result["max_window"] = best_window
                result["max_pct"] = round(window_returns[best_window], 4)

        except Exception as exc:
            logger.debug(f"[P6兑现] {sector} 多窗口收益计算失败: {exc}")

        return result

    def _determine_pead_direction(
        self, expected_direction: str, window_returns: Dict,
    ) -> str:
        """PEAD 漂移方向判定

        比较多窗口实际收益与预期方向，返回漂移方向标签。

        Args:
            expected_direction: positive / negative / binary
            window_returns: {"day_1": float|None, "day_3": float|None, "day_5": float|None}

        Returns:
            "aligned"  — 多数窗口方向与预期一致
            "counter"  — 多数窗口方向与预期相反
            "neutral"  — 无法判定（数据不足或方向混乱）
        """
        available = [v for v in (
            window_returns.get("day_1"),
            window_returns.get("day_3"),
            window_returns.get("day_5"),
        ) if v is not None]

        if not available:
            return "neutral"

        if expected_direction == "positive":
            aligned_count = sum(1 for r in available if r > 0)
        elif expected_direction == "negative":
            aligned_count = sum(1 for r in available if r < 0)
        elif expected_direction == "binary":
            # 二元事件：大幅波动（|r|>1%）视为 aligned
            aligned_count = sum(1 for r in available if abs(r) > 1.0)
        else:
            return "neutral"

        majority = aligned_count > len(available) / 2
        minority = aligned_count == 0

        if majority:
            return "aligned"
        elif minority:
            return "counter"
        return "neutral"

    # -----------------------------------------------------------------
    # K 线数据辅助
    # -----------------------------------------------------------------

    def _fetch_board_kline(self, board_code: str, days: int = 15):
        """获取板块 K 线数据（通过 DataFetcherManager）"""
        try:
            from data_provider.base import DataFetcherManager
            fetcher = DataFetcherManager()
            return fetcher.get_board_kline(board_code=board_code, days=days)
        except Exception as exc:
            logger.debug(f"[P6兑现] 获取板块 {board_code} K线失败: {exc}")
            return None

    @staticmethod
    def _filter_kline_by_date_range(df, start: date, end: date):
        """按日期范围筛选 K 线 DataFrame"""
        if "date" not in df.columns:
            return df
        try:
            mask = (df["date"] >= start.strftime("%Y-%m-%d")) & (
                df["date"] <= end.strftime("%Y-%m-%d")
            )
            filtered = df.loc[mask]
            return filtered if not filtered.empty else None
        except Exception:
            return None

    @staticmethod
    def _find_event_date_index(df, event_date: date) -> Optional[int]:
        """在 K 线 DataFrame 中查找 event_date 对应的行索引

        支持 date 列为字符串（YYYY-MM-DD）或 datetime 类型。
        如果精确匹配失败，尝试回退到最近的交易日（前后 3 天范围内）。
        """
        if "date" not in df.columns:
            return None

        target_str = event_date.strftime("%Y-%m-%d")

        # 精确匹配
        for idx in range(len(df)):
            date_val = df.iloc[idx]["date"]
            if isinstance(date_val, str) and date_val == target_str:
                return idx
            if hasattr(date_val, "strftime") and date_val.strftime("%Y-%m-%d") == target_str:
                return idx

        # 回退：找最近交易日（前后 3 天）
        for delta in range(1, 4):
            for sign in (1, -1):
                alt_date = event_date + timedelta(days=sign * delta)
                alt_str = alt_date.strftime("%Y-%m-%d")
                for idx in range(len(df)):
                    date_val = df.iloc[idx]["date"]
                    date_str = date_val if isinstance(date_val, str) else (
                        date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else ""
                    )
                    if date_str == alt_str:
                        return idx

        return None

    def _get_board_code(self, sector_name: str) -> Optional[str]:
        """板块名 → TDX 代码映射（从 TDX_BOARD_REGISTRY 查找）"""
        try:
            from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY
            return TDX_BOARD_REGISTRY.get(sector_name)
        except (ImportError, AttributeError):
            return None

    def _check_direction(self, expected_direction: str, actual_return: float) -> bool:
        """判断漂移方向预判是否正确（旧版单窗口兼容）"""
        if expected_direction == "positive":
            return actual_return > 0
        elif expected_direction == "negative":
            return actual_return < 0
        elif expected_direction == "binary":
            return abs(actual_return) > 2.0
        return False

    def _group_stats(self, session, cutoff: date, group_field: str) -> Dict:
        """按指定字段分组统计兑现率"""
        records = (
            session.query(CatalystRealizationRecord)
            .filter(CatalystRealizationRecord.event_date >= cutoff)
            .all()
        )

        groups: Dict[str, Dict] = {}
        for r in records:
            key = getattr(r, group_field, "unknown")
            if key not in groups:
                groups[key] = {"total": 0, "realized": 0, "returns": []}
            groups[key]["total"] += 1
            if r.is_realized:
                groups[key]["realized"] += 1
                if r.actual_return is not None:
                    groups[key]["returns"].append(r.actual_return)

        # 计算统计量
        result = {}
        for key, data in groups.items():
            result[key] = {
                "total": data["total"],
                "realized": data["realized"],
                "rate": data["realized"] / max(data["total"], 1),
                "avg_return": (
                    sum(data["returns"]) / len(data["returns"])
                    if data["returns"] else 0
                ),
            }

        return result
