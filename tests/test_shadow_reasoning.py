# -*- coding: utf-8 -*-
"""影子模式观测段（reasoning.md 末尾）与 A6 转正子块的渲染测试。

覆盖五条影子线（4b 市场结构 / 3b 分歧摘要 / 4.1 超限减仓 / T1 候选股终审 /
T1-H 持仓审查）各自的：
- 有内容时的叙述 + 明细
- **未执行/无产物时的原因留痕**（用户要求：没有就说一句没有，且要说明为什么）
- 产物缺失时的降级
并覆盖 A6 glue 转正后的 Node 6 正文子块（叙述 + 全量语义表）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.got.archive import (  # noqa: E402
    _render_shadow_section,
    _render_a6_glue_formal,
    SHADOW_SECTION_TITLE,
    A6_FORMAL_SECTION_TITLE,
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


def _unified_with_shadow() -> dict:
    """unified.json 影子字段样本（4b market_theme_shadow + 3b conflict_summary_shadow）。"""
    return {
        "confirmed_sectors": [
            {"sector_code": "880754", "sector_name": "培育钻石"},
            {"sector_code": "880550", "sector_name": "PCB概念"},
        ],
        "contradictions": [{"a": 1}, {"b": 2}],
        "market_theme_shadow": {
            "status": "ok",
            "source": "mootdx_quick_change",
            "top5_concepts": [
                {"name": "红黄酒", "rank": 1, "change_pct": 4.03},
                {"name": "房产服务", "rank": 2, "change_pct": 0.57},
            ],
            "breadth": {"accelerating_count": 11, "warming_count": 55,
                        "cooling_count": 586, "total_count": 652},
            "note": "active=15 leading=10",
        },
        "conflict_summary_shadow": {
            "bullish_agents": [{"agent_name": "family_rank", "signal": "buy", "confidence": 1.0}],
            "bearish_agents": [{"agent_name": "capital", "signal": "sell", "confidence": 0.8}],
            "neutral_agents": [{"agent_name": "regime", "signal": "hold", "confidence": 0.5}],
            "conflict_type": "bull_bear_split",
            "decision_path_hint": "多空分歧，降仓观察",
            "risk_control": {"veto_buy": True, "reason": "极端分歧触发否决"},
        },
    }


def _sell_source_shadow() -> dict:
    """portfolio_decision.json 的 4.1 超限减仓影子样本。"""
    return {
        "mode": "shadow",
        "excess_value": 12000.0,
        "projected_trim_value": 9000.0,
        "planned_trims": [
            {"symbol": "600189", "name": "泉阳泉", "reduce_quantity": 500, "sell_value": 9000.0},
        ],
        "grace_skipped_symbols": ["603716"],
    }


def test_all_five_shadow_lines_present() -> None:
    """五条线都有内容时，各自成段（A5 已移出影子段，仅剩降级观测）。"""
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
            unified=_unified_with_shadow(),
            sell_source_shadow=_sell_source_shadow(),
        )
    )
    assert SHADOW_SECTION_TITLE in text
    assert "4b 市场结构" in text
    assert "3b 分歧摘要" in text
    assert "4.1 超限减仓" in text
    assert "T1 候选股终审" in text
    assert "T1-H 持仓审查" in text
    # A5 已转正，正常态不再出现在影子段
    assert "A5 语义粘合层" not in text
    # 明细被展开
    assert "000520" in text and "凤凰航运" in text
    assert "600250" in text and "reduce" in text


def test_a6_formal_renders_table_with_board_name() -> None:
    """A6 转正子块出表，board_code 映射为中文板块名，措辞为已转正。"""
    text = "\n".join(_render_a6_glue_formal(
        _glue_ok(), glue_consumed=True, glue_status="ok", code_to_name=CODE_TO_NAME))
    assert A6_FORMAL_SECTION_TITLE in text
    assert "已转正" in text
    assert "| 板块 |" in text          # 表头
    assert "培育钻石" in text           # 名字映射成功
    assert "PCB概念" in text
    assert "AI硬件" in text             # 家族列
    assert "苹果折叠屏" in text          # 传导路径
    assert "880754" not in text         # 表内用中文名，不出现裸 board_code
    assert "2 个候选板块" in text or "2 条强度判定" in text


def test_a6_formal_consumed_vs_not() -> None:
    """glue_consumed 决定措辞：已消费替换四件套 / 未消费保留规则侧。"""
    consumed = "\n".join(_render_a6_glue_formal(
        _glue_ok(), glue_consumed=True, glue_status="ok", code_to_name=CODE_TO_NAME))
    not_consumed = "\n".join(_render_a6_glue_formal(
        _glue_ok(), glue_consumed=False, glue_status="S1_call_failed", code_to_name=CODE_TO_NAME))
    assert "已消费" in consumed
    assert "未消费" in not_consumed


def test_market_theme_shadow_render_and_missing() -> None:
    """4b 影子线：有 market_theme_shadow 时展开读数，无时明说开关/不可达。"""
    has = "\n".join(_render_shadow_section(
        glue=None, t1=None, t1h=None, code_to_name={}, unified=_unified_with_shadow()))
    assert "4b 市场结构" in has and "红黄酒" in has and "accelerating=11" in has
    missing = "\n".join(_render_shadow_section(
        glue=None, t1=None, t1h=None, code_to_name={}, unified={}))
    assert "4b 市场结构" in missing and "无产物" in missing


def test_disagreement_shadow_render_counts_and_diff() -> None:
    """3b 影子线：分类计数 + 与现行 contradictions[] 差异 + veto_buy。"""
    text = "\n".join(_render_shadow_section(
        glue=None, t1=None, t1h=None, code_to_name={}, unified=_unified_with_shadow()))
    assert "3b 分歧摘要" in text
    assert "bull_bear_split" in text
    assert "看多 1 / 看空 1 / 中性 1" in text
    assert "veto_buy" in text
    assert "差异" in text


def test_sell_source_shadow_render_and_missing() -> None:
    """4.1 影子线：展示缺口/计划减仓/宽限跳过；无产物时明说未超限。"""
    has = "\n".join(_render_shadow_section(
        glue=None, t1=None, t1h=None, code_to_name={}, sell_source_shadow=_sell_source_shadow()))
    assert "4.1 超限减仓" in has
    assert "600189" in has and "泉阳泉" in has
    assert "603716" in has  # 宽限跳过
    missing = "\n".join(_render_shadow_section(
        glue=None, t1=None, t1h=None, code_to_name={}, sell_source_shadow=None))
    assert "4.1 超限减仓" in missing and "无产物" in missing


def test_baseline_warning_note_present() -> None:
    """影子段头注必须标明 2026-09-29 前读数无效（P0 #1 修复前 run() 崩）。"""
    text = "\n".join(_render_shadow_section(
        glue=None, t1=None, t1h=None, code_to_name={}))
    assert "2026-09-29" in text and "import os" in text


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
        json.dumps({
            "confirmed_sectors": [
                {"sector_code": "880754", "sector_name": "培育钻石"},
                {"sector_code": "880550", "sector_name": "PCB概念"},
            ],
            "market_theme_shadow": {"status": "ok", "source": "mootdx_quick_change",
                                   "top5_concepts": [], "breadth": {"total_count": 3}},
            "conflict_summary_shadow": {"conflict_type": "none",
                                        "bullish_agents": [], "bearish_agents": [],
                                        "neutral_agents": []},
        }, ensure_ascii=False), encoding="utf-8")
    (day_dir / "portfolio_decision.json").write_text(
        json.dumps({"sell_source_shadow": None}, ensure_ascii=False), encoding="utf-8")


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
    # 五条影子线标题均在（4b/3b/4.1/T1/T1-H），且 A5 已移出影子段
    for line in ("4b 市场结构", "3b 分歧摘要", "4.1 超限减仓",
                 "T1 候选股终审", "T1-H 持仓审查"):
        assert line in second
    assert "A5 语义粘合层" not in second
    # 重跑后各线也只留一份（不堆积）
    assert second.count("4b 市场结构") == 1 and second.count("T1-H 持仓审查") == 1
    assert "无候选进入终审" in second


