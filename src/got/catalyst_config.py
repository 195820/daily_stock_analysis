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
# 5. 【OPEN-D10 字段分工】direction = 价格方向（positive/negative/binary），供
#    aggregator 利空排除与 P6 兑现方向判定消费；事件类型（drift/fade/binary）只用
#    category_tag。二者为正交维度，不得混用（历史 13 条曾把类型塞进 direction，
#    导致 _check_direction 对 drift/fade 恒判 False、35% 样本误判）。

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
        # 锚定词守卫：新闻须含财报语义才可作为财报事件锚点（公司名裸命中会伪造财报日期）
        "anchor_keywords": ["财报", "业绩", "earnings", "季报", "指引", "展望"],
        "a_share_sectors": ["AI算力", "CPO", "光模块", "PCB", "服务器", "半导体"],
        "base_impact_level": 3,
        "direction": "binary",
        "category_tag": "drift",
    },
    {
        "id": 4,
        "global_event": "OpenAI/Google/Meta 发布会",
        "global_keywords": ["OpenAI", "Google I/O", "Meta", "GPT", "Gemini", "Llama", "AI发布会"],
        "a_share_sectors": ["AI应用", "传媒", "游戏", "AI算力"],
        "base_impact_level": 2,
        "direction": "positive",
        "category_tag": "fade",
    },
    {
        "id": 5,
        "global_event": "苹果财报/秋季发布会",
        "global_keywords": ["苹果", "Apple", "iPhone", "苹果发布会", "WWDC"],
        "a_share_sectors": ["消费电子", "果链", "光学光电子"],
        "base_impact_level": 2,
        "direction": "positive",
        "category_tag": "fade",
    },
    {
        "id": 6,
        "global_event": "特斯拉财报/Optimus 进展",
        "global_keywords": ["特斯拉", "Tesla", "Optimus", "马斯克", "FSD", "Robotaxi"],
        "a_share_sectors": ["汽车零部件", "人形机器人", "自动驾驶"],
        "base_impact_level": 2,
        "direction": "binary",
        "category_tag": "drift",
    },
    {
        "id": 7,
        "global_event": "台积电/ASML 财报",
        "global_keywords": ["台积电", "TSMC", "ASML", "光刻机"],
        "a_share_sectors": ["半导体设备", "中芯"],
        "base_impact_level": 2,
        "direction": "binary",
        "category_tag": "drift",
    },
    {
        "id": 8,
        "global_event": "礼来/诺和诺德财报及临床",
        "global_keywords": ["礼来", "Eli Lilly", "诺和诺德", "Novo Nordisk", "GLP-1", "减肥药"],
        "a_share_sectors": ["创新药", "CXO", "多肽"],
        "base_impact_level": 2,
        "direction": "binary",
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
        "direction": "binary",
        "category_tag": "drift",
    },
    {
        "id": 11,
        "global_event": "金价异动/地缘升级",
        "global_keywords": ["金价", "黄金", "地缘", "避险", "COMEX黄金", "伦敦金"],
        "a_share_sectors": ["黄金", "军工"],
        "base_impact_level": 2,
        "direction": "positive",
        "category_tag": "drift",
    },
    {
        "id": 12,
        "global_event": "SK 海力士/三星财报及 HBM 指引",
        "global_keywords": ["SK海力士", "三星", "HBM", "存储芯片", "DRAM", "NAND"],
        "a_share_sectors": ["存储芯片", "先进封装", "接口芯片", "PCB"],
        "base_impact_level": 3,
        "direction": "binary",
        "category_tag": "drift",
    },
    {
        "id": 13,
        "global_event": "SpaceX 星舰试飞/星链节点",
        "global_keywords": ["SpaceX", "星舰", "Starship", "星链", "Starlink"],
        "a_share_sectors": ["商业航天", "卫星互联网", "军工电子"],
        "base_impact_level": 2,
        "direction": "positive",
        "category_tag": "fade",
        "note": "偏主题情绪，跳票=落空退出",
    },
]


# =============================================================================
# 催化剂常量
# =============================================================================

