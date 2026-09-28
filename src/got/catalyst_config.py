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
CATALYST_CONFIDENCE_GATE = 0.7      # TODO: 待校准 (当前样本量: 0) — 低于此值不入评分（只进日历展示标"参考"）
CATALYST_FULL_REFRESH_INTERVAL = 5  # 全量刷新间隔（交易日）
CATALYST_MAX_POOL_SIZE = 10         # 正式池上限（宁缺毋滥）
CATALYST_RESERVOIR_WEEKS = 4        # [遗留] 已被 RESERVOIR_EXPIRY_DAYS=10 替代，不再使用
RESERVOIR_EXPIRY_DAYS = 10           # 蓄水池驻留 ≥N 交易日 → 自动除名（与催化前瞻窗口14日≈10交易日对齐）
RESERVOIR_PROMOTION_MIN_DAYS = 2     # TODO: 待校准 — 升格最低驻留天数（太短无意义）
RESERVOIR_MAX_SIZE = 15              # 蓄水池容量上限（超出按优先级驱逐最老条目）
# Q3 (audit_20260922)：驱逐保护参数，与 RESERVOIR_PROMOTION_MIN_DAYS 一起校准
RESERVOIR_NEW_ENTRY_PROTECTION_DAYS = 3  # 新入保护：days_in_reservoir < N 的条目容量驱逐豁免
RESERVOIR_DAILY_NEW_ENTRY_MAX = 5        # TODO: 待校准 — 当日新入上限（实测日均新入 9-16 ≫ 容量，超出按入池强度降序截断）

# D-P3: 正式池水位目标（用于归档落盘，使偏差可见；不改任何阈值）
AMBUSH_POOL_TARGET_RANGE = (2, 6)    # 正式池水位目标区间（实测 12 天 0–2，下界 2 为略高于现状的最小要求）

# =============================================================================
# 影子模式开关（Track B agent 增强）
# =============================================================================

import os
SECTOR_ALIGN_SHADOW = os.environ.get("SECTOR_ALIGN_SHADOW", "false").lower() == "true"   # T5: 事件→板块对齐层（2026-09-01 转正；影子观察已验证无异常，回滚设 SECTOR_ALIGN_SHADOW=true）
NARRATIVE_CRITIC_SHADOW = os.environ.get("NARRATIVE_CRITIC_SHADOW", "true").lower() == "true"  # T2: 叙事校验者（影子 2 周）
AGENT_STOCK_REVIEW_ENABLED = os.environ.get("AGENT_STOCK_REVIEW_ENABLED", "false").lower() == "true"  # T1: Agent 终审
AGENT_STOCK_REVIEW_SHADOW = os.environ.get("AGENT_STOCK_REVIEW_SHADOW", "true").lower() == "true"  # T1: 影子模式（跑审查但不执行降级）
AGENT_STOCK_REVIEW_MAX = 2           # T1: Agent 终审最大调用数（R7 成本预算）

# =============================================================================
# LLM 精炼视野扩围（0831 短剧案例修复，决策 20）
# =============================================================================

REFINE_TOP_N = 10                    # 基础片：composite 降序前 N（原硬编码上提）
REFINE_MAX_SECTORS = 20              # 总量上限（基础片 + 埋伏候选追加片）

# =============================================================================
# LLM 调用可观测性（docs/got/archive/agent_glue_plan_0903.md §6.3 R3/R5 + §7.3 C3）
# =============================================================================

LLM_CALL_TIMEOUT_S = 60              # 单次 LLM 调用超时（秒）；glue 单次 max_tokens≈5000 需留足
AGENT_SEMANTIC_GLUE_ENABLED = os.environ.get("AGENT_SEMANTIC_GLUE_ENABLED", "true").lower() == "true"
# A6 阶段 1（2026-09-17）：SHADOW 默认翻为 false，glue 结果正式接入 _build_sector_intel 及下游消费路径。
# 回滚：GOT_A6_ENABLED=false 或 AGENT_SEMANTIC_GLUE_SHADOW=true 均可回到影子期「归档不消费」行为。
AGENT_SEMANTIC_GLUE_SHADOW = os.environ.get("AGENT_SEMANTIC_GLUE_SHADOW", "false").lower() == "true"
# C-P3: replay_mode 下允许运行 glue（默认关闭，保持 0903 A1「不产生任何持久化副作用」契约）
AGENT_SEMANTIC_GLUE_IN_REPLAY = os.environ.get("AGENT_SEMANTIC_GLUE_IN_REPLAY", "false").lower() == "true"
# C-P12 prompt A/B（D-C6）：glue 回放变体文案选择——"a"=保守版（现行口径+澄清句）/ "b"=全量归属版 /
# 其余值（默认空）= 现行生产 prompt。⚠️ 仅 replay_mode 下生效（aggregator 调用点强制），
# 生产链路永远忽略该变量；与 GLUE_REPLAY_VARIANT（落盘目录隔离）双保险配套，防「以为跑 B 实际跑 A」。
GLUE_PROMPT_VARIANT = os.environ.get("GLUE_PROMPT_VARIANT", "").strip().lower()
# C-P12 配套：glue LLM 输出 max_tokens（默认 16000 = 现行生产值，不改行为）。
# B 版（全量归属）输出体量更大，0902 实测撞 16000 顶被截断（finish_reason=length）；
# 需要时仅对回放日临时调高，且 A/B 同侧重跑须用同一值以保对照公平。
GLUE_MAX_TOKENS = int(os.environ.get("GLUE_MAX_TOKENS", "16000"))
# A6 灰度总开关：控制 4 个消费点（_build_sector_intel / refine_events_map / _llm_refine_sectors.adjustments /
# _synthesize_mainline_endurance）是否走 glue 分支。false = 完全回退到影子期行为。
GOT_A6_ENABLED = os.environ.get("GOT_A6_ENABLED", "true").lower() == "true"

