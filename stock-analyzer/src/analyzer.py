"""
股票分析引擎
技术面分析(60%) + 基本面分析(40%) + 综合评分 + 买卖建议
功能升级版：趋势判断、支撑压力、量价关系、买卖信号、操作建议
"""

import numpy as np
import pandas as pd
from typing import Optional


# ─── 技术指标计算 ──────────────────────────────────────────

def calc_ma(series: pd.Series, period: int) -> float:
    """计算移动平均线"""
    if len(series) < period:
        return None
    return round(series.tail(period).mean(), 2)


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """计算指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(close: pd.Series) -> dict:
    """
    计算MACD指标
    返回: {macd, signal, hist}
    """
    if len(close) < 26:
        return {"macd": 0, "signal": 0, "hist": 0}

    ema12 = calc_ema(close, 12)
    ema26 = calc_ema(close, 26)
    dif = ema12 - ema26
    dea = calc_ema(dif, 9)
    macd_hist = 2 * (dif - dea)

    return {
        "macd": round(float(dif.iloc[-1]), 4),
        "signal": round(float(dea.iloc[-1]), 4),
        "hist": round(float(macd_hist.iloc[-1]), 4)
    }


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> dict:
    """
    计算KDJ指标
    返回: {k, d, j}
    """
    if len(close) < n:
        return {"k": 50, "d": 50, "j": 50}

    # 计算RSV
    low_n = low.rolling(window=n).min()
    high_n = high.rolling(window=n).max()
    rsv = ((close - low_n) / (high_n - low_n)) * 100
    rsv = rsv.fillna(50)

    # 计算K、D
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d

    return {
        "k": round(float(k.iloc[-1]), 2),
        "d": round(float(d.iloc[-1]), 2),
        "j": round(float(j.iloc[-1]), 2)
    }


def calc_rsi(close: pd.Series, period: int = 14) -> float:
    """计算RSI指标"""
    if len(close) < period + 1:
        return 50.0

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.inf)
    rsi = 100 - (100 / (1 + rs))

    val = rsi.iloc[-1]
    return round(float(val), 2) if pd.notna(val) else 50.0


def calc_bollinger(close: pd.Series, period: int = 20) -> dict:
    """计算布林带"""
    if len(close) < period:
        return {"upper": 0, "mid": 0, "lower": 0}

    mid = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()

    return {
        "upper": round(float((mid + 2 * std).iloc[-1]), 2),
        "mid": round(float(mid.iloc[-1]), 2),
        "lower": round(float((mid - 2 * std).iloc[-1]), 2)
    }


def calc_volume_ratio(volume: pd.Series, short: int = 5, long: int = 20) -> float:
    """计算量比（短期均量/长期均量）"""
    if len(volume) < long:
        return 1.0
    short_avg = volume.tail(short).mean()
    long_avg = volume.tail(long).mean()
    if long_avg == 0:
        return 1.0
    return round(float(short_avg / long_avg), 2)


# ─── 趋势分析 ────────────────────────────────────────────

def analyze_trend(close: pd.Series) -> dict:
    """
    趋势分析：短期(5日)、中期(20日)、长期(60日)
    返回: {short, mid, long, summary}
    """
    def _trend_direction(series_len: int, label: str):
        if len(close) < series_len + 1:
            return {"direction": "flat", "icon": "→", "desc": f"数据不足，无法判断{label}趋势", "change_pct": 0}

        ma = close.tail(series_len).mean()
        prev_ma = close.iloc[-(series_len + 1):-1].mean() if len(close) >= series_len + 1 else ma
        change_pct = round(((float(close.iloc[-1]) / float(close.iloc[-(series_len + 1)])) - 1) * 100, 2) if len(close) >= series_len + 1 else 0
        slope = ma - prev_ma
        if slope > 0:
            direction = "up"
            icon = "↗️"
            desc = f"{series_len}日均线向上，近{series_len}日{change_pct:+.2f}%"
        elif slope < 0:
            direction = "down"
            icon = "↘️"
            desc = f"{series_len}日均线向下，近{series_len}日{change_pct:+.2f}%"
        else:
            direction = "flat"
            icon = "→"
            desc = f"{series_len}日均线走平，近{series_len}日{change_pct:+.2f}%"
        return {"direction": direction, "icon": icon, "desc": desc, "change_pct": change_pct}

    short = _trend_direction(5, "短期")
    mid = _trend_direction(20, "中期")
    long = _trend_direction(60, "长期")

    # 趋势综合研判
    up_count = sum(1 for t in [short, mid, long] if t["direction"] == "up")
    down_count = sum(1 for t in [short, mid, long] if t["direction"] == "down")
    if up_count >= 2:
        summary = "多头趋势占优，整体偏强"
    elif down_count >= 2:
        summary = "空头趋势占优，整体偏弱"
    elif short["direction"] == "up" and mid["direction"] == "down":
        summary = "短期反弹，中期仍弱，关注能否突破中期均线"
    elif short["direction"] == "down" and mid["direction"] == "up":
        summary = "短期回调，中期趋势尚可，关注支撑"
    else:
        summary = "趋势不明，建议观望等待方向确认"

    return {"short": short, "mid": mid, "long": long, "summary": summary}


def calc_support_resistance(close: pd.Series, high: pd.Series, low: pd.Series,
                            boll_data: dict, ma60: float, current_price: float) -> dict:
    """
    计算支撑位和压力位
    返回: {support_levels: [...], resistance_levels: [...]}
    """
    candidates_support = []
    candidates_resistance = []

    # 近20日最低点
    if len(low) >= 20:
        low20 = round(float(low.tail(20).min()), 2)
        if low20 < current_price:
            candidates_support.append((low20, "近20日最低"))
        else:
            candidates_resistance.append((low20, "近20日最低"))

    # 近20日最高点
    if len(high) >= 20:
        high20 = round(float(high.tail(20).max()), 2)
        if high20 > current_price:
            candidates_resistance.append((high20, "近20日最高"))
        else:
            candidates_support.append((high20, "近20日最高"))

    # 布林带下轨
    if boll_data["lower"] > 0:
        bl = boll_data["lower"]
        if bl < current_price:
            candidates_support.append((bl, "布林下轨"))
        else:
            candidates_resistance.append((bl, "布林下轨"))

    # 布林带上轨
    if boll_data["upper"] > 0:
        bu = boll_data["upper"]
        if bu > current_price:
            candidates_resistance.append((bu, "布林上轨"))
        else:
            candidates_support.append((bu, "布林上轨"))

    # MA60
    if ma60 is not None:
        if ma60 < current_price:
            candidates_support.append((ma60, "MA60"))
        else:
            candidates_resistance.append((ma60, "MA60"))

    # 整数关口
    base = int(current_price)
    round_down = base if base < current_price else base - 1
    round_up = base + 1 if base <= current_price else base
    if round_down > 0:
        candidates_support.append((float(round_down), "整数关口"))
    candidates_resistance.append((float(round_up), "整数关口"))

    # 取最接近当前价的2个支撑位和2个压力位
    supports = sorted([(p, l) for p, l in candidates_support if p < current_price], key=lambda x: current_price - x[0])[:2]
    resistances = sorted([(p, l) for p, l in candidates_resistance if p > current_price], key=lambda x: x[0] - current_price)[:2]

    return {
        "support_levels": [round(p, 2) for p, _ in supports],
        "support_labels": [l for _, l in supports],
        "resistance_levels": [round(p, 2) for p, _ in resistances],
        "resistance_labels": [l for _, l in resistances]
    }


def analyze_volume_price(volume: pd.Series, close: pd.Series) -> dict:
    """
    量价关系分析
    返回: {vol_trend, price_vol_harmony, detail}
    """
    if len(volume) < 5 or len(close) < 5:
        return {"vol_trend": "数据不足", "price_vol_harmony": "未知", "detail": "量价数据不足"}

    vol_5 = volume.tail(5).values
    close_5 = close.tail(5).values

    # 成交量趋势
    vol_ma5 = vol_5.mean()
    vol_ma_prev5 = volume.iloc[-10:-5].mean() if len(volume) >= 10 else vol_ma5
    if vol_ma_prev5 == 0:
        vol_trend = "平稳"
    elif vol_ma5 / vol_ma_prev5 > 1.3:
        vol_trend = "放量"
    elif vol_ma5 / vol_ma_prev5 < 0.7:
        vol_trend = "缩量"
    else:
        vol_trend = "平稳"

    # 量价配合度
    price_up = close_5[-1] > close_5[0]
    vol_up = vol_5[-1] > vol_5[0]
    if price_up and vol_up:
        harmony = "价涨量增，量价配合健康"
    elif price_up and not vol_up:
        harmony = "价涨量缩，量价背离，上涨动能不足"
    elif not price_up and vol_up:
        harmony = "价跌量增，抛压较重"
    else:
        harmony = "价跌量缩，下跌动能减弱"

    return {"vol_trend": vol_trend, "price_vol_harmony": harmony, "detail": f"近5日成交量{vol_trend}，{harmony}"}


def generate_signals(macd_data: dict, kdj_data: dict, rsi: float,
                     close_price: float, boll_data: dict,
                     ma5: float, ma10: float, ma20: float, ma60: float) -> dict:
    """
    生成买卖信号
    返回: {buy: [...], sell: [...], strength: buy/sell/neutral}
    """
    buy_signals = []
    sell_signals = []

    dif = macd_data["macd"]
    dea = macd_data["signal"]
    hist = macd_data["hist"]
    k = kdj_data["k"]
    d = kdj_data["d"]
    j = kdj_data["j"]

    # MACD信号
    if dif > dea:
        buy_signals.append("MACD金叉状态")
    else:
        sell_signals.append("MACD死叉状态")
    if hist > 0 and dif > 0:
        buy_signals.append("MACD红柱+零轴上方，多头强势")
    elif hist < 0 and dif < 0:
        sell_signals.append("MACD绿柱+零轴下方，空头强势")

    # KDJ信号
    if k < 20 and d < 20:
        buy_signals.append("KDJ超卖（K<20），存在反弹机会")
    elif k > 80 and d > 80:
        sell_signals.append("KDJ超买（K>80），注意回调风险")
    if k > d:
        buy_signals.append("KDJ金叉（K>D）")
    else:
        sell_signals.append("KDJ死叉（K<D）")

    # RSI信号
    if rsi < 30:
        buy_signals.append(f"RSI超卖（RSI={rsi:.1f}<30），超跌反弹概率大")
    elif rsi > 70:
        sell_signals.append(f"RSI超买（RSI={rsi:.1f}>70），短期回调风险")

    # 布林带信号
    if boll_data["lower"] > 0:
        boll_width = boll_data["upper"] - boll_data["lower"]
        if boll_width > 0:
            position = (close_price - boll_data["lower"]) / boll_width
            if position < 0.1:
                buy_signals.append("价格接近布林下轨，支撑位买入")
            elif position > 0.9:
                sell_signals.append("价格接近布林上轨，压力位卖出")

    # 均线信号
    if ma5 and ma10 and ma20 and ma60:
        if ma5 > ma10 > ma20 > ma60:
            buy_signals.append("均线多头排列，趋势强劲")
        elif ma5 < ma10 < ma20 < ma60:
            sell_signals.append("均线空头排列，趋势偏弱")
        if close_price > ma5 and close_price < ma20:
            buy_signals.append("价格突破MA5，短期转强")
        elif close_price < ma5 and close_price > ma20:
            sell_signals.append("价格跌破MA5，短期转弱")

    # 综合判断
    buy_count = len(buy_signals)
    sell_count = len(sell_signals)
    if buy_count > sell_count + 1:
        strength = "buy"
    elif sell_count > buy_count + 1:
        strength = "sell"
    else:
        strength = "neutral"

    return {"buy": buy_signals, "sell": sell_signals, "strength": strength}


def generate_action(signals: dict, total_score: float, current_price: float,
                    support_levels: list, resistance_levels: list) -> dict:
    """
    生成操作建议
    返回: {operation, position, stop_loss, take_profit, period, risk_level}
    """
    strength = signals["strength"]

    # 建议操作
    if total_score >= 75:
        operation = "买入"
        position = "重仓"
    elif total_score >= 60:
        operation = "买入"
        position = "半仓"
    elif total_score >= 50:
        operation = "持有"
        position = "半仓"
    elif total_score >= 40:
        operation = "观望"
        position = "轻仓"
    elif total_score >= 25:
        operation = "卖出"
        position = "轻仓"
    else:
        operation = "卖出"
        position = "清仓"

    # 根据信号调整
    if strength == "buy" and operation in ["观望", "持有"]:
        operation = "轻仓买入"
        position = "轻仓"
    elif strength == "sell" and operation in ["买入", "持有"]:
        operation = "减仓"
        position = "轻仓"

    # 止损价（当前价下方3-5%）
    stop_loss_pct = 0.05 if total_score < 40 else 0.03
    stop_loss = round(current_price * (1 - stop_loss_pct), 2)

    # 止盈价（最近压力位）
    take_profit = resistance_levels[0] if resistance_levels else round(current_price * 1.1, 2)

    # 持有周期
    if total_score >= 70:
        period = "短线1-3天"
    elif total_score >= 50:
        period = "中线1-2周"
    else:
        period = "长线1月+"

    # 风险等级
    if total_score >= 70:
        risk_level = "低"
    elif total_score >= 45:
        risk_level = "中等"
    else:
        risk_level = "高"

    return {
        "operation": operation,
        "position": position,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "period": period,
        "risk_level": risk_level
    }


def evaluate_valuation(pe_ratio, pb_ratio) -> dict:
    """
    估值评价
    返回: {pe_level, pb_level, summary}
    """
    # PE评价
    if pe_ratio is None or pe_ratio <= 0:
        pe_level = "数据不可用"
    elif pe_ratio < 15:
        pe_level = "低估"
    elif pe_ratio < 30:
        pe_level = "合理"
    elif pe_ratio < 60:
        pe_level = "偏高"
    else:
        pe_level = "高估"

    # PB评价
    if pb_ratio is None or pb_ratio <= 0:
        pb_level = "数据不可用"
    elif pb_ratio < 1:
        pb_level = "破净低估"
    elif pb_ratio < 2:
        pb_level = "合理"
    elif pb_ratio < 5:
        pb_level = "偏高"
    else:
        pb_level = "高估"

    # 综合估值
    levels = [l for l in [pe_level, pb_level] if "不可用" not in l]
    if not levels:
        summary = "估值数据不足"
    elif all("低估" in l or "破净" in l for l in levels):
        summary = "综合估值偏低，具备投资价值"
    elif all("高估" in l for l in levels):
        summary = "综合估值偏高，注意风险"
    elif any("高估" in l for l in levels):
        summary = "部分指标估值偏高，需谨慎"
    else:
        summary = "综合估值适中"

    return {"pe_level": pe_level, "pb_level": pb_level, "summary": summary}



def classify_market_cap(market_cap) -> str:
    """市值分类"""
    if market_cap is None:
        return "未知"
    cap_yi = market_cap if market_cap is not None else 0
    if cap_yi > 1000:
        return f"大盘股（{cap_yi:.0f}亿），波动较小，稳定性好"
    elif cap_yi > 100:
        return f"中盘股（{cap_yi:.0f}亿），适中风险收益"
    else:
        return f"小盘股（{cap_yi:.0f}亿），波动较大，高风险高收益"


# ─── 流量定价模型分析 ──────────────────────────────────────

def _clamp_score(value, default: float = 50.0) -> int:
    """Normalize a numeric score to 0-100."""
    number = _to_float(value, default)
    if number is None:
        number = default
    if 0 <= number <= 1:
        number *= 100
    return int(round(max(0, min(100, number))))


def _first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _score_from_signed(value, default: float = 50.0) -> int:
    number = _to_float(value)
    if number is None:
        return _clamp_score(default)
    if -1 <= number <= 1:
        return _clamp_score((number + 1) * 50)
    if -100 <= number <= 100:
        return _clamp_score(number + 50 if number < 0 else number)
    return _clamp_score(number, default)


def _rank_heat_score(rank, total: int = 100, default: int = 50) -> int:
    try:
        rank_num = int(rank)
        total_num = max(int(total or 100), 1)
    except (TypeError, ValueError):
        return default
    if rank_num <= 0:
        return default
    percentile = 1 - ((rank_num - 1) / total_num)
    return _clamp_score(20 + percentile * 80, default)


def _rank_change_heat_score(rank_change, default: int = 50) -> int:
    change = _to_float(rank_change)
    if change is None:
        return default
    return _clamp_score(50 + change * 4, default)


def _safe_len(value) -> int:
    return len(value) if isinstance(value, (list, tuple, set, dict)) else 0


def _normalize_stock_code(stock_info: dict) -> str:
    raw_code = _first_present(stock_info.get("code"), stock_info.get("stock_code"), stock_info.get("symbol"))
    if raw_code is None:
        return ""
    digits = "".join(ch for ch in str(raw_code) if ch.isdigit())
    if not digits:
        return ""
    return digits[-6:].zfill(6)


def _stock_market_prefix(code: str) -> str:
    if str(code).startswith(("0", "2", "3")):
        return "SZ"
    if str(code).startswith(("4", "8", "9")):
        return "BJ"
    return "SH"


def _discussion_links(stock_info: dict) -> dict:
    code = _normalize_stock_code(stock_info)
    if not code:
        return {}
    prefix = _stock_market_prefix(code)
    return {
        "eastmoney_guba": f"https://guba.eastmoney.com/list,{code}.html",
        "eastmoney_quote": f"https://quote.eastmoney.com/{prefix.lower()}{code}.html",
        "xueqiu": f"https://xueqiu.com/S/{prefix}{code}",
    }


def _build_heat_trend_profile(
    current_score: int,
    trend_score: int,
    acceleration_score: int,
    rank_change_score: int,
    direct_external_available: bool,
) -> dict:
    current = _clamp_score(current_score)
    trend = _clamp_score(trend_score)
    acceleration = _clamp_score(acceleration_score)
    rank_change = _clamp_score(rank_change_score)

    pressure = (trend - 50) * 0.72 + (acceleration - 50) * 0.20 + (rank_change - 50) * 0.08
    if not direct_external_available:
        pressure *= 0.86
    inferred_change = int(round(max(-35, min(35, pressure * 0.58))))
    if inferred_change == 0:
        if trend >= 58:
            inferred_change = 3
        elif trend <= 42:
            inferred_change = -3

    start = _clamp_score(current - inferred_change)
    change = current - start
    ratios = [0, 0.22, 0.48, 0.74, 1]
    points = [_clamp_score(start + change * ratio) for ratio in ratios]
    direction = "升温" if change > 0 else "降温" if change < 0 else "横盘"

    return {
        "start_score": start,
        "current_score": current,
        "change_score": change,
        "points": points,
        "direction": direction,
        "window": "5d",
        "basis": "代理热度",
        "is_proxy": True,
        "source": "东财排名变化/关键词/板块排行/成交代理",
        "summary": f"代理热度 {start} → {current}（{change:+d}）",
    }


def _stock_topic_keywords(stock_info: dict, heat: dict) -> list[str]:
    raw = heat.get("raw") if isinstance(heat.get("raw"), dict) else {}
    stock_hints = heat.get("stock_ranking_hints") or {}
    keyword_hits = stock_hints.get("keyword_hits") if isinstance(stock_hints, dict) else []
    keywords = []
    for item in keyword_hits if isinstance(keyword_hits, list) else []:
        if isinstance(item, dict):
            keyword = item.get("keyword") or item.get("name")
        else:
            keyword = item
        if keyword:
            keywords.append(str(keyword))
    for text in [
        stock_info.get("name"),
        heat.get("sector_name"),
        heat.get("industry"),
        raw.get("sector_name") if isinstance(raw, dict) else None,
    ]:
        if text:
            keywords.append(str(text))
    return keywords


def _topic_conversion_score(stock_info: dict, heat: dict) -> tuple[int, str, list[str]]:
    sector_name = str(heat.get("sector_name") or "")
    sector_type = str(heat.get("sector_type") or "")
    stock_name = str(stock_info.get("name") or "")
    stock_hints = heat.get("stock_ranking_hints") or {}
    keyword_hits = stock_hints.get("keyword_hits") if isinstance(stock_hints, dict) else []

    score = 40
    reasons = []
    if sector_name and "未知" not in sector_name:
        score += 15
        reasons.append(f"题材名称为「{sector_name}」，散户识别成本较低")
    else:
        score -= 12
        reasons.append("板块题材暂未识别，转化率降级")

    if sector_type == "concept":
        score += 12
        reasons.append("概念板块优先，短线叙事更容易传播")
    elif sector_type == "industry":
        score += 6
        reasons.append("行业板块可识别，但短线传播通常弱于概念板块")

    if keyword_hits:
        score += min(14, _safe_len(keyword_hits) * 4)
        reasons.append("东财热门关键词有命中")

    recognizable_words = [
        "智能", "人工智能", "ai", "算力", "deepseek", "机器人", "芯片", "半导体",
        "国产", "替代", "信创", "军工", "航天", "卫星", "低空", "量子",
        "新能源", "储能", "光伏", "数据", "传媒", "游戏", "医药",
    ]
    text_blob = " ".join(_stock_topic_keywords(stock_info, heat)).lower()
    hot_hits = [word for word in recognizable_words if word.lower() in text_blob]
    if hot_hits:
        score += min(16, 6 + len(hot_hits) * 2)
        reasons.append("题材包含高转化叙事：" + "、".join(hot_hits[:4]))
    elif stock_name and sector_name and any(ch in stock_name for ch in sector_name[:4]):
        score += 5
        reasons.append("股票名称与题材存在直观关联")
    else:
        reasons.append("暂未看到特别强的名字/概念直观识别点")

    if heat.get("external_heat_score", 50) >= 75:
        score += 8
        reasons.append("外部讨论代理热度较高，转化漏斗更顺")

    label = "高转化" if score >= 75 else "中等转化" if score >= 55 else "低转化"
    return _clamp_score(score), label, reasons


def _risk_label_from_score(score: int, phase: str = "") -> str:
    if phase == "退潮" or score >= 82:
        return "退潮回避" if phase == "退潮" else "高潮风险"
    if score >= 68:
        return "高热分歧"
    if score >= 50:
        return "分歧观察"
    if phase in ["启动", "冷启动"]:
        return "低位升温"
    if phase == "扩散":
        return "扩散中"
    return "低位升温"


def _normalize_heat_context(stock_info: dict) -> dict:
    heat_context = stock_info.get("heat_context") or {}
    if not isinstance(heat_context, dict):
        heat_context = {}
    sector_ctx = heat_context.get("sector") if isinstance(heat_context.get("sector"), dict) else {}
    capital_ctx = heat_context.get("capital") if isinstance(heat_context.get("capital"), dict) else {}
    leader_ctx = heat_context.get("leader") if isinstance(heat_context.get("leader"), dict) else {}
    stock_ranking_hints = heat_context.get("stock_ranking_hints") if isinstance(heat_context.get("stock_ranking_hints"), dict) else {}
    board_ranking_hints = heat_context.get("board_ranking_hints") if isinstance(heat_context.get("board_ranking_hints"), dict) else {}
    score_details = heat_context.get("score_details") if isinstance(heat_context.get("score_details"), dict) else {}
    data_status = heat_context.get("data_status") if isinstance(heat_context.get("data_status"), dict) else {}

    sector_name = _first_present(
        heat_context.get("sector_name"),
        sector_ctx.get("name"),
        stock_info.get("sector_name"),
        stock_info.get("industry"),
    )
    industry = _first_present(stock_info.get("industry"), heat_context.get("industry"), sector_ctx.get("industry"))

    sector_heat_raw = _first_present(
        heat_context.get("sector_heat_score"),
        heat_context.get("sector_heat"),
        heat_context.get("heat"),
        sector_ctx.get("heat_score"),
        sector_ctx.get("heat"),
    )
    external_heat_raw = _first_present(
        heat_context.get("external_heat_score"),
        heat_context.get("external_heat"),
        heat_context.get("market_heat_score"),
        heat_context.get("market_heat"),
        sector_ctx.get("external_heat_score"),
        sector_ctx.get("external_heat"),
    )
    capital_slope_raw = _first_present(
        heat_context.get("capital_slope_score"),
        heat_context.get("capital_slope"),
        heat_context.get("fund_slope_score"),
        heat_context.get("fund_slope"),
        capital_ctx.get("slope_score"),
        capital_ctx.get("slope"),
    )
    heat_acceleration_raw = _first_present(
        heat_context.get("heat_acceleration_score"),
        heat_context.get("heat_acceleration"),
        heat_context.get("acceleration_score"),
        heat_context.get("acceleration"),
        sector_ctx.get("acceleration_score"),
        sector_ctx.get("acceleration"),
    )
    exit_risk_raw = _first_present(
        heat_context.get("exit_risk_score"),
        heat_context.get("exit_risk"),
        heat_context.get("risk_score"),
        capital_ctx.get("exit_risk_score"),
        capital_ctx.get("exit_risk"),
    )

    sector_heat_score = _clamp_score(sector_heat_raw)
    external_heat_score = _clamp_score(external_heat_raw)
    capital_slope_score = _score_from_signed(capital_slope_raw)
    heat_acceleration_score = _score_from_signed(heat_acceleration_raw)
    exit_risk_score = _clamp_score(exit_risk_raw, 35)

    rank = _first_present(
        heat_context.get("sector_rank"),
        heat_context.get("rank"),
        sector_ctx.get("rank"),
        leader_ctx.get("rank"),
    )
    rank_total = _first_present(
        heat_context.get("sector_rank_total"),
        heat_context.get("rank_total"),
        sector_ctx.get("rank_total"),
        leader_ctx.get("rank_total"),
    )
    rank_hint = _first_present(heat_context.get("rank_hint"), leader_ctx.get("rank_hint"))
    if not rank_hint and rank is not None:
        rank_hint = f"板块排名第{rank}" + (f"/{rank_total}" if rank_total else "")

    leader_label = _first_present(heat_context.get("leader_label"), leader_ctx.get("label"))
    board_inner_rank = _first_present(
        heat_context.get("board_inner_rank"),
        heat_context.get("rank_in_sector"),
        heat_context.get("stock_rank_in_sector"),
        leader_ctx.get("board_inner_rank"),
        leader_ctx.get("rank_in_sector"),
        leader_ctx.get("stock_rank_in_sector"),
    )
    board_inner_rank_total = _first_present(
        heat_context.get("board_inner_rank_total"),
        heat_context.get("rank_in_sector_total"),
        leader_ctx.get("board_inner_rank_total"),
        leader_ctx.get("rank_in_sector_total"),
    )

    return {
        "sector_name": sector_name or "未知",
        "industry": industry or "未知",
        "sector_heat_score": sector_heat_score,
        "sector_heat_window": _first_present(
            heat_context.get("sector_heat_window"),
            heat_context.get("window"),
            sector_ctx.get("window"),
        ),
        "sector_heat_date": _first_present(
            heat_context.get("sector_heat_date"),
            heat_context.get("date"),
            sector_ctx.get("date"),
        ),
        "sector_source": _first_present(
            heat_context.get("sector_source"),
            heat_context.get("source"),
            sector_ctx.get("source"),
        ),
        "sector_confidence": _first_present(
            heat_context.get("sector_confidence"),
            heat_context.get("confidence"),
            sector_ctx.get("confidence"),
        ),
        "sector_type": _first_present(
            heat_context.get("sector_type"),
            sector_ctx.get("type"),
            sector_ctx.get("sector_type"),
        ),
        "external_heat_score": external_heat_score,
        "capital_slope_score": capital_slope_score,
        "heat_acceleration_score": heat_acceleration_score,
        "exit_risk_score": exit_risk_score,
        "sector_rank": rank,
        "sector_rank_total": rank_total,
        "rank_hint": rank_hint or "暂无板块排名",
        "leader_label": leader_label,
        "board_inner_rank": board_inner_rank,
        "board_inner_rank_total": board_inner_rank_total,
        "stock_ranking_hints": stock_ranking_hints,
        "board_ranking_hints": board_ranking_hints,
        "score_details": score_details,
        "data_status": data_status,
        "warnings": data_status.get("warnings", []) if isinstance(data_status.get("warnings"), list) else [],
        "raw": heat_context,
    }


def _append_source_once(sources: list[dict], key: str, label: str, score: Optional[float] = None, status: str = "ok") -> None:
    if any(item.get("key") == key for item in sources):
        return
    source = {"key": key, "label": label, "status": status}
    if score is not None:
        source["score"] = _clamp_score(score)
    sources.append(source)


def _build_retail_calculation_sources(
    heat: dict,
    eastmoney_rank,
    keyword_hits: list,
    snowball_hits: list,
    amount_proxy_ratio,
    volume_burst: float,
) -> list[dict]:
    sources: list[dict] = []
    sector_source = str(heat.get("sector_source") or "")
    if heat.get("sector_heat_score") is not None and heat.get("sector_name") != "未知":
        _append_source_once(sources, "sector_board", "板块榜单", heat.get("sector_heat_score"))
    if "eastmoney" in sector_source:
        _append_source_once(sources, "eastmoney_board", "东财板块/人气", heat.get("external_heat_score"))
    if eastmoney_rank or keyword_hits:
        _append_source_once(sources, "eastmoney_hot", "东财人气/关键词", heat.get("external_heat_score"))
    if snowball_hits:
        _append_source_once(sources, "xueqiu", "雪球关注", 55 + _safe_len(snowball_hits) * 12)
    if amount_proxy_ratio is not None or volume_burst:
        _append_source_once(sources, "local_price_volume", "本地成交/量能", heat.get("capital_slope_score"), "fallback")
    if "ths_hot" in sector_source or "ths_hot_reason" in sector_source:
        _append_source_once(sources, "ths_hot_reason", "同花顺题材归因", heat.get("external_heat_score"), "fallback")
    return sources


def analyze_flow_signals(df: pd.DataFrame, stock_info: dict) -> dict:
    """
    retail_attention_v2：散户注意力与题材接盘潜力评分。

    量价指标只作为资金承接的辅助证据，不再作为“流量”主评分。
    """
    close = df["close"]
    volume = df["volume"]

    # 辅助承接证据：成交量/成交额代理。它们不直接等同于流量。
    if len(volume) >= 20:
        vol_5 = float(volume.tail(5).mean())
        vol_20 = float(volume.tail(20).mean())
        volume_burst = round(vol_5 / vol_20, 2) if vol_20 > 0 else 1.0
    else:
        volume_burst = 1.0

    if len(close) >= 6:
        price_momentum = round(((float(close.iloc[-1]) / float(close.iloc[-6])) - 1) * 100, 2)
    else:
        price_momentum = 0.0

    up_days = 0
    for i in range(-1, max(-6, -len(close)), -1):
        if float(close.iloc[i]) > float(close.iloc[i - 1]):
            up_days += 1
        else:
            break

    if len(close) >= 11:
        returns = close.pct_change().dropna().tail(10)
        volatility = round(float(returns.std()) * 100, 2)
    else:
        volatility = 1.0

    amount_proxy_ratio = None
    if "amount" in df.columns and len(df) >= 8:
        amount = pd.to_numeric(df["amount"], errors="coerce").dropna()
        if len(amount) >= 8:
            recent_amount = float(amount.tail(3).mean())
            previous_amount = float(amount.iloc[-8:-3].mean())
            amount_proxy_ratio = recent_amount / previous_amount if previous_amount > 0 else None
    volume_proxy_score = _clamp_score(50 + (volume_burst - 1) * 30)
    amount_proxy_score = _clamp_score(50 + ((amount_proxy_ratio or 1) - 1) * 30)

    heat = _normalize_heat_context(stock_info)
    sector_heat_score = heat["sector_heat_score"]
    external_heat_score = heat["external_heat_score"]
    provider_capital_slope_score = heat["capital_slope_score"]
    heat_acceleration_score = heat["heat_acceleration_score"]
    stock_hints = heat.get("stock_ranking_hints") or {}
    eastmoney_rank = stock_hints.get("eastmoney_hot_rank")
    eastmoney_rank_change = stock_hints.get("eastmoney_rank_change")
    keyword_hits = stock_hints.get("keyword_hits") if isinstance(stock_hints.get("keyword_hits"), list) else []
    snowball_hits = stock_hints.get("snowball_hits") if isinstance(stock_hints.get("snowball_hits"), list) else []

    eastmoney_rank_score = _rank_heat_score(eastmoney_rank, 100, 45 if not eastmoney_rank else 50)
    eastmoney_rank_change_score = _rank_change_heat_score(eastmoney_rank_change, 50)
    keyword_score = _clamp_score(45 + _safe_len(keyword_hits) * 8)
    snowball_score = _clamp_score(45 + _safe_len(snowball_hits) * 12)

    direct_external_available = bool(eastmoney_rank or keyword_hits or snowball_hits)
    calculation_sources = _build_retail_calculation_sources(
        heat,
        eastmoney_rank,
        keyword_hits,
        snowball_hits,
        amount_proxy_ratio,
        volume_burst,
    )
    retail_attention_score = round(
        external_heat_score * 0.40
        + sector_heat_score * 0.24
        + eastmoney_rank_score * 0.16
        + keyword_score * 0.10
        + snowball_score * 0.10
    )
    if not direct_external_available:
        retail_attention_score = round(retail_attention_score * 0.84 + 42 * 0.16)
    retail_attention_score = _clamp_score(retail_attention_score)

    topic_conversion_score, topic_conversion_level, topic_conversion_reasons = _topic_conversion_score(stock_info, heat)
    heat_trend_score = round(
        heat_acceleration_score * 0.50
        + eastmoney_rank_change_score * 0.25
        + retail_attention_score * 0.15
        + sector_heat_score * 0.10
    )
    heat_trend_score = _clamp_score(heat_trend_score)
    heat_trend_profile = _build_heat_trend_profile(
        retail_attention_score,
        heat_trend_score,
        heat_acceleration_score,
        eastmoney_rank_change_score,
        direct_external_available,
    )

    capital_acceptance_score = round(
        provider_capital_slope_score * 0.62
        + amount_proxy_score * 0.20
        + volume_proxy_score * 0.12
        + max(0, 100 - volatility * 8) * 0.06
    )
    capital_acceptance_score = _clamp_score(capital_acceptance_score)

    high_attention = max(retail_attention_score, external_heat_score, sector_heat_score)
    saturation_risk = max(0, high_attention - 76) * 1.15
    capital_drag_risk = max(0, 55 - capital_acceptance_score) * 0.82
    trend_drag_risk = max(0, 45 - heat_trend_score) * 0.72
    price_overheat_risk = max(0, price_momentum - 18) * 1.1 + max(0, up_days - 4) * 4
    exit_risk_score = _clamp_score(
        heat["exit_risk_score"] * 0.34
        + saturation_risk
        + capital_drag_risk
        + trend_drag_risk
        + price_overheat_risk
    )

    takeover_raw = round(
        retail_attention_score * 0.32
        + heat_trend_score * 0.20
        + topic_conversion_score * 0.20
        + capital_acceptance_score * 0.20
        + sector_heat_score * 0.08
    )
    if exit_risk_score >= 72 and capital_acceptance_score <= 45:
        takeover_raw -= 18
    elif exit_risk_score >= 68:
        takeover_raw -= 8
    takeover_potential_score = _clamp_score(takeover_raw)

    if retail_attention_score >= 78 and heat_trend_score >= 60:
        attention_direction = "扩散"
    elif retail_attention_score >= 58 and heat_trend_score >= 52:
        attention_direction = "升温"
    elif high_attention >= 76 and (capital_acceptance_score <= 45 or heat_trend_score <= 42):
        attention_direction = "高潮"
    elif heat_trend_score <= 38 and capital_acceptance_score <= 45:
        attention_direction = "退潮"
    else:
        attention_direction = "横盘"

    if takeover_potential_score >= 80:
        flow_level = "强"
    elif takeover_potential_score >= 65:
        flow_level = "较强"
    elif takeover_potential_score >= 45:
        flow_level = "中性"
    elif takeover_potential_score >= 30:
        flow_level = "偏弱"
    else:
        flow_level = "弱"

    if capital_acceptance_score <= 40:
        flow_phase = "萎缩"
    elif capital_acceptance_score >= 65:
        flow_phase = "承接增强"
    else:
        flow_phase = "承接中性"

    source_labels = []
    if eastmoney_rank:
        source_labels.append(f"东财人气排名{eastmoney_rank}")
    if eastmoney_rank_change is not None:
        source_labels.append(f"东财排名变化{eastmoney_rank_change:+g}")
    if keyword_hits:
        source_labels.append(f"热门关键词{_safe_len(keyword_hits)}个")
    if snowball_hits:
        source_labels.append(f"雪球关注命中{_safe_len(snowball_hits)}条")
    for source in calculation_sources:
        label = source.get("label")
        if label and label not in "；".join(source_labels):
            source_labels.append(f"{label}参与计算")
    if not source_labels:
        source_labels.append("外部讨论数据不足，采用板块榜单与本地代理")

    warnings = list(heat.get("warnings") or [])
    if not direct_external_available:
        warnings.append("股吧/雪球直接讨论趋势不足，散户讨论热度使用东财热榜、板块排行和本地代理降级")
    warnings.append("抖音 provider 未接入，本次不使用抖音热度")

    flow_desc = (
        f"散户讨论热度{retail_attention_score}，板块热度{sector_heat_score}，"
        f"热度趋势{heat_trend_score}，题材转化{topic_conversion_score}，"
        f"资金承接{capital_acceptance_score}，退出风险{exit_risk_score}。"
        f"数据口径：{'；'.join(source_labels)}。"
    )

    return {
        "model_version": "retail_attention_v2",
        "flow_score": takeover_potential_score,
        "flow_level": flow_level,
        "takeover_potential_score": takeover_potential_score,
        "retail_attention_score": retail_attention_score,
        "topic_conversion_score": topic_conversion_score,
        "topic_conversion_level": topic_conversion_level,
        "topic_conversion_reasons": topic_conversion_reasons,
        "capital_acceptance_score": capital_acceptance_score,
        "capital_slope_score": capital_acceptance_score,
        "capital_slope_proxy_score": provider_capital_slope_score,
        "capital_proxy_source": "资金流/成交额代理",
        "heat_trend_score": heat_trend_score,
        "heat_acceleration_score": heat_trend_score,
        "heat_acceleration_proxy_score": heat_acceleration_score,
        "heat_trend_profile": heat_trend_profile,
        "attention_direction": attention_direction,
        "eastmoney_hot_rank": eastmoney_rank,
        "eastmoney_rank_change": eastmoney_rank_change,
        "eastmoney_rank_score": eastmoney_rank_score,
        "keyword_hits": keyword_hits,
        "snowball_hits": snowball_hits,
        "eastmoney_guba_status": (
            "proxy_by_eastmoney_hot_rank"
            if eastmoney_rank or keyword_hits
            else "fallback_used"
            if any(source.get("key") in {"sector_board", "eastmoney_board", "local_price_volume", "ths_hot_reason"} for source in calculation_sources)
            else "unavailable"
        ),
        "xueqiu_status": "proxy_available" if snowball_hits else "unavailable",
        "douyin_status": "unavailable",
        "available_sources": calculation_sources,
        "calculation_sources": calculation_sources,
        "source_labels": source_labels,
        "data_warnings": warnings,
        "legacy_flow_score": None,
        "volume_burst": volume_burst,
        "amount_proxy_ratio": round(amount_proxy_ratio, 3) if amount_proxy_ratio else None,
        "amount_proxy_score": amount_proxy_score,
        "volume_proxy_score": volume_proxy_score,
        "price_momentum": price_momentum,
        "volatility": volatility,
        "up_days": up_days,
        "sector_heat_score": sector_heat_score,
        "external_heat_score": external_heat_score,
        "exit_risk_score": exit_risk_score,
        "heat_quality_score": round(
            retail_attention_score * 0.38
            + heat_trend_score * 0.26
            + topic_conversion_score * 0.18
            + capital_acceptance_score * 0.18
        ),
        "sector_name": heat["sector_name"],
        "industry": heat["industry"],
        "flow_phase": flow_phase,
        "flow_desc": flow_desc,
    }


def analyze_timing(df: pd.DataFrame, flow_signals: dict) -> dict:
    """
    视频框架下的题材周期判断：冷启动/启动/扩散/高潮/分歧/退潮。
    """
    retail_attention_score = flow_signals.get("retail_attention_score", flow_signals.get("external_heat_score", 50))
    topic_conversion_score = flow_signals.get("topic_conversion_score", 50)
    capital_acceptance_score = flow_signals.get("capital_acceptance_score", flow_signals.get("capital_slope_score", 50))
    heat_trend_score = flow_signals.get("heat_trend_score", flow_signals.get("heat_acceleration_score", 50))
    sector_heat_score = flow_signals.get("sector_heat_score", 50)
    external_heat_score = flow_signals.get("external_heat_score", 50)
    exit_risk_score = flow_signals.get("exit_risk_score", 35)
    direction = flow_signals.get("attention_direction") or "横盘"

    high_heat = max(retail_attention_score, sector_heat_score, external_heat_score) >= 76
    weak_capital = capital_acceptance_score <= 42
    weak_trend = heat_trend_score <= 40
    low_heat = max(retail_attention_score, sector_heat_score, external_heat_score) < 45

    if high_heat and (weak_capital or exit_risk_score >= 72):
        phase = "分歧"
        phase_score = 38
        phase_desc = "散户讨论热度仍高，但资金承接或热度趋势转弱，按高热分歧处理"
        hold_advice = "先看承接是否恢复，不把高热度直接理解为继续升温"
        exit_signal = "热榜高位但资金承接继续走弱、前排强后排弱或热度加速度下滑时，视为退出风险抬升"
    elif high_heat and heat_trend_score >= 68 and exit_risk_score >= 62:
        phase = "高潮"
        phase_score = 55
        phase_desc = "散户可见度已经很高，新增接盘资金可能接近流量高潮"
        hold_advice = "重点观察热度是否开始失速，以及资金承接是否还能维持"
        exit_signal = "外部讨论极高后排名不再上升、承接转弱或板块内掉队扩散时，视为高潮风险"
    elif heat_trend_score >= 62 and retail_attention_score >= 62 and sector_heat_score >= 55 and capital_acceptance_score >= 50:
        phase = "扩散" if retail_attention_score >= 72 or sector_heat_score >= 70 else "启动"
        phase_score = 76 if phase == "扩散" else 64
        phase_desc = "散户讨论、板块热度与资金承接同向抬升，题材处于扩散/启动链路"
        hold_advice = "观察热榜、股吧/雪球代理和板块内跟随数量能否继续扩散"
        exit_signal = "若热度仍升但资金承接先转弱，阶段会从扩散切到分歧"
    elif low_heat and heat_trend_score >= 52:
        phase = "冷启动"
        phase_score = 48
        phase_desc = "绝对热度不高，但讨论/关注代理开始抬升，仍属早期观察"
        hold_advice = "先确认是否能进入东财/雪球等散户可见入口"
        exit_signal = "若热度趋势回落且板块排行无改善，则冷启动失败"
    elif weak_trend and capital_acceptance_score <= 45:
        phase = "退潮"
        phase_score = 18
        phase_desc = "讨论热度趋势与资金承接同步走弱，题材进入退潮观察区"
        hold_advice = "等待新的讨论增量和承接恢复，不用旧热度解释新方向"
        exit_signal = "热度、承接和板块排行同步回落时，按退潮处理"
    else:
        phase = "启动"
        phase_score = 54
        phase_desc = "散户注意力有一定基础，但趋势、承接或题材转化还未形成强共振"
        hold_advice = "继续观察讨论热度是否进入更高能见度入口"
        exit_signal = "若外部热度抬升失败且资金承接转弱，阶段会降级"

    risk_warning = _risk_label_from_score(exit_risk_score, phase)

    return {
        "phase": phase,
        "phase_score": phase_score,
        "phase_desc": phase_desc,
        "attention_direction": direction if phase not in ["分歧", "退潮"] else phase,
        "risk_warning": risk_warning,
        "retail_attention_score": retail_attention_score,
        "topic_conversion_score": topic_conversion_score,
        "capital_acceptance_score": capital_acceptance_score,
        "heat_trend_score": heat_trend_score,
        "exit_risk_score": exit_risk_score,
        "hold_advice": hold_advice,
        "exit_signal": exit_signal,
    }


def analyze_leadership(df: pd.DataFrame, stock_info: dict) -> dict:
    """
    板块内位置评估：高热板块内的前排/后排，而不是单纯技术强弱。
    """
    close = df["close"]
    volume = df["volume"]
    market_cap = stock_info.get("market_cap")
    stock_name = stock_info.get("name", "")
    heat = _normalize_heat_context(stock_info)
    sector_heat_score = heat["sector_heat_score"]
    external_heat_score = heat["external_heat_score"]
    capital_slope_score = heat["capital_slope_score"]
    heat_acceleration_score = heat["heat_acceleration_score"]
    exit_risk_score = heat["exit_risk_score"]
    stock_hints = heat.get("stock_ranking_hints") or {}

    if len(close) >= 6:
        change_5d = round(((float(close.iloc[-1]) / float(close.iloc[-6])) - 1) * 100, 2)
    else:
        change_5d = 0
    first_mover = change_5d > 5

    hot_keywords = [
        "智能", "科技", "新能源", "芯片", "半导体", "锂电", "光伏",
        "数据", "信息", "网络", "通信", "生物", "医药", "材料",
        "数字", "量子", "算力", "人工", "机器人", "储能", "汽车",
        "电子", "光电", "云", "信创", "卫星", "航天", "军工",
    ]
    name_hits = sum(1 for kw in hot_keywords if kw in stock_name)
    if name_hits >= 2:
        name_recognition = "高"
    elif name_hits >= 1:
        name_recognition = "中等"
    else:
        name_recognition = "低"

    if market_cap is not None:
        cap_yi = market_cap if market_cap is not None else 0
        market_cap_fit = 30 <= cap_yi <= 800
    else:
        cap_yi = 0
        market_cap_fit = False

    if len(close) >= 5 and len(volume) >= 5:
        price_up = float(close.iloc[-1]) > float(close.iloc[-5])
        vol_up = float(volume.iloc[-1]) > float(volume.iloc[-5])
        vol_price_harmony = price_up and vol_up
    else:
        vol_price_harmony = False

    topic_conversion_score, _, _ = _topic_conversion_score(stock_info, heat)
    eastmoney_hot_rank = stock_hints.get("eastmoney_hot_rank")
    retail_rank_score = _rank_heat_score(eastmoney_hot_rank, 100, 45)
    first_mover_score = _clamp_score(45 + change_5d * 2.8)
    name_score = {"高": 86, "中等": 64, "低": 42}.get(name_recognition, 42)
    cap_score = 72 if market_cap_fit else 45
    capital_score = _clamp_score(capital_slope_score)

    score = round(
        sector_heat_score * 0.22
        + external_heat_score * 0.14
        + retail_rank_score * 0.16
        + first_mover_score * 0.16
        + topic_conversion_score * 0.12
        + capital_score * 0.10
        + name_score * 0.06
        + cap_score * 0.04
    )
    if exit_risk_score >= 70 and capital_score <= 45:
        score -= 12
    score = _clamp_score(score)

    board_inner_rank = heat.get("board_inner_rank")
    board_inner_rank_total = heat.get("board_inner_rank_total")
    try:
        board_inner_rank_number = int(board_inner_rank) if board_inner_rank is not None else None
    except (TypeError, ValueError):
        board_inner_rank_number = None

    high_topic = max(sector_heat_score, external_heat_score) >= 65
    ranking_available = board_inner_rank_number is not None
    if not high_topic:
        leader_label = "不在主线"
    elif exit_risk_score >= 72 and capital_score <= 45:
        leader_label = "高热分歧"
    elif ranking_available and board_inner_rank_number == 1 and score >= 70 and capital_score >= 50:
        leader_label = "龙一候选"
    elif ranking_available and board_inner_rank_number <= 3 and score >= 62:
        leader_label = "龙二候选"
    elif ranking_available and board_inner_rank_number <= 8:
        leader_label = "前排跟随"
    elif ranking_available:
        leader_label = "后排补涨"
    elif high_topic and first_mover and retail_rank_score >= 70 and capital_score >= 50:
        leader_label = "前排跟随"
    elif high_topic and change_5d > 0:
        leader_label = "后排补涨"
    else:
        leader_label = "不在主线"

    if leader_label in ["龙一候选", "龙二候选"]:
        leadership_level = "板块核心"
    elif leader_label in ["前排跟随", "后排补涨"]:
        leadership_level = "板块跟随"
    elif leader_label == "高热分歧":
        leadership_level = "风险前排"
    else:
        leadership_level = "不在主线"

    parts = []
    parts.append(f"板块热度{sector_heat_score}、外部讨论{external_heat_score}")
    parts.append(f"近5日涨{change_5d:.1f}%，{'先于题材有表现' if first_mover else '暂未体现明显前排涨幅'}")
    parts.append(f"名字辨识度{name_recognition}")
    parts.append(f"东财人气排名{eastmoney_hot_rank if eastmoney_hot_rank else '暂无'}")
    if ranking_available:
        parts.append(f"板块内排名第{board_inner_rank_number}" + (f"/{board_inner_rank_total}" if board_inner_rank_total else ""))
    else:
        parts.append("板块内排名数据不足，暂不标龙一/龙二")
    parts.append(f"资金承接{capital_score}")
    parts.append(f"退出风险{exit_risk_score}")
    leadership_desc = "，".join(parts)

    return {
        "leadership_score": score,
        "leadership_level": leadership_level,
        "leader_label": leader_label,
        "position_score": score,
        "board_inner_rank": board_inner_rank_number,
        "board_inner_rank_total": board_inner_rank_total,
        "ranking_available": ranking_available,
        "ranking_status": "available" if ranking_available else "板块内排名数据不足",
        "rank_hint": heat["rank_hint"],
        "sector_rank": heat["sector_rank"],
        "sector_rank_total": heat["sector_rank_total"],
        "eastmoney_hot_rank": eastmoney_hot_rank,
        "sector_name": heat["sector_name"],
        "industry": heat["industry"],
        "sector_heat_score": sector_heat_score,
        "external_heat_score": external_heat_score,
        "capital_slope_score": capital_slope_score,
        "capital_acceptance_score": capital_score,
        "heat_acceleration_score": heat_acceleration_score,
        "exit_risk_score": exit_risk_score,
        "first_mover": first_mover,
        "name_recognition": name_recognition,
        "market_cap_fit": market_cap_fit,
        "vol_price_harmony": vol_price_harmony,
        "leadership_desc": leadership_desc,
    }


def analyze_flow(df: pd.DataFrame, stock_info: dict) -> dict:
    """
    retail_attention_v2：按视频框架整合散户注意力、题材转化、资金承接与龙头位置。
    """
    flow_signals = analyze_flow_signals(df, stock_info)
    timing = analyze_timing(df, flow_signals)
    leadership = analyze_leadership(df, stock_info)
    heat = _normalize_heat_context(stock_info)
    sector = {
        "name": heat["sector_name"],
        "type": heat["sector_type"],
        "industry": heat["industry"],
        "heat_score": heat["sector_heat_score"],
        "heat": heat["sector_heat_score"],
        "window": heat["sector_heat_window"],
        "date": heat["sector_heat_date"],
        "source": heat["sector_source"],
        "confidence": heat["sector_confidence"],
        "external_heat_score": heat["external_heat_score"],
        "capital_slope_score": heat["capital_slope_score"],
        "heat_acceleration_score": heat["heat_acceleration_score"],
        "exit_risk_score": heat["exit_risk_score"],
        "rank": heat["sector_rank"],
        "rank_total": heat["sector_rank_total"],
        "rank_hint": heat["rank_hint"],
    }

    flow_total_score = _clamp_score(flow_signals["takeover_potential_score"])
    phase = timing["phase"]
    exit_risk_score = flow_signals["exit_risk_score"]
    if phase == "退潮":
        flow_recommendation = "退潮回避"
    elif phase == "分歧":
        flow_recommendation = "高热分歧"
    elif phase == "高潮" or exit_risk_score >= 78:
        flow_recommendation = "高潮风险"
    elif phase == "扩散":
        flow_recommendation = "扩散中"
    else:
        flow_recommendation = "低位升温"

    retail_attention = {
        "score": flow_signals["retail_attention_score"],
        "direction": timing["attention_direction"],
        "eastmoney_hot_rank": flow_signals.get("eastmoney_hot_rank"),
        "eastmoney_rank_change": flow_signals.get("eastmoney_rank_change"),
        "keyword_hits": flow_signals.get("keyword_hits", []),
        "snowball_hits": flow_signals.get("snowball_hits", []),
        "guba_status": flow_signals.get("eastmoney_guba_status"),
        "xueqiu_status": flow_signals.get("xueqiu_status"),
        "douyin_status": flow_signals.get("douyin_status"),
        "available_sources": flow_signals.get("available_sources", []),
        "calculation_sources": flow_signals.get("calculation_sources", []),
        "source_labels": flow_signals.get("source_labels", []),
        "links": _discussion_links(stock_info),
    }
    topic_conversion = {
        "score": flow_signals["topic_conversion_score"],
        "level": flow_signals.get("topic_conversion_level"),
        "reasons": flow_signals.get("topic_conversion_reasons", []),
    }
    capital_acceptance = {
        "score": flow_signals["capital_acceptance_score"],
        "slope_score": flow_signals["capital_slope_proxy_score"],
        "source": flow_signals["capital_proxy_source"],
        "amount_proxy_ratio": flow_signals.get("amount_proxy_ratio"),
        "volume_burst": flow_signals.get("volume_burst"),
        "note": "资金承接用于判断新增接盘资金是否继续进入，不等同于散户讨论流量",
    }
    heat_trend = {
        "score": flow_signals["heat_trend_score"],
        "direction": timing["attention_direction"],
        "proxy_score": flow_signals.get("heat_acceleration_proxy_score"),
        "confidence": heat["sector_confidence"],
        "source": "东财排名变化/关键词/板块排行/成交代理",
        "start_score": flow_signals.get("heat_trend_profile", {}).get("start_score"),
        "current_score": flow_signals.get("heat_trend_profile", {}).get("current_score"),
        "change_score": flow_signals.get("heat_trend_profile", {}).get("change_score"),
        "points": flow_signals.get("heat_trend_profile", {}).get("points", []),
        "window": flow_signals.get("heat_trend_profile", {}).get("window", "5d"),
        "basis": flow_signals.get("heat_trend_profile", {}).get("basis", "代理热度"),
        "summary": flow_signals.get("heat_trend_profile", {}).get("summary"),
        "is_proxy": flow_signals.get("heat_trend_profile", {}).get("is_proxy", True),
    }
    leader_position = {
        "score": leadership["leadership_score"],
        "leader_label": leadership["leader_label"],
        "level": leadership["leadership_level"],
        "ranking_status": leadership["ranking_status"],
        "board_inner_rank": leadership.get("board_inner_rank"),
        "board_inner_rank_total": leadership.get("board_inner_rank_total"),
        "rank_hint": leadership["rank_hint"],
        "desc": leadership["leadership_desc"],
    }
    cycle_stage = {
        "phase": phase,
        "score": timing["phase_score"],
        "desc": timing["phase_desc"],
        "attention_direction": timing["attention_direction"],
    }
    risk_warning = {
        "label": flow_recommendation,
        "score": exit_risk_score,
        "desc": timing["exit_signal"],
    }
    data_status = {
        "status": (heat.get("data_status") or {}).get("status", "partial"),
        "source": heat["sector_source"],
        "confidence": heat["sector_confidence"],
        "warnings": flow_signals.get("data_warnings", []),
        "douyin_status": "unavailable",
        "guba_status": retail_attention["guba_status"],
        "xueqiu_status": retail_attention["xueqiu_status"],
        "available_sources": retail_attention["available_sources"],
        "calculation_sources": retail_attention["calculation_sources"],
    }

    flow_analysis_text = (
        f"散户讨论热度：{retail_attention['score']}（{retail_attention['direction']}）\n"
        f"题材转化率：{topic_conversion['score']}（{topic_conversion['level']}）\n"
        f"资金承接：{capital_acceptance['score']}（{capital_acceptance['source']}）\n"
        f"周期阶段：{phase}；{timing['phase_desc']}\n"
        f"龙头位置：{leader_position['leader_label']}；{leader_position['desc']}\n"
        f"风险提示：{flow_recommendation}；{timing['exit_signal']}"
    )

    return {
        "flow_model": {
            "version": "retail_attention_v2",
            "formula": "接盘资金潜力 = 散户讨论流量 × 题材转化率 × 资金承接能力",
            "note": "量价指标仅作为资金承接/退潮风险辅助证据",
        },
        "retail_attention": retail_attention,
        "topic_conversion": topic_conversion,
        "capital_acceptance": capital_acceptance,
        "heat_trend": heat_trend,
        "leader_position": leader_position,
        "cycle_stage": cycle_stage,
        "risk_warning": risk_warning,
        "data_status": data_status,
        "flow_signals": flow_signals,
        "timing": timing,
        "leadership": leadership,
        "sector": sector,
        "sector_name": sector["name"],
        "sector_type": sector["type"],
        "sector_heat": sector["heat_score"],
        "sector_heat_score": sector["heat_score"],
        "sector_heat_window": sector["window"],
        "sector_heat_date": sector["date"],
        "sector_source": sector["source"],
        "sector_confidence": sector["confidence"],
        "external_heat": sector["external_heat_score"],
        "external_heat_score": sector["external_heat_score"],
        "capital_slope": sector["capital_slope_score"],
        "capital_slope_score": sector["capital_slope_score"],
        "heat_acceleration": sector["heat_acceleration_score"],
        "heat_acceleration_score": sector["heat_acceleration_score"],
        "exit_risk": sector["exit_risk_score"],
        "exit_risk_score": sector["exit_risk_score"],
        "industry": heat["industry"],
        "flow_total_score": flow_total_score,
        "flow_recommendation": flow_recommendation,
        "flow_analysis_text": flow_analysis_text,
    }


# ─── 策略卖点分析 ─────────────────────────────────────────

STRATEGY_EXIT_DEFINITIONS = {
    "position_risk": "持仓风控",
    "first_board_relay": "首板接力",
    "leader_chase": "龙头追击",
    "trend_break": "趋势破位",
    "rotation_quality": "轮动/趋势质量",
    "etf_rotation": "ETF动量轮动",
    "qlib_model": "Qlib模型研究参考",
}

ALLOWED_EXIT_ACTION_TYPES = {"stop_loss", "take_profit", "sell", "reduce", "hold", "watch"}
EXIT_DECISION_LABELS = {
    "stop_loss": "止损",
    "take_profit": "止盈",
    "sell": "卖出",
    "reduce": "减仓",
    "hold": "继续持有",
    "watch": "观察",
}
SELL_RECOMMENDATIONS = {"强烈卖出", "卖出"}


def _to_float(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_trade_date(value) -> Optional[str]:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _get_trade_dates(df: pd.DataFrame) -> list:
    if "date" in df.columns:
        values = df["date"]
    elif isinstance(df.index, pd.DatetimeIndex):
        values = df.index
    else:
        raise ValueError("日K数据缺少date列，无法校验买入日期。")

    dates = []
    for value in values:
        date_text = _format_trade_date(value)
        if date_text:
            dates.append(date_text)
    return dates


def _validate_buy_date(df: pd.DataFrame, buy_date: Optional[str]) -> tuple:
    trade_dates = _get_trade_dates(df)
    latest_trade_date = trade_dates[-1] if trade_dates else None
    if not buy_date:
        return None, latest_trade_date, trade_dates

    parsed = pd.to_datetime(buy_date, format="%Y-%m-%d", errors="coerce")
    if pd.isna(parsed) or parsed.strftime("%Y-%m-%d") != buy_date:
        raise ValueError("buy_date must use YYYY-MM-DD format")

    normalized = parsed.strftime("%Y-%m-%d")
    if normalized not in set(trade_dates):
        raise ValueError(f"buy_date {normalized} is not a trading date in current data")
    return normalized, latest_trade_date, trade_dates


def _make_exit_signal(source: str, priority: str, title: str, detail: str,
                      data_status: str = "available", executable: bool = True,
                      strategy_key: str = "rotation_quality",
                      action_type: str = "watch") -> dict:
    if strategy_key not in STRATEGY_EXIT_DEFINITIONS:
        strategy_key = "rotation_quality"
    if action_type not in ALLOWED_EXIT_ACTION_TYPES:
        action_type = "watch"
    return {
        "source": source,
        "priority": priority,
        "title": title,
        "detail": detail,
        "data_status": data_status,
        "executable": executable,
        "strategy_key": strategy_key,
        "action_type": action_type,
    }


def _make_risk_control(key: str, title: str, trigger_condition: str,
                       current_status: str, is_triggered: bool = False,
                       data_available: bool = True, offset_by_strength: bool = False,
                       action_type: str = "watch") -> dict:
    if offset_by_strength:
        status_label = "强势抵消"
    elif not data_available:
        status_label = "数据不足"
    else:
        status_label = "已触发" if is_triggered else "未触发"
    return {
        "key": key,
        "title": title,
        "trigger_condition": trigger_condition,
        "current_status": current_status,
        "is_triggered": bool(is_triggered),
        "status_label": status_label,
        "action_type": action_type if action_type in ALLOWED_EXIT_ACTION_TYPES else "watch",
    }


def _round_exit_price(value) -> Optional[float]:
    number = _to_float(value)
    return round(number, 3) if number is not None else None


def _find_risk_control(risk_controls: dict, strategy_key: str, control_key: str) -> Optional[dict]:
    for control in risk_controls.get(strategy_key, []):
        if control.get("key") == control_key:
            return control
    return None


def _make_exit_price(primary_price=None, price_label: str = "暂无可计算卖点价",
                     trigger_condition: str = "", current_status: str = "",
                     is_triggered: bool = False, detail: str = "",
                     source: str = "strategy_exit") -> dict:
    return {
        "primary_price": _round_exit_price(primary_price),
        "price_label": price_label,
        "trigger_condition": trigger_condition,
        "current_status": current_status,
        "is_triggered": bool(is_triggered),
        "detail": detail,
        "source": source,
    }


def _exit_price_from_control(control: Optional[dict], primary_price,
                             price_label: str, detail: str,
                             source: str = "strategy_exit") -> dict:
    if not control:
        return _make_exit_price(
            primary_price=None,
            price_label=price_label,
            current_status="数据不足，暂无可计算卖点价。",
            detail=detail,
            source=source,
        )
    return _make_exit_price(
        primary_price=primary_price,
        price_label=price_label,
        trigger_condition=control.get("trigger_condition", ""),
        current_status=control.get("current_status", ""),
        is_triggered=control.get("is_triggered", False),
        detail=detail or control.get("current_status", ""),
        source=source,
    )


def _build_dropdown_exit_prices(current_price: float, cost_price: Optional[float],
                                pre_close: Optional[float], open_price: Optional[float],
                                ma5: Optional[float], intraday_high: Optional[float],
                                risk_controls: dict) -> dict:
    has_cost = cost_price is not None and cost_price > 0
    hard_stop = _find_risk_control(risk_controls, "position_risk", "hard_stop_loss")
    day_drop = _find_risk_control(risk_controls, "first_board_relay", "day_drop_2pct")
    open_break = _find_risk_control(risk_controls, "first_board_relay", "open_break_4pct")
    low_open = _find_risk_control(risk_controls, "leader_chase", "low_open_weak")
    pullback = _find_risk_control(risk_controls, "leader_chase", "intraday_pullback_5pct")
    ma5_break = _find_risk_control(risk_controls, "trend_break", "ma5_break")
    flow_divergence = _find_risk_control(risk_controls, "rotation_quality", "flow_divergence")

    first_board_price = (
        _exit_price_from_control(
            day_drop,
            pre_close * 0.98 if pre_close else None,
            "当日跌幅卖点",
            "首板接力优先看当日跌幅是否破坏接力承接。",
        )
        if pre_close
        else _exit_price_from_control(
            open_break,
            open_price * 0.96 if open_price else None,
            "跌破开盘价卖点",
            "昨收价不可用时，用跌破开盘价规则作为接力卖点参考。",
        )
    )

    leader_price = (
        _exit_price_from_control(
            low_open,
            open_price * 1.01 if open_price else None,
            "低开承接风控价",
            "龙头追击优先看低开后能否快速修复开盘弱势。",
        )
        if open_price and pre_close
        else _exit_price_from_control(
            pullback,
            intraday_high * 0.95 if intraday_high else None,
            "冲高回落风控价",
            "低开承接数据不足时，用盘中高点回撤规则作为龙头追击参考。",
        )
    )

    position_price = _exit_price_from_control(
        hard_stop,
        float(cost_price) * 0.93 if has_cost else None,
        "硬止损线",
        "通用持仓风控，以成本价回撤7%作为硬止损参考。",
    )

    exit_prices = {
        "position_risk": position_price,
        "first_board_relay": first_board_price,
        "leader_chase": leader_price,
        "trend_break": _exit_price_from_control(
            ma5_break,
            ma5 * 0.97 if ma5 else None,
            "MA5防线卖点",
            "短线趋势防线失守时，用 MA5 下方3%作为趋势破位参考。",
        ),
        "rotation_quality": _exit_price_from_control(
            flow_divergence,
            current_price if flow_divergence and flow_divergence.get("is_triggered") else None,
            "轮动质量风控价",
            "流量分歧或退潮是质量信号，价格仅作当前观察参考。",
        ),
        "etf_rotation": _make_exit_price(
            primary_price=None,
            price_label="不适用于个股卖点",
            trigger_condition="ETF动量轮动不套用个股持仓卖点",
            current_status="该策略不适用于个股卖点。",
            detail="ETF动量轮动不适用于个股卖点，仅用于ETF推荐观察，不对单只个股生成卖点价。",
            source="not_applicable",
        ),
    }

    qlib_price = _make_exit_price(
        primary_price=float(cost_price) * 0.93 if has_cost else None,
        price_label="通用持仓风控价",
        trigger_condition=hard_stop.get("trigger_condition", "现价 <= 成本价 * 0.93 时触发止损卖出") if hard_stop else "现价 <= 成本价 * 0.93 时触发止损卖出",
        current_status=hard_stop.get("current_status", "") if hard_stop else "未填写持仓成本，无法给出通用风控价。",
        is_triggered=hard_stop.get("is_triggered", False) if hard_stop else False,
        detail="Qlib模型组仅作量化研究与观察参考；这里复用通用持仓硬止损价，不代表 Qlib 自动卖出信号。",
        source="mapped_position_risk",
    )
    exit_prices["qlib_model"] = qlib_price
    return exit_prices


def _minute_high(minute_df: Optional[pd.DataFrame]):
    if minute_df is None or minute_df.empty:
        return None
    for col in ["high", "close", "price"]:
        if col in minute_df.columns:
            vals = pd.to_numeric(minute_df[col], errors="coerce").dropna()
            if not vals.empty:
                return float(vals.max())
    return None


def _risk_score_from_level(risk_level: str, signal_count: int = 0) -> int:
    if risk_level == "high":
        return 88 if signal_count >= 2 else 78
    if risk_level == "medium":
        return 58 if signal_count >= 2 else 42
    return 18 if signal_count == 0 else 25


def _decision_from_signals(signals: list) -> str:
    active_signals = [signal for signal in signals if signal.get("executable", True)]
    for action_type in ["stop_loss", "take_profit", "sell", "reduce", "hold"]:
        if any(signal.get("action_type") == action_type for signal in active_signals):
            return action_type
    if signals:
        return "watch"
    return "watch"


def _build_strategy_result(key: str, signals: list,
                           risk_controls: Optional[list] = None,
                           summary: Optional[str] = None,
                           decision: Optional[str] = None,
                           risk_level: Optional[str] = None,
                           risk_score: Optional[int] = None,
                           exit_price: Optional[dict] = None) -> dict:
    active_risk_signals = [
        signal for signal in signals
        if signal.get("executable", True) and signal.get("priority") in {"high", "medium"}
    ]
    high_count = sum(1 for signal in active_risk_signals if signal.get("priority") == "high")
    medium_count = sum(1 for signal in active_risk_signals if signal.get("priority") == "medium")

    if risk_level is None:
        if high_count:
            risk_level = "high"
        elif medium_count:
            risk_level = "medium"
        else:
            risk_level = "low"
    if risk_score is None:
        risk_score = _risk_score_from_level(risk_level, len(active_risk_signals))
    if decision is None:
        decision = _decision_from_signals(signals)
    if summary is None:
        if signals:
            title_text = "、".join(signal["title"] for signal in signals[:2])
            summary = f"触发{STRATEGY_EXIT_DEFINITIONS[key]}信号：{title_text}。"
        elif key == "etf_rotation":
            summary = "ETF动量轮动不适用于个股卖点。"
        elif key == "qlib_model":
            summary = "Qlib模型组仅作量化研究参考，卖点价复用通用持仓风控。"
        else:
            summary = f"未触发{STRATEGY_EXIT_DEFINITIONS[key]}卖点。"

    return {
        "key": key,
        "name": STRATEGY_EXIT_DEFINITIONS[key],
        "risk_level": risk_level,
        "risk_score": risk_score,
        "decision": decision,
        "decision_label": EXIT_DECISION_LABELS.get(decision, "观察"),
        "summary": summary,
        "signals": signals,
        "risk_controls": risk_controls or [],
        "exit_price": exit_price or _make_exit_price(),
    }


def _build_strategy_results(signals: list, cost_price: Optional[float],
                            risk_controls: Optional[dict] = None,
                            exit_prices: Optional[dict] = None) -> dict:
    grouped = {key: [] for key in STRATEGY_EXIT_DEFINITIONS}
    for signal in signals:
        grouped.setdefault(signal["strategy_key"], []).append(signal)
    risk_controls = risk_controls or {}
    exit_prices = exit_prices or {}

    strategies = {}
    for key in STRATEGY_EXIT_DEFINITIONS:
        summary = None
        decision = None
        risk_level = None
        risk_score = None

        if key == "position_risk" and not (cost_price is not None and cost_price > 0):
            summary = "未填写持仓成本，持仓亏损、收益止盈和成本保护仅作为缺省观察。"
            decision = "watch"
            risk_level = "low"
            risk_score = 0
        elif key in {"etf_rotation", "qlib_model"}:
            decision = "watch"
            risk_level = "low"
            risk_score = 0

        strategies[key] = _build_strategy_result(
            key,
            grouped.get(key, []),
            risk_controls=risk_controls.get(key, []),
            summary=summary,
            decision=decision,
            risk_level=risk_level,
            risk_score=risk_score,
            exit_price=exit_prices.get(key),
        )
    return strategies


def _build_trade_eligibility(buy_date: Optional[str], latest_trade_date: Optional[str]) -> dict:
    can_sell_today = True
    guidance_scope = "today"
    if buy_date and latest_trade_date and buy_date == latest_trade_date:
        can_sell_today = False
        guidance_scope = "next_trading_day"

    if not buy_date:
        summary = "未填写买入日期，今日是否可卖无法确认；策略卖点按风险观察口径提示。"
    elif can_sell_today:
        summary = f"买入日 {buy_date} 早于最新交易日 {latest_trade_date}，今日可按策略纪律执行。"
    else:
        summary = "买入日为最新交易日，今日不能执行卖出；卖点信号保留为下一交易日风控指导。"

    return {
        "buy_date": buy_date,
        "latest_trade_date": latest_trade_date,
        "can_sell_today": can_sell_today,
        "guidance_scope": guidance_scope,
        "summary": summary,
    }


def _is_same_day_buy_blocked(strategy_exit: dict) -> bool:
    eligibility = strategy_exit.get("trade_eligibility", {})
    return eligibility.get("can_sell_today") is False


def _build_etf_strategy_exit(df: pd.DataFrame, buy_date: Optional[str] = None) -> dict:
    normalized_buy_date, latest_trade_date, trade_dates = _validate_buy_date(df, buy_date)
    trade_eligibility = _build_trade_eligibility(normalized_buy_date, latest_trade_date)
    return {
        "risk_level": "low",
        "risk_score": 0,
        "recommendation_adjustment": "none",
        "summary": "ETF 不参与个股卖点策略，保留技术面与流量分析作为参考。",
        "signals": [],
        "missing_context": [] if normalized_buy_date else ["未填写买入日期，今日是否可卖无法确认。"],
        "position": {
            "cost_price": None,
            "profit_pct": None,
        },
        "buy_date": normalized_buy_date,
        "latest_trade_date": latest_trade_date,
        "trade_date_count": len(trade_dates),
        "trade_eligibility": trade_eligibility,
        "strategies": {},
        "data_status": {
            "minute": "not_applicable",
            "minute_error": None,
        },
    }


def _align_action_with_recommendation(technical_action: dict, recommendation: str,
                                      base_recommendation: str, strategy_recommendation: str,
                                      strategy_exit: dict, strategy_adjusted: bool,
                                      t_plus_one_adjusted: bool) -> dict:
    action = dict(technical_action)

    if recommendation == "强烈买入":
        operation = "买入"
        position = "重仓"
    elif recommendation == "买入":
        operation = "买入"
        position = "半仓"
    elif recommendation in SELL_RECOMMENDATIONS:
        operation = "卖出"
        position = "清仓"
    else:
        operation = "观望"
        position = "轻仓"

    if t_plus_one_adjusted:
        operation = "观望"
        position = "原仓观察"

    basis_parts = [
        f"技术面原始操作为{technical_action['operation']}（{technical_action['position']}）",
        f"技术面与流量综合建议为{base_recommendation}",
    ]
    if strategy_adjusted:
        basis_parts.append(
            f"策略卖点风险{strategy_exit['risk_level']}，先调整为{strategy_recommendation}"
        )
    else:
        basis_parts.append("策略卖点未降低综合建议")
    if t_plus_one_adjusted:
        basis_parts.append("买入日为最新交易日，今日不能执行卖出，卖点作为下一交易日风控指导")
    basis_parts.append(f"最终按{recommendation}给出{operation}/{position}")
    action_risk_level = {
        "high": "高",
        "medium": "中等",
        "low": "低",
    }.get(strategy_exit.get("risk_level"), technical_action["risk_level"])

    action.update({
        "operation": operation,
        "position": position,
        "risk_level": action_risk_level,
        "basis": "；".join(basis_parts),
        "reason": "；".join(basis_parts),
        "technical_operation": technical_action["operation"],
        "technical_position": technical_action["position"],
        "strategy_adjusted": strategy_adjusted,
        "t_plus_one_blocked": t_plus_one_adjusted,
    })
    return action


def analyze_strategy_exit(
    df: pd.DataFrame,
    stock_info: dict,
    minute_df: Optional[pd.DataFrame] = None,
    cost_price: Optional[float] = None,
    flow_result: Optional[dict] = None,
    minute_error: Optional[str] = None,
    buy_date: Optional[str] = None,
) -> dict:
    """
    策略卖点提示：融合首板接力、龙头追击和趋势轮动退出思想。
    cost_price 为可选持仓成本；传入后启用更客观的止损/利润保护规则。
    """
    close = df["close"]
    current_price = _to_float(stock_info.get("price")) or float(close.iloc[-1])
    open_price = _to_float(stock_info.get("open")) or (float(df["open"].iloc[-1]) if "open" in df.columns else None)
    pre_close = _to_float(stock_info.get("pre_close"))
    if pre_close is None and len(close) >= 2:
        pre_close = float(close.iloc[-2])
    high_limit = _to_float(stock_info.get("high_limit"))
    low_limit = _to_float(stock_info.get("low_limit"))
    ma5 = calc_ma(close, 5)
    flow_result = flow_result or {}
    normalized_buy_date, latest_trade_date, trade_dates = _validate_buy_date(df, buy_date)

    signals = []
    risk_controls = {key: [] for key in STRATEGY_EXIT_DEFINITIONS}
    missing_context = []
    has_cost = cost_price is not None and cost_price > 0
    position = {
        "cost_price": round(float(cost_price), 3) if has_cost else None,
        "profit_pct": None,
    }
    if has_cost:
        profit_pct = round((current_price / float(cost_price) - 1) * 100, 2)
        position["profit_pct"] = profit_pct
    else:
        missing_context.append("未填写持仓成本，无法判断持仓亏损、收益止盈和成本保护。")
    if not normalized_buy_date:
        missing_context.append("未填写买入日期，今日是否可卖无法确认。")

    near_limit_up = bool(high_limit and high_limit > 0 and current_price >= high_limit * 0.99)
    if near_limit_up:
        signals.append(_make_exit_signal(
            "首板接力/龙头追击",
            "support",
            "接近涨停强势持有",
            f"现价 {current_price:.2f} 已接近涨停价 {high_limit:.2f}，短线强势股优先观察承接，不因普通超买信号直接卖出。",
            strategy_key="first_board_relay",
            action_type="hold",
        ))

    if has_cost:
        hard_stop_triggered = current_price <= float(cost_price) * 0.93
        soft_stop_triggered = (not hard_stop_triggered) and current_price <= float(cost_price) * 0.95
        risk_controls["position_risk"].extend([
            _make_risk_control(
                "hard_stop_loss",
                "硬止损线",
                "现价 <= 成本价 * 0.93 时触发止损卖出",
                f"当前现价 {current_price:.2f}，成本 {cost_price:.2f}，收益 {position['profit_pct']:.2f}%，"
                f"{'已触发硬止损' if hard_stop_triggered else '未触发硬止损'}。",
                hard_stop_triggered,
                action_type="stop_loss",
            ),
            _make_risk_control(
                "soft_stop_reduce",
                "亏损减仓线",
                "现价 <= 成本价 * 0.95 且尚未触发硬止损时，触发减仓风控",
                f"当前现价 {current_price:.2f}，成本 {cost_price:.2f}，收益 {position['profit_pct']:.2f}%，"
                f"{'已触发亏损减仓' if soft_stop_triggered else '未触发亏损减仓'}。",
                soft_stop_triggered,
                action_type="reduce",
            ),
        ])
    else:
        risk_controls["position_risk"].extend([
            _make_risk_control(
                "hard_stop_loss",
                "硬止损线",
                "现价 <= 成本价 * 0.93 时触发止损卖出",
                "未填写持仓成本，无法判断硬止损是否触发。",
                data_available=False,
                action_type="stop_loss",
            ),
            _make_risk_control(
                "soft_stop_reduce",
                "亏损减仓线",
                "现价 <= 成本价 * 0.95 且尚未触发硬止损时，触发减仓风控",
                "未填写持仓成本，无法判断亏损减仓是否触发。",
                data_available=False,
                action_type="reduce",
            ),
        ])

    if has_cost:
        if current_price <= float(cost_price) * 0.93:
            signals.append(_make_exit_signal(
                "持仓风控",
                "high",
                "持仓亏损超过7%",
                f"现价 {current_price:.2f} 相对成本 {cost_price:.2f} 亏损 {position['profit_pct']:.2f}%，触发硬止损线。",
                strategy_key="position_risk",
                action_type="stop_loss",
            ))
        elif current_price <= float(cost_price) * 0.95:
            signals.append(_make_exit_signal(
                "持仓风控",
                "medium",
                "持仓亏损接近5%",
                f"现价 {current_price:.2f} 相对成本 {cost_price:.2f} 亏损 {position['profit_pct']:.2f}%，需控制仓位风险。",
                strategy_key="position_risk",
                action_type="reduce",
            ))

    if pre_close and pre_close > 0:
        day_change = (current_price / pre_close - 1) * 100
        day_drop_triggered = day_change < -2
        risk_controls["first_board_relay"].append(_make_risk_control(
            "day_drop_2pct",
            "当日跌幅卖点",
            "当日跌幅 < -2% 时，接力承接转弱，触发卖出风控",
            f"当前当日涨跌幅 {day_change:.2f}%，{'已触发' if day_drop_triggered else '未触发'}当日跌幅卖点。",
            day_drop_triggered,
            action_type="sell",
        ))
        if day_change < -2:
            signals.append(_make_exit_signal(
                "首板接力",
                "high",
                "当日跌幅超过2%",
                f"现价相对昨收 {pre_close:.2f} 下跌 {day_change:.2f}%，接力资金承接转弱。",
                strategy_key="first_board_relay",
                action_type="sell",
            ))
    else:
        risk_controls["first_board_relay"].append(_make_risk_control(
            "day_drop_2pct",
            "当日跌幅卖点",
            "当日跌幅 < -2% 时，接力承接转弱，触发卖出风控",
            "昨收价不可用，无法判断当日跌幅卖点是否触发。",
            data_available=False,
            action_type="sell",
        ))
        missing_context.append("昨收价不可用，无法判断当日跌幅卖点。")

    if open_price and open_price > 0:
        open_break_triggered = current_price < open_price * 0.96
        low_open_weak_triggered = bool(pre_close and open_price < pre_close and current_price <= open_price * 1.01)
        risk_controls["first_board_relay"].append(_make_risk_control(
            "open_break_4pct",
            "跌破开盘价",
            "现价 < 开盘价 * 0.96 时，日内走势转弱，触发卖出风控",
            f"当前现价 {current_price:.2f}，开盘价 {open_price:.2f}，"
            f"{'已触发跌破开盘价' if open_break_triggered else '未触发跌破开盘价'}。",
            open_break_triggered,
            action_type="sell",
        ))
        risk_controls["leader_chase"].append(_make_risk_control(
            "low_open_weak",
            "低开承接",
            "开盘价 < 昨收，且现价 <= 开盘价 * 1.01 时，触发减仓观察",
            (
                f"当前开盘价 {open_price:.2f}，昨收 {pre_close:.2f}，现价 {current_price:.2f}，"
                f"{'已触发低开承接偏弱' if low_open_weak_triggered else '未触发低开承接偏弱'}。"
                if pre_close else
                "昨收价不可用，无法判断低开承接是否触发。"
            ),
            low_open_weak_triggered,
            data_available=bool(pre_close),
            action_type="reduce",
        ))
        if current_price < open_price * 0.96:
            signals.append(_make_exit_signal(
                "首板接力",
                "high",
                "跌破开盘价4%",
                f"现价 {current_price:.2f} 低于开盘价 {open_price:.2f} 的96%，日内走势明显转弱。",
                strategy_key="first_board_relay",
                action_type="sell",
            ))
        if pre_close and open_price < pre_close and current_price <= open_price * 1.01:
            signals.append(_make_exit_signal(
                "龙头追击",
                "medium",
                "低开承接偏弱",
                f"开盘价 {open_price:.2f} 低于昨收 {pre_close:.2f}，且当前未明显收复开盘弱势。",
                strategy_key="leader_chase",
                action_type="reduce",
            ))
    else:
        risk_controls["first_board_relay"].append(_make_risk_control(
            "open_break_4pct",
            "跌破开盘价",
            "现价 < 开盘价 * 0.96 时，日内走势转弱，触发卖出风控",
            "开盘价不可用，无法判断跌破开盘价卖点是否触发。",
            data_available=False,
            action_type="sell",
        ))
        risk_controls["leader_chase"].append(_make_risk_control(
            "low_open_weak",
            "低开承接",
            "开盘价 < 昨收，且现价 <= 开盘价 * 1.01 时，触发减仓观察",
            "开盘价不可用，无法判断低开承接是否触发。",
            data_available=False,
            action_type="reduce",
        ))
        missing_context.append("开盘价不可用，无法判断跌破开盘价与低开承接规则。")

    trend_break_triggered = bool(ma5 and current_price < ma5 * 0.97)
    risk_controls["trend_break"].append(_make_risk_control(
        "ma5_break",
        "MA5防线",
        "现价 < MA5 * 0.97 时，短线趋势防线失守，触发卖出风控",
        (
            f"当前现价 {current_price:.2f}，MA5 {ma5:.2f}，"
            f"{'已触发跌破MA5防线' if trend_break_triggered else '未触发跌破MA5防线'}。"
            if ma5 else
            "MA5数据不足，无法判断趋势破位是否触发。"
        ),
        trend_break_triggered,
        data_available=bool(ma5),
        action_type="sell",
    ))
    if ma5 and current_price < ma5 * 0.97:
        signals.append(_make_exit_signal(
            "趋势破位",
            "high",
            "跌破MA5防线",
            f"现价 {current_price:.2f} 低于 MA5 {ma5:.2f} 的97%，短线趋势防线失守。",
            strategy_key="trend_break",
            action_type="sell",
        ))

    intraday_high = _minute_high(minute_df)
    minute_status = "available" if intraday_high else "missing"
    if intraday_high and intraday_high > 0:
        pullback = (intraday_high - current_price) / intraday_high * 100
        pullback_triggered = pullback >= 5
        risk_controls["leader_chase"].append(_make_risk_control(
            "intraday_pullback_5pct",
            "冲高回落",
            "盘中高点回撤 >= 5% 时，触发龙头追击卖出/减仓风控",
            f"当前盘中高点 {intraday_high:.2f}，现价 {current_price:.2f}，回撤 {pullback:.2f}%，"
            f"{'接近涨停强势抵消' if pullback_triggered and near_limit_up else ('已触发冲高回落' if pullback_triggered else '未触发冲高回落')}。",
            pullback_triggered,
            offset_by_strength=pullback_triggered and near_limit_up,
            action_type="sell" if not near_limit_up else "reduce",
        ))
        if pullback >= 5:
            signals.append(_make_exit_signal(
                "龙头追击",
                "high" if not near_limit_up else "medium",
                "冲高回落风险",
                f"分钟最高价 {intraday_high:.2f} 回落到 {current_price:.2f}，回撤 {pullback:.2f}%。",
                data_status=minute_status,
                strategy_key="leader_chase",
                action_type="sell" if not near_limit_up else "reduce",
            ))
        if has_cost and intraday_high >= float(cost_price) * 1.15:
            max_profit = (intraday_high / float(cost_price) - 1) * 100
            current_profit = (current_price / float(cost_price) - 1) * 100
            giveback = max_profit - current_profit
            giveback_triggered = giveback >= 10 or (max_profit >= 30 and current_profit < 10)
            risk_controls["position_risk"].append(_make_risk_control(
                "profit_giveback",
                "利润回吐保护",
                "盘中最高收益 >= 15%，且收益回吐 >= 10个百分点，或最高收益 >= 30% 后当前收益 < 10% 时触发止盈",
                f"当前盘中最高收益约 {max_profit:.2f}%，当前收益约 {current_profit:.2f}%，回吐 {giveback:.2f} 个百分点，"
                f"{'已触发利润回吐保护' if giveback_triggered else '未触发利润回吐保护'}。",
                giveback_triggered,
                action_type="take_profit",
            ))
            if giveback >= 10 or (max_profit >= 30 and current_profit < 10):
                signals.append(_make_exit_signal(
                    "持仓风控",
                    "high",
                    "利润回吐保护",
                    f"盘中最高收益约 {max_profit:.2f}%，当前收益约 {current_profit:.2f}%，回吐 {giveback:.2f} 个百分点。",
                    data_status=minute_status,
                    strategy_key="position_risk",
                    action_type="take_profit",
                ))
        elif has_cost:
            risk_controls["position_risk"].append(_make_risk_control(
                "profit_giveback",
                "利润回吐保护",
                "盘中最高收益 >= 15%，且收益回吐 >= 10个百分点，或最高收益 >= 30% 后当前收益 < 10% 时触发止盈",
                f"当前盘中最高价 {intraday_high:.2f} 尚未达到成本 {cost_price:.2f} 的115%，未触发利润回吐保护。",
                False,
                action_type="take_profit",
            ))
        else:
            risk_controls["position_risk"].append(_make_risk_control(
                "profit_giveback",
                "利润回吐保护",
                "盘中最高收益 >= 15%，且收益回吐 >= 10个百分点，或最高收益 >= 30% 后当前收益 < 10% 时触发止盈",
                "未填写持仓成本，无法判断利润回吐保护是否触发。",
                data_available=False,
                action_type="take_profit",
            ))
    else:
        risk_controls["leader_chase"].append(_make_risk_control(
            "intraday_pullback_5pct",
            "冲高回落",
            "盘中高点回撤 >= 5% 时，触发龙头追击卖出/减仓风控",
            "分钟数据不可用，无法判断冲高回落是否触发。",
            data_available=False,
            action_type="sell",
        ))
        risk_controls["position_risk"].append(_make_risk_control(
            "profit_giveback",
            "利润回吐保护",
            "盘中最高收益 >= 15%，且收益回吐 >= 10个百分点，或最高收益 >= 30% 后当前收益 < 10% 时触发止盈",
            "分钟数据不可用，无法判断盘中利润回吐是否触发。",
            data_available=False,
            action_type="take_profit",
        ))
        msg = "分钟数据不可用，无法判断冲高回落和盘中利润回吐。"
        if minute_error:
            msg += f" 数据源提示：{minute_error}"
        missing_context.append(msg)

    timing = flow_result.get("timing") or flow_result.get("flow_timing") or {}
    leadership = flow_result.get("leadership") or flow_result.get("flow_leadership") or {}
    flow_signals = flow_result.get("flow_signals") or {}
    phase = timing.get("phase")
    phase_triggered = phase in ["分歧", "退潮"] and not near_limit_up
    risk_controls["rotation_quality"].append(_make_risk_control(
        "flow_divergence",
        "流量分歧退潮",
        "流量阶段为分歧/退潮且未接近涨停时，触发减仓或卖出风控",
        (
            f"当前流量阶段 {phase}，{'接近涨停强势抵消' if phase in ['分歧', '退潮'] and near_limit_up else ('已触发流量分歧退潮' if phase_triggered else '未触发流量分歧退潮')}。"
            if phase else
            "流量阶段数据不足，无法判断分歧退潮是否触发。"
        ),
        phase_triggered,
        data_available=bool(phase),
        offset_by_strength=phase in ["分歧", "退潮"] and near_limit_up,
        action_type="sell" if phase == "退潮" else "reduce",
    ))
    if phase in ["分歧", "退潮"] and not near_limit_up:
        signals.append(_make_exit_signal(
            "轮动/趋势质量",
            "high" if phase == "退潮" else "medium",
            "流量分歧退潮信号",
            f"流量阶段处于{phase}期，趋势质量和机会成本需要重新评估。",
            strategy_key="rotation_quality",
            action_type="sell" if phase == "退潮" else "reduce",
        ))
    harmony_available = "vol_price_harmony" in leadership
    harmony_triggered = leadership.get("vol_price_harmony") is False and not near_limit_up
    risk_controls["rotation_quality"].append(_make_risk_control(
        "vol_price_divergence",
        "量价背离",
        "量价配合度为弱且未接近涨停时，触发减仓风控",
        (
            f"当前量价配合度{'不足' if leadership.get('vol_price_harmony') is False else '未见明显背离'}，"
            f"{'接近涨停强势抵消' if leadership.get('vol_price_harmony') is False and near_limit_up else ('已触发量价背离' if harmony_triggered else '未触发量价背离')}。"
            if harmony_available else
            "量价配合数据不足，无法判断量价背离是否触发。"
        ),
        harmony_triggered,
        data_available=harmony_available,
        offset_by_strength=leadership.get("vol_price_harmony") is False and near_limit_up,
        action_type="reduce",
    ))
    if leadership.get("vol_price_harmony") is False and not near_limit_up:
        signals.append(_make_exit_signal(
            "轮动/趋势质量",
            "medium",
            "量价背离",
            "价格与成交量配合度不足，资金承接质量偏弱。",
            strategy_key="rotation_quality",
            action_type="reduce",
        ))
    flow_phase = flow_signals.get("flow_phase")
    flow_shrink_triggered = flow_phase == "萎缩" and not near_limit_up
    risk_controls["rotation_quality"].append(_make_risk_control(
        "volume_shrink",
        "量能萎缩",
        "成交量进入萎缩阶段且未接近涨停时，触发观察风控",
        (
            f"当前量能阶段 {flow_phase}，{'接近涨停强势抵消' if flow_phase == '萎缩' and near_limit_up else ('已触发量能萎缩' if flow_shrink_triggered else '未触发量能萎缩')}。"
            if flow_phase else
            "量能阶段数据不足，无法判断量能萎缩是否触发。"
        ),
        flow_shrink_triggered,
        data_available=bool(flow_phase),
        offset_by_strength=flow_phase == "萎缩" and near_limit_up,
        action_type="watch",
    ))
    if flow_signals.get("flow_phase") == "萎缩" and not near_limit_up:
        signals.append(_make_exit_signal(
            "轮动/趋势质量",
            "medium",
            "量能萎缩",
            "成交量进入萎缩阶段，短线资金关注度下降。",
            strategy_key="rotation_quality",
            action_type="watch",
        ))

    high_count = sum(1 for s in signals if s["priority"] == "high" and s.get("executable", True))
    medium_count = sum(1 for s in signals if s["priority"] == "medium" and s.get("executable", True))
    if high_count >= 2:
        risk_level = "high"
        risk_score = 90
        adjustment = "downgrade_two"
    elif high_count == 1:
        risk_level = "high"
        risk_score = 78
        adjustment = "downgrade_one"
    elif medium_count >= 2:
        risk_level = "medium"
        risk_score = 58
        adjustment = "downgrade_one"
    elif medium_count == 1:
        risk_level = "medium"
        risk_score = 42
        adjustment = "none"
    else:
        risk_level = "low"
        risk_score = 18 if near_limit_up else 25
        adjustment = "none"

    if near_limit_up and high_count == 0:
        risk_level = "low" if medium_count <= 1 else "medium"
        adjustment = "none"
        risk_score = min(risk_score, 35)

    if risk_level == "high":
        summary = "策略卖点风险较高，建议优先控制仓位或执行卖出纪律。"
    elif risk_level == "medium":
        summary = "出现策略卖点观察信号，建议降低仓位弹性并跟踪盘中承接。"
    else:
        summary = "暂未触发明确策略卖点，按原有技术面和流量分析跟踪。"
    if near_limit_up and risk_level != "high":
        summary = "接近涨停，强势持有信号占优；普通超买或分歧提示暂不作为强卖依据。"

    trade_eligibility = _build_trade_eligibility(normalized_buy_date, latest_trade_date)
    if trade_eligibility["guidance_scope"] == "next_trading_day" and risk_level != "low":
        summary = f"{summary} 今日不可卖出，以上卖点作为下一交易日风控指导。"

    exit_prices = _build_dropdown_exit_prices(
        current_price,
        cost_price,
        pre_close,
        open_price,
        ma5,
        intraday_high,
        risk_controls,
    )
    strategies = _build_strategy_results(signals, cost_price, risk_controls, exit_prices)

    return {
        "risk_level": risk_level,
        "risk_score": risk_score,
        "recommendation_adjustment": adjustment,
        "summary": summary,
        "signals": signals,
        "missing_context": missing_context,
        "position": position,
        "buy_date": normalized_buy_date,
        "latest_trade_date": latest_trade_date,
        "trade_date_count": len(trade_dates),
        "trade_eligibility": trade_eligibility,
        "strategies": strategies,
        "data_status": {
            "minute": minute_status,
            "minute_error": minute_error,
        },
    }


def apply_strategy_exit_adjustment(base_recommendation: str, strategy_exit: dict) -> dict:
    levels = ["强烈卖出", "卖出", "观望", "买入", "强烈买入"]
    base = base_recommendation if base_recommendation in levels else "观望"
    idx = levels.index(base)
    adjustment = strategy_exit.get("recommendation_adjustment", "none")
    if adjustment == "downgrade_two":
        idx = max(0, idx - 2)
    elif adjustment == "downgrade_one":
        idx = max(0, idx - 1)
    final = levels[idx]
    return {
        "base_recommendation": base_recommendation,
        "recommendation": final,
        "strategy_adjusted": final != base_recommendation,
    }


# ─── 技术面评分 (满分100) ─────────────────────────────────

def score_ma(close: pd.Series) -> tuple:
    """
    均线评分 (满分20)
    多头排列(5>10>20>60) 高分，空头排列低分
    """
    if len(close) < 60:
        return 10, "均线数据不足，给予中性评分"

    ma5 = close.tail(5).mean()
    ma10 = close.tail(10).mean()
    ma20 = close.tail(20).mean()
    ma60 = close.tail(60).mean()

    score = 10  # 基础分
    desc = []

    # 多头排列判断
    if ma5 > ma10 > ma20 > ma60:
        score = 20
        desc.append("均线多头排列，趋势强劲")
    elif ma5 > ma10 > ma20:
        score = 16
        desc.append("短中期均线多头排列")
    elif ma5 < ma10 < ma20 < ma60:
        score = 2
        desc.append("均线空头排列，趋势偏弱")
    elif ma5 < ma10 < ma20:
        score = 6
        desc.append("短中期均线空头排列")
    else:
        score = 10
        desc.append("均线交织，方向不明")

    # 当前价格相对均线位置
    current = close.iloc[-1]
    if current > ma5:
        score = min(20, score + 2)
        desc.append("价格在5日均线上方")
    else:
        score = max(0, score - 2)
        desc.append("价格在5日均线下方")

    return score, "；".join(desc)


def score_macd(macd_data: dict) -> tuple:
    """
    MACD评分 (满分20)
    金叉/柱状图趋势
    """
    dif = macd_data["macd"]
    dea = macd_data["signal"]
    hist = macd_data["hist"]

    score = 10
    desc = []

    # DIF和DEA的关系
    if dif > dea:
        score += 5
        desc.append("MACD金叉状态")
    else:
        score -= 5
        desc.append("MACD死叉状态")

    # 零轴上方/下方
    if dif > 0:
        score += 3
        desc.append("DIF在零轴上方，多头趋势")
    else:
        score -= 3
        desc.append("DIF在零轴下方，空头趋势")

    # 柱状图方向
    if hist > 0:
        score += 2
        desc.append("红柱，做多动能")
    else:
        score -= 2
        desc.append("绿柱，做空动能")

    score = max(0, min(20, score))
    return score, "；".join(desc)


def score_kdj(kdj_data: dict) -> tuple:
    """
    KDJ评分 (满分15)
    超买超卖判断
    """
    k = kdj_data["k"]
    d = kdj_data["d"]
    j = kdj_data["j"]

    score = 8
    desc = []

    # 超买超卖
    if k > 80 and d > 80:
        score = 3
        desc.append(f"KDJ超买区(K={k:.1f})，注意回调风险")
    elif k < 20 and d < 20:
        score = 13
        desc.append(f"KDJ超卖区(K={k:.1f})，可能存在反弹机会")
    elif 20 <= k <= 80:
        score = 8
        desc.append(f"KDJ正常区间(K={k:.1f})")

    # 金叉死叉
    if k > d:
        score += 2
        desc.append("K在D线上方")
    else:
        score -= 2
        desc.append("K在D线下方")

    score = max(0, min(15, score))
    return score, "；".join(desc)


def score_rsi(rsi: float) -> tuple:
    """
    RSI评分 (满分15)
    """
    desc = []

    if rsi > 80:
        score = 3
        desc.append(f"RSI={rsi:.1f}，严重超买")
    elif rsi > 70:
        score = 6
        desc.append(f"RSI={rsi:.1f}，超买区域")
    elif rsi > 50:
        score = 11
        desc.append(f"RSI={rsi:.1f}，多方占优")
    elif rsi > 30:
        score = 8
        desc.append(f"RSI={rsi:.1f}，空方占优")
    elif rsi > 20:
        score = 12
        desc.append(f"RSI={rsi:.1f}，超卖区域")
    else:
        score = 14
        desc.append(f"RSI={rsi:.1f}，严重超卖，可能反弹")

    return score, "；".join(desc)


def score_bollinger(close_price: float, boll_data: dict) -> tuple:
    """
    布林带评分 (满分15)
    """
    upper = boll_data["upper"]
    mid = boll_data["mid"]
    lower = boll_data["lower"]

    if mid == 0:
        return 8, "布林带数据不足"

    desc = []
    # 价格在布林带中的位置 (0=下轨, 1=上轨)
    boll_width = upper - lower
    if boll_width == 0:
        return 8, "布林带宽度为0"

    position = (close_price - lower) / boll_width

    if position > 0.9:
        score = 4
        desc.append("价格接近布林上轨，短期可能承压")
    elif position > 0.7:
        score = 8
        desc.append("价格偏向上轨运行")
    elif position > 0.3:
        score = 12
        desc.append("价格在布林中轨附近，走势平稳")
    elif position > 0.1:
        score = 14
        desc.append("价格偏向下轨运行")
    else:
        score = 10
        desc.append("价格接近布林下轨，可能超跌反弹")

    return score, "；".join(desc)


def score_volume(vol_ratio: float) -> tuple:
    """
    成交量评分 (满分15)
    量比 > 1.5 放量，< 0.7 缩量
    """
    desc = []

    if vol_ratio > 2.5:
        score = 7
        desc.append(f"量比{vol_ratio}，放量明显，注意风险")
    elif vol_ratio > 1.5:
        score = 13
        desc.append(f"量比{vol_ratio}，温和放量，市场活跃")
    elif vol_ratio > 0.8:
        score = 10
        desc.append(f"量比{vol_ratio}，成交量正常")
    elif vol_ratio > 0.5:
        score = 6
        desc.append(f"量比{vol_ratio}，缩量运行")
    else:
        score = 3
        desc.append(f"量比{vol_ratio}，极度缩量")

    return score, "；".join(desc)


# ─── 基本面评分 (满分100) ─────────────────────────────────

def score_pe(pe_ratio: Optional[float]) -> tuple:
    """PE估值评分 (满分30)"""
    if pe_ratio is None or pe_ratio <= 0:
        return 15, "PE数据不可用"

    desc = []
    if pe_ratio < 10:
        score = 28
        desc.append(f"PE={pe_ratio:.1f}，估值极低")
    elif pe_ratio < 20:
        score = 24
        desc.append(f"PE={pe_ratio:.1f}，估值合理偏低")
    elif pe_ratio < 40:
        score = 18
        desc.append(f"PE={pe_ratio:.1f}，估值适中")
    elif pe_ratio < 80:
        score = 10
        desc.append(f"PE={pe_ratio:.1f}，估值偏高")
    else:
        score = 4
        desc.append(f"PE={pe_ratio:.1f}，估值过高")

    return score, "；".join(desc)


def score_pb(pb_ratio: Optional[float]) -> tuple:
    """PB估值评分 (满分20)"""
    if pb_ratio is None or pb_ratio <= 0:
        return 10, "PB数据不可用"

    desc = []
    if pb_ratio < 1:
        score = 18
        desc.append(f"PB={pb_ratio:.2f}，破净，估值极低")
    elif pb_ratio < 2:
        score = 15
        desc.append(f"PB={pb_ratio:.2f}，估值合理")
    elif pb_ratio < 5:
        score = 10
        desc.append(f"PB={pb_ratio:.2f}，估值适中")
    elif pb_ratio < 10:
        score = 6
        desc.append(f"PB={pb_ratio:.2f}，估值偏高")
    else:
        score = 3
        desc.append(f"PB={pb_ratio:.2f}，估值过高")

    return score, "；".join(desc)


def score_change(recent_5d: float, recent_20d: float) -> tuple:
    """涨跌幅评分 (满分30)"""
    score = 15
    desc = []

    # 短期趋势
    if recent_5d > 10:
        score += 3
        desc.append(f"近5日涨{recent_5d:.1f}%，短线强势")
    elif recent_5d > 3:
        score += 5
        desc.append(f"近5日涨{recent_5d:.1f}%，走势良好")
    elif recent_5d > -3:
        score += 0
        desc.append(f"近5日波动{recent_5d:.1f}%，走势平稳")
    elif recent_5d > -10:
        score -= 3
        desc.append(f"近5日跌{recent_5d:.1f}%，短线偏弱")
    else:
        score -= 5
        desc.append(f"近5日跌{recent_5d:.1f}%，短线弱势")

    # 中期趋势
    if recent_20d > 20:
        score += 5
        desc.append(f"近20日涨{recent_20d:.1f}%，中期强势")
    elif recent_20d > 5:
        score += 7
        desc.append(f"近20日涨{recent_20d:.1f}%，中期向好")
    elif recent_20d > -5:
        score += 2
        desc.append(f"近20日波动{recent_20d:.1f}%")
    elif recent_20d > -15:
        score -= 5
        desc.append(f"近20日跌{recent_20d:.1f}%，中期偏弱")
    else:
        score -= 8
        desc.append(f"近20日跌{recent_20d:.1f}%，中期弱势")

    score = max(0, min(30, score))
    return score, "；".join(desc)


def score_market_cap(market_cap: Optional[float]) -> tuple:
    """市值评分 (满分20)"""
    if market_cap is None:
        return 10, "市值数据不可用"

    # market_cap 单位是元，转换为亿
    cap_yi = market_cap if market_cap is not None else 0

    desc = []
    if cap_yi > 1000:
        score = 16
        desc.append(f"大盘股（{cap_yi:.0f}亿），稳定性好")
    elif cap_yi > 300:
        score = 14
        desc.append(f"中大盘股（{cap_yi:.0f}亿）")
    elif cap_yi > 100:
        score = 12
        desc.append(f"中盘股（{cap_yi:.0f}亿）")
    elif cap_yi > 30:
        score = 10
        desc.append(f"小盘股（{cap_yi:.0f}亿），波动较大")
    else:
        score = 8
        desc.append(f"微盘股（{cap_yi:.0f}亿），风险较高")

    return score, "；".join(desc)


# ─── 综合分析 ─────────────────────────────────────────────

def analyze_stock(
    df: pd.DataFrame,
    stock_info: dict,
    stock_code: str,
    minute_df: Optional[pd.DataFrame] = None,
    cost_price: Optional[float] = None,
    minute_error: Optional[str] = None,
    buy_date: Optional[str] = None,
    flow_result: Optional[dict] = None,
) -> dict:
    """
    综合分析股票

    Args:
        df: 日K线数据 DataFrame
        stock_info: 股票基本信息 dict
        stock_code: 股票代码

    Returns:
        完整分析结果 dict
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    stock_name = stock_info.get("name", stock_code)
    instrument_type = stock_info.get("instrument_type")
    if not instrument_type:
        name_for_classification = str(stock_name).upper()
        etf_prefixes = ("159", "510", "511", "512", "513", "515", "516", "517", "518", "520", "588", "589")
        instrument_type = "etf" if stock_code.startswith(etf_prefixes) or any(marker in name_for_classification for marker in ["ETF", "LOF", "基金"]) else "stock"
    is_etf = bool(stock_info.get("is_etf", instrument_type == "etf"))

    current_price = float(close.iloc[-1])

    # ── 计算技术指标 ──
    ma5 = calc_ma(close, 5)
    ma10 = calc_ma(close, 10)
    ma20 = calc_ma(close, 20)
    ma60 = calc_ma(close, 60)

    macd_data = calc_macd(close)
    kdj_data = calc_kdj(high, low, close)
    rsi = calc_rsi(close)
    boll_data = calc_bollinger(close)
    vol_ratio = calc_volume_ratio(volume)

    # ── 技术面评分 ──
    ma_score, ma_desc = score_ma(close)
    macd_score, macd_desc = score_macd(macd_data)
    kdj_score, kdj_desc = score_kdj(kdj_data)
    rsi_score, rsi_desc = score_rsi(rsi)
    boll_score, boll_desc = score_bollinger(current_price, boll_data)
    vol_score, vol_desc = score_volume(vol_ratio)

    tech_total = ma_score + macd_score + kdj_score + rsi_score + boll_score + vol_score
    tech_score = round(tech_total, 1)  # 满分100

    # ── 基本面指标 ──
    pe_ratio = stock_info.get("pe_ratio")
    pb_ratio = stock_info.get("pb_ratio")
    market_cap = stock_info.get("market_cap")

    # 计算涨跌幅
    if len(close) >= 5:
        recent_5d = round(((current_price / float(close.iloc[-6])) - 1) * 100, 2) if len(close) >= 6 else 0
    else:
        recent_5d = 0

    if len(close) >= 20:
        recent_20d = round(((current_price / float(close.iloc[-21])) - 1) * 100, 2) if len(close) >= 21 else 0
    else:
        recent_20d = 0

    # ── 基本面评分 ──
    pe_score, pe_desc = score_pe(pe_ratio)
    pb_score, pb_desc = score_pb(pb_ratio)
    change_score, change_desc = score_change(recent_5d, recent_20d)
    cap_score, cap_desc = score_market_cap(market_cap)

    fund_total = pe_score + pb_score + change_score + cap_score
    fund_score = round(fund_total, 1)  # 满分100

    # ── 综合评分 (技术60% + 基本面40%) ──
    total_score = round(tech_score * 0.6 + fund_score * 0.4, 1)

    # ── 买卖建议（原始技术面） ──
    if total_score >= 80:
        tech_rec = "强烈买入"
    elif total_score >= 60:
        tech_rec = "买入"
    elif total_score >= 40:
        tech_rec = "观望"
    elif total_score >= 20:
        tech_rec = "卖出"
    else:
        tech_rec = "强烈卖出"

    # ── 趋势分析 ──
    trend = analyze_trend(close)

    # ── 支撑位和压力位 ──
    sr = calc_support_resistance(close, high, low, boll_data, ma60, current_price)

    # ── 量价关系分析 ──
    vol_price = analyze_volume_price(volume, close)

    # ── 买卖信号 ──
    signals = generate_signals(macd_data, kdj_data, rsi, current_price, boll_data, ma5, ma10, ma20, ma60)

    # ── 操作建议 ──
    technical_action = generate_action(signals, total_score, current_price, sr["support_levels"], sr["resistance_levels"])

    # ── 估值分析 ──
    valuation = evaluate_valuation(pe_ratio, pb_ratio)

    # ── 市值分类 ──
    cap_class = classify_market_cap(market_cap)

    # ── 流量定价模型分析 ──
    flow_result = flow_result or analyze_flow(df, stock_info)
    heat_context = _normalize_heat_context(stock_info)
    sector = flow_result.get("sector") or {
        "name": heat_context["sector_name"],
        "industry": heat_context["industry"],
        "heat_score": heat_context["sector_heat_score"],
        "external_heat_score": heat_context["external_heat_score"],
        "capital_slope_score": heat_context["capital_slope_score"],
        "heat_acceleration_score": heat_context["heat_acceleration_score"],
        "exit_risk_score": heat_context["exit_risk_score"],
        "rank": heat_context["sector_rank"],
        "rank_total": heat_context["sector_rank_total"],
        "rank_hint": heat_context["rank_hint"],
    }
    industry = heat_context["industry"]

    # ── 综合建议：技术面为基础，题材流量只做风险降级，不再输出参与性建议 ──
    flow_rec = flow_result.get("flow_recommendation", "观望")

    conservative_map = {
        "强烈买入": 5,
        "买入": 4,
        "低位升温": 3,
        "扩散中": 3,
        "分歧观察": 2,
        "高热分歧": 2,
        "高潮风险": 2,
        "退潮回避": 1,
        "观望": 3,
        "卖出": 2,
        "回避": 1,
        "强烈卖出": 1,
    }
    reverse_map = {5: "强烈买入", 4: "买入", 3: "观望", 2: "卖出", 1: "强烈卖出"}

    tech_val = conservative_map.get(tech_rec, 3)
    flow_val = conservative_map.get(flow_rec, 3)
    final_val = min(tech_val, flow_val)
    base_recommendation = reverse_map[final_val]

    # ── 策略卖点提示 ──
    if is_etf:
        strategy_exit = _build_etf_strategy_exit(df, buy_date=buy_date)
    else:
        strategy_exit = analyze_strategy_exit(
            df,
            stock_info,
            minute_df=minute_df,
            cost_price=cost_price,
            flow_result=flow_result,
            minute_error=minute_error,
            buy_date=buy_date,
        )
    adjusted_rec = apply_strategy_exit_adjustment(base_recommendation, strategy_exit)
    strategy_recommendation = adjusted_rec["recommendation"]
    recommendation = strategy_recommendation
    t_plus_one_adjusted = _is_same_day_buy_blocked(strategy_exit) and strategy_recommendation in SELL_RECOMMENDATIONS
    if t_plus_one_adjusted:
        recommendation = "观望"
    strategy_adjusted = adjusted_rec["strategy_adjusted"] or t_plus_one_adjusted
    action = _align_action_with_recommendation(
        technical_action,
        recommendation,
        base_recommendation,
        strategy_recommendation,
        strategy_exit,
        adjusted_rec["strategy_adjusted"],
        t_plus_one_adjusted,
    )

    if tech_rec != base_recommendation:
        conflict_note = f"\n⚠️ 注意：技术面参考为「{tech_rec}」，题材流量状态为「{flow_rec}」，综合取更谨慎的「{base_recommendation}」"
    else:
        conflict_note = ""

    strategy_note = ""
    if adjusted_rec["strategy_adjusted"]:
        strategy_note = f"\n⚠️ 策略卖点提示触发「{strategy_exit['risk_level']}」风险，综合建议由「{base_recommendation}」保守调整为「{strategy_recommendation}」。"
    if t_plus_one_adjusted:
        strategy_note += "\n⚠️ 买入日为最新交易日，今日不能执行卖出，其他策略卖点作为下一交易日风控指导。"

    # ── 生成详细分析报告 ──

    # 拼接趋势描述
    def _trend_line(t):
        icon = t.get("icon", "→")
        cn_dir = t["direction"].replace("up", "上升").replace("down", "下降").replace("flat", "横盘")
        return f"{icon} {cn_dir}（{t['desc']}）"

    # 信号文本
    signal_lines_buy = [f"    • {s}" for s in signals["buy"]]
    signal_lines_sell = [f"    • {s}" for s in signals["sell"]]

    # 支撑压力文本
    support_text = " → ".join([f"{p}（{l}）" for p, l in zip(sr["support_levels"], sr["support_labels"])]) if sr["support_levels"] else "无明显支撑"
    resistance_text = " → ".join([f"{p}（{l}）" for p, l in zip(sr["resistance_levels"], sr["resistance_labels"])]) if sr["resistance_levels"] else "无明显压力"

    summary_parts = [
        f"【{stock_name}（{stock_code}）】综合分析报告",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📊 综合评分：{total_score}/100 | 建议：{recommendation} | 操作：{action['operation']} | 仓位：{action['position']}",
        "",
        "▎趋势分析",
        f"  短期趋势：{_trend_line(trend['short'])}",
        f"  中期趋势：{_trend_line(trend['mid'])}",
        f"  长期趋势：{_trend_line(trend['long'])}",
        f"  趋势研判：{trend['summary']}",
        "",
        f"▎技术面分析（{tech_score}分）",
        f"  均线系统：{ma_desc}",
        f"  MACD：DIF={macd_data['macd']}，{macd_desc}",
        f"  KDJ：K={kdj_data['k']:.1f}，{kdj_desc}",
        f"  RSI：{rsi:.1f}，{rsi_desc}",
        f"  布林带：{boll_desc}",
        f"  成交量：量比{vol_ratio}，{vol_desc}",
        "",
        "▎量价关系",
        f"  {vol_price['detail']}",
        "",
        "▎关键价位",
        f"  支撑位：{support_text}",
        f"  压力位：{resistance_text}",
        "",
        "▎买卖信号",
    ]
    if signals["buy"]:
        summary_parts.append("  ✅ 买入信号：")
        summary_parts.extend(signal_lines_buy)
    if signals["sell"]:
        summary_parts.append("  ❌ 卖出信号：")
        summary_parts.extend(signal_lines_sell)
    if not signals["buy"] and not signals["sell"]:
        summary_parts.append("  ⏳ 暂无明确信号")
    summary_parts.append(f"  信号强度：{'偏多' if signals['strength'] == 'buy' else '偏空' if signals['strength'] == 'sell' else '中性'}")

    summary_parts.extend([
        "",
        "▎操作建议",
        f"  建议：{action['operation']}，{action['position']}",
        f"  依据：{action['basis']}",
        f"  止损：{action['stop_loss']}（当前价下方{5 if total_score < 40 else 3}%）",
        f"  止盈：{action['take_profit']}（最近压力位）",
        f"  周期：{action['period']}",
        f"  风险等级：{action['risk_level']}",
        "",
        "▎估值分析",
        f"  PE={pe_ratio if pe_ratio else 'N/A'}（{valuation['pe_level']}）",
        f"  PB={pb_ratio if pb_ratio else 'N/A'}（{valuation['pb_level']}）",
        f"  {valuation['summary']}",
        "",
        "▎市值特征",
        f"  {cap_class}",
    ])

    # 流量分析报告
    fs = flow_result["flow_signals"]
    tm = flow_result["timing"]
    ld = flow_result["leadership"]
    vol_burst_word = "爆发" if fs["volume_burst"] >= 2.0 else "放大" if fs["volume_burst"] >= 1.5 else "正常" if fs["volume_burst"] >= 1.0 else "萎缩"
    vol_word = "极高" if fs["volatility"] > 4 else "偏高" if fs["volatility"] > 2.5 else "中等"
    retail_attention = flow_result.get("retail_attention", {})
    topic_conversion = flow_result.get("topic_conversion", {})
    capital_acceptance = flow_result.get("capital_acceptance", {})
    heat_trend = flow_result.get("heat_trend", {})
    leader_position = flow_result.get("leader_position", {})
    risk_warning = flow_result.get("risk_warning", {})
    flow_data_status = flow_result.get("data_status", {})
    source_line = "；".join(retail_attention.get("source_labels") or []) or "外部讨论代理数据不足"
    warnings_line = "；".join((flow_data_status.get("warnings") or [])[:2])
    summary_parts.extend([
        "",
        f"▎策略卖点提示（风险{strategy_exit['risk_score']}分）",
        f"  风险等级：{strategy_exit['risk_level']}",
        f"  策略结论：{strategy_exit['summary']}",
        f"  交易资格：{strategy_exit['trade_eligibility']['summary']}",
    ])
    for sig in strategy_exit["signals"][:6]:
        summary_parts.append(f"  • [{sig['source']}] {sig['title']}：{sig['detail']}")
    if strategy_exit["missing_context"]:
        summary_parts.append(f"  数据限制：{strategy_exit['missing_context'][0]}")

    summary_parts.extend([
        "",
        f"▎题材流量分析（接盘潜力{flow_result['flow_total_score']}分）",
        f"  模型：retail_attention_v2（先看散户讨论与板块热度，再看资金承接和龙头位置）",
        f"  散户流量方向：{retail_attention.get('direction', tm.get('attention_direction', '--'))}",
        f"  周期阶段：{tm['phase']}（{tm['phase_desc']}）",
        f"  风险提示：{risk_warning.get('label', flow_result.get('flow_recommendation', '--'))}，退出风险{risk_warning.get('score', fs.get('exit_risk_score', '--'))}",
        f"  板块/行业：{sector.get('name', '未知')} / {industry}",
        f"  散户讨论热度：{retail_attention.get('score', fs.get('retail_attention_score', '--'))}；数据口径：{source_line}",
        f"  题材转化率：{topic_conversion.get('score', fs.get('topic_conversion_score', '--'))}（{topic_conversion.get('level', fs.get('topic_conversion_level', '--'))}）",
        f"  热度趋势：{heat_trend.get('score', fs.get('heat_trend_score', '--'))}，置信度{heat_trend.get('confidence', sector.get('confidence', '--'))}",
        f"  资金承接：{capital_acceptance.get('score', fs.get('capital_acceptance_score', '--'))}（{capital_acceptance.get('source', '成交额代理')}）",
        f"  龙头位置：{leader_position.get('leader_label', ld.get('leader_label', '--'))}；{leader_position.get('ranking_status', ld.get('ranking_status', '--'))}",
        f"  辅助承接证据：成交量{vol_burst_word}{fs['volume_burst']}倍，近5日涨幅{fs['price_momentum']:+.1f}%，波动率{fs['volatility']:.1f}%（{vol_word}）",
        "",
        f"  流量判断：{fs['flow_desc']}",
        f"  周期判断：{tm['hold_advice']}",
        f"  龙头判断：{ld['leadership_desc']}",
        f"  退出风险：{tm['exit_signal']}",
        f"  数据限制：{warnings_line or '暂无额外限制'}",
        "",
        "⚠️ 免责声明：本分析仅供参考，不构成投资建议。"
    ])

    # 如果存在建议冲突，插入冲突说明
    if conflict_note:
        summary_parts.insert(-1, conflict_note)
    if strategy_note:
        summary_parts.insert(-1, strategy_note)

    analysis_text = "\n".join(summary_parts)
    summary_text = (
        f"{stock_name}（{stock_code}）综合评分{total_score}/100，建议{recommendation}，"
        f"操作{action['operation']}、仓位{action['position']}；"
        f"策略卖点风险{strategy_exit['risk_level']}，{strategy_exit['summary']}"
    )

    # ── 返回完整结果 ──
    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "industry": industry,
        "current_price": current_price,
        # 技术指标
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "macd": macd_data["macd"],
        "macd_signal": macd_data["signal"],
        "macd_hist": macd_data["hist"],
        "kdj_k": kdj_data["k"],
        "kdj_d": kdj_data["d"],
        "kdj_j": kdj_data["j"],
        "rsi": rsi,
        "boll_upper": boll_data["upper"],
        "boll_mid": boll_data["mid"],
        "boll_lower": boll_data["lower"],
        "volume_ratio": vol_ratio,
        # 基本面
        "pe_ratio": pe_ratio,
        "pb_ratio": pb_ratio,
        "market_cap": market_cap,
        "recent_change_5d": recent_5d,
        "recent_change_20d": recent_20d,
        # 评分
        "tech_score": tech_score,
        "fundamental_score": fund_score,
        "total_score": total_score,
        "base_recommendation": base_recommendation,
        "recommendation": recommendation,
        "strategy_recommendation": strategy_recommendation,
        "strategy_adjusted": strategy_adjusted,
        "t_plus_one_adjusted": t_plus_one_adjusted,
        "instrument_type": instrument_type,
        "is_etf": is_etf,
        "summary_text": summary_text,
        "analysis_text": analysis_text,
        # 新增：趋势分析
        "trend": trend,
        # 新增：关键价位
        "support_levels": sr["support_levels"],
        "support_labels": sr["support_labels"],
        "resistance_levels": sr["resistance_levels"],
        "resistance_labels": sr["resistance_labels"],
        # 新增：买卖信号
        "signals": signals,
        # 新增：操作建议
        "action": action,
        "technical_action": technical_action,
        # 新增：估值分析
        "valuation": valuation,
        # 新增：市值分类
        "cap_class": cap_class,
        # 新增：量价关系
        "vol_price": vol_price,
        # 新增：流量分析
        "flow_signals": flow_result["flow_signals"],
        "flow_timing": flow_result["timing"],
        "flow_leadership": flow_result["leadership"],
        "flow_model": flow_result.get("flow_model"),
        "retail_attention": flow_result.get("retail_attention"),
        "topic_conversion": flow_result.get("topic_conversion"),
        "capital_acceptance": flow_result.get("capital_acceptance"),
        "heat_trend": flow_result.get("heat_trend"),
        "leader_position": flow_result.get("leader_position"),
        "cycle_stage": flow_result.get("cycle_stage"),
        "risk_warning": flow_result.get("risk_warning"),
        "data_status": flow_result.get("data_status"),
        "flow_data_status": flow_result.get("data_status"),
        "sector": sector,
        "sector_name": sector.get("name"),
        "sector_type": sector.get("type"),
        "sector_heat": sector.get("heat_score"),
        "sector_heat_score": sector.get("heat_score"),
        "sector_heat_window": sector.get("window"),
        "sector_heat_date": sector.get("date"),
        "sector_source": sector.get("source"),
        "sector_confidence": sector.get("confidence"),
        "external_heat": sector.get("external_heat_score"),
        "external_heat_score": sector.get("external_heat_score"),
        "capital_slope": sector.get("capital_slope_score"),
        "capital_slope_score": sector.get("capital_slope_score"),
        "heat_acceleration": sector.get("heat_acceleration_score"),
        "heat_acceleration_score": sector.get("heat_acceleration_score"),
        "exit_risk": sector.get("exit_risk_score"),
        "exit_risk_score": sector.get("exit_risk_score"),
        "flow_total_score": flow_result["flow_total_score"],
        "flow_recommendation": flow_result["flow_recommendation"],
        "flow_analysis_text": flow_result["flow_analysis_text"],
        # 新增：策略卖点提示
        "strategy_exit": strategy_exit,
    }
