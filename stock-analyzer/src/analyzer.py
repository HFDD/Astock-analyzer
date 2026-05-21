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

def analyze_flow_signals(df: pd.DataFrame, stock_info: dict) -> dict:
    """
    流量信号评分（0-100）
    基于量能爆发度、价格动量、波动率、换手率、布林带位置综合评估
    """
    close = df["close"]
    volume = df["volume"]
    high = df["high"]
    low = df["low"]

    # a) 量能爆发度（权重30%）
    if len(volume) >= 20:
        vol_5 = float(volume.tail(5).mean())
        vol_20 = float(volume.tail(20).mean())
        volume_burst = round(vol_5 / vol_20, 2) if vol_20 > 0 else 1.0
    else:
        volume_burst = 1.0

    if volume_burst >= 2.0:
        vol_score = 100
    elif volume_burst >= 1.5:
        vol_score = 70
    elif volume_burst >= 1.0:
        vol_score = 40
    else:
        vol_score = 20

    # b) 价格动量（权重25%）
    if len(close) >= 6:
        price_momentum = round(((float(close.iloc[-1]) / float(close.iloc[-6])) - 1) * 100, 2)
    else:
        price_momentum = 0.0

    # 连续上涨天数
    up_days = 0
    for i in range(-1, max(-6, -len(close)), -1):
        if float(close.iloc[i]) > float(close.iloc[i - 1]):
            up_days += 1
        else:
            break

    if price_momentum > 15 or up_days >= 4:
        momentum_score = 100
    elif price_momentum > 8 or up_days >= 3:
        momentum_score = 80
    elif price_momentum > 3:
        momentum_score = 60
    elif price_momentum > 0:
        momentum_score = 40
    else:
        momentum_score = 20

    # c) 波动率（权重20%）
    if len(close) >= 11:
        returns = close.pct_change().dropna().tail(10)
        volatility = round(float(returns.std()) * 100, 2)
    else:
        volatility = 1.0

    if volatility > 4:
        vol_score2 = 100
    elif volatility > 2.5:
        vol_score2 = 70
    elif volatility > 1.5:
        vol_score2 = 50
    else:
        vol_score2 = 25

    # d) 换手率水平（权重15%）
    turnover = stock_info.get("turnover_rate")
    if turnover is not None and turnover > 0:
        if turnover > 10:
            turn_score = 100
        elif turnover > 5:
            turn_score = 70
        elif turnover > 2:
            turn_score = 45
        else:
            turn_score = 20
    else:
        turn_score = 50  # 无数据给中性分

    # e) 距离布林带位置（权重10%）
    boll_data = calc_bollinger(close)
    if boll_data["upper"] > 0 and boll_data["upper"] != boll_data["lower"]:
        boll_width = boll_data["upper"] - boll_data["lower"]
        boll_position = (float(close.iloc[-1]) - boll_data["lower"]) / boll_width
        if boll_position > 0.85:
            boll_score = 90  # 接近上轨=流量高潮
        elif boll_position > 0.5:
            boll_score = 60
        elif boll_position > 0.15:
            boll_score = 35
        else:
            boll_score = 20  # 接近下轨=流量低谷
    else:
        boll_score = 50

    # 加权总分
    flow_score = round(vol_score * 0.30 + momentum_score * 0.25 + vol_score2 * 0.20 + turn_score * 0.15 + boll_score * 0.10)
    flow_score = max(0, min(100, flow_score))

    # 流量等级
    if flow_score >= 80:
        flow_level = "极高"
    elif flow_score >= 65:
        flow_level = "高"
    elif flow_score >= 45:
        flow_level = "中等"
    elif flow_score >= 25:
        flow_level = "低"
    else:
        flow_level = "极低"

    # 流量阶段
    if volume_burst >= 2.0 and price_momentum > 5:
        flow_phase = "爆发"
    elif volume_burst >= 1.5 or price_momentum > 3:
        flow_phase = "放大"
    elif volume_burst >= 1.0:
        flow_phase = "正常"
    else:
        flow_phase = "萎缩"

    # 文字描述
    vol_desc_word = "爆发" if volume_burst >= 2.0 else "放大" if volume_burst >= 1.5 else "正常" if volume_burst >= 1.0 else "萎缩"
    flow_desc = f"成交量{vol_desc_word}{volume_burst}倍，近5日涨幅{price_momentum:+.1f}%，波动率{volatility:.1f}%，流量处于{flow_phase}阶段"

    return {
        "flow_score": flow_score,
        "flow_level": flow_level,
        "volume_burst": volume_burst,
        "price_momentum": price_momentum,
        "volatility": volatility,
        "up_days": up_days,
        "flow_phase": flow_phase,
        "flow_desc": flow_desc,
    }


