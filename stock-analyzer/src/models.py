"""
数据库操作层
SQLite/PostgreSQL连接管理 + 用户CRUD + Token管理 + 分析记录 + 自选股 + 股票缓存
"""

import sqlite3
import os
import json
import queue
import threading
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

try:  # PostgreSQL is optional for local SQLite-only development.
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.errors import UniqueViolation
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - exercised only when psycopg is absent
    psycopg = None
    dict_row = None
    UniqueViolation = sqlite3.IntegrityError
    Jsonb = None

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "stock.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
POSTGRES_SCHEMA_PATH = Path(os.path.dirname(__file__)) / "schema.postgres.sql"
_POSTGRES_POOL = None
_POSTGRES_POOL_LOCK = threading.Lock()


def _database_url() -> Optional[str]:
    """Return the configured PostgreSQL URL, preferring the Supabase session-pool setting."""
    if os.getenv("STOCK_ANALYZER_DB_BACKEND", "").lower() == "sqlite":
        return None
    for key in ("STOCK_ANALYZER_DATABASE_URL", "SUPABASE_DB_URL", "DATABASE_URL"):
        value = os.getenv(key)
        if value and value.startswith(("postgres://", "postgresql://")):
            return value
    return None


def is_postgres_enabled() -> bool:
    return _database_url() is not None


def _postgres_paramstyle(sql: str) -> str:
    """Convert SQLite ``?`` parameters to psycopg ``%s`` without touching quoted text."""
    converted = []
    in_single_quote = False
    in_double_quote = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'" and not in_double_quote:
            converted.append(char)
            if in_single_quote and index + 1 < len(sql) and sql[index + 1] == "'":
                converted.append(sql[index + 1])
                index += 2
                continue
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            converted.append(char)
            in_double_quote = not in_double_quote
        elif char == "?" and not in_single_quote and not in_double_quote:
            converted.append("%s")
        else:
            converted.append(char)
        index += 1
    return "".join(converted)


class PostgresConnectionPool:
    """Small synchronous connection pool for Supabase's session pooler."""

    def __init__(self, url: str):
        self.url = url
        self.max_size = max(1, int(os.getenv("STOCK_ANALYZER_DB_POOL_SIZE", "4")))
        self.acquire_timeout = float(os.getenv("STOCK_ANALYZER_DB_POOL_TIMEOUT", "10"))
        self.connect_timeout = int(os.getenv("STOCK_ANALYZER_DB_CONNECT_TIMEOUT", "10"))
        self._available = queue.Queue(maxsize=self.max_size)
        self._lock = threading.Lock()
        self._created = 0

    def _connect(self):
        if psycopg is None:
            raise RuntimeError("PostgreSQL backend requires psycopg. Install requirements.txt first.")
        return psycopg.connect(
            self.url,
            row_factory=dict_row,
            prepare_threshold=None,
            application_name="stock-analyzer-fastapi",
            connect_timeout=self.connect_timeout,
        )

    def acquire(self):
        try:
            conn = self._available.get_nowait()
            if not getattr(conn, "closed", False):
                return conn
            self._discard_created_connection()
        except queue.Empty:
            pass

        with self._lock:
            if self._created < self.max_size:
                self._created += 1
                should_create = True
            else:
                should_create = False

        if should_create:
            try:
                return self._connect()
            except Exception:
                self._discard_created_connection()
                raise

        try:
            conn = self._available.get(timeout=self.acquire_timeout)
        except queue.Empty as exc:
            raise RuntimeError("数据库连接池繁忙，请稍后重试") from exc
        if getattr(conn, "closed", False):
            self._discard_created_connection()
            return self.acquire()
        return conn

    def release(self, conn) -> None:
        if getattr(conn, "closed", False):
            self._discard_created_connection()
            return
        try:
            conn.rollback()
        except Exception:
            self.discard(conn)
            return
        try:
            self._available.put_nowait(conn)
        except queue.Full:
            self.discard(conn)

    def discard(self, conn) -> None:
        try:
            conn.close()
        except Exception:
            pass
        self._discard_created_connection()

    def _discard_created_connection(self) -> None:
        with self._lock:
            self._created = max(0, self._created - 1)


def _postgres_pool() -> PostgresConnectionPool:
    global _POSTGRES_POOL
    database_url = _database_url()
    if not database_url:
        raise RuntimeError("PostgreSQL backend is not configured")
    with _POSTGRES_POOL_LOCK:
        if _POSTGRES_POOL is None or _POSTGRES_POOL.url != database_url:
            _POSTGRES_POOL = PostgresConnectionPool(database_url)
        return _POSTGRES_POOL


class PostgresConnection:
    """Tiny adapter that lets the existing SQLite-style data layer use psycopg."""

    dialect = "postgres"

    def __init__(self, conn, pool: PostgresConnectionPool):
        self._conn = conn
        self._pool = pool
        self._closed = False

    def execute(self, sql: str, params: Optional[Union[tuple, list]] = None):
        return self._conn.execute(_postgres_paramstyle(sql), params)

    def executemany(self, sql: str, params_seq):
        with self._conn.cursor() as cursor:
            cursor.executemany(_postgres_paramstyle(sql), params_seq)
            return cursor

    def commit(self):
        self._conn.commit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._pool.release(self._conn)


def _is_postgres_connection(conn) -> bool:
    return getattr(conn, "dialect", "") == "postgres"


def _insert_and_get_id(conn, sql: str, params: tuple) -> int:
    if _is_postgres_connection(conn):
        cursor = conn.execute(f"{sql.rstrip().rstrip(';')} RETURNING id", params)
        row = cursor.fetchone()
        return int(row["id"])
    cursor = conn.execute(sql, params)
    return int(cursor.lastrowid)


def _execute_postgres_schema(conn) -> None:
    for statement in POSTGRES_SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
        sql = statement.strip()
        if sql:
            conn.execute(sql)


def _ensure_postgres_analysis_history_columns(conn) -> None:
    migrations = [
        "ALTER TABLE analysis_history ADD COLUMN IF NOT EXISTS strategy_exit_json JSONB",
        "ALTER TABLE analysis_history ADD COLUMN IF NOT EXISTS retail_direction TEXT",
        "ALTER TABLE analysis_history ADD COLUMN IF NOT EXISTS retail_score DOUBLE PRECISION",
        "ALTER TABLE analysis_history ADD COLUMN IF NOT EXISTS flow_recommendation TEXT",
        "ALTER TABLE analysis_history ADD COLUMN IF NOT EXISTS exit_risk_score DOUBLE PRECISION",
        "ALTER TABLE analysis_history ADD COLUMN IF NOT EXISTS strategy_risk_level TEXT",
        "ALTER TABLE analysis_history ADD COLUMN IF NOT EXISTS strategy_risk_score DOUBLE PRECISION",
    ]
    for ddl in migrations:
        conn.execute(ddl)


