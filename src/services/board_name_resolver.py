# -*- coding: utf-8 -*-
"""
板块名解析器（TDX 板块名 → 问财可识别的同花顺板块名）

背景：
- 系统板块名来自 TDX（通达信），GBK 定长字段导致名称常被截断/带 \\u0000 填充
  （如 "白酒\\u0000..."、"碳纤维概"）
- 问财查询硬拼 "{name}概念板块成分股"，只认同花顺官方板块名
  （如 TDX "生物制药" 对应 THS "生物医药"），名字对不上直接返回 0 只

三层机制：
- L0: clean_board_name() 清洗 \\u0000/首尾空白（上游入口也应调用）
- L1: 静态别名表（data/wencai_board_aliases.json，人工实测验证，最高优先级）
- L2: 问财查空时，用同花顺官方概念板块列表模糊匹配候选名；
      查询命中后由调用方 confirm() 回写别名表（标记 auto_matched），次日直接命中

使用方式：
    resolver = get_board_name_resolver()
    board_name = resolver.resolve(sector_name)   # 查询前
    ... 问财查询 board_name ...
    if 查询命中:
        resolver.confirm(sector_name, board_name)  # 模糊匹配命中才需回写
"""

from __future__ import annotations

import difflib
import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ALIAS_PATH = _PROJECT_ROOT / "data" / "wencai_board_aliases.json"
THS_BOARD_CACHE_PATH = _PROJECT_ROOT / "data" / "ths_concept_boards.json"

# L2 模糊匹配置信度阈值（低于该值不产出候选）
FUZZY_THRESHOLD = 0.68


def clean_board_name(name: str) -> str:
    """清洗板块名：去除 \\u0000 填充符与首尾空白（TDX GBK 定长字段常见脏字符）"""
    if not name:
        return ""
    return name.replace("\u0000", "").strip()


