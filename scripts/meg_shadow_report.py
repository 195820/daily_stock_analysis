#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MEG 动能衰竭门影子日报聚合器（§7.7 观测期）

扫描 `data/got_daily/*/shadow_meg.json`（由 `portfolio_decision._save_shadow_records` 落盘，
**仅 GOT_MOM_EXHAUST_GATE_ENABLED=1 时产生**），输出观测期读数：

1. **命中率**（纯本地，无需行情）：逐日 + 累计的 `evaluated / action_hits / sizing_hits / hit_rate`，
   以及被拦标的/板块频次分布。用于回答"MEG 到底多久拦一次、拦得狠不狠"。
2. **假阳性**（`--forward N`，best-effort）：对已满足 ≥N 交易日的拦截股，取 N 日前向收益，
   判定"本应降级 buy_trend→watch"的追涨股事后是否真的走差——
   走差（前向收益 ≤ 0）= **正确拦截**；上涨 = **假阳性**（误杀）。

回测证真口径（§7.8.1，2026-10-08）：acceleration+snap T+5 横截面超额 -3.07%，
snap-thrust 差 -2.54%（Welch t=-3.28, p=0.001），立案案例 880952 芯片 9/23 命中 -9.41%。

用法：
    python scripts/meg_shadow_report.py                 # 近 30 日命中率聚合
    python scripts/meg_shadow_report.py --days 60        # 近 60 日窗口
    python scripts/meg_shadow_report.py --forward 5       # 叠加 T+5 前向假阳性核算（需行情）
    python scripts/meg_shadow_report.py --days 90 --forward 5
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
DAILY = ROOT / "data" / "got_daily"
ARTIFACT = "shadow_meg.json"


def _load_records(days: int):
    """按日期窗口加载 shadow_meg.json（含零命中日：只要当日 MEG 启用即有产物）。"""
    if not DAILY.exists():
        print(f"[MEG] 归档目录不存在：{DAILY}（先以 shadow 模式跑几次 --got 再来）")
        return []
    cutoff = datetime.now() - timedelta(days=days)
    recs = []
    for d in sorted(DAILY.iterdir()):
        if not d.is_dir():
            continue
        try:
            day = datetime.strptime(d.name, "%Y%m%d")
        except ValueError:
            continue
        if day < cutoff:
            continue
        p = d / ARTIFACT
        if not p.exists():
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  ! 跳过 {p.name}: {exc}")
            continue
        rec["_day"] = day
        rec["_dir"] = d.name
        recs.append(rec)
    return recs


def _aggregate(recs):
    """逐日 + 累计命中率表 + 频次分布（纯本地）。"""
    print("=" * 92)
    print("MEG 影子 · 命中率聚合（shadow_meg.json）")
    print("=" * 92)
    if not recs:
        print("窗口内无 shadow_meg.json —— MEG 尚未在 shadow 模式运行，或早于起算日。")
        return
    tot_eval = tot_act = tot_sz = 0
    stock_freq = {}
    sector_freq = {}
    print(f"{'日期':<12}{'mode':<8}{'评估':>6}{'层2拦':>7}{'层3缩':>7}{'命中率':>9}")
    print("-" * 92)
    for r in recs:
        ev = r.get("evaluated", 0) or 0
        act = len(r.get("flagged_stocks") or [])
        sz = len(r.get("flagged_sectors") or [])
        tot_eval += ev
        tot_act += act
        tot_sz += sz
        rate = act / ev * 100 if ev else 0.0
        print(f"{r['_dir']:<12}{r.get('mode', '-'):<8}{ev:>6}{act:>7}{sz:>7}{rate:>8.1f}%")
        for f in r.get("flagged_stocks") or []:
            stock_freq[f"{f.get('symbol')} {f.get('name')}"] = stock_freq.get(f"{f.get('symbol')} {f.get('name')}", 0) + 1
        for s in r.get("flagged_sectors") or []:
            sector_freq[s.get("sector_name")] = sector_freq.get(s.get("sector_name"), 0) + 1
    print("-" * 92)
    overall = tot_act / tot_eval * 100 if tot_eval else 0.0
    print(f"累计：{len(recs)} 个运行日 · 评估 {tot_eval} 只 · 层2 拦 {tot_act} 只 "
          f"(命中率 {overall:.1f}%) · 层3 缩 {tot_sz} 次")
    if sector_freq:
        top = sorted(sector_freq.items(), key=lambda x: x[1], reverse=True)[:8]
        print("高频被拦板块：" + "、".join(f"{k}×{v}" for k, v in top))
    if stock_freq:
        top = sorted(stock_freq.items(), key=lambda x: x[1], reverse=True)[:8]
        print("高频被拦个股：" + "、".join(f"{k}×{v}" for k, v in top))


