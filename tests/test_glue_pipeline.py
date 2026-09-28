# -*- coding: utf-8 -*-
"""C-P13 阶段1：接地式召回-验证管线（L1 检索接地 + L2 逐事件生成 + L3 CoVe 剪枝）。

覆盖五块：
1. L1 纯函数与缓存：`retrieve_topk` 排序/K 截断；`build_l1_index` 缓存命中不重算 embedding。
2. L2 解析护栏：`parse_l2_response` 的证据 span 存在性校验（缺失/编造均丢弃，P7 硬护栏）。
3. L3 CoVe：`parse_verify_response` yes/no/非法判定解析。
4. 护栏：`apply_guards` 白名单强制、去重、CAP 截断、重复循环 day_void（整日作废）与不误伤。
5. 编排与门控：`run_relations_pipeline` 全链路（mock LLM + embedding）；
   `GLUE_PIPELINE_ENABLED` 仅 replay 生效（生产强制关，C-P12 双保险同款）；
   留痕 prompt_variant="p" / pipeline_status；GLUE_REPLAY_VARIANT=pipeline_l1l3 目录隔离 +
   glue_pipeline_trace.json 落盘。

依据：docs/got/cp13_grounding_pipeline_research_20260926.md §3/§5.3；
计划：C-P13 阶段1（L1+L2+L3，仅回放路径，生产链路零影响）。
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import src.got.glue_pipeline as gp  # noqa: E402
import src.got.nodes.aggregator as agg_mod  # noqa: E402
from src.got.nodes.aggregator import AggregatorNode  # noqa: E402
from src.got.schemas import SectorDetail  # noqa: E402

TEST_RUN_DATE = date(2026, 9, 10)

_CANDIDATES = [
    (
        "航运概念",
        SectorDetail(code="TD_航运", name="航运概念", state="active_mainline", strength_score=80.0),
        {"role": "mainline", "tier": "A"},
    ),
    (
        "石油加工",
        SectorDetail(code="TD_石油", name="石油加工", state="active_mainline", strength_score=70.0),
        {"role": "mainline", "tier": "B"},
    ),
    (
        "减肥药",
        SectorDetail(code="TD_减肥", name="减肥药", state="ambush", strength_score=40.0),
        {"role": "ambush", "tier": None},
    ),
    (
        "短剧游戏",
        SectorDetail(code="TD_短剧", name="短剧游戏", state="ambush", strength_score=35.0),
        {"role": "ambush", "tier": None},
    ),
]

_EVENT_OIL = {
    "event_id": "ev_oil",
    "title": "美伊冲突升级，国际油价大涨",
    "sector": "石油",
    "affected_sectors": ["航运概念", "石油加工"],
    "source_news": "美伊冲突导致原油运输风险上升，油运运价大涨，航运公司受益。",
}
_EVENT_LOAN = {
    "event_id": "ev_loan",
    "title": "房贷信贷政策放松",
    "sector": "房地产",
    "affected_sectors": ["房地产"],
    "source_news": "多家银行下调房贷利率，地产链需求有望回暖。",
}
_EVENTS = [_EVENT_OIL, _EVENT_LOAN]


def _fake_vec(code_hint: str) -> list[float]:
    """构造确定性假向量：与 embed 文本关键词挂钩，保证 top-K 可预期。"""
    base = [0.0] * 8
    idx = abs(hash(code_hint)) % 8
    base[idx] = 1.0
    return base


async def _fake_embed_texts(texts):
    """假 embedding：5 维正交基（航运=e0 石油=e1 减肥=e2 短剧=e3 其他=e4）。

    语义分组：油价类事件↔航运/石油；房贷类事件↔减肥药；短剧游戏与两者均无关。
    """
    def basis(i):
        v = [0.0] * 5
        v[i] = 1.0
        return v

    def mix(pairs):
        v = [0.0] * 5
        for i, w in pairs:
            v[i] += w
        return v

    vecs = []
    for t in texts:
        if "航运" in t or "油运" in t or "原油运输" in t:
            vecs.append(basis(0))
        elif "石油" in t or "油价" in t or "原油" in t or "美伊冲突" in t:
            vecs.append(mix([(0, 0.6), (1, 0.8)]))
        elif "减肥" in t or "房贷" in t or "地产" in t:
            vecs.append(basis(2))
        elif "短剧" in t:
            vecs.append(basis(3))
        else:
            vecs.append(basis(4))
    return vecs


def _l2_json(relations, abandoned=None):
    return json.dumps({"relations": relations, "abandoned": abandoned or []}, ensure_ascii=False)


def _l3_json(verdicts):
    return json.dumps({"results": verdicts}, ensure_ascii=False)


class _PipelineNode:
    """最小 node 替身：只提供 _call_llm_async（按 call_type 分发假响应）。"""

    def __init__(self, l2_handler=None, l3_handler=None):
        self.calls = []
        self._l2_handler = l2_handler
        self._l3_handler = l3_handler

    async def _call_llm_async(self, prompt, **kwargs):
        call_type = kwargs.get("call_type", "")
        self.calls.append(call_type)
        if call_type == "got_cp13_l2_generate":
            return self._l2_handler(prompt) if self._l2_handler else ""
        if call_type == "got_cp13_l3_verify":
            return self._l3_handler(prompt) if self._l3_handler else ""
        return ""


class _RaisingL2Node:
    """H-1：_call_llm_async 在 L2 阶段直接抛异常（模拟违反契约），验证 gather 隔离不击穿。"""

    def __init__(self):
        self.calls = []

    async def _call_llm_async(self, prompt, **kwargs):
        call_type = kwargs.get("call_type", "")
        self.calls.append(call_type)
        if call_type == "got_cp13_l2_generate":
            raise RuntimeError("模拟网络崩溃")
        return ""


class _TmpCacheMixin:
    """把 L1 embedding 缓存重定向到临时目录（防污染 data/got_daily/_cache/）。"""

    def _redirect_cache(self):
        self._orig_cache_root = gp.L1_EMBED_CACHE_ROOT
        self._tmp_cache = Path(tempfile.mkdtemp(prefix="cp13_cache_"))
        gp.L1_EMBED_CACHE_ROOT = self._tmp_cache
        self.addCleanup(shutil.rmtree, self._tmp_cache, ignore_errors=True)
        self.addCleanup(setattr, gp, "L1_EMBED_CACHE_ROOT", self._orig_cache_root)


# =============================================================================
# 1. L1 检索接地
# =============================================================================

class TestRetrieveTopK(unittest.TestCase):
    """retrieve_topk 纯函数：cosine 排序 + K 截断。"""

    def test_ordering_by_cosine(self):
        sector_vecs = [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]
        out = gp.retrieve_topk([1.0, 0.0], sector_vecs, ["a", "b", "c"], k=3)
        self.assertEqual([c for c, _ in out], ["a", "c", "b"])
        self.assertAlmostEqual(out[0][1], 1.0, places=3)

    def test_k_truncation(self):
        sector_vecs = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]
        out = gp.retrieve_topk([1.0, 0.0], sector_vecs, ["a", "b", "c"], k=2)
        self.assertEqual(len(out), 2)
        self.assertEqual([c for c, _ in out], ["a", "b"])

    def test_empty_inputs(self):
        self.assertEqual(gp.retrieve_topk([], [[1.0]], ["a"], 3), [])
        self.assertEqual(gp.retrieve_topk([1.0], [], [], 3), [])

    def test_zero_vector_no_crash(self):
        out = gp.retrieve_topk([0.0, 0.0], [[0.0, 0.0], [1.0, 0.0]], ["a", "b"], k=2)
        self.assertEqual(len(out), 2)


class TestBuildL1Index(_TmpCacheMixin, unittest.TestCase):
    """build_l1_index：top-K 候选 + 按日缓存命中不重算。"""

    def test_topk_and_semantic_grouping(self):
        """油价事件 top-K 应含航运/石油；房贷事件 top-K 前端应是减肥药/地产侧（假向量语义）。"""
        self._redirect_cache()
        with patch.object(gp, "embed_texts", _fake_embed_texts):
            index = asyncio.run(gp.build_l1_index(_CANDIDATES, _EVENTS, TEST_RUN_DATE))
        # 8 段文本（4 板块 + 2 事件）中 ev_oil 事件文本含「航运」被假 embed 归入航运基向量，
        # 与「航运概念」板块文本同 key 去重 → 实际唯一 key 6 个
        self.assertEqual(index.cache_misses, 6)
        oil_topk = dict(index.topk_by_event["ev_oil"])
        self.assertIn("TD_航运", oil_topk)
        self.assertIn("TD_石油", oil_topk)
        # 油价事件与航运/石油的 cosine 高于减肥药/短剧
        self.assertGreater(oil_topk["TD_航运"], oil_topk["TD_减肥"])

    def test_cache_hit_no_recompute(self):
        """同日期同文本二次构建：embed_texts 不再被调用（缓存命中）。"""
        self._redirect_cache()
        call_count = {"n": 0}

        async def counting_embed(texts):
            call_count["n"] += 1
            return await _fake_embed_texts(texts)

        with patch.object(gp, "embed_texts", counting_embed):
            asyncio.run(gp.build_l1_index(_CANDIDATES, _EVENTS, TEST_RUN_DATE))
            first_calls = call_count["n"]
            index2 = asyncio.run(gp.build_l1_index(_CANDIDATES, _EVENTS, TEST_RUN_DATE))
        self.assertEqual(call_count["n"], first_calls)  # 第二次零新增调用
        self.assertEqual(index2.cache_misses, 0)
        self.assertGreater(index2.cache_hits, 0)

    def test_no_valid_candidates_raises(self):
        self._redirect_cache()
        bad = [("无代码", None, {"role": "mainline"})]
        with patch.object(gp, "embed_texts", _fake_embed_texts):
            with self.assertRaises(RuntimeError):
                asyncio.run(gp.build_l1_index(bad, _EVENTS, TEST_RUN_DATE))


class TestEmbedTextsUnavailable(unittest.TestCase):
    """embed_texts：未配 key → RuntimeError（上层降级依据）。"""

    def test_missing_api_key_raises(self):
        with patch.object(gp, "GLUE_EMBEDDING_API_KEY", ""):
            with self.assertRaises(RuntimeError):
                asyncio.run(gp.embed_texts(["测试文本"]))

    def test_empty_input_returns_empty(self):
        self.assertEqual(asyncio.run(gp.embed_texts([])), [])


# =============================================================================
# 2. L2 解析 + 证据 span 硬护栏
# =============================================================================

class TestParseJsonLenient(unittest.TestCase):
    """H-1：_parse_json_lenient / _as_object 顶层非对象一律归一为 {}。"""

    def test_top_level_array_normalized(self):
        self.assertEqual(gp._parse_json_lenient('[{"a": 1}]'), {})

    def test_scalar_normalized(self):
        self.assertEqual(gp._parse_json_lenient('"just a string"'), {})
        self.assertEqual(gp._parse_json_lenient('123'), {})

    def test_object_preserved(self):
        self.assertEqual(gp._parse_json_lenient('{"relations": []}'), {"relations": []})

    def test_as_object_helper(self):
        self.assertEqual(gp._as_object([1, 2]), {})
        self.assertEqual(gp._as_object(None), {})
        self.assertEqual(gp._as_object({"k": 1}), {"k": 1})


class TestParseL2Response(unittest.TestCase):
    def test_valid_span_kept(self):
        raw = _l2_json([{
            "board_code": "TD_航运", "strength": 0.8, "direction": "positive",
            "propagation_path": ["原油运输", "油运运价"],
            "evidence_span": "油运运价大涨", "confidence": 0.7,
        }])
        kept, no_span, outside = gp.parse_l2_response(raw, _EVENT_OIL)
        self.assertEqual(len(kept), 1)
        self.assertEqual((no_span, outside), (0, 0))

    def test_missing_span_dropped(self):
        raw = _l2_json([{"board_code": "TD_航运", "strength": 0.8, "evidence_span": ""}])
        kept, no_span, outside = gp.parse_l2_response(raw, _EVENT_OIL)
        self.assertEqual(len(kept), 0)
        self.assertEqual(no_span, 1)

    def test_fabricated_span_dropped(self):
        """span 定位不到原文 → 编造嫌疑，丢弃（P7 Langextract 式存在性校验）。"""
        raw = _l2_json([{
            "board_code": "TD_航运", "strength": 0.8,
            "evidence_span": "油运运价大涨特句不存在",
        }])
        kept, no_span, outside = gp.parse_l2_response(raw, _EVENT_OIL)
        self.assertEqual(len(kept), 0)
        self.assertEqual(outside, 1)

    def test_truncation_rescue(self):
        """截断输出（第二条不完整）仍可抢救出第一条完整对象。"""
        raw = ('```json\n{"relations": [{"board_code": "TD_石油", "strength": 0.6, '
               '"evidence_span": "国际油价大涨"}, {"board_code": "TD_航运", "stre')
        kept, _, _ = gp.parse_l2_response(raw, _EVENT_OIL)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["board_code"], "TD_石油")

    def test_top_level_array_no_raise(self):
        """H-1：L2 输出顶层数组（合法 JSON 但非对象）→ 不抛 AttributeError，按空降级。"""
        kept, no_span, outside = gp.parse_l2_response(
            '[{"board_code": "TD_航运", "evidence_span": "油运运价大涨"}]', _EVENT_OIL)
        self.assertEqual((kept, no_span, outside), ([], 0, 0))

    def test_non_list_relations_field_no_raise(self):
        """H-1：relations 字段不是数组（模型写成对象/字符串）→ 当空，不崩。"""
        raw = json.dumps({"relations": {"board_code": "TD_航运"}}, ensure_ascii=False)
        self.assertEqual(gp.parse_l2_response(raw, _EVENT_OIL), ([], 0, 0))


# =============================================================================
# 3. L3 CoVe 解析
# =============================================================================

class TestParseVerifyResponse(unittest.TestCase):
    def test_yes_no_parsing(self):
        raw = _l3_json([
            {"id": "0", "verdict": "yes", "mechanism": "油价→运价"},
            {"id": "1", "verdict": "no", "mechanism": "无关"},
        ])
        out = gp.parse_verify_response(raw, ["0", "1"])
        self.assertEqual(out["0"].verdict, "yes")
        self.assertEqual(out["1"].verdict, "no")

    def test_unknown_id_ignored(self):
        raw = _l3_json([{"id": "9", "verdict": "yes"}])
        self.assertEqual(gp.parse_verify_response(raw, ["0"]), {})

    def test_unparseable_returns_empty(self):
        self.assertEqual(gp.parse_verify_response("完全不是 JSON", ["0"]), {})

    def test_top_level_array_no_raise(self):
        """H-1：L3 输出顶层数组 → 不崩，返回空 dict（上层保守保留）。"""
        out = gp.parse_verify_response('[{"id": "0", "verdict": "yes"}]', ["0"])
        self.assertEqual(out, {})

    def test_non_list_results_field_no_raise(self):
        raw = json.dumps({"results": {"id": "0"}}, ensure_ascii=False)
        self.assertEqual(gp.parse_verify_response(raw, ["0"]), {})

    def test_prompt_contains_items(self):
        prompt = gp.build_l3_verify_prompt([
            {"id": "0", "event_title": "油价大涨", "board_code": "TD_航运",
             "board_name": "航运概念", "propagation_path": ["油运"], "evidence_span": "油运运价大涨"},
        ])
        self.assertIn("独立的事实核查器", prompt)
        self.assertIn("油运运价大涨", prompt)


# =============================================================================
# 4. 护栏
# =============================================================================

class TestApplyGuards(unittest.TestCase):
    WHITELIST = {"ev_oil": ["TD_航运", "TD_石油"], "ev_loan": ["TD_减肥"]}

    def _rel(self, eid, code, strength=0.5):
        return {"event_id": eid, "board_code": code, "strength": strength}

    def test_outside_topk_dropped(self):
        kept, warnings, void = gp.apply_guards(
            [self._rel("ev_loan", "TD_短剧"), self._rel("ev_oil", "TD_航运")],
            self.WHITELIST, cap=6,
        )
        self.assertFalse(void)
        self.assertEqual([r["board_code"] for r in kept], ["TD_航运"])
        self.assertTrue(any("白名单外" in w for w in warnings))

    def test_unknown_event_dropped(self):
        kept, warnings, void = gp.apply_guards(
            [self._rel("ev_ghost", "TD_航运")], self.WHITELIST, cap=6,
        )
        self.assertEqual(kept, [])
        self.assertFalse(void)
        self.assertTrue(any("event_id 不在管线事件集" in w for w in warnings))

    def test_dedup_keeps_max_strength(self):
        kept, warnings, void = gp.apply_guards(
            [self._rel("ev_oil", "TD_航运", 0.4), self._rel("ev_oil", "TD_航运", 0.9)],
            self.WHITELIST, cap=6,
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["strength"], 0.9)
        self.assertFalse(void)

    def test_cap_truncation_by_strength(self):
        rels = [self._rel("ev_oil", "TD_航运", 0.9), self._rel("ev_oil", "TD_石油", 0.3)]
        kept, warnings, void = gp.apply_guards(rels, self.WHITELIST, cap=1)
        self.assertFalse(void)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["board_code"], "TD_航运")
        self.assertTrue(any("截断" in w for w in warnings))

    def test_day_void_on_repetition_loop(self):
        """0902 式退化循环：单事件去重前产出 > 3×cap → 整日作废。"""
        rels = [self._rel("ev_oil", "TD_航运", 0.5) for _ in range(7)]
        kept, warnings, void = gp.apply_guards(rels, self.WHITELIST, cap=2)
        self.assertTrue(void)
        self.assertEqual(kept, [])
        self.assertTrue(any("整日作废" in w for w in warnings))

    def test_healthy_ratio_above_threshold_no_void(self):
        """去重前样本足够且坠缩不严重（比值 > 0.5）→ 不误伤（H-2 反例）。"""
        wl = {"ev_oil": ["A", "B", "C", "D", "E", "F", "G"]}
        rels = [self._rel("ev_oil", c) for c in ["A", "B", "C", "D", "E", "F"]]
        rels.append(self._rel("ev_oil", "A"))  # 1 条重复 → before=7, after=6, 比值0.86
        kept, warnings, void = gp.apply_guards(rels, wl, cap=6)
        self.assertFalse(void)
        self.assertEqual(len(kept), 6)

    def test_below_ratio_floor_no_false_void(self):
        """去重前样本量未达下限（<6）→ 不因小比例误触 day_void。"""
        # 3 同一条 → before=3 (<_DAY_VOID_MIN_BEFORE) → 跳过比值护栏，仅去重
        rels = [self._rel("ev_oil", "TD_航运") for _ in range(3)]
        kept, warnings, void = gp.apply_guards(rels, self.WHITELIST, cap=6)
        self.assertFalse(void)
        self.assertEqual(len(kept), 1)

    def test_day_void_on_full_day_collapse(self):
        """H-2 护栏④b：多事件各挂重复，单事件到不了 3×cap，但全日去重坠缩比<0.5 → 整日作废。"""
        rels = []
        for _ in range(3):
            rels.append(self._rel("ev_oil", "TD_航运"))
            rels.append(self._rel("ev_loan", "TD_减肥"))
        # before=6, after=2, 比值0.33<0.5；单事件各 3 条不超 3×cap(cap=6→18)不触发④a
        kept, warnings, void = gp.apply_guards(rels, self.WHITELIST, cap=6)
        self.assertTrue(void)
        self.assertEqual(kept, [])
        self.assertTrue(any("④b" in w and "整日作废" in w for w in warnings))


# =============================================================================
# 5. 编排：run_relations_pipeline 全链路（mock）
# =============================================================================

class TestRunRelationsPipeline(_TmpCacheMixin, unittest.TestCase):
    def _run(self, node, top_k=3, cap=6):
        self._redirect_cache()
        with patch.object(gp, "embed_texts", _fake_embed_texts):
            return asyncio.run(gp.run_relations_pipeline(
                node, _CANDIDATES, _EVENTS, TEST_RUN_DATE, top_k=top_k, cap=cap,
            ))

    def test_full_chain_ok(self):
        """L2 合法产出保留；白名单外/编造 span/L3 no 均被剪掉。"""
        def l2_handler(prompt):
            if "美伊冲突" in prompt:
                return _l2_json([
                    {"board_code": "TD_航运", "strength": 0.9, "direction": "positive",
                     "propagation_path": ["原油运输", "油运运价"],
                     "evidence_span": "油运运价大涨", "confidence": 0.8},
                    {"board_code": "TD_石油", "strength": 0.7, "direction": "positive",
                     "propagation_path": ["原油", "炼化"],
                     "evidence_span": "国际油价大涨", "confidence": 0.6},
                    # 编造 span → L2 解析层丢弃
                    {"board_code": "TD_短剧", "strength": 0.5,
                     "evidence_span": "完全不存在的证据", "confidence": 0.5},
                ])
            return _l2_json([
                # 白名单外（房贷事件 top-K 不含短剧，假向量语义决定）→ 护栏① 丢弃
                {"board_code": "TD_短剧", "strength": 0.6,
                 "evidence_span": "房贷利率", "confidence": 0.5},
            ])

        def l3_handler(prompt):
            if "油运运价" in prompt:
                return _l3_json([
                    {"id": "0", "verdict": "yes", "mechanism": "运价上升直接受益"},
                    {"id": "1", "verdict": "no", "mechanism": "炼化利润被压缩"},
                ])
            return _l3_json([])

        node = _PipelineNode(l2_handler, l3_handler)
        result = self._run(node)
        self.assertEqual(result.status, "ok")
        codes = [r["board_code"] for r in result.relations]
        self.assertEqual(codes, ["TD_航运"])
        self.assertEqual(result.relations[0]["event_id"], "ev_oil")
        # evidence_span 是内部字段，不进 GlueRelation 同构输出
        self.assertNotIn("evidence_span", result.relations[0])
        self.assertEqual(result.trace["summary"]["cove_dropped"], 1)
        self.assertIn("got_cp13_l2_generate", node.calls)
        self.assertIn("got_cp13_l3_verify", node.calls)

    def test_l3_failure_keeps_conservatively(self):
        """L3 验证不可解析 → 保守保留 + warning（不误杀召回）。"""
        def l2_handler(prompt):
            if "美伊冲突" in prompt:
                return _l2_json([{
                    "board_code": "TD_航运", "strength": 0.9,
                    "evidence_span": "油运运价大涨", "confidence": 0.8,
                }])
            return _l2_json([])

        node = _PipelineNode(l2_handler, lambda prompt: "坏响应")
        result = self._run(node)
        self.assertEqual(result.status, "ok")
        self.assertEqual([r["board_code"] for r in result.relations], ["TD_航运"])
        self.assertTrue(any("保守保留" in w for w in result.warnings))

    def test_embedding_failure_degrades(self):
        """embedding 不可用 → status=embedding_unavailable，relations 空（base 保留由上层负责）。"""
        self._redirect_cache()

        async def broken_embed(texts):
            raise RuntimeError("GLUE_EMBEDDING_API_KEY 未配置")

        node = _PipelineNode()
        with patch.object(gp, "embed_texts", broken_embed):
            result = asyncio.run(gp.run_relations_pipeline(
                node, _CANDIDATES, _EVENTS, TEST_RUN_DATE,
            ))
        self.assertEqual(result.status, "embedding_unavailable")
        self.assertEqual(result.relations, [])
        self.assertEqual(node.calls, [])  # 未发生任何 LLM 调用

    def test_all_l2_failed_degrades(self):
        node = _PipelineNode(lambda prompt: "", lambda prompt: "")
        result = self._run(node)
        self.assertEqual(result.status, "l2_failed")

    def test_l2_partial_marks_status(self):
        """H-3：部分事件 L2 失败（空响应）→ status=l2_partial，relations 置空保留 base + warning。"""
        def l2_handler(prompt):
            if "美伊冲突" in prompt:
                return _l2_json([{"board_code": "TD_航运", "strength": 0.9,
                                  "evidence_span": "油运运价大涨", "confidence": 0.8}])
            return ""  # ev_loan 空响应 → 该事件 error

        node = _PipelineNode(l2_handler, lambda p: _l3_json([{"id": "0", "verdict": "yes"}]))
        result = self._run(node)
        self.assertEqual(result.status, "l2_partial")
        self.assertEqual(result.relations, [])  # 残缺产出不当效果读入
        self.assertTrue(any("部分失败" in w for w in result.warnings))
        self.assertEqual(result.trace["summary"]["l2_error_events"], 1)

    def test_l2_call_exception_all_isolated_l2_failed(self):
        """H-1：_l2_one 抛异常被 gather(return_exceptions) 隔离 → 全失败 l2_failed，不击穿。"""
        self._redirect_cache()
        node = _RaisingL2Node()
        with patch.object(gp, "embed_texts", _fake_embed_texts):
            result = asyncio.run(gp.run_relations_pipeline(
                node, _CANDIDATES, _EVENTS, TEST_RUN_DATE, top_k=3, cap=6,
            ))
        self.assertEqual(result.status, "l2_failed")
        self.assertEqual(result.relations, [])

    def test_unexpected_exception_degrades_pipeline_error(self):
        """H-1：护栏段未预期异常 → 外层 run_relations_pipeline 兑底 pipeline_error，不抛出。"""
        self._redirect_cache()

        def l2_handler(prompt):
            if "美伊冲突" in prompt:
                return _l2_json([{"board_code": "TD_航运", "strength": 0.9,
                                  "evidence_span": "油运运价大涨", "confidence": 0.8}])
            return _l2_json([])

        node = _PipelineNode(l2_handler, lambda p: _l3_json([{"id": "0", "verdict": "yes"}]))
        with patch.object(gp, "embed_texts", _fake_embed_texts), \
                patch.object(gp, "apply_guards", side_effect=RuntimeError("护栏崩")):
            result = asyncio.run(gp.run_relations_pipeline(
                node, _CANDIDATES, _EVENTS, TEST_RUN_DATE, top_k=3, cap=6,
            ))
        self.assertEqual(result.status, "pipeline_error")
        self.assertEqual(result.relations, [])

    def test_day_void_on_degenerate_loop(self):
        """L2 单事件狂吐重复条目（>3×cap）→ day_void，relations 置空。"""
        def l2_handler(prompt):
            if "美伊冲突" in prompt:
                return _l2_json([
                    {"board_code": "TD_航运", "strength": 0.5,
                     "evidence_span": "油运运价大涨", "confidence": 0.5}
                    for _ in range(8)
                ])
            return _l2_json([])

        node = _PipelineNode(l2_handler, lambda prompt: _l3_json(
            [{"id": str(i), "verdict": "yes"} for i in range(8)]
        ))
        result = self._run(node, cap=2)
        self.assertEqual(result.status, "day_void")
        self.assertEqual(result.relations, [])
        self.assertTrue(result.trace["summary"]["day_void"])

    def test_no_events_ok_empty(self):
        self._redirect_cache()
        node = _PipelineNode()
        with patch.object(gp, "embed_texts", _fake_embed_texts):
            result = asyncio.run(gp.run_relations_pipeline(
                node, _CANDIDATES, [], TEST_RUN_DATE,
            ))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.relations, [])


# =============================================================================
# 6. aggregator 门控 + 留痕 + 落盘隔离
# =============================================================================

def _base_glue_json():
    """base 全量调用的最小合法响应（status=ok 才会进管线分支）。"""
    return json.dumps({
        "strengths": [
            {"board_code": code, "strength": 0.5, "confidence": 0.5,
             "evidence": "e", "capital_read": "medium", "news_read": "medium",
             "sentiment_read": "medium"}
            for _, detail, _ in _CANDIDATES for code in [detail.code]
        ],
        "families": [],
        "relations": [{"event_id": "ev_base", "board_code": "TD_短剧",
                       "strength": 0.5, "direction": "positive",
                       "propagation_path": [], "confidence": 0.5}],
    }, ensure_ascii=False)


def _fake_news():
    return SimpleNamespace(
        catalyst_calendar=SimpleNamespace(events=[
            SimpleNamespace(
                event_id="ev_oil", event_date=TEST_RUN_DATE, event_type="geopolitics",
                title="美伊冲突升级，国际油价大涨", sector="石油", impact_level="high",
                direction="up", confidence=0.9, source="news",
                affected_sectors=["航运概念"], source_news=_EVENT_OIL["source_news"],
            ),
        ]),
        major_events=[],
    )


class TestAggregatorPipelineGating(unittest.TestCase):
    """GLUE_PIPELINE_ENABLED 门控（仅 replay）+ 留痕 + pipeline_l1l3 目录隔离。"""

    def setUp(self):
        self.node = AggregatorNode()
        self.original_root = agg_mod.GLUE_ARCHIVE_ROOT
        self.tmp_root = Path(tempfile.mkdtemp(prefix="cp13_gate_"))
        agg_mod.GLUE_ARCHIVE_ROOT = self.tmp_root
        self.node._run_date = TEST_RUN_DATE
        self._orig_variant = os.environ.get("GLUE_REPLAY_VARIANT")

    def tearDown(self):
        agg_mod.GLUE_ARCHIVE_ROOT = self.original_root
        shutil.rmtree(self.tmp_root, ignore_errors=True)
        if self._orig_variant is None:
            os.environ.pop("GLUE_REPLAY_VARIANT", None)
        else:
            os.environ["GLUE_REPLAY_VARIANT"] = self._orig_variant

    def _run_glue(self, replay: bool, pipeline_result: gp.PipelineResult):
        self.node._replay_mode = replay
        captured = {}

        async def fake_llm(self_, prompt, **kwargs):
            captured.setdefault("prompts", []).append(prompt)
            return _base_glue_json()

        pipeline_calls = []

        async def fake_pipeline(node, candidates, events, run_date, **kwargs):
            pipeline_calls.append((candidates, events, run_date))
            return pipeline_result

        with patch.object(agg_mod, "GLUE_PIPELINE_ENABLED", True), \
                patch.object(agg_mod, "AGENT_SEMANTIC_GLUE_IN_REPLAY", True), \
                patch.object(agg_mod, "GLUE_PROMPT_VARIANT", ""), \
                patch.object(agg_mod._glue_pipeline_mod, "run_relations_pipeline", fake_pipeline), \
                patch.object(type(self.node), "_call_llm_async", fake_llm):
            result = asyncio.run(self.node._agent_semantic_glue(
                _CANDIDATES, _fake_news(), None, None,
            ))
        return result, captured.get("prompts", []), pipeline_calls

    def _pipe_ok(self):
        return gp.PipelineResult(
            relations=[{"event_id": "ev_oil", "board_code": "TD_航运", "strength": 0.9,
                        "direction": "positive", "propagation_path": ["油运"], "confidence": 0.8}],
            trace={"summary": {"final_relations": 1}},
            status="ok",
        )

    def test_replay_pipeline_replaces_relations_and_traces(self):
        os.environ["GLUE_REPLAY_VARIANT"] = "pipeline_l1l3"
        result, prompts, calls = self._run_glue(replay=True, pipeline_result=self._pipe_ok())
        # 门控生效：管线被调用，事件含 catalyst 事件
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1][0]["event_id"], "ev_oil")
        # relations 被管线替换（base 的 TD_短剧 不在）
        self.assertEqual([r.board_code for r in result.relations], ["TD_航运"])
        # 留痕
        self.assertEqual(result.prompt_variant, "p")
        self.assertEqual(result.pipeline_status, "ok")
        # base prompt 用现行默认文案（不含 a/b 变体关键词）
        self.assertNotIn("每条有传导链的事件都要给出归属", prompts[0])
        self.assertNotIn("宁缺毋滥", prompts[0])
        # 落盘隔离：_replay/pipeline_l1l3/{agent_glue.json, glue_pipeline_trace.json}
        vdir = self.tmp_root / "20260910" / "_replay" / "pipeline_l1l3"
        saved = json.loads((vdir / "agent_glue.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["prompt_variant"], "p")
        self.assertEqual(saved["pipeline_status"], "ok")
        trace = json.loads((vdir / "glue_pipeline_trace.json").read_text(encoding="utf-8"))
        self.assertEqual(trace["pipeline_status"], "ok")
        # 生产归档根目录不被污染
        self.assertFalse((self.tmp_root / "20260910" / "agent_glue.json").exists())

    def test_production_ignores_pipeline(self):
        """非 replay 即使 GLUE_PIPELINE_ENABLED=true：不调管线、留痕为空、行为同现行默认。"""
        result, prompts, calls = self._run_glue(replay=False, pipeline_result=self._pipe_ok())
        self.assertEqual(calls, [])
        self.assertEqual(result.prompt_variant, "")
        self.assertEqual(result.pipeline_status, "")
        self.assertEqual([r.board_code for r in result.relations], ["TD_短剧"])  # base 保留
        # 无 trace 落盘；生产路径 {date}/agent_glue.json 正常
        day_dir = self.tmp_root / "20260910"
        self.assertTrue((day_dir / "agent_glue.json").exists())
        self.assertFalse((day_dir / "glue_pipeline_trace.json").exists())

    def test_pipeline_degrade_keeps_base_relations(self):
        """管线 embedding_unavailable → base relations 保留 + pipeline_status 留痕 + warning。"""
        degraded = gp.PipelineResult(
            relations=[], trace={"l1_error": "no key"},
            status="embedding_unavailable", warnings=["L1 embedding 不可用"],
        )
        os.environ["GLUE_REPLAY_VARIANT"] = "pipeline_l1l3"
        result, _, _ = self._run_glue(replay=True, pipeline_result=degraded)
        self.assertEqual(result.pipeline_status, "embedding_unavailable")
        self.assertEqual([r.board_code for r in result.relations], ["TD_短剧"])
        self.assertTrue(any("保留 base" in w for w in result.warnings))
        vdir = self.tmp_root / "20260910" / "_replay" / "pipeline_l1l3"
        self.assertTrue((vdir / "glue_pipeline_trace.json").exists())

    def test_pipeline_takes_precedence_over_ab_variant(self):
        """GLUE_PROMPT_VARIANT=b 与管线同开 → 管线优先（variant=p，base 用默认文案）。"""
        os.environ["GLUE_REPLAY_VARIANT"] = "pipeline_l1l3"
        with patch.object(agg_mod, "GLUE_PROMPT_VARIANT", "b"):
            result, prompts, calls = self._run_glue(replay=True, pipeline_result=self._pipe_ok())
        self.assertEqual(result.prompt_variant, "p")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("每条有传导链的事件都要给出归属", prompts[0])


class TestCollectPipelineEvents(unittest.TestCase):
    def test_catalyst_and_major_events(self):
        node = AggregatorNode()
        news = SimpleNamespace(
            catalyst_calendar=SimpleNamespace(events=[
                SimpleNamespace(
                    event_id="ev1", event_date=TEST_RUN_DATE, event_type="policy",
                    title="t", sector="s", impact_level="high", direction="up",
                    confidence=0.9, source="news", affected_sectors=["航运概念"],
                    source_news="原文",
                ),
            ]),
            major_events=[
                SimpleNamespace(event_type="geopolitics", title="重大事件",
                                impact_level="high", sentiment="negative",
                                affected_sectors=["石油加工"], lifecycle_state=None),
            ],
        )
        events = node._collect_pipeline_events(news)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_id"], "ev1")
        self.assertTrue(events[1]["event_id"].startswith("major_0_"))
        self.assertEqual(events[1]["affected_sectors"], ["石油加工"])

    def test_none_news_safe(self):
        self.assertEqual(AggregatorNode()._collect_pipeline_events(None), [])


if __name__ == "__main__":
    unittest.main()
