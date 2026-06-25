# -*- coding: utf-8 -*-
"""
===================================
选股结果数据访问层
===================================

职责：
1. 封装 ScreenerResultRecord 表的数据库操作
2. 提供选股结果的增删改查接口
3. 支持按日期/评分/股票等维度查询
"""

import logging
from datetime import date, datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import and_, select, delete, desc, func

from src.storage import DatabaseManager, ScreenerResultRecord

logger = logging.getLogger(__name__)


class ScreenerRepository:
    """
    选股结果数据访问层

    封装 ScreenerResultRecord 表的数据库操作
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_result(
        self,
        run_date: str,
        stock_code: str,
        stock_name: str = "",
        composite_score: float = 0.0,
        sector: str = "",
        sector_strength: float = 0.0,
        forum_heat: float = 0.0,
        strategy_score: float = 0.0,
        ai_reasoning: str = "",
        recommendation: str = "watch",
        risk_tags: Optional[List[str]] = None,
        matched_strategies: Optional[List[str]] = None,
    ) -> bool:
        """
        保存单条选股结果（upsert）

        Args:
            run_date: 选股日期 YYYY-MM-DD
            stock_code: 股票代码
            stock_name: 股票名称
            composite_score: 综合评分 0-100
            sector: 所属板块
            sector_strength: 板块强度分
            forum_heat: 论坛热度分
            strategy_score: 策略评分
            ai_reasoning: AI 推荐理由
            recommendation: 推荐级别 strong_buy / buy / watch
            risk_tags: 风险标签列表
            matched_strategies: 匹配策略列表

        Returns:
            True 表示成功
        """
        import json

        try:
            with self.db.get_session() as session:
                stmt = select(ScreenerResultRecord).where(
                    and_(
                        ScreenerResultRecord.run_date == run_date,
                        ScreenerResultRecord.stock_code == stock_code,
                    )
                )
                existing = session.execute(stmt).scalar_one_or_none()

                if existing:
                    existing.stock_name = stock_name
                    existing.composite_score = composite_score
                    existing.sector = sector
                    existing.sector_strength = sector_strength
                    existing.forum_heat = forum_heat
                    existing.strategy_score = strategy_score
                    existing.ai_reasoning = ai_reasoning
                    existing.recommendation = recommendation
                    existing.risk_tags_json = json.dumps(risk_tags or [], ensure_ascii=False)
                    existing.matched_strategies_json = json.dumps(matched_strategies or [], ensure_ascii=False)
                else:
                    record = ScreenerResultRecord(
                        run_date=run_date,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        composite_score=composite_score,
                        sector=sector,
                        sector_strength=sector_strength,
                        forum_heat=forum_heat,
                        strategy_score=strategy_score,
                        ai_reasoning=ai_reasoning,
                        recommendation=recommendation,
                        risk_tags_json=json.dumps(risk_tags or [], ensure_ascii=False),
                        matched_strategies_json=json.dumps(matched_strategies or [], ensure_ascii=False),
                    )
                    session.add(record)

                session.commit()
                return True

        except Exception as e:
            logger.error(f"保存选股结果失败 {stock_code} {run_date}: {e}")
            return False

    def batch_save_results(self, results: List[Dict[str, Any]], run_date: str) -> int:
        """
        批量保存选股结果

        Args:
            results: 选股结果列表
            run_date: 选股日期

        Returns:
            成功保存的记录数
        """
        saved_count = 0
        for r in results:
            if self.save_result(
                run_date=run_date,
                stock_code=r.get("code", ""),
                stock_name=r.get("name", ""),
                composite_score=r.get("score", 0.0),
                sector=r.get("sector_name", ""),
                sector_strength=r.get("sector_score", 0.0),
                forum_heat=r.get("heat_score", 0.0),
                strategy_score=r.get("strategy_score", 0.0),
                ai_reasoning=r.get("ai_reasoning", ""),
                recommendation=r.get("recommendation", "watch"),
                risk_tags=r.get("risk_tags", []),
                matched_strategies=r.get("matched_strategies", []),
            ):
                saved_count += 1
        return saved_count

    def get_results_by_date(
        self,
        run_date: str,
        min_score: Optional[float] = None,
        limit: int = 50,
    ) -> List[ScreenerResultRecord]:
        """
        获取指定日期的选股结果

        Args:
            run_date: 选股日期
            min_score: 最低评分过滤
            limit: 返回数量限制

        Returns:
            ScreenerResultRecord 列表（按评分降序）
        """
        try:
            with self.db.get_session() as session:
                stmt = select(ScreenerResultRecord).where(
                    ScreenerResultRecord.run_date == run_date
                )
                if min_score is not None:
                    stmt = stmt.where(ScreenerResultRecord.composite_score >= min_score)
                stmt = stmt.order_by(desc(ScreenerResultRecord.composite_score)).limit(limit)
                return list(session.execute(stmt).scalars().all())
        except Exception as e:
            logger.error(f"查询选股结果失败 {run_date}: {e}")
            return []

    def get_latest_results(self, limit: int = 20) -> List[ScreenerResultRecord]:
        """
        获取最新一次选股的结果

        Args:
            limit: 返回数量限制

        Returns:
            ScreenerResultRecord 列表
        """
        try:
            with self.db.get_session() as session:
                # 先获取最新日期
                latest_date_stmt = (
                    select(ScreenerResultRecord.run_date)
                    .order_by(desc(ScreenerResultRecord.run_date))
                    .limit(1)
                )
                latest_date = session.execute(latest_date_stmt).scalar_one_or_none()

                if not latest_date:
                    return []

                stmt = (
                    select(ScreenerResultRecord)
                    .where(ScreenerResultRecord.run_date == latest_date)
                    .order_by(desc(ScreenerResultRecord.composite_score))
                    .limit(limit)
                )
                return list(session.execute(stmt).scalars().all())
        except Exception as e:
            logger.error(f"查询最新选股结果失败: {e}")
            return []

    def get_stock_history(
        self,
        stock_code: str,
        days: int = 30,
    ) -> List[ScreenerResultRecord]:
        """
        获取指定股票的历史入选记录

        Args:
            stock_code: 股票代码
            days: 查询天数

        Returns:
            ScreenerResultRecord 列表（按日期降序）
        """
        try:
            with self.db.get_session() as session:
                cutoff = date.today().isoformat()
                stmt = (
                    select(ScreenerResultRecord)
                    .where(ScreenerResultRecord.stock_code == stock_code)
                    .order_by(desc(ScreenerResultRecord.run_date))
                    .limit(days)
                )
                return list(session.execute(stmt).scalars().all())
        except Exception as e:
            logger.error(f"查询股票历史入选记录失败 {stock_code}: {e}")
            return []

    def get_recommendation_stats(self, run_date: str) -> Dict[str, int]:
        """
        获取指定日期的推荐级别统计

        Args:
            run_date: 选股日期

        Returns:
            {"strong_buy": N, "buy": N, "watch": N}
        """
        try:
            with self.db.get_session() as session:
                stmt = (
                    select(
                        ScreenerResultRecord.recommendation,
                        func.count(ScreenerResultRecord.id),
                    )
                    .where(ScreenerResultRecord.run_date == run_date)
                    .group_by(ScreenerResultRecord.recommendation)
                )
                rows = session.execute(stmt).all()
                return {row[0]: row[1] for row in rows}
        except Exception as e:
            logger.error(f"查询推荐统计失败 {run_date}: {e}")
            return {}

    def delete_old_results(self, before_date: str) -> int:
        """
        删除指定日期之前的选股结果（数据清理）

        Args:
            before_date: 删除此日期之前的数据 YYYY-MM-DD

        Returns:
            删除的记录数
        """
        try:
            with self.db.get_session() as session:
                stmt = delete(ScreenerResultRecord).where(
                    ScreenerResultRecord.run_date < before_date
                )
                result = session.execute(stmt)
                session.commit()
                deleted = result.rowcount
                logger.info(f"清理选股结果: 删除 {deleted} 条 {before_date} 之前的记录")
                return deleted
        except Exception as e:
            logger.error(f"清理选股结果失败: {e}")
            return 0
