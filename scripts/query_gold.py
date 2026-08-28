# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sqlite3

conn = sqlite3.connect(r'E:\daily_stock_analysis\data\stock_analysis.db')
conn.row_factory = sqlite3.Row

cur = conn.execute('''SELECT sector_code, sector_name, tracking_date, state, strength_score,
       is_mainline, mainline_days, trend_direction, dwell_days, lifecycle_phase
FROM node2_sector_trends WHERE sector_name LIKE '%黄金%'
ORDER BY sector_code, tracking_date DESC''')
rows = cur.fetchall()
by_code = {}
for r in rows:
    code = r['sector_code']
    if code not in by_code:
        by_code[code] = {'name': r['sector_name'], 'records': []}
    by_code[code]['records'].append(r)

for code, info in by_code.items():
    print(f'--- {info["name"]} ({code}) ---')
    for r in info['records'][:20]:
        ml = '*' if r['is_mainline'] else ' '
        lc = str(r['lifecycle_phase'] or '-')
        td = str(r['trend_direction'] or '-')
        sc = float(r['strength_score'] or 0)
        md = int(r['mainline_days'] or 0)
        dd = int(r['dwell_days'] or 0)
        state = str(r['state'] or '-')
        print(f'  {ml} {r["tracking_date"]} | {state:22s} | {sc:5.1f} | ml={md} dwell={dd} | {td:12s} | {lc}')
    print()

print('=== sector_frames ===')
cur = conn.execute('''SELECT frame_group, entry_date, last_update, group_score, is_active, exit_reason, consecutive_low_days, member_codes
FROM sector_frames WHERE frame_group LIKE '%黄金%' OR member_codes LIKE '%黄金%' ORDER BY entry_date DESC''')
for r in cur.fetchall():
    act = 'ACTIVE' if r['is_active'] else 'EXITED'
    print(f'  [{act}] {r["entry_date"]} -> {r["last_update"]} | {r["frame_group"]} | score={r["group_score"] or 0:.0f} | exit={r["exit_reason"] or "-"} | low={r["consecutive_low_days"] or 0}')
    members = str(r['member_codes'] or '')[:100]
    print(f'    members: {members}')
conn.close()
