# -*- coding: utf-8 -*-
"""P0 组合模拟引擎的成交红线与记账口径单测。

覆盖实施稿 §7.1：次日开盘成交、双边成本、整手、T+1、涨跌停/停牌/缺失不可成交、
组合净值 = 现金 + 持仓市值、FIFO 闭合往返净收益、同输入确定性。
"""

from __future__ import annotations

import unittest
from datetime import date

from src.got.portfolio_sim import (
    Bar,
    CostModel,
    Order,
    PortfolioSimulator,
    closed_trade_metrics,
    max_drawdown,
)

D0 = date(2026, 11, 2)   # 决策日
D1 = date(2026, 11, 3)   # 成交日
D2 = date(2026, 11, 4)


def bar(price: float, prev: float, *, high: float | None = None,
        low: float | None = None, volume: float = 1000.0, day: date = D1) -> Bar:
    return Bar(
        trade_date=day,
        open=price,
        high=high if high is not None else price,
        low=low if low is not None else price,
        close=price,
        prev_close=prev,
        volume=volume,
    )


def order(symbol: str = "600000", side: str = "buy", qty: int = 1000,
          day: date = D0) -> Order:
    return Order(decision_date=day, symbol=symbol, name="测试", side=side, quantity=qty)


class TestFills(unittest.TestCase):
    def setUp(self) -> None:
        self.cost = CostModel()
        self.sim = PortfolioSimulator(initial_cash=100_000.0, cost_model=self.cost)

    def test_buy_fills_at_open_with_slippage_and_fees(self):
        fills = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order()],
            bars={"600000": bar(10.0, 9.9)},
        )
        fill = fills[0]
        expected_price = 10.0 * (1 + self.cost.slippage_bps / 10000)
        expected_notional = 1000 * expected_price
        expected_fees = max(expected_notional * 0.00025, 5.0) + expected_notional * 0.00001
        self.assertEqual(fill.status, "filled")
        self.assertAlmostEqual(fill.price or 0, expected_price, places=6)
        self.assertAlmostEqual(fill.fees, expected_fees, places=6)
        self.assertAlmostEqual(
            self.sim.cash, 100_000 - expected_notional - expected_fees, places=6
        )
        # 成本基础含买入费用
        self.assertAlmostEqual(
            self.sim.positions["600000"].cost_basis,
            (expected_notional + expected_fees) / 1000,
            places=6,
        )

    def test_lot_rounding(self):
        fills = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order(qty=250)],
            bars={"600000": bar(10.0, 9.9)},
        )
        self.assertEqual(fills[0].filled_quantity, 200)
        self.assertEqual(fills[0].status, "partial")

    def test_below_one_lot_rejected(self):
        fills = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order(qty=50)],
            bars={"600000": bar(10.0, 9.9)},
        )
        self.assertEqual(fills[0].status, "rejected")
        self.assertEqual(fills[0].reason, "lot_size")

    def test_t_plus_1_blocks_same_day_sell_and_allows_next_day(self):
        self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order()],
            bars={"600000": bar(10.0, 9.9)},
        )
        same_day = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order(side="sell")],
            bars={"600000": bar(10.2, 9.9)},
        )
        self.assertEqual(same_day[0].reason, "t_plus_1")
        next_day = self.sim.apply_orders(
            decision_date=D1, trade_date=D2, orders=[order(side="sell", day=D1)],
            bars={"600000": bar(10.2, 10.0, day=D2)},
        )
        self.assertEqual(next_day[0].status, "filled")
        self.assertEqual(next_day[0].filled_quantity, 1000)

    def test_limit_up_blocks_buy(self):
        # 主板 10%：前收 10 → 涨停 11.00；开盘即触板
        fills = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order()],
            bars={"600000": bar(11.0, 10.0)},
        )
        self.assertEqual(fills[0].status, "rejected")
        self.assertEqual(fills[0].reason, "limit_up")

    def test_limit_down_blocks_sell_only_when_sealed(self):
        self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order()],
            bars={"600000": bar(10.0, 9.9)},
        )
        sealed = self.sim.apply_orders(
            decision_date=D1, trade_date=D2, orders=[order(side="sell", day=D1)],
            bars={"600000": bar(9.0, 10.0, high=9.0, low=9.0, day=D2)},
        )
        self.assertEqual(sealed[0].status, "rejected")
        self.assertEqual(sealed[0].reason, "limit_down")
        # 盘中开板（最高价高于跌停价）→ 可成交
        opened = self.sim.apply_orders(
            decision_date=D1, trade_date=D2, orders=[order(side="sell", day=D1)],
            bars={"600000": bar(9.0, 10.0, high=9.5, low=9.0, day=D2)},
        )
        self.assertEqual(opened[0].status, "filled")

    def test_missing_quote_prev_close_and_suspension(self):
        no_quote = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order()], bars={},
        )
        self.assertEqual(no_quote[0].reason, "no_quote")
        no_prev = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order()],
            bars={"600000": Bar(D1, 10.0, 10.0, 10.0, 10.0, prev_close=None)},
        )
        self.assertEqual(no_prev[0].reason, "no_prev_close")
        suspended = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order()],
            bars={"600000": bar(10.0, 9.9, volume=0.0)},
        )
        self.assertEqual(suspended[0].reason, "suspended")
        self.assertEqual(self.sim.rejections.get("no_quote"), 1)

    def test_insufficient_cash_partial_then_rejected(self):
        sim = PortfolioSimulator(initial_cash=6_000.0, cost_model=self.cost)
        partial = sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order(qty=1000)],
            bars={"600000": bar(10.0, 9.9)},
        )
        self.assertEqual(partial[0].status, "partial")
        self.assertEqual(partial[0].reason, "insufficient_cash")
        self.assertGreater(partial[0].filled_quantity, 0)
        self.assertLess(partial[0].filled_quantity, 1000)
        # 现金不足 1 手 → rejected
        sim2 = PortfolioSimulator(initial_cash=500.0, cost_model=self.cost)
        rejected = sim2.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order(qty=1000)],
            bars={"600000": bar(10.0, 9.9)},
        )
        self.assertEqual(rejected[0].reason, "insufficient_cash")
        self.assertEqual(rejected[0].filled_quantity, 0)

    def test_sell_without_position(self):
        fills = self.sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order(side="sell")],
            bars={"600000": bar(10.0, 9.9)},
        )
        self.assertEqual(fills[0].reason, "no_position")

    def test_sells_execute_before_buys_for_cash(self):
        sim = PortfolioSimulator(initial_cash=2_000.0, cost_model=self.cost)
        # 先买入建仓（另一个决策日）
        sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order(qty=100)],
            bars={"600000": bar(10.0, 9.9)},
        )
        # 同日先卖后买：卖出释放资金后买入可成交
        fills = sim.apply_orders(
            decision_date=D1, trade_date=D2,
            orders=[order(side="buy", qty=100, day=D1), order(side="sell", qty=100, day=D1)],
            bars={"600000": bar(10.0, 10.0, day=D2)},
        )
        by_side = {f.side: f for f in fills}
        self.assertEqual(by_side["sell"].status, "filled")
        self.assertEqual(by_side["buy"].status, "filled")


