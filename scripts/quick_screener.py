# -*- coding: utf-8 -*-
"""
GOT Phase 2 快速选股 —— 基于 Node 6 四维验证结果
读 unified.json 的 confirmed_sectors → 按 action 构造问财 query → Top 3 + 仓位
"""

import json
import logging
import sys
from pathlib import Path
from datetime import datetime

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows GBK 终端编码兼容
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-7s | %(name)s | %(message)s')
logger = logging.getLogger(__name__)

# ── 1. 读 Node 6 输出 ────────────────────────────────────────
GOT_DATE = "20260803"
GOT_DIR = Path("data/got_daily") / GOT_DATE

with open(GOT_DIR / "unified.json", "r", encoding="utf-8") as f:
    unified_data = json.load(f)

signal = unified_data.get("unified_signal", "neutral")
total_position = unified_data.get("final_position_limit", 0.2)
confirmed_sectors = unified_data.get("confirmed_sectors", [])

logger.info(f"统一信号: {signal}, 仓位上限: {total_position}")
logger.info(f"四维验证板块: {len(confirmed_sectors)} 个")

if not confirmed_sectors:
    logger.warning("confirmed_sectors 为空！请确认 Node 6 Phase D 是否已运行。")
    logger.info("尝试直接从 sector.json 读取（降级模式）...")
    # 降级：直接读 sector.json
    with open(GOT_DIR / "sector.json", "r", encoding="utf-8") as f:
        sector_data = json.load(f)
    for group in sector_data.get("active_mainline", []):
        confirmed_sectors.append({
            "sector_name": group["group_name"],
            "sector_type": "mainline_legacy",
            "action": "buy_trend",
            "position_hint": 0.2,
            "confidence": 0.5,
            "reasoning": f"降级模式: {group['group_name']} (score={group['group_score']})",
            "trend_check": "unknown",
            "trend_detail": "",
            "capital_check": "unknown",
            "capital_detail": "",
            "environment_check": "unknown",
            "environment_detail": "",
            "news_check": "unknown",
            "news_detail": None,
        })

# ── 2. 筛选可执行板块 + 构造问财 query ────────────────────────
from src.services.ths_screener import THSScreenerService
from src.config import Config

config = Config()
ths = THSScreenerService(config)

BASE_CONDITIONS = "非ST非壳非跌停非涨停，上市天数>365天"

def build_query(cs: dict) -> str:
    """根据 action 构造问财 query"""
    name = cs["sector_name"]
    action = cs["action"]
    if action == "buy_trend":
        return f"{name}板块成分股，主力资金净流入>0，近5日涨幅>0%，量比>0.8，{BASE_CONDITIONS}"
    elif action == "buy_dip":
        return f"{name}板块成分股，主力资金净流入>0，今日涨幅<2%，今日涨幅>-3%，{BASE_CONDITIONS}"
    return ""

# 过滤: 只查 buy_trend / buy_dip 的板块
actionable = [cs for cs in confirmed_sectors if cs["action"] in ("buy_trend", "buy_dip")]
logger.info(f"可执行板块: {len(actionable)} 个 (buy_trend + buy_dip)")

if not actionable:
    print("\n[!] Node 6 四维验证结果中无可执行板块（全部 watch/avoid）。建议空仓观望。")
    sys.exit(0)

# ── 3. 执行问财查询 ──────────────────────────────────────────
all_candidates = []

for cs in actionable:
    query = build_query(cs)
    if not query:
        continue
    logger.info(f"[{cs['sector_name']}] ({cs['action']}) 问财查询: {query}")
    result = ths.query(query, use_cache=False)
    if result.error:
        logger.warning(f"[{cs['sector_name']}] 查询失败: {result.error}")
        continue
    for stock in result.stocks:
        all_candidates.append({
            "code": stock.code,
            "name": stock.name,
            "ths_rank": stock.rank,
            "sector_name": cs["sector_name"],
            "action": cs["action"],
            "sector_confidence": cs.get("confidence", 0.5),
            "reasoning": cs.get("reasoning", ""),
        })
    logger.info(f"[{cs['sector_name']}] 获取 {result.total_count} 只")

logger.info(f"全量候选: {len(all_candidates)} 只")

