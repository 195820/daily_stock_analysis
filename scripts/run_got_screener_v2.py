# -*- coding: utf-8 -*-
"""
GOT 驱动选股脚本 v2

动态读取每日 Phase 1 GOT 输出，从主线/埋伏板块成分股中精选 Top 2-3 只
用于波段操作（持仓周期约 2 周）

使用方法：
    python scripts/run_got_screener_v2.py [--date YYYY-MM-DD] [--top N]

示例：
    # 使用今日 GOT 输出
    python scripts/run_got_screener_v2.py

    # 使用指定日期的 GOT 输出
    python scripts/run_got_screener_v2.py --date 2026-07-29

    # 输出 Top 5
    python scripts/run_got_screener_v2.py --top 5
"""

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import Config
from src.services.stock_screener import StockScreenerPipeline

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="GOT 驱动选股脚本 v2")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="GOT 运行日期 (YYYY-MM-DD)，默认今天"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="输出 Top N 只股票，默认 3"
    )
    args = parser.parse_args()

    # 解析日期
    got_date = None
    if args.date:
        try:
            got_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"日期格式错误: {args.date}，应为 YYYY-MM-DD")
            sys.exit(1)

    logger.info("=" * 80)
    logger.info("GOT 驱动选股脚本 v2")
    logger.info("=" * 80)
    logger.info(f"GOT 日期: {got_date or date.today()}")
    logger.info(f"输出数量: Top {args.top}")
    logger.info("")

    # 初始化配置和选股流水线
    config = Config()
    pipeline = StockScreenerPipeline(config)

    # 运行 GOT 选股
    logger.info("开始运行 GOT 选股...")
    result = pipeline.run_got_mode(got_date=got_date, top_n=args.top)

    # 检查结果
    if result.error:
        logger.error(f"选股失败: {result.error}")
        sys.exit(1)

    if not result.stocks:
        logger.warning("未找到满足条件的股票")
        sys.exit(0)

    # 打印结果
    logger.info("")
    logger.info("=" * 80)
    logger.info(f"选股结果 (Top {len(result.stocks)})")
    logger.info("=" * 80)
    logger.info("")

    for i, stock in enumerate(result.stocks, 1):
        print(f"\n{'='*80}")
        print(f"#{i} {stock.code} {stock.name}")
        print(f"{'='*80}")
        print(f"综合评分: {stock.score:.1f}/100")
        print(f"所属板块: {stock.sector_name} ({stock.sector_type})")
        print(f"板块强度: {stock.sector_score:.0f}")
        print("")
        print("各维度评分:")
        print(f"  - 资金面: {stock.capital_score:.1f}/100 (权重 30%)")
        print(f"  - 消息面: {stock.news_score:.1f}/100 (权重 20%)")
        print(f"  - 板块强度: {stock.sector_score:.1f}/100 (权重 20%)")
        print(f"  - 技术面(波段): {stock.tech_wave_score:.1f}/100 (权重 15%)")
        print(f"  - 风险(安全): {stock.risk_score_safe:.1f}/100 (权重 15%)")
        print("")

        if stock.backtest_snapshot:
            snapshot = stock.backtest_snapshot
            print("交易建议:")
            print(f"  - 买入价: {snapshot['entry_price']:.2f} 元")
            print(f"  - 目标价: {snapshot['target_price']:.2f} 元 (+{snapshot['target_pct']:.0f}%)")
            print(f"  - 止损价: {snapshot['stop_loss_price']:.2f} 元 ({snapshot['stop_loss_pct']:.0f}%)")
            print(f"  - 持仓周期: 10-15 个交易日")
            print("")

        print("推荐理由:")
        print(stock.ai_reasoning)
        print("")

    # 汇总
    logger.info("")
    logger.info("=" * 80)
    logger.info("汇总")
    logger.info("=" * 80)
    logger.info(f"扫描股票数: {result.total_scanned}")
    logger.info(f"精选股票数: {result.matched_count}")
    logger.info(f"执行耗时: {result.elapsed_seconds:.1f}s")
    logger.info("")

    # 保存位置
    got_date_str = (got_date or date.today()).strftime("%Y%m%d")
    output_file = Path(f"data/got_screener/{got_date_str}.json")
    if output_file.exists():
        logger.info(f"结果已保存到: {output_file}")
        logger.info("")
        logger.info("明天可运行回测脚本:")
        logger.info(f"  python scripts/backtest_got_screener.py --date {got_date or date.today()}")

    logger.info("")
    logger.info("完成！")


if __name__ == "__main__":
    main()