def _ensure_postgres_watchlist_columns(conn) -> None:
    migrations = [
        "ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ",
    ]
    for ddl in migrations:
        conn.execute(ddl)


def get_connection():
    """获取数据库连接，启用外键约束"""
    database_url = _database_url()
    if database_url:
        pool = _postgres_pool()
        return PostgresConnection(pool.acquire(), pool)
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """初始化数据库：执行schema.sql建表"""
    if is_postgres_enabled():
        conn = get_connection()
        try:
            _execute_postgres_schema(conn)
            _ensure_postgres_analysis_history_columns(conn)
            _ensure_postgres_watchlist_columns(conn)
            conn.commit()
        finally:
            conn.close()
        return

    conn = get_connection()
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        _ensure_analysis_history_columns(conn)
        _ensure_watchlist_columns(conn)
        _ensure_strategy_recommendation_columns(conn)
        _ensure_user_quant_strategy_columns(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_analysis_history_columns(conn: sqlite3.Connection):
    """补齐历史库新增字段，兼容已有本地SQLite文件。"""
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(analysis_history)").fetchall()
    }
    migrations = {
        "buy_date": "ALTER TABLE analysis_history ADD COLUMN buy_date TEXT",
        "cost_price": "ALTER TABLE analysis_history ADD COLUMN cost_price REAL",
        "strategy_exit_json": "ALTER TABLE analysis_history ADD COLUMN strategy_exit_json TEXT",
        "sector_name": "ALTER TABLE analysis_history ADD COLUMN sector_name TEXT",
        "sector_type": "ALTER TABLE analysis_history ADD COLUMN sector_type TEXT",
        "sector_rank": "ALTER TABLE analysis_history ADD COLUMN sector_rank INTEGER",
        "sector_score": "ALTER TABLE analysis_history ADD COLUMN sector_score REAL",
        "sector_heat": "ALTER TABLE analysis_history ADD COLUMN sector_heat REAL",
        "sector_change_pct": "ALTER TABLE analysis_history ADD COLUMN sector_change_pct REAL",
        "sector_heat_window": "ALTER TABLE analysis_history ADD COLUMN sector_heat_window TEXT",
        "sector_heat_date": "ALTER TABLE analysis_history ADD COLUMN sector_heat_date TEXT",
        "sector_source": "ALTER TABLE analysis_history ADD COLUMN sector_source TEXT",
        "sector_confidence": "ALTER TABLE analysis_history ADD COLUMN sector_confidence REAL",
        "sector_context_json": "ALTER TABLE analysis_history ADD COLUMN sector_context_json TEXT",
        "retail_direction": "ALTER TABLE analysis_history ADD COLUMN retail_direction TEXT",
        "retail_score": "ALTER TABLE analysis_history ADD COLUMN retail_score REAL",
        "flow_recommendation": "ALTER TABLE analysis_history ADD COLUMN flow_recommendation TEXT",
        "exit_risk_score": "ALTER TABLE analysis_history ADD COLUMN exit_risk_score REAL",
        "strategy_risk_level": "ALTER TABLE analysis_history ADD COLUMN strategy_risk_level TEXT",
        "strategy_risk_score": "ALTER TABLE analysis_history ADD COLUMN strategy_risk_score REAL",
    }
    for column, ddl in migrations.items():
        if column not in existing:
            conn.execute(ddl)


def _ensure_watchlist_columns(conn: sqlite3.Connection):
    """补齐自选股新增字段，兼容已有本地SQLite文件。"""
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()
    }
    migrations = {
        "pinned_at": "ALTER TABLE watchlist ADD COLUMN pinned_at TIMESTAMP",
    }
    for column, ddl in migrations.items():
        if column not in existing:
            conn.execute(ddl)


def _ensure_strategy_recommendation_columns(conn: sqlite3.Connection):
    """补齐策略推荐新增板块字段，兼容已有本地SQLite文件。"""
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(strategy_recommendations)").fetchall()
    }
    migrations = {
        "sector_name": "ALTER TABLE strategy_recommendations ADD COLUMN sector_name TEXT",
        "sector_type": "ALTER TABLE strategy_recommendations ADD COLUMN sector_type TEXT",
        "sector_rank": "ALTER TABLE strategy_recommendations ADD COLUMN sector_rank INTEGER",
        "sector_score": "ALTER TABLE strategy_recommendations ADD COLUMN sector_score REAL",
        "sector_heat": "ALTER TABLE strategy_recommendations ADD COLUMN sector_heat REAL",
        "sector_change_pct": "ALTER TABLE strategy_recommendations ADD COLUMN sector_change_pct REAL",
        "sector_heat_window": "ALTER TABLE strategy_recommendations ADD COLUMN sector_heat_window TEXT",
        "sector_heat_date": "ALTER TABLE strategy_recommendations ADD COLUMN sector_heat_date TEXT",
        "sector_source": "ALTER TABLE strategy_recommendations ADD COLUMN sector_source TEXT",
        "sector_confidence": "ALTER TABLE strategy_recommendations ADD COLUMN sector_confidence REAL",
        "sector_context_json": "ALTER TABLE strategy_recommendations ADD COLUMN sector_context_json TEXT",
    }
    for column, ddl in migrations.items():
        if column not in existing:
            conn.execute(ddl)


