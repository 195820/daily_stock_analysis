# -*- coding: utf-8 -*-
"""A 层（运行模型）验收红线：R1–R6 —— 同日两跑状态一致性。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
红线原文（`docs/got/archive/got_run_model_plan_0911.md` §6.1「六条」）

| # | 指标 | 达标线 | 数据源 | 可度量 |
|---|---|---|---|---|
| **R1** | **同日两跑状态一致性**：同一天连跑两次，**状态字段**逐字节一致 | 100% 一致 | 见 6.2 字段清单 | ✅ |
| **R2** | **自引用修复的可观测标志**：出现 `previous_regime != regime` 的归档日 | **≥ 1 天**（下一次真实 regime 切换日） | `unified.json` `market_regime` | ✅ |
| **R3** | **事件计数不翻倍**：同日两跑后 `mention_count` / `appearance_count` 相对单跑不增 | 不增 | DB `node4_events` / `node4_raw_news` | ✅ |
| **R4** | **EMA 计数按日**：同日两跑后 `sample_count` 不变、`daily_scores` 键数不变 | 不变 | `ema_baseline.json` | ✅ |
| **R5** | **信号日期无同日重复**：`signal_dates` 中同 `(type, date)` 至多 1 条 | 0 重复 | `monitoring.json` | ✅ |
| **R6** | **状态源清单逐项复测**：§2.2 W1–W6 + §2.1 D1–D9 各跑一次同日两跑验证 | 全部幂等 | §6.2 脚本 | ✅ |

§6.3 手具（同文档）：

    | 手具 | 覆盖 | 命令 |
    | `scripts/verify_same_day_idempotency.py` | R1–R6：同日实跑两次，按 §6.2 清单提取状态字段并 diff，
      输出 A-P0-1…A-P0-9 逐项 PASS/FAIL | `python main.py --got` ×2 |

    并明确「**回放不能替代同日两跑**：`replay_mode` 显式跳过跨日状态写（`aggregator.py:193-194`），
    重建的是规则侧，**不含状态推进** —— 而 R1–R6 验的正是状态推进」。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
实现方式
    - R1 / R3 / R4：**必须两个同日快照**。用 `--runs 快照A 快照B` 传入两个同日快照根目录
      （各含 `YYYYMMDD/` 六个节点 JSON + `aggregator_contexts/ema_baseline.json`
      + `got_timeseries/monitoring.json` + `stock_analysis.db`），脚本按 §6.2 字段清单逐字段 diff。
      未传目录 → 显式 SKIP 并打印产出快照的命令，绝不用静态检查冒充。
    - R2 / R5：**不需要同日两跑**，直接用现有归档 / `monitoring.json` 真算。
    - R6：W1–W6 与 D1–D9 共 15 个状态源需逐源实跑复测，脚本给出清单与所需样本，
      无样本即 SKIP（不做任何替代性判定）。

    ⚠️ §6.2 明令「**不是全文件 diff**」：`data_quality.timestamp`、`meta.*` 时间戳、
      `reasoning` / `reasoning.md` / `hot_topics` 等 LLM 派生字段天然变化，不参与比较。

    ⚠️ 脚本末尾的 6 项代码形状检查为**静态辅助检查**，它们**不等于 R1–R6 达标**
      （R1–R6 验的是状态推进，静态检查只能看代码是否具备幂等性意图），不参与退出码裁决。

退出码：全 PASS 且无 SKIP = 0；有 FAIL = 1；有 SKIP 且无 FAIL = 2。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_common import (  # noqa: E402
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIP,
    Verifier,
    force_utf8_stdout,
    got_daily_root,
    iter_archive_days,
    print_header,
    read_json,
    repo_root,
)

REDLINE_DOC = "docs/got/archive/got_run_model_plan_0911.md §6"

SRC_GOT = repo_root() / "src" / "got"
EMA_BASELINE = repo_root() / "data" / "aggregator_contexts" / "ema_baseline.json"
MONITORING = repo_root() / "data" / "got_timeseries" / "monitoring.json"
DB_PATH = repo_root() / "data" / "stock_analysis.db"

DAY_JSONS = ("unified.json", "sentiment.json", "market.json", "sector.json")
SECTOR_COUNTER_KEYS = (
    "mainline_days", "ambush_days", "wave_days", "wave_start_date",
    "strength_trajectory", "dwell_days", "exit_pressure_days",
    # 连续相位（P2 前置底座，2026-11-05）：同日重跑必须逐字段相同；
    # lifecycle 嵌套块另由 `_collect_sector_counters` 展开为 `lifecycle.<field>`
    "lifecycle_continuous_phase", "lifecycle_anchor_date", "lifecycle_phase_age",
    "lifecycle_phase_dwell", "lifecycle_healthy_days", "lifecycle_weak_days",
    "lifecycle_peak_close", "lifecycle_last_observation_date",
    "lifecycle_transition_reason", "lifecycle_data_status", "lifecycle_time_weight",
)
R2_SWITCH_HINT = "下一次真实 regime 切换日"

TWO_RUN_COMMAND = (
    "① 跑前快照：cp -r data/got_daily/<date> data/_run1 && "
    "cp data/aggregator_contexts/ema_baseline.json data/_run1/ && "
    "cp data/got_timeseries/monitoring.json data/_run1/ && cp data/stock_analysis.db data/_run1/\n"
    "        ② 第二次同日运行：python main.py --got（同一天再跑一次，勿清空归档）\n"
    "        ③ 跑后快照：同法导出 data/_run2\n"
    "        ④ python scripts/verify_same_day_idempotency.py --runs data/_run1 data/_run2"
)


# ══════════════════════════════════════════════════════════════════════
# 快照定位与 §6.2 状态字段提取
# ══════════════════════════════════════════════════════════════════════


def _locate(root: Path, filename: str) -> Path | None:
    """在快照根目录内定位文件：根下 → 一层子目录 → 递归。"""
    candidates = [root / filename, root / "data" / filename]
    for sub in ("got_daily", "aggregator_contexts", "got_timeseries"):
        candidates.append(root / sub / filename)
        candidates.append(root / "data" / sub / filename)
    for path in candidates:
        if path.exists():
            return path
    hits = sorted(root.rglob(filename))
    return hits[0] if hits else None


def _day_dir(root: Path) -> Path | None:
    """快照内的归档日目录：根本身即日目录，或根下唯一的 YYYYMMDD 子目录。"""
    if (root / "unified.json").exists():
        return root
    days = sorted(p for p in root.rglob("*") if p.is_dir() and re.match(r"^\d{8}$", p.name))
    return days[0] if days else None


def _collect_sector_counters(nodes: dict | list, out: dict[str, dict]) -> dict[str, dict]:
    """收集 sector.json 中带跨日计数器的板块条目，按板块名索引。"""
    if isinstance(nodes, dict):
        if any(k in nodes for k in SECTOR_COUNTER_KEYS):
            key = nodes.get("name") or nodes.get("group_name") or nodes.get("code")
            if key:
                out.setdefault(str(key), {k: nodes.get(k) for k in SECTOR_COUNTER_KEYS if k in nodes})
        # 归档里连续相位是 `lifecycle` 嵌套对象（LifecycleInfo），逐字段展开后比对
        _lifecycle = nodes.get("lifecycle")
        if isinstance(_lifecycle, dict):
            key = nodes.get("name") or nodes.get("group_name") or nodes.get("code")
            if key:
                bucket = out.setdefault(str(key), {})
                for _field, _value in _lifecycle.items():
                    bucket[f"lifecycle.{_field}"] = _value
        for value in nodes.values():
            _collect_sector_counters(value, out)
    elif isinstance(nodes, list):
        for value in nodes:
            _collect_sector_counters(value, out)
    return out


def _extract_day_state(root: Path) -> tuple[dict, list[str]]:
    """按 §6.2 清单提取单次运行的日级状态字段（缺失来源记入 missing）。"""
    state: dict = {}
    missing: list[str] = []
    day = _day_dir(root)
    if day is None:
        return state, [f"{root} 下未找到归档日目录（应含 {'/'.join(DAY_JSONS)}）"]

    unified = read_json(day / "unified.json")
    if unified is None:
        missing.append("unified.json")
    else:
        regime = unified.get("market_regime") or {}
        for key in ("regime", "dwell_days", "previous_regime", "transition_pending", "transition_count"):
            state[f"unified.market_regime.{key}"] = regime.get(key)
        for item in unified.get("ambush_reservoir") or []:
            name = item.get("sector_name") or item.get("board_code")
            for field in ("reason", "entered_date", "days_in_reservoir"):
                state[f"unified.ambush_reservoir[{name}].{field}"] = item.get(field)

    sentiment = read_json(day / "sentiment.json")
    if sentiment is None:
        missing.append("sentiment.json")
    else:
        stage = sentiment.get("stage_state") or {}
        for key in ("current_stage", "dwell_days", "previous_stage", "stage_transitions"):
            state[f"sentiment.stage_state.{key}"] = stage.get(key)
        state["sentiment.sentiment_momentum"] = sentiment.get("sentiment_momentum")

    if read_json(day / "market.json") is None:
        missing.append("market.json")

    sector = read_json(day / "sector.json")
    if sector is None:
        missing.append("sector.json")
    else:
        for name, counters in _collect_sector_counters(sector, {}).items():
            for field, value in counters.items():
                state[f"sector[{name}].{field}"] = value

    ema_path = _locate(root, "ema_baseline.json")
    if ema_path is None:
        missing.append("ema_baseline.json")
    else:
        ema = read_json(ema_path) or {}
        state["ema_baseline.sample_count"] = ema.get("sample_count")
        state["ema_baseline.score_std"] = ema.get("score_std")
        state["ema_baseline.daily_scores_keys"] = sorted((ema.get("daily_scores") or {}).keys())

    monitoring_path = _locate(root, "monitoring.json")
    if monitoring_path is None:
        missing.append("monitoring.json")
    else:
        monitoring = read_json(monitoring_path) or {}
        cross_node = monitoring.get("cross_node") or {}
        state["monitoring.cross_node.signal_dates"] = [
            (s.get("type"), s.get("date")) for s in cross_node.get("signal_dates") or []
        ]
        node5 = monitoring.get("node5") or {}
        state["monitoring.node5.contrarian_alert_dates"] = sorted(
            str(d) for d in node5.get("contrarian_alert_dates") or []
        )

    db_path = _locate(root, "stock_analysis.db")
    if db_path is None:
        missing.append("stock_analysis.db（node4_events / node4_raw_news / ambush_position_snapshots）")
    else:
        state.update(_extract_db_state(db_path))

    if not state:
        missing.append("未提取到任何 §6.2 状态字段")
    return state, missing


def _extract_db_state(db_path: Path) -> dict:
    """DB 侧状态：事件计数 + 快照行数（只读打开，绝不写）。"""
    state: dict = {}
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {"db.error": f"无法只读打开 {db_path}"}
    try:
        for table, key, column in (
            ("node4_events", "event_id", "mention_count"),
            ("node4_raw_news", "title_normalized", "appearance_count"),
        ):
            try:
                rows = conn.execute(f"SELECT {key}, {column} FROM {table}").fetchall()
            except sqlite3.Error as exc:
                state[f"db.{table}.error"] = str(exc)
                continue
            state[f"db.{table}.total"] = sum(int(r[1] or 0) for r in rows)
            state[f"db.{table}.row_count"] = len(rows)
            state[f"db.{table}.peak"] = max((int(r[1] or 0) for r in rows), default=0)
        try:
            state["db.ambush_position_snapshots.row_count"] = conn.execute(
                "SELECT COUNT(*) FROM ambush_position_snapshots"
            ).fetchone()[0]
        except sqlite3.Error as exc:
            state["db.ambush_position_snapshots.error"] = str(exc)
    finally:
        conn.close()
    return state


def _diff_states(state_a: dict, state_b: dict) -> list[str]:
    diffs: list[str] = []
    for key in sorted(set(state_a) | set(state_b)):
        a, b = state_a.get(key, "<缺失>"), state_b.get(key, "<缺失>")
        if a != b:
            diffs.append(f"{key}: A={_short(a)} → B={_short(b)}")
    return diffs


def _short(value, limit: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[:limit] + "…"


# ══════════════════════════════════════════════════════════════════════
# R1 / R3 / R4：同日两跑快照 diff
# ══════════════════════════════════════════════════════════════════════


def check_r1(verifier: Verifier, runs: list[str] | None) -> None:
    name = "R1 同日两跑状态字段逐字节一致（§6.2 清单，100%）"
    if not runs:
        verifier.skip(name, "未提供两个同日快照目录（--runs A B）",
                      f"需先产出两个同日快照：{TWO_RUN_COMMAND}")
        return
    root_a, root_b = Path(runs[0]), Path(runs[1])
    state_a, missing_a = _extract_day_state(root_a)
    state_b, missing_b = _extract_day_state(root_b)
    diffs = _diff_states(state_a, state_b)
    missing = sorted(set(missing_a) | set(missing_b))
    evidence = [f"比对字段数 = {len(set(state_a) | set(state_b))}；差异 = {len(diffs)}"]
    if diffs:
        evidence += [f"差异：{d}" for d in diffs[:20]]
    if missing:
        evidence.append(f"缺失来源（无法证明 100% 一致）：{missing}")
    if diffs:
        verifier.add(name, STATUS_FAIL, f"{len(diffs)} 个状态字段不一致", evidence)
    elif missing:
        verifier.add(name, STATUS_SKIP,
                     f"已比对 {len(set(state_a) | set(state_b))} 个字段全部一致，"
                     f"但 {len(missing)} 项来源缺失 → 无法判定 100% 一致", evidence)
    else:
        verifier.add(name, STATUS_PASS, "全部 §6.2 状态字段逐字节一致", evidence)


def check_r3(verifier: Verifier, runs: list[str] | None) -> None:
    name = "R3 事件计数不翻倍（mention_count / appearance_count 相对单跑不增）"
    if not runs:
        evidence = [
            "判定需要两个同日跑后的 DB 快照（node4_events / node4_raw_news）才可比较「不增」",
            f"前置条件：{TWO_RUN_COMMAND}",
        ] + _live_db_symptom(DB_PATH)
        verifier.add(name, STATUS_SKIP,
                     "SKIP 原因：未提供两个同日快照目录（--runs A B）", evidence)
        return
    state_a, _ = _extract_day_state(Path(runs[0]))
    state_b, _ = _extract_day_state(Path(runs[1]))
    rows = []
    increased = []
    for table in ("node4_events", "node4_raw_news"):
        for metric in ("total", "peak", "row_count"):
            key = f"db.{table}.{metric}"
            if key not in state_a or key not in state_b:
                continue
            a, b = state_a[key], state_b[key]
            rows.append(f"{key}: A={a} → B={b}")
            if b > a:
                increased.append(key)
    if not rows:
        verifier.skip(name, "两个快照内都没有可读的 node4_events / node4_raw_news",
                      f"前置条件：{TWO_RUN_COMMAND}")
        return
    verifier.check(name, not increased,
                   "同日两跑后事件计数均未增加" if not increased else f"计数增加：{increased}",
                   rows)


def _live_db_symptom(db_path: Path) -> list[str]:
    """现行 DB 中「同日重复计入」的症状（仅作旁证，非 R3 判据）。"""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM node4_events WHERE first_seen = last_seen AND mention_count > 1"
            ).fetchone()[0]
            raw = conn.execute(
                "SELECT COUNT(*) FROM node4_raw_news WHERE first_seen_date = last_seen_date "
                "AND appearance_count > 1"
            ).fetchone()[0]
        except sqlite3.Error as exc:
            return [f"现行 DB 症状查询失败：{exc}"]
    finally:
        conn.close()
    return [f"旁证（非判据）：现行 DB 中同日重复计入行数 — node4_events {rows} 行 / node4_raw_news {raw} 行"]


def check_r4(verifier: Verifier, runs: list[str] | None) -> None:
    name = "R4 EMA 计数按日（sample_count 不变、daily_scores 键数不变）"
    if not runs:
        verifier.skip(name, "未提供两个同日快照目录（--runs A B）",
                      f"需两个快照内的 aggregator_contexts/ema_baseline.json：{TWO_RUN_COMMAND}")
        return
    rows, bad = [], []
    for root in (Path(runs[0]), Path(runs[1])):
        path = _locate(root, "ema_baseline.json")
        if path is None:
            verifier.skip(name, f"快照 {root} 内未找到 ema_baseline.json",
                          f"前置条件：{TWO_RUN_COMMAND}")
            return
    ema_a = read_json(_locate(Path(runs[0]), "ema_baseline.json")) or {}
    ema_b = read_json(_locate(Path(runs[1]), "ema_baseline.json")) or {}
    for key, getter in (
        ("sample_count", lambda e: e.get("sample_count")),
        ("score_std", lambda e: e.get("score_std")),
        ("daily_scores 键数", lambda e: len((e.get("daily_scores") or {}))),
    ):
        a, b = getter(ema_a), getter(ema_b)
        rows.append(f"{key}: A={a} → B={b}")
        if a != b:
            bad.append(key)
    verifier.check(name, not bad,
                   "EMA sample_count / daily_scores 键数在同日两跑间不变" if not bad
                   else f"发生变化：{bad}",
                   rows + ["说明：ema_baseline.json 无日期键，读-改-写按运行次数推进"
                           "（§2.2 W1），故同日两跑若不幂等此处必现 diff"])


# ══════════════════════════════════════════════════════════════════════
# R2 / R5：无需两跑，直接真算
# ══════════════════════════════════════════════════════════════════════


def check_r2(verifier: Verifier) -> None:
    name = f"R2 出现 previous_regime != regime 的归档日 ≥ 1 天（{R2_SWITCH_HINT}）"
    switches: list[str] = []
    unset = 0
    no_regime = 0
    same = 0
    total = 0
    for day in iter_archive_days():
        unified = read_json(day / "unified.json")
        if not unified:
            continue
        total += 1
        regime = unified.get("market_regime")
        if not regime:
            no_regime += 1
            continue
        previous, current = regime.get("previous_regime"), regime.get("regime")
        if previous is None:
            unset += 1
        elif previous != current:
            switches.append(f"{day.name}: {previous} → {current}")
        else:
            same += 1
    verifier.check(
        name,
        bool(switches),
        f"实算 {len(switches)} 天（需 ≥1）" if switches
        else f"实算 0 天（需 ≥1）；{unset} 天 previous_regime 为 null，{same} 天 previous_regime == regime",
        [
            f"扫描 {total} 个含 unified.json 的归档日（其中 {no_regime} 天无 market_regime 字段）",
            f"previous_regime 为 null 的天数 = {unset}（字段未接线，不构成比较）",
            f"previous_regime == regime 的天数 = {same}",
            "regime 切换日（prev != regime）：" + (", ".join(switches) if switches else "无"),
        ],
    )


def check_r5(verifier: Verifier, runs: list[str] | None) -> None:
    name = "R5 signal_dates 中同 (type, date) 至多 1 条（0 重复）"
    path = MONITORING
    source = f"仓库实盘 {MONITORING.relative_to(repo_root()).as_posix()}"
    if runs:
        located = _locate(Path(runs[1]), "monitoring.json")
        if located:
            path, source = located, f"快照 B {located}"
    monitoring = read_json(path)
    if monitoring is None:
        verifier.skip(name, f"未找到 {source}", "需 monitoring.json（persistence.py 写出）")
        return
    entries = []
    for node_key in ("cross_node", "node5"):
        node = monitoring.get(node_key) or {}
        for field in ("signal_dates", "contrarian_alert_dates"):
            for item in node.get(field) or []:
                if isinstance(item, dict):
                    entries.append((node_key, item.get("type") or field, str(item.get("date"))))
                else:
                    entries.append((node_key, field, str(item)))
    counts = Counter((node, kind, date) for node, kind, date in entries)
    duplicates = {k: v for k, v in counts.items() if v > 1}
    verifier.check(
        name,
        not duplicates,
        f"0 重复（共 {len(entries)} 条日期记录）" if not duplicates
        else f"{len(duplicates)} 组 (type, date) 重复",
        [f"数据源：{source}",
         f"记录总数 = {len(entries)}，唯一 (节点, 类型, 日期) = {len(counts)}",
         "重复明细：" + ("；".join(f"{k[1]} {k[2]} ×{v}" for k, v in sorted(duplicates.items()))
                      if duplicates else "无")],
    )


# ══════════════════════════════════════════════════════════════════════
# R6：状态源清单逐项复测
# ══════════════════════════════════════════════════════════════════════

WRITE_SOURCES = [
    ("W1", "ema_baseline.json 读-改-写无日期键", "ema_baseline.json sample_count / regime_score"),
    ("W2", "monitoring.json signal_dates / contrarian_alert_dates 按条 append",
     "monitoring.json cross_node.signal_dates、node5.contrarian_alert_dates"),
    ("W3", "node4_events.mention_count += 1", "DB node4_events.mention_count（+ 影子字段 run_based）"),
    ("W4", "node4_raw_news.appearance_count += 1", "DB node4_raw_news.appearance_count（+ 影子字段）"),
    ("W5", "catalyst_refresh.trading_days_since_refresh += 1", "DB catalyst_refresh 行（last_full_refresh）"),
    ("W6", "ambush_position_snapshots session.merge 无主键", "DB ambush_position_snapshots 行数"),
]

READ_SOURCES = [
    ("D1", "load_capital_flow_series(5)", "capital.json 板块资金流 5 日序列"),
    ("D2", "load_previous_regime_state()", "unified.json market_regime（当日目录自引用）"),
    ("D3", "load_regime_state_history(20)", "unified.json regime 序列（transition_count 源）"),
    ("D4", "load_previous_stage_state()", "sentiment.json stage_state"),
    ("D5", "load_sentiment_score_history(20)", "sentiment.json score 序列"),
    ("D6", "load_regime_history(20)", "unified/market.json regime 序列"),
    ("D7", "_load_previous_reservoir()", "最近目录 ambush_reservoir"),
    ("D8", "_compute_sentiment_momentum()", "最近 10 天 sentiment score"),
    ("D9", "_load_reservoir_sector_names()", "最近一份 unified.json ambush_reservoir"),
]


def check_r6(verifier: Verifier, runs: list[str] | None) -> None:
    name = "R6 状态源清单逐项复测（§2.2 W1–W6 + §2.1 D1–D9，全部幂等）"
    evidence = [f"写侧 {sid}：{desc} → 观测点 {observable}" for sid, desc, observable in WRITE_SOURCES]
    evidence += [f"读侧 {sid}：{desc} → 观测点 {observable}" for sid, desc, observable in READ_SOURCES]
    evidence.append(
        "判定方式：上述 15 个状态源各需「同日两跑前后」各取一次观测点，逐源比对；"
        "本脚本不在缺少两跑样本时对任一源作替代性判定"
    )
    if runs:
        evidence.append("已提供 --runs，但逐源复测仍需按源分别取数（W5/W6 需 DB、D7/D8/D9 需历史目录），"
                        "脚本不据此推断 R6 达标")
    verifier.add(
        name, STATUS_SKIP,
        "需按 15 个状态源逐项做同日两跑复测（每源取两跑前后观测点），当前无该复测样本",
        evidence,
    )


# ══════════════════════════════════════════════════════════════════════
# 静态辅助检查（历史脚本的 6 项代码形状检查；不等于 R1–R6 达标）
# ══════════════════════════════════════════════════════════════════════


def _read(rel: str) -> str:
    path = SRC_GOT / rel
    return path.read_text(encoding="utf-8") if path.exists() else ""


def aux_r1_loader() -> tuple[bool, str, list[str]]:
    """persistence.py 的 loader 是否都排除当天归档（静态形状）。"""
    lines = _read("persistence.py").splitlines()
    calls = [(i, line.strip()) for i, line in enumerate(lines, 1)
             if "_list_archive_dirs(" in line and not re.match(r"\s*def ", line)]
    if not calls:
        return False, "未找到 _list_archive_dirs 调用", []
    bad = [f"L{ln}: {code}" for ln, code in calls if "before=date.today()" not in code]
    ok = [f"L{ln}: {code}" for ln, code in calls if "before=date.today()" in code]
    return (not bad), (f"{len(ok)}/{len(calls)} 个 loader 传 before=date.today()" if not bad
                       else f"{len(bad)}/{len(calls)} 个 loader 未排除当天"), (bad or ok)[:7]


def aux_news_counter() -> tuple[bool, str, list[str]]:
    """news_analysis.py 的 mention_count / appearance_count 递增是否受跨日守卫。"""
    lines = _read("nodes/news_analysis.py").splitlines()
    increments = [(i, line) for i, line in enumerate(lines, 1)
                  if re.search(r"(mention_count|appearance_count)\s*=\s*\(.*\1.*\)\s*\+\s*1",
                               line.strip())]
    if not increments:
        return False, "未找到跨日计数器递增代码", []
    unguarded, guarded = [], []
    for lineno, line in increments:
        indent = len(line) - len(line.lstrip())
        protected = False
        for j in range(lineno - 1, max(0, lineno - 15), -1):
            stripped = lines[j - 1].strip()
            if re.search(r"last_seen(_date)?\s*<\s*today", stripped):
                protected = indent > (len(lines[j - 1]) - len(lines[j - 1].lstrip()))
                break
        (guarded if protected else unguarded).append(f"L{lineno}: {line.strip()}")
    return (not unguarded), (f"{len(guarded)} 处递增均在 last_seen < today 守卫内" if not unguarded
                             else f"{len(unguarded)} 处递增无跨日守卫"), (unguarded or guarded)


def aux_shadow_counter() -> tuple[bool, str, list[str]]:
    """影子字段 run_based 是否每次运行无条件 +1（不受跨日守卫）。"""
    lines = _read("nodes/news_analysis.py").splitlines()
    shadows = [(i, line) for i, line in enumerate(lines, 1)
               if "run_based" in line and ("+= 1" in line or re.search(r"\+ 1\b", line))]
    if not shadows:
        return False, "未找到影子字段递增代码", []
    guarded, unguarded = [], []
    for lineno, line in shadows:
        indent = len(line) - len(line.lstrip())
        protected = False
        for j in range(lineno - 1, max(0, lineno - 15), -1):
            stripped = lines[j - 1].strip()
            if re.search(r"last_seen(_date)?\s*<\s*today", stripped):
                protected = indent > (len(lines[j - 1]) - len(lines[j - 1].lstrip()))
                break
        (guarded if protected else unguarded).append(f"L{lineno}: {line.strip()}")
    return (not guarded), (f"{len(unguarded)} 处影子递增无条件执行" if not guarded
                           else f"{len(guarded)} 处影子递增被跨日守卫包裹"), (guarded or unguarded)


def aux_sector_tracking_date() -> tuple[bool, str, list[str]]:
    """sector_analysis.py 的 3 个 loader 是否用 tracking_date < today 排除当天。"""
    lines = _read("nodes/sector_analysis.py").splitlines()
    functions = ("_load_prev_actionable_boards", "_load_all_sector_trends", "_load_prev_state")
    missing, ok = [], []
    for func in functions:
        start = next((i for i, line in enumerate(lines, 1) if re.search(rf"def\s+{func}\b", line)), None)
        if start is None:
            missing.append(f"{func}: 未找到函数定义")
            continue
        hit = next((j + 1 for j in range(start - 1, min(start + 50, len(lines)))
                    if re.search(r"tracking_date\s*<\s*(today|date\.today\(\))", lines[j])), None)
        (ok if hit else missing).append(f"{func}: L{hit} tracking_date < today" if hit
                                        else f"{func}: 未找到 tracking_date < today")
    return (not missing), (f"{len(ok)}/{len(functions)} 个 loader 排除当天" if not missing
                           else f"{len(missing)} 个 loader 未排除当天"), (missing or ok)


def aux_ambush_upsert() -> tuple[bool, str, list[str]]:
    """ambush 快照是否按 (symbol, entry_date) 自然键 upsert。"""
    lines = _read("nodes/portfolio_decision.py").splitlines()
    start = next((i for i, line in enumerate(lines, 1)
                  if re.search(r"def\s+_write_ambush_buy_snapshots\b", line)), None)
    if start is None:
        return False, "未找到 _write_ambush_buy_snapshots", []
    body = lines[start - 1:start + 120]
    found = {"symbol": False, "entry_date": False, "upsert": False}
    evidence = []
    for offset, line in enumerate(body):
        stripped = line.strip()
        if re.search(r"AmbushPositionSnapshotRecord\.symbol\s*==", stripped):
            found["symbol"] = True
        if re.search(r"AmbushPositionSnapshotRecord\.entry_date\s*==", stripped):
            found["entry_date"] = True
        if re.search(r"if\s+existing\s*:", stripped):
            found["upsert"] = True
        if stripped and ("自然键" in stripped or "merge" in stripped):
            evidence.append(f"L{start + offset}: {stripped}")
    bad = [k for k, v in found.items() if not v]
    return (not bad), ("按 (symbol, entry_date) 自然键 upsert" if not bad else f"缺少：{bad}"), evidence[:6]


def aux_archive_atomic() -> tuple[bool, str, list[str]]:
    """archive.py 是否 .tmp 旁路 + target→.old→target 原子替换。"""
    content = _read("archive.py")
    checks = {
        ".tmp 旁路目录": bool(re.search(r"\.tmp", content)) and "with_suffix" in content,
        ".old 备份目录": ".old" in content,
        "target→old": bool(re.search(r"rename\(.*archive_dir.*old_dir", content)),
        "tmp→target": bool(re.search(r"rename\(.*tmp_dir.*archive_dir", content)),
    }
    bad = [k for k, v in checks.items() if not v]
    return (not bad), "原子替换四要素齐备" if not bad else f"缺少：{bad}", \
        [f"{k}: {'有' if v else '无'}" for k, v in checks.items()]


AUX_CHECKS = [
    ("A1 persistence.py loader 排除当天归档", aux_r1_loader),
    ("A2 跨日计数器受 last_seen < today 守卫", aux_news_counter),
    ("A3 影子 run_based 计数器无条件 +1", aux_shadow_counter),
    ("A4 sector loader 使用 tracking_date < today", aux_sector_tracking_date),
    ("A5 ambush 快照 (symbol, entry_date) upsert", aux_ambush_upsert),
    ("A6 archive.py .tmp→.old→target 原子替换", aux_archive_atomic),
]


def run_auxiliary(verifier: Verifier) -> None:
    for label, func in AUX_CHECKS:
        try:
            passed, detail, evidence = func()
        except Exception as exc:  # 静态检查异常不应影响红线裁决
            passed, detail, evidence = False, f"检查异常：{exc}", []
        verifier.check(f"辅助 {label}", passed, detail, evidence, auxiliary=True)


# ══════════════════════════════════════════════════════════════════════


def run(runs: list[str] | None, as_json: bool) -> int:
    verifier = Verifier("A 层 R1–R6 同日两跑", REDLINE_DOC)

    print("\n── 红线：R1–R6（§6.1） ──")
    check_r1(verifier, runs)
    check_r2(verifier)
    check_r3(verifier, runs)
    check_r4(verifier, runs)
    check_r5(verifier, runs)
    check_r6(verifier, runs)

    if not runs:
        print("\n[info] 未传 --runs，R1/R3/R4 无法判定。产出两个同日快照后重跑：")
        print("       " + TWO_RUN_COMMAND.replace("\n", "\n       "))

    print("\n── 静态辅助检查（不等于 R1–R6 达标，不参与退出码裁决） ──")
    run_auxiliary(verifier)

    if as_json:
        payload = {
            "runs": runs,
            "redline": [
                {"item": name, "status": status, "detail": detail, "evidence": evidence}
                for name, status, detail, evidence in verifier.redline
            ],
            "auxiliary": [
                {"item": name, "status": status, "detail": detail}
                for name, status, detail, _ in verifier.auxiliary
            ],
        }
        print("\n── --json ──")
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    return verifier.finish()


def main() -> int:
    force_utf8_stdout()
    import argparse

    parser = argparse.ArgumentParser(description="A 层 R1–R6 同日两跑红线验收脚本")
    parser.add_argument("--runs", nargs=2, metavar=("RUN_A", "RUN_B"),
                        help="两个同日快照根目录（各含 YYYYMMDD/ 节点 JSON + ema_baseline.json "
                             "+ monitoring.json + stock_analysis.db）")
    parser.add_argument("--json", action="store_true", help="附加输出机器可读 JSON")
    args = parser.parse_args()

    print_header("A 层 §6.1 红线验收：R1–R6 同日两跑", REDLINE_DOC)
    return run(args.runs, args.json)


if __name__ == "__main__":
    sys.exit(main())
