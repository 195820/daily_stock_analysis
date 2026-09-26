# -*- coding: utf-8 -*-
"""C-P12 变体目录隔离（批2 前置改造）。

覆盖两块：
1. `AggregatorNode._save_glue_result` 的 `GLUE_REPLAY_VARIANT` 落盘路径隔离——
   A/B prompt 变体回放各落 `_replay/<variant>/`，避免同写 `{date}/_replay/agent_glue.json`
   互相覆盖；仅 replay_mode 生效，生产链路（非 replay）永远不受该变量影响。
2. `scripts/verify_glue_metrics.py` 的 `_replay_sample_inventory(variant=...)` /
   `_discover_replay_variants()` 识别变体目录（R-C6 A/B 对照的读盘前置）。

依据：docs/got/backlog.md §1.C C-P12、计划「第二批前置改造」。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import src.got.nodes.aggregator as agg_mod  # noqa: E402
from src.got.nodes.aggregator import AggregatorNode  # noqa: E402
from src.got.schemas import SemanticGlueResult  # noqa: E402

TEST_RUN_DATE = date(2026, 9, 10)


class TestSaveGlueResultVariant(unittest.TestCase):
    """_save_glue_result 的 GLUE_REPLAY_VARIANT 路径隔离。"""

    def setUp(self):
        self.node = AggregatorNode()
        self.original_root = agg_mod.GLUE_ARCHIVE_ROOT
        self.tmp_root = Path(tempfile.mkdtemp(prefix="glue_variant_"))
        agg_mod.GLUE_ARCHIVE_ROOT = self.tmp_root
        self.node._run_date = TEST_RUN_DATE
        self._orig_variant = os.environ.get("GLUE_REPLAY_VARIANT")

    def tearDown(self):
        agg_mod.GLUE_ARCHIVE_ROOT = self.original_root
        if self._orig_variant is None:
            os.environ.pop("GLUE_REPLAY_VARIANT", None)
        else:
            os.environ["GLUE_REPLAY_VARIANT"] = self._orig_variant
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _day_dir(self) -> Path:
        return self.tmp_root / TEST_RUN_DATE.strftime("%Y%m%d")

    def test_replay_with_variant_writes_variant_subdir(self):
        """replay + GLUE_REPLAY_VARIANT=variant_a → 落 _replay/variant_a/agent_glue.json。"""
        os.environ["GLUE_REPLAY_VARIANT"] = "variant_a"
        self.node._replay_mode = True
        self.node._save_glue_result(SemanticGlueResult(status="ok"))
        variant_path = self._day_dir() / "_replay" / "variant_a" / "agent_glue.json"
        base_path = self._day_dir() / "_replay" / "agent_glue.json"
        self.assertTrue(variant_path.exists(), f"变体产物应落 {variant_path}")
        self.assertFalse(base_path.exists(), "带变体时不应再写基线 _replay/agent_glue.json")

    def test_replay_without_variant_writes_base(self):
        """replay + 未设变体 → 落基线 _replay/agent_glue.json（不改变 C-P11 回填行为）。"""
        os.environ.pop("GLUE_REPLAY_VARIANT", None)
        self.node._replay_mode = True
        self.node._save_glue_result(SemanticGlueResult(status="ok"))
        base_path = self._day_dir() / "_replay" / "agent_glue.json"
        self.assertTrue(base_path.exists(), "无变体时应落基线 _replay 路径")

    def test_non_replay_ignores_variant(self):
        """非 replay（生产链路）即使设了变体也忽略之 → 落 {date}/agent_glue.json，守 A1 契约。"""
        os.environ["GLUE_REPLAY_VARIANT"] = "variant_b"
        self.node._replay_mode = False
        self.node._save_glue_result(SemanticGlueResult(status="ok"))
        prod_path = self._day_dir() / "agent_glue.json"
        self.assertTrue(prod_path.exists(), "生产链路应落当日主文件，不受变体影响")
        self.assertFalse(
            (self._day_dir() / "_replay").exists(),
            "非 replay 不得创建 _replay 目录",
        )


class TestReplayInventoryVariant(unittest.TestCase):
    """verify_glue_metrics 的变体目录识别。"""

    def setUp(self):
        import verify_glue_metrics as vgm

        self.vgm = vgm
        self.tmp_root = Path(tempfile.mkdtemp(prefix="inv_variant_"))
        self._make_day("20260901", {"_replay": "ok", "variant_a": "ok", "variant_b": "ok"})
        self._make_day("20260902", {"_replay": "replay_mode"})  # 占位态
        self._make_day("20260903", {"variant_a": "ok"})
        self._orig_iter = vgm.iter_archive_days
        days = sorted(self.tmp_root.iterdir(), key=lambda p: p.name)
        vgm.iter_archive_days = lambda *a, **k: days  # noqa: E731

    def tearDown(self):
        self.vgm.iter_archive_days = self._orig_iter
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _make_day(self, name: str, spec: dict) -> None:
        """spec: {"_replay": status} 落基线，{"variant_a": status} 落 _replay/variant_a/。"""
        day = self.tmp_root / name
        for key, status in spec.items():
            if key == "_replay":
                target = day / "_replay" / "agent_glue.json"
            else:
                target = day / "_replay" / key / "agent_glue.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"status": status}), encoding="utf-8")

    def test_base_inventory_ignores_variants(self):
        """基线盘点只数 _replay/agent_glue.json，不受变体目录影响（R-C6 旧输出不回归）。"""
        inv = self.vgm._replay_sample_inventory()
        self.assertEqual(inv["variant"], "")
        self.assertEqual(inv["files"], 2, "仅 0901/0902 有基线 _replay 产物")
        self.assertEqual(inv["valid_days"], ["20260901"], "replay_mode 占位不计入")
        self.assertIn("20260902(replay_mode)", inv["placeholders"])

    def test_variant_inventory_reads_variant_dir(self):
        """variant_a 盘点只数 _replay/variant_a/agent_glue.json（0901 + 0903）。"""
        inv = self.vgm._replay_sample_inventory("variant_a")
        self.assertEqual(inv["variant"], "variant_a")
        self.assertEqual(inv["files"], 2)
        self.assertEqual(inv["valid_days"], ["20260901", "20260903"])

    def test_discover_replay_variants(self):
        """_discover_replay_variants 列出实际存在的变体目录名（基线不算）。"""
        self.assertEqual(self.vgm._discover_replay_variants(), ["variant_a", "variant_b"])


if __name__ == "__main__":
    unittest.main()