def _ensure_user_quant_strategy_columns(conn: sqlite3.Connection):
    """Create/upgrade user quant strategy table for existing local SQLite files."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_quant_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            platform TEXT DEFAULT 'python_joinquant',
            source_code TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            parse_status TEXT NOT NULL DEFAULT 'pending',
            parse_result_json TEXT,
            ai_status TEXT NOT NULL DEFAULT 'disabled',
            ai_analysis_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_analyzed_at TIMESTAMP
        )
    """)
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(user_quant_strategies)").fetchall()
    }
    migrations = {
        "platform": "ALTER TABLE user_quant_strategies ADD COLUMN platform TEXT DEFAULT 'python_joinquant'",
        "source_hash": "ALTER TABLE user_quant_strategies ADD COLUMN source_hash TEXT DEFAULT ''",
        "parse_status": "ALTER TABLE user_quant_strategies ADD COLUMN parse_status TEXT NOT NULL DEFAULT 'pending'",
        "parse_result_json": "ALTER TABLE user_quant_strategies ADD COLUMN parse_result_json TEXT",
        "ai_status": "ALTER TABLE user_quant_strategies ADD COLUMN ai_status TEXT NOT NULL DEFAULT 'disabled'",
        "ai_analysis_json": "ALTER TABLE user_quant_strategies ADD COLUMN ai_analysis_json TEXT",
        "updated_at": "ALTER TABLE user_quant_strategies ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "last_analyzed_at": "ALTER TABLE user_quant_strategies ADD COLUMN last_analyzed_at TIMESTAMP",
    }
    for column, ddl in migrations.items():
        if column not in existing:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_quant_strategies_user ON user_quant_strategies(user_id)")


# ─── 用户 CRUD ───────────────────────────────────────────

def create_user(username: str, email: str, password_hash: str) -> dict:
    """创建用户，返回用户信息"""
    conn = get_connection()
    try:
        user_id = _insert_and_get_id(
            conn,
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, password_hash)
        )
        conn.commit()
        return {"id": user_id, "username": username, "email": email, "role": "free", "credits": 10}
    except (sqlite3.IntegrityError, UniqueViolation) as e:
        if "username" in str(e):
            raise ValueError("用户名已存在")
        elif "email" in str(e):
            raise ValueError("邮箱已被注册")
        raise ValueError("注册失败")
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[dict]:
    """通过用户名查找用户"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    """通过邮箱查找用户"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    """通过ID查找用户"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_last_login(user_id: int):
    """更新最后登录时间"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (datetime.now().isoformat(), user_id)
        )
        conn.commit()
    finally:
        conn.close()


def deduct_credits(user_id: int, amount: int = 1) -> bool:
    """扣除credits，成功返回True，余额不足返回False"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["credits"] < amount:
            return False
        conn.execute(
            "UPDATE users SET credits = credits - ? WHERE id = ? AND credits >= ?",
            (amount, user_id, amount)
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ─── Token 管理 ──────────────────────────────────────────

def save_token(user_id: int, token: str, expires_at: str):
    """保存token"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO user_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at)
        )
        conn.commit()
    finally:
        conn.close()


def get_token(token: str) -> Optional[dict]:
    """获取token记录"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM user_tokens WHERE token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_token(token: str):
    """删除token"""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM user_tokens WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def clean_expired_tokens():
    """清理过期token"""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM user_tokens WHERE expires_at < ?",
            (datetime.now().isoformat(),)
        )
        conn.commit()
    finally:
        conn.close()


# ─── 分析记录 ─────────────────────────────────────────────

