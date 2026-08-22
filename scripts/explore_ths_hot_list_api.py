# -*- coding: utf-8 -*-
"""
探测同花顺热榜页面的 API 端点

用 Playwright 打开页面，拦截所有 XHR/fetch 请求，找到热榜数据的真实 API。
"""

import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

sys.stdout.reconfigure(encoding="utf-8")


def explore_ths_hot_list_api():
    """用 Playwright 探测同花顺热榜的 API 端点"""
    from playwright.sync_api import sync_playwright

    HOT_LIST_URL = "https://eq.10jqka.com.cn/webpage/ths-hot-list/index.html?showStatusBar=true#/"

    # 收集所有 API 请求
    api_requests = []

    def handle_response(response):
        url = response.url
        # 过滤出 API 请求（排除静态资源）
        if any(keyword in url for keyword in [
            "api", "hot", "rank", "list", "query", "data",
            "10jqka", "ths", "eq."
        ]):
            if not any(ext in url for ext in [".js", ".css", ".png", ".jpg", ".gif", ".ico", ".woff"]):
                try:
                    content_type = response.headers.get("content-type", "")
                    if "json" in content_type or "text" in content_type:
                        api_requests.append({
                            "url": url,
                            "method": response.request.method,
                            "status": response.status,
                            "content_type": content_type,
                        })
                except Exception:
                    pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()
        page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

        # 监听所有响应
        page.on("response", handle_response)

        print(f"[探测] 打开同花顺热榜页面...")
        page.goto(HOT_LIST_URL, timeout=30000)

        # 等待页面加载
        page.wait_for_timeout(5000)

        # 尝试切换到不同标签页（热股/板块/热门等）
        tabs = ["热股", "板块", "ETF", "热门", "可转债", "港美", "热基", "期货"]
        for tab_name in tabs:
            try:
                tab_elem = page.locator(f"text={tab_name}").first
                if tab_elem.is_visible():
                    tab_elem.click()
                    page.wait_for_timeout(2000)
                    print(f"[探测] 切换到标签: {tab_name}")
            except Exception as e:
                pass

        # 再等一会儿收集更多请求
        page.wait_for_timeout(3000)

        browser.close()

    # 输出结果
    print(f"\n{'='*70}")
    print(f"  发现 {len(api_requests)} 个 API 请求")
    print(f"{'='*70}\n")

    # 去重并排序
    seen_urls = set()
    unique_requests = []
    for req in api_requests:
        # 去掉 query string 进行去重
        base_url = req["url"].split("?")[0]
        if base_url not in seen_urls:
            seen_urls.add(base_url)
            unique_requests.append(req)

    for i, req in enumerate(unique_requests, 1):
        print(f"[{i}] {req['method']} {req['status']}")
        print(f"    URL: {req['url'][:200]}")
        print(f"    Content-Type: {req['content_type']}")
        print()

    # 保存完整结果
    output_path = Path(__file__).parent / "ths_hot_list_api_discovery.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(unique_requests, f, ensure_ascii=False, indent=2)
    print(f"[保存] 结果已保存到: {output_path}")

    return unique_requests


if __name__ == "__main__":
    explore_ths_hot_list_api()
