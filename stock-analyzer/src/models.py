"""
数据库操作层
SQLite连接管理 + 用户CRUD + Token管理 + 分析记录 + 自选股 + 股票缓存
"""

import sqlite3
import os
import json
from datetime import datetime
from typing import Optional

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "stock.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_connection() -> sqlite3.Connection:
    """获取数据库连接，启用外键约束"""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """初始化数据库：执行schema.sql建表"""
    conn = get_connection()
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


# ─── 用户 CRUD ───────────────────────────────────────────

def create_user(username: str, email: str, password_hash: str) -> dict:
    """创建用户，返回用户信息"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, password_hash)
        )
        conn.commit()
        user_id = cursor.lastrowid
        return {"id": user_id, "username": username, "email": email, "role": "free", "credits": 10}
    except sqlite3.IntegrityError as e:
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
        cursor = conn.execute("""
            INSERT INTO analysis_history (
                user_id, stock_code, stock_name,
                ma5, ma10, ma20, ma60,
                macd, macd_signal, macd_hist,
                kdj_k, kdj_d, kdj_j,
                rsi, boll_upper, boll_mid, boll_lower,
                pe_ratio, pb_ratio, recent_change_5d, recent_change_20d,
                volume_ratio, tech_score, fundamental_score,
                total_score, recommendation, analysis_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, data.get("stock_code"), data.get("stock_name"),
            data.get("ma5"), data.get("ma10"), data.get("ma20"), data.get("ma60"),
            data.get("macd"), data.get("macd_signal"), data.get("macd_hist"),
            data.get("kdj_k"), data.get("kdj_d"), data.get("kdj_j"),
            data.get("rsi"), data.get("boll_upper"), data.get("boll_mid"), data.get("boll_lower"),
            data.get("pe_ratio"), data.get("pb_ratio"), data.get("recent_change_5d"), data.get("recent_change_20d"),
            data.get("volume_ratio"), data.get("tech_score"), data.get("fundamental_score"),
            data.get("total_score"), data.get("recommendation"), data.get("analysis_text")
        ))
        conn.commit()
        return cursor.lastrowid
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
        return [dict(r) for r in rows]
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
        return dict(row) if row else None
    finally:
        conn.close()


# ─── 自选股 ───────────────────────────────────────────────

def add_watchlist(user_id: int, stock_code: str, stock_name: str = None):
    """添加自选股"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (user_id, stock_code, stock_name) VALUES (?, ?, ?)",
            (user_id, stock_code, stock_name)
        )
        conn.commit()
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
                   ah.total_score AS latest_score,
                   ah.recommendation AS latest_recommendation
            FROM watchlist w
            LEFT JOIN analysis_history ah
              ON ah.stock_code = w.stock_code
              AND ah.user_id = w.user_id
              AND ah.id = (
                  SELECT MAX(id) FROM analysis_history
                  WHERE stock_code = w.stock_code AND user_id = w.user_id
              )
            WHERE w.user_id = ?
            ORDER BY w.added_at DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]
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
        conn.execute("""
            INSERT OR REPLACE INTO stock_cache (code, date, open, high, low, close, volume, pe_ratio, pb_ratio, market_cap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            code, date,
            data.get("open"), data.get("high"), data.get("low"), data.get("close"),
            data.get("volume"), data.get("pe_ratio"), data.get("pb_ratio"), data.get("market_cap")
        ))
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
            cursor = conn.execute(
                """
                INSERT INTO strategy_runs (
                    strategy_key, trade_date, status, started_at, finished_at,
                    total_scanned, total_picks, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (strategy_key, trade_date, status, started, finished, total_scanned, len(recommendations), error),
            )
            run_id = cursor.lastrowid

        for idx, rec in enumerate(recommendations, start=1):
            conn.execute(
                """
                INSERT INTO strategy_recommendations (
                    run_id, strategy_key, code, name, rank, score, mode,
                    reasons_json, risks_json, metrics_json, data_status_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _json_dumps(rec.get("metrics", {})),
                    _json_dumps(rec.get("data_status", {})),
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
            group["recommendations"] = [
                {
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
                    "metrics": _json_loads(rec["metrics_json"], {}),
                    "data_status": _json_loads(rec["data_status_json"], {}),
                    "created_at": rec["created_at"],
                }
                for rec in rec_rows
            ]
            groups[key] = group
        return {
            "trade_date": resolved_date,
            "latest_trade_date": resolved_date,
            "groups": list(groups.values()),
        }
    finally:
        conn.close()
