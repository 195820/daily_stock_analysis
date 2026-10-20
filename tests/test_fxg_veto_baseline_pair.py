"""FXG veto 双跑基线捕获（GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW）测试。

覆盖 orchestrator._maybe_capture_fxg_baseline：
- 四条件门：GATE_ENABLED=1 且 EXCLUDE_ENABLED=1 且 EXCLUDE_MODE=veto 且 BASELINE_SHADOW=1 才触发
- 缺省态（任一不满足）→ 单跑、不产 fxg_baseline_scoring.json、不调 _score_candidates
- 基线跑置于 EXCLUDE_ENABLED=0（env 隔离）且跑后复原为 1（不泄漏给真实 run）
- 用独立实例、打桩 _save_candidate_pool_snapshot（不 clobber 正式 veto candidate_pool）
- 快照字段按 UnifiedScoringResult 真实属性映射（sector=sector_name / family=family_name）
- helper 自兜异常：基线 _score_candidates 抛错 → 吞掉、不产文件、绝不 raise（防真实 veto 决策被打成 failed）
- helper 只调一次 _score_candidates（双跑严格限定在 Phase2 无状态选股，Node6 不在此路径）
"""
import os
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.got.schemas import UnifiedScoringResult


def _usr(symbol="600513", name="联环药业", sector_name="创新药", family_name="医药",
         action="buy_trend", score=80.0, is_holding=False):
    return UnifiedScoringResult(
        symbol=symbol, name=name, is_holding=is_holding, composite_score=score,
        dimension_scores={"capital": 80.0, "news": 80.0, "sector": 80.0, "tech": 80.0, "risk": 80.0},
        action_recommendation=action, confidence=0.7, reasoning="x",
        sector_name=sector_name, family_name=family_name,
    )


# 四条件全真（veto 双跑基线开启）
_FULL = {
    "GOT_FAMILY_TOPPING_GATE_ENABLED": "1",
    "GOT_FAMILY_TOPPING_EXCLUDE_ENABLED": "1",
    "GOT_FAMILY_TOPPING_EXCLUDE_MODE": "veto",
    "GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW": "1",
}


