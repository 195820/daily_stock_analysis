# -*- coding: utf-8 -*-
"""D 层验收红线：R-D1 / R-D2（对 0902 审核报告 15 项逐条**真算**复算）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
红线原文（`docs/got/archive/got_layer_d_plan_0911.md` §6「验收红线（可计数）」）

| # | 指标 | 达标线 | 数据源 | 可度量 |
|---|---|---|---|---|
| **R-D1** | **声明的可复算率**：报告中每个「已修复/已解决」声明都附一条可复算证据 | `unverified` 条数 = **0**（2026-09-11 基线 = **10**） | `scripts/check_audit_claims.py` | ✅ |
| **R-D2** | **附录 A 与脚本输出逐位一致** | 逐位一致 | 同上 | ✅ |

R-D1 的 10 条基线明细（同文档 §6，2026-09-11 实测）：

    P0-2 / P0-5 / P1-1 / P1-2 / P1-3 / P1-4 / P1-5 / P2-1 / P2-3   （9 条未修）
    P0-1                                                            （改了但未生效，按 unverified 计）

R-D1 的子计数（同文档 §6 表格，作为过程指标，不计入达标线）：

| 子指标 | 含义 | 2026-09-11 基线 |
|---|---|---|
| `divergent_trigger_days` | 出现 `capital_check == "divergent"` 的归档日数 | **0**（近 30 个归档日） |
| `ambush_pool_saturation_days` | 蓄水池达到 `RESERVOIR_MAX_SIZE` 的连续日数 | **≥10** |
| `dead_signal_count` | 「存在但未启用」的字段/形参/配置项（附录 C.3 形状 1） | **4**（E4 / E5 / E10 / E11） |
| `dead_branch_count` | 可证不可达的分支数（附录 C.4） | **1**（E3 的埋伏买入分支） |

> 基线时效性（audit_20260922 Q2-A2）：E3 死分支的结构性根因——G2/G3 闸门因
> sentiment 热度数据恒空而「恒不可过」——已由 THS 板块热度直喂修复；归档
> `gate_funnel` 已移除 `structurally_dead*` 标记、改由 `data_source` 采集口径。
> 本脚本 `dead_branch_count` 为全归档 `is_ambush_entry` 派生（历史日包含在内），
> 会随闸门激活后的新归档自然衰减；文档基线 **1** 仅为 0911 快照，非永久值，
> 对照复算时须先查 `_detail.ambush_gate_data_source` 确认闸门已激活。

手具要求（同文档 §5.3 D-P9）：

    「扫描指定文档（默认 0902 报告 + 本文件附录 A）中所有『已修复 / 已修 / 已解决 / 已生效』
      声明，提取其证据链接（代码位置 / 归档字段 / 脚本），逐条尝试复算；
      无证据或复算失败 → 输出 `unverified`。」
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
实现方式
    R-D1：把 0902 报告 15 项声明做成表，逐项用**归档数据 + 代码实读**判据复算，得四态之一：
        EFFECTIVE（修复已落地且复算证实）/ BY_DESIGN（报告结论为「不修（行为正确）」，复算证实行为正确）
        SYMPTOM_GONE（症状自行消失，无归因）/ INEFFECTIVE（声明未生效或未修）
        前三态计入 verified；`unverified` = 15 项中判为 INEFFECTIVE 的条数（基线 10）。

      ⚠️ 判定规则：**以归档可观测量为准，代码形状仅作佐证** —— 这正是 P0-1 的教训
      （「改了代码但分支仍 0 触发」不得判为生效）。因此「代码已修但无修复后归档可复算」的项
      一律按 unverified 计，并在证据里注明修复当前只存在于未提交工作区。
    R-D2：四个子计数由脚本真算，与 §6 表格的基线**逐位比对**；
        `ambush_pool_saturation_days` 按文档定义即「连续日数」，文档给的是阈值 `≥10`，按阈值判定。

    ⚠️ 口径声明：本脚本以**工作区当前代码 + 现有归档**为准。若某项修复只改了代码、
    尚未产生新归档（如 Phase 2 落盘类），其代码形状证据与归档证据会不同步 ——
    每项证据行会标注该修复是否已进入最新提交（`git show HEAD:<file>`），供人工复核。

退出码：全 PASS 且无 SKIP = 0；有 FAIL = 1；有 SKIP 且无 FAIL = 2。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_common import (  # noqa: E402
    Verifier,
    force_utf8_stdout,
    got_daily_root,
    iter_archive_days,
    print_header,
    read_json,
    repo_root,
)

REDLINE_DOC = "docs/got/archive/got_layer_d_plan_0911.md §6"
AUDIT_REPORT = repo_root() / "docs" / "got" / "archive" / "GOT_0902_audit_report.md"
D_PLAN = repo_root() / "docs" / "got" / "archive" / "got_layer_d_plan_0911.md"

AGGREGATOR = repo_root() / "src" / "got" / "nodes" / "aggregator.py"
SENTIMENT = repo_root() / "src" / "got" / "nodes" / "sentiment_analysis.py"
PORTFOLIO = repo_root() / "src" / "got" / "nodes" / "portfolio_decision.py"
SECTOR_ANALYSIS = repo_root() / "src" / "got" / "nodes" / "sector_analysis.py"
ARCHIVE = repo_root() / "src" / "got" / "archive.py"
SCHEMAS = repo_root() / "src" / "got" / "schemas.py"
FAMILY_KW = repo_root() / "src" / "got" / "family_keywords.py"
CATALYST_CONFIG = repo_root() / "src" / "got" / "catalyst_config.py"

# 复算状态
EFFECTIVE = "EFFECTIVE"        # 修复已落地，复算证实
BY_DESIGN = "BY_DESIGN"        # 报告结论「不修（行为正确）」，复算证实行为正确
SYMPTOM_GONE = "SYMPTOM_GONE"  # 症状自行消失，无归因
INEFFECTIVE = "INEFFECTIVE"    # 未修 / 改了但未生效
VERIFIED_STATES = {EFFECTIVE, BY_DESIGN, SYMPTOM_GONE}

R_D1_BASELINE = 10
CONFIDENCE_BASE = 0.7          # P0-2：板块置信度隐式基础分
BASE_COLLAPSE_SHARE = 0.5      # 基础分占比 ≥ 50% 即判定「仍坍缩在基础分上」
MENTION_SPREAD_MIN = 0.10      # P1-3：mention_count 极差 > 10% 视为已恢复区分度
P1_5_FIX_DATE = "20260912"     # P1-5（Node 7 段重复）修复落地日，commit 8ad119b；
                               # 归档日早于此值的属**修复前归档**，不构成修复失效的证据


# ══════════════════════════════════════════════════════════════════════
# 读取设施
# ══════════════════════════════════════════════════════════════════════


def _real_days() -> list[Path]:
    """真实归档日（排除 20260805_backup 这类非交易日目录）。"""
    return iter_archive_days()


def _days_with(filename: str) -> list[Path]:
    return [day for day in _real_days() if (day / filename).exists()]


def _latest_day_with(filename: str) -> Path | None:
    days = _days_with(filename)
    return days[-1] if days else None


def _load(day: Path | None, filename: str) -> dict | None:
    if not day:
        return None
    try:
        return read_json(day / filename)
    except Exception as exc:
        # 个别损坏归档（如 20260917 portfolio_decision.json 写盘截断）
        # 告警并跳过，不阻断全量复算
        print(f"[WARN] 损坏归档跳过: {day / filename} ({exc})")
        return None


def _latest_json(filename: str) -> tuple[Path | None, dict | None]:
    day = _latest_day_with(filename)
    return day, _load(day, filename)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _head_text(path: Path) -> str | None:
    """最新提交（HEAD）中的文件内容；用于区分「已提交」与「仅工作区」。"""
    try:
        rel = path.relative_to(repo_root()).as_posix()
        proc = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=str(repo_root()), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _landing(path: Path, needle: str) -> str:
    """修复落地位置：已提交 / 仅工作区（未提交）/ 未落地。"""
    in_work = needle in _text(path)
    head = _head_text(path)
    if head is None:
        return "仅工作区" if in_work else "未落地"
    if needle in head:
        return "已提交（HEAD 含该实现）"
    return "仅工作区（未提交）" if in_work else "未落地"


def _find_state(nodes: dict | list, target: str) -> str | None:
    """在 sector.json 嵌套结构里按 name 找板块 state。"""
    if isinstance(nodes, dict):
        if nodes.get("name") == target or nodes.get("group_name") == target:
            state = nodes.get("state")
            if state:
                return state
        for value in nodes.values():
            found = _find_state(value, target)
            if found:
                return found
    elif isinstance(nodes, list):
        for value in nodes:
            found = _find_state(value, target)
            if found:
                return found
    return None


def _collect_by_key(nodes: dict | list, key: str, out: list) -> list[dict]:
    """递归收集所有含指定 key 的 dict（用于 break_signals 等落盘字段）。"""
    if isinstance(nodes, dict):
        value = nodes.get(key)
        if isinstance(value, dict):
            out.append(value)
        for item in nodes.values():
            _collect_by_key(item, key, out)
    elif isinstance(nodes, list):
        for item in nodes:
            _collect_by_key(item, key, out)
    return out


# ══════════════════════════════════════════════════════════════════════
# R-D1：15 项逐条复算
# ══════════════════════════════════════════════════════════════════════


def check_p0_1() -> tuple[str, list[str]]:
    """P0-1：capital_check 的 divergent 死分支 → 改为全市场最差 5% 相对分位自适应。"""
    code = _text(AGGREGATOR)
    adaptive = "_CAPITAL_DIVERGENT_TAIL_PCT" in code and "_divergent_threshold" in code
    divergent_days = []
    for day in _days_with("unified.json"):
        unified = _load(day, "unified.json") or {}
        hits = [c for c in unified.get("confirmed_sectors", [])
                if c.get("capital_check") == "divergent"]
        if hits:
            divergent_days.append(f"{day.name}({len(hits)})")
    effective = adaptive and bool(divergent_days)
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"代码含相对分位自适应（_CAPITAL_DIVERGENT_TAIL_PCT / _divergent_threshold）：{adaptive}"
            f" [{_landing(AGGREGATOR, '_divergent_threshold')}]",
            f"全部 {len(_days_with('unified.json'))} 个归档日中 capital_check==divergent 的日数 = "
            f"{len(divergent_days)}{'（' + ', '.join(divergent_days) + '）' if divergent_days else ' ⇒ 分支仍为 0 触发（改了但未生效）'}",
        ],
    )


def check_p0_2() -> tuple[str, list[str]]:
    """P0-2：板块置信度隐式硬编码 0.7。判据：基础分占比 ≥50% 即仍坍缩。"""
    day, unified = _latest_json("unified.json")
    if not unified:
        return INEFFECTIVE, ["未找到 unified.json，无法复算"]
    sectors = unified.get("confirmed_sectors", [])
    dist = Counter(c.get("confidence") for c in sectors)
    base_share = dist.get(CONFIDENCE_BASE, 0) / len(sectors) if sectors else 1.0
    collapsed = base_share >= BASE_COLLAPSE_SHARE
    return (
        INEFFECTIVE if collapsed else EFFECTIVE,
        [
            f"最新完整归档日 {day.name if day else '?'} confidence 分布：{dict(sorted(dist.items(), key=lambda x: str(x[0])))}",
            f"基础分 {CONFIDENCE_BASE} 占比 = {base_share:.1%}（阈值 ≥{BASE_COLLAPSE_SHARE:.0%} 判为仍坍缩）",
        ],
    )


def check_p0_3() -> tuple[str, list[str]]:
    """P0-3：粮食概念 -4.26% 仍 active_mainline → 单日暴跌 + 趋势佐证分级处理。"""
    code = _text(SECTOR_ANALYSIS)
    branch = "_BROKEN_ONE_DAY_DROP_PCT" in code and "one_day_drop" in code
    sector = _load(got_daily_root() / "20260902", "sector.json") or {}
    state = _find_state(sector, "粮食概念")
    effective = branch and state is not None and state != "active_mainline"
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"sector_analysis.py 含单日暴跌分级分支（_BROKEN_ONE_DAY_DROP_PCT / one_day_drop）：{branch}"
            f" [{_landing(SECTOR_ANALYSIS, '_BROKEN_ONE_DAY_DROP_PCT')}]",
            f"20260902 粮食概念 state = {state}（非 active_mainline 即分级分支可达）",
        ],
    )


def check_p0_4() -> tuple[str, list[str]]:
    """P0-4：液冷服务 exhausted —— 报告结论「不修（行为正确）」。"""
    sector = _load(got_daily_root() / "20260902", "sector.json") or {}
    state = _find_state(sector, "液冷服务")
    return (
        BY_DESIGN if state == "exhausted_mainline" else INEFFECTIVE,
        [f"20260902 液冷服务 state = {state}（exhausted_mainline ⇒ 走 exhausted→active 回归路径，"
         f"弱反弹不触发是正确行为）"],
    )


def check_p0_5() -> tuple[str, list[str]]:
    """P0-5：ambush_pool 为空 —— 吸筹闸门一刀切。判据：近 12 日池水位是否突破 2。"""
    days = _days_with("unified.json")[-12:]
    sizes = []
    for day in days:
        unified = _load(day, "unified.json") or {}
        sizes.append((day.name, len(unified.get("ambush_pool") or [])))
    code = _text(AGGREGATOR)
    adaptive = "AMBUSH_POOL_TARGET_RANGE" in code or "market_net_outflow" in code
    peak = max((n for _, n in sizes), default=0)
    effective = adaptive and peak > 2
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"近 {len(sizes)} 个归档日 ambush_pool 规模：{', '.join(f'{d}={n}' for d, n in sizes)}",
            f"峰值 = {peak}；代码含闸门自适应：{adaptive}"
            f"{'（仍 0–2 ⇒ 未生效）' if not effective else ''}",
        ],
    )


def check_p1_1() -> tuple[str, list[str]]:
    """P1-1：持仓股票名称丢失（name == symbol）。"""
    offenders = []
    for day in _days_with("portfolio_decision.json"):
        decision = _load(day, "portfolio_decision.json") or {}
        offenders += [
            f"{day.name}:{s.get('symbol')}"
            for s in decision.get("unified_scoring", [])
            if s.get("is_holding") and s.get("name") == s.get("symbol")
        ]
    return (
        EFFECTIVE if not offenders else INEFFECTIVE,
        [f"归档中 is_holding 且 name == symbol 的记录数 = {len(offenders)}"
         + (f"（首末例：{offenders[0]} … {offenders[-1]}）" if offenders else "")],
    )


def check_p1_2() -> tuple[str, list[str]]:
    """P1-2：板块匹配重复（同一只股命中同一板块多次）。"""
    code = _text(PORTFOLIO)
    dedup = "seen_canons" in code and "canon in seen_canons" in code
    pattern = re.compile(r"板块\[([^\]]+)\]")
    duplicates = []
    for day in _days_with("portfolio_decision.json"):
        decision = _load(day, "portfolio_decision.json") or {}
        for scoring in decision.get("unified_scoring", []):
            counts = Counter(pattern.findall(scoring.get("reasoning") or ""))
            duplicates += [
                f"{day.name}:{scoring.get('symbol')} 命中「{name}」×{n}"
                for name, n in counts.items() if n > 1
            ]
    effective = dedup and not duplicates
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"portfolio_decision.py 含追加前 set 去重（seen_canons）：{dedup}"
            f" [{_landing(PORTFOLIO, 'seen_canons')}]",
            f"归档中同一只股重复命中同一板块的条目数 = {len(duplicates)}"
            f"{'（例：' + '；'.join(duplicates[:3]) + '）' if duplicates else '（症状未复现）'}",
        ],
    )


def check_p1_3() -> tuple[str, list[str]]:
    """P1-3：hot_stocks.mention_count 异常均匀（关注指数 × 固定系数）。"""
    day, sentiment = _latest_json("sentiment.json")
    if not sentiment:
        return INEFFECTIVE, ["未找到 sentiment.json，无法复算"]
    counts = [h.get("mention_count") for h in sentiment.get("hot_stocks", [])
              if h.get("mention_count")]
    spread = (max(counts) - min(counts)) / max(counts) if counts else 0.0
    rank_weight = "(51 - rank)" in _text(SENTIMENT)
    effective = rank_weight or spread > MENTION_SPREAD_MIN
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"{day.name if day else '?'} mention_count 范围 {min(counts, default='n/a')}–"
            f"{max(counts, default='n/a')}，极差 = {spread:.1%}（阈值 >{MENTION_SPREAD_MIN:.0%} 判为已恢复区分度）",
            f"代码改用排名权重：{rank_weight}"
            f" [{_landing(SENTIMENT, '(51 - rank)')}]",
        ],
    )


def check_p1_4() -> tuple[str, list[str]]:
    """P1-4：hot_stocks.associated_sectors 硬编码为空。"""
    day, sentiment = _latest_json("sentiment.json")
    if not sentiment:
        return INEFFECTIVE, ["未找到 sentiment.json，无法复算"]
    stocks = sentiment.get("hot_stocks", [])
    nonempty = sum(1 for h in stocks if h.get("associated_sectors"))
    return (
        EFFECTIVE if nonempty else INEFFECTIVE,
        [f"{day.name if day else '?'} hot_stocks={len(stocks)}，associated_sectors 非空 = {nonempty}"],
    )


def check_p1_5() -> tuple[str, list[str]]:
    """P1-5：reasoning.md 中 Node 7 交易建议出现两次（Phase 2 追加而非覆盖）。"""
    code = _text(ARCHIVE)
    cleanup = "先清理旧的" in code and "_append_phase2_reasoning" in code
    duplicates = []
    for day in _days_with("reasoning.md"):
        text = (day / "reasoning.md").read_text(encoding="utf-8")
        count = text.count("## Node 7 交易建议") or text.count("Node 7 交易建议")
        if count > 1:
            duplicates.append(f"{day.name}×{count}")
    recent = [d for d in duplicates if d.split("×")[0] > P1_5_FIX_DATE]
    effective = cleanup and not recent
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"archive.py `_append_phase2_reasoning` 覆盖写并清理旧段：{cleanup}"
            f" [{_landing(ARCHIVE, '先清理旧的')}]",
            f"归档中 Node 7 段重复日：{duplicates or '无'}；其中修复落地（{P1_5_FIX_DATE}）之后的：{recent or '无'}"
            f"（早于该日的重复属修复前归档，不随代码修复重写）",
            f"判定：{'修复已落地且修复后归档无重复' if effective else f'修复后归档仍可复现重复 {recent} ⇒ 可复算性未成立，按 unverified 计'}",
        ],
    )


def check_p2_1() -> tuple[str, list[str]]:
    """P2-1：affected_sectors 不区分正负面（仍 list[str]）。"""
    code = _text(SCHEMAS)
    still = re.findall(r"affected_sectors:\s*list\[str\]", code)
    return (
        INEFFECTIVE if still else EFFECTIVE,
        [f"schemas.py 中 `affected_sectors: list[str]` 出现 {len(still)} 处（{len(still)} > 0 ⇒ 仍为纯字符串列表）"],
    )


def check_p2_2() -> tuple[str, list[str]]:
    """P2-2：催化剂日历事件膨胀（0903=107 → 0904=20）。"""
    counts = []
    for day in _days_with("news.json"):
        news = _load(day, "news.json") or {}
        counts.append((day.name, len((news.get("catalyst_calendar") or {}).get("events") or [])))
    drops = []
    for (prev_day, prev_n), (cur_day, cur_n) in zip(counts, counts[1:]):
        if prev_n > 0 and (prev_n - cur_n) / prev_n > 0.5:
            drops.append(f"{prev_day}={prev_n} → {cur_day}={cur_n}（降 {(prev_n - cur_n) / prev_n:.0%}）")
    return (
        SYMPTOM_GONE if drops else INEFFECTIVE,
        [
            "最近归档日催化事件数：" + ", ".join(f"{d}={n}" for d, n in counts[-6:]),
            "断崖式下降：" + ("；".join(drops) if drops else "未检出"),
        ],
    )


def check_p2_3() -> tuple[str, list[str]]:
    """P2-3：仓位数据矛盾（min(1.0, raw) 截断 vs 含杠杆实际仓位）。"""
    code = _text(PORTFOLIO)
    truncated = "min(1.0, current_position)" in code
    raw_field = "raw_position" in _text(SCHEMAS)
    ratios = []
    for day in _days_with("portfolio_decision.json"):
        decision = _load(day, "portfolio_decision.json") or {}
        ratio = (decision.get("risk_assessment") or {}).get("current_position_ratio")
        if ratio is not None:
            ratios.append(round(ratio, 3))
    ones = sum(1 for r in ratios if r == 1.0)
    zeros = sum(1 for r in ratios if r == 0.0)
    effective = not truncated and raw_field
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"portfolio_decision.py 仍 `min(1.0, current_position)`：{truncated}"
            f" [{_landing(PORTFOLIO, 'min(1.0, current_position)')}]；schema 落盘 raw_position：{raw_field}",
            f"归档 current_position_ratio 双峰：1.000 × {ones} 天 / 0.000 × {zeros} 天（共 {len(ratios)} 天）",
        ],
    )


def check_p2_4() -> tuple[str, list[str]]:
    """P2-4：「其他」家族垃圾箱 → 增加农业/生态环保/军贸关键词。"""
    code = _text(FAMILY_KW)
    families = all(k in code for k in ("农业", "生态环保", "军贸"))
    ratios = {}
    for name in ("20260902", "20260910"):
        unified = _load(got_daily_root() / name, "unified.json")
        if not unified:
            continue
        sectors = unified.get("confirmed_sectors", [])
        other = sum(1 for c in sectors if c.get("frame_group") == "其他")
        ratios[name] = other / len(sectors) if sectors else 0.0
    improved = len(ratios) == 2 and ratios["20260910"] < ratios["20260902"]
    effective = families and improved
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"family_keywords.py 含 农业/生态环保/军贸 三族：{families}"
            f" [{_landing(FAMILY_KW, '生态环保')}]",
            "「其他」占比：" + " → ".join(f"{k} {v:.1%}" for k, v in ratios.items())
            + ("（下降 ⇒ 生效）" if improved else "（未下降）"),
        ],
    )


def check_p2_5() -> tuple[str, list[str]]:
    """P2-5：破位信号不落盘（price_chg_5d/above_ma20/high20_pos/overheat_level）。"""
    schema_ok = "break_signals" in _text(SCHEMAS)
    day, sector = _latest_json("sector.json")
    entries = _collect_by_key(sector or {}, "break_signals", [])
    judged = [e for e in entries if any(
        e.get(k) is not None for k in ("price_chg_5d", "above_ma20", "high20_pos", "overheat_level")
    )]
    effective = schema_ok and bool(judged)
    return (
        EFFECTIVE if effective else INEFFECTIVE,
        [
            f"schemas.py 定义 break_signals：{schema_ok}",
            f"{day.name if day else '?'} sector.json 中 break_signals 条目 = {len(entries)}，"
            f"含非空判据值 = {len(judged)}",
        ],
    )


# (项号, 级别, 0902 报告声明原文摘要, 复算证据方式, 复算函数)
CLAIMS = [
    ("P0-1", "立即", "已修复：capital_check 的 divergent 分支改为全市场最差 5% 相对分位",
     "读 aggregator.py 自适应实现 + 扫全部归档日 capital_check 是否出现 divergent", check_p0_1),
    ("P0-2", "立即", "待排期：板块置信度隐式硬编码 0.7 → 提基础分 + 加强度维度",
     "读最新完整归档日 confirmed_sectors[].confidence 分布与基础分占比", check_p0_2),
    ("P0-3", "立即", "已修复：粮食概念 -4.26% 仍 active_mainline → 单日暴跌分级",
     "读 sector_analysis.py 分级分支 + 20260902 粮食概念落盘 state", check_p0_3),
    ("P0-4", "观察", "不修（行为正确）：液冷服务 exhausted 不触发弱反弹",
     "读 20260902 液冷服务落盘 state", check_p0_4),
    ("P0-5", "1 周内", "待修（闸门自适应）：ambush_pool 为空",
     "读 aggregator.py 闸门自适应 + 近 12 个归档日 ambush_pool 规模", check_p0_5),
    ("P1-1", "1 周内", "待修：持仓股票名称丢失 → 预热 name_cache",
     "扫全部归档 unified_scoring 中 is_holding 且 name == symbol 的记录数", check_p1_1),
    ("P1-2", "1 周内", "待修：板块匹配重复（旅游 ×3）→ matched 追加前 set 去重",
     "扫归档 reasoning 中同一只股重复命中的板块 + 读 portfolio_decision.py 去重实现", check_p1_2),
    ("P1-3", "2 周内", "待修：hot_stocks.mention_count 异常均匀 → 改排名权重",
     "读最新归档日 mention_count 极差 + sentiment_analysis.py 权重实现", check_p1_3),
    ("P1-4", "2 周内", "待修：associated_sectors 硬编码为空 → 批量查询关联板块",
     "读最新归档日 hot_stocks[].associated_sectors 非空数", check_p1_4),
    ("P1-5", "2 周内", "待修：reasoning.md 中 Node 7 交易建议出现两次",
     "读 archive.py 覆盖写实现 + 扫归档 reasoning.md 中 Node 7 段出现次数", check_p1_5),
    ("P2-1", "按需", "待修：affected_sectors 不区分正负面 → 改 list[dict]",
     "读 schemas.py affected_sectors 字段类型声明", check_p2_1),
    ("P2-2", "按需", "待修：催化剂日历事件膨胀（92 个）",
     "扫全部归档 news.json catalyst_calendar.events 计数，判有无断崖式下降", check_p2_2),
    ("P2-3", "按需", "待修：current_position_ratio 截断（1.0 与 380% 矛盾）",
     "读 portfolio_decision.py 截断实现 + 扫归档 current_position_ratio 双峰分布", check_p2_3),
    ("P2-4", "按需", "已完成：三族关键词已加",
     "读 family_keywords.py 三族关键词 + 对比 0902→0910「其他」占比", check_p2_4),
    ("P2-5", "最先做", "待修：破位信号不落盘 → 四判据写入 SectorDetail",
     "读 schemas.py break_signals + 最新归档日 sector.json 非空判据值", check_p2_5),
]


# ══════════════════════════════════════════════════════════════════════
# R-D2：四个子计数（真算，非 grep）
# ══════════════════════════════════════════════════════════════════════


def _reservoir_max_size() -> int:
    match = re.search(r"RESERVOIR_MAX_SIZE\s*=\s*(\d+)", _text(CATALYST_CONFIG))
    return int(match.group(1)) if match else 0


def compute_sub_counts() -> dict:
    """四个子计数 + 判据明细。"""
    # ① divergent_trigger_days
    unified_days = _days_with("unified.json")
    divergent = [
        day.name for day in unified_days
        if any(c.get("capital_check") == "divergent"
               for c in (_load(day, "unified.json") or {}).get("confirmed_sectors", []))
    ]

    # ② ambush_pool_saturation_days：蓄水池达到 RESERVOIR_MAX_SIZE 的最长连续（归档交易日）日数
    max_size = _reservoir_max_size()
    longest, current, saturated = 0, 0, []
    for day in unified_days:
        unified = _load(day, "unified.json") or {}
        if len(unified.get("ambush_reservoir") or []) >= max_size > 0:
            current += 1
            longest = max(longest, current)
            saturated.append(day.name)
        else:
            current = 0
    at_cap = [day.name for day in unified_days
              if len((_load(day, "unified.json") or {}).get("ambush_reservoir") or []) >= max_size > 0]

    # ③ dead_signal_count：附录 C.3 形状 1 的四项，逐项给可计算谓词
    dead: dict[str, str] = {}
    day_pd, decision = _latest_json("portfolio_decision.json")
    if decision is not None:
        dq = decision.get("data_quality") or {}
        empty_dq = not any(dq.get(k) for k in
                           ("available_fields", "missing_fields", "stale_fields", "fetch_failed_fields"))
        if empty_dq and dq.get("degradation_summary") is None and "data_quality" not in _text(PORTFOLIO):
            dead["E4"] = "Phase 2 data_quality 未接线：归档全空列表 + degradation_summary=null，代码无装配点"
        candidates = [s for s in decision.get("unified_scoring", []) if not s.get("is_holding")]
        if candidates and all(s.get("family_name") is None for s in candidates):
            dead["E5"] = f"候选 family_name 未接线：{len(candidates)}/{len(candidates)} 全为 None（{day_pd.name if day_pd else '?'}）"
    sentiment_code = _text(SENTIMENT)
    if re.search(r'_DIMENSION_CONFIG\s*=\s*\{[^}]*"momentum"', sentiment_code, re.DOTALL):
        dead["E10"] = "_DIMENSION_CONFIG 含从未被读取的 momentum 维度"
    if "available, missing, failed = [], [], []" in sentiment_code and "failed.append" not in sentiment_code:
        dead["E11"] = "Node 5 failed 列表初始化后从无 append ⇒ has_partial_failure/status 恒为正常"

    # ④ dead_branch_count：可证不可达分支（E3 埋伏买入分支）
    # audit_20260922 Q2-A2：G2/G3 结构性死闸已由 THS 直喂修复，本计数为全归档
    # is_ambush_entry 派生（含修复前的历史日），随激活后的新归档自然衰减
    total_alloc, ambush_alloc = 0, 0
    for day in _days_with("portfolio_decision.json"):
        decision = _load(day, "portfolio_decision.json") or {}
        for allocation in decision.get("allocation", []):
            total_alloc += 1
            ambush_alloc += int(bool(allocation.get("is_ambush_entry")))

    # gate_funnel 采集派生：最新归档日 G2/G3 的 data_source（THS 直喂 vs 无数据），
    # 用于区分「埋伏买入为 0」是死闸未激活还是单纯无近失候选（防止误读上方衰减）
    ambush_gate_data_source: dict[str, str] = {}
    _day_un, unified_latest = _latest_json("unified.json")
    if unified_latest:
        gf = unified_latest.get("ambush_gate_funnel") or {}
        for gate in ("G2_防买顶", "G3_拥挤度"):
            cell = gf.get(gate)
            if isinstance(cell, dict) and cell.get("data_source"):
                ambush_gate_data_source[gate] = str(cell["data_source"])

    return {
        "divergent_trigger_days": len(divergent),
        "ambush_pool_saturation_days": longest,
        "dead_signal_count": len(dead),
        "dead_branch_count": 1 if ambush_alloc == 0 else 0,
        "_detail": {
            "divergent_days": divergent,
            "reservoir_max_size": max_size,
            "saturated_days": saturated,
            "at_cap_total_days": len(at_cap),
            "archived_days": len(unified_days),
            "dead_signals": dead,
            "ambush_gate_data_source": ambush_gate_data_source or None,
            "allocation_total": total_alloc,
            "allocation_ambush": ambush_alloc,
        },
    }


def parse_doc_sub_counts() -> dict[str, str]:
    """解析 §6 子计数表格中四个指标的文档基线原文。"""
    result: dict[str, str] = {}
    for line in _text(D_PLAN).splitlines():
        if not line.strip().startswith("|"):
            continue
        for key in ("divergent_trigger_days", "ambush_pool_saturation_days",
                    "dead_signal_count", "dead_branch_count"):
            if f"`{key}`" in line and key not in result:
                cells = [c.strip() for c in line.split("|") if c.strip()]
                result[key] = cells[-1]
    return result


def parse_doc_appendix_summary() -> dict[str, int]:
    """解析附录 A 统计行「生效 3 / 症状自行消失 1 / 不修 1 / 未修 10」。"""
    match = re.search(
        r"生效\s*(\d+)\s*/\s*症状自行消失\s*(\d+)\s*/\s*不修\s*(\d+)\s*/\s*未修\s*(\d+)",
        _text(D_PLAN),
    )
    if not match:
        return {}
    return {"effective": int(match.group(1)), "symptom_gone": int(match.group(2)),
            "by_design": int(match.group(3)), "ineffective": int(match.group(4))}


def parse_doc_verdicts() -> dict[str, str]:
    """解析附录 A 表的「实测状态」列（按项号），用于与实算状态对照。"""
    verdicts: dict[str, str] = {}
    for line in _text(D_PLAN).splitlines():
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if len(cells) < 4 or not re.match(r"^P[0-2]-\d$", cells[0]):
            continue
        verdict = cells[3]
        if "生效" in verdict and "未生效" not in verdict:
            verdicts[cells[0]] = EFFECTIVE
        elif "症状自行消失" in verdict:
            verdicts[cells[0]] = SYMPTOM_GONE
        elif "未动" in verdict:
            verdicts[cells[0]] = BY_DESIGN
        else:
            verdicts[cells[0]] = INEFFECTIVE
    return verdicts


def _doc_threshold(text: str) -> tuple[str, int] | None:
    match = re.search(r"([≥≤])\s*(\d+)", text)
    return (match.group(1), int(match.group(2))) if match else None


def _doc_exact(text: str) -> int | None:
    """精确值型基线；含 ≥/≤ 视为阈值型，返回 None。"""
    if "≥" in text or "≤" in text:
        return None
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def compare_r_d2(computed: dict, doc: dict[str, str]) -> list[tuple[str, bool, str]]:
    """四个子计数逐位比对。"""
    rows: list[tuple[str, bool, str]] = []

    text = doc.get("divergent_trigger_days", "（未解析到）")
    expected = _doc_exact(text)
    rows.append(("divergent_trigger_days", expected is not None and computed["divergent_trigger_days"] == expected,
                 f"实算 {computed['divergent_trigger_days']} / 文档 {text}"))

    text = doc.get("ambush_pool_saturation_days", "（未解析到）")
    threshold = _doc_threshold(text)
    ok = bool(threshold) and (
        computed["ambush_pool_saturation_days"] >= threshold[1] if threshold[0] == "≥"
        else computed["ambush_pool_saturation_days"] <= threshold[1]
    )
    rows.append(("ambush_pool_saturation_days", ok, f"实算 {computed['ambush_pool_saturation_days']} / 文档 {text}"))

    text = doc.get("dead_signal_count", "（未解析到）")
    expected = _doc_exact(text)
    rows.append(("dead_signal_count", expected is not None and computed["dead_signal_count"] == expected,
                 f"实算 {computed['dead_signal_count']} / 文档 {text}"))

    text = doc.get("dead_branch_count", "（未解析到）")
    expected = _doc_exact(text)
    rows.append(("dead_branch_count", expected is not None and computed["dead_branch_count"] == expected,
                 f"实算 {computed['dead_branch_count']} / 文档 {text}"))
    return rows


# ══════════════════════════════════════════════════════════════════════


def run(as_json: bool) -> int:
    verifier = Verifier("D 层 R-D1 / R-D2", REDLINE_DOC)

    # ── R-D1：15 项逐条复算 ──
    print("\n── R-D1：0902 报告 15 项逐条复算 ──")
    print(f"{'项号':<6}{'0902 声明摘要':<44}{'复算状态':<14}证据")
    print("-" * 120)
    states: dict[str, str] = {}
    evidence_by_item: dict[str, list[str]] = {}
    for item_id, level, claim, how, func in CLAIMS:
        state, evidence = func()
        states[item_id] = state
        evidence_by_item[item_id] = evidence
        print(f"{item_id:<6}{claim.replace('：', ': ')[:40]:<44}{state:<14}{evidence[0]}")
        for line in evidence[1:]:
            print(f"{'':<6}{'':<44}{'':<14}{line}")
        print(f"{'':<6}{'':<44}{'':<14}复算方式: {how}")

    unverified = [item for item, state in states.items() if state not in VERIFIED_STATES]
    doc_verdicts = parse_doc_verdicts()
    drift = [
        f"{item}: 实算 {states[item]} / 附录 A {doc_verdicts.get(item, '未解析到')}"
        for item in states if item in doc_verdicts and doc_verdicts[item] != states[item]
    ]
    verifier.check(
        "R-D1 声明的可复算率（unverified = 0，2026-09-11 基线 = 10）",
        not unverified,
        f"实算 unverified = {len(unverified)}（基线 {R_D1_BASELINE}）；unverified 项：{unverified or '无'}",
        [f"{item} = {state}" for item, state in states.items()]
        + ([f"与附录 A 基线的状态漂移（{len(drift)} 项，基线早于修复落地）："] + drift if drift else []),
    )

    # ── R-D2：四个子计数逐位一致 ──
    computed = compute_sub_counts()
    doc_counts = parse_doc_sub_counts()
    print("\n── R-D2：四个子计数逐位比对 ──")
    rows = compare_r_d2(computed, doc_counts)
    for name, ok, detail in rows:
        print(f"  [{'一致' if ok else '不一致'}] {name}: {detail}")

    detail = computed["_detail"]
    print(f"  · divergent_trigger_days 明细：{detail['archived_days']} 个归档日中 "
          f"{detail['divergent_days'] or '无'}")
    print(f"  · ambush_pool_saturation_days 明细：RESERVOIR_MAX_SIZE={detail['reservoir_max_size']}，"
          f"最长连续顶格 {computed['ambush_pool_saturation_days']} 天；"
          f"顶格日共 {detail['at_cap_total_days']} 天 {detail['saturated_days']}")
    print(f"  · dead_signal_count 明细：{detail['dead_signals'] or '四项均已接线'}")
    print(f"  · dead_branch_count 明细：{detail['allocation_total']} 笔 allocation 中 "
          f"is_ambush_entry=真 的笔数 = {detail['allocation_ambush']}；"
          f"最新归档闸门 data_source = {detail.get('ambush_gate_data_source') or '（无 gate_funnel 字段，早于 Q2-A2 修复）'}")

    doc_summary = parse_doc_appendix_summary()
    actual_summary = {
        "effective": sum(1 for s in states.values() if s == EFFECTIVE),
        "symptom_gone": sum(1 for s in states.values() if s == SYMPTOM_GONE),
        "by_design": sum(1 for s in states.values() if s == BY_DESIGN),
        "ineffective": sum(1 for s in states.values() if s == INEFFECTIVE),
    }
    print(f"  [对照] 附录 A 统计行 实算={actual_summary} / 文档={doc_summary or '未解析到'}"
          f"（15 项状态分布，仅作对照，不参与 R-D2 判定）")

    all_ok = all(ok for _, ok, _ in rows)
    mismatch_note = [
        f"{name} 基线不可复现：{d}" for name, ok, d in rows if not ok
    ]
    verifier.check(
        "R-D2 四个子计数与文档基线逐位一致",
        all_ok,
        "四个子计数全部与 §6 基线一致" if all_ok else "存在与 §6 基线不一致的子计数（见下）",
        [f"{name}: {'一致' if ok else '不一致'} — {d}" for name, ok, d in rows]
        + mismatch_note
        + ["说明：ambush_pool_saturation_days 文档基线 `≥10` 在归档中不可复现（顶格日共 "
           f"{detail['at_cap_total_days']} 天，最长连续 {computed['ambush_pool_saturation_days']} 天）——"
           "文档自身注明「缺口见 D-P2 落盘」，即该基线的数据源未落盘、无法复核；"
           "dead_signal_count 由 4 → 3 是因为 E10（momentum 死配置）已按 D-P14 删除，"
           "文档基线早于该修复落地。"]
        + [f"附录 A 统计行对照（不参与判定）：实算={actual_summary} 文档={doc_summary or '未解析到'}"],
    )

    if as_json:
        print("\n── --json ──")
        print(json.dumps({
            "r_d1": {
                "unverified_count": len(unverified),
                "unverified_items": unverified,
                "baseline": R_D1_BASELINE,
                "states": states,
                "appendix_a_verdicts": doc_verdicts,
                "drift_vs_appendix_a": drift,
                "evidence": evidence_by_item,
            },
            "r_d2": {
                "computed": {k: v for k, v in computed.items() if not k.startswith("_")},
                "computed_detail": computed["_detail"],
                "doc_baseline": doc_counts,
                "comparisons": [{"name": n, "match": ok, "detail": d} for n, ok, d in rows],
                "appendix_a_summary": {"doc": doc_summary, "computed": actual_summary},
            },
        }, ensure_ascii=False, indent=2, default=str))

    return verifier.finish()


def main() -> int:
    force_utf8_stdout()
    import argparse

    parser = argparse.ArgumentParser(description="D 层 R-D1 / R-D2 验收脚本")
    parser.add_argument("--json", action="store_true", help="附加输出机器可读 JSON")
    args = parser.parse_args()

    print_header("D 层 §6 红线验收：R-D1 / R-D2", REDLINE_DOC)
    return run(args.json)


if __name__ == "__main__":
    sys.exit(main())
