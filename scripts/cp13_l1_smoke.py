# -*- coding: utf-8 -*-
"""C-P13 阶段1 冒烟验证：L1 检索接地的已知病例核对（矩阵跑批的前置闸门）。

背景（docs/got/archive/cp13_grounding_pipeline_research_20260926.md §1/§5.3）：
- A 版漏挂病例：0910「美伊冲突」→ 油运/航运关系为空；
- B 版摊派病例：0903「房贷信贷」→ 24 板（含减肥药/短剧游戏）。
L1 是整条管线的地基：若 top-K 检索本身漏掉直接项，管线就从「治漏挂」变成「制造漏挂」。
本脚本只对归档日跑 L1（embedding + cosine top-K），打印逐事件候选与自动核对结果，
供人工确认后再进入 0901–0914 矩阵跑批。

用法（需先配 GLUE_EMBEDDING_API_KEY，见 .env.example C-P13 段）：
    python scripts/cp13_l1_smoke.py                       # 默认 0910 + 0903 两个病例日
    python scripts/cp13_l1_smoke.py --dates 20260910 --top-k 15
    python scripts/cp13_l1_smoke.py --skip-checks         # 只出人工核对表，不做自动判定

退出码：0 = 自动核对全部通过；1 = 存在未通过项（先调 embedding 文本构造再验）；
2 = 运行失败（归档缺失 / embedding 不可用）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# catalyst_config 在 import 时读 os.environ，必须先加载 .env（与 main.py 同源入口）
from src.config import setup_env  # noqa: E402

setup_env()

from src.got import glue_pipeline as gp  # noqa: E402
from src.got.archive import load_daily_contexts  # noqa: E402
from src.got.nodes.aggregator import AggregatorNode  # noqa: E402

# 已知病例自动核对（关键词为启发式，最终判读归人工——脚本只搬运事实）：
# include: 命中该标题关键词的事件，top-K 板块名中必须出现任一 must_include 关键词；
# exclude: 命中该标题关键词的事件，top-K 板块名中不得出现任何 must_exclude 关键词。
CASE_CHECKS: dict[str, list[dict]] = {
    "20260910": [
        {
            "name": "A 版漏挂病例：美伊冲突/油价 → 油运/航运必须进 top-K",
            "title_keywords": ["美伊", "油价", "原油", "冲突"],
            "must_include": ["航运", "水运", "油运", "港口", "石油"],
        },
    ],
    "20260903": [
        {
            "name": "B 版摊派病例：房贷/信贷 → 减肥药、短剧游戏不得进 top-K",
            "title_keywords": ["房贷", "信贷", "利率"],
            "must_exclude": ["减肥", "短剧", "游戏"],
        },
    ],
}


def _collect_day_inputs(date_str: str):
    """从归档还原 glue 同款输入：candidates（Node 6 候选收集）+ 管线事件集。"""
    run_date = datetime.strptime(date_str, "%Y%m%d").date()
    contexts = load_daily_contexts(run_date)
    if "sector" not in contexts or "news" not in contexts:
        raise FileNotFoundError(f"{date_str}: 归档缺 sector/news context，无法冒烟")
    node = AggregatorNode(use_mock=False)
    candidates, _ = node._collect_sector_candidates(contexts["sector"], None)
    events = node._collect_pipeline_events(contexts["news"])
    return run_date, candidates, events


def _print_topk_table(events, topk_by_event, sector_names, top_k):
    print(f"\n  逐事件 top-{top_k} 候选（共 {len(events)} 事件）：")
    for ev in events:
        eid = str(ev.get("event_id", ""))
        topk = topk_by_event.get(eid, [])
        title = str(ev.get("title", ""))[:42]
        boards = "、".join(
            f"{sector_names.get(c, c)}({s:.3f})" for c, s in topk[:top_k]
        )
        print(f"  - [{eid}] {title}")
        print(f"      → {boards or '（无候选）'}")


def _run_checks(date_str, events, topk_by_event, sector_names) -> tuple[int, int]:
    """执行 CASE_CHECKS 自动核对，返回 (通过数, 失败数)。"""
    checks = CASE_CHECKS.get(date_str, [])
    if not checks:
        print("\n  （该日无预置病例核对项，仅出人工核对表）")
        return 0, 0
    passed = failed = 0
    print("\n  自动核对（启发式关键词，最终判读归人工）：")
    for check in checks:
        hit_events = [
            ev for ev in events
            if any(kw in str(ev.get("title", "")) for kw in check["title_keywords"])
        ]
        if not hit_events:
            print(f"  [SKIP] {check['name']} —— 当日无命中标题关键词的事件")
            continue
        ok = True
        for ev in hit_events:
            eid = str(ev.get("event_id", ""))
            names = [sector_names.get(c, c) for c, _ in topk_by_event.get(eid, [])]
            if "must_include" in check:
                if not any(kw in n for n in names for kw in check["must_include"]):
                    ok = False
                    print(f"    ✗ [{eid}] top-K 未含任一必挂关键词 {check['must_include']}：{names}")
            if "must_exclude" in check:
                bad = [n for n in names if any(kw in n for kw in check["must_exclude"])]
                if bad:
                    ok = False
                    print(f"    ✗ [{eid}] top-K 混入摊派板块 {bad}")
        if ok:
            passed += 1
            print(f"  [PASS] {check['name']}（命中 {len(hit_events)} 事件）")
        else:
            failed += 1
            print(f"  [FAIL] {check['name']}（命中 {len(hit_events)} 事件）")
    return passed, failed


async def _smoke_one(date_str: str, top_k: int, skip_checks: bool) -> int:
    """单日冒烟，返回该日失败核对数（运行异常向上抛）。"""
    print(f"\n{'=' * 70}\n冒烟日 {date_str}\n{'=' * 70}")
    run_date, candidates, events = _collect_day_inputs(date_str)
    print(f"  候选板块 {len(candidates)} 个，管线事件 {len(events)} 个")
    if not events:
        print("  [WARN] 当日无事件，跳过")
        return 0

    index = await gp.build_l1_index(candidates, events, run_date, top_k=top_k)
    print(f"  embedding 模型={index.model}，缓存命中 {index.cache_hits} / 新算 {index.cache_misses}")
    _print_topk_table(events, index.topk_by_event, index.sector_names, top_k)
    if skip_checks:
        return 0
    _, failed = _run_checks(date_str, events, index.topk_by_event, index.sector_names)
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description="C-P13 L1 检索接地冒烟验证（矩阵跑批前置闸门）")
    parser.add_argument("--dates", nargs="*", default=["20260910", "20260903"],
                        help="冒烟归档日 YYYYMMDD（默认两个已知病例日）")
    parser.add_argument("--top-k", type=int, default=None,
                        help="覆盖 GLUE_L1_TOP_K（默认用配置值，K 值初选依据见输出分数）")
    parser.add_argument("--skip-checks", action="store_true",
                        help="只出人工核对表，不做自动判定")
    args = parser.parse_args()

    top_k = args.top_k or gp.GLUE_L1_TOP_K
    total_failed = 0
    try:
        for date_str in args.dates:
            total_failed += asyncio.run(_smoke_one(date_str, top_k, args.skip_checks))
    except RuntimeError as e:
        print(f"\n[FAIL] 运行失败: {e}", file=sys.stderr)
        print("  提示：L1 需要 embedding 服务——检查 GLUE_EMBEDDING_API_KEY / "
              "GLUE_EMBEDDING_BASE_URL / GLUE_EMBEDDING_MODEL（.env.example C-P13 段）",
              file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"\n[FAIL] {e}", file=sys.stderr)
        return 2

    print(f"\n{'=' * 70}")
    if args.skip_checks:
        print("冒烟完成（--skip-checks：自动核对已跳过，人工判读上方 top-K 表）")
        return 0
    if total_failed:
        print(f"[FAIL] {total_failed} 项病例核对未通过 —— 按计划先调 embedding 文本构造"
              f"（build_sector_text/build_event_text）再验，不得进入矩阵跑批")
        return 1
    print("[PASS] 病例核对全部通过 —— 可进入 0901–0914 矩阵跑批"
          "（GLUE_PIPELINE_ENABLED=true + GLUE_REPLAY_VARIANT=pipeline_l1l3）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