CATALYST_WINDOW_DAYS = 14           # 前瞻窗口（日历日，约 10 交易日）
CATALYST_DRIFT_TAIL_TRADE_DAYS = 3  # 缺陷②：drift 型（个股/全球传导类）到期尾巴
                                     # 压到 3 交易日（以首次入库日为锚），旧传导不再霸屏
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
# 【2026-09-28 注】D-P3 已收口：本常量为唯一权威源，archive.py 读取落盘（不再硬编码 (2,6)）；
# 取值本身仍归 OPEN-D7（暂定 (2,6)，未另拍板）。
AMBUSH_POOL_TARGET_RANGE = (2, 6)    # 正式池水位目标区间（实测 12 天 0–2，下界 2 为略高于现状的最小要求）

# =============================================================================
# 影子模式开关（Track B agent 增强）
# =============================================================================

import os
SECTOR_ALIGN_SHADOW = os.environ.get("SECTOR_ALIGN_SHADOW", "false").lower() == "true"   # T5: 事件→板块对齐层（2026-09-01 转正；影子观察已验证无异常，回滚设 SECTOR_ALIGN_SHADOW=true）
# 项一②b（外网对标 2026-10-11）：macro_rule 事件是否也走 T5 语义对齐（扇出感知当日埋伏候选/蓄水池）。
# 【2026-10-15 负责人拍板翻 true】与 GOT_MACRO_FANOUT_ENABLED 同日开启开始影子读数；转正判据见
# verification.md 2026-10-15 节（题材类零事件率<30% + still_missed 降≥50%，连续≥10 交易日）。
# 回滚：GOT_MACRO_SECTOR_ALIGN_ENABLED=false。
GOT_MACRO_SECTOR_ALIGN_ENABLED = os.environ.get("GOT_MACRO_SECTOR_ALIGN_ENABLED", "true").lower() == "true"
# 项一②（外网对标）：宏观扇出扩到资源/周期/消费类真实板块名（旧伪类目“周期/消费”从不命中 concept_set）。
# 这会给 PMI/CPI 日多个板块注入宏观催化分。【2026-10-15 负责人拍板翻 true】开始影子读数（实测增益：
# PMI 扇出 1→5 板块、CPI 0→6 板块；与 glue L3 relations 互补不重复，分工口径见 backlog §1.H A-2 行）。
# 运维需比对 PMI/CPI 日板块排序漂移；转正判据见 verification.md 2026-10-15 节。
# 回滚：GOT_MACRO_FANOUT_ENABLED=false。
GOT_MACRO_FANOUT_ENABLED = os.environ.get("GOT_MACRO_FANOUT_ENABLED", "true").lower() == "true"
# 开关关闭时仅保留的“旧口径真实板块”（历史唯一能命中 concept_set 的三个宏观扇出目标）。
MACRO_FANOUT_LEGACY_BOARDS = frozenset({"券商", "银行", "房地产"})
NARRATIVE_CRITIC_SHADOW = os.environ.get("NARRATIVE_CRITIC_SHADOW", "true").lower() == "true"  # T2: 叙事校验者（影子 2 周）
AGENT_STOCK_REVIEW_ENABLED = os.environ.get("AGENT_STOCK_REVIEW_ENABLED", "false").lower() == "true"  # T1: Agent 终审
AGENT_STOCK_REVIEW_SHADOW = os.environ.get("AGENT_STOCK_REVIEW_SHADOW", "true").lower() == "true"  # T1: 影子模式（跑审查但不执行降级）
AGENT_STOCK_REVIEW_MAX = 2           # T1: Agent 终审最大调用数（R7 成本预算）

