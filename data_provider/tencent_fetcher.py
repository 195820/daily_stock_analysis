# -*- coding: utf-8 -*-
"""
===================================
TencentFetcher - 指数 K 线补充源 (Priority 1)
===================================

数据来源：腾讯财经免费公开接口
- K 线: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
- 行情: https://qt.gtimg.cn/q=...

用途：
  Node 1 大盘分析 5 指数 K 线的 fallback 源。
  当前 `_filter_fetchers_for_a_index` 跳过 Mootdx/Efinance，
  叠加 Akshare push2his 被封，指数 K 线实际只剩慢速 yfinance 兜底。
  Tencent (P1) 位于 mootdx(P0) 与 akshare(P1) 之间，
  A 股指数路由中 Mootdx/Efinance 被跳过后 Tencent 成为第一选择。

限制：
  - 免费公共服务，无 SLA，可能随时限流或变更
  - 前复权 K 线上限 800 bars（~3 年），Node 1 需 250 日绰绰有余
  - 不支持 CSI 指数（csi930955 等），后续需 TickFlow 补位
  - 仅支持 6 位 A 股代码 + SH/SZ/BJ 交易所
"""

import json
import logging
import os
import re
import urllib.request
from datetime import datetime
from typing import Optional

import pandas as pd

from .base import (
    BaseFetcher,
    DataFetchError,
    STANDARD_COLUMNS,
    is_bse_code,
    normalize_stock_code,
)

logger = logging.getLogger(__name__)

# 腾讯 K 线接口上限 800 bars（~3 年日线）
_TENCENT_MAX_BARS = 800
# HTTP 超时（秒）
_TENCENT_HTTP_TIMEOUT = int(os.getenv("TENCENT_HTTP_TIMEOUT", "8"))
# K 线端点
_KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
# 行情端点
_QUOTE_ENDPOINT = "https://qt.gtimg.cn/q="


def _to_tencent_symbol(stock_code: str) -> Optional[str]:
    """将标准代码转换为腾讯 symbol 格式。

    转换规则：
      600519.SH / 600519.SS → sh600519
      000001.SZ             → sz000001
      399006.SZ             → sz399006
      000001.SH             → sh000001
      920748.BJ             → bj920748
      600519（裸码）        → 根据首位推断 sh/sz

    仅支持 6 位 A 股代码 + SH/SZ/BJ 交易所后缀。
    无法识别时返回 None。
    """
    code = stock_code.strip()
    upper = code.upper()

    # 去掉 .SH / .SZ / .SS / .BJ 后缀，提取交易所
    exchange = ""
    bare = code
    if "." in code:
        parts = code.rsplit(".", 1)
        if len(parts) == 2:
            bare, suffix = parts[0], parts[1].upper()
            if suffix in ("SH", "SS"):
                exchange = "sh"
            elif suffix == "SZ":
                exchange = "sz"
            elif suffix == "BJ":
                exchange = "bj"

    # 已有 sh/sz/bj 前缀
    if not exchange:
        if upper.startswith("SH") and len(upper) > 2:
            exchange, bare = "sh", upper[2:]
        elif upper.startswith("SZ") and len(upper) > 2:
            exchange, bare = "sz", upper[2:]
        elif upper.startswith("BJ") and len(upper) > 2:
            exchange, bare = "bj", upper[2:]

    # 裸码推断：6/9 开头 → 上海，其余 → 深圳
    if not exchange:
        if bare.isdigit() and len(bare) == 6:
            if bare.startswith(("6", "9")):
                exchange = "sh"
            else:
                exchange = "sz"
        else:
            return None

    # 校验 bare 为 6 位数字
    if not bare.isdigit() or len(bare) != 6:
        return None

    return f"{exchange}{bare}"


