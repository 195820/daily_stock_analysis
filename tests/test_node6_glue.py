# -*- coding: utf-8 -*-
"""
Phase 1-A4 单元测试：_agent_semantic_glue 语义粘合层。

覆盖：
1. F1 降级态（S6 开关关闭 / replay_mode）
2. 事件去冗余（4 个冗余字段剥离）
3. prompt 组装（候选板块 + 事件 + 资金流 + 情绪热榜）
4. 输出解析 + 校验（S3 覆盖不全 / S4 家族名越界 / S5 强度越界）
5. 受控词表（CONTROLLED_FAMILIES 12 + 1）
6. 0903 回放：开关关闭时输出与 A3 基线一致
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.schemas import (
    CapitalContext,
    CapitalEvidence,
    CatalystCalendar,
    CatalystEvent,
    CONTROLLED_FAMILIES,
    GlueFamily,
    GlueRelation,
    GlueStrength,
    HotStock,
    MainlineGroup,
    MarketRegime,
    NewsContext,
    SectorContext,
    SectorDetail,
    SemanticGlueResult,
    SentimentContext,
)
from src.got.nodes.aggregator import AggregatorNode
from datetime import date


def _make_detail(name, code=None, score=50.0, state="active_mainline"):
    return SectorDetail(
        code=code or f"CODE_{name}", name=name,
        state=state, strength_score=score,
    )


def _make_candidates():
    """构造 3 个候选板块用于测试。"""
    d1 = _make_detail("旅游", "TD_旅游", 88.0)
    d2 = _make_detail("半导体", "TD_半导体", 75.0)
    d3 = _make_detail("量子计算", "TD_量子计算", 42.0, state="ambush")
    return [
        ("旅游", d1, {"role": "mainline", "tier": "A", "quality": "strong", "mainline_days": 5}),
        ("半导体", d2, {"role": "mainline", "tier": "A", "quality": "strong", "mainline_days": 3}),
        ("量子计算", d3, {"role": "ambush", "tier": None, "quality": None, "mainline_days": None}),
    ]


def _make_news(events=None):
    cal = CatalystCalendar(
        target_date=date(2026, 9, 3),
        events=events or [],
    )
    return NewsContext(
        overall_impact="neutral", impact_score=0.0,
        catalyst_calendar=cal,
    )


def _make_capital():
    return CapitalContext(
        status="normal", institutional_attitude="neutral",
        institutional_score=0.0, confidence_multiplier=1.0,
        evidence=CapitalEvidence(
            sector_flow={"sector_flow_all_3d": {"旅游概念": 1.5, "半导体": -0.3}},
        ),
    )


def _make_sentiment():
    return SentimentContext(
        overall_sentiment="neutral", sentiment_score=0.0,
        sentiment_stage="neutral", trading_signal="neutral",
        signal_confidence=0.5,
        hot_stocks=[
            HotStock(stock_code="000001", stock_name="平安银行", mention_count=100, sentiment="positive"),
        ],
    )


def _make_regime():
    return MarketRegime(
        regime="bull", risk_budget=0.6, fear_greed=0.5,
        dwell_days=1, regime_score=0.5,
    )


TEST_RUN_DATE = date(2026, 9, 14)


def _redirect_glue_archive(testcase, node, run_date=TEST_RUN_DATE):
    """把 glue 落盘根目录重定向到临时目录，并把运行日固定为 run_date。

    凡真实调用 `_agent_semantic_glue` 的测试都必须先调这一层：`_save_glue_result`
    是真实副作用，不重定向就会写进 `data/got_daily/{date.today()}/`，污染影子期归档
    —— 0911 / 0912 的 `agent_glue.json`（含 `_replay/` 那份）正是这样产生的，
    见 `docs/got/verification.md` §2.2 / §4.1。

    （不用 `TestCase.enterContext`：它需要 Python 3.11+，本仓库实测运行在 3.10。）
    """
    import src.got.nodes.aggregator as agg_mod

    original_root = agg_mod.GLUE_ARCHIVE_ROOT
    tmp_root = Path(tempfile.mkdtemp(prefix="glue_archive_"))
    agg_mod.GLUE_ARCHIVE_ROOT = tmp_root
    node._run_date = run_date

    def _restore():
        agg_mod.GLUE_ARCHIVE_ROOT = original_root
        if hasattr(node, "_run_date"):
            del node._run_date

    testcase.addCleanup(shutil.rmtree, tmp_root, True)
    testcase.addCleanup(_restore)
    return tmp_root


class TestControlledFamilies(unittest.TestCase):
    """测试受控词表定义。"""

    def test_twelve_plus_one(self):
        """14 个真实家族 + '无归属' = 15 个合法值（B2 修复：华为生态/前沿科技加入）。"""
        self.assertEqual(len(CONTROLLED_FAMILIES), 14)
        self.assertIn("AI软件", CONTROLLED_FAMILIES)
        self.assertIn("金融", CONTROLLED_FAMILIES)
        self.assertIn("消费", CONTROLLED_FAMILIES)


class TestGlueF1Degraded(unittest.TestCase):
    """测试 F1 降级态。"""

    def setUp(self):
        self.node = AggregatorNode()
        self.candidates = _make_candidates()

    def test_f1_all_unknown(self):
        """F1 降级：capital_read/news_read/sentiment_read 全 unknown。"""
        result = self.node._glue_f1_degraded(self.candidates, reason="测试")
        self.assertTrue(result.degraded)
        self.assertEqual(len(result.degraded_sectors), 3)
        for s in result.strengths:
            self.assertEqual(s.capital_read, "unknown")
            self.assertEqual(s.news_read, "unknown")
            self.assertEqual(s.sentiment_read, "unknown")

    def test_f1_strength_equals_tech(self):
        """F1-1: composite_score = tech_score（Node 2 尺子，裸技术分）。"""
        result = self.node._glue_f1_degraded(self.candidates)
        # 旅游 tech=88 → strength=0.88
        tourism = [s for s in result.strengths if s.board_code == "TD_旅游"][0]
        self.assertAlmostEqual(tourism.strength, 0.88, places=2)
        # 量子计算 tech=42 → strength=0.42
        quantum = [s for s in result.strengths if s.board_code == "TD_量子计算"][0]
        self.assertAlmostEqual(quantum.strength, 0.42, places=2)

    def test_f1_confidence_low(self):
        """F1 降级 confidence=0.3（低置信度标记）。"""
        result = self.node._glue_f1_degraded(self.candidates)
        for s in result.strengths:
            self.assertAlmostEqual(s.confidence, 0.3)

    def test_f1_reason_in_warning(self):
        """F1 reason 出现在 warnings 中。"""
        result = self.node._glue_f1_degraded(self.candidates, reason="测试原因")
        self.assertTrue(any("测试原因" in w for w in result.warnings))


class TestGlueStripEvent(unittest.TestCase):
    """测试事件去冗余（§5.2）。"""

    def test_four_redundant_fields_removed(self):
        """4 个冗余字段被剥离。"""
        ev = CatalystEvent(
            event_id="test_001",
            event_date=date(2026, 9, 3),
            event_type="global",
            title="SK海力士HBM指引",
            sector="半导体",
            source="global_map",
            consensus_expectation="全球事件传导: SK 海力士/三星财报及 HBM 指引",
            category_tag="drift",
            is_global=True,
            created_at=None,
        )
        stripped = AggregatorNode._glue_strip_event(ev)
        # 保留 11 个字段
        self.assertIn("event_id", stripped)
        self.assertIn("title", stripped)
        self.assertIn("direction", stripped)
        self.assertIn("affected_sectors", stripped)
        # 剥离 4 个冗余字段
        self.assertNotIn("consensus_expectation", stripped)
        self.assertNotIn("category_tag", stripped)
        self.assertNotIn("is_global", stripped)
        self.assertNotIn("created_at", stripped)


class TestGluePrompt(unittest.TestCase):
    """测试 prompt 组装。"""

    def test_prompt_contains_candidates(self):
        """prompt 包含候选板块代码。"""
        candidates = _make_candidates()
        prompt = AggregatorNode._build_glue_prompt(
            candidates, _make_news(), _make_capital(), _make_sentiment(),
        )
        self.assertIn("TD_旅游", prompt)
        self.assertIn("TD_半导体", prompt)
        self.assertIn("TD_量子计算", prompt)

    def test_prompt_contains_controlled_families(self):
        """prompt 包含受控词表。"""
        prompt = AggregatorNode._build_glue_prompt(
            _make_candidates(), _make_news(), _make_capital(), _make_sentiment(),
        )
        for family in ["AI软件", "金融", "消费"]:
            self.assertIn(family, prompt)

    def test_prompt_json_format_instruction(self):
        """prompt 要求严格 JSON 输出。"""
        prompt = AggregatorNode._build_glue_prompt(
            _make_candidates(), _make_news(), _make_capital(), _make_sentiment(),
        )
        self.assertIn("严格 JSON", prompt)
        self.assertIn("relations", prompt)
        self.assertIn("strengths", prompt)
        self.assertIn("families", prompt)


class TestValidateGlue(unittest.TestCase):
    """测试输出解析 + 校验（S3-S5）。"""

    def setUp(self):
        self.node = AggregatorNode()
        self.candidates = _make_candidates()

    def test_valid_full_response(self):
        """合法全量响应正确解析。"""
        parsed = {
            "relations": [
                {"event_id": "ev1", "board_code": "TD_旅游", "strength": 0.8,
                 "direction": "positive", "propagation_path": ["景区"], "confidence": 0.9},
            ],
            "strengths": [
                {"board_code": "TD_旅游", "strength": 0.7, "confidence": 0.8,
                 "evidence": "主线+催化", "capital_read": "high", "news_read": "medium", "sentiment_read": "low"},
                {"board_code": "TD_半导体", "strength": 0.5, "confidence": 0.6,
                 "evidence": "资金分歧", "capital_read": "unknown", "news_read": "unknown", "sentiment_read": "unknown"},
                {"board_code": "TD_量子计算", "strength": 0.3, "confidence": 0.4,
                 "evidence": "观望", "capital_read": "unknown", "news_read": "unknown", "sentiment_read": "unknown"},
            ],
            "families": [
                {"board_code": "TD_量子计算", "family": "AI软件"},
            ],
        }
        result = self.node._validate_glue(parsed, self.candidates)
        self.assertFalse(result.degraded)
        self.assertEqual(len(result.relations), 1)
        self.assertEqual(len(result.strengths), 3)
        self.assertEqual(len(result.families), 1)
        self.assertEqual(result.families[0].family, "AI软件")

    def test_s3_missing_sectors(self):
        """S3: Agent 未覆盖的板块 → 逐板块 F1 降级。"""
        parsed = {
            "relations": [],
            "strengths": [
                {"board_code": "TD_旅游", "strength": 0.7, "confidence": 0.8,
                 "evidence": "", "capital_read": "high", "news_read": "high", "sentiment_read": "high"},
                # 半导体 和 量子计算 缺失
            ],
            "families": [],
        }
        result = self.node._validate_glue(parsed, self.candidates)
        self.assertTrue(result.degraded)
        self.assertEqual(len(result.degraded_sectors), 2)
        # 缺失板块应被补充 F1 降级条目
        self.assertEqual(len(result.strengths), 3)  # 1 Agent + 2 F1
        self.assertTrue(any("S3" in w for w in result.warnings))

    def test_s4_family_out_of_vocabulary(self):
        """S4: 家族名不在受控词表 → 丢弃，走兜底链。"""
        parsed = {
            "relations": [],
            "strengths": [
                {"board_code": "TD_旅游", "strength": 0.5, "confidence": 0.5,
                 "evidence": "", "capital_read": "unknown", "news_read": "unknown", "sentiment_read": "unknown"},
                {"board_code": "TD_半导体", "strength": 0.5, "confidence": 0.5,
                 "evidence": "", "capital_read": "unknown", "news_read": "unknown", "sentiment_read": "unknown"},
                {"board_code": "TD_量子计算", "strength": 0.5, "confidence": 0.5,
                 "evidence": "", "capital_read": "unknown", "news_read": "unknown", "sentiment_read": "unknown"},
            ],
            "families": [
                {"board_code": "TD_量子计算", "family": "量子科技"},  # 不在受控词表
            ],
        }
        result = self.node._validate_glue(parsed, self.candidates)
        # 越界家族被丢弃
        self.assertEqual(len(result.families), 0)
        self.assertTrue(any("S4" in w for w in result.warnings))

    def test_s5_strength_clipped(self):
        """S5: strength 越界 → clip 后使用。"""
        parsed = {
            "relations": [],
            "strengths": [
                {"board_code": "TD_旅游", "strength": 1.5, "confidence": 0.8,
                 "evidence": "", "capital_read": "unknown", "news_read": "unknown", "sentiment_read": "unknown"},
                {"board_code": "TD_半导体", "strength": -0.2, "confidence": 0.5,
                 "evidence": "", "capital_read": "unknown", "news_read": "unknown", "sentiment_read": "unknown"},
                {"board_code": "TD_量子计算", "strength": 0.5, "confidence": 0.5,
                 "evidence": "", "capital_read": "unknown", "news_read": "unknown", "sentiment_read": "unknown"},
            ],
            "families": [],
        }
        result = self.node._validate_glue(parsed, self.candidates)
        tourism = [s for s in result.strengths if s.board_code == "TD_旅游"][0]
        self.assertAlmostEqual(tourism.strength, 1.0)
        semi = [s for s in result.strengths if s.board_code == "TD_半导体"][0]
        self.assertAlmostEqual(semi.strength, 0.0)
        self.assertTrue(any("S5" in w for w in result.warnings))


class TestAgentSemanticGlueDispatch(unittest.TestCase):
    """测试 _agent_semantic_glue 的调度逻辑。"""

    def setUp(self):
        self.node = AggregatorNode()
        self.candidates = _make_candidates()
        self.news = _make_news()
        self.capital = _make_capital()
        self.sentiment = _make_sentiment()
        self.regime = _make_regime()
        # 这几个用例会真实落盘 → 必须隔离归档根目录
        _redirect_glue_archive(self, self.node)

    def test_s6_switch_off(self):
        """S6: 开关关闭 → 报错（glue 已转正）。"""
        import asyncio
        with patch("src.got.nodes.aggregator.AGENT_SEMANTIC_GLUE_ENABLED", False):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(
                    self.node._agent_semantic_glue(
                        self.candidates, self.news, self.capital, self.sentiment,
                    )
                )
            self.assertIn("AGENT_SEMANTIC_GLUE_ENABLED=false", str(ctx.exception))

    def test_replay_mode_skips_llm(self):
        """replay_mode → 跳过 LLM 调用（需先开启开关以到达 replay 检查）。"""
        import asyncio
        import src.got.nodes.aggregator as agg_mod
        orig = agg_mod.AGENT_SEMANTIC_GLUE_ENABLED
        agg_mod.AGENT_SEMANTIC_GLUE_ENABLED = True
        self.node._replay_mode = True
        try:
            result = asyncio.run(
                self.node._agent_semantic_glue(
                    self.candidates, self.news, self.capital, self.sentiment,
                )
            )
            self.assertTrue(result.degraded)
            self.assertTrue(any("replay_mode" in w for w in result.warnings))
        finally:
            agg_mod.AGENT_SEMANTIC_GLUE_ENABLED = orig
            del self.node._replay_mode

    def test_empty_candidates(self):
        """空候选集 → 直接返回降级。"""
        import asyncio
        # 临时启用开关以测试空候选分支
        import src.got.nodes.aggregator as agg_mod
        orig = agg_mod.AGENT_SEMANTIC_GLUE_ENABLED
        agg_mod.AGENT_SEMANTIC_GLUE_ENABLED = True
        try:
            result = asyncio.run(
                self.node._agent_semantic_glue(
                    [], self.news, self.capital, self.sentiment,
                )
            )
            self.assertTrue(result.degraded)
        finally:
            agg_mod.AGENT_SEMANTIC_GLUE_ENABLED = orig


class TestGlueReplay0903(unittest.TestCase):
    """0903 回放：开关关闭时输出与 A3 基线一致。"""

    def test_0903_replay_unchanged(self):
        """0903 回放：glue 降级后不影响 confirmed_sectors。"""
        from src.got.archive import load_daily_contexts
        contexts = load_daily_contexts(date(2026, 9, 3))
        if contexts is None:
            self.skipTest("0903 归档数据不可用")

        sector = contexts.get("sector")
        news = contexts.get("news")
        capital = contexts.get("capital")
        sentiment = contexts.get("sentiment")
        unified = contexts.get("unified")
        if not all([sector, news, capital, sentiment]):
            self.skipTest("context 不完整")

        ambush_pool = getattr(unified, "ambush_pool", []) if unified else []

        node = AggregatorNode()
        node.use_mock = False
        node._replay_mode = True

        candidates, _ = node._collect_sector_candidates(sector, ambush_pool)

        import asyncio
        _redirect_glue_archive(self, node)
        glue = asyncio.run(
            node._agent_semantic_glue(candidates, news, capital, sentiment)
        )

        # glue 降级（replay_mode）
        self.assertTrue(glue.degraded)
        # 候选数量不变
        self.assertEqual(len(candidates), 24)


class TestGlueFailureSamplePersistence(unittest.TestCase):
    """R-C1：S1 / S2 失败样本必须落盘，且 `status` 取值正确。

    红线原文（`docs/got/archive/got_layer_c_plan_0911.md` §6.1）：
    「制造 S1（断网/空响应）与 S2（畸形 JSON）各一次，`agent_glue.json`
      **仍存在**且 `status` 取值正确」—— 判据落在**落盘副作用**上。

    这正是 `TestAgentSemanticGlueDispatch` 覆盖不到的那一层：那一组断言的是返回值
    （`degraded` / `warnings`），把 `_save_glue_result(...)` 删掉它照样全绿。
    """

    def setUp(self):
        self.node = AggregatorNode()
        self.candidates = _make_candidates()
        self.news = _make_news()
        self.capital = _make_capital()
        self.sentiment = _make_sentiment()

    def _run_and_read_archive(self, llm_stub):
        """在隔离归档目录下跑一次调度，返回 (result, 落盘 JSON)。"""
        import asyncio
        import src.got.nodes.aggregator as agg_mod
        original_enabled = agg_mod.AGENT_SEMANTIC_GLUE_ENABLED
        agg_mod.AGENT_SEMANTIC_GLUE_ENABLED = True  # 越过 S6，才能到达 S1/S2 分支
        try:
            tmp_root = _redirect_glue_archive(self, self.node)
            with patch.object(self.node, "_call_llm_async", llm_stub):
                result = asyncio.run(self.node._agent_semantic_glue(
                    self.candidates, self.news, self.capital, self.sentiment,
                ))
            path = tmp_root / TEST_RUN_DATE.strftime("%Y%m%d") / "agent_glue.json"
            self.assertTrue(path.exists(), f"失败样本未落盘: {path}")
            return result, json.loads(path.read_text(encoding="utf-8"))
        finally:
            agg_mod.AGENT_SEMANTIC_GLUE_ENABLED = original_enabled

    def _assert_failure_sample_landed(self, llm_stub, expected_status):
        result, archived = self._run_and_read_archive(llm_stub)
        self.assertEqual(result.status, expected_status)
        self.assertEqual(archived["status"], expected_status)
        self.assertTrue(result.degraded)
        # C-P1：降级态须保留 raw_response 供离线复盘
        self.assertIn("raw_response", archived)

    def test_s1_call_failed_lands(self):
        """S1 断网：LLM 调用抛异常 → `S1_call_failed` 仍落盘。"""
        self._assert_failure_sample_landed(
            AsyncMock(side_effect=ConnectionError("模拟断网")), "S1_call_failed",
        )

    def test_s1_empty_response_lands(self):
        """S1 空响应：LLM 返回空串 → `S1_empty` 仍落盘。"""
        self._assert_failure_sample_landed(AsyncMock(return_value=""), "S1_empty")

    def test_s2_parse_failed_lands(self):
        """S2 畸形响应：无 JSON 对象 → `S2_parse_failed` 仍落盘。"""
        self._assert_failure_sample_landed(
            AsyncMock(return_value="模型没按格式返回，没有 JSON 对象"), "S2_parse_failed",
        )

    def test_s2_validate_failed_lands(self):
        """S2 校验失败：结构畸形击穿校验 → `S2_validate_failed` 仍落盘。"""
        self._assert_failure_sample_landed(
            AsyncMock(return_value='{"relations": 123}'), "S2_validate_failed",
        )


if __name__ == "__main__":
    unittest.main()
