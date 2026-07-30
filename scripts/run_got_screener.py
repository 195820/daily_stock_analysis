# -*- coding: utf-8 -*-
"""
GOT + 问财联合选股
根据今日 GOT Phase 1 输出，构造防守型问财 query 并执行选股
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config
from src.services.ths_screener import THSScreenerService
from src.got.news_feeds import fetch_all_news
import asyncio
import json
import time


def run_got_screener():
    config = Config()
    ths = THSScreenerService(config)

    # ── 根据 GOT 输出构造 query ──
    # 今日 GOT 结论：cautious, 仓位30%, 防守为主
    # 主线：汽车整车(late_stage,exit_only) → 不追
    # 防守轮动：船舶(+3.91%)、多元金融(+1.77%)
    # 埋伏：航运、医疗保健、化纤、绿色电力
    # 市场宽度强：涨停92家，涨跌比4322:1148
    # 消息面：央行2.1万亿逆回购释放流动性

    queries = {
        # 策略1: 防守型 — 低位 + 大单流入 + 非高位股
        "防守低位精选": (
            "非ST, 上市天数>365天, "
            "1个交易日前的dde大单净量>=0.5%, "
            "近5日涨跌幅<10%, "
            "收盘价<60日均线*1.1, "
            "今日涨跌幅>0%, "
            "流通市值>50亿"
        ),

        # 策略2: 船舶/航运/军工方向（防守轮动+埋伏）
        "船舶航运方向": (
            "非ST, 所属同花顺行业包含船舶制造或航运或港口或军工, "
            "今日涨跌幅>0%, "
            "1个交易日前的dde大单净量>0%, "
            "量比>1, "
            "上市天数>365天"
        ),

        # 策略3: 医疗保健埋伏（防守+埋伏方向）
        "医疗保健方向": (
            "非ST, 所属同花顺行业包含医药或医疗器械或生物科技或中药或化学制药, "
            "今日涨跌幅>0%, "
            "1个交易日前的dde大单净量>0%, "
            "近20日涨跌幅<15%, "
            "上市天数>365天"
        ),

        # 策略4: 央行放水受益 — 金融+低估值（非银行，银行已衰竭）
        "流动性受益方向": (
            "非ST, 所属同花顺行业包含保险或多元金融或证券或信托, "
            "今日涨跌幅>1%, "
            "1个交易日前的dde大单净量>0%, "
            "市盈率>0, 市盈率<30, "
            "上市天数>365天"
        ),

        # 策略5: 超跌反弹 — 创业板/科技成长（今日涨幅较大方向）
        "科技超跌反弹": (
            "非ST, "
            "所属同花顺行业包含半导体或消费电子或计算机设备或通信设备, "
            "近20日涨跌幅<-10%, "
            "今日涨跌幅>1%, "
            "1个交易日前的dde大单净量>0%, "
            "流通市值>50亿, "
            "上市天数>365天"
        ),
    }

    all_results = {}
    for name, query in queries.items():
        print(f"\n{'='*60}")
        print(f"策略: {name}")
        print(f"Query: {query[:80]}...")
        print(f"{'='*60}")

        t0 = time.time()
        result = ths.query(query, use_cache=False)
        elapsed = time.time() - t0

        if result.error:
            print(f"  错误: {result.error}")
            all_results[name] = {"error": result.error, "stocks": []}
        else:
            stocks = result.stocks[:15]  # 取前15
            print(f"  共 {result.total_count} 只, 耗时 {elapsed:.1f}s, Top {len(stocks)}:")
            for s in stocks:
                print(f"    {s.rank:3d}. {s.code} {s.name}")
            all_results[name] = {
                "total": result.total_count,
                "elapsed": round(elapsed, 1),
                "stocks": [{"code": s.code, "name": s.name, "rank": s.rank} for s in stocks],
            }

    # ── 汇总跨策略出现的高频股票 ──
    code_count = {}
    code_names = {}
    code_strategies = {}
    for name, data in all_results.items():
        for s in data.get("stocks", []):
            code = s["code"]
            code_count[code] = code_count.get(code, 0) + 1
            code_names[code] = s["name"]
            if code not in code_strategies:
                code_strategies[code] = []
            code_strategies[code].append(name)

    # 按出现频次排序
    ranked = sorted(code_count.items(), key=lambda x: -x[1])

    print(f"\n{'='*60}")
    print("跨策略高频股票（出现≥2次优先关注）")
    print(f"{'='*60}")
    for code, count in ranked[:30]:
        strategies = ", ".join(code_strategies[code])
        marker = " ★" if count >= 2 else ""
        print(f"  {code} {code_names[code]: <8s} 出现{count}次{marker}  [{strategies}]")

    # 保存结果
    output = {
        "date": "2026-07-29",
        "got_signal": "cautious",
        "got_position": 0.3,
        "queries": {name: data for name, data in all_results.items()},
        "cross_strategy_ranking": [
            {"code": code, "name": code_names[code], "count": count, "strategies": code_strategies[code]}
            for code, count in ranked[:30]
        ],
    }

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "got_screener_20260729.json"
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {output_path}")


if __name__ == "__main__":
    run_got_screener()
