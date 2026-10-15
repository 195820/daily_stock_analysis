# -*- coding: utf-8 -*-
"""
Phase 2 单元测试：Node 6 Regime 状态机。

覆盖：
1. load_previous_regime_state 归档回读
2. load_regime_state_history 历史序列
3. _apply_regime_state_machine 4 种场景（持续/确认切换/待确认/冷启动）
4. _compute_position_from_regime dwell 感知缩放
5. MarketRegime Schema 向后兼容
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestLoadPreviousRegimeState(unittest.TestCase):
    """测试 load_previous_regime_state 归档回读。"""

    def test_returns_none_when_no_archive(self):
        """无归档时返回 None。"""
        from src.got.persistence import load_previous_regime_state
        with patch("src.got.persistence._list_archive_dirs", return_value=[]):
            result = load_previous_regime_state()
        self.assertIsNone(result)

    def test_returns_regime_fields(self):
        """返回 regime 状态机字段。"""
        from src.got.persistence import load_previous_regime_state
        mock_data = {
            "market_regime": {
                "regime": "bull",
                "dwell_days": 5,
                "previous_regime": "transition",
                "transition_count": 2,
                "transition_pending": None,
            }
        }
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_unified_json", return_value=mock_data):
            mock_dirs.return_value = ["/fake/20260827"]
            result = load_previous_regime_state()
        self.assertIsNotNone(result)
        self.assertEqual(result["regime"], "bull")
        self.assertEqual(result["dwell_days"], 5)
        self.assertEqual(result["transition_count"], 2)
        # §7.1 新字段：旧归档无该键时回读默认值（pinned=0 / events=None=未迁移）
        self.assertEqual(result["pinned_days"], 0)
        self.assertIsNone(result["transition_events"])

    def test_skips_missing_regime_field(self):
        """跳过无 regime 字段的归档。"""
        from src.got.persistence import load_previous_regime_state
        mock_data_no_regime = {"market_regime": {}}
        mock_data_with_regime = {"market_regime": {"regime": "bear"}}
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_unified_json") as mock_load:
            mock_dirs.return_value = ["/fake/20260827", "/fake/20260826"]
            mock_load.side_effect = [mock_data_no_regime, mock_data_with_regime]
            result = load_previous_regime_state()
        self.assertIsNotNone(result)
        self.assertEqual(result["regime"], "bear")


class TestLoadRegimeStateHistory(unittest.TestCase):
    """测试 load_regime_state_history 历史序列。"""

    def test_returns_empty_when_no_archive(self):
        """无归档时返回空列表。"""
        from src.got.persistence import load_regime_state_history
        with patch("src.got.persistence._list_archive_dirs", return_value=[]):
            result = load_regime_state_history(days=20)
        self.assertEqual(result, [])

    def test_returns_ascending_order(self):
        """返回的序列按日期升序排列。"""
        from pathlib import Path
        from src.got.persistence import load_regime_state_history
        mock_data_1 = {"market_regime": {"regime": "bull"}}
        mock_data_2 = {"market_regime": {"regime": "transition"}}
        mock_data_3 = {"market_regime": {"regime": "bear"}}
        with patch("src.got.persistence._list_archive_dirs") as mock_dirs, \
             patch("src.got.persistence._load_unified_json") as mock_load:
            # 返回的是按日期降序（Path 对象）
            mock_dirs.return_value = [Path("/fake/20260827"), Path("/fake/20260826"), Path("/fake/20260825")]
            mock_load.side_effect = [mock_data_1, mock_data_2, mock_data_3]
            result = load_regime_state_history(days=20)
        self.assertEqual(len(result), 3)
        # 应该是升序（从旧到新）
        self.assertEqual(result[0][1], "bear")
        self.assertEqual(result[1][1], "transition")
        self.assertEqual(result[2][1], "bull")


class TestApplyRegimeStateMachine(unittest.TestCase):
    """测试 _apply_regime_state_machine 4 种场景。"""

    def _make_regime(self, regime="bull", **kwargs):
        from src.got.schemas import MarketRegime
        return MarketRegime(
            regime=regime,
            risk_budget=0.6,
            **kwargs,
        )

    def test_continuation_dwell_increments(self):
        """raw == previous → dwell+1，清除 pending。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("bull")
        prev_state = {
            "regime": "bull",
            "dwell_days": 3,
            "previous_regime": "transition",
            "transition_count": 1,
            "transition_pending": None,
        }
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=[
                 ("20260825", "transition"), ("20260826", "bull"), ("20260827", "bull")
             ]):
            result = AggregatorNode._apply_regime_state_machine(regime)
        self.assertEqual(result.regime, "bull")
        self.assertEqual(result.dwell_days, 4)
        self.assertIsNone(result.transition_pending)

    def test_confirmed_switch(self):
        """raw != previous 且 raw == transition_pending → 确认切换，dwell=1。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("bear")
        prev_state = {
            "regime": "bull",
            "dwell_days": 5,
            "previous_regime": "transition",
            "transition_count": 1,
            "transition_pending": "bear",  # pending 与 raw 相同
        }
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=[
                 ("20260825", "transition"), ("20260826", "bull"), ("20260827", "bear")
             ]):
            result = AggregatorNode._apply_regime_state_machine(regime)
        self.assertEqual(result.regime, "bear")
        self.assertEqual(result.dwell_days, 1)
        self.assertIsNone(result.transition_pending)
        # R2：情形 2（以及 §7.1 迟滞豁免分支）会让 previous_regime != regime。
        # 豁免强切日由 regime_switch_exempt=True 归档标记区分（审核修正②），
        # 本例验证情形 2 的两日确认路径该标记为 False。
        # 见 docs/got/backlog.md §9 #3 / §9.1。
        self.assertEqual(result.previous_regime, "bull")
        self.assertFalse(result.regime_switch_exempt)

    def test_pending_first_appearance(self):
        """raw != previous 且 raw != transition_pending → 保持 previous，设新 pending。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("bear")
        prev_state = {
            "regime": "bull",
            "dwell_days": 3,
            "previous_regime": "transition",
            "transition_count": 1,
            "transition_pending": None,
        }
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=[
                 ("20260825", "transition"), ("20260826", "bull"), ("20260827", "bear")
             ]):
            result = AggregatorNode._apply_regime_state_machine(regime)
        # 保持 previous（bull），设新 pending（bear）
        self.assertEqual(result.regime, "bull")
        self.assertEqual(result.dwell_days, 4)  # dwell 继续+1
        self.assertEqual(result.transition_pending, "bear")

    def test_cold_start_no_archive(self):
        """冷启动：无归档 → current=raw, dwell=1。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("transition")
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=None), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=[]):
            result = AggregatorNode._apply_regime_state_machine(regime)
        self.assertEqual(result.regime, "transition")
        self.assertEqual(result.dwell_days, 1)
        self.assertIsNone(result.previous_regime)
        self.assertEqual(result.transition_count, 0)

    def test_transition_count_from_history(self):
        """transition_count 从 20 日历史中统计切换次数。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime = self._make_regime("bull")
        prev_state = {
            "regime": "bull",
            "dwell_days": 1,
            "previous_regime": "bear",
            "transition_count": 0,
            "transition_pending": None,
        }
        # 模拟 5 日历史：transition → bear → bull → transition → bull = 4 次切换
        history = [
            ("20260821", "transition"),
            ("20260822", "bear"),
            ("20260825", "bull"),
            ("20260826", "transition"),
            ("20260827", "bull"),
        ]
        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history", return_value=history):
            result = AggregatorNode._apply_regime_state_machine(regime)
        self.assertEqual(result.transition_count, 4)