def analyze_timing(df: pd.DataFrame, flow_signals: dict) -> dict:
    """
    时机判断：基于流量信号判断当前处于什么阶段
    启动/加速/分歧/退潮
    """
    close = df["close"]
    volume = df["volume"]

    vb = flow_signals.get("volume_burst", 1.0)
    pm = flow_signals.get("price_momentum", 0)
    up_days = flow_signals.get("up_days", 0)
    vol = flow_signals.get("volatility", 1.0)

    # 量能变化趋势（近3日量vs前3日量）
    if len(volume) >= 8:
        recent_vol = float(volume.iloc[-3:].mean())
        prev_vol = float(volume.iloc[-6:-3].mean())
        vol_trend = recent_vol / prev_vol if prev_vol > 0 else 1.0
    else:
        vol_trend = 1.0

    # 价格趋势（近3日收盘价方向）
    if len(close) >= 4:
        price_trend_up = float(close.iloc[-1]) > float(close.iloc[-4])
    else:
        price_trend_up = True

    # 阶段判定
    if vb >= 2.0 and pm > 8 and up_days >= 3:
        phase = "加速"
        phase_score = 80
        phase_desc = "量能持续放大，价格快速上涨，流量处于加速阶段"
        hold_advice = "可持有，关注量能是否持续放大"
        exit_signal = "量能萎缩跌破前一日低点时减仓，跌破5日均线时清仓"
    elif vb >= 1.5 and pm > 3 and vol_trend > 1.2:
        phase = "启动"
        phase_score = 60
        phase_desc = "量能开始放大，价格刚开始上涨，流量从低位起来"
        hold_advice = "可轻仓试探，确认趋势后加仓"
        exit_signal = "跌破启动前低点止损"
    elif vb >= 2.0 and (pm < 3 or not price_trend_up) and vol > 3:
        phase = "分歧"
        phase_score = 40
        phase_desc = "量能最大但价格震荡分化，流量见顶信号"
        hold_advice = "谨慎持有，随时准备撤退"
        exit_signal = "放量滞涨即减仓，缩量破位即清仓"
    elif vb < 1.2 and pm < 0:
        phase = "退潮"
        phase_score = 20
        phase_desc = "量能萎缩，价格下跌，流量消散"
        hold_advice = "不宜参与，等待新流量信号"
        exit_signal = "已无仓位则观望，有仓位逢反弹减仓"
    else:
        phase = "启动"
        phase_score = 55
        phase_desc = "流量信号中性，暂处于启动初期或过渡阶段"
        hold_advice = "观望为主，等待更明确信号"
        exit_signal = "跌破近期低点止损"

    return {
        "phase": phase,
        "phase_score": phase_score,
        "phase_desc": phase_desc,
        "hold_advice": hold_advice,
        "exit_signal": exit_signal,
    }


