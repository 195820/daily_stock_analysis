# -*- coding: utf-8 -*-
"""Tests for portfolio OCR import service."""

import pytest
from src.services.portfolio_ocr_import import (
    HoldingItem,
    PortfolioOcrImportService,
    preview_trades,
)


class TestParseHoldingsFromOcr:
    """Test OCR text parsing logic."""

    def setup_method(self):
        self.service = PortfolioOcrImportService()

    def test_single_line_format(self):
        """Test single-line holding format: name qty cost current."""
        ocr_text = """
        株冶集团 1400 24.404 26.340
        铭普光磁 1500 23.303 23.660
        凡拓数创 1000 35.715 37.140
        """
        holdings = self.service.parse_holdings_from_ocr(ocr_text)
        assert len(holdings) == 3
        assert holdings[0].name == "株冶集团"
        assert holdings[0].quantity == 1400
        assert holdings[0].cost_price == 24.404
        assert holdings[0].current_price == 26.340

    def test_two_line_format(self):
        """Test two-line holding format from CITIC Securities."""
        ocr_text = """
        株冶集团    2708.15    1400    24.404
        36876.00    7.931%     1400    26.340
        """
        holdings = self.service.parse_holdings_from_ocr(ocr_text)
        assert len(holdings) == 1
        assert holdings[0].name == "株冶集团"
        assert holdings[0].quantity == 1400

    def test_empty_ocr_text(self):
        """Test empty OCR text returns empty list."""
        holdings = self.service.parse_holdings_from_ocr("")
        assert holdings == []

    def test_no_holdings_in_text(self):
        """Test text with no holdings returns empty list."""
        ocr_text = """
        总资产 110723.69
        浮动盈亏 +4668.13
        """
        holdings = self.service.parse_holdings_from_ocr(ocr_text)
        assert holdings == []

    def test_mixed_valid_invalid_lines(self):
        """Test mix of valid and invalid lines."""
        ocr_text = """
        总资产 110723.69
        株冶集团 1400 24.404 26.340
        浮动盈亏 +4668.13
        铭普光磁 1500 23.303 23.660
        """
        holdings = self.service.parse_holdings_from_ocr(ocr_text)
        assert len(holdings) == 2


class TestGenerateInitialTrades:
    """Test trade generation from holdings."""

    def setup_method(self):
        self.service = PortfolioOcrImportService()

    def test_generate_trades(self):
        """Test generating initial buy trades."""
        holdings = [
            HoldingItem(name="株冶集团", quantity=1400, cost_price=24.404, current_price=26.340, symbol="SH600519"),
            HoldingItem(name="铭普光磁", quantity=1500, cost_price=23.303, current_price=23.660, symbol="SZ002929"),
        ]
        trades = self.service.generate_initial_trades(holdings)
        assert len(trades) == 2
        assert trades[0]['symbol'] == "SH600519"
        assert trades[0]['side'] == 'buy'
        assert trades[0]['quantity'] == 1400.0
        assert trades[0]['price'] == 24.404
        assert 'OCR初始化导入' in trades[0]['note']

    def test_skip_unmatched_holdings(self):
        """Test that unmatched holdings (no symbol) are skipped."""
        holdings = [
            HoldingItem(name="未知股票", quantity=100, cost_price=10.0, current_price=11.0, symbol=None),
            HoldingItem(name="铭普光磁", quantity=1500, cost_price=23.303, current_price=23.660, symbol="SZ002929"),
        ]
        trades = self.service.generate_initial_trades(holdings)
        assert len(trades) == 1
        assert trades[0]['symbol'] == "SZ002929"


class TestComputeSyncTrades:
    """Test sync trade computation."""

    def setup_method(self):
        self.service = PortfolioOcrImportService()

    def test_new_stock(self):
        """Test generating buy trade for new stock."""
        holdings = [
            HoldingItem(name="新股票", quantity=1000, cost_price=20.0, current_price=22.0, symbol="SH600000"),
        ]
        system_positions = {}  # Empty system
        trades = self.service.compute_sync_trades(holdings, system_positions)
        assert len(trades) == 1
        assert trades[0]['side'] == 'buy'
        assert trades[0]['quantity'] == 1000.0
        assert '新增' in trades[0]['note']

    def test_disappeared_stock(self):
        """Test generating sell trade for disappeared stock."""
        holdings = []  # Empty screenshot
        system_positions = {
            "SH600000": {'quantity': 1000, 'last_price': 22.0},
        }
        trades = self.service.compute_sync_trades(holdings, system_positions)
        assert len(trades) == 1
        assert trades[0]['side'] == 'sell'
        assert trades[0]['quantity'] == 1000.0
        assert '清仓' in trades[0]['note']

    def test_quantity_increase(self):
        """Test generating buy trade for quantity increase."""
        holdings = [
            HoldingItem(name="股票", quantity=1500, cost_price=20.0, current_price=22.0, symbol="SH600000"),
        ]
        system_positions = {
            "SH600000": {'quantity': 1000, 'last_price': 22.0},
        }
        trades = self.service.compute_sync_trades(holdings, system_positions)
        assert len(trades) == 1
        assert trades[0]['side'] == 'buy'
        assert trades[0]['quantity'] == 500.0
        assert '补仓' in trades[0]['note']

    def test_quantity_decrease(self):
        """Test generating sell trade for quantity decrease."""
        holdings = [
            HoldingItem(name="股票", quantity=800, cost_price=20.0, current_price=22.0, symbol="SH600000"),
        ]
        system_positions = {
            "SH600000": {'quantity': 1000, 'last_price': 22.0},
        }
        trades = self.service.compute_sync_trades(holdings, system_positions)
        assert len(trades) == 1
        assert trades[0]['side'] == 'sell'
        assert trades[0]['quantity'] == 200.0
        assert '减仓' in trades[0]['note']

    def test_no_change(self):
        """Test no trades when holdings match system."""
        holdings = [
            HoldingItem(name="股票", quantity=1000, cost_price=20.0, current_price=22.0, symbol="SH600000"),
        ]
        system_positions = {
            "SH600000": {'quantity': 1000, 'last_price': 22.0},
        }
        trades = self.service.compute_sync_trades(holdings, system_positions)
        assert len(trades) == 0


class TestPreviewTrades:
    """Test trade preview formatting."""

    def test_preview_empty(self):
        """Test preview with empty trades."""
        result = preview_trades([])
        assert "无交易" in result

    def test_preview_with_trades(self):
        """Test preview with trades."""
        trades = [
            {'symbol': 'SH600519', 'side': 'buy', 'quantity': 100, 'price': 24.404, 'trade_date': '2026-08-11', 'note': 'test'},
        ]
        result = preview_trades(trades)
        assert "SH600519" in result
        assert "BUY" in result
        assert "100" in result
