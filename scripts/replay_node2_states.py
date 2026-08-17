"""Node 2 状态机回放验证（只读历史档，不联网）。

事后审计 data/got_daily/2026*/sector.json 中板块状态的逐日变化，
找出违反新状态机禁止边的实例（active→ambush / cooling→ambush / exhausted→ambush）。

输出：
- 非法边清单（日期、板块、前 state、后 state）
- 黄金 880328 / CXO 880729 等核心板块的逐日状态时间线
- 新旧实现对比（旧=历史档实际 state，新=应用禁止边后的理想 state）

用法：
    python scripts/replay_node2_states.py
    python scripts/replay_node2_states.py --focus 黄金,医药
"""
import argparse
import glob
import json
import os
from collections import defaultdict

GOT_DAILY_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "got_daily")

# 新状态机禁止边：前 state → 后 state 不得直接跳转
FORBIDDEN_EDGES = {
    ("active_mainline", "ambush"),
    ("high_position_cooling", "ambush"),
    ("exhausted_mainline", "ambush"),
    # 防御性禁止（保守）：exhausted/cooling 也不应直接回 active（需驻留确认）
    # 此处不强制禁止，仅报告为可疑
}
SUSPICIOUS_EDGES = {
    ("active_mainline", "exhausted_mainline"),  # 单日衰竭需确认（破位 vs 未破位）
    ("exhausted_mainline", "active_mainline"),  # 衰竭→active 需连续 2 天确认
    ("high_position_cooling", "active_mainline"),  # 冷却→active 需驻留≥2
}


def _extract_sectors(sector_json_path: str) -> dict[str, dict]:
    """从 sector.json 提取每板块 {state, strength_score, rrg, today_change, mainline_days}。"""
    with open(sector_json_path, encoding="utf-8") as f:
        ctx = json.load(f)
    result = {}

    # 各列表字段 → 默认 state 映射
    list_fields = [
        ("active_mainline", None, "active_mainline"),  # 组结构特殊处理
        ("exhausted_mainline", "exhausted_mainline", "exhausted_mainline"),
        ("ambush", "ambush", "ambush"),
        ("defensive_rotation", "defensive_rotation", "defensive_rotation"),
    ]
    for lst_name, default_state, group_state in list_fields:
        lst = ctx.get(lst_name, [])
        if lst_name == "active_mainline":
            # MainlineGroup 嵌套结构
            for group in lst:
                for member in group.get("members", []):
                    code = member.get("code")
                    if code:
                        result[code] = {
                            "name": member.get("name", group.get("group_name", "?")),
                            "state": member.get("state", "active_mainline"),
                            "strength_score": member.get("strength_score", 0),
                            "rrg": member.get("rrg"),
                            "today_change": member.get("today_change"),
                        }
        else:
            for detail in lst:
                code = detail.get("code")
                if code:
                    result[code] = {
                        "name": detail.get("name", "?"),
                        "state": default_state,
                        "strength_score": detail.get("strength_score", 0),
                        "rrg": detail.get("rrg"),
                        "today_change": detail.get("today_change"),
                    }

    # cooling_down（2026-08-17 后才有，旧档无该字段）
    for detail in ctx.get("cooling_down", []):
        code = detail.get("code")
        if code:
            result[code] = {
                "name": detail.get("name", "?"),
                "state": "high_position_cooling",
                "strength_score": detail.get("strength_score", 0),
                "rrg": detail.get("rrg"),
                "today_change": detail.get("today_change"),
            }

    # 兜底：top_by_score（state 可能非上述）
    for detail in ctx.get("top_by_score", []):
        code = detail.get("code")
        if code and code not in result:
            result[code] = {
                "name": detail.get("name", "?"),
                "state": detail.get("state", "neutral"),
                "strength_score": detail.get("strength_score", 0),
                "rrg": detail.get("rrg"),
                "today_change": detail.get("today_change"),
            }

    return result


def _clean_name(name: str) -> str:
    """清洗板块名（去除  填充）。"""
    return name.replace("", "").strip()


