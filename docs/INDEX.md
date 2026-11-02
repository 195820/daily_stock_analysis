# 文档中心

> 2026-09-17 文档结构重排：全目录按**三区**组织——`guide/`（旧项目基底，非 GOT，均已实施）、`got/`（GOT 现行：待实施 / 待验证 / 待实施计划）、`archive/` 与 `got/archive/`（已完结历史）。本页为**全量索引**（收口原「INDEX 收录失效」缺陷）。

这里是项目文档入口。README 负责项目概览和快速开始；更完整的配置、部署、功能说明和排障内容从这里进入。

## 按场景选择

| 我想要 | 先看 | 继续看 |
| --- | --- | --- |
| 快速了解项目能做什么 | [README](../README.md) | [完整配置与部署指南](guide/full-guide.md) |
| 第一次把项目跑起来 | [小白客户端安装与配置](guide/beginner-client-setup.md) | [完整配置与部署指南](guide/full-guide.md) |
| 配置大模型渠道 | [LLM 配置指南](guide/LLM_CONFIG_GUIDE.md) | [LLM 服务商配置指南](guide/llm-providers.md) |
| 配置推送通知 | [通知能力基线](guide/notifications.md) | [完整配置与部署指南](guide/full-guide.md) |
| 部署到服务器或云平台 | [部署指南](guide/DEPLOY.md) | [云端 WebUI 部署](guide/deploy-webui-cloud.md)、[Zeabur 部署](docker/zeabur-deployment.md) |
| 使用 Bot / IM 接入 | [Bot 命令与接入](guide/bot-command.md) | [Bot 平台配置](bot/) |
| 排查运行问题 | [FAQ](guide/FAQ.md) | [诊断可靠性](guide/run-diagnostics-p0.md)、[更新日志](CHANGELOG.md) |
| 了解 GOT 决策系统现状 | [GOT 架构设计](got/architecture.md) | [GOT 文档导航](got/README.md) |
| 查看 GOT 待办与红线 | [GOT 待实施清单](got/backlog.md) | [GOT 待验证/影子期清单](got/verification.md) |
| 参与开发或提交 PR | [贡献指南](CONTRIBUTING.md) | [开发规则](AGENTS.md)、[API 规格](architecture/api_spec.json) |

---

## 一区 · 旧项目基底（`guide/`，非 GOT，均已实施）

### 快速开始与总览

| 文档 | 内容 |
| --- | --- |
| [README](../README.md) | 项目定位、核心能力、快速开始、推送效果 |
| [小白客户端安装与配置](guide/beginner-client-setup.md) | 面向不会代码用户的客户端下载、模型配置、新闻源配置和常见问题 |
| [完整配置与部署指南](guide/full-guide.md) | 环境准备、运行方式、配置说明、部署路径和常见问题 |
| [FAQ](guide/FAQ.md) | 常见配置、模型、通知、部署和运行问题 |
| [更新日志](CHANGELOG.md) | 版本变化、能力调整和迁移说明（[Unreleased] 已拆「本地工作 / 上游同步」两段） |

### 配置

| 文档 | 内容 |
| --- | --- |
| [LLM 配置指南](guide/LLM_CONFIG_GUIDE.md) | 大模型渠道、三层配置、Web 设置页和常见模型配置 |
| [LLM 服务商配置指南](guide/llm-providers.md) | Provider 预设、Actions 映射、错误分类和诊断建议 |
| [LiteLLM YAML 示例](examples/litellm_config.example.yaml) | LiteLLM 多渠道配置示例 |
| [通知能力基线](guide/notifications.md) | 企业微信、飞书、Telegram、Discord、Slack、邮件等通知渠道配置 |
| [Tushare 股票列表指南](guide/TUSHARE_STOCK_LIST_GUIDE.md) | Tushare 股票列表相关配置和使用说明 |
| [设置页帮助文案](guide/settings-help.md) | WebUI 设置页关键说明（维护向） |

### 使用专题

