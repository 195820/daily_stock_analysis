#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§7 #12 WI：家族兜底阈值 (S,M) 网格标定脚本。

背景：
- family_embed.py V2 打分 = cos_semantic*(1-W) + keyword_overlap*W（W=0.4）
- 保守阈值：score ≥ S 且 margin ≥ M 才兜底，否则维持「其他」
- 现值 S=0.55, M=0.05 是工程启发式，未经 shadow 数据标定
- 实测 10 归档日 93 个「其他」板块 93/93 全 low_score 被拒（score 最高 0.5143）
  → 现值下 enabled 转正即空转

目标：
- 扫全部归档日「其他」板块（消毒+过滤纪律）
- 对 (S,M) 网格 S∈{0.45,0.46,...,0.50}×M∈{0.03,0.04,0.05}
- 报告各档通过数/通过率/边际家族分布
- 输出推荐 (S,M)（通过率落在 5%~20% 区间且 margin 有分辨力的最小 S）

数据源限制：
- 归档 sector.json 不含 cosine 分数（family_embed 历史默认 off）
- 本脚本用 hash-based 伪 cosine 模拟每板块-家族对的方差（确定性可复现）
- 真实标定需开启 GOT_FAMILY_SCORING_V2=1 + GOT_FAMILY_EMBED_FALLBACK=shadow
  跑 ≥5 个交易日，从 shadow 输出提取 cos+kw 分项后复跑本脚本

使用：
    python scripts/calibrate_family_threshold.py
    python scripts/calibrate_family_threshold.py --keyword-weight 0.4
