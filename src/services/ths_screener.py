# -*- coding: utf-8 -*-
"""
===================================
同花顺问财选股服务
===================================

通过 Playwright 模拟浏览器访问同花顺问财，获取选股结果。
直接使用同花顺的 DDE 计算，与用户在问财中看到的结果完全一致。

优点：
- 无需自己计算 DDE，避免计算差异
- 支持问财所有自然语言查询
- 结果与同花顺 APP/网站完全一致
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from src.config import Config

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class THSStock:
    """同花顺问财返回的股票"""
    code: str
    name: str
    rank: int = 0
    # 从问财 JSON API 提取的额外字段
    price: float = 0.0
    change_pct: float = 0.0
    dde_net_pct: float = 0.0
    dde_5d_sum: float = 0.0   # 近5个交易日 dde 大单净量合计（ambush 口径，查询未请求时为 0）
    turnover_rate: float = 0.0
    volume: float = 0.0
    sector: str = ""  # 概念板块
    industry: str = ""  # 行业板块（同花顺行业分类）


@dataclass
class THSScreenerResult:
    """同花顺问财筛选结果"""
    stocks: List[THSStock] = field(default_factory=list)
    query: str = ""
    total_count: int = 0
    fetch_time: Optional[datetime] = None
    error: Optional[str] = None


# ============================================================
# 同花顺问财选股服务
# ============================================================

class THSScreenerService:
    """
    同花顺问财选股服务

    通过 Playwright 访问问财页面，解析返回结果。
    """

    BASE_URL = "https://search.10jqka.com.cn/html/wencaimobileresult/result.html"

    def __init__(self, config: Config):
        self.config = config
        self._cache: Optional[THSScreenerResult] = None
        self._cache_timestamp: float = 0
        self._cache_key: str = ""
        self._CACHE_TTL = 300  # 5分钟缓存

    @property
    def is_available(self) -> bool:
        return True

    def query(self, question: str, use_cache: bool = True) -> THSScreenerResult:
        """
        查询同花顺问财

        Args:
            question: 自然语言查询，如 "1个交易日前的dde大单净量>=0.8%"
            use_cache: 是否使用缓存

        Returns:
            THSScreenerResult
        """
        # 检查缓存
        if use_cache and question == self._cache_key:
            cache_age = time.time() - self._cache_timestamp
            if self._cache is not None and cache_age < self._CACHE_TTL:
                logger.debug(f"[问财缓存命中] 缓存年龄 {cache_age:.0f}s")
                return self._cache

        result = self._do_query(question)

        if use_cache and result.error is None:
            self._cache = result
            self._cache_timestamp = time.time()
            self._cache_key = question
            logger.info(f"[问财缓存更新] 已缓存 {len(result.stocks)} 条结果")

        return result

    def _do_query(self, question: str) -> THSScreenerResult:
        """执行实际的问财查询（含 JSON API 数据提取）"""
        from playwright.sync_api import sync_playwright

        result = THSScreenerResult(
            query=question,
            fetch_time=datetime.now()
        )

        try:
            # 构建 URL
            encoded_q = quote(question)
            url = f"{self.BASE_URL}?q={encoded_q}&source=ths_mobile_iwencai"

            logger.info(f"[问财] 查询: {question[:50]}...")

            # 用于捕获 JSON API 响应
            captured_json = {"data": None}

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080}
                )
                page = context.new_page()
                page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

                # 监听 get-parser-data JSON API
                def handle_response(response):
                    if 'get-parser-data' in response.url and captured_json["data"] is None:
                        try:
                            captured_json["data"] = response.json()
                        except Exception:
                            pass

                page.on('response', handle_response)
                page.goto(url, timeout=30000)
                page.wait_for_timeout(3000)

                # 滚动加载更多
                for _ in range(10):
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    page.wait_for_timeout(800)

                # 如果 JSON API 未捕获，降级到文本解析
                text = page.inner_text('body')
                browser.close()

            # 优先使用 JSON API 数据（包含完整量价+DDE）
            if captured_json["data"] is not None:
                self._parse_json_api(captured_json["data"], result)
            else:
                # 降级：从文本提取（仅 code/name/rank）
                self._parse_text(text, result)

            result.total_count = len(result.stocks)
            logger.info(f"[问财] 获取到 {result.total_count} 只股票")

        except Exception as e:
            result.error = f"问财查询失败: {e}"
            logger.error(f"[问财] {result.error}")

        return result

    def _parse_json_api(self, json_data: dict, result: THSScreenerResult) -> None:
        """从问财 JSON API 解析完整数据（量价 + DDE + 板块）"""
        data = json_data.get('data', {})
        rows = data.get('data', [])

        seen_codes = set()
        for rank, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                continue

            # 提取代码（格式: 002131.SZ → 002131）
            raw_code = row.get('股票代码', '')
            code = raw_code.split('.')[0] if '.' in raw_code else raw_code
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)

            # 提取各字段（键名带日期后缀，需模糊匹配）
            # 问财字段名可能变更（如 现价→最新价，涨跌幅:前复权→最新涨跌幅），兼容新旧
            name = row.get('股票简称', '')
            price = self._safe_float(
                row.get('最新价') or row.get('现价', 0)
            )
            change_pct = (
                self._find_float_field(row, '最新涨跌幅')
                or self._find_float_field(row, '涨跌幅:前复权')
                or self._find_float_field(row, '涨跌幅')
            )
            dde_net = self._find_float_field(row, 'dde大单净量')
            # 审核修复 2.2：5日累计 DDE 列（键名形如「近5个交易日的dde大单净量合计:xxx」，
            # startswith 匹配不到，需 contains 匹配；与单日列区分开）
            dde_5d_sum = 0.0
            for k, v in row.items():
                if 'dde大单净量合计' in k:
                    dde_5d_sum = self._safe_float(v)
                    break
            turnover = self._safe_float(
                row.get('换手率') or self._find_float_field(row, '换手率')
            )
            volume = self._safe_float(
                row.get('成交量') or self._find_float_field(row, '成交量')
            )
            sector = row.get('所属板块', '')

            # 动态查找行业字段（问财的 key 可能有编码差异）
            industry = ''
            for k in row.keys():
                if '行业' in k and '涨跌' not in k:
                    industry = row.get(k, '')
                    break

            result.stocks.append(THSStock(
                code=code,
                name=name,
                rank=rank,
                price=price,
                change_pct=change_pct,
                dde_net_pct=dde_net,
                dde_5d_sum=dde_5d_sum,
                turnover_rate=turnover,
                volume=volume,
                sector=sector,
                industry=industry,
            ))

        if result.stocks:
            logger.info(f"[问财-JSON] 解析到 {len(result.stocks)} 只（含量价+DDE）")

    def _parse_text(self, text: str, result: THSScreenerResult) -> None:
        """降级：从页面文本提取（仅 code/name/rank）"""
        pattern = r'(\d+)\s*\n\s*\n\s*([^\n]+?)\s*\n\s*(\d{6})\s*\n'
        matches = re.findall(pattern, text)

        seen_codes = set()
        for match in matches:
            rank, name, code = match
            if code not in seen_codes:
                seen_codes.add(code)
                result.stocks.append(THSStock(
                    code=code,
                    name=name.strip(),
                    rank=int(rank)
                ))

    @staticmethod
    def _find_float_field(row: dict, key_prefix: str) -> float:
        """在 row 中模糊匹配带日期后缀的字段名并提取数值"""
        for key, val in row.items():
            if key.startswith(key_prefix):
                return THSScreenerService._safe_float(val)
        return 0.0

    @staticmethod
    def _safe_float(val) -> float:
        """安全转换为 float"""
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def get_dde_screener(self, use_cache: bool = True) -> THSScreenerResult:
        """
        获取 DDE 策略选股结果

        默认查询条件：
        - 1个交易日前的dde大单净量>=0.8%
        - (最低价/1个交易日前的最低价)<1.02
        - (最低价/1个交易日前的最低价)>=1
        - 近2个交易日的区间dde大单净量>=0.8%
        - 上市天数>365天
        """
        query = "1个交易日前的dde大单净量>=0.8%,(最低价/1个交易日前的最低价)<1.02,(最低价/1个交易日前的最低价)>=1,近2个交易日的区间dde大单净量>=0.8%,上市天数>365天"
        return self.query(query, use_cache=use_cache)

    def invalidate_cache(self) -> None:
        """清除缓存"""
        self._cache = None
        self._cache_timestamp = 0
        self._cache_key = ""
        logger.debug("[问财缓存] 已清除")


# ============================================================
# 便捷函数
# ============================================================

def get_ths_dde_stocks(config: Optional[Config] = None) -> List[THSStock]:
    """
    获取同花顺 DDE 选股结果

    Args:
        config: 配置对象，为 None 时自动获取

    Returns:
        股票列表
    """
    if config is None:
        from src.config import get_config
        config = get_config()

    service = THSScreenerService(config)
    result = service.get_dde_screener()

    if result.error:
        logger.error(f"[问财] 获取失败: {result.error}")
        return []

    return result.stocks
