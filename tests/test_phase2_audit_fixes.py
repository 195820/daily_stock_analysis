# -*- coding: utf-8 -*-
"""Phase 2 审核修复计划（方案 A）单元测试。

覆盖计划「测试计划」节的直修项：
- 1.1 archive_phase2 原子写：回读自检失败 → .corrupt 保留 + 好文件不被覆盖；NaN 清洗
- 1.2 partial 落盘：Step2.5/Step3/LLM 精炼失败 → status="partial"、unified_scoring 保留
- 2.1 轮次 2 Tier A 主线保底：补回 1 只且打标；上限 5 只；已有代表不补
- 2.2 ambush 5 日 DDE 口径：分组百分位 + 降级票回退打标；未打标缺值 fail-fast（ValueError）
- 2.3 _score_capital：并列 tie 一致、孤样本 0 分、排序键无数据后置
- 3.3 仲裁降级：见 test_verify_candidate_sectors.py（保守分支断言已更新）
- 回放（轻量）：0924 归档上复算未命中板块 + 保底规则让红黄酒至少 1 只进轮次 3
"""
from __future__ import annotations

import asyncio
import json
import math
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.got.schemas import (
    DataQuality,
    PortfolioDecision,
    PortfolioRiskAssessment,
    UnifiedScoringResult,
)
from src.services.stock_screener import StockScreenerPipeline

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────── 公共构造 ───────────────────────────

def _minimal_decision(**overrides) -> PortfolioDecision:
    kwargs = dict(
        status="normal",
        risk_assessment=PortfolioRiskAssessment(
            position_limit=0.5, current_position_ratio=0.2,
            constraint_reasoning="测试约束",
        ),
        unified_scoring=[UnifiedScoringResult(
            symbol="600030", name="中信证券", is_holding=False,
            composite_score=72.0,
            dimension_scores={"capital": 30.0, "news": 10.0, "sector": 12.0,
                              "tech": 10.0, "risk": 10.0},
            action_recommendation="hold", confidence=0.6, reasoning="测试",
        )],
        allocation=[],
        summary="测试总结",
        warnings=[],
        reasoning="测试推理",
        data_quality=DataQuality(),
    )
    kwargs.update(overrides)
    return PortfolioDecision(**kwargs)


def _screener() -> StockScreenerPipeline:
    """绕过 __init__（避免 DatabaseManager 依赖）；被测方法均不触碰实例状态"""
    return StockScreenerPipeline.__new__(StockScreenerPipeline)


def _capital_ctx(flow_all: dict) -> dict:
    # _parse_sector_flow 有 len>10 的 sector_flow_all 有效性门槛，补足填充板块
    padded = {f"填充板块{i}": float(i) for i in range(12)}
    padded.update(flow_all)
    return {"evidence": {"sector_flow": {"sector_flow_all": padded}}}


# ─────────────────────────── 1.1 原子写盘 ───────────────────────────

class TestArchivePhase2Atomic(unittest.TestCase):

    def setUp(self):
        self._tmp = __import__("tempfile").TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.run_date = date(2026, 9, 24)
        self.day_dir = self.base / "20260924"
        self.out_file = self.day_dir / "portfolio_decision.json"
        self._patcher = patch("src.got.archive._default_base_dir", return_value=self.base)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def test_atomic_write_success_no_tmp_left(self):
        from src.got.archive import archive_phase2
        archive_phase2(_minimal_decision(), run_date=self.run_date)
        self.assertTrue(self.out_file.exists())
        data = json.loads(self.out_file.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "normal")
        self.assertFalse(list(self.day_dir.glob("*.tmp")))
        self.assertFalse(list(self.day_dir.glob("*.corrupt")))

    def test_readback_failure_keeps_good_file_and_writes_corrupt(self):
        """回读自检失败 → 坏文件改名 .corrupt 保留，已有好文件不被覆盖，异常上抛"""
        from src.got import archive as archive_mod
        good_text = '{"status": "ok_sentinel"}'
        self.day_dir.mkdir(parents=True, exist_ok=True)
        self.out_file.write_text(good_text, encoding="utf-8")

        real_loads = json.loads

        def fake_loads(s, *a, **kw):
            if "ok_sentinel" in str(s):
                return real_loads(s, *a, **kw)
            raise json.JSONDecodeError("模拟半截 JSON", str(s), 0)

        with patch.object(archive_mod.json, "loads", side_effect=fake_loads):
            with self.assertRaises(json.JSONDecodeError):
                archive_mod.archive_phase2(_minimal_decision(), run_date=self.run_date)

        self.assertEqual(self.out_file.read_text(encoding="utf-8"), good_text)
        corrupt = self.day_dir / "portfolio_decision.json.corrupt"
        self.assertTrue(corrupt.exists())
        self.assertFalse(list(self.day_dir.glob("*.tmp")))

    def test_nan_sanitized_to_null(self):
        """allow_nan=False 拒写 NaN → 清洗为 null 落盘（已知坑：不炸整个归档）"""
        from src.got.archive import archive_phase2
        decision = _minimal_decision()
        decision.unified_scoring[0].composite_score = float("nan")
        archive_phase2(decision, run_date=self.run_date)
        data = json.loads(self.out_file.read_text(encoding="utf-8"))
        self.assertIsNone(data["unified_scoring"][0]["composite_score"])


