"""
股票数据获取模块
主数据源：新浪财经API（不受代理影响）
备用数据源：AKShare（东方财富）
带缓存策略
"""

import os
# 禁用所有代理
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        os.environ.pop(k, None)
os.environ['NO_PROXY'] = '*'

import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

try:
    from models import save_stock_cache, get_stock_cache
except ImportError:  # pragma: no cover - package execution fallback
    from .models import save_stock_cache, get_stock_cache

# 强制不走代理的请求Session
def _direct_session():
    s = requests.Session()
    s.proxies = {'http': '', 'https': ''}
    s.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
    return s


def _normalize_code(code: str) -> str:
    """标准化股票代码 → 6位纯数字"""
    code = code.strip().upper()
    for prefix in ["SH", "SZ", "BJ"]:
        if code.startswith(prefix):
            code = code[2:]
        if code.endswith(prefix):
            code = code[:-2]
    if "." in code:
        code = code.split(".")[0]
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        raise ValueError(f"无效的股票代码格式: {code}，应为6位数字")
    return code


def _sina_symbol(code: str) -> str:
    """6位代码 → 新浪格式 (sh600519 / sz000001)"""
    if code.startswith("6"):
        return f"sh{code}"
    elif code.startswith(("0", "3")):
        return f"sz{code}"
    elif code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sh{code}"


def _ak_symbol(code: str) -> str:
    """6位代码 → AkShare/Sina分钟格式 (sh600519 / sz000001)"""
    code = _normalize_code(code)
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sh{code}"


def classify_instrument(code: str, name: str = "") -> str:
    """Classify A-share instruments that need different strategy prompts."""
    normalized_code = _normalize_code(code)
    normalized_name = (name or "").upper()
    etf_markers = ["ETF", "LOF", "基金"]
    etf_prefixes = ("159", "510", "511", "512", "513", "515", "516", "517", "518", "520", "588", "589")
    if normalized_code.startswith(etf_prefixes) or any(marker in normalized_name for marker in etf_markers):
        return "etf"
    return "stock"


def get_stock_daily(code: str, days: int = 120) -> pd.DataFrame:
    """
    获取股票日K线数据（新浪API）
    
    Args:
        code: 股票代码 (支持多种格式)
        days: 获取最近多少天的数据
        
    Returns:
        DataFrame: date/open/high/low/close/volume
    """
    code = _normalize_code(code)
    
    # 尝试从缓存获取
    cache_data = get_stock_cache(code)
    if cache_data:
        df = pd.DataFrame(cache_data)
        df["date"] = pd.to_datetime(df["date"])
        latest_date = df["date"].max()
        if (datetime.now() - latest_date).days <= 1 and len(df) >= days * 0.8:
            df = df.sort_values("date").tail(days).reset_index(drop=True)
            return df
    
    # ── 主数据源：新浪财经 ──
    try:
        symbol = _sina_symbol(code)
        session = _direct_session()
        
        # scale=240 表示日K线，datalen获取条数
        url = (
            f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{symbol}_kline=/"
            f"CN_MarketDataService.getKLineData?"
            f"symbol={symbol}&scale=240&ma=no&datalen={days + 10}"
        )
        
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        
        # 解析JSONP响应
        text = resp.text
        start = text.index('(') + 1
        end = text.rindex(')')
        kline_data = json.loads(text[start:end])
        
        if not kline_data:
            raise ValueError(f"股票 {code} 无数据，请检查代码是否正确")
        
        # 转换为DataFrame
        df = pd.DataFrame(kline_data)
        df = df.rename(columns={
            'day': 'date',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'volume': 'volume'
        })
        
        # 转换数据类型
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
        df['date'] = pd.to_datetime(df['date'])
        
        # 计算额外指标
        df['pct_change'] = df['close'].pct_change() * 100
        df['amount'] = df['close'] * df['volume']  # 近似成交额
        
        # 缓存到数据库
        for _, row in df.iterrows():
            save_stock_cache(code, row["date"].strftime("%Y-%m-%d"), {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            })
        
        df = df.sort_values("date").tail(days).reset_index(drop=True)
        return df
        
    except (ValueError, KeyError):
        raise
    except Exception as e:
        # ── 备用数据源：AKShare ──
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=(datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
                adjust="qfq"
            )
            if df is None or df.empty:
                raise ValueError(f"股票 {code} 无数据")
            
            df = df.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume",
                "涨跌幅": "pct_change", "成交额": "amount"
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").tail(days).reset_index(drop=True)
            return df
        except Exception:
            raise RuntimeError(f"所有数据源均失败，最后错误: {str(e)}")


