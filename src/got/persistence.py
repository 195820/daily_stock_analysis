"""
GOT 聚合节点上下文持久化

存储方案：
- JSON 文件按交易日日期命名：previous_context_YYYYMMDD.json
- 存放目录：data/aggregator_contexts/
- 跨节假日：仅取交易日数据（由调用方通过交易日历确定前一交易日日期）
- 首日运行：无历史文件，返回 None（跳过时间一致性校验）
"""

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 持久化目录（项目根目录下 data/aggregator_contexts/）
_CONTEXTS_DIR = Path("data/aggregator_contexts")


def _ensure_dir() -> None:
    """确保目录存在"""
    _CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(filepath: Path, data: Any) -> None:
    """原子写入 JSON：先写 .tmp 再 os.replace，防止写入中途崩溃损坏文件"""
    tmp_path = filepath.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp_path, filepath)


def _filepath_for(trading_date: date) -> Path:
    """根据交易日返回对应 JSON 文件路径"""
    date_str = trading_date.strftime("%Y%m%d")
    return _CONTEXTS_DIR / f"previous_context_{date_str}.json"


def load_previous_context(trading_date: date) -> Optional[dict[str, Any]]:
    """加载前一交易日的聚合输出

    Args:
        trading_date: 前一交易日（调用方已通过交易日历确定）

    Returns:
        前一交易日的 unified_context dict；不存在时返回 None
    """
    filepath = _filepath_for(trading_date)
    if not filepath.exists():
        logger.info(f"[persistence] 无历史上下文文件: {filepath.name}")
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"[persistence] 已加载历史上下文: {filepath.name}")
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[persistence] 读取失败: {filepath.name}, 错误: {e}")
        return None


def save_current_context(context: dict[str, Any], trading_date: date) -> Path:
    """保存当前交易日的聚合输出

    Args:
        context: unified_context 序列化后的 dict
        trading_date: 当前交易日

    Returns:
        保存的文件路径
    """
    _ensure_dir()
    filepath = _filepath_for(trading_date)

    _atomic_write_json(filepath, context)

    logger.info(f"[persistence] 已保存上下文: {filepath.name}")
    return filepath


def list_saved_contexts() -> list[tuple[date, Path]]:
    """列出所有已保存的上下文文件（用于调试）

    Returns:
        [(日期, 文件路径), ...] 按日期降序排列
    """
    if not _CONTEXTS_DIR.exists():
        return []

    results = []
    for filepath in _CONTEXTS_DIR.glob("previous_context_*.json"):
        date_str = filepath.stem.replace("previous_context_", "")
        try:
            trading_date = datetime.strptime(date_str, "%Y%m%d").date()
            results.append((trading_date, filepath))
        except ValueError:
            continue

    return sorted(results, key=lambda x: x[0], reverse=True)


# =============================================================================
# EMA 基线（时间一致性自适应阈值）
# =============================================================================

_EMA_BASELINE_FILE = _CONTEXTS_DIR / "ema_baseline.json"

# EMA 平滑系数 α=0.3（近期权重 30%，历史权重 70%）
EMA_ALPHA = 0.3

# 最小样本数：低于此值时使用宽松阈值（避免冷启动误报）
EMA_WARMUP_SAMPLES = 5