class TestMaybeCaptureFxgBaseline(unittest.IsolatedAsyncioTestCase):

    def _orch(self):
        # helper 不依赖实例状态；__new__ 避开 __init__ 里各 node 构造的潜在副作用
        from src.got.orchestrator import GOTOrchestrator
        return GOTOrchestrator.__new__(GOTOrchestrator)

    async def _call(self, env, score_ret=None, side_effect=None, unset=()):
        """跑 helper，返回 (诊断 dict, 是否产 fxg_baseline_scoring.json, 解析后的 record 或 None)。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode

        diag = {"calls": 0}

        async def _fake_score(self_node, sector_ctx, capital_ctx, news_ctx, market_ctx,
                              unified, sentiment_context=None):
            diag["calls"] += 1
            # 基线运行时应处于 EXCLUDE_ENABLED=0（env 隔离）
            diag["exclude_enabled_during"] = os.environ.get("GOT_FAMILY_TOPPING_EXCLUDE_ENABLED")
            # 桩函数应落在实例 __dict__（原方法只在类上，不在实例 dict）→ 判定被 stub
            diag["stub_in_instance_dict"] = callable(
                self_node.__dict__.get("_save_candidate_pool_snapshot", None))
            if side_effect is not None:
                raise side_effect
            return score_ret if score_ret is not None else [_usr()]

        with patch.object(PortfolioDecisionNode, "_score_candidates", _fake_score), \
             patch.dict(os.environ, env):
            # .env 已把 GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW 等置 1 并载入 os.environ，
            # patch.dict 不清空环境 ⇒ "缺省不在 env" 的用例会被 .env 值污染。
            # 显式 pop 才能真实模拟"该键不存在"。
            for _k in unset:
                os.environ.pop(_k, None)
            old = os.getcwd()
            with tempfile.TemporaryDirectory() as d:
                os.chdir(d)
                try:
                    await self._orch()._maybe_capture_fxg_baseline(
                        MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                        MagicMock(), MagicMock(), date(2026, 10, 10))
                    # helper 内部应已把 EXCLUDE_ENABLED 复原回进入前值（=1）
                    diag["exclude_enabled_after"] = os.environ.get("GOT_FAMILY_TOPPING_EXCLUDE_ENABLED")
                    p = Path("data/got_daily/20261010") / "fxg_baseline_scoring.json"
                    produced = p.exists()
                    rec = json.loads(p.read_text(encoding="utf-8")) if produced else None
                finally:
                    os.chdir(old)
        return diag, produced, rec

    # ── 缺省/门控未满足 → 单跑，不触发 ──
    async def test_default_off_no_capture(self):
        """BASELINE_SHADOW 缺省（不在 env）→ 不触发、不产文件、不调 _score_candidates。"""
        env = {k: v for k, v in _FULL.items() if k != "GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW"}
        diag, produced, rec = await self._call(
            env, unset=("GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW",)
        )
        self.assertEqual(diag["calls"], 0)
        self.assertFalse(produced)
        self.assertIsNone(rec)

    async def test_mode_shadow_no_capture(self):
        """EXCLUDE_MODE=shadow（非 veto）→ 不触发。"""
        env = dict(_FULL, GOT_FAMILY_TOPPING_EXCLUDE_MODE="shadow")
        diag, produced, _ = await self._call(env)
        self.assertEqual(diag["calls"], 0)
        self.assertFalse(produced)

    async def test_parent_gate_off_no_capture(self):
        """GATE_ENABLED=0 → 不触发（EXCLUDE_ENABLED 独立子开关不越过父门）。"""
        env = dict(_FULL, GOT_FAMILY_TOPPING_GATE_ENABLED="0")
        diag, produced, _ = await self._call(env)
        self.assertEqual(diag["calls"], 0)
        self.assertFalse(produced)

    # ── 四条件全真 → 捕获 + env 隔离 ──
    async def test_veto_baseline_captures(self):
        diag, produced, rec = await self._call(dict(_FULL))
        self.assertEqual(diag["calls"], 1)  # 只调一次（双跑限定 Phase2）
        self.assertTrue(produced)
        self.assertEqual(rec["line"], "fxg_baseline")
        self.assertEqual(rec["mode"], "baseline_off")
        self.assertEqual(rec["date"], "20261010")

    async def test_env_isolated_to_exclude_off(self):
        """基线跑时 EXCLUDE_ENABLED=0，跑后复原=1（不泄漏给真实 run）。"""
        diag, _, _ = await self._call(dict(_FULL))
        self.assertEqual(diag["exclude_enabled_during"], "0")
        self.assertEqual(diag["exclude_enabled_after"], "1")

    async def test_snapshot_writer_stubbed(self):
        """基线实例的 _save_candidate_pool_snapshot 被打桩（落在实例 __dict__，不 clobber candidate_pool）。"""
        diag, produced, _ = await self._call(dict(_FULL))
        self.assertTrue(diag["stub_in_instance_dict"])

    async def test_snapshot_field_mapping(self):
        """行字段按真实属性映射：sector=sector_name、family=family_name，含 is_holding/action/composite_score。"""
        _, _, rec = await self._call(dict(_FULL), score_ret=[
            _usr(symbol="600000", name="浦发银行", sector_name="半导体", family_name="科技",
                 action="watch", score=66.0, is_holding=True),
        ])
        row = rec["rows"][0]
        self.assertEqual(row["symbol"], "600000")
        self.assertEqual(row["sector"], "半导体")     # ← 源 sector_name
        self.assertEqual(row["family"], "科技")        # ← 源 family_name
        self.assertTrue(row["is_holding"])
        self.assertEqual(row["action_recommendation"], "watch")
        self.assertEqual(row["composite_score"], 66.0)
        # 键名必须是 sector/family（对照脚本按此取键），不是 sector_name/family_name
        self.assertNotIn("sector_name", row)
        self.assertNotIn("family_name", row)

    async def test_empty_scoring_still_writes(self):
        """基线返回空列表 → 仍产文件（rows=[]），使"跑过就有产物"可区分停牌无数据。"""
        _, produced, rec = await self._call(dict(_FULL), score_ret=[])
        self.assertTrue(produced)
        self.assertEqual(rec["rows"], [])

    # ── 异常自兜：纯观测崩溃绝不冒泡、不产文件 ──
    async def test_exception_swallowed_no_raise(self):
        """基线 _score_candidates 抛错 → helper 吞掉（不 raise）、不产文件、env 仍复原=1。"""
        diag, produced, rec = await self._call(dict(_FULL), side_effect=RuntimeError("boom"))
        self.assertFalse(produced)
        self.assertIsNone(rec)
        self.assertEqual(diag.get("exclude_enabled_after"), "1")


async def _passthrough_verify(self_node, finalists, sector_intel_map):
    return finalists


# ────────── 集成冒烟：真实 _score_candidates（仅 mock 问财/校验器）经 helper 跑通 ──────────
class TestFxgBaselineIntegrationSmoke(unittest.IsolatedAsyncioTestCase):
    """不 mock _score_candidates 本身，只 mock 外部 I/O，验证独立实例 standalone 调用真能跑通、
    落 fxg_baseline_scoring.json、不产 candidate_pool、env 隔离并复原。"""

    async def test_helper_drives_real_score_candidates(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        from src.got.orchestrator import GOTOrchestrator
        from tests.test_family_topping_suppression import (
            _make_stock, _make_unified, _make_sector_ctx, _make_capital, _make_news,
            _make_sentiment, _market_with_regime, _intel_fxg, _fs_full,
        )

        stock = _make_stock(code="600000", name="浦发银行", sector_name="半导体")
        screener_mock = MagicMock()
        screener_mock._extract_got_universe.return_value = [{"code": stock.code}]
        screener_mock._filter_got_finalists.return_value = [stock]

        intel = {"创新药": _intel_fxg(), "半导体": _intel_fxg(family="半导体")}
        unified = _make_unified(family_suppression=[_fs_full(members=["创新药"])])
        orch = GOTOrchestrator.__new__(GOTOrchestrator)

        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d, \
             patch.dict(os.environ, _FULL), \
             patch("src.services.stock_screener.StockScreenerPipeline", return_value=screener_mock), \
             patch.object(PortfolioDecisionNode, "_build_sector_intel_map", lambda self, uc: intel), \
             patch.object(PortfolioDecisionNode, "_adjust_score_by_sector_intel", lambda self, b, n, mm: b), \
             patch.object(PortfolioDecisionNode, "_compute_overheat_penalty", lambda self, n, mm, uc: 1.0), \
             patch.object(PortfolioDecisionNode, "_collect_shadow_t1", lambda self, f: None), \
             patch.object(PortfolioDecisionNode, "_verify_candidate_sectors", _passthrough_verify):
            os.chdir(d)
            try:
                await orch._maybe_capture_fxg_baseline(
                    unified, _market_with_regime("transition"), _make_sector_ctx(),
                    _make_capital(), _make_news(), _make_sentiment(), date(2026, 10, 10))
                p = Path("data/got_daily/20261010") / "fxg_baseline_scoring.json"
                self.assertTrue(p.exists())
                rec = json.loads(p.read_text(encoding="utf-8"))
                self.assertEqual(rec["mode"], "baseline_off")
                self.assertGreaterEqual(len(rec["rows"]), 1)
                # 基线实例 bear gate 拦单观测随产物留痕（tombstone 契约：真跑必为 dict）
                self.assertIsInstance(rec["bear_gate"], dict)
                self.assertEqual(rec["bear_gate"]["hits"], 0)
                # 基线不产/不 clobber 正式 candidate_pool.json
                self.assertFalse((p.parent / "candidate_pool.json").exists())
                # 基线跑后 EXCLUDE_ENABLED 已复原为 1（不泄漏给真实 run）
                self.assertEqual(os.environ.get("GOT_FAMILY_TOPPING_EXCLUDE_ENABLED"), "1")
            finally:
                os.chdir(old)


class TestSaveSnapshotAsOf(unittest.TestCase):
    """backlog §7 #12 修法①：候选池快照 + 影子落盘的归档业务日贯通。

    run(as_of=...) 写 self._as_of；两个 _save_* 直读（_score_candidates 内部调用，
    不加参波及桩签名），None 兜底 date.today()＝旧行为逐字节不变。
    复现 20261010 孤儿归档根因的修复面：带外跑历史日不再错落墙钟日目录。
    """

    def test_default_none_falls_back_to_today(self):
        """_as_of=None（未跑 run，如基线实例直调）→ 兜底 date.today()，不炸。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        self.assertIsNone(node._as_of)
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                node._save_candidate_pool_snapshot([], [], {})
                p = Path("data/got_daily") / date.today().strftime("%Y%m%d") / "candidate_pool.json"
                self.assertTrue(p.exists())
                self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["date"],
                                 date.today().strftime("%Y%m%d"))
            finally:
                os.chdir(old)

    def test_explicit_as_of_writes_business_day(self):
        """显式 _as_of=历史日 → 快照落历史日目录、record.date=业务日、墙钟日目录不产。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        node._as_of = date(2026, 10, 10)
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                node._save_candidate_pool_snapshot([], [], {})
                hist = Path("data/got_daily/20261010/candidate_pool.json")
                self.assertTrue(hist.exists())
                rec = json.loads(hist.read_text(encoding="utf-8"))
                self.assertEqual(rec["date"], "20261010")
                self.assertFalse((Path("data/got_daily") /
                                  date.today().strftime("%Y%m%d") / "candidate_pool.json").exists())
            finally:
                os.chdir(old)

    def test_shadow_records_follow_as_of(self):
        """影子五件套同面：_as_of=历史日 → shadow_bear_gate tombstone 落历史日目录。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode(use_mock=True)
        node._as_of = date(2026, 10, 10)
        node._bear_gate_shadow = {"line": "bear_gate", "executed": False,
                                  "node1_regime": "transition", "regime": "transition",
                                  "dip_mode": "veto", "hits": 0, "flagged_stocks": []}
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                node._save_shadow_records()
                hist = Path("data/got_daily/20261010/shadow_bear_gate.json")
                self.assertTrue(hist.exists())
                self.assertEqual(json.loads(hist.read_text(encoding="utf-8"))["hits"], 0)
                self.assertFalse((Path("data/got_daily") /
                                  date.today().strftime("%Y%m%d") / "shadow_bear_gate.json").exists())
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
