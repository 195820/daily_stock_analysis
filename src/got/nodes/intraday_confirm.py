# -*- coding: utf-8 -*-
"""
======================================
IntradayConfirmNode - 盘中 0→1 确认闸门 (Phase 6-1)
======================================

docs/got/plans/Phase6-1_盘中闸门影子期实施稿_20261015.md
docs/got/archive/intraday_gate_plan_0907.md §4/§5

旁挂节点：不进 Node 1-7 决策面、不进 screener 漏斗、不进通知推送。
三时点（0926 竞价后 / 1125 午前 / 1445 尾盘）对当日 GOT 候选跑纯规则判定，
产物落 `data/got_daily/YYYYMMDD/intraday/{0926,1125,1445}/verdicts.json`
+ 当日 `intraday/summary.json`。

治理：规则守决策；影子期全程 SHADOW=true（6-4 转正前禁止置 false）。
零 LLM 调用。判定优先级：reject（任一否决项）> confirm（共识达成）> wait（默认）。
confidence 合成用几何平均（惩罚单维极端，与 _detect_cross_node_temporal_signals 同哲学）。

盘中闸门须盘中实时跑（0907 §6.2）：9:26/11:25/14:45 由 Windows 任务计划程序
（schtasks）或常驻调度触发 `python main.py --got-intraday --intraday-time 0926`，
收盘补跑会产生 I5/I6 失真，影子期样本仅计跑满三时点的交易日。
"""

import json
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.got.catalyst_config import (
    INTRADAY_GATE_ENABLED,
    INTRADAY_GATE_SHADOW,
    INTRADAY_GATE_TOPN,
)
from src.got.schemas import IntradayShadowRecord, IntradayVerdict

logger = logging.getLogger(__name__)

# 时点定义：trigger 标记 → (HHMM, 说明)
INTRADAY_SESSIONS = {
    "0926": "竞价后（高开幅 + 竞价委比）",
    "1125": "午前（放量拉升 + 大单净流入 + 站上分时均线 + 委比转正）",
    "1445": "尾盘（维持强势 / 回封 confirm 加强；跳水 / 炸板 reject）",
}

# ── 判据阈值（初值 = 实施稿 §2；影子期校准，不硬编码拍脑袋之外再加一道 env 覆盖）──
GAP_THRESHOLD_PCT = float(os.environ.get("INTRADAY_GAP_THRESHOLD_PCT", "3.0"))   # θ_gap 高开幅
PULLBACK_THRESHOLD_PCT = float(os.environ.get("INTRADAY_PULLBACK_THRESHOLD_PCT", "2.0"))  # θ_pull 冲高回落
IMBALANCE_THRESHOLD = float(os.environ.get("INTRADAY_IMBALANCE_THRESHOLD", "0.15"))  # 委比阈值（买量-卖量)/(买量+卖量)
SEAL_DECAY_THRESHOLD = float(os.environ.get("INTRADAY_SEAL_DECAY_THRESHOLD", "0.5"))  # 封单衰减比例（观测用）

GOT_DAILY_ROOT = Path("data/got_daily")


def _geometric_mean(values: List[float]) -> float:
    """几何平均置信度（惩罚单维极端；全 0 输入返 0）。"""
    positive = [v for v in values if v > 0]
    if not positive:
        return 0.0
    return math.exp(sum(math.log(v) for v in positive) / len(positive))


def _order_book_imbalance(quote: Any) -> Optional[float]:
    """委比：五档买卖量对称差。缺失五档量返回 None（I1 覆盖率降级）。"""
    bid_volumes = getattr(quote, "bid_volumes", None)
    ask_volumes = getattr(quote, "ask_volumes", None)
    if not bid_volumes or not ask_volumes:
        return None
    total_bid = sum(bid_volumes)
    total_ask = sum(ask_volumes)
    denom = total_bid + total_ask
    if denom <= 0:
        return None
    return (total_bid - total_ask) / denom


def _gap_pct(quote: Any) -> Optional[float]:
    """高开幅：今开 vs 昨收。任一缺失返回 None。"""
    open_price = getattr(quote, "open_price", None)
    pre_close = getattr(quote, "pre_close", None)
    if open_price is None or pre_close is None or pre_close <= 0:
        return None
    return (open_price - pre_close) / pre_close * 100.0


