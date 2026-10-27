# -*- coding: utf-8 -*-
"""`/portfolio` 截图更新命令的行为测试（OCR/DB 全部用假实现，不触网不写库）。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.commands import portfolio as portfolio_module
from bot.commands.portfolio import PortfolioCommand
from bot.models import BotMessage, ChatType
from src.services.portfolio_ocr_import import HoldingItem
from src.services.portfolio_screenshot_ingest import IngestResult


TRADE = {
    "symbol": "SH600519",
    "side": "buy",
    "quantity": 100.0,
    "price": 1600.0,
    "trade_date": date(2026, 10, 27),
}


class _FakeOcr:
    def generate_initial_trades(self, holdings, trade_date=None):
        return [dict(TRADE)]


class _FakeIngest:
    def __init__(self, ok=True, trades=None):
        self._ocr = _FakeOcr()
        self.ok = ok
        self.trades = trades if trades is not None else [dict(TRADE)]
        self.ingest_calls = []

    def ingest_path(self, image_path):
        self.ingest_calls.append(image_path)
        if not self.ok:
            return IngestResult(ok=False, error="OCR 未识别到任何文本")
        holdings = [HoldingItem(name="贵州茅台", symbol="SH600519", quantity=100,
                                cost_price=1580.0, current_price=1602.5)]
        return IngestResult(ok=True, holdings=holdings, summary="识别到 1 只持仓")

    def reconcile(self, holdings, system_positions, trade_date=None):
        return list(self.trades)

    def build_reply(self, result, trades=None):
        return f"{result.summary}\n需要 {len(trades or [])} 笔调整"


class _FakePortfolio:
    def __init__(self, positions=None):
        self.recorded = []
        self._positions = positions or {}

    def get_portfolio_snapshot(self):
        if not self._positions:
            return {"accounts": []}
        return {
            "accounts": [{
                "account_id": 7,
                "account_name": "主账户",
                "total_equity": 123456.0,
                "positions": [
                    {"symbol": sym, "name": "持仓", "quantity": qty, "last_price": 10.0}
                    for sym, qty in self._positions.items()
                ],
            }]
        }

    def record_trade(self, **kwargs):
        self.recorded.append(kwargs)
        return kwargs


def _message(image_path=None, chat_id="dsa-topic"):
    return BotMessage(
        platform="ntfy",
        message_id="m1",
        user_id="ntfy:user",
        user_name="ntfy",
        chat_id=chat_id,
        chat_type=ChatType.PRIVATE,
        content="/portfolio",
        raw_content="/portfolio",
        raw_data={"image_path": image_path} if image_path else {},
    )


class TestPortfolioCommand(unittest.TestCase):
    def setUp(self):
        portfolio_module._PENDING.clear()
        # 每个用例独立的落盘队列，避免污染仓库 data/
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_pending_path = portfolio_module.PENDING_PATH
        portfolio_module.PENDING_PATH = Path(self._tmpdir.name) / "pending.json"

    def tearDown(self):
        portfolio_module.PENDING_PATH = self._orig_pending_path
        portfolio_module._PENDING.clear()
        self._tmpdir.cleanup()

    def _command(self, ingest=None, portfolio=None, portfolio_service=None):
        return PortfolioCommand(
            ingest_service=ingest or _FakeIngest(),
            portfolio_service=portfolio or portfolio_service or _FakePortfolio({"SH600519": 100.0}),
        )

    def test_image_produces_preview_without_writing(self):
        ingest = _FakeIngest()
        portfolio = _FakePortfolio({"SH600519": 100.0})
        command = self._command(ingest, portfolio)

        response = command.execute(_message("/tmp/holdings.png"), [])

        self.assertIn("识别到 1 只持仓", response.text)
        self.assertIn("/portfolio confirm", response.text)
        self.assertEqual(ingest.ingest_calls, ["/tmp/holdings.png"])
        self.assertEqual(portfolio.recorded, [], "预览阶段绝不能写库")
        self.assertIn("dsa-topic", portfolio_module._PENDING)

    def test_confirm_writes_trades_and_clears_pending(self):
        portfolio = _FakePortfolio({"SH600519": 100.0})
        command = self._command(portfolio_service=portfolio)
        command.execute(_message("/tmp/holdings.png"), [])

        response = command.execute(_message(), ["confirm"])

        self.assertIn("已写入 1/1", response.text)
        self.assertEqual(len(portfolio.recorded), 1)
        self.assertEqual(portfolio.recorded[0]["account_id"], 7)
        self.assertEqual(portfolio.recorded[0]["symbol"], "SH600519")
        self.assertNotIn("dsa-topic", portfolio_module._PENDING)

    def test_cancel_discards_pending(self):
        portfolio = _FakePortfolio({"SH600519": 100.0})
        command = self._command(portfolio_service=portfolio)
        command.execute(_message("/tmp/holdings.png"), [])

        response = command.execute(_message(), ["cancel"])

        self.assertIn("已取消", response.text)
        self.assertEqual(portfolio.recorded, [])
        self.assertNotIn("dsa-topic", portfolio_module._PENDING)

    def test_confirm_without_pending_is_noop(self):
        portfolio = _FakePortfolio({"SH600519": 100.0})
        command = self._command(portfolio_service=portfolio)

        response = command.execute(_message(), ["confirm"])

        self.assertIn("没有待确认", response.text)
        self.assertEqual(portfolio.recorded, [])

    def test_expired_preview_rejected(self):
        portfolio = _FakePortfolio({"SH600519": 100.0})
        command = self._command(portfolio_service=portfolio)
        command.execute(_message("/tmp/holdings.png"), [])
        portfolio_module._PENDING["dsa-topic"]["created_at"] -= portfolio_module.PENDING_TTL_SECONDS + 1

        response = command.execute(_message(), ["confirm"])

        self.assertIn("已过期", response.text)
        self.assertEqual(portfolio.recorded, [])

    def test_failed_ocr_returns_error_and_no_pending(self):
        command = self._command(_FakeIngest(ok=False))

        response = command.execute(_message("/tmp/bad.png"), [])

        self.assertIn("解析失败", response.text)
        self.assertNotIn("dsa-topic", portfolio_module._PENDING)

    def test_confirm_survives_process_restart(self):
        """预览后进程重启（内存清空）仍应能 confirm —— 队列已落盘。"""
        portfolio = _FakePortfolio({"SH600519": 100.0})
        command = self._command(portfolio_service=portfolio)
        command.execute(_message("/tmp/holdings.png"), [])

        # 模拟重启：内存态清空，只剩磁盘
        portfolio_module._PENDING.clear()
        restarted = self._command(portfolio_service=portfolio)

        response = restarted.execute(_message(), ["confirm"])

        self.assertIn("已写入 1/1", response.text)
        self.assertEqual(len(portfolio.recorded), 1)
        self.assertEqual(portfolio.recorded[0]["trade_date"], date(2026, 10, 27))

    def test_unmatched_symbol_is_excluded_from_trades(self):
        class _NoSymbolOcr(_FakeOcr):
            def generate_initial_trades(self, holdings, trade_date=None):
                return []

        class _NoSymbolIngest(_FakeIngest):
            def __init__(self):
                super().__init__()
                self._ocr = _NoSymbolOcr()

            def ingest_path(self, image_path):
                item = HoldingItem(name="某只新股", symbol=None, quantity=100,
                                   cost_price=10.0, current_price=11.0)
                return IngestResult(ok=True, holdings=[item], summary="识别到 1 只持仓")

        portfolio = _FakePortfolio()
        command = PortfolioCommand(ingest_service=_NoSymbolIngest(), portfolio_service=portfolio)

        response = command.execute(_message("/tmp/new.png"), [])

        self.assertIn("未匹配到股票代码", response.text)
        self.assertEqual(portfolio.recorded, [])

    def test_no_image_shows_snapshot(self):
        portfolio = _FakePortfolio({"SH600519": 100.0})
        command = self._command(portfolio_service=portfolio)

        response = command.execute(_message(), [])

        self.assertIn("主账户", response.text)
        self.assertIn("SH600519", response.text)

    def test_empty_portfolio_hints_how_to_import(self):
        command = self._command(portfolio_service=_FakePortfolio())

        response = command.execute(_message(), [])

        self.assertIn("发一张券商持仓截图", response.text)


if __name__ == "__main__":
    unittest.main()
