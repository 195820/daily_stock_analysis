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

| 文档 | 性质 |
| --- | --- |
| [got/README.md](got/README.md) | GOT 文档导航 + 待办排序总览 |
| [got/backlog.md](got/backlog.md) | **待实施**清单（六类处置、0902 15 项处置表、A6 转正与退役、需裁决事项） |
| [got/verification.md](got/verification.md) | **待验证 / 影子期**清单（判据、实测值与出处，不落达标判定） |
| [got/architecture.md](got/architecture.md) | GOT 架构设计 v2.0（基于代码实际实现；原根目录 `agent_cot_design.md`） |
| [got/plans/ambush_split_plan.md](got/plans/ambush_split_plan.md) | 埋伏分支拆分设计（设计收口，待实施；原根目录 `plan.md`） |
| [got/audit_20260922.md](got/audit_20260922.md) | 2026-09-22 当日运行审计报告（P0-P3 分级 + Q1-Q9 决策 + 修复锚点；含 `4ad12b6` 第二轮审核修复 Q1-Q9） |
| [got/decision_brief_20260926.md](got/decision_brief_20260926.md) | 2026-09-26 决策简报（三类未结项分诊的待拍板项：选项/影响面/实测证据/建议，对应 backlog §7；已改登「拍板结果」） |
| [got/decision_brief_openc3_20260928.md](got/decision_brief_openc3_20260928.md) | OPEN-C3 死分支决策简报（排期轮批 2 取证产出：建议删分支，待拍板） |
| [got/cp12_prompt_variants_draft_20260926.md](got/cp12_prompt_variants_draft_20260926.md) | C-P12 prompt A/B 两版文案草案 + 拍板结果 + M2×M3 对照矩阵运行结论（含 0902 max_tokens 不对称披露） |
| [got/cp12_m3_audit_20260926.md](got/cp12_m3_audit_20260926.md) | C-P12 M3 误配率分层抽样预审表（两变体各 10 条 relation，待负责人终审划勾） |
| [got/cp12_event_level_ab_20260926.md](got/cp12_event_level_ab_20260926.md) | C-P12 事件级 A/B 覆盖对照表（逐日覆盖汇总 + 能源/油价类明细 + 覆盖分歧明细；暴露 0902 A 版退化循环与 B 版过度归属两项质量发现） |
| [got/cp13_grounding_pipeline_research_20260926.md](got/cp13_grounding_pipeline_research_20260926.md) | C-P13 设计笔记：基于外网论文（THEME/CoVe/CAD/CausalStock/FinRipple/LangExtract）的事件→板块「接地式召回-验证」5 层管线方案（A/B 单旋钮架构天花板的整合修正，待拍板） |
| [got/cp13_stage1_matrix_20260927.md](got/cp13_stage1_matrix_20260927.md) | C-P13 阶段1（L1+L2+L3）落地结论：L1 冒烟 K 标定（12→8）+ 9 天矩阵 M2×M3 读数 + 验收六条对照（点 2 未达归因摊派极端日，判读归负责人）+ 调用成本实测（OPEN-C7 回填） |
| [got/cp13_phase2_closeout_20260928.md](got/cp13_phase2_closeout_20260928.md) | **C-P13 阶段2 收口决策**：realized 收益率标定 confidence 红线证伪（19 天池+反事实扩样到 1521，alpha≈0/conf rho−0.06/CoVe kept−dropped 仅 0.04pp）→ 阶段2 关闭、阶段1 接地管线休眠；关键纠偏：confidence 在 M3 归属口径有效（≠收益率），勿误记为无用 |
| [got/plans/win_rate_optimization_plan.md](got/plans/win_rate_optimization_plan.md) | 选股胜率优化 4 步计划（9 月回测驱动；2026-09-17 已实施，待回测复验） |

---

## 三区 · 归档（历史，不再更新）

### GOT 历史文档（[`got/archive/`](got/archive/)）

原始文档索引与「已实施并完成验收」清单见 [got/archive/README.md](got/archive/README.md)：0902 审核报告、0903 主方案（agent_glue）、0911 四层计划与复审、修订台账、Phase 6 草案、已验证完成日志，以及原 `docs/custom/` 的 3 篇已完结 GOT 文档（0827 诊断、时间线增强计划、试点报告）。

### 非 GOT 已完结计划（[`archive/plans/`](archive/plans/)）

| 文档 | 状态 |
| --- | --- |
| [个性化选股系统详细设计](archive/plans/screener_design.md) | 已实施并归档 |
| [持仓服务整合到选股流程](archive/plans/plan_portfolio_integration.md) | 已实施并归档 |
| [DSA 上云部署计划](archive/plans/cloud_deployment_plan.md) | 挂起待确认（里程碑全部未执行） |
| [选股系统实施记录（原 docs/CLAUDE.md）](archive/plans/screener_phase_devlog.md) | 已实施历史记录（Phase 1–5，2026-06） |

---

*索引重排日期：2026-09-17（本次起 INDEX 为全量索引，新增文档须同步登记）*
