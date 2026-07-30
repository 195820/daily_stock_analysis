"""网络连通性诊断 — 排查东财 API 连接问题"""

import os
import sys
import time

for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

sys.path.insert(0, ".")

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def test(name, fn):
    try:
        result = fn()
        print(f"  [{name}] OK: {result}")
        return True
    except Exception as e:
        print(f"  [{name}] FAIL: {type(e).__name__}: {e}")
        return False


print("=" * 60)
print("网络连通性诊断")
print("=" * 60)

# 1. 基础网络
print("\n1. 基础网络连通性")
test("baidu.com", lambda: requests.get("https://www.baidu.com", timeout=5).status_code == 200 and "baidu")
test("eastmoney.com", lambda: requests.get("https://www.eastmoney.com", timeout=5).status_code == 200 and "eastmoney")

# 2. 东财 push2 API（与 _EastMoneyClient 相同参数）
print("\n2. 东财 push2 API（直接请求）")
EM_URL = "https://79.push2.eastmoney.com/api/qt/clist/get"
EM_PARAMS = {
    "pn": "1", "pz": "5", "po": "1", "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2", "invt": "2", "fid": "f3",
    "fs": "m:90+t:3+f:!50",
    "fields": "f2,f3,f12,f14,f104,f105",
}

def em_direct():
    resp = requests.get(EM_URL, params=EM_PARAMS, timeout=10)
    data = resp.json()
    diff = data.get("data", {}).get("diff", [])
    return f"{len(diff)} boards, first={diff[0]['f14'] if diff else 'none'}"

test("push2 direct", em_direct)

# 3. 使用 _EastMoneyClient 的 Session 配置
print("\n3. 使用 _EastMoneyClient Session 配置")
def em_session():
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}
    retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
    })
    resp = s.get(EM_URL, params=EM_PARAMS, timeout=10)
    data = resp.json()
    diff = data.get("data", {}).get("diff", [])
    return f"{len(diff)} boards, first={diff[0]['f14'] if diff else 'none'}"

test("push2 session", em_session)

# 4. akshare 直调
print("\n4. akshare 调用")
def ak_test():
    import akshare as ak
    df = ak.stock_board_concept_name_em()
    return f"{len(df)} boards, cols={list(df.columns[:5])}"

test("akshare concept", ak_test)

# 5. SectorRotationService
print("\n5. SectorRotationService")
def srs_test():
    from src.config import Config
    from src.services.sector_rotation import SectorRotationService
    svc = SectorRotationService(Config())
    result = svc.get_rotation_analysis(top_n=5)
    return f"{len(result.all_sectors)} sectors, theme={result.market_theme[:40]}"

test("SectorRotationService", srs_test)

# 6. DataFetcherManager
print("\n6. DataFetcherManager (东财 fetcher)")
def dfm_test():
    from src.data_provider.manager import DataFetcherManager
    mgr = DataFetcherManager()
    top, bottom = mgr.get_concept_rankings(5)
    return f"top={len(top)} bottom={len(bottom)}"

test("DataFetcherManager", dfm_test)

print("\n" + "=" * 60)
print("诊断完成")
