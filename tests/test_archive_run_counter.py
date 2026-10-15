"""
测试 §8 #1: meta.json 运行记次功能

验收标准：
1. 同日两次运行 → runs 数组长度 2，run_seq 递增
2. meta.json 损坏 → 不崩溃，重建 runs 数组
3. 现有字段（如 funnel_stats）保留
"""
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from src.got.archive import (
    _read_meta_safe,
    _build_allocation_summary,
    _build_meta,
)


def test_read_meta_safe_handles_corrupt_file(tmp_path: Path):
    """meta.json 损坏 → 返回空 dict，不崩溃"""
    meta_path = tmp_path / "meta.json"
    meta_path.write_text("{invalid json", encoding="utf-8")
    
    result = _read_meta_safe(meta_path)
    assert result == {}


def test_read_meta_safe_handles_missing_file(tmp_path: Path):
    """meta.json 缺失 → 返回空 dict"""
    meta_path = tmp_path / "meta.json"
    
    result = _read_meta_safe(meta_path)
    assert result == {}


def test_read_meta_safe_handles_nul_bytes(tmp_path: Path):
    """meta.json 含 NUL 字节 → 消毒后正常读取"""
    meta_path = tmp_path / "meta.json"
    meta_path.write_text('{"key": "value"}\x00\x00', encoding="utf-8")
    
    result = _read_meta_safe(meta_path)
    assert result == {"key": "value"}


def test_build_allocation_summary_empty():
    """空 allocation → 返回空列表"""
    result = _build_allocation_summary([])
    assert result == []


def test_build_allocation_summary_limit_10():
    """allocation_summary 最多 10 条"""
    class FakeAlloc:
        def __init__(self, s, n, a, v):
            self.symbol = s
            self.name = n
            self.action = a
            self.target_value = v
    
    allocs = [FakeAlloc(f"{i:06d}", f"S{i}", "buy", 1000) for i in range(15)]
    result = _build_allocation_summary(allocs)
    assert len(result) == 10


def test_build_allocation_summary_weight_calculation():
    """allocation_summary 的 weight 字段正确计算"""
    class FakeAlloc:
        def __init__(self, s, n, a, v):
            self.symbol = s
            self.name = n
            self.action = a
            self.target_value = v
    
    allocs = [
        FakeAlloc("000001.SZ", "股票1", "buy", 3000.0),
        FakeAlloc("000002.SZ", "股票2", "sell", 7000.0),
    ]
    result = _build_allocation_summary(allocs)
    
    assert len(result) == 2
    assert result[0]["symbol"] == "000001.SZ"
    assert result[0]["name"] == "股票1"
    assert result[0]["action"] == "buy"
    assert result[0]["target_value"] == 3000.0
    assert result[0]["weight"] == pytest.approx(0.3, abs=0.01)
    
    assert result[1]["symbol"] == "000002.SZ"
    assert result[1]["name"] == "股票2"
    assert result[1]["action"] == "sell"
    assert result[1]["target_value"] == 7000.0
    assert result[1]["weight"] == pytest.approx(0.7, abs=0.01)


def test_build_meta_first_run_no_existing_meta(tmp_path: Path):
    """首次运行（无旧 meta.json）→ runs 数组长度 1，run_seq=1"""
    archive_dir = tmp_path / "20261015"
    archive_dir.mkdir()
    
    meta = _build_meta(
        run_date=date(2026, 10, 15),
        use_mock=False,
        start_time=datetime.now(),
        contexts={},
        archive_dir=archive_dir,
    )
    
    assert "runs" in meta
    assert len(meta["runs"]) == 1
    assert meta["runs"][0]["run_seq"] == 1
    assert meta["runs"][0]["phase"] == "phase1"
    assert meta["runs"][0]["allocation_summary"] == []
    assert meta["runs"][0]["position_ratio"] is None


def test_build_meta_second_run_appends(tmp_path: Path):
    """第二次运行 → runs 数组长度 2，run_seq 递增"""
    archive_dir = tmp_path / "20261015"
    archive_dir.mkdir()
    
    # 模拟第一次运行后的 meta.json
    meta_path = archive_dir / "meta.json"
    first_meta = {
        "run_date": "2026-10-15",
        "runs": [
            {
                "run_seq": 1,
                "run_at": "2026-10-15T10:00:00",
                "phase": "phase1",
                "allocation_summary": [],
                "position_ratio": None,
            }
        ],
    }
    meta_path.write_text(json.dumps(first_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # 第二次运行
    meta = _build_meta(
        run_date=date(2026, 10, 15),
        use_mock=False,
        start_time=datetime.now(),
        contexts={},
        archive_dir=archive_dir,
    )
    
    assert "runs" in meta
    assert len(meta["runs"]) == 2
    assert meta["runs"][0]["run_seq"] == 1
    assert meta["runs"][1]["run_seq"] == 2
    assert meta["runs"][1]["phase"] == "phase1"


def test_build_meta_preserves_existing_fields(tmp_path: Path):
    """现有字段（如 funnel_stats）在 _build_meta 中保留"""
    archive_dir = tmp_path / "20261015"
    archive_dir.mkdir()
    
    # 模拟已有 meta.json 含 funnel_stats
    meta_path = archive_dir / "meta.json"
    old_meta = {
        "run_date": "2026-10-15",
        "funnel_stats": {"round_survival_counts": {"r0": 10}},
        "net_preflight": {"all_ok": True},
        "runs": [
            {
                "run_seq": 1,
                "run_at": "2026-10-15T10:00:00",
                "phase": "phase1",
                "allocation_summary": [],
                "position_ratio": None,
            }
        ],
    }
    meta_path.write_text(json.dumps(old_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    
    # 新运行
    meta = _build_meta(
        run_date=date(2026, 10, 15),
        use_mock=False,
        start_time=datetime.now(),
        contexts={},
        archive_dir=archive_dir,
    )
    
    # 旧字段应保留
    assert meta["funnel_stats"]["round_survival_counts"]["r0"] == 10
    assert meta["net_preflight"]["all_ok"] is True
    # runs 数组应追加
    assert len(meta["runs"]) == 2
    assert meta["runs"][1]["run_seq"] == 2


def test_build_meta_handles_corrupt_existing_meta(tmp_path: Path):
    """旧 meta.json 损坏 → 不崩溃，重建 runs 数组"""
    archive_dir = tmp_path / "20261015"
    archive_dir.mkdir()
    
    # 写入损坏的 meta.json
    meta_path = archive_dir / "meta.json"
    meta_path.write_text("{invalid json", encoding="utf-8")
    
    # 新运行，应不崩溃
    meta = _build_meta(
        run_date=date(2026, 10, 15),
        use_mock=False,
        start_time=datetime.now(),
        contexts={},
        archive_dir=archive_dir,
    )
    
    assert "runs" in meta
    assert len(meta["runs"]) == 1
    assert meta["runs"][0]["run_seq"] == 1
