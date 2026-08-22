"""
查找缺失板块的 TDX 代码

运行方式：
    python scripts/find_missing_board_codes.py

输出：
    打印所有需要添加到 TDX_BOARD_REGISTRY 的板块及其代码
"""

import sys
from pathlib import Path

# 添加项目根目录到 path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def find_missing_codes():
    """查找缺失板块的 TDX 代码"""
    try:
        from mootdx.quotes import Quotes

        # 连接通达信服务器（market=2 概念板块）
        client = Quotes.factory(market=2)
        boards = client.stock_all()

        if boards is None or len(boards) == 0:
            print("ERROR: 无法获取板块列表")
            return

        print(f"总共获取到 {len(boards)} 个概念板块\n")

        # 需要查找的板块名列表
        targets = [
            # 半导体上游（高优先级）
            "元件", "电子化学品", "被动元件", "IGBT", "碳化硅", "氮化镓",
            "高带宽内存", "HBM", "玻璃基板",

            # 电池技术
            "BC电池", "钙钛矿", "TOPCon", "钠离子", "麒麟电池", "刀片电池",
            "换电", "动力电池回收",

            # 新材料
            "PEEK", "碳纤维", "石墨烯", "超导", "有机硅", "合成生物",

            # 华为生态
            "华为欧拉", "欧拉",

            # 机器人
            "机器人执行器", "执行器",

            # 数字经济
            "数字经济", "数字孪生", "国资云",

            # 其他热点
            "短剧", "谷子经济", "可控核聚变", "高压快充",

            # 名称对齐确认
            "CPO概念", "6G概念", "鸿蒙概念", "华为海思", "稀土永磁",
        ]

        # 查找匹配
        found = {}
        for idx, row in boards.iterrows():
            name = str(row.get('name', ''))
            code = str(row.get('code', ''))

            for target in targets:
                if target in name:
                    if code not in found:
                        found[code] = name

        # 输出结果
        print("=== 找到的板块 ===")
        for code in sorted(found.keys()):
            print(f'    "{code}": {{"name": "{found[code]}", "category": "TODO"}},')

        print(f"\n找到 {len(found)} 个板块")

        # 检查未找到的
        found_names = set(found.values())
        not_found = [t for t in targets if not any(t in name for name in found_names)]

        if not_found:
            print("\n=== 未找到的板块（可能东财无此独立板块）===")
            for t in not_found:
                print(f"  - {t}")

    except ImportError as e:
        print(f"ERROR: 无法导入 mootdx: {e}")
        print("请确保已安装: pip install mootdx")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    find_missing_codes()
