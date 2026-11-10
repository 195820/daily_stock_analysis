"""GOT 影子模式登记表（单一真源）。

四问准入（详见 ``docs/AGENTS.md``「影子模式治理」）：

1. **会改决策吗？** 不会 → 只能叫 ``trace`` / ``observation``，不设转正时钟。
2. **归档输入 + 历史行情能复算吗？** 能 → 先写回测/回放脚本，按回测结果拍板；
   禁止用影子替代回测。
3. **多久能触发多少有效样本？** 必须写 ``min_samples`` 与 ``max_days``；
   到期样本不足 → 先改触发条件或做历史回放，不得以「再攒几天」续期。
4. **到期怎么办？** 必须写 ``expiry``；到期默认 ``retire``，续期必须写 ``waiver``
   （为什么历史数据仍不足、下一评估日）。

任何新增 ``*_SHADOW`` / ``MODE=shadow`` / ``REVIEW=shadow`` 都必须在此登记；
``tests/test_shadow_registry.py`` 会扫描 ``src/``、``main.py`` 与 ``.env.example``，未登记即失败。
扫描器覆盖：``os.getenv`` / ``os.environ.get`` 字面量键、默认值为 ``shadow`` 的读取、
``_holdings_mode`` 字面量键、``.env.example`` 中键名含 SHADOW / 属于 ``MODE_KEYS`` / 值为 ``shadow`` 的项。
变量键与自定义 mode helper 无法静态识别，仍须人工登记。

字段口径：
- ``kind``：``gate``（会改决策）/ ``trace``（永久观测）/ ``frozen``（被上游设计卡住）/
  ``dead``（已转正或删除）。
- ``data_basis``：``backtestable``（归档 + 行情可复算）/ ``replayable``（可回放，
  含 LLM 重放）/ ``forward_only``（盘中逐笔、五档、竞价、真实成交等无法历史复现）/
  ``n/a``。
- ``status``：``shadow`` / ``enabled`` / ``off`` / ``frozen`` / ``trace`` /
  ``retire_pending`` / ``needs_artifact`` / ``converted``。
- ``default`` 是代码缺省值；``status`` 是治理状态（生产 ``.env`` 可覆盖；
  例如 ``GOT_TRADABILITY_GATE`` default=off，但生产可设 shadow）。
- ``expiry``：gate 的 shadow / retire_pending / needs_artifact 必须是 ``YYYY-MM-DD``；
  frozen 条目可填 blocker ID（如 ``WI-P-7``），不参与日期检查；
  enabled / off 条目的 expiry 仅作复查提醒，不触发 ``--fail-on-expired``。
- 回滚键：用 ``env``；回滚值写在 ``note`` 或 ``default``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping


# 读取 shadow 语义的 mode 型开关（布尔型 ``*_SHADOW`` 由名称自动发现）。
MODE_KEYS = {
    "GOT_MOM_EXHAUST_MODE",
    "GOT_FAMILY_TOPPING_MODE",
    "GOT_FAMILY_TOPPING_EXCLUDE_MODE",
    "GOT_FAMILY_CLUSTER_REVIEW",
    "GOT_FAMILY_EMBED_FALLBACK",
    "GOT_TRADABILITY_GATE",
    "GOT_EXPOSURE_ENFORCE",
    "GOT_SIGNAL_SIZE",
    "GOT_AUDIT_ENABLED",
    "GOT_HOLDINGS_VERDICT_V2",
    "GOT_HOLDINGS_HISTORY_FEED",
    "GOT_NEWS_SHORTLIST_QUOTA",
    "GOT_THEME_LEDGER",
    # Node 2 连续相位模式（legacy|enabled，P2 前置底座；无 shadow 态）
    "GOT_NODE2_LIFECYCLE",
}

SHADOW_STATUSES = {"shadow", "retire_pending", "needs_artifact"}
KINDS = {"gate", "trace", "frozen", "dead"}
DATA_BASES = {"backtestable", "replayable", "forward_only", "n/a"}
STATUSES = {"shadow", "enabled", "off", "frozen", "trace", "retire_pending", "needs_artifact", "converted"}


@dataclass(frozen=True)
class ShadowRecord:
    env: str
    default: str
    kind: str
    data_basis: str
    status: str
    artifact: str = ""
    metric: str = ""
    min_samples: int | None = None
    max_days: int | None = None
    expiry: str = ""
    waiver: str = ""
    note: str = ""


SHADOW_REGISTRY: dict[str, ShadowRecord] = {
    # ── gate：会改决策，且历史可复算/可回放；存量 legacy 必须带 waiver ──
    "GOT_DISAGREEMENT_SHADOW": ShadowRecord(
        env="GOT_DISAGREEMENT_SHADOW",
        default="1",
        kind="gate",
        data_basis="replayable",
        status="shadow",
        artifact="conflict_summary_shadow + risk_override_shadow",
        metric="回放一致率 + 11 类冲突覆盖",
        min_samples=20,
        max_days=20,
        expiry="2026-11-30",
        waiver=(
            "legacy：回放 21/21 + 合成 9/9 已过；2026-11-04 负责人裁决："
            "保持 shadow 至 11-30，不注入 T3"
        ),
        note="先做 bounded A/B / 合成用例；到期按结果 enable 或 retire。",
    ),
    "GOT_THEME_LEDGER": ShadowRecord(
        env="GOT_THEME_LEDGER",
        default="off",
        kind="trace",
        data_basis="backtestable",
        status="trace",
        artifact="theme_ledger.json",
        metric="观察段/age、状态与输出桶可分、setup 变体可复算（非收益指标）",
        note=(
            "P1 账本只写观察产物（off 缺省）；影子期零决策改动。"
            "P2 若把 observation/setup 接入路由，必须改登记为 gate 并补 min_samples/expiry。"
        ),
    ),
    "GOT_NODE2_LIFECYCLE": ShadowRecord(
        env="GOT_NODE2_LIFECYCLE",
        default="legacy",
        kind="gate",
        data_basis="backtestable",
        status="enabled",
        artifact="node2 sector.json / node2_sector_trends.lifecycle_* / "
                 "scripts/audit_node2_lifecycle_ab.py --json-out",
        metric="相位/年龄/路由/unresolved 单列（收益仍由 P4 验收）；"
               "launch 输出条数由 GOT_NODE2_LAUNCH_TOPN 控制",
        expiry="2026-12-05",
        note=(
            "P2 prerequisite / direct enabled / rollback legacy：本计划不设 Node2 shadow，"
            "也不存在生产 `ab` 模式（`ab` 仅离线回测脚本标签）。"
            "2026-11-05 离线 A/B 通过（33 个完整输入日、hard_break 一律 decline+exhausted）"
            "→ 代码侧转 enabled；生产生效=`.env` 置 GOT_NODE2_LIFECYCLE=enabled`，"
            "回滚=改回 legacy（缺省值即 legacy）。"
            "**偏离已批准计划**：计划要求 launch 全量入道、`in_output=True` 是硬验收项；"
            "实际生产 `.env` 置 `GOT_NODE2_LAUNCH_TOPN=4`，按 launch_score 截断"
            "（证据：`scripts/audit_node2_launch_forward_returns.py` 全量 T+5 胜率 25.5%、"
            "前 4 条 42.9%、16 条外尾部 8.5%/均值 -4.18%）。因此 08-03 缓存重建里"
            "880710 排第 27、`in_output=False`，计划硬验收第 2 条**未满足**；"
            "`GOT_NODE2_LAUNCH_TOPN=0` 时四项全过。该偏离待裁决。"
        ),
    ),
    "GOT_MARKET_THEME_SHADOW": ShadowRecord(
        env="GOT_MARKET_THEME_SHADOW",
        default="1",
        kind="gate",
        data_basis="backtestable",
        status="enabled",
        artifact="market_theme_shadow",
        metric="题材阶段与主线的相关性验收",
        min_samples=20,
        max_days=20,
        expiry="2026-11-30",
        waiver=(
            "legacy：2026-11-04 验收：21 非空日 / 21 status=ok / 真值违约 0（硬门槛 PASS）；"
            "top5 strength 88.8 vs 全市场 50.2、同日相关 +0.35、同日主线重合 39.8%、"
            "次日 20.4%、噪声 6.7%"
        ),
        note=(
            "2026-11-04 负责人裁决 enable 试运行；ECS GOT_MARKET_THEME_ENABLED=1，"
            "shadow 产物继续留痕；回滚=0。下个 --got 核对 market_theme 真值非空。"
        ),
    ),
    "AGENT_STOCK_REVIEW_SHADOW": ShadowRecord(
        env="AGENT_STOCK_REVIEW_SHADOW",
        default="true",
        kind="gate",
        data_basis="backtestable",
        status="retire_pending",
        artifact="shadow_t1.json",
        metric="reject T+3/T+5 vs keep-side 同窗收益",
        min_samples=50,
        max_days=30,
        expiry="2026-12-15",
        waiver=(
            "legacy：2026-11-04 reject T+5 n=23 均值 -2.46%（69.6% 下跌），"
            "同窗 keep-side 推荐票 T+5 n=92 均值 -4.59%（存活 26.1%）→ rejects 反而好 +2.13pp；"
            "T+3 亦不占优；raw precision 未控 beta"
        ),
        note=(
            "T1 veto 无增量证据：被拒票好于被推荐票，不得 enable。"
            "保留 shadow 仅日志；候选 retire，生产 AGENT_STOCK_REVIEW_SHADOW 未改。"
        ),
    ),
    "AGENT_HOLDING_REVIEW_SHADOW": ShadowRecord(
        env="AGENT_HOLDING_REVIEW_SHADOW",
        default="true",
        kind="gate",
        data_basis="replayable",
        status="shadow",
        artifact="shadow_t1h.json",
        metric="reduce/exit 的 T+3 前向精度",
        min_samples=30,
        max_days=40,
        expiry="2026-12-31",
        waiver="legacy：26 天仅 1 次 reduce，触发条件过窄；先改触发/历史持仓回放",
        note="继续按天挂影子攒不到样本。",
    ),
    "NARRATIVE_CRITIC_SHADOW": ShadowRecord(
        env="NARRATIVE_CRITIC_SHADOW",
        default="true",
        kind="gate",
        data_basis="replayable",
        status="shadow",
        artifact="shadow_t2.json",
        metric="drop 事件的误杀率 / 保留事件的兑现率",
        min_samples=30,
        max_days=20,
        expiry="2026-11-30",
        waiver=(
            "legacy：2026-11-04 补 shadow_t2.json（event_id/verdict/reason/checks）；"
            "尚无归档样本，误杀/兑现评估脚本待跑"
        ),
        note="artifact 已落；下个 Node 4 归档日产生首样本；无样本前不得转正。",
    ),
    "GOT_MOM_EXHAUST_MODE": ShadowRecord(
        env="GOT_MOM_EXHAUST_MODE",
        default="shadow",
        kind="gate",
        data_basis="backtestable",
        status="shadow",
        artifact="shadow_meg.json",
        metric="命中率 + 假阳性（T+5 超额）",
        min_samples=30,
        max_days=30,
        expiry="2026-12-15",
        waiver=(
            "legacy：回测 T+5 -2.54% 是**被拦标的**的前向收益（支持门的方向）；"
            "生产 16 日 62 只评估层2 命中 0，问题是触发过窄"
        ),
        note=(
            "2026-11-04 口径更正：不是「门亏钱」，是「门不触发」；负责人裁决保持 shadow，"
            "排期修触发路径/阈值后重跑，12-15 未修则 retire。"
        ),
    ),
    "GOT_FAMILY_TOPPING_MODE": ShadowRecord(
        env="GOT_FAMILY_TOPPING_MODE",
        default="shadow",
        kind="gate",
        data_basis="backtestable",
        status="shadow",
        artifact="shadow_fts.json",
        metric="T+10 超额 + 假阳性",
        min_samples=30,
        max_days=30,
        expiry="2026-12-15",
        waiver=(
            "legacy：回测 T+10 -7.38%（口径待复核：若为绝对收益则受弱市影响；"
            "被压制家族方向性支持）；生产 15 日 57 只评估仅 2 次 flagged，问题是触发过窄"
        ),
        note=(
            "2026-11-04 口径更正：方向有支持，但生产几乎不触发；负责人裁决保持 shadow，"
            "排期修触发/阈值后重跑，12-15 未修则 retire。"
        ),
    ),
    "GOT_FAMILY_TOPPING_EXCLUDE_MODE": ShadowRecord(
        env="GOT_FAMILY_TOPPING_EXCLUDE_MODE",
        default="shadow",
        kind="gate",
        data_basis="backtestable",
        status="enabled",
        artifact="shadow_fts.json.fxg",
        metric="veto vs 基线补位净收益",
        min_samples=20,
        max_days=20,
        expiry="2026-12-31",
        note="已切 veto；本项只登记语义，不再按 shadow 计时。",
    ),
    "GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW": ShadowRecord(
        env="GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW",
        default="0",
        kind="gate",
        data_basis="backtestable",
        status="off",
        artifact="fxg_baseline_scoring.json",
        metric="veto/基线族差",
        min_samples=20,
        max_days=20,
        expiry="2026-11-15",
        waiver="legacy：15 个交易日对照全为「—」，双跑未产可用差值",
        note=(
            "2026-11-04 负责人裁决停止双跑；ECS .env=0；下个 --got 核对不再产 "
            "fxg_baseline_scoring.json。FXG veto 本身保持，不随本项回滚。"
        ),
    ),
    "INTRADAY_GATE_SHADOW": ShadowRecord(
        env="INTRADAY_GATE_SHADOW",
        default="true",
        kind="gate",
        data_basis="forward_only",
        status="off",
        artifact="intraday/YYYYMMDD/{0926,1125,1445}/verdicts.json",
        metric="I1–I6 影子判据",
        min_samples=30,
        max_days=30,
        expiry="2026-12-31",
        note=(
            "父开关 INTRADAY_GATE_ENABLED=false（I-6 未注册调度/未补 F5）；"
            "父开关与调度就绪后再开 shadow，当前不计影子时钟。"
        ),
    ),
    "GOT_TRADABILITY_GATE": ShadowRecord(
        env="GOT_TRADABILITY_GATE",
        default="off",
        kind="gate",
        data_basis="forward_only",
        status="shadow",
        artifact="tradability_shadow",
        metric="checked sell 数 + blocked 精度 + quote_unavailable 率",
        min_samples=30,
        max_days=60,
        expiry="2027-01-31",
        note=(
            "依赖盘中五档/涨跌停/真实卖单；2026-11-04 补 checked_exits 分母，"
            "pending_exits 空≠无样本；仍需真实 blocked 事件才能算精度。"
        ),
    ),
    "GOT_REGIME_JUMP_SHADOW": ShadowRecord(
        env="GOT_REGIME_JUMP_SHADOW",
        default="0",
        kind="gate",
        data_basis="backtestable",
        status="off",
        artifact="shadow_regime_jump.json",
        metric="agree_conf / switch 数",
        min_samples=20,
        max_days=20,
        expiry="2026-11-15",
        waiver="legacy：10 日 agree 6/10、switch 7>3；57 日回测 agree_conf 49% vs base 74%",
        note=(
            "2026-11-04 负责人裁决停止；code default 0 + ECS .env=0；"
            "下个 --got 核对不再产 shadow_regime_jump.json。离线重调 λ/θ 或放弃。"
        ),
    ),

    # ── frozen：被上游设计卡住，不累计影子天数 ──
    # 注：mature_reduce_shadow 无独立 env 开关（仅随 Phase 2 计算），同归 WI-P-7；
    # 不在下方登记（扫描器只认真实 env key），避免制造假开关。
    "GOT_SELL_SOURCE_SHADOW": ShadowRecord(
        env="GOT_SELL_SOURCE_SHADOW",
        default="1",
        kind="frozen",
        data_basis="backtestable",
        status="frozen",
        artifact="sell_source_shadow",
        metric="超限减仓精度",
        expiry="WI-P-7",
        note="W3 多账户口径/真相源未决；WI-P-7 逐条判留/改/删，裁决前不得开 enabled。",
    ),
    "GOT_EXPOSURE_ENFORCE": ShadowRecord(
        env="GOT_EXPOSURE_ENFORCE",
        default="shadow",
        kind="frozen",
        data_basis="backtestable",
        status="frozen",
        artifact="exposure_enforce_shadow",
        metric="截断影响",
        expiry="WI-P-7",
        note="归 WI-P-7；不按影子天数计时。",
    ),
    "GOT_SIGNAL_SIZE": ShadowRecord(
        env="GOT_SIGNAL_SIZE",
        default="shadow",
        kind="frozen",
        data_basis="backtestable",
        status="frozen",
        artifact="signal_size_shadow",
        metric="等分 vs score^γ 仓位差",
        expiry="WI-P-7",
        note="conf_w/ECE 未校准；归 WI-P-7。",
    ),

    # ── trace：永久观测，不设转正时钟 ──
    "GOT_LANE_BUDGET_SHADOW": ShadowRecord(
        env="GOT_LANE_BUDGET_SHADOW",
        default="1",
        kind="trace",
        data_basis="backtestable",
        status="trace",
        artifact="lane_budget_split_shadow",
        metric="regime×lane 前向超额",
        note="D3 已 enabled；本字段是 trace，不是待转正影子。",
    ),
    "GOT_T3_ROUTING_SHADOW": ShadowRecord(
        env="GOT_T3_ROUTING_SHADOW",
        default="1",
        kind="trace",
        data_basis="backtestable",
        status="trace",
        artifact="t3_routing_shadow",
        metric="rule↔T3 不一致率",
        note="action_taken 恒 shadow_none；路由落地是另一实施项。",
    ),
    "GOT_XNODE_ROUTING_SHADOW": ShadowRecord(
        env="GOT_XNODE_ROUTING_SHADOW",
        default="1",
        kind="trace",
        data_basis="backtestable",
        status="trace",
        artifact="cross_node_signals_shadow",
        metric="routing_mode 语义一致率",
        note="零决策改动；不是 gate。",
    ),
    "GOT_AMBUSH_VALIDATION_SHADOW": ShadowRecord(
        env="GOT_AMBUSH_VALIDATION_SHADOW",
        default="1",
        kind="trace",
        data_basis="backtestable",
        status="trace",
        artifact="ambush_lane_validation",
        metric="rule-2 early 离开率 vs 对照组",
        note="三规则只产提案；回测已显示区分度仅 +4.1pp。",
    ),
    "GOT_AMBUSH_EXIT_GRACE_SHADOW": ShadowRecord(
        env="GOT_AMBUSH_EXIT_GRACE_SHADOW",
        default="1",
        kind="trace",
        data_basis="backtestable",
        status="trace",
        artifact="shadow_ambush_exit_grace.json",
        metric="grace_would_hold 精度",
        note="零决策改动；可离线回放。",
    ),
    "GOT_ATTENTION_SHADOW": ShadowRecord(
        env="GOT_ATTENTION_SHADOW",
        default="true",
        kind="trace",
        data_basis="forward_only",
        status="trace",
        artifact="shadow_attention.json",
        metric="platform_count 分布 / 多平台同题抽检",
        note="新热榜元数据只能前向积累；纯观测，无转正时钟。",
    ),
    "GOT_ANTIBIAS_SHADOW": ShadowRecord(
        env="GOT_ANTIBIAS_SHADOW",
        default="true",
        kind="trace",
        data_basis="backtestable",
        status="trace",
        artifact="shadow_antibias.json",
        metric="counter_thesis 非空率 / 人工抽样",
        note="纯观测；若无消费计划应关掉省 LLM 成本。",
    ),
    "GOT_AUDIT_ENABLED": ShadowRecord(
        env="GOT_AUDIT_ENABLED",
        default="shadow",
        kind="trace",
        data_basis="n/a",
        status="trace",
        artifact="data/got_audit/YYYYMMDD/",
        metric="audit status / findings",
        note="只读审计工具，不是决策 gate；建议按需跑，不每日跑。",
    ),
    "GOT_HOLDINGS_VERDICT_V2": ShadowRecord(
        env="GOT_HOLDINGS_VERDICT_V2",
        default="enabled",
        kind="gate",
        data_basis="backtestable",
        status="enabled",
        artifact="holdings_verdict_shadow",
        metric="changed 行归因",
        expiry="2026-12-31",
        note="H2 已 enabled；shadow 是取证态，不是转正路径；对拍字段是 trace。",
    ),
    "GOT_HOLDINGS_HISTORY_FEED": ShadowRecord(
        env="GOT_HOLDINGS_HISTORY_FEED",
        default="enabled",
        kind="gate",
        data_basis="backtestable",
        status="enabled",
        artifact="holdings_history_shadow",
        metric="catalyst_status 一致率",
        expiry="2026-12-31",
        note="H3 已 enabled；shadow 是取证态；对拍字段是 trace。",
    ),
    "GOT_FAMILY_CLUSTER_REVIEW": ShadowRecord(
        env="GOT_FAMILY_CLUSTER_REVIEW",
        default="off",
        kind="gate",
        data_basis="backtestable",
        status="off",
        artifact="family_cluster_shadow",
        metric="纯度 / 覆盖 / 跨日 ID",
        expiry="2026-11-30",
        note="19 日回测已结案；live/off 是规则开关，不是影子期。",
    ),
    "GOT_FAMILY_EMBED_FALLBACK": ShadowRecord(
        env="GOT_FAMILY_EMBED_FALLBACK",
        default="off",
        kind="gate",
        data_basis="backtestable",
        status="off",
        artifact="family_embed_shadow",
        metric="其他回收率 / 纯度",
        expiry="2026-12-31",
        note="可用归档『其他』板块离线重算 embedding，不应按 5 日 shadow 等。",
    ),
    "GOT_NEWS_SHORTLIST_QUOTA": ShadowRecord(
        env="GOT_NEWS_SHORTLIST_QUOTA",
        default="false",
        kind="gate",
        data_basis="backtestable",
        status="off",
        artifact="shadow_shortlist_quota.json",
        metric="category_dist / 单类目超额",
        expiry="2026-12-31",
        note="归档短名单已有类目分布，可一次性 A/B；不是 gate 影子。",
    ),

    # ── dead：已转正/删除，保留仅防复活 ──
    "AGENT_SEMANTIC_GLUE_SHADOW": ShadowRecord(
        env="AGENT_SEMANTIC_GLUE_SHADOW",
        default="false",
        kind="dead",
        data_basis="n/a",
        status="converted",
        note=(
            "A6 已转正；SHADOW 只影响日志，置 false 不会回退；回退只能 git revert。"
            "存量名称保留（legacy 不强制改名），语义以登记表 dead 为准；新增 trace 不得再叫 shadow。"
        ),
    ),
    "SECTOR_ALIGN_SHADOW": ShadowRecord(
        env="SECTOR_ALIGN_SHADOW",
        default="false",
        kind="dead",
        data_basis="n/a",
        status="converted",
        note=(
            "T5 已转正，代码无读取点；仅存于 .env(.example) 作为历史回滚档案，"
            "docs/got/flowchart.md 仍记载其回滚语义。不删除，避免破坏回滚文档链；"
            "存量名称保留（legacy 不强制改名），新增 trace 不得再叫 shadow。"
        ),
    ),
}


def discover_shadow_keys(root: Path | None = None) -> set[str]:
    """扫描代码与 .env.example，返回所有 shadow 语义的 env key。"""
    root = root or Path(__file__).resolve().parents[2]
    keys: set[str] = set()

    read_re = re.compile(r"os\.(?:environ\.get|getenv)\(\s*[\"']([A-Z0-9_]+)[\"']")
    helper_re = re.compile(r"_holdings_mode\(\s*[\"']([A-Z0-9_]+)[\"']")
    shadow_default_re = re.compile(
        r"os\.(?:environ\.get|getenv)\(\s*[\"']([A-Z0-9_]+)[\"']\s*,\s*[\"']shadow[\"']"
    )
    for pattern in ("src/**/*.py", "main.py"):
        for path in root.glob(pattern):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            matches = (
                list(read_re.finditer(text))
                + list(helper_re.finditer(text))
                + list(shadow_default_re.finditer(text))
            )
            for match in matches:
                key = match.group(1)
                if "SHADOW" in key or key in MODE_KEYS:
                    keys.add(key)

    env_example = root / ".env.example"
    if env_example.exists():
        env_re = re.compile(r"^\s*#?\s*([A-Z0-9_]+)\s*=\s*([^\s#]*)")
        for line in env_example.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = env_re.match(line)
            if not match:
                continue
            key = match.group(1)
            value = match.group(2).strip().strip("\"'").lower()
            if "SHADOW" in key or key in MODE_KEYS or value == "shadow":
                keys.add(key)

    return keys


def validate_registry(registry: Mapping[str, ShadowRecord] | None = None) -> list[str]:
    """返回结构性错误；空列表表示通过。到期检查用 ``expired_gates()``（CLI 见 checker）。"""
    registry = registry or SHADOW_REGISTRY
    errors: list[str] = []
    seen: set[str] = set()

    for key, rec in registry.items():
        if key != rec.env:
            errors.append(f"{key}: 键名与 env 不一致 ({rec.env})")
        if key in seen:
            errors.append(f"{key}: 重复登记")
        seen.add(key)
        if not rec.default:
            errors.append(f"{key}: 缺少 default")
        if rec.kind not in KINDS:
            errors.append(f"{key}: 非法 kind={rec.kind}")
        if rec.data_basis not in DATA_BASES:
            errors.append(f"{key}: 非法 data_basis={rec.data_basis}")
        if rec.status not in STATUSES:
            errors.append(f"{key}: 非法 status={rec.status}")

        expected_status = {"trace": "trace", "frozen": "frozen", "dead": "converted"}.get(rec.kind)
        if expected_status and rec.status != expected_status:
            errors.append(
                f"{key}: kind={rec.kind} 只能对应 status={expected_status}，实际 {rec.status}"
            )
        if rec.kind == "gate" and rec.status in {"trace", "frozen", "converted"}:
            errors.append(f"{key}: gate 不得使用 status={rec.status}")

        if rec.kind == "gate":
            if not rec.artifact:
                errors.append(f"{key}: gate 必须登记 artifact")
            if not rec.metric:
                errors.append(f"{key}: gate 必须登记 metric")
            if rec.status in SHADOW_STATUSES:
                if rec.min_samples is None or rec.min_samples <= 0:
                    errors.append(f"{key}: shadow gate 必须登记 min_samples")
                if rec.max_days is None or rec.max_days <= 0:
                    errors.append(f"{key}: shadow gate 必须登记 max_days")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rec.expiry or ""):
                    errors.append(f"{key}: shadow gate 必须登记 YYYY-MM-DD expiry")
                if rec.data_basis in {"backtestable", "replayable"} and not rec.waiver:
                    errors.append(
                        f"{key}: 可回测/可回放的 gate 不得新开 shadow；"
                        "存量 legacy 必须写 waiver 与到期日"
                    )

    return errors


def expired_gates(
    registry: Mapping[str, ShadowRecord] | None = None,
    as_of: date | None = None,
) -> list[tuple[str, str]]:
    """返回已到期的 gate（key, expiry）；``as_of`` 缺省为今天。

    只检查 ``kind=gate`` 且 ``status`` 属于 shadow / retire_pending / needs_artifact 的条目；
    enabled / off / frozen / trace / converted 不参与影子到期。``expiry`` 非日期（如 WI-P-7）
    的条目跳过。
    """
    registry = registry or SHADOW_REGISTRY
    today = as_of or date.today()
    expired: list[tuple[str, str]] = []
    for key, rec in registry.items():
        if rec.kind != "gate" or rec.status not in SHADOW_STATUSES:
            continue
        try:
            expiry = date.fromisoformat(rec.expiry)
        except ValueError:
            continue
        if expiry <= today:
            expired.append((key, rec.expiry))
    return expired
