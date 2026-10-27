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

import logging
import time
from typing import Any, Dict, List, Optional

from bot.commands.base import BotCommand
from bot.models import BotMessage, BotResponse

logger = logging.getLogger(__name__)

# 预览有效期：超过则 confirm 失效（防止隔天误确认）
PENDING_TTL_SECONDS = 1800
# chat_id -> {"trades": [...], "account_id": int|None, "created_at": float}
_PENDING: Dict[str, Dict[str, Any]] = {}


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
            _PENDING.pop(key, None)
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

        if system_positions:
            trades = service.reconcile(result.holdings, system_positions)
        else:
            trades = service._ocr.generate_initial_trades(result.holdings)

        _PENDING[key] = {
            "trades": trades,
            "account_id": account_id,
            "created_at": time.time(),
        }

        reply = service.build_reply(result, trades)
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
        pending = _PENDING.get(key)
        if not pending:
            return BotResponse.text_response("没有待确认的持仓更新。先发一张持仓截图。")
        if time.time() - pending["created_at"] > PENDING_TTL_SECONDS:
            _PENDING.pop(key, None)
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

        _PENDING.pop(key, None)
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
