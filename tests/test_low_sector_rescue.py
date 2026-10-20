# -*- coding: utf-8 -*-
"""
Node 4 单元测试：Q1 low 事件板块级救援（2026-10-16 补登 #6，用户裁决直连）。

覆盖（T1-T13）：
1. 开关关闭 → disabled 占位影子 + 不调 LLM + 零升级（回滚语义）
2. 正常升级：sector_impact=high → medium 原地升级（全字段保留）
3. 无复审候选 → 有效样本落盘（review_candidates=0）
4. LLM 空响应 → failure 影子落盘 + 降级不升级
5. 解析失败（非 JSON）→ 同 T4
6. 解析器剥 ``` 围栏
7. 解析器非数组输出 raise
8. 解析器空响应 raise
9. 非法 verdict 丢弃（index 越界 / sector_impact 非法值）
10. 升级事件在 run() 过滤行存活（impact_level != "low" 保留）
11. disabled 占位键完整
12. 落盘 OSError → 不 raise，升级结果照常返回（留痕失败≠救援失败）
13. 影子 events[] 含未送审行（无映射事件 reason 标注）
"""

import asyncio
import json
import os
import shutil
import sys
import unittest
from datetime import date as real_date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.got.nodes.news_analysis as na


class _FixedDate(real_date):
    """固定 today 的 date 替身（隔离 data/got_daily 写入到 20000101 测试沙箱日）。"""

    @classmethod
    def today(cls):
        return cls(2000, 1, 1)


_TEST_DAY_DIR = Path("data/got_daily/20000101")
_SHADOW_FILE = _TEST_DAY_DIR / "shadow_low_events.json"


def _make_event(title, impact="low", sectors=None, sentiment="positive"):
    from src.got.schemas import NewsEvent
    return NewsEvent(
        event_type="policy",
        title=title,
        summary="摘要内容",
        impact_level=impact,
        sentiment=sentiment,
        affected_sectors=sectors or [],
    )


def _fake_llm(verdicts):
    """构造 _call_llm 替身（签名对齐 (prompt, *, temperature, max_tokens, max_retries)）。"""
    def _impl(prompt, *, temperature=0.0, max_tokens=0, max_retries=3):
        return json.dumps(verdicts, ensure_ascii=False)
    return staticmethod(_impl)