def _forward_fp(recs, horizon: int):
    """best-effort 前向假阳性核算：对 ≥horizon 交易日前的拦截股取 N 日前向收益。"""
    print()
    print("=" * 92)
    print(f"MEG 影子 · T+{horizon} 前向假阳性核算（正确拦截=前向≤0；假阳性=前向>0）")
    print("=" * 92)
    try:
        from data_provider import get_fetcher
    except Exception as exc:  # pragma: no cover - 环境依赖
        print(f"  ! 无法导入 data_provider，跳过前向核算：{exc}")
        return
    fetcher = None
    try:
        fetcher = get_fetcher()
    except Exception as exc:  # pragma: no cover
        print(f"  ! fetcher 初始化失败，跳过前向核算：{exc}")
        return

    # 去重：同一 code 在同一 flag 日只算一次
    seen = set()
    correct = fp = pending = failed = 0
    rets = []
    for r in recs:
        flag_day = r["_day"]
        # 需 flag 日后至少有 horizon 个交易日已过去（粗略用自然日 ≥ horizon*1.6 天）
        if flag_day > datetime.now() - timedelta(days=int(horizon * 1.6) + 2):
            continue
        for f in r.get("flagged_stocks") or []:
            code = f.get("symbol")
            key = (code, r["_dir"])
            if not code or key in seen:
                continue
            seen.add(key)
            try:
                start = flag_day.strftime("%Y-%m-%d")
                end = (flag_day + timedelta(days=horizon * 2 + 20)).strftime("%Y-%m-%d")
                df = fetcher.fetch_daily(code, start, end)
                if df is None or len(df) == 0:
                    failed += 1
                    continue
                df = df.sort_values("date").reset_index(drop=True)
                idx = next((i for i, dt in enumerate(df["date"])
                            if (dt if isinstance(dt, datetime) else pd_to_dt(dt)) >= flag_day), None)
                if idx is None or idx + horizon >= len(df):
                    pending += 1  # 前向窗口尚未成熟
                    continue
                entry = float(df.iloc[idx]["close"])
                exitp = float(df.iloc[idx + horizon]["close"])
                if entry <= 0:
                    failed += 1
                    continue
                ret = (exitp - entry) / entry * 100
                rets.append((code, f.get("name"), ret))
                if ret <= 0:
                    correct += 1
                else:
                    fp += 1
            except Exception as exc:  # pragma: no cover
                failed += 1
                print(f"  ! {code} 前向核算异常：{exc}")

    done = correct + fp
    if done == 0:
        print(f"暂无成熟样本（pending={pending}, 取数失败={failed}）——观测期未满 {horizon} 交易日属正常。")
        return
    print(f"成熟样本 {done} 只：正确拦截 {correct} ({correct / done * 100:.1f}%) · "
          f"假阳性 {fp} ({fp / done * 100:.1f}%) · 待成熟 {pending} · 取数失败 {failed}")
    if rets:
        avg = sum(x[2] for x in rets) / len(rets)
        print(f"拦截股平均 T+{horizon} 前向收益：{avg:+.2f}%（越负说明拦得越准）")
        worst = sorted(rets, key=lambda x: x[2])[:5]
        fplist = sorted(rets, key=lambda x: x[2], reverse=True)[:5]
        print("  拦得最准：" + "、".join(f"{c} {n}{v:+.1f}%" for c, n, v in worst))
        print("  假阳性（误杀）：" + "、".join(f"{c} {n}{v:+.1f}%" for c, n, v in fplist))
    print("  判读：命中率≈回测 snap 占比且假阳性偏低 → 支持切 veto；假阳性高 → 复核 decay 阈值或加 confirm。")


def pd_to_dt(x):
    import pandas as pd
    return pd.to_datetime(str(x))


def main():
    ap = argparse.ArgumentParser(description="MEG 动能衰竭门影子日报聚合器")
    ap.add_argument("--days", type=int, default=30, help="回看窗口（自然日，默认 30）")
    ap.add_argument("--forward", type=int, default=0, help="前向假阳性核算的交易日数（如 5=T+5；0=跳过）")
    args = ap.parse_args()

    recs = _load_records(args.days)
    _aggregate(recs)
    if args.forward > 0:
        _forward_fp(recs, args.forward)
    print()


if __name__ == "__main__":
    main()
