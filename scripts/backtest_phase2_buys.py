"""回测 Phase 2 unified_scoring 每日 Top 候选的历史表现"""
import json, os, sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")
from data_provider.base import DataFetcherManager

DATES = ["20260811","20260812","20260813","20260814","20260817","20260819"]
TOP_N = 3  # 每天取 Top N 候选

manager = DataFetcherManager()

# ── Step 1: 提取每日 Top N 候选 ──
all_picks = []  # [{date, symbol, name, score, is_holding}]

for d in DATES:
    fpath = f"data/got_daily/{d}/portfolio_decision.json"
    if not os.path.exists(fpath):
        continue
    data = json.load(open(fpath, encoding="utf-8"))
    scoring = data.get("unified_scoring", [])
    # 过滤掉持仓（is_holding=True），只看候选股
    candidates = [s for s in scoring if not s.get("is_holding", False)]
    candidates.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    top = candidates[:TOP_N]
    for c in top:
        sym = c.get("symbol", "")
        clean = sym.replace("SH","").replace("SZ","").replace("BJ","")
        all_picks.append({
            "date": d,
            "symbol": clean,
            "name": c.get("name", "?"),
            "score": c.get("composite_score", 0),
            "sector": c.get("sector_name", ""),
        })

print(f"共 {len(all_picks)} 条候选推荐（{len(DATES)}天 × Top{TOP_N}）\n")
print(f"{'日期':>10} {'代码':>8} {'名称':<10} {'评分':>6} {'板块'}")
print("-" * 65)
for p in all_picks:
    print(f"{p['date']:>10} {p['symbol']:>8} {p['name']:<10} {p['score']:>6.1f} {p['sector']}")

# ── Step 2: 回测每只候选 T+1 / T+3 / T+5 ──
print(f"\n{'='*80}")
print("回测: 推荐日收盘 → T+1 / T+3 / T+5 涨跌")
print(f"{'='*80}")

results = []
for p in all_picks:
    sym = p["symbol"]
    rec_date_str = p["date"]
    rec_date = f"{rec_date_str[:4]}-{rec_date_str[4:6]}-{rec_date_str[6:8]}"
    try:
        result = manager.get_daily_data(sym, days=30)
        if isinstance(result, tuple):
            df, _src = result
        else:
            df = result
        if df is None or df.empty:
            print(f"  {sym} {p['name']}: 无K线数据")
            continue

        df["date"] = df["date"].astype(str).str[:10]
        # 找到推荐日或之前最近的交易日
        mask = df["date"] <= rec_date
        if not mask.any():
            rec_idx = 0
        else:
            rec_idx = df[mask].index[-1]

        rec_close = float(df.iloc[rec_idx]["close"])
        n = len(df)

        t1 = float(df.iloc[rec_idx+1]["close"]) if rec_idx+1 < n else None
        t3 = float(df.iloc[rec_idx+3]["close"]) if rec_idx+3 < n else None
        t5 = float(df.iloc[rec_idx+5]["close"]) if rec_idx+5 < n else None

        pct = lambda price: (price - rec_close) / rec_close * 100 if price else None
        r1, r3, r5 = pct(t1), pct(t3), pct(t5)

        results.append({
            **p, "rec_close": rec_close, "t1": r1, "t3": r3, "t5": r5,
            "rec_date": rec_date,
        })

        t1s = f"{r1:+.1f}%" if r1 is not None else "  N/A"
        t3s = f"{r3:+.1f}%" if r3 is not None else "  N/A"
        t5s = f"{r5:+.1f}%" if r5 is not None else "  N/A"
        print(f"  {rec_date} {sym} {p['name']:<10} 收={rec_close:.2f}  T+1={t1s:>7}  T+3={t3s:>7}  T+5={t5s:>7}")
    except Exception as e:
        print(f"  {sym} {p['name']}: 失败 ({e})")

# ── Step 3: 汇总 ──
if results:
    print(f"\n{'='*80}")
    print("汇总统计")
    print(f"{'='*80}")

    for label, key in [("T+1", "t1"), ("T+3", "t3"), ("T+5", "t5")]:
        vals = [r[key] for r in results if r[key] is not None]
        if not vals:
            print(f"  {label}: 无数据")
            continue
        win = sum(1 for v in vals if v > 0)
        avg = sum(vals) / len(vals)
        print(f"  {label}: {len(vals)}只 | 胜率 {win}/{len(vals)} ({win/len(vals)*100:.0f}%) | 均涨跌 {avg:+.2f}% | 最佳 {max(vals):+.1f}% | 最差 {min(vals):+.1f}%")

    # 按日期分组
    print(f"\n按日期分组:")
    by_date = {}
    for r in results:
        by_date.setdefault(r["date"], []).append(r)
    for d in sorted(by_date):
        picks = by_date[d]
        t1v = [p["t1"] for p in picks if p["t1"] is not None]
        if t1v:
            avg = sum(t1v) / len(t1v)
            win = sum(1 for v in t1v if v > 0)
            print(f"  {d}: {len(t1v)}只 T+1 均涨跌={avg:+.1f}% 胜率={win}/{len(t1v)}")
            for p in picks:
                t1s = f"{p['t1']:+.1f}%" if p["t1"] is not None else "N/A"
                print(f"    {p['symbol']} {p['name']:<10} score={p['score']:.0f} T+1={t1s}")
