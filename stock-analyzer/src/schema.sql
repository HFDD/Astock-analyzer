-- A股股票分析系统 数据库 Schema
-- SQLite

PRAGMA foreign_keys = ON;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    phone TEXT,
    nickname TEXT,
    avatar_url TEXT,
    role TEXT DEFAULT 'free' CHECK(role IN ('free', 'pro', 'admin')),
    credits INTEGER DEFAULT 10,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP
);

-- 用户Token表
CREATE TABLE IF NOT EXISTS user_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token TEXT UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 股票数据缓存（公共）
CREATE TABLE IF NOT EXISTS stock_cache (
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    pe_ratio REAL,
    pb_ratio REAL,
    market_cap REAL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- 分析历史记录
CREATE TABLE IF NOT EXISTS analysis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    -- 技术指标
    ma5 REAL,
    ma10 REAL,
    ma20 REAL,
    ma60 REAL,
    macd REAL,
    macd_signal REAL,
    macd_hist REAL,
    kdj_k REAL,
    kdj_d REAL,
    kdj_j REAL,
    rsi REAL,
    boll_upper REAL,
    boll_mid REAL,
    boll_lower REAL,
    -- 基本面
    pe_ratio REAL,
    pb_ratio REAL,
    recent_change_5d REAL,
    recent_change_20d REAL,
    volume_ratio REAL,
    -- 评分
    tech_score REAL,
    fundamental_score REAL,
    total_score REAL,
    recommendation TEXT,
    analysis_text TEXT,
    buy_date TEXT,
    cost_price REAL,
    strategy_exit_json TEXT,
    -- 板块热度
    sector_name TEXT,
    sector_type TEXT,
    sector_rank INTEGER,
    sector_score REAL,
    sector_heat REAL,
    sector_change_pct REAL,
    sector_heat_window TEXT,
    sector_heat_date TEXT,
    sector_source TEXT,
    sector_confidence REAL,
    sector_context_json TEXT,
    -- 自选股流量摘要
    retail_direction TEXT,
    retail_score REAL,
    flow_recommendation TEXT,
    exit_risk_score REAL,
    strategy_risk_level TEXT,
    strategy_risk_score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 自选股
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pinned_at TIMESTAMP,
    UNIQUE(user_id, stock_code)
);

-- 每日策略推荐运行记录
CREATE TABLE IF NOT EXISTS strategy_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_key TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    total_scanned INTEGER DEFAULT 0,
    total_picks INTEGER DEFAULT 0,
    error TEXT,
    UNIQUE(strategy_key, trade_date)
);

-- 每日策略推荐结果
CREATE TABLE IF NOT EXISTS strategy_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
    strategy_key TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    rank INTEGER,
    score REAL,
    mode TEXT,
    reasons_json TEXT,
    risks_json TEXT,
    metrics_json TEXT,
    data_status_json TEXT,
    -- 板块热度
    sector_name TEXT,
    sector_type TEXT,
    sector_rank INTEGER,
    sector_score REAL,
    sector_heat REAL,
    sector_change_pct REAL,
    sector_heat_window TEXT,
    sector_heat_date TEXT,
    sector_source TEXT,
    sector_confidence REAL,
    sector_context_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 每日资金/散户热度快照
CREATE TABLE IF NOT EXISTS attention_heat_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT,
    snapshot_ts REAL,
    snapshot_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 用户量化策略库
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
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_tokens_user ON user_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_token ON user_tokens(token);
CREATE INDEX IF NOT EXISTS idx_cache_code ON stock_cache(code);
CREATE INDEX IF NOT EXISTS idx_analysis_user ON analysis_history(user_id);
CREATE INDEX IF NOT EXISTS idx_analysis_code ON analysis_history(stock_code);
CREATE INDEX IF NOT EXISTS idx_analysis_time ON analysis_history(created_at);
CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_date ON strategy_runs(trade_date);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_key_date ON strategy_runs(strategy_key, trade_date);
CREATE INDEX IF NOT EXISTS idx_strategy_recommendations_run ON strategy_recommendations(run_id);
CREATE INDEX IF NOT EXISTS idx_attention_heat_snapshots_date ON attention_heat_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_user_quant_strategies_user ON user_quant_strategies(user_id);
