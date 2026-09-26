# -*- coding: utf-8 -*-
"""C-P12 prompt A/B 变体文案（批3 前置改造）。

覆盖三块：
1. `_build_glue_prompt(variant=)` 三态：""（现行默认，逐字节不变）/ "a"（保守版=默认+澄清句）/
   "b"（全量归属版：任务1 不得留 unknown + 任务3 全量归属低置信保留 + 防幻觉护栏）。
2. 调用点门控：`GLUE_PROMPT_VARIANT` 仅 replay_mode 生效，生产链路强制现行默认（D-C6）；
   未知取值回退默认。
3. 留痕：`agent_glue.json` 的 `prompt_variant` 字段随实际使用变体落盘（防「以为跑 B 实际跑 A」）。

依据：docs/got/cp12_prompt_variants_draft_20260926.md（负责人 2026-09-26 拍板：
A 保留澄清句；B 纳入任务1；窗口 0901–0910 + 0914）。
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
from unittest.mock import AsyncMock, patch

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)

import src.got.nodes.aggregator as agg_mod  # noqa: E402
from src.got.nodes.aggregator import AggregatorNode  # noqa: E402
from src.got.schemas import SectorDetail  # noqa: E402

TEST_RUN_DATE = date(2026, 9, 10)

# 最小候选（prompt 只用 code/name/role/tier/strength_score 五个字段）
_CANDIDATES = [
    (
        "旅游",
        SectorDetail(code="TD_旅游", name="旅游", state="active_mainline", strength_score=88.0),
        {"role": "mainline", "tier": "A"},
    ),
    (
        "量子计算",
        SectorDetail(code="TD_量子", name="量子计算", state="ambush", strength_score=42.0),
        {"role": "ambush", "tier": None},
    ),
]

# 现行 prompt 的两版共用锚点（任何变体都必须原样保留）
COMMON_ANCHORS = [
    "你是一个 A 股板块语义分析引擎",
    "### 任务2: families — 家族归属（仅对落「其他」的板块）",
    "AI软件 / AI硬件 / 周期资源",
    "只输出 JSON，不要其他内容。",
    '"strengths": [',
    '"relations": [',
]


def _build(variant: str) -> str:
    return AggregatorNode._build_glue_prompt(
        _CANDIDATES, None, None, None, variant=variant,
    )


class TestBuildGluePromptVariants(unittest.TestCase):
    """_build_glue_prompt 三态文案。"""

    def test_default_prompt_unchanged(self):
        """variant="" 与改前现行口径逐锚点一致（生产默认零变更）。"""
        prompt = _build("")
        for anchor in COMMON_ANCHORS:
            self.assertIn(anchor, prompt)
        self.assertIn('- 没有判据的维度必须写 "unknown"（不要写 "neutral"）', prompt)
        self.assertIn("对每条有把握的事件，判断它影响哪个**候选板块**", prompt)
        self.assertIn("- 没把握的事件不要输出（不要编造）\n", prompt + "\n")
        self.assertNotIn("宁缺毋滥", prompt)
        self.assertNotIn("每个维度都必须给出", prompt)

    def test_variant_a_keeps_conservative_and_adds_clarifier(self):
        """a=保守版：任务1 与默认一致，任务3 保留「没把握不输出」并加澄清句。"""
        prompt = _build("a")
        self.assertIn('- 没有判据的维度必须写 "unknown"（不要写 "neutral"）', prompt)
        self.assertIn("只对**有把握**的事件输出归属判断", prompt)
        self.assertIn("宁缺毋滥，省略不是错误", prompt)
        self.assertNotIn("每个维度都必须给出", prompt)

    def test_variant_b_full_attribution(self):
        """b=全量归属版：任务1 禁 unknown、任务3 低置信保留 + 禁止编造护栏。"""
        prompt = _build("b")
        self.assertIn('每个维度都必须给出 high/medium/low 判据，不得留 "unknown"', prompt)
        self.assertIn("每条有传导链的事件都要给出归属", prompt)
        self.assertIn("用低 confidence（<0.5）表达，不要省略", prompt)
        self.assertIn("禁止编造", prompt)
        self.assertIn("放弃量将作为本版纪律指标统计", prompt)
        self.assertNotIn('- 没有判据的维度必须写 "unknown"', prompt)
        self.assertNotIn("没把握的事件不要输出", prompt)

    def test_variants_share_input_and_schema_blocks(self):
        """三版共用区块一致：输入区（含任务1 标题/任务2）与输出格式段不受变体影响
        （单自由度原则：变体只改任务1 unknown 行与任务3 整块）。"""
        tops, bottoms = {}, {}
        for v in ("", "a", "b"):
            prompt = _build(v)
            start = prompt.index("### 任务3")
            end = prompt.index("## 输出格式")
            tops[v] = prompt[:start]
            bottoms[v] = prompt[end:]
        self.assertEqual(tops[""], tops["a"])   # a 的差异仅在任务3 块内 → 前段逐字节不变
        self.assertEqual(bottoms[""], bottoms["a"])
        self.assertEqual(bottoms[""], bottoms["b"])
        # b 版前段差异只允许出现在任务1 unknown 行：任务2 区块仍逐字节共用
        self.assertIn('每个维度都必须给出 high/medium/low 判据，不得留 "unknown"', tops["b"])
        task2_start = tops[""].index("### 任务2")
        b_task2_start = tops["b"].index("### 任务2")
        self.assertEqual(tops[""][task2_start:], tops["b"][b_task2_start:])


class TestPromptVariantGating(unittest.TestCase):
    """调用点门控 + prompt_variant 留痕（env 仅在 replay 生效）。"""

    def setUp(self):
        self.node = AggregatorNode()
        self.original_root = agg_mod.GLUE_ARCHIVE_ROOT
        self.tmp_root = Path(tempfile.mkdtemp(prefix="pvariant_"))
        agg_mod.GLUE_ARCHIVE_ROOT = self.tmp_root
        self.node._run_date = TEST_RUN_DATE
        self._orig_env = os.environ.get("GLUE_PROMPT_VARIANT")

    def tearDown(self):
        agg_mod.GLUE_ARCHIVE_ROOT = self.original_root
        shutil.rmtree(self.tmp_root, ignore_errors=True)
        if self._orig_env is None:
            os.environ.pop("GLUE_PROMPT_VARIANT", None)
        else:
            os.environ["GLUE_PROMPT_VARIANT"] = self._orig_env

    def _run_glue(self, env_value: str, replay: bool):
        os.environ["GLUE_PROMPT_VARIANT"] = env_value
        self.node._replay_mode = replay
        captured = {}

        async def fake_llm(self_, prompt, **kwargs):
            # patch.object 替换的是类属性，实例访问时描述器已绑定 self → 需多收一个形参
            captured["prompt"] = prompt
            return ""  # 空响应 → S1_empty 短路，不触真实网络

        with patch.object(agg_mod, "GLUE_PROMPT_VARIANT", env_value.strip().lower()), \
                patch.object(agg_mod, "AGENT_SEMANTIC_GLUE_IN_REPLAY", True), \
                patch.object(type(self.node), "_call_llm_async", fake_llm):
            result = asyncio.run(self.node._agent_semantic_glue(
                _CANDIDATES, None, None, None,
            ))
        return result, captured.get("prompt", "")

    def _day_dir(self) -> Path:
        return self.tmp_root / TEST_RUN_DATE.strftime("%Y%m%d")

    def test_replay_b_uses_full_attribution_and_traces(self):
        """replay + b：prompt 为全量归属版，留痕字段落盘 = "b"（S1 取证文件同样带留痕）。"""
        os.environ["GLUE_REPLAY_VARIANT"] = "variant_b"
        self.addCleanup(os.environ.pop, "GLUE_REPLAY_VARIANT", None)
        result, prompt = self._run_glue("b", replay=True)
        self.assertEqual(result.prompt_variant, "b")
        self.assertIn("每条有传导链的事件都要给出归属", prompt)
        vdir = self._day_dir() / "_replay" / "variant_b"
        # S1_empty 属取证态 → 写 agent_glue_degraded_*.json（Q1 audit 契约），从其中读留痕
        saved = json.loads(next(vdir.glob("agent_glue*.json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["prompt_variant"], "b")

    def test_replay_a_uses_conservative_clarifier(self):
        """replay + a：prompt 含澄清句，留痕 = "a"。"""
        result, prompt = self._run_glue("a", replay=True)
        self.assertEqual(result.prompt_variant, "a")
        self.assertIn("宁缺毋滥，省略不是错误", prompt)

    def test_production_ignores_env(self):
        """非 replay（生产链路）即使设了 b 也强制现行默认，留痕为空。"""
        result, prompt = self._run_glue("b", replay=False)
        self.assertEqual(result.prompt_variant, "")
        self.assertNotIn("每条有传导链的事件都要给出归属", prompt)
        self.assertIn("对每条有把握的事件", prompt)
        saved = json.loads(next(self._day_dir().glob("agent_glue*.json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["prompt_variant"], "")

    def test_unknown_env_falls_back_to_default(self):
        """replay + 未知取值（如 c）→ 回退现行默认，防错跑错文案。"""
        result, prompt = self._run_glue("c", replay=True)
        self.assertEqual(result.prompt_variant, "")
        self.assertIn("对每条有把握的事件", prompt)


if __name__ == "__main__":
    unittest.main()
