# -*- coding: utf-8 -*-
"""
Phase 3 单元测试：§15 T1/T1-H 审查层 + §14.1 A1/A2 AgentMemory 修复。

覆盖：
1. §15.2 build_review_executor — 审查专用 profile 构建
2. §15.3 T1 候选股审查 — JSON 解析（嵌套支持）+ 影子模式
3. §15.4 T1-H 持仓审查 — 降档惩罚系数 + 优先级选择
4. §14.1 A1 — avg_confidence 计算（非硬编码）
5. §14.1 A2 — outcome 回填逻辑
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# =====================================================================
# §15.2: build_review_executor
# =====================================================================

class TestBuildReviewExecutor(unittest.TestCase):
    """§15.2 审查专用 profile 构建。"""

    def test_build_review_executor_returns_executor(self):
        """build_review_executor 返回 AgentExecutor 实例。"""
        from src.agent.factory import build_review_executor
        from src.agent.executor import AgentExecutor

        executor = build_review_executor()
        self.assertIsInstance(executor, AgentExecutor)

    def test_review_executor_has_custom_system_prompt(self):
        """审查 executor 使用 system_prompt_override。"""
        from src.agent.factory import build_review_executor

        executor = build_review_executor()
        self.assertIsNotNone(executor.system_prompt_override)
        self.assertIn("verdict", executor.system_prompt_override)
        self.assertIn("confirm", executor.system_prompt_override)

    def test_review_executor_has_reduced_max_steps(self):
        """审查 executor max_steps = 4（非默认 10）。"""
        from src.agent.factory import build_review_executor
        from src.got.catalyst_config import AGENT_REVIEW_MAX_STEPS

        executor = build_review_executor()
        self.assertEqual(executor.max_steps, AGENT_REVIEW_MAX_STEPS)
        self.assertEqual(executor.max_steps, 4)

    def test_review_executor_has_filtered_tools(self):
        """审查 executor 只有 3 个工具（get_realtime_quote, get_daily_history, search_stock_news）。"""
        from src.agent.factory import build_review_executor
        from src.got.catalyst_config import AGENT_REVIEW_TOOLS

        executor = build_review_executor()
        tool_names = set(executor.tool_registry._tools.keys())
        expected_tools = set(AGENT_REVIEW_TOOLS)
        self.assertEqual(tool_names, expected_tools)

    def test_review_executor_no_skill_instructions(self):
        """审查 executor 不加载技能指令。"""
        from src.agent.factory import build_review_executor

        executor = build_review_executor()
        self.assertEqual(executor.skill_instructions, "")
        self.assertEqual(executor.default_skill_policy, "")


# =====================================================================
# §15.3: T1 JSON 解析（try_parse_json 替代旧正则）
# =====================================================================

class TestT1JsonParsing(unittest.TestCase):
    """§15.3 T1 候选股审查 JSON 解析。"""

    def test_parse_nested_json(self):
        """try_parse_json 支持嵌套 JSON（旧正则 \\{[^}]+\\} 不支持）。"""
        from src.agent.runner import try_parse_json

        response = '''
        ```json
        {
            "verdict": "reject",
            "reasoning": "高位放量滞涨，主力出货迹象",
            "confidence": 0.85,
            "risk_flags": ["放量滞涨", "板块走弱"]
        }
        ```
        '''
        data = try_parse_json(response)
        self.assertIsNotNone(data)
        self.assertEqual(data["verdict"], "reject")
        self.assertEqual(data["confidence"], 0.85)
        self.assertEqual(len(data["risk_flags"]), 2)

    def test_parse_plain_json(self):
        """try_parse_json 解析纯 JSON。"""
        from src.agent.runner import try_parse_json

        response = '{"verdict": "confirm", "confidence": 0.9, "reasoning": "技术面强势"}'
        data = try_parse_json(response)
        self.assertIsNotNone(data)
        self.assertEqual(data["verdict"], "confirm")

    def test_parse_invalid_returns_none(self):
        """try_parse_json 对无效输入返回 None。"""
        from src.agent.runner import try_parse_json

        data = try_parse_json("这不是 JSON")
        self.assertIsNone(data)

    def test_parse_agent_verdict_v2_valid(self):
        """_parse_agent_verdict_v2 正确解析有效 verdict。"""
        from src.services.stock_screener import StockScreenerPipeline
        from src.agent.runner import try_parse_json

        response = '{"verdict": "reject", "reasoning": "风险过高", "confidence": 0.8}'
        result = StockScreenerPipeline._parse_agent_verdict_v2(response, try_parse_json)
        self.assertEqual(result["verdict"], "reject")
        self.assertEqual(result["reasoning"], "风险过高")
        self.assertEqual(result["confidence"], 0.8)

    def test_parse_agent_verdict_v2_invalid_verdict(self):
        """_parse_agent_verdict_v2 对无效 verdict 降级为 warn。"""
        from src.services.stock_screener import StockScreenerPipeline
        from src.agent.runner import try_parse_json

        response = '{"verdict": "invalid_value", "reasoning": "测试"}'
        result = StockScreenerPipeline._parse_agent_verdict_v2(response, try_parse_json)
        self.assertEqual(result["verdict"], "warn")


# =====================================================================
# §15.4: T1-H 持仓审查
# =====================================================================

class TestT1HPenaltyComputation(unittest.TestCase):
    """§15.4 T1-H 降档惩罚系数。"""

    def test_exit_penalty(self):
        """exit verdict 惩罚系数（最大惩罚 0.4 * confidence）。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        # exit + confidence=1.0 → penalty = 1.0 - 0.4*1.0 = 0.6
        penalty = node._compute_holding_penalty("exit", 1.0)
        self.assertEqual(penalty, 0.6)

        # exit + confidence=0.8 → penalty = 1.0 - 0.4*0.8 = 0.68
        penalty = node._compute_holding_penalty("exit", 0.8)
        self.assertAlmostEqual(penalty, 0.68, places=2)

    def test_reduce_penalty(self):
        """reduce verdict 惩罚系数（中等惩罚 0.2 * confidence）。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        # reduce + confidence=1.0 → penalty = 1.0 - 0.2*1.0 = 0.8
        penalty = node._compute_holding_penalty("reduce", 1.0)
        self.assertEqual(penalty, 0.8)

    def test_penalty_clamped_to_0_6(self):
        """惩罚系数下限 0.6。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        # 极端情况：exit + confidence=1.5（异常值）→ 不应低于 0.6
        penalty = node._compute_holding_penalty("exit", 1.5)
        self.assertGreaterEqual(penalty, 0.6)

    def test_hold_no_penalty(self):
        """hold/add verdict 不应调用惩罚（返回 1.0）。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        # hold/add 不在降档范围，应返回 1.0
        penalty_hold = node._compute_holding_penalty("hold", 0.8)
        self.assertEqual(penalty_hold, 1.0)

        penalty_add = node._compute_holding_penalty("add", 0.9)
        self.assertEqual(penalty_add, 1.0)


class TestT1HTargetSelection(unittest.TestCase):
    """§15.4 T1-H 审查目标优先级选择。"""

    def _make_holding(self, symbol, composite_score=50, ambush_type=None, exit_signal=None):
        """创建测试用 UnifiedScoringResult。"""
        from src.got.schemas import UnifiedScoringResult
        return UnifiedScoringResult(
            symbol=symbol,
            name=f"测试{symbol}",
            is_holding=True,
            composite_score=composite_score,
            dimension_scores={"capital": 50, "news": 50, "sector": 50, "tech": 50, "risk": 50},
            action_recommendation="hold",
            confidence=0.7,
            reasoning="测试",
            ambush_position_type=ambush_type,
            ambush_exit_signal=exit_signal,
        )

    def test_ambush_first_priority(self):
        """埋伏持仓优先审查。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holdings = [
            self._make_holding("000001", composite_score=60),
            self._make_holding("000002", composite_score=50, ambush_type="drift"),
            self._make_holding("000003", composite_score=30),
        ]
        targets = node._select_holding_review_targets(holdings, max_count=2)
        # 000002 是埋伏持仓，应排第一
        self.assertEqual(targets[0].symbol, "000002")

    def test_exit_signal_second_priority(self):
        """P4 退出信号第二优先。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holdings = [
            self._make_holding("000001", composite_score=60),
            self._make_holding("000002", composite_score=50, exit_signal="fade_sell"),
        ]
        targets = node._select_holding_review_targets(holdings, max_count=2)
        self.assertEqual(targets[0].symbol, "000002")

    def test_low_score_third_priority(self):
        """低分持仓第三优先。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holdings = [
            self._make_holding("000001", composite_score=60),
            self._make_holding("000002", composite_score=30),  # < 40
        ]
        targets = node._select_holding_review_targets(holdings, max_count=2)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].symbol, "000002")

    def test_max_count_limit(self):
        """审查数量不超过预算上限。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holdings = [
            self._make_holding(f"00000{i}", composite_score=30) for i in range(1, 6)
        ]
        targets = node._select_holding_review_targets(holdings, max_count=2)
        self.assertLessEqual(len(targets), 2)


class TestT1HCatalystSnapshot(unittest.TestCase):
    """§15.4 T1-H P4 催化剂快照注入（_build_holding_review_prompt）。"""

    def _make_holding(self, symbol, ambush_type=None, exit_signal=None, exit_reason=None):
        from src.got.schemas import UnifiedScoringResult
        return UnifiedScoringResult(
            symbol=symbol,
            name=f"测试{symbol}",
            is_holding=True,
            composite_score=50,
            dimension_scores={"capital": 50, "news": 50, "sector": 50, "tech": 50, "risk": 50},
            action_recommendation="hold",
            confidence=0.7,
            reasoning="测试",
            ambush_position_type=ambush_type,
            ambush_exit_signal=exit_signal,
            ambush_exit_reason=exit_reason,
        )

    def test_full_snapshot_injected(self):
        """快照 map 命中 → prompt 注入事件标题/共识预期/影响等级/建仓信息。"""
        from datetime import date
        from types import SimpleNamespace
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holding = self._make_holding("000002", ambush_type="binary")
        node._ambush_snapshot_map = {
            "000002": SimpleNamespace(
                category_tag="binary",
                catalyst_event_title="半年报业绩预告",
                catalyst_event_date=date(2026, 9, 10),
                impact_level=3,
                consensus_expectation="预期净利同比+50%",
                entry_date=date(2026, 9, 1),
                entry_price=10.50,
            )
        }
        prompt = node._build_holding_review_prompt(holding, "bull", None, {})
        self.assertIn("埋伏类型=binary", prompt)
        self.assertIn("催化剂事件=半年报业绩预告", prompt)
        self.assertIn("共识预期=预期净利同比+50%", prompt)
        self.assertIn("影响等级=★★★", prompt)
        self.assertIn("建仓=2026-09-01@10.50", prompt)

    def test_exit_reason_included_from_snapshot_path(self):
        """快照命中 + holding 有退出信号/理由 → 两者都注入（补上遗漏的 exit_reason）。"""
        from types import SimpleNamespace
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holding = self._make_holding(
            "000002", ambush_type="drift",
            exit_signal="drift_exhausted", exit_reason="漂移动能衰竭",
        )
        node._ambush_snapshot_map = {
            "000002": SimpleNamespace(
                category_tag="drift",
                catalyst_event_title="",
                catalyst_event_date=None,
                impact_level=0,
                consensus_expectation="",
                entry_date=None,
                entry_price=None,
            )
        }
        prompt = node._build_holding_review_prompt(holding, "bull", None, {})
        self.assertIn("退出信号=drift_exhausted", prompt)
        self.assertIn("退出理由=漂移动能衰竭", prompt)

    def test_fallback_to_holding_fields_when_no_snapshot(self):
        """快照 map 为空 → 回退 holding 上的 P4 代号字段（含补上的 exit_reason）。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holding = self._make_holding(
            "000003", ambush_type="fade",
            exit_signal="fade_sell", exit_reason="利好兑现",
        )
        node._ambush_snapshot_map = {}
        prompt = node._build_holding_review_prompt(holding, "bear", None, {})
        self.assertIn("埋伏类型=fade", prompt)
        self.assertIn("退出信号=fade_sell", prompt)
        self.assertIn("退出理由=利好兑现", prompt)

    def test_no_snapshot_map_attr_does_not_crash(self):
        """未初始化 _ambush_snapshot_map（getattr 默认 {}）→ 不抛异常，回退生效。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holding = self._make_holding("000004", ambush_type="binary")
        # 故意不设置 node._ambush_snapshot_map
        prompt = node._build_holding_review_prompt(holding, "unknown", None, {})
        self.assertIn("埋伏类型=binary", prompt)

    def test_non_ambush_holding_shows_none(self):
        """非埋伏持仓（无快照、无 position_type）→ 催化剂=无。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        node = PortfolioDecisionNode()
        holding = self._make_holding("000005")
        node._ambush_snapshot_map = {}
        prompt = node._build_holding_review_prompt(holding, "bull", None, {})
        self.assertIn("催化剂: 无", prompt)


