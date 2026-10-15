# -*- coding: utf-8 -*-
"""
======================================
MootdxFetcher - 通达信数据源 (Priority 0)
======================================

数据来源：通达信行情服务器（mootdx 库，TCP 7709 协议）
特点：TCP 协议不受 HTTP 反爬影响，免费、无需 Token

关键策略：
1. pin 验活行情主站池 + 真实取数验活 + 顺序故障转移（不用 BESTIP）
2. TCP 连接失败后指数退避重试
3. 不支持的市场（美/港/北交所）直接抛异常触发降级

与 PytdxFetcher 的区别：
- mootdx 内置主站池 + 真实取数验活（pytdx 需手动遍历且只做 TCP 探测）
- 优先级 P0（高于 PytdxFetcher P2）
"""

import logging
import threading
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

# 全池失败冷却：网络中断时逐台扫描本身要花 池大小×超时，若只冷却 15s 会陷入
# "扫 45s → 歇 15s" 的忙等，把上层拖成分钟级挂起。此时改用长冷却。
_MOOTDX_ALL_SERVERS_DOWN_COOLDOWN_SECONDS = 120.0

# ── 通达信行情主站池（TCP 7709）──
# 2026-09-10 全量验活结论（73 台候选 → 9 台能取到 K 线）：
# mootdx 内置的 38 台名单最后更新于 2024-07，现已整批失效——TCP 握手与
# get_security_count 均正常，唯独 K 线接口回空表，属"握手通过 ≠ 能取数"的
# 静默失败。BESTIP 只在该陈旧名单内选优，因此换服务器必须绕开它。
# 可用环境变量 MOOTDX_SERVERS="ip:port,ip:port" 覆盖本池。
_DEFAULT_HQ_SERVERS: Tuple[Tuple[str, int], ...] = (
    ("117.34.114.15", 7709),  # 国泰君安9
    ("117.34.114.13", 7709),  # 国泰君安7
    ("117.34.114.14", 7709),  # 国泰君安8
    ("117.34.114.16", 7709),  # 国泰君安10
    ("117.34.114.17", 7709),  # 国泰君安11
    ("117.34.114.18", 7709),  # 国泰君安12
    ("117.34.114.20", 7709),  # 国泰君安13
    ("117.34.114.27", 7709),  # 国泰君安14
    ("59.36.5.11", 7709),     # 安信15
)

# 验活探针：TCP 握手/连接数查询成功不等于能取数，必须真取一根 K 线
_TDX_PROBE_MARKET = 1        # 1 = 上海
_TDX_PROBE_SYMBOL = "600519"
_TDX_PROBE_FREQUENCY = 9     # 日线
_TDX_PROBE_COUNT = 1
_TDX_CONNECT_TIMEOUT = 5

# 单次会话内的选路尝试次数：第一次复用已选节点，失败后再作废重选一次
_TDX_SELECT_ATTEMPTS = 2


