# -*- coding: utf-8 -*-
"""OCR-based portfolio import service.

Extract holdings from broker APP screenshots (e.g., CITIC Securities)
using PaddleOCR, then generate trade events to initialize/sync positions.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 表头/导航词：不当作股票名（中信/华泰等 APP 的两种版面都会出现）
_HEADER_WORDS = {
    "市值", "盈亏", "持仓", "可用", "成本", "现价", "总资产", "浮动盈亏", "当日参考盈亏",
    "持仓股", "总市值", "逆回购", "可取", "转账", "买入", "卖出", "撤单", "查询", "仓位",
    "查看已清仓股票", "持仓管理", "批量买入", "批量卖出", "止盈止损", "持仓资讯",
    "资产分析", "首页", "行情", "自选", "交易", "资讯", "理财", "人民币账户A股",
    "持仓/可用", "成本/现价", "名称", "代码", "数量", "参考市值", "现价/成本",
}

# 名称 → 代码 的进程内缓存（取一次全表，失败则保持 None 不重试）
_NAME_MEMO: Dict[str, Optional[str]] = {}

# OCR 引擎（ONNX 模型，实测常驻 +350 MB）不用时自动卸载：空闲超过该秒数即释放。
# 0 表示不自动卸载。下次发图会重新加载（冷启动约 10-30 s，视机器而定）。
OCR_IDLE_UNLOAD_SECONDS = int(os.getenv("OCR_IDLE_UNLOAD_SECONDS", "180"))
_OCR_WATCHDOG_INTERVAL = 30
# 子进程 OCR：模型跑完即退出，内存全归还（常驻进程不再背 ~350 MB）。
# Linux 默认开；Windows 默认关（开发机免去每次起进程的开销）。
OCR_SUBPROCESS = os.getenv(
    "OCR_SUBPROCESS", "1" if os.name == "posix" else "0"
).strip().lower() not in ("0", "false", "no", "off")
OCR_SUBPROCESS_TIMEOUT = int(os.getenv("OCR_SUBPROCESS_TIMEOUT", "300"))
# 名称 → 裸码 全表缓存（优先 TDX，其次 akshare；落盘避免每次重取）
_NAME_MAP: Dict[str, str] = {}
_NAME_MAP_PATH = Path("data/stock_name_cache.json")


@dataclass(frozen=True)
class HoldingItem:
    """Parsed holding from OCR screenshot."""
    name: str           # 股票中文名
    quantity: int       # 持仓数量
    cost_price: float   # 成本价
    current_price: float  # 现价
    symbol: Optional[str] = None  # 6 位裸码（与 portfolio_positions 存储一致）


class PortfolioOcrImportService:
    """OCR screenshot → holdings → trade events."""

    # A 股代码前缀：000/001/002/003(深主板)、300/301/302(创业)、600/601/603/605(沪)、688/689(科创)、8xx/9xx(北交)
    _CODE_PREFIXES = (
        "000", "001", "002", "003", "004",
        "300", "301", "302",
        "600", "601", "603", "605",
        "688", "689",
        "830", "831", "832", "833", "834", "835", "836", "837", "838", "839",
        "870", "871", "872", "873", "920",
    )

    def __init__(self, prefer_subprocess: Optional[bool] = None):
        self._ocr = None
        self._backend: Optional[str] = None
        self._prefer_subprocess = (
            OCR_SUBPROCESS if prefer_subprocess is None else bool(prefer_subprocess)
        )
        self._ocr_lock = threading.RLock()
        self._last_used = 0.0
        self._watchdog: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()

    def _load_backend(self) -> str:
        """选定 OCR 后端：paddle 优先，缺失时退到 rapidocr（ONNX 版 PP-OCR）。

        Python 3.14 上 paddlepaddle 无 wheel（`pip index versions paddlepaddle` → No matching），
        因此服务器实际走 rapidocr；两者都是 PP-OCR 模型，输出框格式统一在本类里归一化。
        """
        if self._backend is not None:
            return self._backend
        try:
            import paddleocr  # noqa: F401
            self._backend = "paddle"
        except Exception:  # noqa: BLE001 - 缺失或二进制不兼容都退级
            try:
                import rapidocr_onnxruntime  # noqa: F401
                self._backend = "rapidocr"
            except Exception as exc:  # noqa: BLE001
                raise ImportError(
                    "未安装 OCR 后端。paddlepaddle 在 Python 3.14 无 wheel，请装 rapidocr："
                    " pip install rapidocr-onnxruntime（另需 apt install libgl1）"
                ) from exc
        logger.info("[OCR] 后端: %s", self._backend)
        return self._backend

    def release_engine(self) -> bool:
        """卸载 OCR 引擎并回收内存；返回是否真的释放了。"""
        with self._ocr_lock:
            if self._ocr is None:
                return False
            self._ocr = None
        gc.collect()
        # glibc 会把释放的内存留在 arena 里，主动 trim 才能真正还给 OS（Linux）
        try:
            import ctypes

            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass
        logger.info("[OCR] 引擎已卸载（空闲 %.0fs，阈值 %ss）",
                    time.time() - self._last_used, OCR_IDLE_UNLOAD_SECONDS)
        return True

    def _ensure_watchdog(self) -> None:
        if OCR_IDLE_UNLOAD_SECONDS <= 0:
            return
        if self._watchdog and self._watchdog.is_alive():
            return
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name="OcrIdleUnload", daemon=True
        )
        self._watchdog.start()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(_OCR_WATCHDOG_INTERVAL):
            if self._ocr is None:
                continue
            idle = time.time() - self._last_used
            if idle >= OCR_IDLE_UNLOAD_SECONDS:
                self.release_engine()

    def _get_ocr(self):
        """Lazy-load OCR engine."""
        self._last_used = time.time()
        self._ensure_watchdog()
        if self._ocr is None:
            backend = self._load_backend()
            if backend == "paddle":
                from paddleocr import PaddleOCR
                try:
                    self._ocr = PaddleOCR(lang='ch')
                except TypeError:
                    self._ocr = PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)
            else:
                from rapidocr_onnxruntime import RapidOCR
                self._ocr = RapidOCR()
        return self._ocr

    def _ocr_boxes_in_subprocess(self, image_path: str) -> List[Dict[str, Any]]:
        """在子进程里跑 OCR；失败抛异常由调用方回退到进程内。"""
        import subprocess

        env = dict(os.environ)
        env["OCR_SUBPROCESS"] = "0"  # 防递归
        root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join(
            [root, env.get("PYTHONPATH", "")] if env.get("PYTHONPATH") else [root]
        )
        completed = subprocess.run(
            [sys.executable, "-m", "src.services.ocr_worker", image_path],
            capture_output=True,
            text=True,
            timeout=OCR_SUBPROCESS_TIMEOUT,
            env=env,
            cwd=root,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or "").strip()[:300] or f"ocr_worker exit {completed.returncode}"
            )
        payload = json.loads(completed.stdout.strip() or "{}")
        boxes = payload.get("boxes")
        if not isinstance(boxes, list):
            raise RuntimeError("ocr_worker 输出缺少 boxes")
        logger.info("[OCR] 子进程识别完成: %d 个文本框", len(boxes))
        return boxes

    def ocr_boxes(self, image_path: str, min_score: float = 0.5) -> List[Dict[str, Any]]:
        """OCR → 归一化文本框列表 [{text, score, x0, y0, x1, y1}]，按 (y, x) 排序。"""
        if self._prefer_subprocess:
            try:
                return self._ocr_boxes_in_subprocess(image_path)
            except Exception as exc:
                logger.warning("[OCR] 子进程不可用，改用进程内: %s", str(exc)[:120])

        return self._ocr_boxes_inprocess(image_path, min_score)

    def _ocr_boxes_inprocess(self, image_path: str, min_score: float = 0.5) -> List[Dict[str, Any]]:
        backend = self._load_backend()
        with self._ocr_lock:
            ocr = self._get_ocr()
            self._last_used = time.time()
        boxes: List[Dict[str, Any]] = []

        if backend == "paddle":
            try:
                result = ocr.ocr(image_path)
            except Exception:
                result = ocr.predict(image_path)
            if isinstance(result, list) and result and result[0]:
                for line in result[0]:
                    bbox, (text, score) = line
                    if float(score) >= min_score:
                        xs = [p[0] for p in bbox]
                        ys = [p[1] for p in bbox]
                        boxes.append({
                            "text": text, "score": float(score),
                            "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
                        })
            elif isinstance(result, dict):
                for text, score, poly in zip(
                    result.get("rec_texts", []), result.get("rec_scores", []), result.get("rec_polys", [])
                ):
                    if float(score) >= min_score:
                        xs = [p[0] for p in poly]
                        ys = [p[1] for p in poly]
                        boxes.append({
                            "text": text, "score": float(score),
                            "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
                        })
        else:
            result, _elapse = ocr(str(image_path))
            for item in result or []:
                bbox, text, score = item[0], item[1], item[2]
                if float(score) >= min_score:
                    xs = [p[0] for p in bbox]
                    ys = [p[1] for p in bbox]
                    boxes.append({
                        "text": text, "score": float(score),
                        "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
                    })

        boxes.sort(key=lambda b: (b["y0"], b["x0"]))
        return boxes

    def ocr_screenshot(self, image_path: str) -> str:
        """Run OCR on screenshot, return raw text（按视觉顺序逐行拼接）。"""
        return "\n".join(b["text"] for b in self.ocr_boxes(image_path))

    @classmethod
    def _is_stock_code(cls, token: str) -> bool:
        return len(token) == 6 and token.isdigit() and token.startswith(cls._CODE_PREFIXES)

    @staticmethod
    def _row_split(boxes: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """按 y 中心聚类成行（容差取中位行高的 0.6 倍），行内按 x 排序。"""
        if not boxes:
            return []
        heights = sorted(b["y1"] - b["y0"] for b in boxes)
        median_h = heights[len(heights) // 2] or 1.0
        tol = max(6.0, median_h * 0.6)

        rows: List[List[Dict[str, Any]]] = []
        for box in sorted(boxes, key=lambda b: (b["y0"] + b["y1"]) / 2):
            center = (box["y0"] + box["y1"]) / 2
            if rows:
                last = rows[-1]
                last_center = sum((b["y0"] + b["y1"]) / 2 for b in last) / len(last)
                if abs(center - last_center) <= tol:
                    last.append(box)
                    continue
            rows.append([box])
        return [sorted(row, key=lambda b: b["x0"]) for row in rows]

    @staticmethod
    def _number_variants(token: str) -> List[float]:
        """把 OCR 数字串解析成候选值。

        券商截图的千分位常被 OCR 读成 '.'（`68.484.00`、`-2.942.86`），而价格又可能是
        `54.945` 这种三位小数，单看一个 token 无法确定语义，因此返回全部合理读法，
        由 `qty × (price - cost) ≈ pnl` 的恒等式挑选。
        """
        raw = str(token).strip().replace("，", ",").replace(" ", "")
        raw = raw.replace("%", "").replace("+", "")
        if not raw or not re.fullmatch(r"-?\d[\d.,]*", raw):
            return []
        neg = raw.startswith("-")
        body = raw.lstrip("-")
        variants: List[float] = []

        # 千分位（一个或多个 . / , 分隔的三位组），可能再带两位小数
        m = re.fullmatch(r"(\d{1,3}(?:[.,]\d{3})+)(?:[.,](\d{1,2}))?", body)
        if m:
            int_part = re.sub(r"[.,]", "", m.group(1))
            frac = m.group(2) or ""
            variants.append(float(int_part + ("." + frac if frac else "")))
            # 单个分隔符 + 三位尾数既是千分位也是三位小数（价格常见 54.945 / 11.200）
            if body.count(".") + body.count(",") == 1:
                variants.append(float(body.replace(",", ".")))
        else:
            m2 = re.fullmatch(r"(\d+)(?:[.,](\d+))?", body)
            if m2:
                int_part, frac = m2.group(1), m2.group(2)
                if frac is None:
                    variants.append(float(int_part))
                else:
                    variants.append(float(f"{int_part}.{frac}"))
                    # 三位尾数也可能其实是千分位
                    if len(frac) == 3 and len(int_part) <= 3:
                        variants.append(float(int_part + frac))

        seen: List[float] = []
        for value in variants:
            if value not in seen:
                seen.append(-value if neg else value)
        return seen

    @staticmethod
    def _bare_code(code: str) -> str:
        """SH600519 / 600519.SH / 600519 → 600519（positions 表存裸码）。"""
        digits = re.sub(r"\D", "", str(code))
        return digits[-6:] if len(digits) >= 6 else str(code).upper()

    def _load_name_map(self) -> Dict[str, str]:
        """全量名称→裸码表：磁盘缓存 → TDX(mootdx) → akshare 三级。"""
        global _NAME_MAP
        if _NAME_MAP:
            return _NAME_MAP

        try:
            import json as _json

            if _NAME_MAP_PATH.exists():
                cached = _json.loads(_NAME_MAP_PATH.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached:
                    _NAME_MAP = {str(k): str(v) for k, v in cached.items()}
                    logger.info("[OCR] 名称库命中磁盘缓存 %d 条", len(_NAME_MAP))
                    return _NAME_MAP
        except Exception as exc:
            logger.debug("[OCR] 名称库缓存读取失败: %s", type(exc).__name__)

        mapping: Dict[str, str] = {}
        try:  # TDX TCP：复用 MootdxFetcher 的验活/故障转移节点池，不受 HTTP 反爬影响
            from data_provider.mootdx_fetcher import MootdxFetcher

            with MootdxFetcher()._mootdx_session() as client:
                frame = client.stock_all()
            for row in frame.to_dict("records"):
                code = str(row.get("code") or "")
                name = str(row.get("name") or "").replace("\u0000", "").strip()
                if len(code) == 6 and code.isdigit() and name:
                    mapping.setdefault(name, code)
            logger.info("[OCR] 名称库来自 TDX，共 %d 条", len(mapping))
        except Exception as exc:
            logger.warning("[OCR] TDX 名称库失败: %s: %s", type(exc).__name__, str(exc)[:80])

        if not mapping:
            try:  # akshare 兜底（HTTP，可能被限流/断连）
                import akshare as ak

                frame = ak.stock_zh_a_spot_em()
                for row in frame.to_dict("records"):
                    name = str(row.get("名称") or "").strip()
                    code = str(row.get("代码") or "").strip()
                    if len(code) == 6 and code.isdigit() and name:
                        mapping.setdefault(name, code)
                logger.info("[OCR] 名称库来自 akshare，共 %d 条", len(mapping))
            except Exception as exc:
                logger.warning("[OCR] akshare 名称库失败: %s", type(exc).__name__)

        if mapping:
            _NAME_MAP = mapping
            try:
                import json as _json

                _NAME_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
                _NAME_MAP_PATH.write_text(
                    _json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
                )
            except Exception as exc:
                logger.debug("[OCR] 名称库缓存写入失败: %s", type(exc).__name__)
        return _NAME_MAP

    def _resolve_symbol(self, name: str) -> Optional[str]:
        """名称 → 裸码：全量表精确匹配 → 模糊匹配 → akshare 单查兜底。"""
        if name in _NAME_MEMO:
            return _NAME_MEMO[name]

        code: Optional[str] = None
        name_map = self._load_name_map()
        if name in name_map:
            code = name_map[name]
        elif name_map:
            from difflib import SequenceMatcher

            best_name, best_score = None, 0.0
            for candidate in name_map:
                score = SequenceMatcher(None, name, candidate).ratio()
                if score > best_score:
                    best_name, best_score = candidate, score
            if best_name and best_score >= 0.85:
                code = name_map[best_name]
                logger.info("[OCR] 名称模糊匹配 %s → %s (%.2f)", name, best_name, best_score)

        if code is None:
            # 项目内既有解析器：本地 STOCK_NAME_MAP 反查 + 拼音 + 自带 akshare 缓存，
            # 与 forum_sentiment / import_parser 同一套口径；命名未命中才现查东财。
            try:
                from src.services.name_to_code_resolver import resolve_name_to_code

                code = resolve_name_to_code(name)
            except Exception as exc:
                logger.debug("[OCR] 项目解析器未命中 %s: %s", name, type(exc).__name__)

        if code is None:
            try:
                code = self.match_stock_name(name)
            except Exception as exc:  # 离线/限流都不阻塞主流程
                logger.warning("[OCR] 名称匹配失败 %s: %s", name, type(exc).__name__)

        code = self._bare_code(code) if code else None
        _NAME_MEMO[name] = code
        return code

    def _parse_two_row_layout(self, boxes: List[Dict[str, Any]]) -> List[HoldingItem]:
        """解析「一只票横跨两行」的 APP 版面（中信证券等）。

        形如：
            市值 | 盈亏 | 持仓/可用 | 成本/现价      ← 表头
            润建股份 | -2.942.86 | 1300 | 54.945     ← 名称/盈亏/数量/成本
            68.484.00 | 4.120% | 1300 | 52.680       ← 市值/涨幅/可用/现价

        该版面**没有股票代码列**，名称需另行解析；数量在上下两行重复出现，
        成本在上行、现价在下行，用 `qty × (现价 - 成本) ≈ 盈亏` 校验并挑选读数。
        """
        rows = self._row_split(boxes)
        holdings: List[HoldingItem] = []

        for index, row in enumerate(rows[:-1]):
            tokens = [b["text"].strip() for b in row if str(b["text"]).strip()]
            names = [
                t for t in tokens
                if re.fullmatch(r"[\u4e00-\u9fff]{2,8}", t) and t not in _HEADER_WORDS
            ]
            if len(names) != 1:
                continue
            name = names[0]

            upper = [self._number_variants(t) for t in tokens if self._number_variants(t)]
            lower_texts = [b["text"].strip() for b in rows[index + 1] if str(b["text"]).strip()]
            lower = [self._number_variants(t) for t in lower_texts if self._number_variants(t)]
            if len(upper) < 2 or len(lower) < 2:
                continue

            best: Optional[Tuple[float, int, float, float, float]] = None
            for qty_variants in upper:
                for qty in qty_variants:
                    if qty <= 0 or qty != int(qty) or qty > 100_000_000:
                        continue
                    # 数量通常上下两行重复；但 T+1 买入当日「可用」为空，
                    # 下行会缺数量列（2026-10-28 天奇股份 2000 股实测）。
                    # 此时用下行市值 ≈ 数量×现价 兜底校验，避免整行丢失。
                    qty_in_lower = any(qty in variants for variants in lower)
                    lower_values = [v for variants in lower for v in variants]
                    for cost in [v for variants in upper for v in variants if v != qty]:
                        for price in [v for variants in lower for v in variants if v != qty]:
                            if cost <= 0 or price <= 0:
                                continue
                            mkt_hit = any(
                                m > 0 and abs(qty * price - m) / max(m, 1.0) <= 0.02
                                for m in lower_values
                            )
                            if not (qty_in_lower or mkt_hit):
                                continue
                            for pnl in [v for variants in upper for v in variants
                                        if v not in (qty, cost)]:
                                residual = abs(qty * (price - cost) - pnl)
                                tolerance = max(5.0, abs(pnl) * 0.02, qty * max(cost, price) * 0.002)
                                if residual <= tolerance and (best is None or residual < best[0]):
                                    best = (residual, int(qty), cost, price, pnl)

            if best is None:
                logger.warning("[OCR] 两行版面校验失败，跳过: %s", " | ".join(tokens))
                continue

            _, quantity, cost_price, current_price, _pnl = best
            holdings.append(HoldingItem(
                name=name,
                quantity=quantity,
                cost_price=round(cost_price, 3),
                current_price=round(current_price, 3),
                symbol=self._resolve_symbol(name),
            ))
            logger.info(
                "[OCR] 两行版面命中: %s qty=%s cost=%.3f price=%.3f",
                name, quantity, cost_price, current_price,
            )

        return holdings

    def parse_holdings_from_boxes(self, boxes: List[Dict[str, Any]]) -> List[HoldingItem]:
        """按版面（行/列语义）解析持仓，避免把 6 位股票代码当数量。

        旧 `parse_holdings_from_ocr` 的正则要求「名称 数量 成本 现价」紧邻，遇到券商常见的
        「名称 000001 1000 11.20 11.35」会把 `000001` 当数量（int→1 股）、成本变 1000，静默毁数据。
        这里按语义取列：名称=CJK 串、代码=6 位且前缀合法、数量=整数、价格=小数；
        并用「数量×现价≈市值」在存在市值列时做自检。
        """
        holdings: List[HoldingItem] = []
        pending_name: Optional[str] = None

        for row in self._row_split(boxes):
            tokens = [b["text"].strip() for b in row if b["text"].strip()]
            if not tokens:
                continue

            name = next((t for t in tokens if re.fullmatch(r"[一-鿿]{2,8}", t)), None)
            code = next((t for t in tokens if self._is_stock_code(t)), None)
            floats = [
                float(t.replace(",", ""))
                for t in tokens
                if re.fullmatch(r"\d[\d,]*\.\d+", t)
            ]
            ints = [
                int(t.replace(",", ""))
                for t in tokens
                if re.fullmatch(r"\d{1,7}", t) and not self._is_stock_code(t)
            ]
            money = next(
                (float(t.replace(",", "")) for t in tokens if re.fullmatch(r"\d{1,3}(,\d{3})+\.\d{2}", t)),
                None,
            )

            if name and not (floats or ints):
                # 只有名称的行：可能是「名称/代码」行，数字在下一行
                pending_name = name
                continue

            if name is None and pending_name:
                name = pending_name

            if not name:
                continue

            quantity = next((v for v in ints if 1 <= v <= 10_000_000), None)
            cost_price = floats[0] if floats else None
            current_price = floats[1] if len(floats) > 1 else None

            if quantity is None or cost_price is None or current_price is None:
                logger.warning("[OCR] 行内字段不全，跳过: %s", " | ".join(tokens))
                pending_name = None
                continue

            if money is not None:
                expected = quantity * current_price
                if expected > 0 and abs(expected - money) / max(money, 1.0) > 0.05:
                    logger.warning(
                        "[OCR] 数量×现价与市值不符（%s vs %s），按版面列取值: %s",
                        f"{expected:.2f}", f"{money:.2f}", " | ".join(tokens),
                    )

            holdings.append(HoldingItem(
                name=name,
                quantity=quantity,
                cost_price=cost_price,
                current_price=current_price,
                # 存裸码：`record_trade` 落库走 canonical_stock_code（只做 upper），
                # portfolio_positions 的键就是 `600519` 这种形态；匹配函数给的是 `SH600519`，
                # 两者在 compute_sync_trades 里由 _symbol_key 归一化后比较。
                symbol=code,
            ))
            pending_name = None

        # 无代码列、且一只票横跨两行的券商版面（中信证券等）。
        # 不与主解析二选一：同页可能既有「单行带代码」也有「两行无代码」，
        # 两行解析带恒等式校验（qty×(现价-成本)≈盈亏），命中时覆盖同名结果。
        fallback = self._parse_two_row_layout(boxes)
        if not fallback:
            return holdings

        # 以两行解析（强校验）为基准，主解析仅补齐它没覆盖的名称，保持页面顺序
        merged: Dict[str, HoldingItem] = {item.name: item for item in fallback}
        for item in holdings:
            merged.setdefault(item.name, item)
        logger.info(
            "[OCR] 版面合并: 主解析 %d 条 + 两行解析 %d 条 → %d 条",
            len(holdings), len(fallback), len(merged),
        )
        return list(merged.values())


    def parse_holdings(
        self,
        *,
        image_path: Optional[str] = None,
        boxes: Optional[List[Dict[str, Any]]] = None,
        ocr_text: Optional[str] = None,
    ) -> List[HoldingItem]:
        """统一入口：有坐标就按版面解析，否则退回旧文本正则。

        券商 APP 截图普遍带「代码」列，走 `parse_holdings_from_ocr` 的纯文本正则会把它当数量；
        因此默认路径是 boxes（`ocr_boxes()` 的产物）。文本路径保留只为兼容无坐标的老数据。
        """
        if boxes is None and image_path is not None:
            boxes = self.ocr_boxes(image_path)
        if boxes is not None:
            return self.parse_holdings_from_boxes(boxes)
        if ocr_text:
            logger.warning("[OCR] 无坐标信息，退回旧文本正则（含代码列的多列截图可能误读）")
            return self.parse_holdings_from_ocr(ocr_text)
        raise ValueError("parse_holdings 需要 image_path / boxes / ocr_text 之一")

    def parse_holdings_from_ocr(self, ocr_text: str) -> List[HoldingItem]:
        """Parse OCR text into structured holdings.

        ⚠️ 仅用于无坐标的纯文本。`pattern1` 是「名称 数量 成本 现价」紧邻匹配，遇到
        「平安银行 000001 1000 11.20 11.35」会把 `000001` 当数量（int→1 股、成本变 1000）。
        带代码列的截图必须走 `parse_holdings_from_boxes`。

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

    @staticmethod
    def _symbol_key(symbol: str) -> str:
        """比较用键：直接用仓内权威归一化 `normalize_stock_code`。

        持仓表里的键是裸码（`record_trade` → `canonical_stock_code` 只做 upper，
        见 portfolio_service.py:195/1156），而 `match_stock_name()` 返回 `SH600519`。
        直接按字符串做集合差会把同一只票同时判成「新增买入」和「清仓卖出」。
        `normalize_stock_code` 同时覆盖 `SH600519` / `600519.SH` / `BJ920748` / `HK00700`，
        自写前缀表容易漏北交与港美股形态。
        """
        from data_provider.base import normalize_stock_code

        return normalize_stock_code(symbol)

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
        current_by_key = {
            self._symbol_key(h.symbol): h for h in current_holdings if h.symbol
        }
        system_by_key = {
            self._symbol_key(sym): (sym, pos) for sym, pos in system_positions.items()
        }

        # New stocks in screenshot (not in system)
        for key, h in current_by_key.items():
            if key not in system_by_key:
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
        for key, (sym, pos) in system_by_key.items():
            if key in current_by_key:
                continue
            last_price = float(pos.get('last_price') or 0.0)
            note = f'OCR同步清仓-{sym}'
            if last_price <= 0:
                # 截图里已消失，拿不到现价时退回成本价（盈亏按 0 计），
                # 否则该票会永久留在系统持仓里，越积越多。
                fallback = float(pos.get('avg_cost') or pos.get('cost_price') or 0.0)
                if fallback <= 0:
                    logger.warning(
                        "[OCR] 清仓行既无现价也无成本（%s），跳过并留待人工处理", sym
                    )
                    continue
                last_price = fallback
                note = f'OCR同步清仓-{sym}（缺现价，按成本价）'
                logger.warning(
                    "[OCR] 清仓行缺现价（%s），按成本价 %.3f 生成卖出", sym, fallback
                )
            trades.append({
                'symbol': sym,
                'trade_date': trade_date,
                'side': 'sell',
                'quantity': float(pos['quantity']),
                'price': last_price,
                'fee': 0.0,
                'tax': 0.0,
                'note': note,
            })

        # Quantity changes
        for key, h in current_by_key.items():
            if key in system_by_key:
                sys_qty = system_by_key[key][1]['quantity']
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