# =====================================================================
# §14.1 A1: avg_confidence 计算
# =====================================================================

class TestAvgConfidenceComputation(unittest.TestCase):
    """§14.1 A1 avg_confidence 计算（非硬编码）。"""

    def test_compute_avg_confidence_no_data(self):
        """无历史数据时返回中性值 0.5。"""
        from src.agent.memory import AgentMemory

        memory = AgentMemory(enabled=True)
        avg = memory._compute_avg_confidence(stock_code="999999_NONEXISTENT")
        self.assertEqual(avg, 0.5)

    def test_avg_confidence_not_hardcoded(self):
        """验证 avg_confidence 不是硬编码 0.6（§14.1 A1 核心修复）。"""
        from src.agent.memory import AgentMemory
        import inspect

        # 读取源码，确认没有 "avg_confidence": 0.6
        source = inspect.getsource(AgentMemory._get_accuracy_stats)
        self.assertNotIn('"avg_confidence": 0.6', source)
        self.assertNotIn("'avg_confidence': 0.6", source)

    def test_compute_avg_confidence_real_mean(self):
        """A1 正路径：从历史记录计算真实均值（非 0.5/0.6 兜底）。"""
        import json
        from src.agent.memory import AgentMemory

        records = [
            (json.dumps({"confidence": 0.8}),),
            (json.dumps({"confidence": 0.6}),),
            (json.dumps({"overall_confidence": 0.7}),),
            (json.dumps({"no_conf": 1}),),   # 无 confidence 字段 → 跳过
            ("not-json",),                    # 解析失败 → 跳过
        ]

        class _FakeQuery:
            def filter(self, *a, **k): return self
            def order_by(self, *a, **k): return self
            def limit(self, *a, **k): return self
            def all(self): return records

        class _FakeSession:
            def query(self, *a, **k): return _FakeQuery()
            def close(self): pass

        class _FakeDB:
            def get_session(self): return _FakeSession()

        memory = AgentMemory(enabled=True)
        with patch("src.storage.get_db", return_value=_FakeDB()):
            avg = memory._compute_avg_confidence(stock_code="600519")
        # (0.8 + 0.6 + 0.7) / 3 = 0.7
        self.assertAlmostEqual(avg, 0.7, places=6)


