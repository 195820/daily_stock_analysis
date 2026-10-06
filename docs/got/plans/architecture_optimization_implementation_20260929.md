# GOT 架构优化 · 明细实施报告

> **来源**：本报告将《GOT 各模块「意图 → 派生优化」架构蓝图》（含本轮 exa 检索证据与代码核验自审）转为**可执行工作项（WI）级**实施说明。
> **性质**：实施设计（仍不含代码提交）。每个 WI 给出：现状代码事实 → 改动点（文件·函数）→ 灰度开关 → 验收标准 → 风险·回滚 → 依赖。
> **基准**：2026-09-29 工作区快照。行号为**现状锚点，随版本可能漂移**，以函数名/符号为准。
> **对账**：与 [`backlog.md`](../backlog.md)、[`verification.md`](../verification.md) 交叉引用见 §9。

---

## 0. 阅读指引与全局工程约束

### 0.1 六条贯穿性工程红线（所有 WI 共用）
1. **三态开关惯例**：所有决策类改动默认 `shadow`（只落观测字段、不改既有决策），`enabled` 转正需**影子期读数 + 负责人裁决**。环境变量命名沿用 `GOT_<TOPIC>_SHADOW` / `GOT_<TOPIC>_ENABLED`（对齐 `GOT_DISAGREEMENT_SHADOW`/`GOT_WINRATE_*`）。
2. **单 WI 单 commit、可独立回退**：每个 WI 是一个不跨项耦合的原子提交，旧路径在开关关闭时**逐字节不变**。
3. **候选池归零防护**：禁止新增多条件 AND 硬闸门；新判据一律走"评分 + 排名截断"，并配"候选数=0"的回归用例（历史 P0 教训）。
4. **冷启动默认值不倒挂**：新引入的连续评分字段必须有中性默认值，且默认值不得使新候选优于既有候选（历史"评分倒挂"陷阱）。
5. **A 股可成交性前置（见 §2 GATE-1）**：任何产生 sell/reduce 的逻辑，必须先有可成交性投影层，否则信号在跌停封板/T+1 下不可执行，回测与实盘双失真。
6. **阈值来自数据、不来自拍脑袋**：任何新阈值先占位为"待 P6 标定"，接入 §5 WI-P0-5 回归闭环后再固化。

### 0.2 WI 编号
`GATE-*`（前置约束，先于 P0）、`WI-P0-*`、`WI-P1-*`、`WI-P2-*`。

---

## 1. 工作项总览矩阵

| WI | 主题 | 主要模块 / 文件 | 优先级 | 硬依赖 | 灰度开关 |
|---|---|---|---|---|---|
| GATE-1 | 卖出侧可成交性投影层（C1） | `portfolio_decision.py` 退出链 | 前置 | — | `GOT_TRADABILITY_GATE` |
| GATE-2 | 最优停止的 pooled/分层估计方案（C2） | `catalyst_realization.py` + 新标定脚本 | 前置 | WI-P0-5 | —（离线） |
| P0-1 | 清仓 = 概率化最优停止引擎（轴三①） | `portfolio_decision.py` `_lifecycle_sell_signal` | P0 | GATE-1,2 | `GOT_OPTIMAL_STOP` |
| P0-2 | lifecycle 时间阈值前移（轴二①） | `portfolio_decision.py` / `sector_analysis.py` | P0 | WI-P0-5 | 复用 P0-1 |
| P0-3 | 卖出信号 → 执行确定性接线（约束执行） | `portfolio_decision.py` `_allocate` | P0 | — | `GOT_EXPOSURE_ENFORCE` |
| P0-4 | 真实 sizing 补信号强度连续缩放 | `portfolio_decision.py` `_allocate` | P0 | WI-P0-5(ECE) | `GOT_SIGNAL_SIZE` |
| P0-5 | P6 → 阈值回归校准闭环 | `catalyst_realization.py` + 标定脚本 | P0 | — | 离线产物 |
| P1-1 | Node1 sizing 理论上限（Kelly + vol-target） | `market_analysis.py` / `portfolio_decision.py` | P1 | GATE-2 | `GOT_KELLY_CAP` |
| P1-2 | 转换期显式减仓带 | `market_analysis.py` regime 分支 | P1 | P1-1 | 复用 P1-1 |
| P1-3 | Node3 资金多维重建 + 唯一键 | `capital_analysis.py` | P1 | — | `GOT_CAPITAL_MULTIDIM` |
| P1-4 | Node2 二波识别（fresh momentum 豁免老化） | `sector_analysis.py` | P1 | — | `GOT_SECOND_WAVE` |
| P1-5 | 过热→破位优先级 + change-point 切换 | `sector_analysis.py` | P1 | WI-P0-5 | `GOT_OVERHEAT_PRIORITY` |
| P1-6 | Node6 闸门评分化（G1–G4） | `aggregator.py` | P1 | — | `GOT_GATE_SCORING` |
| P1-7 | 家族词表歧义治理（O2） | `family_keywords.py` + 兜底 | P1 | — | `GOT_FAMILY_EMBED_FALLBACK` |
| P1-8 | disagreement 转正 + memory 校准（O5） | `disagreement.py` / `portfolio_decision.py` | P1 | — | 既有 `GOT_DISAGREEMENT_*` |
| P1-9 | 真实交易日历接入（O3a） | `catalyst_calendar.py` `_count_trade_days` | P1 | — | `GOT_REAL_TRADE_CAL` |
| P2-1 | 账户级熔断扩展（连亏冷却/周月回撤） | `portfolio_decision.py` 3b 骨架 | P2 | P1-8 | `GOT_ACCOUNT_BREAKER` |
| P2-2 | 统一止损真相源（C3） | `portfolio_decision.py` 两套止损 | P2 | P0-1 | `GOT_STOP_UNIFY` |
| P2-3 | expected_return 动态化（C4） | `portfolio_decision.py` `_allocate` | P2 | P0-1 | 复用 P0-1 |
| P2-4 | 利空事件 risk_events 接入退出（O3b） | `catalyst_calendar.py` + 新通道 | P2 | GATE-1 | `GOT_RISK_EVENTS` |
| P2-5 | Node5 情绪接入清仓 | `sentiment_analysis.py` + `disagreement.py` | P2 | P1-8 | 复用 P1-8 |
| P2-6 | Node4 新闻反应失败反向闸 | `news_analysis.py` / `portfolio_decision.py` | P2 | GATE-1 | `GOT_NEWS_REACTION_GATE` |
| P2-7 | 选股漏斗分数化 + DDE 降级权重重分配 | `stock_screener.py` | P2 | — | `GOT_FUNNEL_SCORED` |
| P2-8 | orchestrator per-source availability（O1） | `orchestrator.py` 各 node | P2 | GATE-1 | `GOT_AVAILABILITY_MAP` |
| P2-9 | persistence 跨日一致性断言（O4） | `persistence.py` | P2 | — | `GOT_CONTEXT_CONSISTENCY` |
| P2-10 | 轮动换手成本护栏 | `sector_analysis.py` 轮动通道 | P2 | — | `GOT_TURNOVER_PENALTY` |
| P2-11 | Node2 资金维度叠加（RRG 第三轴） | `sector_analysis.py` | P2 | P1-3 | 复用 P1-3 |
| P2-12 | Node6 蓄水池容量自适应 | `aggregator.py` 蓄水池 | P2 | — | `GOT_RESERVOIR_ADAPTIVE` |

---

## 2. 前置约束（GATE，先于 P0 落地）

