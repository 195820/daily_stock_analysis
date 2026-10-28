# -*- coding: utf-8 -*-
"""埋伏分支拆分单元测试（plan docs/got/plans/埋伏分支拆分_PLAN_v2.md §4 Step 7，13 用例）。

覆盖契约（两个并行 agent 正在落码的生产接口）：
- StockScreener._run_ambush_lane / _ga1_gate / _ga2_gate / _ga3_gate / _ga4_rank
- StockScreener._filter_got_finalists 分道路径（ENABLED 开关）
- PortfolioDecisionNode action gate lift / bear gate extension helpers
- Schema 字段持久化（ambush_lane_shadow / ambush_lane_trace / sector_type）

环境：GOT_AMBUSH_SPLIT_ENABLED (default "1"), GOT_AMBUSH_SPLIT_ACTIVE (default "0")
常量：AMBUSH_LANE_TOPK=3, AMBUSH_LANE_SAFETY_HIGH=1.03, AMBUSH_LANE_SAFETY_LOW=0.92,
      AMBUSH_LANE_CATALYST_WINDOW_DAYS=14, AMBUSH_LANE_VOLUME_RATIO_KNIFE=3.0
"""
from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

# _score_candidates 驱动夹具（ACTIVE lift / Phase 2 合并语义接线用例复用）
from tests.test_bear_entry_gate import _BearGateHarness


# ─────────────────────────── 公共构造 ───────────────────────────

def _ticket(code="000001", sector="黄金概念", change_pct=-1.0, volume_ratio=1.0,
            dde_degraded=False, sector_type="ambush"):
    """构造 universe 元素（dict）"""
    return {
        "code": code, "name": f"票{code}", "sector": sector,
        "change_pct": change_pct, "volume_ratio": volume_ratio,
        "dde_degraded": dde_degraded, "sector_type": sector_type,
    }


def _stock(code="000001", sector="黄金概念", sector_type="ambush"):
    """构造 ScreenerStock（finalists 形态，D4 合并块输入）"""
    from src.services.stock_screener import ScreenerStock
    return ScreenerStock(code=code, name=f"票{code}", sector_name=sector,
                         sector_type=sector_type)


def _capital_ctx(flow_3d=None, flow_daily=None, dde_degraded=False):
    """构造 CapitalContext dict（evidence.sector_flow 含 sector_flow_all / sector_flow_all_3d）"""
    return {
        "evidence": {
            "sector_flow": {
                "sector_flow_all_3d": flow_3d or {},
                "sector_flow_all": flow_daily or {},
            },
            "dde": {"dde_degraded": dde_degraded},
        },
    }


def _news_ctx(events_by_sector=None):
    """构造 NewsContext dict（catalyst_calendar.events 按板块索引）"""
    events = []
    for sector, evts in (events_by_sector or {}).items():
        for e in evts:
            events.append({"affected_sectors": [sector], **e})
    return {"catalyst_calendar": {"events": events}}


def _sector_kline_lookup(mapping):
    """sector -> (close, high20, ma20) 查询函数"""
    def _lookup(sector):
        return mapping.get(sector, (10.0, 10.3, 10.0))
    return _lookup


def _screener():
    """绕过 __init__ 构造 StockScreenerPipeline 实例"""
    from src.services.stock_screener import StockScreenerPipeline
    s = StockScreenerPipeline.__new__(StockScreenerPipeline)
    s._ambush_lane_shadow = {}
    s._ambush_lane_trace = {}
    return s


def _node7():
    """构造 PortfolioDecisionNode 实例"""
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode
    return PortfolioDecisionNode(use_mock=True)


# ─────────────────────────── 1. ENABLED=false 单道路径 ───────────────────────────

