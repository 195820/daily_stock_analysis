# -*- coding: utf-8 -*-
"""清空持仓：卖出所有当前持仓"""
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.portfolio_service import PortfolioService

svc = PortfolioService()

# 获取当前持仓
snapshot = svc.get_portfolio_snapshot()
print(f"当前总资产: {snapshot.get('total_equity', 0):,.0f} 元")
print(f"持仓市值: {snapshot.get('total_market_value', 0):,.0f} 元")

accounts = snapshot.get("accounts", [])
if not accounts:
    print("无账户，无需清空")
    sys.exit(0)

account_id = accounts[0]["account_id"]
positions = accounts[0].get("positions", [])

if not positions:
    print("无持仓，无需清空")
    sys.exit(0)

print(f"\n即将卖出 {len(positions)} 只持仓:")
for pos in positions:
    symbol = pos.get("symbol", "")
    name = pos.get("name", symbol)
    qty = pos.get("quantity", 0)
    last_price = pos.get("last_price", 0)
    print(f"  {symbol} {name}: {qty}股 x {last_price:.2f}")

# 卖出所有持仓
today = date.today()
for pos in positions:
    symbol = pos.get("symbol", "")
    name = pos.get("name", symbol)
    qty = pos.get("quantity", 0)
    last_price = pos.get("last_price", 0)

    if qty > 0:
        try:
            svc.record_trade(
                account_id=account_id,
                symbol=symbol,
                trade_date=today,
                side="sell",
                quantity=qty,
                price=last_price,
                fee=5.0,
                note=f"清空持仓 - {name}",
            )
            print(f"  [SELL] {symbol} {name}: {qty}股 @ {last_price:.2f}")
        except Exception as e:
            print(f"  [ERROR] {symbol} {name}: {e}")

# 验证
print("\n清空后快照:")
snapshot2 = svc.get_portfolio_snapshot(account_id=account_id)
print(f"总资产: {snapshot2.get('total_equity', 0):,.0f} 元")
print(f"持仓市值: {snapshot2.get('total_market_value', 0):,.0f} 元")
print(f"现金: {snapshot2.get('total_cash', 0):,.0f} 元")
