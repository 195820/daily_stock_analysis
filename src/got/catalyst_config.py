"""
催化剂日历配置

全球→A股映射表 v1.2（13 条）+ 催化剂常量。
映射表中 a_share_sectors 为关键词列表，运行时与 CONCEPT_UNIVERSE 模糊匹配。
"""


# =============================================================================
# 全球→A股映射表 v1.2
# =============================================================================
# 使用方法：
# 1. 匹配 global_keywords 与新闻标题
# 2. 命中后展开 a_share_sectors 关键词 → 与 CONCEPT_UNIVERSE 交集 → 确定实际板块名
# 3. base_impact_level 为基线，LLM 可调一档但须给理由
# 4. 同板块同窗口命中多事件取最高等级、不叠加

GLOBAL_TO_A_SHARE_MAP = [
    {
        "id": 1,
        "global_event": "FOMC 议息/鲍威尔讲话",
        "global_keywords": ["FOMC", "鲍威尔", "Powell", "美联储", "Fed", "议息", "利率决议"],
        "a_share_sectors": ["贵金属", "有色", "券商"],
        "base_impact_level": 3,
        "direction": "binary",
        "category_tag": "binary",
    },
    {
        "id": 2,
        "global_event": "美国 CPI/PCE",
        "global_keywords": ["CPI", "PCE", "美国通胀", "美国物价", "核心CPI"],
        "a_share_sectors": ["贵金属", "有色", "券商"],
        "base_impact_level": 2,
        "direction": "binary",
        "category_tag": "binary",
    },
    {
        "id": 3,
        "global_event": "英伟达财报",
        "global_keywords": ["英伟达", "NVIDIA", "NVDA", "黄仁勋"],
        "a_share_sectors": ["AI算力", "CPO", "光模块", "PCB", "服务器", "半导体"],
        "base_impact_level": 3,
        "direction": "drift",
        "category_tag": "drift",
    },
    {
        "id": 4,
        "global_event": "OpenAI/Google/Meta 发布会",
        "global_keywords": ["OpenAI", "Google I/O", "Meta", "GPT", "Gemini", "Llama", "AI发布会"],
        "a_share_sectors": ["AI应用", "传媒", "游戏", "AI算力"],
        "base_impact_level": 2,
        "direction": "fade",
        "category_tag": "fade",
    },
    {
        "id": 5,
        "global_event": "苹果财报/秋季发布会",
        "global_keywords": ["苹果", "Apple", "iPhone", "苹果发布会", "WWDC"],
        "a_share_sectors": ["消费电子", "果链", "光学光电子"],
        "base_impact_level": 2,
        "direction": "fade",
        "category_tag": "fade",
    },
    {
        "id": 6,
        "global_event": "特斯拉财报/Optimus 进展",
        "global_keywords": ["特斯拉", "Tesla", "Optimus", "马斯克", "FSD", "Robotaxi"],
        "a_share_sectors": ["汽车零部件", "人形机器人", "自动驾驶"],
        "base_impact_level": 2,
        "direction": "drift",
        "category_tag": "drift",
    },
    {
        "id": 7,
        "global_event": "台积电/ASML 财报",
        "global_keywords": ["台积电", "TSMC", "ASML", "光刻机"],
        "a_share_sectors": ["半导体设备", "中芯"],
        "base_impact_level": 2,
        "direction": "drift",
        "category_tag": "drift",
    },
    {
        "id": 8,
        "global_event": "礼来/诺和诺德财报及临床",
        "global_keywords": ["礼来", "Eli Lilly", "诺和诺德", "Novo Nordisk", "GLP-1", "减肥药"],
        "a_share_sectors": ["创新药", "CXO", "多肽"],
        "base_impact_level": 2,
        "direction": "drift",
        "category_tag": "drift",
    },
    {
        "id": 9,
        "global_event": "油价异动（OPEC/地缘）",
        "global_keywords": ["OPEC", "原油", "油价", "WTI", "布伦特", "石油"],
        "a_share_sectors_positive": ["油气开采", "油服工程", "油运"],
        "a_share_sectors_negative": ["航空"],
        "base_impact_level": 3,
        "direction": "binary",
        "category_tag": "binary",
        "note": "突发 ★★★，例会 ★★",
    },
    {
        "id": 10,
        "global_event": "铜价/LME 库存",
        "global_keywords": ["铜价", "LME", "伦铜", "COMEX铜", "铜库存"],
        "a_share_sectors": ["铜", "矿山", "有色"],
        "base_impact_level": 2,
        "direction": "drift",
        "category_tag": "drift",
    },
    {
        "id": 11,
        "global_event": "金价异动/地缘升级",
        "global_keywords": ["金价", "黄金", "地缘", "避险", "COMEX黄金", "伦敦金"],
        "a_share_sectors": ["黄金", "军工"],
        "base_impact_level": 2,
        "direction": "drift",
        "category_tag": "drift",
    },
    {
        "id": 12,
        "global_event": "SK 海力士/三星财报及 HBM 指引",
        "global_keywords": ["SK海力士", "三星", "HBM", "存储芯片", "DRAM", "NAND"],
        "a_share_sectors": ["存储芯片", "先进封装", "接口芯片", "PCB"],
        "base_impact_level": 3,
        "direction": "drift",
        "category_tag": "drift",
    },
    {
        "id": 13,
        "global_event": "SpaceX 星舰试飞/星链节点",
        "global_keywords": ["SpaceX", "星舰", "Starship", "星链", "Starlink"],
        "a_share_sectors": ["商业航天", "卫星互联网", "军工电子"],
        "base_impact_level": 2,
        "direction": "fade",
        "category_tag": "fade",
        "note": "偏主题情绪，跳票=落空退出",
    },
]


