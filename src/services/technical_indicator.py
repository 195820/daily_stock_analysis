# -*- coding: utf-8 -*-
"""
===================================
短线技术指标服务
===================================

职责：
1. 采集短线交易相关技术指标（DDE、量比、换手率、RSI6、MA5/MA10、布林带）
2. 计算各指标评分（0-100）
3. 输出综合技术评分 + 信号标签

短线指标权重：
- DDE大单净量: 25%  （主力动向）
- 量比:         20%  （放量程度）
- 换手率:       15%  （活跃度）
- RSI(6):       15%  （超买超卖）
- MA5/MA10:     10%  （短期趋势）
- 布林带位置:    5%  （波动位置）
- 突破信号:     10%  （创新高）

数据源: akshare 实时行情 + 历史K线
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.config import Config

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class TechnicalSignals:
    """单只股票的技术指标信号"""
    code: str
    name: str = ""

    # 行情数据
    price: float = 0.0            # 最新价
    change_pct: float = 0.0       # 涨跌幅 %
    volume_ratio: float = 1.0     # 量比
    turnover_rate: float = 0.0    # 换手率 %
    amount: float = 0.0           # 成交额（万元）

    # DDE 资金
    dde_big_order: float = 0.0    # DDE 大单净量 %

    # 均线
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma_alignment: bool = False    # MA5 > MA10 > MA20

    # RSI
    rsi6: float = 50.0            # RSI(6)
    rsi12: float = 50.0           # RSI(12)

    # 布林带
    boll_upper: float = 0.0
    boll_mid: float = 0.0
    boll_lower: float = 0.0
    boll_position: float = 0.5    # 0=下轨, 0.5=中轨, 1=上轨

    # 突破信号
    high_5d: float = 0.0          # 5日最高价
    low_5d: float = 0.0           # 5日最低价
    breakout_5d: bool = False     # 突破5日高点

    # 综合评分
    technical_score: float = 0.0  # 技术评分 0-100
    signal_tags: List[str] = field(default_factory=list)  # 信号标签


@dataclass
class TechnicalAnalysisResult:
    """技术分析报告"""
    signals: Dict[str, TechnicalSignals] = field(default_factory=dict)  # code → signals
    total_analyzed: int = 0
    error: Optional[str] = None
    fetched_at: Optional[datetime] = None


# ============================================================
# 工具函数
# ============================================================

def _safe_float(val: Any) -> float:
    """安全转换为 float，无效值返回 0.0。"""
    if val is None:
        return 0.0
    try:
        v = float(val)
        return v if v == v else 0.0  # NaN check
    except (ValueError, TypeError):
        return 0.0


# ============================================================
# 技术指标计算
# ============================================================

def _calc_rsi(series: pd.Series, period: int = 6) -> float:
    """计算 RSI 指标"""
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean().iloc[-1]
    avg_loss = loss.rolling(window=period, min_periods=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_ma(series: pd.Series, period: int) -> float:
    """计算简单移动平均"""
    if len(series) < period:
        return 0.0
    return series.rolling(window=period).mean().iloc[-1]


def _calc_bollinger(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[float, float, float]:
    """计算布林带，返回 (upper, mid, lower)"""
    if len(series) < period:
        last = series.iloc[-1] if len(series) > 0 else 0.0
        return last * 1.05, last, last * 0.95
    sma = series.rolling(window=period).mean().iloc[-1]
    std = series.rolling(window=period).std().iloc[-1]
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    return upper, sma, lower


# ============================================================
# 技术指标服务
# ============================================================

class TechnicalIndicatorService:
    """
    短线技术指标服务

    Usage::

        svc = TechnicalIndicatorService(config)
        result = svc.analyze_stocks(["600519", "000858"])
        for code, signals in result.signals.items():
            print(f"{signals.name}: {signals.technical_score}")
    """

    # EastMoney API（HTTPS 直连，与 sector_rotation 一致）
    _SPOT_URL = "https://79.push2.eastmoney.com/api/qt/clist/get"
    _KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    _COMMON_UT = "bd1d9ddb04089700cf9c27f6f7426281"
    _REQUEST_TIMEOUT = 15

    def __init__(self, config: Config):
        self._config = config
        self._cache: Dict[str, Tuple[TechnicalAnalysisResult, float]] = {}
        self._cache_lock = threading.RLock()
        self._spot_cache: Optional[Tuple[pd.DataFrame, float]] = None
        self._spot_lock = threading.Lock()
        self._spot_ttl = 300  # 5分钟缓存

        # HTTP 直连（绕过系统代理，与 sector_rotation 一致）
        import requests as _req
        from requests.adapters import HTTPAdapter as _HA
        from urllib3.util.retry import Retry as _Retry
        self._session = _req.Session()
        self._session.trust_env = False
        _retry = _Retry(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        _adapter = _HA(max_retries=_retry)
        self._session.mount("https://", _adapter)
        self._session.mount("http://", _adapter)
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        })

        # 激活东财反爬 patch（注入 NID 授权 + 随机 UA + 延迟）
        from src.patches.eastmoney_patch import eastmoney_patch
        eastmoney_patch()

    # ============================================================
    # 公开接口
    # ============================================================

    def analyze_stocks(
        self,
        codes: List[str],
        *,
        cache_ttl: int = 300,
    ) -> TechnicalAnalysisResult:
        """
        分析多只股票的技术指标

        Args:
            codes: 股票代码列表
            cache_ttl: 缓存时间（秒）

        Returns:
            TechnicalAnalysisResult
        """
        cache_key = f"tech_{'_'.join(sorted(codes[:20]))}"
        cached = self._get_cache(cache_key, cache_ttl)
        if cached is not None:
            return cached

        result = TechnicalAnalysisResult(fetched_at=datetime.now())

        try:
            # 获取 DDE 数据（复用已有服务，可选）
            dde_map = self._get_dde_map()

            # 逐只股票分析（直接从K线数据获取，不依赖实时行情）
            for code in codes:
                try:
                    signals = self._analyze_single(code, dde_map)
                    if signals:
                        result.signals[code] = signals
                except Exception as e:
                    logger.debug(f"分析 {code} 失败: {e}")
                    continue

            result.total_analyzed = len(result.signals)

        except Exception as e:
            logger.error(f"技术分析失败: {e}")
            result.error = str(e)

        self._set_cache(cache_key, result)
        return result

    def get_single_stock_signals(self, code: str) -> Optional[TechnicalSignals]:
        """获取单只股票的技术信号（供 Agent Tool 调用）"""
        result = self.analyze_stocks([code])
        return result.signals.get(code)

    # ============================================================
    # 内部实现
    # ============================================================

    def _analyze_single(
        self,
        code: str,
        dde_map: Dict[str, float],
    ) -> Optional[TechnicalSignals]:
        """分析单只股票（从K线数据获取所有信息）"""
        # 获取历史K线
        hist_df = self._get_history(code, days=60)
        if hist_df is None or len(hist_df) < 6:
            return None

        # 从K线提取基础数据
        latest = hist_df.iloc[0]  # 最新一条（已按日期降序）
        prev = hist_df.iloc[1] if len(hist_df) > 1 else latest

        price = float(latest['close'])
        prev_close = float(prev['close'])
        change_pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0

        # 量比: 今日成交量 / 5日均量
        vol_today = float(latest['volume'])
        vol_avg5 = float(hist_df['volume'].iloc[1:6].mean()) if len(hist_df) > 5 else vol_today
        volume_ratio = vol_today / vol_avg5 if vol_avg5 > 0 else 1.0

        # 换手率估算（无流通股本，用相对活跃度代替）
        # 量比 1.0 对应约 3% 换手率
        turnover_rate = volume_ratio * 3.0

        signals = TechnicalSignals(
            code=code,
            name="",  # K线接口不返回名称，后续可补充
            price=price,
            change_pct=round(change_pct, 2),
            volume_ratio=round(volume_ratio, 2),
            turnover_rate=round(turnover_rate, 1),
            amount=float(latest.get('amount', 0)) / 10000,
        )

        # DDE
        signals.dde_big_order = dde_map.get(code, 0.0)

        # 从K线计算技术指标（MA、RSI、布林带等）
        if hist_df is not None and len(hist_df) >= 5:
            self._calc_history_indicators(signals, hist_df)

        # 计算综合评分
        signals.technical_score = self._calc_technical_score(signals)

        # 生成信号标签
        signals.signal_tags = self._generate_signal_tags(signals)

        return signals

    def _calc_history_indicators(self, signals: TechnicalSignals, df: pd.DataFrame) -> None:
        """从历史K线计算技术指标"""
        close = df['close'].astype(float)

        # 均线
        signals.ma5 = _calc_ma(close, 5)
        signals.ma10 = _calc_ma(close, 10)
        signals.ma20 = _calc_ma(close, 20)

        # 均线排列: MA5 > MA10 > MA20
        if signals.ma5 > 0 and signals.ma10 > 0 and signals.ma20 > 0:
            signals.ma_alignment = signals.ma5 > signals.ma10 > signals.ma20

        # RSI
        signals.rsi6 = _calc_rsi(close, 6)
        signals.rsi12 = _calc_rsi(close, 12)

        # 布林带
        upper, mid, lower = _calc_bollinger(close, 20)
        signals.boll_upper = upper
        signals.boll_mid = mid
        signals.boll_lower = lower
        if upper > lower:
            signals.boll_position = (signals.price - lower) / (upper - lower)
        else:
            signals.boll_position = 0.5

        # 5日高低点
        recent_5 = df.head(5)
        signals.high_5d = float(recent_5['high'].max())
        signals.low_5d = float(recent_5['low'].min())
        signals.breakout_5d = signals.price >= signals.high_5d

    def _calc_technical_score(self, s: TechnicalSignals) -> float:
        """
        计算短线技术评分（0-100）

        权重分配：
        - DDE大单净量: 25%
        - 量比: 20%
        - 换手率: 15%
        - RSI(6): 15%
        - MA排列: 10%
        - 布林带位置: 5%
        - 突破信号: 10%
        """
        # 1. DDE 评分: -5%→0, 0%→50, +5%→100
        dde_score = max(0, min(100, (s.dde_big_order + 5) / 10 * 100))

        # 2. 量比评分: 0.5→0, 1.0→30, 2.0→70, 3.0→100
        if s.volume_ratio <= 0.5:
            vr_score = 0
        elif s.volume_ratio <= 1.0:
            vr_score = (s.volume_ratio - 0.5) / 0.5 * 30
        elif s.volume_ratio <= 3.0:
            vr_score = 30 + (s.volume_ratio - 1.0) / 2.0 * 70
        else:
            vr_score = 100

        # 3. 换手率评分: 0→0, 3%→60, 10%→100, >15%→80（过热）
        if s.turnover_rate <= 0:
            tr_score = 0
        elif s.turnover_rate <= 3:
            tr_score = s.turnover_rate / 3 * 60
        elif s.turnover_rate <= 10:
            tr_score = 60 + (s.turnover_rate - 3) / 7 * 40
        elif s.turnover_rate <= 15:
            tr_score = 100
        else:
            tr_score = 100 - (s.turnover_rate - 15) / 5 * 20  # 过热扣分

        # 4. RSI 评分: 强势区 50-70 最优
        if s.rsi6 < 30:
            rsi_score = s.rsi6 / 30 * 40  # 超卖区，中等分
        elif s.rsi6 < 50:
            rsi_score = 40 + (s.rsi6 - 30) / 20 * 40  # 弱势→中性
        elif s.rsi6 <= 70:
            rsi_score = 80 + (s.rsi6 - 50) / 20 * 20  # 强势区，满分
        elif s.rsi6 <= 85:
            rsi_score = 100 - (s.rsi6 - 70) / 15 * 30  # 超买开始扣分
        else:
            rsi_score = 70  # 严重超买

        # 5. 均线排列评分
        ma_score = 100 if s.ma_alignment else (50 if s.ma5 > s.ma10 else 20)

        # 6. 布林带位置评分: 中轨以上得分，上轨附近最优
        bp = s.boll_position
        if bp < 0.2:
            boll_score = bp / 0.2 * 30  # 下轨附近，低分
        elif bp < 0.5:
            boll_score = 30 + (bp - 0.2) / 0.3 * 40  # 中下轨
        elif bp <= 0.8:
            boll_score = 70 + (bp - 0.5) / 0.3 * 30  # 中上轨，高分
        else:
            boll_score = 100 - (bp - 0.8) / 0.2 * 20  # 上轨，略扣

        # 7. 突破评分
        breakout_score = 100 if s.breakout_5d else 30

        # 加权求和
        score = (
            dde_score * 0.25 +
            vr_score * 0.20 +
            tr_score * 0.15 +
            rsi_score * 0.15 +
            ma_score * 0.10 +
            boll_score * 0.05 +
            breakout_score * 0.10
        )

        return round(max(0, min(100, score)), 1)

    def _generate_signal_tags(self, s: TechnicalSignals) -> List[str]:
        """生成信号标签"""
        tags = []

        # 资金信号
        if s.dde_big_order > 1.0:
            tags.append("主力强攻")
        elif s.dde_big_order > 0.3:
            tags.append("资金流入")
        elif s.dde_big_order < -1.0:
            tags.append("主力出逃")

        # 量价信号
        if s.volume_ratio > 2.5 and s.change_pct > 2:
            tags.append("放量上攻")
        elif s.volume_ratio > 2.0:
            tags.append("显著放量")

        # 均线信号
        if s.ma_alignment:
            tags.append("多头排列")

        # RSI 信号
        if s.rsi6 > 80:
            tags.append("RSI超买")
        elif s.rsi6 < 20:
            tags.append("RSI超卖")

        # 突破信号
        if s.breakout_5d:
            tags.append("突破5日高")

        # 布林带信号
        if s.boll_position > 0.9:
            tags.append("触及上轨")
        elif s.boll_position < 0.1:
            tags.append("触及下轨")

        return tags

    # ============================================================
    # 数据获取
    # ============================================================

    def _get_spot_data(self) -> Optional[pd.DataFrame]:
        """获取全市场实时行情（带缓存）"""
        with self._spot_lock:
            if self._spot_cache is not None:
                df, ts = self._spot_cache
                if time.time() - ts < self._spot_ttl:
                    return df

        try:
            params = {
                "pn": "1",
                "pz": "5000",
                "po": "1",
                "np": "1",
                "ut": self._COMMON_UT,
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18",
            }

            resp = self._session.get(
                self._SPOT_URL,
                params=params,
                timeout=self._REQUEST_TIMEOUT,
            )
            data = resp.json()
            diff = data.get("data", {}).get("diff", [])

            if not diff:
                logger.warning("实时行情数据为空")
                return None

            # 转换为 DataFrame
            rows = []
            for item in diff:
                rows.append({
                    "代码": str(item.get("f12", "")),
                    "名称": str(item.get("f14", "")),
                    "最新价": _safe_float(item.get("f2")),
                    "涨跌幅": _safe_float(item.get("f3")),
                    "涨跌额": _safe_float(item.get("f4")),
                    "成交量": _safe_float(item.get("f5")),
                    "成交额": _safe_float(item.get("f6")),
                    "振幅": _safe_float(item.get("f7")),
                    "换手率": _safe_float(item.get("f8")),
                    "量比": _safe_float(item.get("f10")),
                    "最高": _safe_float(item.get("f15")),
                    "最低": _safe_float(item.get("f16")),
                    "开盘": _safe_float(item.get("f17")),
                    "昨收": _safe_float(item.get("f18")),
                })

            df = pd.DataFrame(rows)
            with self._spot_lock:
                self._spot_cache = (df, time.time())
            logger.info(f"[技术指标] 获取实时行情 {len(df)} 只")
            return df

        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")

        return None

    def _get_history(self, code: str, days: int = 30) -> Optional[pd.DataFrame]:
        """获取历史K线（通过 EastMoney API）"""
        try:
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')

            # 判断市场: 6开头=沪市(1.), 其他=深市(0.)
            secid = f"1.{code}" if code.startswith('6') else f"0.{code}"

            resp = self._session.get(
                self._KLINE_URL,
                params={
                    "secid": secid,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                    "klt": "101",
                    "fqt": "1",
                    "beg": start_date,
                    "end": end_date,
                },
                timeout=self._REQUEST_TIMEOUT,
            )
            data = resp.json()
            klines = data.get("data", {}).get("klines", [])

            if not klines:
                return None

            rows = []
            for k in klines:
                parts = k.split(",")
                if len(parts) < 7:
                    continue
                rows.append({
                    "date": parts[0],
                    "open": _safe_float(parts[1]),
                    "close": _safe_float(parts[2]),
                    "high": _safe_float(parts[3]),
                    "low": _safe_float(parts[4]),
                    "volume": _safe_float(parts[5]),
                    "amount": _safe_float(parts[6]),
                })

            df = pd.DataFrame(rows)
            df = df.sort_values("date", ascending=False).reset_index(drop=True)
            return df

        except Exception as e:
            logger.debug(f"获取 {code} 历史数据失败: {e}")

        return None

    def _get_dde_map(self) -> Dict[str, float]:
        """获取 DDE 数据映射"""
        try:
            from src.services.dde_fetcher import DdeFetcher
            fetcher = DdeFetcher(self._config)
            result = fetcher.fetch_market_dde(use_cache=True)
            if not result.error:
                return {
                    code: snap.dde_big_order
                    for code, snap in result.snapshots.items()
                }
        except Exception as e:
            logger.debug(f"获取 DDE 失败: {e}")
        return {}

    # ============================================================
    # 缓存
    # ============================================================

    def _get_cache(self, key: str, ttl: int) -> Optional[TechnicalAnalysisResult]:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            result, ts = entry
            if time.time() - ts > ttl:
                del self._cache[key]
                return None
            return result

    def _set_cache(self, key: str, result: TechnicalAnalysisResult) -> None:
        with self._cache_lock:
            self._cache[key] = (result, time.time())
