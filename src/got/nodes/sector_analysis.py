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
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

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

logger = logging.getLogger(__name__)


# =============================================================================
# 精选概念列表（~25-30 概念，按产业链组织）
# =============================================================================
# 关键词匹配东方财富概念板块名称，每个 category 取首个匹配板块。
# 板块名称随市场变化，关键词需定期维护。

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
        "power_grid": ["电力", "电力设备", "特高压", "智能电网", "电力物联网", "绿色电力", "虚拟电厂"],
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

# 5 维强度评分权重（总量 = 1.0）
STRENGTH_WEIGHTS: dict[str, float] = {
    "volume_price": 0.30,
    "price_momentum": 0.30,
    "market_breadth": 0.10,
    "relative_strength": 0.20,
    "capital_flow": 0.10,
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

# 国家级政策重点方向（主线分级加分项）
_POLICY_SUPPORTED_CHAINS = {"ai_mainline", "tech_defensive"}

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

# 并发请求限制
_CONCURRENT_FETCHES = 5

# K 线回溯天数
_KLINE_LOOKBACK_DAYS = 90


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

            # Step 2: 逐板块分析（强度 + 信号 + 状态 + 分级 + 生命周期）
            analyzed = []
            for sector_info in sectors_raw:
                detail = self._analyze_single_sector(
                    sector_info, market_context, quality,
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

            # Step 3: 按状态分类
            result = self._classify_sectors(analyzed, market_context)
            result.data_quality = quality

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
        """获取概念板块数据。

        数据源：通达信 TCP 协议（mootdx index_bars），不依赖东财 HTTP。
        流程：
        1. 从 TDX_BOARD_REGISTRY 按 category 获取板块代码
        2. 并发获取每个板块的 K 线（mootdx TCP P0）
        3. 从 K 线计算涨跌幅
        4. 获取 T-0 实时涨跌幅（mootdx TCP）
        """
        days = _KLINE_LOOKBACK_DAYS

        # Step 1: 从 TDX 映射表构建板块列表（按 category 分组）
        boards_by_cat = get_boards_by_category()
        matched: list[dict] = []

        for cat_name, boards in boards_by_cat.items():
            for board in boards:
                matched.append({
                    "code": board["code"],
                    "name": board["name"],
                    "category": cat_name,
                    "group": _CATEGORY_GROUP.get(cat_name, cat_name),
                    "change_pct": 0.0,
                    "up_count": 0,
                    "down_count": 0,
                    "turnover_rate": 0.0,
                })

        quality.available_fields.append("concept_board_list")
        logger.info(f"[Node 2] TDX 板块映射: {len(boards_by_cat)} 类, {len(matched)} 个板块")

        if not matched:
            quality.missing_fields.append("concept_universe_match")
            return []

        quality.available_fields.append("concept_universe_match")

        # Step 2: 并发获取 K 线（mootdx TCP P0）
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

                    # 从 K 线计算当日涨跌幅
                    if len(kline_df) >= 2:
                        latest_close = float(kline_df.iloc[-1]["close"])
                        prev_close = float(kline_df.iloc[-2]["close"])
                        if prev_close > 0:
                            m["change_pct"] = round(
                                (latest_close - prev_close) / prev_close * 100, 2
                            )

                    # 从最后一根 K 线提取 up_count / down_count（mootdx 特有字段）
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

        # 第一轮并发
        results = await asyncio.gather(*(fetch_one(m) for m in matched))
        sectors = [r for r in results if r is not None]
        failed = [m for m, r in zip(matched, results) if r is None]

        # 第二轮重试（TCP 偶尔超时）
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

        # Step 3: 获取 T-0 板块实时涨跌幅（mootdx TCP）
        realtime_changes = self._fetch_sector_realtime()
        for s in sectors:
            board_name = s.get("name", "")
            if board_name in realtime_changes:
                s["today_change"] = realtime_changes[board_name]

        logger.info(
            f"[Node 2] 完成: {len(sectors)}/{len(matched)} 个板块获取成功",
        )
        return sectors

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

    @staticmethod
    def _match_concept_universe(boards: list[dict]) -> list[dict]:
        """关键词匹配精选概念列表。

        匹配优先级：精确匹配 > 前缀匹配 > 子串匹配（覆盖 ≥30%）> 括号别名。
        每个 category 取涨幅最高的前 2 个匹配板块（boards 已按涨跌幅降序）。
        """
        matched: list[dict] = []
        seen_codes: set[str] = set()
        _TOP_N_PER_CATEGORY = 2

        for group_name, categories in CONCEPT_UNIVERSE.items():
            for cat_name, keywords in categories.items():
                cat_matches = []
                for board in boards:
                    code = board.get("code", "")
                    name = board.get("name", "")
                    if code in seen_codes:
                        continue
                    if _keyword_matches(name, keywords):
                        cat_matches.append(board)
                        if len(cat_matches) >= _TOP_N_PER_CATEGORY:
                            break

                for best_board in cat_matches:
                    seen_codes.add(best_board["code"])
                    matched.append({
                        "code": best_board["code"],
                        "name": best_board["name"],
                        "change_pct": best_board.get("change_pct", 0.0),
                        "up_count": best_board.get("up_count", 0),
                        "down_count": best_board.get("down_count", 0),
                        "turnover_rate": best_board.get("turnover_rate", 0.0),
                        "group": group_name,
                        "category": cat_name,
                    })

        return matched

    # =================================================================
    # 单板块分析流水线
    # =================================================================

    def _analyze_single_sector(
        self,
        sector_info: dict,
        market_context: MarketContext,
        quality: DataQuality,
    ) -> Optional[SectorDetail]:
        """对单个板块执行完整分析：强度 → 信号 → 状态 → 分级 → 生命周期。"""
        klines = sector_info["klines"]
        df = self._klines_to_df(klines)
        if df is None or len(df) < 15:
            return None

        # 1) 5 维强度评分
        indicators = self._calculate_strength(
            df, sector_info, market_context,
        )
        strength_score = self._calc_strength_score(indicators)

        # 2) 启动信号
        launch = self._detect_launch_signal(df, sector_info)

        # 3) 过热信号
        overheat = self._detect_overheat_signal(df, sector_info)

        # 4) 状态判定
        state = self._determine_state(
            strength_score, indicators, launch, overheat,
            sector_info, market_context, df,
        )

        # 5) 主线分级（仅 active/exhausted）
        tier: Optional[str] = None
        if state in ("active_mainline", "exhausted_mainline"):
            tier = self._classify_mainline_tier(sector_info, indicators)

        # 6) 生命周期（仅 active/exhausted）
        lifecycle: Optional[LifecycleInfo] = None
        if state in ("active_mainline", "exhausted_mainline"):
            lifecycle = self._classify_lifecycle(
                df, tier, overheat, launch,
            )

        # 7) 推荐操作
        action: Optional[str] = None
        if launch.get("triggered"):
            action = _LIFECYCLE_ACTION_MAP.get(
                lifecycle.phase if lifecycle else "mature",
            )
        elif state == "exhausted_mainline":
            action = "exit"

        return SectorDetail(
            code=sector_info["code"],
            name=sector_info["name"],
            state=state,
            strength_score=round(strength_score, 1),
            indicators=indicators,
            launch_signal=LaunchSignal(**launch) if launch.get("triggered") else None,
            lifecycle=lifecycle,
            tier=tier,
            recommended_action=action,
            today_change=sector_info.get("today_change"),
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

    def _calculate_strength(
        self,
        df: pd.DataFrame,
        sector_info: dict,
        market_context: MarketContext,
    ) -> dict[str, float]:
        """计算 5 维强度指标。

        各维度原始值 → [-1, 1] 归一化区间。
        """
        close = df["close"]
        volume = df["volume"]

        # --- 1. 量价关系 (volume_price) ---
        # 放量上涨/缩量回调 → +1; 放量下跌/缩量反弹 → -1
        vp = self._calc_volume_price(df)

        # --- 2. 价格动量 (price_momentum) ---
        # 近 5 日涨幅, clip[-15%, 15%], 归一化到 [-1, 1]
        if len(close) >= 5:
            raw_momentum = close.iloc[-1] / close.iloc[-5] - 1
            pm = clip(raw_momentum, -0.15, 0.15) / 0.15
        else:
            pm = 0.0

        # --- 3. 市场宽度 (market_breadth) ---
        # 板块内上涨比例（up_ratio），归一化到 [-1, 1]
        up_count = sector_info.get("up_count", 0)
        down_count = sector_info.get("down_count", 0)
        total = up_count + down_count
        up_ratio = up_count / total if total > 0 else 0.5
        mb = (up_ratio - 0.5) * 2.0  # 0→-1, 0.5→0, 1→+1

        # --- 4. 相对强度 (relative_strength) ---
        # 板块 20 日涨幅 - 大盘同期涨幅, clip[-15%, 15%], 归一化
        if len(close) >= 20:
            sector_gain_20d = close.iloc[-1] / close.iloc[-20] - 1
        else:
            sector_gain_20d = 0.0

        benchmark_gain = self._get_benchmark_gain(market_context)
        raw_rs = sector_gain_20d - benchmark_gain
        rs = clip(raw_rs, -0.15, 0.15) / 0.15

        # --- 5. 资金流向 (capital_flow) ---
        # 基于成交额变化估算, clip[-5亿, 5亿], 归一化
        cf_raw = self._estimate_capital_flow(df)
        cf = clip(cf_raw, -5.0, 5.0) / 5.0 if cf_raw is not None else 0.0

        return {
            "volume_price": round(clip(vp, -1.0, 1.0), 3),
            "price_momentum": round(clip(pm, -1.0, 1.0), 3),
            "market_breadth": round(clip(mb, -1.0, 1.0), 3),
            "relative_strength": round(clip(rs, -1.0, 1.0), 3),
            "capital_flow": round(clip(cf, -1.0, 1.0), 3),
        }

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
        """5 维加权 → 0-100 分。"""
        raw = sum(
            indicators[dim] * w
            for dim, w in STRENGTH_WEIGHTS.items()
        )
        # raw ∈ [-1, 1] → score ∈ [0, 100]
        return (raw + 1.0) / 2.0 * 100.0

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
    ) -> str:
        """按优先级链判定板块状态。

        优先级: 强启动信号 > 过热信号 > 持续强势 > 高位派发 > 埋伏期 > 弱启动信号 > 防守 > 中性

        修复：弱启动信号（weak_launch）不再直接判定为主线，需结合埋伏期条件判断。
        - strong_launch（≥0.75）→ active_mainline
        - weak_launch（0.55-0.75）→ 优先检查埋伏条件，满足则为 ambush
        """
        launch_level = launch.get("level", "none")

        # P1: 强启动信号 → active_mainline
        if launch_level == "strong_launch":
            return "active_mainline"

        # P2: 过热信号触发 → exhausted_mainline
        if any(overheat.values()):
            return "exhausted_mainline"

        # P3: 持续强势 (score>70, vp>0.5, breadth>0.7)
        # 修复：弱启动信号不应通过 P3 成为主线
        if (
            strength_score > 70
            and indicators["volume_price"] > 0.5
            and indicators["market_breadth"] > 0.7
            and launch_level != "weak_launch"
        ):
            return "active_mainline"

        # P4: 高位派发 (20d 涨幅 >15% 但量价关系转负 → 主力出货信号)
        close = df["close"]
        if len(close) >= 20:
            gain_20d = close.iloc[-1] / close.iloc[-20] - 1
            if gain_20d > 0.15 and indicators["volume_price"] < -0.3:
                return "exhausted_mainline"

        # P5: 埋伏期
        if self._check_ambush(df, indicators):
            return "ambush"

        # P5.5: 弱启动信号但不满足埋伏条件 → neutral（而非主线）
        # 弱启动 = 整理不充分或量能不足，即使强度高也不应算主线
        if launch_level == "weak_launch":
            return "neutral"

        # P6: 防守轮动
        if self._check_defensive(sector_info, market_context, df):
            return "defensive_rotation"

        return "neutral"

    @staticmethod
    def _check_ambush(df: pd.DataFrame, indicators: dict[str, float]) -> bool:
        """埋伏期条件（需满足 ≥2/3）：
        1. 20 日波动率 < 25%（年化）
        2. 最新成交量 > 1.3× MA20 量
        3. 布林宽度 < 历史 60 日低位

        修复：旧逻辑阈值 10% 对板块指数过严（A 股板块普遍 20-40%），
        改为 25% 更合理。
        """
        if len(df) < 20:
            return False

        close = df["close"]
        volume = df["volume"]

        # 条件 1: 20d 波动率 < 25%（年化）
        returns = close.pct_change().dropna()
        vol_20d = returns.tail(20).std() * np.sqrt(252) if len(returns) >= 20 else 1.0
        cond_volatility = vol_20d < 0.25

        # 条件 2: 量能温和放大
        vol_ma20 = volume.rolling(20).mean().iloc[-1] if len(volume) >= 20 else volume.mean()
        cond_volume = vol_ma20 > 0 and volume.iloc[-1] > vol_ma20 * 1.3

        # 条件 3: 布林收口（宽度低于历史 40% 分位）
        cond_boll = False
        if len(close) >= 60:
            ma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            bw_current = 2 * std20.iloc[-1] / ma20.iloc[-1] if ma20.iloc[-1] > 0 else 0
            bw_series = (2 * std20 / ma20).dropna()
            if len(bw_series) > 20 and not pd.isna(bw_current):
                cond_boll = bw_current < bw_series.quantile(0.4)

        return sum([cond_volatility, cond_volume, cond_boll]) >= 2

    @staticmethod
    def _check_defensive(
        sector_info: dict,
        market_context: MarketContext,
        df: pd.DataFrame,
    ) -> bool:
        """防守轮动条件（大盘走弱时，资金流入相对抗跌的板块）。

        触发条件（满足任一即视为弱势市场）：
        - 短期/中期信号 bearish
        - 大盘单日跌幅 > 3%（硬性暴跌触发）
        - 整体趋势 bearish

        板块筛选条件：
        - 20d 涨幅跑赢大盘（相对强度）
        - 距 60d 高点 ≤20%（排除深度崩盘板块）
        """
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
        """S/A/B 主线分级评分。

        | 标准                     | 分值 |
        |--------------------------|------|
        | 国家级政策支持            | +3   |
        | 市场规模 > 千亿           | +2   |
        | 产业链 ≥3 子行业          | +1   |
        | 有千亿市值龙头            | +1   |
        | 连续强势 >20 天           | +1   |

        S ≥ 5, A ≥ 3, B < 3

        修复：旧逻辑 capital_flow > 0.5 近似"千亿市值"过严，
        price_momentum > 0.5 近似"连续强势>20天"太短视。
        改为更合理的阈值和多维度验证。
        """
        score = 0
        group = sector_info.get("group", "")

        # 国家级政策支持
        if group in _POLICY_SUPPORTED_CHAINS:
            score += 3

        # 产业链子行业数
        categories = CONCEPT_UNIVERSE.get(group, {})
        sub_count = len(categories)
        if sub_count >= 3:
            score += 1

        # 市场规模（用资金强度近似，降低阈值）
        cf = indicators.get("capital_flow", 0)
        if cf > 0.3:
            score += 2
        elif cf > 0.1:
            score += 1

        # 连续强势（多维度验证：短期动量 + 相对强度）
        pm = indicators.get("price_momentum", 0)
        rs = indicators.get("relative_strength", 0)
        # 5日涨>3% 且 跑赢大盘>3% → 连续强势信号
        if pm > 0.2 and rs > 0.2:
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
    ) -> LifecycleInfo:
        """按主线等级映射生命周期阈值，判断当前阶段。"""
        effective_tier = tier or "B"
        launch_end, accel_end, mature_end = LIFECYCLE_THRESHOLDS[effective_tier]
        thresholds = {
            "launch_end": launch_end,
            "acceleration_end": accel_end,
            "mature_end": mature_end,
        }

        # 过热 → mature_exhausted
        if any(overheat.values()):
            return LifecycleInfo(
                phase="mature_exhausted",
                days_since_launch=self._calc_days_since_launch(df),
                thresholds=thresholds,
            )

        days = self._calc_days_since_launch(df)

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
            candidates = sorted(
                [s for s in sectors if s.code not in used_codes],
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
            active_mainline=active,
            exhausted_mainline=exhausted,
            ambush=ambush,
            defensive_rotation=defensive,
            top_by_score=top_by_score,
            market_direction=direction,
            notes=notes,
        )

    @staticmethod
    def _fill_fallback(
        sectors: list[SectorDetail],
        active: list[SectorDetail],
        exhausted: list[SectorDetail],
        ambush: list[SectorDetail],
        defensive: list[SectorDetail],
    ) -> Optional[SectorDetail]:
        """从 neutral 板块中选取最高分，补入 active_mainline（最多 1 个）。

        已在其他分类中的板块不重复补入。
        只选择有 tier 的板块（确保达到主线标准）。
        """
        used_codes = {
            s.code for lst in (active, exhausted, ambush, defensive) for s in lst
        }
        neutral_candidates = sorted(
            [s for s in sectors if s.state == "neutral" and s.code not in used_codes and s.tier is not None],
            key=lambda s: s.strength_score,
            reverse=True,
        )
        if not neutral_candidates:
            return None
        return neutral_candidates[0]

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
            f"{s.name}({s.strength_score:.0f}分)" for s in ctx.active_mainline
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

        prompt = (
            "你是一个专业的板块分析师。根据以下板块分析结果，撰写一段简洁的分析报告。\n\n"
            f"板块分析结果：\n"
            f"- 活跃主线：{mainline_names}\n"
            f"- 衰竭主线：{exhausted_names}\n"
            f"- 埋伏期：{ambush_names}\n"
            f"- 防守轮动：{defensive_names}\n"
            f"- 市场方向：{ctx.market_direction}\n"
            f"- 备注：{notes_text}\n\n"
            "请输出：\n"
            "1. 一段200字以内的分析文本，解释当前板块轮动格局\n"
            "2. 重点说明哪些板块是主线、持续性如何\n"
            "3. 提示哪些板块处于埋伏期，可能成为下一个主线\n"
            "4. 风险提示（哪些板块过热需要回避）\n"
            "5. 若备注中有\"评分未达阈值\"说明，需在报告末尾如实告知\n\n"
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
        if ctx.active_mainline:
            names = "、".join(s.name for s in ctx.active_mainline)
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
        parts.append(f"市场方向: {ctx.market_direction}。")
        # 附加说明（如评分不足警告）
        if ctx.notes:
            parts.extend(ctx.notes)
        return " ".join(parts)

    # =================================================================
    # Mock 输出
    # =================================================================

    def _mock_output(self) -> SectorContext:
        """Mock 输出（保持向后兼容）。"""
        return SectorContext(
            active_mainline=[
                SectorDetail(
                    code="BK1144", name="AI芯片", state="active_mainline",
                    strength_score=82.5,
                    indicators={
                        "volume_price": 0.75, "price_momentum": 0.08,
                        "market_breadth": 0.80, "relative_strength": 0.05,
                        "capital_flow": 0.60,
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
                ),
                SectorDetail(
                    code="BK1145", name="人形机器人", state="active_mainline",
                    strength_score=75.0,
                    indicators={
                        "volume_price": 0.65, "price_momentum": 0.05,
                        "market_breadth": 0.70, "relative_strength": 0.03,
                        "capital_flow": 0.50,
                    },
                    launch_signal=LaunchSignal(
                        triggered=True, level="weak_launch", score=0.60,
                    ),
                    lifecycle=LifecycleInfo(
                        phase="launch", days_since_launch=3,
                    ),
                    tier="B", recommended_action="aggressive_buy",
                ),
            ],
            exhausted_mainline=[
                SectorDetail(
                    code="BK1146", name="算力", state="exhausted_mainline",
                    strength_score=45.0,
                    indicators={
                        "volume_price": -0.30, "price_momentum": -0.05,
                        "market_breadth": 0.40,
                    },
                    lifecycle=LifecycleInfo(
                        phase="mature_exhausted", days_since_launch=45,
                    ),
                    tier="A", recommended_action="exit",
                ),
            ],
            ambush=[
                SectorDetail(
                    code="BK1147", name="半导体设备", state="ambush",
                    strength_score=55.0,
                    indicators={
                        "volume_price": 0.40, "price_momentum": 0.02,
                        "market_breadth": 0.50,
                    },
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