### GATE-1 · 卖出侧可成交性投影层（对应 C1）
- **现状代码事实**：`portfolio_decision.py` 全文无 `涨停/跌停/limit_up/limit_down/T+1/tradable/can_sell` 校验（仅 `:3027` 一条无关日志）；退出链 `_lifecycle_sell_signal`/`_ambush_exit_signal`/`_drift_action_with_grace` 直接产 sell/reduce，不判当日能否成交。买入侧仅选股 R0 用 T-1 涨幅剔涨停。
- **改动点**：
  - 新增纯函数 `_tradability_check(symbol, target_date, action) -> {executable: bool, reason: str}`，输入当日盘口/前收/涨跌停价，判：跌停一字（sell 不可）、T+1 当日新建仓（sell 不可顺延）、涨停一字（buy 不可）。数据源复用现有实时行情获取路径（`_get_realtime_price` 同级）。
  - 在 `_allocate` 生成 sell/reduce 后、落 `TradeAllocation` 前调用：不可成交 → 不置零，而是打标 `pending_exit=True` + `blocked_reason`，顺延复核。
  - 新建仓当日在持仓层记 `entry_date`，T+1 判定据此屏蔽当日卖出候选。
- **灰度开关**：`GOT_TRADABILITY_GATE`（`shadow` 默认：只给 allocation 打 `pending_exit` 观测标签，不改 quantity/decision；`enabled`：真正把不可成交卖出转为顺延）。
- **验收标准**：构造"跌停一字持仓 + 触发 sell"用例 → shadow 记录 `blocked_reason=limit_down`，enabled 时该卖单不进入当日成交集合；构造"T+1 当日建仓 + 当日 sell 信号" → 被拦截顺延；构造"行情 API 超时/返回 None" → shadow 与 enabled 下均**不拦截、不抛异常**，落 `blocked_reason=quote_unavailable` 告警标签。红线：全开关关闭时既有 allocation 输出逐字节不变。
- **风险·回滚**：风险在行情源缺涨跌停价 → 降级为"不拦截、仅告警"，绝不因取不到盘口而误判为"可成交"。回滚：关开关即回旧路径。
- **依赖**：无（本身是其它退出类 WI 的前置）。

### GATE-2 · 最优停止的 pooled/分层估计方案（对应 C2）
- **现状代码事实**：主线寿命 93.5%<7 天；`AMBUSH_DRIFT_*` 全标"样本量 0"；lifecycle 阈值为经验值。单持仓日线观测点 ≤7，无法逐持仓估后验。
- **改动点**：新增离线标定脚本 `scripts/calibrate_optimal_stop.py`：汇集所有历史"主线段"日收益 → 跨板块 pooled 拟合漂移/波动/切换点 θ 的先验分布 → 产出分层先验查表（按 板块家族 × regime 分箱），落 `data/wave_calibration.jsonl` 同目录新工件 `optimal_stop_priors.json`。
- **灰度开关**：无（离线产物）；消费方 P0-1 读该工件。
- **验收标准**：产出 prior 表的样本量分箱计数 ≥ 阈值；小样本箱回退到全局箱（ hierarchical shrinkage），不得为空。
- **风险·回滚**：分布误设 → P0-1 冷启动仍退回规则化 lifecycle。
- **依赖**：WI-P0-5（需要 P6 realized 收益数据喂分布）。

---

## 3. P0 工作项（意图闭环，最高杠杆）

### WI-P0-1 · 清仓 = 概率化最优停止引擎（轴三①）
- **现状代码事实**：`_lifecycle_sell_signal`（`:2124`）是 5 态硬编码查表：硬止损 → `mature_exhausted` 清仓 → `overheat>0.7` 清仓 → `late_stage` 减仓 → `mature & day>30` 减仓 → `launch/accel` 持有 → `mature & pnl>10%` 减仓。`mature>30 天` 与"93.5% 主线 day7 前结束"严重错配。
- **改动点**：
  - 新增 `_exit_posterior(holding, observations) -> P(θ≤t | obs)`：以破位/背离/资金流出/过热分为观测证据，按 GATE-2 的 pooled 先验做贝叶斯更新（可用共轭近似，避免在线 MCMC）。
  - `_lifecycle_sell_signal` 保留为 `legacy` 分支；新增 `posterior` 分支：`P(θ≤t) > 阈值` → sell，介于警戒带 → reduce。开关控制走哪条。
  - 停止判定输出"决策时刻"，成交与否交 GATE-1。
- **灰度开关**：`GOT_OPTIMAL_STOP`（`shadow`：并行计算后验、只落 `exit_posterior` 观测字段，action 仍走 legacy；`enabled`：后验接管 action）。
- **验收标准**：影子期对比"后验 vs legacy"在历史衰竭案例（0825 半导体等）的退出日差；要求后验不晚于 legacy 触发且候选池不归零；分歧率、误清仓率读数入库。
- **风险·回滚**：后验误清（早退丢 alpha）→ 阈值保守 + 分层先验收紧；样本不足 → 自动退回 legacy 分支。回滚：关开关，legacy 立即接管。
- **依赖**：GATE-1、GATE-2。

### WI-P0-2 · lifecycle 时间阈值前移（轴二①）
- **现状代码事实**：`mature & day>30` 减仓档几乎永不触发；day 分箱阈值是写死经验值。
- **改动点**：将 `mature`/`late_stage` 的 day 阈值改由 WI-P0-5 回归结果给出（day4–5 分箱方向），从常量提为可配置并接入标定；`sector_analysis.py` 生命周期状态切换天数同步。
- **灰度开关**：复用 `GOT_OPTIMAL_STOP`（阈值来源切换随 P0-1 转正）。
- **验收标准**：标定后阈值使 `mature` 减仓档在历史数据上有非零且合理触发率；对照"30 天"旧值给出触发率差。
- **风险·回滚**：过拟合近端行情 → 分箱带内用平滑而非硬跳变；保留旧常量回退位。
- **依赖**：WI-P0-5。

### WI-P0-3 · 卖出信号 → 执行确定性接线（约束执行 V2）
- **现状代码事实**：`_allocate`（`:3720`）真实仓位 = `risk.position_limit × total_equity` → `buy_budget` 三等分 top3 × `ROLE_MULTIPLIER`；`sector_exposure_limits`（`:4093` 自陈"全仓无执行消费者，仅用于落盘记录"）、`risk_budget` 均为装饰。
- **改动点**：二选一（对齐"接线 or 删除"）：
  - **接线路线**：`_allocate` 消费 `sector_exposure_limits` 作为买入可行集投影——超限时按 limit 截断该板块买入额、余额回流现金或分给未超限候选；卖出侧经 GATE-1 后仍受组合层敞口约束。**⚠️ confidence 隔离前置**：`sector_exposure_limits` 的计算式（`:374`）含 `conf_w = sector_intel.confidence` 因子，一旦接线，未经校准的 confidence 会**间接**成为 sizing 上界决定因子，与 WI-P0-4"confidence 不直接进 sizing"冲突。故接线时 `conf_w` 须先经 P0-5 ECE 校准达标；**未达标前 `conf_w` 固定为 1.0（中性）**，避免未校准置信度间接驱动仓位。
  - **删除路线**：若暂不接线，则在 schemas/落盘明确标注"参考值·非约束"，并从决策路径移除任何误读，杜绝"看起来在控风险"的装饰假象。
- **灰度开关**：`GOT_EXPOSURE_ENFORCE`（shadow 记录"若强制会截断多少"，enabled 真截断）。
- **验收标准**：enabled 下组合板块敞口 ≤ 各 limit；shadow 读数给出"超限发生频次/金额"。候选池归零红线：截断后买入候选仍 ≥1（否则放宽）。
- **风险·回滚**：截断逻辑与"高切低轮动"预算冲突 → 明确优先级：GATE-1 顺延 > 敞口截断 > 轮动回补。回滚：关开关。
- **依赖**：GATE-1（卖出侧）。

