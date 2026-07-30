# -*- coding: utf-8 -*-
"""
======================================
MootdxFetcher - 通达信数据源 (Priority 0)
======================================

数据来源：通达信行情服务器（mootdx 库，TCP 7709 协议）
特点：TCP 协议不受 HTTP 反爬影响，免费、无需 Token

关键策略：
1. mootdx BESTIP 自动探测最优服务器（带数据验证）
2. TCP 连接失败后指数退避重试
3. 不支持的市场（美/港/北交所）直接抛异常触发降级

与 PytdxFetcher 的区别：
- mootdx 内置 BESTIP 服务器探测（pytdx 需手动遍历）
- 旧 TDX 服务器 IP 频繁过期，mootdx 探测更可靠
- 优先级 P0（高于 PytdxFetcher P2）
"""

import logging
import time
from contextlib import contextmanager
from typing import Optional, Generator, List, Tuple, Dict, Any

import pandas as pd
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from .base import (
    BaseFetcher,
    DataFetchError,
    DataSourceUnavailableError,
    STANDARD_COLUMNS,
    is_bse_code,
    normalize_stock_code,
    _is_hk_market,
)
import os
import re

logger = logging.getLogger(__name__)

# 连接冷却时间：连接失败后暂时跳过，避免反复探测
_MOOTDX_CONNECTION_COOLDOWN_SECONDS = 15.0


def _is_us_code(stock_code: str) -> bool:
    """判断是否为美股代码（1-5个大写字母，可含'.'）"""
    code = stock_code.strip().upper()
    return bool(re.match(r'^[A-Z]{1,5}(\.[A-Z])?$', code))