| 文档 | 内容 |
| --- | --- |
| [Bot 命令与接入](guide/bot-command.md) | Bot 命令、Webhook、平台接入和回调说明 |
| [Bot 平台配置](bot/) | 飞书、钉钉、Discord 等 Bot 配置截图和补充说明 |
| [ntfy 双向通道](bot/ntfy-channel.md) | ntfy 接入、4096 B/附件等硬限额、回声防护、多项目复用与排错对照表 |
| [实时告警中心](guide/alerts.md) | EventMonitor 基线、Web 规则管理、通知结果、冷却状态和 Phase 边界 |
| [分析上下文包](guide/analysis-context-pack.md) | AnalysisContextPack 契约、运行态消费与可见性（P0–P6） |
| [诊断可靠性 Phase 0–3](guide/run-diagnostics-p0.md) | [P0](guide/run-diagnostics-p0.md) / [P1](guide/run-diagnostics-p1.md) / [P2](guide/run-diagnostics-p2.md) / [P3](guide/run-diagnostics-p3.md) 运行诊断链路 |
| [图片识别 Prompt](guide/image-extract-prompt.md) | 图片识别股票信息的 Prompt 与使用边界 |
| [OpenClaw Skill 集成](guide/openclaw-skill-integration.md) | OpenClaw / Skill 外部集成说明 |
| [AlphaSift 选股集成](guide/alphasift-integration.md) | AlphaSift 锁定 commit、Hotspot 契约边界、兼容回退路径 |

### 部署与打包

| 文档 | 内容 |
| --- | --- |
| [部署指南](guide/DEPLOY.md) | 服务器部署、Docker、systemd、Supervisor 等部署方式 |
| [云端 WebUI 部署](guide/deploy-webui-cloud.md) | 云服务器访问 WebUI 的部署说明 |
| [Zeabur 部署](docker/zeabur-deployment.md) | Zeabur 平台部署说明 |
| [桌面端打包说明](guide/desktop-package.md) | Electron 桌面端和 Web 构建产物打包说明 |

### 参考与开发

| 文档 | 内容 |
| --- | --- |
| [API 规格](architecture/api_spec.json) | FastAPI OpenAPI 规格产物 |
| [贡献指南](CONTRIBUTING.md) | Issue、PR、测试、文档同步和协作要求 |
| [开发规则 AGENTS.md](AGENTS.md) | 编码、测试、GOT 运行规范（唯一权威；`CLAUDE.md` 为重定向页） |

### 多语言

| 文档 | 内容 |
| --- | --- |
| [英文文档索引](INDEX_EN.md) | English documentation index |
| [英文 README](guide/README_EN.md) | English project overview and quick start |
| [繁中 README](guide/README_CHT.md) | 繁體中文項目概覽與快速開始 |

---

## 二区 · GOT 现行（`got/`）

> 2026-10-10 文档整理：顶层只留**清单类**，已完结的过程稿（0922 审计 / 0926 决策简报 / cp12 系列 / cp13 系列共 8 份）全部移入 [`got/archive/`](got/archive/)。**2026-10-14 治理轮再收 1 份**（`架构审查计划_20261011.md`，原仓库根 untracked）；同时把根目录仅剩的 2 份现行文档（`外网对标实施_PLAN.md` / `GOT_问题分析_PLAN.md`）按「计划单一落点」条款改名移入 `got/plans/`——**仓库根目录自此无 GOT 文档**。**2026-11-02**：`decision_brief_openc3_20260928.md` 由顶层移入 `got/archive/`（更正头 + §3.3 ② 已拍板）。