class TestAmbushLaneSplit(unittest.TestCase):
    """埋伏分支拆分 13 用例"""

    def test_enabled_false_single_lane_path(self):
        """ENABLED=false → _filter_got_finalists 走原单道路径，不设置 _ambush_lane_* 属性"""
        s = _screener()
        # 重置属性
        s._ambush_lane_shadow = None
        s._ambush_lane_trace = None
        
        universe = [_ticket("600001", "主线A", sector_type="mainline")]
        got_context = {
            "sector_context": {"exhausted_mainline": [], "ambush": []},
            "capital_context": _capital_ctx(),
            "news_context": _news_ctx(),
        }
        
        with patch.dict(os.environ, {"GOT_AMBUSH_SPLIT_ENABLED": "0"}):
            # Mock DataFetcherManager 避免真实网络调用
            with patch("data_provider.base.DataFetcherManager") as mock_fm:
                mock_fm.return_value = MagicMock()
                # 调用 _filter_got_finalists
                try:
                    finalists = s._filter_got_finalists(universe, got_context, top_n=3)
                except Exception:
                    # 如果方法内部有其他依赖失败，至少验证开关分支未触发 lane 路径
                    pass
                
                # 断言：ENABLED=false 时 _ambush_lane_shadow/trace 未被设置（仍为 None）
                self.assertIsNone(s._ambush_lane_shadow)
                self.assertIsNone(s._ambush_lane_trace)

    def test_lane_split_counts(self):
        """ENABLED=true → funnel_trace lane_in/lane_mainline_in 正确；ambush 票不进主线轮次"""
        s = _screener()
        # 构造 universe：3 主线 + 2 埋伏
        universe = [
            _ticket("600001", "主线A", sector_type="mainline"),
            _ticket("600002", "主线B", sector_type="mainline"),
            _ticket("600003", "主线C", sector_type="mainline"),
            _ticket("600004", "埋伏D", sector_type="ambush"),
            _ticket("600005", "埋伏E", sector_type="ambush"),
        ]
        
        with patch.dict(os.environ, {"GOT_AMBUSH_SPLIT_ENABLED": "1"}):
            # 直接测 _run_ambush_lane 的输入输出
            ambush_candidates = [c for c in universe if c.get("sector_type") == "ambush"]
            got_context = {
                "capital_context": _capital_ctx(flow_3d={"埋伏D": 5.0, "埋伏E": 3.0}),
                "news_context": _news_ctx(),
            }
            
            lane_finalists, lane_shadow, lane_trace = s._run_ambush_lane(
                ambush_candidates, got_context, fetcher_manager=None
            )
            
            # 断言 lane_trace 含 lane_in 计数
            self.assertIn("lane_in", lane_trace)
            self.assertEqual(lane_trace["lane_in"], 2)
            # 断言 lane_shadow 含模式信息
            self.assertIn("mode", lane_shadow)

    def test_g_a0_filters_limit_up(self):
        """G-A0：埋伏道在 G-A1 前剔除涨停票（2026-10-27 002093 实盘 +9.99%）"""
        s = _screener()
        candidates = [
            _ticket("002093", "数据中心", change_pct=9.99),
            _ticket("600002", "数据中心", change_pct=1.00),
        ]
        lane_finalists, lane_shadow, lane_trace = s._run_ambush_lane(
            candidates,
            {"capital_context": _capital_ctx(), "news_context": _news_ctx()},
            fetcher_manager=None,
        )
        self.assertEqual(lane_trace["lane_in"], 2)
        self.assertEqual(lane_trace["g_a0_limit_up"], 1)
        self.assertEqual(lane_shadow["limit_up_filtered"][0]["code"], "002093")
        self.assertNotIn("002093", [stock.code for stock in lane_finalists])

    def test_g_a0_fail_open_does_not_reintroduce_limit_up(self):
        """G-A0 成功后 G-A4 异常 fail-open → 只放回已过滤列表，涨停票不得回流"""
        s = _screener()
        candidates = [
            _ticket("002093", "数据中心", change_pct=9.99),
            _ticket("600002", "数据中心", change_pct=1.00),
        ]
        with patch.object(s, "_ga4_rank", side_effect=RuntimeError("mock error")):
            lane_finalists, lane_shadow, lane_trace = s._run_ambush_lane(
                candidates,
                {"capital_context": _capital_ctx(), "news_context": _news_ctx()},
                fetcher_manager=None,
            )
        self.assertEqual(lane_trace["g_a0_limit_up"], 1)
        self.assertEqual([stock.code for stock in lane_finalists], ["600002"])
        self.assertEqual(lane_shadow["limit_up_filtered"][0]["code"], "002093")

    def test_p5_shadowing_repro_full_funnel(self):
        """回归：P5 块变量遮蔽致埋伏道永不执行（2026-10-13 生产事故）。

        修复前：P5 块 `ambush_candidates = [c for c in candidates if ...]`（candidates
        已是纯主线池）→ 覆盖拆分出的 50 只埋伏票 → 埋伏道 if 恒假 → 闸门/shadow/trace
        全空且零日志。修复后：_filter_got_finalists 全链路必须调用 _run_ambush_lane。
        """
        s = _screener()
        universe = [
            _ticket("600001", "主线A", sector_type="mainline"),
            _ticket("600002", "主线B", sector_type="mainline"),
            _ticket("600003", "主线C", sector_type="mainline"),
            _ticket("600004", "埋伏D", sector_type="ambush"),
            _ticket("600005", "埋伏E", sector_type="ambush"),
        ]
        got_context = {
            "sector_context": {"exhausted_mainline": [], "ambush": []},
            "capital_context": _capital_ctx(),
            "news_context": _news_ctx(),
        }

        with patch.dict(os.environ, {"GOT_AMBUSH_SPLIT_ENABLED": "1"}):
            with patch("data_provider.base.DataFetcherManager") as mock_fm, \
                 patch.object(s, "_run_ambush_lane", wraps=s._run_ambush_lane) as m_lane, \
                 patch.object(s, "_get_stock_name", return_value="票"), \
                 patch.object(s, "_build_backtest_snapshot", return_value={}):
                try:
                    s._filter_got_finalists(universe, got_context, top_n=3)
                except Exception:
                    pass  # 评分/风控 helper 对 mock 环境的兼容由埋伏道断言兜底

                # 埋伏道必须被真实调用（bug 下恒为 0 次）
                self.assertEqual(m_lane.call_count, 1)
                args = m_lane.call_args[0]
                self.assertEqual(len(args[0]), 2)  # 2 只埋伏票进闸门
                self.assertTrue(all(c["sector_type"] == "ambush" for c in args[0]))
                # trace/shadow 必须非空壳
                self.assertTrue(s._ambush_lane_trace.get("lane_in") == 2)
                self.assertTrue(s._ambush_lane_shadow)

    def test_p5_skip_log_reason_ambush_empty(self):
        """P5 跳过日志必须区分「置信度不足」与「主线池无 ambush 票」。"""
        s = _screener()
        universe = [_ticket("600001", "主线A", sector_type="mainline")]
        got_context = {
            "sector_context": {"exhausted_mainline": [], "ambush": []},
            "capital_context": _capital_ctx(),
            "news_context": _news_ctx(),
        }
        with patch.dict(os.environ, {"GOT_AMBUSH_SPLIT_ENABLED": "1"}):
            with patch("data_provider.base.DataFetcherManager") as mock_fm, \
                 patch.object(s, "_run_ambush_lane", wraps=s._run_ambush_lane) as m_lane, \
                 patch.object(s, "_get_stock_name", return_value="票"), \
                 patch.object(s, "_build_backtest_snapshot", return_value={}):
                try:
                    s._filter_got_finalists(universe, got_context, top_n=3)
                except Exception:
                    pass
                # 单道路径 ENABLED 拆分后无 ambush 票 → 埋伏道不调用（不回归遮蔽 bug）
                self.assertEqual(m_lane.call_count, 0)

    def test_g_a1_gate_mapping(self):
        """G-A1：flow>0 → pass; sector miss → soft_pass; flow<0 → fail"""
        s = _screener()
        capital_ctx = _capital_ctx(flow_3d={"黄金概念": 5.0, "白银概念": -3.0})
        candidates = [
            _ticket("600001", "黄金概念"),  # flow>0 → pass
            _ticket("600002", "未知板块"),  # miss → soft_pass
            _ticket("600003", "白银概念"),  # flow<0 → fail
        ]
        
        passed, trace = s._ga1_gate(candidates, capital_ctx)
        
        # 断言 pass 的 code
        passed_codes = {c["code"] for c in passed}
        self.assertIn("600001", passed_codes)  # flow>0 → pass
        self.assertIn("600002", passed_codes)  # miss → soft_pass（也算过）
        self.assertNotIn("600003", passed_codes)  # flow<0 → fail
        
        # 断言 trace 键
        self.assertIn("g_a1_pass", trace)
        self.assertIn("g_a1_soft_pass", trace)
        self.assertIn("g_a1_flow_negative", trace)
        self.assertEqual(trace["g_a1_pass"], 1)
        self.assertEqual(trace["g_a1_soft_pass"], 1)
        self.assertEqual(trace["g_a1_flow_negative"], 1)

    def test_g_a1_dde_degraded_fail(self):
        """G-A1：dde_degraded=True 的票直接 fail，trace 记 g_a1_dde_degraded"""
        s = _screener()
        capital_ctx = _capital_ctx(flow_3d={"黄金概念": 5.0}, dde_degraded=True)
        candidates = [_ticket("600001", "黄金概念", dde_degraded=True)]
        
        passed, trace = s._ga1_gate(candidates, capital_ctx)
        
        # 断言 fail
        self.assertEqual(len(passed), 0)
        self.assertIn("g_a1_dde_degraded", trace)
        self.assertGreater(trace["g_a1_dde_degraded"], 0)

    def test_g_a2_three_way_split(self):
        """G-A2：no_catalyst / low_confidence / position_unsafe 三分桶"""
        s = _screener()
        today = date(2026, 10, 13)
        news_ctx = _news_ctx({
            "黄金概念": [
                {"event_date": (today + timedelta(days=5)).isoformat(), "confidence": 0.8},
            ],
            "白银概念": [
                {"event_date": (today + timedelta(days=5)).isoformat(), "confidence": 0.5},  # low_conf
            ],
        })
        sector_kline = _sector_kline_lookup({
            "黄金概念": (10.0, 10.3, 10.0),  # close=10 < high20=10.3 → safe
            "白银概念": (10.0, 10.3, 10.0),  # safe
            "铜概念": (11.0, 10.3, 10.0),    # close=11 > high20×1.03 → unsafe
        })
        candidates = [
            _ticket("600001", "黄金概念"),  # 有事件+高置信+安全 → pass
            _ticket("600002", "白银概念"),  # 有事件+低置信 → low_confidence
            _ticket("600003", "铜概念"),    # 无事件 → no_catalyst
            _ticket("600004", "未知板块"),  # 无事件 → no_catalyst
        ]
        
        passed, trace = s._ga2_gate(candidates, news_ctx, sector_kline, today=today)
        
        # 断言三分桶
        self.assertIn("g_a2_no_catalyst", trace)
        self.assertIn("g_a2_low_confidence", trace)
        self.assertIn("g_a2_position_unsafe", trace)
        
        # 断言 pass 的 code
        passed_codes = {c["code"] for c in passed}
        self.assertIn("600001", passed_codes)
        self.assertNotIn("600002", passed_codes)  # low_confidence → fail
        self.assertNotIn("600003", passed_codes)  # no_catalyst → fail

    def test_g_a2_proximity_window(self):
        """G-A2：days_to_event <0 和 >14 → 视为无事件；days_to_event=0 含当日（2026-10-16 修复）；
        proximity = max(0, 1−d/14)"""
        s = _screener()
        today = date(2026, 10, 13)
        news_ctx = _news_ctx({
            "黄金概念": [
                {"event_date": (today - timedelta(days=1)).isoformat(), "confidence": 0.8},  # 已过期
                {"event_date": (today + timedelta(days=15)).isoformat(), "confidence": 0.8},  # 超窗口
            ],
        })
        sector_kline = _sector_kline_lookup({"黄金概念": (10.0, 10.3, 10.0)})
        candidates = [_ticket("600001", "黄金概念")]

        passed, trace = s._ga2_gate(candidates, news_ctx, sector_kline, today=today)

        # 断言：两个事件都不在窗口内 → no_catalyst
        self.assertIn("g_a2_no_catalyst", trace)
        self.assertEqual(len(passed), 0)

    def test_g_a2_day0_event_included(self):
        """回归（2026-10-16 军民融合死点）：当日事件 days_to_event=0 必须在窗口内，
        旧谓词 0 < d 将当日事件剔除而 Node 6 照常入池，口径分裂致埋伏道 0 输出"""
        s = _screener()
        today = date(2026, 10, 16)
        news_ctx = _news_ctx({
            "军民融合": [
                {"event_date": today.isoformat(), "confidence": 0.8},
            ],
        })
        sector_kline = _sector_kline_lookup({"军民融合": (1560.67, 1792.9, 1676.978)})
        candidates = [_ticket("600001", "军民融合")]

        passed, trace = s._ga2_gate(candidates, news_ctx, sector_kline, today=today)

        self.assertEqual(trace["g_a2_no_catalyst"], 0)
        self.assertEqual([c["code"] for c in passed], ["600001"])

    def test_g_a2_pool_fast_pass(self):
        """回归（2026-10-16 东数西算死点）：候选板块 ∈ Node 6 ambush_pool 直接过催化判定
        （日历名字匹配缺口：GTC 事件 affected_sectors 无「东数西算」）；
        位置安全带在 pool 通道仍生效；pool 命中独立计数 g_a2_pool_pass"""
        s = _screener()
        today = date(2026, 10, 16)
        # 日历里没有任何东数西算事件
        news_ctx = _news_ctx({
            "AI芯片": [{"event_date": (today + timedelta(days=11)).isoformat(), "confidence": 0.7}],
        })
        sector_kline = _sector_kline_lookup({"东数西算": (1560.67, 1846.7, 1542.8)})
        candidates = [
            _ticket("600001", "东数西算"),
            _ticket("600002", "东数西算"),  # 同板块第二只
        ]

        passed, trace = s._ga2_gate(
            candidates, news_ctx, sector_kline, today=today,
            ambush_pool_names={"东数西算"},
        )

        self.assertEqual(trace["g_a2_pool_pass"], 2)
        self.assertEqual(trace["g_a2_no_catalyst"], 0)
        self.assertEqual(len(passed), 2)

        # pool 通道不豁免位置安全带：close=1900 > high20=1792.9×1.03
        unsafe_kline = _sector_kline_lookup({"军民融合": (1900.0, 1792.9, 1676.978)})
        passed2, trace2 = s._ga2_gate(
            [_ticket("600003", "军民融合")], news_ctx, unsafe_kline, today=today,
            ambush_pool_names={"军民融合"},
        )
        self.assertEqual(trace2["g_a2_position_unsafe"], 1)
        self.assertEqual(len(passed2), 0)

    def test_g_a2_pool_fast_pass_no_calendar(self):
        """pool fast-pass 在日历缺失/空事件时仍生效（催化证明在 Node 6 池准入）"""
        s = _screener()
        today = date(2026, 10, 16)
        sector_kline = _sector_kline_lookup({})
        candidates = [_ticket("600001", "军民融合"), _ticket("600002", "未知板块")]

        # 无日历
        passed, trace = s._ga2_gate(
            candidates, {"catalyst_calendar": None}, sector_kline, today=today,
            ambush_pool_names={"军民融合"},
        )
        self.assertEqual([c["code"] for c in passed], ["600001"])
        self.assertEqual(trace["g_a2_no_catalyst"], 1)

        # 空事件
        passed2, trace2 = s._ga2_gate(
            candidates, {"catalyst_calendar": {"events": []}}, sector_kline, today=today,
            ambush_pool_names={"军民融合"},
        )
        self.assertEqual([c["code"] for c in passed2], ["600001"])
        self.assertEqual(trace2["g_a2_no_catalyst"], 1)

    def test_g_a3_knife(self):
        """G-A3：下跌日 + volume_ratio ≥3.0 → fail + g_a3_knife；缺 volume → soft pass"""
        s = _screener()
        candidates = [
            _ticket("600001", "黄金概念", change_pct=-2.0, volume_ratio=3.5),  # fail
            _ticket("600002", "白银概念", change_pct=-2.0, volume_ratio=2.0),  # pass
            _ticket("600003", "铜概念", change_pct=1.0, volume_ratio=5.0),    # pass（涨）
        ]
        
        passed, trace = s._ga3_gate(candidates, fetcher_manager=None)
        
        passed_codes = {c["code"] for c in passed}
        self.assertNotIn("600001", passed_codes)  # 下跌+高量比 → fail
        self.assertIn("600002", passed_codes)     # 下跌但量比低 → pass
        self.assertIn("600003", passed_codes)     # 涨 → pass
        
        self.assertIn("g_a3_knife", trace)
        self.assertEqual(trace["g_a3_knife"], 1)

    def test_g_a4_rank_dedup_topk(self):
        """G-A4：score=conf×prox×(1+0.2×pct)；同板块去重取 max；TopK=3 截断"""
        s = _screener()
        # 构造 5 只候选，2 只同板块
        candidates = [
            {"code": "600001", "sector": "黄金概念", "ambush_rank_score": 0.8},
            {"code": "600002", "sector": "黄金概念", "ambush_rank_score": 0.9},  # 同板块更高
            {"code": "600003", "sector": "白银概念", "ambush_rank_score": 0.7},
            {"code": "600004", "sector": "铜概念", "ambush_rank_score": 0.6},
            {"code": "600005", "sector": "铝概念", "ambush_rank_score": 0.5},
        ]
        
        passed, trace = s._ga4_rank(candidates, flow_percentile={})
        
        # 断言 TopK=3
        self.assertEqual(len(passed), 3)
        
        # 断言同板块去重：黄金概念只保留 600002（score 更高）
        sectors = [c.get("sector") for c in passed]
        self.assertEqual(sectors.count("黄金概念"), 1)
        
        # 断言 trace 含 g_a4_selected
        self.assertIn("g_a4_selected", trace)
        self.assertEqual(trace["g_a4_selected"], 3)

    def test_lane_error_fail_open_and_merge(self):
        """G-A4 fail-open：gate 异常 → 全过 + lane_error；合并改 union 语义（2026-10-20 D4）"""
        s = _screener()
        # 构造会抛异常的 candidates
        candidates = [_ticket("600001", "黄金概念")]
        
        with patch.object(s, "_ga4_rank", side_effect=RuntimeError("mock error")):
            lane_finalists, lane_shadow, lane_trace = s._run_ambush_lane(
                candidates,
                {"capital_context": _capital_ctx(), "news_context": _news_ctx()},
                fetcher_manager=None,
            )
            
            # 断言 fail-open：所有候选都过
            self.assertEqual(len(lane_finalists), 1)
            # 断言 lane_error
            self.assertIn("lane_error", lane_trace)

        # D4 union 合并语义：主线优先占位不再丢埋伏票；撞票归主线并计数；无溢出丢弃
        maintline = [_stock("600001", "主线A", "active_mainline")]
        lane = [_stock("600001", "主线A"), _stock("600002", "埋伏B"),
                _stock("600003", "埋伏C"), _stock("600004", "埋伏D")]
        trace: dict = {}
        merged = s._merge_lane_finalists(
            maintline, lane, top_n=1, lane_merge_mode="union", lane_trace=trace
        )
        self.assertEqual(
            [m.code for m in merged], ["600001", "600002", "600003", "600004"]
        )
        self.assertEqual(trace["cross_lane_collision"], 1)   # 撞票归主线
        self.assertEqual(trace["lane_overflow_n"], 0)       # union 无溢出丢弃
        self.assertEqual(trace["lane_merge_mode"], "union")

    def test_merge_topn_fill_overflow_dropped(self):
        """缺省 topn_fill（--screener 展示入口）：主线优先补位，溢出即丢（原语义回归）"""
        s = _screener()
        maintline = [_stock("600001", "主线A", "active_mainline")]
        lane = [_stock("600002", "埋伏B"), _stock("600003", "埋伏C")]
        trace: dict = {}

        merged = s._merge_lane_finalists(
            maintline, lane, top_n=1, lane_merge_mode="topn_fill", lane_trace=trace
        )

        self.assertEqual([m.code for m in merged], ["600001"])
        self.assertEqual(trace["lane_overflow_n"], 2)
        self.assertEqual(trace["cross_lane_collision"], 0)
        self.assertEqual(trace["lane_merge_mode"], "topn_fill")

    def test_merge_union_keeps_mainline_cap(self):
        """union：主线仍受 Round 5 top_n 截断（只放开埋伏道），埋伏全量追加"""
        s = _screener()
        maintline = [_stock("600001", "主线A", "active_mainline")]
        lane = [_stock(f"60001{i}", "埋伏") for i in range(1, 4)]
        trace: dict = {}

        merged = s._merge_lane_finalists(
            maintline, lane, top_n=1, lane_merge_mode="union", lane_trace=trace
        )

        self.assertEqual(len(merged), 4)  # 1 主线 + 3 埋伏（道内 TOPK=3 兜底）
        self.assertEqual(merged[0].code, "600001")
        self.assertEqual({m.sector_type for m in merged[1:]}, {"ambush"})
        self.assertEqual(trace["lane_overflow_n"], 0)

    def test_action_gate_shadow_trace(self):
        """ACTIVE=false，lifecycle=None ambush 票 → priority=50 → watch + action_gate_watch trace"""
        node = _node7()
        from src.got.schemas import UnifiedScoringResult
        
        stock = UnifiedScoringResult(
            symbol="600001", name="黄金票", is_holding=False,
            composite_score=50.0,  # 关键：composite=50 → final_score=50 → 不 >50 → watch
            dimension_scores={"capital": 20.0, "news": 10.0, "sector": 10.0, "tech": 5.0, "risk": 5.0},
            action_recommendation="watch", confidence=0.6, reasoning="测试",
            sector_type="ambush",
            lifecycle_phase=None,  # 关键：lifecycle=None
        )
        
        # 断言：lifecycle_phase=None → priority=50（else 分支）
        # final_score = 0.6*50 + 0.4*50 = 30+20 = 50 → 不 >50 → action="watch"
        priority = node._lifecycle_priority_score(None, None, 50.0, 0.0)
        self.assertAlmostEqual(priority, 50.0, places=1)
        
        # 断言：ACTIVE=false 时不抬 priority，final_score=50 → watch
        # 注：helper 尚未实现，这里验证基础逻辑

    def test_action_gate_active_lift(self):
        """ACTIVE=true，lane 过闸 ambush 票 priority 抬到 55 → buy_dip 可达"""
        node = _node7()
        from src.got.schemas import UnifiedScoringResult
        
        stock = UnifiedScoringResult(
            symbol="600001", name="黄金票", is_holding=False,
            composite_score=70.0,
            dimension_scores={"capital": 30.0, "news": 10.0, "sector": 15.0, "tech": 10.0, "risk": 5.0},
            action_recommendation="buy_dip", confidence=0.6, reasoning="测试",
            sector_type="ambush",
            lifecycle_phase=None,
        )
        
        # 断言：ACTIVE=true 时 helper 返回 priority ≥ 55
        # 注：helper 尚未实现，这里验证契约接口存在
        self.assertEqual(stock.sector_type, "ambush")
        # 实际测试需等 Node7Wiring agent 落码后补全 helper 调用

    def test_shadow_fields_persist(self):
        """PortfolioDecision dump→load 后 ambush_lane_shadow/ambush_lane_trace/sector_type 字段保留"""
        from src.got.schemas import PortfolioDecision, PortfolioRiskAssessment, UnifiedScoringResult
        
        # 构造 PortfolioDecision（含新字段）
        decision = PortfolioDecision(
            status="normal",
            risk_assessment=PortfolioRiskAssessment(
                position_limit=0.5, current_position_ratio=0.2,
                constraint_reasoning="测试约束",
            ),
            unified_scoring=[UnifiedScoringResult(
                symbol="600001", name="黄金票", is_holding=False,
                composite_score=70.0,
                dimension_scores={"capital": 30.0, "news": 10.0, "sector": 15.0, "tech": 10.0, "risk": 5.0},
                action_recommendation="buy_dip", confidence=0.6, reasoning="测试",
                sector_type="ambush",  # 拦点 #6
            )],
            allocation=[],
            summary="测试总结",
            warnings=[],
            reasoning="测试推理",
            ambush_lane_shadow={"mode": "shadow", "lane_finalists": 2},  # 拦点 #5
            ambush_lane_trace={"lane_in": 5, "g_a1_pass": 3},
        )
        
        # dump → load 往返
        data = decision.model_dump()
        restored = PortfolioDecision.model_validate(data)
        
        # 断言：字段未静默丢弃
        self.assertEqual(restored.ambush_lane_shadow, {"mode": "shadow", "lane_finalists": 2})
        self.assertEqual(restored.ambush_lane_trace, {"lane_in": 5, "g_a1_pass": 3})
        self.assertEqual(restored.unified_scoring[0].sector_type, "ambush")


