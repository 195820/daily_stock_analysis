# -*- coding: utf-8 -*-
"""
GOT 选股回测脚本

读取 GOT 选股结果，模拟触碰目标价/止损价，计算胜率和收益率

使用方法：
    python scripts/backtest_got_screener.py --date YYYY-MM-DD

示例：
    python scripts/backtest_got_screener.py --date 2026-07-29

输出：
    - 每只股票的退出情况（触碰目标价/止损价/持有中）
    - 胜率（触碰目标价的比例）
    - 平均收益率
    - 最大回撤
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from data_provider.base import DataFetcherManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def backtest_stock(stock_data: dict, fetcher_manager: DataFetcherManager) -> dict:
    """回测单只股票

    Args:
        stock_data: 选股结果中的单只股票数据
        fetcher_manager: 数据获取管理器

    Returns:
        回测结果字典
    """
    code = stock_data["code"]
    name = stock_data["name"]
    snapshot = stock_data.get("backtest_snapshot", {})

    if not snapshot:
        logger.warning(f"{code} {name} 无回测快照，跳过")
        return None

    entry_date = snapshot.get("entry_date")
    entry_price = snapshot.get("entry_price")
    target_price = snapshot.get("target_price")
    stop_loss_price = snapshot.get("stop_loss_price")

    if not all([entry_date, entry_price, target_price, stop_loss_price]):
        logger.warning(f"{code} {name} 回测快照不完整，跳过")
        return None

    # 获取从买入日到今天的行情数据
    try:
        start_date = datetime.strptime(entry_date, "%Y-%m-%d").date()
        end_date = date.today()

        df, source = fetcher_manager.get_daily_data(
            code,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat()
        )

        if df is None or df.empty:
            logger.warning(f"{code} {name} 无行情数据，跳过")
            return None

        # 模拟触碰退出
        exit_date = None
        exit_price = None
        exit_reason = None
        max_pnl_pct = 0.0
        min_pnl_pct = 0.0

        for idx, row in df.iterrows():
            current_date = row["date"]
            high = row["high"]
            low = row["low"]
            close = row["close"]

            # 计算当日盈亏
            pnl_pct = (close / entry_price - 1) * 100
            max_pnl_pct = max(max_pnl_pct, pnl_pct)
            min_pnl_pct = min(min_pnl_pct, pnl_pct)

            # 检查是否触碰目标价（最高价 >= 目标价）
            if high >= target_price:
                exit_date = current_date
                exit_price = target_price
                exit_reason = "target_hit"
                break

            # 检查是否触碰止损价（最低价 <= 止损价）
            if low <= stop_loss_price:
                exit_date = current_date
                exit_price = stop_loss_price
                exit_reason = "stop_loss_hit"
                break

        # 如果未触碰，计算当前盈亏
        if exit_date is None:
            latest_close = df.iloc[-1]["close"]
            exit_date = df.iloc[-1]["date"]
            exit_price = latest_close
            exit_reason = "holding"

        # 计算最终盈亏
        final_pnl_pct = (exit_price / entry_price - 1) * 100
        # 统一日期类型（Timestamp 转 date）
        exit_date_obj = exit_date.date() if hasattr(exit_date, 'date') else exit_date
        holding_days = (exit_date_obj - start_date).days

        return {
            "code": code,
            "name": name,
            "entry_date": entry_date,
            "entry_price": entry_price,
            "target_price": target_price,
            "stop_loss_price": stop_loss_price,
            "exit_date": exit_date.isoformat() if hasattr(exit_date, 'isoformat') else str(exit_date),
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pct": round(final_pnl_pct, 2),
            "max_pnl_pct": round(max_pnl_pct, 2),
            "min_pnl_pct": round(min_pnl_pct, 2),
            "holding_days": holding_days,
        }

    except Exception as e:
        logger.error(f"{code} {name} 回测失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="GOT 选股回测脚本")
    parser.add_argument(
        "--date",
        type=str,
        required=True,
        help="选股日期 (YYYY-MM-DD)"
    )
    args = parser.parse_args()

    # 解析日期
    try:
        got_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"日期格式错误: {args.date}，应为 YYYY-MM-DD")
        sys.exit(1)

    logger.info("=" * 80)
    logger.info("GOT 选股回测脚本")
    logger.info("=" * 80)
    logger.info(f"选股日期: {got_date}")
    logger.info("")

    # 读取选股结果
    date_str = got_date.strftime("%Y%m%d")
    input_file = Path(f"data/got_screener/{date_str}.json")

    if not input_file.exists():
        logger.error(f"未找到选股结果文件: {input_file}")
        logger.info("请先运行: python scripts/run_got_screener_v2.py --date {args.date}")
        sys.exit(1)

    with open(input_file, "r", encoding="utf-8") as f:
        screener_result = json.load(f)

    top_stocks = screener_result.get("top_stocks", [])
    if not top_stocks:
        logger.warning("选股结果为空，无法回测")
        sys.exit(0)

    logger.info(f"读取到 {len(top_stocks)} 只股票")
    logger.info("")

    # 初始化数据获取管理器
    fetcher_manager = DataFetcherManager()

    # 回测每只股票
    backtest_results = []
    for stock_data in top_stocks:
        result = backtest_stock(stock_data, fetcher_manager)
        if result:
            backtest_results.append(result)

    if not backtest_results:
        logger.warning("所有股票回测失败")
        sys.exit(1)

    # 打印回测结果
    logger.info("")
    logger.info("=" * 80)
    logger.info("回测结果")
    logger.info("=" * 80)
    logger.info("")

    win_count = 0
    loss_count = 0
    holding_count = 0
    total_pnl_pct = 0.0
    max_pnl_pct = 0.0
    min_pnl_pct = 0.0

    for i, result in enumerate(backtest_results, 1):
        print(f"\n{'='*80}")
        print(f"#{i} {result['code']} {result['name']}")
        print(f"{'='*80}")
        print(f"买入日期: {result['entry_date']}")
        print(f"买入价格: {result['entry_price']:.2f} 元")
        print(f"目标价格: {result['target_price']:.2f} 元 (+{(result['target_price']/result['entry_price']-1)*100:.0f}%)")
        print(f"止损价格: {result['stop_loss_price']:.2f} 元 ({(result['stop_loss_price']/result['entry_price']-1)*100:.0f}%)")
        print("")
        print(f"退出日期: {result['exit_date']}")
        print(f"退出价格: {result['exit_price']:.2f} 元")
        print(f"退出原因: {result['exit_reason']}")
        print(f"持仓天数: {result['holding_days']} 天")
        print("")
        print(f"最终盈亏: {result['pnl_pct']:+.2f}%")
        print(f"最大浮盈: {result['max_pnl_pct']:+.2f}%")
        print(f"最大浮亏: {result['min_pnl_pct']:+.2f}%")

        # 统计
        total_pnl_pct += result['pnl_pct']
        max_pnl_pct = max(max_pnl_pct, result['max_pnl_pct'])
        min_pnl_pct = min(min_pnl_pct, result['min_pnl_pct'])

        if result['exit_reason'] == 'target_hit':
            win_count += 1
            print("结果: ✅ 胜利（触碰目标价）")
        elif result['exit_reason'] == 'stop_loss_hit':
            loss_count += 1
            print("结果: ❌ 失败（触碰止损价）")
        else:
            holding_count += 1
            print("结果: ⏳ 持有中")

    # 汇总统计
    total = len(backtest_results)
    win_rate = (win_count / total * 100) if total > 0 else 0
    avg_pnl_pct = total_pnl_pct / total if total > 0 else 0

    logger.info("")
    logger.info("=" * 80)
    logger.info("汇总统计")
    logger.info("=" * 80)
    logger.info(f"总股票数: {total}")
    logger.info(f"胜利次数: {win_count} (触碰目标价)")
    logger.info(f"失败次数: {loss_count} (触碰止损价)")
    logger.info(f"持有中: {holding_count}")
    logger.info(f"胜率: {win_rate:.1f}%")
    logger.info(f"平均盈亏: {avg_pnl_pct:+.2f}%")
    logger.info(f"最大单笔浮盈: {max_pnl_pct:+.2f}%")
    logger.info(f"最大单笔浮亏: {min_pnl_pct:+.2f}%")
    logger.info("")

    # 保存回测结果
    output_file = Path(f"data/got_screener/{date_str}_backtest.json")
    backtest_data = {
        "got_date": got_date.isoformat(),
        "backtest_date": date.today().isoformat(),
        "total_stocks": total,
        "win_count": win_count,
        "loss_count": loss_count,
        "holding_count": holding_count,
        "win_rate": round(win_rate, 2),
        "avg_pnl_pct": round(avg_pnl_pct, 2),
        "max_pnl_pct": round(max_pnl_pct, 2),
        "min_pnl_pct": round(min_pnl_pct, 2),
        "results": backtest_results,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(backtest_data, f, ensure_ascii=False, indent=2)

    logger.info(f"回测结果已保存到: {output_file}")
    logger.info("")
    logger.info("完成！")


if __name__ == "__main__":
    main()