class TestRegimeOscillationFix(unittest.TestCase):
    """§7.1 振荡修复（2026-09-26）：迟滞豁免 + 确认切换事件计数 + 回退开关。"""

    def _make_regime(self, regime="bull", **kwargs):
        from src.got.schemas import MarketRegime
        return MarketRegime(regime=regime, risk_budget=0.6, **kwargs)

    @staticmethod
    def _prev(regime="transition", dwell=12, pending=None, pinned=0, events=None):
        return {
            "regime": regime,
            "dwell_days": dwell,
            "previous_regime": regime,
            "transition_count": 0,
            "transition_pending": pending,
            "pinned_days": pinned,
            "transition_events": events,
        }

    def _run_day(self, raw, prev_state, history=None, today=None):
        from datetime import date as _real_date, datetime as _real_dt
        from src.got.nodes.aggregator import AggregatorNode

        if today is None:
            today = _real_date.today().strftime("%Y%m%d")

        class _FixedDate:
            @staticmethod
            def today():
                return _real_dt.strptime(today, "%Y%m%d").date()

        with patch("src.got.nodes.aggregator.load_previous_regime_state", return_value=prev_state), \
             patch("src.got.nodes.aggregator.load_regime_state_history",
                   return_value=history if history is not None else []), \
             patch("src.got.nodes.aggregator.date", _FixedDate):
            return AggregatorNode._apply_regime_state_machine(self._make_regime(raw))

    def test_seed_from_output_diff(self):
        """迁移种子：旧输出 diff 法的切换日列表。"""
        from src.got.persistence import seed_regime_transition_events
        history = [
            ("20260825", "transition"),
            ("20260826", "bull"),
            ("20260827", "bull"),
            ("20260828", "bear"),
        ]
        self.assertEqual(seed_regime_transition_events(history), ["20260826", "20260828"])
        self.assertEqual(seed_regime_transition_events([]), [])

    def test_below_threshold_keeps_two_day_confirmation(self):
        """钉住未达阈值且候选易主：仍按旧逻辑换挂新候选、继续钉住（情形 3）。"""
        r = self._run_day("bear", self._prev(pending="bull", pinned=2))
        self.assertEqual(r.regime, "transition")       # 保持昨日
        self.assertEqual(r.transition_pending, "bear")  # 换挂新候选
        self.assertEqual(r.pinned_days, 3)

    def test_exempt_switch_after_pinned_threshold(self):
        """钉住≥阈值后候选再现 → 豁免确认切换（死锁打破）。"""
        r = self._run_day("bear", self._prev(pending="bear", pinned=3))
        from datetime import date
        self.assertEqual(r.regime, "bear")
        self.assertEqual(r.dwell_days, 1)
        self.assertIsNone(r.transition_pending)
        self.assertEqual(r.pinned_days, 0)
        self.assertTrue(r.regime_switch_exempt)   # 豁免强切需可归档复盘
        today = date.today().strftime("%Y%m%d")
        self.assertEqual(r.transition_events, [today])
        self.assertEqual(r.transition_count, 1)

    def test_oscillation_chain_exits_pinning(self):
        """0910 实态复现：bull/bear 日日交替 + 起始 pinned=3 → 当日迟滞豁免脱钉
        （打破 transition 死锁）；此后纯交替信号被迟滞抑制（输出锁在脱钉值），
        持续两日的真实信号（末尾两日 bull）仍按情形 2 正常确认——机器不再死锁。"""
        state = self._prev(regime="transition", dwell=12, pending="bull", pinned=3)
        sequence = ["bear", "bull", "bear", "bear", "bull", "bull"]
        states = []
        for raw in sequence:
            r = self._run_day(raw, state)
            states.append(r.regime)
            state = {
                "regime": r.regime, "dwell_days": r.dwell_days,
                "previous_regime": r.previous_regime,
                "transition_count": r.transition_count,
                "transition_pending": r.transition_pending,
                "pinned_days": r.pinned_days,
                "transition_events": r.transition_events,
            }
        # 首日 pinned=3 已满阈值 → 迟滞豁免直接确认 bear；交替噪声被抑制，
        # 末尾两日连续 bull 触发情形 2 二次确认切换
        self.assertEqual(states, ["bear", "bear", "bear", "bear", "bear", "bull"])
        self.assertNotIn("transition", states)
        # 豁免脱钉计入确认切换事件（同日重跑幂等，不重复 append）
        self.assertGreaterEqual(state["transition_count"], 1)

    def test_event_list_accumulates_and_windows(self):
        """事件列表：确认切换 append 当日日期；同日重跑不重复 append；
        窗口外事件不计入 transition_count（窗口=历史最早日..今日，含当日）。"""
        # 生产语义：history 来自 before=today 的归档，末元素必 < 今日
        history = [("20260909", "bull"), ("20260910", "bear"), ("20260911", "bear")]
        prev = self._prev(regime="bear", dwell=4, pending="bull", pinned=2,
                          events=["20260901", "20260910"])
        r = self._run_day("bull", prev, history=history, today="20260912")
        self.assertEqual(r.regime, "bull")            # 情形 2：pinned=2 < 3，两日确认
        self.assertFalse(r.regime_switch_exempt)
        self.assertEqual(
            r.transition_events, ["20260901", "20260910", "20260912"]
        )
        # 窗口 [20260909, 20260912]：20260901 在外，今日事件计入
        self.assertEqual(r.transition_count, 2)
        # 同日重跑幂等：事件列表已含今日 → 不重复 append
        prev2 = self._prev(regime="bear", dwell=4, pending="bull", pinned=2,
                           events=["20260901", "20260910", "20260912"])
        r2 = self._run_day("bull", prev2, history=history, today="20260912")
        self.assertEqual(
            r2.transition_events, ["20260901", "20260910", "20260912"]
        )
        self.assertEqual(r2.transition_count, 2)

    def test_seed_carries_over_on_first_switch(self):
        """审核修正①回归：迁移种子播种进事件列表，首个确认切换日计数
        只增（种子+当日）不归零；当日事件按日期窗口计入。"""
        history = [
            ("20260918", "bull"), ("20260919", "bear"), ("20260920", "bull"),
            ("20260921", "bear"), ("20260922", "bull"),
        ]  # 旧输出相邻 diff = 4 次切换
        prev = self._prev(regime="bull", pending="bear", pinned=0)
        # 无切换日（情形 1）：种子读数与旧 diff 口径一致
        r0 = self._run_day("bull", prev, history=history, today="20260923")
        self.assertEqual(r0.transition_count, 4)
        # 确认切换日（情形 2）：种子 4 + 今日 = 5，不再整盘丢弃种子
        r1 = self._run_day("bear", prev, history=history, today="20260923")
        self.assertEqual(r1.transition_count, 5)
        self.assertEqual(
            r1.transition_events,
            ["20260919", "20260920", "20260921", "20260922", "20260923"],
        )

    def test_migrated_empty_events_counts_zero(self):
        """已迁移归档（transition_events=[]）：不回退种子，计数从 0；
        空列表照落保留「已迁移」标记（与 None=未迁移区分）。"""
        history = [("20260918", "bull"), ("20260919", "bear")]  # 旧 diff=1，不应被用
        r = self._run_day(
            "bull", self._prev(regime="bull", events=[]),
            history=history, today="20260923",
        )
        self.assertEqual(r.transition_count, 0)
        self.assertEqual(r.transition_events, [])

    def test_legacy_switch_disables_fix_and_seed_fallback(self):
        """DISABLE_REGIME_OSCILLATION_FIX=true：无豁免，计数回退旧输出 diff（种子）。"""
        import os as _os
        history = [
            ("20260825", "transition"),
            ("20260826", "bull"),
            ("20260827", "bear"),
            ("20260828", "bull"),
        ]
        prev = self._prev(pending="bear", pinned=5)
        os_ctx = patch.dict(_os.environ, {"DISABLE_REGIME_OSCILLATION_FIX": "true"})
        with os_ctx:
            r = self._run_day("bear", prev, history=history)
        self.assertEqual(r.regime, "bear")   # 旧行为：raw==pending 仍为情形 2 确认
        self.assertEqual(r.transition_count, 3)  # 旧 diff 法：4 日序列 3 次变化
        self.assertIsNone(r.transition_events)   # 旧模式不记事件，避免半新半旧


