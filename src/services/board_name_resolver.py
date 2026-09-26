# -*- coding: utf-8 -*-
"""
板块名解析器（TDX 板块名 → 问财可识别的同花顺板块名）

背景：
- 系统板块名来自 TDX（通达信），GBK 定长字段导致名称常被截断/带 \\u0000 填充
  （如 "白酒\\u0000..."、"碳纤维概"）
- 问财有两种板块语法：
  - "所属同花顺板块是X" → 只匹配**行业板块**
  - "所属同花顺概念板块是X" → 匹配**概念板块**
  Node 2 输出的板块绝大多数是概念板块，必须用概念语法才能查到

四层机制：
- L0: clean_board_name() 清洗 \\u0000/首尾空白（上游入口也应调用）
- L1: 静态别名表（data/wencai_board_aliases.json，含 kind 字段，最高优先级）
- L2: 用同花顺官方概念/行业板块列表模糊匹配；
      查询命中后由调用方 confirm() 回写别名表

使用方式：
    resolver = get_board_name_resolver()
    board_name, kind = resolver.resolve_with_kind(sector_name)   # 查询前
    ... 按 kind 选择问财语法 ...
    if 查询命中:
        resolver.confirm(sector_name, board_name, kind=kind)
"""

from __future__ import annotations

import difflib
import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ALIAS_PATH = _PROJECT_ROOT / "data" / "wencai_board_aliases.json"
THS_BOARD_CACHE_PATH = _PROJECT_ROOT / "data" / "ths_concept_boards.json"

# L2 模糊匹配置信度阈值（低于该值不产出候选）
FUZZY_THRESHOLD = 0.68

# 审核修复 3.2：Q7-B 自愈（L3）相似度下限——字符相近不等于资金语义相同，
# 低于阈值宁可不自愈，避免把无关板块配成最近名后永久固化
SELF_HEAL_SIM_THRESHOLD = 0.6

# 取列优先级：akshare 不同版本列名可能不同
_NAME_COLUMNS = ("name", "名称", "概念名称", "板块名称")


def clean_board_name(name: str) -> str:
    """清洗板块名：去除 \\u0000 填充符与首尾空白（TDX GBK 定长字段常见脏字符）"""
    if not name:
        return ""
    return name.replace("\u0000", "").strip()


def _is_dirty_cache(names: List[str]) -> bool:
    """检测脏缓存：若超过半数条目是 6 位纯数字（股票代码），判定为脏缓存"""
    if not names:
        return True
    digit_count = sum(1 for n in names if isinstance(n, str) and n.isdigit() and len(n) == 6)
    return digit_count > len(names) * 0.5


