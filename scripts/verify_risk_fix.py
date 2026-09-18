# -*- coding: utf-8 -*-
"""验证 _score_risk 波动率单位修复：修复前全员撞顶 85，修复后应有区分度"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import get_config
from src.services.stock_screener import StockScreenerPipeline
from data_provider.base import DataFetcherManager

# 上轮追踪中的代表票（汽车芯片 4 只 + 水产品独苗）
codes = [
    ("002213", "大为股份", 3.78),   # 修复前波动率
    ("688601", "赛微电子", 4.22),
    ("688508", "跃进村", 7.53),
    ("301099", "雅创电子", 4.19),
    ("300313", "天山生物", 8.09),
]

pipeline = StockScreenerPipeline(get_config())
fm = DataFetcherManager()

candidates = [{"code": c, "name": n} for c, n, _ in codes]
scores = pipeline._score_risk(candidates, fm)

print("code    name      risk   (修复前全为 85，天山 55)")
for (code, name, vol_old) in codes:
    print(f"{code}  {name:<6}  {scores.get(code, '?')}   (旧口径 vol={vol_old})")