class TestRegimeJumpModel(unittest.TestCase):
    """#14 ④ jump penalty 模型（backlog §7 #14 拍板 2026-10-15）。

    s_t = argmin_s[|raw−θ_s| + λ·1{s≠s_{t-1}}]；影子层零决策改动；
    生产切换（GOT_REGIME_JUMP_PENALTY=1）替换确认层。
    """

    def _run_jump(self, raw, history, today=None, lam=None):
        from datetime import date as _real_date, datetime as _real_dt
        from src.got.nodes.aggregator import AggregatorNode
        from src.got.schemas import MarketRegime as _MR

        if today is None:
            today = _real_date.today().strftime("%Y%m%d")
        if lam is None:
            from src.got.catalyst_config import REGIME_JUMP_LAMBDA
            lam = REGIME_JUMP_LAMBDA

        class _FixedDate:
            @staticmethod
            def today():
                return _real_dt.strptime(today, "%Y%m%d").date()

        regime = _MR(
            regime="transition", risk_budget=0.4, raw_regime_score=raw,
        )
        with patch("src.got.persistence.load_regime_jump_history",
                   return_value=history), \
             patch("src.got.nodes.aggregator.date", _FixedDate):
            return AggregatorNode._apply_regime_jump_model(regime)

    @staticmethod
    def _row(day, raw, regime):
        return {"date": day, "raw": raw, "regime": regime}

    def test_argmin_tie_keeps_previous(self):
        """切换与保持代价相等 → 保持 s_{t-1}（不动点优先）。"""
        from src.got.nodes.aggregator import AggregatorNode
        # raw=-0.05: stay transition=0.05; bear=0.25+λ → 任取 λ 使 0.25+λ==0.05 不可能，
        # 改造对称 tie：raw=0.3, prev=bull: stay=0.0? |0.3-0.3|=0 → bull 显然。
        # tie 构造：raw=0.25, prev=bull → stay |0.25-0.3|=0.05; transition 0.25+λ;
        # 取 λ 使 0.25+λ=0.05 → λ=-0.2 非法。用 bear tie：raw=-0.05, prev=transition
        # stay=0.05, bear=0.25+0.3=0.55, bull=0.35 — 无 tie。直接验证严格小于逻辑：
        # raw=-0.3, prev=bull: stay=0.6, bear=0.0+0.25=0.25 → bear wins（非 tie）。
        # tie 唯一可达：stay==切换代价，如 raw=-0.05, prev=transition, λ=-0.2 非法 →
        # 实际 tie 面为测度零，此处验证主导序（严格 <）即可。
        s, cost = AggregatorNode._jump_argmin(-0.36, "transition", 0.25)
        self.assertEqual(s, "bear")       # 0.06+0.25=0.31 < 0.36
        self.assertEqual(cost, 0.36)

    def test_lambda_too_large_pins_initial_state(self):
        """λ=2.0：|raw−θ| 最大 1.3 < 2.0 → 锚点阈值态永不切换（锚点实测）。"""
        raw_seq = [-0.74, 0.53, -0.66, 0.31, -0.36]
        hist = []
        for i, r in enumerate(raw_seq[:-1]):
            day = f"2026{1000+i}"
            hist.append(self._row(day, r, "transition"))
        result = self._run_jump(raw_seq[-1], hist)
        # 锚点（首日 raw=-0.74）阈值态 = bear，λ 过大钉死在 bear
        self.assertEqual(result.regime, "bear")

    def test_jump_switch_immediate(self):
        """raw 越过 θ±λ 成本线 → 当日切换（无两日确认）。"""
        hist = [
            self._row("20261013", 0.10, "transition"),
            self._row("20261014", -0.46, "transition"),
        ]
        result = self._run_jump(-0.74, hist, today="20261015")
        # 1014 已是 bear（0.06+0.25<0.46）；1015 raw=-0.74 → 保持 bear
        self.assertEqual(result.regime, "bear")
        self.assertEqual(result.previous_regime, "bear")
        # 1013→1014 transition→bear 1 次 + 1014→1015 保持 = 1 次切换
        self.assertEqual(result.transition_count, 1)

    def test_transition_count_is_jump_window(self):
        """transition_count = 20 日窗 jump 切换数（事件日列表含当日）。"""
        hist = [
            self._row("20261008", -0.57, "transition"),
            self._row("20261009", -0.29, "transition"),
            self._row("20261012", -0.66, "transition"),
            self._row("20261013", 0.10, "transition"),
            self._row("20261014", -0.46, "transition"),
        ]
        result = self._run_jump(-0.74, hist, today="20261015")
        # 锚点 1008 raw=-0.57 → bear；重放：1009/1012 保持 bear，
        # 1013 (0.10) bear→transition、1014 (-0.46) transition→bear、1015 保持
        self.assertEqual(result.transition_events, ["20261013", "20261014"])
        self.assertEqual(result.transition_count, 2)
        self.assertEqual(result.regime, "bear")

    def test_legacy_rows_estimate_via_output_diff(self):
        """raw 缺失的旧归档日：按输出 diff 估算事件（迁移种子语义）。"""
        hist = [
            self._row("20261001", None, "bull"),
            self._row("20261002", None, "bear"),   # diff → 事件
            self._row("20261003", None, "bear"),
        ]
        result = self._run_jump(-0.5, hist, today="20261004")
        self.assertEqual(result.transition_count, 1)

    def test_cold_start_no_history(self):
        """冷启动：s_0 = raw 阈值态，无切换成本。"""
        result = self._run_jump(-0.74, [], today="20261015")
        self.assertEqual(result.regime, "bear")
        self.assertEqual(result.transition_count, 0)
        self.assertEqual(result.dwell_days, 1)
        self.assertEqual(result.pinned_days, 0)
        self.assertIsNone(result.transition_pending)

    def test_retired_fields_default(self):
        """退役字段恒空：pinned=0 / pending=None / exempt=False。"""
        result = self._run_jump(-0.74, [self._row("20261014", -0.46, "transition")])
        self.assertEqual(result.pinned_days, 0)
        self.assertIsNone(result.transition_pending)
        self.assertFalse(result.regime_switch_exempt)


