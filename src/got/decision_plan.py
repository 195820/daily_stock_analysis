# -*- coding: utf-8 -*-
"""
决策编排层 v0：交易计划装配与对账（C2，2026-10-15）。

THESIS_STATE_MACHINE: pending step-0
W3-pending: reconcile 对账口径当前只实现单账户（accounts[0]）口径，多账户合并口径待 Step 0 B1 裁决后重列。

职责：
1. assemble: Phase 2 run() 末尾调用，将 allocation 映射为 TradePlanRecord 写入 trade_plans 表
2. reconcile: Phase 2 run() 开头调用，对昨日 pending 计划回填执行状态

装配点：portfolio_decision.py run() 末尾（allocation 组装完成后、archive_phase2 前）调 assemble；
        run() 开头调 reconcile 回填昨日状态。两处均 try/except 包裹，失败只 logger.warning 不阻断主流程。
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

from src.got.schemas import PortfolioDecision, UnifiedContext
from src.storage import TradePlanRecord

logger = logging.getLogger(__name__)


def _add_trading_days(start_date: date, trading_days: int, market: str = "cn") -> date:
    """
    从 start_date 开始偏移 trading_days 个交易日（不含 start_date 本身）。
    
    使用 trading_calendar.is_market_open 逐日 walk，跳过非交易日。
    若 is_market_open 失败（fail-open 返回 True），按自然日偏移兜底。
    
    Args:
        start_date: 起始日期
        trading_days: 偏移交易日数（正数）
        market: 市场代码，默认 'cn'（A 股）
    
    Returns:
        偏移后的日期
    """
    if trading_days <= 0:
        return start_date
    
    try:
        from src.core.trading_calendar import is_market_open
    except ImportError:
        logger.warning("trading_calendar 导入失败，降级为自然日偏移")
        return start_date + timedelta(days=trading_days)
    
    current = start_date
    added = 0
    # 安全阀：最多 walk 30 个自然日（防止无限循环）
    max_walk = 30
    walked = 0
    
    while added < trading_days and walked < max_walk:
        current += timedelta(days=1)
        walked += 1
        # is_market_open fail-open 返回 True（交易日），False 为休市
        if is_market_open(market, current):
            added += 1
    
    if walked >= max_walk:
        logger.warning(f"交易日偏移 walk 达到上限 {max_walk} 日，降级为自然日偏移")
        return start_date + timedelta(days=trading_days)
    
    return current


def _determine_lane(action: str, is_ambush_entry: bool) -> str:
    """
    根据 action 和 is_ambush_entry 判定 lane。
    
    规则：
    - action 以 sell/reduce 开头（大小写不敏感）→ 'holding_reduce'
    - 否则 is_ambush_entry=True → 'ambush'
    - 否则 → 'mainline'
    
    Args:
        action: 交易动作（buy/sell/hold/reduce 等）
        is_ambush_entry: 是否为埋伏仓买入
    
    Returns:
        lane 标识: 'mainline' / 'ambush' / 'holding_reduce'
    """
    action_lower = action.lower()
    if action_lower.startswith("sell") or action_lower.startswith("reduce"):
        return "holding_reduce"
    elif is_ambush_entry:
        return "ambush"
    else:
        return "mainline"


def _find_composite_score(unified_scoring: list, symbol: str) -> Optional[float]:
    """
    在 unified_scoring 中按 symbol 匹配 composite_score。
    
    Args:
        unified_scoring: UnifiedScoringResult 列表
        symbol: 股票代码
    
    Returns:
        composite_score（找不到返回 None）
    """
    for scoring in unified_scoring:
        if scoring.symbol == symbol:
            return scoring.composite_score
    return None


def assemble(
    decision: PortfolioDecision,
    unified: UnifiedContext,
    today: date,
    session=None,
) -> list[TradePlanRecord]:
    """
    装配交易计划：从 decision.allocation 逐条映射为 TradePlanRecord 写入 trade_plans 表。
    
    映射规则：
    - lane ← action 以 sell/reduce 开头 → 'holding_reduce'；否则 is_ambush_entry → 'ambush'；否则 'mainline'
    - trigger_price ← price_range[0]
    - target_quantity / target_value ← 同名字段
    - source_composite ← unified_scoring 中该 symbol 的 composite_score（找不到 → None）
    - valid_until ← today + 3 交易日（trading_calendar 偏移）
    - tranches ← None（本版不分批）
    
    UPSERT 语义：(plan_date, symbol, action) 已存在 → 更新字段；不存在 → 插入。
    全程 try/except，异常 logger.warning 后返回已成功部分，不抛出。
    
    Args:
        decision: PortfolioDecision（Phase 2 输出）
        unified: UnifiedContext（聚合节点输出，用于查 composite_score）
        today: 计划日期（通常为 date.today()）
        session: SQLAlchemy Session（可选；为 None 时自建，用完提交关闭）
    
    Returns:
        成功写入的 TradePlanRecord 列表
    """
    written = []
    own_session = session is None
    
    try:
        if own_session:
            from src.storage import DatabaseManager
            db = DatabaseManager()
            session = db.get_session()
            # 返回值在 finally close() 之后才被接线层读取（assemble_log_lines）；
            # sessionmaker 缺省 expire_on_commit=True 会在 commit 时过期全部属性，
            # 关闭后读取即 DetachedInstanceError → 成功装配被误报成「装配失败」。
            # 自建 session 关闭后不再复用，仅要求属性可读，故关掉自动过期；
            # 外部传入 session 的生命周期归调用方，不在此改动。
            session.expire_on_commit = False
        
        # 预计算 valid_until（today + 3 交易日）
        valid_until = _add_trading_days(today, 3)
        
        for alloc in decision.allocation:
            try:
                # 判定 lane
                lane = _determine_lane(alloc.action, alloc.is_ambush_entry)
                
                # 提取 trigger_price
                trigger_price = alloc.price_range[0] if alloc.price_range else None
                
                # 查找 source_composite
                source_composite = _find_composite_score(decision.unified_scoring, alloc.symbol)
                
                # UPSERT：查 (plan_date, symbol, action) 是否已存在
                existing = session.query(TradePlanRecord).filter(
                    TradePlanRecord.plan_date == today,
                    TradePlanRecord.symbol == alloc.symbol,
                    TradePlanRecord.action == alloc.action,
                ).first()
                
                if existing:
                    # 同日重跑：更新字段（UPSERT 语义）
                    existing.name = alloc.name
                    existing.lane = lane
                    existing.target_quantity = float(alloc.target_quantity)
                    existing.target_value = float(alloc.target_value)
                    existing.trigger_price = trigger_price
                    existing.valid_until = valid_until
                    existing.source_composite = source_composite
                    # blocked_reason 保留原值（GATE-1 拦截原因不应被覆盖）
                    logger.info(f"交易计划已更新（同日重跑）: {alloc.symbol} {alloc.action} @ {today}")
                    written.append(existing)
                else:
                    # 新插入
                    record = TradePlanRecord(
                        plan_date=today,
                        symbol=alloc.symbol,
                        name=alloc.name,
                        lane=lane,
                        action=alloc.action,
                        target_quantity=float(alloc.target_quantity),
                        target_value=float(alloc.target_value),
                        target_weight=None,  # 本版不计算权重
                        trigger_price=trigger_price,
                        valid_until=valid_until,
                        tranches=None,  # 本版不分批
                        execution_status='pending',
                        source_composite=source_composite,
                        blocked_reason=alloc.blocked_reason or '',
                    )
                    session.add(record)
                    written.append(record)
                    logger.info(f"交易计划已创建: {alloc.symbol} {alloc.action} @ {today}")
            
            except Exception as e:
                logger.warning(f"装配交易计划失败（跳过）: {alloc.symbol} {alloc.action} - {e}")
                continue
        
        if own_session:
            session.commit()
            logger.info(f"交易计划装配完成: {len(written)} 条 @ {today}")
    
    except Exception as e:
        logger.warning(f"交易计划装配整体失败: {e}")
        if own_session and session:
            try:
                session.rollback()
            except Exception:
                pass
    
    finally:
        if own_session and session:
            try:
                session.close()
            except Exception:
                pass
    
    return written


def _close_buy_plan_anchors(session, symbols: set[str], as_of: date) -> tuple[int, int]:
    """买入计划 cancelled 时关闭对应 open thesis / ambush 快照。

    根因（2026-11-02 I-4）：reconcile 只改 trade_plans.execution_status，计划取消后
    `holding_thesis_snapshots` / `ambush_position_snapshots` 仍 open，污染下一轮持仓
    回查与 P4 判断（002741/002093 只有 cancelled buy plan、无持仓，却仍被当 open）。
    仅关闭「买入计划取消且快照无持仓」的锚；partial/executed 不在此列。
    """
    if not symbols:
        return 0, 0
    from src.storage import AmbushPositionSnapshotRecord, HoldingThesisRecord

    closed_thesis = (
        session.query(HoldingThesisRecord)
        .filter(
            HoldingThesisRecord.symbol.in_(symbols),
            HoldingThesisRecord.is_open == True,  # noqa: E712
        )
        .update(
            {"is_open": False, "lifecycle_state": "closed"},
            synchronize_session=False,
        )
    )
    closed_ambush = (
        session.query(AmbushPositionSnapshotRecord)
        .filter(
            AmbushPositionSnapshotRecord.symbol.in_(symbols),
            AmbushPositionSnapshotRecord.is_open == True,  # noqa: E712
        )
        .update(
            {
                "is_open": False,
                "exit_signal": "plan_cancelled",
                "exit_date": as_of,
            },
            synchronize_session=False,
        )
    )
    if closed_thesis or closed_ambush:
        logger.info(
            "买入计划取消 → 关闭锚: thesis %d 条 / ambush %d 条（%s）",
            closed_thesis, closed_ambush, ", ".join(sorted(symbols)),
        )
    return closed_thesis, closed_ambush


def reconcile(
    yesterday: date,
    holdings_snapshot: dict,
    session=None,
) -> list[TradePlanRecord]:
    """
    对账昨日交易计划：读 plan_date=yesterday 且 execution_status='pending' 的行，
    与当日持仓快照比对，回填执行状态。
    
    对账规则（单账户口径，W3-pending：多账户合并口径待 Step 0 B1 裁决后重列）：
    - buy 计划（数量语义 = 期望持仓量）：票在快照且数量 >= target_quantity*0.5 → 'executed'；
      票在但 < 半仓 → 'partial'；票不在 → 'cancelled'
    - sell/reduce 计划（holding_reduce 道，数量语义 = 期望减掉的量）：票已从快照消失 →
      'executed'（卖光即全额执行）；票仍在但数量 <= 昨日持仓 - target*0.5 → 'partial'；
      票在且几乎未减 → 'cancelled'（未执行卖出）
    - valid_until < today 且仍未成交（仍为 pending）→ 'expired'

    注意卖出计划的数量语义与买入相反：持仓减少才是执行方向。sell 计划的 partial/cancelled
    判定基于「减掉量」而非「剩余量」；仓内持仓快照无昨日基线时（holdings_snapshot 只有当日），
    sell 类只区分 executed（消失）/ cancelled（仍在）两态，不强行判 partial（缺基线数据，
    硬猜会引入假读数——Step 0 B1 裁决后若有昨日快照再补 partial 分支）。
    
    状态写回 + updated_at 由 ORM onupdate 承担。
    全程 try/except，异常 logger.warning 后返回已成功部分，不抛出。
    
    Args:
        yesterday: 昨日日期（对账目标）
        holdings_snapshot: 当日持仓快照 dict，symbol → 数量（股）
        session: SQLAlchemy Session（可选；为 None 时自建，用完提交关闭）
    
    Returns:
        状态已更新的 TradePlanRecord 列表
    """
    updated = []
    cancelled_buy_symbols: set[str] = set()
    own_session = session is None
    today = date.today()
    
    try:
        if own_session:
            from src.storage import DatabaseManager
            db = DatabaseManager()
            session = db.get_session()
            # 同 assemble：返回值在 close() 后由 reconcile_log_lines 读取，关掉 commit 过期，
            # 否则对账成功也会被日志层 DetachedInstanceError 误报成「对账失败」。
            session.expire_on_commit = False
        
        # 读昨日 pending 计划
        pending_plans = session.query(TradePlanRecord).filter(
            TradePlanRecord.plan_date == yesterday,
            TradePlanRecord.execution_status == 'pending',
        ).all()
        
        for plan in pending_plans:
            try:
                # 先检查是否过期（valid_until < today 且仍为 pending）
                if plan.valid_until and plan.valid_until < today:
                    plan.execution_status = 'expired'
                    updated.append(plan)
                    logger.info(f"交易计划已过期: {plan.symbol} {plan.action} @ {yesterday}")
                    continue
                
                # 比对持仓快照
                current_quantity = holdings_snapshot.get(plan.symbol, 0)
                
                _is_sell = (plan.lane == 'holding_reduce') or (plan.action or '').lower().startswith(('sell', 'reduce'))

                if current_quantity == 0:
                    # 票消失：buy 道 = 未成交取消；sell/reduce 道 = 卖光全执行（语义反转修复）
                    if _is_sell:
                        plan.execution_status = 'executed'
                        logger.info(f"卖出计划已执行（票消失）: {plan.symbol} {plan.action} @ {yesterday}")
                    else:
                        plan.execution_status = 'cancelled'
                        cancelled_buy_symbols.add(plan.symbol)
                        logger.info(f"买入计划已取消（票消失）: {plan.symbol} {plan.action} @ {yesterday}")
                elif _is_sell:
                    # 卖出计划票仍在：缺昨日基线无法算减掉量，只判 cancelled（见 docstring）
                    plan.execution_status = 'cancelled'
                    logger.info(f"卖出计划未执行（票仍在）: {plan.symbol} {plan.action} @ {yesterday}")
                else:
                    # 买入计划票在快照
                    target_qty = plan.target_quantity or 0
                    half_target = target_qty * 0.5

                    if current_quantity >= half_target:
                        plan.execution_status = 'executed'
                        logger.info(f"买入计划已执行: {plan.symbol} {plan.action} @ {yesterday}")
                    else:
                        plan.execution_status = 'partial'
                        logger.info(f"买入计划部分执行: {plan.symbol} {plan.action} @ {yesterday}")
                
                updated.append(plan)
            
            except Exception as e:
                logger.warning(f"对账交易计划失败（跳过）: {plan.symbol} {plan.action} @ {yesterday} - {e}")
                continue
        
        if cancelled_buy_symbols:
            try:
                _close_buy_plan_anchors(session, cancelled_buy_symbols, today)
            except Exception as e:
                logger.warning(f"买入计划取消后关闭锚失败（不阻断对账）: {e}")

        if own_session:
            session.commit()
            logger.info(f"交易计划对账完成: {len(updated)} 条 @ {yesterday}")
    
    except Exception as e:
        logger.warning(f"交易计划对账整体失败: {e}")
        if own_session and session:
            try:
                session.rollback()
            except Exception:
                pass
    
    finally:
        if own_session and session:
            try:
                session.close()
            except Exception:
                pass
    
    return updated


# =============================================================================
# 人读日志行（纯函数，2026-10-20）
# =============================================================================
# run() 的 reconcile/assemble 接线处把返回值转成人读行，透传进
# PortfolioDecision.trade_plan_log → reasoning.md Node 7 段的「决策编排层」子块。
# 纯函数：无 IO、无副作用、可单测；异常路径也产一行（失败要可见，但不阻断主流程）。

def reconcile_log_lines(
    day: date, updated: list, error: Optional[str] = None
) -> list[str]:
    """昨日对账回填结果 → reasoning.md 人读行。

    Args:
        day: 对账目标日（昨日交易日）
        updated: reconcile 返回的状态已更新记录列表（空列表=无 pending 计划）
        error: 接线层捕获的异常字符串（None=未失败）
    """
    if error:
        return [f"昨日对账({day})失败（不阻断）: {error}"]
    if not updated:
        return [f"昨日对账({day}): 无 pending 计划待回填"]
    lines = [f"昨日对账({day}): {len(updated)} 条状态回填"]
    lines.extend(
        f"- [{getattr(p, 'lane', '') or '-'}] {getattr(p, 'symbol', '')} "
        f"{getattr(p, 'action', '')} → {getattr(p, 'execution_status', '')}"
        for p in updated
    )
    return lines


def assemble_log_lines(written: list, error: Optional[str] = None) -> list[str]:
    """当日计划装配结果 → reasoning.md 人读行。

    Args:
        written: assemble 返回的成功写入记录列表（空列表=allocation 为空或无写入）
        error: 接线层捕获的异常字符串（None=未失败）
    """
    if error:
        return [f"当日计划装配失败（不阻断）: {error}"]
    if not written:
        return ["当日计划装配: allocation 为空，无计划写入"]
    lines = [f"当日计划装配: {len(written)} 条写入 trade_plans"]
    for p in written:
        _qty = float(getattr(p, "target_quantity", 0) or 0)
        _trigger = getattr(p, "trigger_price", None)
        _trigger_txt = f"¥{float(_trigger):.2f}" if _trigger is not None else "—"
        _valid_until = getattr(p, "valid_until", None)
        lines.append(
            f"- [{getattr(p, 'lane', '') or '-'}] {getattr(p, 'symbol', '')} "
            f"{getattr(p, 'action', '')} {_qty:.0f}股 @触发 {_trigger_txt} → "
            f"{getattr(p, 'execution_status', '')}（有效至 {_valid_until or '—'}）"
        )
    return lines


# =============================================================================
# 持仓裁决（H2，2026-10-27 持仓模块三原则接线）
# =============================================================================
# 原则①：持仓的分析是决策编排的一部分。
# `portfolio_decision._score_holdings` 的 8 分支 action 判定链搬到这里——
# **搬家不重写**：分支顺序、阈值、宽限语义逐条对齐（原 :961-1081）。
# Node 7 只负责「算分 + 供事实（IO/写库仍在节点内）」，判定本身是纯函数：
# 无 IO、无副作用、可表驱动单测。
#
# 影子纪律（GOT_HOLDINGS_VERDICT_V2，三态）：
#   off     → guard 关闭，不产对拍产物（回滚路径）
#   shadow  → guard 关闭（实际 action 与 off 等价），落 `holdings_verdict_shadow` 对拍
#   enabled → guard 生效（修正「confirmed 板块 + lifecycle 缺失 → 40/60 降温档」误判）
# legacy 行为 = 同一函数在 guard=False 下的输出（除 guard 外无任何语义改动）。

# 原因码 → 人读标签（归档/日志用；不改判定）
HOLDING_REASON_LABELS: dict[str, str] = {
    "no_sector": "无板块归属且无家族宽限",
    "no_sector_grace": "无板块归属但家族强（宽限持有）",
    "thesis_drift": "论点漂移且无宽限",
    "thesis_drift_grace": "论点漂移但家族仍强（宽限）",
    "lifecycle_none_weak": "板块不在追踪名单且强度<40（深破位）",
    "lifecycle_none_cooling": "板块不在追踪名单且强度40-60（降温档减仓）",
    "lifecycle_none_strong": "板块不在追踪名单但强度≥60（观察）",
    "sector_confirmed": "板块已被确认为 actionable（守卫：不落降温档）",
    "strength_lt50": "板块强度<50",
    "cooling_hold": "高位降温板块（不强制卖出）",
    "role_other": "非主线/埋伏角色且无宽限",
    "role_other_grace": "非主线/埋伏角色但家族强（宽限）",
    "p4_exit": "埋伏退出信号",
    "p4_hold": "埋伏持有（无退出信号）",
    "stop_loss": "触发止损线",
    "lifecycle_signal": "生命周期驱动",
}

# thesis 催化剂锚「已到期/已失效」状态（H3 enabled 时的提前退出判据）
_CATALYST_ELAPSED_STATES = ("elapsed", "expired", "cancelled")


@dataclass
class HoldingAssessmentFacts:
    """持仓裁决的输入事实（Node 7 采集，纯数据）。

    设计约束：判定所需的一切都在这里；需要 IO/写库的两件事（家族宽限、
    P4 退出信号）以「已求值结果 / 懒求值回调」传入，保证 assess_holding 本身纯净。
    """
    symbol: str = ""
    # ── 板块事实 ──
    sector_name: str = ""
    sector_strength: float = 50.0
    sector_role: str = ""
    sector_confirmed: bool = False       # 见 assess_holding：选股通道 actionable 或持仓补入 board_state 证据
    board_state: Optional[str] = None    # Node 2 状态机原始态（holding 通道的守卫证据，审计用）
    lifecycle_phase: Optional[str] = None
    lifecycle_day: Optional[int] = None
    overheat_severity: Optional[float] = None
    # ── 论点/漂移事实 ──
    thesis_drift: bool = False
    thesis_family: str = ""
    all_belong_boards: list[str] = field(default_factory=list)
    # ── 懒求值：家族宽限（Node 7 内部会写 drift_watch，故按分支调用，不预求值）──
    grace_resolver: Optional[Callable[[str], tuple[str, bool]]] = None
    # ── P4 埋伏退出（Node 7 用 snapshot 求得；None=无快照）──
    ambush_exit: Optional[dict] = None    # {"signal","reason","category_tag"}；有 open snapshot 即求值，不要求当前 sector_role=="ambush"
    # ── 生命周期驱动信号（纯计算，Node 7 预算；仅在 fallback 分支消费）──
    lifecycle_action: str = ""
    lifecycle_reason: str = ""
    # ── 历史回查（H3，GOT_HOLDINGS_HISTORY_FEED=enabled 时才参与分支）──
    history: Optional[dict] = None
    # ── 观测 ──
    pnl_pct: float = 0.0                 # 百分数（-5.31），仅展示/证据；勿与阈值直接比
    pnl_pct_decimal: float = 0.0         # 小数（-0.0531），与 stop_loss_threshold 同量纲
    stop_loss_threshold: Optional[float] = None  # 小数（-0.05）；None=不评估硬止损
    capital_data_missing: Optional[bool] = None


@dataclass
class HoldingVerdict:
    """持仓裁决结果（结构化，替代散落的 action 赋值与 reasoning 拼接）。"""
    action: str
    reason_code: str
    reason_text: str = ""              # 直接拼进 reasoning 的后缀（含前导 ", "）
    grace_applied: bool = False
    thesis_broken: bool = False        # 调用方据此 _mark_thesis_broken
    sector_confirmed_guard: bool = False
    evidence: dict = field(default_factory=dict)

    @property
    def reason_label(self) -> str:
        return HOLDING_REASON_LABELS.get(self.reason_code, self.reason_code)


def _call_grace(facts: HoldingAssessmentFacts, drift_sector: str) -> tuple[str, bool]:
    if facts.grace_resolver is None:
        return "sell", False
    return facts.grace_resolver(drift_sector)


def assess_holding(
    facts: HoldingAssessmentFacts,
    *,
    guard_enabled: bool = True,
    history_enabled: bool = False,
) -> HoldingVerdict:
    """持仓动作判定（单入口分支表：P4 快照退出最高优先级；guard/history 为可选前置分支）。

    guard_enabled:
        - True：confirmed 板块（actionable + mainline/ambush）+ lifecycle 缺失 → hold。
        - False：与搬迁前的生产行为逐条一致（`GOT_HOLDINGS_VERDICT_V2=off/shadow`）。
    history_enabled:
        - True（H3 `GOT_HOLDINGS_HISTORY_FEED=enabled`）：thesis 催化剂锚已到期/失效时，
          埋伏持仓不再要求 ambush snapshot 才判退出（双锚）。
        - False：历史只进 evidence（影子期纪律）。
    """
    strength = facts.sector_strength
    base_evidence = {
        "sector": facts.sector_name,
        "sector_role": facts.sector_role,
        "sector_confirmed": facts.sector_confirmed,
        "board_state": facts.board_state,
        "strength": round(float(strength or 0.0), 1),
        "lifecycle_phase": facts.lifecycle_phase,
        "lifecycle_day": facts.lifecycle_day,
        "thesis_drift": facts.thesis_drift,
        "capital_data_missing": facts.capital_data_missing,
    }
    if facts.history:
        # 影子期：历史只作证据（不参与任何分支判定），enabled 时 catalyst 锚另起一支
        base_evidence["history"] = {
            "thesis": bool((facts.history or {}).get("thesis")),
            "catalyst_status": ((facts.history or {}).get("catalyst") or {}).get("status"),
            "plan_executed_ratio": ((facts.history or {}).get("plan_stats") or {}).get("executed_ratio"),
            "ambush_snap": bool((facts.history or {}).get("ambush_snap")),
        }

    # ⓪ P4 快照退出（最高优先级，2026-11-02）
    # 快照是入场锚：只要 open snapshot 的 exit signal 非空，就以 P4 退出为准。
    # 旧链把它放在 thesis_drift 之后且要求 sector_role=="ambush"，导致 002009
    # 被当日 boards 判为 mainline 时 fade_sell 永不登记（KPI 级漏卖）。
    _p4_signal = (
        (facts.ambush_exit or {}).get("signal")
        if facts.ambush_exit is not None else None
    )
    if _p4_signal:
        return HoldingVerdict(
            action="sell", reason_code="p4_exit",
            reason_text=f", [埋伏退出:{_p4_signal}]",
            evidence={**base_evidence, "p4_signal": _p4_signal,
                      "p4_reason": (facts.ambush_exit or {}).get("reason") or "",
                      "p4_anchor": "snapshot"},
        )

    # ⓪′ 硬止损（P0-6，2026-11-04）：必须先于任何宽限/生命周期分支。
    # 旧链把硬止损藏在 ⑧ 消费的 lifecycle_action 里，分支②一旦命中「论点漂移宽限」
    # 就会吞掉 -5% 止损（10-29 002009：P&L -5.31%、阈值 -5%，输出 hold/宽限）。
    # P4 快照仍是最高优先级：它是入场锚的明确退出信号，且需要登记/关闭快照。
    if (
        facts.stop_loss_threshold is not None
        and facts.pnl_pct_decimal < facts.stop_loss_threshold
    ):
        return HoldingVerdict(
            action="sell", reason_code="stop_loss",
            reason_text=(
                f", [止损:{facts.stop_loss_threshold:.0%}, "
                f"P&L {facts.pnl_pct_decimal:.1%}]"
            ),
            evidence={
                **base_evidence,
                "stop_loss_threshold": facts.stop_loss_threshold,
                "pnl_pct_decimal": facts.pnl_pct_decimal,
            },
        )

    # ① 无板块归属 → 家族宽限（否则卖）
    if not facts.sector_name:
        drift_sector = facts.all_belong_boards[0] if facts.all_belong_boards else ""
        action, grace = _call_grace(facts, drift_sector)
        if grace:
            return HoldingVerdict(
                action=action, reason_code="no_sector_grace",
                reason_text=", [家族宽限]", grace_applied=True, evidence=base_evidence,
            )
        return HoldingVerdict(
            action="sell", reason_code="no_sector", thesis_broken=True, evidence=base_evidence,
        )

    # ② 论点漂移 → 家族宽限（否则卖 + 标记论点已破）
    if facts.thesis_drift:
        action, grace = _call_grace(facts, facts.sector_name)
        if grace:
            return HoldingVerdict(
                action=action, reason_code="thesis_drift_grace", grace_applied=True,
                reason_text=f", [论点漂移宽限: {facts.thesis_family}]", evidence=base_evidence,
            )
        return HoldingVerdict(
            action="sell", reason_code="thesis_drift", thesis_broken=True,
            reason_text=f", [论点漂移卖出: {facts.thesis_family}→{facts.sector_name}]",
            evidence=base_evidence,
        )

    # ③′ P4 thesis 事件锚（仅 ambush + H3 enabled；快照锚已在 ⓪ 提前返回）
    if facts.sector_role == "ambush":
        _thesis_status = (
            ((facts.history or {}).get("catalyst") or {}).get("status") if facts.history else None
        )
        if history_enabled and _thesis_status in _CATALYST_ELAPSED_STATES:
            return HoldingVerdict(
                action="sell", reason_code="p4_exit",
                reason_text=f", [埋伏退出:event_elapsed(thesis锚:{_thesis_status})]",
                evidence={**base_evidence, "p4_signal": "event_elapsed", "p4_anchor": "thesis"},
            )

    # ③ lifecycle 缺失（板块不在 confirmed/持仓补入情报里）
    if facts.lifecycle_phase is None:
        # H2 修正（guard）：板块已被确认为 actionable 的持仓，不因 lifecycle 缺失落降温档
        if guard_enabled and facts.sector_confirmed:
            return HoldingVerdict(
                action="hold", reason_code="sector_confirmed", sector_confirmed_guard=True,
                reason_text=", [板块确认守卫: 不落降温档]", evidence=base_evidence,
            )
        if strength < 40:
            return HoldingVerdict(action="sell", reason_code="lifecycle_none_weak", evidence=base_evidence)
        if strength < 60:
            return HoldingVerdict(action="reduce", reason_code="lifecycle_none_cooling", evidence=base_evidence)
        return HoldingVerdict(action="hold", reason_code="lifecycle_none_strong", evidence=base_evidence)

    # ④ 强度破位
    if strength < 50:
        return HoldingVerdict(action="sell", reason_code="strength_lt50", evidence=base_evidence)

    # ⑤ 高位降温（前主线回调未破位）：持仓者 hold，不 force-sell
    if facts.sector_role == "cooling":
        return HoldingVerdict(action="hold", reason_code="cooling_hold", evidence=base_evidence)

    # ⑥ 非主线/埋伏角色 → 家族宽限（否则卖）
    if facts.sector_role not in ("mainline", "ambush"):
        drift_sector = facts.all_belong_boards[0] if facts.all_belong_boards else ""
        action, grace = _call_grace(facts, drift_sector)
        if grace:
            return HoldingVerdict(
                action=action, reason_code="role_other_grace", grace_applied=True,
                reason_text=", [家族宽限]", evidence=base_evidence,
            )
        return HoldingVerdict(
            action="sell", reason_code="role_other", thesis_broken=True, evidence=base_evidence,
        )

    # ⑦ 埋伏持仓（P4 无退出信号）→ 持有
    # 触发退出的情形已在 ③′ 提前返回；到这里只剩「有快照但无信号」的埋伏仓保持持有。
    if facts.sector_role == "ambush" and facts.ambush_exit is not None:
        return HoldingVerdict(
            action="hold", reason_code="p4_hold",
            reason_text=f", [埋伏持有:{facts.ambush_exit.get('category_tag', '')}]",
            evidence={**base_evidence, "p4_anchor": "snapshot"},
        )

    # ⑧ 生命周期驱动（吃鱼身 + 高切低）
    action = facts.lifecycle_action or "hold"
    return HoldingVerdict(
        action=action, reason_code="lifecycle_signal",
        evidence={**base_evidence, "lifecycle_reason": facts.lifecycle_reason},
    )


def build_verdict_shadow(
    rows: list[dict],
    *,
    mode: str,
    holdings_seen: int,
) -> dict:
    """H2 对拍产物：逐票 legacy vs v2(guard) 的 action 差异（只留痕，不参与判定）。"""
    changed = [r for r in rows if r.get("changed")]
    return {
        "mode": mode,
        "holdings_seen": holdings_seen,
        "changed_count": len(changed),
        "changed_symbols": [r.get("symbol") for r in changed],
        "rows": rows,
    }


# =============================================================================
# 历史决策回查（H3，2026-10-27）
# =============================================================================
# 原则③：买入时的历史决策数据必须为当前决策提供依据。
# 纯 SQLite 读（thesis / catalyst_events / trade_plans / ambush_position_snapshots），
# 零 LLM、零写副作用；失败逐表降级为空（单表坏了不拖垮其他表）。
# 统计纪律：样本 n<3 不出比率（仓库既有「n≤2 不算统计」口径）。

HISTORY_PLAN_WINDOW_DAYS = 10        # 近 N 个交易日（自然日窗口取 2N 覆盖周末）
HISTORY_MIN_STATS_SAMPLE = 3         # 低于此样本量只出明细，不出 executed_ratio


def _thesis_history_row(record: Any) -> dict:
    return {
        "sector_name": getattr(record, "sector_name", "") or "",
        "family_name": getattr(record, "family_name", "") or "",
        "lifecycle_state": getattr(record, "lifecycle_state", "") or "",
        "catalyst_event_id": getattr(record, "catalyst_event_id", "") or "",
        "entry_date": getattr(record, "entry_date", None),
        "entry_price": getattr(record, "entry_price", None),
        "composite_at_entry": getattr(record, "composite_at_entry", None),
        "drift_watch_since": getattr(record, "drift_watch_since", None),
        "source": getattr(record, "source", "") or "",
    }


def _catalyst_status(event_date: Optional[date], expires_at: Optional[date], as_of: date) -> str:
    """催化剂锚状态：upcoming / today / elapsed / expired。"""
    if expires_at is not None and expires_at < as_of:
        return "expired"
    if event_date is None:
        return "unknown"
    if event_date > as_of:
        return "upcoming"
    if event_date == as_of:
        return "today"
    return "elapsed"


def lookup_decision_history(
    symbols: list[str],
    as_of: date,
    *,
    plan_window_days: int = HISTORY_PLAN_WINDOW_DAYS,
    session=None,
) -> dict[str, dict]:
    """逐票回查买入时点痕迹，供当日裁决做证据（H3）。

    返回 {symbol: {thesis, catalyst, plan_stats, ambush_snap}}：
    - thesis：最新一条 open 论点（板块/家族/四态/催化剂锚/入场分）
    - catalyst：join catalyst_events（事件日/等级/置信度/方向/状态）
    - plan_stats：近 N 交易日 trade_plans 的执行明细与兑现率（n<3 时 ratio=None）
    - ambush_snap：open 的埋伏仓位快照（有则 P4 可达）

    任何表读取失败 → 该字段 None/空，不抛异常（调用方 try/except 兜底）。
    """
    clean_symbols = [s for s in (symbols or []) if s]
    if not clean_symbols:
        return {}

    result: dict[str, dict] = {
        s: {"thesis": None, "catalyst": None, "plan_stats": None, "ambush_snap": None}
        for s in clean_symbols
    }

    own_session = session is None
    try:
        if own_session:
            from src.storage import DatabaseManager

            session = DatabaseManager().get_session()

        # ── thesis（每票最新 open 一条）──
        try:
            from src.storage import HoldingThesisRecord

            thesis_rows = (
                session.query(HoldingThesisRecord)
                .filter(
                    HoldingThesisRecord.is_open == True,  # noqa: E712
                    HoldingThesisRecord.symbol.in_(clean_symbols),
                )
                .order_by(HoldingThesisRecord.entry_date.desc())
                .all()
            )
            seen: set[str] = set()
            for row in thesis_rows:
                if row.symbol in seen:
                    continue
                seen.add(row.symbol)
                result[row.symbol]["thesis"] = _thesis_history_row(row)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[H3] thesis 回查失败（降级）: %s", exc)

        # ── catalyst（join catalyst_events by event_id）──
        try:
            from src.storage import CatalystEventRecord

            event_ids = [
                (result[s]["thesis"] or {}).get("catalyst_event_id")
                for s in clean_symbols
            ]
            event_ids = [eid for eid in event_ids if eid]
            if event_ids:
                events = (
                    session.query(CatalystEventRecord)
                    .filter(CatalystEventRecord.event_id.in_(event_ids))
                    .all()
                )
                by_id = {e.event_id: e for e in events}
                for symbol in clean_symbols:
                    thesis = result[symbol]["thesis"] or {}
                    eid = thesis.get("catalyst_event_id")
                    event = by_id.get(eid) if eid else None
                    if event is None:
                        continue
                    result[symbol]["catalyst"] = {
                        "event_id": event.event_id,
                        "event_date": event.event_date,
                        "expires_at": getattr(event, "expires_at", None),
                        "impact_level": getattr(event, "impact_level", None),
                        "confidence": getattr(event, "confidence", None),
                        "direction": getattr(event, "direction", None),
                        "category_tag": getattr(event, "category_tag", None),
                        "sector": getattr(event, "sector", None),
                        "title": getattr(event, "title", None),
                        "status": _catalyst_status(
                            getattr(event, "event_date", None),
                            getattr(event, "expires_at", None),
                            as_of,
                        ),
                    }
        except Exception as exc:  # noqa: BLE001
            logger.debug("[H3] catalyst 回查失败（降级）: %s", exc)

        # ── trade_plans 近 N 交易日兑现明细 ──
        try:
            window_start = as_of - timedelta(days=max(plan_window_days, 1) * 2)
            plans = (
                session.query(TradePlanRecord)
                .filter(
                    TradePlanRecord.symbol.in_(clean_symbols),
                    TradePlanRecord.plan_date >= window_start,
                    TradePlanRecord.plan_date < as_of,
                )
                .all()
            )
            buckets: dict[str, dict[str, int]] = {}
            for plan in plans:
                bucket = buckets.setdefault(
                    plan.symbol,
                    {"sampled": 0, "executed": 0, "partial": 0,
                     "expired": 0, "cancelled": 0, "pending": 0},
                )
                bucket["sampled"] += 1
                status = (plan.execution_status or "pending").lower()
                if status in bucket:
                    bucket[status] += 1
                else:
                    bucket["pending"] += 1
            for symbol, bucket in buckets.items():
                decided = bucket["executed"] + bucket["partial"] + bucket["expired"] + bucket["cancelled"]
                ratio = None
                if decided >= HISTORY_MIN_STATS_SAMPLE:
                    ratio = round((bucket["executed"] + 0.5 * bucket["partial"]) / decided, 3)
                result[symbol]["plan_stats"] = {
                    **bucket,
                    "decided": decided,
                    "executed_ratio": ratio,
                    "note": "" if ratio is not None else f"样本 n={decided}<{HISTORY_MIN_STATS_SAMPLE}，不出比率",
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("[H3] trade_plans 回查失败（降级）: %s", exc)

        # ── ambush_position_snapshots（open 行）──
        try:
            from src.storage import AmbushPositionSnapshotRecord

            snaps = (
                session.query(AmbushPositionSnapshotRecord)
                .filter(
                    AmbushPositionSnapshotRecord.is_open == True,  # noqa: E712
                    AmbushPositionSnapshotRecord.symbol.in_(clean_symbols),
                )
                .all()
            )
            for snap in snaps:
                result[snap.symbol]["ambush_snap"] = {
                    "category_tag": getattr(snap, "category_tag", None),
                    "catalyst_event_id": getattr(snap, "catalyst_event_id", ""),
                    "entry_date": getattr(snap, "entry_date", None),
                    "entry_price": getattr(snap, "entry_price", None),
                    "exit_signal": getattr(snap, "exit_signal", None),
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("[H3] ambush snapshot 回查失败（降级）: %s", exc)

        return result
    except Exception as exc:  # noqa: BLE001 — 会话建立失败：整体降级
        logger.warning("[H3] 历史回查整体降级: %s", exc)
        return result
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:
                pass


def build_history_shadow(history: dict[str, dict], *, mode: str) -> dict:
    """H3 喂入留痕（只读快照，供 reasoning 渲染与影子审阅）。"""
    rows = []
    for symbol, row in (history or {}).items():
        thesis = row.get("thesis") or {}
        catalyst = row.get("catalyst") or {}
        stats = row.get("plan_stats") or {}
        rows.append({
            "symbol": symbol,
            "thesis_present": bool(thesis),
            "thesis_sector": thesis.get("sector_name"),
            "thesis_state": thesis.get("lifecycle_state"),
            "catalyst_status": catalyst.get("status"),
            "catalyst_event_date": catalyst.get("event_date"),
            "plans_sampled": stats.get("sampled", 0),
            "executed_ratio": stats.get("executed_ratio"),
            "ambush_snap": bool(row.get("ambush_snap")),
        })
    return {"mode": mode, "symbols": sorted(history.keys()), "rows": rows}
