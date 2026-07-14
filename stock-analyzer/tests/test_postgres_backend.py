import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import models


def test_postgres_paramstyle_conversion_skips_quoted_question_marks():
    sql = "SELECT * FROM users WHERE username = ? AND note = '?' AND email = ?"

    converted = models._postgres_paramstyle(sql)

    assert converted == "SELECT * FROM users WHERE username = %s AND note = '?' AND email = %s"


def test_postgres_connection_pool_reuses_released_connections(monkeypatch):
    class FakeConnection:
        closed = False

        def rollback(self):
            pass

        def close(self):
            self.closed = True

    class FakePsycopg:
        def __init__(self):
            self.connections = []

        def connect(self, *args, **kwargs):
            conn = FakeConnection()
            self.connections.append(conn)
            return conn

    fake_psycopg = FakePsycopg()
    monkeypatch.setattr(models, "psycopg", fake_psycopg)
    monkeypatch.setattr(models, "dict_row", object())
    monkeypatch.setattr(models, "_POSTGRES_POOL", None)
    monkeypatch.setenv("STOCK_ANALYZER_DATABASE_URL", "postgresql://example/session-pool")
    monkeypatch.setenv("STOCK_ANALYZER_DB_POOL_SIZE", "1")

    first = models.get_connection()
    raw_first = first._conn
    first.close()
    second = models.get_connection()

    assert second._conn is raw_first
    assert len(fake_psycopg.connections) == 1
    second.close()


def test_recent_trading_cache_stays_fresh_over_weekend():
    import datetime as dt
    import data_fetcher

    friday_close = dt.datetime(2026, 5, 22)
    sunday = dt.datetime(2026, 5, 24, 12, 0)
    monday = dt.datetime(2026, 5, 25, 12, 0)

    assert data_fetcher._is_recent_trading_cache(friday_close, sunday) is True
    assert data_fetcher._is_recent_trading_cache(friday_close, monday) is False


def test_postgres_schema_contains_project_tables():
    schema = models.POSTGRES_SCHEMA_PATH.read_text(encoding="utf-8")

    for table_name in [
        "users",
        "user_tokens",
        "watchlist",
        "analysis_history",
        "strategy_runs",
        "strategy_recommendations",
        "stock_cache",
        "attention_heat_snapshots",
        "user_quant_strategies",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in schema


def test_strategy_meta_contains_qlib_model_group():
    assert models.STRATEGY_META["qlib_model"] == {
        "name": "AI模型选股",
        "description": "Qlib训练流水线生成的样本外预测分数。",
        "scheduled_time": "15:30",
    }


def test_watchlist_ordering_does_not_compare_postgres_timestamp_to_empty_string(monkeypatch):
    class FakeCursor:
        def fetchall(self):
            return []

    class FakeConnection:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, _params):
            self.sql = sql
            return FakeCursor()

        def close(self):
            pass

    connection = FakeConnection()
    monkeypatch.setattr(models, "get_connection", lambda: connection)

    assert models.get_watchlist(1) == []
    assert "w.pinned_at = ''" not in connection.sql
    assert "buy_date <> ''" not in connection.sql
    assert "strategy_exit_json <> ''" not in connection.sql
    assert "CASE WHEN w.pinned_at IS NULL THEN 1 ELSE 0 END" in connection.sql


def test_attention_heat_snapshots_round_trip_through_database(tmp_path, monkeypatch):
    db_path = tmp_path / "stock.db"
    monkeypatch.setattr(models, "DB_PATH", str(db_path))
    monkeypatch.delenv("STOCK_ANALYZER_DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    models.init_db()

    first = {
        "timestamp": 1779550073,
        "date": "2026-05-23",
        "topics": {"topic:光模块": {"topic_name": "光模块", "entry_power_score": 76.0}},
    }
    second = {
        "timestamp": 1779636473,
        "date": "2026-05-24",
        "topics": {"topic:商业航天": {"topic_name": "商业航天", "entry_power_score": 78.0}},
    }

    first_id = models.save_attention_heat_snapshot(first)
    second_id = models.save_attention_heat_snapshot(second)
    snapshots = models.get_attention_heat_snapshots(limit=20)

    assert first_id > 0
    assert second_id > first_id
    assert snapshots == [first, second]