# =============================================================================
# Phase 6-1: 盘中 0→1 确认闸门（docs/got/plans/Phase6-1_盘中闸门影子期实施稿_20261015.md）
# =============================================================================
# 旁挂节点（Node 8）：三时点（0926/1125/1445）纯规则判定当日 GOT 候选，
# 产物落 data/got_daily/YYYYMMDD/intraday/，零决策影响、零 LLM 调用。
# INTRADAY_GATE_ENABLED=false：不跑（缺省，6-2 影子运营才翻 true）；
# INTRADAY_GATE_ENABLED=true + SHADOW=true：照跑只归档；
# ⚠️ INTRADAY_GATE_SHADOW=false 属 Phase 6-4 转正动作——影子期 I1-I6 双达标
# （0907 §6.2）+ 负责人批准前**禁止置 false**（写侧有防御性拦载兜底）。
INTRADAY_GATE_ENABLED = os.environ.get("INTRADAY_GATE_ENABLED", "false").lower() == "true"
INTRADAY_GATE_SHADOW = os.environ.get("INTRADAY_GATE_SHADOW", "true").lower() == "true"
INTRADAY_GATE_TOPN = int(os.environ.get("INTRADAY_GATE_TOPN", "8"))

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
# ⚠️ 2026-10-12 复核更正：本行原写「回滚：GOT_A6_ENABLED=false 或 AGENT_SEMANTIC_GLUE_SHADOW=true
# 均可回到影子期『归档不消费』行为」——**两条均不成立，切勿照原话操作**：
#   · GOT_A6_ENABLED=false 会在 aggregator.run() 入口直接 raise RuntimeError（fail-fast，不是回退）；
#   · AGENT_SEMANTIC_GLUE_SHADOW=true 只影响日志与 agent_glue.json 的 [SHADOW] 标记，
#     不拦 Pass 1.5 的三件套替换（_apply_glue_override_to_raw_data 无条件被调用）；
#   · AGENT_SEMANTIC_GLUE_ENABLED=false 同样 raise（glue 已是三件套唯一来源，关掉 = 让 Node 6 无数据可用）。
# 规则侧三件套已随 A6 阶段 2（2026-09-26）退役 ⇒ 已无「影子期行为」可回；撤销 A6 只能 git revert 转正提交。
# 详见 docs/got/backlog.md §4.3（已改写为真态）与 §8 #15。
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
# A6 总开关：控制 4 个消费点（_build_sector_intel / refine_events_map / _llm_refine_sectors.adjustments /
# _synthesize_mainline_endurance）是否走 glue 分支。⚠️ 缺省 true 且**不可热回退**：置 false 时 run() 直接
# raise RuntimeError，它不是回滚键（见上方 2026-10-12 更正）；仓内保留该变量仅作 fail-fast 守卫与测试标。
GOT_A6_ENABLED = os.environ.get("GOT_A6_ENABLED", "true").lower() == "true"

# =============================================================================
# 竞品经验补强（计划 2026-10-06）批次 D：持仓异动归因通道（news_pulse，一期纯观测）
# =============================================================================
# 默认关：先手动跑一周验证输出质量（计划 §D 纪律）。转正（推送接线）另立子项。
# 回滚：GOT_NEWS_PULSE_ENABLED=0。
GOT_NEWS_PULSE_ENABLED = os.environ.get("GOT_NEWS_PULSE_ENABLED", "0") == "1"
GOT_NEWS_PULSE_THRESHOLD = float(os.environ.get("GOT_NEWS_PULSE_THRESHOLD", "7.0"))  # 触发幅度（|当日涨跌%|）
GOT_NEWS_PULSE_MAX_STOCKS = int(os.environ.get("GOT_NEWS_PULSE_MAX_STOCKS", "3"))    # 单次运行硬上限（LLM 成本）

# =============================================================================
# 批次 G：产出前运行时自查 gate（output_review，一期只 warn 不阻断）
# =============================================================================
# 默认开（warn 态，零行为影响：只写日志 + 落 review.json）；回滚 GOT_OUTPUT_REVIEW=0。
# 阻断态（拦推送/终端总结，归档照常）需两周零误报后人工新增 ENABLE_BLOCKING 类开关。
GOT_OUTPUT_REVIEW = os.environ.get("GOT_OUTPUT_REVIEW", "1") == "1"

# =============================================================================
# 批次 B：轻量反偏见层 antibias（Phase 2 后对 actionable 板块一次小 LLM 调用，一期纯影子）
# =============================================================================
# 产 counter_thesis（板块 5 日回撤 8% 最可能的 1 条反方原因）+ red_line_hit（红线自报），
# 落 shadow_antibias.json。一期不参与排序/不进 warnings。缺省开（只观测，有 LLM 成本）。
# 回滚：GOT_ANTIBIAS_SHADOW=false。
GOT_ANTIBIAS_SHADOW = os.environ.get("GOT_ANTIBIAS_SHADOW", "true").lower() == "true"
GOT_ANTIBIAS_MAX_SECTORS = int(os.environ.get("GOT_ANTIBIAS_MAX_SECTORS", "8"))  # 单次 LLM 覆盖板块上限

