# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sqlite3
from datetime import date

conn = sqlite3.connect(r'E:\daily_stock_analysis\data\stock_analysis.db')
conn.row_factory = sqlite3.Row

# 活跃框
print('=== 当前活跃框（is_active=1）===')
cur = conn.execute('''SELECT frame_group, entry_date, last_update, group_score,
    consecutive_low_days, consecutive_rise_days, member_codes, exit_reason
    FROM sector_frames WHERE is_active = 1
    ORDER BY group_score DESC''')
rows = cur.fetchall()
print(f'共 {len(rows)} 个')
for r in rows:
    members = (r['member_codes'] or '').strip('[]').replace('"', '')
    entry = r['entry_date'] or ''
    last = r['last_update'] or ''
    try:
        days = (date.fromisoformat(last) - date.fromisoformat(entry)).days
    except:
        days = 0
    gs = float(r['group_score'] or 0)
    cld = int(r['consecutive_low_days'] or 0)
    crd = int(r['consecutive_rise_days'] or 0)
    print(f'  {r["frame_group"]:8s} | score={gs:5.1f} | {entry} ({days:2d}天) | low={cld} rise={crd} | {members}')

print()
print('=== 近 30 天退出框 ===')
cur = conn.execute('''SELECT frame_group, entry_date, last_update, group_score, exit_reason, consecutive_low_days
    FROM sector_frames WHERE is_active = 0
    AND last_update >= date('now', '-30 days')
    ORDER BY last_update DESC''')
rows = cur.fetchall()
print(f'共 {len(rows)} 个')
for r in rows:
    gs = float(r['group_score'] or 0)
    cld = int(r['consecutive_low_days'] or 0)
    print(f'  {r["frame_group"]:8s} | score={gs:5.1f} | {r["entry_date"]} -> {r["last_update"]} | exit={r["exit_reason"] or "-"} | low={cld}')

# 配合今日 Node 2 输出看埋伏
print()
print('=== 今日(0826)埋伏板块 ===')
import json
from pathlib import Path
sector_path = Path(r'E:\daily_stock_analysis\data\got_daily\20260826\sector.json')
if sector_path.exists():
    with open(sector_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    ambush = data.get('ambush', [])
    print(f'共 {len(ambush)} 个埋伏板块')
    for a in ambush:
        name = a.get('name', '?')
        code = a.get('code', '?')
        sc = a.get('strength_score', 0)
        state = a.get('state', '?')
        rrg = a.get('rrg', {}).get('quadrant', '?')
        traj = a.get('strength_trajectory', [])
        print(f'  {code} {name:10s} | score={sc:5.1f} | state={state:15s} | rrg={rrg:10s} | traj={[round(t,1) for t in (traj or [])[-5:]]}')

conn.close()