class MootdxFetcher(BaseFetcher):
    """
    通达信数据源（mootdx 实现）

    优先级：0（最高，TCP 协议不受 HTTP 反爬影响）
    数据来源：通达信行情服务器（TCP 7709）
    支持市场：仅 A 股（沪深）
    不支持：美股、港股、北交所（自动抛异常触发降级）
    """

    name = "MootdxFetcher"
    priority = int(os.getenv("MOOTDX_PRIORITY", "0"))

    def __init__(self):
        self._client = None
        self._unavailable_until = 0.0
        self._last_unavailable_reason = ""
        self._stock_name_cache: Dict[str, str] = {}

    def _is_in_cooldown(self) -> bool:
        return time.time() < self._unavailable_until

    def _mark_cooldown(self, reason: str) -> None:
        self._unavailable_until = time.time() + _MOOTDX_CONNECTION_COOLDOWN_SECONDS
        self._last_unavailable_reason = str(reason or "").strip()
        logger.info(
            "Mootdx 连接冷却 %.0fs: %s",
            _MOOTDX_CONNECTION_COOLDOWN_SECONDS,
            self._last_unavailable_reason,
        )

    def is_available_for_request(self, capability: str = "") -> bool:
        return not self._is_in_cooldown()

    def _get_mootdx(self):
        """延迟加载 mootdx 模块"""
        try:
            from mootdx.quotes import Quotes
            return Quotes
        except ImportError:
            logger.warning("mootdx 未安装，请运行: pip install mootdx")
            return None

    @contextmanager
    def _mootdx_session(self) -> Generator:
        """
        mootdx 连接上下文管理器

        使用 BESTIP 自动探测最优服务器（带数据验证），
        连接失败自动冷却避免反复探测。
        """
        if self._is_in_cooldown():
            raise DataSourceUnavailableError(
                f"Mootdx 暂不可用: {self._last_unavailable_reason or 'connection cooldown'}"
            )

        Quotes = self._get_mootdx()
        if Quotes is None:
            raise DataFetchError("mootdx 库未安装")

        try:
            # BESTIP 自动探测（timeout=10s，含数据验证）
            client = Quotes.factory(market='std', timeout=10)
            logger.debug("mootdx BESTIP 连接成功")
            yield client
        except Exception as e:
            self._mark_cooldown(f"mootdx 连接失败: {e}")
            raise DataFetchError(f"mootdx 连接失败: {e}") from e

    def _get_market_prefix(self, stock_code: str) -> str:
        """
        根据股票代码获取 mootdx 市场前缀

        mootdx bars() 接受带前缀的代码：
        - 'sh600519' → 上海
        - 'sz000001' → 深圳

        但部分 mootdx 版本接受纯代码并自动判断市场。
        优先尝试纯代码，失败时回退到带前缀格式。
        """
        raw = stock_code.strip().upper()
        prefix, sep, suffix = raw.partition(".")
        if sep and prefix:
            if prefix in ('SH', 'SS'):
                return f"sh{normalize_stock_code(suffix)}"
            if prefix == 'SZ':
                return f"sz{normalize_stock_code(suffix)}"

        if raw.startswith(('SH', 'SS')):
            return f"sh{raw[2:]}"
        if raw.startswith('SZ'):
            return f"sz{raw[2:]}"
        if raw.endswith(('.SH', '.SS')):
            return f"sh{raw.split('.')[0]}"
        if raw.endswith('.SZ'):
            return f"sz{raw.split('.')[0]}"

        # 按代码首位数字判断
        code = normalize_stock_code(stock_code)
        if code.startswith(('6', '9')):
            return f"sh{code}"
        return f"sz{code}"

    @staticmethod
    def _strip_market_prefix(symbol: str) -> str:
        """去除 sh/sz 前缀，返回纯6位代码"""
        s = symbol.strip().lower()
        if s.startswith(('sh', 'sz')):
            return s[2:]
        return s

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """从通达信获取 K 线数据"""
        if _is_us_code(stock_code):
            raise DataFetchError(f"MootdxFetcher 不支持美股 {stock_code}")
        if _is_hk_market(stock_code):
            raise DataFetchError(f"MootdxFetcher 不支持港股 {stock_code}")
        if is_bse_code(stock_code):
            raise DataFetchError(f"MootdxFetcher 不支持北交所 {stock_code}")

        symbol = self._get_market_prefix(stock_code)
        code_only = self._strip_market_prefix(symbol)

        # 估算交易日数量（多取一些用于日期过滤）
        from datetime import datetime as dt
        start_dt = dt.strptime(start_date, '%Y-%m-%d')
        end_dt = dt.strptime(end_date, '%Y-%m-%d')
        days = (end_dt - start_dt).days
        count = min(max(days * 5 // 7 + 10, 30), 800)

        logger.debug(f"调用 mootdx bars(symbol={code_only}, frequency=9, offset={count})")

        with self._mootdx_session() as client:
            try:
                # frequency=9 → 日线
                # mootdx 优先使用纯代码（自动判断市场），失败时尝试带前缀格式
                df = client.bars(symbol=code_only, frequency=9, offset=count)

                if df is None or df.empty:
                    df = client.bars(symbol=symbol, frequency=9, offset=count)

                if df is None or df.empty:
                    raise DataFetchError(f"mootdx 未查询到 {stock_code} 的数据")

                # 过滤日期范围（优先用 DatetimeIndex，回退到 datetime 列）
                if isinstance(df.index, pd.DatetimeIndex):
                    # mootdx 可能返回异常日期值（如 6678-15-11），用 coerce 转 NaT 后过滤
                    clean_index = pd.to_datetime(df.index, errors='coerce')
                    valid_mask = clean_index.notna()
                    if not valid_mask.all():
                        bad_count = (~valid_mask).sum()
                        logger.debug(f"mootdx 过滤 {bad_count} 条异常日期")
                        df = df[valid_mask]
                        clean_index = clean_index[valid_mask]
                    mask = (clean_index >= start_date) & (clean_index <= end_date + ' 23:59:59')
                    df = df[mask]
                elif 'datetime' in df.columns:
                    df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')
                    df = df.dropna(subset=['datetime'])
                    df = df[(df['datetime'] >= start_date) & (df['datetime'] <= end_date)]

                return df
            except DataFetchError:
                raise
            except Exception as e:
                raise DataFetchError(f"mootdx 获取数据失败: {e}") from e

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        标准化 mootdx 数据

        mootdx 返回结构：DatetimeIndex(name='datetime') + columns(open/close/high/low/vol/amount/...)
        目标列名：date, open, high, low, close, volume, amount, pct_chg
        """
        df = df.copy()

        # mootdx 返回 DatetimeIndex + 同名 'datetime' 列，先重置索引避免冲突
        if isinstance(df.index, pd.DatetimeIndex):
            # 用索引的 datetime 值作为 date 列（比字符串列更精确）
            df['date'] = df.index
            # 删除 mootdx 自带的字符串 datetime 列（避免 rename 冲突）
            if 'datetime' in df.columns:
                df = df.drop(columns=['datetime'])
        elif 'datetime' in df.columns:
            df = df.rename(columns={'datetime': 'date'})

        # mootdx 同时返回 'vol' 和 'volume'（值相同），丢弃 'volume' 避免 rename 后重复列
        if 'vol' in df.columns and 'volume' in df.columns:
            df = df.drop(columns=['volume'])

        df = df.rename(columns={'vol': 'volume'})

        # mootdx 不返回涨跌幅，需自行计算
        if 'pct_chg' not in df.columns and 'close' in df.columns:
            df['pct_chg'] = df['close'].pct_change() * 100
            df['pct_chg'] = df['pct_chg'].fillna(0).round(2)

        df['code'] = stock_code

        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        return df[existing_cols]

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """
        获取股票名称

        mootdx Quotes 不直接提供名称查询（StdQuotes 无 get_security_list），
        返回 None 由 DataFetcherManager 从其他数据源补齐。
        """
        if _is_hk_market(stock_code) or _is_us_code(stock_code) or is_bse_code(stock_code):
            return None

        if stock_code in self._stock_name_cache:
            return self._stock_name_cache[stock_code]

        # 尝试通过底层 pytdx API 获取（mootdx 封装了 pytdx）
        try:
            symbol = self._get_market_prefix(stock_code)
            code_only = self._strip_market_prefix(symbol)
            market_code = 1 if code_only.startswith(('6', '9')) else 0

            with self._mootdx_session() as client:
                # mootdx StdQuotes 可能暴露底层 pytdx api
                raw_api = getattr(client, 'api', None) or getattr(client, 'client', None)
                if raw_api is not None:
                    for page_start in range(0, 10000, 1000):
                        stocks = raw_api.get_security_list(market_code, page_start) or []
                        for stock in stocks:
                            if stock.get('code') == code_only:
                                name = stock.get('name', '')
                                if name:
                                    self._stock_name_cache[stock_code] = name
                                    return name
                        if len(stocks) < 1000:
                            break
        except Exception as e:
            logger.debug(f"mootdx 获取股票名称失败 {stock_code}: {e}")

        return None

    def get_realtime_quote(self, stock_code: str) -> Optional[dict]:
        """
        获取实时行情（46字段）

        mootdx quotes() 返回丰富字段：
        - price, open, high, low, last_close（昨收）
        - vol, amount
        - bid1~bid5, ask1~ask5（五档报价）
        """
        if is_bse_code(stock_code):
            raise DataFetchError(f"MootdxFetcher 不支持北交所 {stock_code}")
        if _is_us_code(stock_code) or _is_hk_market(stock_code):
            raise DataFetchError(f"MootdxFetcher 不支持非A股 {stock_code}")

        try:
            symbol = self._get_market_prefix(stock_code)
            code_only = self._strip_market_prefix(symbol)
            with self._mootdx_session() as client:
                quotes = client.quotes(symbol=code_only)
                if quotes is not None and not quotes.empty:
                    q = quotes.iloc[0]
                    return {
                        'code': stock_code,
                        'name': '',
                        'price': float(q.get('price', 0)),
                        'open': float(q.get('open', 0)),
                        'high': float(q.get('high', 0)),
                        'low': float(q.get('low', 0)),
                        'pre_close': float(q.get('last_close', 0)),
                        'volume': float(q.get('vol', 0)),
                        'amount': float(q.get('amount', 0)),
                        'bid_prices': [float(q.get(f'bid{i}', 0)) for i in range(1, 6)],
                        'ask_prices': [float(q.get(f'ask{i}', 0)) for i in range(1, 6)],
                    }
        except DataFetchError:
            raise
        except Exception as e:
            logger.warning(f"mootdx 获取实时行情失败 {stock_code}: {e}")

        return None

    # =================================================================
    # 板块 K 线（通达信 TCP 协议，不封 IP）
    # =================================================================

    def get_board_kline(
        self,
        board_code: str,
        board_type: str = "concept",
        days: int = 60,
    ) -> Optional[pd.DataFrame]:
        """
        通达信板块指数 K 线（TCP 7709 协议）。

        Args:
            board_code: 通达信板块代码（880xxx / 881xxx）
            board_type: 忽略（mootdx 不区分概念/行业）
            days: 获取天数

        Returns:
            DataFrame(date, open, close, high, low, volume, amount)
            失败返回 None（触发 Manager 降级到下一个数据源）
        """
        try:
            with self._mootdx_session() as client:
                offset = min(days * 5 // 7 + 10, 800)
                logger.info(f"[API调用] mootdx index_bars 板块K线: {board_code} ({days}天)")

                df = client.index_bars(
                    symbol=board_code, frequency=9, start=0, offset=offset,
                )

                if df is None or df.empty:
                    logger.debug(f"[Mootdx] 板块 {board_code} 无数据")
                    return None

                # 过滤掉当日未开盘的 bar（vol=0）
                if 'vol' in df.columns:
                    df = df[df['vol'] > 0]

                if df.empty:
                    return None

                # 转换为 Node 2 期望的标准格式
                result = pd.DataFrame({
                    'date': df['datetime'].astype(str).str[:10] if 'datetime' in df.columns
                           else df.index.strftime('%Y-%m-%d'),
                    'open': df['open'].astype(float),
                    'close': df['close'].astype(float),
                    'high': df['high'].astype(float),
                    'low': df['low'].astype(float),
                    'volume': df['vol'].astype(float),
                    'amount': df['amount'].astype(float),
                })
                # 通达信板块指数特有字段：涨跌家数
                if 'up_count' in df.columns:
                    result['up_count'] = df['up_count'].astype(float)
                if 'down_count' in df.columns:
                    result['down_count'] = df['down_count'].astype(float)
                return result

        except DataSourceUnavailableError:
            raise
        except Exception as e:
            logger.debug(f"[Mootdx] 板块K线 {board_code} 失败: {e}")
            return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    fetcher = MootdxFetcher()

    try:
        df = fetcher.get_daily_data('600519')
        print(f"K线获取成功，共 {len(df)} 条")
        print(df.tail(3))

        name = fetcher.get_stock_name('600519')
        print(f"股票名称: {name}")

        quote = fetcher.get_realtime_quote('600519')
        print(f"实时行情: price={quote.get('price')}, vol={quote.get('volume')}")
    except Exception as e:
        print(f"获取失败: {e}")
