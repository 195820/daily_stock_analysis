# -*- coding: utf-8 -*-
"""二波判据验证：11 起回跳事件的间隙结构拆解。

回答："降温一天也算二波吗？"
- 对每起 exhausted/cooling → active_mainline 回跳，计算：
  1. 间隙长度（离开 active_mainline 的交易日数）
  2. 间隙期强度最低点（是否真回撤）
  3. 回归日强度相对出框前的变化（是否真再启动）
- 判据候选：间隙 ≥2 日 且 间隙期强度实质回撤（跌破出框前平台）→ 二波；
  否则视为状态机单日噪声 → 同波延续。
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = open("data/_wave_gap_out.txt", "w", encoding="utf-8")

DB = Path("data/stock_analysis.db")
con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row
cur = con.cursor()

by_sector = defaultdict(list)
for r in cur.execute(
    """
    SELECT sector_name, tracking_date, state, mainline_days, strength_score
    FROM node2_sector_trends WHERE state IS NOT NULL
    ORDER BY sector_name, tracking_date
    """
).fetchall():
    by_sector[r["sector_name"]].append(r)

wave2_events = []
for name, recs in by_sector.items():
    for i, (prev, cur_rec) in enumerate(zip(recs, recs[1:])):
        if (prev["state"] in ("high_position_cooling", "exhausted_mainline")
                and cur_rec["state"] == "active_mainline"):
            # 向前找间隙起点（最后一次 active_mainline 日）
            gap_start_idx = i - 1
            while gap_start_idx >= 0 and recs[gap_start_idx]["state"] != "active_mainline":
                gap_start_idx -= 1
            pre = recs[gap_start_idx] if gap_start_idx >= 0 else None
            # 间隙 = prev..（即离开 active 的所有日子，这里 prev 就是间隙最后一条）
            gap_recs = recs[gap_start_idx + 1: i + 1] if gap_start_idx >= 0 else [prev]
            gap_len = len(gap_recs)
            gap_min = min((g["strength_score"] or 0) for g in gap_recs)
            pre_score = pre["strength_score"] if pre else None
            re_score = cur_rec["strength_score"] or 0
            wave2_events.append({
                "name": name, "date": str(cur_rec["tracking_date"]),
                "from": prev["state"], "gap_len": gap_len,
                "gap_min": gap_min, "pre": pre_score,
                "re_entry": re_score, "day": cur_rec["mainline_days"],
            })

print(f"共 {len(wave2_events)} 起回跳，按间隙结构分类：\n")
print(f"{'板块':<10} {'回归日':<7} {'间隙':<4} {'出框前':<6} {'隙最低':<6} "
      f"{'回归':<6} {'回撤幅度':<8} 判定")
print("-" * 80)
blip, real = 0, 0
for e in sorted(wave2_events, key=lambda x: x["gap_len"]):
    pre = e["pre"]
    if pre:
        drop = pre - e["gap_min"]
        drop_txt = f"-{drop:.0f}分"
        # 判据：间隙≥2日 且 回撤≥10分 → 真二波
        is_real = e["gap_len"] >= 2 and drop >= 10
    else:
        drop_txt = "?"
        is_real = e["gap_len"] >= 2
    verdict = "真二波" if is_real else "单日噪声→同波"
    if is_real:
        real += 1
    else:
        blip += 1
    print(f"{e['name']:<10} {e['date'][5:]:<7} {e['gap_len']:<4} "
          f"{pre if pre is not None else '-':<6} {e['gap_min']:<6.0f} "
          f"{e['re_entry']:<6.0f} {drop_txt:<8} {verdict}")

print(f"\n汇总: 真二波 {real} 起 / 单日噪声 {blip} 起")
con.close()