# ── 4. 去重 + 排序 ──────────────────────────────────────────
stock_map = {}
for c in all_candidates:
    key = c["code"]
    if key not in stock_map:
        stock_map[key] = c
    else:
        existing = stock_map[key]
        if c["sector_confidence"] > existing["sector_confidence"]:
            stock_map[key] = c

# 综合评分：板块置信度 × 0.7 + (1/排名) × 0.3（归一化）
for stock in stock_map.values():
    rank_score = 1.0 / max(stock["ths_rank"], 1)
    stock["composite_score"] = stock["sector_confidence"] * 0.7 + rank_score * 0.3

ranked = sorted(stock_map.values(), key=lambda x: x["composite_score"], reverse=True)

# ── 5. Top 3 + 仓位分配 ─────────────────────────────────────
top3 = ranked[:3]

if top3:
    total_score = sum(s["composite_score"] for s in top3)
    for s in top3:
        s["position_pct"] = round(
            total_position * (s["composite_score"] / total_score) * 100, 1
        )

# ── 6. 主营业务获取 ──────────────────────────────────────────
def fetch_main_business(stock_code: str) -> str:
    """获取主营业务（一行摘要，截断60字）"""
    try:
        import akshare as ak
        df = ak.stock_individual_info_em(symbol=stock_code)
        row = df[df["item"] == "主营业务"]
        if not row.empty:
            return str(row.iloc[0]["value"])[:60]
    except Exception as e:
        logger.debug(f"[主营业务] {stock_code} 获取失败: {e}")
    return "暂无数据"

logger.info("获取 Top 3 主营业务...")
for s in top3:
    s["main_business"] = fetch_main_business(s["code"])

# ── 7. 输出 ─────────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"GOT Phase 2 选股结果 | {GOT_DATE} | 信号: {signal} | 总仓位: {total_position*100:.0f}%")
print("=" * 70)

# 先展示 Node 6 四维验证结果摘要
print("\n  [Node 6 四维验证板块]")
for cs in confirmed_sectors[:8]:
    icon = {"confirmed": "✓", "avoid": "✗", "watch": "○", "divergent": "△", "speculative": "~"}.get(
        cs.get("sector_type", ""), "?"
    )
    print(f"    {icon} {cs['sector_name']:8s} [{cs.get('sector_type', '?'):10s}] "
          f"action={cs.get('action', '?'):8s} conf={cs.get('confidence', 0):.2f}")

print("\n" + "-" * 70)
print("  [精选个股]")

action_label = {"buy_trend": "趋势", "buy_dip": "低吸"}

for i, s in enumerate(top3, 1):
    label = action_label.get(s["action"], s["action"])
    print(f"\n  [{i}] {s['name']} ({s['code']}) -- {label}")
    print(f"      板块: {s['sector_name']} (置信度 {s['sector_confidence']:.2f})")
    print(f"      主营: {s.get('main_business', '暂无数据')}")
    print(f"      问财排名: #{s['ths_rank']} | 综合评分: {s['composite_score']:.3f}")
    print(f"      建议仓位: {s['position_pct']:.1f}%")
    print(f"      逻辑: {s['reasoning'][:60]}")

total_used = sum(s["position_pct"] for s in top3)
print(f"\n  合计仓位: {total_used:.1f}%  |  现金: {100 - total_used:.1f}%")
print("=" * 70)

# 风险提示
risks = unified_data.get("key_risks", [])
if risks:
    print("\n风险提示：")
    for risk in risks[:3]:
        print(f"  [!] {risk}")

# 保存结果
output = {
    "date": GOT_DATE,
    "signal": signal,
    "total_position": total_position,
    "confirmed_sectors_count": len(confirmed_sectors),
    "actionable_sectors": len(actionable),
    "picks": [{
        "code": s["code"],
        "name": s["name"],
        "sector": s["sector_name"],
        "action": s["action"],
        "sector_confidence": s["sector_confidence"],
        "ths_rank": s["ths_rank"],
        "composite_score": round(s["composite_score"], 4),
        "position_pct": s["position_pct"],
        "main_business": s.get("main_business", ""),
        "reasoning": s.get("reasoning", ""),
    } for s in top3],
    "timestamp": datetime.now().isoformat(),
}

out_path = Path("data/got_screener") / f"{GOT_DATE}_fast.json"
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
logger.info(f"结果已保存: {out_path}")
