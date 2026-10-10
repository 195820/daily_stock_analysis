"""FXG veto 双跑一周对照报告（研究用、只读，不入生产链路）。

用途：FXG 切 veto 后，每日并行捕获"剔前原模式"基线（fxg_baseline_scoring.json）。
本脚本扫描 data/got_daily/<date>/ 的三份产物，量化 FXG 家族级剔池的"删了谁 / 谁补位"：
  ① 基线有、veto 无的板块/家族 = FXG 命中的见顶家族（应非空，证 veto 生效）。
  ② veto 有、基线无的板块/家族 = 腾出坑后被补进来的候选（应多为跨家族埋伏/新主线，证"补位轮动"）。
  ③（辅助、best-effort）dropped vs added 两组的前向 T+5/T+10 收益对照。

口径与局限（评审注记，读数前务必知悉）：
- 口径差异：基线 = 纯评分阶段（_score_candidates，未经持仓去重/_allocate/终审），
  veto 正式 = 分配阶段。故③"两版 buy 前向收益"口径不一致、会误估 veto 收益；
  主口径应为①②（池成员/家族差集，同口径有意义）。③仅供方向性参考，且已剔除 is_holding。
- 数据漂移：基线先抓、真实再抓（差数分钟），家族级差集会混入当日榜单/行情自然漂移，
  非纯 FXG 效应；读数时人工剔除当日榜单变动，或取多日汇总/中位数再判。
- 键名归一：基线行 sector/family，veto unified_scoring 行 sector_name/family_name，脚本内部统一为 sector/family。
- 评价窗口：埋伏类看 T+10，不以 T+3/T+5 单独否决（既有教训）。

用法：python scripts/fxg_pair_report.py [--start YYYYMMDD] [--end YYYYMMDD] [--with-returns]
"""
import os
import json
import argparse
from pathlib import Path
from collections import Counter

DAILY_ROOT = Path("data/got_daily")


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[warn] 解析失败 {path}: {exc}")
        return None


def _norm_row(symbol, name, sector, family, action, score, is_holding, src):
    return {
        "symbol": symbol, "name": name,
        "sector": sector or "", "family": family or "",
        "action": action or "", "score": score, "is_holding": bool(is_holding),
        "src": src,
    }


def _baseline_rows(day: Path):
    rec = _load_json(day / "fxg_baseline_scoring.json")
    if not rec:
        return None
    out = []
    for r in rec.get("rows", []):
        out.append(_norm_row(
            r.get("symbol"), r.get("name"), r.get("sector"), r.get("family"),
            r.get("action_recommendation"), r.get("composite_score"),
            r.get("is_holding"), "baseline",
        ))
    return out


def _veto_rows(day: Path):
    rec = _load_json(day / "portfolio_decision.json")
    if not rec:
        return None
    out = []
    for r in rec.get("unified_scoring", []):
        out.append(_norm_row(
            r.get("symbol"), r.get("name"), r.get("sector_name"), r.get("family_name"),
            r.get("action_recommendation"), r.get("composite_score"),
            r.get("is_holding"), "veto",
        ))
    return out


def _candidates(rows):
    """候选口径：非持仓、action 以 buy 开头（新开买入意图）。"""
    return [r for r in (rows or []) if not r["is_holding"] and r["action"].startswith("buy")]


def _diff_sets(baseline, veto, key):
    b = {r[key] for r in baseline if r[key]}
    v = {r[key] for r in veto if r[key]}
    return b, v, (b - v), (v - b)


def _forward_returns(symbols, entry_day: Path, horizon_days):
    """best-effort 前向收益：data_provider 价盘缓存，取不到即返回 None（不臆造）。"""
    if not symbols:
        return None
    try:
        import pandas as pd
        from data_provider.base import DataFetcherManager
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 价盘依赖不可用，跳过前向收益: {exc}")
        return None
    manager = DataFetcherManager()
    entry = _parse_day(entry_day.name)
    vals = []
    for sym in symbols:
        try:
            df = manager.get_daily_data(sym, start_date=entry, end_date=None)
            if df is None or getattr(df, "empty", True) or "close" not in df.columns:
                continue
            closes = df["close"].dropna().tolist()
            if len(closes) < horizon_days + 1:
                continue
            r = closes[horizon_days] / closes[0] - 1.0
            vals.append(r)
        except Exception:  # noqa: BLE001 - 单票取价失败不影响整体
            continue
    if not vals:
        return None
    return sum(vals) / len(vals), len(vals)


