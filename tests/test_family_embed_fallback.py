# -*- coding: utf-8 -*-
"""WI-P1-7(b) · 「其他」板块 embedding 家族兜底（src/got/family_embed.py）

红线复现点（缺一即视为破坏「不碰在线决策」）：
1. 缺省 off —— 不触网、不算向量、不改判，Node 2 输出逐字节不变；
2. shadow 只留痕「若兜底会改判的板块清单」，frame_group 不得动；
3. 只有 enabled 才真改判；
4. embedding 不可用只留痕降级，异常不得冒到 Node 2 主流程；
5. 相似度/缓存一律复用 glue_pipeline 的 L1 件，不另写第二套口径。

全部 embedding 调用均被 monkeypatch，测试不触网、不写真实归档。
"""
import asyncio
import json
from datetime import date, timedelta

import pytest

from src.got import family_embed as fe
from src.got.family_keywords import FAMILY_KEYWORDS

RUN_DAY = date(2026, 10, 6)


def _sync(coro):
    return asyncio.run(coro)


def _basis(v):
    """把二维向量补齐为三维（供可控 cosine 用例）。"""
    return [v[0], v[1], 0.0]


def _apply_wiring_semantics(details, report):
    """与 sector_analysis Step 3.4c 同口径的改判谓词（只测这段，不起 Node 2）。"""
    if not report or report.get("mode", "off") == "off":
        return 0
    if report["mode"] != "enabled":
        return 0
    n = 0
    for s in details:
        hit = report["overrides"].get(s.name)
        if hit:
            s.frame_group = hit["family"]
            n += 1
    return n


# ── 文本构造 ────────────────────────────────────────────────
def test_prototype_text_truncates_keywords_and_keeps_family_name():
    text = fe.build_family_prototype_text("AI硬件", [f"k{i}" for i in range(20)])
    assert text.startswith("主线家族: AI硬件；代表关键词: ")
    kept = text.split("代表关键词: ")[1].split("、")
    assert kept == [f"k{i}" for i in range(fe._PROTO_KEYWORDS)]


def test_board_query_text_is_name_only():
    assert fe.build_board_query_text("POE胶膜") == "板块名称: POE胶膜"


# ── 保守阈值裁决（纯函数）──────────────────────────────────
def test_select_family_ok_when_score_and_margin_pass():
    hit = fe.select_family([("新能源", 0.80), ("周期资源", 0.60)])
    assert hit["fallback"] is True and hit["reason"] == "ok"
    assert hit["family"] == "新能源" and hit["margin"] == pytest.approx(0.20)
    assert [c["family"] for c in hit["candidates"]] == ["新能源", "周期资源"]


def test_select_family_rejects_low_score():
    miss = fe.select_family([("新能源", 0.40), ("周期资源", 0.10)])
    assert miss["fallback"] is False and miss["reason"] == "low_score"


def test_select_family_rejects_ambiguous_margin():
    miss = fe.select_family([("新能源", 0.80), ("周期资源", 0.78)])
    assert miss["fallback"] is False and miss["reason"] == "ambiguous"
    assert miss["family"] == "新能源"      # 拒绝也要留首选痕迹，供人工复核


def test_select_family_single_candidate_has_no_margin_but_passes_score():
    hit = fe.select_family([("军工", 0.90)])
    assert hit["fallback"] is True and hit["margin"] is None


def test_select_family_empty_scores():
    assert fe.select_family([])["reason"] == "no_candidate"


# ── 开关与词表复核 ─────────────────────────────────────────
def test_resolve_mode_normalizes_and_defaults_off(monkeypatch):
    monkeypatch.delenv("GOT_FAMILY_EMBED_FALLBACK", raising=False)
    assert fe.resolve_mode() == "off"
    monkeypatch.setenv("GOT_FAMILY_EMBED_FALLBACK", " SHADOW ")
    assert fe.resolve_mode() == "shadow"
    monkeypatch.setenv("GOT_FAMILY_EMBED_FALLBACK", "nonsense")
    assert fe.resolve_mode() == "off"          # 非法值不得意外开启生产改判
    assert fe.resolve_mode("Enabled") == "enabled"


def test_keyword_family_of_matches_declaration_order():
    assert fe.keyword_family_of("芯片概念") == "AI硬件"
    assert fe.keyword_family_of("POE胶膜") == fe.OTHER_FAMILY


