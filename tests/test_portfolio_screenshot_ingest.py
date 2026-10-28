# -*- coding: utf-8 -*-
"""持仓截图摄取服务的单测（OCR 用假实现，不依赖 rapidocr/paddle）。"""

from src.services.portfolio_ocr_import import HoldingItem
from src.services.portfolio_screenshot_ingest import PortfolioScreenshotIngestService


class FakeOcr:
    def __init__(self, boxes=None, holdings=None):
        self._boxes = boxes if boxes is not None else [{"text": "x", "score": 0.9,
                                                       "x0": 0, "y0": 0, "x1": 1, "y1": 1}]
        self._holdings = holdings if holdings is not None else []

    def ocr_boxes(self, image_path, min_score: float = 0.5):
        return self._boxes

    def parse_holdings_from_boxes(self, boxes):
        return self._holdings

    def compute_sync_trades(self, current_holdings, system_positions, trade_date=None):
        return []


HOLDINGS = [
    HoldingItem(name="平安银行", quantity=1000, cost_price=11.20, current_price=11.35, symbol="000001"),
    HoldingItem(name="贵州茅台", quantity=100, cost_price=1580.00, current_price=1602.50, symbol="600519"),
]


class TestIngest:
    def test_ok_result_and_summary(self):
        svc = PortfolioScreenshotIngestService(ocr=FakeOcr(holdings=HOLDINGS))
        res = svc.ingest_path("/tmp/x.png")
        assert res.ok
        assert len(res.holdings) == 2
        assert "识别到 2 只持仓" in res.summary
        assert "平安银行(000001) 1000 股" in res.summary
        assert "合计市值" in res.summary

    def test_no_boxes_is_failure(self):
        svc = PortfolioScreenshotIngestService(ocr=FakeOcr(boxes=[]))
        res = svc.ingest_path("/tmp/x.png")
        assert not res.ok
        assert "未识别到任何文本" in res.error

    def test_no_holdings_is_failure(self):
        svc = PortfolioScreenshotIngestService(ocr=FakeOcr(holdings=[]))
        res = svc.ingest_path("/tmp/x.png")
        assert not res.ok
        assert "未解析出持仓行" in res.error

    def test_ingest_bytes_writes_temp_and_cleans_up(self):
        svc = PortfolioScreenshotIngestService(ocr=FakeOcr(holdings=HOLDINGS))
        res = svc.ingest_bytes(b"\x89PNG\r\n\x1a\nfake")
        assert res.ok

    def test_reply_when_consistent(self):
        svc = PortfolioScreenshotIngestService(ocr=FakeOcr(holdings=HOLDINGS))
        res = svc.ingest_path("/tmp/x.png")
        reply = svc.build_reply(res, trades=[])
        assert "与系统持仓一致" in reply

    def test_reply_on_failure_mentions_reason(self):
        svc = PortfolioScreenshotIngestService(ocr=FakeOcr(boxes=[]))
        res = svc.ingest_path("/tmp/x.png")
        assert svc.build_reply(res).startswith("❌")
