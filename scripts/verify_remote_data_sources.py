# -*- coding: utf-8 -*-
"""
验证远端新增数据源在本地环境的可用性。

测试目标：
1. TencentFetcher — 免费，无需 API Key
2. TickFlowFetcher — 需要 tickflow 包 + TICKFLOW_API_KEY
3. FutuFetcher — 需要 futu-api 包 + OpenD 本地服务
4. 指数多源 fallback — Tencent/Akshare/TickFlow/Yfinance 各取上证指数日线
5. MarketHotspotService 依赖的板块排名数据
"""

import sys
import os
import time
import traceback
from datetime import datetime, timedelta

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# conda 环境检查
print(f"Python: {sys.executable}")
print(f"CWD: {os.getcwd()}\n")


def test_tencent_fetcher():
    """Tencent 免费端点：日 K + 实时行情"""
    print("=" * 60)
    print("[1] TencentFetcher — 日 K 线 + 指数行情")
    print("=" * 60)

    # 1a. 日 K 线端点
    import requests
    url_kline = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": "sh000001,day,2026-08-01,2026-08-29,10,qfq",
    }
    try:
        t0 = time.time()
        resp = requests.get(url_kline, params=params, timeout=10)
        elapsed = time.time() - t0
        data = resp.json()
        # 解析返回结构
        kline_data = data.get("data", {}).get("sh000001", {})
        day_data = kline_data.get("day", kline_data.get("qfqday", []))
        print(f"  [PASS] 日 K 端点 OK ({elapsed:.1f}s)")
        print(f"     返回 {len(day_data)} 条 K 线")
        if day_data:
            print(f"     最新: {day_data[-1]}")
    except Exception as e:
        print(f"  [FAIL] 日 K 端点失败: {e}")

    # 1b. 实时行情端点（指数）
    url_quote = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
    try:
        t0 = time.time()
        resp = requests.get(url_quote, timeout=10)
        elapsed = time.time() - t0
        lines = [l for l in resp.text.strip().split("\n") if l.strip()]
        print(f"  [PASS] 行情端点 OK ({elapsed:.1f}s)")
        print(f"     返回 {len(lines)} 条指数行情")
        for line in lines[:3]:
            # 截取前 80 字符
            print(f"     {line[:100]}...")
    except Exception as e:
        print(f"  [FAIL] 行情端点失败: {e}")

    # 1c. 东财单股指数实时行情（eb1333a 新增）
    url_em_index = "https://push2.eastmoney.com/api/qt/stock/get"
    params_em = {"secid": "1.000001", "fields": "f43,f44,f45,f46,f47,f48,f50,f57,f58,f170"}
    try:
        t0 = time.time()
        resp = requests.get(url_em_index, params=params_em, timeout=10)
        elapsed = time.time() - t0
        data = resp.json()
        rc = data.get("rc", -1)
        print(f"  {'[PASS]' if rc == 0 else '[WARN]'} 东财指数行情 rc={rc} ({elapsed:.1f}s)")
        if rc == 0:
            d = data.get("data", {})
            print(f"     上证指数: {d.get('f58', '?')} 最新价={d.get('f43', '?')}")
    except Exception as e:
        print(f"  [FAIL] 东财指数行情失败: {e}")