def load_ema_baseline() -> Optional[dict[str, Any]]:
    """加载 EMA 基线（指数移动平均 + 滚动标准差）

    Returns:
        {"regime_score": float, "score_std": float,
         "last_updated": str, "sample_count": int}
        不存在时返回 None
    """
    if not _EMA_BASELINE_FILE.exists():
        logger.info("[persistence] 无 EMA 基线（首次运行）")
        return None

    try:
        with open(_EMA_BASELINE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(
            f"[persistence] 已加载 EMA 基线: score={data.get('regime_score', data.get('weighted_score', 0)):.3f}, "
            f"std={data.get('score_std', 0):.3f}, samples={data.get('sample_count', 0)}"
        )
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[persistence] EMA 基线读取失败: {e}")
        return None


def save_ema_baseline(baseline: dict[str, Any]) -> Path:
    """保存 EMA 基线

    Args:
        baseline: {"regime_score": float, "score_std": float,
                   "last_updated": str, "sample_count": int}

    Returns:
        保存的文件路径
    """
    _ensure_dir()

    _atomic_write_json(_EMA_BASELINE_FILE, baseline)

    logger.info(f"[persistence] 已保存 EMA 基线: score={baseline.get('regime_score', baseline.get('weighted_score', 0)):.3f}")
    return _EMA_BASELINE_FILE


# =============================================================================
# 归档回读工具族（v2.1：从 data/got_daily/ 回读板块逐日资金流 + DDE 序列）
# =============================================================================

_ARCHIVE_DIR = Path("data/got_daily")

# 连续缺失天数阈值：超过此值则该板块序列降级为 None
_MAX_CONSECUTIVE_MISSING = 5

# 排除的目录后缀（备份/修复前缀目录）
_SKIP_DIR_SUFFIXES = ("_backup", "_pre_fix")


def _list_archive_dirs(reverse: bool = True) -> list[Path]:
    """列出归档日期目录，按日期排序。排除备份/修复目录。"""
    if not _ARCHIVE_DIR.exists():
        return []
    return sorted(
        (d for d in _ARCHIVE_DIR.iterdir()
         if d.is_dir() and not d.name.endswith(_SKIP_DIR_SUFFIXES)),
        reverse=reverse,
    )


def _load_capital_json(date_dir: Path) -> Optional[dict]:
    """加载单日 capital.json，失败返回 None。"""
    path = date_dir / "capital.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[persistence] {date_dir.name}/capital.json 读取失败: {e}")
        return None


def load_capital_flow_series(
    days: int = 5,
) -> tuple[dict[str, list[tuple[str, float]]], list[tuple[str, float]]]:
    """从归档回读板块逐日资金流序列 + DDE 序列。

    扫描 data/got_daily/ 最近 days 个有效归档日的 capital.json，
    提取板块级资金流和全市场 DDE。缺失日跳过（不插值）。

    Args:
        days: 回读天数（交易日口径，默认 5）

    Returns:
        (sector_series, dde_series)

        sector_series: {板块名: [(日期, 净流入亿), ...]}
            - 日期升序排列
            - 连续缺失 ≥ _MAX_CONSECUTIVE_MISSING 天的板块从 dict 中移除
            - 仅包含至少有 1 天数据的板块

        dde_series: [(日期, 全市场DDE净流入亿), ...]
            - 日期升序排列
            - 归档不足时可能为空列表

    注意：
        - 返回的日期是 YYYYMMDD 字符串
        - 净流入单位为亿元
        - 失败/状态为 failed 的归档日视为缺失
    """
    date_dirs = _list_archive_dirs(reverse=True)
    if not date_dirs:
        logger.info("[persistence] 无归档目录，返回空序列")
        return {}, []

    # 收集最近 days 个有效日的数据（反向遍历，取最近的 N 天）
    # daily_data: [(date_str, sector_flow_all_dict, dde_net_or_None)]
    daily_data: list[tuple[str, dict, Optional[float]]] = []

    for d in date_dirs:
        if len(daily_data) >= days:
            break
        data = _load_capital_json(d)
        if data is None:
            continue
        if data.get("status") == "failed":
            continue

        evidence = data.get("evidence") or {}
        sector_flow = evidence.get("sector_flow") or {}
        sector_flow_all = sector_flow.get("sector_flow_all") or {}
        dde = evidence.get("dde") or {}
        dde_net = dde.get("total_net_inflow")

        # sector_flow_all 为空也视为有效日（可能当日板块数据缺失但 DDE 有）
        daily_data.append((d.name, sector_flow_all, dde_net))

    if not daily_data:
        logger.info("[persistence] 归档中无有效数据日")
        return {}, []

    # 反转为日期升序
    daily_data.reverse()

    # 构建板块级序列
    sector_series: dict[str, list[tuple[str, float]]] = {}
    all_sector_names: set[str] = set()
    for _, sfa, _ in daily_data:
        all_sector_names.update(sfa.keys())

    for sector_name in all_sector_names:
        series: list[tuple[str, float]] = []
        consecutive_missing = 0
        degraded = False
        for date_str, sfa, _ in daily_data:
            flow = sfa.get(sector_name)
            if flow is not None:
                series.append((date_str, float(flow)))
                consecutive_missing = 0
            else:
                consecutive_missing += 1
                if consecutive_missing >= _MAX_CONSECUTIVE_MISSING:
                    degraded = True
                    break
        if not degraded and series:
            sector_series[sector_name] = series

    if degraded:
        logger.info(
            f"[persistence] 部分板块因连续缺失 ≥{_MAX_CONSECUTIVE_MISSING} 天被降级，"
            f"保留 {len(sector_series)}/{len(all_sector_names)} 个板块"
        )

    # 构建 DDE 序列
    dde_series: list[tuple[str, float]] = [
        (date_str, float(dde_net))
        for date_str, _, dde_net in daily_data
        if dde_net is not None
    ]

    logger.info(
        f"[persistence] 归档回读完成: "
        f"{len(daily_data)} 有效日, "
        f"{len(sector_series)} 板块有数据, "
        f"{len(dde_series)} 天 DDE"
    )
    return sector_series, dde_series


