# 非 GOT 已完结计划归档

> 2026-09-17 文档结构重排建立本目录，收纳旧项目（非 GOT）线上**已实施或已终结**的计划/设计文档（原 `docs/custom/` 非 GOT 部分 + 原 `docs/CLAUDE.md` 正文历史）。GOT 线历史归档在 [`../../got/archive/`](../../got/archive/README.md)。
> 本目录**不再更新**；现行文档入口见 [`docs/INDEX.md`](../../INDEX.md)。

| 文档 | 性质 | 状态 |
| --- | --- | --- |
| [`screener_design.md`](screener_design.md) | 个性化选股系统详细设计（NGA 舆情 → 板块形态 → 策略选股 → AI 挑选 → 推送） | ✅ 已实施（服务均在 `src/services/`） |
| [`plan_portfolio_integration.md`](plan_portfolio_integration.md) | 持仓服务整合到选股流程 | ✅ 已实施（`_enrich_portfolio_context`，开关 `SCREENER_PORTFOLIO_ENABLED`） |
| [`cloud_deployment_plan.md`](cloud_deployment_plan.md) | DSA 上云部署计划（阿里云轻量 2 核 2G） | ⚠️ 挂起待确认（2026-09-14 盘点里程碑全部未执行） |
| [`screener_phase_devlog.md`](screener_phase_devlog.md) | 选股系统 Phase 1–5 实施记录（原 `docs/CLAUDE.md` 正文，含当时的 conda 环境与东财反爬配置） | 📜 历史记录（2026-06，开发规则以 `docs/AGENTS.md` 为唯一权威） |
