# -*- coding: utf-8 -*-
"""
一键运行完整选股流水线 (Phase 1-4)

用法:
    python scripts/run_screener.py                        # 默认运行，不推送通知
    python scripts/run_screener.py --notify               # 运行并推送通知
    python scripts/run_screener.py --dry-run              # 等价于默认（不推送）
    python scripts/run_screener.py --source eastmoney     # 使用东方财富数据源
    python scripts/run_screener.py --strategy dde_low     # 指定策略名
    python scripts/run_screener.py --top 20               # 返回 Top 20
"""

import argparse
import io
import logging
import os
import sys
import time

# 确保项目根目录在 sys.path 中
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 确保 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger("run_screener")


def parse_args():
    parser = argparse.ArgumentParser(description="一键选股脚本 (Phase 1-4)")
    parser.add_argument(
        "--notify", action="store_true",
        help="推送通知到已配置的渠道",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅运行不推送（默认行为）",
    )
    parser.add_argument(
        "--source", default="ths", choices=["ths", "eastmoney"],
        help="数据源: ths(同花顺问财) / eastmoney(东方财富)",
    )
    parser.add_argument(
        "--strategy", default="dde_low_screener",
        help="策略名称 (默认 dde_low_screener)",
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="返回 Top N 结果",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="开启 DEBUG 日志",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 65)
    print("  选股流水线 (Phase 1-4)")
    print("  同花顺问财 → 技术评分 → 板块整合 → 舆情整合 → 风险评估 → AI评分")
    print("=" * 65)
    print()

    # 初始化配置
    from src.config import get_config
    config = get_config()

    # 运行选股流水线
    from src.services.stock_screener import StockScreenerPipeline
    screener = StockScreenerPipeline(config)

    start_time = time.time()
    result = screener.run(
        strategy_name=args.strategy,
        data_source=args.source,
        top_n=args.top,
    )
    elapsed = time.time() - start_time

    # 输出结果
    print()
    if result.error:
        print(f"❌ 选股失败: {result.error}")
        return 1

    # 终端摘要
    source_label = "同花顺问财" if result.data_source == "ths" else "东方财富"
    print(f"数据源: {source_label} | 策略: {result.strategy_name}")
    print(f"满足: {result.matched_count} 只 | 耗时: {elapsed:.1f}s")
    print()

    if not result.stocks:
        print("未找到满足条件的股票")
        return 0

    # 详细输出
    print("━" * 65)
    print(f"{'排名':>4}  {'代码':<8} {'名称':<10} {'评分':>6}  {'技术':>5} {'板块':>5} {'舆情':>5} {'风险':>5}")
    print("━" * 65)

    for i, s in enumerate(result.stocks, 1):
        tags = ""
        if s.signal_tags:
            tags = " ".join(s.signal_tags[:2])
        if s.risk_tags:
            tags += " ⚠" + "/".join(s.risk_tags[:2])

        print(
            f"{i:>4}  {s.code:<8} {s.name:<10} "
            f"{s.score:>6.1f}  "
            f"{s.technical_score:>5.0f} "
            f"{s.sector_score:>5.0f} "
            f"{(s.sentiment_score + 1) / 2 * 100 if s.heat_score > 0 or s.sentiment_score != 0 else 50:>5.0f} "
            f"{s.risk_score:>5.0f}  "
            f"{tags}"
        )

        # AI 点评
        if s.ai_reasoning:
            print(f"      💡 {s.ai_reasoning}")

        # 板块信息
        if s.sector_name:
            print(f"      📊 {s.sector_name} (强度{s.sector_score:.0f})")

    print("━" * 65)

    # 五维评分汇总
    print()
    avg_tech = sum(s.technical_score for s in result.stocks) / len(result.stocks)
    avg_sector = sum(s.sector_score for s in result.stocks) / len(result.stocks)
    avg_risk = sum(s.risk_score for s in result.stocks) / len(result.stocks)
    sector_hit = sum(1 for s in result.stocks if s.sector_name)
    sentiment_hit = sum(1 for s in result.stocks if s.heat_score > 0)
    print(f"五维汇总: 技术均分{avg_tech:.0f} | 板块命中{sector_hit}/{len(result.stocks)} | "
          f"舆情命中{sentiment_hit}/{len(result.stocks)} | 风险均分{avg_risk:.0f}")

    # 通知推送
    should_notify = args.notify and not args.dry_run
    if should_notify:
        print("\n📤 推送通知中...")
        try:
            from src.notification import NotificationService
            notifier = NotificationService()
            if notifier.is_available():
                content = screener.format_notification(result)
                success = notifier.send(content)
                if success:
                    print("✅ 通知推送成功")
                else:
                    print("⚠️ 通知推送失败（所有渠道发送失败）")
            else:
                print("⚠️ 无可用通知渠道，请在 .env 中配置")
        except Exception as e:
            print(f"⚠️ 通知推送异常: {e}")
    elif not args.notify:
        print("\n💡 使用 --notify 参数可推送通知到已配置的渠道")

    return 0


if __name__ == "__main__":
    sys.exit(main())