| 文档 | 性质 |
| --- | --- |
| [got/README.md](got/README.md) | GOT 文档导航 + 待实施计划排序总览 |
| [got/backlog.md](got/backlog.md) | **待实施**清单（六类处置、0902 15 项处置表、A6 转正与退役、需裁决事项） |
| [got/verification.md](got/verification.md) | **待验证 / 影子期**清单（判据、实测值与出处，不落达标判定；**2026-10-10 起含 MEG/FTS/FXG 三道买入侧新闸门判据**；「读数起算」已经 10-12 全流程实跑订正：三闸门时钟自 2026-10-12 起算，另含 GATE-1 / D-3 基线 / bear 拦单留痕等新起算节） |
| [got/architecture.md](got/architecture.md) | GOT 架构设计 v2.0（基于代码实际实现；⚠️ 正文基线为 0831 快照，**文首时效注记已于 2026-10-10 刷新**并列出四个已知脱节区） |
| [got/archive/decision_brief_openc3_20260928.md](got/archive/decision_brief_openc3_20260928.md) | **历史更正件（已归档）**：OPEN-C3「死分支」事实链已被 2026-10-10 更正头推翻；`range_reversal` 维持观测面已于 2026-10-15 拍板，不再待拍板 |

### 待实施 / 待批准计划（[`got/plans/`](got/plans/)，十三份）

