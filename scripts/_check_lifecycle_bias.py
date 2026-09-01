# -*- coding: utf-8 -*-
"""主线生命周期实证检查（支撑 momentum_bias_research 建议 1 的断点校准）。

验证目标：
1. 主线实际寿命分布：mainline_days 的分布（多少主线活过 8/10/15 天）
2. 衰减曲线：按 mainline_days 分箱，统计强度均值与次日涨跌占比
   —— 验证"day 8-10 开始衰减"的启发式是否被自有数据支持
3. 退出方式：衰竭前最后几天的强度轨迹（渐进衰竭 vs 急跌破位）
"""
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path("data/stock_analysis.db")
con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row
cur = con.cursor()

# ── 0. 表结构与日期范围 ──
rows = cur.execute(
    "SELECT MIN(tracking_date) dmin, MAX(tracking_date) dmax, COUNT(*) n "
    "FROM node2_sector_trends"
).fetchone()
print(f"数据范围: {rows['dmin']} ~ {rows['dmax']}, 总记录 {rows['n']}")

MAINLINE_STATES = ("active_mainline", "high_position_cooling", "exhausted_mainline")

# ── 1. 主线寿命分布：每个板块每段连续主线期的最大 mainline_days ──
spells = cur.execute(
    """
    SELECT sector_name, MAX(mainline_days) max_days, COUNT(*) recs
    FROM node2_sector_trends
    WHERE state IN ('active_mainline', 'high_position_cooling', 'exhausted_mainline')
      AND is_mainline = 1
    GROUP BY sector_name
    """
).fetchall()
print(f"\n[1] 主线期数量: {len(spells)} 个板块曾为主线")
buckets = {"≤3": 0, "4-7": 0, "8-10": 0, "11-15": 0, "16-20": 0, ">20": 0}
for s in spells:
    d = s["max_days"] or 0
    if d <= 3: buckets["≤3"] += 1
    elif d <= 7: buckets["4-7"] += 1
    elif d <= 10: buckets["8-10"] += 1
    elif d <= 15: buckets["11-15"] += 1
    elif d <= 20: buckets["16-20"] += 1
    else: buckets[">20"] += 1
for k, v in buckets.items():
    print(f"    寿命 {k:>5} 天: {v} 段")
long_spells = sorted(spells, key=lambda s: -(s["max_days"] or 0))[:10]
print("    最长 10 段:", [(s["sector_name"], s["max_days"]) for s in long_spells])

# ── 2. 衰减曲线：按 mainline_days 分箱看强度 + 次日变化 ──
# 取每个板块逐日记录，配对相邻交易日
all_main = cur.execute(
    """
    SELECT sector_name, tracking_date, mainline_days, strength_score, state
    FROM node2_sector_trends
    WHERE is_mainline = 1 AND state IN ('active_mainline', 'high_position_cooling')
    ORDER BY sector_name, tracking_date
    """
).fetchall()

by_day = defaultdict(lambda: {"scores": [], "next_up": 0, "next_down": 0})
prev_by_sector = {}
for r in all_main:
    key = (r["sector_name"],)
    prev = prev_by_sector.get(key)
    if prev is not None and prev["mainline_days"] is not None:
        d = prev["mainline_days"]
        bin_name = ("≤3" if d <= 3 else "4-7" if d <= 7 else "8-10" if d <= 10
                    else "11-15" if d <= 15 else "16-20" if d <= 20 else ">20")
        delta = (r["strength_score"] or 0) - (prev["strength_score"] or 0)
        by_day[bin_name]["scores"].append(prev["strength_score"] or 0)
        if delta >= 0:
            by_day[bin_name]["next_up"] += 1
        else:
            by_day[bin_name]["next_down"] += 1
    prev_by_sector[key] = r

print("\n[2] 衰减曲线（主线期内，按当日 mainline_days 分箱）:")
print("    天数箱   样本   强度均值   次日强度上升占比")
order = ["≤3", "4-7", "8-10", "11-15", "16-20", ">20"]
for b in order:
    v = by_day.get(b)
    if not v or not v["scores"]:
        continue
    n = len(v["scores"])
    avg = sum(v["scores"]) / n
    up_ratio = v["next_up"] / (v["next_up"] + v["next_down"]) if (v["next_up"] + v["next_down"]) else 0
    print(f"    {b:>5}   {n:>4}   {avg:>7.1f}   {up_ratio:>6.0%}")

# ── 3. 逐日明细：mainline_days=1..20 的次日变化均值（更细粒度）──
daily = defaultdict(list)
prev_by_sector = {}
for r in all_main:
    key = (r["sector_name"],)
    prev = prev_by_sector.get(key)
    if prev is not None and prev["mainline_days"]:
        delta = (r["strength_score"] or 0) - (prev["strength_score"] or 0)
        daily[prev["mainline_days"]].append(delta)
    prev_by_sector[key] = r

print("\n[3] 逐日明细（mainline_days=N 时的次日强度变化均值）:")
print("    day   样本   次日Δ均值   Δ<0占比")
for d in sorted(daily):
    if d > 20:
        continue
    arr = daily[d]
    if len(arr) < 3:
        continue
    avg = sum(arr) / len(arr)
    neg = sum(1 for x in arr if x < 0) / len(arr)
    print(f"    {d:>3}   {len(arr):>4}   {avg:>+8.2f}   {neg:>6.0%}")

# ── 4. 退出前轨迹：进入 exhausted 前 3 天的强度 ──
print("\n[4] 退出方式检查（进入 exhausted 前最后 4 条记录的强度轨迹）:")
exhausts = cur.execute(
    """
    SELECT sector_name, tracking_date FROM node2_sector_trends
    WHERE state = 'exhausted_mainline'
    """
).fetchall()
seen = set()
count = 0
for e in exhausts:
    key = (e["sector_name"], e["tracking_date"])
    if key in seen:
        continue
    seen.add(key)
    trail = cur.execute(
        """
        SELECT tracking_date, state, strength_score, mainline_days
        FROM node2_sector_trends
        WHERE sector_name = ? AND tracking_date <= ?
        ORDER BY tracking_date DESC LIMIT 4
        """,
        (e["sector_name"], e["tracking_date"]),
    ).fetchall()
    trail = list(reversed(trail))
    traj = " → ".join(
        f"{t['strength_score']:.0f}" if t["strength_score"] is not None else "-"
        for t in trail
    )
    days = trail[-1]["mainline_days"]
    print(f"    {e['sector_name'][:8]:<8} {str(e['tracking_date'])[5:]} "
          f"(day {days}): {traj}")
    count += 1
    if count >= 15:
        break

con.close()
