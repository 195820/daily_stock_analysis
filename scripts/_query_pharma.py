import sys
sys.path.insert(0, r"E:\daily_stock_analysis")
from src.storage import DatabaseManager, Node2SectorTrendRecord

db = DatabaseManager()
codes = ['880729', '880652', '880681', '880400', '880530', '880328']
names = {'880729':'CXO', '880652':'创新药', '880681':'减肥药', '880400':'医药', '880530':'合成生物', '880328':'黄金'}

with db.get_session() as s:
    for code in codes:
        recs = s.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.sector_code == code
        ).order_by(Node2SectorTrendRecord.tracking_date.desc()).limit(7).all()
        print(f"\n=== {names[code]} ({code}) ===")
        for r in recs:
            state = r.state or "(None)"
            score = r.strength_score or 0
            dwell = r.dwell_days or 0
            signal = r.unified_signal or "(None)"
            ratio = r.rrg_ratio_ema if r.rrg_ratio_ema is not None else "-"
            mom = r.rrg_momentum_ema if r.rrg_momentum_ema is not None else "-"
            md = r.mainline_days or 0
            print(f"  {r.tracking_date} | {state:<24} | score={score:.1f} | dwell={dwell} | ratio_ema={ratio} | mom_ema={mom} | mainline_days={md}")