# =============================================================================
# C-P13 阶段1：事件→板块「接地式召回-验证」管线（L1 检索接地 + L2 逐事件生成 + L3 CoVe 剪枝）
# 依据 docs/got/cp13_grounding_pipeline_research_20260926.md §3/§5.3。
# ⚠️ 仅 replay_mode 下生效（aggregator 调用点强制），生产链路永远忽略这些变量；
# 落盘用 GLUE_REPLAY_VARIANT=pipeline_l1l3 隔离，与 variant_a/variant_b 互不覆盖。
# =============================================================================

GLUE_PIPELINE_ENABLED = os.environ.get("GLUE_PIPELINE_ENABLED", "false").lower() == "true"
# L1 embedding（dashscope compatible-mode，OpenAI 协议）；未配 key 时管线降级不替换 base relations
GLUE_EMBEDDING_MODEL = os.environ.get("GLUE_EMBEDDING_MODEL", "text-embedding-v3")
GLUE_EMBEDDING_BASE_URL = os.environ.get(
    "GLUE_EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
GLUE_EMBEDDING_API_KEY = os.environ.get("GLUE_EMBEDDING_API_KEY", "")
GLUE_L1_TOP_K = int(os.environ.get("GLUE_L1_TOP_K", "8"))            # OPEN-1：冒烟标定 8（K=12 时 0903 房贷病例 top-K 混入 0.251 噪声「短剧游戏」，K=8 两病例全过）；矩阵跑批后终标
GLUE_EVENT_RELATION_CAP = int(os.environ.get("GLUE_EVENT_RELATION_CAP", "6"))  # 笔记 §4 单事件硬上限
GLUE_VERIFY_BATCH = int(os.environ.get("GLUE_VERIFY_BATCH", "10"))   # L3 CoVe 每批验证条数
GLUE_L2_CONCURRENCY = int(os.environ.get("GLUE_L2_CONCURRENCY", "4"))  # L2 逐事件调用并发上限

# =============================================================================
# §15 审查层配置（T1 候选股终审 + T1-H 持仓审查）
# =============================================================================

# Review profile 参数
AGENT_REVIEW_MAX_STEPS = 4             # 审查专用步数上限（全量 Agent 默认 10）
AGENT_REVIEW_TEMPERATURE = 0.2         # 审查专用温度（确定性输出）
AGENT_REVIEW_TIMEOUT_S = 30            # 审查专用超时（秒）
AGENT_REVIEW_TOOLS = ("get_realtime_quote", "get_daily_history", "search_stock_news")

# T1-H 持仓审查（§15.4，Phase 2 新增）
AGENT_HOLDING_REVIEW_ENABLED = os.environ.get("AGENT_HOLDING_REVIEW_ENABLED", "false").lower() == "true"
AGENT_HOLDING_REVIEW_SHADOW = os.environ.get("AGENT_HOLDING_REVIEW_SHADOW", "true").lower() == "true"
AGENT_HOLDING_REVIEW_MAX = 3           # 持仓审查最大只数（优先级：埋伏持仓 > P4 退出信号 > 浮亏）
AGENT_HOLDING_REVIEW_MIN_CONFIDENCE = 0.6  # 低于此阈值忽略 Agent 输出（与 T3 仲裁一致）

# =============================================================================
# Agent 催化剂先行提名（0831 短剧案例修复，决策 21）
# =============================================================================

AMBUSH_NOMINATION_MAX = 2            # 单次运行提名候选/采纳上限（R7 成本预算）
AMBUSH_NOMINATION_MIN_CONFIDENCE = 0.6  # Agent 提名采纳置信度下限
AMBUSH_NOMINATION_MULTIPLIER = 0.6   # 提名条目催化分折扣（未确认吸筹的保守折扣）
AMBUSH_NOMINATION_MIN_CONSECUTIVE = 2   # G3: 归档连续正流入下限（或 3 日均 >0 二选一）
AMBUSH_NOMINATION_DISABLED = os.environ.get("AMBUSH_NOMINATION_DISABLED", "").lower() == "true"  # 紧急回滚开关
ACCUMULATION_TOLERANCE = 0.5          # 吸筹闸噪声容忍度（亿）：净流出 < 0.5 亿视为持平，不算否决

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
