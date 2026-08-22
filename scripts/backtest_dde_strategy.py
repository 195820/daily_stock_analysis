# -*- coding: utf-8 -*-
"""
DDE 近似回测脚本

用"资金流向指标"近似 DDE（大单净量），回测策略：
1. 资金流向 >= 0.8%（近似 DDE）
2. 当日涨幅 2.5% ~ 6.5%
3. 近 5 日累计涨幅 < 15%
4. 按资金流向降序取前 5 只
5. 持有 1 天，计算次日收益

近似 DDE 计算：
  money_flow = ((close - low) - (high - close)) / (high - low) * amount
  dde_proxy = money_flow / circ_market_cap * 100

数据源：mootdx TCP（P0，不受反爬影响）
"""

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── 策略参数 ──
DDE_THRESHOLD = 0.01        # 资金流向阈值下限 1%
DDE_UPPER = 0.02            # 资金流向阈值上限 2%
GAIN_LOW = 0.045            # 涨幅下限 4.5%
GAIN_HIGH = 0.055           # 涨幅上限 5.5%
MAX_5D_GAIN = 0.15          # 近5日涨幅上限 15%
TOP_N = 5                   # 每天取前 N 只
HOLD_DAYS = 1               # 持有天数（默认，可通过 --hold 覆盖）


def get_all_stock_codes() -> List[str]:
    """获取全市场 A 股代码（从预生成的 JSON 文件加载）"""
    codes_path = project_root / "data" / "a_share_codes.json"
    if codes_path.exists():
        import json
        with open(codes_path) as f:
            codes = json.load(f)
        logger.info(f"从缓存加载 {len(codes)} 只 A 股代码")
        return codes

    # 缓存不存在，用 mootdx stock_all 生成
    logger.info("生成 A 股代码列表...")
    from data_provider.mootdx_fetcher import MootdxFetcher
    f = MootdxFetcher()
    with f._mootdx_session() as client:
        df = client.stock_all()
    df['code'] = df['code'].astype(str).str.zfill(6)
    valid_prefixes = {'600','601','603','605','688','689','000','001','002','003','300','301'}
    codes = df[df['code'].str[:3].isin(valid_prefixes)]['code'].tolist()
    # 保存缓存
    import json
    codes_path.parent.mkdir(parents=True, exist_ok=True)
    with open(codes_path, 'w') as jf:
        json.dump(codes, jf)
    logger.info(f"生成 {len(codes)} 只 A 股代码，已缓存")
    return codes


def fetch_stock_daily(code: str, days: int = 250) -> Optional[pd.DataFrame]:
    """用 mootdx 获取单只股票日线数据"""
    try:
        from data_provider.mootdx_fetcher import MootdxFetcher
        fetcher = MootdxFetcher()
        df = fetcher.get_daily_data(code, days=days)
        if df is None or not isinstance(df, pd.DataFrame) or len(df) < 30:
            return None
        return df
    except Exception:
        return None


def calc_approx_dde(df: pd.DataFrame) -> pd.DataFrame:
    """计算近似 DDE（资金流向指标）

    公式：
      money_flow_ratio = ((close - low) - (high - close)) / (high - low)
      dde_proxy = money_flow_ratio * amount / avg_amount_20d * 100

    money_flow_ratio ∈ [-1, 1]：+1 表示全部为买方主动，-1 反之
    乘以 amount/avg_amount 放大资金异常信号
    """
    df = df.copy()

    high = df['high'].values.astype(float)
    low = df['low'].values.astype(float)
    close = df['close'].values.astype(float)
    amount = df['amount'].values.astype(float)

    # 资金流向比率：(收盘价-最低价) - (最高价-收盘价) = 净买入方向
    hl_range = high - low
    hl_range[hl_range <= 0] = 0.001  # 防除零

    money_flow_ratio = ((close - low) - (high - close)) / hl_range

    # 用 20 日均量标准化（放大异常放量信号）
    avg_amount = pd.Series(amount).rolling(20, min_periods=5).mean().values
    avg_amount[avg_amount <= 0] = 1.0
    vol_ratio = amount / avg_amount

    # DDE 近似 = 资金流向比率 × 量比（放大异常信号）
    # 乘以 0.01 缩放使阈值与真实 DDE（0.8%）接近
    dde_proxy = money_flow_ratio * vol_ratio * 0.01

    df['dde_proxy'] = dde_proxy

    # 日收益率（优先用 pct_chg 列，否则手动计算）
    if 'pct_chg' in df.columns:
        df['daily_return'] = pd.to_numeric(df['pct_chg'], errors='coerce') / 100.0
    else:
        df['daily_return'] = pd.Series(close).pct_change().values

    # 近 5 日累计涨幅
    df['return_5d'] = pd.Series(close).pct_change(5).values

    return df


