# -*- coding: utf-8 -*-
"""B 层验收红线：R-B3 / R-B8 / R-B9（归档回放 / 归档 diff，真算数据）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
红线原文（`docs/got/archive/got_layer_b_plan_0911.md` §6.1）

| **R-B3** | 家族降权影响面：同一期归档回放，`crowded_families` 非空天数与 `家族拥挤降权`
触发次数变化 | **有界**（给出前后对比表，无单日增幅 > 100%） | 归档回放 | ✅ |

| **R-B8** | 值不变性（零行为变更项）：B1/B3/B4 修复前后，同期 `unified.json` 的
`final_position_limit` / `composite_score` / `confirmed_sectors` 逐字段一致 | 100% 一致 | 归档 diff | ✅ |

| **R-B9** | 行为变更留痕（B2/B6）：同期归档的 `classification` 与 `dimension_scores["sector"]`
变化逐条列出 | 有清单 | 归档 diff | ✅ |

验收手具（同文档 §6.3）：
「B 层**不需要**同日两跑……B 层用**同期归档回放**：以 0910（或最近一个完整日）的 6 个节点 JSON
为输入，跑 Node 6 + Phase 2，与既有归档 diff。」
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

实现方式
    R-B3 与 R-B8/R-B9 的红线定义都要求「回放 / diff 归档」，不能由源码 grep 证明：
      - `--replay-before ROOT --replay-after ROOT`：两个多日回放归档根目录 → 真算 R-B3；
      - `--archive-before DIR --archive-after DIR`：同期单日归档对 → 真算 R-B8 / R-B9。
    未提供目录时 **显式 SKIP 并给出前置条件**，绝不用静态检查冒充。

    文件末尾的 import 路径 / 家族数检查是**辅助检查**（历史脚本把它们误标为 R-B3/R-B8/R-B9），
    它们与上述三条红线无关，不参与退出码裁决。

退出码：全 PASS 且无 SKIP = 0；有 FAIL = 1；有 SKIP 且无 FAIL = 2。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_common import (  # noqa: E402
    Verifier,
    force_utf8_stdout,
    print_header,
    read_json,
    repo_root,
)

REDLINE_DOC = "docs/got/archive/got_layer_b_plan_0911.md §6.1"
CROWDING_TRIGGER_TEXT = "家族拥挤降权"

FAMILY_KW_FILE = repo_root() / "src" / "got" / "family_keywords.py"
SCHEMAS_FILE = repo_root() / "src" / "got" / "schemas.py"
SECTOR_FILE = repo_root() / "src" / "got" / "nodes" / "sector_analysis.py"
EXPECTED_FAMILY_COUNT = 14


# ══════════════════════════════════════════════════════════════════════
# R-B3：家族降权影响面（多日归档回放对比）
# ══════════════════════════════════════════════════════════════════════


def _list_day_dirs(root: Path) -> dict[str, Path]:
    """列出根目录下的 YYYYMMDD 子目录。"""
    if not root.exists():
        return {}
    return {
        p.name: p
        for p in root.iterdir()
        if p.is_dir() and re.match(r"^\d{8}$", p.name)
    }


def _counting_family_crowding(day_dir: Path) -> dict:
    """单日：crowded_families 是否非空 + 家族拥挤降权触发次数（reasoning 文本计数）。"""
    unified = read_json(day_dir / "unified.json")
    crowded = (unified or {}).get("crowded_families") or []
    # 触发次数：Phase 2 reasoning / unified reasoning 中出现「家族拥挤降权」的次数
    trigger_count = 0
    for filename in ("reasoning.md",):
        path = day_dir / filename
        if path.exists():
            trigger_count += path.read_text(encoding="utf-8").count(CROWDING_TRIGGER_TEXT)
    if unified:
        trigger_count += str(unified.get("reasoning", "")).count(CROWDING_TRIGGER_TEXT)
    return {"crowded_nonempty": bool(crowded), "crowded_size": len(crowded),
            "trigger_count": trigger_count}


def check_r_b3(verifier: Verifier, before_root: str | None, after_root: str | None) -> None:
    name = "R-B3 家族降权影响面（同期回放对比，无单日增幅 > 100%）"
    if not before_root or not after_root:
        verifier.skip(
            name,
            "未提供两个多日回放归档根目录，红线要求「同一期归档回放」后再对比",
            "需先具备 `--got-replay` 回放能力（含 LLM 可用 + 状态推进），"
            "对同一批交易日分别用修复前/修复后代码各跑一遍，产出 before_root/after_root "
            "两组 data/got_daily/{date}/；本仓库当前无该回放产物",
        )
        return

    before_days = _list_day_dirs(Path(before_root))
    after_days = _list_day_dirs(Path(after_root))
    common = sorted(set(before_days) & set(after_days))
    if not common:
        verifier.check(name, False, "两个回放根目录没有交集交易日，无法对比", [])
        return

    rows: list[str] = []
    violations: list[str] = []
    before_nonempty = 0
    after_nonempty = 0
    before_total_triggers = 0
    after_total_triggers = 0
    for day in common:
        b = _counting_family_crowding(before_days[day])
        a = _counting_family_crowding(after_days[day])
        before_nonempty += int(b["crowded_nonempty"])
        after_nonempty += int(a["crowded_nonempty"])
        before_total_triggers += b["trigger_count"]
        after_total_triggers += a["trigger_count"]
        base = b["trigger_count"]
        delta_pct = None if base == 0 else (a["trigger_count"] - base) / base * 100
        flag = ""
        if delta_pct is not None and delta_pct > 100:
            flag = " ⚠️ 单日增幅 > 100%"
            violations.append(day)
        rows.append(
            f"{day}: crowded_families 非空 before={b['crowded_nonempty']} after={a['crowded_nonempty']}"
            f" | 降权触发 before={b['trigger_count']} after={a['trigger_count']}"
            f" | 增幅={('n/a' if delta_pct is None else f'{delta_pct:+.1f}%')}{flag}"
        )
    rows.append(
        f"合计：非空天数 before={before_nonempty} → after={after_nonempty}；"
        f"降权触发 before={before_total_triggers} → after={after_total_triggers}"
    )
    verifier.check(
        name,
        not violations,
        "变化有界（无单日增幅 > 100%）" if not violations else f"存在超界交易日：{violations}",
        rows,
    )


# ══════════════════════════════════════════════════════════════════════
# R-B8 / R-B9：同期归档 diff
# ══════════════════════════════════════════════════════════════════════


def _collect_state_fields(day_dir: Path) -> dict:
    """按 §6.2 清单提取状态字段（不做全文件 diff）。"""
    state: dict[str, object] = {}

    unified = read_json(day_dir / "unified.json")
    if unified is not None:
        state["unified.final_position_limit"] = unified.get("final_position_limit")
        # 决策 #5a（2026-09-26 简报）：market_regime 跨日状态机输出移出「应不变」清单——
        # regime/dwell_days/previous_regime/transition_count/transition_pending 按设计
        # 依赖前一日归档，回放语义下不可能逐字段一致（永久 SKIP 属口径错误而非缺陷）。
        # 差异仍在 check_r_b8_r_b9 中单独比对并标注「按设计 SKIP」，不计入 FAIL。
        for sector in unified.get("confirmed_sectors", []):
            key = sector.get("sector_name")
            for field in ("composite_score", "classification", "frame_group", "confidence"):
                state[f"unified.confirmed_sectors[{key}].{field}"] = sector.get(field)

    sector_json = read_json(day_dir / "sector.json")
    if sector_json is not None:
        state["sector.frame_group_lookup"] = sector_json.get("frame_group_lookup")

    portfolio = read_json(day_dir / "portfolio_decision.json")
    if portfolio is not None:
        risk = portfolio.get("risk_assessment", {}) or {}
        state["portfolio.risk_assessment.position_limit"] = risk.get("position_limit")
        state["portfolio.risk_assessment.sector_exposure_limits"] = risk.get("sector_exposure_limits")
        for scoring in portfolio.get("unified_scoring", []):
            state[f"portfolio.unified_scoring[{scoring.get('symbol')}].dimension_scores"] = scoring.get(
                "dimension_scores"
            )

    return state


def check_r_b8_r_b9(
    verifier: Verifier, before_dir: str | None, after_dir: str | None
) -> None:
    r_b8_name = (
        "R-B8 值不变性（同期归档 final_position_limit / composite_score / confirmed_sectors 逐字段一致；"
        "market_regime 跨日状态机字段已按决策 #5a 移出不变组，另列 SKIP 留痕）"
    )
    r_b9_name = "R-B9 行为变更留痕（classification / dimension_scores[\"sector\"] 变化逐条列出）"

    if not before_dir or not after_dir:
        precondition = (
            "需 B1/B3/B4 修复前后、同一批节点 JSON 各跑一次 Node 6 + Phase 2，"
            "产出同期两个归档目录；本仓库当前无该回放产物"
        )
        verifier.skip(r_b8_name, "未提供同期归档对（--archive-before/--archive-after）", precondition)
        verifier.skip(r_b9_name, "未提供同期归档对（--archive-before/--archive-after）", precondition)
        return

    before_path = Path(before_dir)
    after_path = Path(after_dir)
    missing = [
        str(p) for p in (before_path / "unified.json", after_path / "unified.json") if not p.exists()
    ]
    if missing:
        verifier.check(r_b8_name, False, f"归档缺失 unified.json：{missing}", [])
        verifier.check(r_b9_name, False, f"归档缺失 unified.json：{missing}", [])
        return

    before_state = _collect_state_fields(before_path)
    after_state = _collect_state_fields(after_path)
    all_keys = sorted(set(before_state) | set(after_state))

    # 决策 #5a：market_regime 子集单独比对，只留痕不计 FAIL（按设计 SKIP）
    regime_before = read_json(before_path / "unified.json") or {}
    regime_after = read_json(after_path / "unified.json") or {}
    regime_changes: list[str] = []
    for key in ("regime", "dwell_days", "previous_regime", "transition_count", "transition_pending"):
        b = (regime_before.get("market_regime") or {}).get(key)
        a = (regime_after.get("market_regime") or {}).get(key)
        if b != a:
            regime_changes.append(f"market_regime.{key}: {b} → {a}")

    invariant_diffs: list[str] = []
    classification_changes: list[str] = []
    dimension_changes: list[str] = []
    frame_group_changes: list[str] = []

    for key in all_keys:
        b = before_state.get(key, "<缺失>")
        a = after_state.get(key, "<缺失>")
        if b == a:
            continue
        # R-B9 清单：classification / dimension_scores["sector"] / frame_group_lookup
        if key.endswith(".classification"):
            classification_changes.append(f"{key}: {b} → {a}")
            continue
        if key.startswith("portfolio.unified_scoring") and "dimension_scores" in key:
            dim_sector_b = (b or {}).get("sector") if isinstance(b, dict) else None
            dim_sector_a = (a or {}).get("sector") if isinstance(a, dict) else None
            if dim_sector_b != dim_sector_a:
                dimension_changes.append(
                    f"{key}.sector: {dim_sector_b} → {dim_sector_a}"
                )
            continue
        if key == "sector.frame_group_lookup":
            frame_group_changes.append(f"{key}: 有变化（B2 预期，逐条列出见下）")
            if isinstance(b, dict) and isinstance(a, dict):
                for board in sorted(set(b) | set(a)):
                    if b.get(board) != a.get(board):
                        frame_group_changes.append(
                            f"  {board}: {b.get(board)} → {a.get(board)}"
                        )
            continue
        invariant_diffs.append(f"{key}: {b} → {a}")

    verifier.check(
        r_b8_name,
        not invariant_diffs,
        "零行为变更字段逐字段一致（100%）"
        if not invariant_diffs
        else f"{len(invariant_diffs)} 个应不变字段发生变化",
        (invariant_diffs or [f"比对字段数 = {len(all_keys)}"])
        + ([
            f"🧾 market_regime 跨日状态机字段（按设计 SKIP，不计 FAIL）"
            f"——差异 {len(regime_changes)} 项：{regime_changes or '比对值相同'}"
        ] if regime_changes else [
            "🧾 market_regime 跨日状态机字段已按决策 #5a 移出不变组（本次比对值恰好相同）"
        ]),
    )

    change_ledger = classification_changes + dimension_changes + frame_group_changes
    verifier.check(
        r_b9_name,
        True,  # 红线要求「有清单」；脚本产出清单即达标（清单内容如实列出）
        f"已产出变化清单：classification {len(classification_changes)} 条 / "
        f"dimension_scores.sector {len(dimension_changes)} 条 / "
        f"frame_group_lookup {len(frame_group_changes)} 条",
        change_ledger or ["无变化（清单为空，同样如实列出）"],
    )


# ══════════════════════════════════════════════════════════════════════
# 辅助检查（历史脚本误标为 R-B3/R-B8/R-B9，实为静态 import/定义检查）
# ══════════════════════════════════════════════════════════════════════


class _ImportRecorder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: dict[str, list[str]] = {}

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            self.imports.setdefault(alias.asname or alias.name, []).append(module)
        self.generic_visit(node)


class _DefinitionLocator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.definitions: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.definitions.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self.definitions.add(node.target.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.definitions.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.definitions.add(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.definitions.add(node.name)
        self.generic_visit(node)


def _run_auxiliary_checks(verifier: Verifier) -> None:
    """辅助检查：词表单一权威源 / 家族数 / import 路径。不是 R-B3/R-B8/R-B9。"""

    # A1: CONTROLLED_FAMILIES 单一权威源
    if SCHEMAS_FILE.exists():
        tree = ast.parse(SCHEMAS_FILE.read_text(encoding="utf-8"), filename=str(SCHEMAS_FILE))
        recorder = _ImportRecorder()
        recorder.visit(tree)
        locator = _DefinitionLocator()
        locator.visit(tree)
        imported = [m for m in recorder.imports.get("CONTROLLED_FAMILIES", []) if "family_keywords" in m]
        verifier.check(
            "辅助 A1 CONTROLLED_FAMILIES 是否从 family_keywords 导入",
            bool(imported) and "CONTROLLED_FAMILIES" not in locator.definitions,
            f"import 来源={imported or '无'}；schemas.py 内定义="
            f"{'CONTROLLED_FAMILIES' in locator.definitions}",
            [],
            auxiliary=True,
        )

    # A2: FAMILY_KEYWORDS 家族数
    if FAMILY_KW_FILE.exists():
        tree = ast.parse(FAMILY_KW_FILE.read_text(encoding="utf-8"), filename=str(FAMILY_KW_FILE))
        count = None
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == "FAMILY_KEYWORDS" for t in targets):
                    if isinstance(node.value, ast.List):
                        count = len(node.value.elts)
                    break
        verifier.check(
            "辅助 A2 FAMILY_KEYWORDS 家族数",
            count == EXPECTED_FAMILY_COUNT,
            f"实际={count}，期望={EXPECTED_FAMILY_COUNT}",
            [],
            auxiliary=True,
        )

    # A3: sector_analysis.py 从 family_keywords 导入 FAMILY_KEYWORDS
    if SECTOR_FILE.exists():
        tree = ast.parse(SECTOR_FILE.read_text(encoding="utf-8"), filename=str(SECTOR_FILE))
        recorder = _ImportRecorder()
        recorder.visit(tree)
        sources = [m for m in recorder.imports.get("FAMILY_KEYWORDS", []) if "family_keywords" in m]
        verifier.check(
            "辅助 A3 sector_analysis.py 的 FAMILY_KEYWORDS import 路径",
            bool(sources),
            f"来源={sources or '无'}",
            [],
            auxiliary=True,
        )


def main() -> int:
    force_utf8_stdout()
    import argparse

    parser = argparse.ArgumentParser(description="B 层 R-B3 / R-B8 / R-B9 验收脚本")
    parser.add_argument("--replay-before", help="R-B3：修复前多日回放归档根目录")
    parser.add_argument("--replay-after", help="R-B3：修复后多日回放归档根目录")
    parser.add_argument("--archive-before", help="R-B8/R-B9：修复前同期单日归档目录")
    parser.add_argument("--archive-after", help="R-B8/R-B9：修复后同期单日归档目录")
    parser.add_argument("--json", action="store_true", help="附加输出机器可读 JSON（当前仅回显参数）")
    args = parser.parse_args()

    print_header("B 层 §6.1 红线验收：R-B3 / R-B8 / R-B9", REDLINE_DOC)
    verifier = Verifier("B 层 R-B3 / R-B8 / R-B9", REDLINE_DOC)
    check_r_b3(verifier, args.replay_before, args.replay_after)
    check_r_b8_r_b9(verifier, args.archive_before, args.archive_after)
    _run_auxiliary_checks(verifier)

    if args.json:
        print("\n── --json ──")
        print(json.dumps({
            "replay_before": args.replay_before,
            "replay_after": args.replay_after,
            "archive_before": args.archive_before,
            "archive_after": args.archive_after,
        }, ensure_ascii=False, indent=2))

    return verifier.finish()


if __name__ == "__main__":
    sys.exit(main())
