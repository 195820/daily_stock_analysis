# -*- coding: utf-8 -*-
"""板块身份校验（_verify_candidate_sectors）——web 检索证据 + LLM 判定（2026-10-20）

efinance 跨口径比对退役后，流程变为：
    每票 2 模板 × 2 provider 顺位检索 → 来源白名单 → 同段身份护栏 → 批量 LLM 判 verdict

本文件钉死 10 条契约（含 fail-closed 与"单案例脏输出不废整日"）：
- 证据命中 + member → 保留标签，query 只跑 1 次
- 4 次查询无证据 → insufficient_evidence，且**不调 LLM**
- 结果非空但全在白名单外 → 不 raise，走 insufficient
- LLM not_member / uncertain / 非法枚举 → 清空，不清盘
- LLM 响应级失败 → raise SectorVerifyUnavailable
- 全部查询零结果 → raise（消息含「检索不可用」）
- 零候选 → 直接返回，零查询、不 raise
- 601778 型反例：唯一「军贸」片段讲他票 → 护栏拒绝
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.got.nodes.portfolio_decision import PortfolioDecisionNode, SectorVerifyUnavailable


def _stock(code="600030", name="中信证券", sector="券商"):
    return SimpleNamespace(
        code=code, name=name, sector=sector, sector_name=sector,
        sector_available=True, ai_reasoning="",
    )


def _resp(*items, provider="Bocha"):
    results = [SimpleNamespace(title=t, snippet=s, url=u) for t, s, u in items]
    return SimpleNamespace(results=results, provider=provider, success=bool(results))


class FakeSearch:
    """按 (模板, offset) 返回预置响应；记录调用次数。"""

    def __init__(self, responder, provider_count=2):
        self.calls = []
        self.available_provider_count = provider_count
        self._responder = responder

    def search_generic(self, query, max_results=5, days=365, provider_offset=0,
                       max_providers=2):
        self.calls.append((query, provider_offset, days))
        return self._responder(query, provider_offset)


def _llm(content=None, *, raises=False, verdicts=None):
    adapter = MagicMock()
    if raises:
        adapter.call_text.side_effect = RuntimeError("llm down")
    else:
        payload = content if content is not None else json.dumps(
            [{"index": i, "verdict": v} for i, v in enumerate(verdicts or [])]
        )
        adapter.call_text.return_value = SimpleNamespace(content=payload)
    return adapter


class TestVerifyCandidateSectors(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # 类级共享态（死顺位记忆/决策缓存）在用例间必须清零，否则互相污染
        PortfolioDecisionNode._sector_verify_dead_offsets = set()
        PortfolioDecisionNode._sector_verify_decision_cache = {}
        self.node = PortfolioDecisionNode()
        self.intel_map = {"券商": {}, "参股券商": {}, "液冷服务器": {}}

    async def _run(self, stocks, responder, adapter, provider_count=2):
        fake = FakeSearch(responder, provider_count=provider_count)
        with patch("src.search_service.get_search_service", return_value=fake), \
             patch("src.agent.llm_adapter.LLMToolAdapter", return_value=adapter):
            result = await self.node._verify_candidate_sectors(stocks, self.intel_map)
        return result, fake, adapter

    # ── 1. 证据命中 + member ──
    async def test_evidence_member_keeps_tag_and_queries_once(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("中信证券 600030 涨停复盘", "该股为券商概念热股,当日券商概念上涨2.1%",
             "https://stock.quote.stockstar.com/x.shtml")
        )
        result, fake, adapter = await self._run([stock], responder, _llm(verdicts=["member"]))

        self.assertEqual(result[0].sector, "券商")
        self.assertTrue(result[0].sector_available)
        self.assertEqual(len(fake.calls), 1)                       # 命中即停
        self.assertEqual(fake.calls[0][2], 365)                    # 事实型窗口
        self.assertTrue(adapter.call_text.called)
        self.assertEqual(self.node._sector_verify_evidence[0]["sector"], "券商")
        self.assertIn("券商概念", self.node._sector_verify_evidence[0]["quote"])
        self.assertIsNone(self.node._sector_verify_unverified)

    # ── 2. 无证据 → insufficient，且不调 LLM ──
    async def test_no_evidence_clears_and_skips_llm(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("行情页", "今日收盘价 12.34 元", "https://quote.eastmoney.com/x.html")
        )
        result, fake, adapter = await self._run([stock], responder, _llm(verdicts=["member"]))

        self.assertEqual(result[0].sector, "")
        self.assertFalse(result[0].sector_available)
        self.assertEqual(len(fake.calls), 4)                       # 2 模板 × 2 顺位
        self.assertFalse(adapter.call_text.called)                 # 无证据不进 LLM
        self.assertEqual(self.node._sector_verify_unverified[0]["reason"], "insufficient_evidence")

    # ── 2a. 第二顺位整轮无结果 → 记死顺位，同进程再跑不再打它 ──
    async def test_dead_secondary_offset_disabled_within_process(self):
        stock = _stock()
        # offset=0 有结果（但不过护栏）、offset=1（SearXNG 位）整轮空 → 应记死顺位
        responder = lambda q, o: _resp(
            ("行情页", "今日收盘价 12.34 元", "https://quote.eastmoney.com/x.html")
        ) if o == 0 else SimpleNamespace(results=[], provider="None", success=False)
        # 第 1 轮：2 模板 × 2 顺位 = 4 次（offset=1 全无结果）
        result, fake, adapter = await self._run([stock], responder, _llm())
        self.assertEqual(len(fake.calls), 4)
        self.assertIn(1, PortfolioDecisionNode._sector_verify_dead_offsets)
        # 第 2 轮（新实例、换票 → 绕开决策缓存，只验死顺位收敛）：只打 offset=0，2 次
        fake2 = FakeSearch(responder)
        other = SimpleNamespace(code="000001", name="平安银行", sector="券商",
                                sector_name="券商", sector_available=True, ai_reasoning="")
        node2 = PortfolioDecisionNode()
        with patch("src.search_service.get_search_service", return_value=fake2), \
             patch("src.agent.llm_adapter.LLMToolAdapter", return_value=_llm()):
            await node2._verify_candidate_sectors([other], self.intel_map)
        self.assertEqual(len(fake2.calls), 2)
        self.assertEqual({c[1] for c in fake2.calls}, {0})

    # ── 2b. 决策缓存：同 (symbol, sector) 双跑不二次检索/二次 LLM ──
    async def test_fxg_double_run_reuses_decision_cache(self):
        responder = lambda q, o: _resp(
            ("中信证券 600030 涨停复盘", "该股为券商概念热股,当日券商概念上涨2.1%",
             "https://stock.quote.stockstar.com/x.shtml")
        )
        adapter = _llm(verdicts=["member"])
        result, fake, adapter = await self._run([_stock()], responder, adapter)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(adapter.call_text.call_count, 1)

        # FXG 基线双跑：独立实例、同一票 → 零检索零 LLM，决策复用
        fake2 = FakeSearch(responder)
        adapter2 = _llm(verdicts=["member"])
        node2 = PortfolioDecisionNode()
        with patch("src.search_service.get_search_service", return_value=fake2), \
             patch("src.agent.llm_adapter.LLMToolAdapter", return_value=adapter2):
            result2 = await node2._verify_candidate_sectors([_stock()], self.intel_map)
        self.assertEqual(fake2.calls, [])
        self.assertEqual(adapter2.call_text.call_count, 0)
        self.assertEqual(result2[0].sector, "券商")
        self.assertTrue(result2[0].sector_available)

    # ── 2b. 只有一家可用引擎 → 每票只查 2 次（不对空位干等超时） ──
    async def test_single_provider_halves_queries(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("行情页", "今日收盘价 12.34 元", "https://quote.eastmoney.com/x.html")
        )
        result, fake, adapter = await self._run(
            [stock], responder, _llm(verdicts=["member"]), provider_count=1
        )

        self.assertEqual(len(fake.calls), 2)          # 2 模板 × 1 引擎
        self.assertEqual({c[1] for c in fake.calls}, {0})  # 只跑 offset=0
        self.assertEqual(result[0].sector, "")

    # ── 3. 结果非空但全在白名单外 → 不 raise ──
    async def test_offlist_sources_only_are_insufficient_not_unavailable(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("自媒体稿", "该股为券商概念热股", "https://weixin.sogou.com/x")
        )
        result, fake, adapter = await self._run([stock], responder, _llm(verdicts=["member"]))

        self.assertEqual(result[0].sector, "")
        self.assertFalse(adapter.call_text.called)
        self.assertEqual(self.node._sector_verify_unverified[0]["reason"], "insufficient_evidence")

    # ── 4/5/6. LLM 三态 ──
    async def test_llm_not_member_clears(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("中信证券 600030 涨停复盘", "该股为券商概念热股", "https://stock.quote.stockstar.com/x.shtml")
        )
        result, _, _ = await self._run([stock], responder, _llm(verdicts=["not_member"]))
        self.assertEqual(result[0].sector, "")
        self.assertEqual(self.node._sector_verify_unverified[0]["reason"], "llm_rejected")

    async def test_llm_uncertain_clears_without_raising(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("中信证券 600030 涨停复盘", "该股为券商概念热股", "https://stock.quote.stockstar.com/x.shtml")
        )
        result, _, _ = await self._run([stock], responder, _llm(verdicts=["uncertain"]))
        self.assertEqual(result[0].sector, "")
        self.assertEqual(self.node._sector_verify_unverified[0]["reason"], "llm_uncertain")

    async def test_illegal_verdict_is_treated_as_uncertain(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("中信证券 600030 涨停复盘", "该股为券商概念热股", "https://stock.quote.stockstar.com/x.shtml")
        )
        result, _, _ = await self._run([stock], responder, _llm(verdicts=["maybe"]))
        self.assertEqual(result[0].sector, "")
        self.assertEqual(self.node._sector_verify_unverified[0]["reason"], "llm_uncertain")

    # ── 7. 响应级 LLM 失败 → raise ──
    async def test_llm_exception_raises(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("中信证券 600030 涨停复盘", "该股为券商概念热股", "https://stock.quote.stockstar.com/x.shtml")
        )
        with self.assertRaises(SectorVerifyUnavailable):
            await self._run([stock], responder, _llm(raises=True))
        self.assertTrue(self.node._sector_verify_degraded)

    # ── 8. 全部查询零结果 → raise ──
    async def test_all_queries_empty_raises(self):
        stock = _stock()
        with self.assertRaises(SectorVerifyUnavailable) as ctx:
            await self._run([stock], lambda q, o: _resp(), _llm(verdicts=["member"]))
        self.assertIn("检索不可用", str(ctx.exception))

    # ── 9. 零候选 → 直接返回 ──
    async def test_zero_candidates_returns_early(self):
        stock = _stock(sector="")
        fake = FakeSearch(lambda q, o: _resp())
        with patch("src.search_service.get_search_service", return_value=fake):
            result = await self.node._verify_candidate_sectors([stock], self.intel_map)
        self.assertEqual(result, [stock])
        self.assertEqual(fake.calls, [])

    # ── 10. 部分空不抛错 ──
    async def test_partial_empty_run_does_not_raise(self):
        ok, empty = _stock(code="600030"), _stock(code="600999", name="招商证券")

        def responder(q, o):
            if "600030" in q:
                return _resp(
                    ("中信证券 600030 涨停复盘", "该股为券商概念热股",
                     "https://stock.quote.stockstar.com/x.shtml")
                )
            return _resp()

        result, _, _ = await self._run([ok, empty], responder, _llm(verdicts=["member"]))
        self.assertEqual(result[0].sector, "券商")
        self.assertEqual(result[1].sector, "")
        self.assertEqual(
            [u["reason"] for u in self.node._sector_verify_unverified],
            ["insufficient_evidence"],
        )

    # ── 11. 601778 型反例：他票新闻不算证据 ──
    async def test_other_stock_snippet_is_not_evidence(self):
        stock = _stock(code="601778", name="晶科科技", sector="军贸概念")
        self.intel_map["军贸概念"] = {}
        responder = lambda q, o: _resp(
            ("晶品特装(688084)智能化编组核心供应商;军贸+内装双轮驱动",
             "晶品特装(688084)智能化编组核心供应商;军贸+内装双轮驱动 - 远瞻慧库",
             "https://www.dabanke.com/gupiao-688084.html")
        )
        result, fake, adapter = await self._run([stock], responder, _llm(verdicts=["member"]))

        self.assertEqual(result[0].sector, "")
        self.assertFalse(adapter.call_text.called)
        self.assertEqual(
            self.node._sector_verify_unverified[0]["reason"], "insufficient_evidence"
        )