def test_tickflow_fetcher():
    """TickFlow：需要 pip 包 + API Key"""
    print("\n" + "=" * 60)
    print("[2] TickFlowFetcher — 需要 tickflow 包 + TICKFLOW_API_KEY")
    print("=" * 60)

    # 2a. 检查包
    try:
        import tickflow
        print(f"  [PASS] tickflow 包已安装 (版本: {getattr(tickflow, '__version__', 'unknown')})")
    except ImportError:
        print(f"  [FAIL] tickflow 包未安装")
        print(f"     安装: pip install tickflow")
        return

    # 2b. 检查 API Key
    api_key = os.getenv("TICKFLOW_API_KEY", "").strip()
    if not api_key:
        print(f"  [FAIL] TICKFLOW_API_KEY 未配置")
        print(f"     在 .env 中添加 TICKFLOW_API_KEY=xxx")
        return

    print(f"  [PASS] TICKFLOW_API_KEY 已配置 ({api_key[:8]}...)")

    # 2c. 尝试获取数据
    try:
        from tickflow import TickFlow
        client = TickFlow(api_key=api_key, timeout=10)
        print(f"  [PASS] TickFlow 客户端初始化成功")
        # 尝试获取上证指数日线
        # 具体 API 需要看 tickflow 文档
        print(f"  [INFO]  需要确认 TickFlow API 调用方式（远端代码中使用 client 对象）")
    except Exception as e:
        print(f"  [FAIL] TickFlow 初始化失败: {e}")


def test_futu_fetcher():
    """Futu OpenD：需要 futu-api 包 + 本地 OpenD 服务"""
    print("\n" + "=" * 60)
    print("[3] FutuFetcher — 需要 futu-api 包 + FutuOpenD 本地服务")
    print("=" * 60)

    # 3a. 检查包
    try:
        import futu
        print(f"  [PASS] futu-api 包已安装 (版本: {getattr(futu, '__version__', 'unknown')})")
    except ImportError:
        print(f"  [FAIL] futu-api 包未安装")
        print(f"     安装: pip install futu-api")
        return

    # 3b. 检查 OpenD 连接
    futu_host = os.getenv("FUTU_HOST", "127.0.0.1")
    futu_port = int(os.getenv("FUTU_PORT", "11111"))
    print(f"  [INFO]  FutuOpenD 地址: {futu_host}:{futu_port}")

    try:
        from futu import OpenQuoteContext
        ctx = OpenQuoteContext(host=futu_host, port=futu_port)
        ret, data = ctx.get_market_state(market="HK")
        ctx.close()
        if ret == 0:
            print(f"  [PASS] FutuOpenD 连接成功")
        else:
            print(f"  [FAIL] FutuOpenD 返回错误: {data}")
    except Exception as e:
        print(f"  [FAIL] FutuOpenD 连接失败: {e}")
        print(f"     确保 FutuOpenD 客户端正在运行")


def test_index_fallback_chain():
    """指数日线 fallback：Akshare（现有）+ Tencent（远端新增）"""
    print("\n" + "=" * 60)
    print("[4] 指数日线 fallback 链 — 上证 000001")
    print("=" * 60)

    import requests

    # 4a. Akshare（现有 P1）
    try:
        import akshare as ak
        t0 = time.time()
        df = ak.stock_zh_index_daily_em(symbol="sh000001")
        elapsed = time.time() - t0
        print(f"  [PASS] Akshare 指数日线 OK ({elapsed:.1f}s)")
        print(f"     最后 3 条:")
        print(df.tail(3).to_string(index=False).replace("\n", "\n     "))
    except Exception as e:
        print(f"  [FAIL] Akshare 指数日线失败: {e}")

    # 4b. Tencent（远端新增）
    try:
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {"param": "sh000001,day,2026-08-20,2026-08-29,10,qfq"}
        t0 = time.time()
        resp = requests.get(url, params=params, timeout=10)
        elapsed = time.time() - t0
        data = resp.json()
        day_data = data.get("data", {}).get("sh000001", {}).get("day", [])
        if not day_data:
            day_data = data.get("data", {}).get("sh000001", {}).get("qfqday", [])
        print(f"  [PASS] Tencent 指数日线 OK ({elapsed:.1f}s), {len(day_data)} 条")
        if day_data:
            print(f"     最新: {day_data[-1]}")
    except Exception as e:
        print(f"  [FAIL] Tencent 指数日线失败: {e}")

    # 4c. Yfinance（远端 fallback 末端）
    try:
        import yfinance as yf
        t0 = time.time()
        ticker = yf.Ticker("000001.SS")
        df = ticker.history(period="5d")
        elapsed = time.time() - t0
        print(f"  [PASS] Yfinance 指数日线 OK ({elapsed:.1f}s), {len(df)} 条")
        if not df.empty:
            print(f"     最新: Close={df['Close'].iloc[-1]:.2f}")
    except Exception as e:
        print(f"  [FAIL] Yfinance 指数日线失败: {e}")