class BoardNameResolver:
    """板块名解析器（线程安全，进程内单例复用）"""

    def __init__(self, alias_path: Optional[Path] = None):
        self._alias_path = Path(alias_path) if alias_path else DEFAULT_ALIAS_PATH
        self._lock = threading.Lock()
        self._aliases: Optional[Dict[str, dict]] = None
        self._ths_names: Optional[List[str]] = None

    # ── L1: 静态别名表 ──

    def _load_aliases(self) -> Dict[str, dict]:
        if self._aliases is not None:
            return self._aliases
        aliases: Dict[str, dict] = {}
        try:
            if self._alias_path.exists():
                with open(self._alias_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for name, entry in raw.get("aliases", {}).items():
                    if isinstance(entry, str):
                        entry = {"target": entry}
                    target = clean_board_name(entry.get("target", ""))
                    if target:
                        aliases[clean_board_name(name)] = {
                            "target": target,
                            "source": entry.get("source", "manual"),
                        }
        except Exception as e:
            logger.warning(f"[板块名解析] 加载别名表失败({self._alias_path}): {e}")
        self._aliases = aliases
        return aliases

    def _save_aliases(self) -> None:
        try:
            aliases = self._aliases or {}
            payload = {
                "_comment": "TDX 板块名 → 问财可识别的同花顺板块名。source: manual=人工实测, auto=模糊匹配回写",
                "aliases": {
                    name: {
                        "target": entry["target"],
                        "source": entry.get("source", "manual"),
                        "verified": entry.get("verified", date.today().isoformat()),
                    }
                    for name, entry in sorted(aliases.items())
                },
            }
            self._alias_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._alias_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[板块名解析] 回写别名表失败: {e}")

    # ── L2: 同花顺官方板块列表（日缓存） ──

    def _load_ths_board_names(self) -> List[str]:
        if self._ths_names is not None:
            return self._ths_names

        names: List[str] = []
        # 1. 当日磁盘缓存
        try:
            if THS_BOARD_CACHE_PATH.exists():
                with open(THS_BOARD_CACHE_PATH, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                if cache.get("date") == date.today().isoformat() and cache.get("names"):
                    self._ths_names = cache["names"]
                    return self._ths_names
        except Exception as e:
            logger.warning(f"[板块名解析] 读取板块列表缓存失败: {e}")

        # 2. akshare 拉取同花顺概念板块官方列表
        try:
            import akshare as ak
            df = ak.stock_board_concept_name_ths()
            if df is not None and not df.empty:
                col = "名称" if "名称" in df.columns else df.columns[1]
                names = sorted({
                    clean_board_name(str(n))
                    for n in df[col].tolist()
                    if clean_board_name(str(n))
                })
                try:
                    with open(THS_BOARD_CACHE_PATH, "w", encoding="utf-8") as f:
                        json.dump(
                            {"date": date.today().isoformat(), "names": names},
                            f, ensure_ascii=False,
                        )
                except Exception as e:
                    logger.warning(f"[板块名解析] 写入板块列表缓存失败: {e}")
                logger.info(f"[板块名解析] 已获取同花顺概念板块官方列表: {len(names)} 个")
        except Exception as e:
            logger.warning(f"[板块名解析] 获取同花顺板块列表失败，L2 模糊匹配跳过: {e}")

        self._ths_names = names
        return names

    @staticmethod
    def _fuzzy_match(cleaned: str, official_names: List[str]) -> Optional[str]:
        """在官方列表中找最佳候选名

        打分规则：
        - 完全一致 → 直接返回
        - 子串包含（短名 >= 3 字）→ 包含度得分
        - 其余按 difflib 相似度
        """
        if not cleaned:
            return None

        core = cleaned[:-2] if cleaned.endswith("概念") and len(cleaned) > 4 else cleaned

        best_name, best_score = None, 0.0
        for cand in official_names:
            if cand == cleaned:
                return cand
            cand_core = cand[:-2] if cand.endswith("概念") and len(cand) > 4 else cand
            score = difflib.SequenceMatcher(None, core, cand_core).ratio()
            # 子串包含加分（生物制药↔生物医药 这类近义名靠相似度即可命中）
            if len(core) >= 3 and (core in cand_core or cand_core in core):
                contain_score = min(len(core), len(cand_core)) / max(len(core), len(cand_core))
                score = max(score, 0.5 + contain_score * 0.4)
            if score > best_score:
                best_name, best_score = cand, score

        if best_name and best_score >= FUZZY_THRESHOLD and best_name != cleaned:
            logger.info(
                f"[板块名解析] 模糊匹配: {cleaned} → {best_name} (置信度 {best_score:.2f})"
            )
            return best_name
        return None

    # ── 对外接口 ──

    def resolve(self, sector_name: str) -> str:
        """解析问财查询用板块名

        Args:
            sector_name: 系统内部板块名（可能含 \\u0000 等脏字符）

        Returns:
            问财可识别的板块名；无匹配时返回清洗后的原名
        """
        cleaned = clean_board_name(sector_name)
        if not cleaned:
            return sector_name

        with self._lock:
            aliases = self._load_aliases()
            entry = aliases.get(cleaned)
            if entry:
                target = entry["target"]
                if target != cleaned:
                    logger.info(f"[板块名解析] 别名映射(L1): {cleaned} → {target}")
                return target

        # L2 模糊匹配（不加锁，网络/IO 耗时操作）
        candidate = self._fuzzy_match(cleaned, self._load_ths_board_names())
        return candidate or cleaned

    def confirm(self, sector_name: str, resolved_name: str) -> None:
        """问财查询命中后调用：模糊匹配产出的映射回写别名表

        已在别名表中的映射不重复写入。
        """
        cleaned = clean_board_name(sector_name)
        target = clean_board_name(resolved_name)
        if not cleaned or not target or cleaned == target:
            return
        with self._lock:
            aliases = self._load_aliases()
            if cleaned in aliases and aliases[cleaned]["target"] == target:
                return
            aliases[cleaned] = {"target": target, "source": "auto"}
            self._save_aliases()
            logger.info(f"[板块名解析] 模糊匹配已确认并回写: {cleaned} → {target}")


_resolver_instance: Optional[BoardNameResolver] = None


def get_board_name_resolver() -> BoardNameResolver:
    """获取进程内单例解析器"""
    global _resolver_instance
    if _resolver_instance is None:
        _resolver_instance = BoardNameResolver()
    return _resolver_instance
