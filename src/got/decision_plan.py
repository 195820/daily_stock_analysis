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
from datetime import date, timedelta
from typing import Optional

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
    own_session = session is None
    today = date.today()
    
    try:
        if own_session:
            from src.storage import DatabaseManager
            db = DatabaseManager()
            session = db.get_session()
        
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
