# -*- coding: utf-8 -*-
"""
===================================
DDE 大单净量数据获取服务
===================================

数据来源：东方财富 DDE (Data Decision Engine)
通过 Playwright 模拟浏览器获取全市场资金流数据。

新 DDE 算法: 大单净流入金额 / 流通市值 × 100%
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from src.config import Config

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class DdeSnapshot:
    """单只股票的 DDE 快照"""
    code: str
    name: str = ""
    dde_big_order: float = 0.0      # 新DDE: 大单净流入金额/流通市值(%)
    dde_east_order: float = 0.0     # 东财原始: 大单净流入/成交额(%)
    circ_mv: float = 0.0            # 流通市值（元）
    big_net_amount: float = 0.0     # 大单净流入金额（元）
    dde_mid_order: float = 0.0
    dde_small_order: float = 0.0
    fetched_at: Optional[datetime] = None


@dataclass
class DdeMarketResult:
    """全市场 DDE 采集结果"""
    snapshots: Dict[str, DdeSnapshot] = field(default_factory=dict)
    fetch_time: Optional[datetime] = None
    source: str = "eastmoney_playwright"
    error: Optional[str] = None


# ============================================================
# DDE 数据获取服务
# ============================================================

class DdeFetcher:
    """
    DDE 大单净量数据获取服务

    使用 Playwright 模拟浏览器获取全市场资金流数据。
    新 DDE = 大单净流入金额 / 流通市值 × 100%
    """

    _CACHE_TTL = 300  # 缓存有效期（秒）

    def __init__(self, config: Config):
        self.config = config
        self._cache: Optional[DdeMarketResult] = None
        self._cache_timestamp: float = 0
        self._lock = threading.RLock()

    @property
    def is_available(self) -> bool:
        return True

    def fetch_market_dde(
        self,
        stock_codes: Optional[List[str]] = None,
        use_cache: bool = True
    ) -> DdeMarketResult:
        """获取全市场 DDE 数据"""
        # 检查缓存
        if use_cache:
            with self._lock:
                cache_age = time.time() - self._cache_timestamp
                if self._cache is not None and cache_age < self._CACHE_TTL:
                    logger.debug(f"[DDE缓存命中] 缓存年龄 {cache_age:.0f}s")
                    return self._cache

        # 获取新数据
        result = self._do_fetch(stock_codes)

        # 更新缓存
        if use_cache and result.error is None:
            with self._lock:
                self._cache = result
                self._cache_timestamp = time.time()
                logger.info(f"[DDE缓存更新] 已缓存 {len(result.snapshots)} 条")

        return result

    def get_stock_dde(self, stock_code: str, use_cache: bool = True) -> Optional[DdeSnapshot]:
        """获取单只股票 DDE 数据"""
        result = self.fetch_market_dde(use_cache=use_cache)
        return result.snapshots.get(stock_code)

    def get_dde_range(self, stock_code: str, days: int = 2, use_cache: bool = True) -> Optional[float]:
        """获取区间 DDE"""
        snapshot = self.get_stock_dde(stock_code, use_cache=use_cache)
        if snapshot is None:
            return None
        return snapshot.dde_big_order

    def _do_fetch(self, stock_codes: Optional[List[str]] = None) -> DdeMarketResult:
        """使用 Playwright 批量获取全市场资金流数据"""
        from playwright.sync_api import sync_playwright

        result = DdeMarketResult(fetch_time=datetime.now())

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[DDE获取] 尝试 {attempt}/{max_retries}...")
                self._do_fetch_with_playwright(stock_codes, result)
                if result.snapshots or result.error:
                    break
            except Exception as e:
                logger.warning(f"[DDE获取] 尝试 {attempt} 失败: {e}")
                if attempt < max_retries:
                    time.sleep(2)

        return result

    def _do_fetch_with_playwright(self, stock_codes: Optional[List[str]], result: DdeMarketResult) -> None:
        """实际执行 Playwright 获取"""
        from playwright.sync_api import sync_playwright

        try:
            start_time = time.time()

            with sync_playwright() as p:
                # 启动浏览器（直连，隐藏自动化标志）
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080}
                )
                page = context.new_page()
                # 隐藏 webdriver 标志
                page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

                # 1. 先访问东财主页（获取 Cookie）
                page.goto('https://www.eastmoney.com/', timeout=30000)
                page.wait_for_timeout(2000)

                # 2. 获取全市场资金流数据（分页获取，每页100条）
                logger.info("[DDE获取] 获取全市场资金流数据...")
                all_items = []
                page_size = 100  # API 限制每页最多100条
                page_num = 1
                total_pages = 60  # 约5600只股票，需要56页

                while page_num <= total_pages:
                    url = 'https://push2.eastmoney.com/api/qt/clist/get'
                    params = {
                        'pn': str(page_num),
                        'pz': str(page_size),
                        'po': '1',
                        'np': '1',
                        'fltt': '2',
                        'invt': '2',
                        'ut': 'b2884a393a59ad64002292a3e90d46a5',
                        'fid': 'f62',
                        'fs': 'm:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23,m:1+t:3,m:0+t:7,m:1+t:3',
                        'fields': 'f12,f14,f2,f3,f20,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87'
                    }

                    full_url = url + '?' + '&'.join(f'{k}={v}' for k, v in params.items())
                    page.goto(full_url, timeout=30000)

                    text = page.inner_text('body')
                    data = json.loads(text)

                    if data.get('data') and data['data'].get('diff'):
                        items = data['data']['diff']
                        if not items:
                            break
                        all_items.extend(items)

                        # 进度日志
                        if page_num % 10 == 0:
                            logger.info(f"[DDE获取] 进度: {page_num}/{total_pages} 页, 已获取 {len(all_items)} 条")

                        # 如果返回数量小于 page_size，说明已经是最后一页
                        if len(items) < page_size:
                            break
                        page_num += 1

                        # 每10页暂停一下，避免被限制
                        if page_num % 10 == 0:
                            page.wait_for_timeout(1000)
                    else:
                        break

                browser.close()

            elapsed = time.time() - start_time

            if all_items:
                logger.info(f"[DDE获取] 共获取 {len(all_items)} 条，耗时 {elapsed:.1f}s")
                self._parse_fund_flow_data(all_items, stock_codes, result)
            else:
                result.error = "返回数据为空"
                logger.error(f"[DDE获取] {result.error}")

        except Exception as e:
            result.error = f"Playwright 获取失败: {e}"
            logger.error(f"[DDE获取] {result.error}")

    def _parse_fund_flow_data(
        self,
        items: List[Dict],
        filter_codes: Optional[List[str]],
        result: DdeMarketResult
    ) -> None:
        """解析资金流数据，计算新 DDE"""
        filter_set = set(filter_codes) if filter_codes else None

        for item in items:
            try:
                code = str(item.get('f12', '')).strip()
                if not code or len(code) != 6:
                    continue

                # 过滤
                if filter_set and code not in filter_set:
                    continue

                name = str(item.get('f14', ''))
                circ_mv = self._safe_float(item.get('f20', 0))  # 流通市值（元）
                big_net_amount = self._safe_float(item.get('f72', 0))  # 大单净流入金额（元）
                east_dde = self._safe_float(item.get('f75', 0))  # 东财原始大单净占比

                # 计算新 DDE = 大单净流入金额 / 流通市值 * 100%
                if circ_mv > 0:
                    new_dde = big_net_amount / circ_mv * 100
                else:
                    new_dde = 0.0

                mid_dde = self._safe_float(item.get('f81', 0))
                small_dde = self._safe_float(item.get('f87', 0))

                snapshot = DdeSnapshot(
                    code=code,
                    name=name,
                    dde_big_order=new_dde,
                    dde_east_order=east_dde,
                    circ_mv=circ_mv,
                    big_net_amount=big_net_amount,
                    dde_mid_order=mid_dde,
                    dde_small_order=small_dde,
                    fetched_at=result.fetch_time,
                )
                result.snapshots[code] = snapshot

            except Exception as e:
                logger.debug(f"[DDE解析] 跳过: {e}")
                continue

        logger.info(f"[DDE解析] 成功解析 {len(result.snapshots)} 只股票")

    @staticmethod
    def _safe_float(value: Any) -> float:
        if value is None or value == '-':
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def invalidate_cache(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_timestamp = 0
            logger.debug("[DDE缓存] 已清除")