# =============================================================================
# 批次 C1：数值双源交叉验证（抽样、非逐日全量，一期纯观测）
# =============================================================================
# 每日抽样 N 只持仓/候选做双源比对（源A=manager 实时行情 / 源B=腾讯批量涨跌幅），
# 偏差/不一致 → 落 consistency.json，**不改任何决策输入**。缺省 10；设 0=关闭。
# 回滚：GOT_CONSISTENCY_SAMPLE=0。阈值：符号相反或 |Δ|>GOT_CONSISTENCY_TOL（默认 1.0pp）报 alert。
GOT_CONSISTENCY_SAMPLE = int(os.environ.get("GOT_CONSISTENCY_SAMPLE", "10"))
GOT_CONSISTENCY_TOL = float(os.environ.get("GOT_CONSISTENCY_TOL", "1.0"))

# =============================================================================
# 批次 E1：轻量网络预检（一期仅观测，落 meta.json net_preflight，不据此降级）
# =============================================================================
# 缺省开（短超时 GET，纯观测零决策影响）；回滚 GOT_NET_PREFLIGHT=false。
# lite 手动/自动降级为独立子项（计划 §E1：自动降级延后到 lite/full 历史差异读数产出后）。
GOT_NET_PREFLIGHT = os.environ.get("GOT_NET_PREFLIGHT", "true").lower() == "true"

# =============================================================================
# C-P13 阶段1：事件→板块「接地式召回-验证」管线（L1 检索接地 + L2 逐事件生成 + L3 CoVe 剪枝）
# 依据 docs/got/archive/cp13_grounding_pipeline_research_20260926.md §3/§5.3。
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

# ── 埋伏通道 relations 补挂（A/B 根因修复，2026-09-30）──
# 事件名分桶查表落空时，用 glue.relations（board_code 键，对截断名/别名免疫）反查
# 补挂当日日历事件。索引当日 in-memory glue 优先，前日归档仅作独立调用回退
# （实测：埋伏名单日间轮换，前日 relations 对当日名单覆盖为 0）；事件回连后仍走
# CATALYST_CONFIDENCE_GATE 与 ★★★利空线，glue 自报 confidence/direction 不作数。
# 缺省 on，设 0 一键回滚。
AMBUSH_RELATIONS_WIRING = os.environ.get("GOT_AMBUSH_RELATIONS_WIRING", "1") == "1"

# ── 埋伏通道 G4 资金闸门冻结（工作流 A，2026-10-01）──
# 缺省 "0"（关闭＝现行为完全不变，合入不改生产）；上线动作＝在 .env 显式置 1 开启，
# 回滚置 0 或删变量即恢复。只做「无资金流数据(missing)」的冻结放行，坏信号(吸筹观察/净流出)照旧拒。
AMBUSH_G4_FREEZE = os.environ.get("GOT_AMBUSH_G4_FREEZE", "0") == "1"

# 吸筹档位→催化分乘数（防倒挂：missing 严格低于 weak，避免冻结放行条目反而高于有弱吸筹证据者）。
# 主循环与升格支路共用此表，禁止各写各的；查表直接用真实 accum_strength，不做映射改写。
AMBUSH_ACCUM_MULTIPLIER = {"strong": 1.0, "medium": 0.8, "weak": 0.6, "legacy": 1.0, "missing": 0.5}

# ── 工作流 C：持仓延续性管理（移动止盈 + 天数断点，2026-10-01）──
# 缺省关闭（合入不改生产）；上线动作＝在 .env 显式置 1，回滚置 0/删变量。
# 仅作用于非埋伏主线持仓（埋伏仓走 P4 催化剂退出，不叠加追踪，避免双重触发，§4.2 C-B1）。
LIFECYCLE_TRAILING_STOP_ENABLED = os.environ.get("GOT_TRAILING_STOP_ENABLED", "0") == "1"
TRAILING_ARM_PCT = float(os.environ.get("GOT_TRAILING_ARM_PCT", "0.10"))            # 曾盈利≥10% 才激活追踪
TRAILING_DRAWDOWN_REDUCE = float(os.environ.get("GOT_TRAILING_DD_REDUCE", "0.08"))  # 自高点回撤≥8% → reduce 减半锁盈
TRAILING_DRAWDOWN_SELL = float(os.environ.get("GOT_TRAILING_DD_SELL", "0.12"))      # 自高点回撤≥12% → sell 清仓

