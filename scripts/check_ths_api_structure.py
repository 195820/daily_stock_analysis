# -*- coding: utf-8 -*-
"""检查同花顺热榜 API 原始响应结构"""

import json
import requests

headers = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://eq.10jqka.com.cn/",
}

# 检查热门话题 API
print("=" * 70)
print("热门话题 API 原始响应")
print("=" * 70)
resp = requests.get(
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/topic",
    params={"page": 1, "page_size": 5},
    headers=headers,
    timeout=10,
)
data = resp.json()
print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])

# 检查期货 API
print("\n" + "=" * 70)
print("期货 API 原始响应")
print("=" * 70)
resp = requests.get(
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/future",
    headers=headers,
    timeout=10,
)
data = resp.json()
print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