# =============================================================================
# Regime 状态机归档回读（v2.1：从 data/got_daily/ 回读 market_regime 序列）
# =============================================================================


def _load_unified_json(date_dir: Path) -> Optional[dict]:
    """加载单日 unified.json，失败返回 None。"""
    path = date_dir / "unified.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[persistence] {date_dir.name}/unified.json 读取失败: {e}")
        return None


def load_previous_regime_state() -> Optional[dict[str, Any]]:
    """从最近一个有效归档回读 regime 状态机字段。

    Returns:
        {"regime": str, "dwell_days": int, "previous_regime": str|None,
         "transition_count": int, "transition_pending": str|None}
        不存在或缺少 regime 字段时返回 None。

    用于 Node 6 Level 0 状态机的跨日记忆。
    """
    date_dirs = _list_archive_dirs(reverse=True)
    for d in date_dirs:
        data = _load_unified_json(d)
        if data is None:
            continue
        regime_data = data.get("market_regime") or {}
        regime = regime_data.get("regime")
        if regime is None:
            continue
        return {
            "regime": regime,
            "dwell_days": regime_data.get("dwell_days", 1),
            "previous_regime": regime_data.get("previous_regime"),
            "transition_count": regime_data.get("transition_count", 0),
            "transition_pending": regime_data.get("transition_pending"),
        }
    logger.info("[persistence] 无 regime 状态机归档")
    return None


def load_regime_state_history(
    days: int = 20,
) -> list[tuple[str, str]]:
    """从归档回读近 N 个有效日的 regime 序列（用于 transition_count 统计）。

    Args:
        days: 回读天数（默认 20 交易日）

    Returns:
        [(日期YYYYMMDD, regime), ...] 按日期升序排列

    跳过缺失/失败的归档日。仅含 regime 字段的日才纳入。
    """
    date_dirs = _list_archive_dirs(reverse=True)
    entries: list[tuple[str, str]] = []

    for d in date_dirs:
        if len(entries) >= days:
            break
        data = _load_unified_json(d)
        if data is None:
            continue
        regime_data = data.get("market_regime") or {}
        regime = regime_data.get("regime")
        if regime is None:
            continue
        entries.append((d.name, regime))

    entries.reverse()
    if entries:
        logger.info(
            f"[persistence] regime 历史回读: {len(entries)} 日, "
            f"从 {entries[0][0]} 到 {entries[-1][0]}"
        )
    return entries


# =============================================================================
# 情绪阶段状态机归档回读（v2.1：从 data/got_daily/ 回读 stage_state + score 序列）
# =============================================================================


def _load_sentiment_json(date_dir: Path) -> Optional[dict]:
    """加载单日 sentiment.json，失败返回 None。"""
    path = date_dir / "sentiment.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[persistence] {date_dir.name}/sentiment.json 读取失败: {e}")
        return None


def load_previous_stage_state() -> Optional[dict[str, Any]]:
    """从最近一个有效归档回读情绪阶段状态机字段。

    Returns:
        {"current_stage": str, "dwell_days": int, "previous_stage": str|None,
         "momentum_direction": str, "stage_transitions": list,
         "contrarian_alert": bool}
        不存在或缺少 current_stage 字段时返回 None。

    用于 Node 5 阶段状态机的跨日记忆。
    """
    date_dirs = _list_archive_dirs(reverse=True)
    for d in date_dirs:
        data = _load_sentiment_json(d)
        if data is None:
            continue
        # stage_state 是嵌套对象（SentimentStageState）
        stage_data = data.get("stage_state") or {}
        current_stage = stage_data.get("current_stage")
        if current_stage is None:
            continue
        return {
            "current_stage": current_stage,
            "dwell_days": stage_data.get("dwell_days", 1),
            "previous_stage": stage_data.get("previous_stage"),
            "momentum_direction": stage_data.get("momentum_direction", "stable"),
            "stage_transitions": stage_data.get("stage_transitions", []),
            "contrarian_alert": stage_data.get("contrarian_alert", False),
        }
    logger.info("[persistence] 无情绪阶段状态机归档")
    return None