def save_analysis(user_id: int, data: dict) -> int:
    """保存分析记录，返回记录ID"""
    conn = get_connection()
    try:
        sector_fields = _sector_fields_from(data)
        flow_fields = _flow_summary_fields_from(data)
        strategy_exit = data.get("strategy_exit") if isinstance(data.get("strategy_exit"), dict) else None
        strategy_exit_value = (
            Jsonb(strategy_exit)
            if strategy_exit and _is_postgres_connection(conn) and Jsonb is not None
            else (_json_dumps(strategy_exit) if strategy_exit else None)
        )
        record_id = _insert_and_get_id(conn, """
            INSERT INTO analysis_history (
                user_id, stock_code, stock_name,
                ma5, ma10, ma20, ma60,
                macd, macd_signal, macd_hist,
                kdj_k, kdj_d, kdj_j,
                rsi, boll_upper, boll_mid, boll_lower,
                pe_ratio, pb_ratio, recent_change_5d, recent_change_20d,
                volume_ratio, tech_score, fundamental_score,
                total_score, recommendation, analysis_text,
                buy_date, cost_price, strategy_exit_json,
                sector_name, sector_type, sector_rank, sector_score, sector_heat,
                sector_change_pct, sector_heat_window, sector_heat_date, sector_source,
                sector_confidence, sector_context_json,
                retail_direction, retail_score, flow_recommendation, exit_risk_score,
                strategy_risk_level, strategy_risk_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, data.get("stock_code"), data.get("stock_name"),
            data.get("ma5"), data.get("ma10"), data.get("ma20"), data.get("ma60"),
            data.get("macd"), data.get("macd_signal"), data.get("macd_hist"),
            data.get("kdj_k"), data.get("kdj_d"), data.get("kdj_j"),
            data.get("rsi"), data.get("boll_upper"), data.get("boll_mid"), data.get("boll_lower"),
            data.get("pe_ratio"), data.get("pb_ratio"), data.get("recent_change_5d"), data.get("recent_change_20d"),
            data.get("volume_ratio"), data.get("tech_score"), data.get("fundamental_score"),
            data.get("total_score"), data.get("recommendation"), data.get("analysis_text"),
            data.get("buy_date"), data.get("cost_price"), strategy_exit_value,
            sector_fields["sector_name"], sector_fields["sector_type"], sector_fields["sector_rank"], sector_fields["sector_score"],
            sector_fields["sector_heat"], sector_fields["sector_change_pct"],
            sector_fields["sector_heat_window"], sector_fields["sector_heat_date"],
            sector_fields["sector_source"], sector_fields["sector_confidence"],
            _sector_context_json(sector_fields),
            flow_fields["retail_direction"], flow_fields["retail_score"],
            flow_fields["flow_recommendation"], flow_fields["exit_risk_score"],
            flow_fields["strategy_risk_level"], flow_fields["strategy_risk_score"],
        ))
        conn.commit()
        return record_id
    finally:
        conn.close()


def get_analysis_history(user_id: int, limit: int = 50) -> list:
    """获取用户分析历史"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM analysis_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
        return [_with_analysis_json_fields(_with_sector_fallback(dict(r))) for r in rows]
    finally:
        conn.close()


def get_analysis_by_id(analysis_id: int, user_id: int) -> Optional[dict]:
    """获取单条分析记录"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM analysis_history WHERE id = ? AND user_id = ?",
            (analysis_id, user_id)
        ).fetchone()
        return _with_analysis_json_fields(_with_sector_fallback(dict(row))) if row else None
    finally:
        conn.close()


# ─── 自选股 ───────────────────────────────────────────────

def add_watchlist(user_id: int, stock_code: str, stock_name: str = None):
    """添加自选股"""
    conn = get_connection()
    try:
        sql = (
            "INSERT INTO watchlist (user_id, stock_code, stock_name) VALUES (?, ?, ?) "
            "ON CONFLICT (user_id, stock_code) DO NOTHING"
            if _is_postgres_connection(conn)
            else "INSERT OR IGNORE INTO watchlist (user_id, stock_code, stock_name) VALUES (?, ?, ?)"
        )
        conn.execute(
            sql,
            (user_id, stock_code, stock_name)
        )
        conn.commit()
    finally:
        conn.close()


def update_watchlist_pin(user_id: int, stock_code: str, pinned: bool) -> bool:
    """更新自选股置顶状态，返回是否命中当前用户的自选股。"""
    conn = get_connection()
    try:
        if pinned:
            pinned_at = datetime.now().isoformat(sep=" ", timespec="microseconds")
            cursor = conn.execute(
                "UPDATE watchlist SET pinned_at = ? WHERE user_id = ? AND stock_code = ?",
                (pinned_at, user_id, stock_code)
            )
        else:
            cursor = conn.execute(
                "UPDATE watchlist SET pinned_at = NULL WHERE user_id = ? AND stock_code = ?",
                (user_id, stock_code)
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_watchlist(user_id: int) -> list:
    """获取用户自选股列表，包含最近一次分析的评分和建议"""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT w.id, w.user_id, w.stock_code,
                   COALESCE(w.stock_name, ah.stock_name) AS stock_name,
                   w.added_at,
                   w.pinned_at,
                   ah.total_score AS latest_score,
                   ah.recommendation AS latest_recommendation,
                   exit_ah.buy_date AS latest_buy_date,
                   exit_ah.cost_price AS latest_cost_price,
                   exit_ah.strategy_exit_json AS latest_strategy_exit_json,
                   position_ah.buy_date AS latest_position_buy_date,
                   position_ah.cost_price AS latest_position_cost_price,
                   ah.sector_name AS latest_sector_name,
                   ah.sector_type AS latest_sector_type,
                   ah.sector_rank AS latest_sector_rank,
                   ah.sector_score AS latest_sector_score,
                   ah.sector_heat AS latest_sector_heat,
                   ah.sector_change_pct AS latest_sector_change_pct,
                   ah.sector_heat_window AS latest_sector_heat_window,
                   ah.sector_heat_date AS latest_sector_heat_date,
                   ah.sector_source AS latest_sector_source,
                   ah.sector_confidence AS latest_sector_confidence,
                   ah.sector_context_json AS latest_sector_context_json,
                   ah.retail_direction AS latest_retail_direction,
                   ah.retail_score AS latest_retail_score,
                   ah.flow_recommendation AS latest_flow_recommendation,
                   ah.exit_risk_score AS latest_exit_risk_score,
                   ah.strategy_risk_level AS latest_strategy_risk_level,
                   ah.strategy_risk_score AS latest_strategy_risk_score
            FROM watchlist w
            LEFT JOIN analysis_history ah
              ON ah.stock_code = w.stock_code
              AND ah.user_id = w.user_id
              AND ah.id = (
                  SELECT MAX(id) FROM analysis_history
                  WHERE stock_code = w.stock_code AND user_id = w.user_id
              )
            LEFT JOIN analysis_history exit_ah
              ON exit_ah.stock_code = w.stock_code
              AND exit_ah.user_id = w.user_id
              AND exit_ah.id = (
                  SELECT MAX(id) FROM analysis_history
                  WHERE stock_code = w.stock_code
                    AND user_id = w.user_id
                    AND cost_price IS NOT NULL
                    AND cost_price > 0
                    AND strategy_exit_json IS NOT NULL
              )
            LEFT JOIN analysis_history position_ah
              ON position_ah.stock_code = w.stock_code
              AND position_ah.user_id = w.user_id
              AND position_ah.id = (
                  SELECT id FROM analysis_history
                  WHERE stock_code = w.stock_code
                    AND user_id = w.user_id
                    AND cost_price IS NOT NULL
                    AND cost_price > 0
                  ORDER BY
                    CASE WHEN buy_date IS NOT NULL THEN 0 ELSE 1 END,
                    id DESC
                  LIMIT 1
              )
            WHERE w.user_id = ?
            ORDER BY
                CASE WHEN w.pinned_at IS NULL THEN 1 ELSE 0 END,
                w.pinned_at DESC,
                w.added_at DESC,
                w.id DESC
        """, (user_id,)).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            context = _json_loads(item.pop("latest_sector_context_json", None), None)
            strategy_exit_raw = item.pop("latest_strategy_exit_json", None)
            parsed_strategy_exit = _json_loads(strategy_exit_raw, None) if not isinstance(strategy_exit_raw, dict) else strategy_exit_raw
            item["latest_strategy_exit"] = parsed_strategy_exit if isinstance(parsed_strategy_exit, dict) else None
            if isinstance(context, dict) and context:
                item["latest_sector_context"] = context
                fields = _sector_fields_from({"sector_context": context})
                item["latest_sector_name"] = item.get("latest_sector_name") or fields["sector_name"]
                item["latest_sector_type"] = item.get("latest_sector_type") or fields["sector_type"]
                item["latest_sector_rank"] = item.get("latest_sector_rank") or fields["sector_rank"]
                item["latest_sector_score"] = item.get("latest_sector_score") or fields["sector_score"]
                item["latest_sector_heat"] = item.get("latest_sector_heat") or fields["sector_heat"]
                item["latest_sector_change_pct"] = item.get("latest_sector_change_pct") or fields["sector_change_pct"]
                item["latest_sector_heat_window"] = item.get("latest_sector_heat_window") or fields["sector_heat_window"]
                item["latest_sector_heat_date"] = item.get("latest_sector_heat_date") or fields["sector_heat_date"]
                item["latest_sector_source"] = item.get("latest_sector_source") or fields["sector_source"]
                item["latest_sector_confidence"] = item.get("latest_sector_confidence") or fields["sector_confidence"]
                item["latest_retail_direction"] = item.get("latest_retail_direction") or context.get("retail_direction") or context.get("attention_direction")
                item["latest_retail_score"] = item.get("latest_retail_score") or context.get("retail_score") or context.get("retail_attention_score")
                item["latest_flow_recommendation"] = item.get("latest_flow_recommendation") or context.get("flow_recommendation")
                item["latest_exit_risk_score"] = item.get("latest_exit_risk_score") or context.get("exit_risk_score")
                item["latest_strategy_risk_level"] = item.get("latest_strategy_risk_level") or context.get("strategy_risk_level")
                item["latest_strategy_risk_score"] = item.get("latest_strategy_risk_score") or context.get("strategy_risk_score")
            items.append(item)
        return items
    finally:
        conn.close()