def test_collect_unmatched_splits_by_keyword_table():
    unmatched, skipped = fe.collect_unmatched([
        {"code": "880330", "name": "芯片概念"},
        {"code": "880555", "name": "POE胶膜"},
        {"code": "", "name": "  "},              # 空名直接丢弃
    ])
    assert [u["name"] for u in unmatched] == ["POE胶膜"]
    assert skipped == [{"name": "芯片概念", "keyword_family": "AI硬件"}]


def test_fallback_families_exclude_other_bucket():
    # 2026-10-15 拍板选项 A：兜底候选集与 glue prompt 白名单对齐（12 族），
    # 盲区家族（华为生态/前沿科技）不在候选，语义兜底判不出时落「其他」。
    from src.got.family_keywords import PROMPT_EXPOSED_FAMILIES
    assert fe.FALLBACK_FAMILIES == [
        n for n, _ in FAMILY_KEYWORDS
        if n != fe.OTHER_FAMILY and n in PROMPT_EXPOSED_FAMILIES
    ]
    assert len(fe.FALLBACK_FAMILIES) == len(PROMPT_EXPOSED_FAMILIES) == 12
    assert fe.OTHER_FAMILY not in fe.FALLBACK_FAMILIES
    assert "华为生态" not in fe.FALLBACK_FAMILIES
    assert "前沿科技" not in fe.FALLBACK_FAMILIES


# ── 缓存复用（按日 l1_embed_*.json，sha1 键跨日命中）──────────
def test_cache_candidates_prefers_existing_days(tmp_path):
    (tmp_path / f"l1_embed_{RUN_DAY.strftime('%Y%m%d')}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "l1_embed_19700101.json").write_text("{}", encoding="utf-8")
    files = fe.cache_candidates(RUN_DAY, tmp_path, lookback_days=3)
    assert [p.name for p in files] == [f"l1_embed_{RUN_DAY.strftime('%Y%m%d')}.json"]


def test_load_family_vectors_cache_hit_skips_embedding(monkeypatch, tmp_path):
    yesterday = RUN_DAY - timedelta(days=1)
    text = "主线家族: 新能源；代表关键词: 储能"
    cache = tmp_path / f"l1_embed_{yesterday.strftime('%Y%m%d')}.json"
    cache.write_text(
        json.dumps({"model": fe.GLUE_EMBEDDING_MODEL,
                    "entries": {fe._cache_key(fe.GLUE_EMBEDDING_MODEL, text): [1.0, 0.0, 0.0]}}),
        encoding="utf-8",
    )
    calls = []

    async def _boom(_texts):
        calls.append(1)
        raise AssertionError("命中缓存不应请求 embedding")

    monkeypatch.setattr(fe, "embed_texts", _boom)
    vecs, stats = _sync(fe.load_family_vectors([text], RUN_DAY, cache_root=tmp_path))
    assert vecs == [[1.0, 0.0, 0.0]]
    assert stats == {"cache_hits": 1, "cache_misses": 0}
    assert not calls


def test_load_family_vectors_miss_persists_day_file(monkeypatch, tmp_path):
    text = "板块名称: POE胶膜"
    calls = []

    async def _fake(texts):
        calls.append(list(texts))
        return [[0.0, 1.0, 0.0] for _ in texts]

    monkeypatch.setattr(fe, "embed_texts", _fake)
    vecs, stats = _sync(fe.load_family_vectors([text], RUN_DAY, cache_root=tmp_path))
    assert vecs == [[0.0, 1.0, 0.0]] and stats["cache_misses"] == 1
    day_file = tmp_path / f"l1_embed_{RUN_DAY.strftime('%Y%m%d')}.json"
    blob = json.loads(day_file.read_text(encoding="utf-8"))
    assert blob["model"] == fe.GLUE_EMBEDDING_MODEL
    assert blob["entries"][fe._cache_key(fe.GLUE_EMBEDDING_MODEL, text)] == [0.0, 1.0, 0.0]
    assert calls == [[text]]


def test_load_family_vectors_rejects_count_mismatch(monkeypatch, tmp_path):
    async def _short(_texts):
        return []

    monkeypatch.setattr(fe, "embed_texts", _short)
    with pytest.raises(RuntimeError, match="数量不匹配"):
        _sync(fe.load_family_vectors(["x"], RUN_DAY, cache_root=tmp_path))


# ── 生产入口三态 ────────────────────────────────────────────
def _patch_two_family_vectors(monkeypatch):
    """两族原型 + 一块板名的可控向量（走真 retrieve_topk，不假造分数）。

    词表复核 / 原型文本 / 候选家族名全部桩掉，不依赖真实 FAMILY_KEYWORDS 内容。
    """
    async def _fake(texts):
        out = []
        for t in texts:
            if t.startswith("主线家族: 新能源"):
                out.append([1.0, 0.0, 0.0])
            elif t.startswith("主线家族: "):
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.9, 0.1, 0.0])   # cos(·, e1)=0.9938 → 归新能源
        return out

    monkeypatch.setattr(fe, "embed_texts", _fake)
    monkeypatch.setattr(fe, "FALLBACK_FAMILIES", ["新能源", "AI硬件"])
    monkeypatch.setattr(fe, "build_family_prototype_text",
                        lambda family, kws: f"主线家族: {family}")
    monkeypatch.setattr(fe, "keyword_family_of",
                        lambda name: "AI硬件" if "芯片" in name else fe.OTHER_FAMILY)
    return {}


