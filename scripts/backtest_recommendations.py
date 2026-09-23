#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
荐股回测脚本
测试近期荐股的胜率和表现
"""

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from data_provider import get_fetcher
import sys

def get_recent_recommendations(days=30, limit=50):
    """获取近期荐股记录"""
    conn = sqlite3.connect('data/stock_analysis.db')
    
    # 获取荐股记录
    query = """
    SELECT 
        code, 
        name, 
        created_at,
        operation_advice,
        ideal_buy,
        secondary_buy,
        stop_loss,
        take_profit,
        sentiment_score,
        trend_prediction
    FROM analysis_history 
    WHERE created_at >= date('now', '-{} days')
        AND ideal_buy IS NOT NULL
        AND ideal_buy > 0
    ORDER BY created_at DESC
    LIMIT {}
    """.format(days, limit)
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    return df

def backtest_recommendation(code, name, rec_date, ideal_buy, secondary_buy, stop_loss, take_profit, hold_days=10):
    """
    回测单只荐股
    
    参数:
    - code: 股票代码
    - name: 股票名称
    - rec_date: 荐股日期
    - ideal_buy: 理想买入价
    - secondary_buy: 次要买入价
    - stop_loss: 止损价
    - take_profit: 止盈价
    - hold_days: 最大持有天数
    
    返回:
    - dict: 回测结果
    """
    try:
        # 获取股票数据
        fetcher = get_fetcher()
        
        # 解析荐股日期
        if isinstance(rec_date, str):
            rec_date = datetime.strptime(rec_date.split()[0], '%Y-%m-%d')
        
        # 获取荐股后 hold_days+5 天的数据（多取几天以防数据缺失）
        start_date = rec_date.strftime('%Y-%m-%d')
        end_date = (rec_date + timedelta(days=hold_days + 5)).strftime('%Y-%m-%d')
        
        df = fetcher.fetch_daily(code, start_date, end_date)
        
        if df is None or len(df) == 0:
            return {
                'code': code,
                'name': name,
                'rec_date': rec_date.strftime('%Y-%m-%d'),
                'status': 'NO_DATA',
                'result': None
            }
        
        # 确保日期排序
        df = df.sort_values('date').reset_index(drop=True)
        
        # 找到荐股日或之后第一个交易日
        rec_date_str = rec_date.strftime('%Y-%m-%d')
        start_idx = 0
        for idx, row in df.iterrows():
            if row['date'] >= rec_date:
                start_idx = idx
                break
        
        if start_idx >= len(df):
            return {
                'code': code,
                'name': name,
                'rec_date': rec_date.strftime('%Y-%m-%d'),
                'status': 'NO_DATA',
                'result': None
            }
        
        # 回测逻辑
        entry_price = ideal_buy
        exit_price = None
        exit_date = None
        exit_reason = None
        hold_period = 0
        
        # 从荐股日后一天开始检查
        for i in range(start_idx, min(start_idx + hold_days + 1, len(df))):
            row = df.iloc[i]
            current_date = row['date']
            high = row['high']
            low = row['low']
            close = row['close']
            
            hold_period = i - start_idx
            
            # 检查止损
            if low <= stop_loss:
                exit_price = stop_loss
                exit_date = current_date
                exit_reason = 'STOP_LOSS'
                break
            
            # 检查止盈
            if high >= take_profit:
                exit_price = take_profit
                exit_date = current_date
                exit_reason = 'TAKE_PROFIT'
                break
        
        # 如果未触发止盈止损，按最后收盘价计算
        if exit_price is None:
            last_idx = min(start_idx + hold_days, len(df) - 1)
            exit_price = df.iloc[last_idx]['close']
            exit_date = df.iloc[last_idx]['date']
            exit_reason = 'HOLD_END'
        
        # 计算收益率
        return_pct = (exit_price - entry_price) / entry_price * 100
        
        # 判断胜负
        if return_pct > 0:
            result = 'WIN'
        elif return_pct < 0:
            result = 'LOSS'
        else:
            result = 'BREAKEVEN'
        
        return {
            'code': code,
            'name': name,
            'rec_date': rec_date.strftime('%Y-%m-%d'),
            'entry_price': entry_price,
            'exit_price': exit_price,
            'exit_date': exit_date.strftime('%Y-%m-%d') if hasattr(exit_date, 'strftime') else str(exit_date),
            'exit_reason': exit_reason,
            'hold_days': hold_period,
            'return_pct': round(return_pct, 2),
            'result': result,
            'status': 'OK'
        }
        
    except Exception as e:
        return {
            'code': code,
            'name': name,
            'rec_date': rec_date.strftime('%Y-%m-%d') if hasattr(rec_date, 'strftime') else str(rec_date),
            'status': 'ERROR',
            'error': str(e),
            'result': None
        }

def main():
    print("=" * 80)
    print("荐股回测分析")
    print("=" * 80)
    print()
    
    # 获取近期荐股
    print("正在获取近期荐股记录...")
    recommendations = get_recent_recommendations(days=30, limit=50)
    
    if len(recommendations) == 0:
        print("未找到近期荐股记录")
        return
    
    print(f"找到 {len(recommendations)} 条荐股记录")
    print()
    
    # 回测每只荐股
    results = []
    print("开始回测...")
    
    for idx, row in recommendations.iterrows():
        code = row['code']
        name = row['name']
        rec_date = row['created_at']
        ideal_buy = row['ideal_buy']
        secondary_buy = row['secondary_buy'] if pd.notna(row['secondary_buy']) else ideal_buy * 0.98
        stop_loss = row['stop_loss'] if pd.notna(row['stop_loss']) else ideal_buy * 0.95
        take_profit = row['take_profit'] if pd.notna(row['take_profit']) else ideal_buy * 1.10
        
        print(f"  回测 {code} {name} ({rec_date})...", end=' ')
        
        result = backtest_recommendation(
            code, name, rec_date, 
            ideal_buy, secondary_buy, stop_loss, take_profit,
            hold_days=10
        )
        
        results.append(result)
        
        if result['status'] == 'OK':
            print(f"{result['result']} ({result['return_pct']:+.2f}%)")
        elif result['status'] == 'NO_DATA':
            print("无数据")
        else:
            print(f"错误: {result.get('error', 'unknown')}")
    
    print()
    print("=" * 80)
    print("回测结果汇总")
    print("=" * 80)
    print()
    
    # 过滤有效结果
    valid_results = [r for r in results if r['status'] == 'OK']
    
    if len(valid_results) == 0:
        print("无有效回测结果")
        return
    
    # 统计指标
    total = len(valid_results)
    wins = sum(1 for r in valid_results if r['result'] == 'WIN')
    losses = sum(1 for r in valid_results if r['result'] == 'LOSS')
    breakevens = sum(1 for r in valid_results if r['result'] == 'BREAKEVEN')
    
    win_rate = wins / total * 100 if total > 0 else 0
    
    avg_return = sum(r['return_pct'] for r in valid_results) / total
    avg_win = sum(r['return_pct'] for r in valid_results if r['result'] == 'WIN') / wins if wins > 0 else 0
    avg_loss = sum(r['return_pct'] for r in valid_results if r['result'] == 'LOSS') / losses if losses > 0 else 0
    
    max_win = max(r['return_pct'] for r in valid_results)
    max_loss = min(r['return_pct'] for r in valid_results)
    
    # 按退出原因统计
    take_profit_count = sum(1 for r in valid_results if r['exit_reason'] == 'TAKE_PROFIT')
    stop_loss_count = sum(1 for r in valid_results if r['exit_reason'] == 'STOP_LOSS')
    hold_end_count = sum(1 for r in valid_results if r['exit_reason'] == 'HOLD_END')
    
    print(f"总荐股数: {total}")
    print(f"盈利次数: {wins} ({win_rate:.1f}%)")
    print(f"亏损次数: {losses} ({losses/total*100:.1f}%)")
    print(f"持平次数: {breakevens} ({breakevens/total*100:.1f}%)")
    print()
    print(f"平均收益率: {avg_return:+.2f}%")
    print(f"平均盈利: {avg_win:+.2f}%")
    print(f"平均亏损: {avg_loss:+.2f}%")
    print(f"最大盈利: {max_win:+.2f}%")
    print(f"最大亏损: {max_loss:+.2f}%")
    print()
    print(f"触发止盈: {take_profit_count} 次")
    print(f"触发止损: {stop_loss_count} 次")
    print(f"持有到期: {hold_end_count} 次")
    print()
    
    # 详细结果
    print("=" * 80)
    print("详细回测结果")
    print("=" * 80)
    print()
    
    # 按日期排序
    valid_results.sort(key=lambda x: x['rec_date'], reverse=True)
    
    print(f"{'代码':<8} {'名称':<10} {'荐股日期':<12} {'买入价':>8} {'卖出价':>8} {'收益率':>8} {'持有天数':>8} {'退出原因':<12} {'结果':<8}")
    print("-" * 100)
    
    for r in valid_results:
        exit_reason_map = {
            'TAKE_PROFIT': '止盈',
            'STOP_LOSS': '止损',
            'HOLD_END': '持有到期'
        }
        exit_reason_cn = exit_reason_map.get(r['exit_reason'], r['exit_reason'])
        
        print(f"{r['code']:<8} {r['name']:<10} {r['rec_date']:<12} {r['entry_price']:>8.2f} {r['exit_price']:>8.2f} {r['return_pct']:>+7.2f}% {r['hold_days']:>6}天 {exit_reason_cn:<12} {r['result']:<8}")
    
    print()
    print("=" * 80)

if __name__ == '__main__':
    main()
