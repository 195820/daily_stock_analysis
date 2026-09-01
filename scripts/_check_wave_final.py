# -*- coding: utf-8 -*-
"""终验：新波次判据对 11 起真实回跳样本的分类（对照 data/_wave_gap_out.txt 人工判定）。

期望：4 起真二波在回归日重置（wave_days=1），7 起单日噪声冻结（wave_days>1）。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout = open(PROJECT_ROOT / "data" / "_wave_final_check.txt", "w", encoding="utf-8")

from datetime import date
from scripts.backfill_wave_days import calc_wave_days_for_sector
from src.storage import DatabaseManager, Node2SectorTrendRecord

# (板块名, 回归日, 人工判定) — 来自 data/_wave_gap_out.txt
CASES = [
    ("水产品",   date(2026, 9, 1),  "噪声"),
    ("百货",     date(2026, 9, 1),  "噪声"),
    ("种业",     date(2026, 8, 25), "噪声"),
    ("种植业",   date(2026, 9, 1),  "噪声"),
    ("粮食概念", date(2026, 9, 1),  "噪声"),
    ("复合铜箔", date(2026, 8, 31), "真二波"),
    ("石墨烯",   date(2026, 8, 28), "噪声"),
    ("种植业",   date(2026, 8, 27), "噪声"),
    ("焦炭加工", date(2026, 8, 31), "真二波"),
    ("煤炭开采", date(2026, 8, 31), "真二波"),
    ("航运概念", date(2026, 8, 26), "真二波"),
]

db = DatabaseManager()
with db.get_session() as session:
    ok = 0
    print(f"{'板块':<8} {'回归日':<12} {'人工':<6} {'新判据':<6} {'回归日wave_days':<8} 结果")
    print("-" * 72)
    for name, ret_date, label in CASES:
        recs = (
            session.query(Node2SectorTrendRecord)
            .filter(Node2SectorTrendRecord.sector_name == name)
            .order_by(Node2SectorTrendRecord.tracking_date)
            .all()
        )
        res = calc_wave_days_for_sector(recs)
        idx = next((i for i, r in enumerate(recs) if r.tracking_date == ret_date), None)
        if idx is None:
            print(f"{name:<8} {ret_date} {label:<6} 缺记录")
            continue
        wd, w_start, w_peak = res[idx]
        # 重置的严格判据：wave_start 落在回归日（冻结继承的 wd 也可能恰为 1）
        verdict = "真二波" if w_start == ret_date else "噪声"
        passed = verdict == label
        ok += int(passed)
        print(f"{name:<8} {ret_date} {label:<6} {verdict:<6} {wd:<14} {'PASS' if passed else 'FAIL'}")
    print("-" * 72)
    print(f"命中率: {ok}/{len(CASES)}")