def test_off_mode_touches_nothing(monkeypatch, tmp_path):
    async def _never(_texts):
        raise AssertionError("off 态不得请求 embedding")

    monkeypatch.setattr(fe, "embed_texts", _never)
    monkeypatch.setenv("GOT_FAMILY_EMBED_FALLBACK", "off")
    out = _sync(fe.resolve_family_overrides([{"name": "POE胶膜", "code": "1"}],
                                            RUN_DAY, cache_root=tmp_path))
    assert out["mode"] == "off" and out["skipped_reason"] == "mode_off"
    assert out["overrides"] == {} and out["unmatched_boards"] == []
    assert list(tmp_path.iterdir()) == []       # 零 IO


def test_shadow_mode_records_without_override(monkeypatch, tmp_path):
    _patch_two_family_vectors(monkeypatch)
    monkeypatch.setenv("GOT_FAMILY_EMBED_FALLBACK", "shadow")
    out = _sync(fe.resolve_family_overrides(
        [{"name": "POE胶膜", "code": "880555"}, {"name": "芯片概念", "code": "880000"}],
        RUN_DAY, cache_root=tmp_path, min_score=0.5, min_margin=0.05,
    ))
    assert out["mode"] == "shadow"
    assert out["skipped_count"] == 1                       # 命中关键词的不参与
    assert out["would_flip_count"] == 1 and out["overrides"]              # 计算照做
    assert out["unmatched_boards"][0]["family"] == "新能源"
    assert out["unmatched_boards"][0]["code"] == "880555"


def test_enabled_mode_produces_override(monkeypatch, tmp_path):
    _patch_two_family_vectors(monkeypatch)
    monkeypatch.setenv("GOT_FAMILY_EMBED_FALLBACK", "enabled")
    out = _sync(fe.resolve_family_overrides(
        [{"name": "POE胶膜", "code": "880555"}], RUN_DAY, cache_root=tmp_path,
        min_score=0.5, min_margin=0.05,
    ))
    assert out["mode"] == "enabled"
    assert "POE胶膜" in out["overrides"]


def test_margin_gate_blocks_override(monkeypatch, tmp_path):
    _patch_two_family_vectors(monkeypatch)
    monkeypatch.setenv("GOT_FAMILY_EMBED_FALLBACK", "enabled")
    out = _sync(fe.resolve_family_overrides(
        [{"name": "POE胶膜", "code": "880555"}], RUN_DAY, cache_root=tmp_path,
        min_score=0.5, min_margin=0.99,          # 平票不兜底
    ))
    assert out["overrides"] == {} and out["would_flip_count"] == 0
    assert out["unmatched_boards"][0]["reason"] == "ambiguous"