def run_backtest(
    start_date: str = "2026-01-02",
    end_date: str = None,
    max_stocks: int = None,
    max_workers: int = 8,
) -> Dict:
    """运行回测"""
    if end_date is None:
        end_date = date.today().isoformat()

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

    logger.info(f"回测区间: {start_date} ~ {end_date}")
    logger.info(f"策略参数: DDE {DDE_THRESHOLD:.1%}~{DDE_UPPER:.1%}, 涨幅{GAIN_LOW:.1%}~{GAIN_HIGH:.1%}, "
                f"5日涨幅<{MAX_5D_GAIN:.0%}, Top{TOP_N}, 持有{HOLD_DAYS}天")

    # 获取股票列表
    all_codes = get_all_stock_codes()
    if max_stocks:
        all_codes = all_codes[:max_stocks]
        logger.info(f"限制前 {max_stocks} 只")

    # 并发获取日线数据
    logger.info(f"开始获取 {len(all_codes)} 只股票日线数据（{max_workers} 线程）...")
    stock_data = {}
    fetched = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_stock_daily, code, 300): code for code in all_codes}
        for future in as_completed(futures):
            code = futures[future]
            fetched += 1
            if fetched % 500 == 0:
                elapsed = time.time() - t0
                logger.info(f"  进度: {fetched}/{len(all_codes)} ({elapsed:.0f}s)")

            df = future.result()
            if df is not None:
                stock_data[code] = df

    logger.info(f"成功获取 {len(stock_data)} 只股票数据（{time.time()-t0:.0f}s）")

    # 计算近似 DDE
    logger.info("计算近似 DDE 指标...")
    for code in stock_data:
        stock_data[code] = calc_approx_dde(stock_data[code])

    # ── 向量化回测：合并为单一大 DataFrame ──
    logger.info("合并数据...")
    frames = []
    for code, df in stock_data.items():
        df = df.copy()
        df['code'] = code
        df['date'] = pd.to_datetime(df['date'])
        df['trade_date'] = df['date'].dt.date
        frames.append(df)

    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.sort_values(['code', 'date']).reset_index(drop=True)

    # 计算次日收益（向量化）
    all_df['next_close'] = all_df.groupby('code')['close'].shift(-HOLD_DAYS)
    all_df['next_return'] = all_df['next_close'] / all_df['close'] - 1
    all_df = all_df.dropna(subset=['next_return'])

    # 过滤日期范围
    all_df = all_df[
        (all_df['trade_date'] >= start_dt) &
        (all_df['trade_date'] <= end_dt)
    ]

    logger.info(f"总数据量: {len(all_df)} 行, {all_df['trade_date'].nunique()} 个交易日")

    # ── 策略筛选（全量向量化）──
    logger.info("策略筛选...")
    mask = (
        (all_df['dde_proxy'] >= DDE_THRESHOLD) &
        (all_df['dde_proxy'] <= DDE_UPPER) &
        (all_df['daily_return'] >= GAIN_LOW) &
        (all_df['daily_return'] <= GAIN_HIGH) &
        (all_df['return_5d'].isna() | (all_df['return_5d'] < MAX_5D_GAIN))
    )
    filtered = all_df[mask].copy()
    logger.info(f"条件筛选后: {len(filtered)} 行")

    if filtered.empty:
        logger.warning("无符合条件交易")
        return _calc_stats([])

    # 按日期分组，每组按 DDE 降序取 Top N
    filtered = filtered.sort_values(['trade_date', 'dde_proxy'], ascending=[True, False])
    top_n = filtered.groupby('trade_date').head(TOP_N)

    # 构建结果
    results = []
    for _, row in top_n.iterrows():
        results.append({
            'trade_date': row['trade_date'],
            'code': row['code'],
            'dde_proxy': row['dde_proxy'],
            'daily_return': row['daily_return'],
            'next_return': row['next_return'],
            'win': row['next_return'] > 0,
        })

    logger.info(f"最终候选: {len(results)} 笔交易, {top_n['trade_date'].nunique()} 个交易日")
    return _calc_stats(results)