| 计划 | 状态 | 开工前置 |
| --- | --- | --- |
| [plans/ambush_split_plan.md](got/plans/ambush_split_plan.md) | **合稿两份计划**（v3 埋伏分支 + v3.5 波次化生命周期），**两块主体均已实施**（v3：09-28 核实｜v3.5 S2–S5：10-10 核实；S5 校准脚本在库，首跑待埋点满 40 交易日） | 实施前先对账，勿重复；原残留两项均已收口（NUL 幽灵行清理 ✅ 10-10 实施；`wave_days` 回填 ✅ 10-12 裁决作废）⇒ **无待实施尾巴** |
| [plans/win_rate_optimization_plan.md](got/plans/win_rate_optimization_plan.md) | **已实施**（2026-09-17，4 步 + `GOT_WINRATE_*` 开关） | 待回测复验胜率改善 |
| [plans/入场分道与持仓延续性_总计划_20260930.md](got/plans/%E5%85%A5%E5%9C%BA%E5%88%86%E9%81%93%E4%B8%8E%E6%8C%81%E4%BB%93%E5%BB%B6%E7%BB%AD%E6%80%A7_%E6%80%BB%E8%AE%A1%E5%88%92_20260930.md) | 工作流 A/B/C **已合入**（`4be371b`）｜ **A 已判完成**（10-10 裁决：冻结机制实测生效，`final_pool>0` 不属 A 的责任边界，已移出）｜ B 行为面正常（拦单留痕真缺口已于 **2026-10-14 修法②收口**（tombstone + FXG 基线子块，backlog §1.H）；非 bear 日对照＝等条件，「埋伏被放行」归 A-2 线；非 bear 日两路合并排序缓析待分析）｜ C **已终结**（2026-10-10 裁，转持仓模块文档）｜ 下游两条已登 [backlog §1.H](got/backlog.md)；详见下行分析稿 |
| [plans/持仓模块重构_待分析与待实施清单_20261010.md](got/plans/%E6%8C%81%E4%BB%93%E6%A8%A1%E5%9D%97%E9%87%8D%E6%9E%84_%E5%BE%85%E5%88%86%E6%9E%90%E4%B8%8E%E5%BE%85%E5%AE%9E%E6%96%BD%E6%B8%85%E5%8D%95_20261010.md) | **待分析 + 待实施清单，未批准实施**；持仓侧问题的唯一归集点（以已终结的工作流 C 为开头） | 最硬一条：近似 HWM 下「曾盈利≥10%」形同恒真（实测 P&L −17.6% 却反推高点收益 +176%）⇒ **WI-P-1 修完前不得开 `GOT_TRAILING_STOP_ENABLED`**；快照地基近乎空（1 行 / 0 行）；挂号于 backlog §1.I |
| [plans/埋伏池归零下游拦点分析_20261010.md](got/plans/%E5%9F%8B%E4%BC%8F%E6%B1%A0%E5%BD%92%E9%9B%B6%E4%B8%8B%E6%B8%B8%E6%8B%A6%E7%82%B9%E5%88%86%E6%9E%90_20261010.md) | **分析稿，未批准实施**（与旧计划不混排） | 结论：不是 0.7 门槛拦的（全库 43/50 事件 ≥0.7，全天仅 1 例被拦），而是**事件→板块映射覆盖盲区**（16 候选 11 个整链零事件；两个板块宇宙不相交）；三条候选修法均未拍板 |
| [plans/architecture_optimization_implementation_20260929.md](got/plans/architecture_optimization_implementation_20260929.md) | 2 GATE + 26 WI 明细实施报告（§11 逐条登记已落地/延后裁决） | GATE-1/GATE-2/P0-3/P0-4/P0-5/P1-7/P1-9/P1-11/P2-9(a) 已落；余见 §11.2 |
| [plans/加速末期识别缺陷分析_20261008.md](got/plans/%E5%8A%A0%E9%80%9F%E6%9C%AB%E6%9C%9F%E8%AF%86%E5%88%AB%E7%BC%BA%E9%99%B7%E5%88%86%E6%9E%90_20261008.md) | 分析稿 + **MEG 已影子接线**（回测证真）；派生出 FTS / FXG 两道闸门 | 影子读数 ≥1 周后切 veto；〔10-10 曾测读数 MEG 1 / FTS 0 / FXG 0，**〔10-12 实跑订正：三闸门时钟自 2026-10-12 起算，首份 FTS/FXG 产物已落盘〕**判读按 `bar_complete` 分组〕；三闸门判据见 [verification.md](got/verification.md) 顶部 |
| [plans/竞品经验补强计划_20261006.md](got/plans/%E7%AB%9E%E5%93%81%E7%BB%8F%E9%AA%8C%E8%A1%A5%E5%BC%BA%E8%AE%A1%E5%88%92_20261006.md) | ✅ **一期已实施（2026-10-11，commit `5a4f29f`）**——经外网对标计划 §7 拍板批准全量按序，已落 D0→D→G→A→B→C1/C2→E1→E3→F（均影子/观测先行）；E2 回放与 lite 自动降级按前置阻塞未开工 | 逐批读数达标后转正（判据见 verification.md 10-11 补登节）；A 二期 rumor 折扣需历史重放 + 人工确认 |
| [plans/埋伏分支拆分_PLAN_v2.md](got/plans/埋伏分支拆分_PLAN_v2.md) | **已实施（2026-10-13，Step 0 门 PASS，影子期 ACTIVE=0）**——埋伏道拆分 v2（抽道/四闸门/Node 7 接线；后经 10-13 P5 变量遮蔽修复 + 10-14 G-A2 三标量接线补强） | 转正判据见该计划 §5.2；生效验证（新归档含 pos_* 三键）登 [verification.md](got/verification.md) 顶部 10-14 节 |
| [plans/GOT三问架构裁决_实施计划_20261015.md](got/plans/GOT%E4%B8%89%E9%97%AE%E6%9E%B6%E6%9E%84%E8%A3%81%E5%86%B3_%E5%AE%9E%E6%96%BD%E8%AE%A1%E5%88%92_20261015.md)（原仓库根 `GOT三问架构裁决_PLAN.md`，10-15 治理轮移入） | **现行实施计划（Phase A–F）**：A 文档落账 ✅（10-15，backlog 三处挂号 + 持仓文档 §七 + 归档稿 [GOT三问架构裁决_20261014.md](got/plans/GOT%E4%B8%89%E9%97%AE%E6%9E%B6%E6%9E%84%E8%A3%81%E5%86%B3_20261014.md)，裁决正文 [docs/ 顶层](GOT%E4%B8%89%E9%97%AE%E6%9E%B6%E6%9E%84%E8%A3%81%E5%86%B3_PLAN.md)保留不删）；C 决策编排层 v0（C1 trade_plans 表 / C2 `decision_plan.py` / C3 通知接线 / C4 thesis 状态机挂 B 门 / C5 归档渲染）、D 分道预算标定（`SCORING_FORMULA_VERSION` → 2026-10-15.1）、E 三路路由影子、F 埋伏 per-lane validation 待实施 | **Phase B step 0 裁决资料包＝用户裁决门**（W3 五处口径枚举 + 入场锚选项 + thesis 状态机定稿），C4/Phase D ACTIVE 接线挂其后；C1/C2/C3/C5、E、F 不受 B 门阻塞可先行 |
| [plans/外网对标实施计划_20261011.md](got/plans/%E5%A4%96%E7%BD%91%E5%AF%B9%E6%A0%87%E5%AE%9E%E6%96%BD%E8%AE%A1%E5%88%92_20261011.md)（原根目录 `外网对标实施_PLAN.md`，10-14 治理轮移入） | **部分实施**——项二（direction 净化）✅ / 项一（宏观扇出②+②b）代码在位但双开关缺省关 / 项三 3a ✅（D-3 基线 10-12 起算）/ 项四另列竞品计划 | 3b 双前置＝D-3 基线满 ≥10 交易日 + AlphaSift 接口三点设计拍板；项一翻 `GOT_MACRO_FANOUT_ENABLED`/`GOT_MACRO_SECTOR_ALIGN_ENABLED` 后待全流程读数（判据见 verification.md 项一节） |
| [plans/regime与数据流断路_文献对照分析_20261011.md](got/plans/regime%E4%B8%8E%E6%95%B0%E6%8D%AE%E6%B5%81%E6%96%AD%E8%B7%AF_%E6%96%87%E7%8C%AE%E5%AF%B9%E7%85%A7%E5%88%86%E6%9E%90_20261011.md)（原根目录 untracked `GOT_问题分析_PLAN.md`，10-14 治理轮移入） | **分析稿（现行，零生产代码）**——分析一（regime 钉死豁免键四方案）供 §7 #14 拍板；分析二（数据流断路接线）中 `confidence_multiplier` 接线建议已被 10-14 §1.J 裁决覆盖为「删除（已实施）」 | backlog §7 **#14** 待拍板；**#16**（cross_node_signals）与 #14 串联出稿；**#15**（industry_top）可独立出分析稿 |
| [plans/埋伏道误判复盘_002009天奇_20261029.md](got/plans/%E5%9F%8B%E4%BC%8F%E9%81%93%E8%AF%AF%E5%88%A4%E5%A4%8D%E7%9B%98_002009%E5%A4%A9%E5%A5%87_20261029.md) | **分析稿，未批准实施**（1028 建仓 / 1029 未止损全链复盘 + Node 2 主线/埋伏盲区；零生产代码） | 6 条 P0：板块流不可按主题归因 / G-A4 无个股项且同分取输入序 / 个股资金未入闸门 / ambush 缺评（tech 用 ambush_rank、risk 默认 100）/ 叙事道身份错位 / 硬止损优先级最低；**§8 另列 N-1～N-6**（组名隐藏成员 / HI1 刀锋 / ambush 分数不落盘 / D7-D9 映射仅前 300 且逐日排序、可能静默缺失 / 无「具身智能/物理AI」软件模型家族（仅硬件别名） / 无龙头先行通道）；**§9 拍板方向 B+C**（T1 评分+催化 / T2 单评分 / T3 单催化分层准入；催化源扩到原始新闻检索、潜在事件池、跨日发酵；未实施）；**§10 催化剂重复身份**（全球 5 条为板块扇出、HBM 11-04/05 为叙事同源重复；事件身份 vs 行身份拆分、展示聚合/同源合并分级，未实施）；**§11 叙事层 Agent 化**（概念身份时间序列 + 个股涨跌归因两个窄任务；规则保留硬闸门/仓位/止损；影子先行，未实施）；**§12 自迭代审计 Agent 化**（收集→异常分诊→取证→grilling→报告→校验；只读草稿、人工裁决，未实施） |

