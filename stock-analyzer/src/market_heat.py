"""
Market heat provider for A-share sector context.

This module is intentionally defensive: every external data source is optional,
slow calls are timeout-bounded, and the public result shape remains stable even
when AKShare or a specific upstream endpoint is unavailable.
"""

from __future__ import annotations

import math
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from io import StringIO
from typing import Any, Optional

import pandas as pd
import requests

try:
    import a_stock_data_provider as a_stock_data
except ImportError:  # pragma: no cover - package execution fallback
    try:
        from . import a_stock_data_provider as a_stock_data
    except ImportError:  # pragma: no cover - isolated tests can monkeypatch internals
        a_stock_data = None

try:
    from models import get_attention_heat_snapshots, save_attention_heat_snapshot
except ImportError:  # pragma: no cover - package execution fallback
    try:
        from .models import get_attention_heat_snapshots, save_attention_heat_snapshot
    except ImportError:  # pragma: no cover - optional in isolated tests
        get_attention_heat_snapshots = None
        save_attention_heat_snapshot = None


DEFAULT_TTL_SECONDS = 10 * 60
BOARD_TTL_SECONDS = 30 * 60
HOT_TTL_SECONDS = 5 * 60
REQUEST_TIMEOUT_SECONDS = 2.0
ATTENTION_SNAPSHOT_FILE = os.environ.get(
    "ATTENTION_HEAT_SNAPSHOT_FILE",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "attention_heat_snapshots.json"),
)
ENABLE_THS_FUND_FALLBACK = os.environ.get("ENABLE_THS_FUND_FALLBACK", "").lower() in {"1", "true", "yes"}
ENABLE_THS_HOT_REASON_FALLBACK = os.environ.get("ENABLE_THS_HOT_REASON_FALLBACK", "").lower() in {"1", "true", "yes"}
ATTENTION_HEAT_BUDGET_SECONDS = float(os.environ.get("ATTENTION_HEAT_BUDGET_SECONDS", "6.0"))

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.RLock()

RELATED_ETF_CATALOG: list[dict] = [
    {"code": "512480", "name": "半导体ETF", "keywords": ["半导体", "芯片", "集成电路", "先进封装", "存储芯片", "数字芯片", "芯片设计", "长江存储"]},
    {"code": "159995", "name": "芯片ETF", "keywords": ["半导体", "芯片", "集成电路", "先进封装", "存储芯片", "数字芯片", "芯片设计"]},
    {"code": "588200", "name": "科创芯片ETF", "keywords": ["半导体", "芯片", "科创芯片", "先进封装", "存储芯片", "芯片设计"]},
    {"code": "512760", "name": "芯片ETF", "keywords": ["半导体", "芯片", "集成电路", "先进封装", "存储芯片"]},
    {"code": "515980", "name": "人工智能ETF", "keywords": ["人工智能", "AI", "算力", "大模型", "机器人", "数字经济"]},
    {"code": "159819", "name": "人工智能ETF", "keywords": ["人工智能", "AI", "算力", "大模型", "数字经济"]},
    {"code": "516510", "name": "云计算ETF", "keywords": ["云计算", "算力", "数据中心", "AI服务器", "服务器"]},
    {"code": "159998", "name": "计算机ETF", "keywords": ["计算机", "软件", "信创", "数字经济", "金融科技"]},
    {"code": "159770", "name": "机器人ETF", "keywords": ["机器人", "工业母机", "智能制造"]},
    {"code": "562500", "name": "机器人ETF", "keywords": ["机器人", "工业母机", "智能制造"]},
    {"code": "515050", "name": "5GETF", "keywords": ["5G", "通信", "光模块", "CPO", "PCB", "6G"]},
    {"code": "515880", "name": "通信ETF", "keywords": ["通信", "光模块", "CPO", "PCB", "6G", "运营商"]},
    {"code": "512660", "name": "军工ETF", "keywords": ["军工", "国防", "卫星", "低空经济", "航空航天"]},
    {"code": "512670", "name": "国防ETF", "keywords": ["军工", "国防", "卫星", "低空经济", "航空航天"]},
    {"code": "515030", "name": "新能源车ETF", "keywords": ["新能源车", "锂电池", "汽车", "智能汽车", "固态电池"]},
    {"code": "516160", "name": "新能源ETF", "keywords": ["新能源", "光伏", "风电", "储能", "锂电池"]},
    {"code": "512800", "name": "银行ETF", "keywords": ["银行", "金融", "保险"]},
    {"code": "512000", "name": "券商ETF", "keywords": ["证券", "券商", "金融", "互联金融"]},
    {"code": "512880", "name": "证券ETF", "keywords": ["证券", "券商", "金融", "互联金融"]},
    {"code": "159928", "name": "消费ETF", "keywords": ["消费", "食品饮料", "白酒", "零售", "旅游"]},
    {"code": "512690", "name": "酒ETF", "keywords": ["白酒", "食品饮料", "消费"]},
    {"code": "512010", "name": "医药ETF", "keywords": ["医药", "创新药", "医疗", "中药", "CXO"]},
    {"code": "512170", "name": "医疗ETF", "keywords": ["医疗", "医药", "创新药", "CXO"]},
    {"code": "159865", "name": "养殖ETF", "keywords": ["养殖", "猪肉", "农业", "农牧"]},
    {"code": "515220", "name": "煤炭ETF", "keywords": ["煤炭", "能源", "资源"]},
    {"code": "512400", "name": "有色ETF", "keywords": ["有色", "金属", "铜", "铝", "稀土", "黄金"]},
    {"code": "518880", "name": "黄金ETF", "keywords": ["黄金", "贵金属", "金价"]},
]


