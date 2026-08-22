# -*- coding: utf-8 -*-
"""
=============================================
同花顺热榜数据采集模块
=============================================

数据源（直接 HTTP API，无需 Playwright）：

  热股: dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock
  板块: dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate
  话题: dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/topic
  期货: dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/future

反爬策略：
  - UA 轮换
  - Referer 伪装
  - 请求间隔 0.5s
  - 独立 try/except，单类失败不阻塞

接入节点：
  - Node 4（消息面）: 热门话题 + 期货
  - Node 5（情绪面）: 热股
  - Node 2（板块分析）: 板块热度
"""

import logging
import random
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# =============================================================================
# 常量
# =============================================================================

# 基础 URL
_BASE_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1"

# 请求间隔（秒）
REQUEST_INTERVAL = 0.5

# 超时
TIMEOUT = 10

# UA 池
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]

# =============================================================================
# 通用请求函数
# =============================================================================


def _make_request(url: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """
    发送 HTTP 请求到同花顺 API。

    Args:
        url: API 端点 URL
        params: 查询参数

    Returns:
        JSON 响应字典，失败返回 None
    """
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Referer": "https://eq.10jqka.com.cn/",
        "Accept": "application/json, text/plain, */*",
    }

    try:
        resp = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        data = resp.json()
        if data.get("status_code") != 0:
            logger.warning("[ths_hot_list] API 返回错误: %s", data.get("status_msg", "unknown"))
            return None

        return data

    except requests.RequestException as e:
        logger.warning("[ths_hot_list] 请求失败 %s: %s", url, e)
        return None
    except ValueError as e:
        logger.warning("[ths_hot_list] JSON 解析失败: %s", e)
        return None


# =============================================================================
# 热股排行
# =============================================================================


def fetch_hot_stocks(
    stock_type: str = "a",
    time_type: str = "hour",
    list_type: str = "normal",
    top_n: int = 50,
) -> List[Dict[str, Any]]:
    """
    获取同花顺热股排行。

    Args:
        stock_type: 股票类型 (a=A股, hk=港股, us=美股)
        time_type: 时间维度 (hour=1小时, day=24小时)
        list_type: 榜单类型 (normal=普通)
        top_n: 返回前 N 条

    Returns:
        热股列表，每项包含:
        - code: 股票代码
        - name: 股票名称
        - rate: 热度值
        - rise_and_fall: 涨跌幅
        - concept_tag: 概念标签列表
        - order: 排名
    """
    url = f"{_BASE_URL}/stock"
    params = {
        "stock_type": stock_type,
        "type": time_type,
        "list_type": list_type,
    }

    data = _make_request(url, params)
    if not data:
        return []

    stock_list = data.get("data", {}).get("stock_list", [])
    results = []

    for item in stock_list[:top_n]:
        results.append({
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "rate": float(item.get("rate", 0) or 0),
            "rise_and_fall": float(item.get("rise_and_fall", 0) or 0),
            "concept_tag": item.get("tag", {}).get("concept_tag", []),
            "order": item.get("order", 0),
        })

    logger.info("[ths_hot_list] 热股排行: %d 条", len(results))
    time.sleep(REQUEST_INTERVAL)
    return results


# =============================================================================
# 板块热度
# =============================================================================


def fetch_hot_sectors(
    sector_type: str = "concept",
    top_n: int = 30,
) -> List[Dict[str, Any]]:
    """
    获取同花顺板块热度排行。

    Args:
        sector_type: 板块类型 (concept=概念, industry=行业)
        top_n: 返回前 N 条

    Returns:
        板块列表，每项包含:
        - name: 板块名称
        - rate: 热度值
        - rise_and_fall: 涨跌幅
        - order: 排名
    """
    url = f"{_BASE_URL}/plate"
    params = {"type": sector_type}

    data = _make_request(url, params)
    if not data:
        return []

    plate_list = data.get("data", {}).get("plate_list", [])
    results = []

    for item in plate_list[:top_n]:
        results.append({
            "name": item.get("name", ""),
            "rate": float(item.get("rate", 0) or 0),
            "rise_and_fall": float(item.get("rise_and_fall", 0) or 0),
            "order": item.get("order", 0),
        })

    logger.info("[ths_hot_list] 板块热度: %d 条", len(results))
    time.sleep(REQUEST_INTERVAL)
    return results


# =============================================================================
# 热门话题
# =============================================================================