> ℹ️ **MEG / FTS / FXG 三道买入侧闸门无独立计划文档**（判据在 [verification.md](got/verification.md)，设计与回测在 `plans/加速末期识别缺陷分析_20261008.md` §七）。新计划一律写入 `docs/got/plans/`，**仓库根目录不留 PLAN 稿**（见 [AGENTS.md](AGENTS.md) 文档治理条款）。

### GOT 过程稿（已移入 [`got/archive/`](got/archive/)，不再更新）

[audit_20260922.md](got/archive/audit_20260922.md) / [decision_brief_20260926.md](got/archive/decision_brief_20260926.md) / cp12_×3（[文案草案](got/archive/cp12_prompt_variants_draft_20260926.md)、[M3 预审表](got/archive/cp12_m3_audit_20260926.md)、[事件级对照](got/archive/cp12_event_level_ab_20260926.md)）/ cp13_×3（[设计笔记](got/archive/cp13_grounding_pipeline_research_20260926.md)、[阶段1 矩阵](got/archive/cp13_stage1_matrix_20260927.md)、[阶段2 收口](got/archive/cp13_phase2_closeout_20260928.md)）/ **10-14 新收**：[架构审查计划_20261011.md](got/archive/%E6%9E%B6%E6%9E%84%E5%AE%A1%E6%9F%A5%E8%AE%A1%E5%88%92_20261011.md)——原位置与取代关系见 [got/archive/README.md](got/archive/README.md) §一之补。