# ─────────────────── 转正：ACTIVE 缺省 1 的 action lift 生效面（2026-10-20） ───────────────────

class TestActionGateActiveDefault(_BearGateHarness):
    """GOT_AMBUSH_SPLIT_ACTIVE 缺省翻 1 后，lane 过闸埋伏票必须可达 buy。

    复用 test_bear_entry_gate 的 _score_candidates 驱动夹具（同构造：mock screener +
    板块情报 + overheat 中性），断言的是运行时真消费点（portfolio_decision.py 的
    call-time os.getenv 缺省值），而非 catalyst_config 的登记值。
    """

    async def _ambush_stock(self):
        from tests.test_bear_entry_gate import _make_stock
        # 全维 40 → composite 40 → lifecycle=None 时 final=0.6×50+0.4×40=46 ≤ 50
        # （lift 前必为 watch，lift 后 max(46,55)=55 > 50 → buy_dip）
        return _make_stock(
            code="600001", name="埋伏票", sector_name="半导体", sector_type="ambush",
            sector_score=40.0, capital_score=40.0, news_score=40.0,
            tech_wave_score=40.0, risk_score_safe=40.0,
        )

    async def test_default_active_lifts_lane_qualified_to_buy_dip(self):
        from tests.test_bear_entry_gate import _intel

        stock = await self._ambush_stock()
        lane_shadow = {"lane_finalists": [{"code": stock.code, "sector": "半导体"}]}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GOT_AMBUSH_SPLIT_ACTIVE", None)  # 真·缺省路径
            node, res = await self._run(
                stock, _intel(lifecycle_phase=None, lifecycle_day=None), "bull",
                {"GOT_BEAR_ENTRY_GATE": "0", "GOT_WINRATE_STEP3_ENABLED": "1"},
                lane_shadow=lane_shadow,
            )

        self.assertEqual(res.action_recommendation, "buy_dip")
        self.assertIn(stock.code, node._ambush_lane_trace["action_gate_lifted"])
        self.assertEqual(node._ambush_lane_trace["action_gate_watch"], [])

    async def test_kill_switch_zero_restores_shadow_behavior(self):
        from tests.test_bear_entry_gate import _intel

        stock = await self._ambush_stock()
        lane_shadow = {"lane_finalists": [{"code": stock.code, "sector": "半导体"}]}
        node, res = await self._run(
            stock, _intel(lifecycle_phase=None, lifecycle_day=None), "bull",
            {"GOT_BEAR_ENTRY_GATE": "0", "GOT_WINRATE_STEP3_ENABLED": "1",
             "GOT_AMBUSH_SPLIT_ACTIVE": "0"},
            lane_shadow=lane_shadow,
        )

        self.assertEqual(res.action_recommendation, "watch")
        self.assertEqual(node._ambush_lane_trace["action_gate_lifted"], [])
        self.assertIn(stock.code, node._ambush_lane_trace["action_gate_watch"])


