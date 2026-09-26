# -*- coding: utf-8 -*-
"""C 层验收红线：R-C3 / R-C6（复算脚本，真算归档数据）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
红线原文（`docs/got/archive/got_layer_c_plan_0911.md` §6.1）

| **R-C3** | **复算脚本可复现**：`verify_glue_metrics.py --date 20260910` 输出与本文**对应章节**逐位一致。
必需锚点（**每项标注口径 —— 禁止跨口径比对**）：`M1=3.1%`（规则侧，§1）/ `M1′=34.4%`（glue 侧，§1）/
`M2=28.1%`（§1）/ `M4-a=0.591P,0.419S`（§1）/ `M4-b/c=0.646P,0.500S`（§1）/
`M6=65.6%`（**修复前**规则侧，§1）/ `M6′=96.9%`（glue 侧，§1）/
「其他」回收率 `90.9%`（**C-P7 修复后定义**，§2.2） | 全部相等 | 脚本输出 | ✅ |

| **R-C6** | **A/B 有曲线**：M2 × M3 在 8 天回填样本上的对照表产出，两版 prompt 各有 8 个点 |
2×8=16 点 | `verify_glue_metrics.py --json` | ✅ |

手具定位（同文档 §6.3）：本脚本是「红线的唯一权威口径」「回填期与实盘期的同一把尺」
「本文所有数字的可复现锚点」。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Spearman 口径声明（不得静默取数）
    文档 §1 / 附录 A 记录的 M4-a `0.419S`、M4-b/c `0.500S`，**只在并列秩按「出现顺序」
    （`rank(method="first")`）时复现**；按标准平均秩（`rank(method="average")`，等价
    `scipy.stats.spearmanr`）会得到不同的值。本脚本**两套都打印**：
      - 默认 `--spearman-method first` 用于复现文档锚点（R-C3 的达标判定用此口径）；
      - `--spearman-method average` 给出统计学标准值，并在输出中标注差异。
    `first` 口径依赖输入顺序，仅在「归档顺序不变」时稳定，这是文档锚点的既有性质，
    不是本脚本引入的近似。

C-P8 红线口径声明（M4 系数判定 + M4-a 独立性前提与数值标定，2026-09-26 落地）
    ① **M4 的判定系数 = Spearman**：Pearson 与 Spearman 对同一批数据给出不同的值
      （M4-a Pearson 0.591 vs Spearman 0.419），0903 §8.1 旧表从未写明用哪个系数——本脚本
      已固定以 **Spearman** 作为 M4-a/b/c 的判定系数（first 口径复现文档锚点，average 为统计标准值）。
    ② **M4-a 的独立性以 B1 修复为条件**：当规则侧退化（B1：同一组信号被 Node 6 与 Phase 2 重复消费）
      未修时，实测 `ρ(tech, composite)≈0.985` 使 M4-a ≡ M4-c（共线）。B1 现状 = D-B1「只标注口径、不改数值」
      ⇒ **仍未修**，M4-a 与 M4-c 目前测的是同一个量，删除 M4-a 会把「由 B1 引起的共线」固化成永久设计决定。
    ③ **M4-a 红线数值 0.6 标记「待标定」**：n=32 下 Spearman 标准误 ≈ 1/√(n-1) ≈ 0.18，0.591 的 95% 区间
      约 ±0.35——「0.591 不过 0.6」是一次抽样不是结论。红线留到 C-P13 标定阶段按实测重设（本轮不标定）。

指标口径（逐项标注，禁止跨口径比对）
    M1   规则侧：`unified.json` `confirmed_sectors[].news_check != "neutral"` 占比
    M1′  glue 侧：`agent_glue.json` `strengths[].news_read` 非 low/unknown 占比
    M2   glue 侧：`confirmed_sectors` 中至少 1 条 `relations[]` 的候选占比
    M4-a glue 侧 vs 技术面：ρ(glue.strength, tech_score)
    M4-b/c glue 侧 vs 旧 composite：ρ(glue.strength, composite_score)
    M6   规则侧（修复前口径）：`confirmed_sectors[].frame_group != "其他"` 占比
    M6′  glue 侧：`families[].family` 非「无归属」占比
    回收率 C-P7 修复后定义：规则侧落「其他」的板块中，glue 给出真实家族名的占比

退出码：全 PASS 且无 SKIP = 0；有 FAIL = 1；有 SKIP 且无 FAIL = 2。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_common import (  # noqa: E402
    STATUS_SKIP,
    Verifier,
    force_utf8_stdout,
    format_pct,
    got_daily_root,
    iter_archive_days,
    pct,
    pearson,
    print_header,
    read_json,
    spearman,
)

REDLINE_DOC = "docs/got/archive/got_layer_c_plan_0911.md §6.1"
ANCHOR_DATE = "20260910"

NON_NEUTRAL_READS = {"low", "unknown"}
NO_FAMILY = {"无归属"}
REPLAY_PLACEHOLDER_STATUSES = {"S6_disabled", "replay_mode"}

# ── C-P6 / C-P7 主口径（2026-09-14 落地） ──
# C-P6：M1 改量 glue 侧 `strengths[].news_read`，规则侧 `news_check` 降为并列对照列。
# C-P7：M6 改量「其他」回收率，「无归属」计为未回收；规则侧覆盖率降为观察项。
# 依据 `docs/got/archive/got_layer_c_plan_0911.md` §5.2 P2（该项自述「零行为变更」）。
#
# ⚠️ 两者原本只能量规则侧字段 ⇒ M1 恒 3.1% / M6 恒 65.6%，构成「门禁自锁」。
#    C-P6 改数据源只**解除死锁**，不解除「红线不可达」（glue 侧 34.4% 仍 < 60%）。
# ⚠️ 下方仅为 0903 §8.1 的**参考线**，用于并列展示；M1 / M6 的达标判定归项目负责人
#    （C-P8 已把 M1 标为「待标定」，C-P13 明示本轮不标定）⇒ 本脚本不对两者产生 PASS/FAIL。
REDLINE_REFERENCE = {"M1": 60.0, "M6": 80.0}


def _load_pair(date: str) -> tuple[dict, dict] | None:
    """加载 agent_glue.json + unified.json。"""
    day_dir = got_daily_root() / date
    glue = read_json(day_dir / "agent_glue.json")
    unified = read_json(day_dir / "unified.json")
    if glue is None or unified is None:
        return None
    return glue, unified


def _build_series(glue: dict, unified: dict) -> tuple[list[float], list[float], list[float], int]:
    """按 sector_code 逐板块 join，返回 (glue.strength, tech_score, composite_score, 匹配数)。"""
    by_code = {c.get("sector_code"): c for c in unified.get("confirmed_sectors", [])}
    glue_strengths: list[float] = []
    tech_scores: list[float] = []
    composite_scores: list[float] = []
    for item in glue.get("strengths", []):
        sector = by_code.get(item.get("board_code"))
        if sector is None:
            continue
        glue_strengths.append(float(item.get("strength", 0.0)))
        tech_scores.append(float(sector.get("tech_score", 0.0)))
        composite_scores.append(float(sector.get("composite_score", 0.0)))
    return glue_strengths, tech_scores, composite_scores, len(glue_strengths)


def compute_metrics(date: str, spearman_method: str) -> dict:
    """真算 R-C3 的全部指标。"""
    pair = _load_pair(date)
    if pair is None:
        raise FileNotFoundError(
            f"归档缺失：data/got_daily/{date}/ 需要 agent_glue.json + unified.json"
        )
    glue, unified = pair

    sectors = unified.get("confirmed_sectors", [])
    strengths = glue.get("strengths", [])
    families = glue.get("families", [])
    relations = glue.get("relations", [])

    # M1 规则侧
    m1_num = sum(1 for c in sectors if c.get("news_check") != "neutral")
    m1 = pct(m1_num, len(sectors))

    # M1′ glue 侧（口径：strengths[].news_read）
    m1p_num = sum(1 for s in strengths if s.get("news_read") not in NON_NEUTRAL_READS)
    m1p = pct(m1p_num, len(strengths))

    # M2 glue 侧（口径：至少 1 条 relations[] 的候选占比）
    relation_codes = {r.get("board_code") for r in relations}
    m2_num = sum(1 for c in sectors if c.get("sector_code") in relation_codes)
    m2 = pct(m2_num, len(sectors))

    # M6 规则侧（修复前口径：frame_group != "其他"）
    m6_num = sum(1 for c in sectors if c.get("frame_group") != "其他")
    m6 = pct(m6_num, len(sectors))

    # M6′ glue 侧（口径：families[].family 非「无归属」）
    m6p_num = sum(1 for f in families if f.get("family") not in NO_FAMILY)
    m6p = pct(m6p_num, len(families))

    # 「其他」回收率（C-P7 修复后定义）
    family_by_code = {f.get("board_code"): f.get("family") for f in families}
    other_codes = [c.get("sector_code") for c in sectors if c.get("frame_group") == "其他"]
    recovered = [
        code
        for code in other_codes
        if family_by_code.get(code) is not None and family_by_code.get(code) not in NO_FAMILY
    ]
    recovery = pct(len(recovered), len(other_codes))

    # M4 相关系数（Pearson 与 Spearman 同时输出）
    glue_strengths, tech_scores, composite_scores, matched = _build_series(glue, unified)
    m4_a_pearson = pearson(glue_strengths, tech_scores)
    m4_a_spearman = spearman(glue_strengths, tech_scores, spearman_method)
    m4_a_spearman_average = spearman(glue_strengths, tech_scores, "average")
    m4_bc_pearson = pearson(glue_strengths, composite_scores)
    m4_bc_spearman = spearman(glue_strengths, composite_scores, spearman_method)
    m4_bc_spearman_average = spearman(glue_strengths, composite_scores, "average")

    return {
        "date": date,
        "spearman_method": spearman_method,
        "n_confirmed_sectors": len(sectors),
        "n_strengths": len(strengths),
        "n_families": len(families),
        "n_relations": len(relations),
        "n_joined": matched,
        "m1_rule_display": format_pct(m1),
        "m1p_glue_display": format_pct(m1p),
        "m2_display": format_pct(m2),
        "m6_rule_before_fix_display": format_pct(m6),
        "m6p_glue_display": format_pct(m6p),
        "recovery_display": format_pct(recovery),
        "recovery_detail": f"{len(recovered)}/{len(other_codes)}",
        # C-P6 / C-P7 主口径（与旧键同值但语义为主；旧键保留供 R-C3 锚点与历史对照）
        "m1_primary_pct": m1p,
        "m1_primary_display": format_pct(m1p),
        "m6_primary_pct": recovery,
        "m6_primary_display": format_pct(recovery),
        "m6_other_total": len(other_codes),
        "m6_other_recovered": len(recovered),
        "m4a_pearson": m4_a_pearson,
        "m4a_spearman": m4_a_spearman,
        "m4a_spearman_average": m4_a_spearman_average,
        "m4bc_pearson": m4_bc_pearson,
        "m4bc_spearman": m4_bc_spearman,
        "m4bc_spearman_average": m4_bc_spearman_average,
    }


def _fmt_corr(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def compare_anchor(metrics: dict) -> list[tuple[str, str, str, bool]]:
    """把实算值与文档锚点逐位比对。"""
    comparisons = [
        ("M1（规则侧，§1）", metrics["m1_rule_display"], "3.1%"),
        ("M1′（glue 侧，§1）", metrics["m1p_glue_display"], "34.4%"),
        ("M2（§1）", metrics["m2_display"], "28.1%"),
        ("M4-a Pearson（§1）", _fmt_corr(metrics["m4a_pearson"]), "0.591"),
        ("M4-a Spearman（§1，first 口径）", _fmt_corr(metrics["m4a_spearman"]), "0.419"),
        ("M4-b/c Pearson（§1）", _fmt_corr(metrics["m4bc_pearson"]), "0.646"),
        ("M4-b/c Spearman（§1，first 口径）", _fmt_corr(metrics["m4bc_spearman"]), "0.500"),
        ("M6（修复前规则侧，§1）", metrics["m6_rule_before_fix_display"], "65.6%"),
        ("M6′（glue 侧，§1）", metrics["m6p_glue_display"], "96.9%"),
        ("「其他」回收率（C-P7，§2.2）", metrics["recovery_display"], "90.9%"),
    ]
    return [(name, actual, expected, actual == expected) for name, actual, expected in comparisons]


def _replay_sample_inventory(variant: str = "") -> dict:
    """盘点 `_replay/` 回填样本（R-C6 前置）。

    variant="" → 基线路径 `{date}/_replay/agent_glue.json`（C-P11 单跑回填，行为与旧版一致）；
    variant="variant_a" → `{date}/_replay/variant_a/agent_glue.json`（C-P12 A/B 变体隔离目录）。

    ⚠️ 必须分开数「文件数」与「有效样本数」：
      - 同一份产物在不同日期重复落盘会被文件数数成多天（实测 0911 / 0912 两份 md5 相同）；
      - `S6_disabled` / `replay_mode` 是开关短路产生的**占位**，回放在此处即返回，
        从未调用 LLM，不构成 A/B 对照的有效样本。
    """
    rel = Path("_replay") / variant if variant else Path("_replay")
    day_of: dict[Path, str] = {}
    file_paths: list[Path] = []
    for day in iter_archive_days():
        p = day / rel / "agent_glue.json"
        if p.exists():
            file_paths.append(p)
            day_of[p] = day.name
    by_digest: dict[str, list[str]] = {}
    placeholders: list[str] = []
    valid_days: list[str] = []
    for path in file_paths:
        day = day_of[path]
        by_digest.setdefault(hashlib.md5(path.read_bytes()).hexdigest(), []).append(day)
        status = (read_json(path) or {}).get("status")
        if status in REPLAY_PLACEHOLDER_STATUSES:
            placeholders.append(f"{day}({status})")
        else:
            valid_days.append(day)
    return {
        "variant": variant,
        "files": len(file_paths),
        "distinct": len(by_digest),
        "valid_days": valid_days,
        "placeholders": placeholders,
        "duplicate_groups": [days for days in by_digest.values() if len(days) > 1],
    }


def _discover_replay_variants() -> list[str]:
    """列出归档中实际存在的 C-P12 变体目录名（`_replay/<variant>/agent_glue.json`）。

    基线 `_replay/agent_glue.json`（C-P11 单跑回填）不算变体，返回空列表时
    R-C6 渲染与旧版逐字节一致（无变体目录 ⇒ 无新增证据行）。
    """
    found: set[str] = set()
    for day in iter_archive_days():
        replay_dir = day / "_replay"
        if not replay_dir.is_dir():
            continue
        for sub in replay_dir.iterdir():
            if sub.is_dir() and (sub / "agent_glue.json").exists():
                found.add(sub.name)
    return sorted(found)


def _latest_complete_day() -> str | None:
    """最近一个同时含 agent_glue.json 与 unified.json 的归档日。

    0911/0912 只有 `agent_glue.json`（且 `_replay/` 那份也无对应 unified），
    是测试直调 `_agent_semantic_glue` 产生的污染产物（污染源已在
    tests/test_node6_glue.py 中隔离），不能作为指标复算样本，必须成对存在。
    """
    for day in reversed(iter_archive_days()):
        if (day / "agent_glue.json").exists() and (day / "unified.json").exists():
            return day.name
    return None


def run(date: str | None, as_json: bool, spearman_method: str) -> int:
    verifier = Verifier("C 层 R-C3 / R-C6 复算", REDLINE_DOC)

    if date is None:
        date = _latest_complete_day()
        if date is None:
            verifier.check(
                "R-C3 归档定位",
                False,
                "未找到同时含 agent_glue.json + unified.json 的归档日",
                [],
            )
            return verifier.finish()
        print(f"[info] 未指定 --date，自动选取最近一个完整归档日：{date}")
    else:
        print(f"[info] --date {date}")

    try:
        metrics = compute_metrics(date, spearman_method)
    except FileNotFoundError as exc:
        verifier.check("R-C3 归档加载", False, str(exc), [])
        return verifier.finish()

    # ── 归档样本概况 ──
    print("\n── 归档样本 ──")
    print(
        f"  归档日 = {metrics['date']}  "
        f"confirmed_sectors={metrics['n_confirmed_sectors']} "
        f"strengths={metrics['n_strengths']} families={metrics['n_families']} "
        f"relations={metrics['n_relations']} join={metrics['n_joined']}"
    )
    # ── 主口径（C-P6 / C-P7 落地后，M1 / M6 从此侧读取） ──
    print("\n── 主口径（C-P6 / C-P7，M1 / M6 从此侧读取） ──")
    print(
        f"  M1  glue 侧 strengths[].news_read 非 low/unknown  = {metrics['m1_primary_display']}"
        f"   [参考线 ≥{REDLINE_REFERENCE['M1']:.0f}%]"
    )
    print(
        f"  M6  「其他」回收率 ({metrics['recovery_detail']})"
        f"{' ' * 20}= {metrics['m6_primary_display']}"
        f"   [参考线 ≥{REDLINE_REFERENCE['M6']:.0f}%]"
    )

    # ── 对照列 / 观察项（C-P6 要求规则侧并列保留；C-P7 要求覆盖率降为观察项） ──
    print("\n── 对照列 / 观察项（均不参与判定） ──")
    print(f"  M1 对照  规则侧 news_check != neutral             = {metrics['m1_rule_display']}")
    print(f"  M6 观察  规则侧 frame_group != \"其他\"（修复前口径） = {metrics['m6_rule_before_fix_display']}")
    print(f"  M6 观察  glue 侧 family 非「无归属」               = {metrics['m6p_glue_display']}")

    # ── 其余指标 ──
    print("\n── 其余指标 ──")
    print(f"  M2  (confirmed_sectors 中至少 1 条 relations[] 的占比)          = {metrics['m2_display']}")
    print(f"  M4-a Pearson  ρ(glue.strength, tech_score)                     = {_fmt_corr(metrics['m4a_pearson'])}")
    print(f"  M4-a Spearman ρ [method={spearman_method}]                     = {_fmt_corr(metrics['m4a_spearman'])}")
    print(f"  M4-a Spearman ρ [method=average，统计学标准值]                 = {_fmt_corr(metrics['m4a_spearman_average'])}")
    print(f"  M4-b/c Pearson  ρ(glue.strength, composite_score)              = {_fmt_corr(metrics['m4bc_pearson'])}")
    print(f"  M4-b/c Spearman ρ [method={spearman_method}]                    = {_fmt_corr(metrics['m4bc_spearman'])}")
    print(f"  M4-b/c Spearman ρ [method=average]                             = {_fmt_corr(metrics['m4bc_spearman_average'])}")
    print("\n  ⚠️ M1 / M6 的达标判定归项目负责人，本脚本只列数值与参考线，不产生 PASS/FAIL。")
    print("\n  ⚠️ Spearman 口径声明：文档锚点 0.419S / 0.500S 只在 rank(method=\"first\") 下复现；")
    print("     标准平均秩（等价 scipy.stats.spearmanr；本环境未安装 scipy，用 pandas")
    print("     rank(method=\"average\") 的等价实现）会得到上面 average 行的值。两值均列出，未静默取舍。")

    # ── R-C3：逐位比对锚点 ──
    comparisons = compare_anchor(metrics)
    anchor_ok = all(ok for _, _, _, ok in comparisons)
    evidence = [
        f"{name}: 实算 {actual} / 文档 {expected} → {'一致' if ok else '不一致'}"
        for name, actual, expected, ok in comparisons
    ]
    if metrics["date"] != ANCHOR_DATE:
        verifier.check(
            "R-C3 锚点逐位一致",
            False,
            f"锚点仅对 --date {ANCHOR_DATE} 定义；当前 date={metrics['date']} 无法比对",
            evidence,
        )
    else:
        verifier.check(
            "R-C3 锚点逐位一致",
            anchor_ok,
            "与 docs/got/archive/got_layer_c_plan_0911.md §6.1 / §1 锚点全部一致"
            if anchor_ok
            else "存在与文档锚点不一致的项",
            evidence,
        )

    # ── R-C6：A/B 曲线（需回填 + 两版 prompt，不能算则 SKIP） ──
    replay = _replay_sample_inventory()
    replay_samples = len(replay["valid_days"])
    sample_evidence = [
        f"文件数 = {replay['files']} / 去重后 = {replay['distinct']}；"
        f"有效样本 = {replay_samples} 天"
        + (f"（{', '.join(replay['valid_days'])}）" if replay["valid_days"] else ""),
    ]
    if replay["duplicate_groups"]:
        sample_evidence.append(
            "⚠️ 同内容重复落盘（文件数因此高估）："
            + "、".join("/".join(group) for group in replay["duplicate_groups"])
        )
    if replay["placeholders"]:
        sample_evidence.append(
            "⚠️ 占位态（开关短路，未调用 LLM，不计入有效样本）："
            + ", ".join(replay["placeholders"])
        )
    variants = _discover_replay_variants()
    if variants:
        per_variant = "；".join(
            f"{v}: 有效 {len(_replay_sample_inventory(v)['valid_days'])} 天"
            for v in variants
        )
        sample_evidence.append(f"🧬 已识别 C-P12 变体目录（{len(variants)} 个）：{per_variant}")
    sample_evidence += [
        "前置条件：① C-P11 回填跑批产出 ≥8 天 _replay/agent_glue.json；"
        "② C-P12 prompt A/B 两版各跑这 8 天；③ 回填期 M3 需人工抽查样本（OPEN-C2 流程未定义）",
        "本脚本不把 R-C6 替换成任何静态检查 —— 无样本即 SKIP",
    ]
    verifier.add(
        "R-C6 A/B 曲线（M2 × M3，8 天回填 × 2 版 prompt）",
        STATUS_SKIP,
        f"当前回填**有效**样本 = {replay_samples} 天"
        f"（文件 {replay['files']} 份，去重后 {replay['distinct']} 份），"
        "两版 prompt 的 A/B 对照表尚未产出",
        sample_evidence,
    )

    if as_json:
        payload = {
            "metrics": metrics,
            "primary": {
                "m1": {
                    "definition": "glue 侧 strengths[].news_read 非 low/unknown（C-P6）",
                    "value": metrics["m1_primary_display"],
                    "reference_line": REDLINE_REFERENCE["M1"],
                    "rule_side_comparison": metrics["m1_rule_display"],
                    "judged_by": "项目负责人",
                },
                "m6": {
                    "definition": "「其他」回收率（C-P7，无归属计为未回收）",
                    "value": metrics["m6_primary_display"],
                    "detail": metrics["recovery_detail"],
                    "reference_line": REDLINE_REFERENCE["M6"],
                    "coverage_observation": metrics["m6_rule_before_fix_display"],
                    "judged_by": "项目负责人",
                },
            },
            "anchor_comparisons": [
                {"name": name, "actual": actual, "expected": expected, "match": ok}
                for name, actual, expected, ok in comparisons
            ],
            "rc6": {
                "status": STATUS_SKIP,
                "replay_samples": replay_samples,
                "replay_files": replay["files"],
                "replay_distinct": replay["distinct"],
                "replay_valid_days": replay["valid_days"],
                "replay_placeholders": replay["placeholders"],
            },
        }
        print("\n── --json ──")
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    return verifier.finish()


def main() -> int:
    force_utf8_stdout()
    import argparse

    parser = argparse.ArgumentParser(description="C 层 R-C3 / R-C6 复算脚本")
    parser.add_argument("--date", help="归档日 YYYYMMDD（默认取最近一个有 agent_glue.json 的归档日）")
    parser.add_argument("--json", action="store_true", help="附加输出机器可读 JSON")
    parser.add_argument(
        "--spearman-method",
        choices=["first", "average"],
        default="first",
        help="Spearman 并列秩处理；first 复现文档锚点，average 为统计学标准值（默认 first）",
    )
    args = parser.parse_args()

    print_header("C 层 §6.1 红线复算：R-C3 / R-C6", REDLINE_DOC)
    return run(args.date, args.json, args.spearman_method)


if __name__ == "__main__":
    sys.exit(main())