def get_stock_info(code: str) -> dict:
    """
    获取股票基本信息（新浪财经实时行情 + 腾讯备用）
    
    Returns:
        dict: code/name/industry/pe_ratio/pb_ratio/market_cap
    """
    code = _normalize_code(code)
    symbol = _sina_symbol(code)
    session = _direct_session()
    info = {"code": code, "name": code}
    
    # ── 新浪实时行情 ──
    try:
        url = f"https://hq.sinajs.cn/list={symbol}"
        resp = session.get(url, timeout=10)
        # 格式: var hq_str_sh600519="贵州茅台,1335.150,..."
        text = resp.text
        data_str = text.split('"')[1]
        fields = data_str.split(',')
        if len(fields) > 1:
            info["name"] = fields[0]
            info["open"] = float(fields[1]) if fields[1] else None
            info["pre_close"] = float(fields[2]) if fields[2] else None
            info["price"] = float(fields[3]) if fields[3] else None
            info["high"] = float(fields[4]) if fields[4] else None
            info["low"] = float(fields[5]) if fields[5] else None
    except Exception:
        pass
    
    # ── 腾讯行情获取PE/PB ──
    try:
        tencent_url = f"https://qt.gtimg.cn/q={symbol}"
        resp = session.get(tencent_url, timeout=10)
        text = resp.text
        # 格式: v_sh600519="1~贵州茅台~600519~1332.95~..."
        data_str = text.split('"')[1]
        fields = data_str.split('~')
        if len(fields) > 50:
            if info["name"] == code and fields[1]:
                info["name"] = fields[1]
            info["open"] = float(fields[5]) if len(fields) > 5 and fields[5] else info.get("open")
            info["price"] = float(fields[3]) if fields[3] else info.get("price")
            info["pre_close"] = float(fields[4]) if fields[4] else info.get("pre_close")
            info["high"] = float(fields[33]) if len(fields) > 33 and fields[33] else info.get("high")
            info["low"] = float(fields[34]) if len(fields) > 34 and fields[34] else info.get("low")
            info["high_limit"] = float(fields[47]) if len(fields) > 47 and fields[47] and fields[47] != "-1" else None
            info["low_limit"] = float(fields[48]) if len(fields) > 48 and fields[48] and fields[48] != "-1" else None
            
            # PE (动态市盈率)
            try:
                pe = float(fields[39]) if fields[39] and fields[39] != '' else None
                info["pe_ratio"] = pe
            except (ValueError, IndexError):
                info["pe_ratio"] = None
            
            # 总市值 — 腾讯API fields[45] 返回的是亿元单位，转为元
            try:
                market_cap = float(fields[45]) if fields[45] else None
                if market_cap:
                    info["market_cap"] = market_cap  # 腾讯API返回单位已是亿元，直接存储
            except (ValueError, IndexError):
                pass
            
            # PB (市净率)
            try:
                info["pb_ratio"] = float(fields[46]) if fields[46] else None
            except (ValueError, IndexError):
                info["pb_ratio"] = None
            
            # 行业（腾讯不直接提供，留空）
            info["industry"] = fields[100] if len(fields) > 100 and fields[100] else "未知"
    except Exception:
        pass
    
    # 如果还是没拿到名字，尝试AKShare
    if info["name"] == code:
        try:
            import akshare as ak
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    item = row.get("item", "")
                    value = row.get("value", "")
                    if "股票简称" in item or "名称" in item:
                        info["name"] = value
                    elif "行业" in item:
                        info["industry"] = value
                    elif "市盈率" in item and "动态" in item:
                        try:
                            info["pe_ratio"] = float(value) if value and value != "-" else None
                        except (ValueError, TypeError):
                            pass
                    elif "市净率" in item:
                        try:
                            info["pb_ratio"] = float(value) if value and value != "-" else None
                        except (ValueError, TypeError):
                            pass
                    elif "总市值" in item:
                        try:
                            info["market_cap"] = float(value) if value and value != "-" else None
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass
    
    instrument_type = classify_instrument(code, info.get("name", ""))
    info["instrument_type"] = instrument_type
    info["is_etf"] = instrument_type == "etf"
    return info


def get_stock_minute(code: str, period: str = "1") -> tuple[pd.DataFrame, Optional[str]]:
    """
    获取股票分钟K线。失败不抛出给业务层，返回 (空DataFrame, 错误信息) 供分析降级。
    """
    try:
        import akshare as ak
        symbol = _ak_symbol(code)
        df = ak.stock_zh_a_minute(symbol=symbol, period=period, adjust="")
        if df is None or df.empty:
            return pd.DataFrame(), "分钟数据为空"
        df = df.rename(columns={
            "day": "datetime",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "amount": "amount",
        })
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            latest_day = df["datetime"].dt.date.max()
            if latest_day:
                df = df[df["datetime"].dt.date == latest_day]
        return df.reset_index(drop=True), None
    except Exception as e:
        return pd.DataFrame(), str(e)[:160]
