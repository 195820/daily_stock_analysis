# -*- coding: utf-8 -*-
"""影子模式观测段（reasoning.md 末尾）的渲染测试。

覆盖三条影子线（A5 glue / T1 候选股终审 / T1-H 持仓审查）各自的：
- 有内容时的叙述 + 明细
- **未执行时的原因留痕**（用户要求：没有就说一句没有，且要说明为什么）
- 产物缺失时的降级
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.got.archive import (  # noqa: E402
    _render_shadow_section,
    SHADOW_SECTION_TITLE,
)

CODE_TO_NAME = {"880754": "培育钻石", "880550": "PCB概念"}


def _glue_ok() -> dict:
    return {
        "status": "ok",
        "degraded": False,
        "degraded_sectors": [],
        "warnings": [],
        "relations": [
            {
                "event_id": "e1",
                "board_code": "880550",
                "strength": 0.7,
                "direction": "positive",
                "propagation_path": ["苹果折叠屏", "消费电子", "PCB", "PCB概念"],
                "confidence": 0.8,
            }
        ],
        "strengths": [
            {
                "board_code": "880754",
                "strength": 0.55,
                "confidence": 0.6,
                "evidence": "培育钻石技术面强但资金面净流出",
                "capital_read": "low",
                "news_read": "low",
                "sentiment_read": "medium",
                "basis": "composite",
            },
            {
                "board_code": "880550",
                "strength": 0.70,
                "confidence": 0.8,
                "evidence": "折叠屏催化",
                "capital_read": "medium",
                "news_read": "high",
                "sentiment_read": "medium",
                "basis": "composite",
            },
        ],
        "families": [
            {"board_code": "880754", "family": "消费"},
            {"board_code": "880550", "family": "AI硬件"},
        ],
    }


def test_all_three_lines_present() -> None:
    """三条线都有内容时，各自成段。"""
    text = "\n".join(
        _render_shadow_section(
            glue=_glue_ok(),
            t1={"line": "t1", "executed": True, "reviewed": 2, "budget": 2,
                "rejects": [{"symbol": "000520", "name": "凤凰航运",
                             "reason": "基本面亏损、资金面极弱"}]},
            t1h={"line": "t1h", "executed": True, "reviewed": 1, "budget": 3,
                 "verdicts": [{"symbol": "600250", "name": "600250",
                               "verdict": "reduce", "confidence": 0.7,
                               "reasoning": "浮亏触及止损线"}]},
            code_to_name=CODE_TO_NAME,
        )
    )
    assert SHADOW_SECTION_TITLE in text
    assert "A5 语义粘合层" in text
    assert "T1 候选股终审" in text
    assert "T1-H 持仓审查" in text
    # 明细被展开
    assert "000520" in text and "凤凰航运" in text
    assert "600250" in text
    assert "reduce" in text


def test_a5_renders_table_with_board_name() -> None:
    """A5 段出表，board_code 映射为中文板块名。"""
    text = "\n".join(_render_shadow_section(glue=_glue_ok(), t1=None, t1h=None,
                                            code_to_name=CODE_TO_NAME))
    assert "| 板块 |" in text          # 表头
    assert "培育钻石" in text           # 名字映射成功
    assert "PCB概念" in text
    assert "AI硬件" in text             # 家族列
    assert "苹果折叠屏" in text          # 传导路径
    # 表内用中文板块名，不出现裸 board_code（映射失败才回退为代码）
    assert "880754" not in text
    # 计数正确
    assert "2 个候选板块" in text or "2 个" in text


def test_t1_not_executed_states_reason() -> None:
    """T1 未执行时必须写明原因（用户要求：没有就说一句没有，且带原因）。"""
    text = "\n".join(
        _render_shadow_section(
            glue=None, t1h=None, code_to_name={},
            t1={"line": "t1", "executed": False, "reason": "开关关闭"},
        )
    )
    assert "T1 候选股终审" in text
    assert "未执行" in text
    assert "开关关闭" in text


def test_t1h_not_executed_lists_unmet_conditions() -> None:
    """T1-H 未执行时须说明具体哪个条件不满足，而不只是「无目标」。"""
    text = "\n".join(
        _render_shadow_section(
            glue=None, t1=None, code_to_name={},
            t1h={"line": "t1h", "executed": False,
                 "reason": "无可审持仓",
                 "skipped": [{"symbol": "600250", "composite_score": 50.1}]},
        )
    )
    assert "T1-H 持仓审查" in text
    assert "未执行" in text
    assert "600250" in text
    assert "50.1" in text


def test_missing_artifacts_say_so() -> None:
    """三份产物全缺时，不报错，且明说无产物。"""
    text = "\n".join(_render_shadow_section(glue=None, t1=None, t1h=None,
                                            code_to_name={}))
    assert SHADOW_SECTION_TITLE in text
    assert text.count("无产物") >= 3 or text.count("未执行") >= 3


def test_degraded_glue_expands_warning() -> None:
    """glue 降级时须展开受影响板块与 warnings。"""
    glue = _glue_ok()
    glue["status"] = "S1_call_failed"
    glue["degraded"] = True
    glue["degraded_sectors"] = ["旅游", "半导体"]
    glue["warnings"] = ["F1 降级: LLM 调用失败"]
    text = "\n".join(_render_shadow_section(glue=glue, t1=None, t1h=None,
                                            code_to_name=CODE_TO_NAME))
    assert "S1_call_failed" in text
    assert "旅游" in text and "半导体" in text
    assert "F1 降级" in text


def test_section_is_plain_narrative_not_just_table() -> None:
    """A5 段须有可读叙述，不能只有表（用户明确要求长段文字）。"""
    text = "\n".join(_render_shadow_section(glue=_glue_ok(), t1=None, t1h=None,
                                            code_to_name=CODE_TO_NAME))
    lines = [ln for ln in text.splitlines() if ln.strip()]
    narrative = [ln for ln in lines if not ln.lstrip().startswith(("|", "#", "-"))]
    assert any(len(ln) > 40 for ln in narrative), "缺少足够长的叙述句"


# ══════════════════════════════════════════════════════════════════════
# 集成：接入 reasoning.md 的覆盖写（同日重跑不得堆积）
# ══════════════════════════════════════════════════════════════════════


def _fake_decision(reasoning: str = "本次不做调整。"):
    from types import SimpleNamespace

    return SimpleNamespace(
        reasoning=reasoning, allocation=[], warnings=[],
        summary="", ambush_exit_signals=[],
    )


def _write_artifacts(day_dir: Path) -> None:
    import json

    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "agent_glue.json").write_text(
        json.dumps(_glue_ok(), ensure_ascii=False), encoding="utf-8")
    (day_dir / "shadow_t1.json").write_text(
        json.dumps({"line": "t1", "executed": False, "reason": "无候选进入终审"},
                   ensure_ascii=False), encoding="utf-8")
    (day_dir / "shadow_t1h.json").write_text(
        json.dumps({"line": "t1h", "executed": False, "reason": "当前无持仓"},
                   ensure_ascii=False), encoding="utf-8")
    (day_dir / "unified.json").write_text(
        json.dumps({"confirmed_sectors": [
            {"sector_code": "880754", "sector_name": "培育钻石"},
            {"sector_code": "880550", "sector_name": "PCB概念"},
        ]}, ensure_ascii=False), encoding="utf-8")


def test_shadow_section_appended_and_not_duplicated(tmp_path: Path) -> None:
    """影子段挂在 Node 7 之后；同日重跑只留一份（不堆积）。"""
    from src.got.archive import _append_phase2_reasoning

    day_dir = tmp_path / "20260914"
    _write_artifacts(day_dir)
    reasoning_path = day_dir / "reasoning.md"
    reasoning_path.write_text("# GOT 运行推理汇总\n\n## Node 6 聚合研判\n\n正文\n",
                              encoding="utf-8")

    _append_phase2_reasoning(reasoning_path, _fake_decision())
    first = reasoning_path.read_text(encoding="utf-8")
    _append_phase2_reasoning(reasoning_path, _fake_decision("第二次运行。"))
    second = reasoning_path.read_text(encoding="utf-8")

    assert first.count(SHADOW_SECTION_TITLE) == 1
    assert second.count(SHADOW_SECTION_TITLE) == 1, "重跑后影子段堆积了"
    # 位置：影子段在 Node 7 之后
    assert second.index("## Node 7 交易建议") < second.index(SHADOW_SECTION_TITLE)
    # Node 6 正文保留
    assert "## Node 6 聚合研判" in second
    # 末版为准：第二次的 Node 7 内容生效，第一次的消失
    assert "第二次运行。" in second and "本次不做调整。" not in second
    # 至少三条线的标题都在（均为"未执行+原因"路径）
    assert "A5 语义粘合层" in second and "无候选进入终审" in second


# ══════════════════════════════════════════════════════════════════════
# portfolio_decision 侧的收集与落盘
# ══════════════════════════════════════════════════════════════════════


class _FakeStock:
    """ScreenerStock 的最小替身（只带 T1 写的三个字段）。"""

    def __init__(self, code, name, verdict="", notes=""):
        self.code, self.name = code, name
        self.agent_verdict, self.agent_notes = verdict, notes


def test_collect_shadow_t1_records_rejects(monkeypatch) -> None:
    """T1 收集：reviewed 只数被审过的，rejects 带理由。"""
    from src.got.nodes import portfolio_decision as pd
    import src.got.catalyst_config as cfg

    monkeypatch.setattr(cfg, "AGENT_STOCK_REVIEW_ENABLED", True)
    monkeypatch.setattr(cfg, "AGENT_STOCK_REVIEW_SHADOW", True)
    monkeypatch.setattr(cfg, "AGENT_STOCK_REVIEW_MAX", 2)

    node = pd.PortfolioDecisionNode(use_mock=True)
    node._collect_shadow_t1([
        _FakeStock("000520", "凤凰航运", "reject", "基本面亏损、资金面极弱"),
        _FakeStock("688020", "方邦股份", "confirm", "逻辑通顺"),
        _FakeStock("300999", "未审股", "", ""),          # 未进入 review_targets
    ])

    rec = node._shadow_t1
    assert rec["executed"] is True
    assert rec["reviewed"] == 2, "未审的候选不应计入 reviewed"
    assert len(rec["rejects"]) == 1
    assert rec["rejects"][0]["symbol"] == "000520"
    assert "基本面亏损" in rec["rejects"][0]["reason"]


def test_collect_shadow_t1_disabled_states_reason(monkeypatch) -> None:
    """T1 开关关闭时，记录须写明原因而非静默为空。"""
    from src.got.nodes import portfolio_decision as pd
    import src.got.catalyst_config as cfg

    monkeypatch.setattr(cfg, "AGENT_STOCK_REVIEW_ENABLED", False)
    node = pd.PortfolioDecisionNode(use_mock=True)
    node._collect_shadow_t1([_FakeStock("000520", "凤凰航运", "reject", "x")])

    assert node._shadow_t1["executed"] is False
    assert "关闭" in node._shadow_t1["reason"]


def test_save_shadow_records_writes_both_files(tmp_path: Path, monkeypatch) -> None:
    """落盘：两份产物写入当日目录，且不含未执行的线时不写空文件。"""
    import src.got.nodes.portfolio_decision as pd

    monkeypatch.chdir(tmp_path)
    node = pd.PortfolioDecisionNode(use_mock=True)
    node._shadow_t1 = {"line": "t1", "executed": False, "reason": "无候选进入终审"}
    node._shadow_t1h = {"line": "t1h", "executed": True, "reviewed": 1,
                        "budget": 3, "verdicts": []}
    node._save_shadow_records()

    import json
    from datetime import date

    day_dir = tmp_path / "data" / "got_daily" / date.today().strftime("%Y%m%d")
    t1 = json.loads((day_dir / "shadow_t1.json").read_text(encoding="utf-8"))
    t1h = json.loads((day_dir / "shadow_t1h.json").read_text(encoding="utf-8"))
    assert t1["executed"] is False and "无候选进入终审" in t1["reason"]
    assert t1h["executed"] is True and t1h["reviewed"] == 1
