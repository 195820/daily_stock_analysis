# -*- coding: utf-8 -*-
"""M3 家族误配率人工抽查：导出逐日「板块 → 家族」对照表（自包含 HTML）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
指标出处（`docs/got/archive/agent_glue_plan_0903.md` §8.1 / §8.2）

| M3 | **误配率**：人工抽查 glue 归属中「无实质归属关系」的占比 |
| 红线 | **≤ 15%** |
| 转正条件 | M2/M3（人工）**连续 5 日达标**（§8.2） |
| 样本 | 每日候选 **全量**（§8.1「每日 24 候选全量，≤10 分钟」） |

M3 无法自动化的原因见 §8.1「为何 M2/M3 无法完全自动化」：正确性的 ground truth 是
市场共识判断，不存在可查询的真值表 —— 只能人工。本脚本**只搬运事实，不做判定**，
判定列留空给审核者。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

产出：`data/glue_family_audit/family_audit.html`

用法：
    python scripts/export_glue_family_audit.py
    python scripts/export_glue_family_audit.py --days 20260909 20260914
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.got.family_keywords import FAMILY_KEYWORDS  # noqa: E402
from src.got.schemas import CONTROLLED_FAMILIES  # noqa: E402

ARCHIVE_ROOT = PROJECT_ROOT / "data" / "got_daily"
OUTPUT_DIR = PROJECT_ROOT / "data" / "glue_family_audit"

GLUE_FILE = "agent_glue.json"
UNIFIED_FILE = "unified.json"
NEWS_FILE = "news.json"

RULE_NONE = "其他"
GLUE_NONE = "无归属"

# prompt 任务3 硬编码列举给 LLM 的家族（与 CONTROLLED_FAMILIES 漂移，见 §8 附注）
PROMPT_VOCAB = [
    "AI软件", "AI硬件", "周期资源", "新能源", "农业", "医药",
    "军工", "消费", "智能驾驶", "金融", "机器人", "生态环保",
]


def discover_days(explicit: list[str] | None) -> list[str]:
    """默认取所有「同时有 unified.json 与 agent_glue.json」的归档日，按日期升序。"""
    if explicit:
        return sorted(explicit)
    days = [
        day.name
        for day in sorted(ARCHIVE_ROOT.iterdir())
        if day.is_dir()
        and (day / GLUE_FILE).exists()
        and (day / UNIFIED_FILE).exists()
        and (day / NEWS_FILE).exists()
    ]
    return days


def classify(rule_family: str | None, glue_family: str | None) -> str:
    """行类别。语义见 HTML 页脚图例。"""
    if rule_family == GLUE_NONE or glue_family == RULE_NONE:
        # 归档里两种「放弃」写法不混用，但防御性归并
        rule_family = RULE_NONE if rule_family == GLUE_NONE else rule_family
        glue_family = GLUE_NONE if glue_family == RULE_NONE else glue_family
    rule_gave_up = rule_family == RULE_NONE
    glue_gave_up = glue_family == GLUE_NONE
    if rule_gave_up and glue_gave_up:
        return "both_none"
    if rule_gave_up:
        return "recovered"
    if glue_gave_up:
        return "dropped"
    return "same" if rule_family == glue_family else "overridden"


def load_day(day: str) -> list[dict]:
    unified = json.loads((ARCHIVE_ROOT / day / UNIFIED_FILE).read_text(encoding="utf-8"))
    glue = json.loads((ARCHIVE_ROOT / day / GLUE_FILE).read_text(encoding="utf-8"))
    news = json.loads((ARCHIVE_ROOT / day / NEWS_FILE).read_text(encoding="utf-8"))

    glue_family_by_code = {
        entry.get("board_code"): entry.get("family") for entry in glue.get("families") or []
    }
    glue_strength_by_code = {
        entry.get("board_code"): entry for entry in glue.get("strengths") or []
    }

    # 当日消息面证据：家族归属的判据是「这条消息说的是什么业务」，
    # 而不是板块名的字面语义（概念板块尤其如此，如培育钻石→AI 服务器散热材料）。
    event_title_by_id = {
        event.get("event_id"): event.get("title")
        for event in (news.get("catalyst_calendar") or {}).get("events") or []
    }
    relations_by_code: dict[str, list[dict]] = {}
    for relation in glue.get("relations") or []:
        relations_by_code.setdefault(relation.get("board_code"), []).append(relation)
    major_events_by_sector: dict[str, list[str]] = {}
    for event in news.get("major_events") or []:
        for sector_name in event.get("affected_sectors") or []:
            major_events_by_sector.setdefault(sector_name, []).append(event.get("title") or "")

    rows = []
    for sector in unified.get("confirmed_sectors") or []:
        code = sector.get("sector_code")
        name = sector.get("sector_name") or code
        rule_family = sector.get("frame_group") or RULE_NONE
        glue_family = glue_family_by_code.get(code) or GLUE_NONE
        strength = glue_strength_by_code.get(code) or {}

        news_evidence = []
        for relation in relations_by_code.get(code, []):
            event_id = relation.get("event_id")
            title = event_title_by_id.get(event_id) or f"事件 {event_id}"
            path = " → ".join(relation.get("propagation_path") or [])
            suffix = f"（传导：{path}）" if path else ""
            news_evidence.append(f"{title}{suffix}")
        for title in major_events_by_sector.get(name, []):
            news_evidence.append(f"[重大事件] {title}")

        rows.append({
            "day": day,
            "code": code,
            "name": name,
            "rule_family": rule_family,
            "glue_family": glue_family,
            "category": classify(rule_family, glue_family),
            "glue_strength": strength.get("strength"),
            "glue_confidence": strength.get("confidence"),
            "glue_evidence": strength.get("evidence") or "",
            "news_evidence": news_evidence,
            "rule_actionable": sector.get("classification"),
        })
    return rows


def flag_cross_day_instability(all_rows: list[dict]) -> None:
    """同一板块在不同日拿到不同 glue 家族 → 打标（就地写入 row）。"""
    seen: dict[str, set[str]] = {}
    for row in all_rows:
        seen.setdefault(row["name"], set()).add(row["glue_family"])
    for row in all_rows:
        others = seen[row["name"]] - {row["glue_family"]}
        row["unstable_with"] = sorted(others)


def keyword_lookup() -> dict[str, list[str]]:
    return {family: list(words) for family, words in FAMILY_KEYWORDS}


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def render_rows(rows: list[dict]) -> str:
    out = []
    for index, row in enumerate(rows, start=1):
        unstable = row["unstable_with"]
        unstable_chip = (
            f'<span class="chip chip-unstable" title="其他日给出过：{"、".join(unstable)}">跨日不一致</span>'
            if unstable else ""
        )
        evidence = html.escape(row["glue_evidence"]) or "—"
        if row["news_evidence"]:
            news_cell = "<br>".join(html.escape(item) for item in row["news_evidence"])
        else:
            news_cell = '<span class="empty">无相关消息</span>'
        out.append(f"""
        <tr data-day="{row['day']}" data-cat="{row['category']}" data-code="{row['code']}" data-name="{html.escape(row['name'])}">
          <td class="c-idx">{index}</td>
          <td class="c-name"><span class="board">{html.escape(row['name'])}</span>{unstable_chip}<span class="code">{html.escape(str(row['code']))}</span></td>
          <td class="c-family c-rule">{html.escape(row['rule_family'])}</td>
          <td class="c-family c-glue">{html.escape(row['glue_family'])}</td>
          <td class="c-news">{news_cell}</td>
          <td class="c-evidence" title="{evidence}">{evidence}</td>
          <td class="c-verdict">
            <button type="button" class="v-btn" data-v="ok" aria-label="归属正确">对</button>
            <button type="button" class="v-btn" data-v="bad" aria-label="归属错误">错</button>
            <button type="button" class="v-btn" data-v="unsure" aria-label="依据不足无法判定">存疑</button>
          </td>
        </tr>""")
    return "".join(out)


CATEGORY_LABEL = {
    "same": "一致",
    "recovered": "回收",
    "overridden": "推翻",
    "dropped": "弃权",
    "both_none": "双空",
}


def render_page(all_rows: list[dict], days: list[str]) -> str:
    by_day: dict[str, list[dict]] = {day: [] for day in days}
    for row in all_rows:
        by_day[row["day"]].append(row)

    counts = {key: 0 for key in CATEGORY_LABEL}
    for row in all_rows:
        counts[row["category"]] += 1
    total = len(all_rows)

    sections = []
    for day in days:
        rows = by_day[day]
        day_counts = {key: 0 for key in CATEGORY_LABEL}
        for row in rows:
            day_counts[row["category"]] += 1
        n = len(rows)

        bar = "".join(
            f'<span class="seg seg-{key}" style="width:{day_counts[key] / n * 100:.4f}%" '
            f'title="{CATEGORY_LABEL[key]} {day_counts[key]}"></span>'
            for key in CATEGORY_LABEL if day_counts[key]
        )
        legend = " · ".join(
            f'{CATEGORY_LABEL[key]} {day_counts[key]}' for key in CATEGORY_LABEL if day_counts[key]
        )
        pretty = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        sections.append(f"""
      <section class="day" data-day="{day}">
        <header class="day-head">
          <h2>{pretty}</h2>
          <div class="day-meta"><span class="mono">{n}</span> 个候选板块</div>
          <div class="bar" role="img" aria-label="{legend}">{bar}</div>
          <div class="day-legend">{legend} · 已判 <span class="mono day-done" data-day="{day}">0</span>/<span class="mono">{n}</span></div>
        </header>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="c-idx">#</th>
                <th>候选板块</th>
                <th class="c-family">规则侧<br><span class="th-sub">frame_group</span></th>
                <th class="c-family">glue 侧<br><span class="th-sub">families[].family</span></th>
                <th class="c-news">当日消息面证据<br><span class="th-sub">relations + major_events</span></th>
                <th class="c-evidence">glue 自述依据<br><span class="th-sub">strengths[].evidence</span></th>
                <th class="c-verdict">判定</th>
              </tr>
            </thead>
            <tbody>{render_rows(rows)}</tbody>
          </table>
        </div>
      </section>""")

    vocab = keyword_lookup()
    vocab_rows = []
    for family in CONTROLLED_FAMILIES:
        blind = "" if family in PROMPT_VOCAB else '<span class="chip chip-blind">未列给 LLM</span>'
        words = "、".join(vocab.get(family, [])) or "—"
        vocab_rows.append(
            f'<tr><th scope="row">{html.escape(family)}{blind}</th>'
            f'<td>{html.escape(words)}</td></tr>'
        )

    data_json = json.dumps(
        [
            {"day": r["day"], "code": r["code"], "name": r["name"], "cat": r["category"]}
            for r in all_rows
        ],
        ensure_ascii=False,
    )

    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GOT 家族归属审核</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@600;700&display=swap">
<style>
:root {
  --ground: #f2f3f7;
  --surface: #ffffff;
  --surface-2: #eaecf3;
  --ink: #14161d;
  --ink-2: #565b6d;
  --ink-3: #878ca0;
  --line: #d9dde7;
  --rule: #2c6b74;
  --agent: #5748d4;
  --good: #2b7d55;
  --risk: #a63a30;
  --idle: #7a7f90;
  --shadow: 0 1px 2px rgba(20,22,29,.06), 0 8px 24px -16px rgba(20,22,29,.35);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #101218;
    --surface: #181b23;
    --surface-2: #22262f;
    --ink: #e7e9ef;
    --ink-2: #a3a9b9;
    --ink-3: #767c8d;
    --line: #2b303b;
    --rule: #56aab2;
    --agent: #8d81f2;
    --good: #4fb884;
    --risk: #e07d72;
    --idle: #8d93a4;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.8);
  }
}
:root[data-theme="dark"] {
  --ground: #101218;
  --surface: #181b23;
  --surface-2: #22262f;
  --ink: #e7e9ef;
  --ink-2: #a3a9b9;
  --ink-3: #767c8d;
  --line: #2b303b;
  --rule: #56aab2;
  --agent: #8d81f2;
  --good: #4fb884;
  --risk: #e07d72;
  --idle: #8d93a4;
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px -16px rgba(0,0,0,.8);
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  padding-block: 0 4rem;
  background: var(--ground);
  color: var(--ink);
  font-family: "Noto Sans SC", system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 15px;
  line-height: 1.6;
}
.mono, .c-idx, .code, .bar, .day-meta .mono { font-family: "JetBrains Mono", ui-monospace, Consolas, monospace; font-variant-numeric: tabular-nums; }
h1, h2 { font-family: "Noto Serif SC", "Songti SC", serif; text-wrap: balance; margin: 0; }
a { color: var(--agent); }

.wrap { max-width: 1180px; margin: 0 auto; padding-inline: 20px; }

/* ── 顶部总览 ── */
.top {
  position: sticky; top: 0; z-index: 20;
  background: color-mix(in srgb, var(--ground) 88%, transparent);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line);
}
.top-inner { display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px 22px; padding-block: 14px; }
.top h1 { font-size: 1.32rem; letter-spacing: .01em; }
.top .sub { color: var(--ink-2); font-size: .82rem; }
.tally { display: flex; flex-wrap: wrap; gap: 6px 8px; margin-left: auto; }
.tally .t { display: flex; align-items: baseline; gap: 5px; font-size: .78rem; color: var(--ink-2); padding: 3px 9px; border-radius: 999px; background: var(--surface); border: 1px solid var(--line); }
.tally .t b { font-family: "JetBrains Mono", monospace; font-size: .9rem; font-variant-numeric: tabular-nums; color: var(--ink); }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot-same { background: var(--idle); }
.dot-recovered { background: var(--good); }
.dot-overridden { background: var(--risk); }
.dot-dropped { background: var(--ink-3); }
.dot-both_none { background: var(--line); outline: 1px solid var(--ink-3); outline-offset: -1px; }

/* ── 控制条 ── */
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding-block: 10px; border-bottom: 1px solid var(--line); }
.controls button {
  font: inherit; font-size: .82rem;
  padding: 5px 12px; border-radius: 7px; cursor: pointer;
  background: var(--surface); color: var(--ink-2);
  border: 1px solid var(--line);
}
.controls button[aria-pressed="true"] { background: var(--ink); color: var(--ground); border-color: var(--ink); }
.controls button:focus-visible { outline: 2px solid var(--agent); outline-offset: 2px; }
.progress { margin-left: auto; font-size: .82rem; color: var(--ink-2); }
.progress b { font-family: "JetBrains Mono", monospace; color: var(--ink); }

/* ── 日分组 ── */
.day { margin-top: 34px; }
.day-head { display: grid; grid-template-columns: auto 1fr; align-items: baseline; gap: 4px 14px; }
.day-head h2 { font-size: 1.12rem; }
.day-meta { color: var(--ink-2); font-size: .82rem; }
.day-meta .mono { color: var(--ink); font-weight: 600; }
.bar { grid-column: 1 / -1; display: flex; height: 5px; border-radius: 3px; overflow: hidden; background: var(--surface-2); }
.seg { display: block; height: 100%; }
.seg-same { background: var(--idle); }
.seg-recovered { background: var(--good); }
.seg-overridden { background: var(--risk); }
.seg-dropped { background: var(--ink-3); }
.seg-both_none { background: var(--line); }
.day-legend { grid-column: 1 / -1; font-size: .78rem; color: var(--ink-3); }

/* ── 表 ── */
.table-wrap { margin-top: 10px; overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; box-shadow: var(--shadow); }
table { border-collapse: collapse; width: 100%; min-width: 1180px; }
thead th {
  position: sticky; top: 76px; z-index: 5;
  background: var(--surface-2); color: var(--ink-2);
  font-size: .74rem; font-weight: 600; letter-spacing: .04em;
  text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
.th-sub { font-family: "JetBrains Mono", monospace; font-weight: 400; font-size: .68rem; color: var(--ink-3); letter-spacing: 0; }
tbody td { padding: 9px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover td { background: var(--surface-2); }
tbody tr { border-left: 3px solid transparent; }
tbody tr[data-cat="recovered"] { border-left-color: var(--good); }
tbody tr[data-cat="overridden"] { border-left-color: var(--risk); }
tbody tr[data-cat="dropped"] { border-left-color: var(--ink-3); }
tbody tr[data-cat="same"] { border-left-color: var(--line); }
tbody tr[data-cat="both_none"] { border-left-color: var(--line); }
tbody tr.is-hidden { display: none; }

.c-idx { width: 44px; color: var(--ink-3); font-size: .78rem; }
.c-name { min-width: 168px; }
.board { font-weight: 500; }
.code { display: block; color: var(--ink-3); font-size: .72rem; letter-spacing: .02em; }
.c-family { white-space: nowrap; font-size: .88rem; }
.c-rule { color: var(--rule); }
.c-glue { color: var(--agent); font-weight: 500; }
.c-evidence { min-width: 240px; max-width: 380px; color: var(--ink-2); font-size: .82rem; }
.c-news { min-width: 260px; max-width: 420px; font-size: .8rem; color: var(--ink); }
.empty { color: var(--ink-3); }
.c-verdict { width: 178px; white-space: nowrap; }

.chip { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 4px; font-size: .68rem; line-height: 1.5; letter-spacing: .02em; vertical-align: 1px; }
.chip-unstable { background: color-mix(in srgb, var(--risk) 14%, transparent); color: var(--risk); border: 1px solid color-mix(in srgb, var(--risk) 35%, transparent); }
.chip-blind { margin: 0 0 0 8px; background: color-mix(in srgb, var(--agent) 14%, transparent); color: var(--agent); border: 1px solid color-mix(in srgb, var(--agent) 35%, transparent); }

.v-btn {
  font: inherit; font-size: .78rem; cursor: pointer;
  padding: 3px 9px; margin-right: 4px; border-radius: 6px;
  background: var(--surface); color: var(--ink-3);
  border: 1px solid var(--line);
}
.v-btn:hover { color: var(--ink); border-color: var(--ink-3); }
.v-btn:focus-visible { outline: 2px solid var(--agent); outline-offset: 2px; }
tr[data-verdict="ok"] .v-btn[data-v="ok"] { background: var(--good); border-color: var(--good); color: #fff; }
tr[data-verdict="bad"] .v-btn[data-v="bad"] { background: var(--risk); border-color: var(--risk); color: #fff; }
tr[data-verdict="unsure"] .v-btn[data-v="unsure"] { background: var(--ink-2); border-color: var(--ink-2); color: var(--ground); }

/* ── 词表参考 ── */
details.panel { margin-top: 40px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; }
details.panel > summary { cursor: pointer; padding: 12px 16px; font-weight: 500; }
details.panel > summary:focus-visible { outline: 2px solid var(--agent); outline-offset: -2px; border-radius: 10px; }
.panel-body { padding: 0 16px 18px; }
.vocab { width: 100%; min-width: 0; }
.vocab th { width: 148px; text-align: left; vertical-align: top; padding: 7px 12px 7px 0; border-bottom: 1px solid var(--line); font-size: .84rem; white-space: nowrap; }
.vocab td { padding: 7px 0; border-bottom: 1px solid var(--line); color: var(--ink-2); font-size: .8rem; }
.note { margin-top: 14px; padding: 12px 14px; border-left: 3px solid var(--agent); background: var(--surface-2); border-radius: 0 7px 7px 0; font-size: .82rem; color: var(--ink-2); }
.note b { color: var(--ink); }

/* ── 判定导出 ── */
.export { margin-top: 34px; }
.export h2 { font-size: 1.05rem; }
.export p { color: var(--ink-2); font-size: .84rem; margin: 8px 0 10px; }
textarea {
  width: 100%; min-height: 168px; resize: vertical;
  font-family: "JetBrains Mono", ui-monospace, monospace; font-size: .78rem; line-height: 1.55;
  padding: 12px; border-radius: 9px;
  background: var(--surface); color: var(--ink); border: 1px solid var(--line);
}
textarea:focus-visible { outline: 2px solid var(--agent); outline-offset: 2px; }
.foot { margin-top: 22px; color: var(--ink-3); font-size: .76rem; }

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
@media (max-width: 640px) {
  .c-evidence, .c-news { display: none; }
  thead th { position: static; }
  .tally { margin-left: 0; }
}
</style>
</head>
<body>
<div class="top">
  <div class="wrap top-inner">
    <h1>GOT 家族归属审核</h1>
    <span class="sub">M3 误配率人工抽查 · 全量 @@TOTAL@@ 行 · 红线 ≤ 15%</span>
    <div class="tally">
      <span class="t"><span class="dot dot-same"></span>一致 <b>@@SAME@@</b></span>
      <span class="t"><span class="dot dot-recovered"></span>回收 <b>@@RECOVERED@@</b></span>
      <span class="t"><span class="dot dot-overridden"></span>推翻 <b>@@OVERRIDDEN@@</b></span>
      <span class="t"><span class="dot dot-dropped"></span>弃权 <b>@@DROPPED@@</b></span>
    </div>
  </div>
  <div class="wrap controls">
    <button type="button" id="f-all" aria-pressed="true">全部</button>
    <button type="button" id="f-overridden" aria-pressed="false">只看推翻</button>
    <button type="button" id="f-changed" aria-pressed="false">只看改动</button>
    <button type="button" id="f-unjudged" aria-pressed="false">只看未判</button>
    <span class="progress">已判 <b id="done-count">0</b> / <b>@@TOTAL@@</b> <span id="err-count"></span></span>
  </div>
</div>

<main class="wrap">
  @@SECTIONS@@

  <details class="panel">
    <summary>受控词表参考（14 个家族 + 关键词）— 判定时对照</summary>
    <div class="panel-body">
      <table class="vocab">
        <tbody>@@VOCAB@@</tbody>
      </table>
      <div class="note">
        <b>判定口径（按此审，勿按板块名字面语义）。</b>
        概念级板块的家族归属要<b>按当日消息面判断该板块「实际在做的是什么业务」</b>，
        而非按板块名的字面含义。例：<b>培育钻石</b> 若当日消息是做 AI 服务器散热材料，应归 <b>AI硬件</b>，
        不是按「钻石＝珠宝消费」归 <b>消费</b>。所以先看「当日消息面证据」列再判；
        该列为「无相关消息」时，等于当日没有判据，此种情况按<b>存疑</b>处理。
      </div>
      <div class="note">
        <b>两处口径漂移，判定时须避开。</b>
        ① <code>CONTROLLED_FAMILIES</code> 有 <b>14</b> 个家族，但 <code>_build_glue_prompt</code> 硬编码只列了 <b>12</b> 个 ——
        <b>华为生态</b> 与 <b>前沿科技</b> 从未出现在 prompt 里，全期 128 条 <code>families[]</code> 中 0 次命中，
        属结构性不可达；若某板块本应落这两个家族，glue 只能错填或填「无归属」。
        ② 规则侧的 <b>其他</b> 与 glue 侧的 <b>无归属</b> 是同一含义的两种写法（都是归类失败），页内归并为「双空」。
      </div>
    </div>
  </details>

  <section class="export">
    <h2>判定导出</h2>
    <p>逐行点判定后，此处自动汇总。全选复制即可回贴给我，或直接告诉我「错」的行号。</p>
    <textarea id="export" readonly spellcheck="false" aria-label="判定结果导出"></textarea>
  </section>

  <p class="foot">
    数据源：<code>data/got_daily/{date}/agent_glue.json</code> × <code>unified.json</code> ·
    生成脚本 <code>scripts/export_glue_family_audit.py</code> ·
    指标定义 <code>docs/got/archive/agent_glue_plan_0903.md</code> §8.1
  </p>
</main>

<script>
const ROWS = @@DATA_JSON@@;
const STORE_KEY = "glue-family-audit-v1";
const verdicts = (() => {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch (e) { return {}; }
})();

const rowKey = (r) => r.day + ":" + r.code;
const tableRows = Array.from(document.querySelectorAll(".day tbody tr"));
const rowMeta = new Map(tableRows.map((el) => [el.dataset.day + ":" + el.dataset.code, {
  day: el.dataset.day, code: el.dataset.code, name: el.dataset.name, cat: el.dataset.cat,
}]));

function save() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(verdicts)); } catch (e) { /* 隐私模式：仅内存生效 */ }
}

function paint(el, value) {
  if (value) { el.dataset.verdict = value; } else { delete el.dataset.verdict; }
}

function refreshCounts() {
  let done = 0, bad = 0;
  for (const el of tableRows) {
    if (verdicts[rowKey(rowMeta.get(el.dataset.day + ":" + el.dataset.code))]) done++;
    if (el.dataset.verdict === "bad") bad++;
  }
  document.getElementById("done-count").textContent = done;
  document.getElementById("err-count").textContent = bad ? "· 判错 " + bad + " 行" : "";
  const perDay = {};
  for (const el of tableRows) {
    const meta = rowMeta.get(el.dataset.day + ":" + el.dataset.code);
    if (!verdicts[meta.day + ":" + meta.code]) continue;
    perDay[meta.day] = (perDay[meta.day] || 0) + 1;
  }
  for (const el of document.querySelectorAll(".day-done")) {
    el.textContent = perDay[el.dataset.day] || 0;
  }
  const bad_rows = [];
  for (const el of tableRows) {
    const meta = rowMeta.get(el.dataset.day + ":" + el.dataset.code);
    const v = verdicts[meta.day + ":" + meta.code];
    if (v === "bad") bad_rows.push(meta.day + "\\t" + meta.name + "\\t" + meta.cat);
  }
  const judged = Object.entries(verdicts);
  const lines = [
    "# M3 家族归属人工抽查判定",
    "# 格式: 日期\\t板块\\t类别\\t判定",
    ...judged.map(([k, v]) => {
      const meta = rowMeta.get(k);
      return meta ? meta.day + "\\t" + meta.name + "\\t" + meta.cat + "\\t" + ({ok:"正确",bad:"错误",unsure:"存疑"}[v] || v) : null;
    }).filter(Boolean),
    "",
    "合计: 已判 " + judged.length + " / " + tableRows.length + "，判错 " + bad_rows.length + " 行",
  ];
  document.getElementById("export").value = lines.join("\\n");
}

for (const el of tableRows) {
  const key = el.dataset.day + ":" + el.dataset.code;
  paint(el, verdicts[key]);
  el.querySelectorAll(".v-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = el.dataset.verdict === btn.dataset.v ? null : btn.dataset.v;
      paint(el, next);
      if (next) { verdicts[key] = next; } else { delete verdicts[key]; }
      save(); refreshCounts();
    });
  });
}

const filters = {
  "f-all": () => true,
  "f-overridden": (m) => m.cat === "overridden" || m.cat === "dropped",
  "f-changed": (m) => m.cat !== "same" && m.cat !== "both_none",
  "f-unjudged": (m) => !verdicts[m.day + ":" + m.code],
};
let activeFilter = "f-all";
function applyFilter() {
  const test = filters[activeFilter];
  for (const el of tableRows) {
    const meta = rowMeta.get(el.dataset.day + ":" + el.dataset.code);
    el.classList.toggle("is-hidden", !test(meta));
  }
  for (const section of document.querySelectorAll(".day")) {
    const visible = section.querySelectorAll("tbody tr:not(.is-hidden)").length;
    section.hidden = visible === 0;
  }
}
for (const id of Object.keys(filters)) {
  document.getElementById(id).addEventListener("click", () => {
    activeFilter = id;
    for (const other of Object.keys(filters)) {
      document.getElementById(other).setAttribute("aria-pressed", String(other === id));
    }
    applyFilter();
  });
}

document.addEventListener("keydown", (event) => {
  if (event.target.tagName === "TEXTAREA" || event.target.tagName === "INPUT") return;
  if (!["1", "2", "3"].includes(event.key)) return;
  const row = document.activeElement && document.activeElement.closest("tr");
  if (!row) return;
  const btn = row.querySelector('.v-btn[data-v="' + ({"1":"ok","2":"bad","3":"unsure"}[event.key]) + '"]');
  if (btn) { btn.click(); event.preventDefault(); }
});

refreshCounts();
applyFilter();
</script>
</body>
</html>
"""
    return (
        template.replace("@@TOTAL@@", str(total))
        .replace("@@SAME@@", str(counts["same"] + counts["both_none"]))
        .replace("@@RECOVERED@@", str(counts["recovered"]))
        .replace("@@OVERRIDDEN@@", str(counts["overridden"]))
        .replace("@@DROPPED@@", str(counts["dropped"]))
        .replace("@@SECTIONS@@", "".join(sections))
        .replace("@@VOCAB@@", "".join(vocab_rows))
        .replace("@@DATA_JSON@@", data_json)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 M3 家族误配率人工抽查表")
    parser.add_argument("--days", nargs="*", help="归档日 YYYYMMDD，默认自动发现")
    parser.add_argument("--out", help="输出 HTML 路径")
    args = parser.parse_args()

    days = discover_days(args.days)
    if not days:
        print("[FAIL] 未找到同时含 unified.json 与 agent_glue.json 的归档日", file=sys.stderr)
        return 1

    all_rows: list[dict] = []
    for day in days:
        all_rows.extend(load_day(day))
    flag_cross_day_instability(all_rows)

    output_path = Path(args.out) if args.out else OUTPUT_DIR / "family_audit.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_page(all_rows, days), encoding="utf-8")

    counts: dict[str, int] = {}
    for row in all_rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    print(f"归档日 {len(days)} 天（{'、'.join(days)}），候选 {len(all_rows)} 行")
    print("类别分布：" + "、".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"已写入 {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
