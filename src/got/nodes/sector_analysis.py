"""
Node 2: 板块分析节点

职责：识别板块状态（主线/衰竭/埋伏/防守），判断启动和过热信号，为选股提供方向。

设计理念：
- 分状态输出：active_mainline / exhausted_mainline / ambush / defensive_rotation
- 启动信号：3硬门槛 + 5维加权打分（P0-3 决策）
- 过热信号：4条件任一触发
- 主线分级：S/A/B 三维评估（P2-1）
- 生命周期：launch / acceleration / mature / late_stage / mature_exhausted
- 纯规则驱动 + LLM 仅生成 reasoning 文本

数据源：通过 DataFetcherManager 代理（支持多源降级，替代直连 _EastMoneyClient）
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from data_provider.base import DataFetcherManager
from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY, get_boards_by_category
from src.got.indicators import clip, calculate_macd
from src.got.schemas import (
    DataQuality,
    LaunchSignal,
    LifecycleInfo,
    MarketContext,
    SectorContext,
    SectorDetail,
)
from src.storage import DatabaseManager, Node2SectorTrendRecord, SectorFrameRecord

logger = logging.getLogger(__name__)


# =============================================================================
# 主线家族关键词（Node 2 v2：替代旧的 CONCEPT_UNIVERSE + _FRAME_GROUP_KEYWORDS）
# ~10 个家族，每个板块名命中首个匹配的家族，未命中 → "其他"
# =============================================================================

FAMILY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("AI硬件", [
        "芯片", "半导体", "光刻", "EDA", "封装", "HBM", "存储芯片", "GPU",
        "寒武纪", "昇腾", "RISC-V", "国产芯片", "算力", "光模块", "CPO",
        "液冷", "PCB", "铜缆", "数据中心", "东数西算", "服务器",
        "海思", "华为算力",
    ]),
    ("AI软件", [
        "大模型", "AIGC", "多模态", "人工智能", "AI应用", "AI Agent",
        "智能体", "智算", "AI语料", "AI手机", "AI PC", "AI眼镜", "AIPC",
        "消费电子", "MR", "AR", "VR", "OLED", "MiniLED", "折叠屏",
        "苹果概念", "数据要素", "数据确权", "数据交易", "数据安全",
        "鸿蒙", "信息安全",
    ]),
    ("机器人", [
        "人形机器人", "人形机器", "具身智能", "机器人", "减速器", "丝杠", "伺服",
    ]),
    ("智能驾驶", [
        "自动驾驶", "智能驾驶", "智能座舱", "车路云", "激光雷达",
        "无人驾驶", "车联网", "华为汽车",
    ]),
    ("新能源", [
        "储能", "光伏", "锂电池", "氢能", "风电", "新能源",
        "电力", "特高压", "智能电网", "虚拟电厂", "输变电",
        "核电", "核能", "绿色电力", "绿电", "水利", "水电",
    ]),
    ("医药", [
        "创新药", "生物医药", "CXO", "医疗器械", "医药",
    ]),
    ("金融", [
        "证券", "券商", "保险", "银行", "信托", "多元金融",
    ]),
    ("周期资源", [
        "黄金", "有色", "铜", "铝", "稀土", "锂矿",
        "钢铁", "化工", "化纤",
    ]),
    ("军工", [
        "军工", "航空", "船舶", "航运", "航天", "国防", "军民融合",
    ]),
    ("消费", [
        "白酒", "食品", "家电", "旅游", "酒店", "零售",
    ]),
]

# 家族名快速查找集
_FAMILY_NAMES = {name for name, _ in FAMILY_KEYWORDS}

CONCEPT_UNIVERSE: dict[str, dict[str, list[str]]] = {
    # === AI 上游：芯片/半导体 ===
    "ai_chip": {
        "semiconductor_equip": ["半导体设备", "光刻机", "刻蚀机"],
        "ai_chip": ["AI芯片", "GPU", "寒武纪", "昇腾"],
        "memory_chip": ["存储芯片", "HBM", "内存"],
        "auto_chip": ["汽车芯片", "MCU芯片", "车规芯片"],
        "semiconductor_pkg": ["先进封装", "Chiplet"],
        "eda_ip": ["EDA", "芯片概念", "半导体概念", "第三代半导体", "第四代半导体", "RISC-V", "国产芯片"],
    },
    # === AI 基建：算力/网络/散热 ===
    "ai_infra": {
        "compute_power": ["算力", "算力租赁", "东数西算", "AI服务器", "服务器"],
        "optical_module": ["光模块", "CPO", "共封装光学", "光通信", "光通信模块", "光纤概念"],
        "liquid_cooling": ["液冷", "液冷服务器", "温控", "散热"],
        "pcb_connector": ["PCB", "高速连接器", "PCB概念", "铜缆连接", "铜缆高速连接", "数据中心"],
    },
    # === AI 模型/平台 ===
    "ai_model": {
        "llm": ["大模型", "AIGC", "多模态", "多模态AI", "AI语料"],
        "ai_platform": ["人工智能", "AI应用", "AI Agent", "智算中心"],
    },
    # === AI 终端应用 ===
    "ai_terminal": {
        "ai_phone_pc": ["AI手机", "AI PC", "AI眼镜", "AIPC"],
        "consumer_elec": ["消费电子", "MR", "AR", "VR", "MIXED REALITY", "混合现实", "OLED", "MiniLED", "柔性屏"],
        "apple_chain": ["苹果概念"],
    },
    # === 机器人 ===
    "robot": {
        "humanoid": ["人形机器人", "具身智能", "机器人概念"],
        "robot_parts": ["减速器", "丝杠", "传感器", "伺服", "控制器", "电机"],
        "robot_system": ["机器人", "工业机器人", "服务机器人"],
    },
    # === 智能驾驶 ===
    "auto_drive": {
        "smart_drive": ["自动驾驶", "智能驾驶", "智能座舱", "车路云", "激光雷达"],
        "new_energy": ["新能源汽车", "新能源车", "固态电池", "充电桩"],
        "auto_market": ["汽车整车"],
    },
    # === 新兴主题 ===
    "emerging": {
        "low_altitude": ["低空经济", "飞行汽车", "eVTOL", "无人机"],
        "data_element": ["数据要素", "数据确权", "数据交易", "数据安全"],
        "huawei_chain": ["华为产业链", "鸿蒙", "昇腾", "华为手机", "海思"],
        "satellite": ["卫星互联网", "卫星导航", "北斗", "5G概念", "6G"],
    },
    # === 科技防守 ===
    "tech_defensive": {
        "innovative_drug": ["创新药", "生物医药", "CXO", "医疗器械"],
        "power_grid": ["电力", "电力设备", "特高压", "智能电网", "电力物联网", "绿色电力", "虚拟电厂", "核电", "核电概念"],
        "energy_storage": ["储能", "光伏概念", "锂电池概念", "氢能", "风电", "新能源"],
    },
    # === 金融 ===
    "finance": {
        "securities": ["证券", "券商"],
        "insurance": ["保险"],
        "banking": ["银行"],
        "diversified_finance": ["多元金融", "信托"],
    },
    # === 外围联动 ===
    "external": {
        "nvidia": ["英伟达概念"],
    },
    # === 周期 ===
    "cyclical": {
        "nonferrous": ["有色金属", "铜", "黄金概念", "稀土", "铝"],
        "steel_chem": ["钢铁", "化工", "化纤", "涂料"],
    },
    # === 军工 ===
    "military": {
        "aerospace": ["航天", "军工", "航空发动机", "卫星", "无人机"],
    },
}

# category → 标签
_CATEGORY_GROUP: dict[str, str] = {
    # AI 上游
    "semiconductor_equip": "半导体",
    "ai_chip": "芯片",
    "memory_chip": "存储芯片",
    "auto_chip": "汽车芯片",
    "semiconductor_pkg": "先进封装",
    "eda_ip": "EDA/IP",
    # AI 基建
    "compute_power": "算力",
    "optical_module": "光模块/CPO",
    "liquid_cooling": "液冷散热",
    "pcb_connector": "PCB/连接器",
    # AI 模型
    "llm": "大模型",
    "ai_platform": "AI平台",
    # AI 终端
    "ai_phone_pc": "AI手机/PC",
    "consumer_elec": "消费电子",
    "apple_chain": "苹果产业链",
    # 机器人
    "humanoid": "人形机器人",
    "robot_parts": "机器人零部件",
    "robot_system": "机器人整机",
    # 智能驾驶
    "smart_drive": "智能驾驶",
    "new_energy": "新能源汽车",
    "auto_market": "汽车整车",
    # 新兴主题
    "low_altitude": "低空经济",
    "data_element": "数据要素",
    "huawei_chain": "华为产业链",
    "satellite": "卫星/6G",
    # 科技防守
    "innovative_drug": "创新药",
    "power_grid": "电力",
    "energy_storage": "新能源",
    # 金融
    "securities": "证券",
    "insurance": "保险",
    "banking": "银行",
    "diversified_finance": "多元金融",
    # 外围联动
    "nvidia": "英伟达",
    # 周期
    "nonferrous": "有色",
    "steel_chem": "钢铁化工",
    # 军工
    "aerospace": "军工",
}

# 所有关键词扁平列表（用于匹配）
_ALL_KEYWORDS: list[str] = [
    kw
    for group in CONCEPT_UNIVERSE.values()
    for keywords in group.values()
    for kw in keywords
]


# =============================================================================
# 常量配置
# =============================================================================

# 4 维强度评分权重（Node 2 v2：全部使用截面百分位排名）
STRENGTH_WEIGHTS: dict[str, float] = {
    "price_momentum": 0.30,
    "turnover_expansion": 0.25,
    "relative_strength": 0.25,
    "breadth_health": 0.20,
}

# 板块 → 核心成分股代码映射（mootdx TCP 获取 T-0 实时行情用）
# key 必须与 TDX_BOARD_REGISTRY 中的 name 一致
SECTOR_CONSTITUENTS: dict[str, list[str]] = {
    # 周期
    "黄金":     ["601899", "600547", "600489", "002155", "600988", "600766"],
    "有色":     ["601899", "603993", "000630", "601168", "000878", "600362"],
    # AI 模型/平台
    "人工智能": ["002230", "300496", "688111", "002415", "300474", "688561"],
    "AIGC概念": ["002230", "300496", "688111", "300474", "002415"],
    # 算力/基建
    "算力租赁": ["000977", "603019", "002261", "300474", "688111"],
    "光通信":   ["300308", "300502", "002281", "300394", "688538"],
    "液冷服务": ["002837", "300990", "002182", "300476"],
    "PCB概念":  ["002463", "603920", "002916", "300476"],
    # AI 上游
    "半导体":   ["688981", "603501", "002371", "688012", "603986"],
    "芯片":     ["688981", "603501", "002371", "688012", "603986"],
    "存储芯片": ["603986", "688981", "002371", "688256", "688091"],
    # 机器人
    "人形机器": ["300124", "002747", "688169", "300024", "002527"],
    "机器人概": ["300124", "002747", "688169", "300024"],
    "减速器":   ["002527", "603416", "300024", "002472", "300750"],
    # 军工
    "国防军工": ["600893", "000768", "600760", "601989", "600150"],
    "航空":     ["600893", "600118", "000738", "002013"],
    "船舶":     ["600150", "601989", "600685", "600482"],
    "航运概念": ["601919", "601866", "600428", "601872"],
    # 科技防守
    "创新药":   ["600276", "300347", "002821", "300759", "688180"],
    "CXO概念":  ["300347", "300759", "688180", "300760"],
    "电力":     ["600900", "601985", "600886", "600674", "003816"],
    "特高压":   ["601126", "600089", "002028", "600312"],
    # 新能源
    "储能":     ["300750", "002074", "300274", "688390"],
    "光伏":     ["601012", "002459", "600438", "601615"],
    "新能源车": ["002594", "601238", "600104", "002460"],
    "汽车整车": ["600104", "002594", "601238", "600741", "000625"],
    # 新兴主题
    "华为鸿蒙": ["002415", "300496", "002230", "300033"],
    "华为海思": ["688981", "002371", "603501", "688012"],
    "卫星导航": ["600118", "002025", "600990", "300053"],
    "低空经济": ["002097", "688599", "300034", "002085"],
    "飞行汽车": ["002097", "688599", "300034"],
    "信息安全": ["002268", "300168", "002439", "300352", "300075"],
    # 金融
    "证券":     ["601995", "600030", "601688", "600837", "000776"],
    "保险":     ["601318", "601628", "601601", "002736"],
    "银行":     ["601398", "601939", "601288", "600036", "601166"],
    "多元金融": ["600030", "000776", "600837", "000987", "601688"],
    # 外围联动
    "苹果概念": ["002475", "002241", "300115", "002600"],
    "英伟达概": ["300474", "688111", "002230", "300496"],
}

# 主线分级 → 生命周期天数阈值: (launch_end, acceleration_end, mature_end)
LIFECYCLE_THRESHOLDS: dict[str, tuple[int, int, int]] = {
    "S": (7, 30, 60),
    "A": (5, 15, 30),
    "B": (3, 10, 20),
}

# 启动确认门槛：从低点涨幅超过此比例才算真正启动
_LAUNCH_CONFIRM_THRESHOLD = 0.05  # 5%

# 生命周期 → 推荐操作
_LIFECYCLE_ACTION_MAP: dict[str, str] = {
    "launch": "aggressive_buy",
    "acceleration": "buy",
    "mature": "reduce_or_hold",
    "late_stage": "exit_only",
    "mature_exhausted": "exit",
}

# 国家级政策重点方向（主线分级加分项，对应 FAMILY_KEYWORDS 家族名）
_POLICY_SUPPORTED_CHAINS = {"AI硬件", "AI软件", "新能源"}

# 板块列表获取数量上限（akshare 单次可返回全量 ~500 个）
_BOARD_FETCH_LIMIT = 500

# 子串匹配时，关键词需覆盖板名长度的最低比例（防止短关键词误匹配）
_KEYWORD_MIN_COVERAGE = 0.3


def _keyword_matches(board_name: str, keywords: list[str]) -> bool:
    """判断板名是否命中任一关键词。

    优先级：
    1. 精确匹配（name == kw）
    2. 前缀匹配（name 以 kw 开头）
    3. 子串匹配：仅当 kw 长度 ≥ 板名长度 × _KEYWORD_MIN_COVERAGE
    4. 括号别名匹配：板名括号内包含关键词（如"共封装光学(CPO)" 匹配 "CPO"）
    """
    import re
    # 提取括号内别名
    paren_aliases = re.findall(r'[（(](.*?)[）)]', board_name)

    for kw in keywords:
        if board_name == kw:
            return True
        if board_name.startswith(kw):
            return True
        if len(kw) >= len(board_name) * _KEYWORD_MIN_COVERAGE and kw in board_name:
            return True
        # 括号别名精确匹配
        if any(kw == alias for alias in paren_aliases):
            return True
    return False

# 主线框 → 产业主题映射（sub-category → 框主题名）
# 同一主题的多个板块共享一个主线框记录
_FRAME_GROUP: dict[str, str] = {
    # AI 全产业链
    "semiconductor_equip": "AI产业链", "ai_chip": "AI产业链",
    "memory_chip": "AI产业链", "auto_chip": "AI产业链",
    "semiconductor_pkg": "AI产业链", "eda_ip": "AI产业链",
    "compute_power": "AI产业链", "optical_module": "AI产业链",
    "liquid_cooling": "AI产业链", "pcb_connector": "AI产业链",
    "llm": "AI产业链", "ai_platform": "AI产业链",
    "ai_phone_pc": "AI产业链", "consumer_elec": "AI产业链",
    "apple_chain": "AI产业链",
    # 机器人
    "humanoid": "机器人", "robot_parts": "机器人", "robot_system": "机器人",
    # 智能驾驶
    "smart_drive": "智能驾驶", "new_energy": "智能驾驶", "auto_market": "智能驾驶",
    # 新兴主题（各自独立）
    "low_altitude": "低空经济", "data_element": "数据要素",
    "huawei_chain": "华为系", "satellite": "卫星通信",
    # 科技防守
    "innovative_drug": "医药", "power_grid": "电力", "energy_storage": "新能源",
    # 金融
    "securities": "金融", "insurance": "金融", "banking": "金融",
    "diversified_finance": "金融",
    # 其他
    "nvidia": "外围联动",
    "nonferrous": "黄金/有色", "steel_chem": "钢铁化工",
    "aerospace": "军工",
}

# =============================================================================
# 动态主线框映射（按板块名称关键词匹配，用于全量发现模式）
# 按优先级排列，首个命中即返回。未命中 → "其他"
# =============================================================================
_FRAME_GROUP_KEYWORDS: list[tuple[list[str], str]] = [
    # AI 全产业链
    (["芯片", "半导体", "光刻", "EDA", "封装", "HBM", "存储芯片", "GPU", "寒武纪", "昇腾", "RISC-V", "国产芯片"], "AI产业链"),
    (["算力", "光模块", "CPO", "液冷", "PCB", "铜缆", "数据中心", "东数西算", "服务器"], "AI产业链"),
    (["大模型", "AIGC", "多模态", "人工智能", "AI应用", "AI Agent", "智能体", "智算", "AI语料"], "AI产业链"),
    (["AI手机", "AI PC", "AI眼镜", "AIPC", "消费电子", "MR", "AR", "VR", "OLED", "MiniLED", "折叠屏"], "AI产业链"),
    (["苹果概念"], "AI产业链"),
    # 机器人
    (["人形机器人", "人形机器", "具身智能", "机器人", "减速器", "丝杠", "伺服"], "机器人"),
    # 智能驾驶（必须在新能源/电力之前，"新能源车"优先归驾驶）
    (["自动驾驶", "智能驾驶", "智能座舱", "车路云", "激光雷达", "无人驾驶", "车联网"], "智能驾驶"),
    (["新能源汽车", "新能源车", "固态电池", "充电桩", "汽车整车"], "智能驾驶"),
    # 数据要素
    (["数据要素", "数据确权", "数据交易", "数据安全", "信息安全"], "数据要素"),
    # 华为系
    (["华为", "鸿蒙", "海思"], "华为系"),
    # 低空经济
    (["低空经济", "飞行汽车", "eVTOL", "无人机"], "低空经济"),
    # 卫星/通信
    (["卫星", "北斗", "5G", "6G", "商业航天", "航天"], "卫星通信"),
    # 核电/绿电/电力（水电/水利归此处）
    (["核电", "核能"], "核电/绿电"),
    (["绿色电力", "绿电", "水利", "水电", "清洁"], "核电/绿电"),
    # 新能源/储能（风电/光伏/储能归此处）
    (["储能", "光伏", "锂电池", "氢能", "风电", "新能源"], "新能源"),
    # 电力（特高压/电网归此处，不含水电/核电）
    (["电力", "特高压", "智能电网", "虚拟电厂", "输变电"], "电力"),
    # 医药
    (["创新药", "生物医药", "CXO", "医疗器械", "医药"], "医药"),
    # 金融
    (["证券", "券商", "保险", "银行", "信托", "多元金融"], "金融"),
    # 黄金/有色
    (["黄金", "有色", "铜", "铝", "稀土", "锂矿"], "黄金/有色"),
    # 钢铁化工
    (["钢铁", "化工", "化纤", "涂料"], "钢铁化工"),
    # 军工
    (["军工", "航空", "船舶", "航运", "国防", "军民融合"], "军工"),
    # 外围
    (["英伟达"], "外围联动"),
]


def _build_frame_group(board_name: str) -> str:
    """根据板块名称动态归入主线家族（Node 2 v2：使用 FAMILY_KEYWORDS）。"""
    for family_name, keywords in FAMILY_KEYWORDS:
        if any(kw in board_name for kw in keywords):
            return family_name
    return "其他"


# 并发请求限制
_CONCURRENT_FETCHES = 5

# K 线回溯天数
_KLINE_LOOKBACK_DAYS = 90

# 快筛 Top N（涨跌幅各取 N 个进入完整评分）
_QUICK_TOP_N = 30

# 非概念板块噪声关键词（纯统计指数/风格指数，不应参与主线分析）
_NOISE_BOARD_KEYWORDS = [
    "停板", "涨跌家数", "微盘", "送转", "超跌", "平均股价",
    "全Ａ", "总值", "中位", "停板家数",
]


def _is_noise_board(board_name: str) -> bool:
    """过滤非概念板块（统计指数/风格指数/行业指数）。"""
    return any(kw in board_name for kw in _NOISE_BOARD_KEYWORDS)


class SectorAnalysisNode:
    """板块分析节点（GOT Node 2）"""

    def __init__(self, use_mock: bool = True):
        self.use_mock = use_mock
        self._fetcher_manager: Optional[DataFetcherManager] = None

    @property
    def fetcher(self) -> DataFetcherManager:
        """懒加载 DataFetcherManager（替代直连 _EastMoneyClient）。"""
        if self._fetcher_manager is None:
            self._fetcher_manager = DataFetcherManager()
        return self._fetcher_manager

    async def run(self, market_context: MarketContext) -> SectorContext:
        """执行板块分析。

        流程：获取数据 → 5维强度 → 启动信号 → 过热信号 → 状态判定 → 分级 → 生命周期 → LLM reasoning
        """
        if self.use_mock:
            logger.info("[Node 2] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 2] 开始真实板块分析...")

        quality = DataQuality()

        try:
            # Step 1: 并发获取板块数据
            sectors_raw = await self._fetch_sector_data(quality)
            if not sectors_raw:
                logger.warning("[Node 2] 板块数据不可用，返回降级空结果")
                quality.fetch_failed_fields.append("all_sector_data")
                quality.missing_fields.append("concept_board_list")
                return SectorContext(
                    status="degraded",
                    active_mainline=[],
                    exhausted_mainline=[],
                    ambush=[],
                    defensive_rotation=[],
                    data_quality=quality,
                    reasoning="板块数据源不可用（mootdx TCP 连接失败），跳过板块分析",
                )

            # Step 1.5: 加载板块趋势历史（跨日连续性）
            prev_trends = self._load_all_sector_trends(sectors_raw)

            # Step 1.6: 两阶段评分（Node 2 v2）
            # Pass 1: 计算所有板块原始指标
            all_raw_metrics: list[dict[str, float]] = []
            valid_indices: list[int] = []
            for i, sector_info in enumerate(sectors_raw):
                klines = sector_info.get("klines", [])
                df = self._klines_to_df(klines)
                if df is None or len(df) < 15:
                    all_raw_metrics.append({
                        "price_momentum": 0.0,
                        "turnover_expansion": 1.0,
                        "relative_strength": 0.0,
                        "breadth_health": 0.5,
                    })
                    continue
                raw = self._calculate_raw_metrics(df, sector_info)
                all_raw_metrics.append(raw)
                valid_indices.append(i)

            # Pass 2: 截面百分位排名
            all_ranked = self._apply_cross_sectional_ranking(all_raw_metrics)

            # 记录有效维度到 DataQuality
            has_breadth = any(
                s.get("up_count", 0) + s.get("down_count", 0) > 0
                for s in sectors_raw
            )
            quality.effective_dimensions = {
                "price_momentum": True,
                "turnover_expansion": True,
                "relative_strength": True,
                "breadth_health": has_breadth,
            }
            quality.board_fetch_stats = {
                "total": len(sectors_raw),
                "success": len(valid_indices),
                "failed": len(sectors_raw) - len(valid_indices),
            }

            # Step 2: 逐板块分析（使用百分位排名后的指标）
            analyzed = []
            for i, sector_info in enumerate(sectors_raw):
                prev_trend = prev_trends.get(sector_info["code"])
                ranked_indicators = all_ranked[i] if i < len(all_ranked) else None
                detail = self._analyze_single_sector(
                    sector_info, market_context, quality,
                    prev_trend=prev_trend,
                    ranked_indicators=ranked_indicators,
                )
                if detail is not None:
                    analyzed.append(detail)

            if not analyzed:
                logger.warning("[Node 2] 所有板块分析失败，返回降级空结果")
                quality.fetch_failed_fields.append("all_sector_analysis")
                return SectorContext(
                    status="degraded",
                    active_mainline=[],
                    exhausted_mainline=[],
                    ambush=[],
                    defensive_rotation=[],
                    data_quality=quality,
                    reasoning="板块数据分析全部失败，跳过板块分析",
                )

            # Step 3: 按状态分类（raw，不含框管理）
            result = self._classify_sectors(analyzed, market_context)

            # Step 3.3: 框系统 — 加载 → 进出管理 → 整合输出
            frames = self._load_frames()
            exhausted_from_frame = self._manage_frames(frames, analyzed)
            result = self._integrate_frames(result, frames, exhausted_from_frame, analyzed)

            # Step 3.4: 主线家族聚合（Node 2 v2）
            result.theme_families = self._build_theme_families(analyzed)

            # Step 3.4b: 行业板块双视角（Node 2 v2，与概念板块独立评分）
            result.industry_top = await self._fetch_and_score_industry_boards(quality)

            # Step 3.5: 降级摘要 + 可信度标记（Node 2 v2）
            quality.degradation_summary = self._build_degradation_summary(quality)
            failed_count = quality.board_fetch_stats.get("failed", 0) if quality.board_fetch_stats else 0
            total_count = quality.board_fetch_stats.get("total", 1) if quality.board_fetch_stats else 1
            if failed_count / max(total_count, 1) > 0.3:
                result.confidence_level = "low"
            elif failed_count > 0:
                result.confidence_level = "medium"
            else:
                result.confidence_level = "high"

            result.data_quality = quality

            # Step 3.6: 板块趋势持久化（跨日记忆）
            self._save_sector_trends(analyzed)
            self._save_frames(frames)

            # Step 4: LLM 生成 reasoning
            result.reasoning = await self._generate_reasoning(result)

            logger.info(
                f"[Node 2] 完成 — "
                f"主线:{len(result.active_mainline)} "
                f"衰竭:{len(result.exhausted_mainline)} "
                f"埋伏:{len(result.ambush)} "
                f"防守:{len(result.defensive_rotation)}"
            )
            return result

        except Exception as e:
            logger.error(f"[Node 2] 板块分析失败: {e}", exc_info=True)
            raise

    # =================================================================
    # 数据获取
    # =================================================================

    async def _fetch_sector_data(
        self, quality: DataQuality,
    ) -> list[dict[str, Any]]:
        """获取概念板块数据（两阶段动态发现）。

        Phase A（快筛）：mootdx stock_all() → 全量概念板块 → 2天K线算涨跌 → Top N
        Phase B（完整评分）：Top N 涨 + Top N 跌 + registry → 90天K线 → 5维评分
        降级：stock_all() 失败 → 退回 TDX_BOARD_REGISTRY 硬编码列表
        """
        days = _KLINE_LOOKBACK_DAYS
        matched = await self._discover_boards(quality)

        if not matched:
            quality.missing_fields.append("concept_universe_match")
            return []

        quality.available_fields.append("concept_universe_match")
        logger.info(f"[Node 2] 候选板块: {len(matched)} 个（含动态发现）")

        # Phase B: 并发获取完整 K 线（mootdx TCP P0）
        semaphore = asyncio.Semaphore(_CONCURRENT_FETCHES)

        async def fetch_one(m: dict) -> Optional[dict]:
            async with semaphore:
                try:
                    kline_df = await asyncio.to_thread(
                        self.fetcher.get_board_kline,
                        m["code"], "concept", days,
                    )
                    if kline_df is None or len(kline_df) < 5:
                        return None

                    if len(kline_df) >= 2:
                        latest_close = float(kline_df.iloc[-1]["close"])
                        prev_close = float(kline_df.iloc[-2]["close"])
                        if prev_close > 0:
                            m["change_pct"] = round(
                                (latest_close - prev_close) / prev_close * 100, 2
                            )

                    last_row = kline_df.iloc[-1]
                    if "up_count" in last_row:
                        m["up_count"] = int(last_row.get("up_count", 0))
                    if "down_count" in last_row:
                        m["down_count"] = int(last_row.get("down_count", 0))

                    klines = self._df_to_klines(kline_df, m["name"])
                    if not klines:
                        return None
                    m["klines"] = klines
                    return m
                except Exception as e:
                    logger.info(f"板块 {m['name']}({m['code']}) K线获取失败: {e}")
                    return None

        results = await asyncio.gather(*(fetch_one(m) for m in matched))
        sectors = [r for r in results if r is not None]
        failed = [m for m, r in zip(matched, results) if r is None]

        if failed:
            logger.info(f"[Node 2] K线失败 {len(failed)} 个，3s 后重试: {[m['name'] for m in failed]}")
            await asyncio.sleep(3)
            retry_results = await asyncio.gather(*(fetch_one(m) for m in failed))
            retry_ok = [r for r in retry_results if r is not None]
            retry_fail = [m for m, r in zip(failed, retry_results) if r is None]
            sectors.extend(retry_ok)
            if retry_ok:
                logger.info(f"[Node 2] 重试成功 {len(retry_ok)} 个: {[r['name'] for r in retry_ok]}")
            if retry_fail:
                logger.info(f"[Node 2] 重试仍失败 {len(retry_fail)} 个: {[m['name'] for m in retry_fail]}")
                for m in retry_fail:
                    quality.fetch_failed_fields.append(f"kline_{m['code']}")

        if not sectors:
            quality.fetch_failed_fields.append("all_sector_klines")
        else:
            quality.available_fields.append("sector_klines")

        realtime_changes = self._fetch_sector_realtime()
        for s in sectors:
            board_name = s.get("name", "")
            if board_name in realtime_changes:
                s["today_change"] = realtime_changes[board_name]

        logger.info(
            f"[Node 2] 完成: {len(sectors)}/{len(matched)} 个板块获取成功",
        )
        return sectors

    async def _discover_boards(
        self, quality: DataQuality,
    ) -> list[dict[str, Any]]:
        """两阶段板块发现。

        Phase A: stock_all() → 652 概念板块 → 快筛 2天K线 → 涨跌幅 Top N
        Phase B: Top N 涨 + Top N 跌 ∪ registry 60 → 候选集
        降级: stock_all() 失败 → 仅用 registry
        """
        # 基础盘：TDX_BOARD_REGISTRY 硬编码
        registry_boards = self._build_registry_boards()
        registry_codes = {b["code"] for b in registry_boards}

        # Phase A: 全量发现
        all_boards = await asyncio.to_thread(self.fetcher.get_all_concept_boards)
        if not all_boards:
            logger.warning("[Node 2] stock_all() 失败，降级为 registry 硬编码")
            quality.available_fields.append("concept_board_list")
            return registry_boards

        quality.available_fields.append("concept_board_list")

        # 过滤噪声板块（统计指数/风格指数/行业指数）
        all_boards = [b for b in all_boards if not _is_noise_board(b["name"])]
        logger.info(f"[Node 2] 过滤后概念板块: {len(all_boards)} 个")

        # 快筛：批量取 2天 K线 → 当日涨跌幅
        all_codes = [b["code"] for b in all_boards]
        code_name_map = {b["code"]: b["name"] for b in all_boards}

        quick_changes = await asyncio.to_thread(
            self.fetcher.get_board_quick_change, all_codes,
        )

        if not quick_changes:
            logger.warning("[Node 2] 快筛涨跌全部失败，降级为 registry")
            return registry_boards

        logger.info(f"[Node 2] 快筛成功: {len(quick_changes)}/{len(all_codes)} 个板块")

        # 按涨跌幅排序，取 Top N
        sorted_boards = sorted(quick_changes.items(), key=lambda x: x[1], reverse=True)
        top_up = sorted_boards[:_QUICK_TOP_N]
        top_down = sorted_boards[-_QUICK_TOP_N:] if len(sorted_boards) > _QUICK_TOP_N else []

        # 合并候选集（去重，registry 保底）
        candidate_codes: dict[str, str] = {}  # code → name
        for code, _ in top_up + top_down:
            name = code_name_map.get(code, code)
            # 动态发现的板块必须能归入已知主线框，否则不参与分析
            if code not in registry_codes and _build_frame_group(name) == "其他":
                continue
            candidate_codes[code] = name
        for b in registry_boards:
            if b["code"] not in candidate_codes:
                candidate_codes[b["code"]] = b["name"]

        # 构建候选板块列表（动态 frame_group）
        matched: list[dict] = []
        for code, name in candidate_codes.items():
            frame_group = _build_frame_group(name)
            is_from_registry = code in registry_codes
            matched.append({
                "code": code,
                "name": name,
                "category": "dynamic" if not is_from_registry else "registry",
                "group": frame_group,
                "frame_group": frame_group,
                "change_pct": quick_changes.get(code, 0.0),
                "up_count": 0,
                "down_count": 0,
                "turnover_rate": 0.0,
            })

        dynamic_count = sum(1 for m in matched if m["category"] == "dynamic")
        logger.info(
            f"[Node 2] 候选集: {len(matched)} 个 "
            f"(动态发现 {dynamic_count} + registry {len(matched) - dynamic_count})",
        )
        return matched

    def _build_registry_boards(self) -> list[dict[str, Any]]:
        """从 TDX_BOARD_REGISTRY 构建基础板块列表。"""
        boards_by_cat = get_boards_by_category()
        matched: list[dict] = []
        for cat_name, boards in boards_by_cat.items():
            for board in boards:
                matched.append({
                    "code": board["code"],
                    "name": board["name"],
                    "category": cat_name,
                    "group": _CATEGORY_GROUP.get(cat_name, cat_name),
                    "frame_group": _FRAME_GROUP.get(cat_name, cat_name),
                    "change_pct": 0.0,
                    "up_count": 0,
                    "down_count": 0,
                    "turnover_rate": 0.0,
                })
        return matched

    @staticmethod
    def _df_to_klines(df: pd.DataFrame, board_name: str) -> list[dict]:
        """将 Manager 返回的 DataFrame 转换为 Node 2 期望的 kline 格式。"""
        klines = []
        for _, row in df.iterrows():
            klines.append({
                "date": str(row.get("date", "")),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
                "amount": float(row.get("amount", 0)),
                "name": board_name,
            })
        return klines

    @staticmethod
    def _fetch_sector_realtime() -> dict[str, float]:
        """用 mootdx TCP 获取板块核心成分股 T-0 实时涨跌幅。

        Returns:
            {板块名称: 成分股涨跌幅中位数}，如 {"黄金概念": -3.38, "金属铜": -2.15}
            获取失败时返回空 dict。
        """
        result: dict[str, float] = {}
        try:
            from mootdx.quotes import Quotes
            import statistics

            client = Quotes.factory(market="std")
        except Exception as exc:
            logger.warning(f"[Node 2] mootdx 初始化失败: {exc}")
            return result

        for sector_name, constituents in SECTOR_CONSTITUENTS.items():
            try:
                df = client.quotes(constituents)
                pcts = []
                for _, row in df.iterrows():
                    price = float(row.get("price", 0))
                    last_close = float(row.get("last_close", 0))
                    if last_close > 0 and price > 0:
                        pct = (price - last_close) / last_close * 100
                        pcts.append(pct)
                if pcts:
                    result[sector_name] = round(statistics.median(pcts), 2)
            except Exception as exc:
                logger.debug(f"[Node 2] {sector_name} T-0 获取失败: {exc}")

        if result:
            logger.info(f"[Node 2] T-0 板块涨跌幅: {result}")
        return result

    # =================================================================
    # 单板块分析流水线
    # =================================================================

    def _analyze_single_sector(
        self,
        sector_info: dict,
        market_context: MarketContext,
        quality: DataQuality,
        prev_trend: Optional[dict] = None,
        ranked_indicators: Optional[dict[str, float]] = None,
    ) -> Optional[SectorDetail]:
        """对单个板块执行完整分析：强度 → 信号 → 状态 → 分级 → 生命周期。

        Args:
            prev_trend: 前日 SQLite 趋势记录（用于连续性约束）
            ranked_indicators: 预计算的截面百分位排名指标（Node 2 v2）
        """
        klines = sector_info.get("klines", [])
        df = self._klines_to_df(klines)
        if df is None or len(df) < 15:
            return None

        # 1) 强度评分（优先用预计算的百分位排名，否则回退到旧方法）
        if ranked_indicators is not None:
            indicators = ranked_indicators
        else:
            # 降级：单板块独立评分（无截面排名，精度低）
            raw = self._calculate_raw_metrics(df, sector_info)
            indicators = {k: clip(v, 0.0, 1.0) for k, v in raw.items()}
        strength_score = self._calc_strength_score(indicators)

        # 2) 启动信号
        launch = self._detect_launch_signal(df, sector_info)

        # 3) 过热信号
        overheat = self._detect_overheat_signal(df, sector_info)

        # 4) 状态判定（含连续性约束）
        state = self._determine_state(
            strength_score, indicators, launch, overheat,
            sector_info, market_context, df,
            prev_trend=prev_trend,
        )

        # 5) 主线分级（仅 active/exhausted）
        tier: Optional[str] = None
        if state in ("active_mainline", "exhausted_mainline"):
            tier = self._classify_mainline_tier(sector_info, indicators)

        # 6) 生命周期（仅 active/exhausted，继承前日天数）
        lifecycle: Optional[LifecycleInfo] = None
        if state in ("active_mainline", "exhausted_mainline"):
            prev_lifecycle = prev_trend.get("lifecycle_phase") if prev_trend else None
            prev_days = prev_trend.get("mainline_days") if prev_trend else None
            lifecycle = self._classify_lifecycle(
                df, tier, overheat, launch,
                prev_lifecycle=prev_lifecycle,
                prev_days=prev_days,
            )

        # 7) 推荐操作
        action: Optional[str] = None
        if launch.get("triggered"):
            action = _LIFECYCLE_ACTION_MAP.get(
                lifecycle.phase if lifecycle else "mature",
            )
        elif state == "exhausted_mainline":
            action = "exit"

        # 8) 主线质量：B级标记为 weak
        mainline_quality: Optional[str] = None
        if state in ("active_mainline", "exhausted_mainline"):
            mainline_quality = "weak" if tier == "B" else "strong"

        return SectorDetail(
            code=sector_info["code"],
            name=sector_info["name"],
            state=state,
            strength_score=round(strength_score, 1),
            indicators=indicators,
            launch_signal=LaunchSignal(**launch) if launch.get("triggered") else None,
            lifecycle=lifecycle,
            tier=tier,
            mainline_quality=mainline_quality,
            recommended_action=action,
            today_change=sector_info.get("today_change"),
            frame_group=sector_info.get("frame_group"),
        )

    # =================================================================
    # 5 维强度评分
    # =================================================================

    @staticmethod
    def _klines_to_df(klines: list[dict]) -> Optional[pd.DataFrame]:
        """K 线列表 → DataFrame。"""
        try:
            df = pd.DataFrame(klines)
            for col in ("open", "close", "high", "low", "volume", "amount"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna(subset=["close", "volume"])
        except Exception as e:
            logger.debug(f"K线转 DataFrame 失败: {e}")
            return None

    def _calculate_raw_metrics(
        self,
        df: pd.DataFrame,
        sector_info: dict,
    ) -> dict[str, float]:
        """计算 4 维原始指标（未归一化，用于截面排名）。

        Node 2 v2：替代旧的 _calculate_strength（5维 clip 归一化）。
        原始值后续由 _apply_cross_sectional_ranking() 转为百分位排名。
        """
        close = df["close"]
        amount = df["amount"] if "amount" in df.columns else None

        # --- 1. 价格动量 (price_momentum) — 5 日涨幅 ---
        if len(close) >= 5:
            pm_raw = close.iloc[-1] / close.iloc[-5] - 1
        else:
            pm_raw = 0.0

        # --- 2. 量能扩张 (turnover_expansion) — 5d 均值 / 20d 均值 ---
        if amount is not None and len(amount) >= 20:
            avg_5d = amount.iloc[-5:].mean()
            avg_20d = amount.iloc[-20:].mean()
            te_raw = avg_5d / avg_20d if avg_20d > 0 else 1.0
        elif amount is not None and len(amount) >= 5:
            avg_5d = amount.iloc[-5:].mean()
            avg_all = amount.mean()
            te_raw = avg_5d / avg_all if avg_all > 0 else 1.0
        else:
            te_raw = 1.0

        # --- 3. 相对强度 (relative_strength) — 20 日涨幅 ---
        if len(close) >= 20:
            rs_raw = close.iloc[-1] / close.iloc[-20] - 1
        else:
            rs_raw = 0.0

        # --- 4. 宽度健康度 (breadth_health) — 上涨家数占比 ---
        up_count = sector_info.get("up_count", 0)
        down_count = sector_info.get("down_count", 0)
        total = up_count + down_count
        bh_raw = up_count / total if total > 0 else 0.5

        return {
            "price_momentum": pm_raw,
            "turnover_expansion": te_raw,
            "relative_strength": rs_raw,
            "breadth_health": bh_raw,
        }

    @staticmethod
    def _apply_cross_sectional_ranking(
        all_raw: list[dict[str, float]],
    ) -> list[dict[str, float]]:
        """将原始指标转为截面百分位排名（0-1）。

        每个维度独立排名：值越大 → 百分位越高。
        优势：牛市/熊市都能区分强中弱，不受量纲影响。
        """
        from src.got.indicators import cross_sectional_rank

        if not all_raw:
            return []

        dimensions = ["price_momentum", "turnover_expansion", "relative_strength", "breadth_health"]
        n = len(all_raw)

        # 按维度提取原始值
        dim_values: dict[str, dict[int, float]] = {dim: {} for dim in dimensions}
        for i, raw in enumerate(all_raw):
            for dim in dimensions:
                dim_values[dim][i] = raw.get(dim, 0.0)

        # 截面排名
        dim_ranks: dict[str, dict[int, float]] = {}
        for dim in dimensions:
            dim_ranks[dim] = cross_sectional_rank(dim_values[dim])

        # 重组为 per-board 百分位指标
        ranked: list[dict[str, float]] = []
        for i in range(n):
            ranked.append({
                dim: round(dim_ranks[dim].get(i, 0.5), 3)
                for dim in dimensions
            })
        return ranked

    @staticmethod
    def _calc_volume_price(df: pd.DataFrame, lookback: int = 5) -> float:
        """量价关系评分: [-1, 1]。"""
        if len(df) < lookback + 1:
            return 0.0

        price_change = df["close"].iloc[-1] - df["close"].iloc[-lookback]
        volume_avg = df["volume"].rolling(lookback).mean().iloc[-1]
        volume_cur = df["volume"].iloc[-1]
        vol_ratio = volume_cur / volume_avg if volume_avg > 0 else 1.0

        if price_change > 0:
            return 1.0 if vol_ratio > 1.2 else 0.3
        elif price_change < 0:
            return -1.0 if vol_ratio > 1.2 else -0.3
        return 0.0

    @staticmethod
    def _get_benchmark_gain(market_context: MarketContext) -> float:
        """从 MarketContext 获取大盘近 20 日涨幅近似值。

        注意：当前用 Node 1 的 tech_growth.score * 10% 近似，精度有限。
        tech_growth.score 是技术面综合评分（趋势/ADX/MACD），不是实际涨幅。
        如需更高精度，应在 MarketContext 中传递大盘实际 20 日涨幅数据。
        """
        score = market_context.tech_growth.score
        # score ∈ [-1, 1], 映射到 [-10%, 10%] 作为近似基准涨幅
        return score * 0.10

    @staticmethod
    def _estimate_capital_flow(df: pd.DataFrame) -> Optional[float]:
        """基于成交额变化估算资金流向（亿元）。"""
        if len(df) < 6:
            return None
        recent = df["amount"].iloc[-1]
        avg_amount = df["amount"].iloc[-6:-1].mean()
        if avg_amount <= 0:
            return None
        return (recent - avg_amount) / 1e8

    @staticmethod
    def _calc_strength_score(indicators: dict[str, float]) -> float:
        """4 维百分位加权 → 0-100 分。

        Node 2 v2：indicators 已经是 0-1 百分位排名，直接加权 × 100。
        """
        raw = sum(
            indicators.get(dim, 0.5) * w
            for dim, w in STRENGTH_WEIGHTS.items()
        )
        return raw * 100.0

    # =================================================================
    # 启动信号: 3 硬门槛 + 5 维加权
    # =================================================================

    def _detect_launch_signal(
        self, df: pd.DataFrame, sector_info: dict,
    ) -> dict[str, Any]:
        """检测板块启动信号。

        硬门槛（全部通过才触发）：
        1. 突破: close[-1] > max(high[-15:])
        2. 放量: volume[-1] > volume_ma3 * 1.3
        3. 站上 MA20: close[-1] > ma20
        """
        if len(df) < 20:
            return {"triggered": False, "level": "none", "score": 0.0, "dimensions": {}}

        close = df["close"]
        high = df["high"]
        volume = df["volume"]

        # --- 3 硬门槛 ---
        # 1. 突破 15 日高点
        high_15d = high.iloc[-16:-1].max() if len(high) >= 16 else high.iloc[:-1].max()
        if close.iloc[-1] <= high_15d:
            return {"triggered": False, "level": "none", "score": 0.0, "dimensions": {}}

        # 2. 量能确认: 当日成交量 ≥ 3 日均量 × 1.3
        vol_ma3 = volume.iloc[-4:-1].mean() if len(volume) >= 4 else volume.iloc[:-1].mean()
        if vol_ma3 <= 0 or volume.iloc[-1] < vol_ma3 * 1.3:
            return {"triggered": False, "level": "none", "score": 0.0, "dimensions": {}}

        # 3. 站上 MA20
        ma20 = close.rolling(20).mean().iloc[-1]
        if close.iloc[-1] <= ma20:
            return {"triggered": False, "level": "none", "score": 0.0, "dimensions": {}}

        # --- 5 维软打分（各 0-1）---
        dims = {
            "consolidation": self._calc_consolidation_score(df),
            "volume_expansion": self._calc_volume_expansion_score(volume, vol_ma3),
            "price_strength": self._calc_price_strength_score(close, high, df["low"]),
            "trend_alignment": self._calc_trend_alignment_score(close),
            "breadth": self._calc_breadth_score(sector_info),
        }

        # 加权总分
        weights = {
            "consolidation": 0.25,
            "volume_expansion": 0.25,
            "price_strength": 0.20,
            "trend_alignment": 0.15,
            "breadth": 0.15,
        }
        score = sum(dims[k] * weights[k] for k in weights)

        if score >= 0.75:
            level = "strong_launch"
        elif score >= 0.55:
            level = "weak_launch"
        else:
            level = "none"

        return {
            "triggered": level != "none",
            "level": level,
            "score": round(score, 3),
            "dimensions": {k: round(v, 3) for k, v in dims.items()},
        }

    @staticmethod
    def _calc_consolidation_score(df: pd.DataFrame) -> float:
        """整理充分度: 15 日区间紧凑度 + 布林收口程度。"""
        if len(df) < 20:
            return 0.3

        close = df["close"]
        high = df["high"]
        low = df["low"]

        # 15 日振幅 < 8% → 满分, > 15% → 0 分
        high_15 = high.iloc[-15:].max()
        low_15 = low.iloc[-15:].min()
        mid_15 = close.iloc[-15:].mean()
        amplitude = (high_15 - low_15) / mid_15 if mid_15 > 0 else 0
        range_score = clip((0.15 - amplitude) / (0.15 - 0.08), 0, 1)

        # 布林宽度百分位（越窄越好）
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        if ma20.iloc[-1] > 0 and std20.iloc[-1] > 0:
            bandwidth = 2 * std20.iloc[-1] / ma20.iloc[-1]
            # 对比历史带宽
            bw_series = 2 * std20.dropna() / ma20.dropna()
            if len(bw_series) > 10:
                boll_pct = 1.0 - clip(bandwidth / bw_series.quantile(0.8), 0, 1)
            else:
                boll_pct = 0.5
        else:
            boll_pct = 0.5

        return clip(0.6 * range_score + 0.4 * boll_pct, 0, 1)

    @staticmethod
    def _calc_volume_expansion_score(volume: pd.Series, vol_ma3: float) -> float:
        """量能扩张度: 量比越大越好, 但天量(>5x)扣分。"""
        if vol_ma3 <= 0:
            return 0.0
        vol_ratio = volume.iloc[-1] / vol_ma3

        if vol_ratio < 1.5:
            return 0.0
        elif vol_ratio <= 3.0:
            # 1.5 → 0, 3.0 → 1.0, 线性插值
            return (vol_ratio - 1.5) / 1.5
        elif vol_ratio <= 5.0:
            return 1.0
        else:
            # 天量扣分: 5.0 → 1.0, 8.0+ → 0.3
            return clip(1.0 - (vol_ratio - 5.0) / 3.0, 0.3, 1.0)

    @staticmethod
    def _calc_price_strength_score(
        close: pd.Series, high: pd.Series, low: pd.Series,
    ) -> float:
        """价格强度: 当前价在 20 日区间中的位置。"""
        if len(close) < 20:
            return 0.5

        high_20 = high.iloc[-20:].max()
        low_20 = low.iloc[-20:].min()
        price_range = high_20 - low_20
        if price_range <= 0:
            return 0.5

        return clip((close.iloc[-1] - low_20) / price_range, 0, 1)

    @staticmethod
    def _calc_trend_alignment_score(close: pd.Series) -> float:
        """均线排列度: MA5/10/20 多头排列程度。

        修复：旧逻辑只检查 MA5>MA10>MA20 是否满足（0/0.5/1.0 三档），
        不看差距大小。改为计算均线间距的归一化得分，粒度更细。
        """
        if len(close) < 20:
            return 0.5

        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]

        if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
            return 0.5

        # 计算两组间距的归一化得分
        # gap1: (MA5 - MA10) / MA10, 正值=多头, 负值=空头
        # gap2: (MA10 - MA20) / MA20
        gap1 = (ma5 - ma10) / ma10 if ma10 > 0 else 0
        gap2 = (ma10 - ma20) / ma20 if ma20 > 0 else 0

        # 每组间距映射到 [0, 1]：-2% → 0, 0% → 0.5, +2% → 1.0
        def gap_score(gap: float) -> float:
            return clip((gap + 0.02) / 0.04, 0, 1)

        return (gap_score(gap1) + gap_score(gap2)) / 2

    @staticmethod
    def _calc_breadth_score(sector_info: dict) -> float:
        """宽度: 板块内上涨比例, 0.4→0, 0.7→满分。"""
        up = sector_info.get("up_count", 0)
        down = sector_info.get("down_count", 0)
        total = up + down
        if total == 0:
            return 0.3
        ratio = up / total
        return clip((ratio - 0.4) / 0.3, 0, 1)

    # =================================================================
    # 过热信号: 4 条件任一触发
    # =================================================================

    def _detect_overheat_signal(
        self, df: pd.DataFrame, sector_info: dict,
    ) -> dict[str, bool]:
        """检测板块过热信号（任一触发即过热）。

        所有过热信号均需板块处于近期高位区间（距 60d 高点 ≤10%），
        防止下跌途中的反弹日误触发过热判定。

        信号:
        - strong_divergence: 价格 20d 新高但 MACD DIF 低于前高 80%
        - volume_divergence: 价格新高但量能不足前高 70%
        - breadth_extreme: 价格近高位 + >80% 成分股上涨
        - turnover_extreme: 价格近高位 + 换手率 > 60d 历史 90 分位
        """
        signals = {
            "strong_divergence": False,
            "volume_divergence": False,
            "breadth_extreme": False,
            "turnover_extreme": False,
        }

        if len(df) < 20:
            return signals

        close = df["close"]
        high = df["high"]
        volume = df["volume"]

        # 20d 价格新高判定
        price_at_20d_high = close.iloc[-1] >= close.iloc[-20:].max()

        # 60d 高位判定（过热信号的通用前置条件）
        # 板块距 60d 最高点 ≤10% 才视为"高位区间"
        lookback_60d = min(60, len(close))
        high_60d = close.iloc[-lookback_60d:].max()
        price_near_peak = close.iloc[-1] >= high_60d * 0.90

        # 1. 强背离: 价格新高 + MACD DIF 低于前高的 80%
        if price_at_20d_high and len(df) >= 30:
            macd_df = calculate_macd(df)
            dif = macd_df["MACD_DIF"]
            # 找 20d 内 DIF 最高值（排除最后一天）
            dif_20d_high = dif.iloc[-20:-1].max() if len(dif) >= 21 else dif.iloc[:-1].max()
            if dif_20d_high > 0 and dif.iloc[-1] < dif_20d_high * 0.8:
                signals["strong_divergence"] = True

        # 2. 量能背离: 价格新高 + 量能不足前高时 70%
        if price_at_20d_high and len(df) >= 20:
            price_high_idx = close.iloc[-20:].idxmax()
            vol_at_price_high = volume.loc[price_high_idx] if price_high_idx in volume.index else 0
            if vol_at_price_high > 0 and volume.iloc[-1] < vol_at_price_high * 0.7:
                signals["volume_divergence"] = True

        # 3. 宽度极端: 价格近高位 + >80% 成分股上涨
        #    仅在板块处于高位区间时，宽度极端才有过热含义
        up = sector_info.get("up_count", 0)
        down = sector_info.get("down_count", 0)
        total = up + down
        if price_near_peak and total > 0 and up / total > 0.8:
            signals["breadth_extreme"] = True

        # 4. 换手率极端: 价格近高位 + 成交额 > 60d 90 分位
        #    仅在板块处于高位区间时，放量才有过热含义
        turnover = sector_info.get("turnover_rate", 0.0)
        if price_near_peak and turnover > 0 and len(df) >= 30:
            # 用成交额/量近似换手率变化
            amount_series = df["amount"].iloc[-60:] if len(df) >= 60 else df["amount"]
            if len(amount_series) > 10:
                p90 = amount_series.quantile(0.9)
                if df["amount"].iloc[-1] > p90:
                    signals["turnover_extreme"] = True

        return signals

    # =================================================================
    # 状态判定（优先级链）
    # =================================================================

    def _determine_state(
        self,
        strength_score: float,
        indicators: dict[str, float],
        launch: dict,
        overheat: dict[str, bool],
        sector_info: dict,
        market_context: MarketContext,
        df: pd.DataFrame,
        prev_trend: Optional[dict] = None,
    ) -> str:
        """按优先级链判定板块状态。

        优先级: 强启动信号 > 过热信号 > 持续强势 > 高位派发 > 埋伏期 > 弱启动信号 > 防守 > 中性

        连续性约束：
        - 前日 active_mainline → 今天 score>60 且无过热 → 保持 active_mainline（防跳变）
        - 前日 exhausted_mainline → 今天有强启动信号才翻回 active
        """
        prev_state = prev_trend.get("unified_signal") if prev_trend else None
        # 将 unified_signal 映射回 state
        _signal_to_state = {
            "bullish": "active_mainline",
            "bearish": "exhausted_mainline",
            "neutral": None,
        }
        prev_mainline_state = _signal_to_state.get(prev_state) if prev_state else None

        launch_level = launch.get("level", "none")

        # P0: 连续性保护 — 前日主线不因单日波动跳变
        if prev_mainline_state == "active_mainline":
            if strength_score > 60 and not any(overheat.values()):
                return "active_mainline"

        # P1: 强启动信号 → active_mainline
        if launch_level == "strong_launch":
            return "active_mainline"

        # P2: 过热信号触发 → exhausted_mainline（必须先曾是主线）
        if prev_mainline_state == "active_mainline" and any(overheat.values()):
            return "exhausted_mainline"

        # P3: 持续强势 (score>70, turnover_expansion>0.6, breadth_health>0.6)
        # Node 2 v2：使用百分位排名指标（0-1），0.6 ≈ 前 40%
        if (
            strength_score > 70
            and indicators.get("turnover_expansion", 0) > 0.6
            and indicators.get("breadth_health", 0) > 0.6
            and launch_level != "weak_launch"
        ):
            return "active_mainline"

        # P4: 高位派发 (20d 涨幅 >15% 但量能萎缩 → 主力出货信号，必须先曾是主线)
        close = df["close"]
        if len(close) >= 20:
            gain_20d = close.iloc[-1] / close.iloc[-20] - 1
            if prev_mainline_state == "active_mainline" and gain_20d > 0.15 and indicators.get("turnover_expansion", 0.5) < 0.3:
                return "exhausted_mainline"

        # P5: 埋伏期（技术形态）
        if self._check_ambush(df, indicators):
            return "ambush"

        # P5b: 分数动量埋伏（连续上涨 + 强度达标）
        if self._check_momentum_ambush(strength_score, prev_trend):
            return "ambush"

        # P5.5: 弱启动信号但不满足埋伏条件 → neutral（而非主线）
        # 弱启动 = 整理不充分或量能不足，即使强度高也不应算主线
        if launch_level == "weak_launch":
            return "neutral"

        # P6: 防守轮动
        if self._check_defensive(sector_info, market_context, df, strength_score):
            return "defensive_rotation"

        return "neutral"

    @staticmethod
    def _check_ambush(df: pd.DataFrame, indicators: dict[str, float]) -> bool:
        """埋伏期条件（需满足 ≥2/4，且量能条件 ② 必须满足）。

        条件：
        1. 20 日波动率 < 35%（年化）— 科技板块普遍 35-50%，旧 25% 过严
        2. 最新成交量 > 1.3× MA20 量 — 量能必要条件，不满足直接排除
        3. 布林宽度 < 历史 40 分位
        4. 试盘模式：放量(vp>0.7) + 日内窄幅震荡(<2%) + 宽度不极端
        """
        if len(df) < 20:
            return False

        close = df["close"]
        volume = df["volume"]

        # 条件 2（必要前提）：量能温和放大 — 无量则直接排除
        vol_ma20 = volume.rolling(20).mean().iloc[-1] if len(volume) >= 20 else volume.mean()
        cond_volume = vol_ma20 > 0 and volume.iloc[-1] > vol_ma20 * 1.3
        if not cond_volume:
            return False

        # 条件 1: 20d 波动率 < 35%（年化）
        returns = close.pct_change().dropna()
        vol_20d = returns.tail(20).std() * np.sqrt(252) if len(returns) >= 20 else 1.0
        cond_volatility = vol_20d < 0.35

        # 条件 3: 布林收口（宽度低于历史 40% 分位）
        cond_boll = False
        if len(close) >= 60:
            ma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            bw_current = 2 * std20.iloc[-1] / ma20.iloc[-1] if ma20.iloc[-1] > 0 else 0
            bw_series = (2 * std20 / ma20).dropna()
            if len(bw_series) > 20 and not pd.isna(bw_current):
                cond_boll = bw_current < bw_series.quantile(0.4)

        # 条件 4: 试盘模式 — 量能温和放大 + 日内窄幅 + 宽度居中
        cond_probe = False
        last_row = df.iloc[-1]
        te = indicators.get("turnover_expansion", 0.5)
        intraday_range = (last_row["high"] - last_row["low"]) / last_row["close"] if last_row["close"] > 0 else 1.0
        bh = indicators.get("breadth_health", 0.5)
        cond_probe = te > 0.7 and intraday_range < 0.02 and 0.3 < bh < 0.7

        return sum([cond_volatility, cond_volume, cond_boll, cond_probe]) >= 2

    @staticmethod
    def _check_momentum_ambush(strength_score: float, prev_trend: Optional[dict]) -> bool:
        """分数动量埋伏（连续上涨 + 强度达标）。

        条件（满足任一）：
        A. 连续 3 天上升（trajectory 2 个点 + 当前）
        B. 单日大幅上涨 > 8 分（快速启动信号）
        """
        if not prev_trend:
            return False

        trajectory = prev_trend.get("strength_trajectory", [])
        if not trajectory:
            return False

        prev_score = trajectory[-1]
        ambush_days = prev_trend.get("ambush_days", 0)

        # 埋伏连续性加分：历史埋伏天数 ≥ 2 → 降低准入门槛
        # 普通板块：score > 60 才进入动量埋伏
        # 老埋伏板块：score > 50 即可（已在框内持续追踪）
        score_threshold = 50 if ambush_days >= 2 else 60

        if strength_score <= score_threshold:
            return False

        # 条件 A: 连续上升（trajectory 至少 2 个点）
        if len(trajectory) >= 2:
            recent = trajectory[-2:]
            recent.append(strength_score)
            increases = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i-1])
            if increases >= 2:
                return True

        # 条件 B: 单日大幅上涨 > 8 分
        if strength_score - prev_score > 8:
            return True

        return False

    @staticmethod
    def _check_defensive(
        sector_info: dict,
        market_context: MarketContext,
        df: pd.DataFrame,
        strength_score: float = 0.0,
    ) -> bool:
        """防守轮动条件（大盘走弱时，资金流入相对抗跌的板块）。

        触发条件（满足任一即视为弱势市场）：
        - 短期/中期信号 bearish
        - 大盘单日跌幅 > 3%（硬性暴跌触发）
        - 整体趋势 bearish

        板块筛选条件：
        - 20d 涨幅跑赢大盘（相对强度）
        - 距 60d 高点 ≤20%（排除深度崩盘板块）
        - strength_score ≤ 70（高分板块是进攻型，不属于防守轮动）
        """
        # 高分板块（>70）属于进攻型，不可能是防守轮动
        if strength_score > 70:
            return False

        if len(df) < 20:
            return False

        # ── 弱势市场判定（三选一即触发） ──
        weak_market = False

        # 条件 1a: 短期或中期 bearish
        signals = market_context.tech_growth.signals
        if signals.short_term.trend == "bearish" or signals.medium_term.trend == "bearish":
            weak_market = True

        # 条件 1b: 整体趋势 bearish
        if market_context.tech_growth.trend == "bearish":
            weak_market = True

        # 条件 1c: 大盘单日暴跌 > 3%
        today = market_context.today_change
        if today:
            worst_drop = min(today.values())
            if worst_drop < -3.0:
                weak_market = True

        if not weak_market:
            return False

        close = df["close"]

        # 条件 2: 板块 20d 涨幅 > 大盘同期涨幅
        sector_gain_20d = close.iloc[-1] / close.iloc[-20] - 1
        benchmark_gain = market_context.tech_growth.score * 0.10  # score ∈ [-1,1] → [-10%,10%]
        if sector_gain_20d <= benchmark_gain:
            return False

        # 条件 3: 价格位置保护 — 排除深度崩盘板块
        lookback_60d = min(60, len(close))
        high_60d = close.iloc[-lookback_60d:].max()
        if close.iloc[-1] < high_60d * 0.80:
            return False

        return True

    # =================================================================
    # 主线分级 S/A/B
    # =================================================================

    def _classify_mainline_tier(
        self, sector_info: dict, indicators: dict[str, float],
    ) -> str:
        """S/A/B 主线分级评分（Node 2 v2：使用百分位指标）。

        | 标准                     | 分值 |
        |--------------------------|------|
        | 国家级政策支持            | +3   |
        | 家族成员数 ≥3             | +2   |
        | 量能扩张（turnover>0.6）  | +1   |
        | 连续强势（pm>0.6+rs>0.6） | +1   |

        S ≥ 5, A ≥ 3, B < 3
        """
        score = 0
        frame_group = sector_info.get("frame_group", "")

        # 国家级政策支持
        if frame_group in _POLICY_SUPPORTED_CHAINS:
            score += 3

        # 产业链规模（家族成员数 ≥3 → 说明不是单一概念）
        family_members = sum(
            1 for kw_list in FAMILY_KEYWORDS
            for _ in [0]  # placeholder
            if any(kw in sector_info.get("name", "") for kw in kw_list[1])
        )
        # 简化：如果家族名不是"其他"且有多个关键词可匹配 → 视为有产业链深度
        if frame_group not in ("其他", ""):
            score += 1

        # 量能扩张（turnover_expansion 百分位 > 0.6 = 前 40% 放量）
        te = indicators.get("turnover_expansion", 0)
        if te > 0.6:
            score += 2
        elif te > 0.4:
            score += 1

        # 连续强势（百分位排名双高）
        pm = indicators.get("price_momentum", 0)
        rs = indicators.get("relative_strength", 0)
        if pm > 0.6 and rs > 0.6:
            score += 1

        if score >= 5:
            return "S"
        elif score >= 3:
            return "A"
        return "B"

    # =================================================================
    # 生命周期
    # =================================================================

    def _classify_lifecycle(
        self,
        df: pd.DataFrame,
        tier: Optional[str],
        overheat: dict[str, bool],
        launch: dict,
        prev_lifecycle: Optional[str] = None,
        prev_days: Optional[int] = None,
    ) -> LifecycleInfo:
        """按主线等级映射生命周期阈值，判断当前阶段。

        连续性：如果前日有 lifecycle 且 prev_days > 0，继承 days_since_launch = prev_days + 1，
        而非从 K 线重算（避免窗口滑动导致的天数跳变）。
        """
        effective_tier = tier or "B"
        launch_end, accel_end, mature_end = LIFECYCLE_THRESHOLDS[effective_tier]
        thresholds = {
            "launch_end": launch_end,
            "acceleration_end": accel_end,
            "mature_end": mature_end,
        }

        # 过热 → mature_exhausted
        if any(overheat.values()):
            days = self._inherit_or_calc_days(df, prev_lifecycle, prev_days)
            return LifecycleInfo(
                phase="mature_exhausted",
                days_since_launch=days,
                thresholds=thresholds,
            )

        # 继承前日天数（连续性核心）
        days = self._inherit_or_calc_days(df, prev_lifecycle, prev_days)

        if days <= launch_end:
            phase = "launch"
        elif days <= accel_end:
            phase = "acceleration"
        elif days <= mature_end:
            phase = "mature"
        else:
            phase = "late_stage"

        return LifecycleInfo(
            phase=phase,
            days_since_launch=days,
            thresholds=thresholds,
        )

    @staticmethod
    def _inherit_or_calc_days(
        df: pd.DataFrame,
        prev_lifecycle: Optional[str],
        prev_days: Optional[int],
    ) -> int:
        """优先继承前日天数，无前日记录才从 K 线计算。

        连续性逻辑：前日是 launch/acceleration/mature → 今天 +1。
        前日是 late_stage/mature_exhausted 或无记录 → 从 K 线重算。
        """
        _inheriting_phases = {"launch", "acceleration", "mature"}
        if prev_lifecycle in _inheriting_phases and prev_days is not None and prev_days > 0:
            return prev_days + 1
        return SectorAnalysisNode._calc_days_since_launch(df)

    @staticmethod
    def _calc_days_since_launch(df: pd.DataFrame) -> int:
        """从 K 线数据估算启动以来天数。

        寻找近期最低收盘价作为启动起点，但必须验证从低点涨幅超过
        _LAUNCH_CONFIRM_THRESHOLD（5%）才算真正启动。

        修复：旧逻辑只算"从低点到今天的天数"，不验证是否真正涨起来。
        阴跌 20 天 + 反弹 3 天会被误判为 late_stage（23天 > mature_end=20）。
        """
        if len(df) < 5:
            return 0
        close = df["close"]
        tail = close.tail(60) if len(close) >= 60 else close
        min_idx = tail.idxmin()
        min_val = close.loc[min_idx]
        min_pos = df.index.get_loc(min_idx)

        # 从低点往后找第一个涨幅超过门槛的日期（启动确认日）
        launch_threshold = min_val * (1 + _LAUNCH_CONFIRM_THRESHOLD)
        after_low = close.iloc[min_pos:]
        breakout_mask = after_low >= launch_threshold
        if not breakout_mask.any():
            # 从未真正涨起来过，不算启动
            return 0
        breakout_idx = breakout_mask.idxmax()  # 第一次超过门槛的日期
        breakout_pos = df.index.get_loc(breakout_idx)
        return len(df) - 1 - breakout_pos

    # =================================================================
    # 分类汇总
    # =================================================================

    def _classify_sectors(
        self,
        sectors: list[SectorDetail],
        market_context: MarketContext,
    ) -> SectorContext:
        """按状态分类 + 限额排序。

        兜底机制：若某分类为空，用评分最高的未分配板块补入。
        但在 notes 中明确标注"兜底填充"，让 Node 6 知道这些不是真正达标的主线。
        """
        notes: list[str] = []
        used_codes: set[str] = set()

        # 按状态分类（优先级：状态匹配 > 评分最高）
        def pick_by_state(state: str, limit: int) -> list[SectorDetail]:
            matched = sorted(
                [s for s in sectors if s.state == state and s.code not in used_codes],
                key=lambda s: s.strength_score, reverse=True,
            )[:limit]
            for s in matched:
                used_codes.add(s.code)
            return matched

        active = pick_by_state("active_mainline", 3)
        exhausted = pick_by_state("exhausted_mainline", 3)
        ambush = pick_by_state("ambush", 5)
        defensive = pick_by_state("defensive_rotation", 2)

        # 兜底：空列用评分最高的未分配板块填充（但标注为兜底）
        # 修复：主线（active/exhausted）不再兜底填充，无达标板块就留空
        def fill_empty(target: list, limit: int, label: str):
            remaining = len(target)
            if remaining >= limit:
                return
            # 排除已分入主线/衰竭的板块，防止 active_mainline 板块被重复拉入埋伏
            _excluded_states = {"active_mainline", "exhausted_mainline"}
            candidates = sorted(
                [s for s in sectors if s.code not in used_codes and s.state not in _excluded_states],
                key=lambda s: s.strength_score, reverse=True,
            )
            for s in candidates[:limit - remaining]:
                target.append(s)
                used_codes.add(s.code)
                notes.append(
                    f"⚠️ {label}: 无板块满足标准，**兜底填充** {s.name}（{s.strength_score:.0f}分, {s.state}）— 非真正达标"
                )

        # 主线不兜底填充，无达标板块就留空
        # fill_empty(active, 2, "活跃主线")
        # fill_empty(exhausted, 2, "衰竭主线")
        fill_empty(ambush, 4, "埋伏板块")
        fill_empty(defensive, 1, "防守轮动")

        # 评分 top N 供参考（与分类列表分离）
        top_by_score = sorted(
            [s for s in sectors if s.code not in used_codes],
            key=lambda s: s.strength_score, reverse=True,
        )[:5]

        direction = self._classify_market_direction(sectors)

        return SectorContext(
            active_mainline=[],  # 由 _integrate_frames 填充
            exhausted_mainline=exhausted,
            ambush=ambush,
            defensive_rotation=defensive,
            top_by_score=top_by_score,
            market_direction=direction,
            notes=notes,
        )

    @staticmethod
    def _classify_market_direction(sectors: list[SectorDetail]) -> str:
        """市场方向: 普涨(>60%上涨) / 普跌(>60%下跌) / 分化。"""
        up = sum(1 for s in sectors if s.indicators.get("price_momentum", 0) > 0.2)
        down = sum(1 for s in sectors if s.indicators.get("price_momentum", 0) < -0.2)
        total = len(sectors)

        if total == 0:
            return "分化"

        if up / total > 0.6:
            return "普涨"
        elif down / total > 0.6:
            return "普跌"
        return "分化"

    # =================================================================
    # 框系统：主线框 + 埋伏框（持久化进出管理）
    # =================================================================

    # 主线框常量
    _MAINLINE_EXIT_SCORE_THRESHOLD = 50
    _MAINLINE_STRONG_TOLERANCE_DAYS = 3  # 强组（含 S/A 成员）容忍天数
    _MAINLINE_WEAK_TOLERANCE_DAYS = 1    # 弱组（全 B 成员）零容忍
    _MAINLINE_LATE_STAGE_SCORE_THRESHOLD = 60

    # 埋伏框常量
    _AMBUSH_EXIT_SCORE_THRESHOLD = 50
    _AMBUSH_EXIT_CONSECUTIVE_DECLINE_DAYS = 5
    _AMBUSH_PROMOTE_CONSECUTIVE_RISE_DAYS = 3
    _AMBUSH_PROMOTE_SCORE_THRESHOLD = 70

    def _load_frames(self) -> dict[str, dict]:
        """从 SQLite 加载所有活跃框记录。

        主线框 key = frame_group（产业主题名）
        埋伏框 key = sector_code（个股级）

        Returns:
            {key: {frame_type, frame_group, entry_date, member_codes,
                   group_score, has_strong_member, consecutive_low_days,
                   consecutive_rise_days}}
        """
        result: dict[str, dict] = {}
        try:
            db = DatabaseManager()
            with db.get_session() as session:
                records = session.query(SectorFrameRecord).filter(
                    SectorFrameRecord.is_active == True,
                ).all()
                for r in records:
                    key = r.frame_group
                    result[key] = {
                        "frame_type": r.frame_type,
                        "frame_group": r.frame_group,
                        "entry_date": r.entry_date,
                        "member_codes": r.get_member_codes(),
                        "group_score": r.group_score or 0,
                        "has_strong_member": r.has_strong_member or False,
                        "consecutive_low_days": r.consecutive_low_days or 0,
                        "consecutive_rise_days": r.consecutive_rise_days or 0,
                    }
            if result:
                logger.info(f"[Node 2] 加载框: {len(result)} 个活跃组")
        except Exception as e:
            logger.warning(f"[Node 2] 加载框失败（首次运行？）: {e}")
        return result

    def _manage_frames(
        self,
        frames: dict[str, dict],
        analyzed: list[SectorDetail],
    ) -> list[SectorDetail]:
        """框进出管理（主线框按产业主题组，埋伏框按个股）。

        Returns:
            exhausted_from_frame: 今日被踢出主线框的板块（所有成员）
        """
        today = date.today()
        exhausted_from_frame: list[SectorDetail] = []
        analyzed_by_code: dict[str, SectorDetail] = {s.code: s for s in analyzed}

        # ── 第一步：按 frame_group 聚合今日主线候选 ──
        mainline_by_group: dict[str, list[SectorDetail]] = {}
        for s in analyzed:
            if s.state == "active_mainline" and s.frame_group:
                mainline_by_group.setdefault(s.frame_group, []).append(s)

        # ── 第二步：处理已在主线框内的组 ──
        groups_to_remove: list[str] = []

        for group_name, frame in list(frames.items()):
            if frame["frame_type"] != "mainline":
                continue

            # 用 frame 持久化的 member_codes 查找今日仍存在的成员（而非仅 active_mainline 状态）
            # 避免成员状态降级导致 group_score 与输出成员不一致
            frame_members = [
                analyzed_by_code[c]
                for c in frame.get("member_codes", [])
                if c in analyzed_by_code
            ]
            today_active = mainline_by_group.get(group_name, [])

            if not frame_members and not today_active:
                # 该组今日无任何成员 → 低分计数
                frame["group_score"] = 0
                frame["consecutive_low_days"] += 1
            else:
                # 计算 group_score：优先用 frame 成员的实际分数，兜底用 active 成员
                score_source = frame_members or today_active
                group_score = max(s.strength_score for s in score_source)
                has_strong = any(
                    s.tier in ("S", "A") for s in score_source
                )
                frame["group_score"] = group_score
                frame["has_strong_member"] = has_strong
                frame["member_codes"] = [s.code for s in score_source]

                # 检查任何成员是否过热/衰竭
                any_overheat = any(
                    s.state == "exhausted_mainline" for s in score_source
                )
                if any_overheat:
                    exhausted_from_frame.extend(score_source)
                    groups_to_remove.append(group_name)
                    logger.info(f"[Node 2] 主线框踢出 [{group_name}]: 成员过热")
                    continue

                # 低分检查（强组容忍 3 天，弱组 1 天）
                tolerance = (
                    self._MAINLINE_STRONG_TOLERANCE_DAYS
                    if has_strong
                    else self._MAINLINE_WEAK_TOLERANCE_DAYS
                )
                if group_score < self._MAINLINE_EXIT_SCORE_THRESHOLD:
                    frame["consecutive_low_days"] += 1
                    if frame["consecutive_low_days"] >= tolerance:
                        exhausted_from_frame.extend(score_source)
                        groups_to_remove.append(group_name)
                        logger.info(
                            f"[Node 2] 主线框踢出 [{group_name}]: "
                            f"连续{frame['consecutive_low_days']}天score<{self._MAINLINE_EXIT_SCORE_THRESHOLD}"
                        )
                        continue
                else:
                    frame["consecutive_low_days"] = 0

            frame["last_update"] = today

        for g in groups_to_remove:
            frames.pop(g, None)

        # ── 第三步：新主线组入框（含分数门槛）──
        for group_name, members in mainline_by_group.items():
            if group_name in frames:
                continue
            has_strong = any(s.tier in ("S", "A") for s in members)
            group_score = max(s.strength_score for s in members)
            # 弱组（全 B 级）门槛更高：≥80 才入主线框，否则落入埋伏
            min_score = self._MAINLINE_EXIT_SCORE_THRESHOLD if has_strong else 80
            if group_score < min_score:
                logger.info(
                    f"[Node 2] [{group_name}] score={group_score:.1f} "
                    f"< 入框门槛{min_score}，不入主线框"
                )
                continue
            frames[group_name] = {
                "frame_type": "mainline",
                "frame_group": group_name,
                "entry_date": today,
                "last_update": today,
                "member_codes": [s.code for s in members],
                "group_score": group_score,
                "has_strong_member": has_strong,
                "consecutive_low_days": 0,
                "consecutive_rise_days": 0,
            }
            logger.info(f"[Node 2] 主线框纳入 [{group_name}]: {[s.name for s in members]}")

        # ── 第四步：埋伏框（个股级，逻辑不变）──
        ambush_codes_to_remove: list[str] = []
        ambush_frame_keys = [k for k, v in frames.items() if v["frame_type"] == "ambush"]

        for key in ambush_frame_keys:
            frame = frames[key]
            member_codes = frame.get("member_codes", [])
            if not member_codes:
                continue
            # 埋伏框每组成员只有一个
            sector = analyzed_by_code.get(member_codes[0])
            if sector is None:
                continue

            te = sector.indicators.get("turnover_expansion", 0.5)
            prev_score = frame["group_score"]
            score = sector.strength_score

            # 晋升检查
            launch_level = sector.launch_signal.level if sector.launch_signal else "none"
            if launch_level == "strong_launch":
                # 晋升 → 转为主线框（以 frame_group 为 key）
                fg = sector.frame_group or sector.name
                frames.pop(key, None)
                if fg not in frames:
                    has_strong = sector.tier in ("S", "A")
                    frames[fg] = {
                        "frame_type": "mainline",
                        "frame_group": fg,
                        "entry_date": today,
                        "last_update": today,
                        "member_codes": [sector.code],
                        "group_score": score,
                        "has_strong_member": has_strong,
                        "consecutive_low_days": 0,
                        "consecutive_rise_days": 0,
                    }
                else:
                    if sector.code not in frames[fg]["member_codes"]:
                        frames[fg]["member_codes"].append(sector.code)
                    frames[fg]["group_score"] = max(frames[fg]["group_score"], score)
                logger.info(f"[Node 2] 埋伏→主线晋升 {sector.name}: strong_launch")
                continue

            if score > prev_score and prev_score > 0:
                frame["consecutive_rise_days"] += 1
            else:
                frame["consecutive_rise_days"] = 0

            if (
                frame["consecutive_rise_days"] >= self._AMBUSH_PROMOTE_CONSECUTIVE_RISE_DAYS
                and score > self._AMBUSH_PROMOTE_SCORE_THRESHOLD
            ):
                fg = sector.frame_group or sector.name
                frames.pop(key, None)
                if fg not in frames:
                    has_strong = sector.tier in ("S", "A")
                    frames[fg] = {
                        "frame_type": "mainline",
                        "frame_group": fg,
                        "entry_date": today,
                        "last_update": today,
                        "member_codes": [sector.code],
                        "group_score": score,
                        "has_strong_member": has_strong,
                        "consecutive_low_days": 0,
                        "consecutive_rise_days": 0,
                    }
                logger.info(f"[Node 2] 埋伏→主线晋升 {sector.name}: 连续动量")
                continue

            # 退出检查
            if score < self._AMBUSH_EXIT_SCORE_THRESHOLD:
                ambush_codes_to_remove.append(key)
                logger.info(f"[Node 2] 埋伏框踢出 {sector.name}: score<{self._AMBUSH_EXIT_SCORE_THRESHOLD}")
                continue
            if te < 0.3:
                ambush_codes_to_remove.append(key)
                logger.info(f"[Node 2] 埋伏框踢出 {sector.name}: 量能萎缩(te<0.3)")
                continue
            prev_traj = sector.strength_trajectory or []
            if len(prev_traj) >= self._AMBUSH_EXIT_CONSECUTIVE_DECLINE_DAYS:
                recent = prev_traj[-self._AMBUSH_EXIT_CONSECUTIVE_DECLINE_DAYS:]
                if all(recent[i] > recent[i+1] for i in range(len(recent)-1)):
                    ambush_codes_to_remove.append(key)
                    logger.info(f"[Node 2] 埋伏框踢出 {sector.name}: 连续{self._AMBUSH_EXIT_CONSECUTIVE_DECLINE_DAYS}天走弱")
                    continue

            frame["group_score"] = score
            frame["last_update"] = today

        for key in ambush_codes_to_remove:
            frames.pop(key, None)

        # ── 第五步：新埋伏入框 ──
        # 收集当前活跃的主线 frame_group，防止同主题板块误入埋伏
        active_mainline_groups = {
            group_name for group_name, f in frames.items()
            if f["frame_type"] == "mainline"
        }

        for sector in analyzed:
            if sector.state != "ambush":
                continue
            if sector.code in {
                c for f in frames.values() for c in f.get("member_codes", [])
            }:
                continue
            # 若该板块的 frame_group 已是主线组，不进入埋伏（避免银行/汽车这种大票误判）
            if sector.frame_group and sector.frame_group in active_mainline_groups:
                continue
            key = sector.code  # 埋伏框以 sector_code 为 key
            frames[key] = {
                "frame_type": "ambush",
                "frame_group": key,
                "entry_date": today,
                "last_update": today,
                "member_codes": [sector.code],
                "group_score": sector.strength_score,
                "has_strong_member": False,
                "consecutive_low_days": 0,
                "consecutive_rise_days": 0,
            }
            logger.info(f"[Node 2] 埋伏框纳入 {sector.name}")

        return exhausted_from_frame

    def _integrate_frames(
        self,
        raw_result: SectorContext,
        frames: dict[str, dict],
        exhausted_from_frame: list[SectorDetail],
        analyzed: list[SectorDetail],
    ) -> SectorContext:
        """整合框内容到最终输出。

        主线框 → list[MainlineGroup]（按产业主题聚合）
        埋伏框 → list[SectorDetail]（个股级）
        衰竭主线 = 从主线框踢出的板块
        """
        from src.got.schemas import MainlineGroup

        analyzed_by_code = {s.code: s for s in analyzed}
        used_codes: set[str] = set()

        # 主线框 → MainlineGroup 列表
        mainline_groups: list[MainlineGroup] = []
        for group_name, frame in frames.items():
            if frame["frame_type"] != "mainline":
                continue
            members = [
                analyzed_by_code[c]
                for c in frame.get("member_codes", [])
                if c in analyzed_by_code
            ]
            if not members:
                continue
            for m in members:
                used_codes.add(m.code)

            best_tier = "B"
            for m in members:
                if m.tier == "S":
                    best_tier = "S"
                    break
                elif m.tier == "A" and best_tier != "S":
                    best_tier = "A"

            mainline_groups.append(MainlineGroup(
                group_name=group_name,
                group_score=frame["group_score"],
                tier=best_tier,
                mainline_quality="strong" if frame.get("has_strong_member") else "weak",
                members=sorted(members, key=lambda s: s.strength_score, reverse=True),
            ))
        mainline_groups.sort(key=lambda g: g.group_score, reverse=True)

        # 埋伏框 → SectorDetail 列表
        ambush_from_frame: list[SectorDetail] = []
        for key, frame in frames.items():
            if frame["frame_type"] != "ambush":
                continue
            for code in frame.get("member_codes", []):
                sector = analyzed_by_code.get(code)
                if sector:
                    ambush_from_frame.append(sector)
                    used_codes.add(code)

        # 衰竭主线
        exhausted_mainline = list(exhausted_from_frame)
        used_codes.update(s.code for s in exhausted_from_frame)

        # defensive_rotation
        defensive = [
            s for s in raw_result.defensive_rotation if s.code not in used_codes
        ]
        for s in defensive:
            used_codes.add(s.code)

        # top_by_score
        top_by_score = sorted(
            [s for s in analyzed if s.code not in used_codes],
            key=lambda s: s.strength_score, reverse=True,
        )[:5]

        return SectorContext(
            active_mainline=mainline_groups,
            exhausted_mainline=exhausted_mainline,
            ambush=ambush_from_frame,
            defensive_rotation=defensive,
            top_by_score=top_by_score,
            market_direction=raw_result.market_direction,
            notes=[n for n in raw_result.notes if "埋伏板块" not in n],
            reasoning=raw_result.reasoning,
            data_quality=raw_result.data_quality,
        )

    def _save_frames(self, frames: dict[str, dict]) -> None:
        """持久化框状态到 SQLite。"""
        if not frames:
            return
        try:
            db = DatabaseManager()
            today = date.today()
            with db.get_session() as session:
                # 全量重置 is_active
                session.query(SectorFrameRecord).filter(
                    SectorFrameRecord.is_active == True,
                ).update({"is_active": False})

                for key, frame in frames.items():
                    existing = session.query(SectorFrameRecord).filter_by(
                        frame_group=key,
                    ).first()

                    if existing:
                        existing.frame_type = frame["frame_type"]
                        existing.last_update = today
                        existing.member_codes = json.dumps(
                            frame.get("member_codes", []), ensure_ascii=False,
                        )
                        existing.group_score = frame["group_score"]
                        existing.has_strong_member = frame.get("has_strong_member", False)
                        existing.consecutive_low_days = frame["consecutive_low_days"]
                        existing.consecutive_rise_days = frame["consecutive_rise_days"]
                        existing.is_active = True
                        existing.exit_reason = None
                    else:
                        record = SectorFrameRecord(
                            frame_group=key,
                            frame_type=frame["frame_type"],
                            entry_date=frame["entry_date"],
                            last_update=today,
                            member_codes=json.dumps(
                                frame.get("member_codes", []), ensure_ascii=False,
                            ),
                            group_score=frame["group_score"],
                            has_strong_member=frame.get("has_strong_member", False),
                            consecutive_low_days=frame["consecutive_low_days"],
                            consecutive_rise_days=frame["consecutive_rise_days"],
                            is_active=True,
                        )
                        session.add(record)

                session.commit()
            logger.info(f"[Node 2] 框持久化完成: {len(frames)} 个活跃组")
        except Exception as e:
            logger.error(f"[Node 2] 框持久化失败: {e}", exc_info=True)

    # =================================================================
    # 主线家族聚合（Node 2 v2）
    # =================================================================

    def _build_theme_families(
        self, analyzed: list[SectorDetail],
    ) -> list:
        """将板块按 FAMILY_KEYWORDS 归入主线家族，计算家族强度和题材地位。

        Returns: list[ThemeFamily]
        """
        from src.got.schemas import ThemeFamily

        # 按家族分组
        family_members: dict[str, list[SectorDetail]] = {name: [] for name, _ in FAMILY_KEYWORDS}
        family_members["其他"] = []

        for sector in analyzed:
            frame_group = sector.frame_group or _build_frame_group(sector.name)
            if frame_group in family_members:
                family_members[frame_group].append(sector)
            else:
                family_members["其他"].append(sector)

        # 计算家族评分和题材地位
        families = []
        for family_name, members in family_members.items():
            if not members:
                continue
            if family_name == "其他":
                continue  # "其他" 不参与主线家族输出

            family_score = sum(m.strength_score for m in members) / len(members)
            active_count = sum(
                1 for m in members
                if m.state in ("active_mainline", "exhausted_mainline")
            )
            family_resonance = active_count / len(members)

            # 题材地位判定
            if family_score > 70 and family_resonance > 0.5:
                theme_status = "主线核心"
            elif family_score > 60 and family_resonance > 0.3:
                theme_status = "主线扩散"
            elif family_score > 50:
                theme_status = "独立异动"
            else:
                # 检查动量变化（需要轨迹数据）
                has_rising = any(
                    m.trend_direction == "increasing" for m in members
                )
                if has_rising:
                    theme_status = "修复观察"
                else:
                    theme_status = "高位降温"

            families.append(ThemeFamily(
                family_name=family_name,
                family_score=round(family_score, 1),
                family_resonance=round(family_resonance, 2),
                theme_status=theme_status,
                member_boards=[m.name for m in members],
                member_count=len(members),
            ))

        families.sort(key=lambda f: f.family_score, reverse=True)
        return families

    # =================================================================
    # 行业板块双视角（Node 2 v2）
    # =================================================================

    async def _fetch_and_score_industry_boards(
        self, quality: DataQuality,
    ) -> list[SectorDetail]:
        """获取行业板块 K 线 → 4 维百分位评分 → Top 10。

        独立于概念板块评分，提供中期行业风格轮动视角。
        行业板块使用 mootdx 881xxx K 线数据。
        """
        # 获取行业板块列表
        industry_boards = await asyncio.to_thread(
            self.fetcher.get_all_industry_boards,
        )
        if not industry_boards:
            logger.warning("[Node 2] 行业板块列表不可用，跳过行业视角")
            quality.missing_fields.append("industry_board_list")
            return []

        quality.available_fields.append("industry_board_list")
        logger.info(f"[Node 2] 行业板块: {len(industry_boards)} 个")

        # 并发获取 K 线（限制并发数）
        semaphore = asyncio.Semaphore(_CONCURRENT_FETCHES)
        days = _KLINE_LOOKBACK_DAYS

        async def fetch_one(board: dict) -> Optional[dict]:
            async with semaphore:
                try:
                    kline_df = await asyncio.to_thread(
                        self.fetcher.get_board_kline,
                        board["code"], "industry", days,
                    )
                    if kline_df is None or len(kline_df) < 15:
                        return None
                    board["klines"] = self._df_to_klines(kline_df, board["name"])
                    # 提取涨跌家数（mootdx K 线可能包含）
                    if len(kline_df) > 0:
                        last_row = kline_df.iloc[-1]
                        if "up_count" in last_row:
                            board["up_count"] = int(last_row.get("up_count", 0))
                        if "down_count" in last_row:
                            board["down_count"] = int(last_row.get("down_count", 0))
                    return board
                except Exception:
                    return None

        results = await asyncio.gather(*(fetch_one(b) for b in industry_boards))
        valid = [r for r in results if r is not None]
        if not valid:
            logger.warning("[Node 2] 行业板块 K 线全部获取失败")
            return []

        logger.info(f"[Node 2] 行业 K 线: {len(valid)}/{len(industry_boards)} 成功")

        # Pass 1: 原始指标
        all_raw = [self._calculate_raw_metrics(
            self._klines_to_df(b["klines"]), b,
        ) for b in valid]

        # Pass 2: 截面百分位排名（行业板块内部独立排名）
        all_ranked = self._apply_cross_sectional_ranking(all_raw)

        # 构建 SectorDetail（取 Top 10）
        scored = []
        for i, board in enumerate(valid):
            indicators = all_ranked[i]
            score = self._calc_strength_score(indicators)
            scored.append((score, board, indicators))

        scored.sort(key=lambda x: x[0], reverse=True)

        top_industries = []
        for score, board, indicators in scored[:10]:
            detail = SectorDetail(
                code=board["code"],
                name=board["name"],
                state="neutral",  # 行业板块不做状态分类
                strength_score=round(score, 1),
                indicators=indicators,
                frame_group=board.get("frame_group", _build_frame_group(board["name"])),
            )
            top_industries.append(detail)

        return top_industries

    # =================================================================
    # 降级可见性（Node 2 v2）
    # =================================================================

    def _build_degradation_summary(self, quality: DataQuality) -> str:
        """生成人可读的降级摘要，注入 reasoning.md 和 LLM prompt。"""
        parts = []
        if quality.board_fetch_stats:
            stats = quality.board_fetch_stats
            failed = stats.get("failed", 0)
            total = stats.get("total", 0)
            if failed > 0:
                parts.append(f"⚠️ {failed}/{total} 个板块 K 线获取失败")

        if quality.effective_dimensions:
            inactive = [
                dim for dim, ok in quality.effective_dimensions.items() if not ok
            ]
            if inactive:
                parts.append(f"⚠️ 失效维度: {', '.join(inactive)}")

        if quality.fetch_failed_fields:
            sample = quality.fetch_failed_fields[:5]
            parts.append(f"⚠️ 失败板块: {', '.join(sample)}")

        if not parts:
            return "✅ 数据质量正常，所有维度有效"
        return "；".join(parts)

    # =================================================================
    # 板块趋势持久化（跨日记忆）
    # =================================================================

    def _load_all_sector_trends(self, sectors_raw: list[dict]) -> dict[str, dict]:
        """批量加载所有板块的前日趋势记录（分类前调用一次）。

        Returns:
            {sector_code: {strength_trajectory, trend_direction, mainline_days,
                           unified_signal, lifecycle_phase}}
        """
        result: dict[str, dict] = {}
        try:
            db = DatabaseManager()
            today = date.today()
            with db.get_session() as session:
                # 获取所有板块的最新记录（tracking_date < today）
                for sector_info in sectors_raw:
                    code = sector_info["code"]
                    history = session.query(Node2SectorTrendRecord).filter(
                        Node2SectorTrendRecord.sector_code == code,
                        Node2SectorTrendRecord.tracking_date < today,
                    ).order_by(
                        Node2SectorTrendRecord.tracking_date.desc()
                    ).limit(7).all()

                    if not history:
                        continue

                    latest = history[0]
                    trajectory = [h.strength_score for h in reversed(history)]
                    result[code] = {
                        "sector_code": code,
                        "sector_name": latest.sector_name,
                        "strength_trajectory": trajectory,
                        "trend_direction": latest.trend_direction,
                        "mainline_days": latest.mainline_days or 0,
                        "ambush_days": latest.ambush_days or 0,
                        "unified_signal": latest.unified_signal,
                        "lifecycle_phase": latest.lifecycle_phase,
                    }

            if result:
                logger.info("[Node 2] 加载 %d 个板块的前日趋势", len(result))
        except Exception as e:
            logger.warning("[Node 2] 批量加载板块趋势失败（首次运行？）: %s", e)

        return result

    def _save_sector_trends(self, sectors: list[SectorDetail]) -> None:
        """保存板块趋势到 SQLite，支持跨日追踪。

        追踪目标：
        - strength_score 日度变化（7 天轨迹）
        - 主线持续时间（mainline_days，用于检测 late_stage）
        """
        if not sectors:
            return

        try:
            db = DatabaseManager()
            today = date.today()

            with db.get_session() as session:
                for sector in sectors:
                    # 计算强度趋势
                    trend_data = self._calc_strength_trend(session, sector.code, sector.strength_score)

                    # 计算主线持续时间
                    is_mainline = sector.state in ("active_mainline", "exhausted_mainline")
                    mainline_days = self._calc_mainline_days(session, sector.code, is_mainline)

                    # 计算埋伏持续时间
                    is_ambush = sector.state == "ambush"
                    ambush_days = self._calc_ambush_days(session, sector.code, is_ambush)

                    # 回填到 SectorDetail（确保 JSON 输出不为 null）
                    sector.strength_trajectory = trend_data["trajectory"]
                    sector.trend_direction = trend_data["trend_direction"]
                    sector.mainline_days = mainline_days

                    # 创建或更新记录
                    db_record = session.query(Node2SectorTrendRecord).filter_by(
                        sector_code=sector.code,
                        tracking_date=today,
                    ).first()

                    if db_record:
                        # 更新已有记录
                        db_record.unified_signal = self._state_to_signal(sector.state)
                        db_record.lifecycle_phase = sector.lifecycle.phase if sector.lifecycle else None
                        db_record.strength_score = sector.strength_score
                        db_record.is_mainline = is_mainline
                        db_record.mainline_days = mainline_days
                        db_record.ambush_days = ambush_days
                        db_record.set_strength_trajectory(trend_data["trajectory"])
                        db_record.trend_direction = trend_data["trend_direction"]
                    else:
                        # 创建新记录
                        db_record = Node2SectorTrendRecord(
                            sector_code=sector.code,
                            sector_name=sector.name,
                            tracking_date=today,
                            unified_signal=self._state_to_signal(sector.state),
                            lifecycle_phase=sector.lifecycle.phase if sector.lifecycle else None,
                            strength_score=sector.strength_score,
                            is_mainline=is_mainline,
                            mainline_days=mainline_days,
                            ambush_days=ambush_days,
                            trend_direction=trend_data["trend_direction"],
                        )
                        db_record.set_strength_trajectory(trend_data["trajectory"])
                        session.add(db_record)

                session.commit()

            logger.info("[Node 2] 板块趋势持久化完成: %d 个板块", len(sectors))

        except Exception as e:
            logger.error("[Node 2] 板块趋势持久化失败: %s", e, exc_info=True)

    def _calc_strength_trend(self, session, sector_code: str, current_strength: float) -> dict:
        """计算强度趋势（最近 7 天）。"""
        history = session.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.sector_code == sector_code
        ).order_by(Node2SectorTrendRecord.tracking_date.desc()).limit(7).all()

        trajectory = [h.strength_score for h in reversed(history)] + [current_strength]

        # 趋势判定：3 天变化
        if len(trajectory) >= 3:
            recent_change = trajectory[-1] - trajectory[-3]
            trend = "increasing" if recent_change > 0.05 else \
                    "decreasing" if recent_change < -0.05 else "stable"
        else:
            trend = "stable"

        return {
            "trajectory": trajectory,
            "trend_direction": trend,
            "days_tracked": len(trajectory),
        }

    def _calc_mainline_days(self, session, sector_code: str, is_currently_mainline: bool) -> int:
        """计算连续主线天数。"""
        if not is_currently_mainline:
            return 0

        history = session.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.sector_code == sector_code
        ).order_by(Node2SectorTrendRecord.tracking_date.desc()).all()

        days = 0
        for record in history:
            if record.is_mainline:
                days += 1
            else:
                break
        return days + 1  # +1 for today

    def _calc_ambush_days(self, session, sector_code: str, is_currently_ambush: bool) -> int:
        """计算连续埋伏天数。"""
        if not is_currently_ambush:
            return 0

        history = session.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.sector_code == sector_code
        ).order_by(Node2SectorTrendRecord.tracking_date.desc()).all()

        days = 0
        for record in history:
            if record.ambush_days and record.ambush_days > 0:
                days = record.ambush_days
                break
            # 兼容旧数据：ambush_days 字段可能为 None
            if record.unified_signal == "neutral" and not record.is_mainline:
                days += 1
            else:
                break
        return days + 1  # +1 for today

    def _load_sector_trend_history(self, sector_code: str, days: int = 7) -> Optional[dict]:
        """加载板块趋势历史用于 LLM prompt 注入。"""
        try:
            db = DatabaseManager()
            with db.get_session() as session:
                history = session.query(Node2SectorTrendRecord).filter(
                    Node2SectorTrendRecord.sector_code == sector_code
                ).order_by(Node2SectorTrendRecord.tracking_date.desc()).limit(days).all()

                if not history:
                    return None

                # 取最新记录
                latest = history[0]
                trajectory = [h.strength_score for h in reversed(history)]

                return {
                    "sector_code": sector_code,
                    "sector_name": latest.sector_name,
                    "strength_trajectory": trajectory,
                    "trend_direction": latest.trend_direction,
                    "mainline_days": latest.mainline_days,
                    "unified_signal": latest.unified_signal,
                    "lifecycle_phase": latest.lifecycle_phase,
                }

        except Exception as e:
            logger.error("[Node 2] 加载板块趋势历史失败: %s", e, exc_info=True)
            return None

    @staticmethod
    def _state_to_signal(state: str) -> str:
        """将板块状态转换为信号方向。"""
        mapping = {
            "active_mainline": "bullish",
            "exhausted_mainline": "bearish",
            "ambush": "neutral",
            "defensive_rotation": "neutral",
        }
        return mapping.get(state, "neutral")

    def _format_trend_context_for_prompt(self, sectors: list[SectorDetail]) -> str:
        """格式化板块趋势历史用于 LLM prompt 注入。

        注意：轨迹数据来自个股级 node2_sector_trends 表，非框级历史。
        禁止 LLM 外推"连续 N 日主线"等组级连续性结论。
        """
        if not sectors:
            return ""

        lines = ["板块近期强度轨迹（个股级，非主线框历史）："]
        has_trend_data = False

        for sector in sectors[:5]:  # 最多 5 个板块
            trend_data = self._load_sector_trend_history(sector.code, days=7)
            if not trend_data or not trend_data.get("strength_trajectory"):
                continue

            has_trend_data = True
            trajectory = trend_data["strength_trajectory"]

            # 格式化轨迹
            trajectory_str = " → ".join(f"{s:.0f}" for s in trajectory)

            lines.append(f"- {sector.name}: 强度轨迹 [{trajectory_str}]")

        if not has_trend_data:
            return ""

        lines.append(
            "\n注意：以上轨迹是个股级强度评分历史，不代表主线框持续性。"
            "主线框刚建立，尚无组级历史数据。禁止推断'连续N日主线'等结论。"
        )
        return "\n".join(lines)

    # =================================================================
    # LLM reasoning 生成
    # =================================================================

    async def _generate_reasoning(self, ctx: SectorContext) -> Optional[str]:
        """调用 LLM 生成板块分析 reasoning 文本（纯总结，不影响信号）。"""
        try:
            from src.agent.llm_adapter import LLMToolAdapter
        except ImportError:
            logger.warning("LLMToolAdapter 不可用，跳过 reasoning 生成")
            return self._fallback_reasoning(ctx)

        mainline_names = ", ".join(
            f"{g.group_name}({g.group_score:.0f}分)" for g in ctx.active_mainline
        ) or "无"
        exhausted_names = ", ".join(
            f"{s.name}({s.strength_score:.0f}分)" for s in ctx.exhausted_mainline
        ) or "无"
        ambush_names = ", ".join(
            f"{s.name}({s.strength_score:.0f}分)" for s in ctx.ambush
        ) or "无"
        defensive_names = ", ".join(
            f"{s.name}({s.strength_score:.0f}分)" for s in ctx.defensive_rotation
        ) or "无"

        notes_text = "\n".join(f"- {n}" for n in ctx.notes) if ctx.notes else "无"

        # Node 2 v2: 降级摘要 + 主线家族 + 可信度
        quality_text = ctx.data_quality.degradation_summary or ""
        family_text = ""
        if ctx.theme_families:
            family_parts = []
            for f in ctx.theme_families[:5]:
                family_parts.append(
                    f"  {f.family_name}: {f.family_score:.0f}分, "
                    f"共振{f.family_resonance:.0%}, {f.theme_status}"
                )
            family_text = "\n".join(family_parts)

        # 加载板块趋势历史用于 LLM prompt 注入（跨日记忆）
        all_sectors: list[SectorDetail] = []
        for g in ctx.active_mainline:
            all_sectors.extend(g.members)
        all_sectors.extend(ctx.exhausted_mainline)
        all_sectors.extend(ctx.ambush)
        all_sectors.extend(ctx.defensive_rotation)
        trend_context = self._format_trend_context_for_prompt(all_sectors)

        prompt = (
            "你是一个专业的板块分析师。根据以下板块分析结果，撰写一段简洁的分析报告。\n\n"
        )

        # Node 2 v2: 降级警告放在最前面
        if quality_text and "正常" not in quality_text:
            prompt += f"【数据质量】{quality_text}\n"
            prompt += f"【评分可信度】{ctx.confidence_level}\n\n"

        prompt += (
            f"板块分析结果：\n"
            f"- 活跃主线：{mainline_names}\n"
            f"- 衰竭主线：{exhausted_names}\n"
            f"- 埋伏期：{ambush_names}\n"
            f"- 防守轮动：{defensive_names}\n"
            f"- 市场方向：{ctx.market_direction}\n"
            f"- 备注：{notes_text}\n\n"
        )

        if family_text:
            prompt += f"主线家族视角：\n{family_text}\n\n"

        # Node 2 v2: 行业板块视角
        if ctx.industry_top:
            industry_parts = [
                f"  {s.name}: {s.strength_score:.0f}分" for s in ctx.industry_top[:5]
            ]
            prompt += f"行业板块 Top 5（中期趋势）：\n" + "\n".join(industry_parts) + "\n\n"

        if trend_context:
            prompt += f"{trend_context}\n\n"

        prompt += (
            "请输出：\n"
            "1. 一段200字以内的分析文本，解释当前板块轮动格局\n"
            "2. 重点说明哪些板块是主线、持续性如何\n"
            "3. 主线家族的共振情况（哪些家族有多个板块同时活跃）\n"
            "4. 提示哪些板块处于埋伏期，可能成为下一个主线\n"
            "5. 风险提示（哪些板块过热需要回避）\n"
            "6. 若数据质量有降级警告，需在报告中明确提示\n\n"
            "输出格式：纯文本（不要JSON）"
        )

        try:
            adapter = LLMToolAdapter()
            messages = [{"role": "user", "content": prompt}]
            response = await asyncio.to_thread(
                adapter.call_text, messages, temperature=0.3, max_tokens=400,
            )
            if response and response.content:
                return response.content.strip()
        except Exception as e:
            logger.warning(f"LLM reasoning 生成失败: {e}")

        return self._fallback_reasoning(ctx)

    @staticmethod
    def _fallback_reasoning(ctx: SectorContext) -> str:
        """降级 reasoning（LLM 不可用时使用）。"""
        parts = []
        # Node 2 v2: 降级摘要优先展示
        if ctx.data_quality.degradation_summary:
            parts.append(f"[数据] {ctx.data_quality.degradation_summary}")
        if ctx.active_mainline:
            names = "、".join(g.group_name for g in ctx.active_mainline)
            parts.append(f"当前主线: {names}。")
        if ctx.exhausted_mainline:
            names = "、".join(s.name for s in ctx.exhausted_mainline)
            parts.append(f"衰竭板块: {names}，注意回避。")
        if ctx.ambush:
            names = "、".join(s.name for s in ctx.ambush)
            parts.append(f"埋伏期板块: {names}，关注启动信号。")
        if ctx.defensive_rotation:
            names = "、".join(s.name for s in ctx.defensive_rotation)
            parts.append(f"防守方向: {names}。")
        if ctx.theme_families:
            families_str = "、".join(
                f"{f.family_name}({f.theme_status})" for f in ctx.theme_families[:3]
            )
            parts.append(f"主线家族: {families_str}。")
        if ctx.industry_top:
            top_names = "、".join(s.name for s in ctx.industry_top[:3])
            parts.append(f"行业 Top: {top_names}。")
        parts.append(f"市场方向: {ctx.market_direction}。")
        if ctx.notes:
            parts.extend(ctx.notes)
        return " ".join(parts)

    # =================================================================
    # Mock 输出
    # =================================================================

    def _mock_output(self) -> SectorContext:
        """Mock 输出（保持向后兼容，使用 Node 2 v2 4 维指标）。"""
        from src.got.schemas import MainlineGroup

        ai_chip_member = SectorDetail(
            code="BK1144", name="AI芯片", state="active_mainline",
            strength_score=82.5,
            indicators={
                "price_momentum": 0.82, "turnover_expansion": 0.75,
                "relative_strength": 0.80, "breadth_health": 0.70,
            },
            launch_signal=LaunchSignal(
                triggered=True, level="strong_launch", score=0.82,
                dimensions={
                    "consolidation": 0.85, "volume_expansion": 0.90,
                    "price_strength": 0.75, "trend_alignment": 0.80,
                    "breadth": 0.80,
                },
            ),
            lifecycle=LifecycleInfo(
                phase="acceleration", days_since_launch=12,
                thresholds={"launch_end": 7, "acceleration_end": 30, "mature_end": 60},
            ),
            tier="A", recommended_action="buy",
            frame_group="AI科技",
        )
        robot_member = SectorDetail(
            code="BK1145", name="人形机器人", state="active_mainline",
            strength_score=75.0,
            indicators={
                "price_momentum": 0.70, "turnover_expansion": 0.65,
                "relative_strength": 0.60, "breadth_health": 0.55,
            },
            launch_signal=LaunchSignal(
                triggered=True, level="weak_launch", score=0.60,
            ),
            lifecycle=LifecycleInfo(
                phase="launch", days_since_launch=3,
            ),
            tier="B", recommended_action="aggressive_buy",
            frame_group="机器人",
        )

        return SectorContext(
            active_mainline=[
                MainlineGroup(
                    group_name="AI科技", group_score=82.5,
                    tier="A", mainline_quality="strong",
                    members=[ai_chip_member],
                ),
                MainlineGroup(
                    group_name="机器人", group_score=75.0,
                    tier="B", mainline_quality="weak",
                    members=[robot_member],
                ),
            ],
            exhausted_mainline=[
                SectorDetail(
                    code="BK1146", name="算力", state="exhausted_mainline",
                    strength_score=45.0,
                    indicators={
                        "price_momentum": 0.30, "turnover_expansion": 0.25,
                        "relative_strength": 0.35, "breadth_health": 0.40,
                    },
                    lifecycle=LifecycleInfo(
                        phase="mature_exhausted", days_since_launch=45,
                    ),
                    tier="A", recommended_action="exit",
                    frame_group="AI科技",
                ),
            ],
            ambush=[
                SectorDetail(
                    code="BK1147", name="半导体设备", state="ambush",
                    strength_score=55.0,
                    indicators={
                        "price_momentum": 0.50, "turnover_expansion": 0.55,
                        "relative_strength": 0.45, "breadth_health": 0.50,
                    },
                    frame_group="AI科技",
                ),
            ],
            defensive_rotation=[],
            market_direction="分化",
            reasoning="[Mock] AI芯片+人形机器人为当前主线，算力板块已过热。半导体设备处于埋伏期。",
            data_quality=DataQuality(
                available_fields=["sector_kline", "sector_constituents"],
                missing_fields=[],
            ),
        )
