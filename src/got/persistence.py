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
        {"weighted_score": float, "score_std": float,
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
            f"[persistence] 已加载 EMA 基线: score={data.get('weighted_score', 0):.3f}, "
            f"std={data.get('score_std', 0):.3f}, samples={data.get('sample_count', 0)}"
        )
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[persistence] EMA 基线读取失败: {e}")
        return None


def save_ema_baseline(baseline: dict[str, Any]) -> Path:
    """保存 EMA 基线

    Args:
        baseline: {"weighted_score": float, "score_std": float,
                   "last_updated": str, "sample_count": int}

    Returns:
        保存的文件路径
    """
    _ensure_dir()

    _atomic_write_json(_EMA_BASELINE_FILE, baseline)

    logger.info(f"[persistence] 已保存 EMA 基线: score={baseline.get('weighted_score', 0):.3f}")
    return _EMA_BASELINE_FILE
