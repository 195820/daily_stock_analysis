# -*- coding: utf-8 -*-
"""
通达信板块指数 → CONCEPT_UNIVERSE 分类映射

数据源：mootdx TCP 协议 index_bars()
用途：Node 2 板块分析（完全替代东财 push2/push2his）

映射方式：直接绑定 TDX 代码到 CONCEPT_UNIVERSE category，绕过名称匹配。
验证时间：2026-07-29，所有代码均通过 mootdx stock_all() 交叉验证名称正确。

注意：880xxx 代码的名称以 stock_all() 返回为准，index_bars() 只返回 K 线不含名称。
"""

from typing import Optional

# ============================================================
# TDX 板块代码 → (名称, CONCEPT_UNIVERSE category)
# category 必须与 sector_analysis.py 中 CONCEPT_UNIVERSE 的 key 一致
# 名称来源：mootdx stock_all() 实时查询（2026-07-29 验证）
# ============================================================

TDX_BOARD_REGISTRY: dict[str, dict] = {
    # === AI 上游：芯片/半导体 (ai_chip) ===
    "880491": {"name": "半导体",     "category": "semiconductor_equip"},
    "880952": {"name": "芯片",       "category": "ai_chip"},
    "880672": {"name": "存储芯片",   "category": "memory_chip"},
    "880758": {"name": "汽车芯片",   "category": "auto_chip"},
    "880635": {"name": "先进封装",   "category": "semiconductor_pkg"},
    "880637": {"name": "EDA概念",    "category": "eda_ip"},
    "880608": {"name": "第三代半",   "category": "eda_ip"},
    "880716": {"name": "光刻机",     "category": "semiconductor_equip"},
    # 新增：半导体上游
    "880547": {"name": "玻璃基板",   "category": "semiconductor_pkg"},
    # 注：元件/电子化学品/被动元件/IGBT/碳化硅/氮化镓/高带宽内存 在 TDX 概念板块中不存在独立代码

    # === AI 基建：算力/网络/散热 (ai_infra) ===
    "880669": {"name": "算力租赁",   "category": "compute_power"},
    "880607": {"name": "东数西算",   "category": "compute_power"},
    "880799": {"name": "数据中心",   "category": "compute_power"},
    "880670": {"name": "光通信",     "category": "optical_module"},
    "880656": {"name": "CPO概念",    "category": "optical_module"},
    "880685": {"name": "液冷服务",   "category": "liquid_cooling"},
    "880550": {"name": "PCB概念",    "category": "pcb_connector"},
    "880523": {"name": "铜缆高速",   "category": "pcb_connector"},
    # 新增：充电设施
    "880658": {"name": "高压快充",   "category": "compute_power"},

    # === AI 模型/平台 (ai_model) ===
    "880948": {"name": "人工智能",   "category": "ai_platform"},
    "880645": {"name": "AIGC概念",   "category": "llm"},
    "880693": {"name": "多模态AI",   "category": "llm"},
    "880732": {"name": "AI智能体",   "category": "ai_platform"},
    # 新增：数字经济
    "880738": {"name": "数字孪生",   "category": "digital_economy"},
    "880746": {"name": "国资云",     "category": "digital_economy"},
    # 注：数字经济 在 TDX 概念板块中不存在独立代码

    # === AI 终端应用 (ai_terminal) ===
    "880541": {"name": "AI手机PC",   "category": "ai_phone_pc"},
    "880566": {"name": "AI眼镜",     "category": "ai_phone_pc"},
    "880966": {"name": "消费电子",   "category": "consumer_elec"},
    "880673": {"name": "混合现实",   "category": "consumer_elec"},
    "880945": {"name": "OLED概念",   "category": "consumer_elec"},
    "880725": {"name": "MiniLED",    "category": "consumer_elec"},
    "880561": {"name": "折叠屏",     "category": "consumer_elec"},
    "880574": {"name": "苹果概念",   "category": "apple_chain"},
    # 新增：新内容消费
    "880692": {"name": "短剧游戏",   "category": "new_content"},
    # 注：谷子经济 在 TDX 概念板块中不存在独立代码

    # === 机器人 (robot) ===
    "880703": {"name": "人形机器",   "category": "humanoid"},
    "880904": {"name": "机器人概",   "category": "robot_system"},
    "880675": {"name": "减速器",     "category": "robot_parts"},
    "880664": {"name": "机器视觉",   "category": "robot_parts"},
    # 注：机器人执行器 在 TDX 概念板块中不存在独立代码

    # === 智能驾驶 (auto_drive) ===
    "880944": {"name": "无人驾驶",   "category": "smart_drive"},
    "880552": {"name": "车联网",     "category": "smart_drive"},
    "880686": {"name": "华为汽车",   "category": "smart_drive"},
    "880951": {"name": "新能源车",   "category": "new_energy"},
    "880742": {"name": "固态电池",   "category": "battery_tech"},
    "880583": {"name": "充电桩",     "category": "new_energy"},
    "880391": {"name": "汽车整车",   "category": "auto_market"},
    # 新增：电池技术 + 充电设施
    "880684": {"name": "BC电池",     "category": "battery_tech"},
    "880655": {"name": "钙钛矿电",   "category": "battery_tech"},
    "880638": {"name": "TOPCon电",   "category": "battery_tech"},
    "880755": {"name": "钠电池",     "category": "battery_tech"},
    "880632": {"name": "动力电池",   "category": "battery_tech"},
    "880759": {"name": "换电概念",   "category": "new_energy"},
    # 注：麒麟电池/刀片电池/动力电池回收 在 TDX 概念板块中不存在独立代码

    # === 新兴主题 (emerging) ===
    "880522": {"name": "低空经济",   "category": "low_altitude"},
    "880697": {"name": "飞行汽车",   "category": "low_altitude"},
    "880939": {"name": "无人机",     "category": "low_altitude"},
    "880667": {"name": "数据要素",   "category": "data_element"},
    "880647": {"name": "数据确权",   "category": "data_element"},
    "880901": {"name": "信息安全",   "category": "data_element"},
    "880722": {"name": "华为鸿蒙",   "category": "huawei_chain"},
    "880682": {"name": "华为海思",   "category": "huawei_chain"},
    "880689": {"name": "华为算力",   "category": "huawei_chain"},
    "880546": {"name": "卫星导航",   "category": "satellite"},
    "880548": {"name": "商业航天",   "category": "satellite"},
    "880506": {"name": "5G概念",     "category": "satellite"},
    "880661": {"name": "6G概念",     "category": "satellite"},
    # 注：华为欧拉/可控核聚变 在 TDX 概念板块中不存在独立代码

    # === 科技防守 (tech_defensive) ===
    "880652": {"name": "创新药",     "category": "innovative_drug"},
    "880729": {"name": "CXO概念",    "category": "innovative_drug"},
    "880398": {"name": "医疗保健",   "category": "innovative_drug"},
    "880400": {"name": "医药",       "category": "innovative_drug"},
    "880305": {"name": "电力",       "category": "power_grid"},
    "880520": {"name": "智能电网",   "category": "power_grid"},
    "880964": {"name": "特高压",     "category": "power_grid"},
    "880630": {"name": "虚拟电厂",   "category": "power_grid"},
    "880753": {"name": "绿色电力",   "category": "power_grid"},
    "880308": {"name": "新型电力",   "category": "power_grid"},
    "880730": {"name": "储能",       "category": "energy_storage"},
    "880544": {"name": "光伏",       "category": "energy_storage"},
    "880534": {"name": "锂电池概",   "category": "energy_storage"},
    "880705": {"name": "氢能源",     "category": "energy_storage"},
    "880582": {"name": "风电",       "category": "energy_storage"},
    # 新增：减肥药 + 医美
    "880681": {"name": "减肥药",     "category": "innovative_drug"},
    "880973": {"name": "医美概念",   "category": "innovative_drug"},

    # === 金融 (finance) ===
    "880472": {"name": "券商",       "category": "securities"},
    "880473": {"name": "保险",       "category": "insurance"},
    "880471": {"name": "银行",       "category": "banking"},
    "880474": {"name": "多元金融",   "category": "diversified_finance"},

    # === 外围联动 (external) ===
    "880674": {"name": "英伟达概",   "category": "nvidia"},

    # === 周期 (cyclical) ===
    "880324": {"name": "有色",       "category": "nonferrous"},
    "880325": {"name": "铜",         "category": "nonferrous"},
    "880328": {"name": "黄金",       "category": "nonferrous"},
    "880326": {"name": "铝",         "category": "nonferrous"},
    "880535": {"name": "稀土永磁",   "category": "nonferrous"},
    "880521": {"name": "黄金概念",   "category": "nonferrous"},
    "880318": {"name": "钢铁",       "category": "steel_chem"},
    "880335": {"name": "化工",       "category": "steel_chem"},
    "880330": {"name": "化纤",       "category": "steel_chem"},
    # 新增：新材料
    "880695": {"name": "PEEK材料",   "category": "new_materials"},
    "880571": {"name": "碳纤维概",   "category": "new_materials"},
    "880584": {"name": "石墨烯",     "category": "new_materials"},
    "880905": {"name": "超导概念",   "category": "new_materials"},
    "880975": {"name": "有机硅概",   "category": "new_materials"},
    "880530": {"name": "合成生物",   "category": "new_materials"},

    # === 消费 (consumer) ===
    "880381": {"name": "白酒",       "category": "beverage"},
    "880564": {"name": "白酒概念",   "category": "beverage"},
    "880423": {"name": "酒店餐饮",   "category": "travel"},
    "880424": {"name": "旅游",       "category": "travel"},
    "880597": {"name": "养老概念",   "category": "silver_economy"},
    "880894": {"name": "养老金持",   "category": "silver_economy"},

    # === 军工 (military) ===
    "880507": {"name": "国防军工",   "category": "aerospace"},
    "880430": {"name": "航空",       "category": "aerospace"},
    "880431": {"name": "船舶",       "category": "aerospace"},
    "880728": {"name": "航运概念",   "category": "aerospace"},
    "880528": {"name": "军工信息",   "category": "aerospace"},
    "880950": {"name": "军民融合",   "category": "aerospace"},
}


def get_boards_by_category() -> dict[str, list[dict]]:
    """
    按 CONCEPT_UNIVERSE category 分组返回 TDX 板块。

    Returns:
        {
            "semiconductor_equip": [{"code": "880491", "name": "半导体"}, ...],
            "ai_chip": [{"code": "880952", "name": "芯片"}, ...],
            ...
        }
    """
    grouped: dict[str, list[dict]] = {}
    for code, info in TDX_BOARD_REGISTRY.items():
        cat = info["category"]
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append({"code": code, "name": info["name"]})
    return grouped


def get_board_name(code: str) -> Optional[str]:
    """获取板块名称"""
    info = TDX_BOARD_REGISTRY.get(code)
    return info["name"] if info else None


def get_board_category(code: str) -> Optional[str]:
    """获取板块所属 category"""
    info = TDX_BOARD_REGISTRY.get(code)
    return info["category"] if info else None
