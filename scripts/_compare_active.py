"""深入对比：active_mainline 成员变化、黄金全轨迹"""
import json
import os
os.chdir(r"E:\daily_stock_analysis")

old_path = "data/got_daily/20260817_pre_fix/sector.json"
new_path = "data/got_daily/20260817/sector.json"

with open(old_path, encoding="utf-8") as f:
    old = json.load(f)
with open(new_path, encoding="utf-8") as f:
    new = json.load(f)

def get_active_members(ctx):
    members = {}
    for group in ctx.get("active_mainline", []):
        for m in group.get("members", []):
            code = m.get("code")
            if code:
                members[code] = {
                    "name": m.get("name", "").replace("\x00", "").strip(),
                    "score": m.get("strength_score", 0),
                    "group": group.get("group_name", ""),
                }
    return members

old_active = get_active_members(old)
new_active = get_active_members(new)

print("=" * 80)
print(f"📊 active_mainline 成员对比（旧 {len(old_active)} vs 新 {len(new_active)}）")
print("=" * 80)

# 新增成员（新有旧无）
added = set(new_active.keys()) - set(old_active.keys())
removed = set(old_active.keys()) - set(new_active.keys())
common = set(new_active.keys()) & set(old_active.keys())

print(f"\n【新增成员】（{len(added)} 个）")
for code in sorted(added, key=lambda c: new_active[c]["score"], reverse=True):
    info = new_active[code]
    print(f"  + {info['name']:<15} {code:<8} score={info['score']:.1f}  group={info['group']}")

print(f"\n【移除成员】（{len(removed)} 个）")
for code in sorted(removed, key=lambda c: old_active[c]["score"], reverse=True):
    info = old_active[code]
    print(f"  - {info['name']:<15} {code:<8} score={info['score']:.1f}  group={info['group']}")

# 共同成员但分数变化大
print(f"\n【共同成员分数变化 Top 5】（共 {len(common)} 个共同成员）")
score_changes = []
for code in common:
    old_s = old_active[code]["score"]
    new_s = new_active[code]["score"]
    name = new_active[code]["name"]
    score_changes.append((name, code, old_s, new_s, new_s - old_s))
score_changes.sort(key=lambda x: abs(x[4]), reverse=True)
for name, code, old_s, new_s, diff in score_changes[:5]:
    print(f"  {name:<15} {code:<8} {old_s:.1f} → {new_s:.1f} (diff {diff:+.1f})")

# 黄金详细（从旧/新档里找）
print("\n" + "=" * 80)
print("🔍 黄金板块详细（旧档 vs 新档）")
print("=" * 80)

def find_board(ctx, target_codes):
    for key in ["active_mainline", "exhausted_mainline", "ambush", "defensive_rotation",
                "cooling_down", "top_by_score", "industry_top"]:
        lst = ctx.get(key, [])
        if key == "active_mainline":
            for group in lst:
                for m in group.get("members", []):
                    if m.get("code") in target_codes:
                        return m
        else:
            for d in lst:
                if d.get("code") in target_codes:
                    return d
    return None

target = {"880328", "880521"}
for code in target:
    old_b = find_board(old, {code})
    new_b = find_board(new, {code})
    print(f"\n[{code}]")
    if old_b:
        print(f"  旧: state={old_b.get('state')}, score={old_b.get('strength_score')}, rrg={old_b.get('rrg', {}).get('quadrant')}")
        print(f"      mainline_days={old_b.get('mainline_days')}, lifecycle={old_b.get('lifecycle', {}).get('phase') if old_b.get('lifecycle') else None}")
    else:
        print(f"  旧: (不在任何列表)")
    if new_b:
        print(f"  新: state={new_b.get('state')}, score={new_b.get('strength_score')}, rrg={new_b.get('rrg', {}).get('quadrant')}")
        print(f"      mainline_days={new_b.get('mainline_days')}, lifecycle={new_b.get('lifecycle', {}).get('phase') if new_b.get('lifecycle') else None}")
    else:
        print(f"  新: (不在任何列表)")
