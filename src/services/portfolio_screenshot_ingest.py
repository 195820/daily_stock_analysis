# -*- coding: utf-8 -*-
"""持仓截图摄取：图片字节/路径 → OCR → 版面解析 → 调仓事件 → 供 IM 回执。

设计要点：
- 纯本地逻辑，不依赖任何 IM SDK，便于单测与 CLI 复用；
- 默认 `apply=False`（只算不改库）——写库必须显式开启，避免误同步把持仓洗掉；
- OCR 服务可注入，测试用假实现，生产用 `PortfolioOcrImportService`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional

from src.services.portfolio_ocr_import import HoldingItem, PortfolioOcrImportService, preview_trades

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    ok: bool
    holdings: List[HoldingItem] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None


class PortfolioScreenshotIngestService:
    """把一张持仓截图变成「可回执的文字 + 可选落库的调仓事件」。"""

    def __init__(self, ocr: Optional[PortfolioOcrImportService] = None):
        self._ocr = ocr or PortfolioOcrImportService()

    # ---------- 纯解析 ----------

    def ingest_path(self, image_path: str) -> IngestResult:
        boxes = self._ocr.ocr_boxes(image_path)
        if not boxes:
            return IngestResult(ok=False, error="OCR 未识别到任何文本")
        holdings = self._ocr.parse_holdings_from_boxes(boxes)
        if not holdings:
            return IngestResult(ok=False, error="识别到文本但未解析出持仓行（请确认是持仓表格截图）")
        return IngestResult(ok=True, holdings=holdings, summary=self._render(holdings))

    def ingest_bytes(self, data: bytes, suffix: str = ".png") -> IngestResult:
        tmp = NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(data)
            tmp.flush()
            tmp.close()
            return self.ingest_path(tmp.name)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @staticmethod
    def _render(holdings: List[HoldingItem]) -> str:
        lines = [f"识别到 {len(holdings)} 只持仓："]
        total_value = 0.0
        for h in holdings:
            mv = h.quantity * h.current_price
            total_value += mv
            pnl = (h.current_price - h.cost_price) * h.quantity
            lines.append(
                f"  {h.name}({h.symbol or '?'}) {h.quantity} 股 "
                f"成本 {h.cost_price:.2f} / 现价 {h.current_price:.2f} "
                f"市值 {mv:,.0f} 盈亏 {pnl:+,.0f}"
            )
        lines.append(f"合计市值 {total_value:,.0f}")
        return "\n".join(lines)

    # ---------- 与系统对账 ----------

    def reconcile(
        self,
        holdings: List[HoldingItem],
        system_positions: Dict[str, Dict[str, Any]],
        trade_date=None,
    ) -> List[Dict[str, Any]]:
        return self._ocr.compute_sync_trades(holdings, system_positions, trade_date=trade_date)

    def build_reply(self, result: IngestResult, trades: Optional[List[Dict[str, Any]]] = None) -> str:
        """拼回执：识别结果 + 对账结论。不回执敏感信息。"""
        if not result.ok:
            return f"❌ 持仓截图解析失败：{result.error or '未知错误'}"
        parts = [result.summary]
        if trades is None:
            return "\n".join(parts)
        if not trades:
            parts.append("\n✅ 与系统持仓一致，无需调整")
            return "\n".join(parts)
        parts.append(f"\n⚠️ 需要 {len(trades)} 笔调整（预览，未落库）：")
        parts.append(preview_trades(trades))
        return "\n".join(parts)
