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
import os
from datetime import date, datetime, timedelta
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from data_provider.base import DataFetcherManager
from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY, get_boards_by_category
from src.got.indicators import clip, calculate_macd
from src.got.indicators import (
    ad_distribution_signal,
    ad_line_confirmation,
    accumulation_score,
    bb_width_percentile,
    volume_vcv,
)
from src.got.schemas import (
    DataQuality,
    LaunchSignal,
    LifecycleInfo,
    MarketContext,
    OverheatSeverity,
    RRGQuadrant,
    SectorContext,
    SectorDetail,
)
from src.got.family_keywords import FAMILY_KEYWORDS, FAMILY_NAMES
from src.got import family_embed
from src.storage import DatabaseManager, Node2SectorTrendRecord

logger = logging.getLogger(__name__)


# =============================================================================
# 波次计数器（v3.5 S2：回撤结构判波）——常量与共享判波核心
# 在线 _calc_wave_days 与 scripts/backfill_wave_days.py 共用 decide_wave_days，
# 禁止各自维护分叉实现（审核 P1 修复，2026-09-01）。
# =============================================================================

# 主线族状态（波次存续域：降温/衰竭仍属"在波中"）
MAINLINE_FAMILY_STATES = {"active_mainline", "high_position_cooling", "exhausted_mainline"}
# 非主线族状态（经此退出 → 波次无条件重置）
NON_MAINLINE_RESET_STATES = {"neutral", "ambush", "defensive_rotation"}

# 波次重置阈值（决策 2，11 起样本弱先验，待 S5 校准迭代）
WAVE_GAP_MIN_DAYS = 2          # 出框间隙 ≥2 交易日（按记录条数计，规避日历日跨周末误判）
WAVE_STRENGTH_DRAWDOWN = 10    # 隙期强度自波峰回撤 ≥10 分 → 判真洗盘再启动


def decide_wave_days(
    current_state: str,
    current_strength: float,
    today: date,
    prev_record,
    anchor_record,
    gap_records: list,
) -> tuple[int, Optional[date], float]:
    """波次判定核心（在线 _calc_wave_days 与回填脚本共用，决策 2）。

    语义：
    - 无前记录 / 今日经非主线族退出 → 重置 1（新波）
    - 前日非主线族 → 无条件重置 1（经 neutral/ambush/defensive 退出）
    - 降温期（今日 cooling/exhausted）→ 冻结继承（同波存续，天数不累加）
    - 前日 active → +1，波峰滚动更新
    - cooling/exhausted → active 回归 → 两段式回撤结构判定：
      间隙 <2 交易日（单日闪断/单日深 V）→ 冻结；
      隙期自出框前平台回撤 ≥10 分 → 真洗盘再启动，重置 1；否则冻结。
      回撤取隙期最低强度（非最高）：真实样本中 4 起真二波有 3 起隙期带反弹回升段，
      若取隙期最高强度，回升段会抵消回撤、漏判焦炭加工/煤炭开采/航运概念；
      判据只认回撤深度不认形态：复合铜箔（真二波，隙期 47→78 回升）与种植业 08-27（噪声，浅 V）
      均呈 V 形，V 形态无判别力；单日深 V 的"2 日确认"由后续交易日的回撤判定自然完成（读法 A）。
    - 冻结时波峰滚动更新（波峰仅作展示/埋点口径，不参与重置判定）。
    - 回撤基准 = 出框前平台强度（锚点日 strength，plan.md 口径），不用存储波峰（可能被旧逻辑污染）。
    - 间隙出现非主线族记录 → 视为已经退出过，无条件重置。
    - 冻结继承的 wave_days 兜底 ≥1（防御旧记录 wave_days 为 NULL）。

    Args:
        current_state:   当日状态
        current_strength: 当日强度分（0-100）
        today:           当日日期（交易日历）
        prev_record:     最近一条历史记录（< today），无前记录传 None；
                         需带 state/wave_days/wave_start_date/wave_peak_strength/tracking_date/strength_score
        anchor_record:   最近一条 active_mainline 历史记录（出框前平台锚点），无则 None；
                         仅 current=active 且 prev 为 cooling/exhausted 时需要，其余场景可传 None
        gap_records:     anchor（不含）至今日（不含）之间的记录列表，可不排序（内部按日期升序）
    """
    strength = float(current_strength or 0)

    # 无前记录 → 新波起点；今日经非主线族退出 → 波终结重置（冷却位不存续）
    if prev_record is None or current_state not in MAINLINE_FAMILY_STATES:
        return 1, today, strength

    prev_state = prev_record.state
    prev_wd = int(getattr(prev_record, "wave_days", 0) or 0)
    prev_start = getattr(prev_record, "wave_start_date", None) or prev_record.tracking_date
    prev_peak = float(getattr(prev_record, "wave_peak_strength", 0) or 0)

    # 前日非主线族（neutral/ambush/defensive）→ 波已终结，无条件重置；
    # state 为 None 的旧版本记录同样视为波间断（保守重置）
    if not prev_state or prev_state in NON_MAINLINE_RESET_STATES:
        return 1, today, strength

    # ── 今昨均在主线族 ──
    # 降温期（cooling/exhausted）：冻结继承，天数不累加；波峰照常滚动（仅展示口径，不参与重置判定）
    if current_state != "active_mainline":
        return max(prev_wd, 1), prev_start, max(prev_peak, strength)

    # 前日 active → 当前波延续，波峰滚动更新
    if prev_state == "active_mainline":
        return prev_wd + 1, prev_start, max(prev_peak, strength)

    # ── cooling/exhausted → active 回归：决策 2 回撤结构判定 ──
    if anchor_record is None:
        return 1, today, strength

    anc_wd = int(getattr(anchor_record, "wave_days", 0) or 0)
    anc_start = getattr(anchor_record, "wave_start_date", None) or anchor_record.tracking_date
    anc_peak = float(getattr(anchor_record, "wave_peak_strength", 0) or 0)
    # 出框前平台强度（锚点日强度，回撤基准；plan.md"低于出框前平台 ≥10 分"口径）
    anc_platform = float(getattr(anchor_record, "strength_score", 0) or 0)

    gaps = sorted(gap_records or [], key=lambda r: r.tracking_date)

    # 间隙中出现非主线族退出态 → 波已断过，无条件重置（结论 B）
    if any(getattr(r, "state", None) in NON_MAINLINE_RESET_STATES for r in gaps):
        return 1, today, strength

    # 间隙按交易日计（记录条数）；单日闪断/单日深 V 不重置 → 冻结继承（锚点口径）
    if len(gaps) < WAVE_GAP_MIN_DAYS:
        return max(anc_wd, 1), anc_start, max(anc_peak, strength)

    # 回撤结构：隙期最低强度自出框前平台回撤 ≥10 分 → 真洗盘再启动，重置波次（复合铜箔型）；
    # 取最低而非最高：回升段不能抵消回撤（焦炭加工/煤炭开采/航运三起真二波均带隙期回升）；
    # 基准用锚点日平台强度而非存储波峰（波峰可能被旧逻辑污染）；
    # 不另设 V 形回升豁免（真实样本验证：V 形态对真二波/噪声无判别力）
    gap_strengths = [float(getattr(r, "strength_score", 0) or 0) for r in gaps]
    if anc_platform - min(gap_strengths) >= WAVE_STRENGTH_DRAWDOWN:
        return 1, today, strength

    # 回撤不足 → 同波延续，冻结继承（种业型：状态翻动但强度未回撤；种植业 08-27 型浅 V）
    return max(anc_wd, 1), anc_start, max(anc_peak, strength)


# =============================================================================
# 主线家族关键词已迁移至 src.got.family_keywords（B2 词表同源修复）
# FAMILY_KEYWORDS / FAMILY_NAMES 从该模块 import（见文件顶部）
# =============================================================================

CONCEPT_UNIVERSE: dict[str, dict[str, list[str]]] = {
    # === AI 上游：芯片/半导体 ===
    "ai_chip": {
        "semiconductor_equip": ["半导体设备", "光刻机", "刻蚀机"],
        "ai_chip": ["AI芯片", "GPU", "寒武纪"],
        "memory_chip": ["存储芯片", "HBM概念", "高带宽内存"],
        "auto_chip": ["汽车芯片", "MCU芯片", "车规芯片"],
        "semiconductor_pkg": ["先进封装", "Chiplet", "玻璃基板"],
        "eda_ip": ["EDA概念", "芯片概念", "半导体概念", "第三代半导体", "第四代半导体", "RISC-V", "国产芯片"],
        "power_semiconductor": ["IGBT概念", "碳化硅", "氮化镓"],
        "electronic_components": ["元件", "电子化学品", "被动元件概念"],
    },
    # === AI 基建：算力/网络/散热 ===
    "ai_infra": {
        "compute_power": ["算力", "算力租赁", "东数西算", "AI服务器"],
        "optical_module": ["光模块", "CPO概念", "光通信", "光纤概念"],
        "liquid_cooling": ["液冷概念", "液冷服务器"],
        "pcb_connector": ["PCB概念", "高速连接器", "铜缆高速连接", "数据中心"],
    },
    # === AI 模型/平台 ===
    "ai_model": {
        "llm": ["大模型", "AIGC概念", "多模态AI", "AI语料"],
        "ai_platform": ["人工智能", "AI应用", "AI智能体", "智算中心"],
        "digital_economy": ["数字经济", "数字孪生", "国资云"],
    },
    # === AI 终端应用 ===
    "ai_terminal": {
        "ai_phone_pc": ["AI手机", "AI PC", "AI眼镜", "AIPC概念"],
        "consumer_elec": ["消费电子", "混合现实", "OLED概念", "MiniLED", "柔性屏"],
        "apple_chain": ["苹果概念"],
        "new_content": ["短剧游戏", "谷子经济"],
    },
    # === 机器人 ===
    "robot": {
        "humanoid": ["人形机器人", "具身智能", "机器人概念"],
        "robot_parts": ["减速器", "丝杠", "传感器", "伺服", "控制器", "电机", "机器人执行器", "机器视觉"],
        "robot_system": ["机器人", "工业机器人", "服务机器人"],
    },
    # === 智能驾驶 ===
    "auto_drive": {
        "smart_drive": ["自动驾驶", "智能驾驶", "智能座舱", "车路云", "激光雷达"],
        "new_energy": ["新能源车", "固态电池", "充电桩", "高压快充", "换电概念"],
        "battery_tech": ["BC电池", "钙钛矿电", "TOPCon电", "钠电池", "麒麟电池", "刀片电池", "动力电池回收"],
        "auto_market": ["汽车整车"],
    },
    # === 新兴主题 ===
    "emerging": {
        "low_altitude": ["低空经济", "飞行汽车", "eVTOL", "无人机"],
        "data_element": ["数据要素"],
        "huawei_chain": ["华为概念", "鸿蒙概念", "华为海思", "华为手机", "华为算力", "华为欧拉"],
        "satellite": ["卫星互联网", "卫星导航", "北斗", "5G概念", "6G概念", "商业航天"],
        "frontier": ["可控核聚变"],
    },
    # === 科技防守 ===
    "tech_defensive": {
        "innovative_drug": ["创新药", "生物医药", "CXO概念", "医疗器械", "减肥药", "医美概念"],
        "power_grid": ["电力", "特高压", "智能电网", "绿色电力", "虚拟电厂", "核电概念"],
        "energy_storage": ["储能", "光伏概念", "锂电池概念", "氢能源", "风电"],
    },
    # === 金融 ===
    "finance": {
        "securities": ["券商"],
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
        "nonferrous": ["有色金属", "铜", "黄金概念", "稀土永磁", "铝"],
        "steel_chem": ["钢铁", "化工", "化纤"],
        "new_materials": ["PEEK材料", "碳纤维概", "石墨烯", "超导概念", "有机硅概", "合成生物"],
    },
    # === 消费 ===
    "consumer": {
        "beverage": ["白酒", "白酒概念"],
        "healthcare_consumer": ["医美概念"],
        "travel": ["旅游", "旅游概念", "酒店餐饮"],
        "silver_economy": ["养老概念", "养老金持"],
    },
    # === 军工 ===
    "military": {
        "aerospace": ["航天航空", "国防军工", "航空发动机", "卫星", "无人机", "军民融合"],
    },
}

# category → 标签
_CATEGORY_GROUP: dict[str, str] = {
    # AI 上游
    "semiconductor_equip": "半导体设备",
    "ai_chip": "AI芯片",
    "memory_chip": "存储芯片",
    "auto_chip": "汽车芯片",
    "semiconductor_pkg": "先进封装",
    "eda_ip": "EDA/IP",
    "power_semiconductor": "功率半导体",
    "electronic_components": "元器件/材料",
    # AI 基建
    "compute_power": "算力",
    "optical_module": "光模块/CPO",
    "liquid_cooling": "液冷散热",
    "pcb_connector": "PCB/连接器",
    # AI 模型
    "llm": "大模型",
    "ai_platform": "AI平台",
    "digital_economy": "数字经济",
    # AI 终端
    "ai_phone_pc": "AI手机/PC",
    "consumer_elec": "消费电子",
    "apple_chain": "苹果产业链",
    "new_content": "新内容消费",
    # 机器人
    "humanoid": "人形机器人",
    "robot_parts": "机器人零部件",
    "robot_system": "机器人整机",
    # 智能驾驶
    "smart_drive": "智能驾驶",
    "new_energy": "新能源车",
    "battery_tech": "电池技术",
    "auto_market": "汽车整车",
    # 新兴主题
    "low_altitude": "低空经济",
    "data_element": "数据要素",
    "huawei_chain": "华为产业链",
    "satellite": "卫星/商业航天",
    "frontier": "前沿科技",
    # 科技防守
    "innovative_drug": "创新药",
    "power_grid": "电力",
    "energy_storage": "新能源",
    # 金融
    "securities": "券商",
    "insurance": "保险",
    "banking": "银行",
    "diversified_finance": "多元金融",
    # 外围联动
    "nvidia": "英伟达",
    # 周期
    "nonferrous": "有色",
    "steel_chem": "钢铁化工",
    "new_materials": "新材料",
    # 消费
    "beverage": "白酒",
    "healthcare_consumer": "医美",
    "travel": "旅游酒店",
    "silver_economy": "养老",
    # 军工
    "aerospace": "军工航天",
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

# 生命周期阶段 → 推荐操作（Node 2 v3: RRG 象限映射到 lifecycle phase 后复用此表）
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
    "power_semiconductor": "AI产业链", "electronic_components": "AI产业链",
    "compute_power": "AI产业链", "optical_module": "AI产业链",
    "liquid_cooling": "AI产业链", "pcb_connector": "AI产业链",
    "llm": "AI产业链", "ai_platform": "AI产业链", "digital_economy": "AI产业链",
    "ai_phone_pc": "AI产业链", "consumer_elec": "AI产业链",
    "apple_chain": "AI产业链", "new_content": "AI产业链",
    # 机器人
    "humanoid": "机器人", "robot_parts": "机器人", "robot_system": "机器人",
    # 智能驾驶
    "smart_drive": "智能驾驶", "new_energy": "智能驾驶", "battery_tech": "智能驾驶",
    "auto_market": "智能驾驶",
    # 新兴主题（各自独立）
    "low_altitude": "低空经济", "data_element": "数据要素",
    "huawei_chain": "华为系", "satellite": "卫星通信", "frontier": "前沿科技",
    # 科技防守
    "innovative_drug": "医药", "power_grid": "电力", "energy_storage": "新能源",
    # 金融
    "securities": "金融", "insurance": "金融", "banking": "金融",
    "diversified_finance": "金融",
    # 其他
    "nvidia": "外围联动",
    "nonferrous": "黄金/有色", "steel_chem": "钢铁化工", "new_materials": "新材料",
    "consumer": "消费", "beverage": "消费", "healthcare_consumer": "消费",
    "travel": "消费", "silver_economy": "消费",
    "aerospace": "军工",
}

