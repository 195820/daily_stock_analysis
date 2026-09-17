# 持仓服务整合到选股流程
> **原位置**：`docs/custom/plan_portfolio_integration.md`（2026-09-17 文档结构重排移入当前位置）

> 状态：**已实施并归档**（_enrich_portfolio_context 已落地 src/services/stock_screener.py，开关 SCREENER_PORTFOLIO_ENABLED）

## 目标

让选股系统具备"持仓感知"能力：
1. 已持仓股票不再重复推荐为"买入"，改为"加仓"或"持有"
2. 高评分选股结果自动记录模拟买入交易
3. 选股报告中标注持仓状态

## 改动范围

### 1. `src/services/stock_screener.py` — 新增持仓整合步骤

在 `_enrich_risk_scores` 之后、`_compute_composite_scores` 之前，新增 `_enrich_portfolio_context()`：

```python
def _enrich_portfolio_context(self, result: ScreenerRunResult) -> None:
    """持仓感知整合：标记已持仓股票，调整推荐级别"""
    from src.services.portfolio_service import PortfolioService
    from src.repositories.portfolio_repo import PortfolioRepository

    repo = PortfolioRepository(self.db)
    service = PortfolioService(repo)

    # 获取所有账户的持仓快照
    snapshot = service.get_portfolio_snapshot()
    held_symbols = {}  # symbol → {quantity, avg_cost, unrealized_pnl_pct}
    for account in snapshot.get("accounts", []):
        for pos in account.get("positions", []):
            held_symbols[pos["symbol"]] = {
                "quantity": pos["quantity"],
                "avg_cost": pos["avg_cost"],
                "unrealized_pnl_pct": pos.get("unrealized_pnl_pct"),
            }

    for stock in result.stocks:
        # 标准化代码用于匹配
        from data_provider.base import normalize_stock_code
        norm_code = normalize_stock_code(stock.code)

        if norm_code in held_symbols:
            pos = held_symbols[norm_code]
            stock.portfolio_held = True
            stock.portfolio_quantity = pos["quantity"]
            stock.portfolio_avg_cost = pos["avg_cost"]
            stock.portfolio_pnl_pct = pos["unrealized_pnl_pct"]
            # 已持仓的不改为 "strong_buy"，保持 "buy" 或 "hold"
            if stock.recommendation == "strong_buy":
                stock.recommendation = "buy"
```

ScreenerStock 新增字段：
- `portfolio_held: bool = False`
- `portfolio_quantity: float = 0.0`
- `portfolio_avg_cost: float = 0.0`
- `portfolio_pnl_pct: Optional[float] = None`

### 2. `src/services/stock_screener.py` — 自动记录模拟交易

在 `_save_screener_results` 之后，新增 `_record_simulated_trades()`：

```python
def _record_simulated_trades(self, result: ScreenerRunResult) -> None:
    """为高评分未持仓股票记录模拟买入"""
    if not self.config.screener_auto_trade:
        return

    from src.services.portfolio_service import PortfolioService
    from src.repositories.portfolio_repo import PortfolioRepository

    repo = PortfolioRepository(self.db)
    service = PortfolioService(repo)
    accounts = service.list_accounts()
    if not accounts:
        return

    # 使用第一个活跃账户
    account_id = accounts[0]["id"]

    for stock in result.stocks:
        if stock.portfolio_held:
            continue
        if stock.score < self.config.screener_auto_trade_min_score:
            continue
        if stock.recommendation not in ("strong_buy", "buy"):
            continue

        try:
            service.record_trade(
                account_id=account_id,
                symbol=stock.code,
                trade_date=date.today(),
                side="buy",
                quantity=self.config.screener_auto_trade_quantity,
                price=0.0,  # 模拟买入，价格为 0（后续手动调整）
                note=f"选股自动建仓: 综合评分{stock.score:.1f}",
            )
            stock.signal_tags.append("🆕模拟建仓")
        except Exception as e:
            logger.warning(f"模拟建仓失败 {stock.code}: {e}")
```

### 3. `src/config.py` — 新增配置项

```python
# 持仓整合
screener_portfolio_enabled: bool = True           # 是否启用持仓感知
screener_auto_trade: bool = False                 # 是否自动记录模拟交易
screener_auto_trade_min_score: float = 75.0       # 自动建仓最低评分
screener_auto_trade_quantity: float = 100.0        # 自动建仓数量（股）
```

### 4. `src/services/stock_screener.py` — format_notification 更新

在通知中显示持仓状态：

```
🔥 1. 600519 贵州茅台 — 综合评分 82.0
   技术: 75 | 量比2.1 | DDE+0.8%
   板块: 白酒 (强度85)
   💰 已持仓 100股 成本1800.00 盈利+5.2%
```

### 5. `templates/report_screener.j2` — 模板更新

新增持仓状态展示块。

### 6. `.env.example` — 新增配置说明

```bash
# 选股-持仓整合
SCREENER_PORTFOLIO_ENABLED=true
SCREENER_AUTO_TRADE=false              # 自动记录模拟交易（谨慎开启）
SCREENER_AUTO_TRADE_MIN_SCORE=75
SCREENER_AUTO_TRADE_QUANTITY=100
```

## 执行顺序

1. ScreenerStock 新增持仓字段
2. config.py 新增配置项
3. stock_screener.py 新增 `_enrich_portfolio_context()`
4. stock_screener.py 新增 `_record_simulated_trades()`
5. run() 中接入新步骤
6. format_notification 更新
7. report_screener.j2 更新
8. .env.example 更新
9. 测试
