#!/usr/bin/env python
"""Node 2 保真重放：用历史 OHLCV + 归档 MarketContext 重跑 Node 2，对比归档 state。

只在临时 DB 上写 node2_sector_trends，不碰生产库。

用法
----
  python scripts/replay_node2_fidelity.py --date 20261023
  python scripts/replay_node2_fidelity.py --start 20260902 --end 20261103
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import date as real_date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

REPLAY_DIR = ROOT / "data" / "_replay"
TEMP_DB = REPLAY_DIR / f"node2_replay_{os.getpid()}.db"
PROD_DB = ROOT / "data" / "stock_analysis.db"
CACHE = REPLAY_DIR / "board_ohlcv_cache.json"
INTRADAY_CACHE = REPLAY_DIR / "board_intraday_15m_cache.json"
DAILY = ROOT / "data" / "got_daily"

# 必须在 import src.* 之前设置，DatabaseManager 单例才会用临时库
os.environ["DATABASE_PATH"] = str(TEMP_DB)
os.environ.setdefault("GOT_FAMILY_EMBED_FALLBACK", "off")

import pandas as pd  # noqa: E402

from audit_theme_episode_ledger import BUCKETS, parse_day  # noqa: E402
from audit_theme_episode_forward_returns import load_name_code  # noqa: E402


def load_details_day(j: dict) -> dict[str, dict]:
    """单日板块 detail；桶优先级同 Node 2 输出。"""
    out: dict[str, dict] = {}
    for key, _label in BUCKETS:
        items = j.get(key) or []
        if key == "active_mainline":
            for group in items:
                for m in group.get("members") or []:
                    name = (m.get("name") or "").strip("\x00")
                    if name and name not in out:
                        out[name] = m
        else:
            for m in items:
                name = (m.get("name") or "").strip("\x00")
                if name and name not in out:
                    out[name] = m
    return out
from src.got.nodes import sector_analysis as sa  # noqa: E402
from src.got.schemas import MarketContext  # noqa: E402
from src.storage import Base, DatabaseManager, Node2SectorTrendRecord  # noqa: E402


class FakeDate(real_date):
    _today: real_date = real_date(2026, 1, 1)

    @classmethod
    def today(cls) -> "FakeDate":
        return cls(cls._today.year, cls._today.month, cls._today.day)


class ReplayFetcher:
    """只读历史缓存；盘中按 run_timestamp 取 15m bar，收盘后用日线。"""

    def __init__(self, cache: dict, intraday: dict, as_of: real_date,
                 run_ts: datetime | None, name_map: dict[str, str] | None = None):
        self.cache = cache
        self.intraday = intraday
        self.as_of = as_of
        self.run_ts = run_ts
        # code → name（来自生产库映射；缺失者记 unresolved，不参与通过判定）
        self.name_map = name_map or {}
        self.unresolved: list[str] = []

    def _intraday_bar(self, code: str, day: str) -> dict | None:
        if self.run_ts is None:
            return None
        hhmm = self.run_ts.strftime("%H:%M")
        bars = [
            r for r in self.intraday.get(code, [])
            if r["datetime"][:10] == day and r["datetime"][11:16] <= hhmm
        ]
        if not bars:
            return None
        return {
            "date": day,
            "open": bars[0]["open"],
            "high": max(b["high"] for b in bars),
            "low": min(b["low"] for b in bars),
            "close": bars[-1]["close"],
            "volume": sum(b["vol"] for b in bars),
            "amount": sum(b["amount"] for b in bars),
            "up_count": bars[-1]["up_count"],
            "down_count": bars[-1]["down_count"],
        }

    def get_board_kline(self, code: str, board_type: str = "concept", days: int = 90):
        b = self.cache.get(code)
        if not b:
            return None
        day = self.as_of.isoformat()
        hhmm = self.run_ts.strftime("%H:%M") if self.run_ts else "99:99"
        intraday = "09:45" <= hhmm < "15:00"
        # 盘中/盘前排除当日日线（未来 close）；收盘后包含
        cut = [i for i, d in enumerate(b["dates"])
               if (d < day if hhmm < "15:00" else d <= day)]
        if not cut:
            return None
        idx = cut[-days:]
        df = pd.DataFrame({
            "date": [b["dates"][i] for i in idx],
            "open": [b["open"][i] for i in idx],
            "high": [b["high"][i] for i in idx],
            "low": [b["low"][i] for i in idx],
            "close": [b["close"][i] for i in idx],
            "volume": [b["volume"][i] for i in idx],
            "amount": [b["amount"][i] for i in idx],
            "up_count": [b["up_count"][i] for i in idx],
            "down_count": [b["down_count"][i] for i in idx],
        })
        if intraday:
            bar = self._intraday_bar(code, day)
            if bar is not None:
                df = pd.concat([df, pd.DataFrame([bar])], ignore_index=True)
            else:
                # 没有盘中数据：按用户规则退回收盘（唯一例外，避免丢板块）
                full = [i for i, d in enumerate(b["dates"]) if d <= day][-days:]
                df = pd.DataFrame({
                    "date": [b["dates"][i] for i in full],
                    "open": [b["open"][i] for i in full],
                    "high": [b["high"][i] for i in full],
                    "low": [b["low"][i] for i in full],
                    "close": [b["close"][i] for i in full],
                    "volume": [b["volume"][i] for i in full],
                    "amount": [b["amount"][i] for i in full],
                    "up_count": [b["up_count"][i] for i in full],
                    "down_count": [b["down_count"][i] for i in full],
                })
        return df

    def _cutoff_ok(self, dates: list[str]) -> bool:
        """缓存里是否有 ≤ as_of 的有效 bar（盘中排除当日日线，避免未来函数）。"""
        day = self.as_of.isoformat()
        hhmm = self.run_ts.strftime("%H:%M") if self.run_ts else "99:99"
        return any((d < day if hhmm < "15:00" else d <= day) for d in dates)

    def get_all_concept_boards(self) -> list[dict]:
        """`--full-discovery` 用：截至回放日的全量缓存板块（code/name）。

        名称缺失（生产库映射也没有）→ 记 `unresolved` 并**排除**，不参与通过样本。
        """
        self.unresolved = []
        out: list[dict] = []
        for code, payload in self.cache.items():
            if not self._cutoff_ok(payload.get("dates") or []):
                continue
            name = self.name_map.get(code)
            if not name:
                self.unresolved.append(code)
                continue
            out.append({"code": code, "name": name})
        return out

    def get_board_quick_change(self, codes: list[str], concurrency: int = 20) -> dict[str, dict]:
        """快筛三窗口涨跌幅（与 `MootdxFetcher.get_board_quick_change` 同口径）。

        只读 ≤ as_of 的 bar（`get_board_kline` 已截断），不做未来数据插值。
        """
        out: dict[str, dict] = {}
        for code in codes:
            df = self.get_board_kline(code, "concept", 11)
            if df is None or len(df) < 6:
                continue
            closes = [float(c) for c in df["close"].tolist()]
            if any(c <= 0 for c in closes):
                continue
            n = len(closes)
            change_1d = (closes[-1] - closes[-2]) / closes[-2] * 100
            change_3d = (closes[-1] - closes[-4]) / closes[-4] * 100
            change_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
            daily = [
                (closes[i] - closes[i - 1]) / closes[i - 1] * 100
                for i in range(max(1, n - 5), n)
            ]
            out[code] = {
                "change_1d": round(change_1d, 2),
                "change_3d": round(change_3d, 2),
                "change_5d": round(change_5d, 2),
                "has_crash": any(r < -5.0 for r in daily),
            }
        return out


def _bucket_map(result) -> dict[str, str]:
    """板块名 → 输出桶（active_mainline/ambush/... 先到先得，与归档口径一致）。"""
    out: dict[str, str] = {}
    for group in getattr(result, "active_mainline", None) or []:
        for member in getattr(group, "members", None) or []:
            out.setdefault((member.name or "").strip("\x00"), "active_mainline")
    for key in ("ambush", "near_miss_ambush", "cooling_down", "exhausted_mainline",
                "defensive_rotation", "top_by_score"):
        for item in getattr(result, key, None) or []:
            out.setdefault((item.name or "").strip("\x00"), key)
    return out


def prepare_temp_db() -> None:
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    if TEMP_DB.exists():
        TEMP_DB.unlink()
    db = DatabaseManager()
    Base.metadata.create_all(db._engine)


def seed_previous(start: real_date) -> int:
    """把生产库中 start 之前的 node2_sector_trends 行复制到临时库。"""
    con = sqlite3.connect(TEMP_DB)
    try:
        con.execute("ATTACH DATABASE ? AS prod", (str(PROD_DB),))
        cols = [r[1] for r in con.execute("PRAGMA table_info(node2_sector_trends)")]
        # 只复制**生产库也存在**的列：SQLite 对无法解析的双引号标识符会回退成字符串字面量，
        # 多出来的新列（如 lifecycle_*）若直接 SELECT 会把列名当值写进 DATE 列并炸 ISO 解析。
        prod_cols = {r[1] for r in con.execute("PRAGMA prod.table_info(node2_sector_trends)")}
        cols = [c for c in cols if c in prod_cols]
        collist = ",".join(f'"{c}"' for c in cols)
        cur = con.execute(
            f"INSERT INTO node2_sector_trends ({collist}) "
            f"SELECT {collist} FROM prod.node2_sector_trends WHERE tracking_date < ?",
            (start.isoformat(),),
        )
        con.commit()
        return int(cur.rowcount or 0)
    finally:
        con.close()


def load_prod_day(d: real_date) -> dict[str, tuple[str, float]]:
    """生产库当日全量 state/strength；归档桶会截断，不能作为真值。"""
    con = sqlite3.connect(PROD_DB)
    try:
        rows = con.execute(
            "select sector_name, state, strength_score from node2_sector_trends "
            "where tracking_date = ?",
            (d.isoformat(),),
        ).fetchall()
    finally:
        con.close()
    return {
        (name or "").strip("\x00"): (state, float(strength or 0))
        for name, state, strength in rows
    }


def load_archive(d: real_date):
    day = DAILY / d.strftime("%Y%m%d")
    sector = json.loads((day / "sector.json").read_text(encoding="utf-8"))
    market = MarketContext.model_validate(
        json.loads((day / "market.json").read_text(encoding="utf-8"))
    )
    details = load_details_day(sector)
    strength = {k.strip("\x00"): float(v) for k, v in (sector.get("strength_lookup") or {}).items()}
    codes = {name: det.get("code") for name, det in details.items() if det.get("code")}
    prod = load_prod_day(d)
    archive_state = {}
    for name in strength:
        if name in prod:
            archive_state[name] = prod[name][0]
        elif name in details:
            archive_state[name] = details[name].get("state") or "neutral"
        else:
            archive_state[name] = "neutral"
    return sector, market, strength, codes, archive_state, prod


def build_universe(strength: dict, archive_codes: dict, cache: dict, db_code: dict[str, str]):
    out = []
    missing = []
    for name in sorted(strength):
        code = archive_codes.get(name) or db_code.get(name)
        if not code or code not in cache:
            missing.append(name)
            continue
        out.append({"code": code, "name": name, "category": "replay", "group": "replay",
                    "frame_group": "其他", "change_pct": 0.0, "up_count": 0, "down_count": 0})
    return out, missing


def load_run_ts(d: real_date) -> datetime | None:
    p = DAILY / d.strftime("%Y%m%d") / "meta.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text(encoding="utf-8"))
    ts = m.get("run_timestamp") or m.get("snapshot_time")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def run_one(d: real_date, cache: dict, intraday: dict, db_code: dict[str, str],
            verbose: bool, *, full_discovery: bool = False,
            name_map: dict[str, str] | None = None) -> dict:
    sector, market, strength, archive_codes, archive_state, prod = load_archive(d)
    universe, missing = build_universe(strength, archive_codes, cache, db_code)
    hot = sector.get("ths_hot_sectors") or {}
    run_ts = load_run_ts(d)

    node = sa.SectorAnalysisNode(use_mock=False)
    fetcher = ReplayFetcher(cache, intraday, d, run_ts, name_map=name_map)
    node._fetcher_manager = fetcher

    async def replay_discover(quality):
        quality.available_fields.append("replay_universe")
        return universe

    async def replay_hot():
        return hot

    async def no_industry(quality):
        return []

    if not full_discovery:
        node._discover_boards = replay_discover
    node._fetch_ths_hot_sectors = replay_hot
    node._prefetch_constituent_stats = lambda *a, **k: None
    node._fetch_and_score_industry_boards = no_industry

    FakeDate._today = d
    sa.date = FakeDate
    try:
        result = asyncio.run(node.run(market))
    finally:
        sa.date = real_date

    db = DatabaseManager()
    with db.get_session() as session:
        rows = session.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.tracking_date == d
        ).all()
        replay = {r.sector_name: (r.state, float(r.strength_score or 0)) for r in rows}
        code_by_name = {(r.sector_name or "").strip("\x00"): r.sector_code for r in rows}
        # 生命周期以**落库行**为准（补写行也带 lifecycle_*；phase_lookup 只覆盖当日 analyzed）
        lc_by_name = {
            (r.sector_name or "").strip("\x00"): {
                "phase": getattr(r, "lifecycle_continuous_phase", None),
                "adapted_phase": getattr(r, "lifecycle_phase", None),
                "phase_age": getattr(r, "lifecycle_phase_age", None),
                "phase_dwell": getattr(r, "lifecycle_phase_dwell", None),
                "data_status": getattr(r, "lifecycle_data_status", None),
                "time_weight": getattr(r, "lifecycle_time_weight", None),
                "hard_break": getattr(r, "lifecycle_transition_reason", None) == "hard_break",
            }
            for r in rows
        }

    common = sorted(set(replay) & set(archive_state))
    match = sum(1 for n in common if replay[n][0] == archive_state[n])
    mae = (sum(abs(replay[n][1] - strength.get(n, 0)) for n in common) / len(common)
           if common else 0.0)
    confusion = Counter((archive_state[n], replay[n][0]) for n in common)
    top_diff = sorted(
        ((abs(replay[n][1] - strength.get(n, 0)), n, strength.get(n), replay[n][1])
         for n in common),
        reverse=True,
    )[:5]

    if verbose:
        print(f"\n== {d} (run={run_ts}) ==")
        print(f"  archive={len(strength)} prod={len(prod)} universe={len(universe)} "
              f"replay={len(replay)} common={len(common)} missing_code={len(missing)}")
        print(f"  state_match={match}/{len(common)} ({match/len(common):.1%})  strength_MAE={mae:.2f}")
        print("  confusion (archive→replay):")
        for (a, r), n in confusion.most_common(10):
            if a != r:
                print(f"    {a:>20} → {r:<20} {n}")
        print("  top strength diffs:")
        for diff, n, a, r in top_diff:
            print(f"    {n:<12} archive={a:.1f} replay={r:.1f} Δ={diff:.1f}")
        tg = market.tech_growth
        sig = getattr(tg, "signals", None)
        print(f"  market: trend={getattr(tg,'trend',None)} "
              f"short={getattr(getattr(sig,'short_term',None),'trend',None)} "
              f"medium={getattr(getattr(sig,'medium_term',None),'trend',None)} "
              f"today={market.today_change}")
        mism = [(n, archive_state[n], replay[n][0], strength.get(n), replay[n][1])
                for n in common if archive_state[n] != replay[n][0]][:8]
        print("  mismatch samples:")
        for n, a, r, sa_, sr in mism:
            print(f"    {n:<12} archive={a:<18} replay={r:<18} strength {sa_:.1f}→{sr:.1f}")
        for target in ("减速器", "短剧游戏"):
            if target in replay:
                print(f"  {target}: archive={archive_state.get(target)} "
                      f"replay={replay[target][0]} "
                      f"strength {strength.get(target):.1f}→{replay[target][1]:.1f}")

    bucket = _bucket_map(result)
    analyzed_names = set((getattr(result, "strength_lookup", None) or {}).keys())
    board_rows: dict[str, dict] = {}
    for name, (state, replay_strength) in replay.items():
        info = lc_by_name.get(name) or {}
        board_rows[name] = {
            "code": code_by_name.get(name),
            "state": state,
            "strength": replay_strength,
            "buckets": bucket.get(name),
            "route_state": state,
            # 当日是否有完整 K 线并参与截面（补写行 False → 不进「完整 board」判据）
            "analyzed": name in analyzed_names,
            "phase": info.get("phase"),
            "adapted_phase": info.get("adapted_phase"),
            "phase_age": info.get("phase_age"),
            "phase_dwell": info.get("phase_dwell"),
            "hard_break": info.get("hard_break"),
            "data_status": info.get("data_status"),
            "time_weight": info.get("time_weight"),
        }
    return {"date": d.isoformat(), "common": len(common), "match": match,
            "mae": mae, "confusion": confusion,
            "boards": board_rows,
            "launch_watch": getattr(result, "launch_watch", None) or [],
            "ambush_codes": [s.code for s in getattr(result, "ambush", None) or []],
            "phase_data_quality": getattr(result, "phase_data_quality", None) or {},
            "launch_topn": sa.get_launch_topn(),
            "unresolved": sorted(fetcher.unresolved)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Node 2 保真重放")
    ap.add_argument("--date", default="")
    ap.add_argument("--start", default="20260902")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个日期（冒烟）")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--full-discovery", action="store_true",
                    help="不 monkeypatch _discover_boards：跑通道 1/2/registry/启动供给（enabled 臂）")
    ap.add_argument("--json-out", default="",
                    help="逐日 state/strength/buckets/phase/age/route_state/unresolved 输出路径"
                         "（只写临时目录）")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    intraday = (
        json.loads(INTRADAY_CACHE.read_text(encoding="utf-8"))
        if INTRADAY_CACHE.exists() else {}
    )
    con = sqlite3.connect(PROD_DB)
    try:
        db_code = {}
        for name, code in con.execute(
            "select sector_name, sector_code from node2_sector_trends "
            "group by sector_name, sector_code"
        ):
            db_code.setdefault((name or "").strip("\x00"), code)
    finally:
        con.close()

    name_map: dict[str, str] = {}
    for _name, _code in load_name_code(set(cache)).items():
        name_map.setdefault(_code, _name)

    if args.date:
        dates = [parse_day(args.date)]
    else:
        start, end = parse_day(args.start), parse_day(args.end)
        dates = []
        cur = start
        while cur <= end:
            if (DAILY / cur.strftime("%Y%m%d") / "sector.json").exists():
                dates.append(cur)
            cur = real_date.fromordinal(cur.toordinal() + 1)
        if args.limit:
            dates = dates[: args.limit]

    print(f"缓存板块: {len(cache)}，15m板块: {len(intraday)}，重放日期: {len(dates)}")
    prepare_temp_db()
    if dates:
        seeded = seed_previous(dates[0])
        print(f"临时库 seed: {seeded} 行 (< {dates[0]})")

    total_common = total_match = 0
    verbose = args.verbose or len(dates) == 1
    json_out: dict = {
        "mode": "full_discovery" if args.full_discovery else "archive_universe",
        "enabled_env": os.environ.get("GOT_NODE2_LIFECYCLE", "legacy"),
        "days": [],
    }
    for d in dates:
        if not load_prod_day(d):
            print(f"{d}: skip (no production truth)")
            continue
        res = run_one(d, cache, intraday, db_code, verbose=verbose,
                      full_discovery=args.full_discovery, name_map=name_map)
        total_common += res["common"]
        total_match += res["match"]
        json_out["days"].append({
            key: value for key, value in res.items() if key != "confusion"
        })
        if not verbose and res["common"]:
            pct = res["match"] / res["common"]
            print(f"{d}: {res['match']}/{res['common']} = {pct:.1%}  MAE={res['mae']:.2f}")

    if total_common:
        print(f"\n总保真: {total_match}/{total_common} = {total_match/total_common:.1%}")
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(json_out, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
        print(f"JSON 输出: {out_path}（{len(json_out['days'])} 日）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