def test_embedding_failure_degrades_to_reason_only(monkeypatch, tmp_path):
    async def _down(_texts):
        raise RuntimeError("GLUE_EMBEDDING_API_KEY 未配置")

    monkeypatch.setattr(fe, "embed_texts", _down)
    monkeypatch.setenv("GOT_FAMILY_EMBED_FALLBACK", "shadow")
    out = _sync(fe.resolve_family_overrides([{"name": "POE胶膜", "code": "1"}],
                                            RUN_DAY, cache_root=tmp_path))
    assert out["skipped_reason"] == "embedding_unavailable"
    assert out["overrides"] == {} and out["cache_misses"] == 0


def test_no_unmatched_board_short_circuits(monkeypatch, tmp_path):
    async def _never(_texts):
        raise AssertionError("无「其他」板块时不该算向量")

    monkeypatch.setattr(fe, "embed_texts", _never)
    monkeypatch.setenv("GOT_FAMILY_EMBED_FALLBACK", "shadow")
    out = _sync(fe.resolve_family_overrides([{"name": "芯片概念", "code": "1"}],
                                            RUN_DAY, cache_root=tmp_path))
    assert out["skipped_reason"] == "no_unmatched_board" and out["unmatched_boards"] == []


# ── schemas 观测字段 + Node 2 接线守卫 ──────────────────────
def test_sector_context_field_defaults_none_and_roundtrips():
    from src.got.schemas import SectorContext

    ctx = SectorContext()
    assert ctx.family_embed_shadow is None      # 缺省 None → 旧归档/旧调用零变化
    payload = {"mode": "shadow", "would_flip_count": 0}
    assert SectorContext(family_embed_shadow=payload).model_dump()["family_embed_shadow"] == payload


def test_node2_wiring_is_guarded():
    """接线守卫：Step 3.4c 必须存在、缺省 off、且拥挤检测在改判之后。"""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "got" / "nodes" / "sector_analysis.py").read_text(encoding="utf-8")
    assert 'os.getenv("GOT_FAMILY_EMBED_FALLBACK"' not in src   # 模式只由 family_embed 读一次
    assert "family_embed.resolve_family_overrides" in src
    assert "result.family_embed_shadow" in src
    assert 'fe_report["mode"] == "enabled"' in src
    assert re.search(r"Step 3\.4c[\s\S]{0,4000}?self\._detect_family_crowding\(analyzed\)", src), \
        "拥挤检测须在兜底改判之后"
    assert src.count("GOT_FAMILY_EMBED_MIN_SCORE") == 1


def test_embed_texts_sync_refuses_inside_running_loop():
    async def _inner():
        with pytest.raises(RuntimeError, match="事件循环内"):
            fe.embed_texts_sync(["x"])
        return True

    assert _sync(_inner()) is True


# ── Node 2 接线语义（不起整个 run，只测改判谓词）──────────────
def _details():
    from src.got.schemas import SectorDetail

    return [
        SectorDetail(code="880555", name="POE胶膜", state="neutral", strength_score=50.0,
                     frame_group="其他"),
        SectorDetail(code="880000", name="芯片概念", state="active_mainline",
                     strength_score=80.0, frame_group="AI硬件"),
    ]


def test_wiring_predicate_only_enabled_mutates_frame_group():
    overrides = {"POE胶膜": {"family": "新能源", "score": 0.99, "margin": 0.9}}
    shadow = {"mode": "shadow", "overrides": overrides}
    details = _details()
    assert _apply_wiring_semantics(details, shadow) == 0
    assert [d.frame_group for d in details] == ["其他", "AI硬件"]   # shadow 逐字节不变

    details = _details()
    assert _apply_wiring_semantics(details, {"mode": "off", "overrides": overrides}) == 0
    assert [d.frame_group for d in details] == ["其他", "AI硬件"]

    details = _details()
    assert _apply_wiring_semantics(
        details, {"mode": "enabled", "overrides": overrides}) == 1
    assert [d.frame_group for d in details] == ["新能源", "AI硬件"]


def test_full_family_rank_used_for_margin_gate():
    """候选家族全量排名：前 3 留痕，margin 只对次名算。"""
    ranked = [("新能源", 0.90), ("前沿科技", 0.80), ("军工", 0.10), ("消费", 0.05)]
    hit = fe.select_family(ranked, min_score=0.5, min_margin=0.05)
    assert hit["fallback"] is True and hit["margin"] == pytest.approx(0.10)
    assert len(hit["candidates"]) == 3
