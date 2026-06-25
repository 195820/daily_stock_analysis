# -*- coding: utf-8 -*-
"""
===================================
风险评估服务
===================================

职责：
1. 评估个股风险（估值分位、波动率、超买信号）
2. 输出风险评分（0-100，越高越危险）+ 风险标签

数据源：
- PE/PB: akshare stock_a_indicator_lg
- 波动率: 复用 TechnicalSignals 的 K 线数据（20日收益率标准差）
- 超买信号: 复用 TechnicalSignals 的 RSI/布林带
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from src.config import Config

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class RiskAssessment:
    """单只股票的风险评估"""
    code: str
    risk_score: float = 0.0               # 0-100, 越高越危险
    risk_tags: List[str] = field(default_factory=list)
    pe_ratio: float = 0.0                 # 市盈率
    volatility_20d: float = 0.0           # 20日年化波动率
    valuation_score: float = 0.0          # 估值风险分 0-100
    volatility_score: float = 0.0         # 波动风险分 0-100
    overbought_score: float = 0.0         # 超买风险分 0-100


# ============================================================
# PE 估值分档
# ============================================================

# PE 绝对值 → 风险分（越高越贵越危险）
_PE_RISK_BANDS = [
    # (上限, 风险分)
    (0, 70),      # 亏损股，高风险
    (10, 20),     # 低估值
    (30, 40),     # 合理偏低
    (60, 55),     # 合理
    (100, 70),    # 偏高
    (200, 85),    # 高估值
    (float('inf'), 95),  # 极高估值
]

# 波动率年化值 → 风险分
_VOL_RISK_BANDS = [
    (0.15, 10),   # 低波动
    (0.30, 30),   # 正常
    (0.45, 50),   # 中等
    (0.60, 70),   # 偏高
    (0.80, 85),   # 高波动
    (float('inf'), 95),
]


def _band_score(bands: list, value: float) -> float:
    """根据分档表返回风险分"""
    for threshold, score in bands:
        if value < threshold:
            return score
    return bands[-1][1]


# ============================================================
# 风险评估服务
# ============================================================

class RiskAssessmentService:
    """
    风险评估服务

    使用方式：
        service = RiskAssessmentService(config)
        assessments = service.assess(codes, technical_signals=signals_map)
    """

    def __init__(self, config: Config):
        self.config = config
        self._pe_cache: Dict[str, float] = {}
        self._pe_cache_lock = threading.Lock()
        self._pe_cache_ts: float = 0.0
        self._pe_cache_ttl = 3600  # PE 缓存 1 小时

    def assess(
        self,
        codes: List[str],
        *,
        technical_signals: Optional[Dict] = None,
    ) -> Dict[str, RiskAssessment]:
        """
        批量评估风险

        Args:
            codes: 股票代码列表
            technical_signals: {code: TechnicalSignals} 复用已有技术指标

        Returns:
            {code: RiskAssessment}
        """
        result: Dict[str, RiskAssessment] = {}

        # 1. 批量获取 PE 数据
        pe_map = self._fetch_pe_batch(codes)

        # 2. 逐只评估
        for code in codes:
            assessment = RiskAssessment(code=code)

            # 估值风险
            pe = pe_map.get(code, 0.0)
            assessment.pe_ratio = pe
            assessment.valuation_score = _band_score(_PE_RISK_BANDS, pe) if pe > 0 else 50.0
            if pe <= 0:
                assessment.risk_tags.append("亏损股")
            elif pe > 100:
                assessment.risk_tags.append("高估值")

            # 波动率风险（从技术指标复用）
            if technical_signals and code in technical_signals:
                signals = technical_signals[code]
                assessment.volatility_20d = self._estimate_volatility(signals)
                assessment.volatility_score = _band_score(
                    _VOL_RISK_BANDS, assessment.volatility_20d
                )

                # 超买风险
                overbought = self._check_overbought(signals)
                assessment.overbought_score = overbought
                if overbought >= 70:
                    assessment.risk_tags.append("超买")

            # 综合风险分 = 估值40% + 波动30% + 超买30%
            assessment.risk_score = round(
                assessment.valuation_score * 0.4
                + assessment.volatility_score * 0.3
                + assessment.overbought_score * 0.3,
                1,
            )

            result[code] = assessment

        logger.info(f"[风险评估] 完成: {len(result)}/{len(codes)} 只")
        return result

    def _estimate_volatility(self, signals) -> float:
        """
        从技术指标估算 20 日年化波动率

        利用布林带宽度反推：
        布林带宽度 ≈ 2 × 2σ × price → σ ≈ bandwidth / (4 × price)
        年化 = σ × √252
        """
        try:
            if signals.boll_mid <= 0:
                return 0.30  # 默认中等波动

            bandwidth = signals.boll_upper - signals.boll_lower
            daily_sigma = bandwidth / (4.0 * signals.boll_mid)
            annual_sigma = daily_sigma * np.sqrt(252)
            return min(float(annual_sigma), 2.0)  # 上限 200%
        except Exception:
            return 0.30

    def _check_overbought(self, signals) -> float:
        """
        超买风险评分

        RSI > 80 → 高风险
        布林带位置 > 0.9 → 高风险
        """
        score = 0.0
        count = 0

        # RSI 超买
        if signals.rsi6 > 80:
            score += 80.0
        elif signals.rsi6 > 70:
            score += 40.0
        count += 1

        # 布林带超买
        if signals.boll_position > 0.9:
            score += 80.0
        elif signals.boll_position > 0.8:
            score += 40.0
        count += 1

        return score / count if count > 0 else 0.0

    def _fetch_pe_batch(self, codes: List[str]) -> Dict[str, float]:
        """
        批量获取 PE（市盈率）数据

        优先从缓存获取，过期则批量刷新
        """
        now = time.time()
        result: Dict[str, float] = {}
        missing: List[str] = []

        with self._pe_cache_lock:
            # 检查缓存是否过期
            cache_expired = (now - self._pe_cache_ts) > self._pe_cache_ttl

            for code in codes:
                if not cache_expired and code in self._pe_cache:
                    result[code] = self._pe_cache[code]
                else:
                    missing.append(code)

        if missing:
            fetched = self._fetch_pe_from_realtime(missing)
            with self._pe_cache_lock:
                self._pe_cache.update(fetched)
                if cache_expired:
                    self._pe_cache_ts = now
            result.update(fetched)

        return result

    def _fetch_pe_from_realtime(self, codes: List[str]) -> Dict[str, float]:
        """
        从 DataFetcherManager 获取 PE 数据

        复用项目已有的实时行情接口，包含 pe_ratio 字段
        """
        result: Dict[str, float] = {}
        try:
            from data_provider.base import DataFetcherManager
        except ImportError:
            logger.warning("[风险评估] DataFetcherManager 未安装，跳过 PE 获取")
            return result

        manager = DataFetcherManager()

        for code in codes:
            try:
                quote = manager.get_realtime_quote(code)
                if quote is not None:
                    pe = getattr(quote, "pe_ratio", None)
                    if pe is not None and pe != 0:
                        result[code] = float(pe)
                        continue
                result[code] = 0.0
            except Exception as e:
                logger.debug(f"[风险评估] 获取 PE 失败 {code}: {e}")
                result[code] = 0.0

            # 避免触发限流
            time.sleep(0.05)

        return result
