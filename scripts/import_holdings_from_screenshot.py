# -*- coding: utf-8 -*-
"""CLI script to import holdings from broker APP screenshot via OCR.

Usage:
    python scripts/import_holdings_from_screenshot.py --image 1.jpg --account 1
    python scripts/import_holdings_from_screenshot.py --image 1.jpg --account 1 --sync
    python scripts/import_holdings_from_screenshot.py --image 1.jpg --account 1 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.portfolio_ocr_import import (
    PortfolioOcrImportService,
    preview_trades,
)
from src.services.portfolio_service import PortfolioService
from src.repositories.portfolio_repo import PortfolioRepository


def parse_args():
    parser = argparse.ArgumentParser(description='从券商 APP 截图导入持仓（OCR 识别）')
    parser.add_argument('--image', required=True, help='截图路径')
    parser.add_argument('--account', type=int, required=True, help='账户 ID')
    parser.add_argument('--sync', action='store_true', help='同步模式：对比当前持仓生成调整交易（默认是初始化模式）')
    parser.add_argument('--dry-run', action='store_true', help='仅预览不写入')
    parser.add_argument('--date', help='交易日期 (YYYY-MM-DD)，默认今天')
    parser.add_argument('--skip-ocr', action='store_true', help='跳过 OCR，使用手动输入（调试用）')
    return parser.parse_args()


def manual_input_holdings():
    """Manual input mode for testing/fallback."""
    print("手动输入持仓（每行格式：股票名 数量 成本价 现价，空行结束）:")
    print("示例：株冶集团 1400 24.404 26.340")
    print()

    holdings = []
    from src.services.portfolio_ocr_import import HoldingItem

    while True:
        line = input("> ").strip()
        if not line:
            break

        parts = line.split()
        if len(parts) >= 4:
            try:
                holdings.append(HoldingItem(
                    name=parts[0],
                    quantity=int(parts[1]),
                    cost_price=float(parts[2]),
                    current_price=float(parts[3]),
                ))
                print(f"  已添加：{parts[0]} {parts[1]}股 成本{parts[2]} 现价{parts[3]}")
            except ValueError as e:
                print(f"  格式错误：{e}")
        else:
            print("  格式错误，需要 4 个字段")

    return holdings


def main():
    args = parse_args()

    # Validate image exists
    image_path = Path(args.image)
    if not args.skip_ocr and not image_path.exists():
        print(f"错误：截图不存在：{image_path}")
        sys.exit(1)

    # Initialize services
    ocr_service = PortfolioOcrImportService()
    portfolio_service = PortfolioService()
    repo = PortfolioRepository()

    # Parse trade date
    trade_date = None
    if args.date:
        from datetime import date
        trade_date = date.fromisoformat(args.date)

    # Get holdings
    if args.skip_ocr:
        print("[手动输入模式]")
        holdings = manual_input_holdings()
    else:
        print(f"[OCR 识别] 截图：{image_path}")
        ocr_text = ocr_service.ocr_screenshot(str(image_path))
        print(f"[OCR 结果] 识别到 {len(ocr_text.split(chr(10)))} 行文本")

        print("\n[解析持仓]")
        holdings = ocr_service.parse_holdings_from_ocr(ocr_text)

    if not holdings:
        print("\n未识别到持仓，请检查截图或尝试手动输入（--skip-ocr）")
        sys.exit(1)

    print(f"\n识别到 {len(holdings)} 只股票:")
    for h in holdings:
        print(f"  {h.name}: {h.quantity}股 成本{h.cost_price} 现价{h.current_price}")

    # Match stock names to codes
    print("\n[匹配股票代码]")
    unmatched = []
    for h in holdings:
        symbol = ocr_service.match_stock_name(h.name)
        if symbol:
            h.symbol = symbol
            print(f"  {h.name} -> {symbol}")
        else:
            unmatched.append(h)
            print(f"  {h.name} -> ??? (未匹配)")

    if unmatched:
        print(f"\n有 {len(unmatched)} 只股票未匹配到代码：")
        for h in unmatched:
            manual_code = input(f"  请输入 {h.name} 的股票代码 (如 SH600519，空行跳过): ").strip()
            if manual_code:
                h.symbol = manual_code.upper()
                print(f"    已设置：{h.symbol}")
            else:
                print(f"    已跳过")

    # Filter to matched holdings only
    matched_holdings = [h for h in holdings if h.symbol]
    if not matched_holdings:
        print("\n没有匹配到任何股票代码，无法生成交易")
        sys.exit(1)

    # Generate trades
    if args.sync:
        print("\n[同步模式] 对比系统持仓...")
        snapshot = portfolio_service.get_portfolio_snapshot(account_id=args.account)

        # Build system positions dict
        system_positions = {}
        for account in snapshot.get('accounts', []):
            for pos in account.get('positions', []):
                system_positions[pos['symbol']] = {
                    'quantity': pos['quantity'],
                    'last_price': pos.get('last_price', 0.0),
                }

        trades = ocr_service.compute_sync_trades(
            matched_holdings,
            system_positions,
            trade_date=trade_date,
        )
        print(f"需要 {len(trades)} 笔调整交易")
    else:
        print("\n[初始化模式] 生成初始买入交易")
        trades = ocr_service.generate_initial_trades(matched_holdings, trade_date=trade_date)

    # Preview
    print("\n" + preview_trades(trades))

    if args.dry_run:
        print("\n[预览模式] 未写入数据库")
        return

    # Confirm
    confirm = input("\n确认写入？(y/N): ").strip().lower()
    if confirm != 'y':
        print("已取消")
        return

    # Execute trades
    print("\n[写入交易]")
    success_count = 0
    for trade in trades:
        try:
            result = portfolio_service.record_trade(
                account_id=args.account,
                **trade,
            )
            success_count += 1
            print(f"  ✓ {trade['symbol']} {trade['side']} {trade['quantity']:.0f}股 @ {trade['price']:.3f}")
        except Exception as e:
            print(f"  ✗ {trade['symbol']}: {e}")

    print(f"\n完成：{success_count}/{len(trades)} 笔交易写入成功")

    # Show updated snapshot
    print("\n[当前持仓快照]")
    snapshot = portfolio_service.get_portfolio_snapshot(account_id=args.account)
    for account in snapshot.get('accounts', []):
        print(f"账户：{account.get('account_name', '')}")
        print(f"  总资产：{account.get('total_equity', 0):,.2f}")
        print(f"  持仓：")
        for pos in account.get('positions', []):
            name = pos.get('name', '')
            print(f"    {pos['symbol']} {name}: {pos['quantity']:.0f}股 "
                  f"成本{pos['avg_cost']:.3f} 现价{pos['last_price']:.3f} "
                  f"盈亏{pos.get('unrealized_pnl_pct', 0):+.2f}%")


if __name__ == '__main__':
    main()