def load_sentiment_score_history(
    days: int = 20,
) -> list[tuple[str, float]]:
    """从归档回读近 N 个有效日的 sentiment_score 序列（用于 AR(1) 持续性计算）。

    Args:
        days: 回读天数（默认 20 交易日）

    Returns:
        [(日期YYYYMMDD, sentiment_score), ...] 按日期升序排列

    跳过缺失/失败的归档日。
    """
    date_dirs = _list_archive_dirs(reverse=True)
    entries: list[tuple[str, float]] = []

    for d in date_dirs:
        if len(entries) >= days:
            break
        data = _load_sentiment_json(d)
        if data is None:
            continue
        score = data.get("sentiment_score")
        if score is None:
            continue
        entries.append((d.name, float(score)))

    entries.reverse()
    if entries:
        logger.info(
            f"[persistence] 情绪分数回读: {len(entries)} 日, "
            f"从 {entries[0][0]} 到 {entries[-1][0]}"
        )
    return entries


# =============================================================================
# 生产监控（Phase 6）— data/got_timeseries/monitoring.json
# =============================================================================


def load_regime_history(days: int = 20) -> list[tuple[str, str]]:
    """从归档回读近 N 个有效日的 regime 序列。

    Returns:
        [(日期YYYYMMDD, regime_str), ...] 按日期升序
    """
    date_dirs = _list_archive_dirs(reverse=True)
    entries: list[tuple[str, str]] = []

    for d in date_dirs:
        if len(entries) >= days:
            break
        # 优先从 unified.json 的 market_regime.regime 取
        unified = _load_unified_json(d)
        if unified:
            mr = unified.get("market_regime", {})
            regime = mr.get("regime")
            if regime:
                entries.append((d.name, regime))
                continue
        # 回退到 market.json
        market = _load_market_json(d)
        if market:
            ms = market.get("market_state", {})
            regime = ms.get("regime")
            if regime:
                entries.append((d.name, regime))

    entries.reverse()
    return entries


def _load_market_json(date_dir: Path) -> Optional[dict]:
    """加载单日 market.json。"""
    fp = date_dir / "market.json"
    if not fp.exists():
        return None
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


_MONITORING_DIR = Path("data/got_timeseries")
_MONITORING_FILE = _MONITORING_DIR / "monitoring.json"


def _ensure_monitoring_dir() -> None:
    _MONITORING_DIR.mkdir(parents=True, exist_ok=True)


