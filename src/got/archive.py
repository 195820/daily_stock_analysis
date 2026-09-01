# -*- coding: utf-8 -*-
"""
GOT 每日历史档存档

将每次 GOT Phase 1 运行的 6 个 Context + LLM reasoning 文本 + 运行元数据
存档到 data/got_daily/YYYYMMDD/ 目录。

设计目标：
- 支撑阈值回测、prompt 调优、信号复盘
- 永久保留（磁盘占用小，单档 ~50KB）
- JSON 人读友好（indent=2, ensure_ascii=False）
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from src.got.schemas import (
    CapitalContext,
    MarketContext,
    NewsContext,
    SectorContext,
    SentimentContext,
    UnifiedContext,
)

logger = logging.getLogger(__name__)

# 节点键 → 存档文件名
CONTEXT_FILES = {
    "market": "market.json",
    "sector": "sector.json",
    "capital": "capital.json",
    "news": "news.json",
    "sentiment": "sentiment.json",
    "unified": "unified.json",
}

# reasoning.md 的节点标题
REASONING_NODE_TITLES = {
    "market": "Node 1 大盘分析",
    "sector": "Node 2 板块分析",
    "capital": "Node 3 资金面",
    "news": "Node 4 消息面",
    "sentiment": "Node 5 情绪面",
    "unified": "Node 6 聚合研判",
}

# 节点遍历顺序
NODE_ORDER = ("market", "sector", "capital", "news", "sentiment", "unified")


def archive_daily_run(
    *,
    market_ctx: MarketContext,
    sector_ctx: SectorContext,
    capital_ctx: CapitalContext,
    news_ctx: NewsContext,
    sentiment_ctx: SentimentContext,
    unified_ctx: UnifiedContext,
    run_date: Optional[date] = None,
    base_dir: Optional[Path] = None,
    use_mock: bool = False,
    start_time: Optional[datetime] = None,
) -> Path:
    """将一次 GOT 运行结果存档为当日历史档。

    Args:
        market_ctx: Node 1 输出
        sector_ctx: Node 2 输出
        capital_ctx: Node 3 输出
        news_ctx: Node 4 输出
        sentiment_ctx: Node 5 输出
        unified_ctx: Node 6 输出
        run_date: 运行日期，默认今天
        base_dir: 存档根目录，默认 data/got_daily
        use_mock: 是否 mock 运行（记录到 meta.json）
        start_time: 运行开始时间（用于计算耗时）

    Returns:
        存档目录路径
    """
    run_date = run_date or date.today()
    base_dir = base_dir or _default_base_dir()
    archive_dir = base_dir / run_date.strftime("%Y%m%d")

    # 当天重跑：清理旧档，确保数据全部覆盖（不残留旧版 JSON/reasoning）
    if archive_dir.exists():
        import shutil
        for item in archive_dir.iterdir():
            if item.is_file():
                item.unlink()
        logger.info("[GOT Archive] 清理旧档: %s", archive_dir)

    archive_dir.mkdir(parents=True, exist_ok=True)

    contexts = {
        "market": market_ctx,
        "sector": sector_ctx,
        "capital": capital_ctx,
        "news": news_ctx,
        "sentiment": sentiment_ctx,
        "unified": unified_ctx,
    }

    # 1. 写入 6 个 Context JSON（全量 model_dump）
    # 使用 mode="python" 避免 Pydantic v2 对 numpy 类型的序列化错误，再由 _sanitize_for_json 清洗
    for key, ctx in contexts.items():
        _write_json(
            archive_dir / CONTEXT_FILES[key],
            ctx.model_dump(mode="python"),
        )

    # 2. 写入 reasoning.md（汇总 LLM 推理文本）
    _write_reasoning_md(archive_dir / "reasoning.md", contexts, run_date)

    # 3. 写入 meta.json（运行元数据）
    meta = _build_meta(
        run_date=run_date,
        use_mock=use_mock,
        start_time=start_time,
        contexts=contexts,
    )
    _write_json(archive_dir / "meta.json", meta)

    logger.info("[GOT Archive] 历史档已保存: %s", archive_dir)
    return archive_dir


def _default_base_dir() -> Path:
    """默认存档根目录：项目根目录下的 data/got_daily。"""
    # 从本文件位置推算：<project>/src/got/archive.py → <project>/data/got_daily
    project_root = Path(__file__).resolve().parent.parent.parent
    return project_root / "data" / "got_daily"


def _write_json(path: Path, data: dict) -> None:
    """写 JSON 文件，人读友好格式。处理 numpy 类型转换。"""
    cleaned = _sanitize_for_json(data)
    content = json.dumps(cleaned, ensure_ascii=False, indent=2, default=str)
    path.write_text(content, encoding="utf-8")


def _sanitize_for_json(obj):
    """递归清洗数据中的 numpy 类型，转为 Python 原生类型。"""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    # 处理 numpy 布尔类型（np.bool_ 和 numpy.bool）
    if isinstance(obj, (bool, np.bool_)) or type(obj).__name__ in ('bool_', 'bool'):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.str_,)):
        return str(obj)
    return obj


def _write_reasoning_md(
    path: Path,
    contexts: dict,
    run_date: date,
) -> None:
    """写 reasoning.md，汇总所有节点的 LLM 推理文本。"""
    lines = [f"# GOT 运行推理汇总 - {run_date}", ""]

    # v3: 全局环境 + 板块情报摘要（放在最前面）
    unified_ctx = contexts.get("unified")
    if unified_ctx:
        regime = getattr(unified_ctx, "market_regime", None)
        if regime:
            lines.append("## 🌍 全局环境（Level 0）")
            lines.append("")
            lines.append(f"- **Regime:** {regime.regime}")
            # v2.1: Regime 状态机信息
            dwell_days = getattr(regime, "dwell_days", 1)
            transition_count = getattr(regime, "transition_count", 0)
            transition_pending = getattr(regime, "transition_pending", None)
            lines.append(f"- **持续天数:** {dwell_days}")
            lines.append(f"- **近20日切换次数:** {transition_count}")
            if transition_pending:
                lines.append(f"- **待确认信号:** {transition_pending}（首次出现，待次日确认）")
            if dwell_days < 2:
                lines.append(f"- ⚠️ **低置信度:** regime 刚切换（dwell={dwell_days}），矛盾检测标记低置信")
            lines.append(f"- **风险预算:** {regime.risk_budget:.0%}")
            lines.append(f"- **恐贪指数:** {regime.fear_greed:.2f}（{regime.sentiment_stage}）")
            lines.append(f"- **板块方向:** {regime.market_direction}（{regime.mainline_count} 条主线）")
            lines.append(f"- **宏观摘要:** {regime.macro_overlay}")
            lines.append("")

        confirmed = getattr(unified_ctx, "confirmed_sectors", [])
        actionable_list = [s for s in confirmed if s.classification == "actionable"]
        caution_list = sorted(
            [s for s in confirmed if s.classification == "caution"],
            key=lambda x: x.composite_score, reverse=True,
        )[:5]
        if actionable_list or caution_list:
            lines.append(f"## 📊 板块情报（Level 1）— 确认 {len(actionable_list)} 个 + 观察 {len([s for s in confirmed if s.classification == 'caution'])} 个")
            lines.append("")
            lines.append("| 状态 | 角色 | 板块 | 评分 | 资金 | 催化 | 热度 | 置信度 |")
            lines.append("|------|------|------|------|------|------|------|--------|")
            for s in actionable_list[:10]:
                capital_emoji = {"aligned": "✅", "neutral": "⚪", "divergent": "❌"}.get(s.capital_check, "⚪")
                news_emoji = {"catalyzed": "✅", "neutral": "⚪", "negative": "❌"}.get(s.news_check, "⚪")
                sentiment_emoji = {"high": "🔥", "medium": "⚪", "low": "❄️"}.get(s.sentiment_check, "⚪")
                role = getattr(s, "sector_role", "mainline")
                if role == "mainline":
                    tier = getattr(s, "mainline_tier", None) or "?"
                    role_tag = f"🎯主线-{tier}"
                elif role == "ambush":
                    role_tag = "🔍埋伏"
                elif role == "cooling":
                    role_tag = "🔻高位降温"
                else:
                    role_tag = "📌独立"
                lines.append(
                    f"| ✅确认 | {role_tag} | {s.sector_name} | {s.composite_score:.0f} "
                    f"| {capital_emoji}{s.capital_check} "
                    f"| {news_emoji}{s.news_check} "
                    f"| {sentiment_emoji}{s.sentiment_check} "
                    f"| {s.confidence:.0%} |"
                )
            for s in caution_list:
                capital_emoji = {"aligned": "✅", "neutral": "⚪", "divergent": "❌"}.get(s.capital_check, "⚪")
                news_emoji = {"catalyzed": "✅", "neutral": "⚪", "negative": "❌"}.get(s.news_check, "⚪")
                sentiment_emoji = {"high": "🔥", "medium": "⚪", "low": "❄️"}.get(s.sentiment_check, "⚪")
                status_icon = "👀观察"
                role = getattr(s, "sector_role", "mainline")
                role_tag = (
                    "主线" if role == "mainline"
                    else "埋伏" if role == "ambush"
                    else "降温" if role == "cooling"
                    else "独立"
                )
                lines.append(
                    f"| {status_icon} | {role_tag} | {s.sector_name} | {s.composite_score:.0f} "
                    f"| {capital_emoji}{s.capital_check} "
                    f"| {news_emoji}{s.news_check} "
                    f"| {sentiment_emoji}{s.sentiment_check} "
                    f"| {s.confidence:.0%} |"
                )
            lines.append("")

    # Node 2 v2: 过热检测明细（v2: 9 维度）
    sector_ctx = contexts.get("sector")
    if sector_ctx:
        overheat_sectors = []
        # 从 active_mainline 收集过热板块
        for group in getattr(sector_ctx, "active_mainline", []):
            for m in getattr(group, "members", []):
                sev = getattr(m, "overheat_severity", 0) or 0
                if sev > 0.3:
                    overheat_sectors.append((m.name, sev, getattr(m, "mainline_days", 0)))
        # 从 ambush 收集
        for m in getattr(sector_ctx, "ambush", []):
            sev = getattr(m, "overheat_severity", 0) or 0
            if sev > 0.3:
                overheat_sectors.append((m.name, sev, 0))

        if overheat_sectors:
            overheat_sectors.sort(key=lambda x: -x[1])
            lines.append("### ⚠️ 过热警示板块")
            lines.append("")
            for name, sev, days in overheat_sectors[:8]:
                level = "🔴 exhausted" if sev > 0.5 else "🟡 warning"
                lines.append(f"- {level} **{name}** severity={sev:.2f} (主线D{days})")
            lines.append("")

    # Node 2 v2: 数据质量摘要
    if sector_ctx:
        dq = getattr(sector_ctx, "data_quality", None)
        degradation = getattr(dq, "degradation_summary", None) if dq else None
        confidence = getattr(sector_ctx, "confidence_level", "high")
        if degradation and "正常" not in degradation:
            lines.append(f"> ⚠️ **数据质量**: {degradation}")
            lines.append(f"> 评分可信度: **{confidence}**")
            lines.append("")

        # Node 2 v3: RRG 四象限分布摘要
        _all_sectors: list = []
        for group in getattr(sector_ctx, "active_mainline", []):
            _all_sectors.extend(getattr(group, "members", []))
        for lst_name in ("exhausted_mainline", "ambush", "defensive_rotation", "cooling_down"):
            _all_sectors.extend(getattr(sector_ctx, lst_name, []))
        _rrg_counts: dict[str, int] = {"leading": 0, "improving": 0, "weakening": 0, "lagging": 0}
        for s in _all_sectors:
            rrg = getattr(s, "rrg", None)
            if rrg and rrg.quadrant in _rrg_counts:
                _rrg_counts[rrg.quadrant] += 1
        if any(_rrg_counts.values()):
            lines.append(
                f"> 📐 **RRG 象限分布**: "
                f"Leading {_rrg_counts['leading']} · "
                f"Improving {_rrg_counts['improving']} · "
                f"Weakening {_rrg_counts['weakening']} · "
                f"Lagging {_rrg_counts['lagging']}"
            )
            lines.append("")

    # ── 催化剂日历摘要（P1） ──
    news_ctx = contexts.get("news")
    cal = getattr(news_ctx, "catalyst_calendar", None) if news_ctx else None
    if cal and cal.events:
        from datetime import date as _date_cls
        today = _date_cls.today()
        high_conf = [e for e in cal.events if e.confidence >= 0.7]
        three_star = [e for e in cal.events if e.impact_level == 3]

        lines.append(f"## 📅 催化剂日历（{len(cal.events)} 事件，{len(three_star)} 个 ★★★）— {cal.refresh_mode}")
        lines.append("")

        # ── 今日关键事件（拆分：今日发生 vs 持续传导，避免历史事件被误读为今日发生） ──
        today_events = [e for e in high_conf if e.event_date == today]
        if today_events:
            arrow_map = {"positive": "📈", "negative": "📉", "binary": "⚖️", "drift": "🌀"}

            def _fmt_event(e) -> str:
                stars = "★" * e.impact_level
                arrow = arrow_map.get(e.direction, "•")
                tag = f" `{e.category_tag}`" if e.category_tag else ""
                return f"- {arrow} {stars} **{e.sector}**: {e.title}{tag} (置信{e.confidence:.0%})"

            fresh = [e for e in today_events if e.source != "global_map" and e.category_tag != "drift"]
            carry = [e for e in today_events if e.source == "global_map" or e.category_tag == "drift"]

            if fresh:
                lines.append(f"**今日（{today}）{len(fresh)} 个事件:**")
                for e in sorted(fresh, key=lambda x: -x.impact_level)[:6]:
                    lines.append(_fmt_event(e))
                lines.append("")

            if carry:
                lines.append(f"**持续传导（{len(carry)} 个，历史事件延续至生效，非今日发生）:**")
                for e in sorted(carry, key=lambda x: -x.impact_level)[:6]:
                    lines.append(_fmt_event(e) + " ←历史事件传导")
                lines.append("")

        # ── 未来 2 周按日期展示（每日 Top 事件）──
        future_events = [e for e in high_conf if e.event_date > today]
        if future_events:
            # 按日期分组，每日取 impact_level 最高的 2 个
            from collections import defaultdict
            by_date = defaultdict(list)
            for e in future_events:
                by_date[e.event_date].append(e)

            lines.append(f"**未来 {len(future_events)} 个事件（按日期）:**")
            for d in sorted(by_date.keys())[:7]:  # 最多展示 7 天
                day_events = sorted(by_date[d], key=lambda x: (-x.impact_level, -x.confidence))
                top = day_events[0]
                stars = "★" * top.impact_level
                arrow = {"positive": "📈", "negative": "📉", "binary": "⚖️", "drift": "🌀"}.get(top.direction, "•")
                extra = f" +{len(day_events)-1}个" if len(day_events) > 1 else ""
                lines.append(f"- {d} {arrow} {stars} **{top.sector}**: {top.title}{extra}")
            lines.append("")

        # ── 按板块聚合：哪些板块有 upcoming catalyst ──
        if future_events:
            sector_events = defaultdict(list)
            for e in future_events:
                sector_events[e.sector].append(e)
            # 按事件数排序，展示有最多催化剂的板块
            top_sectors = sorted(sector_events.items(), key=lambda x: -len(x[1]))[:8]
            if top_sectors:
                lines.append("**板块催化密度（未来 2 周）:**")
                for sector, evts in top_sectors:
                    dates = sorted(set(e.event_date for e in evts))
                    max_impact = max(e.impact_level for e in evts)
                    stars = "★" * max_impact
                    tag = f" `{evts[0].category_tag}`" if evts[0].category_tag else ""
                    lines.append(
                        f"- {stars} **{sector}**{tag}: {len(evts)} 个事件"
                        f" ({dates[0]}~{dates[-1]})"
                    )
                lines.append("")

    # ── 埋伏通道摘要（P3） ──
    unified_ctx = contexts.get("unified")
    ambush_pool = getattr(unified_ctx, "ambush_pool", []) if unified_ctx else []
    ambush_reservoir = getattr(unified_ctx, "ambush_reservoir", []) if unified_ctx else []
    ambush_veto = getattr(unified_ctx, "ambush_veto_log", []) if unified_ctx else []
    if ambush_pool or ambush_reservoir or ambush_veto:
        # 吸筹观察 ≠ 市场否决（时机未到的留痕，决策 19），计数与图标区分
        veto_n = sum(1 for v in ambush_veto if not v.split(": ", 1)[-1].startswith("吸筹观察"))
        observe_n = len(ambush_veto) - veto_n
        lines.append(f"## 🔍 埋伏通道（正式池 {len(ambush_pool)} / 蓄水池 {len(ambush_reservoir)} / 否决 {veto_n} / 观察 {observe_n}）")
        lines.append("")
        if ambush_pool:
            lines.append("**正式池（催化剂排序）：**")
            for c in ambush_pool:
                stars = "★" * c.top_event_level
                date_str = str(c.top_event_date) if c.top_event_date else "—"
                tag_emoji = {"drift": "📈", "fade": "🔔", "binary": "⚖️"}.get(c.category_tag, "•")
                # Agent 催化剂先行提名条目（决策 21）：行首 🤖 标记，便于归档校准统计兑现率
                nominated_mark = "🤖 " if getattr(c, "nominated", False) else ""
                lines.append(
                    f"- {nominated_mark}{tag_emoji} **{c.sector_name}** ({stars}, "
                    f"score={c.catalyst_score:.2f}, {c.category_tag})"
                    f" — 最近事件 {date_str}: {', '.join(c.events[:2])}"
                )
            lines.append("")
        if ambush_reservoir:
            lines.append("**蓄水池（观察中）：**")
            for r in ambush_reservoir:
                reason_label = r.reason
                if reason_label.startswith("near_miss:"):
                    failed = reason_label.split(":", 1)[1]
                    reason_label = f"近失({failed})"
                lines.append(f"- {r.sector_name} — {reason_label}")
            lines.append("")
        if ambush_veto:
            lines.append("**否决记录：**")
            for v in ambush_veto[:8]:
                # ⏳ 吸筹观察（资金未到的留痕）与 ❌ 真否决区分显示（决策 19）
                mark = "⏳" if v.split(": ", 1)[-1].startswith("吸筹观察") else "❌"
                lines.append(f"- {mark} {v}")
            lines.append("")

    # ── 情绪阶段状态机摘要（v2.1）──
    sentiment_ctx = contexts.get("sentiment")
    if sentiment_ctx:
        stage_state = getattr(sentiment_ctx, "stage_state", None)
        persistence = getattr(sentiment_ctx, "persistence_regime", None)
        if stage_state or persistence:
            lines.append("## 🧠 情绪阶段状态机")
            lines.append("")
            if stage_state:
                direction_emoji = {"warming": "🔥", "cooling": "❄️", "stable": "⚪"}.get(
                    stage_state.momentum_direction, "⚪"
                )
                lines.append(f"- **当前阶段:** {stage_state.current_stage}")
                lines.append(f"- **持续天数:** {stage_state.dwell_days}")
                lines.append(f"- **动量方向:** {direction_emoji} {stage_state.momentum_direction}")
                if stage_state.contrarian_alert:
                    lines.append("- ⚠️ **反向信号:** 触发（连续升温到极端 / 极端阶段超时）")
                # 近 5 日阶段序列
                recent = stage_state.stage_transitions[-5:]
                if recent:
                    lines.append(f"- **近5日阶段:** {' → '.join(recent)}")
            if persistence:
                persistence_label = {
                    "high": "高持续（trending，异象显著）",
                    "low": "低持续（mean-reverting，反转策略有效）",
                    "medium": "中等持续",
                }.get(persistence, persistence)
                lines.append(f"- **情绪持续性:** {persistence_label}")
            lines.append("")

    # ── 跨节点时间线共振（Phase 6）──
    if unified_ctx:
        cross_signals = getattr(unified_ctx, "cross_node_signals", [])
        if cross_signals:
            lines.append("## 🔗 跨节点时间线共振")
            lines.append("")
            action_emoji = {
                "strong_buy": "🟢",
                "sell": "🔴",
                "add": "🔵",
                "short_term_reversal": "🟡",
            }
            for sig in cross_signals:
                emoji = action_emoji.get(sig.suggested_action, "⚪")
                lines.append(
                    f"- {emoji} **{sig.signal_type}** (置信{sig.confidence:.0%}, "
                    f"建议: {sig.suggested_action})"
                )
                lines.append(f"  - 参与节点: {', '.join(sig.component_nodes)}")
                lines.append(f"  - {sig.reasoning}")
            lines.append("")

    for key in NODE_ORDER:
        ctx = contexts[key]
        title = REASONING_NODE_TITLES[key]
        lines.append(f"## {title}")
        lines.append("")
        reasoning = getattr(ctx, "reasoning", None)
        if reasoning:
            lines.append(reasoning.strip())
        else:
            lines.append("*（本次运行未生成 reasoning 文本）*")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_meta(
    *,
    run_date: date,
    use_mock: bool,
    start_time: Optional[datetime],
    contexts: dict,
) -> dict:
    """构建 meta.json 内容。"""
    meta: dict = {
        "run_date": run_date.isoformat(),
        "run_timestamp": datetime.now().isoformat(timespec="seconds"),
        "use_mock": use_mock,
        "git_commit": _get_git_commit(),
    }

    if start_time:
        meta["duration_seconds"] = round(
            (datetime.now() - start_time).total_seconds(), 1
        )

    # 数据质量摘要
    data_quality_summary = {}
    for key, ctx in contexts.items():
        dq = getattr(ctx, "data_quality", None)
        if dq is None:
            continue
        if getattr(dq, "fetch_failed_fields", None):
            data_quality_summary[key] = "degraded"
        elif getattr(dq, "missing_fields", None):
            data_quality_summary[key] = "partial"
        else:
            data_quality_summary[key] = "normal"
    if data_quality_summary:
        meta["data_quality_summary"] = data_quality_summary

    # CapitalContext 特有：缺失维度
    capital_ctx = contexts.get("capital")
    missing_dims = getattr(capital_ctx, "missing_dimensions", None)
    if missing_dims:
        meta["missing_dimensions"] = {"capital": list(missing_dims)}

    # LLM 模型（从常见环境变量推断）
    llm_model = (
        os.getenv("LITELLM_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or os.getenv("OPENAI_MODEL")
    )
    if llm_model:
        meta["llm_model"] = llm_model

    return meta


def _get_git_commit() -> str:
    """获取当前 git commit short hash（失败时返回 'unknown'）。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