# mature 减仓天数断点（C-B2）：绝对断点不写死为确定性规则（§1.4），只做可配参数 + 观测分箱。
# 缺省 30＝不改生产；上线前盘点确认冲击可接受后置 14（§4.2 C-B2），并按 day 分箱积累 2–3 月回归校准。
LIFECYCLE_MATURE_REDUCE_DAY = int(os.environ.get("GOT_MATURE_REDUCE_DAY", "30"))

# =============================================================================
# 埋伏道拆分（ambush-lane-split-revision v2）
# =============================================================================
# 开关：GOT_AMBUSH_SPLIT_ENABLED=1 启用分道逻辑（默认开）；false 回滚到 A4 单道原始逻辑
# GOT_AMBUSH_SPLIT_ACTIVE=1 启用 Node 7 豁免（拦点#1 action lift / 拦点#8 tech 折算）。
# 已转正（2026-10-20 用户裁决跳过影子纪律），默认开；=0 回滚影子态（只留 trace 不改决策）。
# 注：运行时真消费点在 portfolio_decision.py（call-time 直读 env），本处为登记+测试直读。
GOT_AMBUSH_SPLIT_ENABLED = os.environ.get("GOT_AMBUSH_SPLIT_ENABLED", "1") == "1"
GOT_AMBUSH_SPLIT_ACTIVE = os.environ.get("GOT_AMBUSH_SPLIT_ACTIVE", "1") == "1"

# =============================================================================
# Step 0 裁决资料包——四组影子观测开关（C4 / E1 / E2 / F1）
# 全部缺省 1（影子开），零决策改动；=0 时对应观测逻辑跳过、零额外计算。
# =============================================================================
# C4 分道预算影子 trace（只读现状，零决策改动；=0 时 _allocate 内零额外计算）
GOT_LANE_BUDGET_SHADOW = os.environ.get("GOT_LANE_BUDGET_SHADOW", "1") == "1"
# E1 T3 verdict 路由影子（零数值调制，O3 纪律）
GOT_T3_ROUTING_SHADOW = os.environ.get("GOT_T3_ROUTING_SHADOW", "1") == "1"
# E2 cross_node_signals 四模式路由影子（bull_confirmation 带 blocked_by_regime_pin，#14 未拍板前只留痕）
GOT_XNODE_ROUTING_SHADOW = os.environ.get("GOT_XNODE_ROUTING_SHADOW", "1") == "1"
# F1 埋伏道一致性校验影子（三规则只产提案，埋伏池本体零改动）
GOT_AMBUSH_VALIDATION_SHADOW = os.environ.get("GOT_AMBUSH_VALIDATION_SHADOW", "1") == "1"

# 埋伏出场催化宽限影子（2026-10-16 RCA）：E14 转出时若日历有该板块 N 交易日内
# 合格催化事件，记 grace_would_hold 观测。零决策改动——状态机/池/蓄水池不变。
GOT_AMBUSH_EXIT_GRACE_SHADOW = os.environ.get("GOT_AMBUSH_EXIT_GRACE_SHADOW", "1") == "1"
# 宽限窗口（交易日，含首尾；复用 _count_trade_days 口径）。
# 10 = 低空案例实算 8 交易日 + 余量；N=5 会把动机案例归 false 污染转正判据（advisory 修正）。
AMBUSH_EXIT_GRACE_WINDOW_DAYS = 10

# D3 分道预算（三问裁决 Phase D；2026-10-20 用户裁决跳过影子纪律，直接生效，缺省 1）。
# =1 时 _allocate 的 per_stock_budget 改从 LANE_BUDGET_PARAMS 读分道加权全额投放：
#   per_stock_budget(c) = buy_budget × w(lane(c)) / Σ w(lane(top3))，Σ = buy_budget。
# 原接线判据（D1 前向超额区分度报告 + Layer 2 积样 ≥20 交易日/≥30 票）降级为事后验证：
# 备份归档按候选池道标签积样后重跑 scripts/backtest_lane_budget.py，区分度差即用本开关回滚。
# =0 时逐字节走现状「等分×ROLE_MULTIPLIER」路径（独立 kill-switch）。
GOT_LANE_BUDGET_ENABLED = os.environ.get("GOT_LANE_BUDGET_ENABLED", "1") == "1"

