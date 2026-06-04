"""数据库建表 DDL"""

import json
import os
import shutil
import sqlite3
from datetime import datetime
from backend.config import DATABASE_PATH


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    market      TEXT,
    industry    TEXT,
    sector      TEXT,
    is_main_board INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kline_daily (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code     TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    pre_close   REAL,
    change      REAL,
    pct_chg     REAL,
    vol         REAL,
    amount      REAL,
    turnover_rate REAL,
    ma5         REAL,
    ma10        REAL,
    ma20        REAL,
    ma60        REAL,
    is_limit_up   INTEGER DEFAULT 0,
    is_limit_down INTEGER DEFAULT 0,
    UNIQUE(ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS agent_info (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    display_name TEXT,
    agent_type  TEXT DEFAULT 'custom',
    initial_capital REAL DEFAULT 150000.0,
    current_cash   REAL DEFAULT 150000.0,
    strategy_ids   TEXT,
    risk_config    TEXT,
    status      TEXT DEFAULT 'active',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_position (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES agent_info(id),
    ts_code     TEXT NOT NULL,
    stock_name  TEXT,
    quantity    INTEGER DEFAULT 0,
    available_shares INTEGER DEFAULT 0,
    avg_cost    REAL DEFAULT 0.0,
    current_price REAL DEFAULT 0.0,
    market_value  REAL DEFAULT 0.0,
    unrealized_pnl REAL DEFAULT 0.0,
    realized_pnl   REAL DEFAULT 0.0,
    buy_date    TEXT,
    updated_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, ts_code)
);

CREATE TABLE IF NOT EXISTS agent_order (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES agent_info(id),
    ts_code     TEXT NOT NULL,
    stock_name  TEXT,
    direction   TEXT NOT NULL CHECK(direction IN ('buy', 'sell')),
    order_type  TEXT DEFAULT 'limit' CHECK(order_type IN ('limit', 'stop_loss', 'stop_profit', 'condition')),
    quantity    INTEGER NOT NULL,
    price       REAL NOT NULL,
    open_get_in INTEGER DEFAULT 0,
    reserved_cash REAL DEFAULT 0.0,
    decision_batch_id TEXT,
    fill_probability REAL,
    price_aggressiveness REAL,
    skill_id    TEXT,
    skill_confidence REAL DEFAULT 0.0,
    failure_attribution TEXT,
    evolution_mark TEXT,
    reason      TEXT,
    fail_reason TEXT,
    status      TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'triggered', 'filled', 'cancelled', 'expired')),
    trade_date  TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    triggered_at TEXT,
    filled_at   TEXT,
    expired_at  TEXT
);

CREATE TABLE IF NOT EXISTS agent_trade_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER REFERENCES agent_order(id),
    agent_id    INTEGER NOT NULL REFERENCES agent_info(id),
    ts_code     TEXT NOT NULL,
    stock_name  TEXT,
    direction   TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    price       REAL NOT NULL,
    total_value REAL NOT NULL,
    commission  REAL DEFAULT 0.0,
    stamp_tax   REAL DEFAULT 0.0,
    trade_date  TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_order_trace (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES agent_order(id),
    agent_id    INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date  TEXT,
    event_type  TEXT NOT NULL,
    status_from TEXT,
    status_to   TEXT,
    reason      TEXT,
    payload_json TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_decision_batch (
    id          TEXT PRIMARY KEY,
    agent_id    INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date  TEXT NOT NULL,
    next_trade_date TEXT,
    order_count INTEGER DEFAULT 0,
    buy_count   INTEGER DEFAULT 0,
    sell_count  INTEGER DEFAULT 0,
    avg_fill_probability REAL,
    status      TEXT DEFAULT 'planned',
    summary     TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shared_context (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id       INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date     TEXT NOT NULL,
    market_regime  TEXT DEFAULT 'unknown',
    confidence     REAL DEFAULT 0.0,
    summary        TEXT,
    payload_json   TEXT DEFAULT '{}',
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, trade_date)
);

CREATE TABLE IF NOT EXISTS agent_daily_report (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date  TEXT NOT NULL,
    cash        REAL,
    market_value REAL,
    total_assets  REAL,
    daily_pnl     REAL DEFAULT 0.0,
    daily_return  REAL DEFAULT 0.0,
    cumulative_pnl REAL DEFAULT 0.0,
    cumulative_return REAL DEFAULT 0.0,
    position_count  INTEGER DEFAULT 0,
    factor_weight_log TEXT,
    risk_adjust_log TEXT,
    report_md_path TEXT,
    think_log_path TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, trade_date)
);