def remove_watchlist(user_id: int, stock_code: str) -> bool:
    """删除自选股，返回是否成功"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND stock_code = ?",
            (user_id, stock_code)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ─── 股票缓存 ─────────────────────────────────────────────

def save_stock_cache(code: str, date: str, data: dict):
    """保存股票日线缓存"""
    conn = get_connection()
    try:
        sql = """
            INSERT OR REPLACE INTO stock_cache (code, date, open, high, low, close, volume, pe_ratio, pb_ratio, market_cap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        if _is_postgres_connection(conn):
            sql = """
                INSERT INTO stock_cache (code, date, open, high, low, close, volume, pe_ratio, pb_ratio, market_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (code, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    pe_ratio = EXCLUDED.pe_ratio,
                    pb_ratio = EXCLUDED.pb_ratio,
                    market_cap = EXCLUDED.market_cap,
                    updated_at = NOW()
            """
        conn.execute(sql, (
            code, date,
            data.get("open"), data.get("high"), data.get("low"), data.get("close"),
            data.get("volume"), data.get("pe_ratio"), data.get("pb_ratio"), data.get("market_cap")
        ))
        conn.commit()
    finally:
        conn.close()


def save_stock_cache_many(code: str, rows: list[dict]):
    """保存多条股票日线缓存，使用单连接单事务减少远程数据库往返。"""
    if not rows:
        return
    conn = get_connection()
    try:
        sql = """
            INSERT OR REPLACE INTO stock_cache (code, date, open, high, low, close, volume, pe_ratio, pb_ratio, market_cap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        if _is_postgres_connection(conn):
            sql = """
                INSERT INTO stock_cache (code, date, open, high, low, close, volume, pe_ratio, pb_ratio, market_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (code, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    pe_ratio = EXCLUDED.pe_ratio,
                    pb_ratio = EXCLUDED.pb_ratio,
                    market_cap = EXCLUDED.market_cap,
                    updated_at = NOW()
            """
        params = [
            (
                code,
                row.get("date"),
                row.get("open"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("volume"),
                row.get("pe_ratio"),
                row.get("pb_ratio"),
                row.get("market_cap"),
            )
            for row in rows
        ]
        conn.executemany(sql, params)
        conn.commit()
    finally:
        conn.close()


def get_stock_cache(code: str, date: str = None) -> list:
    """获取股票缓存数据"""
    conn = get_connection()
    try:
        if date:
            rows = conn.execute(
                "SELECT * FROM stock_cache WHERE code = ? AND date = ?",
                (code, date)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM stock_cache WHERE code = ? ORDER BY date ASC",
                (code,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── 每日策略推荐 ─────────────────────────────────────────

STRATEGY_META = {
    "first_board_relay": {
        "name": "低位3连阳首板接力",
        "description": "昨日首板、低波动、量价放量与集合竞价承接过滤。",
        "scheduled_time": "09:27",
    },
    "leader_chase": {
        "name": "龙头追击",
        "description": "昨日涨停龙头按连板数、市场情绪和09:25前竞价筛选。",
        "scheduled_time": "09:27",
    },
    "etf_rotation": {
        "name": "ETF动量轮动",
        "description": "固定ETF池、动态ETF池和大A走弱状态下的动量轮动。",
        "scheduled_time": "13:10",
    },
    "qlib_model": {
        "name": "AI模型选股",
        "description": "Qlib训练流水线生成的样本外预测分数。",
        "scheduled_time": "15:30",
    },
}


def _json_dumps(value) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _json_loads(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _first_present(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _sector_context_from(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    context = data.get("sector_context")
    if isinstance(context, dict):
        return context
    heat_context = data.get("heat_context")
    if isinstance(heat_context, dict):
        return heat_context
    return {}


def _sector_fields_from(data: dict, metrics: Optional[dict] = None) -> dict:
    metrics = metrics if isinstance(metrics, dict) else {}
    context = _sector_context_from(data) or _sector_context_from(metrics)
    board_hints = context.get("board_ranking_hints") if isinstance(context.get("board_ranking_hints"), dict) else {}
    return {
        "sector_name": _first_present(
            data.get("sector_name"),
            data.get("industry"),
            context.get("sector_name"),
            context.get("name"),
            metrics.get("sector_name"),
            metrics.get("industry"),
        ),
        "sector_type": _first_present(
            data.get("sector_type"),
            context.get("sector_type"),
            metrics.get("sector_type"),
        ),
        "sector_rank": _first_present(
            data.get("sector_rank"),
            context.get("sector_rank"),
            context.get("rank"),
            board_hints.get("rank"),
            metrics.get("sector_rank"),
        ),
        "sector_score": _first_present(
            data.get("sector_score"),
            context.get("sector_score"),
            context.get("score"),
            context.get("heat"),
            metrics.get("sector_score"),
            metrics.get("sector_heat"),
        ),
        "sector_heat": _first_present(
            data.get("sector_heat"),
            context.get("sector_heat"),
            context.get("heat"),
            context.get("heat_label"),
            metrics.get("sector_heat"),
        ),
        "sector_change_pct": _first_present(
            data.get("sector_change_pct"),
            context.get("sector_change_pct"),
            context.get("change_pct"),
            board_hints.get("change_pct"),
            metrics.get("sector_change_pct"),
        ),
        "sector_heat_window": _first_present(
            data.get("sector_heat_window"),
            context.get("sector_heat_window"),
            context.get("window"),
            metrics.get("sector_heat_window"),
        ),
        "sector_heat_date": _first_present(
            data.get("sector_heat_date"),
            context.get("sector_heat_date"),
            context.get("date"),
            metrics.get("sector_heat_date"),
        ),
        "sector_source": _first_present(
            data.get("sector_source"),
            context.get("sector_source"),
            context.get("source"),
            metrics.get("sector_source"),
        ),
        "sector_confidence": _first_present(
            data.get("sector_confidence"),
            context.get("sector_confidence"),
            context.get("confidence"),
            metrics.get("sector_confidence"),
        ),
        "sector_context": context,
    }


def _flow_summary_fields_from(data: dict) -> dict:
    retail = data.get("retail_attention") if isinstance(data.get("retail_attention"), dict) else {}
    risk_warning = data.get("risk_warning") if isinstance(data.get("risk_warning"), dict) else {}
    flow_signals = data.get("flow_signals") if isinstance(data.get("flow_signals"), dict) else {}
    strategy_exit = data.get("strategy_exit") if isinstance(data.get("strategy_exit"), dict) else {}
    return {
        "retail_direction": _first_present(
            data.get("retail_direction"),
            retail.get("direction"),
            data.get("attention_direction"),
            flow_signals.get("attention_direction"),
        ),
        "retail_score": _first_present(
            data.get("retail_score"),
            retail.get("score"),
            data.get("retail_attention_score"),
            flow_signals.get("retail_attention_score"),
        ),
        "flow_recommendation": _first_present(
            data.get("flow_recommendation"),
            risk_warning.get("label"),
        ),
        "exit_risk_score": _first_present(
            data.get("exit_risk_score"),
            data.get("exit_risk"),
            risk_warning.get("score"),
            flow_signals.get("exit_risk_score"),
        ),
        "strategy_risk_level": _first_present(
            data.get("strategy_risk_level"),
            strategy_exit.get("risk_level"),
        ),
        "strategy_risk_score": _first_present(
            data.get("strategy_risk_score"),
            strategy_exit.get("risk_score"),
        ),
    }


def _sector_context_json(sector_fields: dict) -> Optional[str]:
    context = sector_fields.get("sector_context")
    return _json_dumps(context) if context else None


def _with_analysis_json_fields(row: dict) -> dict:
    strategy_exit_raw = row.get("strategy_exit_json")
    if isinstance(strategy_exit_raw, dict):
        row["strategy_exit"] = strategy_exit_raw
    else:
        parsed = _json_loads(strategy_exit_raw, None)
        row["strategy_exit"] = parsed if isinstance(parsed, dict) else None
    return row


def _with_sector_fallback(row: dict, metrics: Optional[dict] = None) -> dict:
    fields = _sector_fields_from(row, metrics)
    context = _json_loads(row.get("sector_context_json"), None)
    if isinstance(context, dict) and context:
        fields = _sector_fields_from({**row, "sector_context": context}, metrics)
    for key in (
        "sector_name", "sector_type", "sector_rank", "sector_score", "sector_heat",
        "sector_change_pct", "sector_heat_window", "sector_heat_date",
        "sector_source", "sector_confidence"
    ):
        if row.get(key) in (None, "") and fields.get(key) not in (None, ""):
            row[key] = fields[key]
    if "sector_context" not in row and isinstance(context, dict) and context:
        row["sector_context"] = context
    return row


def _row_to_dict(row) -> dict:
    return row if isinstance(row, dict) else dict(row)


def _format_dt(value) -> Optional[str]:
    return value.isoformat(timespec="seconds") if hasattr(value, "isoformat") else value


def save_strategy_run_results(
    strategy_key: str,
    trade_date: str,
    status: str,
    total_scanned: int,
    recommendations: list,
    error: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> int:
    """幂等保存某策略某交易日的推荐运行结果。"""
    if strategy_key not in STRATEGY_META:
        raise ValueError(f"未知策略: {strategy_key}")

    started = _format_dt(started_at or datetime.now())
    finished = _format_dt(finished_at or datetime.now())
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM strategy_runs WHERE strategy_key = ? AND trade_date = ?",
            (strategy_key, trade_date),
        ).fetchone()
        if existing:
            run_id = existing["id"]
            conn.execute(
                """
                UPDATE strategy_runs
                SET status = ?, started_at = ?, finished_at = ?,
                    total_scanned = ?, total_picks = ?, error = ?
                WHERE id = ?
                """,
                (status, started, finished, total_scanned, len(recommendations), error, run_id),
            )
            conn.execute("DELETE FROM strategy_recommendations WHERE run_id = ?", (run_id,))
        else:
            run_id = _insert_and_get_id(
                conn,
                """
                INSERT INTO strategy_runs (
                    strategy_key, trade_date, status, started_at, finished_at,
                    total_scanned, total_picks, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (strategy_key, trade_date, status, started, finished, total_scanned, len(recommendations), error),
            )

        for idx, rec in enumerate(recommendations, start=1):
            metrics = rec.get("metrics", {})
            sector_fields = _sector_fields_from(rec, metrics)
            conn.execute(
                """
                INSERT INTO strategy_recommendations (
                    run_id, strategy_key, code, name, rank, score, mode,
                    reasons_json, risks_json, metrics_json, data_status_json,
                    sector_name, sector_type, sector_rank, sector_score, sector_heat,
                    sector_change_pct, sector_heat_window, sector_heat_date, sector_source,
                    sector_confidence, sector_context_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    strategy_key,
                    rec.get("code"),
                    rec.get("name"),
                    rec.get("rank", idx),
                    rec.get("score"),
                    rec.get("mode"),
                    _json_dumps(rec.get("reasons", [])),
                    _json_dumps(rec.get("risks", [])),
                    _json_dumps(metrics),
                    _json_dumps(rec.get("data_status", {})),
                    sector_fields["sector_name"],
                    sector_fields["sector_type"],
                    sector_fields["sector_rank"],
                    sector_fields["sector_score"],
                    sector_fields["sector_heat"],
                    sector_fields["sector_change_pct"],
                    sector_fields["sector_heat_window"],
                    sector_fields["sector_heat_date"],
                    sector_fields["sector_source"],
                    sector_fields["sector_confidence"],
                    _sector_context_json(sector_fields),
                ),
            )
        conn.commit()
        return run_id
    finally:
        conn.close()


def get_strategy_run(run_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_strategy_trade_date() -> Optional[str]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT trade_date FROM strategy_runs ORDER BY trade_date DESC, finished_at DESC LIMIT 1"
        ).fetchone()
        return row["trade_date"] if row else None
    finally:
        conn.close()


def _empty_strategy_group(strategy_key: str, trade_date: Optional[str] = None) -> dict:
    meta = STRATEGY_META[strategy_key]
    return {
        "strategy_key": strategy_key,
        "strategy_name": meta["name"],
        "description": meta["description"],
        "scheduled_time": meta["scheduled_time"],
        "trade_date": trade_date,
        "status": "pending",
        "run_started_at": None,
        "run_finished_at": None,
        "total_scanned": 0,
        "total_picks": 0,
        "error": None,
        "recommendations": [],
    }


def get_daily_picks(trade_date: Optional[str] = None) -> dict:
    """读取某交易日三策略推荐；不传日期则读取最近一次有效交易日。"""
    resolved_date = trade_date or get_latest_strategy_trade_date()
    groups = {key: _empty_strategy_group(key, resolved_date) for key in STRATEGY_META}
    if not resolved_date:
        return {"trade_date": None, "latest_trade_date": None, "groups": list(groups.values())}

    conn = get_connection()
    try:
        run_rows = conn.execute(
            "SELECT * FROM strategy_runs WHERE trade_date = ? ORDER BY strategy_key",
            (resolved_date,),
        ).fetchall()
        for row in run_rows:
            run = dict(row)
            key = run["strategy_key"]
            group = groups.get(key, _empty_strategy_group(key, resolved_date))
            group.update({
                "trade_date": resolved_date,
                "status": run["status"],
                "run_started_at": run["started_at"],
                "run_finished_at": run["finished_at"],
                "total_scanned": run["total_scanned"],
                "total_picks": run["total_picks"],
                "error": run["error"],
            })
            rec_rows = conn.execute(
                """
                SELECT * FROM strategy_recommendations
                WHERE run_id = ?
                ORDER BY COALESCE(rank, id), id
                """,
                (run["id"],),
            ).fetchall()
            recommendations = []
            for rec in rec_rows:
                metrics = _json_loads(rec["metrics_json"], {})
                item = _with_sector_fallback(dict(rec), metrics)
                recommendation = {
                    "id": rec["id"],
                    "strategy_key": rec["strategy_key"],
                    "strategy_name": STRATEGY_META.get(rec["strategy_key"], {}).get("name", rec["strategy_key"]),
                    "code": rec["code"],
                    "name": rec["name"],
                    "rank": rec["rank"],
                    "score": rec["score"],
                    "mode": rec["mode"],
                    "reasons": _json_loads(rec["reasons_json"], []),
                    "risks": _json_loads(rec["risks_json"], []),
                    "metrics": metrics,
                    "data_status": _json_loads(rec["data_status_json"], {}),
                    "sector_name": item.get("sector_name"),
                    "sector_type": item.get("sector_type"),
                    "sector_rank": item.get("sector_rank"),
                    "sector_score": item.get("sector_score"),
                    "sector_heat": item.get("sector_heat"),
                    "sector_change_pct": item.get("sector_change_pct"),
                    "sector_heat_window": item.get("sector_heat_window"),
                    "sector_heat_date": item.get("sector_heat_date"),
                    "sector_source": item.get("sector_source"),
                    "sector_confidence": item.get("sector_confidence"),
                    "created_at": rec["created_at"],
                }
                if item.get("sector_context"):
                    recommendation["sector_context"] = item["sector_context"]
                recommendations.append(recommendation)
            group["recommendations"] = recommendations
            groups[key] = group
        return {
            "trade_date": resolved_date,
            "latest_trade_date": resolved_date,
            "groups": list(groups.values()),
        }
    finally:
        conn.close()


def get_local_sector_heat_ranking(limit: int = 20) -> dict:
    """Aggregate recent locally stored sector heat as a fallback heat ranking."""
    safe_limit = max(1, min(int(limit or 20), 50))
    bucket: dict[tuple[str, str], dict] = {}

    def add_item(row: dict, source: str):
        name = (row.get("sector_name") or "").strip()
        if not name or name == "未知板块":
            return
        sector_type = (row.get("sector_type") or "unknown").strip()
        heat = row.get("sector_heat") or row.get("sector_score")
        try:
            heat_num = float(heat)
        except (TypeError, ValueError):
            return
        key = (sector_type, name)
        item = bucket.setdefault(
            key,
            {
                "sector_name": name,
                "sector_type": sector_type,
                "score_sum": 0.0,
                "count": 0,
                "latest_at": "",
                "source_count": {"analysis_history": 0, "strategy_recommendations": 0},
                "change_pct": row.get("sector_change_pct"),
            },
        )
        item["score_sum"] += heat_num
        item["count"] += 1
        item["source_count"][source] = item["source_count"].get(source, 0) + 1
        created_at = _format_dt(row.get("created_at")) or ""
        if created_at > item["latest_at"]:
            item["latest_at"] = created_at
        if item.get("change_pct") is None and row.get("sector_change_pct") is not None:
            item["change_pct"] = row.get("sector_change_pct")

    conn = get_connection()
    try:
        for row in conn.execute(
            """
            SELECT sector_name, sector_type, sector_heat, sector_score,
                   sector_change_pct, created_at
            FROM analysis_history
            WHERE sector_name IS NOT NULL AND sector_name != ''
            ORDER BY created_at DESC
            LIMIT 200
            """
        ).fetchall():
            add_item(dict(row), "analysis_history")
        for row in conn.execute(
            """
            SELECT sector_name, sector_type, sector_heat, sector_score,
                   sector_change_pct, created_at
            FROM strategy_recommendations
            WHERE sector_name IS NOT NULL AND sector_name != ''
            ORDER BY created_at DESC
            LIMIT 200
            """
        ).fetchall():
            add_item(dict(row), "strategy_recommendations")
    finally:
        conn.close()

    rows = []
    for item in bucket.values():
        avg_heat = item["score_sum"] / max(1, item["count"])
        recency_bonus = min(8, item["count"] * 1.4)
        heat = max(0, min(100, round(avg_heat + recency_bonus, 1)))
        rows.append(
            {
                "sector_name": item["sector_name"],
                "sector_type": item["sector_type"],
                "sector_heat": heat,
                "sector_heat_score": heat,
                "change_pct": item.get("change_pct"),
                "local_count": item["count"],
                "latest_at": item["latest_at"],
                "source_count": item["source_count"],
                "source": "local_analysis_history",
                "window": "recent_local",
                "date": (item["latest_at"] or datetime.now().isoformat())[:10],
                "confidence": 0.48,
            }
        )
    rows.sort(key=lambda item: (item["sector_heat_score"], item.get("local_count") or 0), reverse=True)
    for index, item in enumerate(rows[:safe_limit], start=1):
        item["rank"] = index
    return {
        "items": rows[:safe_limit],
        "source": "local_analysis_history",
        "window": "recent_local",
        "date": rows[0]["date"] if rows else datetime.now().strftime("%Y-%m-%d"),
        "confidence": 0.48 if rows else 0.0,
        "data_status": {
            "status": "fallback" if rows else "degraded",
            "warnings": ["东财全市场热榜不可用，当前展示本地最近分析/推荐聚合热度"] if rows else ["本地暂无可聚合板块热度数据"],
        },
    }


# ─── 每日热度快照 ─────────────────────────────────────────

def save_attention_heat_snapshot(snapshot: dict) -> int:
    """保存每日资金/散户热度快照，返回记录ID。"""
    if not isinstance(snapshot, dict):
        raise ValueError("热度快照必须是dict")
    conn = get_connection()
    try:
        snapshot_date = snapshot.get("date")
        snapshot_ts = snapshot.get("timestamp")
        payload = json.dumps(snapshot, ensure_ascii=False)
        value = payload if not _is_postgres_connection(conn) else Jsonb(snapshot)
        snapshot_id = _insert_and_get_id(
            conn,
            """
            INSERT INTO attention_heat_snapshots (snapshot_date, snapshot_ts, snapshot_json)
            VALUES (?, ?, ?)
            """,
            (snapshot_date, snapshot_ts, value),
        )
        conn.commit()
        return snapshot_id
    finally:
        conn.close()


def get_attention_heat_snapshots(limit: int = 20) -> list[dict]:
    """读取最近的每日资金/散户热度快照，按时间升序返回。"""
    safe_limit = max(1, min(int(limit or 20), 200))
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT snapshot_json
            FROM attention_heat_snapshots
            ORDER BY id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        snapshots = []
        for row in reversed(rows):
            value = _row_to_dict(row).get("snapshot_json")
            if isinstance(value, dict):
                if value.get("topics"):
                    snapshots.append(value)
            else:
                loaded = _json_loads(value, None)
                if isinstance(loaded, dict) and loaded.get("topics"):
                    snapshots.append(loaded)
        return snapshots
    finally:
        conn.close()


# ─── 用户量化策略库 ───────────────────────────────────────

def _source_hash(source_code: str) -> str:
    return hashlib.sha256((source_code or "").encode("utf-8")).hexdigest()


def _json_value_for_db(conn, value):
    if value is None:
        return None
    return Jsonb(value) if _is_postgres_connection(conn) else json.dumps(value, ensure_ascii=False)


def _json_from_db(value, default):
    if isinstance(value, (dict, list)):
        return value
    return _json_loads(value, default)


def _quant_strategy_from_row(row, include_source: bool = True) -> dict:
    data = _row_to_dict(row)
    parse_result = _json_from_db(data.get("parse_result_json"), {})
    ai_analysis = _json_from_db(data.get("ai_analysis_json"), None)
    summary = {}
    if isinstance(parse_result, dict):
        summary = dict(parse_result.get("summary") or {})
    summary["title"] = data.get("title")
    item = {
        "id": data.get("id"),
        "user_id": data.get("user_id"),
        "title": data.get("title"),
        "platform": data.get("platform") or "python_joinquant",
        "source_hash": data.get("source_hash"),
        "parse_status": data.get("parse_status"),
        "parse_result": parse_result,
        "ai_status": data.get("ai_status") or "disabled",
        "ai_analysis": ai_analysis,
        "summary": summary,
        "created_at": _format_dt(data.get("created_at")),
        "updated_at": _format_dt(data.get("updated_at")),
        "last_analyzed_at": _format_dt(data.get("last_analyzed_at")),
    }
    if include_source:
        item["source_code"] = data.get("source_code")
    return item


def list_user_quant_strategies(user_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM user_quant_strategies
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
        return [_quant_strategy_from_row(row, include_source=False) for row in rows]
    finally:
        conn.close()


def get_user_quant_strategy(user_id: int, strategy_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM user_quant_strategies
            WHERE id = ? AND user_id = ?
            """,
            (strategy_id, user_id),
        ).fetchone()
        return _quant_strategy_from_row(row, include_source=True) if row else None
    finally:
        conn.close()


def create_user_quant_strategy(
    user_id: int,
    title: str,
    platform: str,
    source_code: str,
    parse_status: str,
    parse_result: dict,
    ai_status: str,
    ai_analysis: Optional[dict],
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    try:
        strategy_id = _insert_and_get_id(
            conn,
            """
            INSERT INTO user_quant_strategies (
                user_id, title, platform, source_code, source_hash,
                parse_status, parse_result_json, ai_status, ai_analysis_json,
                updated_at, last_analyzed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                title,
                platform or "python_joinquant",
                source_code,
                _source_hash(source_code),
                parse_status,
                _json_value_for_db(conn, parse_result),
                ai_status,
                _json_value_for_db(conn, ai_analysis),
                now,
                now,
            ),
        )
        conn.commit()
        return strategy_id
    finally:
        conn.close()


def update_user_quant_strategy(
    user_id: int,
    strategy_id: int,
    title: str,
    platform: str,
    source_code: str,
    parse_status: str,
    parse_result: dict,
    ai_status: str,
    ai_analysis: Optional[dict],
) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE user_quant_strategies
            SET title = ?, platform = ?, source_code = ?, source_hash = ?,
                parse_status = ?, parse_result_json = ?, ai_status = ?,
                ai_analysis_json = ?, updated_at = ?, last_analyzed_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                title,
                platform or "python_joinquant",
                source_code,
                _source_hash(source_code),
                parse_status,
                _json_value_for_db(conn, parse_result),
                ai_status,
                _json_value_for_db(conn, ai_analysis),
                now,
                now,
                strategy_id,
                user_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_user_quant_strategy_analysis(
    user_id: int,
    strategy_id: int,
    parse_status: str,
    parse_result: dict,
    ai_status: str,
    ai_analysis: Optional[dict],
) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE user_quant_strategies
            SET parse_status = ?, parse_result_json = ?, ai_status = ?,
                ai_analysis_json = ?, updated_at = ?, last_analyzed_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                parse_status,
                _json_value_for_db(conn, parse_result),
                ai_status,
                _json_value_for_db(conn, ai_analysis),
                now,
                now,
                strategy_id,
                user_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_user_quant_strategy(user_id: int, strategy_id: int) -> bool:
    """Delete exactly one user strategy by explicit id."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM user_quant_strategies WHERE id = ? AND user_id = ?",
            (strategy_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
