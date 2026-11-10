"""归档原子替换白名单回归：归档前直写的 Node 4 旁挂产物必须存活。

2026-10-30 实锤：Node 4 当日四次落盘 ``shadow_attention.json``，但
``archive_daily_run`` 的搬运白名单漏登该文件，同日归档的原子替换把它整目录
换掉 → 归档里零产物。这里对 Node 4 的两个旁挂产物逐项验证：
第二次归档后文件仍在且内容未被改写。
"""
from __future__ import annotations

import json
from datetime import date

import pytest

import src.got.archive as arch


class _FakeCtx:
    def __init__(self, name: str):
        self._n = name

    def model_dump(self, mode: str = "python"):
        return {"_fake": self._n}


def _archive(base, run_date: date) -> None:
    ctxs = {
        k: _FakeCtx(k)
        for k in ("market", "sector", "capital", "news", "sentiment", "unified")
    }
    arch.archive_daily_run(
        market_ctx=ctxs["market"],
        sector_ctx=ctxs["sector"],
        capital_ctx=ctxs["capital"],
        news_ctx=ctxs["news"],
        sentiment_ctx=ctxs["sentiment"],
        unified_ctx=ctxs["unified"],
        run_date=run_date,
        base_dir=base,
        use_mock=True,
    )


@pytest.mark.parametrize(
    "artifact",
    [
        arch.SHADOW_ATTENTION_ARTIFACT,
        arch.SHADOW_SHORTLIST_QUOTA_ARTIFACT,
        arch.THEME_LEDGER_ARTIFACT,
    ],
)
def test_pre_archive_artifact_survives_atomic_replace(tmp_path, artifact):
    """同日二次归档后，归档前直写的旁挂产物不得被删除。"""
    assert artifact in arch.PRE_ARCHIVE_ARTIFACTS

    base = tmp_path / "got_daily"
    day_dir = base / "20261030"
    day_dir.mkdir(parents=True)
    (day_dir / artifact).write_text(
        json.dumps({"kept": True, "artifact": artifact}), encoding="utf-8"
    )

    _archive(base, date(2026, 10, 30))

    payload = json.loads((day_dir / artifact).read_text(encoding="utf-8"))
    assert payload["kept"] is True
    assert (day_dir / "market.json").exists()
