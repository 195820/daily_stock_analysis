# -*- coding: utf-8 -*-
"""GATE-1 · 卖出侧可成交性投影层（纯函数层）。

架构实施报告 docs/got/plans/architecture_optimization_implementation_20260929.md §2 GATE-1。

职责：对退出链（sell/reduce）与买入候选做 A 股可成交性判定——
- 跌停封板（现价 ≤ 跌停价且当日最高价亦贴住跌停价→全天未开板）→ sell 不可成交，顺延复核
- T+1：当日新建仓 → sell 不可成交，顺延次日（与行情无关，仅依赖建仓日）
- 涨停一字（现价 ≥ 涨停价且当日最低价亦贴住涨停价）→ buy 不可成交

三态开关（在消费方 portfolio_decision._allocate 读取，缺省 off 逐字节不变）：
- GOT_TRADABILITY_GATE=shadow → 只给 allocation 打 pending_exit/blocked_reason
  观测标签，不改 quantity/decision（旧路径逐字节不变）
- enabled → 不可成交的 sell 转为顺延（pending_exit 落盘、不进当日成交集合、
  释放资金不计入预算）；不可成交的 buy 直接不进 allocation

降级契约（红线）：行情缺失/超时/无 pre_close → **绝不拦截**，返回
executable=True + blocked_reason=quote_unavailable 告警标签；本模块任何异常
都不得阻塞 Phase 2 主流程。
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradabilityResult:
    """可成交性判定结果。

    executable=True 且 blocked_reason=None → 正常可成交
    executable=True 且 blocked_reason='quote_unavailable' → 降级放行（仅告警）
    executable=False → 被拦截，blocked_reason 给出原因标签
    """
    executable: bool
    blocked_reason: Optional[str] = None


def limit_pct_for_code(stock_code: str, stock_name: str = "") -> float:
    """涨跌停幅度口径：主板 10% / 创业板·科创板 20% / 北交所 30%。

    与 sector_analysis._limit_up_pct_for_code 同前缀模式；本层仅对 A 股代码生效，
    非 6 位数字代码（美股/港股）返回 0.0 表示「不适用」（调用方按放行处理）。

    ST 保守设计（防误拦截）：ST 股实际限幅 5% 但名称识别不可靠（如中字头简称），
    统一按前缀基础幅度计算——若基础幅度 > 真实幅度，价格达不到算出的跌停价
    → 不拦截 → fail-safe（误判为可成交仅少拦，绝不多拦）。
    """
    code = str(stock_code or "")
    if not (code.isdigit() and len(code) == 6):
        return 0.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("920", "43", "83", "87")):  # 北交所
        return 30.0
    return 10.0


def _round_limit(prev_close: float, pct: float) -> float:
    """涨跌停价 = 前收 × (1±pct)，按交易所规则四舍五入到分。

    注：采用 round-half-up（Python round 为银行家舍入，0.5 分边缘 case 会差 1 分），
    故显式用 +0.005 截断实现；1 分钱误差在封板判定上无影响（判定带 ±0.1% 容差）。
    """
    import math
    raw = prev_close * (1 + pct / 100.0)
    return math.floor(raw * 100 + 0.5) / 100.0


def check_tradability(
    *,
    action: str,
    quote_price: Optional[float],
    pre_close: Optional[float],
    quote_low: Optional[float] = None,
    quote_high: Optional[float] = None,
    limit_pct: float = 10.0,
    entry_date: Optional[date] = None,
    today: Optional[date] = None,
) -> TradabilityResult:
    """纯函数可成交性判定（输入快照，不发起 IO；IO 由调用方完成后注入）。

    Args:
        action: buy / sell（reduce 由调用方折算为 sell 口径传入）
        quote_price: 现价
        pre_close: 昨收（缺失 → 放行 + quote_unavailable）
        quote_low / quote_high: 当日最低价/最高价（一字板近似判定用，可选）
        limit_pct: 涨跌停幅度（limit_pct_for_code 派生）
        entry_date / today: T+1 判定输入（仅 sell 有效）

    Returns:
        TradabilityResult —— 除 T+1 外，任何数据不足场景一律放行（绝不因取不到盘口而误判）。
    """
    # ── T+1：当日新建仓不可卖（仅 sell，且与行情无关，前置到数据充分性检查之前）──
    # 修正（2026-09-30 CR#2）：entry_date 来自持仓 DB，即使取不到现价也能判；
    # 不得因行情缺失而旁路 T+1（红线只禁止“因缺行情误拦”，T+1 拦不依赖行情）。
    if (
        action == "sell"
        and entry_date is not None and today is not None
        and entry_date == today
    ):
        return TradabilityResult(executable=False, blocked_reason="t_plus_1")

    # ── 数据不足（除 T+1 外）：放行 + 告警标签（红线：不误判为可成交之外的任何行为）──
    if (
        quote_price is None or quote_price <= 0
        or pre_close is None or pre_close <= 0
        or limit_pct <= 0
    ):
        return TradabilityResult(executable=True, blocked_reason="quote_unavailable")

    limit_up = _round_limit(pre_close, limit_pct)
    limit_down = _round_limit(pre_close, -limit_pct)

    if action == "sell":
        # 跌停封板：现价贴住跌停价，且当日最高价同样贴住跌停（全天未开板）
        tol = pre_close * 0.001
        if quote_price <= limit_down + tol:
            if quote_high is not None and quote_high <= limit_down + tol:
                return TradabilityResult(executable=False, blocked_reason="limit_down")
            # 盘中曾开板 → 视为可成交（限价单可在跌停附近排队成交）
        return TradabilityResult(executable=True)

    if action == "buy":
        tol = pre_close * 0.001
        if quote_price >= limit_up - tol:
            if quote_low is not None and quote_low >= limit_up - tol:
                return TradabilityResult(executable=False, blocked_reason="limit_up")
            return TradabilityResult(executable=True)
        return TradabilityResult(executable=True)

    # 其它 action：不适用，放行
    return TradabilityResult(executable=True)


def fetch_tradability_inputs(symbol: str) -> Dict[str, Any]:
    """获取判定输入（现价/昨收/最高最低），失败返回空 dict（调用方按放行处理）。

    复用 DataFetcherManager.get_realtime_quote 降级链（与 _get_realtime_price 同级路径）。
    """
    try:
        from data_provider.base import DataFetcherManager
        quote = DataFetcherManager().get_realtime_quote(symbol, log_final_failure=False)
        if quote is None:
            return {}
        return {
            "price": getattr(quote, "price", None),
            "pre_close": getattr(quote, "pre_close", None),
            "high": getattr(quote, "high", None),
            "low": getattr(quote, "low", None),
        }
    except Exception as exc:
        logger.debug("[tradability] %s 行情输入获取失败（按放行处理）: %s", symbol, exc)
        return {}


def get_last_entry_date(symbol: str, today: date) -> Optional[date]:
    """查询持仓最近一次建仓交易日（T+1 判定输入），失败返回 None（放行）。"""
    try:
        from src.services.portfolio_service import PortfolioService
        result = PortfolioService().list_trade_events(
            symbol=symbol, side="buy", date_to=today, page=1, page_size=1,
        )
        items = result.get("items") or []
        if not items:
            return None
        raw = items[0].get("trade_date")
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str) and raw:
            return date.fromisoformat(raw[:10])
    except Exception as exc:
        logger.debug("[tradability] %s 建仓日查询失败（T+1 判定跳过）: %s", symbol, exc)
    return None