# ─────────────────────────── 1.2 partial 降级落盘 ───────────────────────────

class TestPartialDegradation(unittest.IsolatedAsyncioTestCase):

    async def test_step_failures_produce_partial_decision(self):
        """Step2.5/Step3/LLM 精炼全失败 → 正常 return，status=partial、
        allocation 空、unified_scoring 保留、warnings 透出降级步骤"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holding = _minimal_decision().unified_scoring[0]

        ctx = SimpleNamespace(
            final_position_limit=0.5, market_regime=None,
            confirmed_sectors=[], ambush_pool=[], mainline_endurance=[],
        )
        sentiment = SimpleNamespace(sentiment_momentum=0.0)
        empty_ctx = SimpleNamespace()

        risk = PortfolioRiskAssessment(
            position_limit=0.5, current_position_ratio=0.0,
            constraint_reasoning="测试",
        )

        with patch.object(node, "_get_portfolio_snapshot", return_value={}), \
             patch.object(node, "_assess_risk", return_value=risk), \
             patch.object(node, "_score_holdings", return_value=[holding]), \
             patch.object(node, "_score_candidates", AsyncMock(return_value=[])), \
             patch.object(node, "_agent_holding_review",
                          AsyncMock(side_effect=RuntimeError("review boom"))), \
             patch.object(node, "_allocate", side_effect=RuntimeError("alloc boom")), \
             patch.object(node, "_detect_rotation_opportunity", return_value=[]), \
             patch.object(node, "_check_family_crowding", return_value=[]), \
             patch.object(node, "_write_thesis_snapshots", return_value=([], [])), \
             patch.object(node, "_write_ambush_buy_snapshots", return_value=([], [])), \
             patch.object(node, "_close_thesis_snapshots"), \
             patch.object(node, "_close_ambush_snapshots"), \
             patch.object(node, "_llm_refinement",
                          AsyncMock(side_effect=RuntimeError("llm boom"))), \
             patch.object(node, "_generate_summary", return_value="降级总结"), \
             patch.object(node, "_generate_warnings", return_value=[]), \
             patch.object(node, "_save_shadow_records"):
            decision = await node.run(
                unified_context=ctx, market_context=empty_ctx,
                sector_context=empty_ctx, capital_context=empty_ctx,
                news_context=empty_ctx, sentiment_context=sentiment,
            )

        self.assertEqual(decision.status, "partial")
        self.assertEqual(decision.allocation, [])
        self.assertEqual(len(decision.unified_scoring), 1)
        self.assertEqual(decision.unified_scoring[0].symbol, "600030")
        joined = "\n".join(decision.warnings)
        self.assertIn("Step2.5", joined)
        self.assertIn("Step3", joined)
        self.assertIn("LLM 精炼", joined)
        # 产物可 JSON 序列化（archive 必然可执行）
        json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)


# ─────────────────────────── 2.1 轮次 2 Tier A 保底 ───────────────────────────

class TestCapitalFloor(unittest.TestCase):

    @staticmethod
    def _cand(code, sector, sector_type="active_mainline", sector_score=95.0,
              capital_score=0.0, capital_available=False, dde_net_pct=-1.0):
        return {
            "code": code, "sector": sector, "sector_type": sector_type,
            "sector_score": sector_score, "capital_score": capital_score,
            "capital_available": capital_available, "dde_net_pct": dde_net_pct,
        }

    def test_tier_a_wiped_sector_restores_one_flagged(self):
        """红黄酒场景：Tier A 主线全负 DDE 被 Top20 歼灭 → 补回 1 只且打标"""
        top = [self._cand(f"6000{i:02d}", "半导体", capital_score=50 - i,
                          capital_available=True, dde_net_pct=1.0)
               for i in range(20)]
        dropped = [self._cand("600089", "红黄酒", capital_score=0.0),
                   self._cand("600090", "红黄酒", capital_score=0.0)]
        result = StockScreenerPipeline._apply_capital_floor(top, dropped, top + dropped)
        self.assertEqual(len(result), 21)
        restored = [c for c in result if c.get("capital_floor_restored")]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["code"], "600089")
        self.assertEqual(restored[0]["sector"], "红黄酒")

    def test_floor_cap_at_five(self):
        """6 个被歼灭的 Tier A 板块 → 只补 5 只；强度高的优先"""
        top = [self._cand(f"6000{i:02d}", "半导体", capital_score=50 - i,
                          capital_available=True, dde_net_pct=1.0)
               for i in range(20)]
        dropped, all_c = [], list(top)
        for s in range(6):
            c = self._cand(f"6001{s:02d}", f"板块{s}", sector_score=91.0 + s,
                           capital_score=0.0)
            dropped.append(c)
            all_c.append(c)
        result = StockScreenerPipeline._apply_capital_floor(top, dropped, all_c)
        restored = [c for c in result if c.get("capital_floor_restored")]
        self.assertEqual(len(restored), 5)
        self.assertNotIn("板块0", {c["sector"] for c in restored})  # 强度最低被挤出

    def test_sector_already_in_top_not_restored(self):
        top = [self._cand("600000", "红黄酒", capital_score=10.0,
                          capital_available=True, dde_net_pct=0.5)]
        dropped = [self._cand("600001", "红黄酒")]
        result = StockScreenerPipeline._apply_capital_floor(top, dropped, top + dropped)
        self.assertEqual(len(result), 1)
        self.assertFalse(any(c.get("capital_floor_restored") for c in result))

    def test_below_strength_threshold_or_ambush_not_eligible(self):
        top = [self._cand(f"6000{i:02d}", "半导体", capital_score=50 - i,
                          capital_available=True, dde_net_pct=1.0)
               for i in range(20)]
        dropped = [
            self._cand("600100", "弱主线", sector_score=89.9),
            self._cand("600101", "埋伏板块", sector_type="ambush", sector_score=99.0),
        ]
        result = StockScreenerPipeline._apply_capital_floor(top, dropped, top + dropped)
        self.assertEqual(len(result), 20)

    def test_round2_sort_key_no_data_last(self):
        """2.3 排序键：(capital_available, capital_score, dde_net_pct) 降序，无数据后置"""
        cands = [
            {"code": "A", "capital_available": False, "capital_score": 0.0, "dde_net_pct": 9.9},
            {"code": "B", "capital_available": True, "capital_score": 10.0, "dde_net_pct": -5.0},
            {"code": "C", "capital_available": True, "capital_score": 10.0, "dde_net_pct": 1.0},
            {"code": "D", "capital_available": True, "capital_score": 0.0, "dde_net_pct": None},
        ]
        cands.sort(
            key=lambda x: (x["capital_available"], x["capital_score"],
                           x.get("dde_net_pct") or 0),
            reverse=True,
        )
        self.assertEqual([c["code"] for c in cands], ["C", "B", "D", "A"])


# ─────────────────────────── 2.2/2.3 _score_capital ───────────────────────────

class TestScoreCapital(unittest.TestCase):

    def setUp(self):
        self.s = _screener()

    def test_tie_gets_equal_best_rank(self):
        """并列值 tie 口径统一：严格大于计数，并列取最优名次"""
        cands = [
            {"code": "A", "sector": "", "dde_net_pct": 5.0},
            {"code": "B", "sector": "", "dde_net_pct": 5.0},
            {"code": "C", "sector": "", "dde_net_pct": -5.0},
        ]
        scores, avail = self.s._score_capital(cands, {})
        self.assertEqual(scores["A"], scores["B"])
        self.assertEqual(scores["A"], 50.0)  # rank=0 → 百分位 1.0 → 满分 50
        self.assertEqual(scores["C"], 0.0)   # rank=2 → 百分位 0.0
        self.assertTrue(all(avail.values()))

    def test_single_sample_scores_zero_and_unavailable(self):
        """孤样本退化：可排序样本 <2 → 该维度 0 分且不计入 capital_available"""
        cands = [{"code": "A", "sector": "", "dde_net_pct": 5.0}]
        scores, avail = self.s._score_capital(cands, {})
        self.assertEqual(scores["A"], 0.0)
        self.assertFalse(avail["A"])

    def test_ambush_uses_5d_sum_grouped(self):
        """ambush 候选用 dde_5d_sum 在组内排名，不与主线单日混排"""
        cands = [
            {"code": "M1", "sector": "", "sector_type": "active_mainline", "dde_net_pct": 3.0},
            {"code": "M2", "sector": "", "sector_type": "active_mainline", "dde_net_pct": 1.0},
            {"code": "A1", "sector": "", "sector_type": "ambush",
             "dde_net_pct": -9.0, "dde_5d_sum": 8.0},
            {"code": "A2", "sector": "", "sector_type": "ambush",
             "dde_net_pct": -8.0, "dde_5d_sum": 2.0},
        ]
        scores, _ = self.s._score_capital(cands, {})
        self.assertEqual(scores["A1"], 50.0)  # 5日口径组内第一，负单日值不参与
        self.assertEqual(scores["A2"], 0.0)
        self.assertFalse(cands[2].get("dde_degraded"))

    def test_ambush_5d_missing_unflagged_raises(self):
        """未打标的 ambush 缺 5 日值 = 解析契约破坏 → fail-fast，拒绝静默回退"""
        cands = [
            {"code": "A1", "sector": "", "sector_type": "ambush", "dde_net_pct": 2.0},
        ]
        with self.assertRaises(ValueError):
            self.s._score_capital(cands, {})

    def test_ambush_5d_missing_pre_flagged_falls_back_with_warning(self):
        """初筛降级票（已打 dde_degraded）→ 回退单日组并聚合告警"""
        cands = [
            {"code": "A1", "sector": "", "sector_type": "ambush",
             "dde_net_pct": 2.0, "dde_degraded": True},
            {"code": "M1", "sector": "", "sector_type": "active_mainline", "dde_net_pct": 1.0},
        ]
        with self.assertLogs("src.services.stock_screener", level="WARNING") as cm:
            scores, _ = self.s._score_capital(cands, {})
        self.assertEqual(scores["A1"], 50.0)  # 回退后进入主线组排名
        self.assertTrue(any("5 日口径缺失" in m and "A1" in m for m in cm.output))

    def test_sector_flow_normalization_fallback(self):
        """3.1：精确键未命中 → variants/归一化命中（'白酒概念'→'白酒'），不再静默 0 分"""
        flow = {"白酒": -1.0, "半导体": -2.0}
        cands = [
            {"code": "A", "sector": "白酒概念", "dde_net_pct": 0},
            {"code": "B", "sector": "白酒概念", "dde_net_pct": 0},
        ]
        scores, avail = self.s._score_capital(cands, _capital_ctx(flow))
        self.assertGreater(scores["A"], 0.0)
        self.assertTrue(avail["A"])

    def test_unmatched_sector_keeps_zero_with_warning(self):
        """红黄酒无归一化映射 → 板块维度 0 分，且聚合 warning 消除静默丢分"""
        flow = {"白酒": -1.0, "半导体": -2.0}
        cands = [
            {"code": "A", "sector": "红黄酒", "dde_net_pct": 0},
            {"code": "B", "sector": "红黄酒", "dde_net_pct": 0},
        ]
        with self.assertLogs("src.services.stock_screener", level="WARNING") as cm:
            scores, avail = self.s._score_capital(cands, _capital_ctx(flow))
        self.assertEqual(scores["A"], 0.0)
        self.assertFalse(avail["A"])
        self.assertTrue(any("无资金流数据" in m and "红黄酒" in m for m in cm.output))


# ─────────────────────────── 回放（轻量，0924 归档） ───────────────────────────

DAY_0924 = PROJECT_ROOT / "data" / "got_daily" / "20260924"


@unittest.skipUnless((DAY_0924 / "capital.json").exists(), "0924 归档不存在")
class TestReplay0924(unittest.TestCase):
    """计划「回放验证」节的轻量替身：不起完整流水线（依赖问财实时接口），
    在真实归档数据上验证保底规则的两条断言"""

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        from shadow_sector_align import _collect_candidate_sectors, _find_unmatched
        cls._collect = staticmethod(_collect_candidate_sectors)
        cls._find_unmatched = staticmethod(_find_unmatched)
        cls.capital = json.loads((DAY_0924 / "capital.json").read_text(encoding="utf-8"))
        cls.unified = json.loads((DAY_0924 / "unified.json").read_text(encoding="utf-8"))

    def test_honghuangjiu_unmatched_and_floor_restores_one(self):
        """0924 红黄酒未命中资金流（若无归一化映射）→ 全负 DDE 被 Top20 歼灭后，
        保底规则至少补回 1 只进入轮次 3"""
        flow_all = ((self.capital.get("evidence") or {})
                    .get("sector_flow") or {}).get("sector_flow_all") or {}
        sectors = self._collect(DAY_0924)
        self.assertIn("红黄酒", sectors)
        unmatched = self._find_unmatched(sectors, flow_all)

        # 保持 confirmed_sectors 原始顺序（红黄酒居首 = 当日最强主线），
        # 递减赋分避免并列排序不确定性
        actionable = [
            s.get("sector_name") for s in self.unified.get("confirmed_sectors") or []
            if s.get("classification") == "actionable"
        ]
        # 构造轮次 2 现场：20 只强资金票占满 Top20，actionable 主线候选全部被截断
        top = [{"code": f"6000{i:02d}", "sector": "半导体",
                "sector_type": "active_mainline", "sector_score": 80.0,
                "capital_score": 50 - i, "capital_available": True,
                "dde_net_pct": 1.0} for i in range(20)]
        dropped = [{"code": f"6010{i:02d}", "sector": name,
                    "sector_type": "active_mainline",
                    "sector_score": 99.0 - 0.1 * i,
                    "capital_score": 0.0, "capital_available": False,
                    "dde_net_pct": -1.0}
                   for i, name in enumerate(actionable)]
        result = StockScreenerPipeline._apply_capital_floor(top, dropped, top + dropped)
        restored_sectors = {c["sector"] for c in result if c.get("capital_floor_restored")}
        self.assertIn("红黄酒", restored_sectors)
        self.assertLessEqual(len(restored_sectors), 5)
        # 口径自检：未命中名单里的板块确实不在 flow_all（含归一化）中
        from src.got.board_aliases import normalize_sector_name, sector_name_variants
        for name in unmatched:
            self.assertNotIn(name, flow_all)
            self.assertFalse(any(v in flow_all for v in sector_name_variants(name)))
            self.assertNotIn(normalize_sector_name(name),
                             {normalize_sector_name(k) for k in flow_all})


# ─────────────────── OPEN-D9/P2-3 raw_position_ratio 观测留痕 ───────────────────

class TestRawPositionRatio(unittest.TestCase):
    """排期批 3.1（2026-09-28）：current_position_ratio 被 min(1.0, raw) 截断后，
    新增 raw_position_ratio 落盘 pre-clip 真值供离线证伪 13 天 buy_budget=0。
    红线：仅留痕，不得参与任何决策/仓位计算。"""

    def _node_and_ctx(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode()
        ctx = SimpleNamespace(
            final_position_limit=0.5, market_regime=None,
            confirmed_sectors=[], ambush_pool=[],
        )
        return node, ctx

    def test_raw_value_preserved_when_clipped(self):
        """仓位 130% → current 截为 1.0，但 raw 落盘 1.3（证伪截断）"""
        node, ctx = self._node_and_ctx()
        portfolio = {"total_equity": 100_000.0, "total_market_value": 130_000.0}
        risk = node._assess_risk(ctx, portfolio)
        self.assertEqual(risk.current_position_ratio, 1.0)  # 截断后不变
        self.assertAlmostEqual(risk.raw_position_ratio, 1.3, places=4)  # pre-clip 真值

    def test_raw_equals_current_when_not_clipped(self):
        """仓位 20%（未触顶）→ raw == current"""
        node, ctx = self._node_and_ctx()
        portfolio = {"total_equity": 100_000.0, "total_market_value": 20_000.0}
        risk = node._assess_risk(ctx, portfolio)
        self.assertAlmostEqual(risk.current_position_ratio, 0.2, places=4)
        self.assertAlmostEqual(risk.raw_position_ratio, 0.2, places=4)

    def test_raw_position_not_consumed_by_constraint(self):
        """raw 不影响 position_limit / stop_loss 等任何判定输出
        （截断前后两套仓位给出不受 raw 影响的相同约束）"""
        node, ctx = self._node_and_ctx()
        clipped = node._assess_risk(ctx, {"total_equity": 100_000.0, "total_market_value": 130_000.0})
        normal = node._assess_risk(ctx, {"total_equity": 100_000.0, "total_market_value": 50_000.0})
        # 两者 position_limit / stop_loss 一致（不因 raw 差异而变）
        self.assertEqual(clipped.position_limit, normal.position_limit)
        self.assertEqual(clipped.stop_loss_threshold, normal.stop_loss_threshold)


if __name__ == "__main__":
    unittest.main()
