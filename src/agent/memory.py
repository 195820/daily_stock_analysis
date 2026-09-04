# -*- coding: utf-8 -*-
"""
AgentMemory — persistent structured memory for agent learning.

Provides:
1. **Analysis memory** — stores past analysis results with outcomes,
   enabling agents to learn from their own track record.
2. **Confidence calibration** — adjusts agent confidence based on
   historical accuracy (only after sufficient sample count).
3. **Skill performance tracking** — per-skill win-rate and
   signal accuracy for auto-weighting.

Storage uses the existing SQLAlchemy database layer
(``AnalysisHistory`` + ``BacktestResult`` tables) rather than
introducing a new store.

.. note::
   Memory features are gated behind ``AGENT_MEMORY_ENABLED=true``.
   When disabled, all methods return neutral/default values.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default minimum samples before calibration kicks in
_MIN_CALIBRATION_SAMPLES = 30
# Rolling window size for recent accuracy calculation
_ROLLING_WINDOW = 50


@dataclass
class CalibrationResult:
    """Confidence calibration data for an agent or skill."""
    agent_name: str = ""
    total_samples: int = 0
    historical_accuracy: float = 0.5  # 0.0–1.0
    direction_accuracy: float = 0.5
    avg_confidence: float = 0.5
    calibrated: bool = False  # True if samples >= threshold
    calibration_factor: float = 1.0  # multiply raw confidence by this


@dataclass
class AnalysisMemoryEntry:
    """A remembered past analysis for context injection."""
    stock_code: str = ""
    date: str = ""
    signal: str = ""
    sentiment_score: int = 50
    price_at_analysis: float = 0.0
    outcome_5d: Optional[float] = None  # % change after 5 days
    outcome_20d: Optional[float] = None  # % change after 20 days
    was_correct: Optional[bool] = None


class AgentMemory:
    """Structured memory system for agent self-improvement.

    Usage::

        memory = AgentMemory()
        # Get past analyses for context
        past = memory.get_stock_history("600519", limit=5)
        # Calibrate confidence
        cal = memory.get_calibration("technical", stock_code="600519")
    """

    def __init__(self, enabled: bool = False, min_samples: int = _MIN_CALIBRATION_SAMPLES):
        self.enabled = enabled
        self.min_samples = min_samples

    @classmethod
    def from_config(cls) -> "AgentMemory":
        """Create an AgentMemory from the current config."""
        try:
            from src.config import get_config
            config = get_config()
            enabled = getattr(config, "agent_memory_enabled", False)
            return cls(enabled=enabled)
        except Exception:
            return cls(enabled=False)

    # -----------------------------------------------------------------
    # Analysis history retrieval
    # -----------------------------------------------------------------

    def get_stock_history(
        self,
        stock_code: str,
        limit: int = 5,
    ) -> List[AnalysisMemoryEntry]:
        """Retrieve recent analysis results for a stock.

        Returns structured entries that can be injected into agent
        context for learning from past predictions.
        """
        if not self.enabled:
            return []

        try:
            from src.storage import get_db
            db = get_db()
            records = db.get_analysis_history(code=stock_code, limit=limit)
            entries = []
            pending = []  # (entry, signal, created_at)
            for r in records:
                raw_result: Dict[str, Any] = {}
                if isinstance(getattr(r, "raw_result", None), str) and r.raw_result:
                    try:
                        parsed = json.loads(r.raw_result)
                        if isinstance(parsed, dict):
                            raw_result = parsed
                    except (TypeError, ValueError):
                        raw_result = {}
                elif isinstance(getattr(r, "raw_result", None), dict):
                    raw_result = dict(r.raw_result)

                signal = raw_result.get("decision_type") or getattr(r, "operation_advice", "") or "hold"
                price_at_analysis = raw_result.get("current_price")
                if price_at_analysis is None:
                    price_at_analysis = 0.0

                # 构建基础 entry
                entry = AnalysisMemoryEntry(
                    stock_code=stock_code,
                    date=(r.created_at.date().isoformat() if getattr(r, "created_at", None) else ""),
                    signal=signal,
                    sentiment_score=getattr(r, "sentiment_score", 50) or 50,
                    price_at_analysis=float(price_at_analysis or 0.0),
                )
                entries.append(entry)
                pending.append((entry, signal, r.created_at))

            # §14.1 A2: 同一标的仅取一次 K 线，逐条复用计算 outcomes（避免逐条网络请求）
            if any(e.price_at_analysis > 0 for e, _, _ in pending):
                klines = self._fetch_outcome_klines(stock_code, [c for _, _, c in pending])
                for entry, signal, created_at in pending:
                    self._compute_entry_outcomes(entry, signal, created_at, klines=klines)
            return entries
        except Exception as exc:
            logger.debug("[AgentMemory] get_stock_history failed: %s", exc)
            return []

    def _fetch_outcome_klines(self, stock_code: str, created_ats) -> List[Dict[str, Any]]:
        """一次性获取覆盖分析日 +20 交易日窗口的日 K 线（§14.1 A2）。

        返回按日期升序的 ``[{"date": ..., "close": float}]``；失败或无有效分析日返回 ``[]``。
        同一标的的多条历史记录共享本次取数结果，避免逐条网络请求。
        """
        from datetime import datetime

        valid_dates = [c.date() for c in created_ats if isinstance(c, datetime)]
        if not valid_dates:
            return []
        try:
            from data_provider.base import DataFetcherManager

            if getattr(self, "_outcome_fetcher", None) is None:
                self._outcome_fetcher = DataFetcherManager()
            earliest = min(valid_dates)
            span_days = (datetime.now().date() - earliest).days + 30  # 覆盖最早分析日的 20 交易日窗口
            days = max(30, min(span_days, 250))
            df, _src = self._outcome_fetcher.get_daily_data(stock_code, days=days)
            if df is None or getattr(df, "empty", True):
                return []
            klines: List[Dict[str, Any]] = []
            for _, row in df.iterrows():
                d = row.get("date")
                c = row.get("close")
                if d is None or c is None:
                    continue
                klines.append({"date": d, "close": float(c)})
            return klines
        except Exception as exc:
            logger.warning("[AgentMemory] outcome K线获取失败 %s: %s", stock_code, exc)
            return []

    def _compute_entry_outcomes(
        self,
        entry: AnalysisMemoryEntry,
        signal: str,
        created_at,
        klines: Optional[List[Dict[str, Any]]] = None,
    ) -> AnalysisMemoryEntry:
        """计算历史分析的结果指标（§14.1 A2 回填链路）。

        - outcome_5d: 分析后 5 日收益率（%）
        - outcome_20d: 分析后 20 日收益率（%）
        - was_correct: 信号方向是否与实际走势一致

        ``klines`` 由 :meth:`get_stock_history` 一次性取数后传入；直接调用且未传时按需获取。
        若无法获取价格数据则保持 None（降级）。
        """
        if entry.price_at_analysis <= 0 or not created_at:
            return entry

        try:
            from datetime import datetime, date as _date

            # 解析分析日期
            if isinstance(created_at, datetime):
                analysis_date = created_at.date()
            else:
                return entry

            if klines is None:
                klines = self._fetch_outcome_klines(entry.stock_code, [created_at])
            if not klines or len(klines) < 2:
                return entry

            # 找到分析日后的价格（klines 按日期升序）
            entry_price = entry.price_at_analysis
            price_5d = None
            price_20d = None

            for bar in klines:
                bar_date = bar.get("date")
                if isinstance(bar_date, str):
                    try:
                        bar_date = datetime.fromisoformat(bar_date[:10]).date()
                    except ValueError:
                        continue
                elif isinstance(bar_date, datetime):
                    bar_date = bar_date.date()
                elif not isinstance(bar_date, _date):
                    continue

                days_diff = (bar_date - analysis_date).days

                # 找 5 日后的价格
                if price_5d is None and 4 <= days_diff <= 7:
                    price_5d = bar.get("close") or bar.get("price")

                # 找 20 日后的价格
                if price_20d is None and 18 <= days_diff <= 22:
                    price_20d = bar.get("close") or bar.get("price")

            # 计算收益率
            if price_5d is not None and entry_price > 0:
                entry.outcome_5d = round((price_5d - entry_price) / entry_price * 100, 2)

            if price_20d is not None and entry_price > 0:
                entry.outcome_20d = round((price_20d - entry_price) / entry_price * 100, 2)

            # 判断方向正确性（使用 5 日收益率）
            if entry.outcome_5d is not None:
                signal_is_bullish = signal in ("buy", "buy_trend", "buy_dip", "long", "strong_buy")
                signal_is_bearish = signal in ("sell", "short", "avoid")
                actual_went_up = entry.outcome_5d > 0

                if signal_is_bullish:
                    entry.was_correct = actual_went_up
                elif signal_is_bearish:
                    entry.was_correct = not actual_went_up
                else:
                    # hold 等中性信号：若波动 < 3% 视为正确
                    entry.was_correct = abs(entry.outcome_5d) < 3.0

        except Exception as exc:
            logger.warning("[AgentMemory] _compute_entry_outcomes failed for %s: %s", entry.stock_code, exc)

        return entry

    # -----------------------------------------------------------------
    # Confidence calibration
    # -----------------------------------------------------------------

    def get_calibration(
        self,
        agent_name: str,
        stock_code: Optional[str] = None,
        skill_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
    ) -> CalibrationResult:
        """Compute confidence calibration for an agent or skill.

        When ``AGENT_MEMORY_ENABLED=false`` or insufficient samples,
        returns a neutral calibration (factor = 1.0).
        """
        result = CalibrationResult(agent_name=agent_name)

        if not self.enabled:
            return result

        try:
            resolved_skill_id = skill_id or strategy_id
            stats = self._get_accuracy_stats(agent_name, stock_code, resolved_skill_id)
            result.total_samples = stats.get("total", 0)
            result.historical_accuracy = stats.get("accuracy", 0.5)
            result.direction_accuracy = stats.get("direction_accuracy", 0.5)
            result.avg_confidence = stats.get("avg_confidence", 0.5)

            if result.total_samples >= self.min_samples:
                result.calibrated = True
                # Calibration: scale confidence towards historical accuracy
                # If agent is overconfident: factor < 1
                # If agent is underconfident: factor > 1
                if result.avg_confidence > 0:
                    result.calibration_factor = min(
                        1.5,
                        max(0.5, result.historical_accuracy / result.avg_confidence),
                    )
                else:
                    result.calibration_factor = 1.0
            else:
                result.calibrated = False
                result.calibration_factor = 1.0

        except Exception as exc:
            logger.debug("[AgentMemory] calibration failed for %s: %s", agent_name, exc)

        return result

    def calibrate_confidence(self, agent_name: str, raw_confidence: float, stock_code: Optional[str] = None) -> float:
        """Apply calibration to a raw confidence value.

        Returns the adjusted confidence, clamped to [0.0, 1.0].
        """
        cal = self.get_calibration(agent_name, stock_code=stock_code)
        if not cal.calibrated:
            return raw_confidence
        adjusted = raw_confidence * cal.calibration_factor
        return max(0.0, min(1.0, adjusted))

    # -----------------------------------------------------------------
    # Skill performance
    # -----------------------------------------------------------------

    def get_skill_performance(self, skill_id: str) -> Dict[str, Any]:
        """Get performance metrics for a skill.

        Used by :class:`SkillAggregator` for weight computation.
        """
        if not self.enabled:
            return {"available": False}

        try:
            from src.services.backtest_service import BacktestService
            service = BacktestService()
            summary = service.get_skill_summary(skill_id)
            if summary:
                return {
                    "available": True,
                    "win_rate": summary.get("win_rate", 0.5),
                    "total_evaluations": summary.get("total_evaluations", 0),
                    "avg_return": summary.get("avg_return", 0.0),
                    "direction_accuracy": summary.get("direction_accuracy", 0.5),
                    "sufficient_samples": summary.get("total_evaluations", 0) >= self.min_samples,
                }
            return {"available": False}
        except Exception:
            return {"available": False}

    def get_strategy_performance(self, strategy_id: str) -> Dict[str, Any]:
        """Compatibility wrapper for legacy strategy-based callers."""
        return self.get_skill_performance(strategy_id)

    # -----------------------------------------------------------------
    # Auto-weighting
    # -----------------------------------------------------------------

    def compute_skill_weights(
        self,
        skill_ids: List[str],
        use_backtest: bool = True,
    ) -> Dict[str, float]:
        """Compute normalized weights for a set of skills.

        Skills with higher historical performance get higher weights.
        Skills with insufficient samples get neutral weight (1.0).

        Returns:
            Dict mapping skill_id → weight (normalized so mean ≈ 1.0)
        """
        if not self.enabled or not use_backtest:
            return {sid: 1.0 for sid in skill_ids}

        raw_weights: Dict[str, float] = {}
        for sid in skill_ids:
            perf = self.get_skill_performance(sid)
            if perf.get("sufficient_samples"):
                # Weight = 0.5 + win_rate (range: 0.5 to 1.5)
                raw_weights[sid] = 0.5 + perf.get("win_rate", 0.5)
            else:
                raw_weights[sid] = 1.0

        # Normalize so mean = 1.0
        if raw_weights:
            mean_w = sum(raw_weights.values()) / len(raw_weights)
            if mean_w > 0:
                return {sid: w / mean_w for sid, w in raw_weights.items()}

        return {sid: 1.0 for sid in skill_ids}

    def compute_strategy_weights(
        self,
        strategy_ids: List[str],
        use_backtest: bool = True,
    ) -> Dict[str, float]:
        """Compatibility wrapper for legacy strategy-based callers."""
        return self.compute_skill_weights(strategy_ids, use_backtest=use_backtest)

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------

    def _get_accuracy_stats(
        self,
        agent_name: str,
        stock_code: Optional[str],
        skill_id: Optional[str],
    ) -> Dict[str, Any]:
        """Aggregate accuracy statistics from backtest history.

        §14.1 A1 修复：avg_confidence 不再硬编码 0.6，改为从历史数据计算真实均值。
        若无法获取则使用中性值 0.5（使 calibration_factor = accuracy / 0.5 = 2*accuracy）。
        """
        try:
            from src.services.backtest_service import BacktestService
            service = BacktestService()

            if skill_id:
                summary = service.get_skill_summary(skill_id)
            elif stock_code:
                summary = service.get_stock_summary(stock_code)
            else:
                # Global summary across all analyses
                summary = service.get_global_summary() if hasattr(service, "get_global_summary") else None

            if summary:
                # §14.1 A1: 尝试获取真实 avg_confidence，否则从历史分析记录计算
                avg_confidence = self._compute_avg_confidence(stock_code)
                return {
                    "total": summary.get("total_evaluations", 0),
                    "accuracy": summary.get("win_rate", 0.5),
                    "direction_accuracy": summary.get("direction_accuracy", 0.5),
                    "avg_confidence": avg_confidence,
                }
        except Exception:
            pass
        return {"total": 0, "accuracy": 0.5, "direction_accuracy": 0.5, "avg_confidence": 0.5}

    def _compute_avg_confidence(self, stock_code: Optional[str] = None, limit: int = 100) -> float:
        """从历史分析记录计算平均置信度（§14.1 A1 修复）。

        查询 AnalysisHistory.raw_result JSON 中的 confidence 字段。
        若无数据则返回中性值 0.5。
        """
        try:
            from src.storage import get_db, AnalysisHistory
            import json

            db = get_db()
            session = db.get_session()
            query = session.query(AnalysisHistory.raw_result).filter(
                AnalysisHistory.raw_result.isnot(None)
            )
            if stock_code:
                query = query.filter(AnalysisHistory.code == stock_code)

            records = query.order_by(AnalysisHistory.created_at.desc()).limit(limit).all()
            session.close()

            confidences = []
            for (raw_result,) in records:
                try:
                    data = json.loads(raw_result)
                    if isinstance(data, dict):
                        conf = data.get("confidence") or data.get("overall_confidence")
                        if conf is not None:
                            confidences.append(float(conf))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue

            if confidences:
                return sum(confidences) / len(confidences)
        except Exception:
            pass

        # 无数据或查询失败，返回中性值
        return 0.5
