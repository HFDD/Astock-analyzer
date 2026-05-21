"""
每日三策略推荐引擎。

本模块只生成推荐，不下单、不修改持仓。数据源失败会进入 data_status warning，
避免把缺失的竞价或ETF数据静默当成有效信号。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

try:
    from models import STRATEGY_META, get_daily_picks, save_strategy_run_results
    from data_fetcher import get_stock_daily
except ImportError:  # pragma: no cover - package import fallback
    from .models import STRATEGY_META, get_daily_picks, save_strategy_run_results
    from .data_fetcher import get_stock_daily


STRATEGY_KEYS = tuple(STRATEGY_META.keys())


@dataclass
class Recommendation:
    strategy_key: str
    strategy_name: str
    code: str
    name: str
    rank: int
    score: float
    mode: str
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    data_status: dict = field(default_factory=dict)

    def to_record(self) -> dict:
        return asdict(self)


@dataclass
class StrategyRunResult:
    strategy_key: str
    strategy_name: str
    trade_date: str
    status: str = "success"
    total_scanned: int = 0
    recommendations: list[Recommendation] = field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def records(self) -> list[dict]:
        return [rec.to_record() for rec in self.recommendations]


class DataSourceUnavailable(RuntimeError):
    """Raised when a strategy cannot produce a meaningful result from source data."""


def _to_float(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_pct(value: float) -> float:
    return round(value * 100, 3)


def _normalize_code(code: str) -> str:
    value = str(code).strip().upper()
    if "." in value:
        value = value.split(".")[0]
    for prefix in ("SH", "SZ", "BJ"):
        value = value.replace(prefix, "")
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits.zfill(6)[-6:]


def _jq_code_to_plain(code: str) -> str:
    return _normalize_code(code)


def _is_base_stock_code(code: str, exclude_prefixes: tuple[str, ...], exclude_688: bool = True) -> bool:
    plain = _normalize_code(code)
    if exclude_688 and plain.startswith("688"):
        return False
    return not plain.startswith(exclude_prefixes)


def _history_with_amount(provider, code: str, end_date: str, days: int) -> pd.DataFrame:
    df = provider.stock_history(code, end_date, days)
    if df is None:
        return pd.DataFrame()
    df = df.copy()
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("date").reset_index(drop=True) if "date" in df.columns else df.reset_index(drop=True)


class AkshareDailyPickProvider:
    """AKShare-backed adapter used by the runner and API."""

    def __init__(self):
        self._spot_cache = None
        self._etf_spot_cache = None

    def _ak(self):
        import akshare as ak
        return ak

    def normalize_trade_date(self, trade_date: Optional[str] = None) -> str:
        if trade_date and trade_date != "auto":
            return trade_date
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        try:
            dates = self.trade_dates()
            eligible = [d for d in dates if d <= today]
            return eligible[-1] if eligible else today
        except Exception:
            return today

    def trade_dates(self) -> list[str]:
        df = self._ak().tool_trade_date_hist_sina()
        if df is None or df.empty:
            return []
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        return sorted(pd.to_datetime(df[col], errors="coerce").dropna().dt.strftime("%Y-%m-%d").tolist())

    def previous_trade_date(self, trade_date: str) -> str:
        dates = self.trade_dates()
        previous = [d for d in dates if d < trade_date]
        if previous:
            return previous[-1]
        return (pd.to_datetime(trade_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    def before_previous_trade_date(self, trade_date: str) -> str:
        prev = self.previous_trade_date(trade_date)
        return self.previous_trade_date(prev)

    def _spot(self) -> pd.DataFrame:
        if self._spot_cache is None:
            self._spot_cache = self._ak().stock_zh_a_spot_em()
        return self._spot_cache.copy() if self._spot_cache is not None else pd.DataFrame()

    def _etf_spot(self) -> pd.DataFrame:
        if self._etf_spot_cache is None:
            self._etf_spot_cache = self._ak().fund_etf_spot_em()
        return self._etf_spot_cache.copy() if self._etf_spot_cache is not None else pd.DataFrame()

    def limit_up_candidates(self, previous_date: str) -> list[dict]:
        date_arg = previous_date.replace("-", "")
        df = self._ak().stock_zt_pool_em(date=date_arg)
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.iterrows():
            code = _normalize_code(row.get("代码"))
            if not _is_base_stock_code(code, ("4", "8"), exclude_688=True):
                continue
            name = str(row.get("名称") or code)
            if "ST" in name or "退" in name:
                continue
            latest = _to_float(row.get("最新价"))
            continue_count = _to_float(row.get("连板数"), 1) or 1
            high_limit = latest * 1.1 if latest else None
            low_limit = latest * 0.9 if latest else None
            rows.append({
                "code": code,
                "name": name,
                "close": latest,
                "high_limit": high_limit,
                "low_limit": low_limit,
                "volume": None,
                "amount": _to_float(row.get("成交额")),
                "market_cap": _to_float(row.get("总市值")),
                "float_market_cap": _to_float(row.get("流通市值")),
                "continue_count": int(continue_count),
                "industry": row.get("所属行业"),
            })
        return rows

    def first_board_candidates(self, previous_date: str, before_previous_date: str) -> list[dict]:
        yesterday = {item["code"]: item for item in self.limit_up_candidates(previous_date)}
        before = {item["code"] for item in self.limit_up_candidates(before_previous_date)}
        return [item for code, item in yesterday.items() if code not in before]

    def stock_history(self, code: str, end_date: str, days: int) -> pd.DataFrame:
        plain = _normalize_code(code)
        try:
            df = get_stock_daily(plain, days=days)
        except Exception:
            df = pd.DataFrame()
        if df is None or df.empty:
            return pd.DataFrame()
        if "amount" not in df.columns and {"close", "volume"}.issubset(df.columns):
            df = df.copy()
            df["amount"] = df["close"] * df["volume"]
        return df.tail(days).reset_index(drop=True)

    def call_auction(self, code: str, trade_date: str, start_time: str, end_time: str) -> Optional[dict]:
        plain = _normalize_code(code)
        try:
            df = self._ak().stock_zh_a_hist_pre_min_em(
                symbol=plain,
                start_time=start_time,
                end_time=end_time,
            )
        except Exception as exc:
            return {"warning": f"竞价数据不可用: {str(exc)[:80]}"}
        if df is None or df.empty:
            return {"warning": "竞价数据为空"}
        price_col = "收盘" if "收盘" in df.columns else ("close" if "close" in df.columns else None)
        vol_col = "成交量" if "成交量" in df.columns else ("volume" if "volume" in df.columns else None)
        if not price_col or not vol_col:
            return {"warning": "竞价数据缺少价格或成交量字段"}
        last = df.iloc[-1]
        return {
            "price": _to_float(last.get(price_col)),
            "volume": _to_float(last.get(vol_col), 0),
        }

    def market_sentiment(self, trade_date: str) -> dict:
        try:
            df = self._ak().index_zh_a_hist(
                symbol="000852",
                period="daily",
                start_date=(pd.to_datetime(trade_date) - pd.Timedelta(days=130)).strftime("%Y%m%d"),
                end_date=trade_date.replace("-", ""),
            )
            if df is None or df.empty:
                raise ValueError("中证1000指数为空")
            close = pd.to_numeric(df["收盘"] if "收盘" in df.columns else df["close"], errors="coerce").dropna()
            if len(close) < 60:
                raise ValueError("中证1000数据不足60日")
            ma20 = close.rolling(20).mean().iloc[-1]
            ma60 = close.rolling(60).mean().iloc[-1]
            cur = close.iloc[-1]
            above = int((close.iloc[-30:] > ma60).sum())
            dd20 = (cur - close.iloc[-20:].max()) / close.iloc[-20:].max()
            if above >= 20 and ma20 > ma60:
                state = "bull"
            elif dd20 < -0.12 or (cur < ma60 and ma20 < ma60):
                state = "bear"
            else:
                state = "cautious"
            return {"state": state, "summary": f"中证1000情绪{state}", "metrics": {"above_ma60": above, "dd20": round(dd20, 4)}}
        except Exception as exc:
            return {"state": "cautious", "summary": "指数数据不可用，降级为谨慎", "metrics": {}, "warning": str(exc)[:120]}

    def etf_history(self, code: str, end_date: str, days: int) -> pd.DataFrame:
        plain = _normalize_code(code)
        first_error = None
        try:
            df = self._ak().fund_etf_hist_em(
                symbol=plain,
                period="daily",
                start_date=(pd.to_datetime(end_date) - pd.Timedelta(days=days * 2)).strftime("%Y%m%d"),
                end_date=end_date.replace("-", ""),
                adjust="",
            )
        except Exception as exc:
            first_error = exc
            try:
                df = self._ak().fund_lof_hist_em(
                    symbol=plain,
                    period="daily",
                    start_date=(pd.to_datetime(end_date) - pd.Timedelta(days=days * 2)).strftime("%Y%m%d"),
                    end_date=end_date.replace("-", ""),
                    adjust="",
                )
            except Exception as lof_exc:
                raise DataSourceUnavailable(f"ETF历史数据不可用 {plain}: {str(first_error or lof_exc)[:120]}") from lof_exc
        if df is None or df.empty:
            return pd.DataFrame()
        rename = {"日期": "date", "收盘": "close", "成交量": "volume", "成交额": "amount"}
        df = df.rename(columns=rename)
        for col in ["close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
        return df.tail(days).reset_index(drop=True)

    def etf_spot_map(self) -> dict:
        try:
            df = self._etf_spot()
        except Exception as exc:
            raise DataSourceUnavailable(f"ETF实时行情不可用: {str(exc)[:120]}") from exc
        if df.empty:
            raise DataSourceUnavailable("ETF实时行情为空")
        result = {}
        for _, row in df.iterrows():
            code = _normalize_code(row.get("代码"))
            result[code] = {
                "name": row.get("名称") or code,
                "price": _to_float(row.get("最新价")),
                "volume": _to_float(row.get("成交量"), 0),
                "amount": _to_float(row.get("成交额"), 0),
            }
        return result

    def a_share_weak_state(self, trade_date: str) -> dict:
        indexes = {"沪深300": "000300", "小盘": "399101", "创业板": "399006", "中证A500": "000510"}
        below = 0
        above = 0
        details = {}
        for name, code in indexes.items():
            try:
                df = self._ak().index_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=(pd.to_datetime(trade_date) - pd.Timedelta(days=40)).strftime("%Y%m%d"),
                    end_date=trade_date.replace("-", ""),
                )
                close = pd.to_numeric(df["收盘"] if "收盘" in df.columns else df["close"], errors="coerce").dropna()
                if len(close) < 10:
                    continue
                cur = close.iloc[-1]
                ma10 = close.iloc[-10:].mean()
                above += int(cur > ma10)
                below += int(cur < ma10)
                details[name] = {"close": round(float(cur), 3), "ma10": round(float(ma10), 3), "above": bool(cur > ma10)}
            except Exception as exc:
                details[name] = {"warning": str(exc)[:120]}
                continue
        warning = None
        if len([item for item in details.values() if item.get("warning")]) >= 4:
            warning = "A股走弱判断指数数据全部不可用"
        return {"is_weak": below >= 3, "above_count": above, "below_count": below, "details": details, "warning": warning}


class BaseStrategy:
    key = ""

    def __init__(self, provider):
        self.provider = provider

    @property
    def name(self) -> str:
        return STRATEGY_META[self.key]["name"]

    def empty_result(self, trade_date: str, status: str = "success", error: Optional[str] = None) -> StrategyRunResult:
        return StrategyRunResult(
            strategy_key=self.key,
            strategy_name=self.name,
            trade_date=trade_date,
            status=status,
            error=error,
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )


class FirstBoardRelayStrategy(BaseStrategy):
    key = "first_board_relay"

    def build_universe(self, trade_date: str) -> list[dict]:
        previous = self.provider.previous_trade_date(trade_date)
        before_previous = self.provider.before_previous_trade_date(trade_date)
        return self.provider.first_board_candidates(previous, before_previous)

    def evaluate(self, trade_date: str, candidate: dict) -> Optional[Recommendation]:
        code = _normalize_code(candidate["code"])
        if not _is_base_stock_code(code, ("3", "4", "8", "9"), exclude_688=True):
            return None
        previous = self.provider.previous_trade_date(trade_date)
        hist = _history_with_amount(self.provider, code, previous, 35)
        if len(hist) < 6:
            return None
        last = hist.iloc[-1]
        d_b1, d_b2, d_b3 = hist.iloc[-2], hist.iloc[-3], hist.iloc[-4]

        high5 = hist["high"].tail(5).max()
        low5 = hist["low"].tail(5).min()
        if low5 and (high5 - low5) / low5 > 0.20:
            return None

        market_cap = _to_float(candidate.get("market_cap"))
        float_cap = _to_float(candidate.get("float_market_cap"))
        if market_cap is None or float_cap is None:
            return None
        if market_cap < 3_000_000_000:
            return None
        if float_cap > 30_000_000_000:
            return None

        amount = _to_float(last.get("amount"), _to_float(candidate.get("amount"), 0)) or 0
        if amount < 5e8 or amount > 30e8:
            return None
        if _to_float(d_b1.get("volume"), 0) <= 0 or _to_float(last.get("volume"), 0) < _to_float(d_b1.get("volume"), 0) * 2:
            return None
        if _to_float(d_b2.get("volume"), 0) <= 0 or _to_float(d_b1.get("volume"), 0) > _to_float(d_b2.get("volume"), 0) * 2:
            return None
        if len(hist) >= 31:
            prev_vols = hist["volume"].iloc[-6:-1].dropna()
            if len(prev_vols) == 5 and prev_vols.min() > 0:
                if (
                    (last["volume"] > prev_vols.mean() * 8 or last["volume"] > prev_vols.min() * 12)
                    and last["close"] > hist["high"].iloc[-31:-1].max()
                ):
                    return None
        if not (d_b1["close"] > d_b1["open"] and d_b2["close"] > d_b2["open"]):
            return None
        if d_b2["close"] == 0 or d_b3["close"] == 0:
            return None
        g1 = (d_b1["close"] - d_b2["close"]) / d_b2["close"]
        g2 = (d_b2["close"] - d_b3["close"]) / d_b3["close"]
        if g1 >= 0.05 or g2 >= 0.05:
            return None

        auction = self.provider.call_auction(code, trade_date, "09:15:00", "09:26:00")
        if not auction or auction.get("warning"):
            return Recommendation(
                strategy_key=self.key,
                strategy_name=self.name,
                code=code,
                name=candidate.get("name", code),
                rank=0,
                score=0,
                mode="待确认",
                reasons=["昨日首板且基础量价条件通过"],
                risks=["竞价数据不可用，未执行竞价过滤"],
                metrics={},
                data_status={"warning": auction.get("warning") if auction else "竞价数据为空"},
            )
        auction_price = _to_float(auction.get("price"))
        auction_volume = _to_float(auction.get("volume"), 0) or 0
        prev_close = _to_float(last.get("close"))
        prev_volume = _to_float(last.get("volume"), 0) or 0
        if not auction_price or not prev_close or prev_volume <= 0:
            return None
        volume_ratio = auction_volume / prev_volume
        auction_pct = auction_price / prev_close - 1
        if volume_ratio < 0.03 or auction_pct <= 0 or auction_pct >= 0.06:
            return None

        score = 70 + min(volume_ratio * 400, 18) + min(auction_pct * 100, 6)
        metrics = {
            "auction_price": round(auction_price, 3),
            "auction_pct": _safe_pct(auction_pct),
            "auction_volume_ratio": round(volume_ratio, 4),
            "amount_yi": round(amount / 1e8, 2),
            "volatility_5d": float(round((high5 - low5) / low5, 4)) if low5 else None,
        }
        return Recommendation(
            strategy_key=self.key,
            strategy_name=self.name,
            code=code,
            name=candidate.get("name", code),
            rank=0,
            score=round(score, 2),
            mode="首板接力",
            reasons=[
                "昨日首板且前一交易日未涨停",
                f"昨日成交额{metrics['amount_yi']}亿，放量结构通过",
                f"竞价涨幅{metrics['auction_pct']}%，竞价量占昨日量{metrics['auction_volume_ratio']:.2%}",
            ],
            risks=["短线接力策略波动较高，竞价高开过多或承接转弱需回避"],
            metrics=metrics,
            data_status={"warning": None},
        )

    def run(self, trade_date: str) -> StrategyRunResult:
        started = datetime.now()
        candidates = self.build_universe(trade_date)
        picks = [pick for item in candidates if (pick := self.evaluate(trade_date, item))]
        picks.sort(key=lambda rec: rec.score, reverse=True)
        for idx, rec in enumerate(picks, start=1):
            rec.rank = idx
        return StrategyRunResult(self.key, self.name, trade_date, "success", len(candidates), picks, None, started, datetime.now())


class LeaderChaseStrategy(BaseStrategy):
    key = "leader_chase"

    def build_universe(self, trade_date: str) -> list[dict]:
        previous = self.provider.previous_trade_date(trade_date)
        items = [
            item for item in self.provider.limit_up_candidates(previous)
            if _is_base_stock_code(item.get("code"), ("4", "8"), exclude_688=True)
        ]
        return sorted(items, key=lambda item: item.get("continue_count", 1), reverse=True)[:15]

    def evaluate(self, trade_date: str, candidate: dict, sentiment: dict) -> Optional[Recommendation]:
        code = _normalize_code(candidate["code"])
        previous = self.provider.previous_trade_date(trade_date)
        auction = self.provider.call_auction(code, trade_date, "09:15:00", "09:25:00")
        if not auction or auction.get("warning"):
            return Recommendation(
                self.key, self.name, code, candidate.get("name", code), 0, 0, "待确认",
                ["昨日涨停龙头候选"],
                ["竞价数据不可用，未执行龙头追击竞价过滤"],
                {"continue_count": candidate.get("continue_count", 1), "market_state": sentiment.get("state")},
                {"warning": auction.get("warning") if auction else "竞价数据为空"},
            )

        auction_price = _to_float(auction.get("price"))
        auction_vol = _to_float(auction.get("volume"), 0) or 0
        hist5 = _history_with_amount(self.provider, code, previous, 5)
        if len(hist5) < 2 or not auction_price:
            return None
        prev_close = _to_float(hist5["close"].iloc[-1])
        prev_vol = _to_float(hist5["volume"].iloc[-1], _to_float(candidate.get("volume"), 0)) or 0
        high_limit = _to_float(candidate.get("high_limit"))
        low_limit = _to_float(candidate.get("low_limit"))
        if high_limit and auction_price >= high_limit - 0.01:
            return None
        if low_limit and auction_price <= low_limit + 0.01:
            return None
        if prev_vol <= 0:
            return None
        if auction_vol / prev_vol < 0.01:
            return None
        if not prev_close:
            return None
        open_pct = auction_price / prev_close - 1
        dragon = int(candidate.get("continue_count") or 1)
        mode = None
        rel_pos = None
        if dragon >= 3 and 0.03 <= open_pct <= 0.09:
            mode = "龙追"
        elif 0.01 <= open_pct <= 0.07:
            mode = "高开"
        elif -0.05 <= open_pct <= -0.02:
            hist60 = _history_with_amount(self.provider, code, previous, 60)
            if len(hist60) >= 30:
                h60 = hist60["high"].max()
                l60 = hist60["low"].min()
                rel_pos = (prev_close - l60) / (h60 - l60) if h60 != l60 else 0.5
            else:
                rel_pos = 0.5
            if rel_pos <= 0.6:
                mode = "低开反弹"
        if not mode:
            return None
        if sentiment.get("state") == "bear" and mode != "低开反弹":
            return None

        volume_ratio = auction_vol / prev_vol if prev_vol else None
        score = 65 + dragon * 6 + max(min(open_pct * 100, 9), -5) + (5 if mode == "龙追" else 0)
        metrics = {
            "continue_count": dragon,
            "auction_price": round(auction_price, 3),
            "auction_pct": _safe_pct(open_pct),
            "auction_volume_ratio": round(volume_ratio, 4) if volume_ratio is not None else None,
            "market_state": sentiment.get("state", "cautious"),
            "relative_position_60d": round(rel_pos, 4) if rel_pos is not None else None,
        }
        return Recommendation(
            self.key,
            self.name,
            code,
            candidate.get("name", code),
            0,
            round(score, 2),
            mode,
            [
                f"昨日涨停候选，连板数{dragon}",
                f"市场情绪{sentiment.get('state', 'cautious')}",
                f"{mode}模式：竞价涨幅{metrics['auction_pct']}%",
            ],
            ["竞价追高与连板分歧风险较高，跌停/涨停附近已剔除"],
            metrics,
            {"warning": sentiment.get("warning")},
        )

    def run(self, trade_date: str) -> StrategyRunResult:
        started = datetime.now()
        sentiment = self.provider.market_sentiment(trade_date)
        candidates = self.build_universe(trade_date)
        picks = [pick for item in candidates if (pick := self.evaluate(trade_date, item, sentiment))]
        picks.sort(key=lambda rec: rec.score, reverse=True)
        picks = picks[:5]
        for idx, rec in enumerate(picks, start=1):
            rec.rank = idx
        return StrategyRunResult(self.key, self.name, trade_date, "success", len(candidates), picks, None, started, datetime.now())


GLOBAL_ETF_POOL = [
    "518880", "501018", "161226", "159985", "159980", "513310", "159518", "159509",
    "513100", "513520", "513500", "159502", "513400", "513030", "513290", "520830",
    "159529",
]

CHINA_ETF_POOL = [
    "513090", "513120", "513180", "513330", "513750", "159892", "513190", "159605",
    "513630", "159323", "510900", "513920", "513970", "511380", "512050", "510500",
    "159915", "510300", "512100", "159949", "588080", "159967", "588220", "563300",
    "510760", "588200", "515880", "159981", "512880", "513350", "159326", "159516",
    "159206", "512480", "159363", "159870", "512400", "159755", "588170", "159992",
    "159995", "512890", "515220", "159566", "159819", "512800", "512690", "515050",
    "562500", "512170", "517520", "159869", "512070", "159611", "562800", "515120",
    "512010", "510880", "515790", "515980", "512660", "159928", "512710", "560860",
    "515030", "159766", "159218", "159852", "516160", "516150", "159227", "159583",
    "588790", "159865", "512980", "159851", "561360", "561980", "562590", "512200",
    "159732", "159667", "516510", "159840", "159998", "159825", "512670", "159883",
    "515210", "515400", "159256", "561330", "515170", "159638", "516520", "513360",
    "516190",
]


def calculate_momentum_score(price_series, lookback_days: int):
    series = pd.Series(price_series).dropna().astype(float)
    if len(series) < lookback_days + 1:
        return None, None, None
    recent = series.iloc[-(lookback_days + 1):].to_numpy()
    y = np.log(recent)
    x = np.arange(len(y))
    weights = np.linspace(1, 2, len(y))
    w = weights ** 2
    w_sum = np.sum(w)
    x_bar = np.sum(w * x) / w_sum
    y_bar = np.sum(w * y) / w_sum
    dx = x - x_bar
    dy = y - y_bar
    variance_x = np.sum(w * dx ** 2)
    if variance_x == 0:
        return 0, 0, 0
    slope = np.sum(w * dx * dy) / variance_x
    intercept = y_bar - slope * x_bar
    annualized_returns = math.exp(slope * 250) - 1
    y_pred = slope * x + intercept
    ss_res = np.sum(weights * (y - y_pred) ** 2)
    ss_tot = np.sum(weights * (y - np.mean(y)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot else 0
    return annualized_returns * r_squared, annualized_returns, r_squared


def _laplace_filter(price, s=0.05):
    alpha = 1 - np.exp(-s)
    values = np.zeros(len(price))
    values[0] = price[0]
    for idx in range(1, len(price)):
        values[idx] = alpha * price[idx] + (1 - alpha) * values[idx - 1]
    return values


class EtfRotationStrategy(BaseStrategy):
    key = "etf_rotation"
    lookback_days = 25
    volume_lookback = 5
    ma_lookback = 10
    holdings_num = 1
    defensive_etf = "511880"

    def build_universe(self, trade_date: str) -> list[dict]:
        weak = self.provider.a_share_weak_state(trade_date)
        pool = GLOBAL_ETF_POOL if weak.get("is_weak") else sorted(set(GLOBAL_ETF_POOL + CHINA_ETF_POOL))
        spot = self.provider.etf_spot_map()
        return [{"code": code, "name": spot.get(code, {}).get("name", code), "weak_state": weak} for code in pool]

    def _volume_ratio(self, hist_volumes, today_vol):
        if hist_volumes is None or len(hist_volumes) < self.volume_lookback:
            return None
        values = np.array(hist_volumes[-self.volume_lookback:], dtype=float)
        if np.any(np.isnan(values)) or np.any(values == 0):
            return None
        avg = values.mean()
        return float(today_vol) / avg if avg else None

    def evaluate(self, trade_date: str, candidate: dict, spot: dict) -> Optional[Recommendation]:
        code = candidate["code"]
        end_date = self.provider.previous_trade_date(trade_date)
        hist = self.provider.etf_history(code, end_date, 70)
        if hist is None or hist.empty or len(hist) < self.lookback_days:
            return None
        spot_item = spot.get(code, {})
        current_price = _to_float(spot_item.get("price"), _to_float(hist["close"].iloc[-1]))
        today_vol = _to_float(spot_item.get("volume"), 0) or 0
        closes = pd.to_numeric(hist["close"], errors="coerce").dropna().to_numpy()
        volumes = pd.to_numeric(hist["volume"], errors="coerce").dropna().to_numpy() if "volume" in hist.columns else np.array([])
        if current_price is not None:
            price_series = np.append(closes, current_price)
        else:
            price_series = closes
        score, annualized, r2 = calculate_momentum_score(price_series, self.lookback_days)
        if score is None:
            return None
        volume_ratio = self._volume_ratio(volumes, today_vol)
        day_ratios = []
        if len(price_series) >= 4:
            day_ratios = [
                price_series[-1] / price_series[-2],
                price_series[-2] / price_series[-3],
                price_series[-3] / price_series[-4],
            ]
        passed_loss = min(day_ratios) >= 0.97 if day_ratios else False
        ma_value = np.mean(price_series[-self.ma_lookback:]) if len(price_series) >= self.ma_lookback else None
        passed_ma = bool(ma_value and current_price and current_price > ma_value)
        laplace_values = _laplace_filter(price_series, s=0.05) if len(price_series) >= 10 else []
        laplace_value = float(laplace_values[-1]) if len(laplace_values) else None
        laplace_slope = float(laplace_values[-1] - laplace_values[-2]) if len(laplace_values) >= 2 else 0
        passed_laplace = bool(current_price and laplace_value and current_price > laplace_value and laplace_slope >= 0.002)
        weak = candidate.get("weak_state", {}).get("is_weak", False)

        passed_momentum = 0 <= score <= 5
        passed_r2 = r2 is not None and r2 > 0.4
        passed_volume = volume_ratio is not None and volume_ratio < 1.8
        if not (passed_momentum and passed_volume and passed_loss and passed_laplace):
            return None
        if weak and not passed_ma:
            return None
        if not weak and not passed_r2:
            return None

        metrics = {
            "momentum_score": round(float(score), 4),
            "annualized_returns": round(float(annualized), 4),
            "r_squared": round(float(r2), 4),
            "volume_ratio": round(float(volume_ratio), 4) if volume_ratio is not None else None,
            "ma10": round(float(ma_value), 4) if ma_value is not None else None,
            "laplace_slope": round(float(laplace_slope), 5),
            "is_a_share_weak": weak,
        }
        return Recommendation(
            self.key,
            self.name,
            code,
            candidate.get("name", code),
            0,
            round(float(score), 4),
            "走弱期全球ETF" if weak else "正常期动量轮动",
            [
                f"动量得分{metrics['momentum_score']}",
                f"R²={metrics['r_squared']}，成交量比值={metrics['volume_ratio']}",
                "拉普拉斯趋势过滤通过",
            ],
            ["ETF轮动以13:10正式结果为准，本系统仅展示推荐不下单"],
            metrics,
            {"warning": None},
        )

    def run(self, trade_date: str) -> StrategyRunResult:
        started = datetime.now()
        try:
            spot = self.provider.etf_spot_map()
            candidates = self.build_universe(trade_date)
        except DataSourceUnavailable as exc:
            return StrategyRunResult(self.key, self.name, trade_date, "failed", 0, [], str(exc), started, datetime.now())

        weak_warning = None
        if candidates:
            weak_warning = (candidates[0].get("weak_state") or {}).get("warning")
        picks = [pick for item in candidates if (pick := self.evaluate(trade_date, item, spot))]
        picks.sort(key=lambda rec: rec.score, reverse=True)
        if not picks:
            defensive = spot.get(self.defensive_etf, {"name": "银华日利", "price": None})
            warning = weak_warning or "无ETF通过动量过滤，展示防御ETF可用性"
            picks = [
                Recommendation(
                    self.key,
                    self.name,
                    self.defensive_etf,
                    defensive.get("name", "银华日利"),
                    1,
                    0,
                    "防御观察",
                    ["无符合条件ETF，原策略进入防御/空仓判断"],
                    ["防御ETF仅作观察，不自动买入"],
                    {"price": defensive.get("price")},
                    {"warning": warning},
                )
            ]
        else:
            picks = picks[:10]
            picks = picks[:self.holdings_num]
            for idx, rec in enumerate(picks, start=1):
                rec.rank = idx
        for idx, rec in enumerate(picks, start=1):
            rec.rank = idx
        return StrategyRunResult(self.key, self.name, trade_date, "success", len(candidates), picks, None, started, datetime.now())


def get_strategy(strategy_key: str, provider=None) -> BaseStrategy:
    provider = provider or AkshareDailyPickProvider()
    strategies = {
        "first_board_relay": FirstBoardRelayStrategy,
        "leader_chase": LeaderChaseStrategy,
        "etf_rotation": EtfRotationStrategy,
    }
    if strategy_key not in strategies:
        raise ValueError(f"未知策略: {strategy_key}")
    return strategies[strategy_key](provider)


class DailyPickService:
    def __init__(self, provider=None):
        self.provider = provider or AkshareDailyPickProvider()

    def run_strategy(self, strategy_key: str, trade_date: Optional[str] = None) -> dict:
        resolved_date = self.provider.normalize_trade_date(trade_date)
        strategy = get_strategy(strategy_key, self.provider)
        try:
            result = strategy.run(resolved_date)
        except Exception as exc:
            now = datetime.now()
            result = StrategyRunResult(
                strategy_key,
                STRATEGY_META[strategy_key]["name"],
                resolved_date,
                "failed",
                0,
                [],
                str(exc)[:240],
                now,
                now,
            )
        save_strategy_run_results(
            result.strategy_key,
            result.trade_date,
            result.status,
            result.total_scanned,
            result.records(),
            error=result.error,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        return get_daily_picks(result.trade_date)

    def run(self, strategy_key: str = "all", trade_date: Optional[str] = None) -> dict:
        resolved_date = self.provider.normalize_trade_date(trade_date)
        keys = STRATEGY_KEYS if strategy_key == "all" else (strategy_key,)
        for key in keys:
            self.run_strategy(key, resolved_date)
        return get_daily_picks(resolved_date)
