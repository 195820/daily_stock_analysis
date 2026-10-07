# -*- coding: utf-8 -*-
"""
AkShare fundamental adapter (fail-open).

This adapter intentionally uses capability probing against multiple AkShare
endpoint candidates. It should never raise to caller; partial data is allowed.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_DIVIDEND_KEYWORD_MAP: Dict[str, List[str]] = {
    "per_share": [
        "每股派息",
        "每股现金红利",
        "每股分红",
        "每股派现",
        "派现(元/股)",
        "派息(元/股)",
        "税前派息(元/股)",
        "现金分红(税前)",
    ],
    "plan_text": [
        "分配方案",
        "分红方案",
        "实施方案",
        "派息方案",
        "方案",
        "预案",
        "方案说明",
    ],
    "ex_dividend_date": ["除权除息日", "除息日", "除权日", "除权除息", "除息日期"],
    "record_date": ["股权登记日", "登记日"],
    "announce_date": ["公告日期", "公告日", "实施公告日", "预案公告日"],
    "report_date": ["报告期", "报告日期", "截止日期", "统计截止日期"],
}


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort float conversion."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    try:
        return parsed.to_pydatetime()
    except Exception:
        return None


def _normalize_code(raw: Any) -> str:
    s = _safe_str(raw).upper()
    if "." in s:
        s = s.split(".", 1)[0]
    s = re.sub(r"^(SH|SZ|BJ)", "", s)
    return s


def _pick_by_keywords(row: pd.Series, keywords: List[str]) -> Optional[Any]:
    """
    Return first non-empty row value whose column name contains any keyword.
    """
    for col in row.index:
        col_s = str(col)
        if any(k in col_s for k in keywords):
            val = row.get(col)
            if val is not None and str(val).strip() not in ("", "-", "nan", "None"):
                return val
    return None


def _parse_dividend_plan_to_per_share(plan_text: str) -> Optional[float]:
    """Parse per-share cash dividend from Chinese plan text."""
    text = _safe_str(plan_text)
    if not text:
        return None

    for pattern in (
        r"(?:每)?\s*10\s*股?\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    ):
        match = re.search(pattern, text)
        if match:
            parsed = _safe_float(match.group(1))
            if parsed is not None and parsed > 0:
                return parsed / 10.0

    match_per_share = re.search(r"每\s*股\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元", text)
    if match_per_share:
        parsed = _safe_float(match_per_share.group(1))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _extract_cash_dividend_per_share(row: pd.Series) -> Optional[float]:
    """Extract pre-tax cash dividend per share from a row."""
    plan_text = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["plan_text"]))
    # Keep pre-tax semantics; skip explicit after-tax plans unless pre-tax marker exists.
    if "税后" in plan_text and "税前" not in plan_text and "含税" not in plan_text:
        return None

    direct = _safe_float(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["per_share"]))
    if direct is not None and direct > 0:
        return direct
    return _parse_dividend_plan_to_per_share(plan_text)


def _filter_rows_by_code(df: pd.DataFrame, stock_code: str, *, strict: bool = False) -> pd.DataFrame:
    """按代码列过滤本股行。

    strict=True（「按报告期返回全市场表」的接口专用）：连代码列都没有时返回空表，
    绝不把整表当成本股。默认保持历史行为（无代码列时返回原表）。
    """
    if df is None or df.empty:
        return pd.DataFrame()
    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "symbol", "ts_code"))]
    if not code_cols:
        return pd.DataFrame() if strict else df

    target = _normalize_code(stock_code)
    for col in code_cols:
        try:
            series = df[col].astype(str).map(_normalize_code)
            filtered = df[series == target]
            if not filtered.empty:
                return filtered
        except Exception:
            continue
    return pd.DataFrame()


def _normalize_report_date(value: Any) -> Optional[str]:
    parsed = _safe_datetime(value)
    return parsed.date().isoformat() if parsed else None


def _build_dividend_payload(
    dividend_df: pd.DataFrame,
    stock_code: str,
    max_events: int = 5,
) -> Dict[str, Any]:
    work_df = _filter_rows_by_code(dividend_df, stock_code)
    if work_df.empty:
        return {}

    now_date = datetime.now().date()
    ttm_start_date = now_date - timedelta(days=365)
    dedupe_keys = set()
    events: List[Dict[str, Any]] = []

    for _, row in work_df.iterrows():
        if not isinstance(row, pd.Series):
            continue
        ex_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["ex_dividend_date"]))
        record_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["record_date"]))
        announce_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"]))
        event_dt = ex_dt or record_dt or announce_dt
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if event_date > now_date:
            continue

        per_share = _extract_cash_dividend_per_share(row)
        if per_share is None or per_share <= 0:
            continue

        dedupe_key = (event_date.isoformat(), round(per_share, 6))
        if dedupe_key in dedupe_keys:
            continue
        dedupe_keys.add(dedupe_key)

        events.append(
            {
                "event_date": event_date.isoformat(),
                "ex_dividend_date": ex_dt.date().isoformat() if ex_dt else None,
                "record_date": record_dt.date().isoformat() if record_dt else None,
                "announcement_date": announce_dt.date().isoformat() if announce_dt else None,
                "cash_dividend_per_share": round(per_share, 6),
                "is_pre_tax": True,
            }
        )

    if not events:
        return {}

    events.sort(key=lambda item: item.get("event_date") or "", reverse=True)
    ttm_events: List[Dict[str, Any]] = []
    for item in events:
        event_dt = _safe_datetime(item.get("event_date"))
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if ttm_start_date <= event_date <= now_date:
            ttm_events.append(item)

    return {
        "events": events[:max(1, max_events)],
        "ttm_event_count": len(ttm_events),
        "ttm_cash_dividend_per_share": (
            round(sum(float(item.get("cash_dividend_per_share") or 0.0) for item in ttm_events), 6)
            if ttm_events else None
        ),
        "coverage": "cash_dividend_pre_tax",
        "as_of": now_date.isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# 「按报告期返回全市场表」的业绩类接口（预告 / 快报）
#
# akshare 1.18.91 实测签名（三个都**只收 date、不收 symbol**）：
#   stock_yjyg_em(date='20200331')  业绩预告（含「预告类型」列）
#   stock_yjkb_em(date='20211231')  业绩快报（全数值列，无摘要文本）
#   stock_yjbb_em(date='20200331')  业绩报表（不是预告，本适配器已不再使用）
# 修复前用 {"symbol": code} 调用必然 TypeError，回退空参又拿到默认报告期
# （2020Q1 / 2021年报，陈旧 4-6 年）再按代码过滤 → 绝大多数股票取不到行，
# RiskAgent 的「业绩预亏」通路长期为空。另：未公布报告期在 akshare 内部
# 报 TypeError（实测 'NoneType' object is not subscriptable），靠逐期回退处理。
# 详见 tests/test_fundamental_adapter.py 的签名守卫与逐期回退用例。
# ─────────────────────────────────────────────────────────────

REPORT_PERIOD_LOOKBACK = 2        # 逐股回看最近几个报告期（新→旧）
REPORT_TABLE_TTL_SECONDS = 1800   # 全市场表进程内缓存：一次拉取服务整轮逐股查询
REPORT_TABLE_MAX_ENTRIES = 6      # 缓存表数量上限（单表 2k-5k 行，防长跑内存增长）


def _recent_report_periods(as_of: Optional[datetime] = None,
                           count: int = REPORT_PERIOD_LOOKBACK) -> List[str]:
    """最近若干个「已结束的」报告期，YYYYMMDD，新→旧。"""
    now = as_of or datetime.now()
    ends: List[date] = []
    for year in (now.year, now.year - 1, now.year - 2):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            ends.append(date(year, month, day))
    recent = [d for d in sorted(ends, reverse=True) if d <= now.date()]
    return [d.strftime("%Y%m%d") for d in recent[:max(1, count)]]


# 预告表是「一股多行、每行一个预测指标」（营收/归母净利/扣非净利/每股收益…），
# 同一公告日各指标预告类型实测约 1/3 冲突（取证脚本
# audit_earnings_forecast_direction.py：type_conflict_share≈32.45%）。「业绩预亏」
# 命题讲的是利润：若取到营业收入行，「营收预增 + 净利首亏」会被标成预增——
# 故选行口径必须与取证脚本的 PROFIT_PRIORITY 一致（归母 > 扣非 > 净利润），
# 不另立第二套。无净利口径行则退化取首条（不静默丢数据）。
FORECAST_PROFIT_PRIORITY = ("归属于上市公司股东的净利润", "扣除非经常性损益后的净利润", "净利润")


def _pick_canonical_forecast_row(df: pd.DataFrame) -> pd.Series:
    """同一股票多条预告行时取净利润口径行；无该口径/无预测指标列时回退首行。"""
    indicator_col = next((c for c in df.columns if str(c).strip() == "预测指标"), None)
    if indicator_col is not None:
        for want in FORECAST_PROFIT_PRIORITY:
            hit = df[df[indicator_col].astype(str).str.contains(want, na=False, regex=False)]
            if not hit.empty:
                return hit.iloc[0]
    return df.iloc[0]


def _extract_latest_row(df: pd.DataFrame, stock_code: str) -> Optional[pd.Series]:
    """
    Select the most relevant row for the given stock.
    """
    if df is None or df.empty:
        return None

    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))]
    target = _normalize_code(stock_code)
    if code_cols:
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                matched = df[series == target]
                if not matched.empty:
                    return matched.iloc[0]
            except Exception:
                continue
        return None

    # Fallback: use latest row
    return df.iloc[0]


class AkshareFundamentalAdapter:
    """AkShare adapter for fundamentals, capital flow and dragon-tiger signals."""

    def __init__(self) -> None:
        # (接口名, 报告期) → (时间戳, 全市场表)。逐股共用，避免每只股票重拉全市场。
        self._report_tables: Dict[Tuple[str, str], Tuple[float, pd.DataFrame]] = {}
        self._report_table_lock = RLock()

    def _get_report_table(
        self,
        ak_module: Any,
        func_name: str,
        period: str,
        errors: List[str],
    ) -> Optional[pd.DataFrame]:
        """取某报告期的全市场表，进程内按 (接口, 报告期) 缓存。"""
        key = (func_name, period)
        now = time.time()
        with self._report_table_lock:
            cached = self._report_tables.get(key)
            if cached is not None and now - cached[0] < REPORT_TABLE_TTL_SECONDS:
                return cached[1]

        fn = getattr(ak_module, func_name, None)
        if fn is None:
            return None
        try:
            df = fn(date=period)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{func_name}:{type(exc).__name__}")
            return None
        if not isinstance(df, pd.DataFrame) or df.empty:
            return None

        with self._report_table_lock:
            if len(self._report_tables) >= REPORT_TABLE_MAX_ENTRIES and key not in self._report_tables:
                oldest = min(self._report_tables, key=lambda k: self._report_tables[k][0])
                self._report_tables.pop(oldest, None)
            self._report_tables[key] = (now, df)
        return df

    def _fetch_report_rows(
        self,
        func_names: Tuple[str, ...],
        stock_code: str,
        errors: List[str],
    ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """查本股最近几条业绩记录：报告期新→旧，主源无记录再试降级源。"""
        try:
            import akshare as ak
        except Exception as exc:  # noqa: BLE001
            errors.append(f"import_akshare:{type(exc).__name__}")
            return None, None

        periods = _recent_report_periods()
        attempt_errors: List[str] = []
        tables_seen = 0
        for func_name in func_names:
            for period in periods:
                table = self._get_report_table(ak, func_name, period, attempt_errors)
                if table is not None:
                    tables_seen += 1
                matched = _filter_rows_by_code(table, stock_code, strict=True)
                if not matched.empty:
                    return matched, func_name
        # 错误上报口径：拿到过表但本股无行 = 该股确实没有业绩预告/快报（正常缺失）；
        # 未公布的报告期在 akshare 内部报 TypeError（实测）也属正常缺失。
        # 只有「一个表都没拿到」才上报，否则每只股都带一条假错会把 earnings 块误标 failed。
        if tables_seen == 0:
            for err in attempt_errors:
                if err not in errors:
                    errors.append(err)
        return None, None

    def _call_df_candidates(
        self,
        candidates: List[Tuple[str, Dict[str, Any]]],
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], List[str]]:
        errors: List[str] = []
        try:
            import akshare as ak
        except Exception as exc:
            return None, None, [f"import_akshare:{type(exc).__name__}"]

        for func_name, kwargs in candidates:
            fn = getattr(ak, func_name, None)
            if fn is None:
                continue
            try:
                df = fn(**kwargs)
                if isinstance(df, pd.Series):
                    df = df.to_frame().T
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df, func_name, errors
            except Exception as exc:
                errors.append(f"{func_name}:{type(exc).__name__}")
                continue
        return None, None, errors

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        """
        Return normalized fundamental blocks from AkShare with partial tolerance.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }

        # Financial indicators
        fin_df, fin_source, fin_errors = self._call_df_candidates([
            ("stock_financial_abstract", {"symbol": stock_code}),
            ("stock_financial_analysis_indicator", {"symbol": stock_code}),
            ("stock_financial_analysis_indicator", {}),
        ])
        result["errors"].extend(fin_errors)
        if fin_df is not None:
            row = _extract_latest_row(fin_df, stock_code)
            if row is not None:
                revenue_yoy = _safe_float(_pick_by_keywords(row, ["营业收入同比", "营收同比", "收入同比", "同比增长"]))
                profit_yoy = _safe_float(_pick_by_keywords(row, ["净利润同比", "净利同比", "归母净利润同比"]))
                roe = _safe_float(_pick_by_keywords(row, ["净资产收益率", "ROE", "净资产收益"]))
                gross_margin = _safe_float(_pick_by_keywords(row, ["毛利率"]))
                report_date = _normalize_report_date(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["report_date"]))
                revenue = _safe_float(_pick_by_keywords(row, ["营业总收入", "营业收入", "营收"]))
                net_profit_parent = _safe_float(_pick_by_keywords(row, ["归母净利润", "母公司股东净利润", "净利润"]))
                operating_cash_flow = _safe_float(
                    _pick_by_keywords(row, ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"])
                )
                result["growth"] = {
                    "revenue_yoy": revenue_yoy,
                    "net_profit_yoy": profit_yoy,
                    "roe": roe,
                    "gross_margin": gross_margin,
                }
                financial_report_payload = {
                    "report_date": report_date,
                    "revenue": revenue,
                    "net_profit_parent": net_profit_parent,
                    "operating_cash_flow": operating_cash_flow,
                    "roe": roe,
                }
                if any(v is not None for v in financial_report_payload.values()):
                    result["earnings"]["financial_report"] = financial_report_payload
                result["source_chain"].append(f"growth:{fin_source}")

        # Earnings forecast（业绩预告：接口按报告期返回全市场表 → 表内过滤本股）
        # 降级源 stock_yjbb_em（业绩报表）已移除：它不是预告，旧候选按「预告/业绩变动/
        # 内容/摘要/公告」取列时会捡到「公告日期」当摘要（实测 600519），且单期 11,449 行 /
        # ~11 秒，会吃光整个 bundle 的时间预算。本股无预告记录 → 留空就是正确读数。
        forecast_errors: List[str] = []
        forecast_df, forecast_source = self._fetch_report_rows(
            ("stock_yjyg_em",), stock_code, forecast_errors
        )
        result["errors"].extend(forecast_errors)
        if forecast_df is not None:
            # 多指标行必须按净利润口径选（见 _pick_canonical_forecast_row 注释），
            # 直接 iloc[0] 会拿「首个指标行」——可能是营业收入行，预亏被标成预增。
            row = _pick_canonical_forecast_row(forecast_df)
            result["earnings"]["forecast_summary"] = _safe_str(
                _pick_by_keywords(row, ["预告", "业绩变动", "内容", "摘要", "公告"])
            )[:200]
            # 「预告类型」（预增/首亏/减亏/扭亏…）是判断利好/利空的直接依据，
            # 之前整列被丢弃。
            type_col = next(
                (c for c in row.index if str(c).strip() in ("预告类型", "预测类型")), None
            )
            if type_col is not None:
                forecast_type = _safe_str(row.get(type_col))
                if forecast_type:
                    result["earnings"]["forecast_type"] = forecast_type
            result["source_chain"].append(f"earnings_forecast:{forecast_source}")

        # Earnings quick report（业绩快报）
        # stock_yjkb_em 的表里**没有任何摘要类文本列**（实测列：每股收益 / 营业收入-*/
        # 净利润-*/每股净资产/净资产收益率/所处行业/公告日期），旧实现按「快报/摘要/公告/
        # 说明」取列只会得到一个公告日期 → 改落结构化数值，取不到就不写字段。
        quick_errors: List[str] = []
        quick_df, quick_source = self._fetch_report_rows(
            ("stock_yjkb_em",), stock_code, quick_errors
        )
        result["errors"].extend(quick_errors)
        if quick_df is not None:
            row = quick_df.iloc[0]
            quick_payload = {
                "revenue": _safe_float(_pick_by_keywords(row, ["营业收入-营业收入"])),
                "net_profit": _safe_float(_pick_by_keywords(row, ["净利润-净利润"])),
                "net_profit_yoy": _safe_float(_pick_by_keywords(row, ["净利润-同比增长"])),
                "announce_date": _normalize_report_date(_pick_by_keywords(row, ["公告日期"])),
            }
            if any(v is not None for v in quick_payload.values()):
                result["earnings"]["quick_report"] = quick_payload
                result["source_chain"].append(f"earnings_quick:{quick_source}")

        # Dividend details (cash dividend, pre-tax)
        dividend_df, dividend_source, dividend_errors = self._call_df_candidates([
            ("stock_fhps_detail_em", {"symbol": stock_code}),
            ("stock_history_dividend_detail", {"symbol": stock_code, "indicator": "分红", "date": ""}),
            ("stock_dividend_cninfo", {"symbol": stock_code}),
        ])
        result["errors"].extend(dividend_errors)
        if dividend_df is not None:
            dividend_payload = _build_dividend_payload(dividend_df, stock_code, max_events=5)
            if dividend_payload:
                result["earnings"]["dividend"] = dividend_payload
                result["source_chain"].append(f"dividend:{dividend_source}")

        # Institution / top shareholders
        inst_df, inst_source, inst_errors = self._call_df_candidates([
            ("stock_institute_hold", {}),
            ("stock_institute_recommend", {}),
        ])
        result["errors"].extend(inst_errors)
        if inst_df is not None:
            row = _extract_latest_row(inst_df, stock_code)
            if row is not None:
                inst_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "变动", "持股变化"]))
                result["institution"]["institution_holding_change"] = inst_change
                result["source_chain"].append(f"institution:{inst_source}")

        top10_df, top10_source, top10_errors = self._call_df_candidates([
            ("stock_gdfx_top_10_em", {"symbol": stock_code}),
            ("stock_gdfx_top_10_em", {}),
            ("stock_zh_a_gdhs_detail_em", {"symbol": stock_code}),
            ("stock_zh_a_gdhs_detail_em", {}),
        ])
        result["errors"].extend(top10_errors)
        if top10_df is not None:
            row = _extract_latest_row(top10_df, stock_code)
            if row is not None:
                holder_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "持股变化", "变动"]))
                result["institution"]["top10_holder_change"] = holder_change
                result["source_chain"].append(f"top10:{top10_source}")

        has_content = bool(result["growth"] or result["earnings"] or result["institution"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_capital_flow(self, stock_code: str, top_n: int = 5) -> Dict[str, Any]:
        """
        Return stock + sector capital flow.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": {},
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": [],
            "errors": [],
        }

        stock_df, stock_source, stock_errors = self._call_df_candidates([
            ("stock_individual_fund_flow", {"stock": stock_code}),
            ("stock_individual_fund_flow", {"symbol": stock_code}),
            ("stock_individual_fund_flow", {}),
            ("stock_main_fund_flow", {"symbol": stock_code}),
            ("stock_main_fund_flow", {}),
        ])
        result["errors"].extend(stock_errors)
        if stock_df is not None:
            row = _extract_latest_row(stock_df, stock_code)
            if row is not None:
                net_inflow = _safe_float(_pick_by_keywords(row, ["主力净流入", "净流入", "净额"]))
                inflow_5d = _safe_float(_pick_by_keywords(row, ["5日", "五日"]))
                inflow_10d = _safe_float(_pick_by_keywords(row, ["10日", "十日"]))
                result["stock_flow"] = {
                    "main_net_inflow": net_inflow,
                    "inflow_5d": inflow_5d,
                    "inflow_10d": inflow_10d,
                }
                result["source_chain"].append(f"capital_stock:{stock_source}")

        sector_df, sector_source, sector_errors = self._call_df_candidates([
            ("stock_sector_fund_flow_rank", {}),
            ("stock_sector_fund_flow_summary", {}),
        ])
        result["errors"].extend(sector_errors)
        if sector_df is not None:
            name_col = next((c for c in sector_df.columns if any(k in str(c) for k in ("板块", "行业", "名称", "name"))), None)
            flow_col = next((c for c in sector_df.columns if any(k in str(c) for k in ("净流入", "主力", "flow", "净额"))), None)
            if name_col and flow_col:
                work_df = sector_df[[name_col, flow_col]].copy()
                work_df[flow_col] = pd.to_numeric(work_df[flow_col], errors="coerce")
                work_df = work_df.dropna(subset=[flow_col])
                top_df = work_df.nlargest(top_n, flow_col)
                bottom_df = work_df.nsmallest(top_n, flow_col)
                result["sector_rankings"] = {
                    "top": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in top_df.iterrows()],
                    "bottom": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in bottom_df.iterrows()],
                }
                result["source_chain"].append(f"capital_sector:{sector_source}")

        has_content = bool(result["stock_flow"] or result["sector_rankings"]["top"] or result["sector_rankings"]["bottom"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_dragon_tiger_flag(self, stock_code: str, lookback_days: int = 20) -> Dict[str, Any]:
        """
        Return dragon-tiger signal in lookback window.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "is_on_list": False,
            "recent_count": 0,
            "latest_date": None,
            "source_chain": [],
            "errors": [],
        }

        df, source, errors = self._call_df_candidates([
            ("stock_lhb_stock_statistic_em", {}),
            ("stock_lhb_detail_em", {}),
            ("stock_lhb_jgmmtj_em", {}),
        ])
        result["errors"].extend(errors)
        if df is None:
            return result

        # Try code filter
        code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码"))]
        target = _normalize_code(stock_code)
        matched = pd.DataFrame()
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                cur = df[series == target]
                if not cur.empty:
                    matched = cur
                    break
            except Exception:
                continue
        if matched.empty:
            result["source_chain"].append(f"dragon_tiger:{source}")
            result["status"] = "ok" if code_cols else "partial"
            return result

        date_col = next((c for c in matched.columns if any(k in str(c) for k in ("日期", "上榜", "交易日", "time"))), None)
        parsed_dates: List[datetime] = []
        if date_col is not None:
            for val in matched[date_col].astype(str).tolist():
                try:
                    parsed_dates.append(pd.to_datetime(val).to_pydatetime())
                except Exception:
                    continue
        now = datetime.now()
        start = now - timedelta(days=max(1, lookback_days))
        recent_dates = [d for d in parsed_dates if start <= d <= now]

        result["is_on_list"] = bool(recent_dates)
        result["recent_count"] = len(recent_dates) if recent_dates else int(len(matched))
        result["latest_date"] = max(recent_dates).date().isoformat() if recent_dates else (
            max(parsed_dates).date().isoformat() if parsed_dates else None
        )
        result["status"] = "ok"
        result["source_chain"].append(f"dragon_tiger:{source}")
        return result