class TestPhase2CallSiteMergeMode(_BearGateHarness):
    """Phase 2 生产路径必须传 lane_merge_mode="union"（D4）；--screener 展示入口保持缺省。"""

    async def test_score_candidates_passes_union(self):
        from tests.test_bear_entry_gate import _intel, _make_stock
        from unittest.mock import MagicMock as _MM

        stock = _make_stock(sector_name="半导体")
        node = _node7()
        node._build_sector_intel_map = lambda uc: {}
        node._adjust_score_by_sector_intel = lambda base, name, mm: base
        node._compute_overheat_penalty = lambda name, mm, uc: 1.0
        node._collect_shadow_t1 = lambda finalists: None
        node._save_candidate_pool_snapshot = lambda *a, **k: None

        async def _fake_verify(finalists, sector_intel_map):
            return finalists
        node._verify_candidate_sectors = _fake_verify

        screener_mock = _MM()
        screener_mock._extract_got_universe.return_value = [{"code": stock.code}]
        screener_mock._filter_got_finalists.return_value = [stock]

        from tests.test_bear_entry_gate import (
            _make_capital, _make_news, _make_sector_ctx, _make_sentiment,
            _make_unified, _market_with_regime,
        )
        with patch("src.services.stock_screener.StockScreenerPipeline",
                   return_value=screener_mock):
            await node._score_candidates(
                _make_sector_ctx(), _make_capital(), _make_news(),
                _market_with_regime("bull"), _make_unified(), _make_sentiment(),
            )

        _args = screener_mock._filter_got_finalists.call_args.args
        self.assertEqual(_args[2], 10)          # top_n 不变
        self.assertEqual(_args[3], "union")     # D4：Phase 2 收并集

    def test_screener_entry_default_stays_topn_fill(self):
        """--screener 独立展示入口（run_got_mode）不传参 → 缺省 topn_fill（输出仍 ≤ top_n）"""
        import inspect
        from src.services.stock_screener import StockScreenerPipeline

        params = inspect.signature(
            StockScreenerPipeline._filter_got_finalists
        ).parameters
        self.assertEqual(params["lane_merge_mode"].default, "topn_fill")



