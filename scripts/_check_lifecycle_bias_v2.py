# -*- coding: utf-8 -*-
"""生命周期实证 v2：修正方法论——仅用现行逻辑窗口 + 二波专项分析。

方法论修正（用户反馈 2026-09-01）：
- 系统在 07-24 ~ 09-01 间主线判定逻辑改版 ≥5 次（08-04 个体级主线框、
  08-17 状态机文献校准、08-22 P1-P6 全链路、08-25 硬闸门消除、08-28 跨日记忆），
  早期数据与现行逻辑不可比，全池统计无意义。
- 本脚本只做三件事：
  1. 现行窗口（08-28 后，跨日记忆版起）的主线寿命快照
  2. CXO 概念 14 天完整轨迹拆解（连续一波 vs 二波）
  3. cooling/exhausted → active_mainline 回跳事件统计（二波规模）
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# 输出直写 UTF-8 文件，规避 PowerShell 重定向编码问题
sys.stdout = open("data/_lifecycle_v2_out.txt", "w", encoding="utf-8")

DB = Path("data/stock_analysis.db")
con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row
cur = con.cursor()

# ── 1. 现行逻辑窗口（≥2026-08-28）主线寿命快照 ──
print("[1] 现行窗口（08-28 起，v2.1 跨日记忆版）主线寿命快照")
spells = cur.execute(
    """
    SELECT sector_name, MAX(mainline_days) max_days
    FROM node2_sector_trends
    WHERE is_mainline = 1 AND tracking_date >= '2026-08-28'
    GROUP BY sector_name
    ORDER BY max_days DESC
    """
).fetchall()
print(f"    窗口内曾为主线: {len(spells)} 个")
for s in spells[:20]:
    print(f"      {s['sector_name']!r:<20} max_day={s['max_days']}")

# ── 2. CXO 概念完整轨迹（14 天是连续一波还是二波？）──
print("\n[2] 长寿命样本完整轨迹拆解")
long_names = [r["sector_name"] for r in cur.execute(
    """
    SELECT sector_name, MAX(mainline_days) md FROM node2_sector_trends
    WHERE is_mainline = 1 GROUP BY sector_name HAVING md >= 9
    ORDER BY md DESC
    """
).fetchall()]
for name in long_names:
    trail = cur.execute(
        """
        SELECT tracking_date, state, mainline_days, strength_score,
               lifecycle_phase, dwell_days
        FROM node2_sector_trends
        WHERE sector_name = ? ORDER BY tracking_date
        """,
        (name,),
    ).fetchall()
    if not trail:
        continue
    print(f"\n  ◆ {name!r} ({len(trail)} 天记录):")
    # 标记状态跳变点
    prev_state = None
    for t in trail:
        mark = ""
        if prev_state and t["state"] != prev_state:
            mark = f"  ←← 状态跳变: {prev_state} → {t['state']}"
        sc = f"{t['strength_score']:.0f}" if t['strength_score'] is not None else "-"
        md = t['mainline_days'] if t['mainline_days'] is not None else 0
        st = t['state'] or 'NULL'
        ph = t['lifecycle_phase'] or '-'
        print(f"    {str(t['tracking_date'])[5:]} day={md:>2} "
              f"{st:<22} 强度={sc:>3} phase={ph}{mark}")
        prev_state = st if st != 'NULL' else prev_state

# ── 3. 二波事件统计：状态跳变类型分布 ──
print("\n[3] 状态跳变类型分布（全数据，用于识别二波路径）")
transitions = defaultdict(int)
wave2_cases = []  # cooling/exhausted → active_mainline
by_sector = defaultdict(list)
for r in cur.execute(
    """
    SELECT sector_name, tracking_date, state, mainline_days
    FROM node2_sector_trends ORDER BY sector_name, tracking_date
    """
).fetchall():
    by_sector[r["sector_name"]].append(r)

for name, recs in by_sector.items():
    for prev, cur_rec in zip(recs, recs[1:]):
        if not prev["state"] or not cur_rec["state"]:
            continue  # 状态机上线前的记录无 state，跳过
        if prev["state"] != cur_rec["state"]:
            key = f"{prev['state']} → {cur_rec['state']}"
            transitions[key] += 1
            if (prev["state"] in ("high_position_cooling", "exhausted_mainline")
                    and cur_rec["state"] == "active_mainline"):
                wave2_cases.append((name, str(cur_rec["tracking_date"]),
                                    prev["state"], cur_rec["mainline_days"] or 0))
            if (prev["state"] == "active_mainline"
                    and cur_rec["state"] in ("high_position_cooling",)):
                pass  # 进入降温（潜在二波起点）

for k, v in sorted(transitions.items(), key=lambda x: -x[1]):
    print(f"    {k:<55} {v}")

print(f"\n[4] 二波事件明细（cooling/exhausted → active_mainline 回跳）: {len(wave2_cases)} 起")
for name, d, frm, day in wave2_cases:
    print(f"    {name!r:<20} {d[5:]}  从 {frm} 回归  回归时 day={day}")

# ── 5. cooling 起点统计：多少主线进过降温（潜在二波池）──
cool_entries = defaultdict(int)
for name, recs in by_sector.items():
    for prev, cur_rec in zip(recs, recs[1:]):
        if not prev["state"] or not cur_rec["state"]:
            continue
        if prev["state"] == "active_mainline" and cur_rec["state"] == "high_position_cooling":
            cool_entries[name] += 1
print(f"\n[5] 曾进入高位降温的主线（潜在二波候选池）: {len(cool_entries)} 个")
for name, cnt in sorted(cool_entries.items(), key=lambda x: -x[1])[:15]:
    print(f"    {name!r:<20} 进降温 {cnt} 次")

con.close()