class BoardNameResolver:
    """板块名解析器（线程安全，进程内单例复用）"""

    def __init__(self, alias_path: Optional[Path] = None):
        self._alias_path = Path(alias_path) if alias_path else DEFAULT_ALIAS_PATH
        self._lock = threading.Lock()
        self._aliases: Optional[Dict[str, dict]] = None
        self._ths_concept_names: Optional[List[str]] = None
        self._ths_industry_names: Optional[List[str]] = None
        # Q7-B（audit_20260922）：双语法全空失败名的当日负缓存
        # （进程内存活，跨日自动失效；已知永久失败名同日不再重试，避免每日白烧 8 次查询）
        self._failed_today: set = set()
        self._failed_date: Optional[date] = None

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
                            "kind": entry.get("kind"),
                        }
        except Exception as e:
            logger.warning(f"[板块名解析] 加载别名表失败({self._alias_path}): {e}")
        self._aliases = aliases
        return aliases

    def _save_aliases(self) -> None:
        try:
            aliases = self._aliases or {}
            payload = {
                "_comment": "TDX 板块名 → 问财可识别的同花顺板块名。kind: concept=概念板块, industry=行业板块",
                "aliases": {
                    name: {
                        "target": entry["target"],
                        "source": entry.get("source", "manual"),
                        "kind": entry.get("kind"),
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

    # ── L2: 同花顺官方板块列表（日缓存，概念+行业分离） ──

    def _fetch_ths_names_from_akshare(self) -> Tuple[List[str], List[str]]:
        """从 akshare 拉取同花顺概念+行业板块官方列表"""
        import akshare as ak
        concept_names: List[str] = []
        industry_names: List[str] = []

        # 概念板块列表
        try:
            df = ak.stock_board_concept_name_ths()
            if df is not None and not df.empty:
                col = self._pick_name_column(df.columns)
                concept_names = sorted({
                    clean_board_name(str(n))
                    for n in df[col].tolist()
                    if clean_board_name(str(n))
                })
                logger.info(f"[板块名解析] 概念板块列表: {len(concept_names)} 个")
        except Exception as e:
            logger.warning(f"[板块名解析] 获取概念板块列表失败: {e}")

        # 行业板块列表
        try:
            df = ak.stock_board_industry_name_ths()
            if df is not None and not df.empty:
                col = self._pick_name_column(df.columns)
                industry_names = sorted({
                    clean_board_name(str(n))
                    for n in df[col].tolist()
                    if clean_board_name(str(n))
                })
                logger.info(f"[板块名解析] 行业板块列表: {len(industry_names)} 个")
        except Exception as e:
            logger.warning(f"[板块名解析] 获取行业板块列表失败: {e}")

        return concept_names, industry_names

    @staticmethod
    def _pick_name_column(columns) -> str:
        """按优先级选取名称列，都找不到时 fallback 到第一列"""
        for candidate in _NAME_COLUMNS:
            if candidate in columns:
                return candidate
        return columns[0]

    def _load_ths_board_names(self) -> Tuple[List[str], List[str]]:
        """加载同花顺板块列表（概念+行业），带日缓存和脏缓存检测"""
        if self._ths_concept_names is not None and self._ths_industry_names is not None:
            return self._ths_concept_names, self._ths_industry_names

        concept_names: List[str] = []
        industry_names: List[str] = []

        # 1. 当日磁盘缓存
        try:
            if THS_BOARD_CACHE_PATH.exists():
                with open(THS_BOARD_CACHE_PATH, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                if cache.get("date") == date.today().isoformat():
                    cached_concepts = cache.get("concept_names", [])
                    cached_industries = cache.get("industry_names", [])
                    # 旧格式兼容：仅 "names" 键或脏缓存 → 重建
                    if not _is_dirty_cache(cached_concepts) and cached_concepts:
                        self._ths_concept_names = cached_concepts
                        self._ths_industry_names = cached_industries
                        return self._ths_concept_names, self._ths_industry_names
                    elif "names" in cache and not _is_dirty_cache(cache["names"]):
                        # 旧格式（仅 names 键且非脏）→ 当作概念列表
                        self._ths_concept_names = cache["names"]
                        self._ths_industry_names = cached_industries
                        return self._ths_concept_names, self._ths_industry_names
                    else:
                        logger.warning("[板块名解析] 检测到脏缓存（股票代码污染），重新拉取")
        except Exception as e:
            logger.warning(f"[板块名解析] 读取板块列表缓存失败: {e}")

        # 2. akshare 拉取
        concept_names, industry_names = self._fetch_ths_names_from_akshare()

        # 3. 写缓存
        if concept_names:
            try:
                with open(THS_BOARD_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        "date": date.today().isoformat(),
                        "concept_names": concept_names,
                        "industry_names": industry_names,
                    }, f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"[板块名解析] 写入板块列表缓存失败: {e}")

        self._ths_concept_names = concept_names
        self._ths_industry_names = industry_names
        return concept_names, industry_names

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
        """解析问财查询用板块名（向后兼容，只返回名字）"""
        name, _kind = self.resolve_with_kind(sector_name)
        return name

    def resolve_with_kind(self, sector_name: str) -> Tuple[str, Optional[str]]:
        """解析问财查询用板块名，同时返回板块种类

        Returns:
            (board_name, kind): kind 为 "concept"/"industry"/None
        """
        cleaned = clean_board_name(sector_name)
        if not cleaned:
            return sector_name, None

        with self._lock:
            aliases = self._load_aliases()
            entry = aliases.get(cleaned)
            if entry and entry.get("source") == "auto_lowconf":
                # 审核修复 3.2：自愈回写的低置信别名只留痕不消费，
                # 人工把 source 改为 manual/auto 后才生效
                logger.info(
                    f"[板块名解析] 跳过低置信别名(auto_lowconf): "
                    f"{cleaned} → {entry['target']}（需人工升级后生效）"
                )
                entry = None
            if entry:
                target = entry["target"]
                kind = entry.get("kind")
                if target != cleaned:
                    logger.info(f"[板块名解析] 别名映射(L1): {cleaned} → {target} (kind={kind})")
                return target, kind
            # 当日负缓存（Q7-B）：L1 别名表优先（自愈回写的别名可解锁），
            # 未命中别名且已判失败 → 短路，不再进入 L2/查询重试
            if self._in_failed_cache_locked(cleaned):
                logger.info(f"[板块名解析] 当日负缓存命中，短路跳过: {cleaned}")
                return cleaned, None

        # L2 模糊匹配：先概念列表，再行业列表
        concept_names, industry_names = self._load_ths_board_names()

        candidate = self._fuzzy_match(cleaned, concept_names)
        if candidate:
            return candidate, "concept"

        candidate = self._fuzzy_match(cleaned, industry_names)
        if candidate:
            return candidate, "industry"

        return cleaned, None

    def aggressive_match(self, sector_name: str) -> Optional[str]:
        """Q7-B 自愈闭环：双语法全空后的候选推荐（仅取概念列表）

        与 L2 的区别：不要求达到 FUZZY_THRESHOLD，但受 SELF_HEAL_SIM_THRESHOLD
        下限约束（审核修复 3.2：无阈值取最高分会把语义无关板块配成最近名）；
        候选与原解析名相同时返回 None（重试同词无意义）。
        """
        cleaned = clean_board_name(sector_name)
        if not cleaned:
            return None
        concept_names, _ = self._load_ths_board_names()
        if not concept_names:
            return None
        best_name, best_score = None, 0.0
        for cand in concept_names:
            if cand == cleaned:
                return None  # 官方列表里有同名概念，失败非名字问题，不自愈
            score = difflib.SequenceMatcher(None, cleaned, cand).ratio()
            if score > best_score:
                best_name, best_score = cand, score
        if best_name and best_score < SELF_HEAL_SIM_THRESHOLD:
            logger.info(
                f"[板块名解析] 自愈候选低于阈值，放弃: {cleaned} → {best_name} "
                f"(相似度 {best_score:.2f} < {SELF_HEAL_SIM_THRESHOLD})"
            )
            return None
        if best_name and best_name != cleaned:
            logger.info(
                f"[板块名解析] 自愈候选(L3): {cleaned} → {best_name} (相似度 {best_score:.2f})"
            )
            return best_name
        return None

    # ── Q7-B：当日负缓存 ──

    def _in_failed_cache_locked(self, cleaned: str) -> bool:
        """检查当日负缓存（调用方需已持锁）；跨日自动清空"""
        today = date.today()
        if self._failed_date != today:
            self._failed_today = set()
            self._failed_date = today
        return cleaned in self._failed_today

    def mark_failed(self, sector_name: str) -> None:
        """登记当日失败名（双语法全空且自愈未挽回时调用），同日不再重试"""
        cleaned = clean_board_name(sector_name)
        if not cleaned:
            return
        with self._lock:
            self._in_failed_cache_locked(cleaned)  # 顺带跨日清理
            self._failed_today.add(cleaned)
        logger.info(f"[板块名解析] 登记当日负缓存: {cleaned}（同日不再重试）")

    def is_failed_today(self, sector_name: str) -> bool:
        """查询入口预检：该原始名当日是否已判失败"""
        cleaned = clean_board_name(sector_name)
        if not cleaned:
            return False
        with self._lock:
            return self._in_failed_cache_locked(cleaned)

    def confirm(
        self,
        sector_name: str,
        resolved_name: str,
        kind: Optional[str] = None,
        source: str = "auto",
    ) -> None:
        """问财查询命中后调用：映射回写别名表（含 kind）

        source="auto_lowconf"（审核修复 3.2）：自愈路径回写，L1 解析不消费，
        仅留痕供人工审核后升级为 manual/auto。
        已在别名表中的同源映射不重复写入。
        """
        cleaned = clean_board_name(sector_name)
        target = clean_board_name(resolved_name)
        if not cleaned or not target or cleaned == target:
            return
        with self._lock:
            aliases = self._load_aliases()
            existing = aliases.get(cleaned)
            if (
                existing
                and source == "auto_lowconf"
                and existing.get("source", "manual") in ("manual", "auto")
            ):
                return  # 低置信回写不得覆盖已验证别名
            if (
                existing
                and existing["target"] == target
                and existing.get("kind") == kind
                and existing.get("source", "manual") == source
            ):
                return
            aliases[cleaned] = {
                "target": target,
                "source": source,
                "kind": kind,
            }
            self._save_aliases()
            logger.info(
                f"[板块名解析] 已确认并回写: {cleaned} → {target} "
                f"(kind={kind}, source={source})"
            )


_resolver_instance: Optional[BoardNameResolver] = None


def get_board_name_resolver() -> BoardNameResolver:
    """获取进程内单例解析器"""
    global _resolver_instance
    if _resolver_instance is None:
        _resolver_instance = BoardNameResolver()
    return _resolver_instance
