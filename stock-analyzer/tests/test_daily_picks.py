import sys
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import main as api_main
from daily_picks import (
    DailyPickService,
    DataSourceUnavailable,
    EtfRotationStrategy,
    FirstBoardRelayStrategy,
    GLOBAL_ETF_POOL,
    LeaderChaseStrategy,
    calculate_momentum_score,
)
from models import (
    STRATEGY_META,
    get_daily_picks,
    get_strategy_run,
    init_db,
    save_strategy_run_results,
)


class FakeProvider:
    def __init__(self):
        self.today = "2026-05-22"
        self.previous = "2026-05-21"
        self.before_previous = "2026-05-20"
        self.auction_end_times = []

    def normalize_trade_date(self, trade_date=None):
        return trade_date or self.today

    def previous_trade_date(self, trade_date):
        return self.previous

    def before_previous_trade_date(self, trade_date):
        return self.before_previous

    def first_board_candidates(self, previous_date, before_previous_date):
        return [
            {
                "code": "600001",
                "name": "首板强势",
                "close": 10.0,
                "high_limit": 10.0,
                "pre_close": 9.1,
                "market_cap": 8_000_000_000,
                "float_market_cap": 6_000_000_000,
            }
        ]

    def limit_up_candidates(self, previous_date):
        return [
            {
                "code": "600002",
                "name": "三连龙头",
                "close": 12.0,
                "high_limit": 13.2,
                "low_limit": 10.8,
                "pre_close": 10.9,
                "volume": 1_000_000,
                "continue_count": 3,
            },
            {
                "code": "600003",
                "name": "低开弹性",
                "close": 8.0,
                "high_limit": 8.8,
                "low_limit": 7.38,
                "pre_close": 8.2,
                "volume": 800_000,
                "continue_count": 1,
            },
        ]

    def stock_history(self, code, end_date, days):
        if code == "600002":
            return pd.DataFrame({
                "date": pd.date_range("2026-05-17", periods=5),
                "open": [10.0, 10.3, 10.6, 11.0, 11.7],
                "high": [10.4, 10.7, 11.1, 11.9, 12.0],
                "low": [9.8, 10.1, 10.4, 10.8, 11.5],
                "close": [10.2, 10.5, 10.9, 11.4, 12.0],
                "volume": [900_000, 920_000, 950_000, 970_000, 1_000_000],
                "amount": [100_000_000 for _ in range(5)],
            })
        if code == "600003" and days < 60:
            return pd.DataFrame({
                "date": pd.date_range("2026-05-17", periods=5),
                "open": [7.5, 7.7, 7.9, 8.0, 8.1],
                "high": [7.8, 8.0, 8.2, 8.3, 8.4],
                "low": [7.4, 7.6, 7.8, 7.9, 8.0],
                "close": [7.6, 7.8, 8.0, 8.1, 8.2],
                "volume": [700_000, 720_000, 740_000, 760_000, 800_000],
                "amount": [100_000_000 for _ in range(5)],
            })
        if code == "600003" and days >= 60:
            return pd.DataFrame({
                "date": pd.date_range("2026-03-01", periods=60),
                "open": [6 + i * 0.05 for i in range(60)],
                "high": [7 + i * 0.08 for i in range(60)],
                "low": [5 + i * 0.02 for i in range(60)],
                "close": [5.2 + i * 0.04 for i in range(60)],
                "volume": [100_000 + i for i in range(60)],
                "amount": [100_000_000 for _ in range(60)],
            })
        return pd.DataFrame({
            "date": pd.date_range("2026-04-17", periods=35),
            "open": [
                *([6.0] * 29),
                7.00,
                7.10,
                7.20,
                7.35,
                7.45,
                10.00,
            ],
            "high": [
                *([6.2] * 29),
                8.90,
                9.00,
                9.20,
                9.40,
                9.60,
                10.00,
            ],
            "low": [
                *([5.9] * 29),
                8.60,
                8.70,
                8.90,
                9.10,
                9.30,
                9.70,
            ],
            "close": [
                *([6.1] * 29),
                7.05,
                7.15,
                7.30,
                7.45,
                7.60,
                10.00,
            ],
            "volume": [
                *([110_000] * 29),
                120_000,
                130_000,
                140_000,
                100_000,
                150_000,
                330_000,
            ],
            "amount": [
                *([100_000_000] * 34),
                800_000_000,
            ],
        })

    def call_auction(self, code, trade_date, start_time, end_time):
        self.auction_end_times.append(end_time)
        if code == "600001":
            return {"price": 10.35, "volume": 11_000}
        if code == "600002":
            return {"price": 12.60, "volume": 20_000}
        if code == "600003":
            return {"price": 7.84, "volume": 20_000}
        return None

    def market_sentiment(self, trade_date):
        return {"state": "bull", "summary": "测试牛市", "metrics": {}}