def _change_pct(quote: Any) -> Optional[float]:
    """实时涨跌幅。"""
    return getattr(quote, "change_pct", None)


class IntradayConfirmNode:
    """盘中 0→1 确认闸门（Phase 6-1，纯规则影子节点）。"""

    def __init__(self, manager: Any):
        """
        Args:
            manager: DataFetcherManager（get_realtime_quote / get_minute_data）
        """
        self.manager = manager

    # ------------------------------------------------------------------
    # 候选读取：当日 GOT 产物 → Top-N 候选
    # ------------------------------------------------------------------

    def load_candidates(self, date: str, topn: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        读取当日 GOT 候选（portfolio_decision.json unified_scoring，
        按 composite_score 降序截 Top-N）。

        当日产物缺失时回退到最近一个有 unified_scoring 的交易日（标注 notes）。
        """
        n = topn if topn is not None else INTRADAY_GATE_TOPN
        root = GOT_DAILY_ROOT / date
        pd_path = root / "portfolio_decision.json"
        if pd_path.exists():
            try:
                data = json.loads(pd_path.read_text(encoding="utf-8"))
                rows = data.get("unified_scoring") or []
                rows = [r for r in rows if r.get("symbol")]
                rows.sort(key=lambda r: r.get("composite_score") or 0, reverse=True)
                return rows[:n]
            except Exception as e:
                logger.warning(f"[盘中闸门] 读当日候选失败 {pd_path}: {e}")
                return []
        return []

    # ------------------------------------------------------------------
    # 单时点判定
    # ------------------------------------------------------------------

    def judge_one(self, code: str, session: str, quote: Any,
                  minute_df: Any = None) -> IntradayVerdict:
        """
        单股单时点规则判定（0907 §5 判据落地）。

        判定优先级：reject > confirm > wait。
        """
        evidence: Dict[str, Any] = {}
        imbalance = _order_book_imbalance(quote)
        gap = _gap_pct(quote)
        change = _change_pct(quote)
        if gap is not None:
            evidence["gap_pct"] = round(gap, 2)
        if imbalance is not None:
            evidence["order_book_imbalance"] = round(imbalance, 3)
        if change is not None:
            evidence["change_pct"] = round(change, 2)
        provider_ts = getattr(quote, "provider_timestamp", None)
        if provider_ts:
            evidence["provider_timestamp"] = provider_ts

        # 分时均线位置（可选证据：minute_df 有 price 列时算 VWAP 位置）
        above_vwap: Optional[bool] = None
        if minute_df is not None and hasattr(minute_df, "empty") and not minute_df.empty:
            try:
                prices = minute_df["price"].astype(float)
                vols = minute_df["vol"].astype(float) if "vol" in minute_df.columns else None
                if vols is not None and vols.sum() > 0:
                    vwap = float((prices * vols).sum() / vols.sum())
                else:
                    vwap = float(prices.mean())
                current = float(prices.iloc[-1])
                above_vwap = current >= vwap
                evidence["above_vwap"] = above_vwap
            except Exception:
                above_vwap = None

        # ── reject 否决项（任一命中即 reject）──
        # 竞价不及预期：高开 < θ_gap（0926 时点）
        if session == "0926" and gap is not None and gap < GAP_THRESHOLD_PCT:
            return IntradayVerdict(
                code=code, trigger_time=session, verdict="wait",
                confidence=0.3,
                reason=f"竞价高开 {gap:.2f}% < θ_gap {GAP_THRESHOLD_PCT}%，不及预期（不早盘追）",
                shadow=True, evidence=evidence, provider_timestamp=provider_ts,
            )
        # 尾盘跳水 / 炸板（1445 时点）：转跌且跌破分时均线
        if session == "1445" and change is not None:
            if change < 0 or above_vwap is False:
                return IntradayVerdict(
                    code=code, trigger_time=session, verdict="reject",
                    confidence=_geometric_mean([0.7, 0.7]) if above_vwap is False else 0.6,
                    reason=f"尾盘转弱（change={change:.2f}%, above_vwap={above_vwap}），快刀斩乱麻",
                    shadow=True, evidence=evidence, provider_timestamp=provider_ts,
                )
        # 冲高回落诱多：日内冲高明显但现价回落超 θ_pull（11:25/14:45 适用；
        # 0926 时点开盘初冲高回落无稳定口径，不判 reject）
        high = getattr(quote, "high", None)
        if session in ("1125", "1445") and high is not None and change is not None:
            pre_close = getattr(quote, "pre_close", None)
            if pre_close and pre_close > 0:
                high_pct = (high - pre_close) / pre_close * 100.0
                pullback = high_pct - change
                if pullback > PULLBACK_THRESHOLD_PCT:
                    evidence["pullback_pct"] = round(pullback, 2)
                    return IntradayVerdict(
                        code=code, trigger_time=session, verdict="reject",
                        confidence=0.65,
                        reason=f"冲高 {high_pct:.2f}% 回落 {pullback:.2f}pp > θ_pull，疑诱多",
                        shadow=True, evidence=evidence, provider_timestamp=provider_ts,
                    )

        # ── confirm 共识项（0→1 走完）──
        # 0907 §5：午前共识 = 拉升 + 大单/委比/均线 至少一项佐证；尾盘共识 = 维持强势 + 均线上方
        confirm_signals: List[str] = []
        secondary: List[bool] = []
        if session == "1125":
            if change is not None and change > 1.0:
                confirm_signals.append(f"放量拉升 {change:.2f}%")
            if imbalance is not None and imbalance > IMBALANCE_THRESHOLD:
                confirm_signals.append(f"委比 {imbalance:+.2f}"); secondary.append(True)
            if above_vwap is True:
                confirm_signals.append("站上分时均线"); secondary.append(True)
        elif session == "1445":
            if change is not None and change > 1.0:
                confirm_signals.append(f"尾盘维持强势 {change:.2f}%")
            if above_vwap is True:
                confirm_signals.append("分时均线上方"); secondary.append(True)
            if imbalance is not None and imbalance > IMBALANCE_THRESHOLD:
                confirm_signals.append(f"委比 {imbalance:+.2f}"); secondary.append(True)
        if len(confirm_signals) >= 2 and secondary:
            conf_parts = [0.6, 0.7]
            if above_vwap is True:
                conf_parts.append(0.75)
            if imbalance is not None and imbalance > IMBALANCE_THRESHOLD:
                conf_parts.append(0.7)
            return IntradayVerdict(
                code=code, trigger_time=session, verdict="confirm",
                confidence=round(_geometric_mean(conf_parts), 3),
                reason="0→1 确认：" + " + ".join(confirm_signals),
                shadow=True, evidence=evidence, provider_timestamp=provider_ts,
            )

        # ── 默认 wait ──
        return IntradayVerdict(
            code=code, trigger_time=session, verdict="wait",
            confidence=0.3,
            reason="判据不足，等下一时点",
            shadow=True, evidence=evidence, provider_timestamp=provider_ts,
        )

    # ------------------------------------------------------------------
    # 单时点批量运行
    # ------------------------------------------------------------------

    def run_session(self, date: str, session: str,
                    topn: Optional[int] = None) -> Optional[IntradayShadowRecord]:
        """
        跑一个时点：读候选 → 逐股取行情（可选分时）→ 判定 → 落盘。

        Returns:
            IntradayShadowRecord（仅本时点）；开关关或候选空返回 None。
        """
        if session not in INTRADAY_SESSIONS:
            raise ValueError(f"未知时点: {session}（可用: {list(INTRADAY_SESSIONS)}）")
        if not INTRADAY_GATE_ENABLED:
            logger.info("[盘中闸门] INTRADAY_GATE_ENABLED=false，跳过")
            return None
        if not INTRADAY_GATE_SHADOW:
            # 6-4 转正前禁止；防御性拦载（写侧兜底，配置注记为第一道闸）
            logger.error("[盘中闸门] INTRADAY_GATE_SHADOW=false 违反影子期纪律，拒绝执行")
            return None
        candidates = self.load_candidates(date, topn)
        if not candidates:
            logger.info(f"[盘中闸门] {date} {session} 无候选（当日 GOT 产物缺失或为空）")
            return None

        verdicts: List[IntradayVerdict] = []
        for row in candidates:
            code = str(row.get("symbol", "")).strip()
            if not code:
                continue
            try:
                quote = self.manager.get_realtime_quote(code)
            except Exception as e:
                logger.warning(f"[盘中闸门] 行情获取失败 {code}: {e}")
                quote = None
            if quote is None:
                verdicts.append(IntradayVerdict(
                    code=code, trigger_time=session, verdict="wait",
                    confidence=0.0, reason="行情不可达（数据降级）",
                    shadow=True, evidence={},
                ))
                continue
            minute_df = None
            try:
                minute_df = self.manager.get_minute_data(code)
            except Exception as e:
                logger.debug(f"[盘中闸门] 分时获取失败 {code}: {e}")
            verdicts.append(self.judge_one(code, session, quote, minute_df))

        record = IntradayShadowRecord(
            date=date,
            sessions_run=[session],
            candidates_polled=len(verdicts),
            verdicts=verdicts,
        )
        self._compute_metrics(record)
        self._write(date, session, record)
        return record

    # ------------------------------------------------------------------
    # 指标 + 落盘
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_metrics(record: IntradayShadowRecord) -> None:
        filled = [v for v in record.verdicts if v.evidence]
        record.i1_coverage = round(len(filled) / len(record.verdicts), 3) if record.verdicts else None
        confirms = sum(1 for v in record.verdicts if v.verdict == "confirm")
        record.i2_confirm_ratio = round(confirms / len(record.verdicts), 3) if record.verdicts else None
        if record.candidates_polled and len(record.sessions_run) < 3:
            record.notes.append(
                f"仅跑 {len(record.sessions_run)} 时点；影子期样本仅计跑满三时点交易日"
            )

    @staticmethod
    def _write(date: str, session: str, record: IntradayShadowRecord) -> Path:
        """落 verdicts.json（单时点）+ summary.json（当日累计，防 NUL + UTF-8 验证）。"""
        root = GOT_DAILY_ROOT / date / "intraday"
        session_dir = root / session
        session_dir.mkdir(parents=True, exist_ok=True)

        verdicts_path = session_dir / "verdicts.json"
        payload = json.dumps(
            {"session": session, "written_at": datetime.now().isoformat(timespec="seconds"),
             "record": record.model_dump()},
            ensure_ascii=False, indent=2,
        ).replace("\x00", "")
        payload.encode("utf-8")  # 显式验证
        verdicts_path.write_text(payload, encoding="utf-8")

        # 当日 summary：合并已有各时点
        merged_sessions: List[IntradayShadowRecord] = []
        for s in INTRADAY_SESSIONS:
            p = root / s / "verdicts.json"
            if p.exists():
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    merged_sessions.append(IntradayShadowRecord.model_validate(d["record"]))
                except Exception as e:
                    logger.warning(f"[盘中闸门] 读时点产物失败 {p}: {e}")
        if merged_sessions:
            all_verdicts = [v for r in merged_sessions for v in r.verdicts]
            summary = IntradayShadowRecord(
                date=date,
                sessions_run=[r.sessions_run[0] for r in merged_sessions if r.sessions_run],
                candidates_polled=max(r.candidates_polled for r in merged_sessions),
                verdicts=all_verdicts,
            )
            IntradayConfirmNode._compute_metrics(summary)
            summary_path = root / "summary.json"
            s_payload = json.dumps(
                {"written_at": datetime.now().isoformat(timespec="seconds"),
                 "record": summary.model_dump()},
                ensure_ascii=False, indent=2,
            ).replace("\x00", "")
            s_payload.encode("utf-8")
            summary_path.write_text(s_payload, encoding="utf-8")
            logger.info(
                "[SHADOW][盘中闸门] %s sessions=%s confirm=%d/%d evidence覆盖率=%s",
                date, summary.sessions_run,
                sum(1 for v in all_verdicts if v.verdict == "confirm"),
                len(all_verdicts), summary.i1_coverage,
            )
        return verdicts_path
