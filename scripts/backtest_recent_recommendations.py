#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回测近期荐股并计算胜率
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from data_provider import get_fetcher
import sys

def get_recommendations_to_backtest():
    """获取需要回测的荐股记录"""
    conn = sqlite3.connect('data/stock_analysis.db')
    
    # 获取所有荐股记录，标记是否已有回测结果
    query = """
    SELECT 
        ah.id,
        ah.code,
        ah.name,
        ah.created_at,
        ah.operation_advice,
        ah.ideal_buy,
        ah.secondary_buy,
        ah.stop_loss,
        ah.take_profit,
        ah.sentiment_score,
        br.id as backtest_id,
        br.outcome,
        br.simulated_return_pct
    FROM analysis_history ah
    LEFT JOIN backtest_results br ON br.analysis_history_id = ah.id
    WHERE ah.code != 'MARKET'
        AND ah.ideal_buy IS NOT NULL
        AND ah.ideal_buy > 0
    ORDER BY ah.created_at DESC
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    return df

def backtest_with_fresh_data(code, name, rec_date, ideal_buy, stop_loss, take_profit, hold_days=10):
    """
    使用实时数据回测荐股
    
    参数:
    - code: 股票代码
    - name: 股票名称
    - rec_date: 荐股日期
    - ideal_buy: 理想买入价
    - stop_loss: 止损价
    - take_profit: 止盈价
    - hold_days: 最大持有天数
    
    返回:
    - dict: 回测结果
    """
    try:
        fetcher = get_fetcher()
        
        # 解析荐股日期
        if isinstance(rec_date, str):
            rec_date = datetime.strptime(rec_date.split()[0], '%Y-%m-%d')
        
        # 获取荐股后的数据
        start_date = rec_date.strftime('%Y-%m-%d')
        end_date = (rec_date + timedelta(days=hold_days + 10)).strftime('%Y-%m-%d')
        
        df = fetcher.fetch_daily(code, start_date, end_date)
        
        if df is None or len(df) == 0:
            return {
                'status': 'NO_DATA',
                'error': '无法获取股票数据'
            }
        
        # 确保日期排序
        df = df.sort_values('date').reset_index(drop=True)
        
        # 找到荐股日或之后第一个交易日
        start_idx = 0
        for idx, row in df.iterrows():
            if row['date'] >= rec_date:
                start_idx = idx
                break
        
        if start_idx >= len(df):
            return {
                'status': 'NO_DATA',
                'error': '荐股日期后无交易数据'
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
            'status': 'OK',
            'entry_price': entry_price,
            'exit_price': exit_price,
            'exit_date': exit_date.strftime('%Y-%m-%d') if hasattr(exit_date, 'strftime') else str(exit_date),
            'exit_reason': exit_reason,
            'hold_days': hold_period,
            'return_pct': round(return_pct, 2),
            'result': result
        }
        
    except Exception as e:
        return {
            'status': 'ERROR',
            'error': str(e)
        }

def main():
    print("=" * 100)
    print("近期荐股回测分析")
    print("=" * 100)
    print()
    
    # 获取荐股记录
    print("正在获取荐股记录...")
    recommendations = get_recommendations_to_backtest()
    
    if len(recommendations) == 0:
        print("未找到荐股记录")
        return
    
    print(f"找到 {len(recommendations)} 条荐股记录")
    print()
    
    # 分类统计
    has_backtest = recommendations[recommendations['backtest_id'].notna()]
    needs_backtest = recommendations[recommendations['backtest_id'].isna()]
    
    print(f"已有回测结果: {len(has_backtest)} 条")
    print(f"需要新回测: {len(needs_backtest)} 条")
    print()
    
    results = []
    
    # 处理已有回测结果
    if len(has_backtest) > 0:
        print("处理已有回测结果...")
        for idx, row in has_backtest.iterrows():
            results.append({
                'id': row['id'],
                'code': row['code'],
                'name': row['name'],
                'rec_date': row['created_at'],
                'operation_advice': row['operation_advice'],
                'ideal_buy': row['ideal_buy'],
                'stop_loss': row['stop_loss'],
                'take_profit': row['take_profit'],
                'status': 'OK',
                'result': 'WIN' if row['outcome'] == 'win' else 'LOSS',
                'return_pct': row['simulated_return_pct'] if pd.notna(row['simulated_return_pct']) else 0,
                'exit_reason': 'EXISTING',
                'hold_days': None,
                'source': 'existing'
            })
    
    # 处理需要新回测的记录
    if len(needs_backtest) > 0:
        print()
        print("开始新回测...")
        for idx, row in needs_backtest.iterrows():
            code = row['code']
            name = row['name']
            rec_date = row['created_at']
            ideal_buy = row['ideal_buy']
            
            # 设置默认止损止盈
            stop_loss = row['stop_loss'] if pd.notna(row['stop_loss']) else ideal_buy * 0.95
            take_profit = row['take_profit'] if pd.notna(row['take_profit']) else ideal_buy * 1.10
            
            print(f"  回测 {code} {name} ({rec_date})...", end=' ', flush=True)
            
            result = backtest_with_fresh_data(
                code, name, rec_date, 
                ideal_buy, stop_loss, take_profit,
                hold_days=10
            )
            
            if result['status'] == 'OK':
                print(f"{result['result']} ({result['return_pct']:+.2f}%)")
                results.append({
                    'id': row['id'],
                    'code': code,
                    'name': name,
                    'rec_date': rec_date,
                    'operation_advice': row['operation_advice'],
                    'ideal_buy': ideal_buy,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'status': 'OK',
                    'result': result['result'],
                    'return_pct': result['return_pct'],
                    'exit_reason': result['exit_reason'],
                    'hold_days': result['hold_days'],
                    'source': 'fresh'
                })
            else:
                print(f"{result['status']}: {result.get('error', 'unknown')}")
                results.append({
                    'id': row['id'],
                    'code': code,
                    'name': name,
                    'rec_date': rec_date,
                    'operation_advice': row['operation_advice'],
                    'ideal_buy': ideal_buy,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'status': result['status'],
                    'result': None,
                    'return_pct': None,
                    'exit_reason': None,
                    'hold_days': None,
                    'source': 'fresh',
                    'error': result.get('error')
                })
    
    print()
    print("=" * 100)
    print("回测结果汇总")
    print("=" * 100)
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
    
    returns = [r['return_pct'] for r in valid_results if r['return_pct'] is not None]
    avg_return = sum(returns) / len(returns) if returns else 0
    
    win_returns = [r['return_pct'] for r in valid_results if r['result'] == 'WIN' and r['return_pct'] is not None]
    loss_returns = [r['return_pct'] for r in valid_results if r['result'] == 'LOSS' and r['return_pct'] is not None]
    
    avg_win = sum(win_returns) / len(win_returns) if win_returns else 0
    avg_loss = sum(loss_returns) / len(loss_returns) if loss_returns else 0
    
    max_win = max(returns) if returns else 0
    max_loss = min(returns) if returns else 0
    
    # 按退出原因统计
    take_profit_count = sum(1 for r in valid_results if r['exit_reason'] == 'TAKE_PROFIT')
    stop_loss_count = sum(1 for r in valid_results if r['exit_reason'] == 'STOP_LOSS')
    hold_end_count = sum(1 for r in valid_results if r['exit_reason'] == 'HOLD_END')
    existing_count = sum(1 for r in valid_results if r['exit_reason'] == 'EXISTING')
    
    print(f"总荐股数: {total}")
    print(f"盈利次数: {wins} ({win_rate:.1f}%)")
    print(f"亏损次数: {losses} ({losses/total*100:.1f}%)")
    if breakevens > 0:
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
    if existing_count > 0:
        print(f"已有回测: {existing_count} 次")
    print()
    
    # 按操作建议分类统计
    advice_stats = {}
    for r in valid_results:
        advice = r['operation_advice']
        if advice not in advice_stats:
            advice_stats[advice] = {'total': 0, 'wins': 0, 'returns': []}
        advice_stats[advice]['total'] += 1
        if r['result'] == 'WIN':
            advice_stats[advice]['wins'] += 1
        if r['return_pct'] is not None:
            advice_stats[advice]['returns'].append(r['return_pct'])
    
    print("按操作建议分类:")
    print("-" * 80)
    print(f"{'操作建议':<20} {'总数':>6} {'盈利':>6} {'胜率':>8} {'平均收益':>10}")
    print("-" * 80)
    for advice, stats in sorted(advice_stats.items(), key=lambda x: x[1]['total'], reverse=True):
        win_rate_advice = stats['wins'] / stats['total'] * 100 if stats['total'] > 0 else 0
        avg_ret = sum(stats['returns']) / len(stats['returns']) if stats['returns'] else 0
        print(f"{advice:<20} {stats['total']:>6} {stats['wins']:>6} {win_rate_advice:>7.1f}% {avg_ret:>+9.2f}%")
    print()
    
    # 详细结果
    print("=" * 100)
    print("详细回测结果")
    print("=" * 100)
    print()
    
    # 按日期排序
    valid_results.sort(key=lambda x: x['rec_date'], reverse=True)
    
    print(f"{'代码':<8} {'名称':<12} {'荐股日期':<12} {'操作建议':<12} {'买入价':>8} {'收益率':>8} {'持有天数':>8} {'退出原因':<12} {'结果':<8}")
    print("-" * 110)
    
    for r in valid_results:
        exit_reason_map = {
            'TAKE_PROFIT': '止盈',
            'STOP_LOSS': '止损',
            'HOLD_END': '持有到期',
            'EXISTING': '已有回测'
        }
        exit_reason_cn = exit_reason_map.get(r['exit_reason'], r['exit_reason'] or '-')
        
        hold_days_str = f"{r['hold_days']}天" if r['hold_days'] is not None else '-'
        return_str = f"{r['return_pct']:+.2f}%" if r['return_pct'] is not None else '-'
        
        print(f"{r['code']:<8} {r['name']:<12} {r['rec_date'][:10]:<12} {r['operation_advice']:<12} {r['ideal_buy']:>8.2f} {return_str:>8} {hold_days_str:>8} {exit_reason_cn:<12} {r['result']:<8}")
    
    print()
    print("=" * 100)

if __name__ == '__main__':
    main()