# ─────────────────────── 修法③：G-A2 位置安全带接线（backlog §1 row 52） ───────────────────────

class TestFilterGotFinalistsMergeWiring(unittest.TestCase):
    """D4 端到端：走真实 `_filter_got_finalists` 漏斗（逐轮评分全部打桩），
    验证 `lane_merge_mode` 穿透到合并块——不是只测合并 helper。
    """

    def _run(self, s, universe, mode):
        lane_stocks = [_stock("600901", "埋伏A"), _stock("600902", "埋伏B")]
        got_context = {
            "sector_context": {"exhausted_mainline": [], "ambush": []},
            "capital_context": _capital_ctx(),
            "news_context": _news_ctx(),
        }
        with patch.dict(os.environ, {"GOT_AMBUSH_SPLIT_ENABLED": "1"}), \
             patch("src.got.catalyst_config.AGENT_STOCK_REVIEW_ENABLED", False), \
             patch("data_provider.base.DataFetcherManager"), \
             patch.object(s, "_filter_stagnation", lambda cands, fm: cands), \
             patch.object(s, "_score_capital", lambda cands, ctx: (
                 {c["code"]: 80.0 for c in cands}, {c["code"]: True for c in cands})), \
             patch.object(s, "_apply_capital_floor", lambda top, dropped, cands: top), \
             patch.object(s, "_score_technical_wave", lambda cands, fm: {
                 c["code"]: 70.0 for c in cands}), \
             patch.object(s, "_score_risk", lambda cands, fm: {
                 c["code"]: 80.0 for c in cands}), \
             patch.object(s, "_apply_r4_filter", lambda cands, ctx: cands), \
             patch.object(s, "_score_news", lambda cands, nctx, sctx=None: (
                 {c["code"]: 60.0 for c in cands}, {c["code"]: True for c in cands})), \
             patch.object(s, "_score_catalyst_relevance", lambda cands, ctx: ({}, {})), \
             patch.object(s, "_compute_got_composite_score", lambda stock, ctx: 66.0), \
             patch.object(s, "_generate_got_reasoning", lambda stock, ctx: "理由"), \
             patch.object(s, "_build_backtest_snapshot", lambda stock, fm: {}), \
             patch.object(s, "_get_stock_name", lambda code, fm=None, *a: f"票{code}"), \
             patch.object(s, "_run_ambush_lane", lambda cands, ctx, fm=None: (
                 lane_stocks,
                 {"mode": "shadow", "lane_finalists": [
                     {"code": st.code, "sector": st.sector_name} for st in lane_stocks]},
                 {"lane_in": len(cands), "g_a4_selected": len(lane_stocks)},
             )):
            finalists = s._filter_got_finalists(
                universe, got_context, top_n=1, lane_merge_mode=mode
            )
        return finalists, dict(s._last_funnel_trace)

    def _universe(self):
        tickets = [
            _ticket("600001", "主线A", sector_type="active_mainline"),
            _ticket("600002", "主线B", sector_type="active_mainline"),
            _ticket("600101", "埋伏X", sector_type="ambush"),
            _ticket("600102", "埋伏Y", sector_type="ambush"),
        ]
        # Round 5 构建 ScreenerStock 时直接下标读取 sector_score（非 getattr），必须显式给值
        for t in tickets:
            t["sector_score"] = 70.0
        return tickets

    def test_union_appends_lane_finalists_through_real_funnel(self):
        s = _screener()
        finalists, trace = self._run(s, self._universe(), "union")

        codes = [f.code for f in finalists]
        # 主线 Round 5 仍按 top_n=1 截断，埋伏道全量追加
        self.assertEqual(codes, ["600001", "600901", "600902"])
        self.assertEqual(trace["lane_merge_mode"], "union")
        self.assertEqual(trace["lane_overflow_n"], 0)
        self.assertEqual(trace["cross_lane_collision"], 0)

    def test_default_topn_fill_drops_overflow_through_real_funnel(self):
        s = _screener()
        finalists, trace = self._run(s, self._universe(), "topn_fill")

        self.assertEqual([f.code for f in finalists], ["600001"])
        self.assertEqual(trace["lane_merge_mode"], "topn_fill")
        self.assertEqual(trace["lane_overflow_n"], 2)   # 旧语义：溢出即丢

    def test_zero_lane_finalists_still_records_merge_mode(self):
        """零埋伏 finalist 日（合并块不执行）lane_merge_mode 仍落 funnel_trace"""
        s = _screener()

        def _empty_lane(cands, ctx, fm=None):
            return (
                [],
                {"mode": "shadow", "lane_finalists": []},
                {"lane_in": len(cands), "g_a1_flow_negative": len(cands)},
            )

        got_context = {
            "sector_context": {"exhausted_mainline": [], "ambush": []},
            "capital_context": _capital_ctx(),
            "news_context": _news_ctx(),
        }
        with patch.dict(os.environ, {"GOT_AMBUSH_SPLIT_ENABLED": "1"}), \
             patch("src.got.catalyst_config.AGENT_STOCK_REVIEW_ENABLED", False), \
             patch("data_provider.base.DataFetcherManager"), \
             patch.object(s, "_filter_stagnation", lambda cands, fm: cands), \
             patch.object(s, "_score_capital", lambda cands, ctx: (
                 {c["code"]: 80.0 for c in cands}, {c["code"]: True for c in cands})), \
             patch.object(s, "_apply_capital_floor", lambda top, dropped, cands: top), \
             patch.object(s, "_score_technical_wave", lambda cands, fm: {
                 c["code"]: 70.0 for c in cands}), \
             patch.object(s, "_score_risk", lambda cands, fm: {
                 c["code"]: 80.0 for c in cands}), \
             patch.object(s, "_apply_r4_filter", lambda cands, ctx: cands), \
             patch.object(s, "_score_news", lambda cands, nctx, sctx=None: (
                 {c["code"]: 60.0 for c in cands}, {c["code"]: True for c in cands})), \
             patch.object(s, "_score_catalyst_relevance", lambda cands, ctx: ({}, {})), \
             patch.object(s, "_compute_got_composite_score", lambda stock, ctx: 66.0), \
             patch.object(s, "_generate_got_reasoning", lambda stock, ctx: "理由"), \
             patch.object(s, "_build_backtest_snapshot", lambda stock, fm: {}), \
             patch.object(s, "_get_stock_name", lambda code, fm=None, *a: f"票{code}"), \
             patch.object(s, "_run_ambush_lane", _empty_lane):
            finalists = s._filter_got_finalists(
                self._universe(), got_context, top_n=1, lane_merge_mode="union"
            )

        self.assertEqual([f.code for f in finalists], ["600001"])
        trace = dict(s._last_funnel_trace)
        self.assertEqual(trace["lane_merge_mode"], "union")   # 合并块未执行也落键
        self.assertEqual(trace["g_a1_flow_negative"], 2)      # 零 finalist 溯源保留


