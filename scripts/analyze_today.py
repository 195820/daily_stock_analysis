# -*- coding: utf-8 -*-
"""综合分析脚本: 问财选股 + 板块轮动 + 舆情 → 最佳入场标的"""

import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import logging
logging.basicConfig(level=logging.WARNING, format='%(asctime)s [%(name)s] %(message)s')

from src.config import get_config
config = get_config()

# ============================================================
# Step 1: 同花顺问财选股
# ============================================================
print('=' * 70)
print('  综合分析: 问财选股 + 板块轮动 + 舆情 -> 最佳入场标的')
print('=' * 70)

from src.services.ths_screener import THSScreenerService
ths_service = THSScreenerService(config)
ths_result = ths_service.get_dde_screener(use_cache=False)
ths_codes = {s.code: s for s in ths_result.stocks}
print(f'\n[Step 1] 问财选股: {ths_result.total_count} 只候选')

# ============================================================
# Step 2: 板块轮动分析
# ============================================================
print(f'\n[Step 2] 板块轮动分析中...')
from src.services.sector_rotation import SectorRotationService
sector_service = SectorRotationService(config)
rotation_result = sector_service.get_rotation_analysis(top_n=15, lookback_days=5)

print(f'  分析板块总数: {rotation_result.total_sectors}')
print(f'  市场主线: {rotation_result.market_theme}')

# 强势板块
print(f'\n  === 强势板块 (accelerating/peaking) ===')
for s in rotation_result.strong_sectors:
    leaders_str = ', '.join(
        f"{ls.get('name','')}({ls.get('code','')})"
        for ls in s.leader_stocks[:3]
    )
    print(f'  {s.name} | 强度:{s.strength_score:.1f} | '
          f'今日:{s.change_pct_today:+.2f}% | 3日:{s.change_pct_3d:+.2f}% | '
          f'5日:{s.change_pct_5d:+.2f}% | 阶段:{s.rotation_phase}')
    if leaders_str:
        print(f'    龙头: {leaders_str}')

# 崛起板块
print(f'\n  === 崛起板块 (emerging) ===')
for s in rotation_result.emerging_sectors:
    leaders_str = ', '.join(
        f"{ls.get('name','')}({ls.get('code','')})"
        for ls in s.leader_stocks[:3]
    )
    print(f'  {s.name} | 强度:{s.strength_score:.1f} | '
          f'今日:{s.change_pct_today:+.2f}% | 3日:{s.change_pct_3d:+.2f}% | '
          f'阶段:{s.rotation_phase}')
    if leaders_str:
        print(f'    龙头: {leaders_str}')

# 轮动信号
print(f'\n  === 轮动信号 ===')
for sig in rotation_result.rotation_signals[:8]:
    sig_type = sig['type']
    desc = sig['description']
    strength = sig['strength']
    print(f'  [{sig_type}] {desc} (强度:{strength:.1f})')

# ============================================================
# Step 3: 交叉匹配 - 问财候选 in 强势板块成分股
# ============================================================
print(f'\n[Step 3] 交叉匹配: 问财候选 x 板块成分股')

# 获取强势板块的成分股(扩大范围)
sector_stock_map = {}  # stock_code -> [(sector_name, strength_score, phase)]
all_top_sectors = rotation_result.strong_sectors + rotation_result.emerging_sectors
for sector in all_top_sectors:
    try:
        constituents = sector_service.get_sector_stocks(sector.code, top_n=30)
        for c in constituents:
            code = c.get('code', '')
            if code not in sector_stock_map:
                sector_stock_map[code] = []
            sector_stock_map[code].append(
                (sector.name, sector.strength_score, sector.rotation_phase)
            )
        time.sleep(0.1)
    except Exception as e:
        print(f'  获取 {sector.name} 成分股失败: {e}')

print(f'  强势/崛起板块成分股总数: {len(sector_stock_map)}')

# 交叉匹配
matched = []
for code, stock in ths_codes.items():
    if code in sector_stock_map:
        sectors = sector_stock_map[code]
        best_sector = max(sectors, key=lambda x: x[1])
        matched.append({
            'code': code,
            'name': stock.name,
            'ths_rank': stock.rank,
            'sector': best_sector[0],
            'sector_strength': best_sector[1],
            'sector_phase': best_sector[2],
            'sector_count': len(sectors),
            'all_sectors': ', '.join(s[0] for s in sectors),
        })

matched.sort(key=lambda x: (-x['sector_strength'], x['ths_rank']))

print(f'  问财候选中属于强势/崛起板块的: {len(matched)} 只')
print(f'\n  {"代码":<8} {"名称":<10} {"问财排名":<8} {"所属板块":<14} '
      f'{"板块强度":<8} {"阶段":<14} {"覆盖数":<6}')
print('  ' + '-' * 80)
for m in matched[:30]:
    print(f'  {m["code"]:<8} {m["name"]:<10} {m["ths_rank"]:<8} '
          f'{m["sector"]:<14} {m["sector_strength"]:<8.1f} '
          f'{m["sector_phase"]:<14} {m["sector_count"]:<6}')

