# -*- coding: utf-8 -*-
"""
手动模拟 GOT 选股（不需要完整依赖）
基于 2026-07-29 的 GOT 输出，演示选股逻辑
"""
import json
from pathlib import Path

def load_got_data():
    """加载 GOT 数据"""
    date_str = "20260729"
    archive_dir = Path(f"data/got_daily/{date_str}")

    context = {}
    node_files = {
        "sector_context": "sector.json",
        "capital_context": "capital.json",
    }

    for key, filename in node_files.items():
        filepath = archive_dir / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                context[key] = json.load(f)

    return context

def extract_universe(context):
    """提取选股池"""
    sector_ctx = context.get("sector_context", {})

    # 主线板块
    active_mainlines = sector_ctx.get("active_mainline", [])
    # 埋伏板块
    ambush_sectors = sector_ctx.get("ambush", [])

    print("=" * 80)
    print("选股池提取")
    print("=" * 80)
    print(f"\n主线板块: {len(active_mainlines)} 个")
    for s in active_mainlines:
        print(f"  - {s['name']} (强度 {s['strength_score']:.0f}, 状态 {s['state']})")
        if s.get('launch_signal'):
            sig = s['launch_signal']
            print(f"    启动信号: {sig.get('level', 'N/A')} (score {sig.get('score', 0):.2f})")

    print(f"\n埋伏板块: {len(ambush_sectors)} 个")
    for s in ambush_sectors:
        print(f"  - {s['name']} (强度 {s['strength_score']:.0f}, 状态 {s['state']})")
        if s.get('launch_signal'):
            sig = s['launch_signal']
            print(f"    启动信号: {sig.get('level', 'N/A')} (score {sig.get('score', 0):.2f})")

    return active_mainlines + ambush_sectors

def score_capital(context, sectors):
    """资金面评分（简化版）"""
    capital_ctx = context.get("capital_context", {})
    evidence = capital_ctx.get("evidence", {})

    # 龙虎榜买入列表
    dragon_tiger_bought = evidence.get("dragon_tiger", {}).get("mainline_stocks_bought", [])

    print("\n" + "=" * 80)
    print("资金面评分")
    print("=" * 80)
    print(f"\n龙虎榜机构买入: {len(dragon_tiger_bought)} 只")
    for item in dragon_tiger_bought:
        print(f"  - {item}")

    # 由于没有成分股数据，我们假设从这些板块中选股
    # 实际运行时会从问财/东财获取成分股
    print("\n假设成分股（示例）:")
    print("  多元金融: 600030.中信证券, 601688.华泰证券, 000776.广发证券...")
    print("  华为汽车: 002594.比亚迪, 600741.华域汽车, 601689.拓普集团...")
    print("  汽车整车: 600104.上汽集团, 000800.一汽解放, 601238.广汽集团...")
    print("  钢铁: 600019.宝钢股份, 000898.鞍钢股份, 600010.包钢股份...")

    return dragon_tiger_bought

def simulate_selection():
    """模拟选股过程"""
    context = load_got_data()

    # 1. 提取选股池
    sectors = extract_universe(context)

    # 2. 资金面评分
    dragon_tiger = score_capital(context, sectors)

    # 3. 模拟最终选择（基于龙虎榜买入 + 板块强度）
    print("\n" + "=" * 80)
    print("模拟选股结果 (Top 3)")
    print("=" * 80)

    # 假设从龙虎榜买入中选择属于主线/埋伏板块的股票
    # 实际运行时会完整评分
    simulated_results = [
        {
            "code": "300502",
            "name": "新易盛",
            "sector": "多元金融",  # 假设属于某主线板块
            "sector_type": "active_mainline",
            "sector_score": 80,
            "capital_score": 90,  # 龙虎榜买入
            "tech_wave_score": 75,
            "risk_score_safe": 85,
            "score": 85.5,
            "buy_price": 72.50,
            "target_price": 79.75,
            "stop_loss_price": 67.43,
        },
        {
            "code": "600664",
            "name": "哈药股份",
            "sector": "医疗保健",  # 假设属于埋伏板块
            "sector_type": "ambush",
            "sector_score": 67,
            "capital_score": 85,  # 龙虎榜买入
            "tech_wave_score": 70,
            "risk_score_safe": 80,
            "score": 78.2,
            "buy_price": 15.80,
            "target_price": 17.38,
            "stop_loss_price": 14.69,
        },
        {
            "code": "300308",
            "name": "中际旭创",
            "sector": "华为汽车",  # 假设属于主线板块
            "sector_type": "active_mainline",
            "sector_score": 68,
            "capital_score": 80,  # 龙虎榜买入
            "tech_wave_score": 72,
            "risk_score_safe": 78,
            "score": 75.8,
            "buy_price": 95.20,
            "target_price": 104.72,
            "stop_loss_price": 88.54,
        },
    ]

    for i, stock in enumerate(simulated_results, 1):
        print(f"\n{'='*80}")
        print(f"#{i} {stock['code']} {stock['name']}")
        print(f"{'='*80}")
        print(f"综合评分: {stock['score']:.1f}/100")
        print(f"所属板块: {stock['sector']} ({stock['sector_type']})")
        print(f"板块强度: {stock['sector_score']:.0f}")
        print(f"\n各维度评分:")
        print(f"  - 资金面: {stock['capital_score']:.1f}/100 (权重 30%)")
        print(f"  - 消息面: 0.0/100 (权重 20%, 未实现)")
        print(f"  - 板块强度: {stock['sector_score']:.1f}/100 (权重 20%)")
        print(f"  - 技术面(波段): {stock['tech_wave_score']:.1f}/100 (权重 15%)")
        print(f"  - 风险(安全): {stock['risk_score_safe']:.1f}/100 (权重 15%)")
        print(f"\n交易建议:")
        print(f"  - 买入价: {stock['buy_price']:.2f} 元")
        print(f"  - 目标价: {stock['target_price']:.2f} 元 (+10%)")
        print(f"  - 止损价: {stock['stop_loss_price']:.2f} 元 (-7%)")
        print(f"  - 持仓周期: 10-15 个交易日")

    print("\n" + "=" * 80)
    print("说明")
    print("=" * 80)
    print("这是基于 2026-07-29 GOT 输出的模拟结果。")
    print("实际运行时需要:")
    print("  1. 激活 conda 环境: conda activate dsa")
    print("  2. 运行: python scripts/run_got_screener_v2.py --date 2026-07-29")
    print("\n实际选股会:")
    print("  - 从主线/埋伏板块获取真实成分股（~95 只）")
    print("  - 5 轮漏斗筛选（信号→资金→技术→风险→分散）")
    print("  - 计算真实的技术指标和评分")
    print("  - 生成详细的推荐理由")

if __name__ == "__main__":
    simulate_selection()
