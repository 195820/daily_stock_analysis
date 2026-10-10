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

> 2026-10-10 文档整理：顶层只留**清单类 + 一份待拍板简报**，已完结的过程稿（0922 审计 / 0926 决策简报 / cp12 系列 / cp13 系列共 8 份）全部移入 [`got/archive/`](got/archive/)。

| 文档 | 性质 |
| --- | --- |
| [got/README.md](got/README.md) | GOT 文档导航 + 待实施计划排序总览 |
| [got/backlog.md](got/backlog.md) | **待实施**清单（六类处置、0902 15 项处置表、A6 转正与退役、需裁决事项） |
| [got/verification.md](got/verification.md) | **待验证 / 影子期**清单（判据、实测值与出处，不落达标判定；**2026-10-10 起含 MEG/FTS/FXG 三道买入侧新闸门判据**） |
| [got/architecture.md](got/architecture.md) | GOT 架构设计 v2.0（基于代码实际实现；⚠️ 正文基线为 0831 快照，文首有时效注记） |
| [got/decision_brief_openc3_20260928.md](got/decision_brief_openc3_20260928.md) | **当前唯一待拍板简报**（OPEN-C3 `persistence_regime` 死分支：接线/删分支/保留三选一），故不入归档 |

### 待实施 / 待批准计划（[`got/plans/`](got/plans/)，六份）

| 计划 | 状态 | 开工前置 |
| --- | --- | --- |
| [plans/ambush_split_plan.md](got/plans/ambush_split_plan.md) | 设计收口，**主体已实施**（子项进度已在 CHANGELOG 记账） | 实施前先对账，勿重复 |
| [plans/win_rate_optimization_plan.md](got/plans/win_rate_optimization_plan.md) | **已实施**（2026-09-17，4 步 + `GOT_WINRATE_*` 开关） | 待回测复验胜率改善 |
| [plans/入场分道与持仓延续性_总计划_20260930.md](got/plans/%E5%85%A5%E5%9C%BA%E5%88%86%E9%81%93%E4%B8%8E%E6%8C%81%E4%BB%93%E5%BB%B6%E7%BB%AD%E6%80%A7_%E6%80%BB%E8%AE%A1%E5%88%92_20260930.md) | 工作流 A/B/C **已合入**（`4be371b`）｜ **待全流程实跑验收**（2026-10-10 核查：冻结开关已开但 `ambush_gate_funnel.final_pool` 仍为 0） | 交易日 conda 全流程实跑；资金流数据源修复后重调 G4 |
| [plans/architecture_optimization_implementation_20260929.md](got/plans/architecture_optimization_implementation_20260929.md) | 2 GATE + 26 WI 明细实施报告（§11 逐条登记已落地/延后裁决） | GATE-1/GATE-2/P0-3/P0-4/P0-5/P1-7/P1-9/P1-11/P2-9(a) 已落；余见 §11.2 |
| [plans/加速末期识别缺陷分析_20261008.md](got/plans/%E5%8A%A0%E9%80%9F%E6%9C%AB%E6%9C%9F%E8%AF%86%E5%88%AB%E7%BC%BA%E9%99%B7%E5%88%86%E6%9E%90_20261008.md) | 分析稿 + **MEG 已影子接线**（回测证真）；派生出 FTS / FXG 两道闸门 | 影子读数 ≥1 周后切 veto；三闸门判据见 [verification.md](got/verification.md) 顶部 |
| [plans/竞品经验补强计划_20261006.md](got/plans/%E7%AB%9E%E5%93%81%E7%BB%8F%E9%AA%8C%E8%A1%A5%E5%BC%BA%E8%AE%A1%E5%88%92_20261006.md) | ⚠️ **规划稿，未批准实施**（批次 A–G 零落地：`news_pulse.py`/`output_review.py`/`data_consistency.py`/`backtest_standard.py` 均不存在） | 负责人批准 + 建议序 D0→D→G→A→B→C→E→F |

> ℹ️ **MEG / FTS / FXG 三道买入侧闸门无独立计划文档**（判据在 [verification.md](got/verification.md)，设计与回测在 `plans/加速末期识别缺陷分析_20261008.md` §七）。新计划一律写入 `docs/got/plans/`，**仓库根目录不留 PLAN 稿**（见 [AGENTS.md](AGENTS.md) 文档治理条款）。

### GOT 过程稿（已移入 [`got/archive/`](got/archive/)，不再更新）

[audit_20260922.md](got/archive/audit_20260922.md) / [decision_brief_20260926.md](got/archive/decision_brief_20260926.md) / cp12_×3（[文案草案](got/archive/cp12_prompt_variants_draft_20260926.md)、[M3 预审表](got/archive/cp12_m3_audit_20260926.md)、[事件级对照](got/archive/cp12_event_level_ab_20260926.md)）/ cp13_×3（[设计笔记](got/archive/cp13_grounding_pipeline_research_20260926.md)、[阶段1 矩阵](got/archive/cp13_stage1_matrix_20260927.md)、[阶段2 收口](got/archive/cp13_phase2_closeout_20260928.md)）——原位置与取代关系见 [got/archive/README.md](got/archive/README.md) §一之补。

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
*修订：2026-10-10 文档整理——docs/got 顶层 8 份完结过程稿移入 `got/archive/`（代码/测试/CHANGELOG 中的路径引用已同步改指）；仓库根目录 6 份 PLAN/审核/报告/审查稿移入 `got/archive/` 并加状态头（`FIX_SUMMARY.md` 同步改名，`.gitignore` 逐条忽略规则作废）；二区拆为「清单类 / plans 计划 / 已归档过程稿」三段并补齐六份计划状态。*
