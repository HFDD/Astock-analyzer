import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import main as api_main
from analyzer import analyze_stock, analyze_strategy_exit, apply_strategy_exit_adjustment
from data_fetcher import classify_instrument


STRATEGY_KEYS = {
    "position_risk",
    "first_board_relay",
    "leader_chase",
    "trend_break",
    "rotation_quality",
}
ALLOWED_ACTION_TYPES = {"stop_loss", "take_profit", "sell", "reduce", "hold", "watch"}
DECISION_LABELS = {
    "stop_loss": "止损",
    "take_profit": "止盈",
    "sell": "卖出",
    "reduce": "减仓",
    "hold": "继续持有",
    "watch": "观察",
}


def make_daily(closes):
    rows = []
    for i, close in enumerate(closes):
        rows.append({
            "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": 100000 + i,
        })
    return pd.DataFrame(rows)


def assert_strategy_contract(result):
    assert set(result["strategies"]) == STRATEGY_KEYS
    assert "trade_eligibility" in result
    for key, strategy in result["strategies"].items():
        assert strategy["key"] == key
        for field in ["name", "risk_level", "risk_score", "decision", "decision_label", "summary", "signals"]:
            assert field in strategy
        assert strategy["decision_label"] == DECISION_LABELS[strategy["decision"]]
        for signal in strategy["signals"]:
            assert signal["strategy_key"] == key
            assert signal["action_type"] in ALLOWED_ACTION_TYPES
    for signal in result["signals"]:
        assert signal["strategy_key"] in STRATEGY_KEYS
        assert signal["action_type"] in ALLOWED_ACTION_TYPES
    rendered_strategy_text = str(result["strategies"])
    assert "T+1" not in rendered_strategy_text
    assert "T＋1" not in rendered_strategy_text


def clear_dependency_overrides():
    api_main.app.dependency_overrides.clear()


def test_trade_dates_api_requires_auth_and_does_not_deduct_credits(monkeypatch):
    df = make_daily([10, 10.2, 10.4])
    daily_calls = []
    deduct_calls = []

    def fake_get_stock_daily(code, days=120):
        daily_calls.append({"code": code, "days": days})
        return df

    def fake_deduct_credits(*args, **kwargs):
        deduct_calls.append((args, kwargs))
        raise AssertionError("trade-dates endpoint must not deduct credits")

    monkeypatch.setattr(api_main, "get_stock_daily", fake_get_stock_daily)
    monkeypatch.setattr(api_main, "deduct_credits", fake_deduct_credits)
    client = TestClient(api_main.app)

    clear_dependency_overrides()
    unauthenticated = client.get("/api/stock/000001/trade-dates")

    assert unauthenticated.status_code == 401
    assert daily_calls == []

    auth_calls = []

    async def fake_current_user():
        auth_calls.append(True)
        return {"id": 7, "username": "tester", "email": "t@example.com", "role": "user", "credits": 0}

    api_main.app.dependency_overrides[api_main.get_current_user] = fake_current_user
    try:
        response = client.get("/api/stock/000001/trade-dates")
    finally:
        clear_dependency_overrides()

    assert response.status_code == 200
    assert auth_calls == [True]
    assert daily_calls == [{"code": "000001", "days": 120}]
    assert deduct_calls == []
    body = response.json()
    assert body["data"]["stock_code"] == "000001"
    assert body["data"]["trade_dates"] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert body["data"]["latest_trade_date"] == "2026-01-03"


def test_stock_api_passes_buy_date_and_cost_price_to_analyze_stock(monkeypatch):
    df = make_daily([10, 10.2, 10.4])
    analyze_calls = []
    deduct_calls = []
    save_calls = []

    monkeypatch.setattr(api_main, "get_stock_daily", lambda code, days=120: df)
    monkeypatch.setattr(api_main, "get_stock_info", lambda code: {"name": "测试股", "price": 10.4})
    monkeypatch.setattr(api_main, "get_stock_minute", lambda code: (pd.DataFrame([{"high": 10.5}]), "minute warning"))

    def fake_analyze_stock(df_arg, stock_info, stock_code, minute_df=None, cost_price=None, minute_error=None, buy_date=None):
        analyze_calls.append({
            "df": df_arg,
            "stock_info": stock_info,
            "stock_code": stock_code,
            "minute_df": minute_df,
            "cost_price": cost_price,
            "minute_error": minute_error,
            "buy_date": buy_date,
        })
        return {"stock_code": stock_code, "analysis_text": "mocked"}

    def fake_deduct_credits(user_id, amount):
        deduct_calls.append({"user_id": user_id, "amount": amount})

    def fake_save_analysis(user_id, result):
        save_calls.append({"user_id": user_id, "result": result})
        return 99

    async def fake_current_user():
        return {"id": 7, "username": "tester", "email": "t@example.com", "role": "user", "credits": 3}

    monkeypatch.setattr(api_main, "analyze_stock", fake_analyze_stock)
    monkeypatch.setattr(api_main, "deduct_credits", fake_deduct_credits)
    monkeypatch.setattr(api_main, "save_analysis", fake_save_analysis)
    api_main.app.dependency_overrides[api_main.get_current_user] = fake_current_user
    client = TestClient(api_main.app)

    try:
        response = client.get("/api/stock/000001?cost_price=9.75&buy_date=2026-01-02")
    finally:
        clear_dependency_overrides()

    assert response.status_code == 200
    assert len(analyze_calls) == 1
    call = analyze_calls[0]
    assert call["df"] is df
    assert call["stock_code"] == "000001"
    assert call["stock_info"] == {"name": "测试股", "price": 10.4}
    assert call["cost_price"] == 9.75
    assert call["buy_date"] == "2026-01-02"
    assert call["minute_error"] == "minute warning"
    assert isinstance(call["minute_df"], pd.DataFrame)
    assert deduct_calls == [{"user_id": 7, "amount": 1}]
    assert len(save_calls) == 1
    body = response.json()
    assert body["data"]["id"] == 99
    assert body["data"]["credits_remaining"] == 2
    assert body["data"]["kline_data"][-1]["date"] == "2026-01-03"