class TestAccounting(unittest.TestCase):
    def test_equity_is_cash_plus_market_value(self):
        sim = PortfolioSimulator(initial_cash=50_000.0)
        sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order(qty=1000)],
            bars={"600000": bar(10.0, 9.9)},
        )
        sim.mark_to_market(D1, {"600000": 10.5})
        expected = sim.cash + 1000 * 10.5
        self.assertAlmostEqual(sim.equity(), expected, places=6)
        self.assertEqual(sim.equity_curve[-1][0], D1)

    def test_closed_trade_net_return_includes_both_sides_costs(self):
        sim = PortfolioSimulator(initial_cash=100_000.0)
        sim.apply_orders(
            decision_date=D0, trade_date=D1, orders=[order()],
            bars={"600000": bar(10.0, 9.9)},
        )
        sim.apply_orders(
            decision_date=D1, trade_date=D2, orders=[order(side="sell", day=D1)],
            bars={"600000": bar(11.0, 10.0, day=D2)},
        )
        self.assertEqual(len(sim.closed_trades), 1)
        trade = sim.closed_trades[0]
        self.assertGreater(trade.net_pnl, 0)
        self.assertLess(trade.net_return, 0.10)   # 含费后低于 10% 毛收益
        self.assertEqual(trade.entry_date, D1)
        self.assertEqual(trade.exit_date, D2)
        metrics = closed_trade_metrics(sim.closed_trades)
        self.assertEqual(metrics["count"], 1)
        self.assertEqual(metrics["wins"], 1)
        self.assertAlmostEqual(metrics["win_rate"], 1.0)

    def test_fifo_lots_and_partial_sells(self):
        sim = PortfolioSimulator(initial_cash=100_000.0)
        sim.apply_orders(decision_date=D0, trade_date=D1, orders=[order(qty=300)],
                         bars={"600000": bar(10.0, 9.9)})
        sim.apply_orders(decision_date=D1, trade_date=D2, orders=[order(qty=200, day=D1)],
                         bars={"600000": bar(12.0, 11.0, day=D2)})
        sim.apply_orders(decision_date=D2, trade_date=date(2026, 11, 5),
                         orders=[order(side="sell", qty=400, day=D2)],
                         bars={"600000": bar(11.0, 12.0, day=date(2026, 11, 5))})
        # 500 股在建 → 卖 400（可卖 500）→ 两笔闭合往返，剩余 100 股
        self.assertEqual(sum(t.quantity for t in sim.closed_trades), 400)
        self.assertEqual(sim.positions["600000"].quantity, 100)
        self.assertEqual(len(sim.positions["600000"].lots), 1)

    def test_split_does_not_create_fake_loss(self):
        """2 拆 1：raw 价从 100 跌到 50，qfq 不变 → 持仓市值不能腰斩。"""
        sim = PortfolioSimulator(initial_cash=100_000.0)
        sim.apply_orders(
            decision_date=D0, trade_date=D1,
            orders=[order(qty=100)],
            bars={"600000": bar(100.0, 99.0)},
            qfq_closes={"600000": 50.0},
        )
        sim.mark_to_market(D2, {"600000": 50.0}, {"600000": 50.0})
        value = sim.market_value()
        fill_price = sim.positions["600000"].lots[0].entry_fill_price
        # 除权只是价格单位变化：按 qfq 换算后经济价值 = 建仓成交价 × 股数
        self.assertAlmostEqual(value, fill_price * 100, places=4)
        self.assertEqual(sim.qfq_mark_fallbacks, 0)

    def test_qfq_missing_falls_back_to_raw_and_counts(self):
        sim = PortfolioSimulator(initial_cash=100_000.0)
        sim.apply_orders(
            decision_date=D0, trade_date=D1,
            orders=[order(qty=100)],
            bars={"600000": bar(10.0, 9.9)},
        )   # 未提供 qfq → lot.entry_qfq=None
        sim.mark_to_market(D2, {"600000": 9.0}, {"600000": 9.0})
        self.assertEqual(sim.qfq_mark_fallbacks, 1)
        self.assertAlmostEqual(sim.positions["600000"].last_price, 9.0)

    def test_max_drawdown(self):
        self.assertAlmostEqual(max_drawdown([1.0, 1.2, 0.9, 1.1]), 0.25, places=6)
        self.assertAlmostEqual(max_drawdown([]), 0.0)

    def test_determinism_same_inputs_same_fills(self):
        def run():
            sim = PortfolioSimulator(initial_cash=80_000.0)
            sim.apply_orders(
                decision_date=D0, trade_date=D1,
                orders=[order("600000", qty=300), order("300001", "sell", 100)],
                bars={"600000": bar(10.0, 9.9), "300001": bar(20.0, 19.8)},
            )
            sim.mark_to_market(D1, {"600000": 10.2, "300001": 20.1})
            return [f.as_dict() for f in sim.fills], sim.snapshot(), sim.equity_curve

        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
