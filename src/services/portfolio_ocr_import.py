# -*- coding: utf-8 -*-
"""OCR-based portfolio import service.

Extract holdings from broker APP screenshots (e.g., CITIC Securities)
using PaddleOCR, then generate trade events to initialize/sync positions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HoldingItem:
    """Parsed holding from OCR screenshot."""
    name: str           # 股票中文名
    quantity: int       # 持仓数量
    cost_price: float   # 成本价
    current_price: float  # 现价
    symbol: Optional[str] = None  # 股票代码（匹配后填充）


class PortfolioOcrImportService:
    """OCR screenshot → holdings → trade events."""

    def __init__(self):
        self._ocr = None

    def _get_ocr(self):
        """Lazy-load PaddleOCR to avoid import overhead when not used."""
        if self._ocr is None:
            try:
                from paddleocr import PaddleOCR
                # PaddleOCR 3.x uses different params than 2.x
                try:
                    self._ocr = PaddleOCR(lang='ch')
                except TypeError:
                    # Fallback for older versions
                    self._ocr = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)
            except ImportError:
                raise ImportError(
                    "PaddleOCR not installed. Run: pip install paddlepaddle paddleocr"
                )
        return self._ocr

    def ocr_screenshot(self, image_path: str) -> str:
        """Run OCR on screenshot, return raw text."""
        ocr = self._get_ocr()
        # PaddleOCR 3.x returns dict format, 2.x returns list format
        try:
            result = ocr.ocr(image_path)
        except Exception:
            result = ocr.predict(image_path)

        if not result:
            return ""

        lines = []
        # Handle both 2.x and 3.x result formats
        if isinstance(result, list) and result[0]:
            # 2.x format: list of (bbox, (text, confidence))
            for line in result[0]:
                bbox, (text, confidence) = line
                if confidence > 0.5:
                    lines.append(text)
        elif isinstance(result, dict):
            # 3.x format: dict with 'rec_texts' and 'rec_scores'
            rec_texts = result.get('rec_texts', [])
            rec_scores = result.get('rec_scores', [])
            for text, score in zip(rec_texts, rec_scores):
                if score > 0.5:
                    lines.append(text)

        return "\n".join(lines)

    def parse_holdings_from_ocr(self, ocr_text: str) -> List[HoldingItem]:
        """Parse OCR text into structured holdings.

        Expected format (CITIC Securities):
            株冶集团    2,708.15    1400    24.404
            36,876.00   7.931%      1400    26.340

        Or single-line format:
            株冶集团 1400 24.404 26.340
        """
        holdings = []

        # Pattern 1: 股票名 + 数量 + 成本价 + 现价 (single line)
        # Matches: 株冶集团 1400 24.404 26.340
        pattern1 = re.compile(
            r'([一-鿿]{2,6})\s+'  # Chinese stock name (2-6 chars)
            r'(\d{1,6})\s+'               # Quantity
            r'([\d.]+)\s+'                # Cost price
            r'([\d.]+)'                   # Current price
        )

        # Pattern 2: 股票名 + 市值/盈亏 + 持仓/可用 + 成本/现价 (two lines per stock)
        # Line 1: 株冶集团    2,708.15    1400    24.404
        # Line 2: 36,876.00   7.931%      1400    26.340
        pattern2_name = re.compile(r'^([一-鿿]{2,6})\s')
        pattern2_data = re.compile(r'([\d,]+\.\d+)\s+([\d.]+%?)\s+(\d+)\s+([\d.]+)')

        lines = [l.strip() for l in ocr_text.split('\n') if l.strip()]

        i = 0
        while i < len(lines):
            line = lines[i]

            # Try pattern 1 first (single line)
            m = pattern1.search(line)
            if m:
                name = m.group(1)
                try:
                    quantity = int(m.group(2))
                    cost_price = float(m.group(3))
                    current_price = float(m.group(4))
                    holdings.append(HoldingItem(
                        name=name,
                        quantity=quantity,
                        cost_price=cost_price,
                        current_price=current_price,
                    ))
                except ValueError as e:
                    logger.warning("Failed to parse holding: %s - %s", line, e)
                i += 1
                continue

            # Try pattern 2 (two lines)
            name_match = pattern2_name.match(line)
            if name_match and i + 1 < len(lines):
                next_line = lines[i + 1]
                data_match = pattern2_data.search(next_line)
                if data_match:
                    name = name_match.group(1)
                    try:
                        # Extract quantity and prices from the two lines
                        # Line 1 might have: name, pnl, quantity, cost_price
                        # Line 2 might have: market_value, pnl_pct, quantity, current_price
                        parts1 = re.findall(r'[\d,]+\.?\d*', line)
                        parts2 = re.findall(r'[\d,]+\.?\d*', next_line)

                        # Find quantity (integer-like) and prices
                        quantity = None
                        cost_price = None
                        current_price = None

                        for p in parts1 + parts2:
                            val = float(p.replace(',', ''))
                            if val == int(val) and 100 <= val <= 100000:
                                quantity = int(val)
                            elif 1 < val < 1000:
                                if cost_price is None:
                                    cost_price = val
                                elif current_price is None:
                                    current_price = val

                        if quantity and cost_price and current_price:
                            holdings.append(HoldingItem(
                                name=name,
                                quantity=quantity,
                                cost_price=cost_price,
                                current_price=current_price,
                            ))
                    except (ValueError, IndexError) as e:
                        logger.warning("Failed to parse two-line holding: %s - %s", line, e)
                    i += 2
                    continue

            i += 1

        return holdings

    def match_stock_name(self, name: str) -> Optional[str]:
        """Match Chinese stock name to stock code.

        Uses akshare to get A-share stock list and matches by name.
        Returns canonical code like 'SH600519' or None.
        """
        try:
            import akshare as ak
            from difflib import SequenceMatcher

            # Get A-share stock list
            # akshare provides stock_zh_a_spot_em() with name and code columns
            df = ak.stock_zh_a_spot_em()

            # Try exact match first
            exact_matches = df[df['名称'] == name]
            if not exact_matches.empty:
                row = exact_matches.iloc[0]
                code = str(row['代码'])
                # Determine exchange prefix
                if code.startswith('6'):
                    return f"SH{code}"
                elif code.startswith(('0', '3')):
                    return f"SZ{code}"
                elif code.startswith(('4', '8')):
                    return f"BJ{code}"
                return code

            # Fuzzy match
            best_match = None
            best_score = 0.0

            for _, row in df.iterrows():
                stock_name = str(row['名称'])
                score = SequenceMatcher(None, name, stock_name).ratio()
                if score > best_score and score > 0.7:  # 70% threshold
                    best_score = score
                    best_match = row

            if best_match is not None:
                code = str(best_match['代码'])
                if code.startswith('6'):
                    return f"SH{code}"
                elif code.startswith(('0', '3')):
                    return f"SZ{code}"
                elif code.startswith(('4', '8')):
                    return f"BJ{code}"
                return code

        except ImportError:
            logger.error("akshare not installed")
        except Exception as e:
            logger.warning("Failed to match stock name '%s': %s", name, e)

        return None

    def match_stock_name_fuzzy(self, name: str, candidates: List[Dict[str, str]]) -> Optional[str]:
        """Fuzzy match stock name against candidate list.

        candidates: [{'symbol': 'SH600519', 'name': '贵州茅台'}, ...]
        Falls back to match_stock_name() for full A-share matching.
        """
        from difflib import SequenceMatcher

        best_match = None
        best_score = 0.0

        for cand in candidates:
            score = SequenceMatcher(None, name, cand['name']).ratio()
            if score > best_score and score > 0.6:
                best_score = score
                best_match = cand['symbol']

        if best_match:
            return best_match

        # Fallback to full A-share matching
        return self.match_stock_name(name)

    def generate_initial_trades(
        self,
        holdings: List[HoldingItem],
        trade_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """Generate initial buy trade events for holdings.

        Returns list of trade dicts ready for record_trade().
        """
        if trade_date is None:
            trade_date = date.today()

        trades = []
        for h in holdings:
            if not h.symbol:
                logger.warning("Skipping %s: no symbol matched", h.name)
                continue

            trades.append({
                'symbol': h.symbol,
                'trade_date': trade_date,
                'side': 'buy',
                'quantity': float(h.quantity),
                'price': h.cost_price,
                'fee': 0.0,
                'tax': 0.0,
                'note': f'OCR初始化导入-{h.name}',
            })

        return trades

    def compute_sync_trades(
        self,
        current_holdings: List[HoldingItem],
        system_positions: Dict[str, Dict[str, Any]],
        trade_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """Compute adjustment trades to sync system with screenshot.

        Args:
            current_holdings: Holdings from OCR screenshot
            system_positions: Current system positions {symbol: {quantity, ...}}
            trade_date: Date for adjustment trades

        Returns:
            List of trade dicts to execute
        """
        if trade_date is None:
            trade_date = date.today()

        trades = []
        current_symbols = {h.symbol for h in current_holdings if h.symbol}
        system_symbols = set(system_positions.keys())

        # New stocks in screenshot (not in system)
        for h in current_holdings:
            if h.symbol and h.symbol not in system_symbols:
                trades.append({
                    'symbol': h.symbol,
                    'trade_date': trade_date,
                    'side': 'buy',
                    'quantity': float(h.quantity),
                    'price': h.cost_price,
                    'fee': 0.0,
                    'tax': 0.0,
                    'note': f'OCR同步新增-{h.name}',
                })

        # Stocks disappeared from screenshot (sell all)
        for sym in system_symbols - current_symbols:
            pos = system_positions[sym]
            trades.append({
                'symbol': sym,
                'trade_date': trade_date,
                'side': 'sell',
                'quantity': float(pos['quantity']),
                'price': pos.get('last_price', 0.0),
                'fee': 0.0,
                'tax': 0.0,
                'note': f'OCR同步清仓-{sym}',
            })

        # Quantity changes
        for h in current_holdings:
            if h.symbol and h.symbol in system_symbols:
                sys_qty = system_positions[h.symbol]['quantity']
                diff = h.quantity - sys_qty
                if diff > 0:
                    trades.append({
                        'symbol': h.symbol,
                        'trade_date': trade_date,
                        'side': 'buy',
                        'quantity': float(diff),
                        'price': h.cost_price,
                        'fee': 0.0,
                        'tax': 0.0,
                        'note': f'OCR同步补仓-{h.name}',
                    })
                elif diff < 0:
                    trades.append({
                        'symbol': h.symbol,
                        'trade_date': trade_date,
                        'side': 'sell',
                        'quantity': float(-diff),
                        'price': h.current_price,
                        'fee': 0.0,
                        'tax': 0.0,
                        'note': f'OCR同步减仓-{h.name}',
                    })

        return trades


def preview_trades(trades: List[Dict[str, Any]]) -> str:
    """Format trades for user confirmation."""
    if not trades:
        return "无交易需要生成"

    lines = ["即将生成以下交易:", "=" * 50]
    for i, t in enumerate(trades, 1):
        lines.append(
            f"{i}. {t['side'].upper()} {t['symbol']} "
            f"数量={t['quantity']:.0f} 价格={t['price']:.3f} "
            f"日期={t['trade_date']} 备注={t['note']}"
        )
    return "\n".join(lines)
