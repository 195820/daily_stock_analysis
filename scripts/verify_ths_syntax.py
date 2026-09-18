# -*- coding: utf-8 -*-
"""验证问财板块查询语法：概念板块/行业板块 + 养殖业成分股的行业字段"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

import akshare as ak
from src.config import get_config
from src.services.ths_screener import THSScreenerService

# ── 1. 官方列表成员检查（无需问财）──
industry_names = set(ak.stock_board_industry_name_ths()['name'].astype(str))
concept_names = set(ak.stock_board_concept_name_ths()['name'].astype(str))
for n in ['生物医药', '有色黄金', '航运', '养殖业', '汽车整车', '煤炭开采']:
    print(f"[行业列表] {n}: {'OK' if n in industry_names else 'NO'}")
for n in ['粮食概念', '汽车芯片', '水产品']:
    print(f"[概念列表] {n}: {'OK' if n in concept_names else 'NO'}")

# ── 2. 问财语法实测 ──
ths = THSScreenerService(get_config())

def run(label, q):
    r = ths.query(q, use_cache=False)
    n = len(r.stocks) if r else 0
    print(f"\n=== {label}: {n} 只 ===")
    if r and r.stocks:
        for s in r.stocks[:8]:
            print(f"  {s.code} {s.name} 行业='{s.industry}'")
    return r

run("概念语法-粮食概念", "所属同花顺概念板块是粮食概念，上市天数>365天，非ST，所属同花顺行业")
run("行业语法-养殖业", "所属同花顺板块是养殖业，上市天数>365天，非ST，所属同花顺行业")