### WI-P0-4 · 真实 sizing 补信号强度连续缩放
- **现状代码事实**：`composite_score` 仅用于 top3 排序、**不参与配钱**；`conf_w` 只喂装饰 `sector_exposure_limits`，对真实 sizing 零影响；真实 sizing = 等分 top3 × role 乘数。
- **改动点**：在 `_allocate` 把"等分"改为"按 `composite_score` 归一的连续权重" `w_i = score_i^γ / Σ score_j^γ`（γ 平滑信号强度到资金的映射），叠加 AlgoKing 式 `signal_mult = 0.3 + 0.7·s` 下限保护（避免弱信号候选被压到 0）。confidence **不直接进 sizing**（Alpha P4），除非先经 WI-P0-5 的 ECE 校准达标。
- **灰度开关**：`GOT_SIGNAL_SIZE`（shadow 记录等分 vs 加权的仓位差）。
- **验收标准**：加权后单标的敞口 ≤ 上限、总和 ≤ `position_limit`；与旧等分的仓位/收益回测差读数；集中度不升反降或持平。
- **风险·回滚**：马太效应（高分票过度集中）→ γ<1 收敛 + 单标的 cap；回滚关开关回等分。
- **依赖**：WI-P0-5（若引入 confidence 需 ECE）。

### WI-P0-5 · P6 → 阈值回归校准闭环
- **现状代码事实**：`CatalystRealizationTracker`（`catalyst_realization.py`）已产 T+1/3/5 收益、兑现率、故事寿命统计，但**无管道**回流到 lifecycle/AMBUSH 阈值（后者标"样本量 0"）。
- **改动点**：新增离线 `scripts/recalibrate_thresholds.py`：以 P6 realized 分布为因变量，回归出 lifecycle day 阈值、`AMBUSH_DRIFT_*`、最优停止观测证据权重；PEAD 半衰期递减 → 阈值随年份自适应缩短；产出工件供 P0-1/P0-2/P1-5 读取。
- **灰度开关**：离线产物，无在线开关；消费方各自开关。
- **验收标准**：point-in-time 严格（只用 t 之前数据标定 t 之后阈值），杜绝 look-ahead；输出置信区间；标定前后对历史决策的净改动清单。
- **风险·回滚**：幸存者/停牌偏差使兑现率虚高 → 动态宇宙去偏差（见 §6 附注）；样本不足箱回退全局先验。
- **依赖**：无（喂给其它 WI）。

---

## 4. P1 工作项（能力补全）

### WI-P1-1 · Node1 sizing 理论上限（分数 Kelly + vol-targeting）
- **现状**：`risk_budget` 装饰、position_limit 离散三档、sizing 连乘无上限。
- **改动点**：`market_analysis.py` 由 `realized_volatility` 驱动 vol-targeting，`portfolio_decision.py` sizing 叠分数 Kelly（cap 0.25，FinRL regime_sizing），`risk_budget` 从装饰转"波动目标派生值"或被 P0-3 删除路线处置。
- **开关**：`GOT_KELLY_CAP`（shadow 落"理论上限 vs 实际连乘"差）。
- **验收**：cap 生效时总敞口不越 `position_limit`；vol-target 在不同波动日的敞口响应曲线单调合理。
- **风险**：Kelly 对期望估计敏感 → 严格 cap 0.25 + 用 GATE-2 pooled 期望而非单点。
- **依赖**：GATE-2。

### WI-P1-2 · 转换期显式减仓带
- **现状**：transition 仅靠 -7% 止损 / 0.65 乘数被动体现。
- **改动点**：regime 判定输出 `transition` 标志时，系统性降目标敞口 0.6–0.7× + 抬高入场确信门槛（Tradewink/Reign Edge）。
- **开关**：复用 `GOT_KELLY_CAP`。
- **验收**：转换期样本上敞口显著低于稳态，且不以错失反弹为代价（回测给转折后收益差）。
- **依赖**：P1-1。

### WI-P1-3 · Node3 资金多维重建 + 唯一键
- **现状**：DDE 摘除后退化单维 `sector_flow`；`sector_flow_all` 同名板块跨概念/行业取 max 系统性高估。
- **改动点**：恢复"大单净量/北向/融资"多维经多源降级链供给；唯一键修正——同名板块冲突时按 board_code 唯一键、取降级而非取大；输出"拥挤/分化"状态供 P0-1/P0-2 消费。
- **开关**：`GOT_CAPITAL_MULTIDIM`。
- **验收**：多维缺失日正确降级不塌陷（缺失≠零分）；唯一键修正后 sector_flow 高估幅度下降且候选池不归零。
- **风险**：多源 IO 不稳 → 沿用别名映射 + 三态分流 + 比例熔断既有护栏。

### WI-P1-4 · Node2 二波识别（fresh momentum 豁免老化）
- **现状**：`mainline_days` 含冷却期不重置，二波被当老主线惩罚。
- **改动点**：状态机在 `cooling→acceleration` 时重置 `days_since_launch`，标记 `second_wave=True`，老化折扣对二波豁免。
- **开关**：`GOT_SECOND_WAVE`。
- **验收**：历史二波案例（如政策再起）不再被误判 late_stage；冷却期天数不再计入寿命。

### WI-P1-5 · 过热→破位优先级 + change-point 切换
- **现状**：过热检测 23 交易日从未触发，破位永远抢先。
- **改动点**：背离窗口回溯到破位前 3–5 日 + cooling 阶段累积过热分，使过热成为"提前减仓触发器"；生命周期切换用 change-point detection（P0-5 标定）替代固定天数分箱。
- **开关**：`GOT_OVERHEAT_PRIORITY`。
- **验收**：过热触发率由≈0 升到合理区间，且不与破位重复计数。

### WI-P1-6 · Node6 闸门评分化（G1–G4）
- **现状**：G4 吸筹闸近总否决；多条件 AND 硬闸违反"评分+排名截断"原则。
- **改动点**：G1–G4 由 AND 硬闸改为分层分数，与既有 B 公式独立排序统一到同一评分空间，末尾按排名截断而非逐个 veto。
- **开关**：`GOT_GATE_SCORING`。
- **验收**：候选池归零回归用例通过；near_miss 不再被 Top-K 截断饿死。

### WI-P1-7 · 家族词表歧义治理（O2）
- **现状代码事实**：`family_keywords.py` 声明序首命中；"换电"同时列智能驾驶(:49)与新能源(:57)→恒归智能驾驶；未登记板块落"其他"，靠长期手工打补丁。家族是 `_drift_action_with_grace`（`family_anchor_map`）与 relay 判定地基。
- **改动点**：a) 审计并消除关键词跨族重叠（显式优先级或去重）；b) 未命中"其他"的板块用 embedding 最近家族兜底（复用 `glue_pipeline.py` 的 L1 检索接地能力，**但接进生产路径**，非 replay）；c) 家族归属变更留痕，跨日 `thesis_family` 漂移不直接触发清仓。
- **开关**：`GOT_FAMILY_EMBED_FALLBACK`（shadow 记录"若兜底会改判的板块清单"）。
- **验收**：重叠词归属确定且可解释；兜底不引入错误家族（人工抽检一批）；家族变更前后漂移/清仓判定 diff 审校。
- **风险**：embedding 误兜底 → 保守阈值 + 留痕人工复核。
- **落地（2026-10-06，b 项 shadow 态）**：新建生产模块 `src/got/family_embed.py`（家族原型文本 / 保守阈值裁决 / 按日向量缓存）+ Node 2 `run()` Step 3.4c 接线 + `SectorContext.family_embed_shadow`；
  相似度与缓存**全量复用 `glue_pipeline` 的 L1 件**（`embed_texts`/`retrieve_topk`/`_cache_key`/`L1_EMBED_CACHE_ROOT`），glue_pipeline 自身仍 replay-only（本文 §「不把 glue_pipeline 当生产家族粘合」红线不破）；
  开关缺省 **off**（不触网、不改判、旧路径逐字节不变），详见 §11.7。c 项（留痕人工复核 → 转正）仍以 shadow 清单为前置。