class TencentFetcher(BaseFetcher):
    """腾讯财经数据源 — 免费指数 K 线补充源。

    优先级：1（可通过 TENCENT_PRIORITY 环境变量覆盖），注册顺序置于 akshare 之前，
    使其真正落在 mootdx(P0) 与 akshare(P1) 之间（§3.4）。
    能力：daily_data — **仅 A 股指数**（个股路径由 base._filter_fetchers_index_only 门控剔除，
    因其 pct_chg 为 close.pct_change() 计算值、amount 缺失填 0，与交易所权威口径不一致）。
    """

    name = "TencentFetcher"
    priority = int(os.getenv("TENCENT_PRIORITY", "1"))

    def is_available_for_request(self, capability: str = "") -> bool:
        """腾讯接口始终可用（免费公共，无需 token/登录）。

        注：指数/个股门控不在此处（本方法不接收 stock_code），而由
        DataFetcherManager.get_daily_data 的路由过滤（_filter_fetchers_index_only）完成。
        """
        return True

    # ── 核心方法 ──

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str,
    ) -> pd.DataFrame:
        """从腾讯 K 线端点获取前复权日线数据。

        Args:
            stock_code: 标准代码（600519 / 000001.SZ / 399006.SZ 等）
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD

        Returns:
            原始 DataFrame，列名 [date, open, close, high, low, volume_lots, amount?]
        """
        symbol = _to_tencent_symbol(stock_code)
        if symbol is None:
            raise DataFetchError(
                f"TencentFetcher: 无法转换 symbol {stock_code!r}（仅支持 6 位 A 股代码）"
            )

        # 计算请求天数（腾讯按 bars 数返回，需多取一些以覆盖 start_date）
        try:
            d_start = datetime.strptime(start_date, "%Y-%m-%d")
            d_end = datetime.strptime(end_date, "%Y-%m-%d")
            calendar_days = max((d_end - d_start).days, 30)
            # 日历日 → 交易日估算（×0.7），再留 20% 余量
            bars = min(int(calendar_days / 0.7 * 1.2), _TENCENT_MAX_BARS)
        except ValueError:
            bars = _TENCENT_MAX_BARS

        url = f"{_KLINE_ENDPOINT}?param={symbol},day,,,{bars},qfq"
        logger.debug(f"[TencentFetcher] GET {url}")

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://web.sqt.gtimg.cn/",
            })
            resp = urllib.request.urlopen(req, timeout=_TENCENT_HTTP_TIMEOUT)
            raw = resp.read().decode("utf-8")
        except Exception as e:
            raise DataFetchError(f"TencentFetcher HTTP 请求失败: {e}") from e

        # 解析 JSON
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise DataFetchError(f"TencentFetcher JSON 解析失败: {e}") from e

        # 路径: payload["data"][symbol]["qfqday"] 或 ["day"]
        data = payload.get("data", {})
        symbol_data = data.get(symbol)
        if not symbol_data:
            raise DataFetchError(
                f"TencentFetcher: 响应中无 {symbol} 数据, code={payload.get('code')}"
            )

        bars_data = symbol_data.get("qfqday") or symbol_data.get("day")
        if not bars_data:
            raise DataFetchError(
                f"TencentFetcher: {symbol} 无 qfqday/day 字段"
            )

        # 每行: [date, open, close, high, low, volume(股), amount?]
        rows = []
        for bar in bars_data:
            if not isinstance(bar, (list, tuple)) or len(bar) < 5:
                continue
            try:
                row = {
                    "date": bar[0],
                    "open": float(bar[1]),
                    "close": float(bar[2]),
                    "high": float(bar[3]),
                    "low": float(bar[4]),
                    # 腾讯 volume 已经是股（非手），不需要 ×100
                    "volume": int(float(bar[5])) if len(bar) > 5 and bar[5] else 0,
                    "amount": float(bar[6]) if len(bar) > 6 and bar[6] else 0.0,
                }
                rows.append(row)
            except (ValueError, TypeError):
                continue

        if not rows:
            raise DataFetchError(f"TencentFetcher: {symbol} 解析后无有效数据")

        df = pd.DataFrame(rows)
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """标准化列名 + 过滤日期范围。"""
        if df.empty:
            return df

        # 确保 date 列是字符串格式 YYYY-MM-DD
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        df = df.sort_values("date").reset_index(drop=True)

        # 添加 pct_chg（腾讯接口不直接提供，按收盘价计算）
        if "pct_chg" not in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
            df.loc[df.index[0], "pct_chg"] = 0.0

        # 确保 STANDARD_COLUMNS 齐全
        for col in STANDARD_COLUMNS:
            if col not in df.columns:
                df[col] = 0.0 if col != "date" else ""

        return df[STANDARD_COLUMNS]

    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """通过腾讯行情接口获取股票/指数名称。"""
        symbol = _to_tencent_symbol(stock_code)
        if symbol is None:
            return None

        url = f"{_QUOTE_ENDPOINT}{symbol}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=_TENCENT_HTTP_TIMEOUT)
            data = resp.read().decode("gbk")
            # 格式: v_sh000001="1~上证指数~000001~3835.06~..."
            # 字段[1]=名称
            parts = data.split("~")
            if len(parts) >= 2 and parts[1]:
                return parts[1].strip()
        except Exception as e:
            logger.debug(f"[TencentFetcher] get_stock_name 失败: {e}")
        return None

    def get_stock_quick_change(
        self,
        codes: list[str],
        concurrency: int = 20,
    ) -> dict[str, dict]:
        """批量获取个股当日涨跌幅（D0 持仓异动归因取数，单次 HTTP 多 symbol）。

        对标 Node 2 `get_board_quick_change` 的批量契约：**单次请求取全部标的**（腾讯
        qt.gtimg.cn 支持 `q=sym1,sym2,...` 逗号拼接），非逐股循环；`concurrency` 仅为与
        板块快筛同签名，本实现不用并发。获取失败/无此标的**缺席结果、不抛异常**，
        调用方按缺源处理（news_pulse 三维归因据此诚实降级）。

        Returns:
            {入参 code: {"change_pct": float|None, "price": float|None, "name": str}}
            腾讯字段序（split('~') 后）：[1]名称 [3]最新价 [32]涨跌幅%；短 payload 跳过。
        """
        if not codes:
            return {}
        # symbol → 原始入参 code（返回键沿用调用方的 code，便于回查）
        sym_to_code: dict[str, str] = {}
        for c in codes:
            sym = _to_tencent_symbol(c)
            if sym:
                sym_to_code[sym] = c
        if not sym_to_code:
            return {}

        url = _QUOTE_ENDPOINT + ",".join(sym_to_code.keys())
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=_TENCENT_HTTP_TIMEOUT)
            raw = resp.read().decode("gbk", errors="ignore")
        except Exception as e:
            logger.debug(f"[TencentFetcher] get_stock_quick_change HTTP 失败: {e}")
            return {}

        results: dict[str, dict] = {}
        for line in raw.split(";"):
            m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
            if not m:
                continue
            sym, payload = m.group(1), m.group(2)
            code = sym_to_code.get(sym)
            if not code:
                continue
            fields = payload.split("~")
            if len(fields) < 33:
                continue
            try:
                change_pct = float(fields[32]) if fields[32] else None
                price = float(fields[3]) if fields[3] else None
            except (ValueError, TypeError):
                continue
            results[code] = {
                "change_pct": change_pct,
                "price": price,
                "name": fields[1].strip(),
            }
        return results