class TestOutcomeBackfill(unittest.TestCase):
    """§14.1 A2 outcome 回填逻辑。"""

    def test_compute_outcomes_no_price(self):
        """price_at_analysis=0 时不计算 outcomes。"""
        from src.agent.memory import AgentMemory, AnalysisMemoryEntry

        memory = AgentMemory(enabled=True)
        entry = AnalysisMemoryEntry(
            stock_code="600519",
            date="2026-01-01",
            signal="buy",
            price_at_analysis=0.0,
        )
        result = memory._compute_entry_outcomes(entry, "buy", datetime(2026, 1, 1))
        self.assertIsNone(result.outcome_5d)
        self.assertIsNone(result.outcome_20d)
        self.assertIsNone(result.was_correct)

    def test_outcome_fields_exist(self):
        """验证 AnalysisMemoryEntry 有 outcome 字段（§14.1 A2 结构完整性）。"""
        from src.agent.memory import AnalysisMemoryEntry

        entry = AnalysisMemoryEntry()
        self.assertTrue(hasattr(entry, "outcome_5d"))
        self.assertTrue(hasattr(entry, "outcome_20d"))
        self.assertTrue(hasattr(entry, "was_correct"))
        # 默认值为 None
        self.assertIsNone(entry.outcome_5d)
        self.assertIsNone(entry.outcome_20d)
        self.assertIsNone(entry.was_correct)

    @staticmethod
    def _make_klines(entry_date, closes_by_offset):
        """构造伪造 K 线（按日期升序）：{偏移天数: 收盘价}。"""
        return [
            {"date": entry_date + timedelta(days=offset), "close": close}
            for offset, close in sorted(closes_by_offset.items())
        ]

    def test_compute_outcomes_bullish_correct(self):
        """A2 正路径：buy 信号 + 5日上涨 → outcome_5d>0 且 was_correct=True。"""
        from src.agent.memory import AgentMemory, AnalysisMemoryEntry

        memory = AgentMemory(enabled=True)
        created = datetime(2026, 1, 1)
        entry = AnalysisMemoryEntry(
            stock_code="600519", date="2026-01-01", signal="buy",
            price_at_analysis=100.0,
        )
        klines = self._make_klines(created.date(), {0: 100.0, 5: 110.0, 20: 120.0})
        result = memory._compute_entry_outcomes(entry, "buy", created, klines=klines)
        self.assertEqual(result.outcome_5d, 10.0)    # (110-100)/100*100
        self.assertEqual(result.outcome_20d, 20.0)   # (120-100)/100*100
        self.assertTrue(result.was_correct)

    def test_compute_outcomes_bearish_correct(self):
        """A2 正路径：sell 信号 + 5日下跌 → was_correct=True。"""
        from src.agent.memory import AgentMemory, AnalysisMemoryEntry

        memory = AgentMemory(enabled=True)
        created = datetime(2026, 1, 1)
        entry = AnalysisMemoryEntry(
            stock_code="600519", date="2026-01-01", signal="sell",
            price_at_analysis=100.0,
        )
        klines = self._make_klines(created.date(), {0: 100.0, 5: 90.0, 20: 85.0})
        result = memory._compute_entry_outcomes(entry, "sell", created, klines=klines)
        self.assertEqual(result.outcome_5d, -10.0)
        self.assertEqual(result.outcome_20d, -15.0)
        self.assertTrue(result.was_correct)   # bearish + 下跌 → 正确

    def test_compute_outcomes_hold_neutral(self):
        """A2 正路径：hold 信号 5日波动<3% → 正确；>3% → 错误。"""
        from src.agent.memory import AgentMemory, AnalysisMemoryEntry

        memory = AgentMemory(enabled=True)
        created = datetime(2026, 1, 1)
        # 小幅波动 1% → hold 正确
        entry_small = AnalysisMemoryEntry(
            stock_code="600519", date="2026-01-01", signal="hold",
            price_at_analysis=100.0,
        )
        klines_small = self._make_klines(created.date(), {0: 100.0, 5: 101.0})
        r_small = memory._compute_entry_outcomes(entry_small, "hold", created, klines=klines_small)
        self.assertEqual(r_small.outcome_5d, 1.0)
        self.assertTrue(r_small.was_correct)
        # 大幅波动 8% → hold 错误
        entry_big = AnalysisMemoryEntry(
            stock_code="600519", date="2026-01-01", signal="hold",
            price_at_analysis=100.0,
        )
        klines_big = self._make_klines(created.date(), {0: 100.0, 5: 108.0})
        r_big = memory._compute_entry_outcomes(entry_big, "hold", created, klines=klines_big)
        self.assertEqual(r_big.outcome_5d, 8.0)
        self.assertFalse(r_big.was_correct)


if __name__ == "__main__":
    unittest.main()
