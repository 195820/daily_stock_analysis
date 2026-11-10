"""Node 2 锚点验收：四个已归档事实板块必须能在恰当时间进入 launch 输出。

锚点（§2 / 用户指定）：
  A1 880675 减速器    2026-10-23 关键误路由日（E16 防守出口）
  A2 880692 短剧游戏   2026-10-29 新波第 2 天（容量截断）
  A3 880420 影视音像   2026-11-02 整条传媒/游戏分支非交易态
  A4 880710 种业      2026-08-21 8 月中农业一波（无相位归档，仅单步）

两个数据源，必须分开读：
  1. **归档相位路径** `data/_replay/ab/node2_lifecycle_backtest.json` 的 `enabled_days`：
     真实生产输入 + 真实 age；能回答「当天是否已启动、是否被容量截断」。
     覆盖 2026-09-02 ~ 2026-11-03，**8 月无归档**。
  2. **单步重建** `board_ohlcv_cache`：`previous=None`，只能回答「当天该不该新启动」；
     全宇宙重算分位，输入宇宙与生产不同，与归档不一致时以归档为准。

「恰当时间」= 可接受窗口内首次 `in_output=True`；窗口内进池但被截记为「容量截断」，
不是「没发现」。

用法：
  python scripts/audit_node2_anchor_boards.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from src.got.phase_model import build_panel  # noqa: E402
from src.got.sector_lifecycle import compute_launch_score  # noqa: E402

REPLAY = ROOT / "data" / "_replay"
CACHE = REPLAY / "board_ohlcv_cache.json"
AB = REPLAY / "ab" / "node2_lifecycle_backtest.json"

ANCHORS = [
    {"key": "A1", "code": "880675", "name": "减速器", "anchor": "2026-10-23",
     "accept_from": "2026-10-23", "note": "状态边：E16 只能回 neutral"},
    {"key": "A2", "code": "880692", "name": "短剧游戏", "anchor": "2026-10-29",
     "accept_from": "2026-10-28", "note": "容量：16 条顶满，0.31 被截"},
    {"key": "A3", "code": "880420", "name": "影视音像", "anchor": "2026-11-02",
     "accept_from": "2026-10-30", "note": "分支：传媒/游戏整条非交易态"},
    {"key": "A4", "code": "880710", "name": "种业", "anchor": "2026-08-21",
     "accept_from": "2026-08-11", "note": "8 月中农业一波（无相位归档，08-12 起可见）"},
]
TAIL = 2


def load_names() -> dict[str, str]:
    import sqlite3

    con = sqlite3.connect(ROOT / "data" / "stock_analysis.db")
    try:
        rows = con.execute(
            "select sector_code, sector_name, count(*) c from node2_sector_trends "
            "group by sector_code, sector_name"
        ).fetchall()
    finally:
        con.close()
    best: dict[str, tuple[str, int]] = {}
    for code, name, cnt in rows:
        name = (name or "").strip("\x00")
        if not code or not name:
            continue
        if code not in best or cnt > best[code][1]:
            best[code] = (name, int(cnt))
    return {code: name for code, (name, _) in best.items()}


def archive_days(ab: dict) -> dict:
    return ab.get("enabled_days") or {}


def archive_row(day_payload: dict, code: str) -> dict | None:
    rows = day_payload.get("launch_watch") or []
    rows = sorted(rows, key=lambda r: -(r.get("launch_score") if r.get("launch_score") is not None else -1.0))
    for i, r in enumerate(rows):
        if r.get("code") == code:
            return {"rank": i + 1, "total": len(rows), **r}
    return None


def single_step_reason(row: dict, rank: int | None, topn: int) -> str:
    """结构门槛（2026-11-09 起）：站上 MA20 + MA20 上行 + 5/10 日同向。"""
    if row.get("above_ma20") != 1.0:
        return "MA20 下"
    r5, r10, slope = row.get("r5"), row.get("r10"), row.get("ma20_slope")
    if r5 is None or r5 <= 0:
        return "5 日不为正"
    if r10 is None or r10 <= 0:
        return "10 日不为正"
    if slope is None or slope <= 0:
        return "MA20 未上行"
    if rank is not None and topn > 0 and rank > topn:
        return f"容量 rank {rank}>{topn}"
    return "通过"


def _score(row: dict) -> float:
    return compute_launch_score(row.get("r5_pct"), row.get("r10"), row.get("high20_dist"))


def single_step_day(rows: list[dict], code: str, topn: int) -> dict | None:
    target = next((r for r in rows if r["code"] == code), None)
    if target is None:
        return None
    cands = sorted(rows, key=lambda r: (-_score(r), -float(r.get("r5") or 0.0), r["code"]))
    rank = next((i + 1 for i, r in enumerate(cands) if r["code"] == code), None)
    score = _score(target)
    gate_ok = (
        target.get("above_ma20") == 1.0
        and (target.get("r5") or 0) > 0
        and (target.get("r10") or 0) > 0
        and (target.get("ma20_slope") or 0) > 0
    )
    in_output = bool(gate_ok and rank is not None and (topn == 0 or rank <= topn))
    return {
        "r5": target.get("r5"), "r10": target.get("r10"), "r5_pct": target.get("r5_pct"),
        "high20_dist": target.get("high20_dist"), "launch_score": score, "rank": rank,
        "in_output": in_output, "reason": "输出" if in_output else single_step_reason(target, rank, topn),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Node 2 锚点板块验收（只读）")
    ap.add_argument("--topn", type=int, default=None)
    ap.add_argument("--md-out", default="data/_replay/ab/node2_anchor_boards.md")
    args = ap.parse_args()
    if args.topn is None:
        from src.got.nodes.sector_analysis import get_launch_topn

        args.topn = get_launch_topn()

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    names = load_names()
    ab = json.loads(AB.read_text(encoding="utf-8")) if AB.exists() else {}
    arch = archive_days(ab)
    all_dates = sorted({d for p in cache.values() for d in p.get("dates") or []})

    windows: dict[str, list[str]] = {}
    for a in ANCHORS:
        i0 = all_dates.index(a["accept_from"]) if a["accept_from"] in all_dates else None
        i1 = all_dates.index(a["anchor"]) if a["anchor"] in all_dates else None
        if i0 is None or i1 is None:
            windows[a["key"]] = []
            continue
        windows[a["key"]] = all_dates[i0: i1 + TAIL + 1]

    need = set(d for v in windows.values() for d in v)
    by_day: dict[str, list[dict]] = {}
    for row in build_panel(cache):
        if row["date"] in need:
            by_day.setdefault(row["date"], []).append(row)

    lines = ["# Node 2 锚点板块验收（四个归档事实）", "",
             f"- TopN = {args.topn or '不限'}；两个源并列：**归档相位路径**（生产输入）与**单步重建**（缓存输入）",
             "- 归档只覆盖 2026-09-02 ~ 2026-11-03；A4（8 月）无归档，仅单步，结论降一档", ""]
    verdicts = []
    for a in ANCHORS:
        days = windows[a["key"]]
        lines += [f"## {a['key']} {a['name']}（{a['code']}）锚点 {a['anchor']} — {a['note']}", "",
                  "| 日期 | 源 | phase_age | 排名 | 输出 | launch_score | 判读 |",
                  "|---|---|---:|---:|---|---:|---|"]
        first_out, first_pool, src_used = None, None, set()
        for d in days:
            ar = archive_row(arch.get(d) or {}, a["code"]) if d in arch else None
            sr = single_step_day(by_day.get(d) or [], a["code"], args.topn)
            if ar is not None:
                src_used.add("archive")
                if ar.get("in_output") and first_out is None:
                    first_out = d
                if first_pool is None:
                    first_pool = d
                lines.append(
                    f"| {d} | 归档 | {ar.get('phase_age')} | {ar['rank']}/{ar['total']} | "
                    f"{'✓' if ar.get('in_output') else '✗'} | {ar.get('launch_score')} | "
                    f"{ar.get('dropped_reason') or '输出'} |")
            elif d in arch:
                src_used.add("archive")
                lines.append(f"| {d} | 归档 | — | — | ✗ | — | 未进入 launch 相位 |")
            if sr is not None:
                src_used.add("single")
                if sr["in_output"] and first_out is None and "archive" not in src_used:
                    first_out = d
                lines.append(
                    f"| {d} | 单步 | 1 | {sr['rank']} | {'✓' if sr['in_output'] else '✗'} | "
                    f"{sr['launch_score']:.3f} | {sr['reason']} |")
            if ar is None and sr is None and d not in arch:
                lines.append(f"| {d} | — | — | — | ✗ | — | 无有效 bar |")
        lines.append("")

        has_arch = "archive" in src_used
        if first_out:
            verdict = f"按时输出（{first_out}）"
        elif has_arch and first_pool:
            verdict = f"进池但被截断（{first_pool}）"
        elif has_arch:
            verdict = "归档窗口内未进入 launch 相位"
        else:
            verdict = "无归档，单步未通过"
        verdicts.append({"key": a["key"], "name": a["name"], "code": a["code"],
                         "anchor": a["anchor"], "accept_from": a["accept_from"],
                         "source": "archive+single" if has_arch else "single",
                         "first_output": first_out, "verdict": verdict})

    lines += ["## 判读", "", "| 锚点 | 板块 | 锚日 | 数据源 | 首次输出 | 结论 |",
              "|---|---|---|---|---|---|"]
    for v in verdicts:
        lines.append(f"| {v['key']} | {v['name']}（{v['code']}） | {v['anchor']} | {v['source']} | "
                     f"{v['first_output'] or '—'} | {v['verdict']} |")
    lines += ["", "- **归档列是 2026-11-09 之前旧闸门跑出来的历史快照**，与新结构门槛不同口径；"
              "本表只用于「输出可见性」检查，不参与任何参数选择。",
              "- 单步列 = 当前结构门槛（站上 MA20 + MA20 上行 + 5/10 日同向），按缓存宇宙重算。",
              "- 锚点只做输出测试：结果无论好坏都不回头改规则（见 §12.5 的反例）。"]
    md = "\n".join(lines) + "\n"
    out = Path(args.md_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
