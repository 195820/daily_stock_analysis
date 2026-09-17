# -*- coding: utf-8 -*-
"""红线验收脚本公共设施。

设计约束（来自 `docs/got/archive/got_impl_review_0911.md` §R2.3）：
- 红线定义以文档为准，脚本不得自行改写；每个脚本 docstring 顶部须**原文引用**。
- 退出码语义分明：全 PASS 无 SKIP = 0；有 FAIL = 1；有 SKIP 且无 FAIL = 2。
- 「能算的必须真算；不能算的显式 SKIP 并给出前置条件」，禁止用静态 grep 冒充回放/实跑。

本模块只做四件事：结果登记、退出码裁决、归档读取、相关系数计算。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

# 退出码（与任务书约定一致）
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SKIP = 2

# 状态取值
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"
STATUS_AUX_PASS = "AUX-PASS"
STATUS_AUX_FAIL = "AUX-FAIL"

_DAY_DIR_PATTERN = re.compile(r"^\d{8}$")


def force_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，统一转 UTF-8 避免中文/符号乱码。"""
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - 非标准流环境下保持原样
        pass


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def got_daily_root() -> Path:
    return repo_root() / "data" / "got_daily"


def iter_archive_days(include_backups: bool = False) -> list[Path]:
    """返回按日期升序排列的归档日目录。

    include_backups=False 时只保留形如 YYYYMMDD 的目录，
    排除 `20260805_backup` / `20260817_pre_fix` 这类非交易日目录。
    """
    root = got_daily_root()
    if not root.exists():
        return []
    days = [p for p in root.iterdir() if p.is_dir()]
    if not include_backups:
        days = [p for p in days if _DAY_DIR_PATTERN.match(p.name)]
    return sorted(days, key=lambda p: p.name)


def latest_archive_day_with(filename: str) -> str | None:
    """返回最近一个包含指定文件的归档日（YYYYMMDD）。"""
    for day in reversed(iter_archive_days()):
        if (day / filename).exists():
            return day.name
    return None


def read_json(path: Path | str) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ── 统计工具（纯 Python，避免对 scipy/pandas 的硬依赖） ──


def pct(numerator: int, denominator: int) -> float:
    """占比（0–1）；分母为 0 时返回 0.0。"""
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def format_pct(value: float) -> str:
    """按文档口径保留一位小数，例如 3.1% / 34.4%。"""
    return f"{value * 100:.1f}%"


def pearson(x: list[float], y: list[float]) -> float | None:
    """皮尔逊积矩相关系数；样本不足或方差为 0 时返回 None。"""
    n = len(x)
    if n < 2 or n != len(y):
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    var_x = sum((a - mean_x) ** 2 for a in x)
    var_y = sum((b - mean_y) ** 2 for b in y)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def _rank_first(values: list[float]) -> list[float]:
    """并列秩按「出现顺序」编号（等价 pandas rank(method="first")，与文档锚点一致）。"""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0.0] * len(values)
    for rank, index in enumerate(order, start=1):
        ranks[index] = float(rank)
    return ranks


def _rank_average(values: list[float]) -> list[float]:
    """并列秩取平均（等价 scipy.stats.spearmanr / pandas rank(method="average")）。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float], method: str = "average") -> float | None:
    """斯皮尔曼等级相关；并列秩处理方法由 method 指定（first / average）。"""
    if len(x) < 2 or len(x) != len(y):
        return None
    ranker = _rank_first if method == "first" else _rank_average
    return pearson(ranker(x), ranker(y))


class Verifier:
    """结果登记 + 退出码裁决。

    redline 结果决定退出码；auxiliary 结果只展示、不参与裁决
    （用于 R2.3 要求的「静态辅助检查降级」）。
    """

    def __init__(self, title: str, source_doc: str) -> None:
        self.title = title
        self.source_doc = source_doc
        self.redline: list[tuple[str, str, str, list[str]]] = []
        self.auxiliary: list[tuple[str, str, str, list[str]]] = []

    def add(
        self,
        name: str,
        status: str,
        detail: str,
        evidence: list[str] | None = None,
        auxiliary: bool = False,
    ) -> None:
        item = (name, status, detail, list(evidence or []))
        (self.auxiliary if auxiliary else self.redline).append(item)

    def check(
        self,
        name: str,
        passed: bool,
        detail: str,
        evidence: list[str] | None = None,
        auxiliary: bool = False,
    ) -> bool:
        self.add(name, STATUS_PASS if passed else STATUS_FAIL, detail, evidence, auxiliary)
        return passed

    def skip(self, name: str, reason: str, precondition: str) -> None:
        self.add(name, STATUS_SKIP, f"SKIP 原因：{reason}", [f"前置条件：{precondition}"])

    def _print_group(self, title: str, items: list[tuple[str, str, str, list[str]]]) -> None:
        if not items:
            return
        print("\n" + "=" * 68)
        print(title)
        print("=" * 68)
        for name, status, detail, evidence in items:
            print(f"[{status}] {name}")
            print(f"        {detail}")
            for line in evidence:
                print(f"        · {line}")

    def finish(self) -> int:
        self._print_group("红线验收结果", self.redline)
        if self.auxiliary:
            self._print_group(
                "辅助检查结果（不是红线，仅供参考，不参与退出码裁决）",
                self.auxiliary,
            )

        redline_statuses = [status for _, status, _, _ in self.redline]
        has_fail = STATUS_FAIL in redline_statuses
        has_skip = STATUS_SKIP in redline_statuses
        passed = redline_statuses.count(STATUS_PASS)
        total = len(redline_statuses)

        print("\n" + "=" * 68)
        print(
            f"汇总：{passed}/{total} PASS | "
            f"{redline_statuses.count(STATUS_FAIL)} FAIL | "
            f"{redline_statuses.count(STATUS_SKIP)} SKIP"
        )
        if has_fail:
            print("[RESULT] 存在 FAIL → 退出码 = 1")
            return EXIT_FAIL
        if has_skip:
            print("[RESULT] 无 FAIL 但存在 SKIP（未证明达标）→ 退出码 = 2")
            return EXIT_SKIP
        print("[RESULT] 全部 PASS 且无 SKIP → 退出码 = 0")
        return EXIT_PASS


def print_header(title: str, source_doc: str) -> None:
    print("=" * 68)
    print(title)
    print(f"红线定义出处：{source_doc}")
    print("=" * 68)