### 专项报告（[`reports/`](reports/)，诊断与预演类）

| 报告 | 内容 |
| --- | --- |
| [reports/20261012_黄金票出局根因与窗口修复预演.md](reports/20261012_黄金票出局根因与窗口修复预演.md) | 选股器资金面板块流半场窗口错配（当日 90 键 vs 3d 477 键）+ 匹配弱于聚合器双重缺陷诊断；bg_9 窗口反事实（3d 窗口黄金 10 只进 Top20）+ monkeypatch 端到端只读预演（黄金进 R2 短名单、死于 R3 波段技术闸门）；修复方案待拍板 |

---

## 三区 · 归档（历史，不再更新）

### GOT 历史文档（[`got/archive/`](got/archive/)）

原始文档索引与「已实施并完成验收」清单见 [got/archive/README.md](got/archive/README.md)：0902 审核报告、0903 主方案（agent_glue）、0911 四层计划与复审、修订台账、Phase 6 草案、已验证完成日志，原 `docs/custom/` 的 3 篇已完结 GOT 文档，以及 **2026-10-10 新收的 14 份**（cp12/cp13 系列、审计与决策简报、根目录 PLAN 稿与审查留痕）。

### 非 GOT 已完结计划（[`archive/plans/`](archive/plans/)）

| 文档 | 状态 |
| --- | --- |
| [个性化选股系统详细设计](archive/plans/screener_design.md) | 已实施并归档 |
| [持仓服务整合到选股流程](archive/plans/plan_portfolio_integration.md) | 已实施并归档 |
| [DSA 上云部署计划](archive/plans/cloud_deployment_plan.md) | 挂起待确认（里程碑全部未执行） |
| [选股系统实施记录（原 docs/CLAUDE.md）](archive/plans/screener_phase_devlog.md) | 已实施历史记录（Phase 1–5，2026-06） |

---