"""
import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.got.family_keywords import FAMILY_KEYWORDS
from src.got.family_embed import (
    FALLBACK_FAMILIES,
    OTHER_FAMILY,
    compute_keyword_overlap,
)


def load_other_boards() -> list[dict[str, Any]]:
    """从归档提取所有「其他」板块（消毒+过滤纪律）。

    过滤纪律：
    - 目录名 fullmatch 20\\d{6}（排除 _backup/_pre_fix/_cache 等）
    - 文件内容 text.replace('\\x00','') 消毒（0917 归档损坏）
    - try/except 跳过损坏归档
    """
    boards: list[dict[str, Any]] = []
    root = PROJECT_ROOT / "data" / "got_daily"
    if not root.exists():
        return boards

    for d in sorted(root.iterdir()):
        if not re.fullmatch(r"20\d{6}", d.name):
            continue
        p = d / "sector.json"
        if not p.exists():
            continue
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw.replace("\x00", ""))
        except (json.JSONDecodeError, OSError):
            continue

        for key in ["active_mainline", "exhausted_mainline", "ambush",
                     "defensive_rotation", "top_by_score"]:
            items = data.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("frame_group") == OTHER_FAMILY:
                    boards.append({
                        "date": d.name,
                        "name": item.get("name", ""),
                        "code": item.get("code", ""),
                    })
    return boards


def _pseudo_cosine(board_name: str, family: str) -> float:
    """确定性伪 cosine 相似度（hash-based，[0,0.55] 区间）。

    用于模拟真实 embedding 空间的方差：不同板块对不同家族有不同的语义距离。
    「其他」板块的 cosine 通常较低（实测 max=0.5143），故上限设为 0.55。
    真实标定需替换为实际 cosine 分数。
    """
    h = hashlib.md5(f"{board_name}|{family}".encode()).hexdigest()
    # 取前 8 位 hex → [0, 0.55]（模拟「其他」板块的低 cosine 分布）
    return int(h[:8], 16) / 0xFFFFFFFF * 0.55


def compute_v2_scores(
    board_name: str,
    keyword_map: dict[str, list[str]],
    keyword_weight: float,
) -> list[tuple[str, float, float, float]]:
    """V2 打分：返回 (family, combined, cos, kw_overlap) 排序列表。"""
    scores: list[tuple[str, float, float, float]] = []
    for fam in FALLBACK_FAMILIES:
        cos = _pseudo_cosine(board_name, fam)
        kw_overlap = compute_keyword_overlap(board_name, keyword_map.get(fam, []))
        combined = cos * (1.0 - keyword_weight) + kw_overlap * keyword_weight
        scores.append((fam, round(combined, 4), round(cos, 4), round(kw_overlap, 4)))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


def grid_search(
    boards: list[dict[str, Any]],
    keyword_map: dict[str, list[str]],
    keyword_weight: float,
    s_values: list[float],
    m_values: list[float],
) -> dict[tuple[float, float], dict[str, Any]]:
    """(S,M) 网格搜索，返回各档统计。"""
    results: dict[tuple[float, float], dict[str, Any]] = {}

    # 预计算所有板块的分数（避免重复计算）
    all_scores: list[list[tuple[str, float, float, float]]] = []
    for board in boards:
        all_scores.append(compute_v2_scores(board["name"], keyword_map, keyword_weight))

    for s in s_values:
        for m in m_values:
            pass_count = 0
            family_dist: dict[str, int] = defaultdict(int)
            margin_values: list[float] = []

            for scores in all_scores:
                if not scores:
                    continue
                best_fam, best_score = scores[0][0], scores[0][1]
                runner_score = scores[1][1] if len(scores) > 1 else 0.0
                margin = best_score - runner_score

                if best_score >= s and margin >= m:
                    pass_count += 1
                    family_dist[best_fam] += 1
                    margin_values.append(margin)

            total = len(boards)
            pass_rate = pass_count / total if total > 0 else 0.0
            avg_margin = sum(margin_values) / len(margin_values) if margin_values else 0.0

            results[(s, m)] = {
                "pass_count": pass_count,
                "pass_rate": pass_rate,
                "family_dist": dict(family_dist),
                "avg_margin": avg_margin,
                "total": total,
            }

    return results


def recommend_threshold(results: dict[tuple[float, float], dict[str, Any]]) -> tuple[float, float] | None:
    """推荐 (S,M)：通过率 5%~20% 且 margin 有分辨力的最小 S。"""
    candidates: list[tuple[float, float, float]] = []

    for (s, m), stats in results.items():
        rate = stats["pass_rate"]
        avg_margin = stats["avg_margin"]
        if 0.05 <= rate <= 0.20 and avg_margin > 0.02:
            candidates.append((s, m, avg_margin))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]))
    return (candidates[0][0], candidates[0][1])


def main() -> int:
    ap = argparse.ArgumentParser(description="家族兜底阈值 (S,M) 网格标定")
    ap.add_argument("--keyword-weight", type=float, default=0.4,
                    help="关键词权重 W（默认 0.4，cos 权重 = 1-W）")
    args = ap.parse_args()

    keyword_weight = args.keyword_weight
    keyword_map = dict(FAMILY_KEYWORDS)

    print("=" * 70)
    print("§7 #12 WI：家族兜底阈值 (S,M) 网格标定")
    print("=" * 70)
    print()
    print(f"关键词权重 W = {keyword_weight}（cos 权重 = {1 - keyword_weight}）")
    print(f"兜底候选家族：{len(FALLBACK_FAMILIES)} 个")
    print(f"cosine 模拟：hash-based 伪 cosine（确定性可复现，真实标定需替换为实际分数）")
    print()

    boards = load_other_boards()
    if not boards:
        print("⚠ 归档中无「其他」板块，无法标定")
        return 1

    # 去重（同日同名板块）
    seen: set[tuple[str, str]] = set()
    unique_boards: list[dict[str, Any]] = []
    for b in boards:
        key = (b["date"], b["name"])
        if key not in seen:
            seen.add(key)
            unique_boards.append(b)
    boards = unique_boards

    print(f"归档「其他」板块：{len(boards)} 个（去重后）")
    print(f"日期范围：{min(b['date'] for b in boards)} – {max(b['date'] for b in boards)}")
    print()

    # 分数分布预览
    sample_scores = compute_v2_scores("混合现实", keyword_map, keyword_weight)
    print("分数分布预览（板块='混合现实'）：")
    for fam, combined, cos, kw in sample_scores[:5]:
        print(f"  {fam}: combined={combined:.4f} (cos={cos:.4f}, kw={kw:.4f})")
    print()

    s_values = [0.45, 0.46, 0.47, 0.48, 0.49, 0.50]
    m_values = [0.03, 0.04, 0.05]

    results = grid_search(boards, keyword_map, keyword_weight, s_values, m_values)

    print(f"{'S':>6} | {'M':>6} | {'通过数':>7} | {'通过率':>7} | {'平均margin':>10} | 边际家族 Top-3")
    print("-" * 90)

    for s in s_values:
        for m in m_values:
            stats = results[(s, m)]
            family_dist_str = ", ".join(
                f"{fam}:{cnt}"
                for fam, cnt in sorted(stats["family_dist"].items(), key=lambda x: -x[1])[:3]
            )
            print(f"{s:>6.2f} | {m:>6.2f} | {stats['pass_count']:>7} | "
                  f"{stats['pass_rate']:>6.1%} | {stats['avg_margin']:>10.4f} | {family_dist_str}")

    print()

    recommended = recommend_threshold(results)
    if recommended:
        s_rec, m_rec = recommended
        stats_rec = results[(s_rec, m_rec)]
        print(f"✓ 推荐阈值：S={s_rec:.2f}, M={m_rec:.2f}")
        print(f"  通过率：{stats_rec['pass_rate']:.1%}（{stats_rec['pass_count']}/{stats_rec['total']}）")
        print(f"  平均 margin：{stats_rec['avg_margin']:.4f}")
        top_fams = sorted(stats_rec["family_dist"].items(), key=lambda x: -x[1])[:5]
        print(f"  边际家族 Top-5：{', '.join(f'{f}:{c}' for f, c in top_fams)}")
    else:
        print("⚠ 无推荐阈值（通过率 5%~20% 且 margin>0.02 的档位不存在）")

    print()
    print("=" * 70)
    print("下一步：")
    print("1. 开启 shadow 模式：GOT_FAMILY_SCORING_V2=1 + GOT_FAMILY_EMBED_FALLBACK=shadow")
    print("2. 跑 ≥5 个交易日，积累 shadow 数据")
    print("3. 从 data/got_daily/YYYYMMDD/sector.json 提取 family_embed_shadow 字段")
    print("4. 修改本脚本 _pseudo_cosine → 实际 cos_score，复跑真实标定")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
