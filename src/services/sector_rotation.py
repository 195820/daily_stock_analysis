# -*- coding: utf-8 -*-
"""
===================================
板块轮动分析服务
===================================

从东方财富获取板块涨跌数据，分析板块轮动趋势，输出：
- 强势板块排名（概念 + 行业）
- 板块强度评分（多维加权）
- 轮动阶段判断（崛起/加速/见顶/衰退）
- 龙头股识别

数据源:
- 板块列表: EastMoney push2 API (m:90 t:3=概念, t:2=行业)
- 板块K线: EastMoney push2his API (secid=90.BK0xxx)
- 成分股: EastMoney push2 API (fs=b:BK0xxx)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import Config

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class SectorStrength:
    """板块强度数据"""
    name: str                         # 板块名称 (如 "人工智能")
    code: str = ""                    # 板块代码 (如 "BK0711")
    board_type: str = "concept"       # "concept" | "industry"
    change_pct_today: float = 0.0     # 今日涨跌幅 %
    change_pct_3d: float = 0.0        # 近3日累计涨跌 %
    change_pct_5d: float = 0.0        # 近5日累计涨跌 %
    strength_score: float = 0.0       # 综合强度评分 0-100
    rotation_phase: str = "unknown"   # emerging/accelerating/peaking/declining/unknown
    leader_stocks: List[Dict] = field(default_factory=list)  # [{code, name, change_pct}]
    capital_flow: Optional[float] = None   # 资金净流入（亿元）
    stock_count: int = 0              # 板块成分股数量
    up_ratio: float = 0.0             # 板块内上涨比例 0-1
    history: List[Dict] = field(default_factory=list)  # 历史K线 [{date, close, change_pct, volume, amount}]


@dataclass
class SectorRotationResult:
    """板块轮动分析结果"""
    strong_sectors: List[SectorStrength] = field(default_factory=list)      # 强势板块排名
    emerging_sectors: List[SectorStrength] = field(default_factory=list)    # 新晋崛起板块
    declining_sectors: List[SectorStrength] = field(default_factory=list)   # 衰退板块
    all_sectors: List[SectorStrength] = field(default_factory=list)         # 全部分析过的板块
    rotation_signals: List[Dict] = field(default_factory=list)              # 轮动信号
    market_theme: str = ""              # 市场主线概括
    total_sectors: int = 0              # 分析的板块总数
    fetched_at: Optional[datetime] = None


# ============================================================
# EastMoney HTTP 客户端
# ============================================================

class _EastMoneyClient:
    """东方财富板块数据 HTTP 客户端。

    push2 东财 API 对 Python TLS 指纹有反爬封锁，
    需要通过系统代理（HTTP_PROXY）发起请求。
    """

    # 板块列表 API（概念 t:3, 行业 t:2）
    _BOARD_LIST_URL = "https://79.push2.eastmoney.com/api/qt/clist/get"
    # 板块K线历史 API
    _BOARD_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    # 成分股 API
    _CONSTITUENT_URL = "https://79.push2.eastmoney.com/api/qt/clist/get"

    _REQUEST_TIMEOUT = 15
    _COMMON_UT = "bd1d9ddb04089700cf9c27f6f7426281"

    def __init__(self):
        self._session = requests.Session()
        # trust_env=True（默认值）以读取系统代理，push2 需要代理绕过 TLS 指纹检测
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        })

    def fetch_board_list(
        self,
        board_type: str = "concept",
        top_n: int = 50,
    ) -> List[Dict[str, Any]]:
        """获取板块排行列表（按涨跌幅降序）。

        优先 akshare（单次获取全量，避免分页限流），失败降级到 push2 直连。
        """
        # 优先: akshare（内部处理分页，一次返回全量）
        boards = self._fetch_board_list_akshare(board_type, top_n)
        if boards:
            return boards

        # 降级: push2 直连
        logger.debug(f"akshare 板块列表失败({board_type})，降级到 push2 直连")
        try:
            return self._fetch_board_list_direct(board_type, top_n)
        except Exception as e:
            logger.warning(f"EastMoney push2 板块列表也失败({board_type}): {e}")
            return []

    def _fetch_board_list_direct(
        self,
        board_type: str,
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """通过 EastMoney push2 API 直接获取板块列表。

        支持分页：单页上限 100 条，超过时自动翻页。
        某页失败时返回已获取数据，不抛异常。
        """
        fs_type = "3" if board_type == "concept" else "2"
        page_size = min(top_n, 100)  # API 单页上限约 100
        total_pages = (top_n + page_size - 1) // page_size

        all_items: List[Dict] = []
        for page in range(1, total_pages + 1):
            params = {
                "pn": str(page),
                "pz": str(page_size),
                "po": "1",           # 降序
                "np": "1",
                "ut": self._COMMON_UT,
                "fltt": "2",
                "invt": "2",
                "fid": "f3",         # 按涨跌幅排序
                "fs": f"m:90+t:{fs_type}+f:!50",  # 排除 ST 等
                "fields": "f2,f3,f4,f8,f12,f14,f15,f16,f17,f20,f21,f24,f25,f22,f104,f105",
            }
            try:
                data = self._get_json(self._BOARD_LIST_URL, params)
                diff = data.get("data", {}).get("diff", [])
                if not diff:
                    break
                all_items.extend(diff)
                if len(all_items) >= top_n:
                    break
                time.sleep(0.2)  # 分页间隔
            except Exception as e:
                logger.warning(f"板块列表第 {page} 页获取失败（返回已有 {len(all_items)} 条）: {e}")
                break

        boards = []
        for item in all_items[:top_n]:
            code = str(item.get("f12", ""))
            if not code.startswith("BK"):
                continue
            boards.append({
                "name": str(item.get("f14", "")),
                "code": code,
                "change_pct": _safe_float(item.get("f3")),
                "price": _safe_float(item.get("f2")),
                "turnover_rate": _safe_float(item.get("f8")),
                "up_count": int(_safe_float(item.get("f104")) or 0),
                "down_count": int(_safe_float(item.get("f105")) or 0),
            })
        return boards

    def _fetch_board_list_akshare(
        self,
        board_type: str,
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """通过 akshare 获取板块列表（单次全量，避免分页限流）。"""
        try:
            import akshare as ak
            import pandas as pd

            if board_type == "concept":
                df = ak.stock_board_concept_name_em()
            else:
                df = ak.stock_board_industry_name_em()

            if df is None or df.empty:
                return []

            # 标准化数值列
            numeric_cols = ["涨跌幅", "换手率", "上涨家数", "下跌家数"]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

            # 按涨跌幅降序排序，取 top_n
            if "涨跌幅" in df.columns:
                df = df.sort_values("涨跌幅", ascending=False)
            df = df.head(top_n)

            boards = []
            for _, row in df.iterrows():
                code = str(row.get("板块代码", ""))
                if not code.startswith("BK"):
                    continue
                boards.append({
                    "name": str(row.get("板块名称", "")),
                    "code": code,
                    "change_pct": _safe_float(row.get("涨跌幅", 0)),
                    "price": _safe_float(row.get("最新价", 0)),
                    "turnover_rate": _safe_float(row.get("换手率", 0)),
                    "up_count": int(_safe_float(row.get("上涨家数", 0))),
                    "down_count": int(_safe_float(row.get("下跌家数", 0))),
                })
            return boards
        except Exception as e:
            logger.warning(f"akshare 板块列表获取失败({board_type}): {e}")
            return []

    def fetch_board_kline(
        self,
        board_code: str,
        board_name: str = "",
        board_type: str = "concept",
        start_date: str = "",
        end_date: str = "",
    ) -> List[Dict[str, Any]]:
        """获取板块K线历史数据。

        先尝试 EastMoney push2his API，失败后降级到 akshare。

        Args:
            board_code: 板块代码, 如 "BK0711"
            board_name: 板块名称（akshare 降级时需要）
            board_type: "concept" 或 "industry"
            start_date: "YYYYMMDD"
            end_date: "YYYYMMDD"

        Returns:
            [{date, open, close, high, low, volume, amount}, ...]
        """
        # 优先: EastMoney 直连
        if board_code:
            try:
                klines = self._fetch_board_kline_direct(board_code, start_date, end_date)
                if klines:
                    return klines
            except Exception as e:
                logger.debug(f"EastMoney K线直连失败({board_code}): {e}")

        # 降级: akshare
        if board_name:
            return self._fetch_board_kline_akshare(board_name, board_type, start_date, end_date)
        return []

    def _fetch_board_kline_direct(
        self,
        board_code: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """通过 EastMoney push2his API 获取板块K线。"""
        secid = f"90.{board_code}"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": "101",       # 日K
            "fqt": "0",         # 不复权
            "beg": start_date,
            "end": end_date,
        }
        data = self._get_json(self._BOARD_KLINE_URL, params)
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            return []

        result = []
        for line in klines:
            # 格式: "2025-06-12,1063.81,1068.55,1078.67,1063.81,33642021,37226444950.00"
            parts = line.split(",")
            if len(parts) < 7:
                continue
            result.append({
                "date": parts[0],
                "open": _safe_float(parts[1]),
                "close": _safe_float(parts[2]),
                "high": _safe_float(parts[3]),
                "low": _safe_float(parts[4]),
                "volume": _safe_float(parts[5]),
                "amount": _safe_float(parts[6]),
            })
        return result

    def _fetch_board_kline_akshare(
        self,
        board_name: str,
        board_type: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """通过 akshare 获取板块K线（降级方案）。"""
        try:
            import akshare as ak

            if board_type == "concept":
                df = ak.stock_board_concept_hist_em(
                    symbol=board_name, period="daily",
                    start_date=start_date, end_date=end_date, adjust="",
                )
            else:
                df = ak.stock_board_industry_hist_em(
                    symbol=board_name, period="daily",
                    start_date=start_date, end_date=end_date, adjust="",
                )

            if df is None or df.empty:
                return []

            result = []
            for _, row in df.iterrows():
                result.append({
                    "date": str(row.get("日期", "")),
                    "open": _safe_float(row.get("开盘")),
                    "close": _safe_float(row.get("收盘")),
                    "high": _safe_float(row.get("最高")),
                    "low": _safe_float(row.get("最低")),
                    "volume": _safe_float(row.get("成交量")),
                    "amount": _safe_float(row.get("成交额")),
                })
            return result
        except Exception as e:
            logger.warning(f"akshare 板块K线获取失败({board_name}): {e}")
            return []

    def fetch_board_constituents(
        self,
        board_code: str,
        top_n: int = 10,
    ) -> List[Dict[str, Any]]:
        """获取板块成分股列表（按涨跌幅降序）。

        Args:
            board_code: 板块代码, 如 "BK0711"
            top_n: 返回涨幅前 N 只

        Returns:
            [{code, name, change_pct, price, volume, amount}, ...]
        """
        params = {
            "pn": "1",
            "pz": str(top_n),
            "po": "1",
            "np": "1",
            "ut": self._COMMON_UT,
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": f"b:{board_code}+f:!50",
            "fields": "f2,f3,f4,f5,f6,f12,f14",
            # f2=最新价 f3=涨跌幅 f12=代码 f14=名称 f5=成交量 f6=成交额
        }
        data = self._get_json(self._CONSTITUENT_URL, params)
        diff = data.get("data", {}).get("diff", [])
        stocks = []
        for item in diff:
            stocks.append({
                "code": str(item.get("f12", "")),
                "name": str(item.get("f14", "")),
                "change_pct": _safe_float(item.get("f3")),
                "price": _safe_float(item.get("f2")),
            })
        return stocks

    def _get_json(self, url: str, params: Dict) -> Dict[str, Any]:
        """GET 请求 → JSON 解析。"""
        resp = self._session.get(url, params=params, timeout=self._REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(f"EastMoney HTTP {resp.status_code}: {url}")
        data = resp.json()
        if data.get("rc") != 0:
            raise RuntimeError(f"EastMoney API error: rc={data.get('rc')}")
        return data


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
# SectorRotationService
# ============================================================

class SectorRotationService:
    """
    板块轮动分析服务。

    Usage::

        svc = SectorRotationService(config)
        result = svc.get_rotation_analysis()
    """

    def __init__(self, config: Config):
        self._config = config
        self._client = _EastMoneyClient()
        self._cache: Dict[str, Tuple[SectorRotationResult, float]] = {}
        self._cache_lock = threading.RLock()

    @property
    def is_available(self) -> bool:
        return True  # EastMoney API 无需额外配置

    def get_rotation_analysis(
        self,
        *,
        top_n: Optional[int] = None,
        lookback_days: Optional[int] = None,
        cache_ttl: Optional[int] = None,
    ) -> SectorRotationResult:
        """
        主入口：板块轮动分析。

        流程:
        1. 获取概念 + 行业板块排行（当日快照）
        2. 获取每个板块近 N 日K线数据
        3. 计算多维强度评分
        4. 判断轮动阶段
        5. 识别龙头股
        6. 生成轮动信号 + 市场主线
        """
        cfg = self._config
        top_n = top_n or cfg.sector_rotation_top_n
        lookback_days = lookback_days or cfg.sector_rotation_lookback_days
        cache_ttl = cache_ttl or cfg.sector_rotation_cache_ttl

        cache_key = f"rotation_{top_n}_{lookback_days}"
        cached = self._get_cache(cache_key, cache_ttl)
        if cached is not None:
            return cached

        try:
            result = self._do_analysis(top_n, lookback_days)
        except Exception as e:
            logger.error(f"板块轮动分析失败: {e}")
            result = SectorRotationResult(fetched_at=datetime.now())

        self._set_cache(cache_key, result)
        return result

    def get_sector_stocks(self, board_code: str, top_n: int = 10) -> List[Dict]:
        """获取板块成分股列表（按涨幅降序）。"""
        try:
            return self._client.fetch_board_constituents(board_code, top_n)
        except Exception as e:
            logger.warning(f"获取板块 {board_code} 成分股失败: {e}")
            return []

    # ============================================================
    # 内部实现
    # ============================================================

    def _do_analysis(self, top_n: int, lookback_days: int) -> SectorRotationResult:
        """执行完整的板块轮动分析。"""
        cfg = self._config
        today = datetime.now()
        end_date = today.strftime("%Y%m%d")
        # 多取几天以确保有足够的交易日数据（周末/节假日不开盘）
        start_date = (today - timedelta(days=lookback_days * 2 + 5)).strftime("%Y%m%d")

        # Step 1: 获取概念 + 行业板块排行
        concept_boards = self._fetch_with_retry("concept", top_n)
        industry_boards = self._fetch_with_retry("industry", top_n)

        all_boards = []
        for b in concept_boards:
            b["board_type"] = "concept"
            all_boards.append(b)
        for b in industry_boards:
            b["board_type"] = "industry"
            all_boards.append(b)

        # 过滤行情标签（昨日涨停/首板/连板等），这些不是真正的行业/概念板块
        noise_keywords = ["昨日涨停", "昨日首板", "昨日连板", "昨日打", "昨日涨", "昨日跌"]
        all_boards = [
            b for b in all_boards
            if not any(kw in b.get("name", "") for kw in noise_keywords)
        ]

        if not all_boards:
            logger.warning("板块列表数据为空")
            return SectorRotationResult(fetched_at=datetime.now())

        logger.info(f"板块轮动: 获取到 {len(concept_boards)} 概念 + {len(industry_boards)} 行业 板块, 过滤后 {len(all_boards)} 个")

        # Step 2: 获取每个板块的K线历史 + 计算强度
        sectors: List[SectorStrength] = []
        for board in all_boards:
            code = board["code"]
            board_type = board["board_type"]

            # 获取K线
            try:
                klines = self._client.fetch_board_kline(
                    board_code=code,
                    board_name=board["name"],
                    board_type=board_type,
                    start_date=start_date,
                    end_date=end_date,
                )
                time.sleep(0.1)  # 请求间隔，避免限流
            except Exception as e:
                logger.debug(f"板块 {board['name']}({code}) K线获取失败: {e}")
                klines = []

            # 计算多日涨跌
            change_3d = self._calc_period_change(klines, days=3)
            change_5d = self._calc_period_change(klines, days=5)

            # 上涨比例
            total = board["up_count"] + board["down_count"]
            up_ratio = board["up_count"] / total if total > 0 else 0.5

            # 资金流向估算（用成交金额变化近似）
            capital_flow = self._estimate_capital_flow(klines)

            # 计算强度评分
            strength = self._calculate_strength_score(
                change_today=board["change_pct"],
                change_3d=change_3d,
                change_5d=change_5d,
                capital_flow=capital_flow,
                up_ratio=up_ratio,
            )

            # 判断轮动阶段
            phase = self._detect_rotation_phase(klines)

            sector = SectorStrength(
                name=board["name"],
                code=code,
                board_type=board_type,
                change_pct_today=board["change_pct"],
                change_pct_3d=change_3d,
                change_pct_5d=change_5d,
                strength_score=strength,
                rotation_phase=phase,
                capital_flow=capital_flow,
                stock_count=total,
                up_ratio=round(up_ratio, 3),
                history=klines[-lookback_days:] if klines else [],
            )
            sectors.append(sector)

        # Step 3: 获取龙头股（只对 Top 板块）
        top_sectors = sorted(sectors, key=lambda s: s.strength_score, reverse=True)[:top_n]
        for sector in top_sectors:
            try:
                leaders = self._client.fetch_board_constituents(sector.code, top_n=5)
                sector.leader_stocks = leaders
                time.sleep(0.1)
            except Exception as e:
                logger.debug(f"板块 {sector.name} 龙头股获取失败: {e}")

        # Step 4: 分类输出
        strong = [s for s in top_sectors if s.rotation_phase in ("accelerating", "peaking")]
        emerging = [s for s in top_sectors if s.rotation_phase == "emerging"]
        declining = [s for s in sectors if s.rotation_phase == "declining"][:10]

        # 按强度评分排序
        strong.sort(key=lambda s: s.strength_score, reverse=True)
        emerging.sort(key=lambda s: s.strength_score, reverse=True)
        declining.sort(key=lambda s: s.change_pct_today)

        # Step 5: 生成轮动信号
        signals = self._generate_rotation_signals(sectors)

        # Step 6: 市场主线
        theme = self._generate_market_theme(strong, emerging)

        return SectorRotationResult(
            strong_sectors=strong[:top_n],
            emerging_sectors=emerging[:top_n],
            declining_sectors=declining,
            all_sectors=sectors,
            rotation_signals=signals,
            market_theme=theme,
            total_sectors=len(sectors),
            fetched_at=datetime.now(),
        )

    def _fetch_with_retry(self, board_type: str, top_n: int) -> List[Dict]:
        """带重试的板块列表获取。"""
        for attempt in range(3):
            try:
                boards = self._client.fetch_board_list(board_type, top_n)
                return boards
            except Exception as e:
                logger.warning(f"板块列表({board_type}) 第 {attempt+1} 次获取失败: {e}")
                time.sleep(1.0 * (attempt + 1))
        return []

    def _calc_period_change(self, klines: List[Dict], days: int) -> float:
        """计算近 N 个交易日的累计涨跌幅。"""
        if len(klines) < 2:
            return 0.0
        recent = klines[-days:] if len(klines) >= days else klines
        if len(recent) < 2:
            return 0.0
        start_close = recent[0]["close"]
        end_close = recent[-1]["close"]
        if start_close <= 0:
            return 0.0
        return round((end_close - start_close) / start_close * 100, 2)

    def _estimate_capital_flow(self, klines: List[Dict]) -> Optional[float]:
        """基于成交额变化估算资金流向（亿元）。

        近一日成交额 vs 5日均额，差额近似代表资金流入/出趋势。
        """
        if len(klines) < 3:
            return None
        recent_amount = klines[-1]["amount"]
        avg_amount = sum(k["amount"] for k in klines[-6:-1]) / min(5, len(klines) - 1)
        if avg_amount <= 0:
            return None
        # 差额转亿元
        flow = (recent_amount - avg_amount) / 1e8
        return round(flow, 2)

    def _calculate_strength_score(
        self,
        change_today: float,
        change_3d: float,
        change_5d: float,
        capital_flow: Optional[float],
        up_ratio: float,
    ) -> float:
        """
        多维加权强度评分 (0-100)。

        各维度先归一化到 0-100 分，再加权求和。
        """
        cfg = self._config

        # 今日涨幅分: -5%→0, 0%→50, +5%→100
        today_score = max(0, min(100, (change_today + 5) / 10 * 100))

        # 3日趋势分: 类似归一化
        score_3d = max(0, min(100, (change_3d + 10) / 20 * 100))

        # 5日趋势分
        score_5d = max(0, min(100, (change_5d + 15) / 30 * 100))

        # 资金流向分: -5亿→0, 0→50, +5亿→100
        if capital_flow is not None:
            cf_score = max(0, min(100, (capital_flow + 5) / 10 * 100))
        else:
            cf_score = 50.0  # 无数据时给中间分

        # 上涨比例分: 0%→0, 50%→50, 100%→100
        up_score = up_ratio * 100

        score = (
            cfg.sector_weight_today * today_score
            + cfg.sector_weight_3d * score_3d
            + cfg.sector_weight_5d * score_5d
            + cfg.sector_weight_capital_flow * cf_score
            + cfg.sector_weight_up_ratio * up_score
        )
        return round(max(0, min(100, score)), 1)

    def _detect_rotation_phase(self, klines: List[Dict]) -> str:
        """
        轮动阶段判断。

        基于近 N 日K线的涨跌模式:
        - emerging:     由跌转涨，近期涨幅逐步放大
        - accelerating: 连续上涨，涨幅加速
        - peaking:      涨幅开始收窄，或高位震荡
        - declining:    连续下跌，或涨幅转负
        """
        if len(klines) < 3:
            return "unknown"

        # 取最近 5 个交易日（或全部可用数据）
        recent = klines[-5:] if len(klines) >= 5 else klines

        # 计算每日涨跌
        daily_changes = []
        for i in range(1, len(recent)):
            prev_close = recent[i - 1]["close"]
            curr_close = recent[i]["close"]
            if prev_close > 0:
                daily_changes.append((curr_close - prev_close) / prev_close * 100)
            else:
                daily_changes.append(0.0)

        if not daily_changes:
            return "unknown"

        # 统计上涨/下跌天数
        up_days = sum(1 for c in daily_changes if c > 0)
        down_days = sum(1 for c in daily_changes if c < 0)
        total_days = len(daily_changes)

        # 趋势判断
        last_change = daily_changes[-1] if daily_changes else 0
        avg_change = sum(daily_changes) / len(daily_changes) if daily_changes else 0

        # 涨幅是否加速（后半段 vs 前半段）
        mid = len(daily_changes) // 2
        first_half_avg = sum(daily_changes[:mid]) / max(mid, 1)
        second_half_avg = sum(daily_changes[mid:]) / max(len(daily_changes) - mid, 1)

        if down_days >= total_days * 0.7:
            # 大部分时间下跌
            if last_change > 1.0 and second_half_avg > first_half_avg:
                return "emerging"  # 由跌转涨
            return "declining"

        if up_days >= total_days * 0.7:
            # 大部分时间上涨
            if second_half_avg > first_half_avg * 1.2 and second_half_avg > 1.0:
                return "accelerating"  # 涨幅加速
            elif second_half_avg < first_half_avg * 0.8 or last_change < 0:
                return "peaking"  # 涨幅收窄
            else:
                return "accelerating"  # 持续上涨

        # 涨跌参半
        if avg_change > 0.5:
            return "accelerating"
        elif avg_change < -0.5:
            return "declining"
        else:
            return "emerging"

    def _generate_rotation_signals(self, sectors: List[SectorStrength]) -> List[Dict]:
        """生成板块轮动信号。"""
        signals = []

        # 找崛起板块（从衰退→崛起的转变）
        emerging = [s for s in sectors if s.rotation_phase == "emerging" and s.strength_score > 40]
        for s in emerging[:5]:
            signals.append({
                "type": "emerging",
                "sector": s.name,
                "description": f"{s.name} 板块崛起，今日涨 {s.change_pct_today:+.2f}%",
                "strength": s.strength_score,
            })

        # 找加速板块
        accelerating = [s for s in sectors if s.rotation_phase == "accelerating" and s.change_pct_today > 2]
        for s in accelerating[:5]:
            signals.append({
                "type": "accelerating",
                "sector": s.name,
                "description": f"{s.name} 板块加速上涨，今日涨 {s.change_pct_today:+.2f}%",
                "strength": s.strength_score,
            })

        # 找衰退信号（强度分高但阶段衰退 → 可能见顶回落）
        peaking = [s for s in sectors if s.rotation_phase in ("peaking", "declining") and s.strength_score > 50]
        for s in peaking[:3]:
            signals.append({
                "type": "warning",
                "sector": s.name,
                "description": f"{s.name} 板块可能见顶回落，今日涨 {s.change_pct_today:+.2f}%",
                "strength": s.strength_score,
            })

        return signals

    def _generate_market_theme(
        self,
        strong: List[SectorStrength],
        emerging: List[SectorStrength],
    ) -> str:
        """生成市场主线概括。"""
        parts = []

        if strong:
            top_names = "、".join(s.name for s in strong[:3])
            parts.append(f"强势板块: {top_names}")

        if emerging:
            em_names = "、".join(s.name for s in emerging[:3])
            parts.append(f"崛起板块: {em_names}")

        if not parts:
            return "市场分化，板块轮动不明显"

        return "；".join(parts)

    # ============================================================
    # 缓存
    # ============================================================

    def _get_cache(self, key: str, ttl: int) -> Optional[SectorRotationResult]:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            result, ts = entry
            if time.time() - ts > ttl:
                del self._cache[key]
                return None
            return result

    def _set_cache(self, key: str, result: SectorRotationResult) -> None:
        with self._cache_lock:
            self._cache[key] = (result, time.time())
