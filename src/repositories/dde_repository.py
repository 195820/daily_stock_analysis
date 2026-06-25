# -*- coding: utf-8 -*-
"""
===================================
DDE 数据访问层
===================================

职责：
1. 封装 StockDdeRecord 表的数据库操作
2. 提供 DDE 数据的增删改查接口
"""

import logging
from datetime import date, datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import and_, select, delete

from src.storage import DatabaseManager, StockDdeRecord

logger = logging.getLogger(__name__)


class DdeRepository:
    """
    DDE 数据访问层

    封装 StockDdeRecord 表的数据库操作
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_dde_data(
        self,
        code: str,
        trade_date: date,
        dde_big_order: float,
        dde_big_order_buy: float = 0.0,
        dde_big_order_sell: float = 0.0,
        dde_mid_order: float = 0.0,
        dde_small_order: float = 0.0,
    ) -> bool:
        """
        保存 DDE 数据（upsert）

        Args:
            code: 股票代码
            trade_date: 交易日期
            dde_big_order: DDE大单净量(%)
            dde_big_order_buy: DDE大单买入(%)
            dde_big_order_sell: DDE大单卖出(%)
            dde_mid_order: DDE中单净量(%)
            dde_small_order: DDE小单净量(%)

        Returns:
            True 表示成功
        """
        try:
            with self.db.get_session() as session:
                # 检查是否已存在
                stmt = select(StockDdeRecord).where(
                    and_(
                        StockDdeRecord.code == code,
                        StockDdeRecord.date == trade_date,
                    )
                )
                existing = session.execute(stmt).scalar_one_or_none()

                if existing:
                    # 更新
                    existing.dde_big_order = dde_big_order
                    existing.dde_big_order_buy = dde_big_order_buy
                    existing.dde_big_order_sell = dde_big_order_sell
                    existing.dde_mid_order = dde_mid_order
                    existing.dde_small_order = dde_small_order
                else:
                    # 插入
                    record = StockDdeRecord(
                        code=code,
                        date=trade_date,
                        dde_big_order=dde_big_order,
                        dde_big_order_buy=dde_big_order_buy,
                        dde_big_order_sell=dde_big_order_sell,
                        dde_mid_order=dde_mid_order,
                        dde_small_order=dde_small_order,
                    )
                    session.add(record)

                session.commit()
                return True

        except Exception as e:
            logger.error(f"保存 DDE 数据失败 {code} {trade_date}: {e}")
            return False

    def batch_save_dde(self, dde_records: List[Dict[str, Any]], trade_date: date) -> int:
        """
        批量保存 DDE 数据

        Args:
            dde_records: DDE 数据列表 [{code, dde_big_order, ...}, ...]
            trade_date: 交易日期

        Returns:
            成功保存的记录数
        """
        saved_count = 0
        for record in dde_records:
            if self.save_dde_data(
                code=record['code'],
                trade_date=trade_date,
                dde_big_order=record.get('dde_big_order', 0.0),
                dde_big_order_buy=record.get('dde_big_order_buy', 0.0),
                dde_big_order_sell=record.get('dde_big_order_sell', 0.0),
                dde_mid_order=record.get('dde_mid_order', 0.0),
                dde_small_order=record.get('dde_small_order', 0.0),
            ):
                saved_count += 1
        return saved_count

    def get_dde_by_date(self, trade_date: date) -> List[StockDdeRecord]:
        """
        获取指定日期的所有 DDE 数据

        Args:
            trade_date: 交易日期

        Returns:
            StockDdeRecord 列表
        """
        try:
            with self.db.get_session() as session:
                stmt = select(StockDdeRecord).where(StockDdeRecord.date == trade_date)
                return list(session.execute(stmt).scalars().all())
        except Exception as e:
            logger.error(f"查询 DDE 数据失败 {trade_date}: {e}")
            return []

    def get_dde_for_stock(self, code: str, days: int = 5) -> List[StockDdeRecord]:
        """
        获取指定股票最近 N 天的 DDE 数据

        Args:
            code: 股票代码
            days: 天数

        Returns:
            StockDdeRecord 列表（按日期降序）
        """
        try:
            with self.db.get_session() as session:
                stmt = (
                    select(StockDdeRecord)
                    .where(StockDdeRecord.code == code)
                    .order_by(StockDdeRecord.date.desc())
                    .limit(days)
                )
                return list(session.execute(stmt).scalars().all())
        except Exception as e:
            logger.error(f"查询股票 DDE 数据失败 {code}: {e}")
            return []

    def get_dde_range_avg(self, code: str, days: int = 2) -> Optional[float]:
        """
        获取指定股票近 N 日 DDE 平均值

        Args:
            code: 股票代码
            days: 区间天数

        Returns:
            DDE 平均值，或 None（数据不足时）
        """
        records = self.get_dde_for_stock(code, days)
        if len(records) < days:
            return None

        total = sum(r.dde_big_order for r in records)
        return total / len(records)

    def delete_old_dde(self, before_date: date) -> int:
        """
        删除指定日期之前的 DDE 数据（数据清理）

        Args:
            before_date: 删除此日期之前的数据

        Returns:
            删除的记录数
        """
        try:
            with self.db.get_session() as session:
                stmt = delete(StockDdeRecord).where(StockDdeRecord.date < before_date)
                result = session.execute(stmt)
                session.commit()
                deleted = result.rowcount
                logger.info(f"清理 DDE 数据: 删除 {deleted} 条 {before_date} 之前的记录")
                return deleted
        except Exception as e:
            logger.error(f"清理 DDE 数据失败: {e}")
            return 0