def clear_dependency_overrides():
    api_main.app.dependency_overrides.clear()


def test_first_board_strategy_uses_own_pool_and_marks_reasons():
    strategy = FirstBoardRelayStrategy(FakeProvider())

    result = strategy.run("2026-05-22")

    assert result.total_scanned == 1
    assert len(result.recommendations) == 1
    pick = result.recommendations[0]
    assert pick.strategy_key == "first_board_relay"
    assert pick.code == "600001"
    assert pick.mode == "首板接力"
    assert any("竞价涨幅" in reason for reason in pick.reasons)
    assert pick.metrics["auction_volume_ratio"] >= 0.03


def test_first_board_requires_market_cap_context():
    provider = FakeProvider()
    provider.first_board_candidates = lambda previous_date, before_previous_date: [
        {
            "code": "600001",
            "name": "缺市值",
            "close": 10.0,
            "high_limit": 10.0,
            "float_market_cap": 6_000_000_000,
        },
        {
            "code": "600004",
            "name": "缺流通市值",
            "close": 10.0,
            "high_limit": 10.0,
            "market_cap": 8_000_000_000,
        },
    ]
    strategy = FirstBoardRelayStrategy(provider)

    result = strategy.run("2026-05-22")

    assert result.total_scanned == 2
    assert result.recommendations == []


def test_leader_chase_uses_0925_auction_cutoff_and_modes():
    provider = FakeProvider()
    strategy = LeaderChaseStrategy(provider)

    result = strategy.run("2026-05-22")

    assert provider.auction_end_times == ["09:25:00", "09:25:00"]
    assert [pick.code for pick in result.recommendations] == ["600002", "600003"]
    assert result.recommendations[0].mode == "龙追"
    assert result.recommendations[1].mode == "低开反弹"


def test_leader_chase_rejects_missing_previous_volume():
    provider = FakeProvider()

    def zero_volume_history(code, end_date, days):
        return pd.DataFrame({
            "date": pd.date_range("2026-05-17", periods=5),
            "open": [10, 10.3, 10.6, 11, 11.7],
            "high": [10.4, 10.7, 11.1, 11.9, 12],
            "low": [9.8, 10.1, 10.4, 10.8, 11.5],
            "close": [10.2, 10.5, 10.9, 11.4, 12],
            "volume": [900000, 920000, 950000, 970000, 0],
            "amount": [100_000_000 for _ in range(5)],
        })

    provider.stock_history = zero_volume_history
    strategy = LeaderChaseStrategy(provider)

    result = strategy.run("2026-05-22")

    assert result.recommendations == []


def test_momentum_score_rewards_smooth_uptrend():
    uptrend = pd.Series([10 + i * 0.2 for i in range(30)])

    score, annualized, r_squared = calculate_momentum_score(uptrend, 25)

    assert score > 0
    assert annualized > 0
    assert r_squared > 0.9


class FakeEtfProvider:
    def __init__(self, weak=False, passing=True):
        self.weak = weak
        self.passing = passing
        self.previous = "2026-05-21"

    def previous_trade_date(self, trade_date):
        return self.previous

    def a_share_weak_state(self, trade_date):
        return {"is_weak": self.weak, "above_count": 1 if self.weak else 4, "below_count": 3 if self.weak else 0, "details": {}}

    def etf_spot_map(self):
        code = GLOBAL_ETF_POOL[0] if self.weak else "510300"
        return {
            code: {"name": "测试ETF", "price": 13.6 if self.passing else 8.0, "volume": 1200},
            "511880": {"name": "银华日利", "price": 100.0, "volume": 1000},
        }

    def etf_history(self, code, end_date, days):
        if code not in set(GLOBAL_ETF_POOL + ["510300"]):
            return pd.DataFrame()
        if not self.passing:
            return pd.DataFrame({
                "date": pd.date_range("2026-03-01", periods=70),
                "close": [10 - i * 0.03 for i in range(70)],
                "volume": [1000 + i for i in range(70)],
            })
        return pd.DataFrame({
            "date": pd.date_range("2026-03-01", periods=70),
            "close": [10 + i * 0.05 for i in range(70)],
            "volume": [1000 + i for i in range(70)],
        })


