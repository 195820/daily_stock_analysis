# -*- coding: utf-8 -*-
"""K 线家族簇（family_cluster）测试。

覆盖计划 `local://kline-family-cluster-plan.md` 的验收点：
- vote 簇 shadow 期：A6 标签只落 shadow、endurance 键不改 vote 路径、would_rewrite 记录不回写；
- vote_a6_agree_rate = vote 簇中 a6_label == vote_label 的比例；
- 跨日 Jaccard 继承：前日含 shadow（≥门槛继承 / <门槛新 ID）与不含 shadow（新 ID）两分支；
- 纯「其他」簇 A6 失败 → error、不 raise、键回 cluster_id；
- glue_status 非 ok → glue.families 视为空（分歧检测跳过）；
- `_load_previous_family_suppression` / 簇继承均锚 `_run_date`（不读未来归档）；
- ENABLED=0（family_clusters=None）→ endurance 分组键与现状一致；
- REVIEW=live 回写 frame_group → Phase 2 层 A join 与 FXG members 命中。
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got import family_cluster  # noqa: E402
from src.got.family_cluster import (  # noqa: E402
    ClusterRecord,
    build_cluster_id,
    build_family_clusters,
    resolve_endurance_key,
    summarize_review,
)


# ────────────────────────────── 夹具 ──────────────────────────────

def _theme_family(members, name="集群"):
    from src.got.schemas import ThemeFamily
    return ThemeFamily(
        family_name=name,
        family_score=70.0,
        family_resonance=0.5,
        theme_status="主线核心",
        member_boards=list(members),
        member_count=len(members),
    )


def _llm(mapping, calls=None):
    """按 prompt 里出现的首个板块名返回固定 JSON。"""
    async def _call(prompt, **kwargs):
        if calls is not None:
            calls.append(prompt)
        for board, label in mapping.items():
            if f"- {board}" in prompt:
                return json.dumps({"family": label, "confidence": 0.8, "reason": "test"})
        return "{}"
    return _call


def _llm_empty(prompt, **kwargs):
    return ""


async def _build(theme_families, lookup, *, mode="shadow", prev=None, glue=None,
                 glue_status="", llm=None, actionable=None, metrics=None, evidence=None,
                 jaccard_min=None):
    return await build_family_clusters(
        theme_families,
        lookup,
        date(2026, 10, 20),
        family_cluster.DEFAULT_JACCARD_MIN if jaccard_min is None else jaccard_min,
        mode,
        prev,
        glue,
        glue_status,
        evidence,
        actionable_boards=actionable,
        board_metrics=metrics,
        llm_call=llm,
    )


def _prev_shadow(clusters, agree_rate=None):
    stats = {"vote_a6_agree_rate": agree_rate} if agree_rate is not None else {}
    return {"family_cluster_shadow": {"clusters": clusters, "review_stats": stats}}


def _make_detail(name, code, score=60.0):
    from src.got.schemas import SectorDetail
    return SectorDetail(code=code, name=name, state="active_mainline", strength_score=score)


def _make_sector_ctx(details, theme_families=None, frame_group_lookup=None):
    from src.got.schemas import MainlineGroup, SectorContext
    group = MainlineGroup(
        group_name="测试家族",
        group_score=max((d.strength_score for d in details), default=0.0),
        tier="S",
        mainline_quality="strong",
        members=list(details),
    )
    return SectorContext(
        active_mainline=[group],
        theme_families=list(theme_families or []),
        frame_group_lookup=dict(frame_group_lookup or {}),
    )


def _make_intel(name, code, frame_group, *, composite=60.0):
    from src.got.schemas import SectorIntel
    return SectorIntel(
        sector_name=name, sector_code=code, sector_role="mainline",
        classification="actionable", composite_score=composite,
        frame_group=frame_group,
    )


def _make_regime():
    from src.got.schemas import MarketRegime
    return MarketRegime(
        regime="transition", risk_budget=0.5, fear_greed=0.5,
        sentiment_stage="neutral", sentiment_momentum=0.0,
    )


def _make_news():
    from src.got.schemas import NewsContext, DataQuality
    return NewsContext(
        overall_impact="neutral", major_events=[], concept_suggestions=[],
        impact_score=0.0, reasoning=None, data_quality=DataQuality(),
    )


# ────────────────────────────── 开关归一 ──────────────────────────────

class TestConfigNormalization(unittest.TestCase):
    def test_enabled_switch(self):
        with patch.dict(os.environ, {"GOT_FAMILY_CLUSTER_ENABLED": "0"}):
            self.assertFalse(family_cluster.enabled())
        with patch.dict(os.environ, {"GOT_FAMILY_CLUSTER_ENABLED": "1"}):
            self.assertTrue(family_cluster.enabled())

    def test_review_mode_fallback(self):
        self.assertEqual(family_cluster.resolve_review_mode("live"), "live")
        self.assertEqual(family_cluster.resolve_review_mode("shadow"), "shadow")
        self.assertEqual(family_cluster.resolve_review_mode("off"), "off")
        # 缺省/非法值一律 off：规则层不靠每日 LLM 复核存活（回测结案，零常态化 LLM 成本）
        self.assertEqual(family_cluster.resolve_review_mode("bogus"), "off")
        self.assertEqual(family_cluster.resolve_review_mode(""), "off")

    def test_jaccard_min_clamped(self):
        self.assertAlmostEqual(family_cluster.resolve_jaccard_min("0.5"), 0.5)
        self.assertAlmostEqual(family_cluster.resolve_jaccard_min("2.0"), 1.0)
        self.assertAlmostEqual(family_cluster.resolve_jaccard_min("bad"), 0.34)

    def test_review_label_whitelist(self):
        from src.got.family_cluster import parse_review_label
        self.assertEqual(
            parse_review_label('{"family": "石油"}'), (None, None),
            "非受控词表家族一律拒绝（白名单=PROMPT_EXPOSED_FAMILIES）",
        )
        self.assertEqual(parse_review_label('{"family": "AI硬件"}')[0], "AI硬件")
        self.assertIsNone(parse_review_label('{"family": "华为生态"}')[0])
        self.assertIsNone(parse_review_label('{"family": "其他"}')[0])
        self.assertIsNone(parse_review_label("not json")[0])


# ────────────────────────────── shadow 全簇复核 ──────────────────────────────

class TestShadowReview(unittest.IsolatedAsyncioTestCase):

    async def test_vote_cluster_shadow_keeps_key_and_records_rewrite(self):
        lookup = {"石油开采": "其他", "石油": "其他"}
        records = await _build(
            [_theme_family(["石油开采", "石油"])], lookup,
            mode="shadow",
            llm=_llm({"石油开采": "AI硬件"}),
            actionable=["石油开采", "石油"],
        )
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertIsNone(rec.vote_label, "两个成员均落「其他」→ 无投票")
        self.assertEqual(rec.a6_label, "AI硬件")
        self.assertEqual(rec.review_status, "shadow_only")
        # shadow 期不改写现行为：family_label 仍为空，键回 cluster_id
        self.assertIsNone(rec.family_label)
        self.assertEqual(rec.label_source, "cluster_id")
        self.assertEqual(rec.would_rewrite, {"石油开采": "AI硬件", "石油": "AI硬件"})

        clusters_by_board = {b: rec for b in rec.member_boards}
        key, source = resolve_endurance_key(
            "石油开采", frame_group="其他", clusters_by_board=clusters_by_board, mode="shadow",
        )
        self.assertEqual((key, source), (rec.cluster_id, "cluster_id"))

    async def test_mixed_cluster_resolves_per_board(self):
        """逐板块改键：具名板块（白酒=消费）维持现键；落「其他」的（酿酒）换簇 ID。

        整簇下发会把白酒也改判，制造误报；这里只堵漏报。
        """
        records = await _build(
            [_theme_family(["白酒", "酿酒", "贵州板块"])],
            {"白酒": "消费", "酿酒": "其他", "贵州板块": "其他"},
            mode="shadow", llm=_llm({"白酒": "消费"}),
            actionable=["酿酒"],
        )
        rec = records[0]
        self.assertEqual(rec.vote_label, "消费")
        clusters_by_board = {b: rec for b in rec.member_boards}
        self.assertEqual(
            resolve_endurance_key("白酒", frame_group="消费",
                                  clusters_by_board=clusters_by_board, mode="shadow"),
            ("消费", "baseline"),
        )
        self.assertEqual(
            resolve_endurance_key("酿酒", frame_group="其他",
                                  clusters_by_board=clusters_by_board, mode="shadow"),
            (rec.cluster_id, "cluster_id"),
        )
        self.assertEqual(rec.would_rewrite, {"酿酒": "消费"})

    async def test_vote_only_rewrite_touches_unnamed_boards(self):
        """A6 缺席时 would_rewrite 只含落「其他」的成员：已具名板块不被整簇改判。"""
        records = await _build(
            [_theme_family(["白酒", "酿酒"])], {"白酒": "消费", "酿酒": "其他"},
            mode="off", llm=None, actionable=["白酒", "酿酒"],
        )
        self.assertEqual(records[0].would_rewrite, {"酿酒": "消费"})

    async def test_tied_vote_keeps_named_key(self):
        """并列第一 → 无票（不整簇改判），具名板块仍走现状键。"""
        records = await _build(
            [_theme_family(["银行", "芯片"])], {"银行": "金融", "芯片": "AI硬件"},
            mode="off", llm=None,
        )
        rec = records[0]
        self.assertIsNone(rec.vote_label)
        self.assertEqual(rec.label_source, "cluster_id")
        clusters_by_board = {b: rec for b in rec.member_boards}
        self.assertEqual(
            resolve_endurance_key("银行", frame_group="金融",
                                  clusters_by_board=clusters_by_board, mode="off"),
            ("金融", "baseline"),
        )
        self.assertEqual(
            resolve_endurance_key("银行", frame_group="其他",
                                  clusters_by_board=clusters_by_board, mode="off"),
            (rec.cluster_id, "cluster_id"),
        )

    async def test_vote_cluster_shadow_uses_vote_key_not_a6(self):
        lookup = {"石油开采": "周期资源", "石油": "其他"}
        records = await _build(
            [_theme_family(["石油开采", "石油"])], lookup,
            mode="shadow",
            llm=_llm({"石油开采": "AI硬件"}),  # A6 与 vote 分歧：shadow 不下发
            actionable=["石油开采", "石油"],
        )
        rec = records[0]
        self.assertEqual(rec.vote_label, "周期资源")
        self.assertEqual(rec.family_label, "周期资源")
        self.assertEqual(rec.label_source, "vote")
        self.assertEqual(rec.a6_label, "AI硬件")
        key, source = resolve_endurance_key(
            "石油开采", frame_group="周期资源",
            clusters_by_board={b: rec for b in rec.member_boards}, mode="shadow",
        )
        self.assertEqual((key, source), ("周期资源", "baseline"))
        # 若切 live：A6 标签只补给落「其他」的石油；已具名的石油开采不被改判
        self.assertEqual(rec.would_rewrite, {"石油": "AI硬件"})

    async def test_agree_rate_two_of_three(self):
        lookup = {"A": "AI硬件", "B": "机器人", "C": "医药"}
        mapping = {"A": "AI硬件", "B": "机器人", "C": "消费"}
        records = await _build(
            [_theme_family(["A"]), _theme_family(["B"]), _theme_family(["C"])],
            lookup, mode="shadow", llm=_llm(mapping),
        )
        stats = summarize_review(records)
        self.assertEqual(stats["vote_clusters"], 3)
        self.assertEqual(stats["a6_reviewed"], 3)
        self.assertEqual(stats["a6_approved"], 3)
        self.assertAlmostEqual(stats["vote_a6_agree_rate"], 0.667, places=3)


# ────────────────────────────── 跨日 Jaccard 继承 ──────────────────────────────

class TestClusterInheritance(unittest.IsolatedAsyncioTestCase):

    async def test_inherit_when_overlap_above_threshold(self):
        prev = _prev_shadow([{"cluster_id": "abc123456789", "member_boards": ["石油开采", "石油"]}])
        records = await _build(
            [_theme_family(["石油开采", "石油", "油服"])], {},
            prev=prev, llm=_llm({}),
        )
        # Jaccard = 2/3 = 0.667 ≥ 0.34
        self.assertEqual(records[0].cluster_id, "abc123456789")

    async def test_new_id_when_overlap_below_threshold(self):
        prev = _prev_shadow([{"cluster_id": "abc123456789", "member_boards": ["石油开采", "石油"]}])
        records = await _build([_theme_family(["酿酒", "食品"])], {}, prev=prev, llm=_llm({}))
        self.assertEqual(records[0].cluster_id, build_cluster_id(["酿酒", "食品"]))
        self.assertNotEqual(records[0].cluster_id, "abc123456789")

    async def test_new_id_when_prev_has_no_shadow(self):
        records = await _build(
            [_theme_family(["石油开采", "石油"])], {},
            prev={"family_suppression": []}, llm=_llm({}),
        )
        self.assertEqual(records[0].cluster_id, build_cluster_id(["石油开采", "石油"]))

    async def test_new_id_when_no_prev(self):
        records = await _build([_theme_family(["石油开采", "石油"])], {}, prev=None, llm=_llm({}))
        self.assertEqual(records[0].cluster_id, build_cluster_id(["石油开采", "石油"]))


# ────────────────────────────── 复核降级 ──────────────────────────────

class TestReviewDegradation(unittest.IsolatedAsyncioTestCase):

    async def test_error_falls_back_to_cluster_id(self):
        records = await _build(
            [_theme_family(["酿酒", "食品"])], {"酿酒": "其他", "食品": "其他"},
            mode="shadow", llm=_llm_empty,
        )
        rec = records[0]
        self.assertEqual(rec.review_status, "error")
        self.assertIsNone(rec.family_label)
        self.assertEqual(rec.label_source, "cluster_id")
        self.assertEqual(rec.would_rewrite, {})
        key, source = resolve_endurance_key(
            "酿酒", frame_group="其他",
            clusters_by_board={b: rec for b in rec.member_boards},
        )
        self.assertEqual((key, source), (rec.cluster_id, "cluster_id"))

    async def test_exception_falls_back_without_raise(self):
        async def _boom(prompt, **kwargs):
            raise TimeoutError("bailian 429")

        records = await _build([_theme_family(["酿酒"])], {}, mode="live", llm=_boom)
        rec = records[0]
        self.assertEqual(rec.review_status, "error")
        self.assertEqual(rec.label_source, "cluster_id")
        self.assertIn("429", rec.review_error or "")

    async def test_off_mode_never_calls_llm(self):
        calls = []
        records = await _build(
            [_theme_family(["A"]), _theme_family(["B"])], {"A": "AI硬件"},
            mode="off", llm=_llm({"A": "AI硬件", "B": "消费"}, calls),
        )
        self.assertEqual(calls, [])
        self.assertEqual([r.review_status for r in records], ["skipped", "skipped"])
        self.assertEqual(records[0].family_label, "AI硬件")

    async def test_llm_absent_marks_skipped(self):
        records = await _build([_theme_family(["酿酒"])], {}, mode="shadow", llm=None)
        self.assertEqual(records[0].review_status, "skipped")
        self.assertEqual(records[0].label_source, "cluster_id")

    async def test_consecutive_errors_break_circuit(self):
        """连续 3 簇失败 → 剩余簇不再发调用，标 error（LLM 故障日必须显式可见）。"""
        calls = []

        async def _boom(prompt, **kwargs):
            calls.append(prompt)
            raise TimeoutError("bailian 429")

        members = [["A"], ["B"], ["C"], ["D"], ["E"]]
        records = await _build(
            [_theme_family(m) for m in members], {}, mode="shadow", llm=_boom,
        )
        self.assertEqual(len(calls), 3, "熔断后不得继续调用 LLM")
        self.assertEqual([r.review_status for r in records],
                         ["error"] * 5)
        self.assertEqual(records[-1].review_error, "aborted_after_consecutive_errors")
        self.assertEqual(summarize_review(records)["a6_error"], 5)


    async def test_zero_budget_aborts_before_first_call(self):
        calls = []
        with patch.dict(os.environ, {"GOT_FAMILY_CLUSTER_REVIEW_BUDGET_S": "0"}):
            records = await _build(
                [_theme_family(["A"]), _theme_family(["B"])], {},
                mode="shadow", llm=_llm({"A": "AI硬件"}, calls),
            )
        self.assertEqual(calls, [])
        self.assertEqual([r.review_error for r in records],
                         ["aborted_after_budget"] * 2)


# ────────────────────────────── live 信任度 + glue 分歧 ──────────────────────────────

class TestLiveReviewPolicy(unittest.IsolatedAsyncioTestCase):

    async def test_glue_status_unusable_skips_disagreement(self):
        calls = []
        records = await _build(
            [_theme_family(["石油开采", "石油"])], {"石油开采": "周期资源", "石油": "周期资源"},
            mode="live",
            prev=_prev_shadow([], agree_rate=0.95),
            glue={"石油开采": "AI硬件", "石油": "AI硬件"},
            glue_status="S2_parse_failed",
            llm=_llm({"石油开采": "AI硬件"}, calls),
        )
        rec = records[0]
        self.assertEqual(rec.review_status, "skipped", "glue 不可用 → 无分歧检测，vote 直接生效")
        self.assertEqual(rec.family_label, "周期资源")
        self.assertFalse(calls)

    async def test_glue_disagreement_escalates_review(self):
        calls = []
        records = await _build(
            [_theme_family(["石油开采", "石油"])], {"石油开采": "周期资源", "石油": "周期资源"},
            mode="live",
            prev=_prev_shadow([], agree_rate=0.95),
            glue={"石油开采": "AI硬件", "石油": "AI硬件"},
            glue_status="ok",
            llm=_llm({"石油开采": "AI硬件"}, calls),
        )
        rec = records[0]
        self.assertEqual(rec.review_status, "approved")
        self.assertEqual(rec.family_label, "AI硬件")
        self.assertEqual(rec.label_source, "a6_review")
        self.assertTrue(calls)

    async def test_low_trust_reviews_all_vote_clusters(self):
        calls = []
        records = await _build(
            [_theme_family(["石油开采"])], {"石油开采": "周期资源"},
            mode="live",
            prev=_prev_shadow([], agree_rate=0.5),
            llm=_llm({"石油开采": "周期资源"}, calls),
        )
        self.assertEqual(records[0].review_status, "approved")
        self.assertEqual(len(calls), 1)

    async def test_vote_trusted_skips_review(self):
        calls = []
        records = await _build(
            [_theme_family(["石油开采"])], {"石油开采": "周期资源"},
            mode="live",
            prev=_prev_shadow([], agree_rate=0.95),
            llm=_llm({"石油开采": "周期资源"}, calls),
        )
        self.assertEqual(records[0].review_status, "skipped")
        self.assertEqual(records[0].family_label, "周期资源")
        self.assertFalse(calls)

    async def test_other_cluster_always_reviewed_in_live(self):
        calls = []
        records = await _build(
            [_theme_family(["酿酒"])], {"酿酒": "其他"},
            mode="live",
            prev=_prev_shadow([], agree_rate=0.99),
            llm=_llm({"酿酒": "消费"}, calls),
        )
        self.assertEqual(records[0].review_status, "approved")
        self.assertEqual(records[0].family_label, "消费")
        self.assertEqual(len(calls), 1)

    async def test_live_only_relabels_unnamed_members(self):
        """live 也逐板块：A6 标签只补给落「其他」的成员，已具名板块键不动。"""
        records = await _build(
            [_theme_family(["石油开采", "石油"])],
            {"石油开采": "周期资源", "石油": "其他"},
            mode="live",
            prev=_prev_shadow([], agree_rate=0.0),
            llm=_llm({"石油开采": "AI硬件"}),
            actionable=["石油开采", "石油"],
        )
        rec = records[0]
        self.assertEqual(rec.family_label, "AI硬件")
        clusters = {b: rec for b in rec.member_boards}
        self.assertEqual(
            resolve_endurance_key("石油开采", frame_group="周期资源",
                                  clusters_by_board=clusters, mode="live"),
            ("周期资源", "baseline"),
        )
        self.assertEqual(
            resolve_endurance_key("石油", frame_group="其他",
                                  clusters_by_board=clusters, mode="live"),
            ("AI硬件", "cluster_label"),
        )

    async def test_a6_rejection_beats_vote(self):
        """A6 明确判「无归属」时不得回落到 vote——否则「其他」成员被写成被否定的家族。"""
        async def _reject(prompt, **kwargs):
            return '{"family": "其他", "confidence": 0.9, "reason": "混合驱动"}'

        records = await _build(
            [_theme_family(["石油加工", "电力", "新型电力"])],
            {"石油加工": "其他", "电力": "新能源", "新型电力": "新能源"},
            mode="live",
            prev=_prev_shadow([], agree_rate=0.0),  # 强制复核
            llm=_reject,
            actionable=["石油加工"],
        )
        rec = records[0]
        self.assertEqual(rec.vote_label, "新能源")
        self.assertEqual(rec.review_status, "rejected")
        self.assertIsNone(rec.family_label)
        self.assertEqual(rec.label_source, "cluster_id")
        self.assertEqual(rec.would_rewrite, {})
        key, source = resolve_endurance_key(
            "石油加工", frame_group="其他",
            clusters_by_board={b: rec for b in rec.member_boards}, mode="live",
        )
        self.assertEqual((key, source), (rec.cluster_id, "cluster_id"))

    async def test_live_payload_carries_prev_agreement_rate(self):
        """live 期 vote 簇被信任豁免 → 当日无 vote 样本，读数须顺延前日（否则门槛隔日失效）。"""
        prev = _prev_shadow([], agree_rate=0.9)
        records = await _build(
            [_theme_family(["石油开采"])], {"石油开采": "周期资源"},
            mode="live", prev=prev, llm=_llm({"石油开采": "周期资源"}),
        )
        self.assertEqual(records[0].review_status, "skipped")
        payload = family_cluster.build_shadow_payload(
            records, mode="live", jaccard_min=0.34,
            actionable_boards=["石油开采"], frame_group_lookup={"石油开采": "周期资源"},
            glue_families=None, prev_unified=prev,
        )
        stats = payload["review_stats"]
        self.assertEqual(stats["vote_a6_agree_rate"], 0.9)
        self.assertEqual(stats["vote_a6_agree_rate_source"], "carried")

    async def test_shadow_payload_does_not_carry_rate(self):
        records = await _build(
            [_theme_family(["石油开采"])], {"石油开采": "周期资源"},
            mode="shadow", prev=_prev_shadow([], agree_rate=0.9), llm=None,
        )
        payload = family_cluster.build_shadow_payload(
            records, mode="shadow", jaccard_min=0.34,
            actionable_boards=["石油开采"], frame_group_lookup={"石油开采": "周期资源"},
            glue_families=None, prev_unified=_prev_shadow([], agree_rate=0.9),
        )
        self.assertIsNone(payload["review_stats"]["vote_a6_agree_rate"])
        self.assertIsNone(payload["review_stats"]["vote_a6_agree_rate_source"])


# ────────────────────────────── Node 2 聚类基（beta 去除）──────────────────────────────

class TestClusterBasis(unittest.TestCase):
    """raw 相关被大盘 beta 主导（同涨同跌）；residual 剔除截面均值后才分得开真子簇。"""

    def _fixture(self, n=80):
        import math
        from src.got.schemas import SectorDetail
        # 三个互相正交的确定性序列：市场 m、残差 s1、残差 s2
        phase = [math.pi * 2 * i / 12 for i in range(n)]
        m = [0.02 * math.sin(p) for p in phase]
        s1 = [0.02 * math.cos(p) for p in phase]
        s2 = [0.02 * math.sin(2 * p) for p in phase]

        def _klines(rets):
            close, out = 100.0, []
            for r in rets:
                close *= 1 + r
                out.append({"close": close})
            return out

        specs = {
            "A": [a + 0.5 * b for a, b in zip(m, s1)],
            "B": [a + 0.5 * b for a, b in zip(m, s1)],
            "C": [a + 0.5 * b for a, b in zip(m, s2)],
            "D": [a + 0.5 * b for a, b in zip(m, s2)],
        }
        analyzed, raw = [], []
        for i, (name, rets) in enumerate(specs.items()):
            # rets 已是日收益；补一条 0 让 kline 长度 = len(rets)+1
            analyzed.append(SectorDetail(
                code=f"BK{i:04d}", name=name, state="active_mainline", strength_score=60.0,
            ))
            raw.append({"code": f"BK{i:04d}", "klines": _klines([0.0] + list(rets))})
        return analyzed, raw

    def _families(self, basis):
        from src.got.nodes.sector_analysis import SectorAnalysisNode
        node = SectorAnalysisNode(use_mock=True)
        analyzed, raw = self._fixture()
        with patch.dict(os.environ, {
            "GOT_FAMILY_CLUSTER_BASIS": basis, "GOT_FAMILY_CLUSTER_WINDOW": "90",
        }):
            return node._build_theme_families(analyzed, raw)

    def test_raw_basis_merges_everything(self):
        families = self._families("raw")
        self.assertEqual(len(families), 1, "原始收益相关被 beta 拉满 → 四板块粘成一簇")
        self.assertEqual(my_sorted(families[0].member_boards), ["A", "B", "C", "D"])

    def test_residual_basis_splits_true_subclusters(self):
        families = self._families("residual")
        groups = sorted(my_sorted(f.member_boards) for f in families)
        self.assertEqual(groups, [["A", "B"], ["C", "D"]])


def my_sorted(members):
    return sorted(str(m) for m in members)


# ────────────────────────────── endurance 分组键 ──────────────────────────────

class TestEnduranceGrouping(unittest.TestCase):

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)
        self._patcher = patch("src.got.nodes.aggregator.GOT_A6_ENABLED", True)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _run(self, intels, details, clusters):
        sector = _make_sector_ctx(details)
        return self.node._synthesize_mainline_endurance(
            intels, sector, _make_news(), _make_regime(),
            glue=None, family_clusters=clusters, family_cluster_mode="shadow",
        )

    def test_off_path_merges_heterogeneous_boards(self):
        """ENABLED=0（family_clusters=None）：现状三级键 → 异质板块同落「其他」并组。"""
        names = ["石油开采", "酿酒"]
        details = [_make_detail("石油开采", "BK0001"), _make_detail("酿酒", "BK0002")]
        intels = [_make_intel("石油开采", "BK0001", "其他"), _make_intel("酿酒", "BK0002", "其他")]
        endurance = self.node._synthesize_mainline_endurance(
            intels, _make_sector_ctx(details), _make_news(), _make_regime(), glue=None,
        )
        self.assertEqual([e.name for e in endurance], ["其他"])
        self.assertEqual(sorted(endurance[0].tiers["new"]), sorted(names))

    def test_shadow_splits_unresolved_clusters(self):
        details = [_make_detail("石油开采", "BK0001"), _make_detail("酿酒", "BK0002")]
        intels = [_make_intel("石油开采", "BK0001", "其他"), _make_intel("酿酒", "BK0002", "其他")]
        rec_a = ClusterRecord(cluster_id="cid-oil", member_boards=["石油开采", "石油"])
        rec_b = ClusterRecord(cluster_id="cid-liquor", member_boards=["酿酒", "白酒"])
        endurance = self._run(intels, details, [rec_a, rec_b])
        self.assertEqual(sorted(e.name for e in endurance), ["cid-liquor", "cid-oil"])

    def test_shadow_vote_cluster_keeps_frame_group_key(self):
        details = [_make_detail("石油开采", "BK0001")]
        intels = [_make_intel("石油开采", "BK0001", "周期资源")]
        rec = ClusterRecord(
            cluster_id="cid-oil", member_boards=["石油开采", "石油"],
            vote_label="周期资源", family_label="周期资源", label_source="vote",
        )
        endurance = self._run(intels, details, [rec])
        self.assertEqual([e.name for e in endurance], ["周期资源"])

# ────────────────────────────── 回写（live）与 Phase 2 ──────────────────────────────

class TestLiveRewrite(unittest.TestCase):

    def test_rewrite_only_actionable_mainline(self):
        from src.got.nodes.aggregator import AggregatorNode
        oil = _make_intel("石油开采", "BK0001", "其他")
        watch = _make_intel("石油开采", "BK0001", "其他")
        watch.classification = "watch"
        ambush = _make_intel("石油开采", "BK0001", "其他")
        ambush.sector_role = "ambush"
        rec = ClusterRecord(
            cluster_id="cid", member_boards=["石油开采"],
            family_label="周期资源", label_source="a6_review",
            would_rewrite={"石油开采": "周期资源"},
        )
        rewritten = AggregatorNode._apply_family_cluster_rewrite([oil, watch, ambush], [rec])
        self.assertEqual(rewritten, 1)
        self.assertEqual(oil.frame_group, "周期资源")
        self.assertEqual(watch.frame_group, "其他")
        self.assertEqual(ambush.frame_group, "其他")


class TestPhase2Join(unittest.TestCase):
    """REVIEW=live 回写后的 frame_group 让层 A join / FXG members 真正命中。"""

    def setUp(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        self.node = PortfolioDecisionNode(use_mock=True)
        self.env = patch.dict(os.environ, {
            "GOT_FAMILY_TOPPING_GATE_ENABLED": "1",
            "GOT_FAMILY_TOPPING_SIZING": "1",
            "GOT_FAMILY_TOPPING_MODE": "veto",
            "GOT_FAMILY_TOPPING_EXCLUDE_MIN_DAYS": "2",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def _unified(self, frame_group):
        from src.got.schemas import FamilySuppression, SectorIntel, UnifiedContext
        intel = SectorIntel(
            sector_name="石油开采", sector_code="BK0001", sector_role="mainline",
            classification="actionable", composite_score=60.0, confidence=0.8,
            frame_group=frame_group,
        )
        suppression = FamilySuppression(
            name="周期资源", aggravated=True, days_suppressed=2,
            days_healthy=0, members=["石油开采"],
        )
        return UnifiedContext(
            status="normal", unified_signal="neutral", overall_confidence=0.5,
            final_position_limit=0.5, confirmed_sectors=[intel],
            family_suppression=[suppression],
        )

    def test_layer_a_join_hits_after_rewrite(self):
        unified = self._unified("周期资源")
        assessment = self.node._assess_risk(unified, {"total_equity": 1.0, "total_market_value": 0.0})
        self.assertEqual(len(self.node._fts_sizing_flags), 1)
        self.assertEqual(self.node._fts_sizing_flags[0]["family"], "周期资源")
        # 有效上限 = 0.12(base 降档) × 0.65(transition) × 1.0(mainline) × 0.8(conf)
        self.assertAlmostEqual(assessment.sector_exposure_limits["石油开采"], 0.062, places=3)

    def test_layer_a_join_misses_on_other(self):
        """shadow 期现状缺口：frame_group 落「其他」→ 层 A join 空转（live 回写修复）。"""
        unified = self._unified("其他")
        self.node._assess_risk(unified, {"total_equity": 1.0, "total_market_value": 0.0})
        self.assertEqual(self.node._fts_sizing_flags, [])

    def test_fxg_members_hits(self):
        unified = self._unified("周期资源")
        intel_map = {"石油开采": {
            "lifecycle_phase": "acceleration", "news_check": "neutral", "capital_check": "neutral",
        }}
        excluded, exempted, audit = self.node._compute_fxg_exclusions(
            unified, intel_map, {"石油开采"},
        )
        self.assertEqual(excluded, {"石油开采"})
        self.assertEqual(exempted, set())
        self.assertEqual(audit[0]["family"], "周期资源")


# ────────────────────────────── 归档锚定（防前视） ──────────────────────────────

class TestArchiveAnchor(unittest.TestCase):

    def _write_unified(self, root: Path, day: str, payload: dict):
        day_dir = root / day
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / "unified.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8",
        )

    def test_previous_suppression_does_not_read_future(self):
        from src.got.nodes.aggregator import AggregatorNode
        from src.got import persistence

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_unified(root, "20261020", {"family_suppression": [{"name": "过去"}]})
            self._write_unified(root, "20261022", {"family_suppression": [{"name": "未来"}]})
            node = AggregatorNode(use_mock=True)
            node._run_date = date(2026, 10, 21)
            with patch.object(persistence, "_ARCHIVE_DIR", root):
                prev = node._load_previous_family_suppression()
        self.assertEqual(list(prev), ["过去"])

    def test_previous_cluster_shadow_anchor(self):
        from src.got.nodes.aggregator import AggregatorNode
        from src.got import persistence

        shadow = {"clusters": [{"cluster_id": "past-id", "member_boards": ["石油"]}],
                  "review_stats": {"vote_a6_agree_rate": 0.9}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_unified(root, "20261020", {"family_cluster_shadow": shadow})
            self._write_unified(root, "20261022", {"family_cluster_shadow": {
                "clusters": [{"cluster_id": "future-id", "member_boards": ["石油"]}],
            }})
            node = AggregatorNode(use_mock=True)
            node._run_date = date(2026, 10, 21)
            with patch.object(persistence, "_ARCHIVE_DIR", root):
                prev = node._load_previous_family_cluster_shadow()
        self.assertEqual(prev["family_cluster_shadow"]["clusters"][0]["cluster_id"], "past-id")

    def test_previous_shadow_absent_is_chain_head(self):
        from src.got.nodes.aggregator import AggregatorNode
        from src.got import persistence

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_unified(root, "20261020", {"family_suppression": []})
            node = AggregatorNode(use_mock=True)
            node._run_date = date(2026, 10, 21)
            with patch.object(persistence, "_ARCHIVE_DIR", root):
                self.assertIsNone(node._load_previous_family_cluster_shadow())


# ────────────────────────────── 观测投影 ──────────────────────────────

class TestShadowPayload(unittest.IsolatedAsyncioTestCase):

    async def test_payload_fields_and_coverage(self):
        lookup = {"石油开采": "其他", "石油": "其他", "酿酒": "其他"}
        records = await _build(
            [_theme_family(["石油开采", "石油"]), _theme_family(["酿酒"])],
            lookup,
            mode="shadow",
            llm=_llm({"石油开采": "AI硬件", "酿酒": "消费"}),
            actionable=["石油开采", "石油", "酿酒"],
            evidence={"石油开采": {"event_ids": ["ev1"], "theme": "原油大涨"}},
        )
        payload = family_cluster.build_shadow_payload(
            records, mode="shadow", jaccard_min=0.34,
            actionable_boards=["石油开采", "石油", "酿酒"],
            frame_group_lookup=lookup, glue_families=None,
        )
        stats = payload["review_stats"]
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["a6_approved"], 2)
        self.assertEqual(payload["coverage_before"]["其他_rate"], 1.0)
        self.assertEqual(payload["coverage_after"]["其他_rate"], 0.0)
        self.assertEqual(payload["coverage_after"]["cluster_id_rate"], 1.0)
        oil = next(c for c in payload["clusters"] if "石油开采" in c["member_boards"])
        self.assertEqual(oil["evidence_ids"], ["ev1"])
        self.assertEqual(oil["event_theme"], "原油大涨")
        self.assertTrue(oil["would_rewrite"])
        json.dumps(payload, ensure_ascii=False)  # 必须可 JSON 落盘


if __name__ == "__main__":
    unittest.main()
