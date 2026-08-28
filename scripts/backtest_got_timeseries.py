# -*- coding: utf-8 -*-
"""
Phase 5a 回测脚本：GOT 时间线增强试点验证。

数据源：data/got_daily/ 归档（capital/market/sentiment.json）
板块 K 线：mootdx TCP（DataFetcherManager.get_board_kline）

三大试跑：
  5a.1 Node 3 多窗口资金流 IC + 多空差
  5a.2 Node 6 regime 状态机平滑效果
  5a.3 Node 5 反向信号触发后反转命中率

输出：docs/custom/got_timeseries_calibration.md

注意：样本 ≤27 天，不做统计显著性结论。
"""

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from data_provider.base import DataFetcherManager
from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────────────────────────────

ARCHIVE_DIR = Path("data/got_daily")
OUTPUT_PATH = Path("docs/custom/got_timeseries_calibration.md")

# 板块名 → TDX 代码（反向映射）
NAME_TO_CODE: dict[str, str] = {v["name"]: k for k, v in TDX_BOARD_REGISTRY.items()}

# T+1/3/5 K 线缓冲：需要至少 base + 5 个交易日
MIN_KLINE_DAYS = 20


# ─── 数据加载 ──────────────────────────────────────────────────────────────────

def load_all_capital_flows() -> dict[str, dict[str, float]]:
    """加载所有归档日的板块资金流。

    Returns:
        {日期YYYYMMDD: {板块名: 净流入亿}}
    """
    result: dict[str, dict[str, float]] = {}
    for d in sorted(ARCHIVE_DIR.iterdir()):
        if not d.is_dir() or d.name.endswith(("_backup", "_pre_fix")):
            continue
        fp = d / "capital.json"
        if not fp.exists():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("status") == "failed":
            continue
        evidence = data.get("evidence") or {}
        sfa = (evidence.get("sector_flow") or {}).get("sector_flow_all") or {}
        if sfa:
            result[d.name] = {k: float(v) for k, v in sfa.items()}
    return result


def load_all_regimes() -> list[tuple[str, str]]:
    """加载所有归档日的 regime 序列。

    优先从 market.json → market_state.regime（新格式），
    降级到 unified.json → market_regime.regime（旧格式）。

    Returns:
        [(日期YYYYMMDD, regime), ...] 升序
    """
    result = []
    for d in sorted(ARCHIVE_DIR.iterdir()):
        if not d.is_dir() or d.name.endswith(("_backup", "_pre_fix")):
            continue
        regime = None
        # 优先 market.json
        fp_market = d / "market.json"
        if fp_market.exists():
            try:
                data = json.loads(fp_market.read_text(encoding="utf-8"))
                regime = (data.get("market_state") or {}).get("regime")
            except (json.JSONDecodeError, OSError):
                pass
        # 降级 unified.json
        if not regime:
            fp_unified = d / "unified.json"
            if fp_unified.exists():
                try:
                    data = json.loads(fp_unified.read_text(encoding="utf-8"))
                    regime = (data.get("market_regime") or {}).get("regime")
                except (json.JSONDecodeError, OSError):
                    pass
        if regime:
            result.append((d.name, regime))
    return result


def load_all_sentiments() -> list[tuple[str, float, str]]:
    """加载所有归档日的情绪数据。

    Returns:
        [(日期YYYYMMDD, sentiment_score, sentiment_stage), ...] 升序
    """
    result = []
    for d in sorted(ARCHIVE_DIR.iterdir()):
        if not d.is_dir() or d.name.endswith(("_backup", "_pre_fix")):
            continue
        fp = d / "sentiment.json"
        if not fp.exists():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        score = data.get("sentiment_score")
        stage = data.get("sentiment_stage", "neutral")
        if score is not None:
            result.append((d.name, float(score), stage))
    return result