def _resolve_hq_servers() -> Tuple[Tuple[str, int], ...]:
    """解析行情主站池：环境变量 MOOTDX_SERVERS 优先，格式 "ip:port,ip:port"。"""
    raw = os.getenv("MOOTDX_SERVERS", "").strip()
    if not raw:
        return _DEFAULT_HQ_SERVERS

    servers: List[Tuple[str, int]] = []
    for item in raw.split(","):
        host, _, port = item.strip().partition(":")
        if host and port.isdigit():
            servers.append((host, int(port)))
        else:
            logger.warning("忽略非法的 MOOTDX_SERVERS 条目: %r", item)
    return tuple(servers) or _DEFAULT_HQ_SERVERS


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
        # 上次验活通过的行情主站，后续会话优先复用（失效后由选路逻辑换台）
        self._active_server: Optional[Tuple[str, int]] = None
        # 选路锁：Node 2 会并发发起上百个会话，无锁时冷启动期各线程
        # 都看到 _active_server 为空而各自扫全池（惊群）
        self._server_lock = threading.Lock()

    def _is_in_cooldown(self) -> bool:
        return time.time() < self._unavailable_until

    def _mark_cooldown(self, reason: str, seconds: Optional[float] = None) -> None:
        cooldown = _MOOTDX_CONNECTION_COOLDOWN_SECONDS if seconds is None else seconds
        self._unavailable_until = time.time() + cooldown
        self._last_unavailable_reason = str(reason or "").strip()
        logger.info("Mootdx 连接冷却 %.0fs: %s", cooldown, self._last_unavailable_reason)

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

    def _open_client(self, Quotes, server: Tuple[str, int]):
        """按指定节点建连；失败返回 None（不抛，交由选路逻辑继续下一台）。"""
        try:
            return Quotes.factory(
                market="std", server=server, timeout=_TDX_CONNECT_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("mootdx 连接 %s:%s 失败: %s", server[0], server[1], exc)
            return None

    @staticmethod
    def _probe_client(client) -> bool:
        """真实取数验活——故障节点会静默回空表，只测 TCP 通不通会误判。

        异常分类（缺陷③B：可观测性，failover 行为不变，均 return False）：
          - 网络/超时异常 → 瞬态，节点未必坏，warn 后换台；
          - 空表 → 节点静默失效（真该作废），info 后换台；
          - 其它（mootdx 库内确定性异常，如 'month must be 1..12'）→ 非节点
            健康信号，打全栈 exc_info 以便区分，不谎报为"验活失败/节点失效"。
        """
        try:
            bars = client.bars(
                symbol=_TDX_PROBE_SYMBOL,
                frequency=_TDX_PROBE_FREQUENCY,
                offset=_TDX_PROBE_COUNT,
            )
        except (OSError, TimeoutError) as exc:  # socket.timeout / ConnectionError 等瞬态
            logger.warning("mootdx 验活网络异常（瞬态，换台）: %r", exc)
            return False
        except Exception as exc:  # 库内确定性异常 ≠ 节点失效，打全栈供诊断
            logger.warning(
                "mootdx 验活抛出非网络异常（疑似库内缺陷，非节点失效）: %r",
                exc, exc_info=True,
            )
            return False
        if bars is None or bars.empty:
            logger.info("mootdx 验活回空表（节点静默失效，换台）")
            return False
        return True

    def _scan_servers(self, Quotes) -> Optional[Tuple[str, int]]:
        """按池序扫描，返回第一台能真取到 K 线的节点；全败返回 None。"""
        for server in _resolve_hq_servers():
            client = self._open_client(Quotes, server)
            if client is None:
                continue
            if not self._probe_client(client):
                logger.warning("mootdx 节点 %s:%s 验活未通过，换下一台", *server)
                continue
            logger.info("mootdx 选定行情主站 %s:%s", *server)
            return server
        return None

    def _select_server(self, Quotes) -> Optional[Tuple[str, int]]:
        """返回当前选定的主站；未选定（或已失效）时加锁扫描一次。"""
        if self._active_server is not None:
            return self._active_server
        with self._server_lock:
            # 双检：等锁期间可能已有线程扫完并写回结果
            if self._active_server is None:
                self._active_server = self._scan_servers(Quotes)
            return self._active_server

    def _invalidate_active_server(self) -> None:
        """作废当前节点，使下一次会话重新选路。"""
        with self._server_lock:
            self._active_server = None

    def _connect_healthy(self, Quotes):
        """取得一个已验活的 client；节点失效时作废重选，仍失败返回 None。"""
        for _ in range(_TDX_SELECT_ATTEMPTS):
            server = self._select_server(Quotes)
            if server is None:
                return None
            client = self._open_client(Quotes, server)
            if client is not None and self._probe_client(client):
                return client
            logger.warning("mootdx 节点 %s:%s 已失效，作废重选", *server)
            self._invalidate_active_server()
        return None

    @contextmanager
    def _mootdx_session(self) -> Generator:
        """
        mootdx 连接上下文管理器

        复用上次验活通过的节点，每台都必须真取到一根 K 线才采用。全部失败则
        进入冷却并抛 DataSourceUnavailableError，让 Manager 立即降级，而不是
        把空表当成"这个板块没数据"往上抛。
        """
        if self._is_in_cooldown():
            raise DataSourceUnavailableError(
                f"Mootdx 暂不可用: {self._last_unavailable_reason or 'connection cooldown'}"
            )

        Quotes = self._get_mootdx()
        if Quotes is None:
            raise DataFetchError("mootdx 库未安装")

        client = self._connect_healthy(Quotes)
        if client is None:
            reason = f"全部 {len(_resolve_hq_servers())} 个通达信节点验活失败（协议或网络）"
            self._mark_cooldown(reason, _MOOTDX_ALL_SERVERS_DOWN_COOLDOWN_SECONDS)
            raise DataSourceUnavailableError(f"Mootdx 不可用: {reason}")

        yield client

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
                        # Phase 6-1 盘中闸门：五档挂单量（OPEN-I2 实证 quotes() 46 列含 *_vol 列）
                        'bid_volumes': [float(q.get(f'bid_vol{i}', 0) or 0) for i in range(1, 6)],
                        'ask_volumes': [float(q.get(f'ask_vol{i}', 0) or 0) for i in range(1, 6)],
                    }
        except DataFetchError:
            raise
        except Exception as e:
            logger.warning(f"mootdx 获取实时行情失败 {stock_code}: {e}")

        return None

    def get_minute_data(
        self,
        stock_code: str,
    ) -> Optional[pd.DataFrame]:
        """
        获取当日分时序列（Phase 6-1 盘中闸门，OPEN-I3 实证 minute() 240 行可用）。

        Returns:
            DataFrame(time, price, vol)：240 个分钟点（当日 4 小时），
            盘前/非交易时段返回当日已有部分或 None；失败返回 None（触发 Manager 降级）。
        """
        if is_bse_code(stock_code):
            raise DataFetchError(f"MootdxFetcher 不支持北交所 {stock_code}")
        if _is_us_code(stock_code) or _is_hk_market(stock_code):
            raise DataFetchError(f"MootdxFetcher 不支持非A股 {stock_code}")

        try:
            symbol = self._get_market_prefix(stock_code)
            code_only = self._strip_market_prefix(symbol)
            with self._mootdx_session() as client:
                logger.info(f"[API调用] mootdx minute 分时: {stock_code}")
                df = client.minute(symbol=code_only)
                if df is None or df.empty:
                    return None
                # 统一列名：mootdx minute 返回 day/time/price/vol
                cols = {c.lower(): c for c in df.columns}
                out = pd.DataFrame({
                    'time': df[cols['time']] if 'time' in cols else df.index,
                    'price': pd.to_numeric(df[cols['price']], errors='coerce'),
                    'vol': pd.to_numeric(df[cols.get('vol', cols.get('volume'))], errors='coerce'),
                })
                return out.dropna(subset=['price'])
        except DataFetchError:
            raise
        except Exception as e:
            logger.warning(f"mootdx 获取分时数据失败 {stock_code}: {e}")

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


    def get_all_concept_boards(self) -> Optional[list[dict]]:
        """获取全量概念板块列表（mootdx TCP）。

        Returns:
            [{"code": "880xxx", "name": "板块名"}, ...]
            失败返回 None
        """
        try:
            with self._mootdx_session() as client:
                logger.info("[API调用] mootdx stock_all() 全量品种列表")
                df = client.stock_all()
                if df is None or df.empty:
                    return None
                # 筛选 880xxx 概念板块
                mask = df["code"].astype(str).str.startswith("880")
                concepts = df[mask][["code", "name"]].to_dict("records")
                # 清洗 TDX GBK 定长字段的 \u0000 填充/截断空白（如 "白酒\u0000..."）
                for board in concepts:
                    board["name"] = str(board.get("name") or "").replace("\u0000", "").strip()
                concepts = [b for b in concepts if b["name"]]
                logger.info(f"[Mootdx] 概念板块总数: {len(concepts)}")
                return concepts
        except Exception as e:
            logger.warning(f"[Mootdx] stock_all() 失败: {e}")
            return None

    def get_all_industry_boards(self) -> Optional[list[dict]]:
        """获取全量行业板块列表（mootdx TCP，881xxx）。

        Returns:
            [{"code": "881xxx", "name": "板块名"}, ...]
            失败返回 None
        """
        try:
            with self._mootdx_session() as client:
                logger.info("[API调用] mootdx stock_all() 行业板块列表")
                df = client.stock_all()
                if df is None or df.empty:
                    return None
                # 筛选 881xxx 行业板块
                mask = df["code"].astype(str).str.startswith("881")
                industries = df[mask][["code", "name"]].to_dict("records")
                # 清洗 TDX GBK 定长字段的 \u0000 填充/截断空白
                for board in industries:
                    board["name"] = str(board.get("name") or "").replace("\u0000", "").strip()
                industries = [b for b in industries if b["name"]]
                logger.info(f"[Mootdx] 行业板块总数: {len(industries)}")
                return industries
        except Exception as e:
            logger.warning(f"[Mootdx] 行业板块获取失败: {e}")
            return None

    def get_board_quick_change(
        self,
        codes: list[str],
        concurrency: int = 20,
    ) -> dict[str, dict]:
        """批量获取板块快筛数据（每板块取 11 天 K 线，支持 B-lite 累计动量通道）。

        Args:
            codes: 板块代码列表
            concurrency: 并发数

        Returns:
            {code: {
                'change_1d': float,  # 当日涨跌幅
                'change_3d': float,  # 3日累计涨幅
                'change_5d': float,  # 5日累计涨幅
                'has_crash': bool,   # 近5日是否有单日跌幅>5%
            }}
            获取失败的板块不在结果中
        """
        import concurrent.futures

        results: dict[str, dict] = {}

        def fetch_one(code: str) -> tuple[str, Optional[dict]]:
            try:
                with self._mootdx_session() as client:
                    # B-lite: 扩展到 11 根 K 线（offset=10）
                    df = client.index_bars(
                        symbol=code, frequency=9, start=0, offset=10,
                    )
                    if df is None or len(df) < 6:
                        return code, None
                    # 过滤 vol=0 的 bar
                    if "vol" in df.columns:
                        df = df[df["vol"] > 0]
                    if len(df) < 6:
                        return code, None

                    # 计算各时间框架累计涨幅
                    closes = df["close"].astype(float).values
                    n = len(closes)

                    # 1日涨幅
                    change_1d = (closes[-1] - closes[-2]) / closes[-2] * 100 if n >= 2 else 0

                    # 3日累计涨幅
                    change_3d = (closes[-1] - closes[-4]) / closes[-4] * 100 if n >= 4 else 0

                    # 5日累计涨幅
                    change_5d = (closes[-1] - closes[-6]) / closes[-6] * 100 if n >= 6 else 0

                    # 近5日是否有单日跌幅>5%
                    daily_returns = [
                        (closes[i] - closes[i-1]) / closes[i-1] * 100
                        for i in range(max(1, n-5), n)
                    ]
                    has_crash = any(r < -5.0 for r in daily_returns)

                    return code, {
                        'change_1d': round(change_1d, 2),
                        'change_3d': round(change_3d, 2),
                        'change_5d': round(change_5d, 2),
                        'has_crash': has_crash,
                    }
            except Exception:
                return code, None

        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=concurrency,
            ) as executor:
                futures = {
                    executor.submit(fetch_one, code): code for code in codes
                }
                for future in concurrent.futures.as_completed(futures):
                    code, data = future.result()
                    if data is not None:
                        results[code] = data
        except Exception as e:
            logger.warning(f"[Mootdx] 批量快筛失败: {e}")

        return results


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