def _calc_stats(results: List[Dict]) -> Dict:
    """计算回测统计"""
    if not results:
        return {
            'total_trades': 0, 'win_rate': 0, 'avg_return': 0,
            'median_return': 0, 'max_return': 0, 'min_return': 0,
            'total_return': 0, 'results': [], 'daily_summary': [],
        }

    df = pd.DataFrame(results)
    next_returns = df['next_return'].values

    # 每日汇总（等权组合）
    daily_summary = df.groupby('trade_date').agg(
        n_stocks=('code', 'count'),
        avg_return=('next_return', 'mean'),
        win_rate=('win', 'mean'),
    ).reset_index()

    # 累计收益（每日等权复合）
    cum_return = (1 + daily_summary['avg_return']).prod() - 1

    return {
        'total_trades': len(results),
        'winning_trades': int(df['win'].sum()),
        'win_rate': float(df['win'].mean()),
        'avg_return': float(next_returns.mean()),
        'median_return': float(np.median(next_returns)),
        'max_return': float(next_returns.max()),
        'min_return': float(next_returns.min()),
        'total_return': float(cum_return),
        'trading_days': len(daily_summary),
        'results': results,
        'daily_summary': daily_summary.to_dict('records'),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DDE 近似回测")
    parser.add_argument("--start", default="2026-01-02", help="开始日期")
    parser.add_argument("--end", default=None, help="结束日期（默认今天）")
    parser.add_argument("--max-stocks", type=int, default=None, help="最大股票数（调试用）")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数")
    parser.add_argument("--hold", type=int, default=None, help="持有天数（默认1）")
    args = parser.parse_args()

    global HOLD_DAYS
    if args.hold:
        HOLD_DAYS = args.hold

    logger.info("=" * 60)
    logger.info("DDE 近似回测（资金流向指标替代 DDE）")
    logger.info("=" * 60)

    stats = run_backtest(
        start_date=args.start,
        end_date=args.end,
        max_stocks=args.max_stocks,
        max_workers=args.workers,
    )

    logger.info("=" * 60)
    logger.info("回测结果")
    logger.info("=" * 60)
    logger.info(f"总交易次数: {stats['total_trades']}")
    logger.info(f"盈利次数: {stats.get('winning_trades', 0)}")
    logger.info(f"胜率: {stats['win_rate']:.2%}")
    logger.info(f"平均次日收益: {stats['avg_return']:.4%}")
    logger.info(f"中位数次日收益: {stats['median_return']:.4%}")
    logger.info(f"最大单次收益: {stats['max_return']:.4%}")
    logger.info(f"最大单次亏损: {stats['min_return']:.4%}")
    logger.info(f"回测交易日: {stats.get('trading_days', 0)} 天")
    logger.info(f"累计复合收益: {stats['total_return']:.4%}")

    # 保存结果
    if stats['results']:
        output_path = project_root / "data" / "dde_approx_backtest.csv"
        pd.DataFrame(stats['results']).to_csv(output_path, index=False)
        logger.info(f"详细结果: {output_path}")

        daily_path = project_root / "data" / "dde_approx_backtest_daily.csv"
        pd.DataFrame(stats['daily_summary']).to_csv(daily_path, index=False)
        logger.info(f"每日汇总: {daily_path}")


if __name__ == "__main__":
    main()