def fetch_hot_topics(
    page: int = 1,
    page_size: int = 30,
) -> List[Dict[str, Any]]:
    """
    获取同花顺热门话题。

    这是 Node 4（消息面）的关键数据源，直接反映投资者关注焦点。
    例如"美国拟禁止中国光模块进口"类话题会出现在这里。

    Args:
        page: 页码
        page_size: 每页条数

    Returns:
        话题列表，每项包含:
        - title: 话题标题
        - description: 话题描述
        - related_stocks: 相关股票列表
        - heat: 热度值
        - order: 排名
    """
    url = f"{_BASE_URL}/topic"
    params = {
        "page": page,
        "page_size": page_size,
    }

    data = _make_request(url, params)
    if not data:
        return []

    topic_list = data.get("data", {}).get("topic_list", [])
    results = []

    for item in topic_list:
        # 提取相关股票 - 优先 attach_hot_stock，其次 attach_info.att_stock
        related_stocks = []
        attach_stocks = item.get("attach_hot_stock", [])
        if not attach_stocks:
            attach_info = item.get("attach_info", {})
            if isinstance(attach_info, dict):
                attach_stocks = attach_info.get("att_stock", [])

        for stock in attach_stocks:
            related_stocks.append({
                "code": stock.get("code", ""),
                "name": stock.get("name", ""),
            })

        results.append({
            "title": item.get("title", ""),
            "description": item.get("description", ""),
            "related_stocks": related_stocks,
            "heat": float(item.get("hot_value", 0) or 0),  # 注意：字段名是 hot_value
            "order": item.get("order", 0),
            "jump_url": item.get("jump_url", ""),
        })

    logger.info("[ths_hot_list] 热门话题: %d 条", len(results))
    time.sleep(REQUEST_INTERVAL)
    return results


# =============================================================================
# 期货热度
# =============================================================================


def fetch_hot_futures(top_n: int = 20) -> List[Dict[str, Any]]:
    """
    获取同花顺期货热度排行。

    Args:
        top_n: 返回前 N 条

    Returns:
        期货列表，每项包含:
        - name: 品种名称
        - rate: 热度值
        - rise_and_fall: 涨跌幅
        - order: 排名
    """
    url = f"{_BASE_URL}/future"
    data = _make_request(url)
    if not data:
        return []

    futures_list = data.get("data", {}).get("futures_list", [])  # 注意：字段名是 futures_list
    results = []

    for item in futures_list[:top_n]:
        results.append({
            "name": item.get("name", ""),
            "code": item.get("code", ""),
            "rate": float(item.get("rate", 0) or 0),  # rate 可能是字符串
            "rise_and_fall": float(item.get("rise_and_fall", 0) or 0),
            "related_stocks": [
                {"code": s.get("code", ""), "name": s.get("name", "")}
                for s in item.get("stock_list", [])
            ],
            "order": item.get("order", 0),
        })

    logger.info("[ths_hot_list] 期货热度: %d 条", len(results))
    time.sleep(REQUEST_INTERVAL)
    return results


# =============================================================================
# 统一入口
# =============================================================================


def fetch_all_ths_hot_list() -> Dict[str, List[Dict[str, Any]]]:
    """
    获取同花顺热榜全量数据。

    Returns:
        字典，包含:
        - hot_stocks: 热股排行
        - hot_sectors: 板块热度
        - hot_topics: 热门话题
        - hot_futures: 期货热度
    """
    logger.info("[ths_hot_list] 开始采集同花顺热榜...")

    results = {
        "hot_stocks": fetch_hot_stocks(top_n=50),
        "hot_sectors": fetch_hot_sectors(top_n=30),
        "hot_topics": fetch_hot_topics(page_size=30),
        "hot_futures": fetch_hot_futures(top_n=20),
    }

    total = sum(len(v) for v in results.values())
    logger.info("[ths_hot_list] 采集完成: %d 条数据", total)

    return results


# =============================================================================
# 测试入口
# =============================================================================


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    data = fetch_all_ths_hot_list()

    print("\n" + "=" * 70)
    print("  同花顺热榜数据采集结果")
    print("=" * 70)

    # 热股 Top 10
    print("\n【热股排行 Top 10】")
    for i, stock in enumerate(data["hot_stocks"][:10], 1):
        print(f"  {i}. {stock['code']} {stock['name']} 热度={stock['rate']:.0f} 涨跌={stock['rise_and_fall']:+.2f}%")

    # 板块 Top 10
    print("\n【板块热度 Top 10】")
    for i, sector in enumerate(data["hot_sectors"][:10], 1):
        print(f"  {i}. {sector['name']} 热度={sector['rate']:.0f} 涨跌={sector['rise_and_fall']:+.2f}%")

    # 热门话题 Top 10
    print("\n【热门话题 Top 10】")
    for i, topic in enumerate(data["hot_topics"][:10], 1):
        stocks_str = ", ".join(s["name"] for s in topic["related_stocks"][:3])
        print(f"  {i}. {topic['title'][:40]}... 热度={topic['heat']:.0f} 关联: {stocks_str}")

    # 期货 Top 5
    print("\n【期货热度 Top 5】")
    for i, future in enumerate(data["hot_futures"][:5], 1):
        print(f"  {i}. {future['name']} 热度={future['rate']:.0f} 涨跌={future['rise_and_fall']:+.2f}%")

    # 保存 JSON
    output_path = "ths_hot_list_sample.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n[保存] 完整数据已保存到: {output_path}")