def analyze_leadership(df: pd.DataFrame, stock_info: dict) -> dict:
    """
    龙头潜力评估：先涨为王、名字辨识度、市值适中、量价配合
    """
    close = df["close"]
    volume = df["volume"]
    market_cap = stock_info.get("market_cap")
    stock_name = stock_info.get("name", "")

    # 1) 先涨为王 — 近5日涨幅
    if len(close) >= 6:
        change_5d = round(((float(close.iloc[-1]) / float(close.iloc[-6])) - 1) * 100, 2)
    else:
        change_5d = 0
    first_mover = change_5d > 5  # 涨超5%算先涨

    # 2) 名字辨识度（简单规则匹配）
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

    # 3) 市值适中（50-500亿最佳）
    if market_cap is not None:
        cap_yi = market_cap if market_cap is not None else 0
        market_cap_fit = 30 <= cap_yi <= 800
    else:
        cap_yi = 0
        market_cap_fit = False

    # 4) 量价配合 — 价涨量增
    if len(close) >= 5 and len(volume) >= 5:
        price_up = float(close.iloc[-1]) > float(close.iloc[-5])
        vol_up = float(volume.iloc[-1]) > float(volume.iloc[-5])
        vol_price_harmony = price_up and vol_up
    else:
        vol_price_harmony = False

    # 综合评分
    score = 0
    # 先涨（35分）
    if change_5d > 15:
        score += 35
    elif change_5d > 10:
        score += 28
    elif change_5d > 5:
        score += 20
    elif change_5d > 0:
        score += 10
    else:
        score += 0

    # 名字辨识度（20分）
    if name_recognition == "高":
        score += 20
    elif name_recognition == "中等":
        score += 12
    else:
        score += 5

    # 市值（20分）
    score += 20 if market_cap_fit else 8

    # 量价配合（25分）
    score += 25 if vol_price_harmony else 5

    score = max(0, min(100, score))

    if score >= 80:
        leadership_level = "极高"
    elif score >= 65:
        leadership_level = "较高"
    elif score >= 45:
        leadership_level = "中等"
    elif score >= 25:
        leadership_level = "较低"
    else:
        leadership_level = "极低"

    # 描述
    parts = []
    if first_mover:
        parts.append(f"近5日涨{change_5d:.1f}%，先涨为王")
    else:
        parts.append(f"近5日涨{change_5d:.1f}%，暂未领先")
    parts.append(f"名字辨识度{name_recognition}")
    if market_cap_fit:
        parts.append(f"市值{cap_yi:.0f}亿，适中")
    else:
        parts.append(f"市值{cap_yi:.0f}亿，偏{'大' if cap_yi > 800 else '小'}")
    parts.append(f"量价{'配合' if vol_price_harmony else '背离'}")
    leadership_desc = "，".join(parts)

    return {
        "leadership_score": score,
        "leadership_level": leadership_level,
        "first_mover": first_mover,
        "name_recognition": name_recognition,
        "market_cap_fit": market_cap_fit,
        "vol_price_harmony": vol_price_harmony,
        "leadership_desc": leadership_desc,
    }


def analyze_flow(df: pd.DataFrame, stock_info: dict) -> dict:
    """
    综合流量分析：整合流量信号、时机判断、龙头潜力
    """
    flow_signals = analyze_flow_signals(df, stock_info)
    timing = analyze_timing(df, flow_signals)
    leadership = analyze_leadership(df, stock_info)

    # 综合分 = 流量信号40% + 时机30% + 龙头30%
    flow_total_score = round(
        flow_signals["flow_score"] * 0.4
        + timing["phase_score"] * 0.3
        + leadership["leadership_score"] * 0.3
    )
    flow_total_score = max(0, min(100, flow_total_score))

    if flow_total_score >= 75:
        flow_recommendation = "重点关注"
    elif flow_total_score >= 55:
        flow_recommendation = "可参与"
    elif flow_total_score >= 35:
        flow_recommendation = "观望"
    else:
        flow_recommendation = "回避"

    # 分析文字
    flow_analysis_text = (
        f"流量信号：{flow_signals['flow_desc']}\n"
        f"时机判断：{timing['phase_desc']}；{timing['hold_advice']}\n"
        f"龙头评估：{leadership['leadership_desc']}\n"
        f"退出信号：{timing['exit_signal']}"
    )

    return {
        "flow_signals": flow_signals,
        "timing": timing,
        "leadership": leadership,
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
                           summary: Optional[str] = None,
                           decision: Optional[str] = None,
                           risk_level: Optional[str] = None,
                           risk_score: Optional[int] = None) -> dict:
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
    }