*索引重排日期：2026-09-17（本次起 INDEX 为全量索引，新增文档须同步登记）*
*修订：2026-10-10 持仓模块线开工——工作流 C 由负责人裁**直接终结**（持仓模块要大改），新建 [`plans/持仓模块重构_待分析与待实施清单_20261010.md`](got/plans/%E6%8C%81%E4%BB%93%E6%A8%A1%E5%9D%97%E9%87%8D%E6%9E%84_%E5%BE%85%E5%88%86%E6%9E%90%E4%B8%8E%E5%BE%85%E5%AE%9E%E6%96%BD%E6%B8%85%E5%8D%95_20261010.md) 作为持仓侧唯一归集点（plans 八份）；同时清掉一条我自己写错的口径：前面几轮写的「C 等的是开关上线而非样本」不准——实测发现近似 HWM 下「曾盈利≥10%」形同恒真（浮亏 17.6% 的票反推高点收益 +176%），**修完 WI-P-1 前不得开 `GOT_TRAILING_STOP_ENABLED`**；待实施项挂号于 backlog §1.I。零代码行为变更。*
*修订：2026-10-10 口径收口第二批（除状态头外另清四处「文档与实测不符」）——Ⅰ verification.md 顶部三闸门「读数起算」原写自 10-09 起算，实测为 MEG 1 天 / FTS 0 / FXG 0（1009 那次跑的 commit `d391345` 早于 FTS/FXG 合入，10-10 无完整归档）；Ⅱ backlog §5 的 5c「样本 6<10 DEFER」已过期——`glue_consumed=true` 现 11 个归档日 ≥ 10 ⇒ 4.2 P1-1 分位重标解锁；Ⅲ backlog §8 #8（R-D2 附录 A）重标可脚本化的两项（divergent 11 日/154 条且全在 0917 后；蓄水池顶格 20 日/最长连续 8），③④ 两项属人工清点不给值；Ⅳ architecture.md 时效注记刷新至 10-10。未动任何代码行为与开关值；页内同行与本文件页脚同步。*
*修订：2026-10-10 口径不一致收口——`ambush_split_plan.md` 内嵌的 v3.5 节原挂「设计收口，待实施」，与同文件文首「主体已实施」与本索引互相矛盾；逐条代码实测后改为「S2–S5 已实施 + 两处从未落地」（见该文件 v3.5 状态头），本页与 `got/README.md` 同行同步；`sector_analysis.py` 对未创建脚本 `scripts/backfill_wave_days.py` 的死引用注释已订正（注释文案，零行为变更）。*
*修订：2026-10-10 文档整理——docs/got 顶层 8 份完结过程稿移入 `got/archive/`（代码/测试/CHANGELOG 中的路径引用已同步改指）；仓库根目录 6 份 PLAN/审核/报告/审查稿移入 `got/archive/` 并加状态头（`FIX_SUMMARY.md` 同步改名，`.gitignore` 逐条忽略规则作废）；二区拆为「清单类 / plans 计划 / 已归档过程稿」三段并补齐六份计划状态。*

*修订：2026-10-14 文档治理轮（零代码行为变更）——仓库根目录 GOT 文档清零：`GOT_项目架构审查_PLAN.md`（untracked，审查已执行）完结归档为 [`got/archive/架构审查计划_20261011.md`](got/archive/%E6%9E%B6%E6%9E%84%E5%AE%A1%E6%9F%A5%E8%AE%A1%E5%88%92_20261011.md)（登记见 archive README 一之补②）；`GOT_问题分析_PLAN.md`（untracked，现行分析）改名入库 [`got/plans/regime与数据流断路_文献对照分析_20261011.md`](got/plans/regime%E4%B8%8E%E6%95%B0%E6%8D%AE%E6%B5%81%E6%96%AD%E8%B7%AF_%E6%96%87%E7%8C%AE%E5%AF%B9%E7%85%A7%E5%88%86%E6%9E%90_20261011.md)；`外网对标实施_PLAN.md` 改名移入 [`got/plans/外网对标实施计划_20261011.md`](got/plans/%E5%A4%96%E7%BD%91%E5%AF%B9%E6%A0%87%E5%AE%9E%E6%96%BD%E8%AE%A1%E5%88%92_20261011.md)——backlog/verification/CHANGELOG 内带路径引用全部同步改指。二区 plans 行数八→十一（补外网对标/regime 分析/埋伏分支拆分_PLAN_v2 三行），ambush_split 残留尾巴、竞品一期已实施、入场分道 B 拦单留痕 10-14 收口、加速末期三闸门时钟 10-12 起算四处过期状态描述同步更新；`scripts/calibrate_sector_thresholds.py`（10-11 取证脚本）入库，`update_portfolio.py` 经核为硬编码一次性记账脚本移 `.review_tmp/` 不入库；持仓重构文档 §一 加 10-14 覆写注记。*