### WI-P1-8 · disagreement 转正 + memory 校准（O5）
- **现状代码事实**：`disagreement.py` 已实现 11 类冲突分类学 + 加权共识 + RiskOverridePlan + `_SOURCE_PRIOR_WEIGHTS`；情绪 `fear_greed>=0.8→risk_caution` 内建；但 `memory=None` 退化等权（`:189`），默认影子（`GOT_DISAGREEMENT_SHADOW=1`）只落观测。
- **改动点**：a) 接 `AgentMemory.compute_skill_weights` 校准替换等权；b) 经影子读数达标后 `GOT_DISAGREEMENT_ENABLED=1` 转正，让 `veto_buy`/`downgrade` 真正影响 Phase2；c) `mixed_directional_signals` 作为 Node6 4 维同源降权落点（disagreement rate → 可信度折减因子）。
- **开关**：既有 `GOT_DISAGREEMENT_SHADOW`/`GOT_DISAGREEMENT_ENABLED`。
- **验收**：memory 接线后权重非等权且可追溯；转正前后决策改动清单；相关性验收脚本 `.review_tmp/cp4b_correlation_acceptance.py` 同款方法复核。
- **风险**：转正放大误否决 → 保守阈值，仅降级不一票否决卖出。

### WI-P1-9 · 真实交易日历接入（O3a）
- **现状代码事实**：`catalyst_calendar.py` `_count_trade_days`(:85)/`_trade_days_offset`(:74) 只跳周末不跳节假日；`_ambush_exit_signal` 的 drift 超时(20)/衰竭(10)/binary 宽限全靠它数交易日 → 长假前后计数错乱。
- **改动点**：引入真实交易日历（akshare `tool_trade_date_hist_sina` 缓存本地），替换 weekend-only 偏移；drift/fade/binary 计数改按真实交易日。
- **开关**：`GOT_REAL_TRADE_CAL`。
- **验收**：国庆/春节样本日 drift 计数与实际交易日一致；日历缺失降级回 weekend-only 且告警。

---

## 5. P2 工作项（纪律护栏与能力扩展）

### WI-P2-1 · 账户级熔断扩展（连亏冷却 / 周月回撤）
- **现状**：已有 `RiskOverridePlan.veto_buy`（影子）+ W1 超限停买 + 整手归零日志，仅覆盖"买入否决"；缺连亏冷却、周月回撤熔断、无计划入场拦截。
- **改动点**：在现有 veto 骨架扩展：账户级周/月回撤 → 触发降敞口/停买冷却；Pre-Trade Discipline Gate 拦截"论点破位但仍想加"的无计划入场。
- **开关**：`GOT_ACCOUNT_BREAKER`（shadow 只记熔断会触发与否）。
- **验收**：历史连亏段熔断触发但不冻结至无法建仓（冷却期后自动恢复）；与 P0-3 敞口约束优先级清晰。
- **依赖**：P1-8。

### WI-P2-2 · 统一止损真相源（C3）
- **现状**：`stop_loss_threshold`（regime，`_lifecycle_sell_signal` 退出用）与 `ROLE_STOP_LOSS`（role，`_allocate` 买入挂单 `stop_loss`:4134）两套口径分裂，同一持仓可能"挂单 -7% 走、引擎 -10% 走"。
- **改动点**：合成为单一止损真相源（regime × role × 动量持久性），买入挂单位与退出判定共用；`expected_return`/`risk_reward` 用同一真相源重算（衔接 P2-3）。
- **开关**：`GOT_STOP_UNIFY`。
- **验收**：同一持仓两处止损值一致；持久性低时止损收紧（对齐《When Do Stop-Loss Rules Stop Losses?》）。
- **依赖**：P0-1。

### WI-P2-3 · expected_return 动态化（C4）
- **现状**：`_allocate` 买入挂单 `expected_return=0.10` 写死、`risk_reward=round(0.10/stop_loss_pct,2)`（`:4133-4135`），盈亏比只由止损决定、失去区分度。
- **改动点**：expected_return 由 P0-1 后验漂移估计动态给出，与 sizing/退出阈值共用一致预期。
- **开关**：复用 `GOT_OPTIMAL_STOP`。
- **验收**：不同持仓 risk_reward 出现合理区分度；不再恒以 10% 为分子。
- **依赖**：P0-1。

### WI-P2-4 · 利空事件 risk_events 接入退出（O3b）
- **现状代码事实**：`catalyst_calendar.py` 解禁 lockup 被抓取(:242)却明确丢弃(:258/266)，注释"未来由独立 risk_events 通道消费"，该通道不存在。
- **改动点**：新建 `risk_events` 通道，纳入解禁/减持/业绩预亏，接入 P0-1 退出观测证据（解禁临近→提前降仓/退出，作后验的负向观测）。
- **开关**：`GOT_RISK_EVENTS`。
- **验收**：解禁临近持仓的退出后验抬升可测；利空事件不误伤正面催化判定（分类隔离）。
- **依赖**：GATE-1、P0-1。
- ⚠️ **解冻前置取证已落地（2026-10-06，见 §11.6）**：新建离线只读脚本 `scripts/audit_lockup_risk_events.py`（**不建通道、不接在线决策**），拿现存唯一解禁历史样本（`catalyst_realizations` 里 127 条 lockup）复核本 WI 的前提。结论：**本 WI 的立项前提在现有票池上不成立**——解禁板块减基准后 T+5 **显著跑赢**基准（+0.99%，符号检验 p=0.00016），照原设计接线等于系统性反向操作；且「解禁临近持仓」样本=0、`catalyst_events` 里 lockup 已断流为 0。解冻前须先重定风险方向，详见 §11.6。

### WI-P2-5 · Node5 情绪接入清仓
- **现状**：情绪仅买入排序 +5/-10 微调；`disagreement.py` 已内建 `fear_greed>=0.8→risk_caution`（影子 downgrade_one，未接持仓清仓）。
- **改动点**：把 euphoria + 高位从"买入微调"接为"持仓主动减仓触发器"，复用 P1-8 的 risk_caution 通路。
- **开关**：复用 `GOT_DISAGREEMENT_ENABLED`。
- **验收**：极端贪婪日的减仓触发率与非贪婪日有显著差；阈值经 P0-5 校准非经验值。
- **依赖**：P1-8。

### WI-P2-6 · Node4 新闻反应失败反向闸
- **现状**：缺"利好落地但价格不涨=出货"反向闸（from68 Contrarian Setup Gate）。
- **改动点**：binary/drift 事件兑现日 T+1 观测"利好不涨"→ 转为负向退出证据进 P0-1。
- **开关**：`GOT_NEWS_REACTION_GATE`。
- **验收**：历史"利好不涨"案例被捕获为减仓信号；误报（正常盘整）可控。
- **依赖**：GATE-1。

### WI-P2-7 · 选股漏斗分数化 + DDE 降级权重重分配
- **现状代码事实**：`_run_ths_screener` 问财 DDE 是**活跃主路径**（`dde大单净量>=0.5%`:1150，第 1 轮带 DDE:1313），有降级梯 0.5%→0.3%→不带 DDE；东财 DDE 支路才已停用。首个 miss 早退的 binary 淘汰丢分层信息。
- **改动点**：a) 漏斗输出 x/5 分层分数供 Node6 排序，而非 binary 淘汰（对齐"评分+排名截断"）；b) 降级为"不带 DDE"时在评分层做资金维度权重重分配（缺失≠零分），而非静默降维。
- **开关**：`GOT_FUNNEL_SCORED`。
- **验收**：候选池归零回归通过；降级日资金维度不因缺列被系统性低估。