def _build_strategy_results(signals: list, cost_price: Optional[float]) -> dict:
    grouped = {key: [] for key in STRATEGY_EXIT_DEFINITIONS}
    for signal in signals:
        grouped.setdefault(signal["strategy_key"], []).append(signal)

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

        strategies[key] = _build_strategy_result(
            key,
            grouped.get(key, []),
            summary=summary,
            decision=decision,
            risk_level=risk_level,
            risk_score=risk_score,
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
        missing_context.append("昨收价不可用，无法判断当日跌幅卖点。")

    if open_price and open_price > 0:
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
        missing_context.append("开盘价不可用，无法判断跌破开盘价与低开承接规则。")

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
    else:
        msg = "分钟数据不可用，无法判断冲高回落和盘中利润回吐。"
        if minute_error:
            msg += f" 数据源提示：{minute_error}"
        missing_context.append(msg)

    timing = flow_result.get("timing") or flow_result.get("flow_timing") or {}
    leadership = flow_result.get("leadership") or flow_result.get("flow_leadership") or {}
    flow_signals = flow_result.get("flow_signals") or {}
    phase = timing.get("phase")
    if phase in ["分歧", "退潮"] and not near_limit_up:
        signals.append(_make_exit_signal(
            "轮动/趋势质量",
            "high" if phase == "退潮" else "medium",
            "流量分歧退潮信号",
            f"流量阶段处于{phase}期，趋势质量和机会成本需要重新评估。",
            strategy_key="rotation_quality",
            action_type="sell" if phase == "退潮" else "reduce",
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

    strategies = _build_strategy_results(signals, cost_price)

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

    # ── 综合建议：取技术面和流量分析中更保守的那个 ──
    flow_rec = flow_result.get("flow_recommendation", "观望")

    conservative_map = {"强烈买入": 5, "买入": 4, "重点关注": 4, "可参与": 3, "观望": 3, "卖出": 2, "回避": 1, "强烈卖出": 1}
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
        conflict_note = f"\n⚠️ 注意：技术面建议「{tech_rec}」，但流量分析建议「{flow_rec}」，综合取更谨慎的「{base_recommendation}」"
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
    ld_parts = []
    if ld["first_mover"]:
        ld_parts.append("先涨")
    if ld["name_recognition"] in ["高", "中等"]:
        ld_parts.append("名字辨识")
    if ld["market_cap_fit"]:
        ld_parts.append("市值适中")
    if ld["vol_price_harmony"]:
        ld_parts.append("量价配合")
    else:
        ld_parts.append("量价背离")
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
        f"▎流量分析（{flow_result['flow_total_score']}分）",
        f"  流量阶段：{tm['phase']}期",
        f"  量能爆发度：{fs['volume_burst']}倍（成交量{vol_burst_word}）",
        f"  价格动量：{fs['price_momentum']:+.1f}%（近5日）",
        f"  波动率：{fs['volatility']:.1f}%（{vol_word}）",
        f"  龙头潜力：{ld['leadership_level']}（{'+'.join(ld_parts)}）",
        "",
        f"  ⚡ 流量信号：{fs['flow_desc']}",
        f"  ⏰ 时机判断：{tm['hold_advice']}",
        f"  🎯 龙头评估：{ld['leadership_desc']}",
        f"  🚪 退出信号：{tm['exit_signal']}",
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
        "flow_total_score": flow_result["flow_total_score"],
        "flow_recommendation": flow_result["flow_recommendation"],
        "flow_analysis_text": flow_result["flow_analysis_text"],
        # 新增：策略卖点提示
        "strategy_exit": strategy_exit,
    }