# =============================================================================
# 催化剂常量
# =============================================================================

CATALYST_WINDOW_DAYS = 14           # 前瞻窗口（日历日，约 10 交易日）
CATALYST_CONFIDENCE_GATE = 0.7      # 低于此值不入评分（只进日历展示标"参考"）
CATALYST_FULL_REFRESH_INTERVAL = 5  # 全量刷新间隔（交易日）
CATALYST_MAX_POOL_SIZE = 10         # 正式池上限（宁缺毋滥）
CATALYST_RESERVOIR_WEEKS = 4        # 蓄水池板块连续 N 周无催化 → 自动除名

# 影响等级→权重映射
IMPACT_WEIGHT = {1: 0.3, 2: 0.6, 3: 1.0}

# 临近系数：事件距当前交易日的衰减
# 3~10 交易日 = 1.0（正常窗口）
# 1~2 交易日 = 0.6（anticipation 大半走完）
ANTICIPATION_DECAY_NEAR = 0.6
ANTICIPATION_DECAY_NORMAL = 1.0
ANTICIPATION_THRESHOLD_DAYS = 2  # ≤2 交易日视为"临近"

# 宏观日历规则（月度事件近似日期）
MACRO_CALENDAR_RULES = [
    {
        "event_type": "macro",
        "title": "官方制造业PMI",
        "day_rule": "month_end",      # 月末最后一天或次日
        "impact_level": 2,
        "affected_sectors": ["券商", "周期"],
        "direction": "binary",
        "category_tag": "binary",
    },
    {
        "event_type": "macro",
        "title": "CPI/PPI",
        "day_rule": "mid_month",      # 每月 9-12 日
        "impact_level": 2,
        "affected_sectors": ["周期", "消费"],
        "direction": "binary",
        "category_tag": "binary",
    },
    {
        "event_type": "macro",
        "title": "社融/M2",
        "day_rule": "mid_month_late",  # 每月 10-15 日
        "impact_level": 2,
        "affected_sectors": ["券商", "银行"],
        "direction": "binary",
        "category_tag": "binary",
    },
    {
        "event_type": "macro",
        "title": "LPR 报价",
        "day_rule": "day_20",          # 每月 20 日
        "impact_level": 3,
        "affected_sectors": ["银行", "券商", "房地产"],
        "direction": "binary",
        "category_tag": "binary",
    },
]