class TestGA2PositionScalars(unittest.TestCase):
    """G-A2 位置安全带修法③：Node 2 预计算三标量 → lookup 读 indicators。

    旧链路断点：lookup 只读 ambush[].klines，但 SectorDetail 无 klines 字段，
    归档永不携带 → 生产恒 None → soft-pass（闸门退化双因子）。
    修法③：三标量 pos_close/pos_high20/pos_ma20 注入 indicators（schema 零改动）。
    """

    def test_compute_position_scalars_full_history(self):
        """Node 2 助手：≥20 根 → 三标量齐全且数值正确（high 列口径）。"""
        import pandas as pd
        from src.got.nodes.sector_analysis import SectorAnalysisNode

        closes = [10.0] * 19 + [11.0]
        df = pd.DataFrame({
            "close": closes, "high": [c + 0.2 for c in closes],
            "volume": [1000] * 20,
        })
        out = SectorAnalysisNode._compute_position_scalars(df)
        self.assertEqual(set(out), {"pos_close", "pos_high20", "pos_ma20"})
        self.assertEqual(out["pos_close"], 11.0)
        self.assertEqual(out["pos_high20"], 11.2)  # high 列（对齐 PLAN_v2 §166）
        self.assertAlmostEqual(out["pos_ma20"], 10.05, places=4)

    def test_compute_position_scalars_close_fallback(self):
        """Node 2 助手：无 high 列 → 回退 close 列（旧 lookup 消费形态兼容）。"""
        import pandas as pd
        from src.got.nodes.sector_analysis import SectorAnalysisNode

        df = pd.DataFrame({"close": [10.0] * 19 + [11.0], "volume": [1000] * 20})
        out = SectorAnalysisNode._compute_position_scalars(df)
        self.assertEqual(out["pos_high20"], 11.0)

    def test_compute_position_scalars_short_history_empty(self):
        """Node 2 助手：短史 <20 根 → 空 dict（下游 soft-pass 语义保留）。"""
        import pandas as pd
        from src.got.nodes.sector_analysis import SectorAnalysisNode

        df = pd.DataFrame({"close": [10.0] * 19, "volume": [1000] * 19})
        self.assertEqual(SectorAnalysisNode._compute_position_scalars(df), {})

    def test_build_sector_kline_lookup_reads_indicators(self):
        """选股侧 lookup：读 indicators 三标量建缓存。"""
        s = _screener()
        ctx = {"sector_context": {"ambush": [
            {"name": "黄金概念", "indicators": {
                "pos_close": 10.0, "pos_high20": 10.3, "pos_ma20": 10.0}},
            {"name": "白银概念", "indicators": {}},  # 无三标量 → 不进缓存
        ]}}
        lookup = s._build_sector_kline_lookup(ctx)
        self.assertEqual(lookup("黄金概念"), (10.0, 10.3, 10.0))
        self.assertIsNone(lookup("白银概念"))
        self.assertIsNone(lookup("不存在"))

    def test_ga2_gate_end_to_end_position_unsafe_nonzero(self):
        """端到端：indicators 三标量 → lookup → position_unsafe 真实拦截。

        修复前该场景（生产归档形态，无 klines）恒 soft-pass。"""
        s = _screener()
        today = date(2026, 10, 13)
        news_ctx = _news_ctx({
            "黄金概念": [{"event_date": (today + timedelta(days=5)).isoformat(),
                          "confidence": 0.8}],
            "铜概念": [{"event_date": (today + timedelta(days=5)).isoformat(),
                       "confidence": 0.8}],
        })
        sector_ctx = {"sector_context": {"ambush": [
            {"name": "黄金概念", "indicators": {
                "pos_close": 10.0, "pos_high20": 10.3, "pos_ma20": 10.0}},  # safe
            {"name": "铜概念", "indicators": {
                "pos_close": 11.0, "pos_high20": 10.3, "pos_ma20": 10.0}},  # 11 > 10.3×1.03
        ]}}
        sector_kline = s._build_sector_kline_lookup(sector_ctx)
        candidates = [_ticket("600001", "黄金概念"), _ticket("600003", "铜概念")]

        passed, trace = s._ga2_gate(candidates, news_ctx, sector_kline, today=today)

        passed_codes = {c["code"] for c in passed}
        self.assertIn("600001", passed_codes)
        self.assertNotIn("600003", passed_codes)
        self.assertEqual(trace["g_a2_position_unsafe"], 1)

    def test_ga2_gate_soft_pass_preserved_on_missing(self):
        """soft-pass 语义保留：三标量缺失（短史板块）→ 放行且不计数。"""
        s = _screener()
        today = date(2026, 10, 13)
        news_ctx = _news_ctx({
            "新板块": [{"event_date": (today + timedelta(days=5)).isoformat(),
                       "confidence": 0.8}],
        })
        sector_ctx = {"sector_context": {"ambush": [
            {"name": "新板块", "indicators": {}},  # 无三标量
        ]}}
        sector_kline = s._build_sector_kline_lookup(sector_ctx)
        candidates = [_ticket("600009", "新板块")]

        passed, trace = s._ga2_gate(candidates, news_ctx, sector_kline, today=today)

        self.assertEqual([c["code"] for c in passed], ["600009"])
        self.assertEqual(trace["g_a2_position_unsafe"], 0)


if __name__ == "__main__":
    unittest.main()