def _parse_day(yyyymmdd: str):
    from datetime import datetime
    return datetime.strptime(yyyymmdd, "%Y%m%d").date()


def _day_dirs(start, end):
    if not DAILY_ROOT.exists():
        return []
    days = []
    for d in sorted(DAILY_ROOT.iterdir()):
        if not d.is_dir() or len(d.name) != 8 or not d.name.isdigit():
            continue
        if start and d.name < start:
            continue
        if end and d.name > end:
            continue
        if (d / "fxg_baseline_scoring.json").exists():
            days.append(d)
    return days


def report(start=None, end=None, with_returns=False):
    days = _day_dirs(start, end)
    if not days:
        print(f"[fxg_pair_report] {DAILY_ROOT} 下未找到含 fxg_baseline_scoring.json 的交易日。")
        print("  需先在 .env 置 EXCLUDE_MODE=veto + EXCLUDE_BASELINE_SHADOW=1 并连续跑管线数日。")
        return
    agg_drop_fam, agg_add_fam = Counter(), Counter()
    print(f"===== FXG veto vs 原模式基线 对照（{len(days)} 个交易日）=====\n")
    for day in days:
        baseline = _baseline_rows(day)
        veto = _veto_rows(day)
        if baseline is None or veto is None:
            print(f"[{day.name}] 缺基线或 veto 产物，跳过")
            continue
        bc, vc = _candidates(baseline), _candidates(veto)
        for key in ("family", "sector"):
            _, _, dropped, added = _diff_sets(bc, vc, key)
            if key == "family":
                agg_drop_fam.update(dropped)
                agg_add_fam.update(added)
            print(f"[{day.name}] {key:6s}  Δ被删(FXG命中)={sorted(dropped) or '—'}  "
                  f"Δ补位={sorted(added) or '—'}")
        if with_returns:
            drop_syms = [r["symbol"] for r in bc if r["family"] in
                         {f for f in (_diff_sets(bc, vc, 'family')[2])}]
            add_set = _diff_sets(bc, vc, "family")[3]
            add_syms = [r["symbol"] for r in vc if r["family"] in add_set]
            for h in (5, 10):
                rd = _forward_returns(drop_syms, day, h)
                ra = _forward_returns(add_syms, day, h)
                print(f"    T+{h} 前向(方向性,口径有别): 被删组={_fmt(rd)}  补位组={_fmt(ra)}")
        print()
    print("===== 汇总：被 FXG 删 vs 补位的家族频次（跨日）=====")
    print("Δ被删家族:", dict(agg_drop_fam.most_common()))
    print("Δ补位家族:", dict(agg_add_fam.most_common()))
    print("\n[提醒] 差集含当日榜单自然漂移，需人工/中位数复核；前向收益口径非同阶段，仅方向参考。")


def _fmt(pair):
    if pair is None:
        return "N/A"
    mean, n = pair
    return f"{mean:+.2%} (n={n})"


def main():
    p = argparse.ArgumentParser(description="FXG veto 双跑一周对照")
    p.add_argument("--start", help="YYYYMMDD")
    p.add_argument("--end", help="YYYYMMDD")
    p.add_argument("--with-returns", action="store_true", help="附 best-effort 前向收益对照")
    a = p.parse_args()
    if not os.getcwd().split(os.sep)[-1] == "daily_stock_analysis":
        # 容错：脚本可能从任意 cwd 调用，尝试切到仓库根
        repo = Path(__file__).resolve().parents[1]
        os.chdir(repo)
    report(a.start, a.end, a.with_returns)


if __name__ == "__main__":
    main()