# =============================================================================
# 动态主线框映射（按板块名称关键词匹配，用于全量发现模式）
# 按优先级排列，首个命中即返回。未命中 → "其他"
# =============================================================================
_FRAME_GROUP_KEYWORDS: list[tuple[list[str], str]] = [
    # AI 全产业链（半导体上游优先匹配）
    (["芯片", "半导体", "光刻", "EDA", "封装", "HBM", "高带宽内存", "存储芯片", "GPU", "寒武纪", "RISC-V", "国产芯片",
      "元件", "电子化学品", "被动元件", "IGBT", "碳化硅", "氮化镓", "玻璃基板"], "AI产业链"),
    (["算力", "光模块", "CPO", "液冷", "PCB", "铜缆", "数据中心", "东数西算", "华为算力", "华为海思"], "AI产业链"),
    (["大模型", "AIGC", "多模态", "人工智能", "AI应用", "智能体", "智算", "AI语料",
      "数字经济", "数字孪生", "国资云"], "AI产业链"),
    (["AI手机", "AI PC", "AI眼镜", "AIPC", "消费电子", "混合现实", "OLED", "MiniLED", "折叠屏",
      "短剧游戏", "谷子经济"], "AI产业链"),
    (["苹果概念"], "AI产业链"),
    # 机器人
    (["人形机器人", "人形机器", "具身智能", "机器人", "减速器", "丝杠", "伺服", "机器人执行器", "机器视觉"], "机器人"),
    # 智能驾驶（必须在新能源/电力之前）
    (["自动驾驶", "智能驾驶", "智能座舱", "车路云", "激光雷达", "无人驾驶", "车联网", "高压快充", "换电"], "智能驾驶"),
    (["新能源车", "固态电池", "充电桩", "汽车整车"], "智能驾驶"),
    # 数据要素
    (["数据要素"], "数据要素"),
    # 华为系
    (["华为概念", "华为欧拉", "欧拉"], "华为系"),
    (["鸿蒙"], "华为系"),
    # 低空经济
    (["低空经济", "飞行汽车", "eVTOL", "无人机"], "低空经济"),
    # 卫星/通信
    (["卫星", "北斗", "5G", "6G", "商业航天", "航天航空", "国防军工"], "卫星通信"),
    # 前沿科技
    (["可控核聚变", "量子科技"], "前沿科技"),
    # 电池技术（新能源细分）
    (["BC电池", "钙钛矿电", "TOPCon电", "钠电池", "麒麟电池", "刀片电池", "动力电池回收"], "电池技术"),
    # 核电/绿电/电力
    (["核电", "核能"], "核电/绿电"),
    (["绿色电力", "绿电"], "核电/绿电"),
    # 新能源/储能
    (["储能", "光伏", "锂电池", "氢能", "风电"], "新能源"),
    # 电力（特高压/电网）
    (["电力", "特高压", "智能电网", "虚拟电厂"], "电力"),
    # 医药
    (["创新药", "生物医药", "CXO", "医疗器械", "医药", "减肥药", "医美概念"], "医药"),
    # 金融
    (["券商", "保险", "银行", "信托", "多元金融"], "金融"),
    # 黄金/有色
    (["黄金", "有色", "铜", "铝", "稀土", "锂矿"], "黄金/有色"),
    # 新材料
    (["PEEK", "碳纤维概", "石墨烯", "超导", "有机硅概", "合成生物"], "新材料"),
    # 钢铁化工
    (["钢铁", "化工", "化纤"], "钢铁化工"),
    # 军工
    (["军工", "航空", "船舶", "航运", "国防", "军民融合"], "军工"),
    # 消费
    (["白酒", "白酒概念", "食品", "家电", "旅游", "酒店", "零售", "养老概念", "养老金持", "宠物经济"], "消费"),
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

# =============================================================================
# 噪声板块过滤（v2，2026-08-25）
#
# 判定实现已抽至零依赖共享模块 src/got/board_filter.py，供 Node 2 与
# market_theme（4b 影子线）共用——破除 "schemas → market_theme 不能反向
# import sector_analysis" 的循环依赖，并消除两处噪声判定的漂移。
#
# 此处保留旧私有名做别名，使既有调用点（_discover_boards 等）与文档无需改动。
# =============================================================================
from src.got.board_filter import (  # noqa: E402
    NOISE_BOARDS as _NOISE_BOARD_EXACT,
    is_noise_pattern as _is_noise_pattern,
    is_noise_board as _is_noise_board,
)


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
            # Step 0: 获取同花顺板块热度（辅助维度）
            ths_hot_sectors = await self._fetch_ths_hot_sectors()

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
                    near_miss_ambush=[],
                    defensive_rotation=[],
                    data_quality=quality,
                    reasoning="板块数据源不可用（mootdx TCP 连接失败），跳过板块分析",
                )

            # Step 1.5: 加载板块趋势历史（跨日连续性）
            prev_trends = self._load_all_sector_trends(sectors_raw)

            # Step 1.52: 成分股涨跌幅批量预取（v2 过热 D7/D9 数据源，计划 §4.1）
            # 只对前日 actionable + 今日大涨板块预取，失败降级为代理口径
            await asyncio.to_thread(
                self._prefetch_constituent_stats, sectors_raw, prev_trends,
            )

            # Step 1.55: 提前计算市场方向（用于 regime-dependent P0 门槛）
            # 基于板块 K 线 T-0 涨跌幅判断：普涨/普跌/分化
            early_direction = self._early_market_direction(sectors_raw)
            logger.info(f"[Node 2] 提前市场方向: {early_direction}")

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

            # Pass 2.5: 预计算全部 strength_score（供 RRG RS-Ratio 截面排名）
            all_scores_today: list[float] = []
            for ranked in all_ranked:
                score = self._calc_strength_score(ranked)
                all_scores_today.append(score)

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
                    ths_hot_sectors=ths_hot_sectors,
                    market_direction=early_direction,
                    all_scores_today=all_scores_today,
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
                    near_miss_ambush=[],
                    defensive_rotation=[],
                    data_quality=quality,
                    reasoning="板块数据分析全部失败，跳过板块分析",
                )

            # Step 3: 按状态分类（状态机驱动，框系统已废除 2026-08-31）
            result = self._classify_sectors(analyzed, market_context)

            # Step 3.3: 输出层聚合（状态驱动，替代原框系统）
            # 从 Node 1 读 attack_power（市场进攻强度），动态约束主线段输出数量
            attack_power = self._extract_attack_power(market_context)
            result = self._build_mainline_output(
                result, analyzed,
                attack_power=attack_power,
            )

            # Step 3.4: 主线家族聚合（数据驱动相关性聚类，替代硬编码关键词）
            result.theme_families = self._build_theme_families(analyzed, sectors_raw)

            # Step 3.4c: 「其他」板块的 embedding 家族兜底（WI-P1-7(b)，2026-10-06）
            # 三态开关 GOT_FAMILY_EMBED_FALLBACK：off（缺省）/ shadow / enabled。
            # 复用 glue_pipeline 的 L1 向量检索件，但走生产独立开关（计划 §315 红线：
            # glue_pipeline 本身仍 replay-only，本处只借 embed_texts/retrieve_topk/缓存惯例）。
            # off 时 resolve_family_overrides 立即返回、不触网不改判 → 旧路径逐字节不变。
            # 阈值同为环境变量读取期取值（缺省值为未标定启发式，先由 shadow 攒读数）。
            fe_min_score = float(os.getenv("GOT_FAMILY_EMBED_MIN_SCORE", "0.55"))
            fe_min_margin = float(os.getenv("GOT_FAMILY_EMBED_MIN_MARGIN", "0.05"))
            fe_report: Optional[dict] = None
            try:
                fe_report = await family_embed.resolve_family_overrides(
                    [{"code": s.code, "name": s.name} for s in analyzed],
                    date.today(),
                    min_score=fe_min_score,
                    min_margin=fe_min_margin,
                )
            except Exception as e:  # noqa: BLE001 — 兜底能力异常不得影响 Node 2 主流程
                logger.warning("[Node 2] 家族 embedding 兜底异常，保持原家族判定: %s", e)
                fe_report = None
            if fe_report and fe_report.get("mode", "off") != "off":
                result.family_embed_shadow = fe_report
                if fe_report["mode"] == "enabled" and fe_report["overrides"]:
                    # 只改 frame_group，不改 state/score；后续家族拥挤/主线聚合/Node 6
                    # 家族中继（frame_group_lookup）自然消费新家族
                    for s in analyzed:
                        hit = fe_report["overrides"].get(s.name)
                        if hit:
                            s.frame_group = hit["family"]
                    result.notes.append(
                        f"ℹ️ [家族兜底 enabled] embedding 兜底改判 {len(fe_report['overrides'])} "
                        f"个原「其他」板块（阈值 score≥{fe_min_score}/margin≥{fe_min_margin}）"
                    )
                    logger.info("[Node 2] 家族兜底 enabled: %s", list(fe_report["overrides"]))

            # Step 3.4a: 跨板块拥挤检测（R2）—— 必须在兜底改判之后，拥挤才能看到新家族
            crowding_warnings = self._detect_family_crowding(analyzed)
            if crowding_warnings:
                result.notes.extend(crowding_warnings)
                logger.info(f"[Node 2] 家族拥挤警示: {crowding_warnings}")

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

            # Step 3.5b: 全量板块→强度映射（P0-1: 供 Node 6 蓄水池升格消费）
            result.strength_lookup = {
                s.name: s.strength_score for s in analyzed if s.name
            }
            # Q2 audit: THS 板块热度归档（供 Node 6 G2/G3 闸门消费）
            result.ths_hot_sectors = ths_hot_sectors


            # Step 3.6: 板块趋势持久化（跨日记忆）
            self._save_sector_trends(analyzed)

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

        # T-0 涨跌幅：从板块 K 线最后两根 bar 直接计算（覆盖所有有 K 线的板块）
        # mootdx index_bars 盘中最后一根 bar 即为当日实时数据
        t0_count = 0
        for s in sectors:
            klines = s.get("klines", [])
            if len(klines) >= 2:
                today_close = klines[-1].get("close", 0)
                prev_close = klines[-2].get("close", 0)
                if prev_close > 0 and today_close > 0:
                    s["today_change"] = round(
                        (today_close - prev_close) / prev_close * 100, 2
                    )
                    t0_count += 1

        logger.info(
            f"[Node 2] T-0 涨跌幅: {t0_count}/{len(sectors)} 个板块（K线计算）"
        )

        logger.info(
            f"[Node 2] 完成: {len(sectors)}/{len(matched)} 个板块获取成功",
        )
        return sectors

    async def _fetch_ths_hot_sectors(self) -> dict[str, float]:
        """获取同花顺板块热度排行，返回 {板块名: 热度值} 字典。

        作为板块强度评分的辅助维度：高热度板块获得情绪加成。
        降级：采集失败返回空字典（不影响主流程）。
        """
        try:
            from src.services.ths_hot_list import fetch_hot_sectors

            sectors = await asyncio.to_thread(fetch_hot_sectors, top_n=30)
            if not sectors:
                logger.warning("[Node 2] 同花顺板块热度: 0 条")
                return {}

            # 转为 {板块名: 热度值} 字典
            hot_map = {s["name"]: s["rate"] for s in sectors if s.get("name")}
            logger.info("[Node 2] 同花顺板块热度: %d 条", len(hot_map))
            return hot_map

        except Exception as exc:
            logger.warning("[Node 2] 同花顺板块热度采集失败: %s", exc)
            return {}

    async def _discover_boards(
        self, quality: DataQuality,
    ) -> list[dict[str, Any]]:
        """四通道板块发现（B-lite 方案 + 时间线维护）。

        Phase A: stock_all() → 652 概念板块 → 快筛 11天K线
        通道 1: 当日涨跌幅 Top 30 涨 + Top 30 跌
        通道 2 (B-lite): 3日>0 ∧ 5日>0 ∧ 无单日暴跌 → 按5日累计涨幅 Top 20
        通道 3: registry 保底
        通道 4（时间线维护，2026-08-31）：前日 actionable 板块强制并入候选集，
        防止板块因跌出截面排名而"蒸发"（煤炭 0831 根因修复）
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

        # 快筛：批量取 11天 K线（B-lite 需要多时间框架数据）
        all_codes = [b["code"] for b in all_boards]
        code_name_map = {b["code"]: b["name"] for b in all_boards}

        quick_data = await asyncio.to_thread(
            self.fetcher.get_board_quick_change, all_codes,
        )

        if not quick_data:
            logger.warning("[Node 2] 快筛全部失败，降级为 registry")
            return registry_boards

        logger.info(f"[Node 2] 快筛成功: {len(quick_data)}/{len(all_codes)} 个板块")

        # === 通道 1: 当日涨跌幅 Top 30 涨 + Top 30 跌 ===
        sorted_by_1d = sorted(
            quick_data.items(),
            key=lambda x: x[1]['change_1d'],
            reverse=True,
        )
        top_up_1d = sorted_by_1d[:_QUICK_TOP_N]
        top_down_1d = sorted_by_1d[-_QUICK_TOP_N:] if len(sorted_by_1d) > _QUICK_TOP_N else []

        # === 通道 2 (B-lite): 累计动量通道 ===
        # 条件：3日>0 ∧ 5日>0 ∧ 近5日无单日跌幅>5%
        # 排序：5日累计涨幅
        B_LITE_TOP_N = 20
        b_lite_candidates = []
        for code, data in quick_data.items():
            if (data['change_3d'] > 0 and
                data['change_5d'] > 0 and
                not data['has_crash']):
                b_lite_candidates.append((code, data['change_5d']))

        # 按5日累计涨幅排序，取 Top N
        b_lite_candidates.sort(key=lambda x: x[1], reverse=True)
        top_b_lite = b_lite_candidates[:B_LITE_TOP_N]

        logger.info(
            f"[Node 2] B-lite 通道: {len(b_lite_candidates)} 个满足条件，"
            f"取 Top {len(top_b_lite)}"
        )

        # === 合并候选集（去重，registry 保底）===
        candidate_codes: dict[str, str] = {}  # code → name

        # 通道 1: 当日涨跌幅 Top
        for code, _ in top_up_1d + top_down_1d:
            name = code_name_map.get(code, code)
            candidate_codes[code] = name

        # 通道 2: B-lite 累计动量 Top
        for code, _ in top_b_lite:
            if code not in candidate_codes:
                name = code_name_map.get(code, code)
                candidate_codes[code] = name

        # Registry 保底（也需噪声过滤，防止硬编码列表混入统计指数）
        for b in registry_boards:
            if b["code"] not in candidate_codes and not _is_noise_board(b["name"]):
                candidate_codes[b["code"]] = b["name"]

        # === 通道 4（时间线维护，2026-08-31）===
        # 根因修复：板块跌出当日截面候选集 → 不在 analyzed → 状态机跳过 → "蒸发"
        # 从 node2_sector_trends 取前日 actionable 板块，强制并入候选集
        timeline_boards = self._load_prev_actionable_boards()
        timeline_count = 0
        for code, name in timeline_boards:
            if code in candidate_codes:
                continue
            # 噪声过滤：统计指数/行情事件/财务标签等不应参与主线分析
            if _is_noise_board(name):
                continue
            if timeline_count >= self._TIMELINE_SUPPLY_MAX:
                break
            candidate_codes[code] = name
            timeline_count += 1
        if timeline_count > 0:
            logger.info(
                f"[Node 2] 时间线维护通道: 补入 {timeline_count} 个前日 actionable 板块"
            )

        # 构建候选板块列表（动态 frame_group）
        matched: list[dict] = []
        for code, name in candidate_codes.items():
            frame_group = _build_frame_group(name)
            is_from_registry = code in registry_codes
            # 获取该板块的快筛数据（如果有）
            data = quick_data.get(code, {})
            matched.append({
                "code": code,
                "name": name,
                "category": "dynamic" if not is_from_registry else "registry",
                "group": frame_group,
                "frame_group": frame_group,
                "change_pct": data.get('change_1d', 0.0),
                "change_3d": data.get('change_3d', 0.0),
                "change_5d": data.get('change_5d', 0.0),
                "up_count": 0,
                "down_count": 0,
                "turnover_rate": 0.0,
            })

        dynamic_count = sum(1 for m in matched if m["category"] == "dynamic")
        b_lite_count = len(top_b_lite)
        logger.info(
            f"[Node 2] 候选集: {len(matched)} 个 "
            f"(通道1: {len(top_up_1d) + len(top_down_1d)} + "
            f"B-lite: {b_lite_count} + "
            f"registry: {len(matched) - dynamic_count - timeline_count} + "
            f"时间线: {timeline_count})",
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

    # =================================================================
    # 成分股涨跌幅预取（v2 过热 D7/D9 数据源，计划 §4.1）
    # =================================================================

    # 当日缓存：{BK代码: (日期, 统计字典)}——成分股列表日内不变，
    # 且 _detect_overheat_signal 每板块被调用两次，预取一次全局复用
    _constituent_cache: dict[str, tuple[date, dict]] = {}

    @staticmethod
    def _limit_up_pct_for_code(stock_code: str) -> float:
        """按个股代码前缀确定涨停口径：主板 10 / 创业板·科创板 20 / 北交所 30。

        复用 stock_screener.py 轮次 0 _is_limit_up 的前缀判断模式（计划 §3.1）。
        """
        code = str(stock_code or "")
        if code.startswith(("300", "301", "688", "689")):
            return 20.0
        if code.startswith(("920", "43", "83", "87")):  # 北交所
            return 30.0
        return 10.0

    def _prefetch_constituent_stats(
        self, sectors_raw: list[dict], prev_trends: dict[str, dict],
    ) -> None:
        """对候选板块批量预取成分股涨跌幅统计，注入 sector_info。

        注入字段：max_stock_change_pct / max_stock_code / pct_stocks_up_gt5
        候选范围：前日 actionable（主线/埋伏/高位降温）+ 今日涨 ≥3%，上限 25 个。
        降级策略：获取失败不注入（D7 回退 today_change 代理、D9 回退宽度代理），
        方向安全：0 = 无过热，不会触发冷启动评分倒挂。
        """
        from src.got.board_aliases import sector_name_variants

        # 1) 筛选候选板块（控制 IO，计划 §4.1 只对 ~20 个板块获取）
        targets: list[dict] = []
        for s in sectors_raw:
            prev = prev_trends.get(s.get("code", "")) or {}
            prev_actionable = prev.get("state") in (
                "active_mainline", "ambush", "high_position_cooling",
            )
            top_gainer = (s.get("change_pct") or 0) >= 3.0
            if prev_actionable or top_gainer:
                targets.append(s)
        if not targets:
            return
        targets = targets[:25]

        # 2) 东财板块排行一次请求，构建名称 → BK 代码映射（含别名双向归一）
        try:
            boards_df = self.fetcher.get_board_constituents("concept", n=300)
        except Exception as e:
            logger.warning(f"[Node 2] 成分股统计预取降级：东财板块排行获取失败 ({e})")
            boards_df = None
        if boards_df is None or boards_df.empty:
            logger.warning("[Node 2] 成分股统计预取降级：东财板块排行为空")
            return
        em_map: dict[str, str] = {}
        for _, row in boards_df.iterrows():
            bname = str(row.get("板块名称", "")).replace("\u0000", "").strip()
            bcode = str(row.get("板块代码", ""))
            if bname and bcode:
                em_map[bname] = bcode

        # 3) 逐板块拉成分股并计算统计（当日缓存）
        ok_cnt = 0
        for s in targets:
            name = str(s.get("name", "")).replace("\u0000", "").strip()
            bk_code = ""
            for variant in sector_name_variants(name):
                if variant in em_map:
                    bk_code = em_map[variant]
                    break
            if not bk_code:
                continue
            stats = self._fetch_board_constituent_stats(bk_code)
            if stats:
                s["max_stock_change_pct"] = stats["max_stock_change_pct"]
                s["max_stock_code"] = stats["max_stock_code"]
                s["pct_stocks_up_gt5"] = stats["pct_stocks_up_gt5"]
                ok_cnt += 1
        logger.info(f"[Node 2] 成分股统计预取: {ok_cnt}/{len(targets)} 个板块成功")

    def _fetch_board_constituent_stats(self, bk_code: str) -> Optional[dict]:
        """拉取东财 BK 板块成分股（按涨幅降序），计算 D7/D9 统计。

        返回 {max_stock_change_pct, max_stock_code, pct_stocks_up_gt5}；失败返回 None。
        注：一页 100 条按涨幅降序，涨 >5% 的票全在头部，计数不受截断影响；
        分母用 API 报告的成分股总数。
        """
        today = date.today()
        cached = self._constituent_cache.get(bk_code)
        if cached and cached[0] == today:
            return cached[1]

        from data_provider.em_rate_limiter import em_get

        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": f"b:{bk_code}+f:!50",
            "fields": "f2,f3,f12,f14",
        }
        try:
            resp = em_get(url, params=params)
            data = resp.json()
            inner = data.get("data") or {}
            diff = inner.get("diff") or []
            total = inner.get("total") or len(diff)
        except Exception as e:
            logger.debug(f"[Node 2] 成分股获取失败 {bk_code}: {e}")
            return None
        if not diff:
            return None

        def _num(v) -> float:
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        first = diff[0]
        stats = {
            "max_stock_change_pct": _num(first.get("f3")),
            "max_stock_code": str(first.get("f12", "")),
            "pct_stocks_up_gt5": (
                sum(1 for it in diff if _num(it.get("f3")) > 5.0) / total
                if total > 0 else 0.0
            ),
        }
        self._constituent_cache[bk_code] = (today, stats)
        return stats

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
        ths_hot_sectors: Optional[dict[str, float]] = None,
        market_direction: str = "分化",
        all_scores_today: Optional[list[float]] = None,
    ) -> Optional[SectorDetail]:
        """对单个板块执行完整分析：强度 → RRG → 状态 → 分级 → 生命周期。

        Args:
            prev_trend: 前日 SQLite 趋势记录（用于连续性约束）
            ranked_indicators: 预计算的截面百分位排名指标（Node 2 v2）
            ths_hot_sectors: 同花顺板块热度字典 {板块名: 热度值}（辅助加成）
            all_scores_today: 当日全部板块 strength_score（供 RRG RS-Ratio 截面排名）
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

        # 1.5) 同花顺热度加成（辅助维度：高热度板块获得情绪加成）
        if ths_hot_sectors:
            sector_name = sector_info.get("name", "")
            heat_value = ths_hot_sectors.get(sector_name, 0.0)
            if heat_value > 0:
                # 热度加成：Top 10 板块 +5分，Top 20 +3分，Top 30 +1分
                # 热度值范围约 30k~150k，按分位加成
                if heat_value >= 100000:
                    heat_bonus = 5.0
                elif heat_value >= 60000:
                    heat_bonus = 3.0
                elif heat_value > 0:
                    heat_bonus = 1.0
                else:
                    heat_bonus = 0.0
                strength_score = min(100.0, strength_score + heat_bonus)
                logger.debug(f"[Node 2] {sector_name} 热度加成: +{heat_bonus} (热度={heat_value:.0f})")

        # 1.8) RRG 四象限分类（Node 2 v3 核心）
        rrg = self._compute_rrg(strength_score, prev_trend, all_scores_today)

        # 1.9) 提前计算生命周期阶段（供 D8 主线疲劳判定，完整 LifecycleInfo 在 step 6 构建）
        _quadrant_to_phase = {
            "leading": "acceleration",
            "weakening": "mature",
            "lagging": "late_stage",
            "improving": "recovery",
        }
        early_lifecycle_phase = _quadrant_to_phase.get(rrg.quadrant, "")

        # 2) 启动信号（降级为诊断指标，不再驱动状态）
        launch = self._detect_launch_signal(df, sector_info, market_direction=market_direction)

        # 3) 过热信号（v2: 9 维度，含高潮型检测）
        overheat = self._detect_overheat_signal(
            df, sector_info,
            prev_trend=prev_trend,
            lifecycle_phase=early_lifecycle_phase,
        )

        # 3.5) 过热日志（v2: 9 维度明细）
        sector_name_log = sector_info.get("name", "?")
        if overheat.severity > 0.3:
            logger.info(
                f"[Overheat] {sector_name_log}: severity={overheat.severity:.3f} ({overheat.level}), "
                f"D7_leader={overheat.leader_score:.2f}, D8_fatigue={overheat.fatigue_score:.2f}, "
                f"D9_chase={overheat.chase_score:.2f}, "
                f"D1_div={overheat.divergence_score:.2f}, D2_vol={overheat.volume_divergence_score:.2f}, "
                f"D3_breadth={overheat.breadth_score:.2f}"
            )

        # 4) 状态判定（RRG 驱动，替代旧的 8 分支优先级链）
        # 2026-08-31：返回 (state, exit_pressure, tier) 三元组
        # 2026-09-02：追加 break_signals（P2-5 落盘，供状态机决策离线复现）
        state, exit_pressure_days, tier, break_signals = self._determine_state_v3(
            strength_score, indicators, rrg,
            sector_info, market_context, df,
            prev_trend=prev_trend,
            market_direction=market_direction,
        )

        # 5) 主线分级（复用 _determine_state_v3 已计算的 tier）
        # 仅主线相关状态输出 tier，其余状态置 None
        if state not in ("active_mainline", "exhausted_mainline", "high_position_cooling"):
            tier = None

        # 6) 生命周期（RRG 象限直接映射；含 cooling 降温期）
        lifecycle: Optional[LifecycleInfo] = None
        if state in ("active_mainline", "exhausted_mainline", "high_position_cooling"):
            prev_days = prev_trend.get("mainline_days") if prev_trend else None
            lifecycle = self._classify_lifecycle_v3(rrg, prev_days)

        # 7) 推荐操作
        action: Optional[str] = None
        if state == "active_mainline" and rrg.quadrant in ("leading", "improving"):
            action = _LIFECYCLE_ACTION_MAP.get(
                lifecycle.phase if lifecycle else "mature",
            )
        elif state == "exhausted_mainline":
            action = "exit"
        elif state == "high_position_cooling":
            action = "reduce_or_hold"  # 高位降温：防新买入，持仓者减仓/持有

        # 8) 主线质量：B级标记为 weak（仅 active/exhausted 视为"主线质量"）
        mainline_quality: Optional[str] = None
        if state in ("active_mainline", "exhausted_mainline"):
            mainline_quality = "weak" if tier == "B" else "strong"

        # 9) 埋伏近失诊断：非 ambush/defensive 板块，差 1 条闸门 → 标记供 Node 6 观察
        near_miss: Optional[list[str]] = None
        if state not in ("ambush", "defensive_rotation"):
            is_def = self._check_defensive(sector_info, market_context, df, strength_score)
            if not is_def:
                fail_count, fail_list = self._check_ambush_near_miss(
                    df, indicators, rrg, strength_score, prev_trend, is_def,
                )
                if fail_count == 1:
                    near_miss = fail_list

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
            overheat_severity=overheat.severity,
            rrg=rrg,
            near_miss_conditions=near_miss,
            exit_pressure_days=exit_pressure_days,
            break_signals=break_signals,
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
    # RRG 四象限计算（Node 2 v3 状态机核心）
    # =================================================================

    @staticmethod
    def _ema_smooth(prev_value: Optional[float], raw_value: float, alpha: float = 0.5) -> float:
        """3 日 EMA 平滑（2026-08-17 状态机校准）。

        N=3 → alpha = 1 - exp(-2/(N+1)) ≈ 0.5；首日（无前值）取原始值。
        目的：吸收日频截面排名单日噪音——StockCharts 指出日频 RRG 是周频趋势的
        "短期 hiccup"，单日被其他板块挤压不应一次性翻转象限状态（黄金 08-13 教训）。
        """
        if prev_value is None:
            return raw_value
        return alpha * raw_value + (1.0 - alpha) * prev_value

    @staticmethod
    def _compute_rrg(
        strength_score: float,
        prev_trend: Optional[dict],
        all_scores_today: Optional[list[float]],
    ) -> RRGQuadrant:
        """RRG 四象限分类（替代旧的 launch/overheat/lifecycle 三套状态系统）。

        RS-Ratio: 当前 strength_score 在全截面中的百分位排名（趋势强度）
        RS-Momentum: strength_score 的时序变化率（动量方向）

        关键机制：动量领先趋势——momentum 转向是趋势转向的早期预警信号。
        - Improving（低 ratio + 高 momentum）→ 即将进入 Leading
        - Weakening（高 ratio + 低 momentum）→ 即将进入 Lagging

        2026-08-17 状态机校准：rs_ratio/rs_momentum 经 3 日 EMA 平滑后再判象限，
        原始值存 rs_ratio_raw/rs_momentum_raw 供诊断。

        论文依据：
        - Minervini 2016: Relative Rotation Graph 四象限
        - Janssen 2020: RS 动量领先 RS 趋势（早期预警）
        - Ekström et al.: 最优动量退出概率 P(θ≤t|data) 随持续性递增
        """
        # ── RS-Ratio: 截面百分位排名 ──
        if all_scores_today and len(all_scores_today) > 1:
            # 计算 strength_score 在全部板块中的百分位
            below = sum(1 for s in all_scores_today if s < strength_score)
            rs_ratio = below / len(all_scores_today)
        else:
            rs_ratio = 0.5  # 无截面数据时默认中位

        # ── RS-Momentum: 时序变化率 ──
        trajectory = prev_trend.get("strength_trajectory", []) if prev_trend else []
        if len(trajectory) >= 3:
            # 用 trajectory 的最后 3-5 天斜率估算 momentum
            recent = trajectory[-5:] if len(trajectory) >= 5 else trajectory[-3:]
            slope = (recent[-1] - recent[0]) / len(recent)
            # 归一化：±5分/天 → ±1.0，映射到 [0, 1]
            rs_momentum = max(0.0, min(1.0, 0.5 + slope / 10.0))
        else:
            rs_momentum = 0.5  # 无历史数据时默认中性

        # ── 3 日 EMA 平滑（吸收单日截面噪音，防"一夜蒸发"）──
        prev_ratio_ema = prev_trend.get("rrg_ratio_ema") if prev_trend else None
        prev_momentum_ema = prev_trend.get("rrg_momentum_ema") if prev_trend else None
        ratio_ema = SectorAnalysisNode._ema_smooth(prev_ratio_ema, rs_ratio)
        momentum_ema = SectorAnalysisNode._ema_smooth(prev_momentum_ema, rs_momentum)

        # ── 象限判定（用平滑值）──
        if ratio_ema >= 0.5 and momentum_ema >= 0.5:
            quadrant = "leading"
        elif ratio_ema < 0.5 and momentum_ema >= 0.5:
            quadrant = "improving"
        elif ratio_ema >= 0.5 and momentum_ema < 0.5:
            quadrant = "weakening"
        else:
            quadrant = "lagging"

        return RRGQuadrant(
            rs_ratio=round(ratio_ema, 3),
            rs_momentum=round(momentum_ema, 3),
            quadrant=quadrant,
            rs_ratio_raw=round(rs_ratio, 3),
            rs_momentum_raw=round(rs_momentum, 3),
        )

    # =================================================================
    # 启动信号: 3 硬门槛 + 5 维加权（Node 2 v3: 降级为诊断指标）
    # =================================================================

    def _detect_launch_signal(
        self, df: pd.DataFrame, sector_info: dict, market_direction: str = "分化",
    ) -> dict[str, Any]:
        """检测板块启动信号。

        硬门槛（全部通过才触发）：
        1. 突破: close[-1] > max(high[-21:-1]) × 1.005  # 20日突破 + 0.5% buffer
        2. 放量: volume[-1] > volume_ma5 × 1.5           # 5日均量 × 1.5
        3. 站上 MA20: close[-1] > ma20

        6 维软打分（新增 ad_confirmation）：
        consolidation(0.20) + volume_expansion(0.25) + price_strength(0.15)
        + trend_alignment(0.15) + breadth(0.10) + ad_confirmation(0.15)

        R1: Regime-Dependent 阈值自适应（Mokhtarian et al. 2024; Alexiou & Tyagi 2020）
        市场方向    启动门槛(strong)  启动门槛(weak)
        普涨(calm)     0.50              0.35
        分化(transition) 0.75              0.55
        普跌(turbulent)  0.80              0.65

        论文依据：
        - Brock et al. 1992: band filter 减少 whipsaw
        - IEEE 2026: <1.5x 量能视为假突破概率高
        - A/D Line 确认：突破真伪的核心判据（65-72% 准确率）
        """
        if len(df) < 20:
            return {"triggered": False, "level": "none", "score": 0.0, "dimensions": {}}

        close = df["close"]
        high = df["high"]
        volume = df["volume"]

        # --- 3 硬门槛 ---
        # L1: 突破 20 日高点 + 0.5% buffer（Brock et al. 1992: band filter）
        high_20d = high.iloc[-21:-1].max() if len(high) >= 21 else high.iloc[:-1].max()
        if close.iloc[-1] <= high_20d * 1.005:
            return {"triggered": False, "level": "none", "score": 0.0, "dimensions": {}}

        # L2: 量能确认: 当日成交量 ≥ 5 日均量 × 1.5（IEEE 2026: 假突破阈值）
        vol_ma5 = volume.iloc[-6:-1].mean() if len(volume) >= 6 else volume.iloc[:-1].mean()
        if vol_ma5 <= 0 or volume.iloc[-1] < vol_ma5 * 1.5:
            return {"triggered": False, "level": "none", "score": 0.0, "dimensions": {}}

        # 3. 站上 MA20
        ma20 = close.rolling(20).mean().iloc[-1]
        if close.iloc[-1] <= ma20:
            return {"triggered": False, "level": "none", "score": 0.0, "dimensions": {}}

        # --- 6 维软打分（各 0-1）---
        dims = {
            "consolidation": self._calc_consolidation_score(df),
            "volume_expansion": self._calc_volume_expansion_score(volume, vol_ma5),
            "price_strength": self._calc_price_strength_score(close, high, df["low"]),
            "trend_alignment": self._calc_trend_alignment_score(close),
            "breadth": self._calc_breadth_score(sector_info),
            "ad_confirmation": ad_line_confirmation(df),
        }

        # L6: 加权总分（新增 ad_confirmation 15% 权重）
        weights = {
            "consolidation": 0.20,
            "volume_expansion": 0.25,
            "price_strength": 0.15,
            "trend_alignment": 0.15,
            "breadth": 0.10,
            "ad_confirmation": 0.15,
        }
        score = sum(dims[k] * weights[k] for k in weights)

        # R1: Regime-Dependent 阈值
        if market_direction == "普涨":
            strong_threshold = 0.50
            weak_threshold = 0.35
        elif market_direction == "普跌":
            strong_threshold = 0.80
            weak_threshold = 0.65
        else:  # 分化
            strong_threshold = 0.75
            weak_threshold = 0.55

        if score >= strong_threshold:
            level = "strong_launch"
        elif score >= weak_threshold:
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
        """整理充分度: Bollinger 带宽百分位排名。

        L4 优化：改为布林带宽百分位（越低 = 越压缩 = 分数越高）
        论文依据：Bollinger squeeze 在 bottom 10-20th percentile 时突破爆发力最强
        """
        bb_pct = bb_width_percentile(df, period=20, lookback=252)
        # bb_pct 越低 → 越压缩 → 分数越高
        # bottom 20th → score=1.0, bottom 50th → score=0.0
        return clip(1.0 - bb_pct / 0.5, 0, 1)

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
        """均线排列度: MA20 斜率 + MA5/MA20 偏离度的加权。

        L5 优化：从简单排列顺序改为 MA 斜率，直接衡量动量方向和强度。
        理由：仅看 MA5>MA10>MA20 粒度太粗，斜率能区分强趋势和弱趋势。
        """
        if len(close) < 20:
            return 0.5

        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean()

        if pd.isna(ma5) or len(ma20.dropna()) < 6:
            return 0.5

        ma20_last = ma20.iloc[-1]
        ma20_5d_ago = ma20.iloc[-5] if len(ma20) >= 5 else ma20.iloc[0]

        if pd.isna(ma20_last) or pd.isna(ma20_5d_ago) or ma20_5d_ago <= 0:
            return 0.5

        # MA20 斜率：5日变化率，映射到 [-2%, +2%] → [0, 1]
        slope = (ma20_last - ma20_5d_ago) / ma20_5d_ago
        slope_score = clip((slope + 0.02) / 0.04, 0, 1)

        # MA5/MA20 偏离度：短期动量方向
        gap = (ma5 - ma20_last) / ma20_last if ma20_last > 0 else 0
        gap_score = clip((gap + 0.02) / 0.04, 0, 1)

        return 0.6 * slope_score + 0.4 * gap_score

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
    # 过热信号: 渐进式评分（替代旧的 4 条件二元触发）
    # =================================================================

    def _detect_overheat_signal(
        self, df: pd.DataFrame, sector_info: dict,
        prev_trend: Optional[dict] = None,
        lifecycle_phase: str = "",
    ) -> OverheatSeverity:
        """检测板块过热信号 — 渐进式严重度评分（v2: 9 维度）。

        每个信号输出 0~1 连续分数，加权汇总为 severity。
        severity > 0.5 → exhausted（踢出主线框）
        severity 0.3~0.5 → warning（过热警告，降 confidence）
        severity < 0.3 → normal

        9 维度：
        - D1-D6: 背离型见顶（前置条件: price_near_peak）
        - D7-D9: 高潮型见顶（无前置条件限制）

        Args:
            df: 板块 K 线 DataFrame
            sector_info: 板块信息字典
            prev_trend: 前日趋势记录（用于 D8 主线疲劳判定）
            lifecycle_phase: 生命周期阶段（acceleration/mature/late_stage 等）

        前置条件：背离型维度仅在板块距 60d 高点 ≤10% 时有意义

        论文依据：
        - MACD 背离渐进式（Kang 2023）
        - A/D Line distribution 出现在 72% 重大下跌前（TradeAlgo 2026）
        - 天量滞涨 = 分配信号（Hsu 2016）
        - 极端宽度（>top 15%）后 20 日收益显著低于正常宽度（Harvard SSRN）
        - Minervini 领涨股恶化模式（龙头极端强势 = 高潮信号）
        - O'Neil Stall Day（高位放量滞涨 = 机构派发）
        """
        if len(df) < 20:
            return OverheatSeverity()

        close = df["close"]

        # 60d 高位判定（背离型维度 D1-D6 的前置条件）
        lookback_60d = min(60, len(close))
        high_60d = close.iloc[-lookback_60d:].max()
        price_near_peak = close.iloc[-1] >= high_60d * 0.90
        # v2 修正：非高位不再早退——D1-D6 归零，D7-D9（高潮型）照常计算，
        # 高潮型见顶可发生在板块未近 60d 高点时（计划 §3.3）

        # 20d 价格新高
        price_at_20d_high = close.iloc[-1] >= close.iloc[-20:].max()

        # ── 1. MACD 背离（渐进式，替代旧的 DIF < 前高×0.8 二元判定）──
        divergence_score = 0.0
        if price_near_peak and price_at_20d_high and len(df) >= 30:
            macd_df = calculate_macd(df)
            dif = macd_df["MACD_DIF"]
            dea = macd_df["MACD_DEA"]
            dif_20d_high = dif.iloc[-20:-1].max() if len(dif) >= 21 else dif.iloc[:-1].max()
            if dif_20d_high > 0:
                # DIF 下跌幅度占前高的比例（0~1）
                dif_decline_ratio = (dif_20d_high - dif.iloc[-1]) / dif_20d_high
                # MACD 死叉加分（DIF < DEA = 动能已转负）
                signal_cross = 1.0 if dif.iloc[-1] < dea.iloc[-1] else 0.0
                divergence_score = clip(dif_decline_ratio * 0.7 + signal_cross * 0.3, 0, 1)

        # ── 2. 量能背离（渐进式，替代旧的 volume < 前高×0.7 二元判定）──
        volume_divergence_score = 0.0
        if price_near_peak and price_at_20d_high and len(df) >= 20:
            price_high_idx = close.iloc[-20:].idxmax()
            volume = df["volume"]
            vol_at_price_high = volume.loc[price_high_idx] if price_high_idx in volume.index else 0
            if vol_at_price_high > 0:
                vol_ratio = volume.iloc[-1] / vol_at_price_high
                # 量能越不足前高，分数越高
                if vol_ratio < 0.7:
                    volume_divergence_score = clip((0.7 - vol_ratio) / 0.5, 0.3, 1.0)
                elif vol_ratio < 1.0:
                    volume_divergence_score = clip((1.0 - vol_ratio) / 0.3 * 0.3, 0, 0.3)

        # ── 3. 宽度极端（自适应百分位，替代旧的 up/total > 0.8 硬阈值）──
        breadth_score = 0.0
        up = sector_info.get("up_count", 0)
        down = sector_info.get("down_count", 0)
        total = up + down
        if price_near_peak and total > 0:
            today_breadth = up / total
            # 用板块历史宽度序列做百分位排名
            # 注：板块级 breadth 数据来自 mootdx K 线，此处用简化逻辑：
            # 直接按 today_breadth 值分段评分（0.8→0.5, 0.9→0.8, 0.95→1.0）
            if today_breadth >= 0.95:
                breadth_score = 1.0
            elif today_breadth >= 0.90:
                breadth_score = 0.8
            elif today_breadth >= 0.85:
                breadth_score = 0.5
            elif today_breadth >= 0.80:
                breadth_score = 0.3

        # ── 4. 换手率极端（保留原有逻辑）──
        turnover_score = 0.0
        turnover = sector_info.get("turnover_rate", 0.0)
        if price_near_peak and turnover > 0 and len(df) >= 30:
            amount_series = df["amount"].iloc[-60:] if len(df) >= 60 else df["amount"]
            if len(amount_series) > 10:
                p90 = amount_series.quantile(0.9)
                if df["amount"].iloc[-1] > p90:
                    # 渐进式：超过 P90 越多分数越高
                    excess_ratio = (df["amount"].iloc[-1] - p90) / p90
                    turnover_score = clip(0.5 + excess_ratio * 0.5, 0.5, 1.0)
                elif df["amount"].iloc[-1] > amount_series.quantile(0.75):
                    turnover_score = 0.3

        # ── 5. A/D Line 分配信号（背离型，前置条件：高位）──
        ad_dist = ad_distribution_signal(df) if price_near_peak else False

        # ── 6. 天量滞涨（背离型，前置条件：高位）──
        vol_stall = False
        if price_near_peak and len(df) >= 20 and "amount" in df.columns:
            amount_20d_high = df["amount"].iloc[-20:].max()
            if df["amount"].iloc[-1] >= amount_20d_high * 0.95:
                recent_returns = df["close"].pct_change().tail(5).mean()
                today_return = df["close"].iloc[-1] / df["close"].iloc[-2] - 1
                vol_stall = bool(recent_returns > 0 and today_return < recent_returns * 0.3)

        # ── 7. 龙头极端行为（D7，高潮型，无前置条件）──
        # 优先用预取的真实龙头涨幅；缺失时回退板块 today_change 代理（降级）
        # 耦合条件：仅当 mainline_days >= 3 才计分（启动期龙头涨停是加速起点非高潮）
        # 仅统计上涨：暴跌板块不得计入过热分（过热语义只针对上涨亢奋）
        leader_score = 0.0
        mainline_days = (prev_trend or {}).get("mainline_days", 0)
        today_change = sector_info.get("today_change", 0) or 0
        max_stock_pct = sector_info.get("max_stock_change_pct", 0) or 0
        if mainline_days >= 3:
            if max_stock_pct > 0:
                # 真实成分股数据：涨停阈值按龙头股板块前缀区分（主板10/创科20/北交30）
                limit_pct = self._limit_up_pct_for_code(
                    sector_info.get("max_stock_code", ""),
                )
                if max_stock_pct >= limit_pct - 0.2:      # 龙头涨停（同 _is_limit_up 口径）
                    leader_score = 1.0
                elif max_stock_pct >= limit_pct * 0.8:    # 龙头接近涨停
                    leader_score = 0.7
                elif max_stock_pct >= limit_pct * 0.5:    # 龙头大涨
                    leader_score = 0.3
            elif today_change > 0:
                # 代理口径（预取失败降级）：板块涨幅 ≈ 龙头涨幅 / 1.5~2.0
                if today_change >= 3.5:
                    leader_score = 1.0
                elif today_change >= 2.5:
                    leader_score = 0.7
                elif today_change >= 1.5:
                    leader_score = 0.3

        # ── 8. 主线疲劳（D8，高潮型，无前置条件）──
        # mainline_days ≥ 5 且仍标 acceleration → 生命周期滞后惩罚
        fatigue_score = 0.0
        if mainline_days >= 7 and lifecycle_phase == "acceleration":
            fatigue_score = 1.0
        elif mainline_days >= 5 and lifecycle_phase == "acceleration":
            fatigue_score = 0.6
        elif mainline_days >= 5:
            fatigue_score = 0.3

        # ── 9. 成分股追高（D9，高潮型，无前置条件）──
        # 优先用预取的真实占比 pct_stocks_up_gt5；缺失时回退板块宽度代理（降级）
        chase_score = 0.0
        pct_gt5 = sector_info.get("pct_stocks_up_gt5", 0) or 0
        if pct_gt5 > 0:
            if pct_gt5 >= 0.5:            # 半数以上成分股涨 >5%
                chase_score = 1.0
            elif pct_gt5 >= 0.3:
                chase_score = 0.6
            elif pct_gt5 >= 0.15:
                chase_score = 0.3
        else:
            breadth_ratio = up / total if total > 0 else 0.0
            if breadth_ratio >= 0.95 and today_change >= 2.0:
                chase_score = 1.0         # 宽度极端 + 板块大涨 = 追高过热
            elif breadth_ratio >= 0.90 and today_change >= 1.5:
                chase_score = 0.6
            elif breadth_ratio >= 0.85 and today_change >= 1.0:
                chase_score = 0.3

        # ── 加权汇总（v2 Phase 2: 三案例标定后权重，写入 src/config，计划 §3.2/§6）──
        # 标定结果：煤炭 0831 高位场景 → warning；种业 mature/正常加速/启动期涨停 → normal
        from src.config import get_config
        cfg = get_config()
        severity = (
            divergence_score * cfg.overheat_weight_divergence
            + volume_divergence_score * cfg.overheat_weight_volume_divergence
            + breadth_score * cfg.overheat_weight_breadth
            + turnover_score * cfg.overheat_weight_turnover
            + (1.0 if ad_dist else 0.0) * cfg.overheat_weight_ad
            + (1.0 if vol_stall else 0.0) * cfg.overheat_weight_vol_stall
            + leader_score * cfg.overheat_weight_leader
            + fatigue_score * cfg.overheat_weight_fatigue
            + chase_score * cfg.overheat_weight_chase
        )

        if severity > 0.5:
            level = "exhausted"
        elif severity > 0.3:
            level = "warning"
        else:
            level = "normal"

        return OverheatSeverity(
            divergence_score=round(divergence_score, 3),
            volume_divergence_score=round(volume_divergence_score, 3),
            breadth_score=round(breadth_score, 3),
            turnover_score=round(turnover_score, 3),
            ad_distribution=ad_dist,
            volume_stall=vol_stall,
            leader_score=round(leader_score, 3),
            fatigue_score=round(fatigue_score, 3),
            chase_score=round(chase_score, 3),
            severity=round(severity, 3),
            level=level,
        )

    # =================================================================
    # 状态判定（RRG 四象限驱动，Node 2 v3）
    # =================================================================

    def _compute_break_signals(
        self, df: pd.DataFrame, sector_info: dict,
        prev_trend: Optional[dict] = None,
        lifecycle_phase: str = "",
    ) -> tuple[Optional[float], bool, float, str]:
        """计算状态机的破位/未破位判据信号。

        返回 (price_chg_5d, above_ma20, high20_pos, overheat_level)。
        price_chg_5d 用真实 5 日涨幅（不随当日截面排名变），是区分
        high_position_cooling（回调未破位）与 exhausted_mainline（破位）的主判据。
        """
        if df is None or len(df) == 0:
            return None, False, 1.0, "normal"
        close = df["close"]
        # 全部转 native 类型：pandas 比较/除法产出 numpy 标量，沿 context 泄漏进 Phase 2 会崩序列化
        price_chg_5d = float(close.iloc[-1] / close.iloc[-5] - 1.0) if len(close) >= 5 and close.iloc[-5] > 0 else 0.0
        above_ma20 = bool(
            close.iloc[-1] > close.rolling(20).mean().iloc[-1]
            if len(close) >= 20 else False
        )
        if len(close) >= 20 and max(close.iloc[-20:]) > 0:
            high20_pos = float(close.iloc[-1] / max(close.iloc[-20:]))
        else:
            high20_pos = 1.0
        overheat = self._detect_overheat_signal(
            df, sector_info,
            prev_trend=prev_trend,
            lifecycle_phase=lifecycle_phase,
        )
        return price_chg_5d, above_ma20, high20_pos, overheat.level

    @staticmethod
    def _compute_fast_signals(df: pd.DataFrame) -> tuple[Optional[float], float]:
        """计算 FAST 信号（Goulding et al. 2024 思路）

        FAST 信号用于检测 Rebound（降温后重新上攻），绕过 RRG 的 EMA 滞后。

        Returns:
            (fast_3d_change, volume_ratio):
            - fast_3d_change: 3日累计涨幅(%)，用于检测短期爆发
            - volume_ratio: 量比（今日成交量/5日均量），用于确认资金参与
        """
        fast_3d_change: Optional[float] = None
        volume_ratio = 1.0

        if df is None or len(df) < 5:
            return fast_3d_change, volume_ratio

        try:
            close = df["close"]
            volume = df.get("volume")

            # 3日累计涨幅
            if len(close) >= 4:
                fast_3d_change = ((close.iloc[-1] / close.iloc[-4]) - 1) * 100

            # 量比：今日成交量 / 5日均量
            if volume is not None and len(volume) >= 5:
                vol_today = volume.iloc[-1]
                vol_avg_5d = volume.iloc[-6:-1].mean() if len(volume) >= 6 else volume.iloc[:-1].mean()
                if vol_avg_5d > 0:
                    volume_ratio = vol_today / vol_avg_5d

        except Exception:
            pass

        return fast_3d_change, volume_ratio

    @staticmethod
    def _apply_state_machine(
        prev_state: Optional[str],
        prev_dwell: int,
        q: str,
        rs_momentum: float,
        strength_score: float,
        price_chg_5d: Optional[float],
        above_ma20: bool,
        high20_pos: float,
        overheat_level: str,
        today_change: Optional[float],
        score_drop: float,
        is_ambush: bool,
        is_defensive: bool,
        fast_3d_change: Optional[float] = None,
        fast_volume_ratio: float = 1.0,
        exit_pressure: int = 0,
        tier: Optional[str] = None,
    ) -> str:
        """规则版马尔可夫状态转移矩阵（2026-08-17 文献校准，替代 P0-P5 优先级链）。

        设计依据：
        - 状态持久性：市场 regime 自转移概率 0.89-0.99（Liu&Xu&Zhao 2010）
        - 转移路径固定：RRG 实证 Leading 92% 转 Weakening（Optuma 2万次旋转）→
          主线只能经 高位降温/衰竭 退出，**不存在 主线→埋伏 的边**
        - 切换需驻留/置信度门槛（HMM 后验>0.8, Procida 2026），除硬触发外
        - 硬触发 HI1：单日 score 崩≥20 → 立即降级；HI2：单日暴涨>8%+leading → 立即进主线
        - 2026-08-24 移除 HI3（大盘暴跌全局拦截）：职责与输出层主线裁剪重复，
          且破坏状态机马尔可夫性（未破位板块也被强制踢出，回主线需重走 2 天确认）。
          暴跌日的风险控制已统一到 attack_power → 主线裁剪层
          （5 档阶梯：<0.15→1 / <0.30→2 / <0.50→3 / <0.70→4 / ≥0.70→5）。
        - 2026-08-31 吸收主线框退出逻辑：exit_pressure 计数器替代 consecutive_low_days。
          S/A 容忍 3 天、B 容忍 1 天；优先级在 broken 之后、E1 驻留之前。

        破位/未破位判据（区分 高位降温(回调未坏) vs 衰竭(破位)）：
          BROKEN = OR(5日涨幅≤0, 跌破MA20, 过热exhausted且20日高位<0.60, 单日崩≥20,
                      单日暴跌≤-4%)  ← 2026-09-02 P0-3 新增
        """
        # HI2 硬触发：单日暴涨>8% + leading + score>70 → 当日进 active（豁免驻留）
        if today_change is not None and today_change > 8.0 and q == "leading" and strength_score > 70:
            return "active_mainline"

        # 破位判据（含 HI1 单日崩≥20 自然落位）
        # 注：单日暴跌 < -4% 不在此处判定。跨 29 日校准（2026-09-02 审核）显示，
        # active_mainline 池中 3 次命中（黄金 -5.88% / 种业 -7.14% / 粮食概念 -4.26%），
        # 其中黄金、种业为真实主线单日回调，若在此并入 broken 会走 E3 当天踢出，
        # 跳过 high_position_cooling 缓冲 → 误伤。改由主线分支的 _ONE_DAY_DROP 专用逻辑处理。
        broken = bool(
            (price_chg_5d is not None and price_chg_5d <= 0)
            or (not above_ma20)
            or (overheat_level == "exhausted" and high20_pos < 0.60)
            or (score_drop >= 20)
        )

        # 单日暴跌信号（P0-3）：强度证据尚存时只降温观察，趋势同步破坏才驱逐
        one_day_drop = (
            today_change is not None
            and today_change < SectorAnalysisNode._BROKEN_ONE_DAY_DROP_PCT
        )
        # 趋势破坏佐证：价与结构同时转弱（5日涨幅为负 或 跌破MA20），
        # 单日暴跌本身不足以证明趋势终结（黄金 08-11 -5.88% 后仍在主线）
        trend_damaged = (price_chg_5d is not None and price_chg_5d <= 0) or (not above_ma20)

        # ── 冷启动 / neutral：入口探测 ──
        if prev_state in (None, "neutral"):
            if is_ambush:
                return "ambush"
            if not broken and q == "leading":
                # 入口门槛分档：S/A > 60，B >= 80（吸收原框入框门槛，2026-08-31）
                # B 级用 >= 对齐原框 "score < min_score 才拒绝" 语义，80 分恰好入主线
                if tier in ("S", "A"):
                    entry_ok = strength_score > 60
                else:
                    entry_ok = strength_score >= 80
                if entry_ok:
                    return "active_mainline"
            if is_defensive:
                return "defensive_rotation"
            return "neutral"

        # ── 主线：只有 驻留 / 高位降温 / 衰竭 三条路 ──
        if prev_state == "active_mainline":
            # P0-3 单日暴跌：结构性修复 S/A-tier exit_pressure 上限恒 2 < 容忍度 3
            # 的驱逐路径锁死。分级处理（跨 29 日校准，避免误伤真实回调）：
            #   暴跌 + 趋势破坏 → 当天驱逐（粮食概念 09-02 -4.26% 且 breadth 0.133 下行）
            #   暴跌但趋势尚存 → 降温观察（黄金 08-11 -5.88% 属主线深回调）
            # 降温后若次日再破位，由 high_position_cooling 分支 E7 转 exhausted。
            if one_day_drop and trend_damaged:
                return "exhausted_mainline"
            if one_day_drop:
                return "high_position_cooling"
            if broken:
                return "exhausted_mainline"       # E3 破位当天踢出
            # 退出压力驱逐（吸收原框 consecutive_low_days 逻辑，2026-08-31）
            # S/A 容忍 3 天，B 容忍 1 天（原框 _MAINLINE_STRONG/WEAK_TOLERANCE_DAYS）
            pressure_tolerance = 3 if tier in ("S", "A") else 1
            if exit_pressure >= pressure_tolerance:
                return "exhausted_mainline"       # 退出压力累积超限→衰竭
            if q in ("leading", "improving") and strength_score > 55:
                return "active_mainline"          # E1 驻留
            if q == "weakening":
                return "high_position_cooling"    # E2 转弱未破位 → 高位降温
            return "active_mainline"              # 其它中性情况保守驻留

        # ── 高位降温（前主线回调未破位）：修复需 dwell≥2 confirm ──
        if prev_state == "high_position_cooling":
            # HI4 再启动快速通道（同衰竭分支，A股适配：单日涨>3% + 强度>80）
            fast_rebound = (
                today_change is not None
                and today_change > 3.0
                and strength_score > 80
            )
            if fast_rebound:
                return "active_mainline"          # HI4 Rebound 确认

            if broken:
                return "exhausted_mainline"       # E7 破位
            repaired = (
                q in ("leading", "improving")
                and strength_score > 55
                and (price_chg_5d is None or price_chg_5d > 0)
            )
            if repaired and prev_dwell >= 2:
                return "active_mainline"          # E6 驻留确认后回主线
            if strength_score < 45:
                return "neutral"                  # E8 低分熄灭
            return "high_position_cooling"        # E5 驻留（未到确认期）

        # ── 衰竭：观察期上限3天，修复需连续2天 confirm ──
        if prev_state == "exhausted_mainline":
            # HI4 再启动快速通道（Goulding et al. 2024: FAST signal after Correction）
            # A股适配：用单日涨幅（today_change）替代3日累计（板块单日脉冲特征）
            # 砍掉量比门槛（mootdx板块量比受成分股加总噪声影响，不可靠）
            # 阈值：单日涨>3% + 强度>80 = Rebound 确认
            fast_rebound = (
                today_change is not None
                and today_change > 3.0
                and strength_score > 80
            )
            if fast_rebound:
                return "active_mainline"          # HI4 Rebound 确认，不等 RRG 翻转

            # 原有路径：RRG + 2天冷却期
            if (
                not broken
                and q == "leading"
                and strength_score > 60
                and (today_change or 0) > 0
            ):
                if prev_dwell >= 2:
                    return "active_mainline"      # E11 连续2天确认后回主线
                return "exhausted_mainline"       # 未到确认期，驻留观察
            if score_drop >= 20 or strength_score < 50 or broken:
                if prev_dwell >= 3:
                    return "neutral"              # E10 驻留超限 → 熄灭
                return "exhausted_mainline"       # E9 驻留观察
            return "exhausted_mainline"           # E9 继续观察

        # ── 埋伏：低位+动量转正蓄势，只能经 leading 突破上位 ──
        if prev_state == "ambush":
            # E12b 飞刀驱逐（硬退出，不依赖 is_ambush）
            # 修复前：E14 要求 not is_ambush，但"越低越好"维度让崩塌板块
            # is_ambush 恒 True → 锁死在 ambush。半导体 08-24~25 因此无法退出。
            # 触发条件：动量严重恶化（<0.2）+ 强度极低（<25）= 明确崩塌，非蓄势。
            if rs_momentum < 0.2 and strength_score < 25:
                return "neutral"

            if not is_ambush and q == "leading" and strength_score > 60:
                if prev_dwell >= 1:
                    return "active_mainline"      # E13 突破上位
                return "ambush"
            if not is_ambush and (rs_momentum < 0.5 or strength_score < 50):
                if prev_dwell >= 2:
                    return "neutral"              # E14 动量转负/低分连续 → 出埋伏
                return "ambush"
            return "ambush"                       # E12 驻留

        # ── 防守轮动：正交于 RRG ──
        if prev_state == "defensive_rotation":
            if is_defensive:
                return "defensive_rotation"       # E15 驻留
            return "neutral"                      # E16 条件转假

        return "neutral"

    @staticmethod
    def _compute_exit_pressure(
        prev_state: Optional[str],
        prev_exit_pressure: int,
        strength_score: float,
        today_change: Optional[float],
    ) -> int:
        """退出压力计数器（吸收原框 consecutive_low_days，2026-08-31）。

        仅对前日为主线（active_mainline）的板块累计——等价原框只跟踪框内成员；
        入口当天 score 必 >60/>=80（≥50），次日累计自然从 0 起算，无垃圾压力携带。
        规则：score<50 → prev+1，否则归 0；T-0 止损叠加 <-3% +2 / <-1.5% +1。
        日粒度幂等由存储层保证（同日重跑覆盖同日记录，不重复累计）。
        """
        if prev_state != "active_mainline":
            return 0
        if strength_score < SectorAnalysisNode._MAINLINE_EXIT_SCORE_THRESHOLD:
            pressure = prev_exit_pressure + 1
        else:
            pressure = 0
        if today_change is not None:
            if today_change < -3.0:
                pressure += 2
            elif today_change < -1.5:
                pressure += 1
        return pressure

    def _determine_state_v3(
        self,
        strength_score: float,
        indicators: dict[str, float],
        rrg: RRGQuadrant,
        sector_info: dict,
        market_context: MarketContext,
        df: pd.DataFrame,
        prev_trend: Optional[dict] = None,
        market_direction: str = "分化",
    ) -> tuple[str, int, str, dict]:
        """规则版马尔可夫状态机驱动（2026-08-17 文献校准，替代 P0-P5 优先级链）。

        核心变更：
        - 状态记忆：读 prev_trend["state"]（精确六态快照，替代 unified_signal 三值映射，
          修复根因 #3"主线记忆易断开"）
        - 转移矩阵：_apply_state_machine 定义合法边，主线只能经 高位降温/衰竭 退出，
          禁止 主线→埋伏 直接跳；切换需驻留确认（硬触发豁免）
        - 埋伏判据：_is_ambush_candidate（低位+动量刚转正+结构吸筹），
          替代旧 _check_momentum_ambush"score>阈值即埋伏"（根因 #2）
        - 破位判据 price_chg_5d 用真实 5 日涨幅，不随当日截面排名变
          （修复根因 #1"一夜蒸发"，黄金 08-13 不再直接消失）
        - 2026-08-31：返回 (state, exit_pressure_days) 元组，
          exit_pressure 吸收原框 consecutive_low_days 逻辑到状态机。

        Returns:
            (new_state, exit_pressure_days, tier, break_signals)
            break_signals 为破位判据输入快照（P2-5），随 SectorDetail 落盘，
            使 broken 的 OR 链可在归档数据上离线复现。
        """
        prev_state = (prev_trend or {}).get("state")
        prev_dwell = int((prev_trend or {}).get("dwell_days") or 0)
        prev_exit_pressure = int((prev_trend or {}).get("exit_pressure_days") or 0)
        q = rrg.quadrant

        # ── 退出压力计数器（吸收原框逻辑，2026-08-31）──
        # 日粒度幂等由存储层保证（同日重跑覆盖同日记录，不重复累计）
        exit_pressure = self._compute_exit_pressure(
            prev_state, prev_exit_pressure, strength_score,
            sector_info.get("today_change"),
        )

        # 破位/未破位判据信号
        # 提前计算 lifecycle_phase（与 _analyze_single_sector 中 early_lifecycle_phase 同逻辑）
        _quad_to_phase = {"leading": "acceleration", "weakening": "mature", "lagging": "late_stage", "improving": "recovery"}
        _lc_phase = _quad_to_phase.get(rrg.quadrant, "")
        price_chg_5d, above_ma20, high20_pos, overheat_level = self._compute_break_signals(
            df, sector_info,
            prev_trend=prev_trend,
            lifecycle_phase=_lc_phase,
        )

        # FAST 信号（保留用于其他用途，HI4 改用 today_change）
        fast_3d_change, fast_volume_ratio = self._compute_fast_signals(df)
        # 调试日志：HI4 Rebound 检测
        if sector_info.get("name"):
            tc = sector_info.get("today_change")
            logger.debug(
                f"[HI4] {sector_info['name']}: today_change={tc}, "
                f"score={strength_score:.1f}, "
                f"would_trigger={tc is not None and tc > 3.0 and strength_score > 80}"
            )

        # 单日分数回落（硬触发 HI1 信号源）
        trajectory = (prev_trend or {}).get("strength_trajectory", [])
        prev_score = trajectory[-1] if trajectory else strength_score
        score_drop = prev_score - strength_score

        # 防守 + 埋伏（新判据）
        is_defensive = self._check_defensive(sector_info, market_context, df, strength_score)
        is_ambush = self._is_ambush_candidate(
            df, indicators, rrg, strength_score, prev_trend, is_defensive,
        )

        # 提前计算 tier（供状态机入口门槛 + 退出容忍度分档）
        tier = self._classify_mainline_tier(sector_info, indicators)

        state = self._apply_state_machine(
            prev_state=prev_state,
            prev_dwell=prev_dwell,
            q=q,
            rs_momentum=rrg.rs_momentum,
            strength_score=strength_score,
            price_chg_5d=price_chg_5d,
            above_ma20=above_ma20,
            high20_pos=high20_pos,
            overheat_level=overheat_level,
            today_change=sector_info.get("today_change"),
            score_drop=score_drop,
            is_ambush=is_ambush,
            is_defensive=is_defensive,
            fast_3d_change=fast_3d_change,
            fast_volume_ratio=fast_volume_ratio,
            exit_pressure=exit_pressure,
            tier=tier,
        )

        # 破位判据输入快照（P2-5 落盘：broken = OR(price_chg_5d<=0, 跌破MA20,
        # overheat exhausted 且 high20_pos<0.60, 单日崩>=20, 单日暴跌<-4%)）
        # 强制转 native 类型：above_ma20 是 numpy 比较结果（np.bool_），
        # round(np.float64) 也保持 numpy 标量，袋子里混 numpy 会导致 Phase 2 序列化崩溃
        break_signals = {
            "price_chg_5d": round(float(price_chg_5d), 4) if price_chg_5d is not None else None,
            "above_ma20": bool(above_ma20),
            "high20_pos": round(float(high20_pos), 4),
            "overheat_level": overheat_level,
            "score_drop": round(float(score_drop), 2),
        }
        return state, exit_pressure, tier, break_signals

    # =================================================================
    # 埋伏评分化（P0-1: 6 维加权 + 排名截断，替代 6 AND 硬闸门）
    # =================================================================

    # 评分常量（TODO: 待校准，当前样本量: 0）
    AMBUSH_TOP_K = 8                       # Track 2 补位名额上限
    AMBUSH_TRACK1_MAX = 16                 # Track 1 单独上限（2×TOP_K），防状态机膨胀
    AMBUSH_SCORE_FLOOR = 0.30              # 最低分下限（仅约束 Track 2 补位）
    AMBUSH_NEAR_MISS_RATIO = 0.7           # 近失下限 = floor × ratio

    # 6 维权重（和 = 1.0）
    _AMBUSH_W_RS_RATIO = 0.20              # 截面相对强度低位
    _AMBUSH_W_RS_MOMENTUM = 0.15           # 动量转正
    _AMBUSH_W_TURNAROUND = 0.15            # 动量刚转正窗口
    _AMBUSH_W_ACCUMULATION = 0.25          # 结构吸筹（最高权重）
    _AMBUSH_W_VOLUME_POSITION = 0.15       # 量价位置（低位放量=吸筹）
    _AMBUSH_W_STRENGTH_CAP = 0.10          # 强度上限（越低=空间越大）

    @staticmethod
    def _score_rs_ratio(rs_ratio: float) -> float:
        """RS-Ratio 低位评分：越低位 → 分数越高（埋伏起点在 improving 前夜）。

        rs_ratio ∈ [0, 1]，0.55 以下给分，0 给满分，0.7 以上零分。
        """
        if rs_ratio >= 0.70:
            return 0.0
        return max(0.0, 1.0 - rs_ratio / 0.70)

    @staticmethod
    def _score_rs_momentum(rs_momentum: float) -> float:
        """RS-Momentum 评分：动量已转正 → 给分。

        rs_momentum ∈ [0, 1]，0.3 以下零分（趋势仍在恶化），0.65 给半分，1.0 满分。
        """
        if rs_momentum <= 0.30:
            return 0.0
        return min(1.0, (rs_momentum - 0.30) / 0.70)

    @staticmethod
    def _score_turnaround(momentum_trajectory: list[float]) -> float:
        """动量刚转正窗口评分：检测从 <0.5 上穿到 ≥0.5 的翻转点。

        设计意图：捕捉"蓄势待涨"的起点——动量刚从弱势区（<0.5）翻入强势区（≥0.5）。
        区分三种场景：
          ① 刚上穿（1-3 天内）→ 高分（0.4~1.0），最佳埋伏起点
          ② 从未上穿（全程 <0.5）→ 0 分（"从未转正"≠"刚转正"，是飞刀不是蓄势）
          ③ 早已上穿（>3 天）或持续 >0.5 → 低分（已不是起点了）

        修复前（v3.3.1）：below_count/len(recent)，全程<0.5拿满分，半导体08-25
        因此误入ambush（momentum从0.726崩到0.106，turnaround反给1.0满分）。
        """
        traj = [m for m in (momentum_trajectory or []) if m is not None]
        if len(traj) < 2:
            return 0.0  # 数据不足，无法判定上穿，不给分（冷启动惩罚由外层处理）

        # 从末尾往前找最近一次上穿点（<0.5 → ≥0.5）
        # days_ago = 1 表示昨天发生上穿
        crossover_days_ago = None
        for i in range(len(traj) - 1, 0, -1):
            days_ago = len(traj) - 1 - i
            if days_ago > 5:
                break  # 只看最近 5 天
            if traj[i] >= 0.5 and traj[i - 1] < 0.5:
                crossover_days_ago = days_ago
                break

        if crossover_days_ago is None:
            return 0.0  # 无上穿 → 零分（飞刀场景）

        # 按距今天数衰减：1天前=1.0, 2天前=0.7, 3天前=0.4, 4+天前=0.1
        decay_map = {1: 1.0, 2: 0.7, 3: 0.4}
        return decay_map.get(crossover_days_ago, 0.1)

    @staticmethod
    def _score_volume_position(df: pd.DataFrame) -> float:
        """量价位置评分（国联民生命量张力因子思路）。

        低位放量 = 吸筹（高分），高位放量 = 出货（低分）。
        价格位置：当前价在 60 日高低区间的位置（0=最低, 1=最高）。
        量能变化：近 5 日均量 / 20 日均量（>1 = 放量）。
        """
        if len(df) < 20:
            return 0.5  # 数据不足给中间分

        close = df["close"]
        volume = df["volume"]

        # 价格位置（60 日高低区间百分位）
        lookback = min(60, len(close))
        recent_close = close.iloc[-lookback:]
        price_low = recent_close.min()
        price_high = recent_close.max()
        price_range = price_high - price_low
        if price_range <= 0:
            return 0.5
        price_position = (close.iloc[-1] - price_low) / price_range  # 0~1

        # 量能变化
        vol_5 = volume.iloc[-5:].mean() if len(volume) >= 5 else volume.mean()
        vol_20 = volume.iloc[-20:].mean() if len(volume) >= 20 else volume.mean()
        vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

        # 低位放量 = 吸筹（高分），高位放量 = 出货（低分）
        # score = (1 - price_position) * volume_expansion
        vol_expansion = min(2.0, vol_ratio) / 2.0  # 归一化到 0~1
        score = (1.0 - price_position) * vol_expansion
        return max(0.0, min(1.0, score))

    @staticmethod
    def _score_strength_cap(strength_score: float) -> float:
        """强度上限评分：强度越低 → 上涨空间越大 → 埋伏分越高。

        strength_score ∈ [0, 100]，0 给满分，80 以上零分。
        """
        if strength_score >= 80.0:
            return 0.0
        return max(0.0, 1.0 - strength_score / 80.0)

    # 冷启动渐入天数（轨迹不足此值 → 评分打折，防止 RRG 默认值拿高分）
    _AMBUSH_COLDSTART_WARMUP_DAYS = 5

    @classmethod
    def _compute_ambush_score_v2(
        cls,
        df: pd.DataFrame,
        indicators: dict[str, float],
        rrg: "RRGQuadrant",
        strength_score: float,
        prev_trend: Optional[dict],
    ) -> float:
        """6 维加权埋伏评分（0~1）。替代 _is_ambush_candidate 的 6 AND 硬闸门。

        论文依据：
        - 截面百分位排名自适应牛熊市（Western Securities RRG）
        - 低位放量 = 知情资金吸筹（国联民生, Sharpe=3.32）
        - 波动率压缩 + 量能异常 → 启动概率最高（spike_scanner）

        2026-08-25 修正（08-24 回归暴露两个致命缺陷）：
        - 病因B 冷启动惩罚：RRG 轨迹不足 _WARMUP_DAYS 天 → 线性渐入系数
          （防止 rs_ratio=0.00/rs_momentum=0.50 的默认值在 rrg_position 和
          momentum_turnaround 两维同时拿高分）
        - 病因C 方向惩罚：近 3 日累计跌幅每 1% 打 0.9 折
          （找回旧 AND 闸门中"近 3 日不跌"方向约束，保持软评分哲学）
        """
        if rrg is None:
            return 0.0

        momentum_traj = (prev_trend or {}).get("momentum_trajectory") or []

        # 6 维评分
        s1 = cls._score_rs_ratio(rrg.rs_ratio)
        s2 = cls._score_rs_momentum(rrg.rs_momentum)
        s3 = cls._score_turnaround(momentum_traj)
        s4 = cls._compute_ambush_score(df, indicators)  # 复用已有结构吸筹分
        s5 = cls._score_volume_position(df)
        s6 = cls._score_strength_cap(strength_score)

        total = (
            s1 * cls._AMBUSH_W_RS_RATIO
            + s2 * cls._AMBUSH_W_RS_MOMENTUM
            + s3 * cls._AMBUSH_W_TURNAROUND
            + s4 * cls._AMBUSH_W_ACCUMULATION
            + s5 * cls._AMBUSH_W_VOLUME_POSITION
            + s6 * cls._AMBUSH_W_STRENGTH_CAP
        )

        # ── 病因B：冷启动惩罚 ──
        # strength_trajectory 记录跨日强度分历史，长度 = 被追踪天数
        # 轨迹不足 _WARMUP_DAYS 天 → RRG EMA 尚未充分建立，评分打折
        trajectory = (prev_trend or {}).get("strength_trajectory") or []
        warmup = cls._AMBUSH_COLDSTART_WARMUP_DAYS
        cold_start_factor = min(1.0, len(trajectory) / warmup) if warmup > 0 else 1.0
        total *= cold_start_factor

        # ── 病因C：方向惩罚（找回"近 3 日不跌"约束）──
        # 近 3 日累计跌幅每 1% → 总分打 0.9 折（乘性，不退回 AND 硬闸门）
        if len(df) >= 4:
            close_3d_ago = df["close"].iloc[-4]
            close_now = df["close"].iloc[-1]
            if close_3d_ago > 0:
                return_3d = close_now / close_3d_ago - 1.0
                if return_3d < 0:
                    # 每 1% 跌幅 → 0.9 折：-5% → 0.9^5 ≈ 0.59
                    direction_factor = 0.9 ** abs(return_3d * 100)
                    total *= max(0.3, direction_factor)  # 下限 0.3 防止单次暴跌归零

        # ── 病因D：trajectory 斜率约束（2026-08-25 新增）──
        # strength_trajectory 连续 N 天单调递减 → "飞刀"信号，乘性惩罚
        # 与方向惩罚互补：方向惩罚看价格，这里看强度分的趋势性恶化
        # 半导体 08-25 trajectory=[49.7,51.5,29.6,16.2,27.3,18.9,11.2] 末段连续递减
        if len(trajectory) >= 3:
            declining_streak = 0
            for i in range(len(trajectory) - 1, 0, -1):
                if trajectory[i] < trajectory[i - 1]:
                    declining_streak += 1
                else:
                    break
            # 连续3天递减=0.8, 4天=0.6, 5+天=0.4
            if declining_streak >= 3:
                slope_factor = max(0.4, 1.0 - (declining_streak - 2) * 0.2)
                total *= slope_factor

        return round(total, 4)

    @staticmethod
    def _compute_ambush_score(df: pd.DataFrame, indicators: dict[str, float]) -> float:
        """结构吸筹加权分（0-1）— 从 _check_ambush 拆分，供状态机新埋伏判据复用。

        4 维加权：波动率压缩25% + 机构积累35% + 量能特征25% + 试盘15%，A5 乘法加成。
        返回纯加权分（不含"近3日不跌/量能放大"硬门槛），调用方自行叠加方向/位置条件。

        论文依据：
        - Bollinger squeeze bottom 10-20th percentile = spring coiling
        - 机构积累（量增价平）出现在 68% 的拉升前（TradeAlgo 2026）
        - VCV 上升 = 知情资金介入（Boone & White 2015, Kyle 1985）
        - 压缩 + 量能异常同时出现 → 启动概率最高（spike_scanner 开源项目）
        """
        if len(df) < 20:
            return 0.0

        close = df["close"]
        volume = df["volume"]

        # ① 波动率压缩评分 (25%)
        bb_pct = bb_width_percentile(df, period=20, lookback=252)
        if bb_pct < 0.20:
            compression_score = 1.0
        elif bb_pct < 0.40:
            compression_score = 0.5
        else:
            compression_score = 0.0

        # ② 机构积累评分 (35%)：A/D Line 上升 + 价格横盘
        accum_score = accumulation_score(df)

        # ③ 量能特征评分 (25%)：VCV + 量能放大
        vcv_score = volume_vcv(df, lookback=20)
        vol_expansion = indicators.get("turnover_expansion", 0.5)
        volume_feature_score = clip(vcv_score * 0.6 + vol_expansion * 0.4, 0, 1)

        # ④ 试盘确认评分 (15%)
        last_row = df.iloc[-1]
        te = indicators.get("turnover_expansion", 0.5)
        intraday_range = (
            (last_row["high"] - last_row["low"]) / last_row["close"]
            if last_row["close"] > 0 else 1.0
        )
        bh = indicators.get("breadth_health", 0.5)
        # 放宽振幅阈值（板块级数据波动天然小于个股：2% → 3%）
        probe_score = 1.0 if (te > 0.7 and intraday_range < 0.03 and 0.3 < bh < 0.7) else 0.0

        # 加权汇总
        ambush_score = (
            compression_score * 0.25
            + accum_score * 0.35
            + volume_feature_score * 0.25
            + probe_score * 0.15
        )

        # A5: 压缩 + 量能同时达标 → 乘法加成
        if compression_score >= 0.5 and volume_feature_score >= 0.5:
            ambush_score = min(1.0, ambush_score * 1.3)

        return ambush_score

    @staticmethod
    def _check_ambush(df: pd.DataFrame, indicators: dict[str, float]) -> bool:
        """埋伏结构条件（布尔包装：硬门槛 + 结构吸筹分阈值）。

        必要前提：
        - 近 3 日不跌（价格方向门槛——近3日下跌的不是埋伏，是弱势）
        - 量能温和放大（当日量 ≥ MA20 或近 3 日均量温和抬升）
        然后要求 _compute_ambush_score ≥ 0.5。"""
        if len(df) < 20:
            return False

        # 价格方向门槛：近 3 日下跌的板块不是"埋伏"，是弱势
        if len(df) >= 3:
            recent_close = df["close"].iloc[-3:]
            if recent_close.iloc[-1] < recent_close.iloc[0]:
                return False

        # 量能温和放大（对齐 Node 3"温和连续"吸筹定义）：
        # 当日量 ≥ MA20(1.0x) 或 近 3 日均量 > 前 3 日均量（温和抬升）
        volume = df["volume"]
        vol_ma20 = volume.rolling(20).mean().iloc[-1] if len(volume) >= 20 else volume.mean()
        if vol_ma20 <= 0:
            return False
        vol_today = volume.iloc[-1]
        vol_at_ma20 = vol_today >= vol_ma20  # 当日量 ≥ MA20
        vol_ramp = False
        if len(volume) >= 6:
            recent_3d = volume.iloc[-3:].mean()
            prev_3d = volume.iloc[-6:-3].mean()
            vol_ramp = recent_3d > prev_3d  # 3日均量温和抬升
        if not (vol_at_ma20 or vol_ramp):
            return False

        return SectorAnalysisNode._compute_ambush_score(df, indicators) >= 0.5

    @classmethod
    def _is_ambush_candidate(
        cls,
        df: pd.DataFrame,
        indicators: dict[str, float],
        rrg: RRGQuadrant,
        strength_score: float,
        prev_trend: Optional[dict],
        is_defensive: bool,
    ) -> bool:
        """埋伏评分化判据（P0-1: 6 维加权 + 排名截断，替代 6 AND 硬闸门）。

        计算 6 维加权分数 → 存入 indicators["ambush_score_v2"] → 供 _build_mainline_output 排名。
        状态机兼容层：score >= AMBUSH_SCORE_FLOOR → is_ambush=True（保留时间维记忆）。
        防守轮动与埋伏正交，直接排除。
        """
        if is_defensive:
            indicators["ambush_score_v2"] = 0.0
            return False

        score = cls._compute_ambush_score_v2(
            df, indicators, rrg, strength_score, prev_trend,
        )
        # 统一 round 到 2 位小数：消除浮点噪声，保证存储值/比较值/显示值三者一致
        # 否则会出现 ambush_score_v2=0.2999 显示为 0.30 却被判为 < floor 的幽灵 bug
        score = round(score, 2)
        indicators["ambush_score_v2"] = score
        return score >= cls.AMBUSH_SCORE_FLOOR

    @staticmethod
    def _check_ambush_near_miss(
        df: pd.DataFrame,
        indicators: dict[str, float],
        rrg: RRGQuadrant,
        strength_score: float,
        prev_trend: Optional[dict],
        is_defensive: bool,
    ) -> tuple[int, list[str]]:
        """埋伏近失诊断：逐条件检查，返回 (失败数, 失败条件名列表)。

        用于 Node 6 蓄水池的 near_miss 标记——差 1 条闸门通过的板块也值得观察。
        排除 is_defensive（防守轮动与埋伏正交，不算"近失"）。
        """
        failures: list[str] = []
        if rrg is None:
            return 6, ["no_rrg"]

        # 条件 1: 截面低位
        if rrg.rs_ratio >= 0.55:
            failures.append("ratio_high")

        # 条件 2: 动量已转正
        if rrg.rs_momentum < 0.5:
            failures.append("momentum_negative")

        # 条件 3: 动量刚转正（3 日窗口）
        momentum_traj = (prev_trend or {}).get("momentum_trajectory") or []
        recent_moms = momentum_traj[-3:] if momentum_traj else []
        recent_valid = [m for m in recent_moms if m is not None]
        if len(recent_valid) >= 2 and all(m >= 0.5 for m in recent_valid):
            failures.append("momentum_sustained")

        # 条件 4: 结构吸筹
        if not SectorAnalysisNode._check_ambush(df, indicators):
            failures.append("no_accumulation")

        # 条件 5: 分数上限
        if strength_score > 72:
            failures.append("score_too_high")

        return len(failures), failures

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

    def _classify_lifecycle_v3(
        self,
        rrg: RRGQuadrant,
        prev_days: Optional[int] = None,
    ) -> LifecycleInfo:
        """RRG 象限直接映射生命周期阶段（Node 2 v3，替代旧的天数驱动）。

        象限 → 阶段映射：
        - Leading   → acceleration（领涨加速期）
        - Improving → launch（启动改善期）
        - Weakening → mature（成熟衰退期）
        - Lagging   → late_stage（晚期停滞）

        连续性：继承前日天数 +1，不依赖 K 线重算。

        论文依据：
        - RRG 四象限天然对应板块生命周期（Minervini 2016）
        - 动量领先趋势：Improving→Leading 转换比旧版 launch 硬门槛更早（Janssen 2020）
        """
        _quadrant_to_phase = {
            "leading": "acceleration",
            "improving": "launch",
            "weakening": "mature",
            "lagging": "late_stage",
        }
        phase = _quadrant_to_phase[rrg.quadrant]
        days = (prev_days or 0) + 1

        return LifecycleInfo(
            phase=phase,
            days_since_launch=days,
            thresholds={},  # RRG 不需要天数阈值
        )

    # =================================================================
    # 跨板块拥挤检测（R2）
    # =================================================================

    @staticmethod
    def _detect_family_crowding(analyzed: list[SectorDetail]) -> list[str]:
        """检测家族级拥挤信号。

        当同一 frame_group 内 ≥3 个板块的 overheat_severity > 0.3 → 拥挤信号。
        论文依据：Acharya & Marks (2019) — 因子拥挤导致流动性枯竭。

        返回警示信息列表，注入到 SectorContext.notes。
        """
        warnings: list[str] = []
        family_overheat: dict[str, list[str]] = {}  # frame_group → [板块名]

        for sector in analyzed:
            fg = sector.frame_group or "其他"
            severity = sector.overheat_severity or 0.0
            if severity > 0.3:
                family_overheat.setdefault(fg, []).append(
                    f"{sector.name}({severity:.2f})"
                )

        for family, members in family_overheat.items():
            if len(members) >= 3:
                warnings.append(
                    f"⚠️ 家族拥挤: [{family}] 有 {len(members)} 个板块过热 "
                    f"({', '.join(members[:5])})，追高风险显著"
                )

        return warnings

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
        cooling = pick_by_state("high_position_cooling", 3)

        # 兜底：空列用评分最高的未分配板块填充（但标注为兜底）
        # 修复：主线（active/exhausted）不再兜底填充，无达标板块就留空
        def fill_empty(target: list, limit: int, label: str):
            remaining = len(target)
            if remaining >= limit:
                return
            # 排除已分入主线/衰竭/冷却的板块，防止被重复拉入埋伏
            _excluded_states = {"active_mainline", "exhausted_mainline", "high_position_cooling"}
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
            active_mainline=[],  # 由 _build_mainline_output 填充
            exhausted_mainline=exhausted,
            ambush=ambush,
            near_miss_ambush=[],  # 由 _build_mainline_output 填充
            defensive_rotation=defensive,
            cooling_down=cooling,  # 由 _build_mainline_output 覆盖为权威结果
            top_by_score=top_by_score,
            market_direction=direction,
            notes=notes,
        )

    @staticmethod
    def _early_market_direction(sectors_raw: list[dict]) -> str:
        """提前计算市场方向（基于原始板块 dict 的 T-0 涨跌幅）。

        在逐板块分析前调用，用于 Regime-Dependent P0 门槛。
        逻辑与 _classify_market_direction 相同，但操作 dict 而非 SectorDetail。
        """
        with_data = [s for s in sectors_raw if s.get("today_change") is not None]
        if not with_data:
            return "分化"

        up = sum(1 for s in with_data if s["today_change"] > 0)
        down = sum(1 for s in with_data if s["today_change"] < 0)
        total = len(with_data)

        if up / total > 0.6:
            return "普涨"
        elif down / total > 0.6:
            return "普跌"
        return "分化"

    @staticmethod
    def _classify_market_direction(sectors: list[SectorDetail]) -> str:
        """市场方向: 普涨(>60%上涨) / 普跌(>60%下跌) / 分化。

        使用 T-0 真实涨跌幅（today_change），而非百分位排名（price_momentum）。
        百分位排名值域 [0,1]，>0.2 永远 ≈80% 满足，导致永远输出"普涨"。
        """
        # 仅统计有真实涨跌幅数据的板块
        with_data = [s for s in sectors if s.today_change is not None]
        if not with_data:
            return "分化"

        up = sum(1 for s in with_data if s.today_change > 0)
        down = sum(1 for s in with_data if s.today_change < 0)
        total = len(with_data)

        if up / total > 0.6:
            return "普涨"
        elif down / total > 0.6:
            return "普跌"
        return "分化"

    # =================================================================
    # 状态机退出压力常量（2026-08-31 吸收原框逻辑）+ 主线裁剪常量
    # =================================================================

    # 退出压力阈值（原框 _MAINLINE_EXIT_SCORE_THRESHOLD）
    _MAINLINE_EXIT_SCORE_THRESHOLD = 50
    # 退出压力容忍天数（原框 _MAINLINE_STRONG/WEAK_TOLERANCE_DAYS）
    _MAINLINE_STRONG_TOLERANCE_DAYS = 3  # 强组（含 S/A 成员）容忍天数
    _MAINLINE_WEAK_TOLERANCE_DAYS = 1    # 弱组（全 B 成员）零容忍
    _MAINLINE_LATE_STAGE_SCORE_THRESHOLD = 60

    # 破位单日暴跌阈值（P0-3，2026-09-02 审核新增）
    # 跨 29 日 / 930 sector-day 校准：active_mainline 池 3 次命中中 2 次为真实
    # 主线深回调（黄金 08-11 -5.88% 但 5 日 +4.1%、种业 08-21 -7.14% 但 5 日 +1.2%），
    # 故不并入 broken（那会走 E3 当天踢出），而在主线分支分级处理：
    #   暴跌 + 趋势破坏（5日涨幅≤0 或 跌破MA20）→ exhausted_mainline
    #   暴跌但趋势尚存 → high_position_cooling（给一天缓冲）
    # 设计意图：S/A-tier 的 exit_pressure 因 strength>=50 归零恒上限 2 < 容忍度 3，
    # 驱逐路径结构性不可达，单日暴跌是唯一能当日生效的驱逐触发器。
    _BROKEN_ONE_DAY_DROP_PCT = -4.0

    # 主线裁剪常量（2026-08-20：保底 + 独立行情豁免）
    _MAINLINE_PRUNE_FLOOR = 1            # 熊市也保留最强一条主线，防止全裁
    _INDEPENDENT_MARKET_SCORE = 90.0     # 独立行情豁免：组分数门槛
    # 2026-09-02 P2-4 配套调整（原值 3）：
    # 该门槛要求「同一 frame_group 组内」S/A 成员数达标才算家族级共振。
    # P2-4 拆分"其他"垃圾箱后分组粒度变细——0902 实证：军贸组由 3 个 A 级成员
    # （军贸概念+风沙治理+粮食概念，实为不相关主题）降为 1 个，豁免失效，
    # 主线组数 3→2，风沙治理(93.2)、银行(88.7) 被误裁。
    # 拆组后一个真实家族有 2 个 S/A 成员已是强共振信号，故下调至 2 以补偿
    # 分组粒度变化。group_score>=90 的硬门槛仍保留，豁免不会失控。
    _INDEPENDENT_MARKET_MIN_STRONG = 2  # 独立行情豁免：S/A 级成员数门槛（家族级共振）
    _INDEPENDENT_MARKET_EXEMPT_MAX = 2   # 豁免名额上限
    _MAINLINE_FAMILY_MAX_MEMBERS = 3     # 家族内部成员裁剪：只保留 Top 3 最强成员

    def _build_mainline_output(
        self,
        raw_result: SectorContext,
        analyzed: list[SectorDetail],
        attack_power: float = 0.5,
    ) -> SectorContext:
        """状态驱动的输出层聚合（2026-08-31 替代原框系统）。

        核心变更：
        - 主线聚合直接取 analyzed 中 state=="active_mainline" 的板块，
          按 frame_group 聚合为 MainlineGroup（不再依赖 frames dict）
        - 衰竭主线从 analyzed 中 state=="exhausted_mainline" 过滤（不再依赖 exhausted_from_frame）
        - attack_power 5 档裁剪 + 独立行情豁免逻辑原样保留
        - 埋伏双轨 / near_miss / cooling / exhausted / defensive / top_by_score 不变
        """
        from src.got.schemas import MainlineGroup

        used_codes: set[str] = set()

        # ── 主线聚合：状态驱动（替代原框遍历）──
        # 直接取 state == "active_mainline" 的板块，按 frame_group 聚合
        family_boards: dict[str, list[SectorDetail]] = {}
        family_tiers: dict[str, str] = {}

        for sector in analyzed:
            if sector.state != "active_mainline":
                continue
            used_codes.add(sector.code)
            fg = sector.frame_group or _build_frame_group(sector.name)
            family_boards.setdefault(fg, []).append(sector)
            # 追踪家族最高 tier
            current_best = family_tiers.get(fg, "B")
            if sector.tier == "S":
                family_tiers[fg] = "S"
            elif sector.tier == "A" and current_best != "S":
                family_tiers[fg] = "A"

        mainline_groups: list[MainlineGroup] = []
        for fg, boards in family_boards.items():
            best_tier = family_tiers.get(fg, "B")
            group_score = max(b.strength_score for b in boards)
            has_strong = best_tier in ("S", "A")
            sorted_boards = sorted(boards, key=lambda s: s.strength_score, reverse=True)
            # 家族内部成员裁剪：只保留 Top N 最强成员（防膨胀）
            display_name = sorted_boards[0].name
            pruned_boards = sorted_boards[:self._MAINLINE_FAMILY_MAX_MEMBERS]
            if len(sorted_boards) > len(pruned_boards):
                logger.debug(
                    f"[Node 2] 家族成员裁剪: {fg} {len(sorted_boards)}→{len(pruned_boards)} "
                    f"(保留: {[b.name for b in pruned_boards]})"
                )
            mainline_groups.append(MainlineGroup(
                group_name=display_name,
                group_score=group_score,
                tier=best_tier,
                mainline_quality="strong" if has_strong else "weak",
                members=pruned_boards,
            ))
        mainline_groups.sort(key=lambda g: g.group_score, reverse=True)

        # attack_power 5 档阶梯裁剪
        if attack_power < 0.15:
            max_mainline = 1
        elif attack_power < 0.30:
            max_mainline = 2
        elif attack_power < 0.50:
            max_mainline = 3
        elif attack_power < 0.70:
            max_mainline = 4
        else:
            max_mainline = 5

        # 独立行情豁免
        exempt_groups = [
            g for g in mainline_groups
            if g.group_score >= self._INDEPENDENT_MARKET_SCORE
            and sum(1 for m in g.members if m.tier in ("S", "A"))
            >= self._INDEPENDENT_MARKET_MIN_STRONG
        ][:self._INDEPENDENT_MARKET_EXEMPT_MAX]
        exempt_names = {g.group_name for g in exempt_groups}
        if exempt_groups:
            logger.info(
                f"[Node 2] 独立行情豁免: {[g.group_name for g in exempt_groups]} "
                f"(attack_power={attack_power:.2f})"
            )

        regular_groups = [g for g in mainline_groups if g.group_name not in exempt_names]
        if len(regular_groups) > max_mainline:
            logger.info(
                f"[Node 2] 主线裁剪: {len(regular_groups)}→{max_mainline} "
                f"(attack_power={attack_power:.2f}, 豁免{len(exempt_groups)}个)"
            )
            regular_groups = regular_groups[:max_mainline]
        mainline_groups = sorted(
            exempt_groups + regular_groups,
            key=lambda g: g.group_score, reverse=True,
        )

        # ── 埋伏输出：真并集双轨合并 ──
        excluded_states = {"active_mainline", "exhausted_mainline", "high_position_cooling", "defensive_rotation"}

        track1_ambush = [s for s in analyzed if s.state == "ambush" and s.code not in used_codes]
        track1_ambush.sort(
            key=lambda s: s.indicators.get("ambush_score_v2", 0), reverse=True,
        )
        track1_ambush = track1_ambush[:self.AMBUSH_TRACK1_MAX]

        track1_codes = {s.code for s in track1_ambush}
        remaining_slots = max(0, self.AMBUSH_TOP_K - len(track1_ambush))
        track2_candidates = [
            s for s in analyzed
            if s.state not in excluded_states
            and s.code not in used_codes
            and s.code not in track1_codes
            and s.indicators.get("ambush_score_v2", 0) > 0
        ]
        track2_candidates.sort(
            key=lambda s: s.indicators.get("ambush_score_v2", 0), reverse=True,
        )
        track2_topk = track2_candidates[:remaining_slots] if remaining_slots > 0 else []

        ambush_list = list(track1_ambush)
        for s in track2_topk:
            if s.indicators.get("ambush_score_v2", 0) >= self.AMBUSH_SCORE_FLOOR:
                ambush_list.append(s)

        ambush_codes = {s.code for s in ambush_list}
        used_codes.update(ambush_codes)

        if track1_ambush or track2_topk:
            logger.info(
                f"[Node 2] 埋伏双轨: Track1(状态机)={len(track1_ambush)}, "
                f"Track2(补位{remaining_slots})={len(track2_topk)}, "
                f"合并后={len(ambush_list)}"
            )

        # ── 近失埋伏 ──
        near_miss_floor = self.AMBUSH_SCORE_FLOOR * self.AMBUSH_NEAR_MISS_RATIO
        near_miss_list = []
        for s in analyzed:
            if s.code in used_codes:
                continue
            if s.state in excluded_states:
                continue
            score = s.indicators.get("ambush_score_v2", 0)
            if score < near_miss_floor:
                continue
            in_pool = s.code in ambush_codes
            above_floor = score >= self.AMBUSH_SCORE_FLOOR
            if in_pool or above_floor:
                continue
            s.near_miss_conditions = [f"score_below_floor({score:.2f}<{self.AMBUSH_SCORE_FLOOR:.2f})"]
            near_miss_list.append(s)

        near_miss_list.sort(
            key=lambda s: s.indicators.get("ambush_score_v2", 0), reverse=True,
        )
        if near_miss_list:
            logger.info(
                f"[Node 2] 近失埋伏: {len(near_miss_list)} 个 "
                f"(分数区间 [{near_miss_floor:.2f}, {self.AMBUSH_SCORE_FLOOR}))"
            )
        near_miss_list = near_miss_list[:10]
        for s in near_miss_list:
            used_codes.add(s.code)

        # 衰竭主线：从 analyzed 中 state=="exhausted_mainline" 过滤（状态驱动）
        exhausted_mainline = [
            s for s in analyzed
            if s.state == "exhausted_mainline" and s.code not in used_codes
        ]
        used_codes.update(s.code for s in exhausted_mainline)

        # 高位降温
        cooling_list = [
            s for s in analyzed
            if s.state == "high_position_cooling" and s.code not in used_codes
        ]
        cooling_list.sort(key=lambda s: s.strength_score, reverse=True)
        if len(cooling_list) > 5:
            logger.info(f"[Node 2] 高位降温截断: {len(cooling_list)}→5 个")
        cooling_list = cooling_list[:5]
        for s in cooling_list:
            used_codes.add(s.code)

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

        # ── 过热降权链路数据（Part 1）──
        # overheat_lookup: 全部 analyzed 板块的过热严重度（口径同 strength_lookup）
        overheat_lookup = {
            s.name: s.overheat_severity
            for s in analyzed
            if s.overheat_severity is not None and s.overheat_severity > 0
        }
        # crowded_families: 家族内 >=3 板块 severity>0.3 → 列入拥挤名单；
        # frame_group_lookup: 板块名→家族名全量表，供 Node 6 合成 detail 回退时查表（同 overheat_lookup 口径）
        # 【B3 口径标注（2026-09-28 裁决：维持现状）】≥3/>0.3 为工程启发式（未外部标定），
        # 随过热 severity 同源派生；活消费方：Node 6 续航 crowded 标记 / Phase 2 家族追加降权 ×0.90 /
        # 埋伏 G3 黑名单输入。非无消费者字段（旧 OPEN-5 Y2 记载已过期，见 backlog §7 B3）。
        _family_overheat_count: dict[str, int] = {}
        frame_group_lookup: dict[str, str] = {}
        for s in analyzed:
            fg = s.frame_group
            if fg:
                frame_group_lookup[s.name] = fg
                if (s.overheat_severity or 0) > 0.3:
                    _family_overheat_count[fg] = _family_overheat_count.get(fg, 0) + 1
        crowded_families = [fg for fg, cnt in _family_overheat_count.items() if cnt >= 3]

        return SectorContext(
            active_mainline=mainline_groups,
            exhausted_mainline=exhausted_mainline,
            ambush=ambush_list,
            near_miss_ambush=near_miss_list,
            defensive_rotation=defensive,
            cooling_down=cooling_list,
            top_by_score=top_by_score,
            market_direction=raw_result.market_direction,
            notes=[n for n in raw_result.notes if "埋伏板块" not in n],
            reasoning=raw_result.reasoning,
            data_quality=raw_result.data_quality,
            overheat_lookup=overheat_lookup,
            crowded_families=crowded_families,
            frame_group_lookup=frame_group_lookup,
        )

    @staticmethod
    def _extract_attack_power(market_context) -> float:
        """从 Node 1 市场上下文中提取进攻强度（attack_power，0~1）。

        读取 Node1 综合市场状态条件变量（market_state.attack_power）。
        旧版/缺失时降级为 0.5（默认中性），确保向下兼容。
        """
        try:
            market_state = getattr(market_context, "market_state", None)
            if market_state is not None:
                ap = getattr(market_state, "attack_power", None)
                if ap is not None:
                    return float(ap)
        except Exception:
            pass
        return 0.5

    # =================================================================
    # 主线家族聚合（Node 2 v2）
    # =================================================================

    def _build_theme_families(
        self,
        analyzed: list[SectorDetail],
        sectors_raw: list[dict],
    ) -> list:
        """数据驱动的板块家族聚类（替代硬编码 FAMILY_KEYWORDS 分桶）。

        使用板块 90 日收益相关性矩阵 + 层次聚类（average linkage，numpy 实现）
        将相关性高的板块自然聚为一族。不同驱动逻辑的板块（如黄金 vs 稀土）
        因收益相关性低而分入不同族。

        参考：Boubaker et al. (2022) — 按收益相关性合并行业，Sharpe 0.71→1.16。

        Args:
            analyzed: 已分析的 SectorDetail 列表
            sectors_raw: 原始板块数据（含 K 线）

        Returns: list[ThemeFamily]
        """
        from src.got.schemas import ThemeFamily

        if len(analyzed) < 3:
            return []

        # ── Step 1: 构建收益相关性矩阵 ──
        raw_by_code = {s["code"]: s for s in sectors_raw}
        returns_list = []
        valid_indices = []  # analyzed 中有有效收益数据的索引

        for i, sector in enumerate(analyzed):
            raw = raw_by_code.get(sector.code)
            if not raw or not raw.get("klines") or len(raw["klines"]) < 10:
                returns_list.append(None)
                continue
            closes = [k["close"] for k in raw["klines"] if k.get("close", 0) > 0]
            if len(closes) < 10:
                returns_list.append(None)
                continue
            rets = [(closes[j] - closes[j - 1]) / closes[j - 1]
                    for j in range(1, len(closes)) if closes[j - 1] > 0]
            if len(rets) < 5:
                returns_list.append(None)
                continue
            returns_list.append(rets)
            valid_indices.append(i)

        n_valid = len(valid_indices)
        if n_valid < 3:
            logger.warning("[Node 2] 有效收益数据不足，回退到关键词分组")
            return self._build_theme_families_fallback(analyzed)

        # 对齐收益序列长度
        min_len = min(len(r) for r in returns_list if r is not None)

        # 计算相关系数矩阵
        corr_matrix = np.zeros((n_valid, n_valid))
        for a in range(n_valid):
            corr_matrix[a, a] = 1.0
            ra = np.array(returns_list[valid_indices[a]][:min_len])
            for b in range(a + 1, n_valid):
                rb = np.array(returns_list[valid_indices[b]][:min_len])
                if ra.std() > 1e-10 and rb.std() > 1e-10:
                    c = float(np.corrcoef(ra, rb)[0, 1])
                    if not np.isnan(c):
                        corr_matrix[a, b] = c
                        corr_matrix[b, a] = c

        # ── Step 2: 层次聚类（complete linkage，纯 numpy）──
        corr_threshold = 0.65  # 相关性 > 0.65 的板块聚为一族（complete linkage 保守）
        cluster_labels = self._hierarchical_cluster(corr_matrix, corr_threshold)

        # ── Step 3: 计算板块一致性评分（Fix 2）──
        coherence_scores = self._compute_all_coherence(corr_matrix, cluster_labels, n_valid)

        # 写回 SectorDetail
        for vi_idx, analyzed_idx in enumerate(valid_indices):
            analyzed[analyzed_idx].coherence_score = coherence_scores[vi_idx]

        # ── Step 4: 构建家族 ──
        clusters: dict[int, list[int]] = {}
        for vi_idx, label in enumerate(cluster_labels):
            clusters.setdefault(label, []).append(vi_idx)

        # 按簇内最强板块命名（数据驱动，不依赖关键词 frame_group）
        families = []

        for cluster_id, vi_members in sorted(clusters.items()):
            member_sectors = [analyzed[valid_indices[vi]] for vi in vi_members]
            if not member_sectors:
                continue

            # 家族命名：用最强板块的真实名称
            # 清理 null 字符（mootdx 板块名编码问题）
            sorted_members = sorted(
                member_sectors, key=lambda s: s.strength_score, reverse=True,
            )
            top_name = sorted_members[0].name.replace("\x00", "").strip()
            if len(sorted_members) == 1:
                family_name = top_name
            elif len(sorted_members) <= 3:
                names = [s.name.replace("\x00", "").strip() for s in sorted_members[:3]]
                family_name = "/".join(names)
            else:
                family_name = f"{top_name}等{len(sorted_members)}板块"

            # 家族评分（coherence 加权）
            weights = [
                max(coherence_scores[vi], 0.1) if vi in range(n_valid) else 0.5
                for vi in vi_members
            ]
            total_w = sum(weights)
            family_score = sum(
                s.strength_score * w for s, w in zip(member_sectors, weights)
            ) / total_w if total_w > 0 else 0

            # 共振度
            active_count = sum(
                1 for s in member_sectors
                if s.state in ("active_mainline", "exhausted_mainline")
            )
            family_resonance = active_count / len(member_sectors)

            # 混合驱动检测（Fix 3）
            mixed = self._detect_mixed_drivers(
                vi_members, corr_matrix, corr_threshold,
            )

            # 题材地位判定
            if family_score > 70 and family_resonance > 0.5:
                theme_status = "主线核心"
            elif family_score > 60 and family_resonance > 0.3:
                theme_status = "主线扩散"
            elif family_score > 50:
                theme_status = "独立异动"
            else:
                has_rising = any(
                    m.trend_direction == "increasing" for m in member_sectors
                )
                theme_status = "修复观察" if has_rising else "高位降温"

            if mixed:
                theme_status = f"混合驱动"

            families.append(ThemeFamily(
                family_name=family_name,
                family_score=round(family_score, 1),
                family_resonance=round(family_resonance, 2),
                theme_status=theme_status,
                member_boards=[s.name for s in member_sectors],
                member_count=len(member_sectors),
                mixed_driver=mixed,
            ))

        families.sort(key=lambda f: f.family_score, reverse=True)
        logger.info(
            f"[Node 2] 相关性聚类: {n_valid} 个板块 → {len(families)} 个家族"
            f"（阈值 r>{corr_threshold}）"
        )
        return families

    @staticmethod
    def _hierarchical_cluster(
        corr_matrix: np.ndarray, threshold: float,
    ) -> list[int]:
        """层次聚类（complete linkage），纯 numpy 实现。

        complete linkage: 两簇距离 = 簇间最小相关性（最弱环节）。
        只有当两簇 ALL 板块对的相关性都 > threshold 时才合并。
        比 average linkage 更保守，避免不相关板块被大簇裹挟。

        Returns:
            每个有效板块的簇标签（0-indexed）。
        """
        n = corr_matrix.shape[0]
        if n == 0:
            return []

        # 初始化：每个板块一个簇
        cluster_boards: dict[int, list[int]] = {i: [i] for i in range(n)}
        next_id = n

        while len(cluster_boards) > 1:
            cluster_ids = sorted(cluster_boards.keys())

            # 找 complete-linkage 相关性最高的簇对
            # complete: min correlation across all pairs (最弱环节)
            best_pair = None
            best_min_corr = -2.0

            for ii in range(len(cluster_ids)):
                for jj in range(ii + 1, len(cluster_ids)):
                    ci, cj = cluster_ids[ii], cluster_ids[jj]
                    min_corr = min(
                        corr_matrix[a, b]
                        for a in cluster_boards[ci]
                        for b in cluster_boards[cj]
                    )
                    if min_corr > best_min_corr:
                        best_min_corr = min_corr
                        best_pair = (ci, cj)

            # 合并后最弱环节低于阈值 → 停止
            if best_min_corr < threshold or best_pair is None:
                break

            # 合并
            ci, cj = best_pair
            new_id = next_id
            next_id += 1
            cluster_boards[new_id] = cluster_boards.pop(ci) + cluster_boards.pop(cj)

        # 生成标签（连续 0-indexed）
        labels = [0] * n
        for new_label, (_, boards) in enumerate(cluster_boards.items()):
            for b in boards:
                labels[b] = new_label

        return labels

    @staticmethod
    def _compute_all_coherence(
        corr_matrix: np.ndarray,
        cluster_labels: list[int],
        n_valid: int,
    ) -> list[float]:
        """计算每个板块的一致性评分。

        一致性 = 该板块与同簇其他板块的平均相关性。
        >0.7: 高一致性（真实主题）
        <0.4: 噪声/混合概念
        """
        scores = []
        for i in range(n_valid):
            same_cluster = [
                j for j in range(n_valid)
                if cluster_labels[j] == cluster_labels[i] and j != i
            ]
            if not same_cluster:
                scores.append(0.5)  # 单板块族 → 中等一致性
            else:
                avg_corr = np.mean([corr_matrix[i, j] for j in same_cluster])
                scores.append(round(float(avg_corr), 3))
        return scores

    @staticmethod
    def _detect_mixed_drivers(
        vi_members: list[int],
        corr_matrix: np.ndarray,
        threshold: float,
    ) -> bool:
        """家族内驱动混合检测。

        对家族内板块计算平均两两相关性。
        若平均相关性 < threshold * 0.6 → 标记为混合驱动。
        """
        if len(vi_members) < 3:
            return False

        corrs = []
        for ii in range(len(vi_members)):
            for jj in range(ii + 1, len(vi_members)):
                corrs.append(corr_matrix[vi_members[ii], vi_members[jj]])

        avg_corr = np.mean(corrs) if corrs else 0
        return bool(avg_corr < threshold * 0.6)

    def _build_theme_families_fallback(
        self, analyzed: list[SectorDetail],
    ) -> list:
        """回退到硬编码 FAMILY_KEYWORDS 分组（聚类数据不足时使用）。"""
        from src.got.schemas import ThemeFamily

        family_members: dict[str, list[SectorDetail]] = {
            name: [] for name, _ in FAMILY_KEYWORDS
        }
        family_members["其他"] = []

        for sector in analyzed:
            frame_group = sector.frame_group or _build_frame_group(sector.name)
            if frame_group in family_members:
                family_members[frame_group].append(sector)
            else:
                family_members["其他"].append(sector)

        families = []
        for family_name, members in family_members.items():
            if not members or family_name == "其他":
                continue

            family_score = sum(m.strength_score for m in members) / len(members)
            active_count = sum(
                1 for m in members
                if m.state in ("active_mainline", "exhausted_mainline")
            )
            family_resonance = active_count / len(members)

            if family_score > 70 and family_resonance > 0.5:
                theme_status = "主线核心"
            elif family_score > 60 and family_resonance > 0.3:
                theme_status = "主线扩散"
            elif family_score > 50:
                theme_status = "独立异动"
            else:
                has_rising = any(
                    m.trend_direction == "increasing" for m in members
                )
                theme_status = "修复观察" if has_rising else "高位降温"

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

    # 时间线维护通道上限（防爆，前日 actionable 板块可能很多）
    _TIMELINE_SUPPLY_MAX = 50

    # 前日 actionable 状态集合（这些状态的板块需要强制保留在候选集中）
    _ACTIONABLE_STATES = frozenset({
        "active_mainline", "high_position_cooling", "exhausted_mainline", "ambush",
    })

    # 缺失板块连续补写上限：超过则熄灭为 neutral（对应原框"连续3天无数据踢出"，
    # 略放宽到 5 天给恢复机会，防止 K 线持续失败的僵尸板块永久占用通道 4 名额）
    _MISSING_EXTINGUISH_DAYS = 5

    def _load_prev_actionable_boards(self) -> list[tuple[str, str]]:
        """通道 4：从 node2_sector_trends 加载前日 actionable 板块（code, name）。

        根因修复（2026-08-31）：板块跌出 _discover_boards 当日截面候选集 →
        不在 analyzed → 状态机跳过 → "蒸发"。
        此方法确保前日 actionable 板块不被截面排名丢弃。
        """
        result: list[tuple[str, str]] = []
        try:
            db = DatabaseManager()
            today = date.today()
            with db.get_session() as session:
                # 查询前日最新记录中 state ∈ actionable 的板块
                # 使用子查询取每个板块的最新记录
                latest_records = (
                    session.query(Node2SectorTrendRecord)
                    .filter(
                        Node2SectorTrendRecord.tracking_date < today,
                        Node2SectorTrendRecord.state.in_(self._ACTIONABLE_STATES),
                    )
                    .order_by(
                        Node2SectorTrendRecord.sector_code,
                        Node2SectorTrendRecord.tracking_date.desc(),
                    )
                    .all()
                )
                # 去重：每个 code 只取最新一条
                seen_codes: set[str] = set()
                for rec in latest_records:
                    if rec.sector_code in seen_codes:
                        continue
                    seen_codes.add(rec.sector_code)
                    result.append((rec.sector_code, rec.sector_name))
        except Exception as exc:
            logger.warning("[Node 2] 时间线维护通道加载失败: %s", exc)
        return result

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
                    # 动量 EMA 轨迹（供埋伏"刚转正"3 日窗口判定）
                    momentum_traj = [
                        getattr(h, "rrg_momentum_ema", None)
                        for h in reversed(history)
                    ]
                    result[code] = {
                        "sector_code": code,
                        "sector_name": latest.sector_name,
                        "strength_trajectory": trajectory,
                        "trend_direction": latest.trend_direction,
                        "mainline_days": latest.mainline_days or 0,
                        "ambush_days": latest.ambush_days or 0,
                        "unified_signal": latest.unified_signal,
                        "lifecycle_phase": latest.lifecycle_phase,
                        # 2026-08-17 状态机新增字段（老库缺列时默认值兜底）
                        "state": getattr(latest, "state", None),
                        "dwell_days": int(getattr(latest, "dwell_days", 0) or 0),
                        "rrg_ratio_ema": getattr(latest, "rrg_ratio_ema", None),
                        "rrg_momentum_ema": getattr(latest, "rrg_momentum_ema", None),
                        # 2026-08-31 退出压力计数器（吸收原框 consecutive_low_days）
                        "exit_pressure_days": int(getattr(latest, "exit_pressure_days", 0) or 0),
                        # 动量 EMA 历史序列（最近 N 天，最新在末尾）
                        "momentum_trajectory": momentum_traj,
                    }

            if result:
                logger.info("[Node 2] 加载 %d 个板块的前日趋势", len(result))
        except Exception as e:
            logger.warning("[Node 2] 批量加载板块趋势失败（首次运行？）: %s", e)

        return result

    @staticmethod
    def _load_prev_state(session, sector_code: str) -> tuple[Optional[str], int]:
        """读取板块前一日精确状态与驻留天数（状态机 dwell 记忆源）。

        Returns:
            (prev_state, prev_dwell_days)。无历史或旧库缺列时返回 (None, 0)。
        """
        rec = (
            session.query(Node2SectorTrendRecord)
            .filter(
                Node2SectorTrendRecord.sector_code == sector_code,
                Node2SectorTrendRecord.tracking_date < date.today(),
            )
            .order_by(Node2SectorTrendRecord.tracking_date.desc())
            .first()
        )
        if rec is None:
            return None, 0
        return getattr(rec, "state", None), int(getattr(rec, "dwell_days", 0) or 0)

    def _save_sector_trends(self, sectors: list[SectorDetail]) -> None:
        """保存板块趋势到 SQLite，支持跨日追踪。

        追踪目标（2026-08-17 状态机校准扩展）：
        - strength_score 日度变化（7 天轨迹）
        - 精确状态快照（state）与驻留天数（dwell_days）—— 马尔可夫转移矩阵的记忆
        - RRG 3日EMA 平滑值（rrg_ratio_ema / rrg_momentum_ema）—— 次日状态判定的前值
        - 主线持续时间（mainline_days，含 cooling/exhausted 衍生期，保住"曾是主线 N 天"）
        """
        if not sectors:
            return

        try:
            db = DatabaseManager()
            today = date.today()

            with db.get_session() as session:
                for sector in sectors:
                    # ── 写入侧 NUL 剥离（v3.5 S1：防止幽灵行污染）──
                    sector_name_clean = sector.name.strip().strip('\x00') if sector.name else ""
                    # 计算强度趋势
                    trend_data = self._calc_strength_trend(session, sector.code, sector.strength_score)

                    # 计算主线持续时间（含高位降温/衰竭衍生期：is_mainline 扩展）
                    is_mainline = sector.state in (
                        "active_mainline", "exhausted_mainline", "high_position_cooling",
                    )
                    mainline_days = self._calc_mainline_days(session, sector.code, is_mainline)

                    # 计算埋伏持续时间
                    is_ambush = sector.state == "ambush"
                    ambush_days = self._calc_ambush_days(session, sector.code, is_ambush)

                    # ── 波次计数器（v3.5 S2：以回撤结构判波）──
                    wave_days, wave_start_date, wave_peak_strength = self._calc_wave_days(
                        session, sector.code, sector.state, sector.strength_score, today,
                    )

                    # 驻留天数：同状态累加、跨状态归 1（HMM 持久性：切换需驻留确认）
                    prev_state, prev_dwell = self._load_prev_state(session, sector.code)
                    dwell_days = (prev_dwell + 1) if prev_state == sector.state else 1

                    # RRG EMA 平滑值（供次日作为 prev_ratio_ema/prev_momentum_ema）
                    rrg_ratio_ema = sector.rrg.rs_ratio if sector.rrg else None
                    rrg_momentum_ema = sector.rrg.rs_momentum if sector.rrg else None

                    # 回填到 SectorDetail（确保 JSON 输出不为 null）
                    sector.strength_trajectory = trend_data["trajectory"]
                    sector.trend_direction = trend_data["trend_direction"]
                    sector.mainline_days = mainline_days
                    # ── 波次计数器回填（v3.5 S2）──
                    sector.wave_days = wave_days
                    sector.wave_start_date = wave_start_date
                    sector.wave_peak_strength = wave_peak_strength

                    # 创建或更新记录
                    db_record = session.query(Node2SectorTrendRecord).filter_by(
                        sector_code=sector.code,
                        tracking_date=today,
                    ).first()

                    if db_record:
                        # 更新已有记录
                        db_record.state = sector.state
                        db_record.dwell_days = dwell_days
                        db_record.rrg_ratio_ema = rrg_ratio_ema
                        db_record.rrg_momentum_ema = rrg_momentum_ema
                        db_record.unified_signal = self._state_to_signal(sector.state)
                        db_record.lifecycle_phase = sector.lifecycle.phase if sector.lifecycle else None
                        db_record.strength_score = sector.strength_score
                        db_record.exit_pressure_days = sector.exit_pressure_days or 0
                        db_record.is_mainline = is_mainline
                        db_record.mainline_days = mainline_days
                        db_record.ambush_days = ambush_days
                        # ── 波次计数器（v3.5 S2）──
                        db_record.wave_days = wave_days
                        db_record.wave_start_date = wave_start_date
                        db_record.wave_peak_strength = wave_peak_strength
                        db_record.set_strength_trajectory(trend_data["trajectory"])
                        db_record.trend_direction = trend_data["trend_direction"]
                    else:
                        # 创建新记录
                        db_record = Node2SectorTrendRecord(
                            sector_code=sector.code,
                            sector_name=sector_name_clean,
                            tracking_date=today,
                            state=sector.state,
                            dwell_days=dwell_days,
                            rrg_ratio_ema=rrg_ratio_ema,
                            rrg_momentum_ema=rrg_momentum_ema,
                            unified_signal=self._state_to_signal(sector.state),
                            lifecycle_phase=sector.lifecycle.phase if sector.lifecycle else None,
                            strength_score=sector.strength_score,
                            is_mainline=is_mainline,
                            mainline_days=mainline_days,
                            ambush_days=ambush_days,
                            exit_pressure_days=sector.exit_pressure_days or 0,
                            trend_direction=trend_data["trend_direction"],
                            # ── 波次计数器（v3.5 S2）──
                            wave_days=wave_days,
                            wave_start_date=wave_start_date,
                            wave_peak_strength=wave_peak_strength,
                        )
                        db_record.set_strength_trajectory(trend_data["trajectory"])
                        session.add(db_record)

                # ── 缺失板块补写（供给保证兜底，2026-08-31）──
                # 前日 actionable 但今日不在 analyzed（K线获取失败）→ exit_pressure +1
                saved_codes = {s.code for s in sectors}
                prev_actionable = self._load_prev_actionable_boards()
                gap_count = 0
                for code, name in prev_actionable:
                    if code in saved_codes:
                        continue
                    # 日粒度幂等：今日记录已存在（主循环已写入，或同日早轮已补写）则跳过。
                    # 复合主键 (sector_code, tracking_date)，重复 INSERT 会 IntegrityError
                    # 导致当轮全量持久化回滚（同日重跑是常态，必须防）。
                    existing_today = (
                        session.query(Node2SectorTrendRecord)
                        .filter_by(sector_code=code, tracking_date=today)
                        .first()
                    )
                    if existing_today is not None:
                        continue
                    # 查前日记录（严格早于今日，防止同日重跑读到今日行重复累计）
                    prev_rec = (
                        session.query(Node2SectorTrendRecord)
                        .filter(
                            Node2SectorTrendRecord.sector_code == code,
                            Node2SectorTrendRecord.tracking_date < today,
                        )
                        .order_by(Node2SectorTrendRecord.tracking_date.desc())
                        .first()
                    )
                    if prev_rec is None:
                        continue
                    prev_ep = int(getattr(prev_rec, "exit_pressure_days", 0) or 0)
                    new_ep = prev_ep + 1
                    prev_state_val = prev_rec.state
                    new_state = prev_state_val
                    # 压力驱逐（等价状态机驱逐边；缺失板块无当日 tier，保守用强组容忍 3）
                    if (
                        prev_state_val == "active_mainline"
                        and new_ep >= self._MAINLINE_STRONG_TOLERANCE_DAYS
                    ):
                        new_state = "exhausted_mainline"
                    # 长期数据缺失 → 熄灭（防僵尸板块永久占用通道 4 名额）
                    if new_ep >= self._MISSING_EXTINGUISH_DAYS:
                        new_state = "neutral"
                    new_is_mainline = new_state in (
                        "active_mainline", "exhausted_mainline", "high_position_cooling",
                    )
                    # 补写：状态按驱逐/熄灭判定，exit_pressure +1，其余字段从 prev 复制
                    # ── 写入侧 NUL 剥离（v3.5 S1：防止幽灵行污染）──
                    name_clean = name.strip().strip('\x00') if name else ""
                    session.add(Node2SectorTrendRecord(
                        sector_code=code,
                        sector_name=name_clean,
                        tracking_date=today,
                        state=new_state,
                        dwell_days=(
                            int(getattr(prev_rec, "dwell_days", 0) or 0) + 1
                            if new_state == prev_state_val else 1
                        ),
                        rrg_ratio_ema=getattr(prev_rec, "rrg_ratio_ema", None),
                        rrg_momentum_ema=getattr(prev_rec, "rrg_momentum_ema", None),
                        unified_signal=self._state_to_signal(new_state),
                        lifecycle_phase=prev_rec.lifecycle_phase,
                        strength_score=getattr(prev_rec, "strength_score", 0),
                        is_mainline=new_is_mainline,
                        mainline_days=(
                            int(getattr(prev_rec, "mainline_days", 0) or 0) + 1
                            if new_is_mainline else 0
                        ),
                        ambush_days=prev_rec.ambush_days,
                        exit_pressure_days=new_ep,
                        trend_direction=prev_rec.trend_direction,
                    ))
                    gap_count += 1
                if gap_count > 0:
                    logger.info(
                        "[Node 2] 缺失板块补写: %d 个前日 actionable 板块 "
                        "（K线获取失败，exit_pressure+1）", gap_count,
                    )

                session.commit()

            # ── 波次校准埋点（v3.5 S2：次日补齐涨跌幅，供 S5 校准脚本消费）──
            self._write_wave_calibration(sectors, today)

            logger.info("[Node 2] 板块趋势持久化完成: %d 个板块", len(sectors))

        except Exception as e:
            logger.error("[Node 2] 板块趋势持久化失败: %s", e, exc_info=True)

    def _write_wave_calibration(self, sectors: list[SectorDetail], today: date) -> None:
        """写入波次校准埋点到 JSONL（v3.5 S2）。

        格式：每行 {date, sector_code, sector_name, wave_days, strength, wave_peak}
        次日运行由 calibrate_wave_coeff.py 补齐涨跌幅后消费。
        """
        import json
        from pathlib import Path

        # 仅主线族板块参与校准（非主线无波次概念）
        mainline_sectors = [s for s in sectors if s.state in MAINLINE_FAMILY_STATES]

        if not mainline_sectors:
            return

        try:
            # 数据目录：data/wave_calibration.jsonl
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            calibration_path = project_root / "data" / "wave_calibration.jsonl"
            calibration_path.parent.mkdir(parents=True, exist_ok=True)

            # 同日重跑幂等（审核 P2 修复）：先剔除当日旧样本，再以本次运行结果为准重写，
            # 避免同日重复埋点污染校准统计；损坏行（无法解析）原样保留交人工处理。
            today_str = today.isoformat()
            kept_lines: list[str] = []
            if calibration_path.exists():
                with open(calibration_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.rstrip("\n")
                        if not line.strip():
                            continue
                        try:
                            if json.loads(line).get("date") == today_str:
                                continue
                        except (json.JSONDecodeError, AttributeError):
                            pass
                        kept_lines.append(line)

            new_lines = []
            for sector in mainline_sectors:
                record = {
                    "date": today_str,
                    "sector_code": sector.code,
                    "sector_name": sector.name,
                    "wave_days": sector.wave_days or 0,
                    "strength": sector.strength_score,
                    "wave_peak": sector.wave_peak_strength or sector.strength_score,
                }
                new_lines.append(json.dumps(record, ensure_ascii=False))

            with open(calibration_path, "w", encoding="utf-8") as f:
                for line in kept_lines + new_lines:
                    f.write(line + "\n")

            logger.debug(
                "[Node 2] 波次校准埋点: %d 条主线板块 → %s",
                len(mainline_sectors), calibration_path,
            )
        except Exception as e:
            logger.warning("[Node 2] 波次校准埋点写入失败（不阻塞主流程）: %s", e)

    def _calc_strength_trend(self, session, sector_code: str, current_strength: float) -> dict:
        """计算强度趋势（最近 7 天）。"""
        # A-P0-5: 排除当天记录，避免同日重跑重复追加 trajectory
        history = session.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.sector_code == sector_code,
            Node2SectorTrendRecord.tracking_date < date.today(),
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

        # A-P0-5: 排除当天记录，避免同日重跑多计天数
        history = session.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.sector_code == sector_code,
            Node2SectorTrendRecord.tracking_date < date.today(),
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

        # A-P0-5: 排除当天记录，避免同日重跑多计天数
        history = session.query(Node2SectorTrendRecord).filter(
            Node2SectorTrendRecord.sector_code == sector_code,
            Node2SectorTrendRecord.tracking_date < date.today(),
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

    def _calc_wave_days(
        self,
        session,
        sector_code: str,
        current_state: str,
        current_strength: float,
        today: date,
    ) -> tuple[int, Optional[date], float]:
        """计算波次计数器（v3.5 S2：以回撤结构判波）。

        判定语义全部委托模块级 decide_wave_days（与回填脚本共用，禁止分叉实现）。
        本方法只负责两段式取数：
        1) 最近一条历史记录（prev）；
        2) 仅当 prev 为 cooling/exhausted 且今日回归 active 时，
           追查出框前锚点（最近 active 日）与锚点至今的间隙记录（按交易日计）。
        深 V 冻结场景另落观察日志（S5 校准埋点另行写入）。
        """
        # 取数 1：最近一条历史记录（同一查询链 .filter().order_by().first()，兼容单测 mock）
        prev_record = (
            session.query(Node2SectorTrendRecord)
            .filter(
                Node2SectorTrendRecord.sector_code == sector_code,
                Node2SectorTrendRecord.tracking_date < today,
            )
            .order_by(Node2SectorTrendRecord.tracking_date.desc())
            .first()
        )

        anchor_record = None
        gap_records: list = []
        if (
            prev_record is not None
            and current_state == "active_mainline"
            and prev_record.state in ("high_position_cooling", "exhausted_mainline")
        ):
            # 取数 2：出框前锚点（最近一次 active 日）
            anchor_record = (
                session.query(Node2SectorTrendRecord)
                .filter(
                    Node2SectorTrendRecord.sector_code == sector_code,
                    Node2SectorTrendRecord.state == "active_mainline",
                    Node2SectorTrendRecord.tracking_date < today,
                )
                .order_by(Node2SectorTrendRecord.tracking_date.desc())
                .first()
            )
            if anchor_record is not None:
                # 取数 3：锚点（不含）至今日（不含）的间隙记录（交易日口径）
                gap_records = (
                    session.query(Node2SectorTrendRecord)
                    .filter(
                        Node2SectorTrendRecord.sector_code == sector_code,
                        Node2SectorTrendRecord.tracking_date > anchor_record.tracking_date,
                        Node2SectorTrendRecord.tracking_date < today,
                    )
                    .all()
                )

        wave_days, wave_start, wave_peak = decide_wave_days(
            current_state, current_strength, today,
            prev_record, anchor_record, gap_records,
        )

        # 深 V/浅回撤观察埋点：多日间隙但冻结继承（回撤不足 10 分）→ 留日志供复盘（读法 A）
        if (
            anchor_record is not None
            and len(gap_records) >= WAVE_GAP_MIN_DAYS
            and wave_days > 1
        ):
            logger.info(
                "[Node 2] 波次深V观察 %s: 间隙 %d 日未重置（冻结 wave_days=%d，锚点 %s）",
                sector_code, len(gap_records), wave_days, anchor_record.tracking_date,
            )

        return wave_days, wave_start, wave_peak

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
        """将板块状态转换为对外信号方向（三值 bullish/bearish/neutral）。

        注意：状态机主记忆已改用精确 state 列（不再依赖本函数回映射，2026-08-17 校准）。
        本函数仅用于归档/对外展示，保持三值兼容。
        """
        mapping = {
            "active_mainline": "bullish",
            "exhausted_mainline": "bearish",
            "high_position_cooling": "neutral",
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
        max_history_days = 0  # 追踪实际历史天数

        for sector in sectors[:5]:  # 最多 5 个板块
            trend_data = self._load_sector_trend_history(sector.code, days=7)
            if not trend_data or not trend_data.get("strength_trajectory"):
                continue

            has_trend_data = True
            trajectory = trend_data["strength_trajectory"]
            max_history_days = max(max_history_days, len(trajectory))

            # 格式化轨迹
            trajectory_str = " → ".join(f"{s:.0f}" for s in trajectory)

            lines.append(f"- {sector.name}: 强度轨迹 [{trajectory_str}]")

        if not has_trend_data:
            return ""

        # 根据实际历史天数动态调整提示
        if max_history_days < 3:
            lines.append(
                "\n注意：以上轨迹是个股级强度评分历史，不代表主线框持续性。"
                f"主线框刚建立（仅 {max_history_days} 天数据），尚无组级历史数据。"
                "禁止推断'连续N日主线'等结论。"
            )
        else:
            lines.append(
                f"\n注意：以上轨迹是个股级强度评分历史（共 {max_history_days} 天），"
                "不代表主线框组级持续性。禁止推断'连续N日主线'等组级结论。"
            )
        return "\n".join(lines)

    # =================================================================
    # LLM reasoning 生成
    # =================================================================

    @staticmethod
    def _rrg_label(sector: SectorDetail) -> str:
        """RRG 象限短标签（供 reasoning prompt 使用）。"""
        if sector.rrg is None:
            return ""
        _labels = {
            "leading": "领涨",
            "improving": "改善",
            "weakening": "走弱",
            "lagging": "落后",
        }
        return _labels.get(sector.rrg.quadrant, "")

    async def _generate_reasoning(self, ctx: SectorContext) -> Optional[str]:
        """调用 LLM 生成板块分析 reasoning 文本（纯总结，不影响信号）。"""
        try:
            from src.agent.llm_adapter import LLMToolAdapter
        except ImportError:
            logger.warning("LLMToolAdapter 不可用，跳过 reasoning 生成")
            return self._fallback_reasoning(ctx)

        mainline_names = ", ".join(
            f"{g.group_name}(技术{g.group_score:.0f}分,{self._rrg_label(g.members[0]) if g.members else ''})"
            for g in ctx.active_mainline
        ) or "无"
        exhausted_names = ", ".join(
            f"{s.name}(技术{s.strength_score:.0f}分,{self._rrg_label(s)})" for s in ctx.exhausted_mainline
        ) or "无"
        ambush_names = ", ".join(
            f"{s.name}(技术{s.strength_score:.0f}分)" for s in ctx.ambush
        ) or "无"
        defensive_names = ", ".join(
            f"{s.name}(技术{s.strength_score:.0f}分)" for s in ctx.defensive_rotation
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
            "重要：本节点仅基于技术强度视角（动量/换手/相对强度/宽度）分析，"
            "注入的分数均为技术强度分，不代表综合推荐强度（资金/消息/情绪未参与）。"
            "禁止使用“榜首”“最强”“第一主线”等绝对化表述；"
            "若某板块技术分显著领先，请表述为“技术强度领先”，"
            "并说明综合研判由后续聚合阶段给出。\n\n"
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
            near_miss_ambush=[],
            defensive_rotation=[],
            market_direction="分化",
            reasoning="[Mock] AI芯片+人形机器人为当前主线，算力板块已过热。半导体设备处于埋伏期。",
            data_quality=DataQuality(
                available_fields=["sector_kline", "sector_constituents"],
                missing_fields=[],
            ),
        )