def load_monitoring() -> dict:
    """读取生产监控 JSON。不存在时返回空结构。"""
    if not _MONITORING_FILE.exists():
        return _empty_monitoring()
    try:
        with open(_MONITORING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[monitoring] 读取失败，返回空结构: {e}")
        return _empty_monitoring()


def _empty_monitoring() -> dict:
    return {
        "last_successful_run": None,
        "node1": {"regime_dwell_days_history": [], "regime_switch_count_20d": 0, "status": "unknown"},
        "node3": {"flow_5d_non_null_rate_20d": 1.0, "mainline_consistency_avg": 0.0, "status": "unknown"},
        "node5": {"contrarian_alert_count_30d": 0, "persistence_regime_history": [], "status": "unknown"},
        "cross_node": {"signal_count_30d": {}, "status": "unknown"},
        "anomalies": [],
    }


def save_monitoring(
    *,
    regime_dwell: int,
    regime_switch_count_20d: int,
    flow_5d_non_null_rate: float,
    flow_5d_total_boards: int = 0,
    mainline_consistency_avg: float,
    contrarian_alert_today: bool = False,
    persistence_regime: Optional[str],
    cross_node_signal_counts: dict[str, int],
) -> dict:
    """更新生产监控 JSON + 异常检测。

    告警规则：
    - node3.flow_5d_non_null_rate_20d < 0.8 → 🔴 数据源故障（冷启动保护：total<10 跳过）
    - node1.regime_switch_count_20d > 8 → 🟡 状态机过敏感
    - node5.contrarian_alert_count_30d > 10 → 🟡 反向信号过频繁（30 日窗口滚动）
    - cross_node.signal_count_30d 全 0 → 🟡 跨节点共振未触发
    - last_successful_run > 3 交易日 → 🔴 系统中断
    """
    from datetime import timedelta
    _ensure_monitoring_dir()
    prev = load_monitoring()

    today_str = date.today().isoformat()
    today = date.today()

    # 追加历史（保持最近 30 条）
    dwell_history = (prev.get("node1", {}).get("regime_dwell_days_history", []) + [regime_dwell])[-30:]
    persistence_history = (
        prev.get("node5", {}).get("persistence_regime_history", [])
        + ([persistence_regime] if persistence_regime else [])
    )[-30:]

    # 合并 cross_node 信号计数（30 日窗口由日期驱动衰减）
    prev_signal_counts = prev.get("cross_node", {}).get("signal_count_30d", {})
    # 清理超过 30 天的信号日期记录
    prev_signal_dates = prev.get("cross_node", {}).get("signal_dates", [])
    cutoff_30d = (today - timedelta(days=30)).isoformat()
    prev_signal_dates = [d for d in prev_signal_dates if d.get("date", "") >= cutoff_30d]
    # 追加今日信号
    for sig_type, count in cross_node_signal_counts.items():
        for _ in range(count):
            prev_signal_dates.append({"type": sig_type, "date": today_str})
    # 重新计算 30 日计数
    merged_signal_counts: dict[str, int] = {}
    for entry in prev_signal_dates:
        t = entry.get("type", "")
        merged_signal_counts[t] = merged_signal_counts.get(t, 0) + 1

    # 反向信号：30 日窗口滚动（存储日期列表而非纯计数）
    prev_alert_dates = prev.get("node5", {}).get("contrarian_alert_dates", [])
    prev_alert_dates = [d for d in prev_alert_dates if d >= cutoff_30d]
    if contrarian_alert_today:
        prev_alert_dates.append(today_str)
    contrarian_alert_count_30d = len(prev_alert_dates)

    # ── 异常检测 ──
    anomalies: list[dict] = []

    # 冷启动保护：归档不足时 non_null_rate 恒为 0 → 跳过告警
    if flow_5d_total_boards >= 10 and flow_5d_non_null_rate < 0.8:
        anomalies.append({
            "level": "critical",
            "source": "node3",
            "message": f"资金流 5d 数据缺失率过高（非空率 {flow_5d_non_null_rate:.0%} < 80%）",
            "date": today_str,
        })

    if regime_switch_count_20d > 8:
        anomalies.append({
            "level": "warning",
            "source": "node1",
            "message": f"regime 20 日切换 {regime_switch_count_20d} 次 > 8，确认窗口可能过短",
            "date": today_str,
        })

    if contrarian_alert_count_30d > 10:
        anomalies.append({
            "level": "warning",
            "source": "node5",
            "message": f"30 日反向信号 {contrarian_alert_count_30d} 次 > 10，阈值可能过松",
            "date": today_str,
        })

    if all(v == 0 for v in merged_signal_counts.values()) and merged_signal_counts:
        anomalies.append({
            "level": "warning",
            "source": "cross_node",
            "message": "跨节点共振 30 日内从未触发，检查条件是否过严",
            "date": today_str,
        })

    # 构建各节点状态
    def _status_for(anomaly_source: str) -> str:
        return "critical" if any(
            a["source"] == anomaly_source and a["level"] == "critical" for a in anomalies
        ) else "warning" if any(
            a["source"] == anomaly_source for a in anomalies
        ) else "healthy"

    monitoring = {
        "last_successful_run": today_str,
        "node1": {
            "regime_dwell_days_history": dwell_history,
            "regime_switch_count_20d": regime_switch_count_20d,
            "status": _status_for("node1"),
        },
        "node3": {
            "flow_5d_non_null_rate_20d": round(flow_5d_non_null_rate, 3),
            "mainline_consistency_avg": round(mainline_consistency_avg, 2),
            "status": _status_for("node3"),
        },
        "node5": {
            "contrarian_alert_count_30d": contrarian_alert_count_30d,
            "contrarian_alert_dates": prev_alert_dates,
            "persistence_regime_history": persistence_history,
            "status": _status_for("node5"),
        },
        "cross_node": {
            "signal_count_30d": merged_signal_counts,
            "signal_dates": prev_signal_dates,
            "status": _status_for("cross_node"),
        },
        "anomalies": anomalies,
    }

    _atomic_write_json(_MONITORING_FILE, monitoring)
    if anomalies:
        logger.warning(f"[monitoring] {len(anomalies)} 个异常: {[a['message'][:50] for a in anomalies]}")
    else:
        logger.info("[monitoring] 所有指标正常")

    return monitoring
