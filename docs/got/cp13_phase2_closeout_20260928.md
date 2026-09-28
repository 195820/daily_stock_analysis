# C-P13 阶段2 收口决策纪要（2026-09-28）：realized 标定证伪 → 接地管线休眠

出处：阶段2 立项 = 标定 LLM 自报 confidence 的「红线 0.6」（`docs/got/backlog.md` OPEN-C6 / cp13 笔记 OPEN-1、L5）；阶段1 结论见 `docs/got/cp13_stage1_matrix_20260927.md`。
裁决：**阶段2 的 realized 收益率标定关闭为证伪负结果；C-P13 阶段1 接地管线转入休眠（合入保留、replay 门控不解除、不转正、不删除）。**

---

## 1. 阶段2 想做什么、怎么做的

目标：拿历史归档回放，对每条 glue relation 用**板块 T+3 实际涨幅**回算兑现，画 confidence 分桶可靠性曲线，据而定红线阈值 τ。

数据与方法（全程 0 生产 LLM 调用变更，只跑 replay + 纯计算）：
- **19 天回放池**：0828–0924，`pipeline_l1l3`(9) + `pipeline_calib`(10) 两组变体；同模型（env DeepSeek）、temp=0、口径一致。
- **realized 标签**：`excess = 板块T+3 − 当日候选全集(各分组 members 并集)T+3 中位数`；方向感知兑现（positive→excess≥+3pp 记兑现）。
- 三步剥离混淆 + 一次反事实扩样：
  | 实验 | 关键读数 |
  |---|---|
  | 步骤1 基线对照 | glue 选中 vs 候选全集 alpha **mean +0.10% / median −0.09%**（≈0） |
  | 步骤2 特征扫描 | 所有 x 轴 \|Spearman\|<0.13，无一能排序 realized |
  | 步骤3 状态分层（主线延伸度） | 同态 alpha **−0.47%**；conf rho **−0.116**（反标定） |
  | 反事实（cove_detail 埋点，样本 797→**1521**） | CoVe kept−dropped realized 仅 **0.04pp**；cove_verdict rho **−0.019** |

## 2. 结论一：红线用「收益率」标定 = 证伪，关闭

- **confidence 不携带概率信息（对收益率而言）**：分桶曲线呈倒 U、峰值 conf≈0.50，高置信端（0.85）反而转负；rho −0.06。样本扩到 1521 后依旧。
- **glue 选板无前瞻 alpha**：相对自家候选池 mean +0.10%/median −0.09%，19 天逐日在 +3.9%↔−3.0% 随机摆。
- **CoVe（L3 剪枝层）realized 价值≈0**：保留支(+0.13%) 与丢弃支(+0.09%) 统计不可分；CoVe 倾向保留高 conf（kept 均值 conf 0.612 vs dropped 0.508），但复述出来的东西结果上不比它丢掉的强。

## 3. 结论二（关键纠偏，务必勿写歪）：**这不是「confidence 没用」**

阶段2 证伪的仅是 **"confidence 能预测 T+3 收益率"**。而 C-P13 的立身之物是**归属正确率**，不是收益预测。两套尺子给出**相反且都成立**的读数：

- 阶段1 人工 M3 终审（`cp13_stage1_matrix_20260927.md`）：**conf≥0.70 的 4 条全真实、0 误配；4 条牵强 100% 落在 conf≤0.50。**
- 即 **confidence 在「这条事件→板块归属真不真」上有清晰信号**，只是与「板块涨不涨」无关。

**教训**：收益率对弱市 3 日窗噪声过大（板块 T+3 均值 −0.78%、excess σ 仅 1.5%，+3pp≈2σ），是把接地管线放错了考台。**将来若重启红线标定，尺子必须是 M3 归属口径，不是 realized 收益率。**

## 4. 阶段1 处置：合入 + replay 门控，休眠

- 代码保留在 main（commit `33d8e36`），双与门 `pipeline_on = GLUE_PIPELINE_ENABLED and _replay_mode` 使生产永不进入（`test_production_ignores_pipeline` 守门）。
- 不转正、不删除、不 revert。`cove_detail` 逐关系埋点（仅 replay trace 落盘，schema 不变）**保留**，供解冻复评扩样。
- 理由：① 零生产风险；② 是这批证伪结论的取数仪器；③ 阶段1 已在正确尺子（M2 覆盖 ≥ 基线 a、M3=0%）上达标，属「可转正但暂缓」，非「被证伪」。

## 5. 对后续流程的影响（逐项）

| 下游项 | 影响 |
|---|---|
| 生产 Node 6 / A6 glue | **零**：从未接入，休眠=现状 |
| Node 6 矛盾仲裁的 `confidence>=0.6` 门（aggregator L4151/L4177） | **无关**：那是仲裁判定门，非 glue relation 红线，C-P13 废弃不动它 |
| 埋伏分支 `ambush_split_plan` | **不阻塞**：独立轨道；依赖方向相反——埋伏先落地，C-P13 才有 ambush 样本。休眠反而让路 |
| OPEN-C1a（transition 阈值重标，原「与 C-P13 同批」） | 解耦但无实质损失：真阻塞是「样本<40 交易日」，非 C-P13 |
| 阶段2 L4 图谱传播 / L5 方向·幅度校准 / K 终标定(OPEN-C6) | **一起推迟**（治 M3 里 4/10 牵强的「方向反/跨境弱传导」）；但仅在转正后才有回报，休眠期无现损 |

## 6. 解冻 / 重启条件（任一触发即复评）

1. 埋伏通道落地 → 有 ambush/near_miss 样本，可补测「短窗点火」；
2. 决定以 **M3 归属口径**（非收益率）重定 confidence 红线 → 用留存 `cove_detail` 扩样重算 reliability 曲线；
3. 出现新 regime / ≥40 交易日样本窗 → realized 口径可再试，且 OPEN-C1a/C6 一并重标。

## 7. 复现与数据

- 分析脚本：`.review_tmp/cp13s2_step{1,2,3}.py`（三步）、`cp13cf_analyze.py`（反事实）；读数 `cp13cf_out.txt`。
- 埋点：19 天 `_replay/<variant>/glue_pipeline_trace.json` 新增 `cove_detail[]`（逐关系 board_code/confidence/strength/topk_rank/topk_sim/cove_verdict/survived），共 1683 条。
- 板块日线缓存：`.review_tmp/cp13s2_board_klines.json`。
