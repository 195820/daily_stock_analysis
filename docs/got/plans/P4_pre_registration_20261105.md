# P4 样本外验收预注册（Pre-registration，2026-11-05）

> 状态：**协议冻结，未执行**。冻结时点：2026-11-05，代码基线 `158b80c`。
> 目的：在**看到任何 holdout 结果之前**把比较对象、样本门槛、指标、停止条件与变更规则写死，
> 防止事后调参消费 holdout（实施稿 §2.3、§9）。
> 关系：判定器实现见 `src/got/theme_acceptance.py`；判据登记见 `docs/got/verification.md` 2026-11-05 节。

## 1. 冻结输入（不得改动）

| 项 | 冻结值 |
|---|---|
| 基线 arm | `data/got_daily/backtest_portfolio_baseline_20260801_20261103.json` |
| 基线 payload digest | `e4451fbd4c0e879e`（内容寻址；`built_at` 不参与） |
| 行情快照内容指纹 | `data_sha256 = 85f6f079cc2531e7`（allocation + candidate_pool，365 只） |
| 初始资金 | ¥1,000,000 |
| 成本模型 | 佣金 2.5bp（min ¥5）/ 印花税 5bp（卖出）/ 过户费 0.1bp / 滑点 5bp |
| 成交口径 | 决策日收盘 → 下一交易日开盘；100 股整手；lot 级 T+1；涨跌停/停牌/缺前收不可成交 |
| 评分公式版本 | `2026-10-30.1-agent-runtime-v1` |
| 复现命令 | `python scripts/backtest_portfolio_baseline.py --start 20260801 --end 20261103 --check-repro` |

任何一项变更（成本、初始资金、快照、runner schema、评分公式）都必须记为**新协议版本**，旧 holdout 结果作废。

## 2. 开发集 vs 最终样本外

- **开发集**：2026-07-24 ~ 2026-11-03 的全部日期。已用于 episode 规则选择、E16 变体筛选、
  P3 失真口径与全部审计；**不得再称为最终样本外**。
- **最终 holdout**：代码冻结日之后新增的交易日，且：
  - ≥60 个可用交易日；
  - ≥100 笔已闭合模拟往返；
  - 期间不修改任何阈值/开关/口径；一旦查看并据此修改，该段自动降级为开发集，
    最终验证只能改用其后新增日期。
- 历史归档只作限定区间对照；无法从 point-in-time 输入重建的逻辑，只接受前向影子模拟结果。

## 3. 四组 arm 定义（同一 runner、同一输入）

| arm | 内容 | 当前状态 |
|---|---|---|
| A1 冻结基线 | 归档 allocation → 模拟成交与组合记账 | 已有（5 笔闭合） |
| A2 基线 + 观察账本 | 账本零决策改动，**必须与 A1 逐字节同净值** | 可运行（构造上恒等） |
| A3 + 路由/setup 变体 | E16/强 leading/资金 setup 的独立影子变体 | 未产出（需 P2-b-3） |
| A4 + 个股转换 | 窄主题→board→个股证据映射与候选转换 | 未产出（需 P3-b） |

A2 的作用是**不变式检查**：若 A2 与 A1 的 `payload_sha256` 不同，说明账本意外进入决策路径，立即停。

## 4. 指标与判定（机械执行）

判定器：`python scripts/evaluate_theme_arms.py --baseline <A1.json> --variant <Ax.json>`。

| 判据 | 通过条件 |
|---|---|
| 样本 | 两臂均 ≥60 日且 ≥100 笔闭合 |
| 净收益胜率 | 变体−基线 >0，且日期簇 bootstrap 95% CI 下界 >0 |
| 组合累计净收益率 | 逐日收益差 >0，且日期簇 bootstrap 95% CI 下界 >0 |
| 最大回撤 | 变体相对基线恶化 ≤1pp |
| 单日集中度 | 仅披露（单日绝对贡献/累计差值） |
| 风控零绕过 | `not_verifiable` → 必须人工签署 P5 审计产物 |

状态优先级：`insufficient_sample > unresolved > fail > pass_pending_attestation > pass`。
**机械判据全过但未签署时只能是 `pass_pending_attestation`，不自动 pass。**

同时披露（不参与判定）：平均每笔净收益、盈亏比、换手率、仓位利用率、交易数、主线/埋伏与 regime 分组。

## 5. 停止条件（任一触发即停对应变体）

- 为抢救历史案例放宽过热、拥挤、趋势破坏或组合风控；
- 以覆盖率、状态变更或板块指数收益替代组合收益验收；
- 样本不足仍宣称有效；
- 反复调参消费同一 holdout；
- 用不同来源快照拼出有利结果；
- 观察通道成为绕闸买入通道（A2 ≠ A1 即视为触发）。

## 6. 当前状态（2026-11-05）

- 判定器自检：`insufficient_sample`（闭合 5 < 100）；`payload_schema` pass；
  单日集中度为 `disclosure`；`risk_gate_no_bypass` = `not_verifiable`。
- **未执行任何 holdout 比较**；A3/A4 不存在；基线闭合样本远低于门槛。
- 解阻条件：生产 `GOT_THEME_LEDGER=shadow` 积累 ≥20 个交易日 → P2-b-3/P3-b 产出 A3/A4 →
  新增日期达到 60 日/100 笔后，按本协议一次性执行，不允许“看一眼再调”。
