# -*- coding: utf-8 -*-
"""
GOT 指数注册表 + 身份解析（docs/archive/got/agent_glue_plan_0903.md §10 对应 remote_integration_plan.md §4（同目录））

解决 `000001` 等裸码的指数/股票双身份歧义：
  - `mood_index_code="000001"` → 语义为上证指数
  - `stock_mapping["000001"]` → 平安银行

入口：`parse_analysis_target(raw_input)` → `AnalysisTarget`

冲突处理：裸码默认解析为股票，只有显式前缀（`sh000001` / `000001.SH`）
才解析为指数。这与 Node 1 `INDEX_CONFIG` 已使用带后缀代码的设计一致。
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 注册表默认路径
_DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "got_index_registry.json"


@dataclass(frozen=True)
class IndexEntry:
    """指数条目。"""
    bare_code: str           # "000001"
    exchange: str            # "SH" / "SZ"
    canonical_id: str        # "sh000001"
    display_name: str        # "上证指数"
    aliases: Tuple[str, ...] = ()

    @property
    def suffixed_code(self) -> str:
        """带交易所后缀的代码，如 '000001.SH'。"""
        return f"{self.bare_code}.{self.exchange}"


@dataclass(frozen=True)
class AnalysisTarget:
    """解析后的分析目标。"""
    raw_input: str
    asset_type: str           # "stock" | "index" | "unsupported"
    canonical_id: str         # "sh000001" / "600519.SH"
    exchange: str             # "SH" / "SZ" / ""
    matched_index: Optional[IndexEntry] = None

    @property
    def is_index(self) -> bool:
        return self.asset_type == "index"

    @property
    def is_stock(self) -> bool:
        return self.asset_type == "stock"


class IndexRegistry:
    """指数注册表（按 canonical_id / bare_code / 别名 / 中文名查找）。"""

    def __init__(self, registry_path: Optional[Path] = None):
        path = registry_path or _DEFAULT_REGISTRY_PATH
        self._entries: List[IndexEntry] = []
        self._by_canonical: Dict[str, IndexEntry] = {}
        self._by_suffixed: Dict[str, IndexEntry] = {}
        self._by_bare: Dict[str, List[IndexEntry]] = {}
        self._by_alias: Dict[str, IndexEntry] = {}
        self._by_name: Dict[str, IndexEntry] = {}
        self._load(path)

    def _load(self, path: Path) -> None:
        if not path.exists():
            logger.warning(f"[IndexRegistry] 注册表文件不存在: {path}")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except Exception as e:
            logger.warning(f"[IndexRegistry] 注册表解析失败: {e}")
            return

        seen_canonical: set = set()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 8:
                continue
            canonical_id, _, name_zh, pinyin_full, pinyin_abbr, aliases, market, asset_type = row[:8]
            if asset_type != "index" or market != "CN":
                continue
            if canonical_id in seen_canonical:
                continue
            seen_canonical.add(canonical_id)

            # 从 canonical_id 解析 bare_code + exchange
            bare, exchange = _parse_canonical_id(canonical_id)
            if not bare:
                continue

            # 合并 aliases + pinyin_full + pinyin_abbr 为统一别名片
            all_aliases: list[str] = []
            if isinstance(aliases, list):
                all_aliases.extend(str(a) for a in aliases)
            if isinstance(pinyin_full, str) and pinyin_full:
                all_aliases.append(pinyin_full)
            if isinstance(pinyin_abbr, str) and pinyin_abbr:
                all_aliases.append(pinyin_abbr)

            entry = IndexEntry(
                bare_code=bare,
                exchange=exchange,
                canonical_id=canonical_id,
                display_name=name_zh,
                aliases=tuple(all_aliases),
            )
            self._entries.append(entry)
            self._by_canonical[canonical_id] = entry
            self._by_suffixed[entry.suffixed_code] = entry
            self._by_bare.setdefault(bare, []).append(entry)
            for alias in entry.aliases:
                self._by_alias[alias.lower()] = entry
            self._by_name[name_zh] = entry

        logger.debug(f"[IndexRegistry] 加载 {len(self._entries)} 个指数")

    # ── 查找方法 ──

    def get_by_canonical(self, canonical_id: str) -> Optional[IndexEntry]:
        return self._by_canonical.get(canonical_id)

    def get_by_suffixed(self, suffixed_code: str) -> Optional[IndexEntry]:
        """按 '000001.SH' 格式查找。"""
        return self._by_suffixed.get(suffixed_code.upper())

    def get_by_bare(self, bare_code: str) -> List[IndexEntry]:
        """按裸码查找（可能返回多个，因 SH/SZ 均有同名裸码）。"""
        return self._by_bare.get(bare_code, [])

    def get_by_alias(self, alias: str) -> Optional[IndexEntry]:
        return self._by_alias.get(alias.lower())

    def get_by_name(self, name: str) -> Optional[IndexEntry]:
        return self._by_name.get(name)

    def all_entries(self) -> List[IndexEntry]:
        return list(self._entries)


def _parse_canonical_id(canonical_id: str) -> Tuple[str, str]:
    """解析 canonical_id → (bare_code, exchange)。

    Examples:
        "sh000001" → ("000001", "SH")
        "sz399006" → ("399006", "SZ")
    """
    m = re.match(r'^(sh|sz|bj)(\d+)$', canonical_id.lower())
    if m:
        return m.group(2), m.group(1).upper()
    return "", ""


# ── 全局单例 ──

_REGISTRY: Optional[IndexRegistry] = None


def get_registry() -> IndexRegistry:
    """获取全局 IndexRegistry 单例。"""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = IndexRegistry()
    return _REGISTRY


def parse_analysis_target(raw_input: str) -> AnalysisTarget:
    """解析用户输入，判定资产类型并消歧。

    冲突处理：
    - 裸码（如 "000001"）默认解析为**股票**
    - 带交易所前缀（"sh000001"）或后缀（"000001.SH"）才解析为**指数**
    - 中文名（"上证指数"）/ 别名（"szzs"）解析为**指数**
    """
    raw = raw_input.strip()
    if not raw:
        return AnalysisTarget(
            raw_input=raw, asset_type="unsupported",
            canonical_id="", exchange="",
        )

    registry = get_registry()
    upper = raw.upper()

    # ── 1. 中文名 / 别名 → 指数 ──
    entry = registry.get_by_name(raw) or registry.get_by_alias(raw)
    if entry:
        return AnalysisTarget(
            raw_input=raw, asset_type="index",
            canonical_id=entry.canonical_id, exchange=entry.exchange,
            matched_index=entry,
        )

    # ── 2. 带后缀的代码（000001.SH / 399006.SZ）──
    suffixed_match = re.match(r'^(\d{6})\.(SH|SZ|BJ|SS)$', upper)
    if suffixed_match:
        bare, suffix = suffixed_match.group(1), suffixed_match.group(2)
        if suffix == "SS":
            suffix = "SH"
        # 查注册表
        entry = registry.get_by_suffixed(f"{bare}.{suffix}")
        if entry:
            return AnalysisTarget(
                raw_input=raw, asset_type="index",
                canonical_id=entry.canonical_id, exchange=entry.exchange,
                matched_index=entry,
            )
        # 未在注册表中，按代码推断资产类型
        exchange = suffix
        return AnalysisTarget(
            raw_input=raw, asset_type="stock",
            canonical_id=f"{bare}.{exchange}", exchange=exchange,
        )

    # ── 3. 带前缀的代码（sh000001 / sz399006）──
    prefix_match = re.match(r'^(SH|SZ|BJ)(\d{6})$', upper)
    if prefix_match:
        prefix, bare = prefix_match.group(1), prefix_match.group(2)
        canonical = f"{prefix.lower()}{bare}"
        entry = registry.get_by_canonical(canonical)
        if entry:
            return AnalysisTarget(
                raw_input=raw, asset_type="index",
                canonical_id=entry.canonical_id, exchange=entry.exchange,
                matched_index=entry,
            )
        # 未在注册表，按代码推断
        return AnalysisTarget(
            raw_input=raw, asset_type="stock",
            canonical_id=f"{bare}.{prefix}", exchange=prefix,
        )

    # ── 4. 裸码（000001 / 600519）──
    bare_match = re.match(r'^(\d{6})$', raw)
    if bare_match:
        bare = bare_match.group(1)
        # 裸码默认解析为**股票**（消歧关键）
        if bare.startswith(("6", "9")):
            exchange = "SH"
        elif bare.startswith(("0", "3")):
            exchange = "SZ"
        elif bare.startswith("4") or bare.startswith("8"):
            exchange = "BJ"
        else:
            exchange = ""
        return AnalysisTarget(
            raw_input=raw, asset_type="stock",
            canonical_id=f"{bare}.{exchange}" if exchange else bare,
            exchange=exchange,
        )

    # ── 5. 美股 ticker / 其他 ──
    if re.match(r'^[A-Z]{1,5}$', upper):
        return AnalysisTarget(
            raw_input=raw, asset_type="stock",
            canonical_id=upper, exchange="",
        )

    return AnalysisTarget(
        raw_input=raw, asset_type="unsupported",
        canonical_id="", exchange="",
    )


def matches_index_code(idx_code: str, target_code: str) -> bool:
    """判断 idx_code 是否与 target_code 指向同一指数（忽略格式差异）。

    用于消费方匹配 `mood_index_code`（已消歧为带后缀格式）与
    `MarketIndex.code`（可能是 sh000001 / 000001 / 000001.SH 等格式）。

    本函数专用于**指数匹配语境**（idx_code 来自指数列表），故裸码按指数解释：
    当一方为裸码（交易所未知）时，仅比较 6 位数字核心；双方都显式标注交易所时
    才额外要求交易所一致。注意这与 `parse_analysis_target` 的「裸码→股票」消歧规则
    是不同语境（后者用于解析用户输入的资产类型）。

    Examples:
        matches_index_code("sh000001", "000001.SH") → True
        matches_index_code("000001", "000001.SH") → True   # 裸码来自指数列表 = 上证指数
        matches_index_code("sh000001", "sh000001") → True
        matches_index_code("sz399006", "399006.SZ") → True
        matches_index_code("sh000001", "sh000300") → False
    """
    # 拆分为 (6 位数字核心, 交易所)；裸码交易所为空串（未知）
    def _split(code: str):
        c = code.strip().lower()
        m = re.match(r'^(sh|sz|bj)(\d{6})$', c)
        if m:
            return m.group(2), m.group(1)
        m = re.match(r'^(\d{6})\.(sh|sz|bj|ss)$', c)
        if m:
            return m.group(1), ("sh" if m.group(2) == "ss" else m.group(2))
        m = re.match(r'^(\d{6})$', c)
        if m:
            return m.group(1), ""
        return c, ""

    digits_a, ex_a = _split(idx_code)
    digits_b, ex_b = _split(target_code)
    if not digits_a.isdigit() or digits_a != digits_b:
        return False
    # 数字核心相同；仅当双方都显式标注交易所时才要求交易所一致
    if ex_a and ex_b:
        return ex_a == ex_b
    return True