class TestLowSectorRescue(unittest.TestCase):
    """Q1 low 事件板块级救援。"""

    def setUp(self):
        if _TEST_DAY_DIR.is_dir():
            shutil.rmtree(_TEST_DAY_DIR)
        elif _TEST_DAY_DIR.exists():
            _TEST_DAY_DIR.unlink()
        self.node = na.NewsAnalysisNode.__new__(na.NewsAnalysisNode)

    def tearDown(self):
        if _TEST_DAY_DIR.is_dir():
            shutil.rmtree(_TEST_DAY_DIR)
        elif _TEST_DAY_DIR.exists():
            _TEST_DAY_DIR.unlink()

    def _run_rescue(self, events):
        return asyncio.run(self.node._rescue_low_events(events))

    # T1 ─────────────────────────────────────────────────────────
    def test_t1_switch_off_disabled_placeholder_no_llm(self):
        """开关关闭：disabled 占位影子、不调 LLM、零升级（回滚语义）。"""
        events = [_make_event("A事件", sectors=["低空经济"])]
        llm_calls = []
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", False), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm",
                          side_effect=lambda *a, **k: llm_calls.append(a) or ""):
            result = self._run_rescue(events)
        self.assertEqual([e.impact_level for e in result], ["low"])
        self.assertEqual(llm_calls, [])
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "disabled")
        self.assertFalse(record["switch"])
        self.assertEqual(record["upgraded"], 0)

    # T2 ─────────────────────────────────────────────────────────
    def test_t2_high_verdict_upgrades_in_place(self):
        """sector_impact=high → medium 原地升级，其余事件不动，dist 留痕。"""
        events = [
            _make_event("A板块利好", sectors=["低空经济"]),
            _make_event("B负面事件", sectors=["券商"], sentiment="negative"),
            _make_event("C无映射事件"),
            _make_event("D已是medium", impact="medium"),
        ]
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", True), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm", _fake_llm([
                 {"index": 0, "sector_impact": "high", "reason": "行业性政策落地"},
                 {"index": 1, "sector_impact": "low", "reason": "负面不上调"},
             ])):
            result = self._run_rescue(events)
        # A 升级；B/C 保持 low；D 本就 medium 不受影响
        self.assertEqual([e.impact_level for e in result],
                         ["medium", "low", "low", "medium"])
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        self.assertEqual(record["low_count"], 3)          # low 全集（不含 D）
        self.assertEqual(record["review_candidates"], 1)  # 仅 A 送审（B negative / C 无映射）
        self.assertEqual(record["upgraded"], 1)
        self.assertEqual(record["sector_impact_dist"], {"high": 1, "medium": 0, "low": 0})
        # 原地升级全字段保留
        upgraded = result[0]
        self.assertEqual(upgraded.affected_sectors, ["低空经济"])
        self.assertEqual(upgraded.sentiment, "positive")
        self.assertEqual(upgraded.title, "A板块利好")

    # T3 ─────────────────────────────────────────────────────────
    def test_t3_no_candidates_valid_sample(self):
        """无复审候选：review_candidates=0 有效样本落盘，不调 LLM。"""
        events = [_make_event("C无映射事件"), _make_event("B负面", sectors=["券商"], sentiment="negative")]
        llm_calls = []
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", True), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm",
                          side_effect=lambda *a, **k: llm_calls.append(a) or ""):
            result = self._run_rescue(events)
        self.assertEqual([e.impact_level for e in result], ["low", "low"])
        self.assertEqual(llm_calls, [])
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        self.assertEqual(record["review_candidates"], 0)
        self.assertEqual(record["upgraded"], 0)
        self.assertEqual(len(record["events"]), 2)  # 未送审行仍全量留痕

    # T4 ─────────────────────────────────────────────────────────
    def test_t4_llm_empty_response_failure_shadow(self):
        """LLM 空响应：failure 影子落盘 upgraded=0，降级不升级。"""
        events = [_make_event("A事件", sectors=["低空经济"])]
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", True), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm",
                          staticmethod(lambda *a, **k: "")):
            result = self._run_rescue(events)
        self.assertEqual([e.impact_level for e in result], ["low"])
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        self.assertEqual(record["upgraded"], 0)
        self.assertIn("failure", record)
        self.assertTrue(record["failure"])

    # T5 ─────────────────────────────────────────────────────────
    def test_t5_parse_failure_failure_shadow(self):
        """解析失败（非 JSON）：failure 影子落盘，降级不升级。"""
        events = [_make_event("A事件", sectors=["低空经济"])]
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", True), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm",
                          staticmethod(lambda *a, **k: "这不是JSON{{{")):
            result = self._run_rescue(events)
        self.assertEqual([e.impact_level for e in result], ["low"])
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        self.assertEqual(record["upgraded"], 0)
        self.assertIn("failure", record)

    # T6 ─────────────────────────────────────────────────────────
    def test_t6_parser_strips_code_fence(self):
        """解析器剥 ``` 围栏后正常解析。"""
        fenced = "```json\n" + json.dumps(
            [{"index": 0, "sector_impact": "high", "reason": "r"}], ensure_ascii=False
        ) + "\n```"
        verdicts = na.NewsAnalysisNode._parse_low_rescue_verdicts(fenced)
        self.assertEqual(verdicts[0]["sector_impact"], "high")

    # T7 ─────────────────────────────────────────────────────────
    def test_t7_parser_non_array_raises(self):
        """非数组输出 raise ValueError（run() 降级为原过滤行为）。"""
        with self.assertRaises(ValueError):
            na.NewsAnalysisNode._parse_low_rescue_verdicts('{"index": 0}')

    # T8 ─────────────────────────────────────────────────────────
    def test_t8_parser_empty_raises(self):
        """空响应 raise ValueError。"""
        with self.assertRaises(ValueError):
            na.NewsAnalysisNode._parse_low_rescue_verdicts("   ")

    # T9 ─────────────────────────────────────────────────────────
    def test_t9_illegal_verdicts_dropped(self):
        """非法 verdict（index 越界 / 非法 sector_impact）丢弃，合法条目生效。"""
        events = [_make_event("A事件", sectors=["低空经济"]),
                  _make_event("B事件", sectors=["券商"])]
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", True), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm", _fake_llm([
                 {"index": 99, "sector_impact": "high"},        # 越界
                 {"index": 0, "sector_impact": "extreme"},      # 非法值
                 {"index": 1, "sector_impact": "high", "reason": "合法"},
                 "garbage",                                      # 非 dict
             ])):
            result = self._run_rescue(events)
        self.assertEqual([e.impact_level for e in result], ["low", "medium"])
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        self.assertEqual(record["upgraded"], 1)

    # T10 ────────────────────────────────────────────────────────
    def test_t10_upgraded_event_survives_run_filter(self):
        """升级事件在 run() 过滤行存活（impact_level != "low" 保留 medium）。"""
        events = [_make_event("A事件", sectors=["低空经济"]),
                  _make_event("C无映射事件")]
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", True), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm", _fake_llm([
                 {"index": 0, "sector_impact": "high", "reason": "板块级重大"},
             ])):
            result = self._run_rescue(events)
        survivors = [e for e in result if e.impact_level != "low"]
        self.assertEqual([e.title for e in survivors], ["A事件"])

    # T11 ────────────────────────────────────────────────────────
    def test_t11_disabled_record_keys_complete(self):
        """disabled 占位记录键完整（观测渲染消费面）。"""
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", False), \
             patch.object(na, "date", _FixedDate):
            self._run_rescue([_make_event("A事件")])
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        for key in ("date", "switch", "status", "low_count",
                    "review_candidates", "upgraded", "sector_impact_dist", "events", "note"):
            self.assertIn(key, record)

    # T12 ────────────────────────────────────────────────────────
    def test_t12_shadow_write_oserror_nonfatal(self):
        """真实落盘失败（day_dir 被同名文件占用 → mkdir FileExistsError/OSError）：
        writer 内部捕获只告警，升级结果照常返回（留痕失败≠救援失败）。"""
        events = [_make_event("A事件", sectors=["低空经济"])]
        _TEST_DAY_DIR.parent.mkdir(parents=True, exist_ok=True)
        _TEST_DAY_DIR.write_text("blocker", encoding="utf-8")  # 占用目录路径
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", True), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm", _fake_llm([
                 {"index": 0, "sector_impact": "high", "reason": "r"},
             ])):
            result = self._run_rescue(events)
        self.assertEqual([e.impact_level for e in result], ["medium"])

    # T13 ────────────────────────────────────────────────────────
    def test_t13_shadow_records_include_unreviewed_rows(self):
        """影子 events[] 含未送审行（reason 标注未送审原因）。"""
        events = [_make_event("A事件", sectors=["低空经济"]),
                  _make_event("C无映射事件")]
        with patch.object(na, "GOT_LOW_SECTOR_RESCUE", True), \
             patch.object(na, "date", _FixedDate), \
             patch.object(na.NewsAnalysisNode, "_call_llm", _fake_llm([
                 {"index": 0, "sector_impact": "medium", "reason": "不升级"},
             ])):
            self._run_rescue(events)
        record = json.loads(_SHADOW_FILE.read_text(encoding="utf-8"))
        titles = {e["title"]: e for e in record["events"]}
        self.assertIn("C无映射事件", titles)
        self.assertIsNone(titles["C无映射事件"]["sector_impact"])
        self.assertIn("未送审", titles["C无映射事件"]["reason"])
        # 送审且获判定行带 reason
        self.assertEqual(titles["A事件"]["sector_impact"], "medium")
        self.assertEqual(titles["A事件"]["reason"], "不升级")


if __name__ == "__main__":
    unittest.main()