def test_cost_price_hard_stop_loss_marks_high_risk():
    df = make_daily([10, 10.2, 10.4, 10.3, 10.1, 9.2])
    stock_info = {"price": 9.2, "open": 9.9, "pre_close": 10.1, "high_limit": 11.11, "low_limit": 9.09}

    result = analyze_strategy_exit(df, stock_info, minute_df=pd.DataFrame(), cost_price=10.0, flow_result={})

    assert_strategy_contract(result)
    assert result["risk_level"] == "high"
    assert result["recommendation_adjustment"] in {"downgrade_one", "downgrade_two"}
    assert any(
        signal["title"] == "持仓亏损超过7%"
        and signal["strategy_key"] == "position_risk"
        and signal["action_type"] == "stop_loss"
        for signal in result["signals"]
    )
    assert result["position"]["cost_price"] == 10.0


def test_missing_cost_price_keeps_holding_rules_as_context_only():
    df = make_daily([10, 10.2, 10.4, 10.3, 10.1, 9.2])
    stock_info = {"price": 9.2, "open": 9.9, "pre_close": 10.1, "high_limit": 11.11, "low_limit": 9.09}

    result = analyze_strategy_exit(df, stock_info, minute_df=pd.DataFrame(), flow_result={})

    assert_strategy_contract(result)
    assert result["position"]["cost_price"] is None
    assert any("未填写持仓成本" in item for item in result["missing_context"])
    assert not any(signal["source"] == "持仓风控" for signal in result["signals"])
    assert result["strategies"]["position_risk"]["decision"] == "watch"


def test_profit_giveback_uses_cost_price_and_minute_high():
    df = make_daily([10, 10.3, 10.6, 10.8, 11.0, 10.7])
    stock_info = {"price": 10.7, "open": 10.8, "pre_close": 11.0, "high_limit": 12.1, "low_limit": 9.9}
    minute_df = pd.DataFrame([
        {"day": "2026-01-06 09:31:00", "close": 11.8, "high": 11.8, "low": 11.7, "open": 11.7, "volume": 1000},
        {"day": "2026-01-06 10:30:00", "close": 10.7, "high": 10.8, "low": 10.6, "open": 10.8, "volume": 1000},
    ])

    result = analyze_strategy_exit(df, stock_info, minute_df=minute_df, cost_price=9.0, flow_result={})

    assert_strategy_contract(result)
    assert result["risk_level"] == "high"
    assert any(
        signal["title"] == "利润回吐保护"
        and signal["strategy_key"] == "position_risk"
        and signal["action_type"] == "take_profit"
        for signal in result["signals"]
    )
    assert any(signal["title"] == "冲高回落风险" for signal in result["signals"])


def test_near_limit_up_offsets_overbought_but_not_hard_stop():
    df = make_daily([10, 10.5, 11, 11.5, 12, 12.95])
    stock_info = {"price": 12.95, "open": 12.0, "pre_close": 12.0, "high_limit": 13.0, "low_limit": 10.8}

    result = analyze_strategy_exit(df, stock_info, minute_df=pd.DataFrame(), cost_price=10.0, flow_result={"timing": {"phase": "分歧"}})

    assert_strategy_contract(result)
    assert any(signal["title"] == "接近涨停强势持有" for signal in result["signals"])
    assert not any(signal.get("priority") == "high" and signal["title"] == "流量分歧退潮信号" for signal in result["signals"])