def ttl_cache_get(key: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Any:
    """Return a cached value if it exists and is still fresh."""
    now = time.time()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at <= now:
            _CACHE.pop(key, None)
            return None
        return value


def ttl_cache_set(key: str, value: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Any:
    """Store and return a value in the process-local TTL cache."""
    with _CACHE_LOCK:
        _CACHE[key] = (time.time() + max(1, int(ttl_seconds)), value)
    return value


def ttl_cache_clear(prefix: Optional[str] = None) -> None:
    """Clear all cached values, or only keys that start with ``prefix``."""
    with _CACHE_LOCK:
        if prefix is None:
            _CACHE.clear()
            return
        for key in list(_CACHE):
            if key.startswith(prefix):
                _CACHE.pop(key, None)


def build_heat_context(
    code: str,
    stock_info: Optional[dict] = None,
    daily_df: Optional[pd.DataFrame] = None,
    minute_df: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Build a normalized heat context for one A-share code.

    The function is suitable for direct use from ``main.py`` or ``analyzer.py``:
    it never raises for provider failures, keeps work bounded to cached board/hot
    lists plus stock-specific hot endpoints, and fills neutral scores when data
    is missing.
    """
    warnings: list[str] = []
    normalized_code = _normalize_code(code)
    info = stock_info or {}
    stock_name = str(info.get("name") or normalized_code)
    today = _latest_date(daily_df) or datetime.now().strftime("%Y-%m-%d")

    board_context = _resolve_board_context(normalized_code, info, warnings)
    hot_context = _resolve_hot_context(normalized_code, stock_name, warnings)
    capital_score, capital_detail = _capital_slope_score(daily_df, minute_df, warnings)
    acceleration_score, acceleration_detail = _heat_acceleration_score(daily_df, minute_df, warnings)

    external_score = _external_heat_score(board_context, hot_context)
    heat = _weighted_score(
        [
            (external_score, 0.45),
            (capital_score, 0.30),
            (acceleration_score, 0.25),
        ]
    )
    confidence = _confidence(board_context, hot_context, daily_df, minute_df, warnings)

    sector_name = board_context.get("sector_name") or "未知板块"
    sector_type = board_context.get("sector_type") or "unknown"
    source_parts = sorted(
        {
            item
            for item in [
                board_context.get("source"),
                hot_context.get("source"),
                "local_price_volume" if daily_df is not None and not daily_df.empty else None,
            ]
            if item
        }
    )

    status = "ok"
    if warnings and confidence < 0.45:
        status = "degraded"
    elif warnings:
        status = "partial"

    return {
        "code": normalized_code,
        "stock_name": stock_name,
        "sector_name": sector_name,
        "sector_type": sector_type,
        "sector_heat": heat,
        "sector_heat_score": heat,
        "sector_heat_window": "5d",
        "sector_heat_date": today,
        "sector_source": "+".join(source_parts) if source_parts else "local_fallback",
        "sector_confidence": confidence,
        "sector_rank": board_context.get("rank"),
        "heat": heat,
        "window": "5d",
        "date": today,
        "rank": board_context.get("rank"),
        "source": "+".join(source_parts) if source_parts else "local_fallback",
        "confidence": confidence,
        "external_heat_score": external_score,
        "capital_slope_score": capital_score,
        "heat_acceleration_score": acceleration_score,
        "board_ranking_hints": {
            "rank": board_context.get("rank"),
            "rank_percentile": board_context.get("rank_percentile"),
            "change_pct": board_context.get("change_pct"),
            "turnover_rate": board_context.get("turnover_rate"),
            "amount": board_context.get("amount"),
            "matched_by": board_context.get("matched_by"),
            "top_boards": board_context.get("top_boards", []),
        },
        "stock_ranking_hints": {
            "eastmoney_hot_rank": hot_context.get("eastmoney_hot_rank"),
            "eastmoney_rank_change": hot_context.get("eastmoney_rank_change"),
            "keyword_hits": hot_context.get("keyword_hits", []),
            "snowball_hits": hot_context.get("snowball_hits", []),
            "is_in_hot_list": hot_context.get("is_in_hot_list", False),
        },
        "score_details": {
            "external": hot_context.get("details", {}),
            "capital": capital_detail,
            "acceleration": acceleration_detail,
        },
        "data_status": {
            "status": status,
            "warnings": warnings,
            "has_akshare": _akshare_available(warnings),
            "cache_keys": _cache_snapshot_keys(),
        },
    }


def build_market_heat_ranking(limit: int = 20) -> dict:
    """
    Build a best-effort market-wide sector heat ranking.

    v1 uses Eastmoney concept and industry board lists as the primary public
    proxy. The result shape stays stable when upstream data is unavailable so
    the daily-picks page can degrade instead of failing.
    """
    warnings: list[str] = []
    safe_limit = max(1, min(int(limit or 20), 50))
    today = datetime.now().strftime("%Y-%m-%d")

    concept_boards = _ranking_board_list("concept", warnings)
    industry_boards = _ranking_board_list("industry", warnings)
    rows = _top_boards(concept_boards, "concept") + _top_boards(industry_boards, "industry")
    rows = sorted(rows, key=lambda item: item.get("heat_hint") or 0, reverse=True)

    items = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        name = _clean_text(row.get("sector_name"))
        sector_type = _clean_text(row.get("sector_type")) or "unknown"
        if not name:
            continue
        key = (sector_type, name)
        if key in seen:
            continue
        seen.add(key)
        heat = _clamp(row.get("heat_hint") or 50, 0, 100)
        items.append(
            {
                "rank": len(items) + 1,
                "sector_name": name,
                "sector_type": sector_type,
                "sector_heat": heat,
                "sector_heat_score": heat,
                "change_pct": row.get("change_pct"),
                "board_rank": row.get("rank"),
                "source": "eastmoney_board",
                "window": "latest_proxy",
                "date": today,
                "confidence": 0.78,
            }
        )
        if len(items) >= safe_limit:
            break

    if not items:
        _warn_once(warnings, "market heat ranking unavailable; eastmoney board lists returned no rows")

    return {
        "items": items,
        "date": today,
        "window": "latest_proxy",
        "source": "eastmoney_concept+eastmoney_industry" if items else "unavailable",
        "confidence": 0.78 if items else 0.20,
        "data_status": {
            "status": "ok" if items and not warnings else "partial" if items else "degraded",
            "warnings": warnings,
            "has_akshare": _akshare_available(warnings),
            "cache_keys": _cache_snapshot_keys(),
        },
    }


def build_attention_heat_ranking(limit: int = 5, leaders: int = 3) -> dict:
    """
    Build a market-wide topic ranking by capital entry power and retail attention acceleration.

    This is deliberately not a price-change ranking. Eastmoney/THS fund-flow data
    drives the primary ordering; price change is retained only as visibility/risk
    context.
    """
    warnings: list[str] = []
    provider_status = _new_attention_provider_status()
    safe_limit = max(1, min(int(limit or 5), 20))
    safe_leaders = max(0, min(int(leaders or 3), 5))
    today = datetime.now().strftime("%Y-%m-%d")
    now_ts = int(time.time())
    start_ts = time.time()

    def remaining_budget() -> float:
        return ATTENTION_HEAT_BUDGET_SECONDS - (time.time() - start_ts)

    def has_budget(min_seconds: float = 0.75) -> bool:
        return remaining_budget() > min_seconds

    previous_snapshots = _load_attention_snapshots()
    previous_snapshot = _best_attention_snapshot(previous_snapshots, today)

    concept_rows = _fund_flow_topics("概念资金流", warnings, provider_status)
    industry_rows = []
    if has_budget(3.0):
        industry_rows = _fund_flow_topics("行业资金流", warnings, provider_status)
    else:
        _warn_once(warnings, "公开资金流接口耗时超过预算，已跳过行业资金流")
    topic_rows = concept_rows + industry_rows
    fallback_mode = "none"
    if not topic_rows and _ths_hot_reason_fallback_allowed():
        topic_rows = _ths_hot_reason_topics(warnings, provider_status, safe_leaders)
        if topic_rows:
            fallback_mode = "ths_hot_reason"
            _warn_once(warnings, "真实板块资金流不可达，当前使用同花顺强势股题材归因聚合，不代表板块资金净流入排名")
    if not topic_rows and has_budget(0.6):
        topic_rows = _visibility_fallback_topics(warnings, provider_status)
        if topic_rows:
            fallback_mode = "visibility_proxy"
            _warn_once(warnings, "真实资金流接口不可达，当前显示低置信度可见度代理榜，不代表资金净流入排名")
    elif not topic_rows:
        fallback_mode = "unavailable"
        _warn_once(warnings, "公开资金流接口耗时超过预算，已跳过低置信度代理榜")
    if not topic_rows and previous_snapshot:
        topic_rows = _snapshot_fallback_topics(previous_snapshot, warnings)
        if topic_rows:
            fallback_mode = "snapshot"
            _warn_once(warnings, "公开资金流源不可达，当前展示最近一次有效热榜快照")
    stock_rows = _fund_flow_stocks(warnings, provider_status) if topic_rows and has_budget(3.0) and fallback_mode == "none" else []
    if topic_rows and not stock_rows and not has_budget(3.0):
        _warn_once(warnings, "公开资金流接口耗时超过预算，已跳过个股资金流")
    attention_rows = _attention_stock_rows(warnings) if topic_rows and has_budget(1.5) else []
    if topic_rows and not attention_rows and not has_budget(1.5):
        _warn_once(warnings, "公开资金流接口耗时超过预算，已跳过外部人气源")

    snapshot_topics: dict[str, dict] = {}
    ranked_items: list[dict] = []
    for row in topic_rows:
        topic_name = _clean_text(row.get("topic_name"))
        if not topic_name:
            continue
        topic_type = _clean_text(row.get("topic_type")) or "topic"
        key = f"{topic_type}:{topic_name}"
        capital_amount = _to_float(row.get("capital_inflow_amount")) or 0.0
        if row.get("capital_source") == "visibility_proxy":
            capital_score = min(_to_float(row.get("visibility_proxy_score")) or 50.0, 62.0)
        elif row.get("capital_source") == "ths_hot_reason_proxy":
            capital_score = _clamp(_to_float(row.get("capital_proxy_score")) or 58.0, 45, 78)
        else:
            capital_score = _capital_amount_score(capital_amount, row.get("capital_rank"), len(topic_rows))
        if row.get("capital_source") == "ths_hot_reason_proxy":
            capital_slope_score = _clamp(_to_float(row.get("capital_slope_proxy_score")) or 56.0, 45, 78)
            slope_warning = "同花顺热点模式，资金陡度使用强势股成交额/分钟资金代理"
        else:
            capital_slope_score, slope_warning = _snapshot_delta_score(
                previous_snapshot,
                "topics",
                key,
                "capital_inflow_amount",
                capital_amount,
            )
        if row.get("source_provider") == "ths_hot":
            attention_score = _clamp(_to_float(row.get("attention_proxy_score")) or 60.0, 45, 88)
            attention_warning = "外部流量使用同花顺强势股题材归因代理"
        else:
            attention_score, attention_warning = _topic_attention_acceleration(topic_name, attention_rows, previous_snapshot)
        conversion_score = _topic_conversion_score(topic_name, topic_type, attention_rows)
        visibility_score = _visibility_score(row.get("price_change_pct"), row.get("turnover_rate"), row.get("amount"))
        entry_power_score = _weighted_score(
            [
                (capital_score, 0.45),
                (capital_slope_score, 0.25),
                (attention_score, 0.20),
                (conversion_score, 0.10),
            ]
        )
        item_warnings = []
        if slope_warning:
            item_warnings.append(slope_warning)
        if attention_warning:
            item_warnings.append(attention_warning)
        if row.get("capital_source") == "amount_proxy":
            item_warnings.append("缺少主力净流入字段，使用成交额代理资金体量")
        elif row.get("capital_source") == "visibility_proxy":
            item_warnings.append("资金流接口不可达，使用板块成交额/可见度低置信度代理")
        elif row.get("capital_source") == "ths_hot_reason_proxy":
            item_warnings.append("板块资金流不可达，使用同花顺强势股题材归因与成交额代理")

        source_scores = {
            "eastmoney_direct": capital_score if row.get("source_provider") == "eastmoney_direct" else None,
            "eastmoney_fund": capital_score if row.get("source_provider") in {"eastmoney_direct", "akshare"} else None,
            "akshare_fund": capital_score if row.get("source_provider") == "akshare" else None,
            "ths_fund": capital_score if row.get("source_provider") == "ths" else None,
            "ths_hot": attention_score if row.get("source_provider") == "ths_hot" else None,
            "tencent_quote": capital_score if row.get("source_provider") == "ths_hot" else None,
            "eastmoney_minute_flow": capital_slope_score
            if row.get("source_provider") == "ths_hot" and provider_status.get("eastmoney_minute_flow_status") == "ok"
            else None,
            "fallback": capital_score if row.get("source_provider") == "fallback" else None,
            "eastmoney_attention": _attention_source_score(attention_rows, "eastmoney"),
            "xueqiu": _attention_source_score(attention_rows, "xueqiu"),
            "douyin": None,
        }
        risk_flags = _risk_flags(attention_score, capital_slope_score, visibility_score)
        item = {
            "rank": 0,
            "topic_name": topic_name,
            "topic_type": topic_type,
            "sector_name": topic_name,
            "sector_type": topic_type,
            "entry_power_score": entry_power_score,
            "heat_score": entry_power_score,
            "sector_heat": entry_power_score,
            "sector_heat_score": entry_power_score,
            "capital_inflow_score": capital_score,
            "capital_slope_score": capital_slope_score,
            "attention_acceleration_score": attention_score,
            "topic_conversion_score": conversion_score,
            "visibility_score": visibility_score,
            "capital_inflow_amount": capital_amount,
            "capital_inflow_rank": row.get("capital_rank"),
            "capital_window": row.get("capital_window") or "今日",
            "capital_source": row.get("capital_source") or "main_net_inflow",
            "price_change_pct": row.get("price_change_pct"),
            "source": row.get("source"),
            "source_provider": row.get("source_provider") or row.get("source"),
            "source_scores": source_scores,
            "confidence": _topic_confidence(row, attention_rows, item_warnings),
            "warnings": item_warnings,
            "risk_flags": risk_flags,
            "discussion_links": _topic_links(topic_name),
        }
        item["leaders"] = row.get("leaders") or _topic_leader_candidates(item, stock_rows, safe_leaders, warnings)
        ranked_items.append(item)
        snapshot_topics[key] = {
            "topic_name": topic_name,
            "topic_type": topic_type,
            "capital_inflow_amount": capital_amount,
            "entry_power_score": entry_power_score,
            "attention_acceleration_score": attention_score,
        }

    ranked_items.sort(key=lambda item: item.get("entry_power_score") or 0, reverse=True)
    items = []
    for index, item in enumerate(ranked_items[:safe_limit], start=1):
        item["rank"] = index
        item["related_etfs"] = _related_etfs_for_topic(
            item,
            provider_status,
            warnings,
            limit=2,
            include_quote=has_budget(0.4) and provider_status.get("tencent_quote_status") == "ok",
        )
        items.append(item)

    snapshot = {
        "timestamp": now_ts,
        "date": today,
        "topics": snapshot_topics,
        "attention": {
            str(row.get("code")): {
                "code": row.get("code"),
                "name": row.get("name"),
                "rank": row.get("rank"),
                "score": row.get("attention_score"),
                "source": row.get("source"),
            }
            for row in attention_rows
            if row.get("code")
        },
    }
    if snapshot_topics and fallback_mode != "snapshot":
        _save_attention_snapshot(snapshot)

    if not items:
        fallback_mode = "unavailable"
        _warn_once(warnings, "资金进入热榜无可用公开源数据")

    provider_status["fallback_mode"] = fallback_mode
    provider_status["has_akshare"] = _akshare_available(warnings)

    return {
        "items": items,
        "date": today,
        "window": "today+snapshot",
        "source": _attention_ranking_source(items, fallback_mode),
        "confidence": round(sum(item.get("confidence", 0.2) for item in items) / len(items), 2) if items else 0.0,
        "data_status": {
            "status": "fallback" if items and fallback_mode != "none" else "ok" if items and not warnings else "partial" if items else "degraded",
            "a_stock_data_status": provider_status.get("a_stock_data_status", "unavailable"),
            "eastmoney_direct_status": provider_status.get("eastmoney_direct_status", "unavailable"),
            "akshare_fund_status": provider_status.get("akshare_fund_status", "unavailable"),
            "akshare_status": provider_status.get("akshare_status", "unavailable"),
            "eastmoney_fund_status": "ok" if any(row.get("source_provider") in {"eastmoney_direct", "akshare", "a_stock_data"} for row in topic_rows + stock_rows) else "unavailable",
            "ths_fund_status": provider_status.get("ths_fund_status", "disabled"),
            "ths_hot_status": provider_status.get("ths_hot_status", "unavailable"),
            "tencent_quote_status": provider_status.get("tencent_quote_status", "unavailable"),
            "eastmoney_minute_flow_status": provider_status.get("eastmoney_minute_flow_status", "unavailable"),
            "proxy_status": provider_status.get("proxy_status", "not_used"),
            "fallback_mode": fallback_mode,
            "last_error": provider_status.get("last_error"),
            "last_success_at": provider_status.get("last_success_at"),
            "eastmoney_attention_status": "ok" if any(row.get("source", "").startswith("eastmoney") for row in attention_rows) else "unavailable",
            "xueqiu_status": "ok" if any(row.get("source", "").startswith("xueqiu") for row in attention_rows) else "unavailable",
            "douyin_status": "unavailable_public_not_integrated",
            "snapshot_status": "ok" if previous_snapshot else "single_snapshot",
            "warnings": warnings,
            "has_akshare": provider_status.get("has_akshare"),
            "cache_keys": _cache_snapshot_keys(),
        },
    }


def _ranking_board_list(kind: str, warnings: list[str]) -> list[dict]:
    cached_boards = _board_list(kind, warnings, fetch_on_miss=False)
    if cached_boards:
        return cached_boards
    direct_rows = _eastmoney_board_list_direct(kind, warnings)
    if direct_rows:
        return direct_rows
    key = f"board:{kind}:em"
    function_name = "stock_board_concept_name_em" if kind == "concept" else "stock_board_industry_name_em"
    df = _cached_akshare_df(
        key,
        BOARD_TTL_SECONDS,
        warnings,
        f"eastmoney {kind} boards",
        function_name,
        timeout_seconds=5.0,
    )
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    return _normalize_board_df(df, kind)


def _new_attention_provider_status() -> dict:
    return {
        "a_stock_data_status": "unavailable",
        "eastmoney_direct_status": "unavailable",
        "akshare_fund_status": "unavailable",
        "akshare_status": "unavailable",
        "ths_fund_status": "disabled" if not ENABLE_THS_FUND_FALLBACK else "unavailable",
        "ths_hot_status": "unavailable",
        "tencent_quote_status": "unavailable",
        "eastmoney_minute_flow_status": "unavailable",
        "proxy_status": "not_used",
        "fallback_mode": "none",
        "last_error": None,
        "last_success_at": None,
    }


def _snapshot_has_topics(snapshot: Optional[dict]) -> bool:
    return bool(isinstance(snapshot, dict) and isinstance(snapshot.get("topics"), dict) and snapshot.get("topics"))


def _best_attention_snapshot(snapshots: list[dict], today: str) -> Optional[dict]:
    candidates = [snap for snap in snapshots if _snapshot_has_topics(snap)]
    if not candidates:
        return None
    same_day = [snap for snap in candidates if snap.get("date") == today]
    pool = same_day or candidates
    return max(
        pool,
        key=lambda snap: (
            len(snap.get("topics") or {}),
            _to_float(snap.get("timestamp")) or 0.0,
        ),
    )


def _snapshot_fallback_topics(snapshot: dict, warnings: list[str]) -> list[dict]:
    topics = snapshot.get("topics") if isinstance(snapshot, dict) else {}
    if not isinstance(topics, dict) or not topics:
        return []
    rows = []
    for key, topic in topics.items():
        if not isinstance(topic, dict):
            continue
        topic_name = _clean_text(topic.get("topic_name") or str(key).split(":", 1)[-1])
        if not topic_name:
            continue
        topic_type = _clean_text(topic.get("topic_type") or str(key).split(":", 1)[0]) or "topic"
        entry_score = _to_float(topic.get("entry_power_score"))
        rows.append({
            "topic_name": topic_name,
            "topic_type": topic_type,
            "capital_inflow_amount": _to_float(topic.get("capital_inflow_amount")) or 0.0,
            "capital_rank": None,
            "capital_window": f"快照 {snapshot.get('date') or '--'}",
            "capital_source": "snapshot_fallback",
            "visibility_proxy_score": entry_score or 55.0,
            "price_change_pct": None,
            "turnover_rate": None,
            "amount": None,
            "source": "snapshot_fallback",
            "source_provider": "fallback",
            "leaders": [],
        })
    rows.sort(key=lambda row: row.get("visibility_proxy_score") or 0, reverse=True)
    _warn_once(warnings, "快照兜底只代表最近一次有效热榜状态，不代表实时资金净流入")
    return rows


def _ths_hot_reason_fallback_allowed() -> bool:
    if ENABLE_THS_HOT_REASON_FALLBACK:
        return True
    if a_stock_data is not None:
        return True
    return getattr(_ths_hot_reason_rows, "__module__", __name__) != __name__


def _attention_ranking_source(items: list[dict], fallback_mode: str) -> str:
    if not items:
        return "unavailable"
    if fallback_mode == "snapshot":
        return "snapshot_fallback"
    if fallback_mode == "ths_hot_reason":
        return "ths_hot_reason+tencent_quote+retail_attention_proxy"
    if fallback_mode == "visibility_proxy":
        return "visibility_proxy+retail_attention_proxy"
    providers = sorted({item.get("source_provider") for item in items if item.get("source_provider")})
    return "+".join(providers + ["retail_attention_proxy"]) if providers else "retail_attention_proxy"


def _ths_hot_reason_topics(warnings: list[str], provider_status: dict, leaders: int = 3) -> list[dict]:
    hot_rows = _ths_hot_reason_rows(warnings, provider_status)
    if not hot_rows:
        return []
    quote_map = _tencent_quote_map([row.get("code") for row in hot_rows], warnings, provider_status)
    topic_map: dict[str, dict] = {}
    for idx, row in enumerate(hot_rows):
        code = _normalize_code(row.get("code"))
        name = _clean_text(row.get("name"))
        reason = _clean_text(row.get("reason"))
        if not code or not name or not reason:
            continue
        quote = quote_map.get(code, {})
        pct = _to_float(row.get("change_pct")) if row.get("change_pct") is not None else _to_float(quote.get("change_pct"))
        amount = _to_float(quote.get("amount")) or _to_float(row.get("amount")) or 0.0
        turnover = _to_float(quote.get("turnover_rate")) or _to_float(row.get("turnover_rate"))
        for topic in _split_ths_reason(reason)[:4]:
            bucket = topic_map.setdefault(
                topic,
                {
                    "topic_name": topic,
                    "topic_type": "topic",
                    "capital_rank": 0,
                    "capital_inflow_amount": 0.0,
                    "capital_source": "ths_hot_reason_proxy",
                    "capital_window": "today_hot_proxy",
                    "price_change_values": [],
                    "turnover_values": [],
                    "amount": 0.0,
                    "source": "ths_hot_reason",
                    "source_provider": "ths_hot",
                    "rank_total": 0,
                    "stocks": [],
                },
            )
            bucket["capital_inflow_amount"] += amount
            bucket["amount"] += amount
            bucket["rank_total"] += 1
            if pct is not None:
                bucket["price_change_values"].append(pct)
            if turnover is not None:
                bucket["turnover_values"].append(turnover)
            bucket["stocks"].append(
                {
                    "code": code,
                    "name": name,
                    "reason": reason,
                    "price_change_pct": pct,
                    "amount": amount,
                    "turnover_rate": turnover,
                    "hot_rank": idx + 1,
                    "quote": quote,
                }
            )

    rows = []
    total_topics = max(1, len(topic_map))
    for _, bucket in topic_map.items():
        stocks = bucket.pop("stocks", [])
        pct_values = bucket.pop("price_change_values", [])
        turnover_values = bucket.pop("turnover_values", [])
        avg_pct = sum(pct_values) / len(pct_values) if pct_values else 0.0
        avg_turnover = sum(turnover_values) / len(turnover_values) if turnover_values else None
        count = len(stocks)
        attention_score = _clamp(50 + min(count, 8) * 6 + max(avg_pct, 0) * 1.5, 45, 88)
        amount_score = _clamp(45 + math.log10(max(bucket.get("amount") or 1, 1)) * 4.2, 45, 78)
        bucket.update(
            {
                "price_change_pct": round(avg_pct, 2),
                "turnover_rate": avg_turnover,
                "attention_proxy_score": round(attention_score, 1),
                "capital_proxy_score": round(amount_score, 1),
                "capital_slope_proxy_score": round(_clamp(48 + min(count, 8) * 3 + max(avg_pct, 0), 45, 78), 1),
                "_leader_stocks": stocks,
            }
        )
        rows.append(bucket)
    rows.sort(
        key=lambda item: (
            _to_float(item.get("attention_proxy_score")) or 0,
            _to_float(item.get("capital_proxy_score")) or 0,
            _to_float(item.get("capital_inflow_amount")) or 0,
        ),
        reverse=True,
    )
    for idx, row in enumerate(rows, start=1):
        row["capital_rank"] = idx
        row["rank_total"] = total_topics
        stocks = row.pop("_leader_stocks", [])
        row["leaders"] = _ths_hot_leaders(row.get("topic_name"), stocks, leaders, warnings, provider_status) if idx <= 10 else []
    return rows[:80]


def _ths_hot_reason_rows(warnings: list[str], provider_status: dict) -> list[dict]:
    cache_key = "ths:hot_reason:today"
    cached = ttl_cache_get(cache_key, HOT_TTL_SECONDS)
    if cached is not None:
        if cached:
            provider_status["ths_hot_status"] = "ok"
        return cached
    url_date = datetime.now().strftime("%Y-%m-%d")
    if a_stock_data is not None:
        try:
            df = a_stock_data.get_ths_hot_reason(url_date)
            rows = []
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                for _, row in df.iterrows():
                    code = _normalize_code(_first_value(row, ["代码", "code"]))
                    name = _clean_text(_first_value(row, ["名称", "name"]))
                    reason = _clean_text(_first_value(row, ["题材归因", "reason"]))
                    if not code or not name or not reason:
                        continue
                    rows.append(
                        {
                            "code": code,
                            "name": name,
                            "reason": reason,
                            "change_pct": _to_float(_first_value(row, ["涨幅%", "zhangfu", "change_pct"])),
                            "turnover_rate": _to_float(_first_value(row, ["换手率%", "huanshou", "turnover_rate"])),
                            "amount": _to_float(_first_value(row, ["成交额", "chengjiaoe", "amount"])),
                            "large_order_net": _to_float(_first_value(row, ["大单净量", "ddejingliang"])),
                            "source": "a_stock_data_ths_hot_reason",
                        }
                    )
            if rows:
                provider_status["ths_hot_status"] = "ok"
                provider_status["a_stock_data_status"] = "ok"
                provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
                return ttl_cache_set(cache_key, rows, HOT_TTL_SECONDS)
            provider_status["ths_hot_status"] = "empty"
        except Exception as exc:
            provider_status["ths_hot_status"] = "unavailable"
            provider_status["last_error"] = f"a-stock-data同花顺热点: {_friendly_provider_error(exc)}"
            _warn_once(warnings, f"a-stock-data同花顺热点不可达：{_friendly_provider_error(exc)}")

    url = f"http://zx.10jqka.com.cn/event/api/getharden/date/{url_date}/orderby/date/orderway/desc/charset/GBK/"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        data = resp.json()
        if data.get("errocode", 0) != 0:
            raise RuntimeError(data.get("errormsg") or "unknown error")
        raw_rows = data.get("data") or []
    except Exception as exc:
        provider_status["ths_hot_status"] = "unavailable"
        provider_status["last_error"] = f"同花顺热点: {_friendly_provider_error(exc)}"
        _warn_once(warnings, f"同花顺热点不可达：{_friendly_provider_error(exc)}")
        return ttl_cache_set(cache_key, [], max(45, HOT_TTL_SECONDS // 5))
    rows = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        code = _normalize_code(row.get("code"))
        name = _clean_text(row.get("name"))
        reason = _clean_text(row.get("reason"))
        if not code or not name or not reason:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "reason": reason,
                "change_pct": _to_float(row.get("zhangfu")),
                "turnover_rate": _to_float(row.get("huanshou")),
                "amount": _to_float(row.get("chengjiaoe")),
                "large_order_net": _to_float(row.get("ddejingliang")),
                "source": "ths_hot_reason",
            }
        )
    if rows:
        provider_status["ths_hot_status"] = "ok"
        provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        provider_status["ths_hot_status"] = "empty"
        _warn_once(warnings, "同花顺热点返回空数据")
    return ttl_cache_set(cache_key, rows, HOT_TTL_SECONDS)


def _split_ths_reason(reason: str) -> list[str]:
    text = _clean_text(reason)
    if not text:
        return []
    parts = re.split(r"[+＋/、,，;；|｜]+", text)
    topics = []
    stop_words = {"st", "摘帽", "公告", "年报", "一季报", "业绩", "涨停", "复牌"}
    for part in parts:
        topic = re.sub(r"^[0-9.]+[a-zA-Z]?", "", _clean_text(part)).strip()
        if len(topic) < 2:
            continue
        if _compact_name(topic) in stop_words:
            continue
        topics.append(topic[:18])
    return topics[:6]


def _tencent_quote_map(codes: list[str], warnings: list[str], provider_status: dict) -> dict[str, dict]:
    plain_codes = []
    seen = set()
    for code in codes:
        plain = _normalize_code(code)
        if plain and plain not in seen:
            seen.add(plain)
            plain_codes.append(plain)
    result: dict[str, dict] = {}
    for start in range(0, len(plain_codes), 60):
        chunk = plain_codes[start : start + 60]
        cache_key = f"tencent:quote:{','.join(chunk)}"
        cached = ttl_cache_get(cache_key, 60)
        if cached is not None:
            result.update(cached)
            continue
        if a_stock_data is not None:
            try:
                parsed = a_stock_data.get_realtime_quotes(chunk)
                if parsed:
                    provider_status["tencent_quote_status"] = "ok"
                    provider_status["a_stock_data_status"] = "ok"
                    result.update(ttl_cache_set(cache_key, parsed, 60))
                    continue
            except Exception as exc:
                _warn_once(warnings, f"a-stock-data腾讯行情不可达：{_friendly_provider_error(exc)}")
        prefixed = [_market_prefix(code) + code for code in chunk]
        try:
            resp = requests.get("https://qt.gtimg.cn/q=" + ",".join(prefixed), headers={"User-Agent": "Mozilla/5.0"}, timeout=REQUEST_TIMEOUT_SECONDS)
            text = resp.content.decode("gbk", errors="ignore")
        except Exception as exc:
            provider_status["tencent_quote_status"] = "unavailable"
            _warn_once(warnings, f"腾讯行情不可达：{_friendly_provider_error(exc)}")
            continue
        parsed = {}
        for line in text.strip().split(";"):
            if not line.strip() or "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            vals = line.split('"')[1].split("~")
            if len(vals) < 53:
                continue
            code = _normalize_code(key[2:])
            parsed[code] = {
                "name": vals[1],
                "price": _to_float(vals[3]),
                "change_pct": _to_float(vals[32]),
                "amount": (_to_float(vals[37]) or 0) * 10_000,
                "turnover_rate": _to_float(vals[38]),
                "vol_ratio": _to_float(vals[49]),
            }
        if parsed:
            provider_status["tencent_quote_status"] = "ok"
            result.update(ttl_cache_set(cache_key, parsed, 60))
    return result


def _ths_hot_leaders(topic: str, stocks: list[dict], leaders: int, warnings: list[str], provider_status: dict) -> list[dict]:
    if leaders <= 0:
        return []
    ranked = []
    candidates = sorted(
        stocks,
        key=lambda item: (_to_float(item.get("amount")) or 0, -(_to_int(item.get("hot_rank")) or 999)),
        reverse=True,
    )[: max(leaders * 2, leaders)]
    for stock in candidates:
        code = stock.get("code")
        minute_calls = int(provider_status.get("_eastmoney_minute_flow_calls") or 0)
        can_try_minute = (
            code
            and minute_calls < 3
            and provider_status.get("eastmoney_minute_flow_status") != "unavailable"
        )
        minute = _eastmoney_minute_flow_summary(code, warnings, provider_status) if can_try_minute else {}
        main_net = _to_float(minute.get("main_net"))
        amount = _to_float(stock.get("amount")) or 0.0
        pct = _to_float(stock.get("price_change_pct")) or 0.0
        capital_score = _capital_amount_score(main_net if main_net is not None else amount, None, 80)
        visibility = _visibility_score(pct, stock.get("turnover_rate"), amount)
        hot_rank_score = _score_rank(stock.get("hot_rank"), 80)
        score = _weighted_score([(capital_score, 0.35), (visibility, 0.30), (hot_rank_score, 0.25), (65, 0.10)])
        reasons = [f"同花顺题材归因命中：{topic}"]
        reasons.append("东财分钟资金流可用" if main_net is not None else "分钟资金流不可用，使用成交额代理")
        ranked.append(
            {
                "code": code,
                "name": stock.get("name"),
                "leader_label": "热度候选",
                "leader_entry_score": score,
                "capital_inflow_amount": main_net if main_net is not None else amount,
                "capital_rank": stock.get("hot_rank"),
                "attention_rank": stock.get("hot_rank"),
                "attention_change": None,
                "reasons": reasons,
                "discussion_links": _stock_links(code),
            }
        )
    ranked.sort(key=lambda item: item.get("leader_entry_score") or 0, reverse=True)
    for idx, item in enumerate(ranked[:leaders], start=1):
        item["leader_label"] = "龙一候选" if idx == 1 else "龙二候选" if idx == 2 else "龙三候选"
    return ranked[:leaders]


def _eastmoney_minute_flow_summary(code: str, warnings: list[str], provider_status: dict) -> dict:
    plain = _normalize_code(code)
    if not plain:
        return {}
    provider_status["_eastmoney_minute_flow_calls"] = int(provider_status.get("_eastmoney_minute_flow_calls") or 0) + 1
    cache_key = f"fund:minute:{plain}"
    cached = ttl_cache_get(cache_key, 60)
    if cached is not None:
        if cached:
            provider_status["eastmoney_minute_flow_status"] = "ok"
        return cached
    if a_stock_data is not None:
        try:
            summary = a_stock_data.get_minute_fund_flow_summary(plain)
            if summary:
                provider_status["eastmoney_minute_flow_status"] = "ok"
                provider_status["a_stock_data_status"] = "ok"
                return ttl_cache_set(cache_key, summary, 60)
            provider_status["eastmoney_minute_flow_status"] = "empty"
            return ttl_cache_set(cache_key, {}, 60)
        except Exception as exc:
            provider_status["eastmoney_minute_flow_status"] = "unavailable"
            _warn_once(warnings, f"{plain} a-stock-data分钟资金流不可达：{_friendly_provider_error(exc)}")
    secid = f"1.{plain}" if plain.startswith("6") else f"0.{plain}"
    params = {"secid": secid, "klt": 1, "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57"}
    try:
        resp = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
            params=params,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/", "Origin": "https://quote.eastmoney.com"},
            timeout=min(REQUEST_TIMEOUT_SECONDS, 0.8),
        )
        rows = ((resp.json().get("data") or {}).get("klines") or [])
    except Exception as exc:
        provider_status["eastmoney_minute_flow_status"] = "unavailable"
        _warn_once(warnings, f"{plain} 东财分钟资金流不可达：{_friendly_provider_error(exc)}")
        return ttl_cache_set(cache_key, {}, 60)
    main_values = []
    last = {}
    for line in rows:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        main = _to_float(parts[1])
        if main is not None:
            main_values.append(main)
            last = {"time": parts[0], "main_net": main, "small_net": _to_float(parts[2]), "mid_net": _to_float(parts[3]), "large_net": _to_float(parts[4]), "super_net": _to_float(parts[5])}
    if not main_values:
        provider_status["eastmoney_minute_flow_status"] = "empty"
        return ttl_cache_set(cache_key, {}, 60)
    summary = {**last, "main_net_total": sum(main_values), "points": len(main_values)}
    provider_status["eastmoney_minute_flow_status"] = "ok"
    return ttl_cache_set(cache_key, summary, 60)


def _eastmoney_direct_fund_flow_topics(topic_type: str, warnings: list[str], provider_status: dict) -> list[dict]:
    is_concept = topic_type == "concept"
    cache_key = f"fund:sector:{topic_type}:em:direct"
    cached = ttl_cache_get(cache_key, HOT_TTL_SECONDS)
    if cached is not None:
        if cached:
            provider_status["eastmoney_direct_status"] = "ok"
        return cached
    if a_stock_data is not None:
        try:
            normalized = a_stock_data.get_fund_flow_topics(topic_type)
            if normalized:
                provider_status["eastmoney_direct_status"] = "ok"
                provider_status["a_stock_data_status"] = "ok"
                provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
                return ttl_cache_set(cache_key, normalized, HOT_TTL_SECONDS)
            provider_status["eastmoney_direct_status"] = "empty"
        except Exception as exc:
            _record_provider_error(provider_status, warnings, f"a-stock-data{'概念' if is_concept else '行业'}资金流", exc)
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": "m:90+t:3" if is_concept else "m:90+t:2",
        "fields": "f12,f14,f3,f8,f20,f62,f66,f69,f72,f75,f78,f81,f84,f87,f124",
    }
    rows = _eastmoney_direct_clist(cache_key, params, warnings, provider_status, f"东财直连{'概念' if is_concept else '行业'}资金流")
    if not rows:
        return []
    normalized = []
    total = len(rows)
    for idx, row in enumerate(rows[:80]):
        if not isinstance(row, dict):
            continue
        name = _clean_text(row.get("f14"))
        if not name:
            continue
        capital_amount, capital_source = _eastmoney_direct_capital_from_row(row)
        amount = _to_float(row.get("f20"))
        if capital_amount is None:
            capital_amount = amount or 0.0
            capital_source = "amount_proxy"
        normalized.append(
            {
                "topic_name": name,
                "topic_type": topic_type,
                "capital_rank": idx + 1,
                "capital_inflow_amount": capital_amount,
                "capital_source": capital_source,
                "capital_window": "今日",
                "price_change_pct": _to_float(row.get("f3")),
                "turnover_rate": _to_float(row.get("f8")),
                "amount": amount,
                "source": "eastmoney_direct_fund_flow",
                "source_provider": "eastmoney_direct",
                "rank_total": total,
            }
        )
    if normalized:
        provider_status["eastmoney_direct_status"] = "ok"
        provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
    return ttl_cache_set(cache_key, normalized, HOT_TTL_SECONDS)


def _eastmoney_direct_fund_flow_stocks(warnings: list[str], provider_status: dict) -> list[dict]:
    cache_key = "fund:stock:em:direct:today"
    cached = ttl_cache_get(cache_key, HOT_TTL_SECONDS)
    if cached is not None:
        if cached:
            provider_status["eastmoney_direct_status"] = "ok"
        return cached
    if a_stock_data is not None:
        try:
            normalized = a_stock_data.get_fund_flow_stocks()
            if normalized:
                provider_status["eastmoney_direct_status"] = "ok"
                provider_status["a_stock_data_status"] = "ok"
                provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
                return ttl_cache_set(cache_key, normalized, HOT_TTL_SECONDS)
            provider_status["eastmoney_direct_status"] = "empty"
        except Exception as exc:
            _record_provider_error(provider_status, warnings, "a-stock-data个股资金流", exc)
    params = {
        "pn": "1",
        "pz": "200",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f2,f3,f20,f62,f66,f69,f72,f75,f78,f81,f84,f87,f124",
    }
    rows = _eastmoney_direct_clist(cache_key, params, warnings, provider_status, "东财直连个股资金流")
    if not rows:
        return []
    normalized = []
    total = len(rows)
    for idx, row in enumerate(rows[:200]):
        if not isinstance(row, dict):
            continue
        code = _normalize_code(row.get("f12"))
        name = _clean_text(row.get("f14"))
        if not code or not name:
            continue
        capital_amount, capital_source = _eastmoney_direct_capital_from_row(row)
        amount = _to_float(row.get("f20"))
        if capital_amount is None:
            capital_amount = amount or 0.0
            capital_source = "amount_proxy"
        normalized.append(
            {
                "code": code,
                "name": name,
                "capital_rank": idx + 1,
                "capital_inflow_amount": capital_amount,
                "capital_source": capital_source,
                "price_change_pct": _to_float(row.get("f3")),
                "amount": amount,
                "source": "eastmoney_direct_fund_flow",
                "source_provider": "eastmoney_direct",
                "rank_total": total,
            }
        )
    if normalized:
        provider_status["eastmoney_direct_status"] = "ok"
        provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
    return ttl_cache_set(cache_key, normalized, HOT_TTL_SECONDS)


def _eastmoney_direct_clist(
    cache_key: str,
    params: dict,
    warnings: list[str],
    provider_status: dict,
    label: str,
) -> list[dict]:
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": "", "https": ""}
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"})
    try:
        resp = session.get("https://push2.eastmoney.com/api/qt/clist/get", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        rows = data.get("diff") or []
        if not isinstance(rows, list):
            rows = list(rows.values()) if isinstance(rows, dict) else []
        if rows:
            provider_status["eastmoney_direct_status"] = "ok"
            provider_status["proxy_status"] = "not_used"
            return rows
        provider_status["eastmoney_direct_status"] = "empty"
        _warn_once(warnings, f"{label}返回空数据")
    except Exception as exc:
        _record_provider_error(provider_status, warnings, label, exc)
    return ttl_cache_set(cache_key, [], max(45, HOT_TTL_SECONDS // 5))


def _eastmoney_direct_capital_from_row(row: dict) -> tuple[Optional[float], str]:
    for field, source in [
        ("f62", "main_net_inflow"),
        ("f66", "large_order_net_inflow"),
        ("f72", "large_order_net_inflow"),
        ("f78", "large_order_net_inflow"),
        ("f84", "large_order_net_inflow"),
    ]:
        value = _to_float(row.get(field))
        if value is not None:
            return value, source
    return None, "missing"


def _record_provider_error(provider_status: dict, warnings: list[str], label: str, exc: Exception) -> None:
    message = _friendly_provider_error(exc)
    provider_status["eastmoney_direct_status"] = "unavailable"
    provider_status["last_error"] = f"{label}: {message}"
    if "代理" in message or "Proxy" in message:
        provider_status["proxy_status"] = "proxy_error"
    elif "断开" in message:
        provider_status["proxy_status"] = "remote_disconnected"
    elif "超时" in message:
        provider_status["proxy_status"] = "timeout"
    _warn_once(warnings, f"{label}不可达：{message}")


def _friendly_provider_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "proxy" in lowered:
        return "东财公开接口被当前网络/代理断开"
    if "remotedisconnected" in lowered or "remote end closed" in lowered or "connection aborted" in lowered:
        return "东财公开接口连接被远端断开"
    if "timed out" in lowered or "timeout" in lowered:
        return "东财公开接口请求超时"
    return text[:120]


def _visibility_fallback_topics(warnings: list[str], provider_status: dict) -> list[dict]:
    concept_boards = _ranking_board_list("concept", warnings)
    industry_boards = _ranking_board_list("industry", warnings)
    rows = _top_boards(concept_boards, "concept") + _top_boards(industry_boards, "industry")
    rows = sorted(rows, key=lambda item: item.get("heat_hint") or 0, reverse=True)
    if not rows:
        provider_status["fallback_mode"] = "unavailable"
        return []
    provider_status["fallback_mode"] = "visibility_proxy"
    normalized = []
    seen: set[tuple[str, str]] = set()
    for row in rows[:80]:
        name = _clean_text(row.get("sector_name"))
        topic_type = _clean_text(row.get("sector_type")) or "topic"
        key = (topic_type, name)
        if not name or key in seen:
            continue
        seen.add(key)
        visibility_score = _clamp(row.get("heat_hint") or 50, 0, 100)
        normalized.append(
            {
                "topic_name": name,
                "topic_type": topic_type,
                "capital_rank": len(normalized) + 1,
                "capital_inflow_amount": _to_float(row.get("amount")) or 0.0,
                "capital_source": "visibility_proxy",
                "capital_window": "latest_proxy",
                "price_change_pct": row.get("change_pct"),
                "turnover_rate": row.get("turnover_rate"),
                "amount": row.get("amount"),
                "visibility_proxy_score": visibility_score,
                "source": "visibility_proxy",
                "source_provider": "fallback",
                "rank_total": len(rows),
            }
        )
    return normalized


def _fund_flow_topics(sector_type: str, warnings: list[str], provider_status: Optional[dict] = None) -> list[dict]:
    topic_type = "concept" if sector_type == "概念资金流" else "industry"
    provider_status = provider_status if provider_status is not None else _new_attention_provider_status()
    direct_rows = _eastmoney_direct_fund_flow_topics(topic_type, warnings, provider_status)
    if direct_rows:
        provider_status["eastmoney_direct_status"] = "ok"
        provider_status["last_success_at"] = provider_status.get("last_success_at") or datetime.now().isoformat(timespec="seconds")
        return direct_rows

    df = _cached_akshare_df(
        f"fund:sector:{sector_type}:em",
        HOT_TTL_SECONDS,
        warnings,
        f"eastmoney {sector_type}",
        "stock_sector_fund_flow_rank",
        {"indicator": "今日", "sector_type": sector_type},
        timeout_seconds=1.0,
    )
    source = "eastmoney_fund_flow"
    source_provider = "akshare"
    if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
        provider_status["akshare_fund_status"] = "ok"
        provider_status["akshare_status"] = "ok"
        provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        provider_status.setdefault("akshare_fund_status", "unavailable")
        provider_status["akshare_status"] = "unavailable"
    if ENABLE_THS_FUND_FALLBACK and (df is None or not isinstance(df, pd.DataFrame) or df.empty):
        ths_function = "stock_fund_flow_concept" if topic_type == "concept" else "stock_fund_flow_industry"
        df = _cached_akshare_df(
            f"fund:sector:{topic_type}:ths",
            HOT_TTL_SECONDS,
            warnings,
            f"ths {sector_type}",
            ths_function,
            {"symbol": "即时"},
            timeout_seconds=2.0,
        )
        source = "ths_fund_flow"
        source_provider = "ths"
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            provider_status["ths_fund_status"] = "ok"
            provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
        else:
            provider_status["ths_fund_status"] = "unavailable"
    elif df is None or not isinstance(df, pd.DataFrame) or df.empty:
        _warn_once(warnings, "同花顺资金流备选源默认不阻塞首屏，可设置 ENABLE_THS_FUND_FALLBACK=1 开启")
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    rows = []
    total = len(df)
    for idx, row in df.head(80).iterrows():
        name = _clean_text(_first_value(row, ["名称", "板块名称", "行业名称", "概念名称", "name"]))
        if not name:
            continue
        capital_amount, capital_source = _capital_inflow_from_row(row)
        if capital_amount is None:
            amount = _to_float(_first_value(row, ["成交额", "今日成交额", "amount"]))
            capital_amount = amount or 0.0
            capital_source = "amount_proxy"
        rows.append(
            {
                "topic_name": name,
                "topic_type": topic_type,
                "capital_rank": _to_int(_first_value(row, ["序号", "排名", "index"])) or idx + 1,
                "capital_inflow_amount": capital_amount,
                "capital_source": capital_source,
                "capital_window": "今日",
                "price_change_pct": _to_float(_first_value(row, ["今日涨跌幅", "涨跌幅", "涨幅", "tradezdf"])),
                "turnover_rate": _to_float(_first_value(row, ["换手率", "换手", "turnover_rate"])),
                "amount": _to_float(_first_value(row, ["成交额", "今日成交额", "amount"])),
                "source": source,
                "source_provider": source_provider,
                "rank_total": total,
            }
        )
    return rows


def _fund_flow_stocks(warnings: list[str], provider_status: Optional[dict] = None) -> list[dict]:
    provider_status = provider_status if provider_status is not None else _new_attention_provider_status()
    direct_rows = _eastmoney_direct_fund_flow_stocks(warnings, provider_status)
    if direct_rows:
        provider_status["eastmoney_direct_status"] = "ok"
        provider_status["last_success_at"] = provider_status.get("last_success_at") or datetime.now().isoformat(timespec="seconds")
        return direct_rows

    df = _cached_akshare_df(
        "fund:stock:em:today",
        HOT_TTL_SECONDS,
        warnings,
        "eastmoney stock fund flow",
        "stock_individual_fund_flow_rank",
        {"indicator": "今日"},
        timeout_seconds=1.0,
    )
    source = "eastmoney_fund_flow"
    source_provider = "akshare"
    if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
        provider_status["akshare_fund_status"] = "ok"
        provider_status["akshare_status"] = "ok"
        provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
    if ENABLE_THS_FUND_FALLBACK and (df is None or not isinstance(df, pd.DataFrame) or df.empty):
        df = _cached_akshare_df(
            "fund:stock:ths:today",
            HOT_TTL_SECONDS,
            warnings,
            "ths stock fund flow",
            "stock_fund_flow_individual",
            {"symbol": "即时"},
            timeout_seconds=2.0,
        )
        source = "ths_fund_flow"
        source_provider = "ths"
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            provider_status["ths_fund_status"] = "ok"
            provider_status["last_success_at"] = datetime.now().isoformat(timespec="seconds")
        else:
            provider_status["ths_fund_status"] = "unavailable"
    elif df is None or not isinstance(df, pd.DataFrame) or df.empty:
        _warn_once(warnings, "同花顺个股资金流备选源默认不阻塞首屏，可设置 ENABLE_THS_FUND_FALLBACK=1 开启")
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    rows = []
    total = len(df)
    for idx, row in df.head(200).iterrows():
        code = _normalize_code(_first_value(row, ["代码", "股票代码", "code", "证券代码"]))
        name = _clean_text(_first_value(row, ["名称", "股票名称", "name"]))
        if not code or not name:
            continue
        capital_amount, capital_source = _capital_inflow_from_row(row)
        if capital_amount is None:
            amount = _to_float(_first_value(row, ["成交额", "今日成交额", "amount"]))
            capital_amount = amount or 0.0
            capital_source = "amount_proxy"
        rows.append(
            {
                "code": code,
                "name": name,
                "capital_rank": _to_int(_first_value(row, ["序号", "排名", "index"])) or idx + 1,
                "capital_inflow_amount": capital_amount,
                "capital_source": capital_source,
                "price_change_pct": _to_float(_first_value(row, ["今日涨跌幅", "涨跌幅", "涨幅", "zdf"])),
                "source": source,
                "source_provider": source_provider,
                "rank_total": total,
            }
        )
    return rows


def _attention_stock_rows(warnings: list[str]) -> list[dict]:
    rows: list[dict] = []
    if a_stock_data is not None:
        try:
            for idx, row in enumerate(a_stock_data.get_fund_flow_stocks(limit=100) or []):
                code = _normalize_code(row.get("code"))
                name = _clean_text(row.get("name"))
                if not code and not name:
                    continue
                rank = _to_int(row.get("capital_rank")) or idx + 1
                rows.append(
                    {
                        "code": code,
                        "name": name,
                        "rank": rank,
                        "attention_score": _score_rank(rank, 100),
                        "source": "a_stock_data_fund_attention_proxy",
                    }
                )
            if rows:
                return rows
        except Exception as exc:
            _warn_once(warnings, f"a-stock-data个股关注代理不可达：{_friendly_provider_error(exc)}")
    em_rank = _cached_akshare_df("hot:rank:em", HOT_TTL_SECONDS, warnings, "eastmoney hot rank", "stock_hot_rank_em")
    if em_rank is not None and isinstance(em_rank, pd.DataFrame) and not em_rank.empty:
        for idx, row in em_rank.head(100).iterrows():
            code = _normalize_code(_first_value(row, ["代码", "股票代码", "code", "证券代码"]))
            name = _clean_text(_first_value(row, ["股票名称", "名称", "name"]))
            rank = _to_int(_first_value(row, ["当前排名", "排名", "rank"])) or idx + 1
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "rank": rank,
                    "attention_score": _score_rank(rank, 100),
                    "source": "eastmoney_hot_rank",
                }
            )
    for endpoint_name, source in [
        ("stock_hot_follow_xq", "xueqiu_follow"),
        ("stock_hot_tweet_xq", "xueqiu_discussion"),
    ]:
        df = _cached_akshare_df(
            f"hot:{source}",
            HOT_TTL_SECONDS,
            warnings,
            source,
            endpoint_name,
            {"symbol": "最热门"},
        )
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for idx, row in df.head(80).iterrows():
            code = _normalize_code(_first_value(row, ["代码", "股票代码", "code", "symbol", "证券代码"]))
            name = _clean_text(_first_value(row, ["名称", "股票名称", "name", "股票简称"]))
            if not code and not name:
                continue
            rank = _to_int(_first_value(row, ["排名", "rank", "序号"])) or idx + 1
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "rank": rank,
                    "attention_score": _score_rank(rank, 80),
                    "source": source,
                }
            )
    return rows


def _capital_inflow_from_row(row: Any) -> tuple[Optional[float], str]:
    fields = [
        "今日主力净流入-净额",
        "5日主力净流入-净额",
        "3日主力净流入-净额",
        "10日主力净流入-净额",
        "主力净流入净额",
        "主力净流入",
        "净额",
        "今日超大单净流入-净额",
        "今日大单净流入-净额",
        "资金净流入",
        "zljlr",
    ]
    for field in fields:
        value = _to_float(_first_value(row, [field]))
        if value is not None:
            source = "main_net_inflow" if "主力" in field or field in {"净额", "zljlr"} else "large_order_net_inflow"
            return value, source
    return None, "missing"


def _capital_amount_score(amount: Optional[float], rank: Optional[int], total: int) -> float:
    rank_score = _score_rank(rank, max(total, 80)) if rank else 50.0
    if amount is None:
        return rank_score
    signed = float(amount)
    if signed <= 0:
        amount_score = _clamp(38 + signed / 100_000_000 * 6, 0, 48)
    else:
        amount_score = _clamp(52 + math.log10(max(signed, 1_000_000) / 1_000_000) * 13, 52, 100)
    return _weighted_score([(amount_score, 0.70), (rank_score, 0.30)])


def _snapshot_delta_score(
    previous_snapshot: Optional[dict],
    group: str,
    key: str,
    field: str,
    current_value: Optional[float],
) -> tuple[float, str]:
    if not previous_snapshot:
        return 50.0, "单次快照，资金进入陡度暂用中性分"
    try:
        previous_value = (((previous_snapshot.get(group) or {}).get(key) or {}).get(field))
    except AttributeError:
        previous_value = None
    previous_num = _to_float(previous_value)
    current_num = _to_float(current_value)
    if previous_num is None or current_num is None:
        return 50.0, "缺少历史快照字段，资金进入陡度暂用中性分"
    if previous_num == 0:
        if current_num > 0:
            return 68.0, ""
        return 50.0, ""
    ratio = current_num / previous_num
    score = _clamp(50 + (ratio - 1) * 28, 0, 100)
    return round(score, 1), ""


def _topic_attention_acceleration(
    topic_name: str,
    attention_rows: list[dict],
    previous_snapshot: Optional[dict],
) -> tuple[float, str]:
    topic_key = _compact_name(topic_name)
    hits = [
        row for row in attention_rows
        if topic_key and (topic_key in _compact_name(row.get("name")) or _compact_name(row.get("name")) in topic_key)
    ]
    current_score = 50.0
    if hits:
        current_score = max(_to_float(row.get("attention_score")) or 50 for row in hits)
    if not previous_snapshot:
        return current_score if hits else 50.0, "单次快照，外部流量增速暂用当前热度代理"
    return current_score, "" if hits else "未命中东财/雪球个股热榜，外部流量增速使用中性分"


def _topic_conversion_score(topic_name: str, topic_type: str, attention_rows: list[dict]) -> float:
    name = _clean_text(topic_name)
    score = 58 if topic_type == "concept" else 50
    if len(name) <= 6:
        score += 8
    hot_tokens = ["机器人", "ai", "人工智能", "算力", "军工", "低空", "国产", "芯片", "新能源", "半导体"]
    compact = _compact_name(name)
    if any(_compact_name(token) in compact for token in hot_tokens):
        score += 10
    if any(compact and compact in _compact_name(row.get("name")) for row in attention_rows):
        score += 7
    return round(_clamp(score, 0, 100), 1)


def _visibility_score(change_pct: Optional[float], turnover_rate: Optional[float], amount: Optional[float]) -> float:
    change_score = _clamp(50 + (_to_float(change_pct) or 0) * 5, 0, 100)
    turnover_score = _clamp(45 + (_to_float(turnover_rate) or 0) * 4, 0, 100) if turnover_rate is not None else 50
    amount_score = 50
    amount_num = _to_float(amount)
    if amount_num and amount_num > 0:
        amount_score = _clamp(45 + math.log10(max(amount_num, 1)) * 5, 0, 100)
    return _weighted_score([(change_score, 0.45), (turnover_score, 0.25), (amount_score, 0.30)])


def _risk_flags(attention_score: float, capital_slope_score: float, visibility_score: float) -> list[str]:
    flags = []
    if attention_score >= 75 and capital_slope_score <= 45:
        flags.append("高热分歧")
    if visibility_score >= 82 and capital_slope_score <= 45:
        flags.append("涨幅可见度高但资金转弱")
    if capital_slope_score >= 65 and attention_score >= 55:
        flags.append("资金进入增强")
    return flags


def _topic_confidence(row: dict, attention_rows: list[dict], warnings: list[str]) -> float:
    score = 0.40
    if row.get("source_provider") == "eastmoney_direct":
        score += 0.28
    elif row.get("source_provider") == "akshare" or row.get("source") == "eastmoney_fund_flow":
        score += 0.25
    elif row.get("source_provider") == "ths" or row.get("source") == "ths_fund_flow":
        score += 0.18
    elif row.get("source_provider") == "ths_hot":
        score += 0.14
    elif row.get("source_provider") == "fallback":
        score -= 0.18
    if row.get("capital_source") not in {"amount_proxy", "visibility_proxy", "ths_hot_reason_proxy"}:
        score += 0.12
    elif row.get("capital_source") == "ths_hot_reason_proxy":
        score += 0.04
    elif row.get("capital_source") == "visibility_proxy":
        score -= 0.10
    if attention_rows:
        score += 0.10
    score -= min(0.20, len(warnings) * 0.04)
    return round(_clamp(score, 0.15, 0.92), 2)


def _attention_source_score(attention_rows: list[dict], source_prefix: str) -> Optional[float]:
    scores = [
        _to_float(row.get("attention_score"))
        for row in attention_rows
        if str(row.get("source") or "").startswith(source_prefix)
    ]
    scores = [score for score in scores if score is not None]
    return round(max(scores), 1) if scores else None


def _topic_leader_candidates(topic: dict, stock_rows: list[dict], leaders: int, warnings: list[str]) -> list[dict]:
    if leaders <= 0 or not stock_rows:
        return []
    constituent_codes = _topic_constituent_codes(topic, warnings)
    if not constituent_codes and topic.get("topic_type") in {"concept", "industry"}:
        _warn_once(warnings, f"{topic.get('topic_name')} 板块成分股不可用，龙头数据不足")
        return []
    topic_name = _compact_name(topic.get("topic_name"))
    candidates = []
    for row in stock_rows[:120]:
        code = row.get("code") or _normalize_code(_first_value(row, ["代码", "股票代码", "code", "证券代码"]))
        name = row.get("name") or _clean_text(_first_value(row, ["名称", "股票名称", "name", "股票简称"]))
        if constituent_codes and code not in constituent_codes:
            continue
        relevance = _stock_topic_relevance(name, topic_name)
        if relevance <= 0 and topic.get("topic_type") == "concept" and not constituent_codes:
            continue
        capital_amount = _to_float(row.get("capital_inflow_amount"))
        capital_source = row.get("capital_source")
        if capital_amount is None:
            capital_amount, capital_source = _capital_inflow_from_row(row)
        if capital_amount is None:
            amount = _to_float(_first_value(row, ["成交额", "今日成交额", "amount"]))
            capital_amount = amount or 0
            capital_source = "amount_proxy"
        capital_rank = row.get("capital_rank") or _to_int(_first_value(row, ["序号", "排名", "index"]))
        price_change = row.get("price_change_pct")
        if price_change is None:
            price_change = _to_float(_first_value(row, ["今日涨跌幅", "涨跌幅", "涨幅", "zdf"]))
        capital_score = _capital_amount_score(capital_amount, capital_rank, row.get("rank_total") or len(stock_rows))
        visibility = _visibility_score(price_change, None, capital_amount)
        attention = _stock_attention_summary(code, name, warnings)
        attention_rank = attention.get("rank")
        attention_change = attention.get("rank_change")
        attention_score = attention.get("score", 50.0)
        leader_score = _weighted_score(
            [
                (capital_score, 0.35),
                (capital_score, 0.25),
                (attention_score, 0.20),
                (50 + relevance * 50, 0.12),
                (visibility, 0.08),
            ]
        )
        reasons = []
        if capital_source == "amount_proxy":
            reasons.append("资金净流入缺失，使用成交额代理")
        else:
            reasons.append("个股资金净流入靠前")
        if attention_rank:
            reasons.append(f"东财/雪球关注排名{attention_rank}")
        if relevance > 0:
            reasons.append("名称/题材关联度较高")
        if visibility >= 70:
            reasons.append("涨幅榜可见度较高，仅作辅助")
        label = "热度候选"
        if topic.get("confidence", 0) >= 0.55 and capital_source != "amount_proxy" and capital_score >= 60 and attention_score >= 50:
            rank_order = len(candidates) + 1
            label = "龙一候选" if rank_order == 1 else "龙二候选" if rank_order == 2 else "龙三候选"
        candidates.append(
            {
                "code": code,
                "name": name,
                "leader_label": label,
                "leader_entry_score": leader_score,
                "capital_inflow_amount": capital_amount,
                "capital_rank": capital_rank,
                "attention_rank": attention_rank,
                "attention_change": attention_change,
                "reasons": reasons,
                "discussion_links": _stock_links(code),
            }
        )
    candidates.sort(key=lambda item: item.get("leader_entry_score") or 0, reverse=True)
    for idx, item in enumerate(candidates[:leaders], start=1):
        if item["leader_label"] != "热度候选":
            item["leader_label"] = "龙一候选" if idx == 1 else "龙二候选" if idx == 2 else "龙三候选"
    return candidates[:leaders]


def _related_etfs_for_topic(
    topic: dict,
    provider_status: dict,
    warnings: list[str],
    limit: int = 2,
    include_quote: bool = True,
) -> list[dict]:
    topic_name = _clean_text(topic.get("topic_name") or topic.get("sector_name"))
    if not topic_name or limit <= 0:
        return []
    compact_topic = _compact_name(topic_name)
    scored = []
    for order, etf in enumerate(RELATED_ETF_CATALOG):
        relevance, matched_keywords = _etf_topic_relevance(compact_topic, etf.get("keywords") or [])
        if relevance <= 0:
            continue
        scored.append(
            {
                "code": etf["code"],
                "name": etf["name"],
                "matched_keywords": matched_keywords,
                "relevance_score": relevance,
                "_catalog_order": order,
            }
        )
    if not scored:
        return []

    quote_map = _tencent_quote_map([item["code"] for item in scored], warnings, provider_status) if include_quote else {}
    for item in scored:
        quote = quote_map.get(item["code"]) or {}
        amount = _to_float(quote.get("amount"))
        change_pct = _to_float(quote.get("change_pct"))
        visibility_score = _visibility_score(change_pct, quote.get("turnover_rate"), amount) if quote else 50.0
        item.update(
            {
                "name": _clean_text(quote.get("name")) or item["name"],
                "price": _round_or_none(quote.get("price"), 3),
                "change_pct": _round_or_none(change_pct, 2),
                "amount": amount,
                "turnover_rate": _round_or_none(quote.get("turnover_rate"), 2),
                "visibility_score": visibility_score,
                "source": "keyword_map+tencent_quote" if quote else "keyword_map",
                "discussion_links": _stock_links(item["code"]),
            }
        )
        item["etf_entry_score"] = _weighted_score(
            [
                (item["relevance_score"], 0.68),
                (visibility_score, 0.32),
            ]
        )
    scored.sort(
        key=lambda item: (
            item.get("etf_entry_score") or 0,
            item.get("amount") or 0,
            -item.get("_catalog_order", 999),
        ),
        reverse=True,
    )
    result = []
    seen_codes = set()
    for item in scored:
        if item["code"] in seen_codes:
            continue
        seen_codes.add(item["code"])
        clean = {key: value for key, value in item.items() if not key.startswith("_")}
        clean["etf_label"] = f"相关ETF{len(result) + 1}"
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _etf_topic_relevance(compact_topic: str, keywords: list[str]) -> tuple[float, list[str]]:
    if not compact_topic:
        return 0.0, []
    best = 0.0
    matched = []
    for keyword in keywords:
        compact_keyword = _compact_name(keyword)
        if not compact_keyword:
            continue
        score = 0.0
        if compact_keyword == compact_topic:
            score = 100.0
        elif compact_keyword in compact_topic or compact_topic in compact_keyword:
            score = 92.0 if len(compact_keyword) >= 2 else 0.0
        else:
            overlap = len(set(compact_keyword) & set(compact_topic))
            base = max(1, min(len(compact_keyword), len(compact_topic)))
            if overlap >= 2:
                score = _clamp((overlap / base) * 72, 0, 72)
        if score >= 45:
            matched.append(_clean_text(keyword))
            best = max(best, score)
    return round(best, 1), matched[:4]


def _topic_constituent_codes(topic: dict, warnings: list[str]) -> set[str]:
    topic_name = _clean_text(topic.get("topic_name"))
    topic_type = _clean_text(topic.get("topic_type"))
    if not topic_name or topic_type not in {"concept", "industry"}:
        return set()
    function_name = "stock_board_concept_cons_em" if topic_type == "concept" else "stock_board_industry_cons_em"
    df = _cached_akshare_df(
        f"constituents:{topic_type}:{topic_name}",
        BOARD_TTL_SECONDS,
        warnings,
        f"eastmoney {topic_name} constituents",
        function_name,
        {"symbol": topic_name},
        timeout_seconds=4.0,
    )
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return set()
    codes = set()
    for _, row in df.iterrows():
        code = _normalize_code(_first_value(row, ["代码", "股票代码", "code", "证券代码"]))
        if code:
            codes.add(code)
    return codes


def _stock_attention_summary(code: Any, stock_name: Any, warnings: list[str]) -> dict:
    plain = _normalize_code(code)
    name = _clean_text(stock_name)
    em_rank = _eastmoney_hot_rank(plain, warnings) if plain else {}
    snowball_hits = _snowball_hot_hits(plain, name, warnings) if plain or name else []
    rank = em_rank.get("rank")
    rank_change = em_rank.get("rank_change")
    score_parts = []
    if rank:
        score_parts.append(_score_rank(rank, 100))
    if snowball_hits:
        score_parts.append(min(100, 55 + len(snowball_hits) * 12))
    return {
        "rank": rank,
        "rank_change": rank_change,
        "score": round(sum(score_parts) / len(score_parts), 1) if score_parts else 50.0,
        "snowball_hits": snowball_hits,
    }


def _stock_topic_relevance(stock_name: Any, compact_topic: str) -> float:
    name = _compact_name(stock_name)
    if not name or not compact_topic:
        return 0.0
    if compact_topic in name or name in compact_topic:
        return 1.0
    overlap = len(set(name) & set(compact_topic))
    return _clamp(overlap / max(1, min(len(name), len(compact_topic))), 0, 0.8)


def _topic_links(topic_name: str) -> dict:
    encoded = str(topic_name or "").strip()
    return {
        "eastmoney_search": f"https://so.eastmoney.com/web/s?keyword={encoded}",
        "xueqiu_search": f"https://xueqiu.com/k?q={encoded}",
    }


def _stock_links(code: Any) -> dict:
    plain = _normalize_code(code)
    if not plain:
        return {}
    prefix = _market_prefix(plain).upper()
    return {
        "eastmoney_guba": f"https://guba.eastmoney.com/list,{plain}.html",
        "eastmoney_quote": f"https://quote.eastmoney.com/{prefix.lower()}{plain}.html",
        "xueqiu": f"https://xueqiu.com/S/{prefix}{plain}",
    }


def _market_prefix(code: Any) -> str:
    plain = _normalize_code(code)
    if plain.startswith(("5", "6", "9")):
        return "sh"
    if plain.startswith(("4", "8")):
        return "bj"
    return "sz"


def _load_attention_snapshots() -> list[dict]:
    snapshots: list[dict] = []
    if get_attention_heat_snapshots is not None:
        try:
            snapshots.extend(get_attention_heat_snapshots(limit=20))
        except Exception:
            pass
    try:
        if os.path.exists(ATTENTION_SNAPSHOT_FILE):
            with open(ATTENTION_SNAPSHOT_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            file_snapshots = data if isinstance(data, list) else data.get("snapshots", [])
            snapshots.extend(snap for snap in file_snapshots if isinstance(snap, dict))
    except Exception:
        pass
    valid = [snap for snap in snapshots if _snapshot_has_topics(snap)]
    valid.sort(key=lambda snap: _to_float(snap.get("timestamp")) or 0.0)
    return valid[-20:]


def _save_attention_snapshot(snapshot: dict) -> None:
    if save_attention_heat_snapshot is not None:
        try:
            save_attention_heat_snapshot(snapshot)
            return
        except Exception:
            pass
    try:
        snapshots = _load_attention_snapshots()
        snapshots.append(snapshot)
        snapshots = snapshots[-20:]
        os.makedirs(os.path.dirname(ATTENTION_SNAPSHOT_FILE), exist_ok=True)
        tmp_path = f"{ATTENTION_SNAPSHOT_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(snapshots, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, ATTENTION_SNAPSHOT_FILE)
    except Exception:
        return


def _eastmoney_board_list_direct(kind: str, warnings: list[str]) -> list[dict]:
    cache_key = f"board:{kind}:em:direct"
    cached = ttl_cache_get(cache_key, BOARD_TTL_SECONDS)
    if cached is not None:
        return cached
    if a_stock_data is not None:
        try:
            rows = a_stock_data.get_board_ranking(kind)
            if rows:
                normalized = []
                total = max(1, len(rows))
                for index, row in enumerate(rows):
                    item = dict(row)
                    item["source"] = item.get("source") or "a_stock_data_eastmoney_board"
                    item["rank"] = item.get("rank") or index + 1
                    item["rank_percentile"] = item.get("rank_percentile") or round(1 - (index / total), 4)
                    item["heat_hint"] = item.get("heat_hint") or _score_board_row(
                        item.get("rank"),
                        total,
                        item.get("change_pct"),
                        item.get("turnover_rate"),
                        item.get("amount"),
                    )
                    normalized.append(item)
                return ttl_cache_set(cache_key, normalized, BOARD_TTL_SECONDS)
        except Exception as exc:
            _warn_once(warnings, f"a-stock-data {kind} boards failed: {exc}")
    is_concept = kind == "concept"
    url = "https://79.push2.eastmoney.com/api/qt/clist/get" if is_concept else "https://17.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12" if is_concept else "f3",
        "fs": "m:90 t:3 f:!50" if is_concept else "m:90 t:2 f:!50",
        "fields": "f3,f8,f12,f14,f20,f104,f105,f128,f136",
    }
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": "", "https": ""}
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/center/boardlist.html"})
    try:
        resp = session.get(url, params=params, timeout=5.0)
        data = resp.json().get("data") or {}
        rows = data.get("diff") or []
    except Exception as exc:
        _warn_once(warnings, f"eastmoney {kind} boards direct failed: {exc}")
        return ttl_cache_set(cache_key, [], max(60, BOARD_TTL_SECONDS // 6))

    normalized = []
    total = max(1, len(rows))
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = row.get("f14")
        if not name:
            continue
        rank = index + 1
        change_pct = _to_float(row.get("f3"))
        turnover_rate = _to_float(row.get("f8"))
        amount = _to_float(row.get("f20"))
        heat_hint = _score_board_row(rank, total, change_pct, turnover_rate, amount)
        normalized.append(
            {
                "sector_name": str(name),
                "sector_type": kind,
                "source": "eastmoney_board_direct",
                "rank": rank,
                "rank_percentile": round(1 - ((rank - 1) / total), 4),
                "change_pct": change_pct,
                "turnover_rate": turnover_rate,
                "amount": amount,
                "heat_hint": heat_hint,
                "leading_stock": row.get("f128"),
                "leading_stock_change_pct": _to_float(row.get("f136")),
                "up_count": _to_int(row.get("f104")),
                "down_count": _to_int(row.get("f105")),
            }
        )
    return ttl_cache_set(cache_key, normalized, BOARD_TTL_SECONDS)


def _normalize_code(code: Any) -> str:
    value = str(code or "").strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
        if value.endswith(prefix):
            value = value[: -len(prefix)]
    if "." in value:
        value = value.split(".")[0]
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else value


def _em_symbol(code: str) -> str:
    plain = _normalize_code(code)
    if plain.startswith(("0", "3")):
        return f"SZ{plain}"
    if plain.startswith(("4", "8")):
        return f"BJ{plain}"
    return f"SH{plain}"


def _akshare_available(warnings: Optional[list[str]] = None):
    cached = ttl_cache_get("akshare:available", DEFAULT_TTL_SECONDS)
    if cached is not None:
        return cached
    try:
        import akshare  # noqa: F401

        return ttl_cache_set("akshare:available", True, DEFAULT_TTL_SECONDS)
    except Exception as exc:  # pragma: no cover - depends on environment
        if warnings is not None:
            _warn_once(warnings, f"akshare unavailable: {exc}")
        return ttl_cache_set("akshare:available", False, DEFAULT_TTL_SECONDS)


def _warn_once(warnings: list[str], message: str) -> None:
    clipped = str(message).replace("\n", " ")[:180]
    if clipped not in warnings:
        warnings.append(clipped)


def _cache_snapshot_keys() -> list[str]:
    with _CACHE_LOCK:
        return sorted(k for k in _CACHE if not k.startswith("akshare:"))[:20]


def _cached_akshare_df(
    key: str,
    ttl_seconds: int,
    warnings: list[str],
    label: str,
    function_name: str,
    kwargs: Optional[dict] = None,
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
) -> Optional[pd.DataFrame]:
    cached = ttl_cache_get(key, ttl_seconds)
    if cached is not None:
        return cached
    if not _akshare_available(warnings):
        return None
    ok, value = _run_akshare_subprocess(function_name, kwargs or {}, timeout_seconds)
    if ok:
        return ttl_cache_set(key, value, ttl_seconds)
    _warn_once(warnings, f"{label} {value}")
    return None


def _run_akshare_subprocess(function_name: str, kwargs: dict, timeout_seconds: float) -> tuple[bool, Any]:
    code = """
import contextlib
import json
import os
import sys
import warnings
from io import StringIO

warnings.filterwarnings("ignore")
for key in list(os.environ):
    if "proxy" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"
buffer = StringIO()
try:
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        import akshare as ak
        result = getattr(ak, sys.argv[1])(**json.loads(sys.argv[2]))
    if hasattr(result, "to_json"):
        payload = {"ok": True, "kind": "dataframe", "data": result.to_json(orient="split", force_ascii=False, date_format="iso")}
    else:
        payload = {"ok": True, "kind": "value", "data": result}
except Exception as exc:
    payload = {"ok": False, "error": str(exc)}
print(json.dumps(payload, ensure_ascii=False))
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, function_name, json.dumps(kwargs, ensure_ascii=False)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout_seconds:.1f}s"
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().replace("\n", " ")[:160]
        return False, f"failed with exit code {proc.returncode}: {stderr}"
    try:
        payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return False, f"returned invalid payload: {exc}"
    if not payload.get("ok"):
        return False, f"failed: {payload.get('error', 'unknown error')}"
    if payload.get("kind") == "dataframe":
        try:
            return True, pd.read_json(StringIO(payload.get("data", "")), orient="split")
        except ValueError as exc:
            return False, f"returned unreadable dataframe: {exc}"
    return True, payload.get("data")


def _resolve_board_context(code: str, stock_info: dict, warnings: list[str]) -> dict:
    industry_name = _clean_text(stock_info.get("industry"))
    if not industry_name or industry_name == "未知":
        enriched_info = _eastmoney_stock_profile(code, warnings) or _stock_individual_info(code, warnings)
        enriched_industry = _clean_text(enriched_info.get("industry"))
        if enriched_industry and enriched_industry != "未知":
            industry_name = enriched_industry
            stock_info = {**stock_info, **enriched_info}
    industry_boards = _board_list("industry", warnings, fetch_on_miss=bool(industry_name and industry_name != "未知"))
    concept_boards: list[dict] = []
    matched = None

    if industry_name and industry_name != "未知":
        matched = _find_board_by_name(industry_boards, industry_name, "industry")
        if matched:
            matched["matched_by"] = "stock_info.industry"

    if not matched:
        board_hint = _clean_text(
            stock_info.get("sector")
            or stock_info.get("concept")
            or stock_info.get("board")
            or stock_info.get("板块")
        )
        if board_hint:
            concept_boards = _board_list("concept", warnings, fetch_on_miss=True)
            if not industry_boards:
                industry_boards = _board_list("industry", warnings, fetch_on_miss=True)
            matched = _find_board_by_name(concept_boards, board_hint, "concept")
            if not matched:
                matched = _find_board_by_name(industry_boards, board_hint, "industry")
            if matched:
                matched["matched_by"] = "stock_info.board_hint"

    if not matched and industry_name and industry_name != "未知":
        matched = {
            "sector_name": industry_name,
            "sector_type": "industry",
            "source": "stock_info",
            "rank": None,
            "rank_percentile": None,
            "change_pct": None,
            "turnover_rate": None,
            "amount": None,
            "matched_by": "stock_info.industry",
        }

    if not matched:
        matched = {
            "sector_name": "未知板块",
            "sector_type": "unknown",
            "source": "fallback",
            "rank": None,
            "rank_percentile": None,
            "change_pct": None,
            "turnover_rate": None,
            "amount": None,
            "matched_by": None,
        }
        _warn_once(warnings, "sector not identified from stock_info; skipped exhaustive constituent scan")

    if not concept_boards:
        concept_boards = _board_list("concept", warnings, fetch_on_miss=False)
    if not industry_boards:
        industry_boards = _board_list("industry", warnings, fetch_on_miss=False)
    top_boards = _top_boards(concept_boards, "concept")[:5] + _top_boards(industry_boards, "industry")[:5]
    matched["top_boards"] = sorted(
        top_boards,
        key=lambda item: item.get("heat_hint", 50),
        reverse=True,
    )[:8]
    return matched


def _eastmoney_stock_profile(code: str, warnings: list[str]) -> dict:
    cache_key = f"stock:profile:em:{_normalize_code(code)}"
    cached = ttl_cache_get(cache_key, DEFAULT_TTL_SECONDS)
    if cached is not None:
        return cached

    plain = _normalize_code(code)
    if a_stock_data is not None:
        try:
            profile = a_stock_data.get_stock_profile(plain)
            if profile:
                result = {}
                for key in ["name", "industry", "pe_ratio", "pb_ratio", "market_cap", "float_market_cap"]:
                    if profile.get(key) not in (None, "", "-", "--"):
                        result[key] = profile.get(key)
                if result:
                    result["source"] = "a_stock_data_profile"
                    return ttl_cache_set(cache_key, result, DEFAULT_TTL_SECONDS)
        except Exception as exc:
            _warn_once(warnings, f"a-stock-data stock profile failed: {exc}")
    market_code = 1 if plain.startswith("6") else 0
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": "", "https": ""}
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com"})

    profile = _eastmoney_f10_profile(plain, session, warnings)
    if profile:
        return ttl_cache_set(cache_key, profile, DEFAULT_TTL_SECONDS)

    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f57,f58,f127",
        "secid": f"{market_code}.{plain}",
    }
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    for _ in range(2):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            data = resp.json().get("data") or {}
            profile = {}
            if data.get("f58"):
                profile["name"] = data.get("f58")
            if data.get("f127") and data.get("f127") != "-":
                profile["industry"] = data.get("f127")
            if profile:
                return ttl_cache_set(cache_key, profile, DEFAULT_TTL_SECONDS)
        except Exception as exc:
            last_error = exc
            continue
    if "last_error" in locals():
        _warn_once(warnings, f"eastmoney stock profile failed: {last_error}")
    return ttl_cache_set(cache_key, {}, max(60, DEFAULT_TTL_SECONDS // 3))


def _eastmoney_f10_profile(code: str, session: requests.Session, warnings: list[str]) -> dict:
    prefix = "SH" if code.startswith("6") else "BJ" if code.startswith(("4", "8")) else "SZ"
    try:
        resp = session.get(
            "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax",
            params={"code": f"{prefix}{code}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        row = ((resp.json().get("jbzl") or [{}])[0]) or {}
    except Exception as exc:
        _warn_once(warnings, f"eastmoney f10 profile failed: {exc}")
        return {}

    profile = {}
    if row.get("SECURITY_NAME_ABBR"):
        profile["name"] = row.get("SECURITY_NAME_ABBR")
    industry_path = row.get("EM2016") or row.get("INDUSTRYCSRC1")
    if industry_path:
        industry = str(industry_path).split("-")[-1].strip()
        if industry:
            profile["industry"] = industry
    return profile


def _stock_individual_info(code: str, warnings: list[str]) -> dict:
    df = _cached_akshare_df(
        f"stock:individual_info:{_normalize_code(code)}",
        DEFAULT_TTL_SECONDS,
        warnings,
        "eastmoney stock individual info",
        "stock_individual_info_em",
        {"symbol": _normalize_code(code)},
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    )
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}

    info: dict[str, Any] = {}
    for _, row in df.iterrows():
        item = _clean_text(row.get("item"))
        value = _clean_text(row.get("value"))
        if not item or not value:
            continue
        if "行业" in item:
            info["industry"] = value
        elif "股票简称" in item or "名称" in item:
            info["name"] = value
    if info:
        info["source"] = "eastmoney_stock_info"
    return info


def _board_list(kind: str, warnings: list[str], fetch_on_miss: bool = True) -> list[dict]:
    key = f"board:{kind}:em"
    cached = ttl_cache_get(key, BOARD_TTL_SECONDS)
    if cached is not None:
        if isinstance(cached, pd.DataFrame):
            return _normalize_board_df(cached, kind)
        if isinstance(cached, list):
            return cached
        return []
    if not fetch_on_miss:
        return []
    direct_rows = _eastmoney_board_list_direct(kind, warnings)
    if direct_rows:
        return ttl_cache_set(key, direct_rows, BOARD_TTL_SECONDS)
    function_name = "stock_board_concept_name_em" if kind == "concept" else "stock_board_industry_name_em"
    df = _cached_akshare_df(key, BOARD_TTL_SECONDS, warnings, f"eastmoney {kind} boards", function_name)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    return _normalize_board_df(df, kind)


def _normalize_board_df(df: pd.DataFrame, kind: str) -> list[dict]:
    rows: list[dict] = []
    total = max(1, len(df))
    for idx, row in df.reset_index(drop=True).iterrows():
        name = _first_value(row, ["板块名称", "名称", "name", "板块"])
        if not name:
            continue
        rank = _to_int(_first_value(row, ["排名", "序号", "rank"])) or int(idx) + 1
        change_pct = _to_float(_first_value(row, ["涨跌幅", "涨幅", "change_pct", "涨跌幅%"]))
        turnover_rate = _to_float(_first_value(row, ["换手率", "换手", "turnover_rate"]))
        amount = _to_float(_first_value(row, ["成交额", "amount"]))
        heat_hint = _score_board_row(rank, total, change_pct, turnover_rate, amount)
        rows.append(
            {
                "sector_name": str(name),
                "sector_type": kind,
                "source": "eastmoney_board",
                "rank": rank,
                "rank_percentile": round(1 - ((rank - 1) / total), 4),
                "change_pct": change_pct,
                "turnover_rate": turnover_rate,
                "amount": amount,
                "heat_hint": heat_hint,
            }
        )
    return rows


def _find_board_by_name(boards: list[dict], name: str, kind: str) -> Optional[dict]:
    normalized = _compact_name(name)
    if not normalized:
        return None
    for board in boards:
        board_name = _compact_name(board.get("sector_name"))
        if board_name == normalized or normalized in board_name or board_name in normalized:
            found = dict(board)
            found["sector_type"] = kind
            return found
    return None


def _top_boards(boards: list[dict], kind: str) -> list[dict]:
    return [
        {
            "sector_name": item.get("sector_name"),
            "sector_type": kind,
            "rank": item.get("rank"),
            "change_pct": item.get("change_pct"),
            "turnover_rate": item.get("turnover_rate"),
            "amount": item.get("amount"),
            "heat_hint": item.get("heat_hint"),
        }
        for item in sorted(boards, key=lambda row: row.get("heat_hint", 50), reverse=True)
    ]


def _resolve_hot_context(code: str, stock_name: str, warnings: list[str]) -> dict:
    em_rank = _eastmoney_hot_rank(code, warnings)
    em_latest = _eastmoney_hot_rank_latest(code, warnings)
    keywords = _eastmoney_hot_keywords(code, warnings)
    snowball_hits = _snowball_hot_hits(code, stock_name, warnings)

    rank = em_rank.get("rank") or em_latest.get("rank")
    rank_change = em_latest.get("rank_change")
    keyword_hits = keywords[:8]
    score_parts = []
    if rank:
        score_parts.append(_score_rank(rank, 100))
    if rank_change is not None:
        score_parts.append(_score_rank_change(rank_change))
    if keyword_hits:
        score_parts.append(min(100, 50 + len(keyword_hits) * 8))
    if snowball_hits:
        score_parts.append(min(100, 55 + len(snowball_hits) * 12))

    return {
        "source": "eastmoney_hot+snowball_hot" if score_parts else None,
        "eastmoney_hot_rank": rank,
        "eastmoney_rank_change": rank_change,
        "keyword_hits": keyword_hits,
        "snowball_hits": snowball_hits,
        "is_in_hot_list": bool(rank or snowball_hits),
        "hot_score": round(sum(score_parts) / len(score_parts), 1) if score_parts else 50.0,
        "details": {
            "eastmoney_rank_row": em_rank.get("raw", {}),
            "eastmoney_latest_row": em_latest.get("raw", {}),
        },
    }


def _eastmoney_hot_rank(code: str, warnings: list[str]) -> dict:
    df = _cached_akshare_df("hot:rank:em", HOT_TTL_SECONDS, warnings, "eastmoney hot rank", "stock_hot_rank_em")
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    plain = _normalize_code(code)
    for _, row in df.iterrows():
        row_code = _normalize_code(_first_value(row, ["代码", "股票代码", "code", "证券代码"]))
        if row_code == plain:
            rank = _to_int(_first_value(row, ["当前排名", "排名", "rank"]))
            return {"rank": rank, "raw": _row_excerpt(row)}
    return {}


def _eastmoney_hot_rank_latest(code: str, warnings: list[str]) -> dict:
    symbol = _em_symbol(code)
    df = _cached_akshare_df(
        f"hot:latest:em:{symbol}",
        HOT_TTL_SECONDS,
        warnings,
        "eastmoney stock hot rank latest",
        "stock_hot_rank_latest_em",
        {"symbol": symbol},
    )
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    row = df.iloc[-1]
    rank = _to_int(_first_value(row, ["排名", "当前排名", "rank"]))
    prev_rank = _to_int(_first_value(row, ["上一排名", "昨日排名", "previous_rank"]))
    rank_change = (prev_rank - rank) if rank and prev_rank else _to_float(_first_value(row, ["排名变化", "rank_change"]))
    return {"rank": rank, "rank_change": rank_change, "raw": _row_excerpt(row)}


def _eastmoney_hot_keywords(code: str, warnings: list[str]) -> list[dict]:
    symbol = _em_symbol(code)
    df = _cached_akshare_df(
        f"hot:keyword:em:{symbol}",
        HOT_TTL_SECONDS,
        warnings,
        "eastmoney stock hot keywords",
        "stock_hot_keyword_em",
        {"symbol": symbol},
    )
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    hits = []
    for _, row in df.head(12).iterrows():
        keyword = _first_value(row, ["关键词", "keyword", "概念名称", "名称"])
        if keyword:
            hits.append(
                {
                    "keyword": str(keyword),
                    "heat": _to_float(_first_value(row, ["热度", "搜索指数", "count", "score"])),
                    "rank": _to_int(_first_value(row, ["排名", "rank"])),
                }
            )
    return hits


def _snowball_hot_hits(code: str, stock_name: str, warnings: list[str]) -> list[dict]:
    hits = []
    plain = _normalize_code(code)
    for endpoint_name, label in [
        ("stock_hot_follow_xq", "snowball_follow"),
    ]:
        df = _cached_akshare_df(
            f"hot:{label}",
            HOT_TTL_SECONDS,
            warnings,
            label,
            endpoint_name,
            {"symbol": "最热门"},
        )
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for idx, row in df.head(50).iterrows():
            text = " ".join(str(v) for v in row.to_dict().values())
            if plain in text or (stock_name and stock_name in text):
                hits.append({"source": label, "rank": idx + 1, "excerpt": text[:80]})
                break
    return hits


def _external_heat_score(board_context: dict, hot_context: dict) -> float:
    board_score = board_context.get("heat_hint")
    if board_score is None:
        board_score = 50.0 if board_context.get("sector_type") != "unknown" else 45.0
    hot_score = hot_context.get("hot_score", 50.0)
    return _weighted_score([(board_score, 0.45), (hot_score, 0.55)])


def _capital_slope_score(
    daily_df: Optional[pd.DataFrame],
    minute_df: Optional[pd.DataFrame],
    warnings: list[str],
) -> tuple[float, dict]:
    df = _valid_df(daily_df)
    minute = _valid_df(minute_df)
    if df.empty:
        _warn_once(warnings, "daily_df unavailable; capital slope uses neutral score")
        return 50.0, {"reason": "daily_missing"}

    amount = _numeric_series(df, "amount")
    if amount.empty:
        close = _numeric_series(df, "close")
        volume = _numeric_series(df, "volume")
        amount = close * volume if not close.empty and not volume.empty else pd.Series(dtype="float64")
    volume = _numeric_series(df, "volume")

    recent_amount = _tail_mean(amount, 3)
    base_amount = _tail_mean(amount.iloc[:-3], 10) if len(amount) > 4 else None
    recent_volume = _tail_mean(volume, 3)
    base_volume = _tail_mean(volume.iloc[:-3], 10) if len(volume) > 4 else None
    amount_ratio = _safe_ratio(recent_amount, base_amount)
    volume_ratio = _safe_ratio(recent_volume, base_volume)

    minute_boost = None
    if not minute.empty:
        minute_amount = _numeric_series(minute, "amount")
        minute_volume = _numeric_series(minute, "volume")
        live = minute_amount if not minute_amount.empty else minute_volume
        if len(live) >= 10:
            minute_boost = _safe_ratio(_tail_mean(live, 5), _tail_mean(live.iloc[:-5], 20))

    ratios = [r for r in [amount_ratio, volume_ratio, minute_boost] if r is not None and math.isfinite(r)]
    if not ratios:
        return 50.0, {"reason": "amount_volume_missing"}
    blended = sum(ratios) / len(ratios)
    score = _clamp(50 + (blended - 1) * 35, 0, 100)
    return round(score, 1), {
        "amount_ratio": _round_or_none(amount_ratio),
        "volume_ratio": _round_or_none(volume_ratio),
        "minute_ratio": _round_or_none(minute_boost),
        "blended_ratio": round(blended, 3),
    }


def _heat_acceleration_score(
    daily_df: Optional[pd.DataFrame],
    minute_df: Optional[pd.DataFrame],
    warnings: list[str],
) -> tuple[float, dict]:
    df = _valid_df(daily_df)
    if df.empty:
        _warn_once(warnings, "daily_df unavailable; heat acceleration uses neutral score")
        return 50.0, {"reason": "daily_missing"}

    close = _numeric_series(df, "close")
    volume = _numeric_series(df, "volume")
    pct_change = _numeric_series(df, "pct_change")
    if pct_change.empty and len(close) >= 2:
        pct_change = close.pct_change() * 100

    recent_momentum = _tail_mean(pct_change, 3)
    previous_momentum = _tail_mean(pct_change.iloc[:-3], 5) if len(pct_change) > 5 else 0
    momentum_delta = (recent_momentum or 0) - (previous_momentum or 0)

    volume_recent = _tail_mean(volume, 3)
    volume_previous = _tail_mean(volume.iloc[:-3], 5) if len(volume) > 5 else None
    volume_accel = _safe_ratio(volume_recent, volume_previous)

    minute_accel = None
    minute = _valid_df(minute_df)
    if not minute.empty:
        minute_close = _numeric_series(minute, "close")
        if len(minute_close) >= 15:
            recent_ret = _safe_ratio(float(minute_close.iloc[-1]), float(minute_close.iloc[-6]))
            prev_ret = _safe_ratio(float(minute_close.iloc[-6]), float(minute_close.iloc[-15]))
            if recent_ret is not None and prev_ret is not None:
                minute_accel = (recent_ret - prev_ret) * 100

    score = 50 + momentum_delta * 4
    if volume_accel is not None:
        score += (volume_accel - 1) * 18
    if minute_accel is not None:
        score += minute_accel * 3
    score = _clamp(score, 0, 100)
    return round(score, 1), {
        "momentum_delta_pct": _round_or_none(momentum_delta),
        "volume_acceleration": _round_or_none(volume_accel),
        "minute_price_acceleration_pct": _round_or_none(minute_accel),
    }


def _confidence(
    board_context: dict,
    hot_context: dict,
    daily_df: Optional[pd.DataFrame],
    minute_df: Optional[pd.DataFrame],
    warnings: list[str],
) -> float:
    score = 0.25
    if board_context.get("sector_type") != "unknown":
        score += 0.25
    if board_context.get("source") == "eastmoney_board":
        score += 0.15
    if hot_context.get("is_in_hot_list") or hot_context.get("keyword_hits"):
        score += 0.15
    if daily_df is not None and not daily_df.empty:
        score += 0.15
    if minute_df is not None and not minute_df.empty:
        score += 0.05
    score -= min(0.2, len(warnings) * 0.03)
    return round(_clamp(score, 0.1, 0.95), 2)


def _score_board_row(
    rank: Optional[int],
    total: int,
    change_pct: Optional[float],
    turnover_rate: Optional[float],
    amount: Optional[float],
) -> float:
    rank_score = _score_rank(rank, total) if rank else 50
    change_score = _clamp(50 + (change_pct or 0) * 8, 0, 100)
    turnover_score = _clamp(35 + (turnover_rate or 0) * 6, 0, 100) if turnover_rate is not None else 50
    amount_score = 50
    if amount is not None and amount > 0:
        amount_score = _clamp(35 + math.log10(max(amount, 1)) * 6, 0, 100)
    return _weighted_score(
        [
            (rank_score, 0.35),
            (change_score, 0.35),
            (turnover_score, 0.15),
            (amount_score, 0.15),
        ]
    )


def _score_rank(rank: Optional[int], total: int = 100) -> float:
    if not rank or rank <= 0:
        return 50.0
    total = max(total, rank, 1)
    return round(_clamp(100 - ((rank - 1) / total) * 75, 20, 100), 1)


def _score_rank_change(rank_change: Optional[float]) -> float:
    if rank_change is None:
        return 50.0
    return round(_clamp(50 + float(rank_change) * 3, 0, 100), 1)


def _weighted_score(parts: list[tuple[Optional[float], float]]) -> float:
    available = [(float(score), weight) for score, weight in parts if score is not None and math.isfinite(float(score))]
    if not available:
        return 50.0
    weight_sum = sum(weight for _, weight in available)
    if weight_sum <= 0:
        return 50.0
    return round(_clamp(sum(score * weight for score, weight in available) / weight_sum, 0, 100), 1)


def _valid_df(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    return df.copy()


def _numeric_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").dropna().reset_index(drop=True)


def _tail_mean(series: pd.Series, size: int) -> Optional[float]:
    if series is None or series.empty:
        return None
    values = pd.to_numeric(series, errors="coerce").dropna().tail(size)
    if values.empty:
        return None
    return float(values.mean())


def _safe_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    try:
        if numerator is None or denominator is None or denominator == 0:
            return None
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _round_or_none(value: Optional[float], ndigits: int = 3) -> Optional[float]:
    if value is None:
        return None
    try:
        if not math.isfinite(float(value)):
            return None
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        text = str(value).replace("%", "").replace(",", "").strip()
        if text in {"-", "--", "None", "nan"}:
            return None
        multiplier = 1.0
        if text.endswith("亿元"):
            multiplier = 100_000_000
            text = text[:-2]
        elif text.endswith("万元"):
            multiplier = 10_000
            text = text[:-2]
        elif text.endswith("亿"):
            multiplier = 100_000_000
            text = text[:-1]
        elif text.endswith("万"):
            multiplier = 10_000
            text = text[:-1]
        return float(text) * multiplier
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


def _first_value(row: Any, columns: list[str]) -> Any:
    for col in columns:
        try:
            value = row.get(col)
        except AttributeError:
            value = None
        if value is not None and not (isinstance(value, float) and math.isnan(value)) and str(value) != "":
            return value
    return None


def _row_excerpt(row: Any) -> dict:
    try:
        items = row.to_dict()
    except AttributeError:
        return {}
    excerpt = {}
    for key, value in list(items.items())[:12]:
        if isinstance(value, (int, float, str)) or value is None:
            excerpt[str(key)] = value
        else:
            excerpt[str(key)] = str(value)
    return excerpt


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"None", "nan", "-"} else text


def _compact_name(value: Any) -> str:
    text = _clean_text(value)
    for token in ["板块", "行业", "概念", "指数", "II", "Ⅰ", "Ⅱ"]:
        text = text.replace(token, "")
    return text.replace(" ", "").lower()


def _latest_date(df: Optional[pd.DataFrame]) -> Optional[str]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty or "date" not in df.columns:
        return None
    dates = pd.to_datetime(df["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().strftime("%Y-%m-%d")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


__all__ = [
    "build_heat_context",
    "build_market_heat_ranking",
    "build_attention_heat_ranking",
    "ttl_cache_get",
    "ttl_cache_set",
    "ttl_cache_clear",
]
