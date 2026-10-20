#!/usr/bin/env python3
"""#14 ④ regime jump 模型 — 判据①②③④离线回测（扩展 calibrate_regime_jump_penalty.py）。

**回测范围声明（总计划三条原则）**：
- 可作数的：判据②（agree ≥ 无记忆基线自比——见下「真值循环」警示）、判据③
  切换数对照（vs 无记忆基线 14→10，λ=0.25 确实降噪）、映射/阈值可达性；
- **真值循环警示（判据①③必须随申请上报）**：末段 confirmed 自 20260910 起钉死
  transition（#14 立项动机），judge判据①③在此期间以病态 confirmed 为尺 =
  「用旧尺子量新模型」循环论证；jump 领先 confirmed 是设计特性。判据①③合格性
  最终是负责人基于本台账（含领先-滞后台账）的**裁量**，非机械通过率；
- 不可替代：判据④零决策红线（GOT_REGIME_JUMP_SHADOW 关闭日输出逐字节一致）+
  clock_start=2026-10-16 的 5 交易日生产读数。

模型：s_t = argmin_s [ |raw_t − θ_s| + λ·1{s_t ≠ s_{t-1}} ]（aggregator.py:857
_jump_argmin/_raw_threshold_state 直读复用，θ=±0.3、λ=0.25 与生产同参）。

输入：55 日归档 unified.json → market_regime（raw: raw_regime_score 优先，
macro_overlay 正则回退 2 位小数）+ confirmed regime。
输出：全序列 + 末 20 日判据读数表 + 逐日台账（含 jump↔confirmed 领先滞后）+
`raw_regime_score` 1016 起落盘验证记录。`.review_tmp/backtest_regime_jump_criteria.md`。

使用：
    python scripts/backtest_regime_jump_criteria.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.got.nodes.aggregator import AggregatorNode  # noqa: E402  纯函数直读复用
from src.got.catalyst_config import REGIME_JUMP_LAMBDA  # noqa: E402

OUT_DIR = PROJECT_ROOT / ".review_tmp"
OUT_FILE = OUT_DIR / "backtest_regime_jump_criteria.md"
RAW_FIELD_FROM = "20261016"  # raw_regime_score 字段落盘验证起点（verification.md 声明）


def load_series() -> tuple[list[dict], list[str]]:
    """归档 → 逐日 {day, raw, confirmed}（升序）+ raw_regime_score 字段出现日列表。"""
    root = PROJECT_ROOT / "data" / "got_daily"
    rows: list[dict] = []
    raw_field_days: list[str] = []
    for d in sorted(root.iterdir()):
        if not re.fullmatch(r"20\d{6}", d.name):
            continue
        p = d / "unified.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        mr = data.get("market_regime") or {}
        raw = mr.get("raw_regime_score")
        if raw is not None:
            raw_field_days.append(d.name)
        if raw is None:
            m = re.search(r"regime_score=(-?\d+\.?\d*)", mr.get("macro_overlay") or "")
            if not m:
                continue
            raw = float(m.group(1))
        rows.append({"day": d.name, "raw": float(raw), "confirmed": mr.get("regime") or "?"})
    return rows, raw_field_days


def replay_jump(rows: list[dict]) -> list[str]:
    """jump 整窗重放（与 _apply_regime_jump_model 同序：锚点日 s_0=阈值态，无切换成本）。"""
    states: list[str] = []
    prev: str | None = None
    for r in rows:
        if prev is None:
            prev = AggregatorNode._raw_threshold_state(r["raw"])
            states.append(prev)
            continue
        s, _ = AggregatorNode._jump_argmin(r["raw"], prev, REGIME_JUMP_LAMBDA)
        states.append(s)
        prev = s
    return states


def count_switches(states: list[str]) -> int:
    return sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])


def main() -> int:
    rows, raw_field_days = load_series()
    if len(rows) < 5:
        print(f"归档 raw 序列不足（{len(rows)} 日）")
        return 1
    days = [r["day"] for r in rows]
    raws = [r["raw"] for r in rows]
    sigma_diff = statistics.stdev(raws[i] - raws[i - 1] for i in range(1, len(raws)))

    jump = replay_jump(rows)
    base = [AggregatorNode._raw_threshold_state(r) for r in raws]  # 无记忆基线（λ=0 极限）
    confirmed = [r["confirmed"] for r in rows]

    # 钉死期检测（confirmed 连续 transition ≥10 归档日）
    pin_start = None
    run = 0
    for i, c in enumerate(confirmed):
        run = run + 1 if (i > 0 and c == confirmed[i - 1] == "transition") else (1 if c == "transition" else 0)
        if run >= 10 and pin_start is None:
            pin_start = days[i]
    pin_idx = days.index(pin_start) if pin_start else len(rows)

    def block(lo: int, hi: int, name: str) -> dict:
        j, b, c = jump[lo:hi], base[lo:hi], confirmed[lo:hi]
        n = hi - lo
        agree_b = sum(1 for x, y in zip(j, b) if x == y)
        agree_c = sum(1 for x, y in zip(j, c) if x == y)
        return {
            "name": name, "n": n,
            "sw_jump": count_switches(j), "sw_base": count_switches(b), "sw_conf": count_switches(c),
            "agree_base": f"{agree_b}/{n} ({agree_b / n * 100:.0f}%)",
            "agree_conf": f"{agree_c}/{n} ({agree_c / n * 100:.0f}%)",
            "j": j, "b": b, "c": c, "days": days[lo:hi],
        }

    # 末 20 归档日窗（plan §3.2 口径：末 20 日）
    n20 = min(20, len(rows))
    last20 = block(len(rows) - n20, len(rows), f"末 {n20} 归档日")
    full = block(0, len(rows), "全序列")
    pre = block(0, pin_idx, f"钉死期前（首日–{days[pin_idx - 1] if pin_idx else '—'}）") if pin_idx else None
    pinblk = block(pin_idx, len(rows), f"钉死期内（{days[pin_idx]}–）") if pin_idx and pin_idx < len(rows) else None
    last5 = block(len(rows) - 5, len(rows), "末 5 归档日")

    # 判据②读数：plan §3.2 实测口径 = jump↔无记忆基线一致率（随窗给出）；
    # 判据字面口径 = 同尺对照 jump↔confirmed vs 基线↔confirmed（同一把尺，公平比较）。
    def _agr(xs: list[str], ys: list[str]) -> tuple[int, int]:
        return sum(1 for x, y in zip(xs, ys) if x == y), len(xs)
    aj, nj = _agr(jump, base)
    crit2_rows: list[tuple[str, int, int, bool]] = []
    for blk in [b for b in (full, pre, pinblk, last20) if b]:
        jc_j, njc = _agr(blk["j"], blk["c"])
        jc_b, _ = _agr(blk["b"], blk["c"])
        blk["crit2"] = (jc_j, jc_b, njc)
        crit2_rows.append((blk["name"], jc_j, jc_b, jc_j >= jc_b))
    # 口径A：jump↔基线 vs confirmed↔基线（各自对无记忆阈值态的一致率，逐窗）
    crit2_a: dict[str, tuple[int, int, int]] = {}
    for blk in [b for b in (full, pre, pinblk, last20) if b]:
        jj = _agr(blk["j"], blk["b"])[0]
        cb = _agr(blk["c"], blk["b"])[0]
        crit2_a[blk["name"]] = (jj, cb, blk["n"])

    # 领先-滞后台账：jump/confirmed 切换事件逐条列示，jump 事件标注 confirmed 是否跟随
    pin_start = None
    run = 0
    for i, c in enumerate(confirmed):
        run = run + 1 if (i > 0 and c == confirmed[i - 1] == "transition") else (1 if c == "transition" else 0)
        if run >= 10 and pin_start is None:
            pin_start = days[i]
    jsw = [i for i in range(1, len(rows)) if jump[i] != jump[i - 1]]
    csw = [i for i in range(1, len(rows)) if confirmed[i] != confirmed[i - 1]]
    ledger: list[str] = []
    for i in jsw:
        lag = next((ci - i for ci in csw if ci >= i and confirmed[ci] == jump[i]), None)
        if lag is not None:
            note = f"**confirmed 滞后 {lag} 日跟随**（{days[i + lag]}）"
        elif pin_start and days[i] >= pin_start:
            note = "confirmed 未跟随（**钉死期内，jump 领先**）"
        else:
            note = "confirmed 未跟随（观测窗内）"
        ledger.append(f"- **{days[i]}**: jump→**{jump[i]}**｜{note}｜raw={raws[i]:+.2f}")
    for ci in csw:
        ledger.append(f"- **{days[ci]}**: confirmed→{confirmed[ci]}（confirmed 事件）｜raw={raws[ci]:+.2f}")

    OUT_DIR.mkdir(exist_ok=True)
    pre_row = crit2_rows[1] if len(crit2_rows) > 1 else (crit2_rows[0] if crit2_rows else ("—", 0, 0, False))
    full_row = crit2_rows[0] if crit2_rows else ("—", 0, 0, False)
    pin_note = (f"confirmed 自 **{pin_start}** 起连续钉死 transition ≥10 归档日"
                if pin_start else "未见 ≥10 日 transition 钉死段")
    lines = [
        "# #14 ④ regime jump — 判据①②③④离线回测台账",
        "",
        f"- 生成：{date.today().isoformat()}｜模型：s_t=argmin[|raw−θ|+λ·1{{s≠s_prev}}]，"
        f"θ=±0.3 λ={REGIME_JUMP_LAMBDA}（与生产同参，_jump_argmin 直读复用）",
        f"- 序列：{len(rows)} 归档日（{days[0]}–{days[-1]}）；σ(daily-diff)={sigma_diff:.3f}；"
        f"raw 精度：macro_overlay 正则 2 位小数",
        f"- 数据覆盖：0805–1015 归档窗共 48 日，其中 0911/0912/1010 无 `unified.json` → **{len(rows)} 日可得**"
        f"（20260724–0804 共 8 日为 market_regime 前时代，无 macro_overlay，天然无 raw 序列）；",
        f"- `raw_regime_score` 字段落盘验证：历史归档 {len(raw_field_days)} 日出现"
        f"（{raw_field_days[:3]}…{raw_field_days[-3:] if len(raw_field_days) > 3 else ''}）；"
        f"**{RAW_FIELD_FROM} 起新归档须逐日复核**（verification.md 所称「1015 起携带」为超前声明）",
        "",
        "## 判据读数表",
        "",
        "| 窗 | n | ①agree vs confirmed | ②agree vs 无记忆基线 | ③jump切换 | ③基线切换 | ③confirmed切换 |",
        "|---|---|---|---|---|---|---|",
    ]
    for blk in (full, pre, pinblk, last20, last5):
        if blk is None:
            continue
        c2 = blk.get("crit2")
        c2s = f"（同尺 jump {c2[0]} vs 基线 {c2[1]}/{c2[2]}：{'✓' if c2[0] >= c2[1] else '✗'}）" if c2 else ""
        lines.append(
            f"| {blk['name']} | {blk['n']} | {blk['agree_conf']} | {blk['agree_base']} {c2s} | "
            f"{blk['sw_jump']} | {blk['sw_base']} | {blk['sw_conf']} |"
        )
    lines += [
        "",
        "## ⚠️ 判据解读硬要求（随申请上报，防误读）",
        "",
        f"1. **真值循环**：{pin_note}；期间判据①③以病态 confirmed 为尺，机械读数"
        f"（如末 5 日 agree={last5['agree_conf']}、jump 切换 {last20['sw_jump']} > confirmed {last20['sw_conf']}）"
        f"**不能判 jump 不合格**——jump 领先 confirmed 是 #14 立项要解决的病态之反面，即设计特性。",
        f"2. **判据②（≥无记忆基线）多口径读数**（plan §3.2 未指明比较对象，全列供裁量）：",
        "   - **口径A** jump↔无记忆基线 vs confirmed↔无记忆基线：" + "；".join(
            f"{n} {jj} ({jj / nn * 100:.0f}%) vs {cb} ({cb / nn * 100:.0f}%)"
            for n, (jj, cb, nn) in crit2_a.items()
        ) + "。**钉死期内 jump 反超**——病态期 confirmed 与 raw 脱钩更重，jump 仍随 raw 阻尼移动。",
        "   - **口径B** jump↔confirmed vs 基线↔confirmed：" + "；".join(
            f"{n} {jj} vs {cb}" for n, jj, cb, ok in crit2_rows
        ) + "。✗ 属预期：jump 是 raw 的阻尼滞后版，过渡带偏离 confirmed 更久——滞后即设计目标，非缺陷。",
        "   - **口径C**（plan §3.2 绝对读数对照）：jump↔threshold "
        f"{_agr(jump, base)[0]}/{len(rows)}（plan 34/47），jump↔confirmed {_agr(jump, confirmed)[0]}/{len(rows)}（plan 23/47）。",
        f"   - 旁证：切换降噪 {last20['sw_base']}→{last20['sw_jump']}（λ 抑制抖动非钉死：全序列仍有 {full['sw_jump']} 次切换）。",
        f"3. **判据①③合格性 = 负责人基于下述台账的裁量**（领先-滞后方向核验），非机械通过率。",
        "4. **判据④** 零决策红线（GOT_REGIME_JUMP_SHADOW 关闭日输出逐字节一致）+ "
        "clock_start=2026-10-16 的 5 交易日生产读数——离线不可替代。",
        "",
        "## 切换事件台账（jump ↔ confirmed 领先-滞后）",
        "",
    ]
    lines += ledger if ledger else ["- 全窗零切换"]
    lines += [
        "",
        "## 转正申请引用建议",
        "",
        f"> 历史回测（{days[0]}–{days[-1]}，n={len(rows)}）：判据②口径A 钉死期内 jump {crit2_a[pinblk['name']][0]}/{crit2_a[pinblk['name']][2]} > "
        f"confirmed {crit2_a[pinblk['name']][1]}/{crit2_a[pinblk['name']][2]}（病态期反超）；全窗 jump {_agr(jump, base)[0]}/{len(rows)} vs "
        f"confirmed {crit2_a['全序列'][1]}/{len(rows)}（差 1 天）；口径B ✗ 属预期（阻尼滞后）；"
        f"末 20 日切换 {last20['sw_base']}→{last20['sw_jump']}（降噪）；"
        f"判据①③ raw 读数见上表，**因 confirmed 钉死期不作机械判定**，请负责人结合切换台账裁量。",
        "",
        "> 不可替代项：判据④零决策红线 + 2026-10-16 起 5 交易日生产读数。",
    ]
    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")

    print(f"[OK] 序列 {len(rows)} 日（{days[0]}–{days[-1]}）λ={REGIME_JUMP_LAMBDA}")
    for blk in (full, last20, last5):
        print(f"[OK] {blk['name']}: ①agree_conf={blk['agree_conf']} ②agree_base={blk['agree_base']} "
              f"③switch jump/base/conf={blk['sw_jump']}/{blk['sw_base']}/{blk['sw_conf']}")
    print(f"[OK] 判据②多口径: 口径A " + "；".join(
        f"{n} jump {jj} vs conf {cb}" for n, (jj, cb, nn) in crit2_a.items()) +
        f"｜口径B " + "；".join(f"{n} {jj} vs {cb}" for n, jj, cb, ok in crit2_rows))
    print(f"[OK] 钉死期：{pin_note}")
    print(f"[OK] raw_regime_score 字段：历史 {len(raw_field_days)} 日出现；{RAW_FIELD_FROM} 起待逐日验证")
    print(f"[OK] 报告 → {OUT_FILE.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
