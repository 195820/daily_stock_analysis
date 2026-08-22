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
        """检测过期事件并记录兑现结果

        逻辑：
        1. 加载所有未兑现事件（is_realized=False 且 event_date < target_date）
        2. 对每个事件，计算板块事件后收益（event_date → target_date 的涨幅）
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
                    actual_return = self._compute_sector_return(
                        record.sector, record.event_date, target_date
                    )

                    if actual_return is None:
                        logger.debug(f"[P6兑现] {record.event_id} 无法计算收益，跳过")
                        continue

                    # 判断漂移方向
                    direction_correct = self._check_direction(
                        record.direction, actual_return
                    )

                    # 计算故事寿命
                    story_lifetime = (target_date - record.event_date).days

                    # 更新记录
                    record.is_realized = True
                    record.realization_date = target_date
                    record.actual_return = actual_return
                    record.drift_direction_correct = direction_correct
                    record.story_lifetime_days = story_lifetime

                    realized_count += 1
                    logger.debug(
                        f"[P6兑现] {record.event_id}: "
                        f"return={actual_return:.2f}%, correct={direction_correct}"
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

    # =================================================================
    # 内部方法
    # =================================================================

    def _compute_sector_return(
        self, sector: str, start_date: date, end_date: date
    ) -> Optional[float]:
        """计算板块事件后收益

        使用 mootdx TCP 获取板块 K 线，计算 start_date → end_date 的涨幅。
        降级使用 akshare。

        Returns:
            float: 涨幅百分比，或 None（数据不可用）
        """
        try:
            from data_provider.base import DataFetcherManager
            fetcher = DataFetcherManager()

            # 尝试获取板块 K 线
            # 板块代码映射：从 CONCEPT_UNIVERSE 或 TDX_BOARD_REGISTRY 查找
            board_code = self._get_board_code(sector)
            if not board_code:
                return None

            df = fetcher.get_board_kline(
                symbol=board_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
            )

            if df is None or df.empty or len(df) < 2:
                return None

            # 取首日开盘价和末日收盘价
            start_price = df.iloc[0]["open"]
            end_price = df.iloc[-1]["close"]

            if start_price <= 0:
                return None

            return ((end_price - start_price) / start_price) * 100

        except Exception as exc:
            logger.debug(f"[P6兑现] 计算 {sector} 收益失败: {exc}")
            return None

    def _get_board_code(self, sector_name: str) -> Optional[str]:
        """板块名 → TDX 代码映射

        从 TDX_BOARD_REGISTRY 查找。
        """
        try:
            from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY
            return TDX_BOARD_REGISTRY.get(sector_name)
        except (ImportError, AttributeError):
            return None

    def _check_direction(self, expected_direction: str, actual_return: float) -> bool:
        """判断漂移方向预判是否正确

        Args:
            expected_direction: positive / negative / binary
            actual_return: 实际涨幅 (%)

        Returns:
            bool: 方向是否正确
        """
        if expected_direction == "positive":
            return actual_return > 0
        elif expected_direction == "negative":
            return actual_return < 0
        elif expected_direction == "binary":
            # 二元事件：大幅波动（无论方向）算正确
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