### WI-P2-8 · orchestrator per-source availability（O1）
- **现状代码事实**：`bar_complete` 全流程只算一次(:76)，仅透传 Node4(:249) 与 Phase2(:158)，**Node1/2/3/5/6 均不接收**（Node6 `aggregator.run` 于 :114-119 调用亦无 `bar_complete` 参数）。
- **改动点**：availability 从"1 全局 bool"下沉为"每数据源各自标记"（event/bar-end/available-time 三分），逐节点 gate；盘中未完成数据显式标 unavailable 而非按收盘态消费。
- **开关**：`GOT_AVAILABILITY_MAP`（shadow 记录各源可用性判定）。
- **验收**：盘中运行时依赖未收盘数据的判据被正确 gate；收盘态与既有逐字节一致。

### WI-P2-9 · persistence 跨日一致性断言（O4）
- **现状代码事实**：跨日记忆分裂在 JSON（`previous_context_YYYYMMDD.json`）+ 多张 DB 表（thesis/ambush/sentiment）两处。
- **改动点**：统一跨日状态视图（单一 source-of-truth + 版本/日期键），JSON↔DB 一致性断言（重跑/补算日校验同步）。
- **开关**：`GOT_CONTEXT_CONSISTENCY`（shadow 只报警不改写）。
- **验收**：补算/重跑日断言无冲突或冲突被显式拦截；thesis 与 family_anchor/regime 跨日口径一致。

### WI-P2-10 · 轮动换手成本护栏
- **现状**：轮动通道无换手/错时成本惩罚（NBER w16534：扣成本后收益 quickly diminishes）。
- **改动点**：Node2 轮动输出对候选计换手成本惩罚，防通道过拟合。
- **开关**：`GOT_TURNOVER_PENALTY`。
- **验收**：高频轮动候选被降权；净收益（扣成本）读数优于毛读数排名。

### WI-P2-11 · Node2 资金维度叠加（RRG 第三轴）
- **现状**：RRG 四象限纯价格滞后。
- **改动点**：引入板块资金流强度作第三轴（依赖 P1-3 多维资金恢复）；% 成分股高于 52 周均线 + Rational Decay 去短期反转。
- **开关**：复用 `GOT_CAPITAL_MULTIDIM`。
- **验收**：象限切换对资金拐点更敏感；不与价格象限冲突误判。
- **依赖**：P1-3。

### WI-P2-12 · Node6 蓄水池容量自适应
- **现状**：蓄水池饱和钉死 15，高位堵死新候选。
- **改动点**：容量随埋伏质量自适应（低质挤占时不阻断新候选）。
- **开关**：`GOT_RESERVOIR_ADAPTIVE`。
- **验收**：满池时新高质量埋伏仍可入；weeks_without_catalyst 语义不被破坏（已知陷阱）。

---

## 6. 分期排期建议（供项目负责人裁决，非硬性）
- **第 1 批（地基）**：GATE-1 → WI-P0-5（离线闭环）→ GATE-2 → WI-P1-9（真实交易日历）。理由：GATE/标定/日历是后续所有退出与阈值类的前置，且不改在线决策、风险最低。
- **第 2 批（退出引擎）**：WI-P0-1 → P0-2 → P0-3 → P0-4 → P2-2 → P2-3。均在 shadow 下并行读数，达标后逐个 enabled。
- **第 3 批（能力补全）**：P1-1/P1-2（sizing）、P1-3（资金多维）、P1-4/P1-5（Node2）、P1-6（Node6）、P1-7（家族）、P1-8（disagreement 转正）。
- **第 4 批（护栏扩展）**：P2 其余项（熔断/利空/情绪/新闻反证/漏斗/orchestrator/persistence/轮动/蓄水池）。
> 每批内部各 WI 独立开关、独立 commit、独立回退；跨批依赖以"上游 shadow 读数达标"为闸门。

## 7. 测试策略
- **影子优先**：所有决策类 WI 先落 shadow，产出"若生效会改多少决策"的 diff 报告，再 enabled。
- **候选池归零红线用例**：P1-6/P2-7/P1-3 等改判据/闸门的 WI，必须带"候选数=0"回归。
- **冷启动默认值不倒挂用例**：P0-4/P1-1 新连续字段的中性默认回归。
- **可成交性专项**：GATE-1 的跌停一字/T+1/涨停一字三态用例。
- **point-in-time 专项**：P0-5/GATE-2 标定只用 t 前数据标 t 后阈值，禁 look-ahead。
- **逐字节不变红线**：任一开关全关时，与当前基线对齐（沿用四层回放红线方法）。
- 复用 `scripts/` 既有验收工器（`verify_same_day_idempotency.py`、`verify_layer_b_consistency.py`、`verify_glue_metrics.py` 等）。

## 8. 影子 → enabled 转正流程
1. WI 以 shadow 上线，落观测字段（不改决策）。
2. 累积影子期读数（建议覆盖≥1 个完整主线生灭周期 + 1 次长假）。
3. 出转正评审：触发率、误清仓/误否决率、候选池健康度、与 legacy 分歧分析。
4. 负责人裁决 → `GOT_<TOPIC>_ENABLED=1`；保留 shadow 回退位与单 commit 回滚。

## 9. 与现有 backlog / verification 对账
- **disagreement / market_theme / funnel_stats 影子态**已在 backlog §5（Phase 3b/4b/4d 批次 5）登记 → 本报告的 P1-8 是其"转正 + memory 校准"延伸，不重复设计，仅补 calibration 与同源降权。
- **lockup 事件处理**：architecture/backlog 有"催化剂日历架构与 lockup 事件处理流程"记录，P2-4 的 risk_events 通道是其 TODO 承接。
- **sector_strength_map 消费口径 / 过热从未触发 / 冷却期寿命语义**：
  - ⚠️ **更正**：`sector_strength_map` **并非死代码**——经核验，它在 `_reverse_lookup_stock_sector` 的 `_lookup_strength`（`:1860-1893`，`_smap.get(board_name)` / 别名 / 子串匹配）中被活跃消费，返回的 `strength` 经 `_find_stock_sector`（`:618`）→ `_adjust_score_by_sector_intel`（`:629`）进入持仓五维打分的 `sector_score`，影响 `composite_score` 排序与决策。原"死代码"结论仅适用于 **`_allocate` 仓位分配路径**（sizing 不读它），不适用于全文件。
  - 因此本报告**不把"清理 sector_strength_map"列为 WI**；真正的可优化点是它与 `strength_lookup` 存在**双真相源冗余**（`_lookup_strength` 需先查 `_smap` 再查 `_slookup` 再子串兜底），建议 P2 级"统一板块强度单一真相源"，而非删除。
  - "过热从未触发"（P1-5）、"冷却期计入主线寿命"（P0-2）仍为已核验实证结论，作为改动依据成立。
- **Phase 6 盘中确认闸门**：P2-8 per-source availability 是 Phase 6 数据地基的前置子集，可先于盘中闸门独立落地。
- 新增 WI 若负责人认可，应回填 `backlog.md` 待实施清单（标处置类别）与 `verification.md`（影子期判据）。

## 10. 被否决 / 暂缓
- **不采用"LLM confidence 直接驱动 sizing"**（Alpha P4）：confidence 仅在 ECE 校准达标后经 P0-4 谨慎引入，否则维持"仅装饰/仅排序"。
- **不在单持仓尺度做在线贝叶斯 MCMC**（GATE-2 不可识别）：改离线 pooled + 分层先验查表。
- **不把 `glue_pipeline.py` 当生产家族粘合**（它 replay-only）：P1-7 只复用其 L1 检索接地能力，落地走独立生产开关。
- **不新增多条件 AND 硬闸**：一律评分 + 排名截断（P1-6/P2-7）。
- **暂缓项**：P2 全部依赖 P0/GATE 读数达标后启动；未达前置前不进入 enabled。

## 11. 实施进展（2026-09-29 落地）

