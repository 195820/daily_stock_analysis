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
import shutil
import subprocess
from datetime import date, datetime, time
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
from src.got.persistence import list_archive_dirs, load_regime_raw_segment_days  # noqa: F401

logger = logging.getLogger(__name__)


def _sanitize_nan(obj):
    """递归将 float nan/inf 替换为 None（allow_nan=False 落盘前清洗）"""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nan(v) for v in obj]
    if isinstance(obj, set):
        # 自定义 serializer 可能绕过 Pydantic 的 set→list 转换，防 NaN 漏网
        return [_sanitize_nan(v) for v in obj]
    return obj

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

# A-P0-1: 归档目录遍历的唯一权威实现位于 persistence（7 个 loader 共用），
# 此处仅重导出供既有的 `from src.got.archive import list_archive_dirs` 调用方使用。


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
    tmp_dir = archive_dir.with_suffix(".tmp")
    old_dir = archive_dir.with_suffix(".old")

    # ① 写入旁路目录 .tmp（避免「整个写入期」窗口被并发读到半成品）
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # ② 替换前搬运：目标目录的 agent_glue.json 和 _replay/ 搬进 tmp
    # agent_glue.json 是当日影子结论，按 D-1「末版为准」必须随本轮覆盖；
    # 但 aggregator._save_glue_result 在归档前已直接写目标目录，故需搬运。
    # _replay/ 是回放模式的旁路产物，保留语义不变。
    if archive_dir.exists():
        glue_src = archive_dir / "agent_glue.json"
        if glue_src.exists():
            glue_dst = tmp_dir / "agent_glue.json"
            shutil.move(str(glue_src), str(glue_dst))
        # Q1 audit: 搬运 degraded 取证文件（forensics 例外，不受 Q6-A 按日去重约束）
        # 文件名含 HHMMSS 字典序即时间序；同日多次失败仅保留最新 5 份，防止无限堆积
        _degraded_files = sorted(archive_dir.glob("agent_glue_degraded_*.json"), reverse=True)
        for degraded_file in _degraded_files[:5]:
            degraded_dst = tmp_dir / degraded_file.name
            shutil.move(str(degraded_file), str(degraded_dst))
        for stale_degraded in _degraded_files[5:]:
            stale_degraded.unlink(missing_ok=True)
        replay_src = archive_dir / "_replay"
        if replay_src.exists():
            replay_dst = tmp_dir / "_replay"
            shutil.move(str(replay_src), str(replay_dst))

    contexts = {
        "market": market_ctx,
        "sector": sector_ctx,
        "capital": capital_ctx,
        "news": news_ctx,
        "sentiment": sentiment_ctx,
        "unified": unified_ctx,
    }

    # ③ 写入 6 个 Context JSON 到 tmp 目录
    # 使用 mode="python" 避免 Pydantic v2 对 numpy 类型的序列化错误，再由 _sanitize_for_json 清洗
    for key, ctx in contexts.items():
        _write_json(
            tmp_dir / CONTEXT_FILES[key],
            ctx.model_dump(mode="python"),
        )

    # ④ 写入 reasoning.md 到 tmp 目录
    _write_reasoning_md(tmp_dir / "reasoning.md", contexts, run_date)

    # ⑤ 写入 meta.json 到 tmp 目录
    meta = _build_meta(
        run_date=run_date,
        use_mock=use_mock,
        start_time=start_time,
        contexts=contexts,
    )
    _write_json(tmp_dir / "meta.json", meta)

    # ⑥ 原子替换：目标→.old → .tmp→目标 → 删.old
    # 窗口从「整个写入期」缩短到「两次 rename 之间」。
    # Windows 兼容性：os.replace 对非空目录会失败，改用 os.rename 两步替换。
    # 崩溃恢复：若上次在两次 rename 之间中断，日期目录缺席而 .old 幸存 —— .old 被
    # persistence._SKIP_DIR_SUFFIXES 排除在 loader 之外，不恢复就会让该日归档彻底不可见。
    if not archive_dir.exists() and old_dir.exists():
        logger.warning("[GOT Archive] 检测到中断残留，先恢复归档目录: %s", old_dir)
        os.rename(str(old_dir), str(archive_dir))
    if archive_dir.exists():
        if old_dir.exists():
            shutil.rmtree(old_dir)
        os.rename(str(archive_dir), str(old_dir))
    os.rename(str(tmp_dir), str(archive_dir))
    if old_dir.exists():
        shutil.rmtree(old_dir)

    logger.info("[GOT Archive] 历史档已保存（原子替换）: %s", archive_dir)
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
            lines.append(f"- **仓位暴露上限:** {regime.risk_budget:.0%}")
            lines.append(f"- **恐贪指数:** {regime.fear_greed:.2f}（{regime.sentiment_stage}）")
            lines.append(f"- **板块方向:** {regime.market_direction}（{regime.mainline_count} 条主线）")
            lines.append(f"- **宏观摘要:** {regime.macro_overlay}")
            lines.append("")

        # 🧭 市场结构（Phase 4b）：转正后升格主正文；影子态改由 🧪 段承载，避免双处重复。
        # 仅当启用真值（market_theme 非 None）才在此渲染，影子留痕（market_theme_shadow）
        # 交给 _render_shadow_market_theme 统一收口到「影子模式观测」段。
        theme_ctx = getattr(unified_ctx, "market_theme", None)
        if theme_ctx is not None:
            lines.append("## 🧭 市场结构（Phase 4b，已启用）")
            lines.append("")
            status = getattr(theme_ctx, "status", "unknown")
            dq = getattr(theme_ctx, "data_quality", None)
            src = getattr(dq, "source", "") if dq else ""
            lines.append(f"- **状态:** {status}（源: {src}）")
            lead = getattr(theme_ctx, "leading_concepts", []) or []
            if lead:
                lines.append("- **领先概念:** " + "、".join(
                    f"{t.name}(#{t.rank} {t.change_pct:+.1f}%)"
                    for t in lead[:5]
                ))
            breadth = getattr(theme_ctx, "theme_breadth", None)
            if breadth is not None:
                lines.append(
                    f"- **宽度:** accelerating={getattr(breadth, 'accelerating_count', 0)} "
                    f"warming={getattr(breadth, 'warming_count', 0)} "
                    f"cooling={getattr(breadth, 'cooling_count', 0)} "
                    f"total={getattr(breadth, 'total_count', 0)}"
                )
            lines.append("_（4b 已启用，参与 Node 6 置信度一致性因子）_")
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
        # D-P3: 正式池水位目标（实际水位 / 目标区间 / 是否达标）
        from src.got.catalyst_config import AMBUSH_POOL_TARGET_RANGE
        actual_water_level = len(ambush_pool)
        target_lo, target_hi = AMBUSH_POOL_TARGET_RANGE
        if actual_water_level < target_lo:
            level_status = "⚠️ 低于目标"
        elif actual_water_level > target_hi:
            level_status = "⚠️ 高于目标"
        else:
            level_status = "✅ 达标"
        lines.append(f"**水位:** 实际 {actual_water_level} / 目标 [{target_lo}, {target_hi}] → {level_status}")
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

    # ── A6 语义粘合（已转正，Node 6 正文固定子块）──
    # 产物 agent_glue.json 已在 archive_daily_run 搬入当日 tmp 目录（与 reasoning.md 同级）；
    # glue_consumed/glue_status 直接读 unified_ctx，不依赖 json 落盘顺序。
    _a6_uc = contexts.get("unified")
    _a6_code_to_name = {
        s.sector_code: s.sector_name
        for s in (getattr(_a6_uc, "confirmed_sectors", []) if _a6_uc else [])
        if getattr(s, "sector_code", None)
    }
    lines += _render_a6_glue_formal(
        _read_json_quiet(path.parent / GLUE_ARTIFACT),
        glue_consumed=bool(getattr(_a6_uc, "glue_consumed", False)) if _a6_uc else False,
        glue_status=(getattr(_a6_uc, "glue_status", None) or "unknown") if _a6_uc else "unknown",
        code_to_name=_a6_code_to_name,
    )

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
    now = datetime.now()
    meta: dict = {
        "run_date": run_date.isoformat(),
        "run_timestamp": now.isoformat(timespec="seconds"),
        "use_mock": use_mock,
        "git_commit": _get_git_commit(),
        # A-P0-9: 快照标签（只落归档侧，不进节点 JSON / SQLite）
        "snapshot_time": now.isoformat(timespec="seconds"),
        "bar_complete": _is_bar_complete(run_date, now),
    }

    if start_time:
        meta["duration_seconds"] = round(
            (now - start_time).total_seconds(), 1
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

    # C-P10: 震荡惩罚可观测化（零行为变更，只记录本次运行是否生效）
    unified_ctx = contexts.get("unified")
    if unified_ctx is not None:
        market_regime = getattr(unified_ctx, "market_regime", None)
        if market_regime is not None:
            transition_count = getattr(market_regime, "transition_count", 0) or 0
            meta["regime_choppiness_penalty_applied"] = transition_count >= 5
            meta["transition_count_raw_segment_days"] = load_regime_raw_segment_days(20)

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


def _is_bar_complete(run_date: date, now: datetime) -> bool:
    """判断当日日线是否已走完（15:00 后且为交易日）。

    A-P0-9: 标记收盘后补跑版（如 0910 17:51），便于 P6 兑现追踪按 bar_complete 分组。
    先用墙钟判断（简单、与 13:30 节奏配合），后续如需精确再改为 bar 时间戳。
    """
    # 时间条件：15:00 后
    if now.time() < time(15, 0):
        return False
    # 交易日条件：尝试用交易日历判断
    try:
        from src.core.trading_calendar import is_market_open
        return is_market_open("cn", run_date)
    except Exception as exc:
        # 交易日历不可用时降级为工作日判断 —— 该降级会影响 bar_complete 语义
        # （法定节假日被误判为交易日），故用 warning 而非 debug，确保生产可见
        logger.warning("[archive] 交易日历判断失败，降级为工作日: %s", exc)
        return run_date.weekday() < 5


SHADOW_SECTION_TITLE = "## 🧪 影子模式观测"

# A6 glue 转正后在 Node 6 正文的固定子块标题（不再是影子）
A6_FORMAL_SECTION_TITLE = "## 🧬 A6 语义粘合（已转正）"

# 影子读数起算注：P0 #1 修复前 aggregator.run() 缺 import os 必崩，
# 4b/3b/4.1 影子观测从未真正执行——修复提交日（2026-09-29）前的读数全部无效。
SHADOW_READING_BASELINE_NOTE = (
    "> ⚠️ **读数起算注**：2026-09-29 前的 4b/3b/4.1 影子读数无效"
    "（P0 #1 修复前 `aggregator.run()` 缺 `import os` 崩溃，影子观测从未真正执行）；"
    "有效读数自该修复提交日重新起算。"
)

# 影子产物文件名（与 A5 既有的 agent_glue.json 对称，均落在 data/got_daily/{date}/）
GLUE_ARTIFACT = "agent_glue.json"
SHADOW_T1_ARTIFACT = "shadow_t1.json"
SHADOW_T1H_ARTIFACT = "shadow_t1h.json"
SHADOW_MEG_ARTIFACT = "shadow_meg.json"


def _shadow_display_name(name: str, symbol: str) -> str:
    """影子段的股票显示名。

    部分持仓的 `name` 字段等于代码（backlog §2 P1-1，已知未修），
    此时只显示代码一次，避免出现「600250（600250）」。
    """
    text = (name or "").strip()
    return f"{text}（{symbol}）" if text and text != symbol else (symbol or text)


def _render_a6_glue_formal(
    glue: "dict | None",
    *,
    glue_consumed: bool,
    glue_status: str,
    code_to_name: dict,
) -> list[str]:
    """A6 glue（已转正）：Node 6 正文固定子块——叙述 + 全量语义表。

    阶段 1 转正（2026-09-17）后 glue 不再是影子：status=ok 时直接替换板块评分四件套，
    degraded 板块保留规则侧评分。措辞与转正前的「影子，不影响决策」对称可比。
    """
    lines = [A6_FORMAL_SECTION_TITLE, ""]
    if not glue:
        lines += [
            f"**A6 glue**（已转正）：本次无产物（`{GLUE_ARTIFACT}` 缺失），"
            "板块评分保留规则侧。",
            "",
        ]
        return lines

    strengths = glue.get("strengths") or []
    relations = glue.get("relations") or []
    families = glue.get("families") or []
    degraded = bool(glue.get("degraded"))
    consumed_note = (
        "已消费（glue 直接替换四件套）" if glue_consumed
        else "未消费（降级/门控未过，保留规则侧）"
    )
    lines += [
        f"**A6 glue**（已转正，status=`{glue_status}`，{consumed_note}）："
        f"本次对 {len(strengths)} 个候选板块做语义粘合，"
        f"产出 {len(relations)} 条事件→板块关系、{len(strengths)} 条强度判定、"
        f"{len(families)} 个家族归属"
        + ("，degraded 板块保留规则侧评分" if degraded else "")
        + "。下表是 Agent 粘合出的全部语义——「家族」列是 glue 的归属，"
        "消息/资金/情绪是它对三个维度的独立读数（`low` 表示该维度无支撑）。",
        "",
    ]
    if strengths:
        family_by_code = {f.get("board_code"): f.get("family") for f in families}
        path_by_code = {r.get("board_code"): (r.get("propagation_path") or []) for r in relations}
        lines.append("| 板块 | 家族 | 强度 | 消息 | 资金 | 情绪 | 关联事件（传导路径） |")
        lines.append("|---|---|---|---|---|---|---|")
        for item in strengths:
            code = item.get("board_code")
            path = path_by_code.get(code) or []
            lines.append(
                f"| {code_to_name.get(code) or code or '?'} "
                f"| {family_by_code.get(code) or '无归属'} "
                f"| {item.get('strength')} | {item.get('news_read')} "
                f"| {item.get('capital_read')} | {item.get('sentiment_read')} "
                f"| {' → '.join(path) if path else '—'} |"
            )
        lines.append("")
    return lines


def _render_glue_degraded_note(glue: "dict | None") -> list[str]:
    """A6 glue 已转正进 Node 6 正文；仅降级时在 🧪 影子段留一条降级观测行。

    转正前 A5 整段都在影子段；转正后正常态不再进影子段，只有 degraded（保留规则侧）
    需在此显式观测，供审阅降级面。
    """
    if not glue:
        return []
    status = glue.get("status") or "unknown"
    if status == "ok" and not glue.get("degraded"):
        return []
    sectors = glue.get("degraded_sectors") or []
    lines = [
        f"**A6 glue（已转正）降级观测**：本次 status=`{status}`，"
        "degraded 板块已回退规则侧评分"
        + (f"（{len(sectors)} 个：{'、'.join(sectors)}）" if sectors else "")
        + "。"
    ]
    lines += [f"- ⚠️ {w}" for w in (glue.get("warnings") or [])]
    lines.append("")
    return lines


def _render_shadow_t1(t1: "dict | None") -> list[str]:
    """T1 候选股终审：叙述 + 否决明细（无否决时明说）。"""
    if not t1:
        return [f"**T1 候选股终审**（影子，只有否决权）：本次无产物（`{SHADOW_T1_ARTIFACT}` 缺失）。", ""]

    if not t1.get("executed"):
        return [
            f"**T1 候选股终审**（影子，只有否决权）：本次**未执行**。"
            f"原因：{t1.get('reason') or '未说明'}。",
            "",
        ]

    reviewed = t1.get("reviewed") or 0
    budget = t1.get("budget")
    rejects = t1.get("rejects") or []
    lines = [
        f"**T1 候选股终审**（影子，只有否决权）：本次提交审查 {reviewed} 只"
        + (f"（预算上限 {budget}）" if budget else "")
        + f"，Agent 否决 {len(rejects)} 只。"
    ]
    if not rejects:
        return lines + ["", "本次无否决。", ""]

    lines.append("")
    lines += [
        f"- **{_shadow_display_name(r.get('name'), r.get('symbol'))}**：{r.get('reason')}"
        for r in rejects
    ]
    lines += ["", "影子模式下该否决仅影响展示顺序，未从候选池移除。", ""]
    return lines


def _render_shadow_t1h(t1h: "dict | None") -> list[str]:
    """T1-H 持仓审查：叙述 + verdict 明细；未执行时须列出未命中的筛选条件。"""
    if not t1h:
        return [f"**T1-H 持仓审查**（影子，只有降档权）：本次无产物（`{SHADOW_T1H_ARTIFACT}` 缺失）。", ""]

    if not t1h.get("executed"):
        lines = [
            f"**T1-H 持仓审查**（影子，只有降档权）：本次**未执行**。"
            f"原因：{t1h.get('reason') or '未说明'}。"
        ]
        skipped = t1h.get("skipped") or []
        if skipped:
            lines += [""]
            lines += [
                f"- {_shadow_display_name(s.get('name'), s.get('symbol'))}："
                f"综合评分 {s.get('composite_score')}，非埋伏持仓、无 P4 退出信号"
                for s in skipped
            ]
        return lines + [""]

    verdicts = t1h.get("verdicts") or []
    reviewed = t1h.get("reviewed") or 0
    budget = t1h.get("budget")
    lines = [
        f"**T1-H 持仓审查**（影子，只有降档权）：本次审查 {reviewed} 只持仓"
        + (f"（预算上限 {budget}）" if budget else "")
        + f"，Agent 给出 {len(verdicts)} 条裁决。"
    ]
    if not verdicts:
        return lines + ["", "本次无裁决。", ""]

    lines.append("")
    lines += [
        f"- **{_shadow_display_name(v.get('name'), v.get('symbol'))}**"
        f"裁决 `{v.get('verdict')}`（置信度 {v.get('confidence')}）：{v.get('reasoning')}"
        for v in verdicts
    ]
    lines += ["", "影子模式下裁决已归档但未施加降档惩罚，未影响持仓评分。", ""]
    return lines


def _render_shadow_market_theme(unified: "dict | None") -> list[str]:
    """4b 市场结构（影子态）：从主正文 🧭 收编至此，读 unified.json 的 market_theme_shadow。"""
    shadow = (unified or {}).get("market_theme_shadow")
    if not shadow:
        if (unified or {}).get("market_theme"):
            return ["**4b 市场结构**（已启用）：本运行 4b 已转正，读数见主正文「市场结构（Phase 4b，已启用）」段。", ""]
        return ["**4b 市场结构**（影子，不参与评分）：本次无产物（`unified.json` 未落 `market_theme_shadow`，多为开关关闭或数据不可达）。", ""]
    status = shadow.get("status", "unknown")
    lines = [f"**4b 市场结构**（影子，不参与评分）：状态 `{status}`（源: {shadow.get('source', '')}）。"]
    if status == "unknown":
        lines.append(f"- ⚠️ 数据不可达: {shadow.get('note', '')}")
    else:
        top5 = shadow.get("top5_concepts") or []
        if top5:
            lines.append("- **领先概念:** " + "、".join(
                f"{t.get('name')}(#{t.get('rank')} {t.get('change_pct'):+.1f}%)" for t in top5))
        breadth = shadow.get("breadth") or {}
        lines.append(
            f"- **宽度:** accelerating={breadth.get('accelerating_count', 0)} "
            f"warming={breadth.get('warming_count', 0)} "
            f"cooling={breadth.get('cooling_count', 0)} "
            f"total={breadth.get('total_count', 0)}"
        )
        if shadow.get("note"):
            lines.append(f"- **备注:** {shadow.get('note')}")
    lines.append("")
    return lines


def _render_shadow_disagreement(unified: "dict | None") -> list[str]:
    """3b 分歧摘要（影子）：冲突分类计数 + 与现行 contradictions[] 的差异行数。"""
    cs = (unified or {}).get("conflict_summary_shadow")
    if not cs:
        return ["**3b 分歧摘要**（影子，不参与仲裁）：本次无产物（`unified.json` 未落 `conflict_summary_shadow`，多为开关关闭）。", ""]
    bullish = cs.get("bullish_agents") or []
    bearish = cs.get("bearish_agents") or []
    neutral = cs.get("neutral_agents") or []
    contradictions = (unified or {}).get("contradictions") or []
    rc = cs.get("risk_control") or {}
    diverged = len(bullish) + len(bearish)
    lines = [
        f"**3b 分歧摘要**（影子，不参与仲裁）：分类 `{cs.get('conflict_type', 'unknown')}`——"
        f"看多 {len(bullish)} / 看空 {len(bearish)} / 中性 {len(neutral)} 个 Agent。"
    ]
    if cs.get("decision_path_hint"):
        lines.append(f"- **决策路径提示:** {cs.get('decision_path_hint')}")
    if rc.get("veto_buy"):
        lines.append(f"- 🚫 **风控覆盖:** veto_buy（{rc.get('reason', '')}）")
    else:
        lines.append("- 风控覆盖: 无（veto_buy=false）")
    lines.append(
        f"- **与现行矛盾对比:** 本摘要识别分歧方 {diverged} 个 vs "
        f"`contradictions[]` {len(contradictions)} 条（差异 {abs(diverged - len(contradictions))}，影子期仅供对照）"
    )
    lines.append("")
    return lines


def _render_shadow_sell_source(shadow: "dict | None") -> list[str]:
    """4.1 组合层超限减仓（影子）：mode/缺口/计划减仓/宽限跳过。"""
    if not shadow:
        return ["**4.1 超限减仓**（影子，不落减仓）：本次无产物（`portfolio_decision.json` 未落 `sell_source_shadow`，多为仓位未超限）。", ""]
    excess = shadow.get("excess_value", 0) or 0
    projected = shadow.get("projected_trim_value", 0) or 0
    trims = shadow.get("planned_trims") or []
    grace = shadow.get("grace_skipped_symbols") or []
    lines = [
        f"**4.1 超限减仓**（影子，不落减仓）：mode=`{shadow.get('mode', 'shadow')}`，"
        f"超限缺口 ¥{excess:,.0f}，计划削 {len(trims)} 只"
        f"（预计减仓 ¥{projected:,.0f}），家族宽限跳过 {len(grace)} 只。"
    ]
    if trims:
        lines += [
            f"- **{_shadow_display_name(t.get('name'), t.get('symbol'))}**: "
            f"减 {t.get('reduce_quantity')} 股 / ¥{(t.get('sell_value') or 0):,.0f}"
            for t in trims]
    else:
        lines.append("- 本次无可削标的（或均撞家族宽限）。")
    if grace:
        lines.append(f"- 宽限跳过：{', '.join(grace)}")
    lines.append("")
    return lines


def _render_shadow_meg(meg: "dict | None") -> list[str]:
    """MEG 动能衰竭门（影子）：层2 逐股 buy_trend→watch / 层3 逐板块降仓。

    仅当 shadow_meg.json 存在（GOT_MOM_EXHAUST_GATE_ENABLED=1）才渲染；
    缺省 off 态无产物 → 返回 []（reasoning.md 逐字节不变）。
    回测证真（§7.8.1）：acceleration+snap T+5 横截面超额 -3.07%，立案案例 880952 芯片 9/23 命中 -9.41%。
    """
    if not meg:
        return []
    mode = meg.get("mode", "shadow")
    evaluated = meg.get("evaluated", 0)
    flagged = meg.get("flagged_stocks") or []
    sectors = meg.get("flagged_sectors") or []
    verb = "已降级" if mode == "veto" else "本应降级（未生效）"
    tag = "影子，只留痕" if mode != "veto" else "veto，已生效"
    lines = [
        f"**MEG 动能衰竭门**（{tag}，mode=`{mode}`）：评估 {evaluated} 只候选，"
        f"层2 命中 {len(flagged)} 只（buy_trend→watch {verb}），层3 缩仓命中 {len(sectors)} 板块。"
    ]
    if flagged:
        for f in flagged:
            d = f.get("decay_2d")
            ds = f"{d:.0%}" if isinstance(d, (int, float)) else "NA"
            lines.append(
                f"- **{_shadow_display_name(f.get('name'), f.get('symbol'))}**：板块={f.get('sector')} "
                f"decay_2d={ds} {f.get('action_before')}→{f.get('action_wouldbe')}"
            )
    else:
        lines.append("- 层2 本次无 acceleration+snap 追涨候选被拦。")
    for s in sectors:
        d = s.get("decay_2d")
        ds = f"{d:.0%}" if isinstance(d, (int, float)) else "NA"
        lines.append(
            f"- 层3 板块 **{s.get('sector_name')}**：decay_2d={ds} "
            f"base {s.get('base_before')}→{s.get('base_wouldbe')}（{s.get('role')}）"
        )
    lines.append("")
    return lines


def _render_shadow_section(
    *,
    glue: "dict | None",
    t1: "dict | None",
    t1h: "dict | None",
    code_to_name: dict,
    unified: "dict | None" = None,
    sell_source_shadow: "dict | None" = None,
    meg_shadow: "dict | None" = None,
) -> list[str]:
    """渲染「影子模式观测」段（reasoning.md 末尾，五条影子线各一段）。

    五条线：4b 市场结构 / 3b 分歧摘要 / 4.1 超限减仓 / T1 候选股终审 / T1-H 持仓审查。
    A5 语义粘合层已转正（A6），改由 Node 6 正文子块 `_render_a6_glue_formal` 承载；
    仅当 glue 降级时才在本段留一条降级观测行。code_to_name 保留签名位（已转正线不再用）。
    """
    lines = [SHADOW_SECTION_TITLE, "", SHADOW_READING_BASELINE_NOTE, ""]
    lines += _render_shadow_market_theme(unified)
    lines += _render_shadow_disagreement(unified)
    lines += _render_shadow_sell_source(sell_source_shadow)
    lines += _render_shadow_meg(meg_shadow)
    lines += _render_glue_degraded_note(glue)
    lines += _render_shadow_t1(t1)
    lines += _render_shadow_t1h(t1h)
    return lines


def _read_json_quiet(path: Path) -> "dict | None":
    """读 JSON；文件缺失或损坏时返回 None（影子段降级为「无产物」而非中断归档）。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[Archive] 影子产物读取失败 %s: %s", path.name, exc)
        return None


def _append_phase2_reasoning(reasoning_path: Path, decision) -> None:
    """覆盖写 Phase 2 reasoning 到 reasoning.md。

    D-P6（A-P0-8 子任务）: Phase 2 同样遵守「末版为准」，同日重跑时
    先清理旧的「Node 7 交易建议」段再写新内容，而非追加（避免 reasoning.md 出现多份）。
    """
    # 读取现有内容，截取 Node 7 段之前的部分
    existing_content = ""
    if reasoning_path.exists():
        full_text = reasoning_path.read_text(encoding="utf-8")
        # 找到 "## Node 7 交易建议" 段的位置
        marker = "## Node 7 交易建议"
        idx = full_text.find(marker)
        if idx >= 0:
            # 截取 Node 7 段之前的内容
            existing_content = full_text[:idx].rstrip()
        else:
            existing_content = full_text.rstrip()

    # 构建新的 Phase 2 内容
    lines = []
    if existing_content:
        lines.append(existing_content)
        lines.append("")
    lines.append(f"## Node 7 交易建议")
    lines.append("")
    lines.append(decision.reasoning)
    lines.append("")
    if decision.allocation:
        lines.append(f"**操作总结:** {decision.summary}")
        lines.append("")
        lines.append("**交易明细:**")
        lines.append("")
        for alloc in decision.allocation:
            lines.append(
                f"- **{alloc.action.upper()}** {alloc.name} "
                f"({alloc.symbol}): {alloc.quantity_delta:+d} 股, "
                f"¥{alloc.target_value:,.0f}"
            )
            lines.append(f"  - 理由: {alloc.reasoning}")
            lines.append(f"  - 止损: ¥{alloc.stop_loss:.2f}, "
                         f"风险收益比: {alloc.risk_reward_ratio:.2f}")
        lines.append("")
    if decision.warnings:
        lines.append("**风险提示:**")
        lines.append("")
        for w in decision.warnings:
            lines.append(f"- {w}")
        lines.append("")
    # P4：埋伏退出信号
    ambush_exits = getattr(decision, "ambush_exit_signals", [])
    if ambush_exits:
        lines.append(f"**🔍 埋伏退出信号（{len(ambush_exits)} 条）:**")
        lines.append("")
        for exit_info in ambush_exits:
            lines.append(
                f"- {exit_info.get('symbol', '')} "
                f"({exit_info.get('sector', '')}/{exit_info.get('category_tag', '?')}): "
                f"`{exit_info.get('exit_signal', '')}`"
            )
        lines.append("")

    # 影子模式观测（五条影子线）——挂在 Node 7 之后，同日重跑时随 Node 7 段一并被丢弃重建
    day_dir = reasoning_path.parent
    _unified_json = _read_json_quiet(day_dir / "unified.json") or {}
    _pd_json = _read_json_quiet(day_dir / "portfolio_decision.json") or {}
    _code_to_name = {
        s.get("sector_code"): s.get("sector_name")
        for s in (_unified_json.get("confirmed_sectors") or [])
        if s.get("sector_code")
    }
    lines += _render_shadow_section(
        glue=_read_json_quiet(day_dir / GLUE_ARTIFACT),
        t1=_read_json_quiet(day_dir / SHADOW_T1_ARTIFACT),
        t1h=_read_json_quiet(day_dir / SHADOW_T1H_ARTIFACT),
        code_to_name=_code_to_name,
        unified=_unified_json,
        sell_source_shadow=_pd_json.get("sell_source_shadow"),
        meg_shadow=_read_json_quiet(day_dir / SHADOW_MEG_ARTIFACT),
    )

    reasoning_path.write_text("\n".join(lines), encoding="utf-8")


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

    # 1. 写入 portfolio_decision.json（全量 model_dump，原子写 + 回读自检）
    output_path = output_dir / "portfolio_decision.json"
    payload = decision.model_dump(mode="json")
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    except ValueError:
        logger.warning("[archive] 决策数据含 NaN/Inf 字段，已清洗为 null 后落盘")
        text = json.dumps(_sanitize_nan(payload), ensure_ascii=False, indent=2, allow_nan=False)
    tmp_path = output_path.with_suffix(".json.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    # 回读自检：先验 tmp 再原子替换，正式文件永不被半截 JSON 污染
    try:
        json.loads(tmp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        corrupt_path = output_path.with_suffix(".json.corrupt")
        try:
            os.replace(tmp_path, corrupt_path)
        except OSError:
            pass
        logger.error(
            f"[archive] portfolio_decision.json 回读自检失败，"
            f"坏文件保留为 {corrupt_path.name}: {e}"
        )
        raise
    os.replace(tmp_path, output_path)

    # 2. 生成独立的交易建议 MD 文档
    trading_md_path = output_dir / "trading_recommendations.md"
    with open(trading_md_path, "w", encoding="utf-8") as f:
        # Q4-D audit: 盘中快照警告（bar_complete=false 时）
        if any(a.intraday_snapshot for a in decision.allocation):
            f.write(f"## ⚠️ 盘中快照警告\n\n")
            f.write(f"**价格与止损基于盘中快照（bar_complete=false），非收盘价，仅供参考**\n\n")
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

    # 3. 覆盖写 Phase 2 reasoning 到 reasoning.md
    # D-P6（A-P0-8 子任务）: Phase 2 同样遵守「末版为准」，同日重跑时覆盖旧的 Node 7 段
    reasoning_path = output_dir / "reasoning.md"
    _append_phase2_reasoning(reasoning_path, decision)
    logger.info(f"[Archive] Phase 2 reasoning 已写入: {reasoning_path}")

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


# =============================================================================
# 回放 harness（docs/got/archive/agent_glue_plan_0903.md §8.3 A1）
# =============================================================================

_CONTEXT_MODEL_MAP = {
    "market": MarketContext,
    "sector": SectorContext,
    "capital": CapitalContext,
    "news": NewsContext,
    "sentiment": SentimentContext,
    "unified": UnifiedContext,
}


def load_daily_contexts(
    run_date: date,
    *,
    base_dir: Optional[Path] = None,
) -> dict[str, object]:
    """从 data/got_daily/YYYYMMDD/ 加载 6 个 Context JSON 并反序列化为 pydantic 对象。

    用于回放 harness：把归档数据还原为 AggregatorNode.run() 的输入。

    Args:
        run_date: 回放日期
        base_dir: 归档根目录（默认 data/got_daily/）

    Returns:
        dict: {"market": MarketContext, "sector": SectorContext, ...}
              缺失的文件会记 warning 并跳过（不 raise）

    Raises:
        FileNotFoundError: 日期目录不存在
    """
    base_dir = base_dir or _default_base_dir()
    date_str = run_date.strftime("%Y%m%d")
    archive_dir = base_dir / date_str

    if not archive_dir.exists():
        raise FileNotFoundError(f"[Archive] 回放目录不存在: {archive_dir}")

    result = {}
    for key, filename in CONTEXT_FILES.items():
        filepath = archive_dir / filename
        if not filepath.exists():
            logger.warning("[Archive] 回放文件缺失（跳过）: %s", filepath)
            continue
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            model_cls = _CONTEXT_MODEL_MAP[key]
            result[key] = model_cls(**data)
        except Exception as e:
            logger.error("[Archive] 回放文件加载失败 %s: %s", filepath, e)

    logger.info("[Archive] 回放数据已加载: %s — %d/%d 个 Context",
                date_str, len(result), len(CONTEXT_FILES))
    return result