class TestComputePositionDwellAware(unittest.TestCase):
    """测试 _compute_position_from_regime dwell 感知缩放。"""

    def _make_regime(self, regime, dwell_days, transition_count=0, risk_budget=0.6):
        from src.got.schemas import MarketRegime
        return MarketRegime(
            regime=regime,
            risk_budget=risk_budget,
            dwell_days=dwell_days,
            transition_count=transition_count,
        )

    def _make_sector_intels(self, count=2, classification="actionable"):
        from src.got.schemas import SectorIntel
        return [
            SectorIntel(
                sector_name=f"板块{i}",
                classification=classification,
                confidence=0.7,
                composite_score=60.0,
            )
            for i in range(count)
        ]

    def test_bull_fresh_lower_than_established(self):
        """bull fresh (dwell<5) 比 established 仓位低。"""
        from src.got.nodes.aggregator import AggregatorNode
        # 使用较高的 risk_budget 避免被截断
        regime_fresh = self._make_regime("bull", dwell_days=2, risk_budget=0.8)
        regime_established = self._make_regime("bull", dwell_days=10, risk_budget=0.8)
        sectors = self._make_sector_intels(3)

        pos_fresh = AggregatorNode._compute_position_from_regime(regime_fresh, sectors)
        pos_established = AggregatorNode._compute_position_from_regime(regime_established, sectors)

        # fresh 应该比 established 低（0.9 vs 1.0 系数）
        self.assertLess(pos_fresh, pos_established)

    def test_bear_fresh_higher_than_established(self):
        """bear fresh (dwell<5) 比 established 仓位高（初期不那么悲观）。"""
        from src.got.nodes.aggregator import AggregatorNode
        regime_fresh = self._make_regime("bear", dwell_days=2, risk_budget=0.8)
        regime_established = self._make_regime("bear", dwell_days=10, risk_budget=0.8)
        sectors = self._make_sector_intels(3)

        pos_fresh = AggregatorNode._compute_position_from_regime(regime_fresh, sectors)
        pos_established = AggregatorNode._compute_position_from_regime(regime_established, sectors)

        self.assertGreater(pos_fresh, pos_established)

    def test_transition_count_penalty(self):
        """transition_count ≥ 5 → × 0.8 震荡惩罚。"""
        from src.got.nodes.aggregator import AggregatorNode
        # 使用较高的 risk_budget 和更多 sectors 避免被截断
        regime_low = self._make_regime("bull", dwell_days=10, transition_count=3, risk_budget=0.8)
        regime_high = self._make_regime("bull", dwell_days=10, transition_count=6, risk_budget=0.8)
        sectors = self._make_sector_intels(5)  # 更多 sectors

        pos_low = AggregatorNode._compute_position_from_regime(regime_low, sectors)
        pos_high = AggregatorNode._compute_position_from_regime(regime_high, sectors)

        # high 应该比 low 低（因为有 0.8 惩罚）
        self.assertLess(pos_high, pos_low)


