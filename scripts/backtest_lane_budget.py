#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分道预算回测：按 regime×lane 分箱统计候选股前向超额收益分布

回答「给定 regime 与催化供给，预算该向哪条道倾斜、倾斜多少才有统计区分度」。

═══════════════════════════════════════════════════════════════════════════════
口径红线（写死，不可绕过）
═══════════════════════════════════════════════════════════════════════════════

1. 前向收益 = close[t+N] / close[t] - 1
   - 禁止任何 shift(+N) 前瞻写法（历史教训：bt_winrate_pool.py / bt_blame.py
     误用 piv.shift(+3)/piv 取过去值，制造伪短期反转，已撤回）
   - 正确写法：df['close'].shift(-N) / df['close'] - 1
   - t+N 越界（归档日太近无足够未来 K 线）标 NaN 剔除并计数，不得借用 t 日
     之后才知道的信息

2. 一律减基准超额：双基准并列
   - 881100.SH（中证全指）
   - 000852.SH（中证1000）
   - 各自算一遍超额并列报，不得只报单基准

3. 窗口：
   - mainline 道：T+5 / T+10
   - ambush 道：T+3 / T+5
   - unknown 道：T+5 / T+10（同 mainline）

4. 分道（lane）：
   - 首选：candidate_pool.json universe 元素的道标签 sector_type（Phase 2 落盘时写入，
     screener 词表 active_mainline / ambush → mainline / ambush；2026-10-20 起）
   - 降级：无标签的历史归档，从 sector.json 的 active_mainline / ambush 列表按
     sector_name 匹配板块归属（旧口径）
   - 两条路都失败或缺字段 → unknown（计数并在报告给出「unknown 占比」）

5. regime 分箱：
   - 从当日 unified.json 的 market_regime.regime 读（bull / transition / bear）
   - 当日无 unified.json 或 regime 字段缺失 → missing（剔除计数）

6. 样本量纪律：
   - n ≤ 2 的箱不聚合（不算均值/中位数，只列原始值）
   - 写死在聚合函数里，不可绕过

7. 降级出口（D1 验收红线）：
   - 若全部 regime×lane×window 箱区分度不显著（均值差异 < 2pp 或 p > 0.10），
     则 f() 维持现状等权 ROLE_MULTIPLIER，不接线分道预算
   - 引用实施计划 D1 原文语义：「D1 区分度报告显著（负责人认可口径）+
     Layer 2 判据（≥20 交易日 + ≥30 票）完成」才允许接线

═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_provider import DataFetcherManager
from src.core.trading_calendar import is_market_open


# ═════════════════════════════════════════════════════════════════════════════
# 常量
# ═════════════════════════════════════════════════════════════════════════════

GOT_DAILY_DIR = Path("data/got_daily")

# 双基准指数
# 注：原计划基准 881100（中证全指）不可取数——它是 TDX 板块指数族（88xxxx 前缀，
# mootdx 拒收为北交所、6 源全失败、mootdx index_bars 亦空）。中证全指真码
# 000985.SH 已实测可走 DataFetcherManager 链（TencentFetcher）。不改 data 层
# 路由扩展 88xxxx（另一项目量级，不钻）。
BENCHMARK_INDICES = {
    "000985.SH": "中证全指",
    "000852.SH": "中证1000",
}

# 窗口配置（按道）
WINDOW_CONFIG = {
    "mainline": [5, 10],
    "ambush": [3, 5],
    "unknown": [5, 10],  # 同 mainline
}

# 样本量阈值
MIN_SAMPLE_SIZE = 3  # n ≤ 2 不聚合


# ═════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═════════════════════════════════════════════════════════════════════════════

def parse_date(date_str: str) -> datetime:
    """解析日期字符串（YYYYMMDD 或 YYYY-MM-DD）"""
    date_str = date_str.replace("-", "")
    return datetime.strptime(date_str, "%Y%m%d")


def format_date(dt: datetime) -> str:
    """格式化日期为 YYYYMMDD"""
    return dt.strftime("%Y%m%d")


def add_trading_days(start_date: datetime, n_days: int) -> Optional[datetime]:
    """
    从 start_date 向前偏移 n 个交易日
    
    使用 trading_calendar.is_market_open() 逐日 walk 计数。
    若 30 个日历日内无法找到足够交易日，返回 None（越界）。
    """
    current = start_date
    count = 0
    max_calendar_days = 30  # 防止无限循环
    
    for _ in range(max_calendar_days):
        current += timedelta(days=1)
        if is_market_open("cn", current.date()):
            count += 1
            if count >= n_days:
                return current
    
    # 越界
    return None


