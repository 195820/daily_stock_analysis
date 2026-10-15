#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 6-0 盘中数据地基探测脚本
================================

只读探测 mootdx TCP 协议的四类盘中数据接口能力：
1. 五档买量（quotes() 的 bid/ask 价量字段）
2. 分时序列 / 分钟 K（minute()/minutes()/bars(frequency=8/9)）
3. 集合竞价（transaction 早盘 9:15-9:25 段）
4. 盘中调度能力（main.py --got 现状 + 定时调度选项）

双样本测试：沪市 600519（贵州茅台）+ 深市 000001（平安银行）
TCP 失败时自动换台重试（参考 mootdx_fetcher.py 验活模式）

输出：接口存在性、样本 schema、可行性结论（映射到 OPEN-I1…I5）

用法：
    python scripts/probe_intraday_capabilities.py
"""

import sys
import os
import time
import traceback
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd


# =====================================================================
# 配置
# =====================================================================

# 通达信行情服务器池（复用 data_provider/mootdx_fetcher.py 的默认池）
DEFAULT_HQ_SERVERS: Tuple[Tuple[str, int], ...] = (
    ("117.34.114.15", 7709),  # 国泰君安9
    ("117.34.114.13", 7709),  # 国泰君安7
    ("117.34.114.14", 7709),  # 国泰君安8
    ("117.34.114.16", 7709),  # 国泰君安10
    ("117.34.114.17", 7709),  # 国泰君安11
    ("59.36.5.11", 7709),     # 安信15
)

CONNECT_TIMEOUT = 5  # 秒
PROBE_SYMBOL_SH = "600519"  # 沪市样本（贵州茅台）
PROBE_SYMBOL_SZ = "000001"  # 深市样本（平安银行）


# =====================================================================
# 工具函数
# =====================================================================

def try_connect_server(server: Tuple[str, int]) -> Optional[Any]:
    """尝试连接单台服务器，失败返回 None"""
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(
            market='std',
            server=server,
            timeout=CONNECT_TIMEOUT,
        )
        return client
    except Exception as e:
        print(f"  ⚠ 连接 {server[0]}:{server[1]} 失败: {e}")
        return None


def probe_client_alive(client) -> bool:
    """验活：真取一根 K 线，空表视为节点失效"""
    try:
        bars = client.bars(symbol=PROBE_SYMBOL_SH, frequency=9, offset=1)
        if bars is None or bars.empty:
            return False
        return True
    except Exception as e:
        print(f"  ⚠ 验活失败: {e}")
        return False


def get_working_client() -> Optional[Any]:
    """扫描服务器池，返回第一台可用节点"""
    print("\n[连接探测] 扫描通达信行情服务器池...")
    for i, server in enumerate(DEFAULT_HQ_SERVERS, 1):
        print(f"  尝试 {i}/{len(DEFAULT_HQ_SERVERS)}: {server[0]}:{server[1]}")
        client = try_connect_server(server)
        if client is None:
            continue
        if not probe_client_alive(client):
            print(f"  ⚠ 节点 {server[0]}:{server[1]} 验活未通过（空表），换下一台")
            continue
        print(f"  ✓ 选定主站 {server[0]}:{server[1]}")
        return client
    return None


def safe_call(func, *args, **kwargs) -> Tuple[bool, Any, Optional[str]]:
    """安全调用，返回 (成功, 结果, 错误信息)"""
    try:
        result = func(*args, **kwargs)
        return (True, result, None)
    except Exception as e:
        return (False, None, f"{type(e).__name__}: {e}")


def format_sample(df: pd.DataFrame, max_rows: int = 3) -> str:
    """格式化 DataFrame 样本输出"""
    if df is None or df.empty:
        return "(空表)"
    lines = []
    lines.append(f"  列名 ({len(df.columns)}): {list(df.columns)}")
    lines.append(f"  行数: {len(df)}")
    lines.append(f"  样本 (前 {min(max_rows, len(df))} 行):")
    sample = df.head(max_rows)
    for idx, row in sample.iterrows():
        row_str = " | ".join(f"{c}={row[c]}" for c in df.columns[:8])  # 限制列数避免过长
        lines.append(f"    [{idx}] {row_str}")
    return "\n".join(lines)


# =====================================================================
# 探测节 1: 五档买量（quotes()）
# =====================================================================

def probe_quotes_bid_ask(client) -> Dict[str, Any]:
    """探测 quotes() 的五档 bid/ask 价量字段"""
    print("\n" + "="*70)
    print("[探测 1] 五档买量：quotes() 的 bid/ask 字段")
    print("="*70)
    
    results = {}
    
    for symbol, market_name in [(PROBE_SYMBOL_SH, "沪市"), (PROBE_SYMBOL_SZ, "深市")]:
        print(f"\n>>> 测试 {market_name} {symbol}")
        
        success, quotes_df, err = safe_call(client.quotes, symbol=symbol)
        
        if not success:
            print(f"  ✗ 调用失败: {err}")
            results[symbol] = {
                "status": "调用失败",
                "error": err,
                "columns": [],
                "bid_ask_fields": [],
            }
            continue
        
        if quotes_df is None or quotes_df.empty:
            print(f"  ⚠ 返回空表（可能需调整参数或当前时段无数据）")
            results[symbol] = {
                "status": "空表",
                "columns": [],
                "bid_ask_fields": [],
            }
            continue
        
        columns = list(quotes_df.columns)
        print(f"  ✓ 返回 {len(quotes_df)} 行, {len(columns)} 列")
        print(f"  列名: {columns}")
        
        # 检查 bid/ask 相关字段
        bid_fields = [c for c in columns if 'bid' in c.lower()]
        ask_fields = [c for c in columns if 'ask' in c.lower()]
        
        print(f"\n  五档买价字段: {bid_fields}")
        print(f"  五档卖价字段: {ask_fields}")
        
        # 检查字段是否有值（非全 0 / NaN）
        if len(quotes_df) > 0:
            row = quotes_df.iloc[0]
            bid_values = {f: row.get(f, None) for f in bid_fields[:5]}
            ask_values = {f: row.get(f, None) for f in ask_fields[:5]}
            print(f"\n  样本值（第一行）:")
            print(f"    买价: {bid_values}")
            print(f"    卖价: {ask_values}")
            
            # 判断是否有有效值
            has_bid_values = any(v not in [None, 0, float('nan')] for v in bid_values.values())
            has_ask_values = any(v not in [None, 0, float('nan')] for v in ask_values.values())
            
            if has_bid_values or has_ask_values:
                print(f"  ✓ 五档价量字段存在且有值")
                status = "可用"
            else:
                print(f"  ⚠ 字段存在但值为 0/NaN（可能非交易时段）")
                status = "字段存在但无值"
        else:
            status = "字段存在但未取到数据"
        
        results[symbol] = {
            "status": status,
            "columns": columns,
            "bid_ask_fields": bid_fields + ask_fields,
            "sample": format_sample(quotes_df, max_rows=2),
        }
    
    return results


# =====================================================================
# 探测节 2: 分时序列 / 分钟 K
# =====================================================================

def probe_minute_kline(client) -> Dict[str, Any]:
    """探测 minute()/minutes()/bars(frequency=8/9) 的分时与分钟 K 能力"""
    print("\n" + "="*70)
    print("[探测 2] 分时序列 / 分钟 K 线")
    print("="*70)
    
    results = {}
    symbol = PROBE_SYMBOL_SH
    print(f"\n>>> 测试沪市 {symbol}")
    
    # 测试 minute() - 当日分时
    print("\n[2.1] minute() - 当日分时序列")
    success, minute_df, err = safe_call(client.minute, symbol=symbol)
    if success:
        if minute_df is not None and not minute_df.empty:
            print(f"  ✓ 返回 {len(minute_df)} 行")
            print(format_sample(minute_df, max_rows=3))
            results['minute'] = {
                "status": "可用",
                "rows": len(minute_df),
                "columns": list(minute_df.columns),
                "sample": format_sample(minute_df, max_rows=2),
            }
        else:
            print(f"  ⚠ 返回空表（可能非交易时段）")
            results['minute'] = {"status": "空表", "rows": 0, "columns": []}
    else:
        print(f"  ✗ 调用失败: {err}")
        results['minute'] = {"status": "调用失败", "error": err}
    
    # 测试 minutes() - 历史分时（需指定 date）
    print("\n[2.2] minutes(date=...) - 历史分时序列")
    from datetime import timedelta
    test_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    success, minutes_df, err = safe_call(client.minutes, symbol=symbol, date=test_date)
    if success:
        if minutes_df is not None and not minutes_df.empty:
            print(f"  ✓ 返回 {len(minutes_df)} 行 (date={test_date})")
            print(format_sample(minutes_df, max_rows=3))
            results['minutes'] = {
                "status": "可用",
                "rows": len(minutes_df),
                "columns": list(minutes_df.columns),
                "sample": format_sample(minutes_df, max_rows=2),
            }
        else:
            print(f"  ⚠ 返回空表（date={test_date} 可能无数据）")
            results['minutes'] = {"status": "空表", "rows": 0, "columns": []}
    else:
        print(f"  ✗ 调用失败: {err}")
        results['minutes'] = {"status": "调用失败", "error": err}
    
    # 测试 bars(frequency=8) - 1 分钟 K
    print("\n[2.3] bars(frequency=8) - 1 分钟 K 线")
    success, bars_1m_df, err = safe_call(client.bars, symbol=symbol, frequency=8, offset=10)
    if success:
        if bars_1m_df is not None and not bars_1m_df.empty:
            print(f"  ✓ 返回 {len(bars_1m_df)} 行")
            print(format_sample(bars_1m_df, max_rows=3))
            results['bars_1min'] = {
                "status": "可用",
                "rows": len(bars_1m_df),
                "columns": list(bars_1m_df.columns),
                "sample": format_sample(bars_1m_df, max_rows=2),
            }
        else:
            print(f"  ⚠ 返回空表")
            results['bars_1min'] = {"status": "空表", "rows": 0, "columns": []}
    else:
        print(f"  ✗ 调用失败: {err}")
        results['bars_1min'] = {"status": "调用失败", "error": err}
    
    # 测试 bars(frequency=9) - 日线（对照组）
    print("\n[2.4] bars(frequency=9) - 日线（对照）")
    success, bars_1d_df, err = safe_call(client.bars, symbol=symbol, frequency=9, offset=5)
    if success:
        if bars_1d_df is not None and not bars_1d_df.empty:
            print(f"  ✓ 返回 {len(bars_1d_df)} 行")
            print(format_sample(bars_1d_df, max_rows=3))
            results['bars_1day'] = {
                "status": "可用",
                "rows": len(bars_1d_df),
                "columns": list(bars_1d_df.columns),
            }
        else:
            print(f"  ⚠ 返回空表")
            results['bars_1day'] = {"status": "空表", "rows": 0, "columns": []}
    else:
        print(f"  ✗ 调用失败: {err}")
        results['bars_1day'] = {"status": "调用失败", "error": err}
    
    return results


# =====================================================================
# 探测节 3: 集合竞价（transaction）
# =====================================================================

def probe_auction_data(client) -> Dict[str, Any]:
    """探测 transaction() 的集合竞价数据能力"""
    print("\n" + "="*70)
    print("[探测 3] 集合竞价数据：transaction() 早盘 9:15-9:25 段")
    print("="*70)
    
    results = {}
    symbol = PROBE_SYMBOL_SH
    print(f"\n>>> 测试沪市 {symbol}")
    
    # 测试 transaction() - 当日逐笔成交
    print("\n[3.1] transaction() - 当日逐笔成交")
    success, trans_df, err = safe_call(client.transaction, symbol=symbol, start=0, offset=50)
    if success:
        if trans_df is not None and not trans_df.empty:
            print(f"  ✓ 返回 {len(trans_df)} 行")
            print(format_sample(trans_df, max_rows=5))
            
            # 检查时间字段，判断是否包含 9:15-9:25 时段
            time_col = None
            for col in trans_df.columns:
                if 'time' in col.lower() or 'date' in col.lower():
                    time_col = col
                    break
            
            if time_col:
                print(f"\n  时间字段: {time_col}")
                print(f"  样本时间值: {trans_df[time_col].head(5).tolist()}")
            
            results['transaction'] = {
                "status": "可用",
                "rows": len(trans_df),
                "columns": list(trans_df.columns),
                "sample": format_sample(trans_df, max_rows=3),
                "time_field": time_col,
            }
        else:
            print(f"  ⚠ 返回空表（可能非交易时段）")
            results['transaction'] = {"status": "空表", "rows": 0, "columns": []}
    else:
        print(f"  ✗ 调用失败: {err}")
        results['transaction'] = {"status": "调用失败", "error": err}
    
    # 测试 transactions() - 历史逐笔（需指定 date）
    print("\n[3.2] transactions(date=...) - 历史逐笔成交")
    from datetime import timedelta
    test_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    success, trans_hist_df, err = safe_call(client.transactions, symbol=symbol, start=0, offset=50, date=test_date)
    if success:
        if trans_hist_df is not None and not trans_hist_df.empty:
            print(f"  ✓ 返回 {len(trans_hist_df)} 行 (date={test_date})")
            print(format_sample(trans_hist_df, max_rows=5))
            results['transactions'] = {
                "status": "可用",
                "rows": len(trans_hist_df),
                "columns": list(trans_hist_df.columns),
                "sample": format_sample(trans_hist_df, max_rows=3),
            }
        else:
            print(f"  ⚠ 返回空表（date={test_date} 可能无数据）")
            results['transactions'] = {"status": "空表", "rows": 0, "columns": []}
    else:
        print(f"  ✗ 调用失败: {err}")
        results['transactions'] = {"status": "调用失败", "error": err}
    
    print("\n[说明] 集合竞价数据（9:15-9:25）需从 transaction() 的时间字段筛选。")
    print("  当前为非交易时段，无法验证早盘集合竞价段是否真实有数据。")
    print("  建议交易日 9:26 后重测，确认 9:15-9:25 时段的逐笔成交是否完整。")
    
    return results


# =====================================================================
# 探测节 4: 盘中调度能力
# =====================================================================

def probe_scheduling_capability() -> Dict[str, Any]:
    """盘点盘中调度能力（不实施，只调研）"""
    print("\n" + "="*70)
    print("[探测 4] 盘中调度能力盘点")
    print("="*70)
    
    results = {}
    
    # 4.1 检查 main.py --got 现状
    print("\n[4.1] main.py --got 现状")
    print("  当前 --got 模式为日频一次性运行（收盘后跑 Phase 1 + Phase 2）。")
    print("  无盘中定时调度能力。")
    results['current_got'] = {
        "mode": "日频一次性",
        "timing": "收盘后",
        "intraday_scheduling": False,
    }
    
    # 4.2 调研 Windows 任务计划程序选项
    print("\n[4.2] Windows 任务计划程序（Task Scheduler）")
    print("  可行性：")
    print("    ✓ Windows 原生支持，无需额外依赖")
    print("    ✓ 可配置每日 9:26 / 11:25 / 14:45 定时触发")
    print("    ✓ 支持命令行参数传递（python main.py --got --intraday-time 9:26）")
    print("  限制：")
    print("    ⚠ 需手动配置或通过 schtasks 命令行注册")
    print("    ⚠ 错过触发时间（如机器休眠）不补跑")
    print("    ⚠ 日志/错误处理需额外配置")
    results['windows_task_scheduler'] = {
        "feasible": True,
        "pros": ["原生支持", "可配定时触发", "支持参数传递"],
        "cons": ["需手动配置", "不补跑", "日志需额外配置"],
    }
    
    # 4.3 调研 Python schedule 库选项
    print("\n[4.3] Python schedule 库（第三方）")
    print("  可行性：")
    print("    ✓ 轻量级，API 简洁（schedule.every().day.at('09:26').do(...)）")
    print("    ✓ 可在 main.py 内集成，无需外部任务调度")
    print("    ✓ 支持多时间点（9:26 / 11:25 / 14:45）")
    print("  限制：")
    print("    ⚠ 需进程常驻（while True: schedule.run_pending()）")
    print("    ⚠ 进程崩溃需外部守护（systemd / Windows 服务）")
    print("    ⚠ 需安装第三方库（pip install schedule）")
    results['python_schedule'] = {
        "feasible": True,
        "pros": ["API 简洁", "可内集成", "支持多时间点"],
        "cons": ["需进程常驻", "需外部守护", "需安装第三方库"],
    }
    
    # 4.4 综合建议
    print("\n[4.4] 综合建议")
    print("  短期（Phase 6-0 验证期）：")
    print("    → 手动运行脚本验证数据可行性，暂不实施自动调度")
    print("  中期（Phase 6-1 实施期）：")
    print("    → 优先 Windows 任务计划程序（零依赖，适合固定时点触发）")
    print("    → 备选 Python schedule（如需更灵活的盘中逻辑）")
    results['recommendation'] = {
        "short_term": "手动验证，暂不自动调度",
        "long_term": "优先 Windows 任务计划程序，备选 Python schedule",
    }
    
    return results


# =====================================================================
# 主流程
# =====================================================================

def main():
    """主入口"""
    print("="*70)
    print("Phase 6-0 盘中数据地基探测")
    print("="*70)
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"测试样本: 沪市 {PROBE_SYMBOL_SH} / 深市 {PROBE_SYMBOL_SZ}")
    
    # 获取可用客户端
    client = get_working_client()
    if client is None:
        print("\n" + "="*70)
        print("✗ 全部通达信节点验活失败（TCP 协议或网络不可达）")
        print("="*70)
        print("\n结论：当前网络环境无法验证盘中数据接口能力。")
        print("建议：")
        print("  1. 检查网络连接（能否 ping 通 117.34.114.15）")
        print("  2. 检查防火墙（TCP 7709 端口是否放行）")
        print("  3. 交易日盘中重测（部分接口非交易时段返回空表）")
        
        # 输出空结果
        report = {
            "timestamp": datetime.now().isoformat(),
            "status": "网络不可达",
            "probes": {},
        }
        return report
    
    # 执行四节探测
    try:
        probe1_results = probe_quotes_bid_ask(client)
        probe2_results = probe_minute_kline(client)
        probe3_results = probe_auction_data(client)
        probe4_results = probe_scheduling_capability()
    finally:
        # 关闭连接
        try:
            client.close()
        except:
            pass
    
    # 汇总结果
    report = {
        "timestamp": datetime.now().isoformat(),
        "status": "完成",
        "probes": {
            "1_quotes_bid_ask": probe1_results,
            "2_minute_kline": probe2_results,
            "3_auction_data": probe3_results,
            "4_scheduling": probe4_results,
        },
    }
    
    # 输出总结
    print("\n" + "="*70)
    print("[总结] 探测完成")
    print("="*70)
    print(f"运行时间: {report['timestamp']}")
    print(f"状态: {report['status']}")
    print("\n详细结果已输出到上方各节。")
    print("下一步：将结果整理到 docs/got/plans/Phase6-0_盘中数据地基探测_20261015.md")
    
    return report


if __name__ == "__main__":
    main()
