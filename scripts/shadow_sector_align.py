# -*- coding: utf-8 -*-
"""LLM 板块对齐影子线（Phase 2 审核修复计划 §五，A5 范式派生）。

输入（全部来自当日归档，不触发任何实时数据拉取）：
  (a) 未命中板块名列表 —— 对 confirmed_sectors ∪ ambush_pool ∪ unified_scoring[].sector
      复算 _score_capital 同款三级匹配（精确键 → sector_name_variants → normalize），
      未命中的即当日「资金面-板块 0 分」板块；
  (b) 受控词表 —— capital.json evidence.sector_flow.sector_flow_all 全量键名 ∪ {"无映射"}。

LLM 调用（A5 五要素对齐）：
  ① 单次全量调用，temperature=0，max_retries=3 仅对网络错误/空响应重试，
     finish_reason="length" 立即失败不重试（同 prompt 重放必然再截断）；
  ② 严格 JSON 输出 + 多级解析容错（```json 围栏 → 花括号切片）；
  ③ 受控词表校验：映射目标不在词表内即弃该条并记 out_of_vocab；
  ④ 显式降级契约：S1_call_failed / S1_empty / S2_parse_failed / S3_truncated /
     S4_no_archive，降级也落盘（raw_response 保留供复盘）；
  ⑤ 影子期 → 转正闸门：主链路零消费，观察命中率 / 词表越界率 / 跨日映射稳定性，
     影子运行 ≥3 个交易日、降级率 ≤10%、人工抽检无错配后另行评审。

产物：data/got_daily/<date>/sector_align_shadow.json（原子写 + 回读自检，
自检失败改名 .corrupt 保留，不覆盖已有好文件）。

用法：
  python scripts/shadow_sector_align.py                 # 最新归档日
  python scripts/shadow_sector_align.py --date 20260924
  python scripts/shadow_sector_align.py --dry-run       # 只复算未命中，不调 LLM

退出码：0 = ok / no_unmatched / dry_run；1 = 降级态；2 = 归档缺失。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from verify_common import (  # noqa: E402
    force_utf8_stdout,
    got_daily_root,
    iter_archive_days,
    read_json,
)

# A5 同款降级状态集合（写入即视为降级日，计入降级率指标）
DEGRADED_STATUSES = {
    "S1_call_failed", "S1_empty", "S2_parse_failed", "S3_truncated", "S4_no_archive",
}

NO_MAPPING = "无映射"


# ─────────────────────────── 输入收集 ───────────────────────────

def _load_sector_flow_all(capital: dict | None) -> dict[str, float]:
    """capital.json → sector_flow_all 全量映射（缺失时返回空 dict）。"""
    if not capital:
        return {}
    sf = (capital.get("evidence") or {}).get("sector_flow") or {}
    all_map = sf.get("sector_flow_all")
    return all_map if isinstance(all_map, dict) else {}


def _collect_candidate_sectors(day_dir: Path) -> list[str]:
    """当日参与资金面打分的板块名集合（去重、保序）。

    来源与 _score_capital 的 candidates 口径对齐：
    confirmed_sectors（Node 6 确认名单）∪ ambush_pool（埋伏正式池）
    ∪ portfolio_decision.unified_scoring[].sector（实际进入打分的候选）。
    """
    seen: dict[str, None] = {}

    unified = read_json(day_dir / "unified.json") or {}
    for s in unified.get("confirmed_sectors") or []:
        name = (s or {}).get("sector_name") or ""
        if name:
            seen.setdefault(name, None)
    for a in unified.get("ambush_pool") or []:
        name = (a or {}).get("sector_name") or ""
        if name:
            seen.setdefault(name, None)

    decision = read_json(day_dir / "portfolio_decision.json") or {}
    for u in decision.get("unified_scoring") or []:
        name = (u or {}).get("sector") or ""
        if name:
            seen.setdefault(name, None)

    return list(seen.keys())


def _find_unmatched(sectors: list[str], flow_map: dict[str, float]) -> list[str]:
    """复算 stock_screener._score_capital 的三级匹配，返回未命中板块名。

    与主链路同一份 board_aliases 实现，保证影子口径与生产口径一致。
    """
    from src.got.board_aliases import normalize_sector_name, sector_name_variants

    norm_flow_map: dict[str, float] = {}
    for k in flow_map:
        norm_flow_map.setdefault(normalize_sector_name(k), 0.0)

    unmatched = []
    for sector in sectors:
        if sector in flow_map:
            continue
        if any(v in flow_map for v in sector_name_variants(sector)):
            continue
        if normalize_sector_name(sector) in norm_flow_map:
            continue
        unmatched.append(sector)
    return unmatched


# ─────────────────────────── LLM 调用（A5 ①） ───────────────────────────

def _build_prompt(unmatched: list[str], vocab: list[str]) -> str:
    vocab_text = "\n".join(f"- {v}" for v in vocab)
    sectors_text = "\n".join(f"- {s}" for s in unmatched)
    return f"""你是板块名称对齐器。任务：把「待对齐板块名」逐一映射到「受控词表」中语义相同的条目。

