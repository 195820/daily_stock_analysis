# -*- coding: utf-8 -*-
"""批次 C1：数值双源交叉验证单测（纯离线，mock manager）。

覆盖 compare_change_consistency 四态 + run_consistency_check 抽样/告警计数/缺源/关闭。
C2 新鲜度工具扩识别英文 date 列也在此附带断言（与 C 同批）。
"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.got.data_consistency import (
    CONSISTENCY_ARTIFACT,
    compare_change_consistency,
    run_consistency_check,
    _sample_symbols,
)
from src.got.data_freshness import check_data_freshness


def test_compare_ok():
    assert compare_change_consistency(1.0, 1.2, tol=1.0)["status"] == "ok"


def test_compare_sign_mismatch():
    assert compare_change_consistency(3.0, -2.0)["status"] == "sign_mismatch"


def test_compare_zero_noise_not_mismatch():
    """±0.05 内的近零值不做符号判定（防 -0.01 vs +0.01 伪告警）。"""
    assert compare_change_consistency(0.01, -0.02)["status"] == "ok"


def test_compare_magnitude_diff():
    assert compare_change_consistency(1.0, 3.5, tol=1.0)["status"] == "magnitude_diff"


def test_compare_missing():
    assert compare_change_consistency(None, 1.0)["status"] == "missing"
    assert compare_change_consistency(1.0, None)["status"] == "missing"


def _decision():
    return SimpleNamespace(unified_scoring=[
        SimpleNamespace(symbol="600000.SH", is_holding=True),
        SimpleNamespace(symbol="600001.SH", is_holding=True),
        SimpleNamespace(symbol="000002.SZ", is_holding=False),
    ])


@pytest.fixture
def archive_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_sample_holdings_first_and_cap():
    syms = _sample_symbols(_decision(), 2)
    assert syms == ["600000.SH", "600001.SH"]  # 持仓优先，cap=2


def test_disabled_sample_zero_returns_none(archive_dir):
    rec = run_consistency_check(decision=_decision(), target_date=date(2026, 10, 11),
                                sample_n=0, tol=1.0, manager=MagicMock())
    assert rec is None
    assert not (archive_dir / "data").exists()


def test_alerts_and_missing_counted(archive_dir):
    mgr = MagicMock()
    # 源B 批量：600000 一致、600001 符号相反、000002 缺（腾讯无返回）
    mgr.get_stock_quick_change.return_value = {
        "600000.SH": {"change_pct": 1.1},
        "600001.SH": {"change_pct": -2.0},
    }
    # 源A 实时：按 code 返回
    def _a(code):
        return {
            "600000.SH": SimpleNamespace(change_pct=1.0, is_stale=False),
            "600001.SH": SimpleNamespace(change_pct=3.0, is_stale=False),
            "000002.SZ": SimpleNamespace(change_pct=0.5, is_stale=True),
        }.get(code)
    mgr.get_realtime_quote.side_effect = _a

    rec = run_consistency_check(decision=_decision(), target_date=date(2026, 10, 11),
                                sample_n=10, tol=1.0, manager=mgr)
    assert rec["sample_size"] == 3
    assert rec["alerts"] == 1        # 600001 sign_mismatch
    assert rec["missing_source"] == 1  # 000002 源B 缺
    by = {it["symbol"]: it for it in rec["items"]}
    assert by["600000.SH"]["status"] == "ok"
    assert by["600001.SH"]["status"] == "sign_mismatch"
    assert by["000002.SZ"]["status"] == "missing"
    assert by["000002.SZ"]["a_is_stale"] is True
    art = archive_dir / "data" / "got_daily" / "20261011" / CONSISTENCY_ARTIFACT
    assert art.exists()
    assert json.loads(art.read_text(encoding="utf-8"))["line"] == "consistency"


def test_freshness_tool_recognizes_english_date():
    df = pd.DataFrame({"date": ["2026-10-10", "2026-10-11"], "close": [1, 2]})
    r = check_data_freshness(df, "2026-10-11")
    assert r["is_fresh"] is True and r["stale_data"] is False and r["unverifiable"] is False
    r2 = check_data_freshness(df, "2026-10-12")
    assert r2["stale_data"] is True


def test_freshness_tool_no_date_column_unverifiable():
    df = pd.DataFrame({"close": [1, 2]})
    r = check_data_freshness(df, "2026-10-11")
    assert r["unverifiable"] is True and r["is_fresh"] is True