def test_etf_rotation_uses_global_pool_in_weak_state_and_is_recommendation_only():
    strategy = EtfRotationStrategy(FakeEtfProvider(weak=True, passing=True))

    result = strategy.run("2026-05-22")

    assert result.total_scanned == len(GLOBAL_ETF_POOL)
    assert result.recommendations
    assert result.recommendations[0].code in GLOBAL_ETF_POOL
    assert any("不下单" in risk for risk in result.recommendations[0].risks)
    assert STRATEGY_META["etf_rotation"]["scheduled_time"] == "13:10"


def test_etf_rotation_falls_back_to_defensive_observation_when_no_etf_passes():
    strategy = EtfRotationStrategy(FakeEtfProvider(weak=False, passing=False))

    result = strategy.run("2026-05-22")

    assert result.recommendations[0].code == "511880"
    assert result.recommendations[0].mode == "防御观察"
    assert result.recommendations[0].data_status["warning"]


def test_etf_rotation_marks_source_outage_as_failed_run():
    class OutageEtfProvider(FakeEtfProvider):
        def etf_spot_map(self):
            raise DataSourceUnavailable("ETF实时行情不可用: upstream timeout")

    strategy = EtfRotationStrategy(OutageEtfProvider())

    result = strategy.run("2026-05-22")

    assert result.status == "failed"
    assert result.error and "ETF实时行情不可用" in result.error
    assert result.recommendations == []


def test_strategy_run_results_are_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "stock.db"
    monkeypatch.setattr("models.DB_PATH", str(db_path))
    init_db()

    run_id = save_strategy_run_results(
        "first_board_relay",
        "2026-05-22",
        "success",
        3,
        [
            {
                "strategy_key": "first_board_relay",
                "strategy_name": "低位3连阳首板接力",
                "code": "600001",
                "name": "首板强势",
                "rank": 1,
                "score": 83.0,
                "mode": "首板接力",
                "reasons": ["首次写入"],
                "risks": [],
                "metrics": {"auction_pct": 3.5},
                "data_status": {"warning": None},
            }
        ],
    )
    save_strategy_run_results(
        "first_board_relay",
        "2026-05-22",
        "success",
        5,
        [
            {
                "strategy_key": "first_board_relay",
                "strategy_name": "低位3连阳首板接力",
                "code": "600002",
                "name": "替换结果",
                "rank": 1,
                "score": 91.0,
                "mode": "首板接力",
                "reasons": ["第二次覆盖"],
                "risks": [],
                "metrics": {"auction_pct": 4.5},
                "data_status": {"warning": None},
            }
        ],
    )

    run = get_strategy_run(run_id)
    grouped = get_daily_picks("2026-05-22")

    assert run["strategy_key"] == "first_board_relay"
    assert grouped["groups"][0]["total_scanned"] == 5
    assert len(grouped["groups"][0]["recommendations"]) == 1
    assert grouped["groups"][0]["recommendations"][0]["code"] == "600002"


def test_daily_picks_api_reads_existing_results(tmp_path, monkeypatch):
    db_path = tmp_path / "stock.db"
    monkeypatch.setattr("models.DB_PATH", str(db_path))
    monkeypatch.setattr(api_main.models, "DB_PATH", str(db_path), raising=False)
    init_db()
    save_strategy_run_results(
        "leader_chase",
        "2026-05-22",
        "success",
        2,
        [
            {
                "strategy_key": "leader_chase",
                "strategy_name": "龙头追击",
                "code": "600002",
                "name": "三连龙头",
                "rank": 1,
                "score": 88.0,
                "mode": "龙追",
                "reasons": ["连板数3"],
                "risks": [],
                "metrics": {"continue_count": 3},
                "data_status": {"warning": None},
            }
        ],
    )

    async def fake_current_user():
        return {"id": 1, "username": "tester", "email": "t@example.com", "role": "user", "credits": 1}

    api_main.app.dependency_overrides[api_main.get_current_user] = fake_current_user
    client = TestClient(api_main.app)
    try:
        response = client.get("/api/daily-picks?date=2026-05-22")
    finally:
        clear_dependency_overrides()

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["trade_date"] == "2026-05-22"
    leader = [group for group in body["groups"] if group["strategy_key"] == "leader_chase"][0]
    assert leader["recommendations"][0]["code"] == "600002"