def load_candidate_pool(archive_date: str) -> Optional[Dict]:
    """
    加载 candidate_pool.json
    
    容错：文件不存在或解析失败返回 None
    """
    path = GOT_DAILY_DIR / archive_date / "candidate_pool.json"
    if not path.exists():
        return None
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] 解析 {path} 失败: {e}", file=sys.stderr)
        return None


def load_sector(archive_date: str) -> Optional[Dict]:
    """
    加载 sector.json
    
    容错：文件不存在或解析失败返回 None
    """
    path = GOT_DAILY_DIR / archive_date / "sector.json"
    if not path.exists():
        return None
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] 解析 {path} 失败: {e}", file=sys.stderr)
        return None


def load_unified(archive_date: str) -> Optional[Dict]:
    """
    加载 unified.json
    
    容错：文件不存在或解析失败返回 None
    """
    path = GOT_DAILY_DIR / archive_date / "unified.json"
    if not path.exists():
        return None
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] 解析 {path} 失败: {e}", file=sys.stderr)
        return None


def extract_regime(unified: Optional[Dict]) -> str:
    """
    从 unified.json 提取 regime
    
    容错：字段缺失返回 "missing"
    """
    if unified is None:
        return "missing"
    
    try:
        regime = unified.get("market_regime", {}).get("regime")
        if regime in ("bull", "transition", "bear"):
            return regime
        return "missing"
    except Exception:
        return "missing"


def build_sector_lane_map(sector: Optional[Dict]) -> Dict[str, str]:
    """
    从 sector.json 构建 sector_name → lane 映射
    
    容错：字段缺失返回空 dict
    """
    if sector is None:
        return {}
    
    lane_map = {}
    
    # active_mainline → mainline
    for group in sector.get("active_mainline", []):
        # group 有 members 列表，每个 member 有 name
        for member in group.get("members", []):
            name = member.get("name")
            if name:
                lane_map[name] = "mainline"
    
    # ambush → ambush
    for item in sector.get("ambush", []):
        name = item.get("name")
        if name:
            lane_map[name] = "ambush"
    
    return lane_map


def extract_candidates(candidate_pool: Dict, lane_map: Dict[str, str]) -> List[Dict]:
    """
    从 candidate_pool 提取候选股列表，附加 lane 信息
    
    容错：字段缺失标 unknown
    """
    candidates = []
    
    for item in candidate_pool.get("universe", []):
        try:
            symbol = item.get("symbol", "").strip()
            if not symbol:
                continue  # 跳过空 symbol
            
            sector_name = item.get("sector_name", "").strip()

            # 匹配 lane（2026-10-20 D4）：优先读 Phase 2 落盘的道标签 sector_type
            # （screener 词表 active_mainline/ambush 原样透传）；无标签的历史归档
            # 降级走 sector.json 名单匹配 fallback（旧口径，零回归）。
            _stype = (item.get("sector_type") or "").strip()
            if _stype in ("active_mainline", "mainline"):
                lane = "mainline"
            elif _stype == "ambush":
                lane = "ambush"
            else:
                lane = lane_map.get(sector_name, "unknown")
            
            candidates.append({
                "symbol": symbol,
                "sector_name": sector_name,
                "lane": lane,
            })
        except Exception as e:
            print(f"[WARN] 解析候选股失败: {e}", file=sys.stderr)
            continue
    
    return candidates