CREATE TABLE IF NOT EXISTS strategy_repository (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    category    TEXT DEFAULT 'custom' CHECK(category IN ('builtin', 'custom')),
    params_json TEXT,
    code        TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_kline_date ON kline_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_kline_code ON kline_daily(ts_code);
CREATE INDEX IF NOT EXISTS idx_kline_code_date ON kline_daily(ts_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_order_agent ON agent_order(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_order_date ON agent_order(trade_date);
CREATE INDEX IF NOT EXISTS idx_order_trace_order ON agent_order_trace(order_id, created_at);
CREATE INDEX IF NOT EXISTS idx_order_trace_agent ON agent_order_trace(agent_id, trade_date, created_at);
CREATE INDEX IF NOT EXISTS idx_decision_batch_agent ON agent_decision_batch(agent_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_shared_context_date ON agent_shared_context(trade_date, agent_id);
CREATE INDEX IF NOT EXISTS idx_trade_agent ON agent_trade_log(agent_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_position_agent ON agent_position(agent_id);
CREATE INDEX IF NOT EXISTS idx_report_agent ON agent_daily_report(agent_id, trade_date);

CREATE TABLE IF NOT EXISTS agent_evolution_skill (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    skill_id        TEXT NOT NULL,
    skill_name      TEXT NOT NULL,
    market_scene    TEXT,
    confidence_score REAL DEFAULT 0.5,
    recent_fail_rate REAL DEFAULT 0.0,
    dynamic_params  TEXT DEFAULT '{}',
    invalid_scene   TEXT DEFAULT '[]',
    evolution_record TEXT,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, skill_id)
);

CREATE TABLE IF NOT EXISTS agent_evolution_event (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    summary         TEXT,
    payload_json    TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_race_metric (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date      TEXT NOT NULL,
    benchmark_return REAL DEFAULT 0.0,
    excess_return   REAL DEFAULT 0.0,
    max_drawdown    REAL DEFAULT 0.0,
    sharpe_ratio    REAL DEFAULT 0.0,
    calmar_ratio    REAL DEFAULT 0.0,
    win_rate        REAL DEFAULT 0.0,
    profit_factor   REAL DEFAULT 0.0,
    beta_score      REAL DEFAULT 0.0,
    alpha_score     REAL DEFAULT 0.0,
    race_score      REAL DEFAULT 0.0,
    risk_cap        REAL DEFAULT 0.8,
    style_tag       TEXT,
    detail_json     TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, trade_date)
);

CREATE TABLE IF NOT EXISTS agent_eval_metric (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date      TEXT NOT NULL,
    daily_return    REAL DEFAULT 0.0,
    cumulative_return REAL DEFAULT 0.0,
    benchmark_return REAL DEFAULT 0.0,
    excess_return   REAL DEFAULT 0.0,
    alpha_score     REAL DEFAULT 0.0,
    max_drawdown    REAL DEFAULT 0.0,
    volatility      REAL DEFAULT 0.0,
    downside_volatility REAL DEFAULT 0.0,
    var_95          REAL DEFAULT 0.0,
    cvar_95         REAL DEFAULT 0.0,
    win_rate        REAL DEFAULT 0.0,
    profit_factor   REAL DEFAULT 0.0,
    avg_holding_days REAL DEFAULT 0.0,
    turnover_rate   REAL DEFAULT 0.0,
    order_fill_rate REAL DEFAULT 0.0,
    pending_expire_rate REAL DEFAULT 0.0,
    open_get_in_success_rate REAL DEFAULT 0.0,
    llm_calls       INTEGER DEFAULT 0,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    tool_calls      INTEGER DEFAULT 0,
    tool_failures   INTEGER DEFAULT 0,
    tool_failure_rate REAL DEFAULT 0.0,
    llm_latency_ms  REAL DEFAULT 0.0,
    decision_latency_ms REAL DEFAULT 0.0,
    json_parse_failures INTEGER DEFAULT 0,
    price_repair_count INTEGER DEFAULT 0,
    memory_compressions INTEGER DEFAULT 0,
    skill_confidence_delta REAL DEFAULT 0.0,
    system_doc_updates INTEGER DEFAULT 0,
    reflection_triggers INTEGER DEFAULT 0,
    quality_json_ok  INTEGER DEFAULT 0,
    quality_required_tools INTEGER DEFAULT 0,
    quality_tool_evidence INTEGER DEFAULT 0,
    quality_risk_explained INTEGER DEFAULT 0,
    detail_json     TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, trade_date)
);

CREATE TABLE IF NOT EXISTS agent_capital_policy (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date      TEXT NOT NULL,
    max_total_position REAL DEFAULT 0.8,
    max_single_position REAL DEFAULT 0.15,
    disabled_reason TEXT,
    updated_by      TEXT DEFAULT 'race_engine',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, trade_date)
);

CREATE TABLE IF NOT EXISTS agent_reflection_task (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    trade_date      TEXT NOT NULL,
    task_type       TEXT NOT NULL,
    trigger_reason  TEXT,
    status          TEXT DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed','skipped')),
    input_json      TEXT DEFAULT '{}',
    output_md       TEXT,
    system_doc_path TEXT,
    version         TEXT,
    error           TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    started_at      TEXT,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS telegram_recommend_skill (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id        TEXT UNIQUE NOT NULL,
    skill_name      TEXT NOT NULL,
    confidence_score REAL DEFAULT 0.5,
    recent_hit_rate REAL DEFAULT 0.0,
    user_fit_tags   TEXT DEFAULT '[]',
    prompt_template TEXT,
    evolution_record TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS telegram_recommend_feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT NOT NULL,
    username        TEXT,
    query           TEXT,
    ts_code         TEXT,
    stock_name      TEXT,
    agent_id        INTEGER,
    source_agent_name TEXT,
    source_section  TEXT,
    source_summary  TEXT,
    skill_id        TEXT,
    skill_confidence REAL DEFAULT 0.0,
    recommend_price REAL DEFAULT 0.0,
    return_1d       REAL,
    return_3d       REAL,
    return_5d       REAL,
    trace_json      TEXT DEFAULT '{}',
    recommendation_json TEXT DEFAULT '{}',
    feedback_type   TEXT DEFAULT 'recommended',
    feedback_text   TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS telegram_recommend_eval (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT NOT NULL,
    username        TEXT,
    query           TEXT,
    recommendation_ids TEXT DEFAULT '[]',
    intent          TEXT,
    response_text   TEXT,
    trace_json      TEXT DEFAULT '{}',
    trace_complete  INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'ok',
    fallback_used   INTEGER DEFAULT 0,
    llm_calls       INTEGER DEFAULT 0,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    tool_calls      INTEGER DEFAULT 0,
    tool_failures   INTEGER DEFAULT 0,
    tool_failure_rate REAL DEFAULT 0.0,
    response_latency_ms REAL DEFAULT 0.0,
    json_parse_ok   INTEGER DEFAULT 0,
    positive_count  INTEGER DEFAULT 0,
    negative_count  INTEGER DEFAULT 0,
    risk_too_high_count INTEGER DEFAULT 0,
    risk_too_low_count INTEGER DEFAULT 0,
    adoption_rate   REAL DEFAULT 0.0,
    followup_count  INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS telegram_recommend_outcome (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL REFERENCES telegram_recommend_feedback(id),
    ts_code         TEXT NOT NULL,
    base_trade_date TEXT,
    base_price      REAL DEFAULT 0.0,
    return_1d       REAL,
    return_3d       REAL,
    return_5d       REAL,
    benchmark_return_1d REAL,
    benchmark_return_3d REAL,
    benchmark_return_5d REAL,
    beat_benchmark_1d INTEGER,
    beat_benchmark_3d INTEGER,
    beat_benchmark_5d INTEGER,
    sector_return_1d REAL,
    sector_return_3d REAL,
    sector_return_5d REAL,
    beat_sector_1d  INTEGER,
    beat_sector_3d  INTEGER,
    beat_sector_5d  INTEGER,
    max_adverse_excursion REAL DEFAULT 0.0,
    status          TEXT DEFAULT 'pending',
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(recommendation_id)
);

CREATE TABLE IF NOT EXISTS telegram_recommend_cost (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    eval_id         INTEGER REFERENCES telegram_recommend_eval(id),
    chat_id         TEXT NOT NULL,
    model           TEXT,
    llm_calls       INTEGER DEFAULT 0,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    tool_calls      INTEGER DEFAULT 0,
    tool_failures   INTEGER DEFAULT 0,
    response_latency_ms REAL DEFAULT 0.0,
    cost_json       TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shared_stock_report (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code         TEXT NOT NULL,
    stock_name      TEXT,
    chat_id         TEXT,
    username        TEXT,
    source          TEXT DEFAULT 'telegram',
    user_intent     TEXT,
    user_preference TEXT,
    sector          TEXT,
    report_md       TEXT,
    report_path     TEXT,
    recommend_view  TEXT,
    mention_count   INTEGER DEFAULT 1,
    last_mentioned_at TEXT DEFAULT (datetime('now')),
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(ts_code, chat_id)
);

CREATE TABLE IF NOT EXISTS memory_compression_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT NOT NULL,
    agent_id        INTEGER,
    file_path       TEXT,
    reason          TEXT,
    before_chars    INTEGER DEFAULT 0,
    after_chars     INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_evo_skill_agent ON agent_evolution_skill(agent_id, enabled);
CREATE INDEX IF NOT EXISTS idx_evo_event_agent ON agent_evolution_event(agent_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_race_metric_agent ON agent_race_metric(agent_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_agent_eval_agent ON agent_eval_metric(agent_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_reflection_agent ON agent_reflection_task(agent_id, trade_date, status);
CREATE INDEX IF NOT EXISTS idx_tg_feedback_chat ON telegram_recommend_feedback(chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tg_eval_chat ON telegram_recommend_eval(chat_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tg_outcome_rec ON telegram_recommend_outcome(recommendation_id, status);
CREATE INDEX IF NOT EXISTS idx_shared_stock_report_code ON shared_stock_report(ts_code, updated_at);

CREATE TABLE IF NOT EXISTS backtest_task (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name   TEXT NOT NULL,
    params_json     TEXT DEFAULT '{}',
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    initial_capital REAL DEFAULT 150000.0,
    stop_loss_pct   REAL DEFAULT -8.0,
    metrics_json    TEXT,
    equity_curve_json TEXT,
    trades_json     TEXT,
    log_json        TEXT,
    log_file        TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS system_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS simulation_task (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    agents_config   TEXT NOT NULL,
    status          TEXT DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed')),
    progress        REAL DEFAULT 0,
    results_json    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS agent_schedule (
    agent_id        INTEGER PRIMARY KEY REFERENCES agent_info(id),
    enabled         INTEGER DEFAULT 0,
    review_time     TEXT DEFAULT '23:00',
    push_time       TEXT DEFAULT '23:00',
    timezone        TEXT DEFAULT 'Asia/Shanghai',
    last_run_date   TEXT,
    retry_count     INTEGER DEFAULT 0,
    next_retry_at   TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_stock_pool (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    ts_code         TEXT NOT NULL,
    stock_name      TEXT,
    note            TEXT,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, ts_code)
);

CREATE TABLE IF NOT EXISTS agent_user_strategy_version (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    version_no      INTEGER NOT NULL,
    strategy_text   TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_agent_stock_pool_agent ON agent_stock_pool(agent_id, enabled);
CREATE INDEX IF NOT EXISTS idx_agent_user_strategy_agent ON agent_user_strategy_version(agent_id, version_no);

CREATE TABLE IF NOT EXISTS telegram_binding (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agent_info(id),
    chat_id         TEXT NOT NULL,
    username        TEXT,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, chat_id)
);

CREATE TABLE IF NOT EXISTS telegram_user_profile (
    chat_id         TEXT PRIMARY KEY,
    username        TEXT,
    risk_level      TEXT DEFAULT '中等',
    horizon         TEXT DEFAULT '短线',
    preferred_sectors TEXT DEFAULT '[]',
    excluded_sectors  TEXT DEFAULT '[]',
    max_results     INTEGER DEFAULT 5,
    daily_push_enabled INTEGER DEFAULT 0,
    last_summary    TEXT,
    profile_json    TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS telegram_watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT NOT NULL,
    ts_code         TEXT NOT NULL,
    stock_name      TEXT,
    note            TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(chat_id, ts_code)
);

CREATE TABLE IF NOT EXISTS telegram_conversation_message (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT DEFAULT 'telegram',
    chat_id         TEXT NOT NULL,
    user_id         TEXT,
    thread_id       TEXT DEFAULT 'default',
    chat_type       TEXT,
    role            TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'system')),
    content         TEXT NOT NULL,
    intent          TEXT,
    metadata_json   TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS telegram_memory_item (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT NOT NULL CHECK(scope IN ('user', 'chat', 'thread', 'global')),
    scope_id        TEXT NOT NULL,
    memory_type     TEXT DEFAULT 'fact',
    content         TEXT NOT NULL,
    keywords        TEXT DEFAULT '',
    importance      REAL DEFAULT 0.5,
    status          TEXT DEFAULT 'active',
    superseded_by_id INTEGER,
    archived_at     TEXT,
    last_reason     TEXT DEFAULT '',
    last_used_at    TEXT,
    source_message_id INTEGER REFERENCES telegram_conversation_message(id),
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(scope, scope_id, memory_type, content)
);

CREATE TABLE IF NOT EXISTS telegram_memory_distill_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT NOT NULL,
    user_id         TEXT DEFAULT '',
    thread_id       TEXT DEFAULT 'default',
    chat_type       TEXT DEFAULT '',
    last_message_id INTEGER DEFAULT 0,
    last_distilled_message_id INTEGER DEFAULT 0,
    message_count   INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'idle',
    last_error      TEXT DEFAULT '',
    last_result_json TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(chat_id, user_id, thread_id)
);

CREATE TABLE IF NOT EXISTS telegram_session_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         TEXT NOT NULL,
    user_id         TEXT DEFAULT '',
    thread_id       TEXT DEFAULT 'default',
    chat_type       TEXT DEFAULT '',
    summary         TEXT NOT NULL,
    facts_json      TEXT DEFAULT '{}',
    last_message_id INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(chat_id, user_id, thread_id)
);

CREATE INDEX IF NOT EXISTS idx_telegram_msg_chat_thread
    ON telegram_conversation_message(chat_id, thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_telegram_msg_user
    ON telegram_conversation_message(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_telegram_memory_scope
    ON telegram_memory_item(scope, scope_id, status, importance, updated_at);
CREATE INDEX IF NOT EXISTS idx_telegram_memory_type
    ON telegram_memory_item(memory_type, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_telegram_memory_distill_state
    ON telegram_memory_distill_state(chat_id, user_id, thread_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_telegram_session_summary_scope
    ON telegram_session_summary(chat_id, user_id, thread_id, updated_at);
"""


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _backup_existing_db_once(db_path: str):
    if not os.path.exists(db_path):
        return
    marker = f"{db_path}.migration_backup_done"
    if os.path.exists(marker):
        return
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = f"{db_path}.bak_{stamp}"
    shutil.copy2(db_path, backup_path)
    with open(marker, "w", encoding="utf-8") as f:
        f.write(backup_path)


def _migrate_existing_schema(conn: sqlite3.Connection):
    _add_column_if_missing(conn, "stock_basic", "sector_tag", "sector_tag TEXT")
    _add_column_if_missing(conn, "stock_basic", "industry_tag", "industry_tag TEXT")
    _add_column_if_missing(conn, "agent_info", "schedule_enabled", "schedule_enabled INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "agent_info", "review_time", "review_time TEXT DEFAULT '23:00'")
    _add_column_if_missing(conn, "agent_info", "push_time", "push_time TEXT DEFAULT '23:00'")
    _add_column_if_missing(conn, "agent_schedule", "retry_count", "retry_count INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "agent_schedule", "next_retry_at", "next_retry_at TEXT")
    _add_column_if_missing(conn, "agent_daily_report", "updated_at", "updated_at TEXT")
    _add_column_if_missing(conn, "agent_daily_report", "daily_return", "daily_return REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "agent_daily_report", "cumulative_pnl", "cumulative_pnl REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "agent_daily_report", "factor_weight_log", "factor_weight_log TEXT")
    _add_column_if_missing(conn, "agent_daily_report", "risk_adjust_log", "risk_adjust_log TEXT")
    _add_column_if_missing(conn, "agent_order", "open_get_in", "open_get_in INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "agent_order", "reserved_cash", "reserved_cash REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "agent_order", "decision_batch_id", "decision_batch_id TEXT")
    _add_column_if_missing(conn, "agent_order", "fill_probability", "fill_probability REAL")
    _add_column_if_missing(conn, "agent_order", "price_aggressiveness", "price_aggressiveness REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_order_batch ON agent_order(decision_batch_id)")
    _add_column_if_missing(conn, "agent_order", "skill_id", "skill_id TEXT")
    _add_column_if_missing(conn, "agent_order", "skill_confidence", "skill_confidence REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "agent_order", "failure_attribution", "failure_attribution TEXT")
    _add_column_if_missing(conn, "agent_order", "evolution_mark", "evolution_mark TEXT")
    _add_column_if_missing(conn, "agent_order", "reason", "reason TEXT")
    _add_column_if_missing(conn, "agent_order", "fail_reason", "fail_reason TEXT")
    _add_column_if_missing(conn, "agent_order", "expired_at", "expired_at TEXT")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "agent_id", "agent_id INTEGER")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "source_agent_name", "source_agent_name TEXT")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "source_section", "source_section TEXT")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "source_summary", "source_summary TEXT")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "recommend_price", "recommend_price REAL DEFAULT 0.0")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "return_1d", "return_1d REAL")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "return_3d", "return_3d REAL")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "return_5d", "return_5d REAL")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "trace_json", "trace_json TEXT DEFAULT '{}'")
    _add_column_if_missing(conn, "telegram_recommend_feedback", "recommendation_json", "recommendation_json TEXT DEFAULT '{}'")
    _add_column_if_missing(conn, "shared_stock_report", "recommend_view", "recommend_view TEXT")
    _add_column_if_missing(conn, "shared_stock_report", "mention_count", "mention_count INTEGER DEFAULT 1")
    _add_column_if_missing(conn, "telegram_memory_item", "status", "status TEXT DEFAULT 'active'")
    _add_column_if_missing(conn, "telegram_memory_item", "superseded_by_id", "superseded_by_id INTEGER")
    _add_column_if_missing(conn, "telegram_memory_item", "archived_at", "archived_at TEXT")
    _add_column_if_missing(conn, "telegram_memory_item", "last_reason", "last_reason TEXT DEFAULT ''")


def init_db(db_path: str = DATABASE_PATH) -> sqlite3.Connection:
    _backup_existing_db_once(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate_existing_schema(conn)
    conn.commit()

    # Seed default agents
    _seed_default_agents(conn)

    # Seed builtin strategies
    _seed_builtin_strategies(conn)

    # Enable built-in Agent schedules so the daily pipeline can run after setup.
    _enable_default_agent_schedules(conn)

    conn.commit()
    return conn


def _seed_default_agents(conn: sqlite3.Connection):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM agent_info")
    if cursor.fetchone()[0] == 0:
        def cfg(reasoning: str, style: str, preferred: list[str]) -> str:
            return json.dumps({
                "max_position_count": 5,
                "max_daily_loss": 0.05,
                "reasoning_effort": reasoning,
                "max_tool_turns": 8,
                "style_prompt": style,
                "preferred_strategies": preferred,
                "board_permission_mode": "auto",
                "board_permissions": {"main_sme": True, "chinext": False, "star": False, "bj": False},
                "stage_prompts": {
                    "market_scan": "先判断大盘、市场宽度、板块温度和政策方向，再决定 risk-on/neutral/risk-off。",
                    "stock_selection": "打板与多头均线发散并重；候选股必须结合热点板块、业务基本面、量价和流动性二次筛选。",
                    "risk_control": "行情 risk-on 可适当提高进攻仓位；行情差轻仓或空仓。参考赛马指标、技能置信度和失败订单记录，自主解释仓位。",
                    "order_plan": "挂单价必须先计算涨跌幅并校验；换仓必须说明非原子顺序风险。",
                    "reflection": "复盘时把已验证规律写入记忆，删除没有证据的判断。",
                },
            }, ensure_ascii=False)

        chaser_style = (
            "追高打板情绪猎手：偏短线强势与情绪周期，但不机械追逐所有涨停。"
            "必须同时关注多头均线发散、右侧趋势、板块温度、封板质量、换手、炸板风险和次日离场条件；行情 risk-on 时可更积极。"
        )
        autonomous_style = (
            "全因子自主决策交易者：综合政策、基本面、技术、资金、板块温度与情绪，不简单复制追高打板候选。"
            "优先寻找多头均线发散、回踩支撑、热点共振的右侧机会；行情好适度进攻，行情差控制仓位。"
        )
        cursor.executemany(
            "INSERT INTO agent_info (name, display_name, agent_type, strategy_ids, risk_config) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "agent_chaser",
                    "追高打板Agent",
                    "chaser",
                    "momentum",
                    cfg("high", chaser_style, ["momentum"]),
                ),
                (
                    "agent_autonomous",
                    "自主决策Agent",
                    "autonomous",
                    "",
                    cfg("high", autonomous_style, []),
                ),
                (
                    "agent_deepthink",
                    "深度推理Agent",
                    "autonomous",
                    "",
                    cfg("max", autonomous_style, []),
                ),
            ],
        )


def _enable_default_agent_schedules(conn: sqlite3.Connection):
    """Turn on default Agent review schedules if they have not been configured."""
    rows = conn.execute(
        """SELECT id, schedule_enabled, review_time, push_time
           FROM agent_info
           WHERE name IN ('agent_chaser', 'agent_autonomous', 'agent_deepthink')"""
    ).fetchall()
    for row in rows:
        agent_id, _enabled, review_time_value, push_time_value = row
        review_time = review_time_value or "23:00"
        push_time = push_time_value or "23:00"
        conn.execute(
            """UPDATE agent_info
               SET schedule_enabled=1, review_time=?, push_time=?, updated_at=datetime('now')
               WHERE id=?""",
            (review_time, push_time, agent_id),
        )
        conn.execute(
            """INSERT INTO agent_schedule (agent_id, enabled, review_time, push_time)
               VALUES (?, 1, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET
               enabled=1, review_time=excluded.review_time, push_time=excluded.push_time,
               updated_at=datetime('now')""",
            (agent_id, review_time, push_time),
        )


def _seed_builtin_strategies(conn: sqlite3.Connection):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM strategy_repository")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO strategy_repository (name, description, category, params_json) VALUES (?, ?, ?, ?)",
            [
                (
                    "龙头打板战法",
                    "追踪涨停龙头股，分析连板阶段、换手率变化，识别妖股中后期资金接力机会",
                    "builtin",
                    '{"min_limit_up_days": 1, "min_turnover_ratio": 0.03, "max_consecutive_limit": 8}',
                ),
                (
                    "动量趋势策略",
                    "识别多波趋势上涨行情，捕捉健康回调后的二次启动点",
                    "builtin",
                    '{"wave_min_pct": 10, "pullback_max_pct": 15, "volume_confirm": true}',
                ),
                (
                    "20/60均线回调企稳策略",
                    "股价回调至20日或60日均线附近企稳，成交量萎缩后放量反弹",
                    "builtin",
                    '{"ma_periods": [20, 60], "volume_shrink_ratio": 0.5, "pullback_within_pct": 5}',
                ),
                (
                    "MA5/MA20金叉策略",
                    "MA5上穿MA20形成金叉，成交量放大确认，短线看多",
                    "builtin",
                    '{"volume_confirm_ratio": 1.2, "cross_within_days": 3}',
                ),
                (
                    "MACD策略",
                    "DIF上穿DEA金叉信号，结合均线趋势确认，中线操作",
                    "builtin",
                    '{"fast": 12, "slow": 26, "signal": 9, "min_hist_strength": 0.05}',
                ),
                (
                    "K线回踩MA20策略",
                    "股价从高点回落至20日均线附近企稳，缩量后放量反弹",
                    "builtin",
                    '{"pullback_pct": 3.0, "peak_lookback": 15, "shrink_ratio": 0.7, "expand_ratio": 1.5}',
                ),
                (
                    "横盘突破策略",
                    "价格窄幅盘整后放量大阳线突破，捕捉趋势启动点",
                    "builtin",
                    '{"amp_max_pct": 5.0, "window_min": 12, "window_max": 25, "vol_expand": 1.3, "breakout_pct": 4.0}',
                ),
                (
                    "长期上升趋势策略",
                    "筛选MA多头排列+高点上移+低点上移的慢牛股，不依赖涨停",
                    "builtin",
                    '{"trend_check_days": 60, "ma_deviation_max": 30.0, "min_trend_slope": 5.0}',
                ),
                (
                    "底部放量反转策略",
                    "长期下跌后放量企稳，识别潜在趋势反转点",
                    "builtin",
                    '{"decline_pct": 20.0, "decline_days": 60, "vol_expand_ratio": 1.3, "stabilize_days": 5}',
                ),
                (
                    "箱体震荡策略",
                    "识别箱体高低点，箱底附近买入做波段操作",
                    "builtin",
                    '{"box_days": 30, "amp_min": 8.0, "amp_max": 30.0, "touch_tolerance": 3.0}',
                ),
                (
                    "情绪周期策略",
                    "换手率极值判断市场情绪，低情绪区逆势布局",
                    "builtin",
                    '{"low_sentiment_percentile": 25.0, "ma_deviation_max": 8.0, "sentiment_recovery": true}',
                ),
                (
                    "缩量回踩短均策略",
                    "缩量回踩MA5/MA10，趋势延续入场，短线操作",
                    "builtin",
                    '{"pullback_pct": 2.0, "shrink_ratio": 0.7, "ma_period": 10, "min_trend_days": 5}',
                ),
                (
                    "一阳夹三阴策略",
                    "大阳线后3根缩量小阴线调整，再出阳线确认，趋势延续",
                    "builtin",
                    '{"yang_pct_min": 3.0, "yin_pct_max": 2.0, "vol_shrink_ratio": 0.6, "confirm_yang_min": 1.5}',
                ),
            ],
        )


if __name__ == "__main__":
    conn = init_db()
    print(f"Database initialized: {DATABASE_PATH}")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cursor.fetchall()]
    print(f"Tables: {tables}")
    conn.close()
