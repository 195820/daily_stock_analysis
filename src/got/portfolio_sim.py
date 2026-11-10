# -*- coding: utf-8 -*-
"""P0 对照底座：组合模拟成交与记账（纯函数/纯状态，无 IO）。

实施稿：《主题驱动全链路收益改进实施稿_20261105.md》§7.1 成交与收益口径。

职责边界
--------
只做「给定订单 + 给定行情快照 → 确定性成交与组合净值」：
- 决策日收盘后出单，统一在**下一交易日开盘价**成交（调用方负责把 bar 对应到 D+1）；
- 双边交易成本 + 滑点（佣金/印花税/过户费/滑点 bps 全部可配，缺省见 `CostModel`）；
- 100 股买入单位；A 股 T+1（按 lot 建仓日判定，当日买入不可卖）；
- 涨跌停/停牌/报价缺失按**不可成交**处理并留痕（回测口径与生产 fail-open 不同，
  见 §7.2「缺失样本单独计数」；严格拒绝而非降级放行）；
- 成本基础含买入费用，闭合往返按 lot FIFO 计净收益（含双边费用）。

不做：不读归档、不取数、不做组合优化、不做决策变更。这些属于 runner/上层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.got.tradability import check_tradability, limit_pct_for_code

LOT_SIZE = 100

# 未成交/部分成交原因码（全部可审计，禁止静默丢弃）
REASON_NO_QUOTE = "no_quote"              # 当日无该股 bar（停牌/未上市/缓存缺）
REASON_NO_PREV_CLOSE = "no_prev_close"    # 缺前收 → 无法判定涨跌停，按不可成交
REASON_SUSPENDED = "suspended"            # bar 存在但成交量为 0
REASON_LIMIT_UP = "limit_up"              # 开盘触涨停（buy 侧不可买）
REASON_LIMIT_DOWN = "limit_down"          # 跌停封死（sell 侧不可卖）
REASON_T_PLUS_1 = "t_plus_1"              # 当日建仓不可卖
REASON_NO_POSITION = "no_position"        # 无可卖持仓（含卖出被前置卖出耗尽）
REASON_INSUFFICIENT_CASH = "insufficient_cash"
REASON_LOT_SIZE = "lot_size"              # 不足 1 手

STATUS_FILLED = "filled"
STATUS_PARTIAL = "partial"
STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class CostModel:
    """A 股交易成本与滑点（缺省值为保守先验，必须写进 run card）。"""

    commission_rate: float = 0.00025   # 佣金 2.5bp（双边，最低 5 元）
    commission_min: float = 5.0
    stamp_duty_rate: float = 0.0005    # 印花税 5bp（仅卖出）
    transfer_fee_rate: float = 0.00001  # 过户费 0.1bp（双边）
    slippage_bps: float = 5.0          # 单边滑点 5bp

    def buy_fees(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return max(notional * self.commission_rate, self.commission_min) + notional * self.transfer_fee_rate

    def sell_fees(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return (
            max(notional * self.commission_rate, self.commission_min)
            + notional * self.transfer_fee_rate
            + notional * self.stamp_duty_rate
        )

    def buy_price(self, open_price: float) -> float:
        return open_price * (1.0 + self.slippage_bps / 10000.0)

    def sell_price(self, open_price: float) -> float:
        return open_price * (1.0 - self.slippage_bps / 10000.0)

    def as_dict(self) -> Dict[str, float]:
        return {
            "commission_rate": self.commission_rate,
            "commission_min": self.commission_min,
            "stamp_duty_rate": self.stamp_duty_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_bps": self.slippage_bps,
        }


@dataclass(frozen=True)
class Bar:
    """单日行情（未复权，与生产可成交性口径一致）。"""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    prev_close: Optional[float]
    volume: Optional[float] = None


@dataclass(frozen=True)
class Order:
    """决策日生成的订单（数量为股票股数，非手数）。"""

    decision_date: date
    symbol: str
    name: str
    side: str          # buy / sell（reduce 由调用方折算为 sell）
    quantity: int
    lane: str = ""
    reason: str = ""


@dataclass
class Lot:
    quantity: int
    cost_basis: float          # 每股成本（含买入费用，未复权价空间）
    entry_date: date
    entry_fill_price: float = 0.0   # 成交价（含滑点，未复权）
    entry_qfq: Optional[float] = None  # 建仓日前复权收盘（除权除息口径换算锚点）


@dataclass
class Position:
    symbol: str
    name: str
    lots: List[Lot] = field(default_factory=list)
    last_price: float = 0.0
    last_price_date: Optional[date] = None

    @property
    def quantity(self) -> int:
        return sum(lot.quantity for lot in self.lots)

    @property
    def cost_basis(self) -> float:
        qty = self.quantity
        if qty <= 0:
            return 0.0
        return sum(lot.quantity * lot.cost_basis for lot in self.lots) / qty

    def sellable_quantity(self, trade_date: date) -> int:
        return sum(lot.quantity for lot in self.lots if lot.entry_date < trade_date)


@dataclass
class Fill:
    decision_date: date
    trade_date: date
    symbol: str
    name: str
    side: str
    requested_quantity: int
    filled_quantity: int
    price: Optional[float]
    notional: float
    fees: float
    status: str
    reason: str
    cash_after: float
    position_quantity_after: int
    lane: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "decision_date": self.decision_date.isoformat(),
            "trade_date": self.trade_date.isoformat(),
            "symbol": self.symbol,
            "name": self.name,
            "side": self.side,
            "requested_quantity": self.requested_quantity,
            "filled_quantity": self.filled_quantity,
            "price": None if self.price is None else round(self.price, 4),
            "notional": round(self.notional, 2),
            "fees": round(self.fees, 2),
            "status": self.status,
            "reason": self.reason,
            "cash_after": round(self.cash_after, 2),
            "position_quantity_after": self.position_quantity_after,
            "lane": self.lane,
        }


@dataclass
class ClosedTrade:
    symbol: str
    name: str
    entry_date: date
    exit_date: date
    quantity: int
    cost_basis: float        # 每股，含买入费用
    exit_price: float        # 已含卖滑点
    exit_fee_per_share: float
    net_pnl: float
    net_return: float        # 相对含费成本
    lane: str = ""           # mainline / ambush（来自决策 allocation，用于分组披露）

    @property
    def holding_days(self) -> int:
        return (self.exit_date - self.entry_date).days

    def as_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "entry_date": self.entry_date.isoformat(),
            "exit_date": self.exit_date.isoformat(),
            "quantity": self.quantity,
            "cost_basis": round(self.cost_basis, 4),
            "exit_price": round(self.exit_price, 4),
            "net_pnl": round(self.net_pnl, 2),
            "net_return": round(self.net_return, 6),
            "holding_days": self.holding_days,
            "lane": self.lane,
        }


class PortfolioSimulator:
    """确定性组合账本：现金 + 分 lot 持仓。"""

    def __init__(self, initial_cash: float, cost_model: Optional[CostModel] = None):
        if initial_cash <= 0:
            raise ValueError("initial_cash 必须为正")
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.cost = cost_model or CostModel()
        self.positions: Dict[str, Position] = {}
        self.fills: List[Fill] = []
        self.closed_trades: List[ClosedTrade] = []
        self.equity_curve: List[Tuple[date, float]] = []
        self.rejections: Dict[str, int] = {}
        self.partial_counts: Dict[str, int] = {}
        self.stale_marks: int = 0
        self.stale_marks_detail: List[Tuple[str, str]] = []
        self.qfq_mark_fallbacks: int = 0
        self.qfq_fallback_detail: List[str] = []

    # ── 账务 ────────────────────────────────────────────────────────────
    def equity(self) -> float:
        return self.cash + sum(p.quantity * p.last_price for p in self.positions.values())

    def market_value(self) -> float:
        return sum(p.quantity * p.last_price for p in self.positions.values())

    def _position(self, symbol: str, name: str) -> Position:
        pos = self.positions.get(symbol)
        if pos is None:
            pos = Position(symbol=symbol, name=name)
            self.positions[symbol] = pos
        elif name and not pos.name:
            pos.name = name
        return pos

    def _record_rejection(self, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def mark_to_market(
        self,
        trade_date: date,
        closes: Dict[str, float],
        qfq_closes: Optional[Dict[str, float]] = None,
    ) -> None:
        """按「未复权现金 + 前复权收益」双口径计价。

        - 无除权除息（qfq 比例 == raw 比例）：等价于 raw 收盘价，逐字节不变；
        - 有除权除息：持仓价值 = Σ qty × [成交价 × qfq_t/qfq_entry] + 费用（费用不缩放），
          即「分红/送转再投资」的总收益口径；现金与成交仍走未复权真实价格。
        - qfq 缺失 → 回退 raw 收盘并计数（禁止静默插值）。
        """
        for symbol, pos in self.positions.items():
            price = closes.get(symbol)
            qfq_now = (qfq_closes or {}).get(symbol)
            value = None
            if price is not None and price > 0:
                if qfq_now and all(lot.entry_qfq for lot in pos.lots):
                    # 市值只含价格（费用已在现金侧扣除，不资本化进市值）
                    value = sum(
                        lot.quantity * lot.entry_fill_price * (float(qfq_now) / float(lot.entry_qfq))
                        for lot in pos.lots
                    )
                    if pos.quantity > 0:
                        pos.last_price = value / pos.quantity
                        pos.last_price_date = trade_date
                    continue
                if pos.lots and qfq_closes is not None:
                    self.qfq_mark_fallbacks += 1
                    self.qfq_fallback_detail.append(f"{symbol}:{trade_date.isoformat()}")
                pos.last_price = float(price)
                pos.last_price_date = trade_date
            else:
                # 快照缺该股收盘 → 沿用上一有效价，逐条留痕（禁止静默前视/插值）
                self.stale_marks += 1
                self.stale_marks_detail.append(f"{symbol}:{trade_date.isoformat()}")
        self.equity_curve.append((trade_date, self.equity()))

    # ── 成交 ────────────────────────────────────────────────────────────
    def apply_orders(
        self,
        *,
        decision_date: date,
        trade_date: date,
        orders: Sequence[Order],
        bars: Dict[str, Bar],
        qfq_closes: Optional[Dict[str, float]] = None,
    ) -> List[Fill]:
        """按「先卖后买」执行订单，全部结果落 fills，不抛异常。"""
        ordered = sorted(orders, key=lambda o: (0 if o.side == "sell" else 1))
        out: List[Fill] = []
        for order in ordered:
            fill = self._execute_one(order, trade_date, bars, qfq_closes or {})
            self.fills.append(fill)
            out.append(fill)
        return out

    def _execute_one(
        self,
        order: Order,
        trade_date: date,
        bars: Dict[str, Bar],
        qfq_closes: Dict[str, float],
    ) -> Fill:
        bar = bars.get(order.symbol)
        side = order.side

        if bar is None:
            return self._reject(order, trade_date, REASON_NO_QUOTE)
        if bar.volume is not None and bar.volume <= 0:
            return self._reject(order, trade_date, REASON_SUSPENDED)
        if bar.prev_close is None or bar.prev_close <= 0:
            # 回测严格口径：缺前收无法判定封板 → 不可成交（生产 fail-open 不适用）
            return self._reject(order, trade_date, REASON_NO_PREV_CLOSE)
        if bar.open <= 0:
            return self._reject(order, trade_date, REASON_NO_QUOTE)

        limit_pct = limit_pct_for_code(order.symbol, order.name)
        verdict = check_tradability(
            action=side,
            quote_price=bar.open,
            pre_close=bar.prev_close,
            quote_low=bar.low,
            quote_high=bar.high,
            limit_pct=limit_pct,
            entry_date=None,   # T+1 由 lot 精确判定，避免双重逻辑
            today=trade_date,
        )
        if not verdict.executable:
            reason = verdict.blocked_reason or REASON_NO_QUOTE
            return self._reject(order, trade_date, reason)

        if side == "buy":
            return self._execute_buy(order, trade_date, bar, qfq_closes.get(order.symbol))
        if side == "sell":
            return self._execute_sell(order, trade_date, bar)
        return self._reject(order, trade_date, "unsupported_side")

    def _reject(self, order: Order, trade_date: date, reason: str) -> Fill:
        self._record_rejection(reason)
        pos = self.positions.get(order.symbol)
        return Fill(
            decision_date=order.decision_date,
            trade_date=trade_date,
            symbol=order.symbol,
            name=order.name,
            side=order.side,
            requested_quantity=int(order.quantity),
            filled_quantity=0,
            price=None,
            notional=0.0,
            fees=0.0,
            status=STATUS_REJECTED,
            reason=reason,
            cash_after=self.cash,
            position_quantity_after=pos.quantity if pos else 0,
            lane=order.lane,
        )

    def _execute_buy(
        self,
        order: Order,
        trade_date: date,
        bar: Bar,
        qfq_close: Optional[float] = None,
    ) -> Fill:
        price = self.cost.buy_price(bar.open)
        quantity = (int(order.quantity) // LOT_SIZE) * LOT_SIZE
        if quantity <= 0:
            self._record_rejection(REASON_LOT_SIZE)
            return Fill(
                decision_date=order.decision_date, trade_date=trade_date,
                symbol=order.symbol, name=order.name, side="buy",
                requested_quantity=int(order.quantity), filled_quantity=0, price=None,
                notional=0.0, fees=0.0, status=STATUS_REJECTED, reason=REASON_LOT_SIZE,
                cash_after=self.cash,
                position_quantity_after=self.positions.get(order.symbol, Position(order.symbol, order.name)).quantity,
                lane=order.lane,
            )

        while quantity > 0:
            notional = quantity * price
            fees = self.cost.buy_fees(notional)
            if notional + fees <= self.cash + 1e-9:
                break
            quantity -= LOT_SIZE

        if quantity <= 0:
            self._record_rejection(REASON_INSUFFICIENT_CASH)
            pos = self.positions.get(order.symbol)
            return Fill(
                decision_date=order.decision_date, trade_date=trade_date,
                symbol=order.symbol, name=order.name, side="buy",
                requested_quantity=int(order.quantity), filled_quantity=0, price=price,
                notional=0.0, fees=0.0, status=STATUS_REJECTED,
                reason=REASON_INSUFFICIENT_CASH, cash_after=self.cash,
                position_quantity_after=pos.quantity if pos else 0, lane=order.lane,
            )

        notional = quantity * price
        fees = self.cost.buy_fees(notional)
        self.cash -= notional + fees
        cost_basis = (notional + fees) / quantity
        pos = self._position(order.symbol, order.name)
        pos.lots.append(Lot(
            quantity=quantity, cost_basis=cost_basis, entry_date=trade_date,
            entry_fill_price=price, entry_qfq=qfq_close,
        ))
        pos.last_price = bar.close
        pos.last_price_date = trade_date
        status = STATUS_FILLED if quantity == int(order.quantity) else STATUS_PARTIAL
        reason = "" if status == STATUS_FILLED else REASON_INSUFFICIENT_CASH
        if status == STATUS_PARTIAL:
            self.partial_counts[reason] = self.partial_counts.get(reason, 0) + 1
        return Fill(
            decision_date=order.decision_date, trade_date=trade_date,
            symbol=order.symbol, name=order.name, side="buy",
            requested_quantity=int(order.quantity), filled_quantity=quantity,
            price=price, notional=notional, fees=fees, status=status, reason=reason,
            cash_after=self.cash, position_quantity_after=pos.quantity, lane=order.lane,
        )

    def _execute_sell(self, order: Order, trade_date: date, bar: Bar) -> Fill:
        pos = self.positions.get(order.symbol)
        if pos is None or pos.quantity <= 0:
            return self._reject(order, trade_date, REASON_NO_POSITION)

        sellable = pos.sellable_quantity(trade_date)
        if sellable <= 0:
            return self._reject(order, trade_date, REASON_T_PLUS_1)

        price = self.cost.sell_price(bar.open)
        quantity = min((int(order.quantity) // LOT_SIZE) * LOT_SIZE, sellable)
        if quantity <= 0:
            return self._reject(order, trade_date, REASON_LOT_SIZE)

        remaining = quantity
        proceeds = 0.0
        fees_total = 0.0
        # FIFO 消耗可卖 lot
        for lot in list(pos.lots):
            if remaining <= 0:
                break
            if lot.entry_date >= trade_date:
                continue
            take = min(lot.quantity, remaining)
            notional = take * price
            fees = self.cost.sell_fees(notional)
            proceeds += notional - fees
            fees_total += fees
            net_pnl = (notional - fees) - take * lot.cost_basis
            self.closed_trades.append(ClosedTrade(
                symbol=order.symbol,
                name=order.name or pos.name,
                entry_date=lot.entry_date,
                exit_date=trade_date,
                quantity=take,
                cost_basis=lot.cost_basis,
                exit_price=price,
                exit_fee_per_share=fees / take if take else 0.0,
                net_pnl=net_pnl,
                net_return=((notional - fees) / take) / lot.cost_basis - 1.0,
                lane=order.lane,
            ))
            lot.quantity -= take
            remaining -= take

        pos.lots = [lot for lot in pos.lots if lot.quantity > 0]
        self.cash += proceeds
        pos.last_price = bar.close
        pos.last_price_date = trade_date
        status = STATUS_FILLED if quantity == int(order.quantity) else STATUS_PARTIAL
        reason = "" if status == STATUS_FILLED else "insufficient_position"
        if status == STATUS_PARTIAL:
            self.partial_counts[reason] = self.partial_counts.get(reason, 0) + 1
        return Fill(
            decision_date=order.decision_date, trade_date=trade_date,
            symbol=order.symbol, name=order.name, side="sell",
            requested_quantity=int(order.quantity), filled_quantity=quantity,
            price=price, notional=quantity * price, fees=fees_total,
            status=status, reason=reason, cash_after=self.cash,
            position_quantity_after=pos.quantity, lane=order.lane,
        )

    # ── 快照 ────────────────────────────────────────────────────────────
    def snapshot(self) -> Dict[str, object]:
        positions = []
        for pos in sorted(self.positions.values(), key=lambda p: p.symbol):
            if pos.quantity <= 0:
                continue
            positions.append({
                "symbol": pos.symbol,
                "name": pos.name,
                "quantity": pos.quantity,
                "cost_basis": round(pos.cost_basis, 4),
                "last_price": round(pos.last_price, 4),
                "last_price_date": pos.last_price_date.isoformat() if pos.last_price_date else None,
                "market_value": round(pos.quantity * pos.last_price, 2),
                "lots": [
                    {"quantity": lot.quantity, "cost_basis": round(lot.cost_basis, 4),
                     "entry_date": lot.entry_date.isoformat()}
                    for lot in pos.lots
                ],
            })
        return {
            "initial_cash": self.initial_cash,
            "cash": round(self.cash, 2),
            "market_value": round(self.market_value(), 2),
            "equity": round(self.equity(), 2),
            "positions": positions,
            "positions_count": len(positions),
        }


# ── 指标 ────────────────────────────────────────────────────────────────
def max_drawdown(equity_series: Sequence[float]) -> float:
    """最大回撤（正数，0.10 = 10%）。"""
    peak = None
    worst = 0.0
    for value in equity_series:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def closed_trade_metrics(trades: Sequence[ClosedTrade]) -> Dict[str, object]:
    """闭合往返指标：全部为扣除双边成本后的净额。"""
    n = len(trades)
    if n == 0:
        return {
            "count": 0, "wins": 0, "win_rate": None, "avg_net_return": None,
            "median_net_return": None, "avg_win": None, "avg_loss": None,
            "profit_factor": None, "net_pnl_total": 0.0,
        }
    returns = [t.net_return for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    gross_win = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    gross_loss = -sum(t.net_pnl for t in trades if t.net_pnl < 0)
    ordered = sorted(returns)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return {
        "count": n,
        "wins": len(wins),
        "win_rate": len(wins) / n,
        "avg_net_return": sum(returns) / n,
        "median_net_return": median,
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "net_pnl_total": sum(t.net_pnl for t in trades),
    }


def utilization_series(
    equity_curve: Sequence[Tuple[date, float]],
    market_value_curve: Sequence[Tuple[date, float]],
) -> float:
    """平均仓位利用率 = mean(市值/净值)。"""
    pairs = [(eq, mv) for (_, eq), (_, mv) in zip(equity_curve, market_value_curve)]
    if not pairs:
        return 0.0
    return sum(mv / eq for eq, mv in pairs if eq > 0) / len(pairs)


def turnover_ratio(total_notional: float, equity_curve: Sequence[Tuple[date, float]]) -> float:
    if not equity_curve:
        return 0.0
    avg_equity = sum(v for _, v in equity_curve) / len(equity_curve)
    return total_notional / avg_equity if avg_equity > 0 else 0.0
