"""
a-stock-data runtime adapter.

The upstream a-stock-data skill is a collection of direct A-share public data
recipes rather than an importable package. This module turns the subset used by
stock-analyzer into stable project functions and keeps existing app contracts
unchanged when providers fail.
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import requests


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("A_STOCK_DATA_TIMEOUT_SECONDS", "6.0"))
_CACHE: dict[str, tuple[float, Any]] = {}


class AStockDataUnavailable(RuntimeError):
    """Raised when a direct a-stock-data source cannot provide usable data."""


def normalize_code(code: Any) -> str:
    value = str(code or "").strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
        if value.endswith(prefix):
            value = value[: -len(prefix)]
    if "." in value:
        value = value.split(".", 1)[0]
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) != 6:
        raise ValueError(f"无效的股票代码格式: {code}，应为6位数字")
    return digits


def try_normalize_code(code: Any) -> str:
    try:
        return normalize_code(code)
    except ValueError:
        return ""


def market_prefix(code: Any) -> str:
    plain = normalize_code(code)
    if plain.startswith(("5", "6", "9")):
        return "sh"
    if plain.startswith(("4", "8")):
        return "bj"
    return "sz"


def index_market_prefix(code: Any) -> str:
    plain = normalize_code(code)
    if plain.startswith("399"):
        return "sz"
    return "sh"


def eastmoney_market_code(code: Any) -> int:
    plain = normalize_code(code)
    return 1 if plain.startswith("6") else 0


def eastmoney_secu_code(code: Any) -> str:
    plain = normalize_code(code)
    prefix = "SH" if plain.startswith("6") else "BJ" if plain.startswith(("4", "8")) else "SZ"
    return f"{prefix}{plain}"


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "" or value == "-":
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    result = _to_float(value)
    return int(result) if result is not None else default


def _cache_get(key: str, ttl_seconds: int) -> Any:
    item = _CACHE.get(key)
    if not item:
        return None
    expires_at, value = item
    if expires_at <= time.time():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any, ttl_seconds: int) -> Any:
    _CACHE[key] = (time.time() + max(1, int(ttl_seconds)), value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


def _direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": "", "https": ""}
    session.headers.update({"User-Agent": UA})
    return session


def _http_get_text(url: str, params: Optional[dict] = None, headers: Optional[dict] = None, timeout: Optional[float] = None, encoding: Optional[str] = None) -> str:
    session = _direct_session()
    request_headers = {"User-Agent": UA, **(headers or {})}
    resp = session.get(url, params=params, headers=request_headers, timeout=timeout or REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    if encoding:
        return resp.content.decode(encoding, errors="ignore")
    return resp.text


def _http_get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
    session = _direct_session()
    request_headers = {"User-Agent": UA, **(headers or {})}
    resp = session.get(url, params=params, headers=request_headers, timeout=timeout or REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _safe_get(values: list[str], index: int) -> str:
    return values[index] if index < len(values) else ""


def get_realtime_quotes(codes: list[str]) -> dict[str, dict]:
    normalized = [normalize_code(code) for code in codes if try_normalize_code(code)]
    if not normalized:
        return {}
    cache_key = "quote:tencent:" + ",".join(sorted(normalized))
    cached = _cache_get(cache_key, 15)
    if cached is not None:
        return cached

    prefixed = [f"{market_prefix(code)}{code}" for code in normalized]
    text = _http_get_text("https://qt.gtimg.cn/q=" + ",".join(prefixed), timeout=10, encoding="gbk")
    result: dict[str, dict] = {}
    for line in text.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=", 1)[0].split("_")[-1]
        values = line.split('"')[1].split("~")
        code = normalize_code(key[2:] if key[:2].lower() in {"sh", "sz", "bj"} else _safe_get(values, 2))
        result[code] = {
            "code": code,
            "name": _safe_get(values, 1) or code,
            "price": _to_float(_safe_get(values, 3)),
            "pre_close": _to_float(_safe_get(values, 4)),
            "last_close": _to_float(_safe_get(values, 4)),
            "open": _to_float(_safe_get(values, 5)),
            "change_amt": _to_float(_safe_get(values, 31)),
            "change_pct": _to_float(_safe_get(values, 32)),
            "high": _to_float(_safe_get(values, 33)),
            "low": _to_float(_safe_get(values, 34)),
            "amount": (_to_float(_safe_get(values, 37), 0.0) or 0.0) * 10000,
            "amount_wan": _to_float(_safe_get(values, 37)),
            "turnover_rate": _to_float(_safe_get(values, 38)),
            "turnover_pct": _to_float(_safe_get(values, 38)),
            "pe_ratio": _to_float(_safe_get(values, 39)),
            "pe_ttm": _to_float(_safe_get(values, 39)),
            "amplitude_pct": _to_float(_safe_get(values, 43)),
            "market_cap": _to_float(_safe_get(values, 44)),
            "mcap_yi": _to_float(_safe_get(values, 44)),
            "float_market_cap": _to_float(_safe_get(values, 45)),
            "float_mcap_yi": _to_float(_safe_get(values, 45)),
            "pb_ratio": _to_float(_safe_get(values, 46)),
            "pb": _to_float(_safe_get(values, 46)),
            "high_limit": _to_float(_safe_get(values, 47)),
            "low_limit": _to_float(_safe_get(values, 48)),
            "vol_ratio": _to_float(_safe_get(values, 49)),
            "pe_static": _to_float(_safe_get(values, 52)),
            "source": "a_stock_data_tencent",
        }
    return _cache_set(cache_key, result, 15)


def _normalize_ohlcv(df: pd.DataFrame, source: str, days: Optional[int] = None) -> pd.DataFrame:
    if df is None or df.empty:
        raise AStockDataUnavailable(f"{source} returned empty kline data")
    normalized = df.copy()
    normalized = normalized.rename(columns={
        "datetime": "date",
        "day": "date",
        "time": "date",
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "vol": "volume",
    })
    required = {"date", "open", "high", "low", "close", "volume"}
    if not required.issubset(normalized.columns):
        raise AStockDataUnavailable(f"{source} missing required kline columns")
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if "amount" not in normalized.columns:
        normalized["amount"] = normalized["close"] * normalized["volume"]
    normalized = normalized.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)
    normalized["pct_change"] = normalized["close"].pct_change() * 100
    columns = ["date", "open", "high", "low", "close", "volume", "amount", "pct_change"]
    normalized = normalized[columns]
    if days:
        normalized = normalized.tail(days).reset_index(drop=True)
    normalized.attrs["source"] = source
    return normalized


def _mootdx_bars(code: str, category: int, offset: int, source: str) -> pd.DataFrame:
    from mootdx.quotes import Quotes

    client = Quotes.factory(market="std")
    df = client.bars(symbol=normalize_code(code), category=category, offset=offset)
    return _normalize_ohlcv(pd.DataFrame(df), source)


def _fetch_baidu_kline_payload(code: str, start_time: str = "", is_index: bool = False, code_override: Optional[str] = None) -> dict:
    plain = normalize_code(code)
    params = {
        "all": "1",
        "isIndex": "true" if is_index else "false",
        "isBk": "false",
        "isBlock": "false",
        "isFutures": "false",
        "isStock": "false" if is_index else "true",
        "newFormat": "1",
        "group": "quotation_kline_ab",
        "finClientType": "pc",
        "code": code_override or (f"{index_market_prefix(plain)}{plain}" if is_index else plain),
        "start_time": start_time,
        "ktype": "1",
    }
    headers = {
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    data = _http_get_json("https://finance.pae.baidu.com/selfselect/getstockquotation", params=params, headers=headers, timeout=10)
    result = data.get("Result") or {}
    market_data = result.get("newMarketData") or {}
    return {"keys": market_data.get("keys") or [], "rows": str(market_data.get("marketData") or "").split(";")}


def _baidu_daily_bars(code: str, days: int) -> pd.DataFrame:
    payload = _fetch_baidu_kline_payload(code)
    keys = payload.get("keys") or []
    rows = []
    for line in payload.get("rows") or []:
        if not line:
            continue
        values = str(line).split(",")
        if len(values) < len(keys):
            continue
        rows.append(dict(zip(keys, values)))
    return _normalize_ohlcv(pd.DataFrame(rows), "a_stock_data_baidu", days=days)


def _baidu_index_bars(code: str, days: int) -> pd.DataFrame:
    plain = normalize_code(code)
    errors: list[str] = []
    for request_code in (f"{index_market_prefix(plain)}{plain}", plain):
        try:
            payload = _fetch_baidu_kline_payload(plain, is_index=True, code_override=request_code)
            keys = payload.get("keys") or []
            rows = []
            for line in payload.get("rows") or []:
                if not line:
                    continue
                values = str(line).split(",")
                if len(values) < len(keys):
                    continue
                rows.append(dict(zip(keys, values)))
            df = _normalize_ohlcv(pd.DataFrame(rows), "a_stock_data_baidu_index", days=days)
            return df
        except Exception as exc:
            errors.append(f"{request_code}: {exc}")
    raise AStockDataUnavailable("; ".join(errors)[:240])


def get_daily_bars(code: str, days: int = 120) -> pd.DataFrame:
    plain = normalize_code(code)
    errors: list[str] = []
    try:
        df = _mootdx_bars(plain, category=4, offset=max(days + 10, 120), source="a_stock_data_mootdx")
        return df.tail(days).reset_index(drop=True)
    except Exception as exc:
        errors.append(f"mootdx: {exc}")
    try:
        return _baidu_daily_bars(plain, days)
    except Exception as exc:
        errors.append(f"baidu: {exc}")
    raise AStockDataUnavailable("; ".join(errors)[:240])


def get_minute_bars(code: str, period: str = "1") -> pd.DataFrame:
    category_by_period = {"1": 7, "5": 8, "15": 9, "30": 10, "60": 11}
    category = category_by_period.get(str(period), 7)
    df = _mootdx_bars(normalize_code(code), category=category, offset=240, source="a_stock_data_mootdx_minute")
    if "date" in df.columns:
        df = df.rename(columns={"date": "datetime"})
        latest_day = pd.to_datetime(df["datetime"], errors="coerce").dt.date.max()
        if latest_day:
            df = df[pd.to_datetime(df["datetime"], errors="coerce").dt.date == latest_day]
    df.attrs["source"] = "a_stock_data_mootdx_minute"
    return df.reset_index(drop=True)


def _eastmoney_stock_info(code: str) -> dict:
    plain = normalize_code(code)
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43,f162,f167",
        "secid": f"{eastmoney_market_code(plain)}.{plain}",
    }
    data = _http_get_json("https://push2.eastmoney.com/api/qt/stock/get", params=params, headers={"Referer": "https://quote.eastmoney.com"}, timeout=10).get("data") or {}
    if not data:
        return {}
    return {
        "code": normalize_code(data.get("f57") or plain),
        "name": data.get("f58") or "",
        "industry": data.get("f127") if data.get("f127") not in (None, "-") else "",
        "total_shares": _to_float(data.get("f84")),
        "float_shares": _to_float(data.get("f85")),
        "market_cap": (_to_float(data.get("f116")) or 0) / 100000000 if _to_float(data.get("f116")) is not None else None,
        "float_market_cap": (_to_float(data.get("f117")) or 0) / 100000000 if _to_float(data.get("f117")) is not None else None,
        "list_date": str(data.get("f189") or ""),
        "price": _to_float(data.get("f43")),
        "pe_ratio": _to_float(data.get("f162")),
        "pb_ratio": _to_float(data.get("f167")),
        "source": "a_stock_data_eastmoney",
    }


def _eastmoney_f10_profile(code: str) -> dict:
    data = _http_get_json(
        "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax",
        params={"code": eastmoney_secu_code(code)},
        headers={"Referer": "https://emweb.securities.eastmoney.com/"},
        timeout=10,
    )
    row = ((data.get("jbzl") or [{}])[0]) or {}
    profile = {}
    if row.get("SECURITY_NAME_ABBR"):
        profile["name"] = row.get("SECURITY_NAME_ABBR")
    industry_path = row.get("EM2016") or row.get("INDUSTRYCSRC1")
    if industry_path:
        industry = str(industry_path).split("-")[-1].strip()
        if industry:
            profile["industry"] = industry
    if profile:
        profile["source"] = "a_stock_data_eastmoney_f10"
    return profile


def get_stock_profile(code: str) -> dict:
    plain = normalize_code(code)
    profile: dict[str, Any] = {"code": plain, "name": plain}
    sources: list[str] = []
    try:
        quote = get_realtime_quotes([plain]).get(plain) or {}
        if quote:
            profile.update({k: v for k, v in quote.items() if v is not None})
            sources.append("a_stock_data_tencent")
    except Exception:
        pass
    for fetcher in (_eastmoney_stock_info, _eastmoney_f10_profile):
        try:
            extra = fetcher(plain)
        except Exception:
            extra = {}
        if not extra:
            continue
        for key, value in extra.items():
            if key == "source":
                continue
            if value in (None, "", "-", "--"):
                continue
            if key not in profile or profile.get(key) in (None, "", "-", "--", plain):
                profile[key] = value
        source = extra.get("source")
        if source and source not in sources:
            sources.append(source)
    profile["source"] = "+".join(sources) if sources else "a_stock_data_unavailable"
    return profile


def get_ths_hot_reason(trade_date: Optional[str] = None) -> pd.DataFrame:
    date_arg = trade_date or datetime.now().strftime("%Y-%m-%d")
    cache_key = f"ths:hot_reason:{date_arg}"
    cached = _cache_get(cache_key, 60)
    if cached is not None:
        return cached.copy()
    url = f"http://zx.10jqka.com.cn/event/api/getharden/date/{date_arg}/orderby/date/orderway/desc/charset/GBK/"
    data = _http_get_json(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"},
        timeout=10,
    )
    if data.get("errocode", 0) != 0:
        raise AStockDataUnavailable(f"同花顺热点错误: {data.get('errormsg', '')}")
    df = pd.DataFrame(data.get("data") or [])
    if df.empty:
        return df
    df = df.rename(columns={
        "name": "名称",
        "code": "代码",
        "reason": "题材归因",
        "close": "收盘价",
        "zhangdie": "涨跌额",
        "zhangfu": "涨幅%",
        "huanshou": "换手率%",
        "chengjiaoe": "成交额",
        "chengjiaoliang": "成交量",
        "ddejingliang": "大单净量",
        "market": "市场",
    })
    return _cache_set(cache_key, df, 60).copy()


def _eastmoney_clist(params: dict, timeout: Optional[float] = None) -> list[dict]:
    data = _http_get_json(
        "https://push2.eastmoney.com/api/qt/clist/get",
        params=params,
        headers={"Referer": "https://data.eastmoney.com/"},
        timeout=timeout or REQUEST_TIMEOUT_SECONDS,
    ).get("data") or {}
    rows = data.get("diff") or []
    return list(rows.values()) if isinstance(rows, dict) else rows


def _capital_from_row(row: dict) -> tuple[Optional[float], str]:
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


def get_fund_flow_topics(topic_type: str = "concept", limit: int = 80) -> list[dict]:
    is_concept = topic_type == "concept"
    cache_key = f"fund:topics:{topic_type}"
    cached = _cache_get(cache_key, 60)
    if cached is not None:
        return cached
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
    rows = _eastmoney_clist(params, timeout=REQUEST_TIMEOUT_SECONDS)
    normalized = []
    total = len(rows)
    for idx, row in enumerate(rows[:limit]):
        name = str(row.get("f14") or "").strip()
        if not name:
            continue
        capital, capital_source = _capital_from_row(row)
        amount = _to_float(row.get("f20"))
        if capital is None:
            capital = amount or 0.0
            capital_source = "amount_proxy"
        normalized.append({
            "topic_name": name,
            "topic_type": topic_type,
            "capital_rank": idx + 1,
            "capital_inflow_amount": capital,
            "capital_source": capital_source,
            "capital_window": "今日",
            "price_change_pct": _to_float(row.get("f3")),
            "turnover_rate": _to_float(row.get("f8")),
            "amount": amount,
            "source": "a_stock_data_eastmoney_fund_flow",
            "source_provider": "a_stock_data",
            "rank_total": total,
        })
    return _cache_set(cache_key, normalized, 60)


def get_fund_flow_stocks(limit: int = 200) -> list[dict]:
    cache_key = "fund:stocks"
    cached = _cache_get(cache_key, 60)
    if cached is not None:
        return cached
    params = {
        "pn": "1",
        "pz": str(limit),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f2,f3,f20,f62,f66,f69,f72,f75,f78,f81,f84,f87,f124",
    }
    rows = _eastmoney_clist(params, timeout=REQUEST_TIMEOUT_SECONDS)
    normalized = []
    total = len(rows)
    for idx, row in enumerate(rows[:limit]):
        code = try_normalize_code(row.get("f12"))
        name = str(row.get("f14") or "").strip()
        if not code or not name:
            continue
        capital, capital_source = _capital_from_row(row)
        amount = _to_float(row.get("f20"))
        if capital is None:
            capital = amount or 0.0
            capital_source = "amount_proxy"
        normalized.append({
            "code": code,
            "name": name,
            "capital_rank": idx + 1,
            "capital_inflow_amount": capital,
            "capital_source": capital_source,
            "price_change_pct": _to_float(row.get("f3")),
            "amount": amount,
            "source": "a_stock_data_eastmoney_fund_flow",
            "source_provider": "a_stock_data",
            "rank_total": total,
        })
    return _cache_set(cache_key, normalized, 60)


def get_minute_fund_flow_summary(code: str) -> dict:
    plain = normalize_code(code)
    params = {
        "secid": f"{eastmoney_market_code(plain)}.{plain}",
        "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    data = _http_get_json(
        "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
        params=params,
        headers={"Referer": "https://quote.eastmoney.com/", "Origin": "https://quote.eastmoney.com"},
        timeout=min(REQUEST_TIMEOUT_SECONDS, 2.0),
    ).get("data") or {}
    main_values = []
    last = {}
    for line in data.get("klines") or []:
        parts = str(line).split(",")
        if len(parts) < 6:
            continue
        main = _to_float(parts[1])
        if main is None:
            continue
        main_values.append(main)
        last = {
            "time": parts[0],
            "main_net": main,
            "small_net": _to_float(parts[2]),
            "mid_net": _to_float(parts[3]),
            "large_net": _to_float(parts[4]),
            "super_net": _to_float(parts[5]),
        }
    if not main_values:
        return {}
    return {**last, "main_net_total": sum(main_values), "points": len(main_values), "source": "a_stock_data_eastmoney_minute_flow"}


def get_board_ranking(kind: str = "industry", limit: int = 100) -> list[dict]:
    is_concept = kind == "concept"
    cache_key = f"board:{kind}"
    cached = _cache_get(cache_key, 300)
    if cached is not None:
        return cached
    params = {
        "pn": "1",
        "pz": str(limit),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f12" if is_concept else "f3",
        "fs": "m:90 t:3 f:!50" if is_concept else "m:90 t:2 f:!50",
        "fields": "f3,f8,f12,f14,f20,f104,f105,f128,f136",
    }
    rows = _eastmoney_clist(params, timeout=REQUEST_TIMEOUT_SECONDS)
    total = max(1, len(rows))
    normalized = []
    for idx, row in enumerate(rows):
        name = str(row.get("f14") or "").strip()
        if not name:
            continue
        normalized.append({
            "sector_name": name,
            "sector_type": kind,
            "source": "a_stock_data_eastmoney_board",
            "rank": idx + 1,
            "rank_percentile": round(1 - (idx / total), 4),
            "change_pct": _to_float(row.get("f3")),
            "turnover_rate": _to_float(row.get("f8")),
            "amount": _to_float(row.get("f20")),
            "leading_stock": row.get("f128"),
            "leading_stock_change_pct": _to_float(row.get("f136")),
            "up_count": _to_int(row.get("f104")),
            "down_count": _to_int(row.get("f105")),
        })
    return _cache_set(cache_key, normalized, 300)


def get_concept_blocks(code: str) -> dict:
    params = {"code": normalize_code(code), "market": "ab", "typeCode": "all", "finClientType": "pc"}
    headers = {
        "Host": "finance.pae.baidu.com",
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    data = _http_get_json("https://finance.pae.baidu.com/api/getrelatedblock", params=params, headers=headers, timeout=10)
    if str(data.get("ResultCode", -1)) != "0":
        return {"industry": [], "concept": [], "region": [], "concept_tags": []}
    result = {"industry": [], "concept": [], "region": [], "concept_tags": []}
    for block in data.get("Result") or []:
        block_type = str(block.get("type") or "")
        for item in block.get("list") or []:
            entry = {"name": item.get("name", ""), "change_pct": item.get("increase", ""), "desc": item.get("desc", "")}
            if "行业" in block_type:
                result["industry"].append(entry)
            elif "概念" in block_type:
                result["concept"].append(entry)
                if entry["name"]:
                    result["concept_tags"].append(entry["name"])
            elif "地域" in block_type:
                result["region"].append(entry)
    return result


def get_trade_dates(end_date: Optional[str] = None, days: int = 260) -> list[str]:
    end = pd.to_datetime(end_date or datetime.now().strftime("%Y-%m-%d"), errors="coerce")
    if pd.isna(end):
        end = pd.Timestamp(datetime.now().date())
    start = end - pd.Timedelta(days=max(days * 2, 30))
    return pd.bdate_range(start=start, end=end).strftime("%Y-%m-%d").tolist()[-days:]


def get_index_history(code: str, end_date: str, days: int) -> pd.DataFrame:
    plain = normalize_code(code)
    errors: list[str] = []
    try:
        df = _mootdx_bars(plain, category=4, offset=max(days + 10, 120), source="a_stock_data_mootdx_index")
        return df.tail(days).reset_index(drop=True)
    except Exception as exc:
        errors.append(f"mootdx_index: {exc}")
    try:
        return _baidu_index_bars(plain, days)
    except Exception as exc:
        errors.append(f"baidu_index: {exc}")
    raise AStockDataUnavailable("; ".join(errors)[:240])


def get_etf_history(code: str, end_date: str, days: int) -> pd.DataFrame:
    return get_daily_bars(code, days=days)


def get_limit_up_candidates(trade_date: str) -> list[dict]:
    df = get_ths_hot_reason(trade_date)
    rows = []
    if df is None or df.empty:
        return rows
    quotes = get_realtime_quotes([str(value) for value in df.get("代码", []) if try_normalize_code(value)])
    for _, row in df.iterrows():
        code = try_normalize_code(row.get("代码"))
        if not code:
            continue
        quote = quotes.get(code, {})
        latest = _to_float(row.get("收盘价"), quote.get("price"))
        change_pct = _to_float(row.get("涨幅%"))
        if change_pct is not None and change_pct < 8.5:
            continue
        rows.append({
            "code": code,
            "name": row.get("名称") or quote.get("name") or code,
            "close": latest,
            "high_limit": quote.get("high_limit") or (latest * 1.1 if latest else None),
            "low_limit": quote.get("low_limit") or (latest * 0.9 if latest else None),
            "volume": _to_float(row.get("成交量")),
            "amount": _to_float(row.get("成交额"), quote.get("amount")),
            "market_cap": (quote.get("market_cap") * 100000000) if quote.get("market_cap") is not None else None,
            "float_market_cap": (quote.get("float_market_cap") * 100000000) if quote.get("float_market_cap") is not None else None,
            "continue_count": 1,
            "industry": "",
            "reason": row.get("题材归因"),
            "source": "a_stock_data_ths_hot_reason+tencent_quote",
        })
    return rows
