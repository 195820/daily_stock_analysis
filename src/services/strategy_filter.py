# -*- coding: utf-8 -*-
"""
===================================
策略量化筛选引擎
===================================

职责：
1. 加载 YAML 策略文件（含 screener_conditions）
2. 获取全市场数据（行情 + DDE）
3. 按条件评估每只股票
4. 输出满足条件的候选股票列表

条件评估支持：
- 字段：dde_big_order, low_ratio, dde_range 等
- 运算符：gt, gte, lt, lte, eq, between
- 权重加权评分
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from src.config import Config
from src.services.dde_fetcher import DdeFetcher, DdeSnapshot
from src.repositories.dde_repository import DdeRepository
from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ScreenerCondition:
    """单个筛选条件"""
    field: str                 # 字段名
    operator: str              # 运算符: gt, gte, lt, lte, eq, between
    value: Any                 # 阈值
    weight: float = 1.0        # 权重
    offset: int = 0            # 日期偏移（0=今日, 1=昨日）
    range_days: int = 0        # 区间天数（用于 dde_range）
    description: str = ""      # 条件描述


@dataclass
class ConditionResult:
    """条件评估结果"""
    condition: ScreenerCondition
    passed: bool
    actual_value: Any
    threshold: Any


@dataclass
class StrategyMatchResult:
    """策略匹配结果"""
    stock_code: str
    stock_name: str
    strategy_name: str
    score: float                           # 加权得分 0-100
    conditions: List[ConditionResult] = field(default_factory=list)
    matched: bool = False                  # 是否满足所有条件
    dde_big_order: float = 0.0             # DDE大单净量
    low_ratio: float = 0.0                 # 最低价比率
    dde_range: float = 0.0                 # 区间DDE


@dataclass
class StrategyDefinition:
    """策略定义（从 YAML 加载）"""
    name: str
    display_name: str
    description: str
    category: str
    conditions: List[ScreenerCondition] = field(default_factory=list)


# ============================================================
# 运算符
# ============================================================

OPERATORS: Dict[str, Callable[[Any, Any], bool]] = {
    'gt': lambda a, b: a > b,
    'gte': lambda a, b: a >= b,
    'lt': lambda a, b: a < b,
    'lte': lambda a, b: a <= b,
    'eq': lambda a, b: abs(a - b) < 1e-9,
    'between': lambda a, b: b[0] <= a <= b[1] if isinstance(b, (list, tuple)) and len(b) == 2 else False,
}


# ============================================================
# 策略筛选服务
# ============================================================

class StrategyFilterService:
    """
    策略量化筛选引擎

    核心流程：
    1. 加载策略 YAML 文件
    2. 获取全市场 DDE 数据（实时）
    3. 获取全市场股票行情（复用缓存）
    4. 对每只股票评估条件
    5. 返回满足条件的股票列表（按得分排序）
    """

    def __init__(self, config: Config):
        self.config = config
        self.dde_fetcher = DdeFetcher(config)
        self.dde_repo = DdeRepository()
        self.stock_repo = StockRepository()
        self._strategies: Dict[str, StrategyDefinition] = {}

    def load_strategy(self, yaml_path: str) -> StrategyDefinition:
        """
        加载策略 YAML 文件

        Args:
            yaml_path: 策略文件路径

        Returns:
            StrategyDefinition 对象
        """
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"策略文件不存在: {yaml_path}")

        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        # 解析条件
        conditions = []
        for cond_data in data.get('screener_conditions', []):
            cond = ScreenerCondition(
                field=cond_data['field'],
                operator=cond_data['operator'],
                value=cond_data['value'],
                weight=cond_data.get('weight', 1.0),
                offset=cond_data.get('offset', 0),
                range_days=cond_data.get('range_days', 0),
                description=cond_data.get('description', ''),
            )
            conditions.append(cond)

        strategy = StrategyDefinition(
            name=data['name'],
            display_name=data.get('display_name', data['name']),
            description=data.get('description', ''),
            category=data.get('category', 'screener'),
            conditions=conditions,
        )

        self._strategies[strategy.name] = strategy
        logger.info(f"[策略加载] {strategy.name}: {len(conditions)} 个条件")
        return strategy

    def load_custom_strategies(self, strategies_dir: str = "strategies/custom") -> List[StrategyDefinition]:
        """
        加载自定义策略目录下的所有策略

        Args:
            strategies_dir: 策略目录路径

        Returns:
            加载的策略列表
        """
        loaded = []
        dir_path = Path(strategies_dir)

        if not dir_path.exists():
            logger.warning(f"[策略加载] 目录不存在: {strategies_dir}")
            return loaded

        for yaml_file in dir_path.glob("*.yaml"):
            try:
                strategy = self.load_strategy(str(yaml_file))
                loaded.append(strategy)
            except Exception as e:
                logger.error(f"[策略加载] 加载 {yaml_file} 失败: {e}")

        for yaml_file in dir_path.glob("*.yml"):
            try:
                strategy = self.load_strategy(str(yaml_file))
                loaded.append(strategy)
            except Exception as e:
                logger.error(f"[策略加载] 加载 {yaml_file} 失败: {e}")

        logger.info(f"[策略加载] 共加载 {len(loaded)} 个自定义策略")
        return loaded

    def scan_market(
        self,
        strategy_name: str,
        min_score: float = 0.0,
    ) -> Tuple[List[StrategyMatchResult], int]:
        """
        全市场扫描

        Args:
            strategy_name: 策略名称（需先 load_strategy）
            min_score: 最低得分阈值

        Returns:
            (满足条件的股票列表, 扫描总数)，按得分降序
        """
        strategy = self._strategies.get(strategy_name)
        if strategy is None:
            logger.error(f"[扫描] 策略未加载: {strategy_name}")
            return [], 0

        logger.info(f"[扫描] 开始全市场扫描，策略: {strategy.name}, 条件数: {len(strategy.conditions)}")

        # 1. 获取全市场 DDE 数据
        dde_result = self.dde_fetcher.fetch_market_dde(use_cache=True)
        if dde_result.error:
            logger.error(f"[扫描] DDE 获取失败: {dde_result.error}")
            return [], 0

        total_scanned = len(dde_result.snapshots)
        logger.info(f"[扫描] 获取 DDE 数据: {total_scanned} 只股票")

        # 2. 对每只股票评估条件
        results: List[StrategyMatchResult] = []

        for code, dde_snapshot in dde_result.snapshots.items():
            try:
                match_result = self._evaluate_stock(code, dde_snapshot, strategy)
                if match_result and match_result.score >= min_score:
                    results.append(match_result)
            except Exception as e:
                logger.debug(f"[扫描] 评估 {code} 失败: {e}")
                continue

        # 3. 按得分排序
        results.sort(key=lambda r: r.score, reverse=True)

        logger.info(f"[扫描] 完成，扫描 {total_scanned} 只，满足条件: {len(results)} 只")
        return results, total_scanned

    def _evaluate_stock(
        self,
        stock_code: str,
        dde_snapshot: DdeSnapshot,
        strategy: StrategyDefinition,
    ) -> Optional[StrategyMatchResult]:
        """
        评估单只股票是否满足策略条件

        Args:
            stock_code: 股票代码
            dde_snapshot: DDE 快照
            strategy: 策略定义

        Returns:
            StrategyMatchResult 或 None
        """
        results: List[ConditionResult] = []
        total_weight = 0.0
        weighted_score = 0.0
        all_passed = True

        for condition in strategy.conditions:
            cond_result = self._evaluate_condition(stock_code, dde_snapshot, condition)
            results.append(cond_result)

            total_weight += condition.weight
            if cond_result.passed:
                weighted_score += condition.weight
            else:
                # 如果是硬性条件（某些字段必须满足），可以设置 all_passed = False
                # 当前实现：所有条件都是加分项，最终按总分筛选
                pass

        # 计算得分（0-100）
        score = (weighted_score / total_weight * 100) if total_weight > 0 else 0

        # 判断是否全部满足
        all_passed = all(r.passed for r in results)

        return StrategyMatchResult(
            stock_code=stock_code,
            stock_name=dde_snapshot.name,
            strategy_name=strategy.name,
            score=score,
            conditions=results,
            matched=all_passed,
            dde_big_order=dde_snapshot.dde_big_order,
            low_ratio=self._calc_low_ratio(stock_code),
            dde_range=self._calc_dde_range(stock_code),
        )

    def _evaluate_condition(
        self,
        stock_code: str,
        dde_snapshot: DdeSnapshot,
        condition: ScreenerCondition,
    ) -> ConditionResult:
        """评估单个条件"""
        field_name = condition.field
        operator_name = condition.operator
        threshold = condition.value

        # 获取字段值
        actual_value = self._get_field_value(stock_code, dde_snapshot, condition)

        # 评估条件
        op_func = OPERATORS.get(operator_name)
        if op_func is None:
            logger.warning(f"[评估] 未知运算符: {operator_name}")
            return ConditionResult(condition, False, actual_value, threshold)

        if actual_value is None:
            return ConditionResult(condition, False, None, threshold)

        passed = op_func(actual_value, threshold)
        return ConditionResult(condition, passed, actual_value, threshold)

    def _get_field_value(
        self,
        stock_code: str,
        dde_snapshot: DdeSnapshot,
        condition: ScreenerCondition,
    ) -> Optional[float]:
        """获取条件字段的值"""
        field_name = condition.field

        # DDE 相关字段
        if field_name == 'dde_big_order':
            return dde_snapshot.dde_big_order
        elif field_name == 'dde_big_order_buy':
            return dde_snapshot.dde_big_order_buy
        elif field_name == 'dde_big_order_sell':
            return dde_snapshot.dde_big_order_sell
        elif field_name == 'dde_mid_order':
            return dde_snapshot.dde_mid_order
        elif field_name == 'dde_small_order':
            return dde_snapshot.dde_small_order

        # 计算字段：最低价比率
        elif field_name == 'low_ratio':
            return self._calc_low_ratio(stock_code)

        # 计算字段：区间 DDE
        elif field_name == 'dde_range' or field_name == 'dde_range_2d':
            return self._calc_dde_range(stock_code)

        else:
            logger.debug(f"[字段] 未知字段: {field_name}")
            return None

    def _calc_low_ratio(self, stock_code: str) -> float:
        """
        计算最低价比率: 今日最低价 / 昨日最低价

        直接从 akshare 获取历史行情数据。

        Returns:
            比率值，数据不足时返回 0
        """
        try:
            import akshare as ak
            from datetime import datetime, timedelta

            # 计算日期范围（最近5天）
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')

            # 获取历史行情
            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period='daily',
                start_date=start_date,
                end_date=end_date,
                adjust='qfq'
            )

            if df is None or len(df) < 2:
                return 0.0

            # 取最近两天（按日期降序排列后取前两条）
            df = df.sort_values('日期', ascending=False)
            today_low = float(df.iloc[0]['最低'])
            yesterday_low = float(df.iloc[1]['最低'])

            if yesterday_low > 0:
                ratio = today_low / yesterday_low
                logger.debug(f"[low_ratio] {stock_code}: {today_low}/{yesterday_low} = {ratio:.4f}")
                return ratio
            return 0.0

        except Exception as e:
            logger.debug(f"[计算] low_ratio 失败 {stock_code}: {e}")
            return 0.0

    def _calc_dde_range(self, stock_code: str, days: int = 2) -> float:
        """
        计算区间 DDE 平均值: 近 N 日 DDE 平均

        当前实现：使用当日 DDE（实时数据）
        未来可扩展：从数据库读取历史 DDE 计算

        Returns:
            区间 DDE 平均值
        """
        # 优先从数据库读取历史数据
        try:
            avg = self.dde_repo.get_dde_range_avg(stock_code, days)
            if avg is not None:
                return avg
        except Exception:
            pass

        # 兜底：使用当日 DDE
        snapshot = self.dde_fetcher.get_stock_dde(stock_code, use_cache=True)
        if snapshot:
            return snapshot.dde_big_order
        return 0.0

    def get_strategy(self, name: str) -> Optional[StrategyDefinition]:
        """获取已加载的策略"""
        return self._strategies.get(name)

    def list_strategies(self) -> List[str]:
        """列出已加载的策略名称"""
        return list(self._strategies.keys())