# =============================================================================
# #14 ④ regime jump penalty（backlog §7 #14 拍板 2026-10-15：结构性删除确认层）
# 两级开关，影子先行纪律：λ 标定回放 → 影子 5 交易日 → 切默认（归负责人判定）。
# =============================================================================
# 影子层（缺省 1）：每日并行计算 jump 模型 s_t = argmin_s[|raw−θ_s| + λ·1{s≠s_{t-1}}]，
# 落 shadow_regime_jump.json 观测；现行两日确认状态机零改动。
GOT_REGIME_JUMP_SHADOW = os.environ.get("GOT_REGIME_JUMP_SHADOW", "1") == "1"
# 生产切换（缺省 0）：=1 时状态机整体替换为 jump 模型（情形 1/3、pinned、迟滞豁免退役；
# transition_count 重定义为 20 日窗 jump 切换计数）。影子判据达标前禁止置 1。
GOT_REGIME_JUMP_PENALTY = os.environ.get("GOT_REGIME_JUMP_PENALTY", "0") == "1"
# jump 模型 λ（切换惩罚系数，单位=regime_score 量纲）。λ 标定回放（0910–1009 振荡窗）
# 产出选值后更新此处缺省；λ·1 为切换成本，过大钉死初态（λ=2.0→全程 0 次切换实测）。
REGIME_JUMP_LAMBDA = float(os.environ.get("GOT_REGIME_JUMP_LAMBDA", "0.25"))
# 阈值 θ_bull/θ_bear（backlog #14 拍板用固定 ±0.3 起步，与 _compute_market_regime 的
# raw→regime 三段阈值一致，EMA 基线标定留待影子读数后复核）
REGIME_JUMP_THETA_BULL = 0.3
REGIME_JUMP_THETA_BEAR = -0.3

# =============================================================================
# D2 分道预算参数登记（现状 ROLE_MULTIPLIER 数值登记化，非新参数）
# f() 真正形态参数（regime 交互项 / 催化供给项）须 D1 前向超额标定报告出区分度后
# 才允许写入此 dict（O3 纪律：未绑校准源的 f() 不接受落码）。
# =============================================================================
LANE_BUDGET_PARAMS: dict[str, float] = {
    "mainline": 1.0,
    "ambush": 0.55,
    "cooling": 0.3,
    "unknown": 0.55,
}

# G-A4: 埋伏道 TopK（同板块去重后取前 N）
AMBUSH_LANE_TOPK = 3
# G-A2: 催化剂窗口（日历日，约 10 交易日）
AMBUSH_LANE_CATALYST_WINDOW_DAYS = 14
# G-A2: 板块位置安全带（20日高点 + MA20 带状区间）
AMBUSH_LANE_SAFETY_HIGH = 1.03   # close ≤ 20d-high × 1.03
AMBUSH_LANE_SAFETY_LOW = 0.92    # close ≥ MA20 × 0.92
# G-A4: 吸筹强度权重（板块 3d 流百分位）
AMBUSH_LANE_ABSORPTION_WEIGHT = 0.2
# G-A3: 防接刀量比阈值（volume_ratio ≥ 3.0 且当日下跌 → fail）
AMBUSH_LANE_VOLUME_RATIO_KNIFE = 3.0

# 影响等级→权重映射
IMPACT_WEIGHT = {1: 0.3, 2: 0.6, 3: 1.0}

# F3（批次 F）：评分/阈值公式版本戳（UZI consensus_formula.version 模式）。权重/阈值表
# 任何调参必须 bump 本值并写入归档 meta.json，供回测按版本分组（与 git commit 互补：
# commit 定位代码，version 定位参数谱系；防"多版本逻辑混池统计导致结论失真"事故复发）。
# 2026-10-15.1 bump：分道预算 trace(C4) / T3 路由(E1) / cross_node 路由(E2) / 埋伏校验(F1) 四组影子观测字段加入。
# 2026-10-20.1 bump：D3 分道加权预算生效（per_stock_budget 口径变更）+ ACTIVE 转正（拦点#1 lift /
#   #8 tech 折算生效）+ D4 合并改 union 语义（Phase 2 选股池条数上限 10 → 13）。跨此版本的前向
#   统计不可与前序归档混池（预算口径与池规模双变）。
SCORING_FORMULA_VERSION = os.environ.get("GOT_SCORING_FORMULA_VERSION", "2026-10-20.1")