def test_buy_date_equal_latest_moves_trade_rule_out_of_strategy_prompts():
    df = make_daily([10, 10.2, 10.4, 10.3, 10.1, 9.2])
    stock_info = {"price": 9.2, "open": 9.9, "pre_close": 10.1, "high_limit": 11.11, "low_limit": 9.09}

    result = analyze_strategy_exit(
        df,
        stock_info,
        minute_df=pd.DataFrame(),
        cost_price=10.0,
        flow_result={},
        buy_date="2026-01-06",
    )

    assert_strategy_contract(result)
    assert "t_plus_one" not in result["strategies"]
    assert not any(signal["strategy_key"] == "t_plus_one" for signal in result["signals"])
    eligibility = result["trade_eligibility"]
    assert eligibility["buy_date"] == "2026-01-06"
    assert eligibility["latest_trade_date"] == "2026-01-06"
    assert eligibility["can_sell_today"] is False
    assert eligibility["guidance_scope"] == "next_trading_day"
    assert "下一交易日" in eligibility["summary"]
    assert result["risk_level"] == "high"
    assert any(signal["title"] == "持仓亏损超过7%" for signal in result["signals"])


def test_buy_date_before_latest_allows_today_sell_guidance():
    df = make_daily([10, 10.2, 10.4, 10.3, 10.1, 9.2])
    stock_info = {"price": 9.2, "open": 9.9, "pre_close": 10.1, "high_limit": 11.11, "low_limit": 9.09}

    result = analyze_strategy_exit(
        df,
        stock_info,
        minute_df=pd.DataFrame(),
        cost_price=10.0,
        flow_result={},
        buy_date="2026-01-05",
    )

    assert_strategy_contract(result)
    eligibility = result["trade_eligibility"]
    assert eligibility["can_sell_today"] is True
    assert eligibility["guidance_scope"] == "today"
    assert not any(signal["strategy_key"] == "t_plus_one" for signal in result["signals"])


def test_invalid_buy_date_raises_clear_value_error():
    df = make_daily([10, 10.2, 10.4, 10.3, 10.1, 9.2])
    stock_info = {"price": 9.2, "open": 9.9, "pre_close": 10.1, "high_limit": 11.11, "low_limit": 9.09}

    with pytest.raises(ValueError, match=r"buy_date 2025-12-31 is not a trading date"):
        analyze_strategy_exit(
            df,
            stock_info,
            minute_df=pd.DataFrame(),
            cost_price=10.0,
            flow_result={},
            buy_date="2025-12-31",
        )


def test_strategy_exit_strategies_always_contains_stable_keys():
    df = make_daily([10, 10.2, 10.4, 10.6, 10.8, 11])
    stock_info = {"price": 11, "open": 10.8, "pre_close": 10.8, "high_limit": 11.88, "low_limit": 9.72}

    result = analyze_strategy_exit(df, stock_info, minute_df=pd.DataFrame(), cost_price=10.0, flow_result={})

    assert_strategy_contract(result)


def test_analyze_stock_same_day_buy_shows_next_trading_day_exit_plan():
    df = make_daily([10, 10.2, 10.4, 10.3, 10.1, 9.2])
    stock_info = {
        "code": "000001",
        "name": "测试股",
        "price": 9.2,
        "open": 9.9,
        "pre_close": 10.1,
        "high_limit": 11.11,
        "low_limit": 9.09,
        "instrument_type": "stock",
        "is_etf": False,
    }

    result = analyze_stock(
        df,
        stock_info,
        "000001",
        minute_df=pd.DataFrame(),
        cost_price=10.0,
        flow_result=None,
        buy_date="2026-01-06",
    )

    assert result["strategy_exit"]["trade_eligibility"]["can_sell_today"] is False
    assert result["strategy_exit"]["trade_eligibility"]["guidance_scope"] == "next_trading_day"
    assert result["recommendation"] == "观望"
    assert result["action"]["operation"] == "观望"
    assert result["action"]["position"] == "原仓观察"
    assert "下一交易日" in result["summary_text"]
    assert "下一交易日" in result["analysis_text"]


def test_instrument_classification_detects_etf_and_stock():
    assert classify_instrument("510300", "沪深300ETF") == "etf"
    assert classify_instrument("159915", "创业板ETF") == "etf"
    assert classify_instrument("600519", "贵州茅台") == "stock"


def test_etf_skips_individual_stock_exit_prompts():
    df = make_daily([10, 10.2, 10.4, 10.3, 10.1, 9.2])
    stock_info = {
        "code": "510300",
        "name": "沪深300ETF",
        "price": 9.2,
        "open": 9.9,
        "pre_close": 10.1,
        "instrument_type": "etf",
        "is_etf": True,
    }

    result = analyze_stock(
        df,
        stock_info,
        "510300",
        minute_df=pd.DataFrame(),
        cost_price=10.0,
        buy_date="2026-01-06",
    )

    assert result["instrument_type"] == "etf"
    assert result["is_etf"] is True
    assert result["strategy_exit"]["strategies"] == {}
    assert result["strategy_exit"]["signals"] == []
    assert "ETF 不参与个股卖点策略" in result["strategy_exit"]["summary"]


def test_adjustment_downgrades_recommendation_and_keeps_base():
    strategy_exit = {"recommendation_adjustment": "downgrade_two", "risk_level": "high"}
    adjusted = apply_strategy_exit_adjustment("买入", strategy_exit)

    assert adjusted["base_recommendation"] == "买入"
    assert adjusted["recommendation"] == "卖出"
    assert adjusted["strategy_adjusted"] is True