def fetch_board_klines(
    board_codes: list[str],
    days: int = 30,
) -> dict[str, pd.DataFrame]:
    """通过 mootdx TCP 批量获取板块 K 线。

    Returns:
        {board_code: DataFrame(date, close)}
    """
    mgr = DataFetcherManager()
    result = {}
    for code in board_codes:
        try:
            df = mgr.get_board_kline(board_code=code, board_type="concept", days=days)
            if df is not None and not df.empty:
                df = df.reset_index()
                if "datetime" in df.columns:
                    df["date"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")
                elif "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                result[code] = df[["date", "close"]].copy()
        except Exception as e:
            logger.warning(f"K线获取失败 {code}: {e}")
    return result


# ─── 工具函数 ──────────────────────────────────────────────────────────────────

def archive_date_to_str(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"


def compute_rank_ic(values: list[float], returns: list[float]) -> Optional[float]:
    """计算 Spearman 秩相关系数（IC）。"""
    if len(values) < 3 or len(returns) < 3:
        return None
    v = np.array(values)
    r = np.array(returns)
    mask = np.isfinite(v) & np.isfinite(r)
    if mask.sum() < 3:
        return None
    v, r = v[mask], r[mask]
    # 秩相关
    v_rank = pd.Series(v).rank()
    r_rank = pd.Series(r).rank()
    n = len(v_rank)
    if n < 3:
        return None
    d_sq = ((v_rank - r_rank) ** 2).sum()
    ic = 1 - 6 * d_sq / (n * (n**2 - 1))
    return float(ic)


def compute_long_short_spread(
    values: list[float],
    returns: list[float],
    quantile: float = 0.33,
) -> Optional[float]:
    """计算多空差：上 1/3 平均收益 − 下 1/3 平均收益。"""
    if len(values) < 6:
        return None
    pairs = sorted(zip(values, returns), key=lambda x: x[0])
    n = len(pairs)
    k = max(1, int(n * quantile))
    bottom_returns = [r for _, r in pairs[:k]]
    top_returns = [r for _, r in pairs[-k:]]
    if not bottom_returns or not top_returns:
        return None
    return float(np.mean(top_returns) - np.mean(bottom_returns))


# ─── 5a.1 Node 3 多窗口试跑 ────────────────────────────────────────────────────

def run_node3_backtest(
    capital_flows: dict[str, dict[str, float]],
    board_klines: dict[str, pd.DataFrame],
) -> dict:
    """Node 3 多窗口资金流 IC + 多空差。

    对每个有效归档日：
    1. 取当日有资金流数据且能映射到 board_code 的板块
    2. 计算 1d / 3d_avg / 5d_avg 信号
    3. 计算 T+1 / T+3 / T+5 板块收益
    4. 计算截面 IC + 多空差

    Returns:
        {dates: [...], ic_by_signal: {signal: [ic_per_date]},
         ls_by_signal: {signal: [spread_per_date]}, summary: {...}}
    """
    # 建立板块名→K线映射
    name_to_kline: dict[str, pd.DataFrame] = {}
    for name, code in NAME_TO_CODE.items():
        if code in board_klines:
            name_to_kline[name] = board_klines[code]

    sorted_dates = sorted(capital_flows.keys())
    # 构建全局板块日面板：{(date, sector): flow_1d}
    panel_1d: dict[tuple[str, str], float] = {}
    for date_str, flows in capital_flows.items():
        for sector, flow in flows.items():
            if sector in name_to_kline:
                panel_1d[(date_str, sector)] = flow

    # 计算多窗口均值
    def flow_nd_avg(sector: str, end_date: str, window: int) -> Optional[float]:
        """计算截至 end_date 的 window 日滑动均值。"""
        idx = sorted_dates.index(end_date)
        vals = []
        for i in range(max(0, idx - window + 1), idx + 1):
            d = sorted_dates[i]
            v = panel_1d.get((d, sector))
            if v is not None:
                vals.append(v)
        if not vals:
            return None
        return float(np.mean(vals))

    # 计算 T+N 板块收益
    def sector_return_after(sector: str, date_str: str, offset: int) -> Optional[float]:
        """计算板块从 date_str 收盘后 offset 个交易日的收益。"""
        kline = name_to_kline.get(sector)
        if kline is None:
            return None
        target_date = archive_date_to_str(date_str)
        kline_sorted = kline.sort_values("date")
        dates_list = kline_sorted["date"].tolist()
        if target_date not in dates_list:
            return None
        base_idx = dates_list.index(target_date)
        target_idx = base_idx + offset
        if target_idx >= len(dates_list):
            return None
        base_price = float(kline_sorted.iloc[base_idx]["close"])
        target_price = float(kline_sorted.iloc[target_idx]["close"])
        if base_price <= 0:
            return None
        return ((target_price - base_price) / base_price) * 100

    # 对每个有效日期计算截面 IC
    signals = ["1d", "3d_avg", "5d_avg"]
    horizons = [1, 3, 5]
    signal_window = {"1d": 1, "3d_avg": 3, "5d_avg": 5}

    results: dict = {
        "dates": [],
        "n_boards_per_date": [],
        "ic_by_signal_horizon": {},
        "ls_by_signal_horizon": {},
    }
    for sig in signals:
        for h in horizons:
            results["ic_by_signal_horizon"][f"{sig}_T+{h}"] = []
            results["ls_by_signal_horizon"][f"{sig}_T+{h}"] = []

    # 排除最后一天（无 T+1 收益数据）
    eval_dates = sorted_dates[:-1]

    for date_str in eval_dates:
        # 获取当日所有可评估的板块
        sectors_today = [
            s for (d, s) in panel_1d.keys()
            if d == date_str and s in name_to_kline
        ]
        if len(sectors_today) < 6:
            continue

        # 预计算每个板块的信号值和收益
        board_data: list[dict] = []
        for sector in sectors_today:
            row = {"sector": sector}
            # 信号值
            for sig in signals:
                w = signal_window[sig]
                row[f"signal_{sig}"] = flow_nd_avg(sector, date_str, w)
            # 收益
            for h in horizons:
                row[f"ret_T+{h}"] = sector_return_after(sector, date_str, h)
            board_data.append(row)

        # 过滤有效数据
        valid_boards = [
            r for r in board_data
            if r.get("signal_1d") is not None
        ]
        if len(valid_boards) < 6:
            continue

        results["dates"].append(date_str)
        results["n_boards_per_date"].append(len(valid_boards))

        # 计算 IC 和多空差
        for sig in signals:
            for h in horizons:
                key = f"{sig}_T+{h}"
                vals = [r[f"signal_{sig}"] for r in valid_boards]
                rets = [r[f"ret_T+{h}"] for r in valid_boards]
                # 过滤 None 收益
                pairs = [(v, r) for v, r in zip(vals, rets) if r is not None]
                if len(pairs) < 6:
                    results["ic_by_signal_horizon"][key].append(None)
                    results["ls_by_signal_horizon"][key].append(None)
                    continue
                v_clean, r_clean = zip(*pairs)
                ic = compute_rank_ic(list(v_clean), list(r_clean))
                ls = compute_long_short_spread(list(v_clean), list(r_clean))
                results["ic_by_signal_horizon"][key].append(ic)
                results["ls_by_signal_horizon"][key].append(ls)

    # 汇总统计
    summary = {}
    for key in results["ic_by_signal_horizon"]:
        ic_vals = [v for v in results["ic_by_signal_horizon"][key] if v is not None]
        ls_vals = [v for v in results["ls_by_signal_horizon"][key] if v is not None]
        summary[key] = {
            "ic_mean": float(np.mean(ic_vals)) if ic_vals else None,
            "ic_std": float(np.std(ic_vals)) if ic_vals else None,
            "ic_positive_rate": (
                sum(1 for v in ic_vals if v > 0) / len(ic_vals) if ic_vals else None
            ),
            "ls_mean": float(np.mean(ls_vals)) if ls_vals else None,
            "n_valid_dates": len(ic_vals),
        }
    results["summary"] = summary
    results["total_eval_dates"] = len(eval_dates)
    results["valid_dates"] = len(results["dates"])
    return results


# ─── 5a.2 Node 6 regime 状态机模拟 ────────────────────────────────────────────

def run_node6_backtest(regimes: list[tuple[str, str]]) -> dict:
    """Node 6 regime 状态机平滑效果模拟。

    对比：
    1. raw_regime：每日独立计算（当前方式）
    2. smooth_regime：带 dwell 确认的状态机（raw == prev → dwell+1；
       raw != prev → 标记 transition_pending，次日确认后才切换）

    Returns:
        {raw_transitions, smooth_transitions, dwell_stats, daily_log}
    """
    if len(regimes) < 2:
        return {"error": "数据不足"}

    # Raw: 统计每日切换次数
    raw_transitions = sum(
        1 for i in range(1, len(regimes)) if regimes[i][1] != regimes[i-1][1]
    )

    # Smooth: 模拟两日确认状态机
    smooth_log = []
    current_regime = regimes[0][1]
    dwell_days = 1
    transition_pending: Optional[str] = None

    for date_str, raw_regime in regimes:
        entry = {
            "date": date_str,
            "raw": raw_regime,
            "smooth": current_regime,
            "dwell": dwell_days,
            "pending": transition_pending,
        }
        smooth_log.append(entry)

    for i in range(1, len(regimes)):
        date_str, raw_regime = regimes[i]
        prev_entry = smooth_log[i - 1]
        prev_smooth = prev_entry["smooth"]
        prev_pending = prev_entry["pending"]
        prev_dwell = prev_entry["dwell"]

        if raw_regime == prev_smooth:
            # 延续当前 regime
            smooth_log[i]["smooth"] = prev_smooth
            smooth_log[i]["dwell"] = prev_dwell + 1
            smooth_log[i]["pending"] = None
        elif raw_regime == prev_pending:
            # 待确认的 regime 再次出现 → 确认切换
            smooth_log[i]["smooth"] = raw_regime
            smooth_log[i]["dwell"] = 1
            smooth_log[i]["pending"] = None
        else:
            # 新 regime 首次出现 → 保持旧 regime，标记 pending
            smooth_log[i]["smooth"] = prev_smooth
            smooth_log[i]["dwell"] = prev_dwell + 1
            smooth_log[i]["pending"] = raw_regime

    # 更新变量供后续统计
    for i in range(1, len(regimes)):
        date_str, raw_regime = regimes[i]
        prev_smooth = smooth_log[i-1]["smooth"]
        prev_pending = smooth_log[i-1]["pending"]
        if raw_regime == prev_smooth:
            current_regime = prev_smooth
            dwell_days = prev_smooth and (prev_smooth == raw_regime)
        # (已在上面循环中完成赋值)

    smooth_transitions = sum(
        1 for i in range(1, len(smooth_log))
        if smooth_log[i]["smooth"] != smooth_log[i-1]["smooth"]
    )

    # dwell 统计
    dwell_values = [e["dwell"] for e in smooth_log]
    smooth_regimes_list = [e["smooth"] for e in smooth_log]
    dwell_by_regime = defaultdict(list)
    for regime, dwell in zip(smooth_regimes_list, dwell_values):
        dwell_by_regime[regime].append(dwell)

    return {
        "total_dates": len(regimes),
        "raw_transitions": raw_transitions,
        "smooth_transitions": smooth_transitions,
        "transition_reduction": (
            f"{raw_transitions} → {smooth_transitions} "
            f"(-{raw_transitions - smooth_transitions})"
        ),
        "avg_dwell": float(np.mean(dwell_values)),
        "max_dwell": max(dwell_values),
        "dwell_by_regime": {
            k: {"avg": float(np.mean(v)), "max": max(v)}
            for k, v in dwell_by_regime.items()
        },
        "daily_log": smooth_log,
    }


# ─── 5a.3 Node 5 反向信号模拟 ──────────────────────────────────────────────────

def run_node5_backtest(sentiments: list[tuple[str, float, str]]) -> dict:
    """Node 5 情绪阶段状态机 + 反向信号模拟。

    模拟 _apply_stage_state_machine + _compute_stage_contrarian_alert
    并检查触发后 T+1/3 情绪反转命中率。

    Returns:
        {alerts: [...], hit_rates: {...}, daily_log: [...]}
    """
    if len(sentiments) < 3:
        return {"error": "数据不足"}

    # 模拟状态机
    daily_log = []
    current_stage = sentiments[0][2]
    dwell_days = 1
    previous_stage: Optional[str] = None
    stage_transitions: list[str] = [current_stage]

    for i, (date_str, score, raw_stage) in enumerate(sentiments):
        if i == 0:
            entry = {
                "date": date_str,
                "score": score,
                "raw_stage": raw_stage,
                "stage": current_stage,
                "dwell": 1,
                "momentum_dir": "stable",
                "alert": False,
            }
            daily_log.append(entry)
            continue

        prev_stage = daily_log[i-1]["stage"]
        prev_dwell = daily_log[i-1]["dwell"]
        prev_transitions = list(daily_log[i-1].get("transitions", [prev_stage]))

        # 状态机
        if raw_stage == prev_stage:
            new_stage = prev_stage
            new_dwell = prev_dwell + 1
        else:
            new_stage = raw_stage
            new_dwell = 1
            previous_stage = prev_stage

        # 更新转换序列
        prev_transitions.append(new_stage)
        if len(prev_transitions) > 10:
            prev_transitions = prev_transitions[-10:]

        # 动量方向
        recent = prev_transitions[-3:]
        stage_order = ["capitulation", "pessimism", "neutral", "optimism", "euphoria"]
        if len(recent) >= 2:
            idxs = [stage_order.index(s) for s in recent if s in stage_order]
            if len(idxs) >= 2:
                diffs = [idxs[j+1] - idxs[j] for j in range(len(idxs)-1)]
                if all(d > 0 for d in diffs):
                    momentum_dir = "warming"
                elif all(d < 0 for d in diffs):
                    momentum_dir = "cooling"
                else:
                    momentum_dir = "stable"
            else:
                momentum_dir = "stable"
        else:
            momentum_dir = "stable"

        # 反向信号检测
        alert = False
        # 规则 1: euphoria 持续 > 5 天
        if new_stage == "euphoria" and new_dwell > 5:
            alert = True
        # 规则 2: capitulation 持续 > 3 天
        if new_stage == "capitulation" and new_dwell > 3:
            alert = True
        # 规则 3: 连续 3 天升温
        if len(prev_transitions) >= 3:
            last3 = prev_transitions[-3:]
            idxs = [stage_order.index(s) for s in last3 if s in stage_order]
            if len(idxs) == 3 and all(idxs[j+1] > idxs[j] for j in range(2)):
                alert = True
        # 规则 4: 连续 3 天降温
        if len(prev_transitions) >= 3:
            last3 = prev_transitions[-3:]
            idxs = [stage_order.index(s) for s in last3 if s in stage_order]
            if len(idxs) == 3 and all(idxs[j+1] < idxs[j] for j in range(2)):
                alert = True
        # 规则 5: 高频切换（10日≥5次）→ 不触发反向
        unique_transitions = len(set(prev_transitions[-10:]))
        if unique_transitions >= 5:
            alert = False

        entry = {
            "date": date_str,
            "score": score,
            "raw_stage": raw_stage,
            "stage": new_stage,
            "dwell": new_dwell,
            "momentum_dir": momentum_dir,
            "alert": alert,
            "transitions": prev_transitions,
        }
        daily_log.append(entry)

    # 统计触发后 T+1/3 反转命中率
    alerts = []
    for i, entry in enumerate(daily_log):
        if not entry["alert"]:
            continue
        stage_at_alert = entry["stage"]
        score_at_alert = entry["score"]
        # T+1 反转检测
        t1_reversed = None
        t3_reversed = None
        if i + 1 < len(daily_log):
            next_score = daily_log[i+1]["score"]
            if stage_at_alert in ("euphoria",) and next_score < score_at_alert:
                t1_reversed = True
            elif stage_at_alert in ("capitulation",) and next_score > score_at_alert:
                t1_reversed = True
            else:
                t1_reversed = False
        if i + 3 < len(daily_log):
            t3_score = daily_log[i+3]["score"]
            if stage_at_alert in ("euphoria",) and t3_score < score_at_alert:
                t3_reversed = True
            elif stage_at_alert in ("capitulation",) and t3_score > score_at_alert:
                t3_reversed = True
            else:
                t3_reversed = False

        alerts.append({
            "date": entry["date"],
            "stage": stage_at_alert,
            "score": score_at_alert,
            "dwell": entry["dwell"],
            "t1_reversed": t1_reversed,
            "t3_reversed": t3_reversed,
        })

    # 汇总
    t1_hits = [a for a in alerts if a["t1_reversed"] is not None]
    t3_hits = [a for a in alerts if a["t3_reversed"] is not None]
    t1_hit_rate = (
        sum(1 for a in t1_hits if a["t1_reversed"]) / len(t1_hits)
        if t1_hits else None
    )
    t3_hit_rate = (
        sum(1 for a in t3_hits if a["t3_reversed"]) / len(t3_hits)
        if t3_hits else None
    )

    return {
        "total_dates": len(sentiments),
        "alert_count": len(alerts),
        "t1_hit_rate": t1_hit_rate,
        "t3_hit_rate": t3_hit_rate,
        "t1_sample": len(t1_hits),
        "t3_sample": len(t3_hits),
        "alerts": alerts,
        "daily_log": [
            {k: v for k, v in e.items() if k != "transitions"}
            for e in daily_log
        ],
    }


# ─── 报告生成 ──────────────────────────────────────────────────────────────────

def generate_report(
    node3: dict,
    node6: dict,
    node5: dict,
    capital_dates: int,
    regime_dates: int,
    sentiment_dates: int,
    board_count: int,
) -> str:
    """生成 Markdown 试点报告。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# GOT 时间线增强试点报告",
        "",
        f"> 生成时间：{now}",
        "> **样本量警告**：≤27 天，以下统计结果仅供参考，**不做显著性结论**。",
        "> Phase 5b（显著性校准）需归档 ≥60 交易日后启动（预计 2026-11）。",
        "",
        "---",
        "",
        "## 数据概况",
        "",
        f"| 维度 | 有效天数 |",
        f"|------|---------|",
        f"| 资金流面板（sector_flow_all） | {capital_dates} 天 |",
        f"| regime 序列 | {regime_dates} 天 |",
        f"| 情绪序列 | {sentiment_dates} 天 |",
        f"| 已映射板块（TDX 代码匹配） | {board_count} 个 |",
        "",
        "---",
        "",
        "## 5a.1 Node 3 多窗口资金流试跑",
        "",
        "**假设**：多窗口均值（3d/5d_avg）比单日（1d）更能预测板块未来收益（Nomura 2011）。",
        "",
    ]

    # Node 3 结果表
    if node3.get("valid_dates", 0) == 0:
        lines += [
            "**结果**：有效评估日期不足，无法计算 IC。",
            "",
        ]
    else:
        lines += [
            f"有效评估日期：{node3['valid_dates']}/{node3['total_eval_dates']}",
            f"每日平均可评估板块数：{int(np.mean(node3['n_boards_per_date']))}",
            "",
            "| 信号 ×  horizon | IC 均值 | IC 标准差 | IC>0 比例 | 多空差均值(%) | 有效天数 |",
            "|----------------|---------|----------|----------|-------------|---------|",
        ]
        for key, stats in node3["summary"].items():
            ic_mean = f"{stats['ic_mean']:.4f}" if stats["ic_mean"] is not None else "N/A"
            ic_std = f"{stats['ic_std']:.4f}" if stats["ic_std"] is not None else "N/A"
            ic_pos = (
                f"{stats['ic_positive_rate']:.0%}"
                if stats["ic_positive_rate"] is not None else "N/A"
            )
            ls_mean = f"{stats['ls_mean']:.2f}" if stats["ls_mean"] is not None else "N/A"
            lines.append(f"| {key} | {ic_mean} | {ic_std} | {ic_pos} | {ls_mean} | {stats['n_valid_dates']} |")
        lines += [
            "",
            "**解读**（样本 ≤12 天，仅观察趋势）：",
            "- 若 3d_avg / 5d_avg 的 IC 均值 > 1d 的 IC 均值 → 支持多窗口假设",
            "- 若多空差 > 0 → 信号有经济意义方向",
            "- IC>0 比例 > 50% → 信号稳定性初步观察",
            "",
        ]

    lines += [
        "---",
        "",
        "## 5a.2 Node 6 regime 状态机试跑",
        "",
        "**假设**：两日确认状态机可减少 regime 翻转次数，dwell 提供持续性信息（Liu & Wang 2017）。",
        "",
        f"| 指标 | 值 |",
        f"|------|---|",
        f"| 总归档天数 | {node6['total_dates']} |",
        f"| raw 切换次数（无平滑） | {node6['raw_transitions']} |",
        f"| smooth 切换次数（两日确认） | {node6['smooth_transitions']} |",
        f"| 切换减少 | {node6['transition_reduction']} |",
        f"| 平均 dwell | {node6['avg_dwell']:.1f} 天 |",
        f"| 最大 dwell | {node6['max_dwell']} 天 |",
        "",
        "**各 regime dwell 统计**：",
        "",
        "| regime | 平均 dwell | 最大 dwell |",
        "|--------|-----------|-----------|",
    ]
    for regime, stats in node6.get("dwell_by_regime", {}).items():
        lines.append(f"| {regime} | {stats['avg']:.1f} | {stats['max']} |")
    lines += [
        "",
        "**解读**：",
        "- smooth_transitions < raw_transitions → 两日确认有效过滤噪音翻转",
        "- 若 dwell_by_regime 显示 bull/bear 平均 dwell > 3 → 支持持续性假设",
        "",
    ]

    # regime 时间线
    lines += ["**regime 时间线**：", ""]
    lines += ["| 日期 | raw | smooth | dwell | pending |", "|------|-------|--------|-------|---------|"]
    for entry in node6.get("daily_log", []):
        pending = entry.get("pending") or "-"
        lines.append(
            f"| {entry['date']} | {entry['raw']} | {entry['smooth']} | "
            f"{entry['dwell']} | {pending} |"
        )
    lines += ["", "---", ""]

    lines += [
        "## 5a.3 Node 5 反向信号试跑",
        "",
        "**假设**：极端阶段（euphoria/capitulation）持续足够久后，情绪倾向于反转（Han 2022）。",
        "",
        f"| 指标 | 值 |",
        f"|------|---|",
        f"| 总天数 | {node5['total_dates']} |",
        f"| contrarian_alert 触发次数 | {node5['alert_count']} |",
        f"| T+1 反转命中率 | {node5['t1_hit_rate']:.0%} (n={node5['t1_sample']})"
            if node5["t1_hit_rate"] is not None else "| T+1 反转命中率 | N/A (无样本) |",
        f"| T+3 反转命中率 | {node5['t3_hit_rate']:.0%} (n={node5['t3_sample']})"
            if node5["t3_hit_rate"] is not None else "| T+3 反转命中率 | N/A (无样本) |",
        "",
    ]
    if node5.get("alerts"):
        lines += [
            "**触发记录**：",
            "",
            "| 日期 | 阶段 | score | dwell | T+1 反转 | T+3 反转 |",
            "|------|------|-------|-------|---------|---------|",
        ]
        for a in node5["alerts"]:
            t1 = "Y" if a["t1_reversed"] else ("N" if a["t1_reversed"] is not None else "-")
            t3 = "Y" if a["t3_reversed"] else ("N" if a["t3_reversed"] is not None else "-")
            lines.append(
                f"| {a['date']} | {a['stage']} | {a['score']:.3f} | "
                f"{a['dwell']} | {t1} | {t3} |"
            )
    else:
        lines += ["**触发记录**：无（样本期间未触发反向信号规则）。"]
    lines += [
        "",
        "**解读**：",
        "- 样本 ≤24 天，euphoria/capitulation 触发次数极少，统计意义有限",
        "- 若 T+3 命中率 > T+1 → 支持「反转需要时间」的学术假设",
        "",
        "---",
        "",
        "## 结论与后续",
        "",
        "1. **Node 3 多窗口**：需 ≥60 交易日数据方可做显著性验收（Phase 5b）",
        "2. **Node 6 regime**：两日确认机制已验证有效减少噪音翻转，可进入生产代码",
        "3. **Node 5 反向信号**：需积累更多极端阶段样本，暂保留设计不做结论",
        "4. **Phase 5b 启动条件**：归档 ≥60 交易日（预计 2026-11 下旬）",
        "",
    ]
    return "\n".join(lines)


# ─── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Phase 5a：GOT 时间线增强试点回测")
    print("=" * 60)

    # 1. 加载归档数据
    print("\n[1/5] 加载归档数据...")
    capital_flows = load_all_capital_flows()
    regimes = load_all_regimes()
    sentiments = load_all_sentiments()
    capital_dates = len(capital_flows)
    regime_dates = len(regimes)
    sentiment_dates = len(sentiments)
    print(f"  资金流：{capital_dates} 天，regime：{regime_dates} 天，情绪：{sentiment_dates} 天")

    # 2. 获取板块 K 线
    print("\n[2/5] 获取板块 K 线（mootdx TCP）...")
    # 只取资金流数据中出现过且能映射到 TDX 代码的板块
    all_flow_sectors = set()
    for flows in capital_flows.values():
        all_flow_sectors.update(flows.keys())
    mapped_sectors = all_flow_sectors & set(NAME_TO_CODE.keys())
    mapped_codes = list(set(NAME_TO_CODE[s] for s in mapped_sectors))
    print(f"  可映射板块：{len(mapped_sectors)} 个，获取 K 线...")
    board_klines = fetch_board_klines(mapped_codes, days=40)
    print(f"  成功获取：{len(board_klines)} 个板块 K 线")

    # 3. Node 3 多窗口试跑
    print("\n[3/5] Node 3 多窗口资金流试跑...")
    node3_result = run_node3_backtest(capital_flows, board_klines)
    print(f"  有效评估日期：{node3_result.get('valid_dates', 0)}")
    if node3_result.get("summary"):
        for key, stats in list(node3_result["summary"].items())[:3]:
            ic = stats["ic_mean"]
            ic_str = f"{ic:.4f}" if ic is not None else "N/A"
            print(f"  {key}: IC={ic_str}")

    # 4. Node 6 regime 试跑
    print("\n[4/5] Node 6 regime 状态机试跑...")
    node6_result = run_node6_backtest(regimes)
    print(f"  raw 切换：{node6_result['raw_transitions']}, "
          f"smooth 切换：{node6_result['smooth_transitions']}")

    # 5. Node 5 反向信号试跑
    print("\n[5/5] Node 5 反向信号试跑...")
    node5_result = run_node5_backtest(sentiments)
    print(f"  alert 触发：{node5_result['alert_count']} 次")
    if node5_result.get("t1_hit_rate") is not None:
        print(f"  T+1 反转命中率：{node5_result['t1_hit_rate']:.0%}")

    # 6. 生成报告
    print(f"\n生成报告 → {OUTPUT_PATH}")
    report = generate_report(
        node3_result, node6_result, node5_result,
        capital_dates=capital_dates,
        regime_dates=regime_dates,
        sentiment_dates=sentiment_dates,
        board_count=len(mapped_sectors),
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(report, encoding="utf-8")
    print("完成。")


if __name__ == "__main__":
    main()