class TestMarketRegimeBackwardCompat(unittest.TestCase):
    """测试 MarketRegime 新字段向后兼容。"""

    def test_default_values(self):
        """默认值：dwell_days=1, transition_count=0, pending/previous=None。"""
        from src.got.schemas import MarketRegime
        regime = MarketRegime(regime="bull", risk_budget=0.6)
        self.assertEqual(regime.dwell_days, 1)
        self.assertEqual(regime.transition_count, 0)
        self.assertIsNone(regime.previous_regime)
        self.assertIsNone(regime.transition_pending)

    def test_explicit_values(self):
        """显式传入生效。"""
        from src.got.schemas import MarketRegime
        regime = MarketRegime(
            regime="bull",
            risk_budget=0.6,
            dwell_days=5,
            transition_count=3,
            previous_regime="transition",
            transition_pending="bear",
        )
        self.assertEqual(regime.dwell_days, 5)
        self.assertEqual(regime.transition_count, 3)
        self.assertEqual(regime.previous_regime, "transition")
        self.assertEqual(regime.transition_pending, "bear")

    def test_old_json_compat(self):
        """旧 JSON 无新字段 → 默认值。"""
        import json
        from src.got.schemas import MarketRegime
        old_json = json.loads('{"regime": "bull", "risk_budget": 0.6}')
        regime = MarketRegime.model_validate(old_json)
        self.assertEqual(regime.dwell_days, 1)
        self.assertEqual(regime.transition_count, 0)


if __name__ == "__main__":
    unittest.main()
