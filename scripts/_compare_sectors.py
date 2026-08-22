"""对比新旧 sector.json（状态机修复前后）"""
import json
import os
os.chdir(r"E:\daily_stock_analysis")

old_path = "data/got_daily/20260817_pre_fix/sector.json"
new_path = "data/got_daily/20260817/sector.json"

with open(old_path, encoding="utf-8") as f:
    old = json.load(f)
with open(new_path, encoding="utf-8") as f:
    new = json.load(f)

def extract_boards(ctx, key):
    """从 ctx 提取指定列表的板块 {code: {name, state, score}}"""
    result = {}
    lst = ctx.get(key, [])
    if key == "active_mainline":
        for group in lst:
            for m in group.get("members", []):
                code = m.get("code")
                if code:
                    result[code] = {
                        "name": m.get("name", "").replace("\x00", "").strip(),
                        "state": m.get("state", "active_mainline"),
                        "score": m.get("strength_score", 0),
                        "rrg": m.get("rrg", {}).get("quadrant", "?"),
                    }
    else:
        state_map = {
            "exhausted_mainline": "exhausted_mainline",
            "ambush": "ambush",
            "defensive_rotation": "defensive_rotation",
            "cooling_down": "high_position_cooling",
            "top_by_score": None,
        }
        for d in lst:
            code = d.get("code")
            if code:
                result[code] = {
                    "name": d.get("name", "").replace("\x00", "").strip(),
                    "state": d.get("state", state_map.get(key, "?")),
                    "score": d.get("strength_score", 0),
                    "rrg": d.get("rrg", {}).get("quadrant", "?") if d.get("rrg") else "?",
                }
    return result

print("=" * 80)
print("📊 新旧 sector.json 对比")
print("=" * 80)

# 各列表成员数量
lists = ["active_mainline", "exhausted_mainline", "ambush", "defensive_rotation",
         "cooling_down", "top_by_score"]
print("\n【各列表成员数量】")
print(f"{'列表':<25} {'旧':<6} {'新':<6} {'变化'}")
print("-" * 50)
for k in lists:
    old_n = len(extract_boards(old, k))
    new_n = len(extract_boards(new, k))
    diff = new_n - old_n
    diff_str = f"{diff:+d}" if diff else "="
    print(f"{k:<25} {old_n:<6} {new_n:<6} {diff_str}")

# 核心板块状态变化
print("\n【核心板块状态变化（黄金/医药/CXO/创新药）】")
core_keywords = ["黄金", "医药", "创新药", "CXO", "减肥药", "合成生物", "医疗保健"]
old_all = {}
for k in lists:
    old_all.update(extract_boards(old, k))
new_all = {}
for k in lists:
    new_all.update(extract_boards(new, k))

print(f"{'板块':<15} {'代码':<8} {'旧 state':<26} {'新 state':<26} {'分→分':<12} {'象限'}")
print("-" * 110)
for code, info in sorted(new_all.items()):
    name = info["name"]
    if not any(kw in name for kw in core_keywords):
        continue
    old_info = old_all.get(code, {})
    old_state = old_info.get("state", "(absent)")
    new_state = info["state"]
    old_score = old_info.get("score", 0)
    new_score = info["score"]
    rrg = info.get("rrg", "?")
    changed = "✅" if old_state == new_state else "🔄"
    print(f"{changed} {name:<13} {code:<8} {old_state:<26} {new_state:<26} {old_score:.0f}→{new_score:.0f}   {rrg}")

# 新出现的 high_position_cooling 板块
print("\n【新增 cooling_down 列表（状态机新增槽位）】")
new_cooling = extract_boards(new, "cooling_down")
if new_cooling:
    for code, info in sorted(new_cooling.items(), key=lambda x: x[1]["score"], reverse=True):
        old_state = old_all.get(code, {}).get("state", "(absent)")
        print(f"  {info['name']:<20} {code:<8} score={info['score']:.1f}  rrg={info['rrg']:<10} 旧状态={old_state}")
else:
    print("  (空)")

# 检查非法边是否消除
print("\n【非法边检查（active→ambush, exhausted→ambush）】")
forbidden = {("active_mainline", "ambush"), ("exhausted_mainline", "ambush"),
             ("high_position_cooling", "ambush")}
violations = []
for code, info in new_all.items():
    old_info = old_all.get(code, {})
    old_state = old_info.get("state")
    if old_state is None:
        continue
    new_state = info["state"]
    if (old_state, new_state) in forbidden:
        violations.append((info["name"], code, old_state, new_state, info["score"]))

if violations:
    print(f"  ⚠️  仍有 {len(violations)} 条非法边：")
    for name, code, old_s, new_s, score in violations:
        print(f"    {name} ({code}): {old_s} → {new_s} (score={score})")
else:
    print("  ✅ 无非法边，新状态机规则完全生效")