# =============================================================================
# 批次 A（竞品补强）：证据链与出处（一期只降不升，防换池）
# =============================================================================
# A2——按生产实际 source 字面量静态映射默认证据档（akshare/macro=交易所一手/宏观→confirmed；
# global/narrative=传导/LLM→reported）。source 字面量必须 ⊆ 生产实际取值集合（配单测断言防死代码）。
# ⚠ 一期未接入 LLM 逐事件 tier 输出（news 侧三步回连为后续子项），global/narrative 暂取 reported，
#   不标 rumor → 一期零决策变化。
CATALYST_SOURCE_TIER = {
    "akshare_earnings": "confirmed",
    "akshare_lockup": "confirmed",
    "macro_rule": "confirmed",
    "global_map": "reported",
    "llm_narrative": "reported",
}
# 证据档默认（未知 source 回退）
CATALYST_TIER_DEFAULT = "reported"
# 档位序（防倒挂断言用：confirmed > reported > rumor）
CATALYST_TIER_ORDER = ("confirmed", "reported", "rumor")
# 一期未接 LLM 逐事件 tier 接地（source_index 回连会改变 Node 4 抽取 prompt 输入、影响非确定性输出），
# 故默认**关**：关闭时抽取 prompt 与素材行逐字同改造前（零决策面变更），仅回连机制代码就位（source_index 缺失→自然 None）。
# 置 true 启用 url/时间接地（影子期需先做同输入 golden 抽取对照回归确认 major_events 漂移可接受）。
# 回滚：GOT_NEWS_EVIDENCE_GATING=false。
GOT_NEWS_EVIDENCE_GATING = os.environ.get("GOT_NEWS_EVIDENCE_GATING", "false").lower() == "true"
# A3——证据档→催化分乘数。一期 confirmed==reported==1.0（主力事件不变、不换池）；
# rumor<1.0 仅为二期无源事件标注预留（一期无事件被标 rumor，故零行为变化）。
EVIDENCE_TIER_MULTIPLIER = {"confirmed": 1.0, "reported": 1.0, "rumor": 0.8}

# Q1 修复（2026-10-16 RCA，用户裁决直连）：low 事件板块级 LLM 救援。
# 复审「全局 low 但板块 high」事件（affected_sectors 非空、非 negative），
# LLM 按三维判定标准（共同冲击/板块占比/增量信息，文献接地见 verification.md 补登 #6）
# 输出 sector_impact 三态；=high 的事件升级 medium 直连进链路
# （DB/NewsContext/impact_score/日历候选池）。sector_impact 全量落 shadow_low_events.json
# （质量观测+回滚审计）。=0 一键回滚原过滤行为。
GOT_LOW_SECTOR_RESCUE = os.environ.get("GOT_LOW_SECTOR_RESCUE", "1") == "1"
# 复审 LLM 参数（对齐 T2 critic 惯例：低温、批量、一次调用）
LOW_RESCUE_TEMPERATURE = 0.2
LOW_RESCUE_MAX_TOKENS = 2000

# 临近系数：事件距当前交易日的衰减
# 3~10 交易日 = 1.0（正常窗口）
# 1~2 交易日 = 0.6（anticipation 大半走完）
ANTICIPATION_DECAY_NEAR = 0.6
ANTICIPATION_DECAY_NORMAL = 1.0
ANTICIPATION_THRESHOLD_DAYS = 2  # ≤2 交易日视为"临近"

# 宏观日历规则（月度事件近似日期）
# 【项一② 2026-10-11】affected_sectors 统一走 Node 2 口径真实板块名（旧值「周期/消费」为伪类目，
# 从不命中 concept_set 致宏观催化恒无效）；只补可修的题材类缺口（有色/钢铁/化工/机械/黄金/消费），
# 不覆盖结构性事件源缺口板块（煤炭/旅游/运输/电信等，§7 拍板接受现状走纯技术通道）。
# direction 恒 binary（宏观数据结果未知）；事件类型用 category_tag。
MACRO_CALENDAR_RULES = [
    {
        "event_type": "macro",
        "title": "官方制造业PMI",
        "day_rule": "month_end",      # 月末最后一天或次日
        "impact_level": 2,
        "affected_sectors": ["券商", "有色金属", "钢铁", "化工", "工程机械"],
        "direction": "binary",
        "category_tag": "binary",
    },
    {
        "event_type": "macro",
        "title": "CPI/PPI",
        "day_rule": "mid_month",      # 每月 9-12 日
        "impact_level": 2,
        "affected_sectors": ["有色金属", "化工", "钢铁", "黄金概念", "白酒", "乳业"],
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