## 受控词表（唯一合法输出目标，共 {len(vocab)} 项）
{vocab_text}
- {NO_MAPPING}

## 待对齐板块名（共 {len(unmatched)} 个）
{sectors_text}

## 规则
1. 只有当两者指同一个同花顺/东财概念或行业时才映射（如"券商"↔"券商概念"、"白酒"↔"酿酒行业"）。
2. 词表中没有语义相同条目时，输出"{NO_MAPPING}"。禁止猜测、禁止音近/形近但语义不同的映射。
3. 输出必须是严格 JSON，不要任何解释文字，格式：
{{"mappings": [{{"sector": "待对齐板块名", "flow_key": "受控词表中的条目或{NO_MAPPING}"}}]}}
4. mappings 必须覆盖全部 {len(unmatched)} 个待对齐板块名，sector 字段原样照抄。"""


async def _call_llm(prompt: str, *, max_retries: int = 3, timeout: float = 60.0) -> tuple[str, str]:
    """单次全量调用。返回 (content, status_hint)：
    status_hint 为空串表示成功；"S3_truncated" 表示 finish_reason=length 快速失败。
    """
    from src.agent.llm_adapter import LLMToolAdapter

    adapter = LLMToolAdapter()
    messages = [{"role": "user", "content": prompt}]

    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.to_thread(
                adapter.call_text, messages,
                temperature=0.0, max_tokens=8000, timeout=timeout,
            )
            content = (response.content or "").strip()
            try:
                from src.storage import persist_llm_usage
                persist_llm_usage(
                    usage=response.usage or {},
                    model=response.model or "unknown",
                    call_type="shadow_sector_align",
                )
            except Exception:
                pass
            if getattr(response, "finish_reason", None) == "length":
                return "", "S3_truncated"
            if content:
                return content, ""
            print(f"[shadow] LLM 空响应 (attempt {attempt + 1})")
        except Exception as e:
            print(f"[shadow] LLM 调用失败 (attempt {attempt + 1}): {e}")
        if attempt < max_retries:
            await asyncio.sleep(1.0)
    return "", "S1_call_failed"


# ─────────────────────────── 解析与校验（A5 ②③） ───────────────────────────

def _parse_json_loose(text: str) -> dict:
    """多级解析容错：```json 围栏 → 直接 loads → 花括号切片。"""
    content = (text or "").strip()
    if not content:
        raise ValueError("LLM 返回空内容，无可解析的 JSON")
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if m:
        content = m.group(1)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(content[start:end + 1])


def _validate_mappings(
    parsed: dict, unmatched: list[str], vocab_set: set[str],
) -> tuple[list[dict], list[dict], list[str]]:
    """受控词表校验。返回 (mappings, out_of_vocab, warnings)。"""
    raw = parsed.get("mappings")
    if not isinstance(raw, list):
        raise ValueError("mappings 字段缺失或非列表")

    valid = {s: None for s in unmatched}
    mappings: list[dict] = []
    out_of_vocab: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for item in raw:
        if not isinstance(item, dict):
            continue
        sector = str(item.get("sector") or "").strip()
        flow_key = str(item.get("flow_key") or "").strip()
        if not sector or sector in seen:
            continue
        seen.add(sector)
        if sector not in valid:
            warnings.append(f"越界输入已忽略（不在待对齐名单）: {sector}")
            continue
        if flow_key not in vocab_set:
            out_of_vocab.append({"sector": sector, "flow_key": flow_key})
            warnings.append(f"词表越界即弃: {sector} → {flow_key}")
            continue
        mappings.append({"sector": sector, "flow_key": flow_key})

    missing = [s for s in unmatched if s not in seen]
    if missing:
        warnings.append(f"LLM 未覆盖 {len(missing)} 个板块名: {'、'.join(missing)}")
    return mappings, out_of_vocab, warnings


# ─────────────────────────── 落盘（原子写 + 回读自检） ───────────────────────────

def _atomic_write_json(path: Path, data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        json.loads(tmp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        corrupt = path.with_suffix(".json.corrupt")
        try:
            os.replace(tmp, corrupt)
        except OSError:
            pass
        print(f"[shadow] 回读自检失败，坏文件已保留为 {corrupt.name}: {e}")
        raise
    os.replace(tmp, path)


# ─────────────────────────── 主流程 ───────────────────────────

def _resolve_day_dir(requested: str | None) -> Path | None:
    root = got_daily_root()
    if requested:
        day = root / requested
        return day if day.is_dir() else None
    days = iter_archive_days()
    return days[-1] if days else None


async def run(day_dir: Path, dry_run: bool) -> dict:
    day_str = day_dir.name
    capital = read_json(day_dir / "capital.json")
    if capital is None:
        return {"run_date": day_str, "status": "S4_no_archive", "degraded": True,
                "warnings": [f"{day_str} 缺少 capital.json"]}

    flow_map = _load_sector_flow_all(capital)
    sectors = _collect_candidate_sectors(day_dir)
    result: dict = {
        "run_date": day_str,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "ok",
        "degraded": False,
        "warnings": [],
        "candidate_sector_count": len(sectors),
        "vocab_size": len(flow_map),
        "unmatched_sectors": [],
        "mappings": [],
        "out_of_vocab": [],
        "hit_rate": None,
        "out_of_vocab_rate": None,
        "note": "影子线产物：主链路零消费，转正须过 A5 同款闸门（≥3 交易日、降级率≤10%、人工抽检）",
    }

    if not flow_map:
        result.update(status="S4_no_archive", degraded=True)
        result["warnings"].append("sector_flow_all 为空，无受控词表")
        return result

    unmatched = _find_unmatched(sectors, flow_map)
    result["unmatched_sectors"] = unmatched
    print(f"[shadow] {day_str}: 候选板块 {len(sectors)} 个，词表 {len(flow_map)} 项，未命中 {len(unmatched)} 个"
          + (f": {'、'.join(unmatched)}" if unmatched else ""))

    if not unmatched:
        result["status"] = "no_unmatched"
        result["hit_rate"] = 1.0
        result["out_of_vocab_rate"] = 0.0
        return result
    if dry_run:
        result["status"] = "dry_run"
        return result

    vocab = sorted(flow_map.keys())
    prompt = _build_prompt(unmatched, vocab)
    raw, status_hint = await _call_llm(prompt)

    if status_hint or not raw:
        result.update(status=status_hint or "S1_empty", degraded=True)
        result["warnings"].append(f"LLM 调用降级: {status_hint or 'S1_empty'}")
        result["raw_response"] = raw
        return result

    vocab_set = set(vocab) | {NO_MAPPING}
    try:
        parsed = _parse_json_loose(raw)
        mappings, out_of_vocab, warnings = _validate_mappings(parsed, unmatched, vocab_set)
    except (json.JSONDecodeError, ValueError) as e:
        result.update(status="S2_parse_failed", degraded=True)
        result["warnings"].append(f"JSON 解析/校验失败: {e}")
        result["raw_response"] = raw
        return result

    result["mappings"] = mappings
    result["out_of_vocab"] = out_of_vocab
    result["warnings"].extend(warnings)
    result["hit_rate"] = round(
        sum(1 for m in mappings if m["flow_key"] != NO_MAPPING) / len(unmatched), 4
    )
    result["out_of_vocab_rate"] = round(
        len(out_of_vocab) / max(len(out_of_vocab) + len(mappings), 1), 4
    )
    return result


def main() -> int:
    force_utf8_stdout()
    parser = argparse.ArgumentParser(description="LLM 板块对齐影子线（主链路零消费）")
    parser.add_argument("--date", help="归档日 YYYYMMDD，默认最新归档日")
    parser.add_argument("--dry-run", action="store_true", help="只复算未命中板块，不调 LLM")
    args = parser.parse_args()

    day_dir = _resolve_day_dir(args.date)
    if day_dir is None:
        print(f"[shadow] 归档日不存在: {args.date or '(最新)'}")
        return 2

    result = asyncio.run(run(day_dir, args.dry_run))

    out_path = day_dir / "sector_align_shadow.json"
    try:
        _atomic_write_json(out_path, result)
    except Exception as e:
        print(f"[shadow] 落盘失败: {e}")
        return 1
    print(f"[shadow] 已写入 {out_path} (status={result['status']}, degraded={result['degraded']})")
    if result["mappings"]:
        for m in result["mappings"]:
            print(f"  {m['sector']} → {m['flow_key']}")

    return 1 if result["status"] in DEGRADED_STATUSES else 0


if __name__ == "__main__":
    sys.exit(main())