# ── Phase 2: 统一决策节点存档 ──


def archive_phase2(
    decision: "PortfolioDecision",
    run_date: date,
) -> None:
    """
    存档 Phase 2 决策到 data/got_daily/YYYYMMDD/portfolio_decision.json
    并追加 Phase 2 reasoning 到 reasoning.md

    Args:
        decision: Phase 2 统一决策节点输出
        run_date: 运行日期
    """
    from src.got.schemas import PortfolioDecision

    base_dir = _default_base_dir()
    date_str = run_date.strftime("%Y%m%d")
    output_dir = base_dir / date_str
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 写入 portfolio_decision.json（全量 model_dump）
    output_path = output_dir / "portfolio_decision.json"
    output_path.write_text(
        json.dumps(
            decision.model_dump(mode="json"), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    # 2. 生成独立的交易建议 MD 文档
    trading_md_path = output_dir / "trading_recommendations.md"
    with open(trading_md_path, "w", encoding="utf-8") as f:
        f.write(f"# Phase 2 交易建议 — {run_date}\n\n")
        f.write(f"## 风险评估\n\n")
        f.write(f"- **仓位上限:** {decision.risk_assessment.position_limit:.0%}\n")
        f.write(f"- **当前仓位:** {decision.risk_assessment.current_position_ratio:.0%}\n")
        f.write(f"- **止损线:** {decision.risk_assessment.stop_loss_threshold:.0%}\n")
        f.write(f"- **约束理由:** {decision.risk_assessment.constraint_reasoning}\n\n")

        f.write(f"## 持仓分析\n\n")
        holdings = [s for s in decision.unified_scoring if s.is_holding]
        if holdings:
            for i, score in enumerate(holdings, 1):
                f.write(f"### {i}. {score.name} ({score.symbol})\n\n")
                f.write(f"- **综合评分:** {score.composite_score:.1f}/100\n")
                f.write(f"- **动作建议:** {score.action_recommendation}\n")
                f.write(f"- **置信度:** {score.confidence:.0%}\n\n")
                f.write(f"**评分明细:**\n")
                for dim, val in score.dimension_scores.items():
                    f.write(f"- {dim}: {val:.1f}\n")
                f.write(f"\n**理由:** {score.reasoning}\n\n")
        else:
            f.write(f"当前无持仓\n\n")

        f.write(f"## 候选股评分 Top 5\n\n")
        candidates = [s for s in decision.unified_scoring if not s.is_holding][:5]
        for i, score in enumerate(candidates, 1):
            f.write(f"### {i}. {score.name} ({score.symbol})\n\n")
            f.write(f"- **综合评分:** {score.composite_score:.1f}/100\n")
            f.write(f"- **动作建议:** {score.action_recommendation}\n")
            f.write(f"- **置信度:** {score.confidence:.0%}\n\n")
            f.write(f"**评分明细:**\n")
            for dim, val in score.dimension_scores.items():
                f.write(f"- {dim}: {val:.1f}\n")
            f.write(f"\n**理由:** {score.reasoning}\n\n")

        f.write(f"## 交易建议 ({len(decision.allocation)} 条)\n\n")
        if decision.allocation:
            f.write(f"**操作总结:** {decision.summary}\n\n")
            for alloc in decision.allocation:
                action_emoji = {"buy": "🟢", "sell": "🔴", "hold": "🟡"}.get(alloc.action, "⚪")
                f.write(f"### {action_emoji} {alloc.action.upper()} {alloc.name} ({alloc.symbol})\n\n")
                f.write(f"- **数量:** {alloc.quantity_delta:+d} 股\n")
                f.write(f"- **金额:** ¥{alloc.target_value:,.0f}\n")
                f.write(f"- **价格区间:** ¥{alloc.price_range[0]:.2f} ~ ¥{alloc.price_range[1]:.2f}\n")
                f.write(f"- **预期收益:** {alloc.expected_return:.1%}\n")
                f.write(f"- **止损价:** ¥{alloc.stop_loss:.2f}\n")
                f.write(f"- **风险收益比:** {alloc.risk_reward_ratio:.2f}\n")
                f.write(f"- **置信度:** {alloc.confidence:.0%}\n\n")
                f.write(f"**理由:** {alloc.reasoning}\n\n")
        else:
            f.write("**操作总结:** 当前无需操作，维持现有仓位\n\n")

        if decision.warnings:
            f.write(f"## ⚠️ 风险提示\n\n")
            for warning in decision.warnings:
                f.write(f"- {warning}\n")
            f.write("\n")

        # v3.5 S4：主线续航评估
        mainline_endurance = getattr(decision, "mainline_endurance", [])
        if mainline_endurance:
            f.write(f"## 📊 主线续航评估（{len(mainline_endurance)} 个家族，启发式）\n\n")
            band_emoji = {"inertia": "🟢", "divergence": "🟡", "topping_risk": "🔴"}
            for endurance in mainline_endurance:
                emoji = band_emoji.get(endurance.endurance_band, "⚪")
                f.write(f"### {emoji} {endurance.name}\n\n")
                f.write(f"- **波次天数:** {endurance.wave_days} 天\n")
                f.write(f"- **续航区间:** {endurance.endurance_band}\n")
                f.write(f"- **成员数:** {endurance.member_count}（RRG leading: {endurance.rrg_leading_count}）\n")
                f.write(f"- **家族共振:** {endurance.family_resonance:.2f}\n")
                f.write(f"- **资金立场:** {endurance.capital_stance}\n")
                f.write(f"- **催化剂:** {'✓' if endurance.has_catalyst else '✗'}\n")
                if endurance.crowded:
                    f.write(f"- **拥挤警告:** 家族拥挤\n")
                if endurance.max_overheat > 0:
                    f.write(f"- **过热严重度:** {endurance.max_overheat:.2f}\n")

                if endurance.tiers.get("veteran") or endurance.tiers.get("mid") or endurance.tiers.get("new"):
                    f.write(f"- **梯队分层:** ")
                    tier_parts = []
                    if endurance.tiers.get("veteran"):
                        tier_parts.append(f"老龙头({len(endurance.tiers['veteran'])})")
                    if endurance.tiers.get("mid"):
                        tier_parts.append(f"中生代({len(endurance.tiers['mid'])})")
                    if endurance.tiers.get("new"):
                        tier_parts.append(f"新补涨({len(endurance.tiers['new'])})")
                    f.write(" / ".join(tier_parts) + "\n")

                if endurance.support_signals:
                    f.write(f"- **支撑信号:** {', '.join(endurance.support_signals)}\n")
                if endurance.warning_signals:
                    f.write(f"- **警示信号:** {', '.join(endurance.warning_signals)}\n")
                f.write(f"- **次日观察:** {endurance.exit_watch}\n\n")

        # P4：埋伏持仓退出信号
        ambush_exits = getattr(decision, "ambush_exit_signals", [])
        if ambush_exits:
            f.write(f"## 🔍 埋伏退出信号（{len(ambush_exits)} 条）\n\n")
            tag_emoji = {"drift": "📈", "fade": "🔔", "binary": "⚖️"}
            for exit_info in ambush_exits:
                tag = exit_info.get("category_tag", "?")
                emoji = tag_emoji.get(tag, "•")
                f.write(
                    f"- {emoji} **{exit_info.get('symbol', '')}** "
                    f"({exit_info.get('sector', '')}/{tag}): "
                    f"`{exit_info.get('exit_signal', '')}` — "
                    f"{exit_info.get('reason', '')}\n"
                )
            f.write("\n")

        f.write(f"## 推理总结\n\n")
        f.write(f"{decision.reasoning}\n")

    logger.info(f"[Archive] Phase 2 交易建议已生成: {trading_md_path}")

    # 3. 追加 Phase 2 reasoning 到 reasoning.md
    reasoning_path = output_dir / "reasoning.md"
    with open(reasoning_path, "a", encoding="utf-8") as f:
        f.write(f"\n## Node 7 交易建议\n\n")
        f.write(f"{decision.reasoning}\n\n")
        if decision.allocation:
            f.write(f"**操作总结:** {decision.summary}\n\n")
            f.write(f"**交易明细:**\n\n")
            for alloc in decision.allocation:
                f.write(
                    f"- **{alloc.action.upper()}** {alloc.name} "
                    f"({alloc.symbol}): {alloc.quantity_delta:+d} 股, "
                    f"¥{alloc.target_value:,.0f}\n"
                )
                f.write(f"  - 理由: {alloc.reasoning}\n"
                        f"  - 止损: ¥{alloc.stop_loss:.2f}, "
                        f"风险收益比: {alloc.risk_reward_ratio:.2f}\n")
            f.write("\n")
        if decision.warnings:
            f.write(f"**风险提示:**\n\n")
            for w in decision.warnings:
                f.write(f"- {w}\n")
            f.write("\n")
        # P4：埋伏退出信号
        ambush_exits = getattr(decision, "ambush_exit_signals", [])
        if ambush_exits:
            f.write(f"**🔍 埋伏退出信号（{len(ambush_exits)} 条）:**\n\n")
            for exit_info in ambush_exits:
                f.write(
                    f"- {exit_info.get('symbol', '')} "
                    f"({exit_info.get('sector', '')}/{exit_info.get('category_tag', '?')}): "
                    f"`{exit_info.get('exit_signal', '')}`\n"
                )
            f.write("\n")
    logger.info(f"[Archive] Phase 2 reasoning 已追加到: {reasoning_path}")

    logger.info(f"[Archive] Phase 2 决策已存档: {output_path}")


def load_phase2_decision(run_date: date) -> Optional["PortfolioDecision"]:
    """
    加载 Phase 2 决策

    Args:
        run_date: 运行日期

    Returns:
        PortfolioDecision 或 None（如果文件不存在）
    """
    from src.got.schemas import PortfolioDecision

    base_dir = _default_base_dir()
    date_str = run_date.strftime("%Y%m%d")
    decision_path = base_dir / date_str / "portfolio_decision.json"

    if not decision_path.exists():
        logger.debug(f"[Archive] Phase 2 决策不存在: {decision_path}")
        return None

    try:
        data = json.loads(decision_path.read_text(encoding="utf-8"))
        decision = PortfolioDecision(**data)
        logger.info(f"[Archive] Phase 2 决策已加载: {decision_path}")
        return decision
    except Exception as e:
        logger.error(f"[Archive] Phase 2 决策加载失败: {e}")
        return None