> 原则：能纯观测/离线、不碰在线决策的先做（shadow/off 态逐字节不变红线已过），
> 依赖上游读数或需维护者裁决口径的归入延后。

### 11.1 已落地（均含测试，开关缺省 off/shadow）
- **GATE-1 卖出侧可成交性投影层**：新增 `src/got/tradability.py`（纯函数 + 行情获取 + T+1 建仓日）；
  `_allocate` 三处接线（Step1 sell/reduce 打标顺延、Step1.5 W2 削单、Step3 买入涨停拦截）；
  schema 加 `pending_exit`/`blocked_reason`/`tradability_shadow`。降级红线：行情缺失绝不拦截，仅落 `quote_unavailable`。
  开关 `GOT_TRADABILITY_GATE`（off 缺省）。测试 `tests/test_gate1_tradability_wiring.py`。
- **WI-P0-3 敞口执行接线 shadow**：`GOT_EXPOSURE_ENFORCE`（缺省 shadow）记录"若强制会截断多少"，纯观测不改决策；schema 加 `exposure_enforce_shadow`。
- **WI-P0-4 sizing 信号强度连续缩放 shadow**：`GOT_SIGNAL_SIZE`（缺省 shadow）对比等分 vs `score^γ` 加权仓位差（γ=0.5），纯观测；schema 加 `signal_size_shadow`。
- **WI-P0-5 阈值回归校准闭环**：离线脚本 `scripts/recalibrate_thresholds.py` 产出 `data/threshold_calibration.json`（lifecycle day 建议、drift/binary 附 Wilson 区间与 `insufficient_sample` 标注）。
  真实读数（2026-09-30 跑，见 §11.4）：催化剂/lifecycle 箱 **`status=ok`**（449 已兑现样本，`recommendation_day=5`）；埋伏 drift/binary 箱 `insufficient_sample`（0 平仓样本，结构性稀缺，见 §11.4）。
- **GATE-2 最优停止 pooled/分层先验（2026-10-06）**：新增离线脚本 `scripts/calibrate_optimal_stop.py` → 工件 `data/optimal_stop_priors.json`（`data/` 已 gitignore，脚本入库、产物不入库，与 `wave_calibration.jsonl` 同惯例）。
  主线段面板来自 GOT 归档（`unified.sector_role` + `sector.json.today_change`，天然 point-in-time），pooled μ/σ/θ + `家族 × regime` 分层箱，小样本箱 `w=n/(n+k)` 向全局收缩；右删失段（段尾落在归档最后一天）默认剔除。
  读数与使用约束见 §11.5。测试 `tests/test_optimal_stop_priors.py`（10 例纯函数）。**零在线改动**：P0-1/P1-1 消费本工件属后续 WI。
- **WI-P1-9 真实交易日历**：`catalyst_calendar.py` 加 `_is_cn_trading_day`（复用 `src.core.trading_calendar.is_market_open`，缺包降级 weekend-only），`GOT_REAL_TRADE_CAL` 分流（缺省关）。
- **WI-P1-7(a) 家族词表歧义**：`family_keywords.py` 移除新能源族"换电"重叠 + 加 `_audit_keyword_overlaps` 自检。
- **WI-P1-7(b) 「其他」板块 embedding 家族兜底（2026-10-06，shadow 态）**：新增生产模块 `src/got/family_embed.py`（家族原型文本 / `select_family` 保守阈值 / 按日向量缓存复用 L1 惯例）+ Node 2 `run()` Step 3.4c 接线（排在拥挤检测之前，改判后拥挤/主线聚合/Node 6 家族中继自然消费新家族）；`SectorContext.family_embed_shadow`（缺省 None）落「若兜底会改判的板块清单」。开关 `GOT_FAMILY_EMBED_FALLBACK`（**缺省 off**，off 立即返回不触网）；阈值 `GOT_FAMILY_EMBED_MIN_SCORE`/`_MARGIN`（缺省 0.55/0.05，未标定启发式）。测试 `tests/test_family_embed_fallback.py`（26 例，embedding 全桩、不触网）。读数与转正条件见 §11.7。
- **WI-P2-9(a) persistence 跨日一致性断言 shadow**：`persistence.py` 加纯函数 `cross_day_consistency_diff`（板块名归一后取 JSON↔DB 双向差）+ `query_db_mainline_family_sector_names`；`aggregator._save_context` save 成功后按 `GOT_CONTEXT_CONSISTENCY`（缺省 off）影子留痕，异常内部兜底绝不外抛。测试 `tests/test_p2_9_context_consistency.py`。**enabled 语义"统一真相源 + 冲突拦截"归入延后**（需先定单一 source-of-truth 口径）。

### 11.2 延后裁决
- **WI-P1-4 Node2 二波识别**：核心已由 v3.5 `decide_wave_days`（`sector_analysis.py:70`）覆盖——冷却期冻结继承、真洗盘（间隙≥2 交易日 + 自出框前平台回撤≥10 分）重置 wave_days=1。`fresh momentum 豁免老化`残留分支需维护者用真实样本裁决，本轮不动。
- **WI-P2-7 / P2-10 / P2-12**（漏斗分数化 / 轮动换手惩罚 / 蓄水池自适应）：均改在线选股·轮动·池容量判据，触候选池归零红线（§0.1-3），且各自新阈值（漏斗分档权 / 换手惩罚系数 / 池容量函数）**不在 P0-5 本次标定范围**——P0-5 仅覆盖 lifecycle/ambush 阈值。§11.4 已更正：lifecycle 箱已 `ok`（并非全盘 insufficient_sample），但此三项依赖的是其自身未标定阈值，故仍延后。
- **WI-P0-2 lifecycle 时间阈值前移**：数据前置**已满足**（§11.4：催化剂箱 449 样本 `status=ok`，建议 day=5 替代写死 30），非“样本不足”阻塞；仍延后的真实原因是它改在线退出决策（红线 §0.1-1 需先 shadow 读数），且 449 样本存在幸存者偏差（median T1→T5 仍递增 0.02→0.36，工件 caveat①），day=5 只作方向佐证不作精确最优点。
- **WI-P2-1/2/3/4/5/6/8/11**：硬依赖 P0-1/P1-3/P1-8/GATE-2 尚未落地，按 §6 排期属第 2–4 批，本轮不启动。**其中 P2-4 已在 §11.6 完成解冻前置取证，阻塞项从「依赖未落地」升级为「前提被证伪（方向反指）」——不是攒样本能解的，需先重定风险方向再谈接线。**

- **WI-P1-7(b) 家族兜底「enabled 转正」**：shadow 态已落地（§11.7），但 enabled 真改判需先按验收「人工抽检一批」读完 shadow 留痕的误兜底率；阈值 0.55/0.05 属未标定工程启发式，**不得直接当转正阀值**。