def test_write_reasoning_md_inserts_a6_formal_and_defers_theme(tmp_path: Path) -> None:
    """Phase 1 主正文：A6 转正子块固定出现；4b 影子态不进 🧭（收编 🧪）。

    直接跑 _write_reasoning_md（无现存集成测试覆盖 Phase 1 渲染），
    确保新增的 A6 子块与 🧭 只显已用态的改动不破坏主正文生成。
    """
    import json
    from datetime import date
    from types import SimpleNamespace

    from src.got.archive import _write_reasoning_md, GLUE_ARTIFACT

    empty = SimpleNamespace(reasoning="")
    confirmed = [SimpleNamespace(
        sector_code="880754", sector_name="培育钻石", classification="actionable",
        composite_score=70.0, capital_check="aligned", news_check="catalyzed",
        sentiment_check="high", sector_role="mainline", mainline_tier="T1", confidence=0.8,
    )]
    unified = SimpleNamespace(
        market_regime=None, market_theme=None,
        market_theme_shadow={"status": "ok", "source": "mootdx", "top5_concepts": [],
                             "breadth": {}, "note": "x"},
        confirmed_sectors=confirmed, ambush_pool=[], ambush_reservoir=[],
        ambush_veto_log=[], cross_node_signals=[], reasoning="聚合结论",
        glue_consumed=True, glue_status="ok",
    )
    glue = {
        "status": "ok", "degraded": False,
        "strengths": [{"board_code": "880754", "strength": 0.6, "news_read": "high",
                       "capital_read": "low", "sentiment_read": "medium"}],
        "relations": [{"board_code": "880754", "propagation_path": ["事件", "板块"]}],
        "families": [{"board_code": "880754", "family": "消费"}],
    }
    (tmp_path / GLUE_ARTIFACT).write_text(
        json.dumps(glue, ensure_ascii=False), encoding="utf-8")
    ctx = {"market": empty, "sector": empty, "capital": empty,
           "news": empty, "sentiment": empty, "unified": unified}

    out_path = tmp_path / "reasoning.md"
    _write_reasoning_md(out_path, ctx, date(2026, 9, 28))
    out = out_path.read_text(encoding="utf-8")

    assert A6_FORMAL_SECTION_TITLE in out
    assert "已消费（glue 直接替换四件套）" in out
    assert "培育钻石" in out and "消费" in out
    # 影子态 4b 不在主正文（已由 🧪 承载）；仅 market_theme 非 None 才升格
    assert "市场结构" not in out


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