def test_sector_ranking():
    """板块排名数据 — MarketHotspotService 依赖"""
    print("\n" + "=" * 60)
    print("[5] 板块排名数据 — MarketHotspotService 依赖")
    print("=" * 60)

    # 5a. 东财概念板块排名（akshare）
    try:
        import akshare as ak
        t0 = time.time()
        df = ak.stock_board_concept_name_em()
        elapsed = time.time() - t0
        print(f"  [PASS] 东财概念板块 OK ({elapsed:.1f}s), {len(df)} 个板块")
        print(f"     列: {list(df.columns)}")
        # 按涨跌幅排序看 Top 5
        if "涨跌幅" in df.columns:
            top5 = df.nlargest(5, "涨跌幅")[["板块名称", "涨跌幅"]].to_string(index=False)
            print(f"     Top 5:\n     {top5.replace(chr(10), chr(10) + '     ')}")
    except Exception as e:
        print(f"  [FAIL] 东财概念板块失败: {e}")

    # 5b. 东财行业板块排名
    try:
        import akshare as ak
        t0 = time.time()
        df = ak.stock_board_industry_name_em()
        elapsed = time.time() - t0
        print(f"  [PASS] 东财行业板块 OK ({elapsed:.1f}s), {len(df)} 个板块")
        if "涨跌幅" in df.columns:
            top5 = df.nlargest(5, "涨跌幅")[["板块名称", "涨跌幅"]].to_string(index=False)
            print(f"     Top 5:\n     {top5.replace(chr(10), chr(10) + '     ')}")
    except Exception as e:
        print(f"  [FAIL] 东财行业板块失败: {e}")


def test_existing_data_sources():
    """现有数据源健康检查（基线对照）"""
    print("\n" + "=" * 60)
    print("[6] 现有数据源基线 — mootdx TCP + 东财 HTTP")
    print("=" * 60)

    # 6a. mootdx TCP
    try:
        from mootdx.quotes import Quotes
        t0 = time.time()
        client = Quotes.factory(market='std')
        df = client.bars(symbol='000001', frequency=9, offset=10)
        elapsed = time.time() - t0
        print(f"  [PASS] mootdx TCP OK ({elapsed:.1f}s), {len(df)} 条 K 线")
        if not df.empty:
            print(f"     最新: date={df.index[-1]}, close={df['close'].iloc[-1]}")
    except Exception as e:
        print(f"  [FAIL] mootdx TCP 失败: {e}")

    # 6b. 东财 push2（检查是否仍然被封）
    try:
        import requests
        t0 = time.time()
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 5, "po": 1, "np": 1,
            "fs": "m:0+t:6", "fields": "f12,f14,f3",
        }
        resp = requests.get(url, params=params, timeout=10)
        elapsed = time.time() - t0
        data = resp.json()
        total = data.get("data", {}).get("total", 0)
        print(f"  {'[PASS]' if total > 0 else '[WARN]'} 东财 push2 total={total} ({elapsed:.1f}s)")
    except Exception as e:
        print(f"  [FAIL] 东财 push2 失败: {e}")


if __name__ == "__main__":
    print("远端数据源可用性验证")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    test_tencent_fetcher()
    test_tickflow_fetcher()
    test_futu_fetcher()
    test_index_fallback_chain()
    test_sector_ranking()
    test_existing_data_sources()

    print("\n" + "=" * 60)
    print("验证完成")
    print("=" * 60)