### 11.3 代码审查修复（2026-09-30 CR 轮）
对 §11.1 已落地项发起代码审查（无 Critical），7 项 Warning/Suggestion 全部修复：
- **CR#1（WI-P1-9）**：`_is_cn_trading_day` 叠加 `weekday()>=5` 硬约束先短路——exchange-calendars 缺失时 `is_market_open` fail-open 恒返 True，修正前周末被误计为交易日；降级契约改为绝不会把周末判为交易日。
- **CR#2（GATE-1）**：`check_tradability` 将 T+1 判定提到行情充分性检查之前——T+1 仅依赖建仓日，修正前行情缺失走 `quote_unavailable` 放行会旁路当日建仓卖单拦截。
- **CR#3（GATE-1）**：`quote_unavailable` 降级放行时不再置 `pending_exit=True`（sell/reduce/buy/W2 四处统一），仅留 `blocked_reason` 观测标签，消除“已放行却标顺延”的语义矛盾。
- **CR#4（GATE-1）**：`_tradability_check` 加 `_entry_cache` 日内缓存，同标的 entry_date 仅一次查库 IO。
- **CR#5（GATE-1）**：enabled 卖/减顺延时回写 `remaining_qty[symbol]=0`，防 Step 1.5 重复削减同一标的。
- **CR#6（WI-P0-5）**：`recalibrate_thresholds.py` docstring 产出文件名对齐实际 `threshold_calibration.json`；三处统计函数加 `n==0` 早退，空箱不再 `days[-1]`/`mean([])` 崩溃。
- **CR#7（GATE-1）**：`tradability.py` 模块 docstring 封板口径对齐实现（跌停看当日最高价是否全天贴住跌停、涨停看最低价，high/low 双界）。
测试同步：新增 `test_t_plus_1_blocked_even_when_quote_missing`（CR#2 回归）、重写 `test_calendar_library_unavailable_never_counts_weekend`（CR#1 真实 fail-open 口径）、修正 `test_quote_unavailable_never_blocks_enabled`（CR#3 新语义：`pending_exit is False` 且不进顺延集合）。回归：`test_gate1_tradability_wiring.py` + `test_p2_9_context_consistency.py` 36 passed；portfolio/node6/aggregator 相关 11 文件组合 162 passed（开关全关旧路径无扰动）。

### 11.4 P0-5 真实标定读数 + 埋伏侧回测可行性（2026-09-30）

用历史数据直跑 `scripts/recalibrate_thresholds.py`（`as_of=2026-09-30`、`min_samples=30`）的实际产出，纠正 §11.2 此前“标定样本尚 insufficient_sample”的一束式误判：

| 标定箱 | 样本 | 状态 | 关键读数 |
|---|---|---|---|
| lifecycle_day | **449** | ✅ `ok` | `recommendation_day=5`（替代写死 30）；median 收益 T1=0.019 / T3=0.142 / T5=0.360；T1 胜率 0.508（Wilson 95% [0.462, 0.554]）；故事寿命 p50=3 / p75=5 日历日 |
| ambush_drift | **0** | ❌ insufficient_sample | keep_legacy |
| ambush_binary_grace | **0** | ❌ insufficient_sample | keep_legacy |

**存量真相（直查库）**：`catalyst_realizations` 685 条 / 其中 449 条已兑现带 T+1 收益（event_date 跨 2026-08-15→10-12）；`ambush_position_snapshots` 全表仅 3 条且**全部 drift、全部 is_open、0 binary、0 平仓**。

**结论一：催化剂/lifecycle 侧无需额外回测，历史数据已够**——P0-2 的数据前置已满足（`status=ok`），仅因“改在线决策需先 shadow”+幸存者偏差 caveat 而未启用。

**结论二：埋伏 drift/binary 侧“用历史数据回测”不是快赢**，三重阻塞：
1. **结构性稀缺**（最根本）：跨 ~48 个交易日实盘仅产 3 条快照，埋伏建仓 buy 被 gate funnel + 蓄水池重重闸限流，本就极少转化；binary 一条未产。即便完美重放现有 `data/got_daily/` 产物，closed 样本上限仍受限于 buy 触发频率，难达 drift/binary 各 ≥30。
2. **硬编 `date.today()`**：`_write_ambush_buy_snapshots`（entry_date，:2437/:2467）与 `_close_ambush_snapshots`（exit_date，:2886/:2899）均取墙钟今日，且平仓仅按 symbol+is_open 匹配（无日期护栏）——逐日重放会把 held_trading_days 压为 0 或串扰。
3. **无现成重放器**：`backtest_september.py` 仅对 Top-5 推荐算前瞻收益，**不**重放埋伏生命周期；`run_phase2_only.py` 单日跳 Phase2。均需自建“模拟交易日贯穿 + 顺序多日 + 隔离写临时表”的重放 harness（本身就是一个独立 WI）。

**处置**：ambush_drift/binary 保持 `keep_legacy`（工件已如此标注）；若后续重视埋伏阈值标定，需先立“埋伏生命周期重放 harness”专项。lifecycle 侧的 day=5 转正仍走 §8 shadow→读数→裁决流程，不因“数据够了”而跳过决策安全闸门。

### 11.5 GATE-2 最优停止先验真实读数（2026-10-06，`as_of=2026-10-06`）

直跑 `scripts/calibrate_optimal_stop.py`（默认 `--min-len 2 --min-samples 12 --shrink-k 8 --censor-tail-days 1`）：

**面板规模**：47 个合规归档日（20260724→20260930；必须排除 `20260805_backup`/`20260817_pre_fix`/`_cache` 这类快照目录，否则同一天的板块被重复计入两次）→ 主线板块-日 512（`unified.sector_role=mainline` 401 + 仅 `sector.json state=active_mainline` 111）→ 107 段（≥2 交易日）→ 剔 3 条右删失 → **102 段可用 / 173 个板块 / 318 板块-日**。

| 读数 | 绝对（板块自身） | 超额（减中证1000） |
|---|---|---|
| 日均漂移 μ | **+1.21%/日** | **+0.66%/日** |
| 日波动 σ | 1.81% | — |
| 段累计收益 p50 | +2.92% | +1.17% |
| θ（段内峰值日）p50 | **2 日** | **2 日** |
| P(θ≤t) | t=2:64% / t=3:82% / t=5:97% | t=2:72% / t=3:85% / t=5:98% |
| 峰值优于段尾的比例 | 45.1%（Wilson95 35.8–54.8%） | — |
| 出主线后 fwd1 | −3.43%（n=58） | **−1.21%** |

**旁证**：`data/wave_calibration.jsonl` 1249 行自报 `wave_days` p50=2、97.2%<7 天 ↔ 本面板实测段长 p50=2、98.0%<7 天 → 「主线寿命 93.5%<7 天」在板块尺度同向复现（§2 GATE-2 现状事实成立）。

**分层箱状态**：24 个 `家族 × regime` 箱，仅「其他×bull」(17) 与「AI硬件×bull」(13) 达 `min_samples=12`；其余均 `shrunk`（w=n/(n+8)）。**含义**：P0-1 上线应以 pooled 为主、家族箱仅作弱条件化（收缩后差值小于箱内噪声）；早期归档无 `sector_role`/`frame_group`/`regime`，故 `unknown` 箱不可避免。

**两个必须记住的口径坑**（已写进脚本注释 + 测试钉住）：
1. `sector.json.active_mainline` 顶层是**家族分组**（`group_name`+`members[]`），板块在 `members` 里。不下钻就会把全部活跃主线整批丢掉、只剩 `exhausted_mainline`，结果 θ 被系统性拉到第 1 天、pooled 段累计收益由 **+2.9% 变 −1.9%**（初版实踩，属伪结论）。
2. **右删失**：段尾正好是归档最后一天的段，其真实峰值可能在 as_of 之后，留着会把 θ 拉早，故默认剔除并如实计数。

**与 WI-P0-5 的关系**：本 WI 的分布输入是 GOT 归档面板（生产当日所见的板块涨跌幅），而非 P6 `catalyst_realizations`；P6 侧的 median 衰减曲线仍由 `threshold_calibration.json`（§11.4）独立承载。两者互不阻塞：P0-1 的 θ 先验取本工件，lifecycle day 取 P0-5 工件。**待裁决**：窗口只 47 交易日/102 段，且 `edge_next_day` 到第 5 日后 n≤10（只有方向无精度），P0-1 的 `posterior` 分支阈值定值需实盘累积更多完整主线生灭周期后再拍（§8 转正流程）。

### 11.6 P2-4 解禁 risk_events 通道：解冻前置取证（2026-10-06，`as_of=2026-10-06`）

