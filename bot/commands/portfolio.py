# -*- coding: utf-8 -*-
"""
===================================
持仓命令（截图更新 / 查看）
===================================

手机端把券商持仓截图发到 ntfy 主题，由 `ntfy_stream` 落盘后以 `/portfolio`
分发到这里：OCR → 版面解析 → 与系统持仓对账 → **先预览不落库**，
用户回复 `/portfolio confirm` 才写交易。

安全默认：任何写入都必须显式二次确认；预览有 TTL，过期作废。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)

# 预览有效期：超过则 confirm 失效（防止隔天误确认）
PENDING_TTL_SECONDS = 1800
# chat_id -> {"trades": [...], "account_id": int|None, "created_at": float}
_PENDING: Dict[str, Dict[str, Any]] = {}
# 落盘：确认队列是跨进程状态，服务重启/发版后不能丢（否则用户确认时"没有截图"）
PENDING_PATH = Path(os.getenv("PORTFOLIO_PENDING_PATH", "data/pending_portfolio.json"))


def _serialize_trades(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for trade in trades:
        item = dict(trade)
        trade_date = item.get("trade_date")
        if hasattr(trade_date, "isoformat"):
            item["trade_date"] = trade_date.isoformat()
        out.append(item)
    return out


def _deserialize_trades(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for trade in trades:
        item = dict(trade)
        trade_date = item.get("trade_date")
        if isinstance(trade_date, str):
            try:
                item["trade_date"] = date.fromisoformat(trade_date)
            except ValueError:
                pass
        out.append(item)
    return out


def _write_pending_file() -> None:
    try:
        PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: {**entry, "trades": _serialize_trades(entry.get("trades") or [])}
            for key, entry in _PENDING.items()
        }
        PENDING_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("[Portfolio] 待确认队列写入失败: %s", type(exc).__name__)


def _load_pending(key: str) -> Optional[Dict[str, Any]]:
    """内存优先，未命中则从磁盘恢复（服务重启后仍能 confirm）。"""
    entry = _PENDING.get(key)
    if entry is None:
        try:
            raw = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        entry = raw.get(key)
        if not entry:
            return None
        entry["trades"] = _deserialize_trades(entry.get("trades") or [])
        _PENDING[key] = entry
    # TTL 由调用方判断，便于区分「没有预览」与「预览已过期」两种回执
    return entry


def _drop_pending(key: str) -> None:
    _PENDING.pop(key, None)
    _write_pending_file()


class PortfolioCommand(BotCommand):
    """`/portfolio`：发图更新持仓、`confirm` 落库、`cancel` 取消、无参数看持仓。"""

    def __init__(self, ingest_service=None, portfolio_service=None):
        # 注入点：单测传假实现，生产懒加载
        self._ingest = ingest_service
        self._portfolio = portfolio_service

    @property
    def name(self) -> str:
        return "portfolio"

    @property
    def aliases(self) -> List[str]:
        return ["持仓", "pf", "p"]

    @property
    def description(self) -> str:
        return "持仓截图更新 / 查看持仓"

    @property
    def usage(self) -> str:
        return "/portfolio（发图即为更新）| /portfolio confirm | /portfolio cancel"

    # ------------------------------------------------------------------ deps
    def _ingest_service(self):
        if self._ingest is None:
            from src.services.portfolio_screenshot_ingest import PortfolioScreenshotIngestService

            self._ingest = PortfolioScreenshotIngestService()
        return self._ingest

    def _portfolio_service(self):
        if self._portfolio is None:
            from src.services.portfolio_service import PortfolioService

            self._portfolio = PortfolioService()
        return self._portfolio

    # ------------------------------------------------------------------ entry
    def validate_args(self, args: List[str]) -> Optional[str]:
        return None

    def execute(self, message: BotMessage, args: List[str]) -> BotResponse:
        action = (args[0].strip().lower() if args else "")
        key = message.chat_id or message.user_id

        if action in ("confirm", "确认", "ok"):
            return self._confirm(key)
        if action in ("cancel", "取消"):
            _drop_pending(key)
            return BotResponse.text_response("已取消本次持仓更新，未写入任何交易。")

        image_path = (message.raw_data or {}).get("image_path")
        if image_path:
            return self._ingest_image(key, image_path)
        return self._show_snapshot()

    # ------------------------------------------------------------------ paths
    def _ingest_image(self, key: str, image_path: str) -> BotResponse:
        service = self._ingest_service()
        try:
            result = service.ingest_path(image_path)
        except Exception as exc:  # OCR 后端缺失/图片损坏
            logger.exception("持仓截图解析异常")
            return BotResponse.error_response(f"❌ 持仓截图解析失败：{exc}")

        if not result.ok:
            return BotResponse.error_response(f"❌ 持仓截图解析失败：{result.error or '未知错误'}")

        portfolio = self._portfolio_service()
        accounts, system_positions = self._load_system_positions(portfolio)
        account_id = accounts[0].get("account_id") if accounts else None

        # 未匹配到代码的持仓不能进交易（record_trade 需要 symbol），单独回执提示
        matched = [h for h in result.holdings if getattr(h, "symbol", None)]
        unmatched = [h for h in result.holdings if not getattr(h, "symbol", None)]
        if not matched:
            return BotResponse.text_response(
                result.summary
                + "\n\n⚠️ 全部持仓未匹配到股票代码，无法生成交易。"
                "请确认截图里含名称列，或稍后重试（名称库可能正在拉取）。"
            )

        if system_positions:
            trades = service.reconcile(matched, system_positions)
        else:
            trades = service._ocr.generate_initial_trades(matched)

        _PENDING[key] = {
            "trades": trades,
            "account_id": account_id,
            "created_at": time.time(),
        }
        _write_pending_file()

        reply = service.build_reply(result, trades)
        if unmatched:
            names = "、".join(h.name for h in unmatched)
            reply += f"\n\n⚠️ 未匹配到代码（不写入）：{names}"
        if trades:
            reply += "\n\n回复 `/portfolio confirm` 写入，或 `/portfolio cancel` 放弃（30 分钟内有效）。"
        return BotResponse.markdown_response(reply)

    def _load_system_positions(self, portfolio):
        snapshot = portfolio.get_portfolio_snapshot()
        accounts = snapshot.get("accounts") or []
        system_positions: Dict[str, Dict[str, Any]] = {}
        for account in accounts:
            for pos in account.get("positions") or []:
                system_positions[pos["symbol"]] = {
                    "quantity": pos["quantity"],
                    "last_price": pos.get("last_price", 0.0),
                }
        return accounts, system_positions

    def _confirm(self, key: str) -> BotResponse:
        pending = _load_pending(key)
        if not pending:
            return BotResponse.text_response("没有待确认的持仓更新。先发一张持仓截图。")
        if time.time() - float(pending.get("created_at", 0)) > PENDING_TTL_SECONDS:
            _drop_pending(key)
            return BotResponse.text_response("预览已过期（超过 30 分钟），请重新发一次截图。")

        account_id = pending.get("account_id")
        if account_id is None:
            return BotResponse.error_response("未找到可用账户，无法写入。请先在系统里创建账户。")

        portfolio = self._portfolio_service()
        done, failed = 0, []
        for trade in pending["trades"]:
            try:
                portfolio.record_trade(account_id=account_id, **trade)
                done += 1
            except Exception as exc:
                failed.append(f"{trade.get('symbol')} {trade.get('side')}: {exc}")

        _drop_pending(key)
        lines = [f"✅ 已写入 {done}/{len(pending['trades'])} 笔交易。"]
        if failed:
            lines.append("失败明细：")
            lines.extend(f"  • {item}" for item in failed)
        return BotResponse.text_response("\n".join(lines))

    def _show_snapshot(self) -> BotResponse:
        try:
            snapshot = self._portfolio_service().get_portfolio_snapshot()
        except Exception as exc:
            return BotResponse.error_response(f"读取持仓失败：{exc}")

        accounts = snapshot.get("accounts") or []
        if not accounts:
            return BotResponse.text_response(
                "还没有账户。发一张券商持仓截图给我即可导入（回复 `/portfolio confirm` 落库）。"
            )

        lines = []
        for account in accounts:
            positions = account.get("positions") or []
            lines.append(
                f"**{account.get('account_name') or '账户'}** "
                f"总资产 {account.get('total_equity', 0):,.0f} · 持仓 {len(positions)} 只"
            )
            for pos in positions[:10]:
                lines.append(
                    f"  {pos.get('name') or pos['symbol']}({pos['symbol']}) "
                    f"{pos.get('quantity', 0):.0f} 股"
                )
            if len(positions) > 10:
                lines.append(f"  … 其余 {len(positions) - 10} 只")
        lines.append("\n发持仓截图可直接更新，`/portfolio confirm` 落库。")
        return BotResponse.markdown_response("\n".join(lines))