def main():
    parser = argparse.ArgumentParser(description="Node 2 状态机回放验证")
    parser.add_argument("--focus", default="", help="逗号分隔的关键词，过滤板块名（如 黄金,医药）")
    args = parser.parse_args()
    focus_keywords = [k for k in args.focus.split(",") if k]

    archive_dirs = sorted(glob.glob(os.path.join(GOT_DAILY_ROOT, "20*")))
    archive_dirs = [d for d in archive_dirs if os.path.isdir(d)]
    print(f"[回放] 扫描 {len(archive_dirs)} 个历史档: {GOT_DAILY_ROOT}")

    # 追踪每板块的逐日状态变化
    # timeline[code] = [(date_str, state, score, rrg_quadrant, today_change)]
    timeline: dict[str, list] = defaultdict(list)
    forbidden_violations: list[tuple] = []
    suspicious_violations: list[tuple] = []
    prev_state_by_code: dict[str, str] = {}

    for archive_dir in archive_dirs:
        date_str = os.path.basename(archive_dir)
        sector_json = os.path.join(archive_dir, "sector.json")
        if not os.path.isfile(sector_json):
            continue

        current = _extract_sectors(sector_json)
        for code, info in current.items():
            name = _clean_name(info["name"])
            state = info["state"]
            score = info["strength_score"]
            rrg = info["rrg"] or {}
            quadrant = rrg.get("quadrant", "?")
            today_change = info.get("today_change")
            timeline[code].append((date_str, name, state, score, quadrant, today_change))

            prev = prev_state_by_code.get(code)
            if prev is not None and prev != state:
                edge = (prev, state)
                if edge in FORBIDDEN_EDGES:
                    forbidden_violations.append((date_str, name, code, prev, state, score, quadrant))
                elif edge in SUSPICIOUS_EDGES:
                    suspicious_violations.append((date_str, name, code, prev, state, score, quadrant))

            prev_state_by_code[code] = state

    # 输出非法边
    print(f"\n{'=' * 80}")
    print(f"⚠️  违反新状态机禁止边的实例（{len(forbidden_violations)} 个）：")
    print(f"{'=' * 80}")
    if not forbidden_violations:
        print("✅ 无非法边，新状态机规则与历史数据完全兼容")
    else:
        print(f"{'日期':<10} {'板块':<20} {'代码':<8} {'前 state':<24} {'后 state':<24} {'score':<6} {'quadrant'}")
        print("-" * 100)
        for date_str, name, code, prev, new, score, quadrant in forbidden_violations:
            print(f"{date_str:<10} {name:<20} {code:<8} {prev:<24} {new:<24} {score:<6.1f} {quadrant}")

    # 输出可疑边
    print(f"\n{'=' * 80}")
    print(f"🔍 可疑边（需驻留确认但历史可能缺失 confirm）：{len(suspicious_violations)} 个")
    print(f"{'=' * 80}")
    if suspicious_violations:
        for date_str, name, code, prev, new, score, quadrant in suspicious_violations[:15]:
            print(f"{date_str:<10} {name:<20} {code:<8} {prev}→{new:<16} score={score:.1f} {quadrant}")

    # 核心板块时间线
    print(f"\n{'=' * 80}")
    print("📊 核心板块逐日状态时间线：")
    print(f"{'=' * 80}")
    target_codes = [
        "880328",  # 黄金
        "880521",  # 黄金概念
        "880729",  # CXO概念
        "880652",  # 创新药
        "880400",  # 医药
        "880681",  # 减肥药
        "880530",  # 合成生物
    ]
    for code in target_codes:
        if code not in timeline:
            continue
        entries = timeline[code]
        name = entries[0][1]
        if focus_keywords and not any(k in name for k in focus_keywords):
            continue
        print(f"\n▶ {name} ({code})")
        print(f"  {'日期':<10} {'state':<26} {'score':<6} {'quadrant':<12} {'T-0'}")
        for date_str, _name, state, score, quadrant, today_change in entries:
            t0 = f"{today_change:+.2f}%" if today_change is not None else "?"
            print(f"  {date_str:<10} {state:<26} {score:<6.1f} {quadrant:<12} {t0}")

    # 汇总
    print(f"\n{'=' * 80}")
    print("📋 汇总：")
    total_boards = len(timeline)
    print(f"  总板块数: {total_boards}")
    print(f"  非法边（active→ambush/cooling→ambush/exhausted→ambush）: {len(forbidden_violations)}")
    print(f"  可疑边（未经驻留确认的切换）: {len(suspicious_violations)}")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