新增离线只读脚本 `scripts/audit_lockup_risk_events.py` → 工件 `data/lockup_risk_evidence.json`（`data/` 已 gitignore）。**职责是取证而非接线**：建通道属在线决策改动且硬依赖 GATE-1/P0-1（§11.2），本轮只做「值不值得立」的三问。DB 以 `file:...?mode=ro` 打开，不触网。

**口径纪律**（禁第二套口径）：板块名→板码全量复用生产 `CatalystRealizationTracker._get_board_code`（三级解析）；T+N 锚定镜像生产 `_find_event_date_index`（精确优先、±3 日先 + 后 −）+ `_compute_multi_window_returns`（事件日收盘 → T+N 收盘）；基准取中证1000（`.review_tmp/bt_cache/index_sh000852.csv`，**与 §11.5 GATE-2 同文件同口径**）；比例区间复用 `calibrate_optimal_stop.wilson_interval`；同板同日多只解禁股聚合为 1 个板块日样本（否则同一次板块移动被重复计入）。

**关键前提**：生产落库的 `day_N_return`/`actual_return` 是**未减基准的裸板块收益**。按 §1.1 红线（收益一律减基准），不减基准无法区分「解禁利空」与「大盘 beta」。

**真实读数**：

| 问 | 读数 | 判定 |
|---|---|---|
| Q1 数据地基 | `catalyst_realizations` 现存 **127 条 lockup**（event_date 2026-08-19→2026-09-02，**丢弃生效后零新增**）；覆盖 57 板块；44 个独立个股事件→127 条板块记录（扇出均值 2.89、最大 **13**）；可算样本仅 **46/127**（`no_board_code` 27 + `no_kline` 54，受 880 缓存只含当时选中的 142 板块限制）；`catalyst_events` 表 lockup=**0** | ❌ 在线日历已断流：通道先得有数据源（恢复入库或新建落库表） |
| Q2 负向证据（减基准） | T+1 裸 +0.56% / 超额 **+0.19%**（n=46，跑输占比 0.478，p=0.883）；T+3 裸 +0.98% / 超额 **+0.81%**（p=0.0045）；T+5 裸 +0.93% / 超额 **+0.99%**（跑输占比 0.217，**p=0.00016**） | ❌ **方向反指**：解禁板块显著**跑赢**基准，不是「样本不够」而是前提不成立 |
| Q3 可接线性 | 埋伏快照 3 条（全是 0915 入场、global 事件）/ 持仓 0 条 → 「入场后 ≤5 交易日同板解禁」命中 **0** | ❌ P0-1 退出后验无观测地基 |

**与生产落库值对账**（同锚重算 vs DB `day_N`）：T+1 match 30 / diff 16 / na 0（可比 46），T+3 17/5/24，T+5 6/5/35。diff 主要源于当时解析到的板码／K 线截止与现在不同（家族词表、别名表已修法）；故本表读数以「同锚自洽重算」为准，**不得当生产口径复用**（工件 caveats 已钉）。

**结论与红线**：`unlock_ready=False`，三项前置全缺。最重要的一条不是样本量——**Q2 给出的是正向超额**：若照 WI-P2-4 原文（解禁临近→提前降仓/退出）接线，会在现有票池上系统性做反向操作。解冻评估需先回答两问：① 该正向是否板块扇出/小样本假象（同一天 44 个股事件扇到 127 板块日，独立性远小于行数；n=46 亦受 142 板块缓存选择偏差约束）；② 解禁是否该按**个股级**而非板块级度量（现通道是「个股事件→板块扇出」，一个 13 倍扇出的解禁会把无关板块一起打标）。这两问未解前，`GOT_RISK_EVENTS` 不得进入 shadow。

测试 `tests/test_lockup_risk_events.py`（18 例纯函数 + 临时库端到端降级：锚定回退顺序、窗口越界不编数、扇出/板块日去重、符号检验精确概率、四态方向判定、只读约束 `mode=ro` 拒写）。**零在线改动**：不建表、不加开关、不接退出链。

### 11.7 WI-P1-7(b) 「其他」板块 embedding 家族兜底（2026-10-06 落地 shadow 态，开关缺省 off）

新增生产模块 `src/got/family_embed.py` + Node 2 `run()` Step 3.4c 接线 + `SectorContext.family_embed_shadow`。
计划原文只给了一句「复用 glue_pipeline 的 L1 检索接地能力**但接进生产路径**」，与 §315「不把 glue_pipeline 当生产家族粘合（replay-only）」并存 → 落地口径：**只借四个件**（`embed_texts`/`retrieve_topk`/`_cache_key`/`L1_EMBED_CACHE_ROOT` 按日缓存惯例），相似度与缓存不另写第二套；`glue_pipeline` 本身仍 replay-only，兜底走独立开关。

**三态与红线**（`GOT_FAMILY_EMBED_FALLBACK`）：

| 态 | 行为 |
|---|---|
| off（缺省） | `resolve_family_overrides` 立即返回 `skipped_reason=mode_off`：不触网、不读/写向量缓存、`family_embed_shadow` 保持 None、`frame_group` 零改动 |
| shadow | 照常算向量 + 落清单（`unmatched_boards` 逐板含 reason/score/margin/前 3 名 + `overrides`/`would_flip_count`），**不改任何 frame_group** |
| enabled | 按 `overrides` 真写 `SectorDetail.frame_group`（只改家族，不改 state/评分）+ notes 留痕 |

接线位置红线：Step 3.4c 排在 Step 3.4a（家族拥挤）**之前**——否则拥挤降权、主线家族聚合、Node 6 家族中继/漂移宽限（`frame_group_lookup`）都看不到兜底后的家族；`_build_theme_families` 走数据驱动相关性、不用词表，故不受影响（次序不变）。

**离线自检读数（`.review_tmp/p17b_selfcheck.py`，强制清空 embedding key 不触网）**：off → `mode_off`、trace 空、零缓存文件；shadow → `embedding_unavailable`、overrides 0、cache_hits/misses 均 0；非法开关值（`nonsense`）→ 回落 off（不得意外开启改判）；shadow 下命中词表板块被正确排除（skipped_count=1）。候选家族 14 个（= FAMILY_KEYWORDS 去掉「其他」）。

**阈值口径**：`GOT_FAMILY_EMBED_MIN_SCORE=0.55` / `GOT_FAMILY_EMBED_MIN_MARGIN=0.05` 属**未标定工程启发式**（按本文 §179「保守阈值 + 留痕人工复核」拍的缺省值，无数据依据），不得直接当转正阈值。转正需先回答三问：① shadow 期「其他」板块占比与拒绝原因分布（low_score / ambiguous / ok）；② `would_flip` 清单人工抽检——兜底家族是否真的对（验收原文「人工抽检一批」）；③ enabled 首日 `frame_group` 变更清单及其经家族中继/漂移宽限的下游 diff。`.env` 已配 `GLUE_EMBEDDING_API_KEY`，真实 shadow 读数需首个非交易日跑 `GOT_FAMILY_EMBED_FALLBACK=shadow` 产生（本轮只落能力 + 离线自检，不产读数）。

测试 `tests/test_family_embed_fallback.py`（26 例：文本构造含关键词截断、保守阈值四口径、开关归一与非法值回落、词表复核分流、缓存命中不请求 embedding、未命中落按日文件、数量不匹配报错、三态生产入口、改判谓词（off/shadow 不改 enabled 才改）、接线守卫 Step 3.4c 先于拥挤检测）。与 `test_sector_state_machine`/`test_wave_days`/`test_sector_rotation` 同跑 98 passed；与 `test_glue_pipeline`/`test_aggregator_run_smoke`/`test_reverse_lookup`/`test_cross_node_signals`/`test_p2_9_context_consistency`/`test_node6_candidates`/`test_a6_wiring` 同跑 108 passed。

---
> 本报告为实施设计说明，未含代码提交；逐 WI 进入 shadow 前仍需按 §8 流程出评审。