# ============================================================
# Step 4: NGA 舆情
# ============================================================
print(f'\n[Step 4] NGA 舆情采集...')
from src.services.forum_sentiment import ForumSentimentService
forum_service = ForumSentimentService(config)
sentiment_map = {}

if forum_service.is_available:
    try:
        forum_result = forum_service.fetch_hot_stocks(top_n=80)
        print(f'  NGA 采集帖子: {forum_result.thread_count} 条')
        print(f'  市场情绪: {forum_result.market_sentiment:.2f}')
        print(f'  热门股票: {len(forum_result.hot_stocks)} 只')

        overlap_count = 0
        for hs in forum_result.hot_stocks:
            sentiment_map[hs.code] = hs
            if hs.code in ths_codes:
                overlap_count += 1
                print(f'    >> {hs.code} {hs.name}: '
                      f'热度={hs.heat_score:.1f}, 情绪={hs.sentiment_score:.2f}, '
                      f'提及={hs.mention_count}, 趋势={hs.trend}')
        print(f'  问财候选与NGA热门重叠: {overlap_count} 只')

        # 热门话题
        if forum_result.hot_topics:
            print(f'\n  === 热门话题 ===')
            for topic in forum_result.hot_topics[:5]:
                print(f'  {topic["topic"]} | 热度:{topic["heat"]} | '
                      f'情绪:{topic["avg_sentiment"]:+.2f}')
    except Exception as e:
        print(f'  NGA 采集失败: {e}')
else:
    print('  NGA 未配置 Cookie, 跳过舆情分析')

# ============================================================
# Step 5: 综合评分
# ============================================================
print(f'\n[Step 5] 综合评分排名')

final_scores = []
for m in matched:
    score = 0.0

    # 板块强度 (0-40分)
    score += min(40, m['sector_strength'] * 0.4)

    # 阶段加分 (0-20分)
    phase_bonus = {
        'emerging': 20,
        'accelerating': 15,
        'peaking': 5,
        'declining': 0,
    }
    score += phase_bonus.get(m['sector_phase'], 5)

    # 问财排名 (0-20分)
    rank = m['ths_rank']
    if rank <= 10:
        score += 20
    elif rank <= 30:
        score += 15
    elif rank <= 50:
        score += 10
    else:
        score += 5

    # 多板块覆盖 (0-10分)
    score += min(10, m['sector_count'] * 3)

    # 舆情加分 (0-10分)
    sent = sentiment_map.get(m['code'])
    if sent:
        if sent.sentiment_score > 0.2:
            score += 10
        elif sent.sentiment_score > 0:
            score += 5
        score += min(5, sent.heat_score / 10)

    final_scores.append({
        **m,
        'final_score': round(score, 1),
        'sentiment': sent,
    })

final_scores.sort(key=lambda x: x['final_score'], reverse=True)

print(f'\n  {"排名":<4} {"代码":<8} {"名称":<10} {"综合分":<8} '
      f'{"板块强度":<8} {"阶段":<14} {"问财排名":<8} {"舆情":<10}')
print('  ' + '-' * 80)
for i, f in enumerate(final_scores[:20], 1):
    sent_str = 'N/A'
    if f['sentiment']:
        s = f['sentiment']
        sent_str = f'{s.sentiment_score:+.2f}/H{s.heat_score:.0f}'
    print(f'  {i:<4} {f["code"]:<8} {f["name"]:<10} {f["final_score"]:<8.1f} '
          f'{f["sector_strength"]:<8.1f} {f["sector_phase"]:<14} '
          f'{f["ths_rank"]:<8} {sent_str:<10}')

# ============================================================
# 最终推荐
# ============================================================
print()
print('=' * 70)
print('  今日推荐入场标的 (综合评分 Top 10)')
print('=' * 70)

phase_emoji = {
    'emerging': 'RISING',
    'accelerating': 'ACCEL',
    'peaking': 'PEAK',
    'declining': 'DECL',
}

for i, f in enumerate(final_scores[:10], 1):
    pe = phase_emoji.get(f['sector_phase'], '?')
    sent_info = ''
    if f['sentiment']:
        s = f['sentiment']
        sent_info = f' | 舆情:{s.sentiment_score:+.2f} 热度:{s.heat_score:.0f}'
    multi = f'(x{f["sector_count"]})' if f['sector_count'] > 1 else ''
    print(f'  {i:>2}. {f["code"]} {f["name"]:<10} '
          f'综合:{f["final_score"]:.1f}分 | '
          f'[{pe}] {f["sector"]}{multi}(强度{f["sector_strength"]:.0f})'
          f'{sent_info}')

print()
print('评分说明:')
print('  板块强度(40) + 轮动阶段(20) + 问财排名(20) + 多板块覆盖(10) + 舆情(10)')
print('  emerging=崛起(最佳) > accelerating=加速 > peaking=见顶(谨慎)')
print()
