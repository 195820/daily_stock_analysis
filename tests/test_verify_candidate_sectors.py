# -*- coding: utf-8 -*-
"""板块身份校验（_verify_candidate_sectors）——全文交 LLM 判「是否蹭概念」

2026-10-23 用户裁决：删除检索后的白名单/同段共现护栏，检索原文（标题+摘要+URL）
整段交 LLM 判定。钉死契约：

- member → 保留标签（证据留痕：首条标题+摘要）
- not_member → 清空标签（sector 维度剔除）
- uncertain / 单行脏输出 → **保留标签**只留痕（证据不足 ≠ 蹭概念）
- LLM 响应级失败 → raise SectorVerifyUnavailable（fail-closed）
- 全部查询零结果 → raise（检索不可用）
- 零候选 → 直接返回，零查询、不 raise
- 601778 型：讲别的票的片段不再被护栏丢弃，而是进入 prompt 由 LLM 判 not_member
- 同日第二次调用命中决策缓存 → 0 查询
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.got.nodes.portfolio_decision import PortfolioDecisionNode, SectorVerifyUnavailable


def _stock(code="600030", name="中信证券", sector="券商", query_board=None):
    return SimpleNamespace(
        code=code, name=name, sector=sector, sector_name=sector,
        sector_available=True, ai_reasoning="",
        sector_query_board=sector if query_board is None else query_board,
    )


def _resp(*items, provider="Bocha"):
    results = [SimpleNamespace(title=t, snippet=s, url=u) for t, s, u in items]
    return SimpleNamespace(results=results, provider=provider, success=bool(results))


class FakeSearch:
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
        PortfolioDecisionNode._sector_verify_dead_offsets = set()
        PortfolioDecisionNode._sector_verify_decision_cache = {}
        self.node = PortfolioDecisionNode()
        self.intel_map = {"券商": {}, "液冷服务器": {}}

    async def _run(self, stocks, responder, adapter, provider_count=2, intel_map=None):
        fake = FakeSearch(responder, provider_count=provider_count)
        with patch("src.search_service.get_search_service", return_value=fake), \
             patch("src.agent.llm_adapter.LLMToolAdapter", return_value=adapter):
            result = await self.node._verify_candidate_sectors(
                stocks, self.intel_map if intel_map is None else intel_map
            )
        return result, fake, adapter

    async def test_member_keeps_tag_and_prompt_carries_full_text(self):
        stock = _stock()
        responder = lambda q, o: _resp(
            ("中信证券 600030 涨停复盘",
             "该股为券商概念热股,当日券商概念上涨2.1%",
             "https://stock.quote.stockstar.com/x.shtml")
        )
        result, fake, adapter = await self._run([stock], responder, _llm(verdicts=["member"]))

        self.assertEqual(result[0].sector, "券商")
        self.assertTrue(result[0].sector_available)
        self.assertTrue(adapter.call_text.called)
        prompt = adapter.call_text.call_args[0][0][0]["content"]
        self.assertIn("券商概念上涨2.1%", prompt, "检索摘要必须整段进 prompt")
        self.assertIn("quote.stockstar.com", prompt, "URL 一并交给 LLM 判来源")
        self.assertEqual(self.node._sector_verify_evidence[0]["sector"], "券商")
        self.assertIn("券商概念", self.node._sector_verify_evidence[0]["quote"])

    async def test_uncertain_keeps_tag(self):
        stock = _stock()
        responder = lambda q, o: _resp(("中信证券行情", "收盘 12.34", "https://quote.eastmoney.com/x"))
        result, _, _ = await self._run([stock], responder, _llm(verdicts=["uncertain"]))

        self.assertEqual(result[0].sector, "券商", "证据不足不得清空")
        self.assertTrue(result[0].sector_available)
        self.assertEqual(self.node._sector_verify_kept_uncertain[0]["reason"], "llm_uncertain_kept")

    async def test_not_member_clears_tag(self):
        stock = _stock()
        responder = lambda q, o: _resp(("某公司深度", "该股与券商无关", "https://x.com/a"))
        result, _, _ = await self._run([stock], responder, _llm(verdicts=["not_member"]))

        self.assertEqual(result[0].sector, "")
        self.assertFalse(result[0].sector_available)
        self.assertEqual(self.node._sector_verify_unverified[0]["reason"], "llm_rejected")

    async def test_invalid_verdict_row_kept_as_uncertain(self):
        stock = _stock()
        responder = lambda q, o: _resp(("标题", "摘要", "https://x.com/a"))
        result, _, _ = await self._run(
            [stock], responder, _llm(content=json.dumps([{"index": 0, "verdict": "bogus"}])),
        )
        self.assertEqual(result[0].sector, "券商", "非法 verdict 按 uncertain 保留")

    async def test_all_queries_empty_raises(self):
        stock = _stock()
        responder = lambda q, o: SimpleNamespace(results=[], provider="None", success=False)
        with self.assertRaises(SectorVerifyUnavailable):
            await self._run([stock], responder, _llm(verdicts=["member"]))

    async def test_llm_response_failure_raises(self):
        stock = _stock()
        responder = lambda q, o: _resp(("标题", "摘要", "https://x.com/a"))
        with self.assertRaises(SectorVerifyUnavailable):
            await self._run([stock], responder, _llm(raises=True))

    async def test_zero_candidates_no_queries(self):
        fake = FakeSearch(lambda q, o: _resp(("t", "s", "u")))
        with patch("src.search_service.get_search_service", return_value=fake):
            result = await self.node._verify_candidate_sectors([], self.intel_map)
        self.assertEqual(result, [])
        self.assertEqual(fake.calls, [])

    async def test_601778_style_other_stock_segment_reaches_llm(self):
        """601778：唯一「军贸」片段讲的是别家票——不再被规则丢弃，交 LLM 判 not_member。"""
        stock = _stock(code="601778", name="晶科科技", sector="券商", query_board="券商")
        responder = lambda q, o: _resp(
            ("中信证券研报", "晶品特装(688084)智能化编组核心供应商；军贸+内装双轮驱动",
             "https://news.example.com/a"),
        )
        result, _, adapter = await self._run([stock], responder, _llm(verdicts=["not_member"]))

        prompt = adapter.call_text.call_args[0][0][0]["content"]
        self.assertIn("688084", prompt, "串票片段必须原样进 prompt")
        self.assertEqual(result[0].sector, "")
        self.assertEqual(self.node._sector_verify_unverified[0]["reason"], "llm_rejected")

    async def test_second_call_hits_decision_cache(self):
        stock = _stock()
        responder = lambda q, o: _resp(("标题", "摘要", "https://x.com/a"))
        _, fake1, _ = await self._run([stock], responder, _llm(verdicts=["member"]))
        self.assertGreater(len(fake1.calls), 0)
        _, fake2, adapter2 = await self._run([stock], responder, _llm(verdicts=["member"]))
        self.assertEqual(len(fake2.calls), 0, "同日第二次调用应命中缓存")
        self.assertFalse(adapter2.call_text.called)

    async def test_dead_secondary_offset_disabled_within_process(self):
        stock = _stock()
        responder = lambda q, o: _resp(("行情页", "收盘 12.34", "https://quote.eastmoney.com/x")) \
            if o == 0 else SimpleNamespace(results=[], provider="None", success=False)
        _, fake1, _ = await self._run([stock], responder, _llm(verdicts=["uncertain"]))
        self.assertIn(1, PortfolioDecisionNode._sector_verify_dead_offsets)
        self.assertEqual([o for _, o, _ in fake1.calls if o == 1], [1, 1], "首轮仍会试一次第二顺位")
        # 同进程第二只票：只打 offset=0
        stock2 = _stock(code="600031", name="三一重工", sector="券商")
        _, fake2, _ = await self._run([stock2], responder, _llm(verdicts=["uncertain"]))
        self.assertEqual([o for _, o, _ in fake2.calls], [0, 0])


if __name__ == "__main__":
    unittest.main()
