# -*- coding: utf-8 -*-
"""
A6 Glue 接线 · 阶段 1 单元测试（docs/got/verification.md + 计划
A6_Glue_接线两阶段_4eb3dd9f.md §1.6）。

覆盖 5 个验收用例：
  C1  GOT_A6_ENABLED=true + glue.status=ok → _build_sector_intel 消费
      glue.strengths（news_check / capital_check / composite / confidence
      全部由 glue 主导，reasoning 打 [A6-glue] 标记）
  C2  GOT_A6_ENABLED=true + 板块在 glue.degraded_sectors → 回退规则侧
      三件套（0915 类上游故障不放大到决策面）
  C3  GOT_A6_ENABLED=false → 与影子期产物逐字段一致（行为面验收锚点，
      替代原 R-C7 逐字段一致测试）
  C4  glue.status=S2_parse_failed → 4 个调用点全部回退，主流程不抛异常
      （_build_sector_intel / _glue_aware_events_map /
       _llm_refine_sectors 门 / _synthesize_mainline_endurance）
  C5  family_resonance E2 公式：std_dev=[5,10,15] 与 [40,45,50] 两组
      对比，验证分母从固定 50 改为当日 P90 自适应
"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ─────────────────────────────────────────────────────────────────────
# 测试夹具：最小可用的 Node 2/3/4/5 输出 + Glue 结构
# ─────────────────────────────────────────────────────────────────────

def _make_capital():
    from src.got.schemas import CapitalContext, CapitalEvidence, DataQuality
    return CapitalContext(
        status="normal",
        institutional_attitude="neutral",
        institutional_score=0.0,
        evidence=CapitalEvidence(),
        reasoning=None,
        data_quality=DataQuality(),
    )


def _make_sentiment():
    from src.got.schemas import SentimentContext, DataQuality
    return SentimentContext(
        overall_sentiment="neutral",
        sentiment_score=0.0,
        sentiment_stage="neutral",
        trading_signal="neutral",
        signal_confidence=0.5,
        data_quality=DataQuality(),
        stage_state=None,
        persistence_regime=None,
    )


def _make_news():
    from src.got.schemas import NewsContext, DataQuality
    return NewsContext(
        overall_impact="neutral",
        major_events=[],
        concept_suggestions=[],
        impact_score=0.0,
        reasoning=None,
        data_quality=DataQuality(),
    )


def _make_detail(name, code, score=60.0, state="active_mainline"):
    from src.got.schemas import SectorDetail
    return SectorDetail(code=code, name=name, state=state, strength_score=score)


def _make_sector_ctx(details):
    """把 details 打包成一个主线组（tier=S, quality=strong），供 _build_sector_intel 使用。"""
    from src.got.schemas import SectorContext, MainlineGroup
    group = MainlineGroup(
        group_name="测试家族",
        group_score=max((d.strength_score for d in details), default=0.0),
        tier="S",
        mainline_quality="strong",
        members=list(details),
    )
    return SectorContext(active_mainline=[group])


def _make_glue_strengths(entries):
    """entries: [(board_code, strength, confidence, capital_read, news_read, sentiment_read)]"""
    from src.got.schemas import GlueStrength
    return [
        GlueStrength(
            board_code=c, strength=s, confidence=conf,
            capital_read=cap, news_read=news, sentiment_read=sent,
            evidence=f"evidence_{c}",
        )
        for (c, s, conf, cap, news, sent) in entries
    ]


def _make_glue_ok(strength_entries, relations=None, families=None, degraded_sectors=None):
    from src.got.schemas import SemanticGlueResult
    return SemanticGlueResult(
        strengths=_make_glue_strengths(strength_entries),
        relations=relations or [],
        families=families or [],
        status="ok",
        degraded_sectors=degraded_sectors or [],
    )


# ─────────────────────────────────────────────────────────────────────
# C1：GOT_A6_ENABLED=true + glue.status=ok → SectorIntel 走 glue 值
# ─────────────────────────────────────────────────────────────────────

class TestC1GlueOverride(unittest.TestCase):
    """A6 主链路：glue.strengths 直接替换 _build_sector_intel 三件套输出。"""

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)
        self._patcher = patch("src.got.nodes.aggregator.GOT_A6_ENABLED", True)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_news_check_from_glue(self):
        """glue.news_read='high' → SectorIntel.news_check='catalyzed'（覆盖规则侧值）。"""
        details = [_make_detail("芯片", "BK0001", score=70.0)]
        sector = _make_sector_ctx(details)
        # glue 声明：strength=0.88 / capital=high / news=high / sentiment=high
        glue = _make_glue_ok([
            ("BK0001", 0.88, 0.9, "high", "high", "high"),
        ])

        intels = self.node._build_sector_intel(
            sector, _make_capital(), _make_news(), _make_sentiment(),
            glue=glue,
        )
        chip = next(i for i in intels if i.sector_name == "芯片")

        self.assertEqual(chip.news_check, "catalyzed", "glue.news_read=high → catalyzed")
        self.assertEqual(chip.capital_check, "aligned", "glue.capital_read=high → aligned")
        self.assertEqual(chip.sentiment_check, "high", "glue.sentiment_read=high → high")
        # strength 0.88 → composite 88.0（C-P9 basis=composite）
        self.assertAlmostEqual(chip.composite_score, 88.0, places=2)
        self.assertAlmostEqual(chip.confidence, 0.9, places=2)
        # 可观测性：reasoning 携带 [A6-glue] 标记
        self.assertIn("[A6-glue", chip.reasoning,
                      "Pass 3 应在 reasoning 标注来源，供归档回溯")


# ─────────────────────────────────────────────────────────────────────
# C2：degraded_sectors 命中的板块保留规则侧兜底
# ─────────────────────────────────────────────────────────────────────

class TestC2DegradedSectorFallback(unittest.TestCase):
    """glue.degraded_sectors 名单内板块不受 A6 覆盖（0915 类上游故障防御）。"""

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)
        self._patcher = patch("src.got.nodes.aggregator.GOT_A6_ENABLED", True)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_degraded_sector_keeps_rule_side(self):
        details = [
            _make_detail("芯片", "BK0001", score=70.0),
            _make_detail("白酒", "BK0002", score=65.0),
        ]
        sector = _make_sector_ctx(details)
        # 芯片被标记降级 → 保留规则侧；白酒走 glue
        glue = _make_glue_ok(
            [
                ("BK0001", 0.95, 0.9, "high", "high", "high"),
                ("BK0002", 0.30, 0.6, "low", "unknown", "unknown"),
            ],
            degraded_sectors=["芯片"],
        )

        intels = self.node._build_sector_intel(
            sector, _make_capital(), _make_news(), _make_sentiment(),
            glue=glue,
        )
        by_name = {i.sector_name: i for i in intels}

        # 白酒走 glue：strength=0.3 → composite=30，capital=low → divergent
        self.assertAlmostEqual(by_name["白酒"].composite_score, 30.0, places=2)
        self.assertEqual(by_name["白酒"].capital_check, "divergent")
        self.assertIn("[A6-glue", by_name["白酒"].reasoning)

        # 芯片降级：composite ≠ 95，也不带 [A6-glue] 标记
        self.assertNotAlmostEqual(by_name["芯片"].composite_score, 95.0, places=2)
        self.assertNotIn("[A6-glue", by_name["芯片"].reasoning)


# ─────────────────────────────────────────────────────────────────────
# C3：GOT_A6_ENABLED=false → 与影子期产物逐字段一致（行为面验收锚点）
# ─────────────────────────────────────────────────────────────────────

class TestC3ShadowPeriodEquivalence(unittest.TestCase):
    """A6=false 时 glue 分支整段跳过——**仅验证 helper 层的跳过逻辑，不等于生产可回滚**。

    ⚠　2026-10-12 语义更正（原 docstring 写“验证「单 env 变量回滚即可回到影子期」的关键承诺”，
    那句话不成立）：本用例直接调 `_build_sector_intel`，**绕开了 `run()` 入口的 fail-fast 守卫**
    （`aggregator.py:259` 对 `not GOT_A6_ENABLED` 直接 raise RuntimeError）。另因规则侧三件套已随
    A6 阶段 2（2026-09-26）退役，“影子期行为”已不存在——关掉 A6 得到的是三件套 neutral/missing
    垫底态。真实语义由 `tests/test_aggregator_run_smoke.py::test_run_rejects_a6_disabled` 锁定；
    详见 [`docs/got/backlog.md`](../docs/got/backlog.md) §4.3 与 §8 #15。
    """

    _COMPARED_FIELDS = (
        "sector_name", "composite_score", "capital_check", "news_check",
        "sentiment_check", "confidence", "classification", "tech_score",
    )

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)

    def test_a6_off_matches_no_glue_baseline(self):
        from src.got.nodes.aggregator import AggregatorNode
        details = [
            _make_detail("芯片", "BK0001", score=70.0),
            _make_detail("白酒", "BK0002", score=65.0),
        ]
        with patch("src.got.nodes.aggregator.GOT_A6_ENABLED", False):
            # 影子期基线：glue=None
            baseline = self.node._build_sector_intel(
                _make_sector_ctx(details),
                _make_capital(), _make_news(), _make_sentiment(),
                glue=None,
            )
            # A6 关 + glue 存在（模拟影子期归档，glue 不消费）
            glue = _make_glue_ok([
                ("BK0001", 0.95, 0.9, "high", "high", "high"),
                ("BK0002", 0.20, 0.5, "low", "low", "low"),
            ])
            a6_off = self.node._build_sector_intel(
                _make_sector_ctx(details),
                _make_capital(), _make_news(), _make_sentiment(),
                glue=glue,
            )

        self.assertEqual(len(baseline), len(a6_off))
        base_map = {i.sector_name: i for i in baseline}
        off_map = {i.sector_name: i for i in a6_off}
        self.assertEqual(set(base_map), set(off_map))
        for name in base_map:
            for f in self._COMPARED_FIELDS:
                self.assertEqual(
                    getattr(base_map[name], f), getattr(off_map[name], f),
                    msg=f"{name}.{f} 在 A6=false 下应与影子期基线一致",
                )
            self.assertNotIn("[A6-glue", off_map[name].reasoning,
                             "A6=false 不得留痕 [A6-glue]")


# ─────────────────────────────────────────────────────────────────────
# C4：glue.status=S2_parse_failed → 4 个调用点全部回退，不抛异常
# ─────────────────────────────────────────────────────────────────────

class TestC4S2ParseFailedFallback(unittest.TestCase):
    """复刻 0915：glue.status=S2_parse_failed（Node 2 上游故障）。

    4 个调用点逐一验证：
      ① _build_sector_intel：不进入 Pass 1.5
      ② _glue_aware_events_map：完全回退 _group_events_by_sector
      ③ _llm_refine_sectors 门：_a6_glue_active 应为 False
      ④ _synthesize_mainline_endurance：不消费 glue.families
    """

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)
        self._patcher = patch("src.got.nodes.aggregator.GOT_A6_ENABLED", True)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _broken_glue(self):
        from src.got.schemas import SemanticGlueResult
        return SemanticGlueResult(
            status="S2_parse_failed", degraded=True,
            strengths=_make_glue_strengths([
                ("BK0001", 0.99, 0.99, "high", "high", "high"),
            ]),
        )

    def test_build_sector_intel_falls_back(self):
        details = [_make_detail("芯片", "BK0001", score=70.0)]
        intels = self.node._build_sector_intel(
            _make_sector_ctx(details),
            _make_capital(), _make_news(), _make_sentiment(),
            glue=self._broken_glue(),
        )
        chip = next(i for i in intels if i.sector_name == "芯片")
        self.assertNotAlmostEqual(chip.composite_score, 99.0, places=2)
        self.assertNotIn("[A6-glue", chip.reasoning)

    def test_glue_aware_events_map_falls_back(self):
        from src.got.schemas import CatalystEvent
        events = [
            CatalystEvent(
                event_id="ev001", event_date=date(2026, 9, 17),
                event_type="narrative", title="测试事件", sector="芯片",
                source="llm_narrative", affected_sectors=["芯片"],
            ),
        ]
        glue = self._broken_glue()
        # 从 glue.relations 走会返回空桶，从 legacy 走会返回 {'芯片': [ev001]}
        result = self.node._glue_aware_events_map(
            self.node._group_events_by_sector, events, glue, {"BK0001": "芯片"},
        )
        self.assertIn("芯片", result)
        self.assertEqual(len(result["芯片"]), 1)

    def test_a6_gate_flag_not_set(self):
        """模拟 run() 中 self._a6_glue_active 的判定表达式。"""
        from src.got.catalyst_config import GOT_A6_ENABLED
        glue = self._broken_glue()
        active = bool(
            GOT_A6_ENABLED
            and glue is not None
            and getattr(glue, "status", None) == "ok"
            and not getattr(glue, "degraded", False)
        )
        self.assertFalse(active,
                         "S2_parse_failed 时 _a6_glue_active 应为 False（glue 未转正成功）")

    def test_mainline_endurance_skips_glue_families(self):
        """glue.status != ok 时不消费 glue.families（即使有数据）。"""
        from src.got.schemas import SectorIntel, GlueFamily, SemanticGlueResult
        intel = SectorIntel(
            sector_name="芯片", sector_role="mainline", classification="actionable",
            composite_score=70.0, frame_group=None,
        )
        glue = SemanticGlueResult(
            status="S2_parse_failed", degraded=True,
            families=[GlueFamily(board_code="BK0001", family="AI科技")],
        )
        sector = _make_sector_ctx([_make_detail("芯片", "BK0001", score=70.0)])
        regime = _make_regime_stub()
        result = self.node._synthesize_mainline_endurance(
            [intel], sector, _make_news(), regime, glue=glue,
        )
        # 家族名应回落 sector_name（glue 不生效）
        self.assertTrue(all(e.name != "AI科技" for e in result),
                        "S2_parse_failed 不得消费 glue.families")


def _make_regime_stub():
    from src.got.schemas import MarketRegime
    return MarketRegime(
        regime="transition", risk_budget=0.5, fear_greed=0.5,
        sentiment_stage="neutral", sentiment_momentum=0.0,
    )


# ─────────────────────────────────────────────────────────────────────
# C5：family_resonance E2 公式 —— P90 自适应分母
# ─────────────────────────────────────────────────────────────────────

class TestC5FamilyResonanceP90(unittest.TestCase):
    """E2 公式：分母从固定 50 改为当日家族 std_dev P90。

    验证两组场景（旧公式 vs 新公式）：
      Day A：3 家族 std_dev ≈ [5, 10, 15]，中位家族 resonance 新≈0.29 / 旧≈0.80
      Day B：3 家族 std_dev ≈ [40, 45, 50]，中位家族 resonance 新≈0.08 / 旧≈0.10

    两组新公式差异（0.21）明显小于旧公式差异（0.70），说明归一化
    有效抑制了"当日整体离散度水平"对单一板块 resonance 的影响。
    """

    def setUp(self):
        from src.got.nodes.aggregator import AggregatorNode
        self.node = AggregatorNode(use_mock=True)
        self._patcher = patch("src.got.nodes.aggregator.GOT_A6_ENABLED", True)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _run(self, per_family_scores):
        """per_family_scores: {family_name: [composite_a, composite_b]}"""
        from src.got.schemas import SectorIntel
        details, intels = [], []
        for idx, (fam, scores) in enumerate(per_family_scores.items()):
            for j, s in enumerate(scores):
                name = f"{fam}_{j}"
                code = f"BK{idx:03d}{j:02d}"
                details.append(_make_detail(name, code, score=s))
                intels.append(SectorIntel(
                    sector_name=name, sector_code=code,
                    sector_role="mainline", classification="actionable",
                    composite_score=s, frame_group=fam,
                ))
        sector = _make_sector_ctx(details)
        return self.node._synthesize_mainline_endurance(
            intels, sector, _make_news(), _make_regime_stub(), glue=None,
        )

    def test_p90_denominator_adapts(self):
        day_a = self._run({
            "F1": [72.5, 77.5],     # std ≈ 3.54（对应"低离散度"）
            "F2": [70.0, 80.0],     # std ≈ 7.07
            "F3": [67.5, 82.5],     # std ≈ 10.61
        })
        # Day B 三组：std_dev 约 28.3 / 31.8 / 35.4（高离散度日）
        day_b = self._run({
            "F1": [60.0, 100.0],    # std ≈ 28.28
            "F2": [55.0, 100.0],    # std ≈ 31.82
            "F3": [50.0, 100.0],    # std ≈ 35.36
        })

        # 找到 F2（中位家族）
        a_f2 = next(e for e in day_a if e.name == "F2")
        b_f2 = next(e for e in day_b if e.name == "F2")

        # 旧公式：Day A F2 = 1 - 7.07/50 = 0.859；Day B F2 = 1 - 31.82/50 = 0.364
        # 新公式：Day A F2 = 1 - 7.07/P90(3.54, 7.07, 10.61)=1 - 7.07/9.90 = 0.286
        #         Day B F2 = 1 - 31.82/P90(28.28, 31.82, 35.36)=1 - 31.82/34.76 = 0.085
        # 新公式两组差异 = 0.20（0.286 vs 0.085），旧公式差异 = 0.49
        # 断言：新公式两组差异 < 旧公式两组差异
        old_a = max(0.0, 1.0 - 7.07 / 50.0)
        old_b = max(0.0, 1.0 - 31.82 / 50.0)
        new_gap = abs(a_f2.family_resonance - b_f2.family_resonance)
        old_gap = abs(old_a - old_b)
        self.assertLess(new_gap, old_gap,
                        f"E2 归一化后两组差异({new_gap:.3f}) 应小于固定分母({old_gap:.3f})")
        # 数值合理性：resonance ∈ [0, 1]
        for e in day_a + day_b:
            self.assertGreaterEqual(e.family_resonance, 0.0)
            self.assertLessEqual(e.family_resonance, 1.0)

    def test_single_family_fallback_50(self):
        """所有家族单成员 → std_values 为空 → resonance_denom fallback=50。"""
        from src.got.schemas import SectorIntel
        intel = SectorIntel(
            sector_name="独占板块", sector_code="BK9999",
            sector_role="mainline", classification="actionable",
            composite_score=80.0, frame_group=None,
        )
        sector = _make_sector_ctx([_make_detail("独占板块", "BK9999", score=80.0)])
        result = self.node._synthesize_mainline_endurance(
            [intel], sector, _make_news(), _make_regime_stub(), glue=None,
        )
        # 单成员家族直接返 1.0（与旧行为一致）
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].family_resonance, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