def fetch_close_prices(symbols: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """
    批量获取股票/指数的收盘价序列
    
    返回 {symbol: DataFrame}，DataFrame 包含 date, close 列
    """
    manager = DataFetcherManager()
    result = {}
    
    all_symbols = symbols + list(BENCHMARK_INDICES.keys())
    
    for sym in all_symbols:
        try:
            _ret = manager.get_daily_data(sym, start_date=start_date, end_date=end_date, days=60)
            # DataFetcherManager.get_daily_data 返回 (df, source) 二元组（market_analysis.py
            # _get_index_kline 同款先例）；旧写法把 tuple 当 df 用导致
            # 'tuple' object has no attribute 'empty'。
            df = _ret[0] if isinstance(_ret, tuple) else _ret
            if df is not None and not df.empty:
                # 标准化列名
                df = df.rename(columns={"date": "date", "close": "close"})
                if "date" in df.columns and "close" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                    result[sym] = df[["date", "close"]].copy()
        except Exception as e:
            print(f"[WARN] 获取 {sym} 数据失败: {e}", file=sys.stderr)
            continue
    
    return result


def compute_forward_returns(
    archive_date: str,
    candidates: List[Dict],
    price_data: Dict[str, pd.DataFrame],
) -> List[Dict]:
    """
    计算每只候选股的前向收益（减基准超额）
    
    口径红线：
    - 前向收益 = close[t+N] / close[t] - 1
    - 禁止 shift(+N) 前瞻
    - t+N 越界标 NaN 剔除
    """
    archive_dt = parse_date(archive_date)
    
    # 找到归档日或之后第一个交易日作为 t
    t_date = None
    for sym, df in price_data.items():
        df_sorted = df.sort_values("date")
        mask = df_sorted["date"] >= archive_dt
        if mask.any():
            t_date = df_sorted.loc[mask, "date"].iloc[0]
            break
    
    if t_date is None:
        return []
    
    results = []
    
    for cand in candidates:
        symbol = cand["symbol"]
        lane = cand["lane"]
        
        if symbol not in price_data:
            continue
        
        stock_df = price_data[symbol].sort_values("date").reset_index(drop=True)
        
        # 找到 t 日
        t_mask = stock_df["date"] == t_date
        if not t_mask.any():
            continue
        
        t_idx = stock_df.loc[t_mask].index[0]
        close_t = stock_df.loc[t_idx, "close"]
        
        if pd.isna(close_t) or close_t <= 0:
            continue
        
        # 计算各窗口前向收益
        windows = WINDOW_CONFIG.get(lane, WINDOW_CONFIG["unknown"])
        
        for n in windows:
            t_plus_n_date = add_trading_days(t_date.to_pydatetime(), n)
            if t_plus_n_date is None:
                # 越界，标 NaN
                stock_ret = np.nan
            else:
                # 找到 t+N 日或之后第一个交易日
                tn_mask = stock_df["date"] >= t_plus_n_date
                if tn_mask.any():
                    tn_idx = stock_df.loc[tn_mask].index[0]
                    close_tn = stock_df.loc[tn_idx, "close"]
                    if pd.isna(close_tn) or close_tn <= 0:
                        stock_ret = np.nan
                    else:
                        # 正确前向：close[t+N] / close[t] - 1
                        stock_ret = close_tn / close_t - 1
                else:
                    stock_ret = np.nan
            
            # 计算基准超额
            benchmark_rets = {}
            for bench_code, bench_name in BENCHMARK_INDICES.items():
                if bench_code not in price_data:
                    benchmark_rets[bench_code] = np.nan
                    continue
                
                bench_df = price_data[bench_code].sort_values("date").reset_index(drop=True)
                
                # 找到 t 日
                bench_t_mask = bench_df["date"] == t_date
                if not bench_t_mask.any():
                    benchmark_rets[bench_code] = np.nan
                    continue
                
                bench_t_idx = bench_df.loc[bench_t_mask].index[0]
                bench_close_t = bench_df.loc[bench_t_idx, "close"]
                
                if pd.isna(bench_close_t) or bench_close_t <= 0:
                    benchmark_rets[bench_code] = np.nan
                    continue
                
                # 找到 t+N 日
                if t_plus_n_date is None:
                    benchmark_rets[bench_code] = np.nan
                else:
                    bench_tn_mask = bench_df["date"] >= t_plus_n_date
                    if bench_tn_mask.any():
                        bench_tn_idx = bench_df.loc[bench_tn_mask].index[0]
                        bench_close_tn = bench_df.loc[bench_tn_idx, "close"]
                        if pd.isna(bench_close_tn) or bench_close_tn <= 0:
                            benchmark_rets[bench_code] = np.nan
                        else:
                            benchmark_rets[bench_code] = bench_close_tn / bench_close_t - 1
                    else:
                        benchmark_rets[bench_code] = np.nan
            
            # 超额 = 股票收益 - 基准收益（键名跟随 BENCHMARK_INDICES，不再硬编码）
            row = {
                "archive_date": archive_date,
                "symbol": symbol,
                "lane": lane,
                "regime": cand.get("regime", "missing"),
                "window": f"T+{n}",
                "stock_ret": stock_ret,
            }
            for bench_code, bench_name in BENCHMARK_INDICES.items():
                _suffix = bench_code.split(".")[0]
                row[f"bench_{_suffix}"] = benchmark_rets.get(bench_code, np.nan)
                row[f"excess_{_suffix}"] = stock_ret - benchmark_rets.get(bench_code, np.nan)
            results.append(row)
    
    return results


def aggregate_box(values: List[float]) -> Dict:
    """
    聚合统计箱
    
    纪律：n ≤ 2 不聚合，只列原始值
    """
    values = [v for v in values if not pd.isna(v)]
    n = len(values)
    
    if n == 0:
        return {"n": 0, "mean": np.nan, "median": np.nan, "p25": np.nan, "p75": np.nan, "win_rate": np.nan, "raw_values": []}
    
    if n <= 2:
        # 不聚合，只列原始值
        return {
            "n": n,
            "mean": np.nan,
            "median": np.nan,
            "p25": np.nan,
            "p75": np.nan,
            "win_rate": np.nan,
            "raw_values": values,
        }
    
    arr = np.array(values)
    return {
        "n": n,
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
        "win_rate": float(np.mean(arr > 0)),
        "raw_values": [],
    }


def generate_report(
    all_results: List[Dict],
    regime_counts: Dict[str, int],
    lane_counts: Dict[str, int],
    nan_counts: Dict[str, int],
) -> str:
    """生成 Markdown 报告"""
    
    lines = []
    lines.append("# 分道预算回测报告")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    
    # 统计概览
    lines.append("## 统计概览")
    lines.append("")
    lines.append(f"- **总候选股数**: {len(all_results)}")
    lines.append(f"- **regime 分布**: {dict(regime_counts)}")
    lines.append(f"- **lane 分布**: {dict(lane_counts)}")
    lines.append(f"- **NaN 剔除计数**: {dict(nan_counts)}")
    lines.append("")
    
    # unknown / missing 占比
    total = len(all_results) if len(all_results) > 0 else 1
    unknown_count = lane_counts.get("unknown", 0)
    missing_count = regime_counts.get("missing", 0)
    lines.append(f"- **unknown 占比**: {unknown_count}/{total} = {unknown_count/total:.1%}")
    lines.append(f"- **missing 占比**: {missing_count}/{total} = {missing_count/total:.1%}")
    lines.append("")
    
    # 按 regime × lane × window 分箱
    df = pd.DataFrame(all_results)
    
    if df.empty:
        lines.append("## ⚠️ 无有效数据")
        lines.append("")
        lines.append("所有候选股的 t+N 越界或数据缺失，无法计算前向收益。")
        return "\n".join(lines)
    
    # 分组统计
    grouped = df.groupby(["regime", "lane", "window"])
    
    lines.append("## 分箱统计（超额收益）")
    lines.append("")
    
    # 双基准并列（键名跟随 BENCHMARK_INDICES）
    for bench_code, bench_name in BENCHMARK_INDICES.items():
        bench_col = f"excess_{bench_code.split('.')[0]}"
        lines.append(f"### 基准：{bench_name}")
        lines.append("")
        lines.append("| regime | lane | window | n | mean | median | p25 | p75 | win_rate |")
        lines.append("|--------|------|--------|---|------|--------|-----|-----|----------|")
        
        for (regime, lane, window), group in grouped:
            values = group[bench_col].dropna().tolist()
            box = aggregate_box(values)
            
            if box["n"] == 0:
                lines.append(f"| {regime} | {lane} | {window} | 0 | - | - | - | - | - |")
            elif box["n"] <= 2:
                raw_str = ", ".join([f"{v:.2%}" for v in box["raw_values"]])
                lines.append(f"| {regime} | {lane} | {window} | {box['n']} | **n≤2 不聚合** | - | - | - | - |")
                lines.append(f"| | | | | 原始值: {raw_str} | | | | |")
            else:
                lines.append(
                    f"| {regime} | {lane} | {window} | {box['n']} | {box['mean']:.2%} | "
                    f"{box['median']:.2%} | {box['p25']:.2%} | {box['p75']:.2%} | {box['win_rate']:.1%} |"
                )
        
        lines.append("")
    
    # 降级出口声明
    lines.append("## 降级出口声明（D1 验收红线）")
    lines.append("")
    lines.append("若全部 regime×lane×window 箱区分度不显著（均值差异 < 2pp 或 p > 0.10），则：")
    lines.append("")
    lines.append("> **f() 维持现状等权 ROLE_MULTIPLIER，不接线分道预算**")
    lines.append("")
    lines.append("引用实施计划 D1 原文语义：")
    lines.append("")
    lines.append("> 「D1 区分度报告显著（负责人认可口径）+ Layer 2 判据（≥20 交易日 + ≥30 票）完成」")
    lines.append("> 才允许接线分道预算。")
    lines.append("")
    lines.append("当前报告样本量不足，不满足 Layer 2 判据，建议继续积累数据后再做裁决。")
    lines.append("")
    
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# 主流程
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="分道预算回测")
    parser.add_argument("--start", type=str, default="20260724", help="开始日期（YYYYMMDD）")
    parser.add_argument("--end", type=str, default=None, help="结束日期（YYYYMMDD），默认最新")
    parser.add_argument("--out", type=str, default=None, help="输出文件路径（可选，默认 stdout）")
    args = parser.parse_args()
    
    start_dt = parse_date(args.start)
    
    # 结束日期：默认今天
    if args.end:
        end_dt = parse_date(args.end)
    else:
        end_dt = datetime.now()
    
    # 扫描所有归档日
    archive_dates = []
    for item in sorted(GOT_DAILY_DIR.iterdir()):
        if item.is_dir() and item.name.isdigit():
            dt = parse_date(item.name)
            if start_dt <= dt <= end_dt:
                archive_dates.append(item.name)
    
    if not archive_dates:
        print(f"[ERROR] 未找到 {args.start} ~ {args.end} 之间的归档日", file=sys.stderr)
        sys.exit(1)
    
    print(f"[INFO] 扫描 {len(archive_dates)} 个归档日: {archive_dates[0]} ~ {archive_dates[-1]}", file=sys.stderr)
    
    # 收集所有候选股
    all_candidates = []
    regime_counts = defaultdict(int)
    lane_counts = defaultdict(int)
    nan_counts = defaultdict(int)
    
    for archive_date in archive_dates:
        # 加载 JSON
        candidate_pool = load_candidate_pool(archive_date)
        sector = load_sector(archive_date)
        unified = load_unified(archive_date)
        
        if candidate_pool is None:
            continue
        
        # 提取 regime
        regime = extract_regime(unified)
        regime_counts[regime] += 1
        
        # 构建 lane 映射
        lane_map = build_sector_lane_map(sector)
        
        # 提取候选股
        candidates = extract_candidates(candidate_pool, lane_map)
        
        for cand in candidates:
            cand["archive_date"] = archive_date
            cand["regime"] = regime
            lane_counts[cand["lane"]] += 1
        
        all_candidates.extend(candidates)
    
    if not all_candidates:
        print("[WARN] 无候选股数据", file=sys.stderr)
        report = "# 分道预算回测报告\n\n无候选股数据。\n"
    else:
        print(f"[INFO] 共 {len(all_candidates)} 只候选股", file=sys.stderr)
        
        # 收集所有需要获取价格的 symbol
        symbols = list(set([c["symbol"] for c in all_candidates]))
        
        # 计算价格获取的日期范围（多取一些以覆盖 t+N）
        price_start = (start_dt - timedelta(days=5)).strftime("%Y-%m-%d")
        price_end = (end_dt + timedelta(days=30)).strftime("%Y-%m-%d")
        
        print(f"[INFO] 获取 {len(symbols)} 只股票 + {len(BENCHMARK_INDICES)} 个指数的价格数据...", file=sys.stderr)
        price_data = fetch_close_prices(symbols, price_start, price_end)
        
        print(f"[INFO] 成功获取 {len(price_data)} 个标的的价格数据", file=sys.stderr)
        
        # 计算前向收益
        all_results = []
        for archive_date in archive_dates:
            candidates_on_date = [c for c in all_candidates if c["archive_date"] == archive_date]
            results = compute_forward_returns(archive_date, candidates_on_date, price_data)
            
            # 统计 NaN（键名跟随 BENCHMARK_INDICES）
            for r in results:
                if pd.isna(r["stock_ret"]):
                    nan_counts["stock_ret_nan"] += 1
                for bench_code, _bench_name in BENCHMARK_INDICES.items():
                    _suffix = bench_code.split(".")[0]
                    if pd.isna(r.get(f"excess_{_suffix}", np.nan)):
                        nan_counts[f"excess_{_suffix}_nan"] += 1
            
            all_results.extend(results)
        
        print(f"[INFO] 计算完成，共 {len(all_results)} 条收益记录", file=sys.stderr)
        
        # 生成报告
        report = generate_report(all_results, regime_counts, lane_counts, nan_counts)
    
    # 输出
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[INFO] 报告已写入 {args.out}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
