# -*- coding: utf-8 -*-
"""OPEN-C2 · M3 人工终审评审包脚本纯函数回归（2026-10-06）

只测无副作用计算件：分层抽样（确定性 / 不跨层补齐 / 分母类别过滤）、判定档位归一、
误配率计分（只有「无关系」计分子、无法判定不入分母、glue 弃权不入分母）、
连续达标与红线边界、评审包渲染（判定列必须留空）。

脚本本身**不代判**（M3 的 ground truth 是市场共识，见【glue 计划】§8.1），
所以这里测的是「流程口径有没有被代码正确钉住」，不是判读结论。
"""
import csv
import importlib.util
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPEC_PATH = os.path.join(ROOT, "scripts", "build_m3_review_package.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("m3_review_package", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _row(day, code, cat, name=None, rule="其他", glue="新能源"):
    return {
        "day": day, "code": code, "name": name or f"板{code}", "category": cat,
        "rule_family": rule, "glue_family": glue, "glue_strength": 0.4,
        "glue_confidence": 0.5, "glue_evidence": "", "news_evidence": [],
        "unstable_with": [],
    }


def _rows_10():
    """单日：回收 6 + 冲突 4 + 一致 5 + 弃权 3 + 双空 2。"""
    rows = [_row("20260930", f"88{i:04d}", "recovered") for i in range(6)]
    rows += [_row("20260930", f"89{i:04d}", "overridden", rule="消费") for i in range(4)]
    rows += [_row("20260930", f"90{i:04d}", "same", rule="新能源") for i in range(5)]
    rows += [_row("20260930", f"91{i:04d}", "dropped", glue="无归属") for i in range(3)]
    rows += [_row("20260930", f"92{i:04d}", "both_none", glue="无归属") for i in range(2)]
    return rows


# ── 分层抽样 ────────────────────────────────────────────────
def test_stratify_is_deterministic_per_seed(mod):
    a = mod.stratify(_rows_10(), 5, 3, 2, seed=42)["picked"]
    b = mod.stratify(_rows_10(), 5, 3, 2, seed=42)["picked"]
    assert [r["code"] for r in a] == [r["code"] for r in b]
    assert len(a) == 10                                     # 回收5 + 冲突3 + 随机2


def test_stratify_layer_sizes_and_no_cross_layer_backfill(mod):
    rows = [_row("20260930", "880001", "recovered")]         # 只有 1 条回收、0 冲突
    rows += [_row("20260930", f"90000{i}", "same", rule="新能源") for i in range(5)]
    got = mod.stratify(rows, 5, 3, 2, seed=42)
    assert len(got["picked"]) == 3                          # 1 回收 + 0 冲突 + 2 随机
    assert got["shortfalls"]["20260930·recovered"] == {"asked": 5, "available": 1, "taken": 1}
    assert got["shortfalls"]["20260930·overridden"] == {"asked": 3, "available": 0, "taken": 0}
    assert all(r["category"] != "same" or True for r in got["picked"])
    # 关键：缺额不得用「一致」样本补齐（会把误配率稀释成假达标）
    assert sum(1 for r in got["picked"] if r["category"] == "recovered") == 1
    assert sum(1 for r in got["picked"] if r["category"] == "overridden") == 0


def test_stratify_random_layer_only_draws_attributed_same(mod):
    """glue 弃权/双空不是「归属」，不能进 M3 分母，也就不该被抽进对照层。"""
    rows = [_row("20260930", f"91{i:04d}", "dropped", glue="无归属") for i in range(9)]
    got = mod.stratify(rows, 0, 0, 3, seed=7)
    assert got["picked"] == []
    assert got["pools"]["20260930·random"] == 0
    assert got["pools"]["20260930·unattributed"] == 9       # 弃权率如实披露


def test_stratify_days_are_independent_substreams(mod):
    base = [_row("20260930", f"88{i:04d}", "recovered") for i in range(6)]
    two = base + [_row("20260929", f"87{i:04d}", "recovered") for i in range(6)]
    only_last = {r["code"] for r in mod.stratify(base, 5, 0, 0, 42)["picked"]}
    with_extra = {r["code"] for r in mod.stratify(two, 5, 0, 0, 42)["picked"] if r["day"] == "20260930"}
    assert only_last == with_extra                           # 多一天不改变原有日的样本


def test_pick_full_keeps_every_candidate(mod):
    rows = _rows_10()
    got = mod.pick_full(rows)
    assert len(got["picked"]) == len(rows)
    assert got["shortfalls"] == {}


def test_resolve_days_explicit_skips_discovery(mod):
    assert mod.resolve_days(["20260930", "20260929"], 5) == ["20260929", "20260930"]


# ── 判定档归一 ──────────────────────────────────────────────
def test_normalize_grade_aliases_and_unknown(mod):
    assert mod.normalize_grade("强关系") == mod.GRADE_STRONG
    assert mod.normalize_grade("真实") == mod.GRADE_STRONG
    assert mod.normalize_grade("牵强") == mod.GRADE_WEAK
    assert mod.normalize_grade("无关系") == mod.GRADE_NONE
    assert mod.normalize_grade("无法判定") == mod.GRADE_UNKNOWN
    assert mod.normalize_grade("") is None
    assert mod.normalize_grade("看着还行") is None            # 未知值不蒙对


def test_grade_maps_to_audit_html_buttons(mod):
    """评审包四档与审核工具 HTML 的 对/错/存疑 键位必须互认。"""
    assert mod.GRADE_TO_HTML_BTN[mod.GRADE_STRONG] == "ok"
    assert mod.GRADE_TO_HTML_BTN[mod.GRADE_NONE] == "bad"
    assert mod.GRADE_TO_HTML_BTN[mod.GRADE_WEAK] == "unsure"


# ── 计分 ────────────────────────────────────────────────────
def _rec(day, cat, grade):
    return {"day": day, "category": cat, "grade": grade, "code": "880001", "name": "板"}


def test_score_only_none_counts_as_misfire(mod):
    recs = [_rec("20260930", "recovered", mod.GRADE_STRONG) for _ in range(6)]
    recs += [_rec("20260930", "recovered", mod.GRADE_WEAK) for _ in range(3)]
    recs += [_rec("20260930", "recovered", mod.GRADE_NONE)]
    res = mod.score_records(recs)
    d = res["days"][0]
    assert d["judged"] == 10 and d[mod.GRADE_NONE] == 1
    assert d["misfire_pct"] == 10.0                          # 1/10 ≤ 15% → 达标
    assert d["pass"] is True


def test_score_red_line_boundary_two_of_ten_fails(mod):
    recs = [_rec("20260930", "same", mod.GRADE_STRONG) for _ in range(8)]
    recs += [_rec("20260930", "same", mod.GRADE_NONE) for _ in range(2)]
    d = mod.score_records(recs)["days"][0]
    assert d["misfire_pct"] == 20.0 and d["pass"] is False   # 2 条即破线


def test_score_undecidable_and_unjudged_excluded(mod):
    recs = [
        _rec("20260930", "recovered", mod.GRADE_STRONG),
        _rec("20260930", "recovered", mod.GRADE_UNKNOWN),   # 无法判定：不入分子分母
        _rec("20260930", "recovered", ""),                  # 未回填
    ]
    d = mod.score_records(recs)["days"][0]
    assert d["judged"] == 1 and d["undecidable"] == 1 and d["unjudged"] == 1
    assert d["misfire_pct"] == 0.0
    res = mod.score_records([_rec("20260930", "recovered", "")])
    assert res["days"][0]["judged"] == 0
    assert res["days"][0]["misfire_pct"] is None and res["days"][0]["pass"] is False


def test_score_dropped_rows_are_out_of_denominator(mod):
    """glue 弃权（dropped/both_none）即便被填「无关系」也不入 M3 分母（§8.1 定义）。"""
    recs = [
        _rec("20260930", "dropped", mod.GRADE_NONE),
        _rec("20260930", "both_none", mod.GRADE_NONE),
        _rec("20260930", "recovered", mod.GRADE_STRONG),
    ]
    d = mod.score_records(recs)["days"][0]
    assert d["judged"] == 1 and d["excluded_unattributed"] == 2
    assert d["misfire_pct"] == 0.0


def test_score_streak_semantics(mod):
    def day(none, d):
        recs = [_rec(d, "same", mod.GRADE_STRONG) for _ in range(9)]
        recs += [_rec(d, "same", mod.GRADE_NONE) for _ in range(none)]
        return recs

    good = mod.score_records(day(1, "20260930") + day(0, "20260929") + day(1, "20260928"))
    assert good["streak_days_met"] == 3 and good["status"] == "pending_more_days"
    bad = mod.score_records(day(0, "20260930") + day(9, "20260929") + day(0, "20260928"))
    assert bad["streak_days_met"] == 1 and bad["longest_streak_days"] == 1
    assert bad["status"] == "not_met"
    met = mod.score_records(sum((day(0, f"2026093{i}") for i in range(0, 4)), []) + day(1, "20260934"))
    assert met["streak_days_met"] == 5 and met["status"] == "met"


def test_score_no_data_day_does_not_fake_a_fail_or_pass(mod):
    recs = [_rec("20260930", "same", mod.GRADE_STRONG)] + [_rec("20260929", "same", "")]
    res = mod.score_records(recs)
    assert res["days_with_data"] == 1 and res["days_without_data"] == 1
    assert res["streak_days_met"] == 1                       # 无数据日不入连续序列


# ── 渲染与回填表 ────────────────────────────────────────────
def _pkg(mod, sample_mode="stratified"):
    sel = mod.stratify(_rows_10(), 5, 3, 2, seed=42) if sample_mode == "stratified" \
        else mod.pick_full(_rows_10())
    return {
        "generated_at": "2026-10-06T00:00:00", "days": ["20260930"], "sample_mode": sample_mode,
        "seed": 42, "rows": sel["picked"], "pools": sel["pools"],
        "shortfalls": sel["shortfalls"], "csv_path": "m3_review_x.csv",
    }


def test_render_md_keeps_verdict_blank_and_states_process(mod):
    md = mod.render_md(_pkg(mod))
    assert mod.GRADE_STRONG in md and "≤ 15%" in md
    assert "负责人" in md and "本脚本只搬运事实" not in md.split("\n")[0]
    assert "＿" in md                                        # 判定列留空（不代判）
    assert "unattributed" in md                              # 弃权率池规模
    for line in md.splitlines():                            # 表头单元格不得含裸 |
        if line.startswith("| 20260930·"):
            assert line.count("|") == 3


def test_render_md_reports_shortfall(mod):
    md = mod.render_md(_pkg(mod))
    assert "抽样缺口" not in md or "未跨层补齐" in md          # 有缺口必须写明不补齐


def test_process_note_records_promotion_decision(mod):
    """2026-10-06 负责人裁决：M3 直接以分层终审读数结案，不再要求 full 复跑。

    这条断言守的是「口径文案与裁决一致」：full 复跑作为前置已被放弃，若文案里又出现
    「转正必须 full 复跑」，评审包会对使用者发出一个永远不会被满足的要求（假告警）。
    """
    note = mod.process_note("stratified")
    assert "结案" in note and "2026-10-06" in note
    assert "放弃 full 复跑" in note
    assert "必须用 `--sample-mode full`" not in note
    # stratified 仍是偏高读数（只是偏保守），不得退化成「无条件可信」
    assert "难点超采" in note and "日常预警" in note


def test_csv_roundtrip_leaves_grade_empty(mod, tmp_path):
    pkg = _pkg(mod)
    recs = mod.csv_rows(pkg["rows"])
    assert all(r["grade"] == "" and r["note"] == "" for r in recs)
    assert set(recs[0]) == set(mod.CSV_COLUMNS)
    path = tmp_path / "m3.csv"
    mod.write_csv(path, recs)
    back = mod.read_csv(path)
    assert len(back) == len(recs) and back[0]["day"] == "20260930"
    with path.open("rb") as fh:
        assert fh.read(3) == b"\xef\xbb\xbf"          # utf-8-sig：Excel 打开直接可填
