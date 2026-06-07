"""数据库 CRUD 操作"""

import json
import sqlite3
from typing import Optional
from backend.config import DATABASE_PATH
from backend.db.models import (
    AgentInfo, AgentPosition, AgentOrder, AgentTradeLog,
    AgentDailyReport, StrategyInfo
)


def get_conn(db_path: str = DATABASE_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_read_conn(db_path: str = DATABASE_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA query_only=ON")
    return conn


# ─── Agent CRUD ───

def list_agents(conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    c = (conn or get_conn()).cursor()
    c.execute("SELECT * FROM agent_info ORDER BY id")
    return [dict(r) for r in c.fetchall()]


def get_agent(agent_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    c = (conn or get_conn()).cursor()
    c.execute("SELECT * FROM agent_info WHERE id = ?", (agent_id,))
    r = c.fetchone()
    return dict(r) if r else None


def create_agent(data: AgentInfo, conn: Optional[sqlite3.Connection] = None) -> int:
    close_conn = conn is None
    conn = conn or get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO agent_info (name, display_name, agent_type, initial_capital, current_cash, strategy_ids, risk_config)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (data.name, data.display_name, data.agent_type, data.initial_capital, data.current_cash, data.strategy_ids, data.risk_config),
    )
    if close_conn:
        conn.commit()
    return c.lastrowid


def update_agent_cash(agent_id: int, cash: float, conn: sqlite3.Connection):
    conn.execute("UPDATE agent_info SET current_cash = ?, updated_at = datetime('now') WHERE id = ?", (cash, agent_id))


# ─── Position CRUD ───

def get_positions(agent_id: int, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    c = (conn or get_conn()).cursor()
    c.execute("SELECT * FROM agent_position WHERE agent_id = ? AND quantity > 0", (agent_id,))
    return [dict(r) for r in c.fetchall()]


def upsert_position(agent_id: int, ts_code: str, stock_name: str, quantity: int, avg_cost: float, buy_date: str, conn: sqlite3.Connection):
    existing = conn.execute("SELECT * FROM agent_position WHERE agent_id = ? AND ts_code = ?", (agent_id, ts_code)).fetchone()
    if existing:
        conn.execute(
            "UPDATE agent_position SET quantity = ?, available_shares = ?, avg_cost = ?, updated_at = datetime('now') WHERE id = ?",
            (quantity, quantity, avg_cost, existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO agent_position (agent_id, ts_code, stock_name, quantity, available_shares, avg_cost, buy_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (agent_id, ts_code, stock_name, quantity, quantity, avg_cost, buy_date),
        )


# ─── Order CRUD ───

def create_order(data: AgentOrder, conn: sqlite3.Connection) -> int:
    c = conn.cursor()
    c.execute(
        """INSERT INTO agent_order (agent_id, ts_code, stock_name, direction, order_type, quantity, price,
           trigger_price, condition_expr, open_get_in, reserved_cash, parent_order_id, oco_group,
           chase_enabled, chase_pct, split_group, split_seq, split_total, risk_control,
           decision_batch_id, fill_probability, price_aggressiveness,
           skill_id, skill_confidence, failure_attribution, evolution_mark, reason, fail_reason, trade_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data.agent_id, data.ts_code, data.stock_name, data.direction, data.order_type,
         data.quantity, data.price, data.trigger_price, data.condition_expr, 1 if data.open_get_in else 0,
         data.reserved_cash, data.parent_order_id, data.oco_group,
         1 if data.chase_enabled else 0, data.chase_pct, data.split_group, data.split_seq, data.split_total,
         1 if data.risk_control else 0, data.decision_batch_id, data.fill_probability, data.price_aggressiveness,
         data.skill_id, data.skill_confidence, data.failure_attribution,
         data.evolution_mark, data.reason, data.fail_reason, data.trade_date),
    )
    order_id = c.lastrowid
    record_order_trace(
        conn,
        order_id,
        "created",
        data.reason or "",
        status_from="",
        status_to="pending",
        payload={
            "direction": data.direction,
            "quantity": data.quantity,
            "price": data.price,
            "trigger_price": data.trigger_price,
            "condition_expr": data.condition_expr,
            "order_type": data.order_type,
            "open_get_in": bool(data.open_get_in),
            "oco_group": data.oco_group,
            "chase_enabled": bool(data.chase_enabled),
            "chase_pct": data.chase_pct,
            "split_group": data.split_group,
            "split_seq": data.split_seq,
            "split_total": data.split_total,
            "risk_control": bool(data.risk_control),
            "decision_batch_id": data.decision_batch_id,
            "fill_probability": data.fill_probability,
            "price_aggressiveness": data.price_aggressiveness,
            "skill_id": data.skill_id,
        },
    )
    return order_id


def get_pending_orders(agent_id: int, trade_date: str, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    c = (conn or get_conn()).cursor()
    c.execute("SELECT * FROM agent_order WHERE agent_id = ? AND trade_date = ? AND status = 'pending'", (agent_id, trade_date))
    return [dict(r) for r in c.fetchall()]


def record_order_trace(
    conn: sqlite3.Connection,
    order_id: int,
    event_type: str,
    reason: str = "",
    status_from: str | None = None,
    status_to: str | None = None,
    payload: dict | None = None,
) -> None:
    row = conn.execute(
        "SELECT agent_id, trade_date, status FROM agent_order WHERE id=?",
        (order_id,),
    ).fetchone()
    if not row:
        return
    conn.execute(
        """INSERT INTO agent_order_trace
           (order_id, agent_id, trade_date, event_type, status_from, status_to, reason, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            order_id,
            row["agent_id"],
            row["trade_date"],
            event_type,
            row["status"] if status_from is None else status_from,
            status_to,
            reason or "",
            json.dumps(payload or {}, ensure_ascii=False, default=str),
        ),
    )


def list_order_trace(order_id: int, conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT id, order_id, agent_id, trade_date, event_type, status_from, status_to,
                  reason, payload_json, created_at
           FROM agent_order_trace
           WHERE order_id=?
           ORDER BY created_at ASC, id ASC""",
        (order_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except Exception:
            item["payload"] = {}
        result.append(item)
    return result


def list_agent_order_trace(agent_id: int, limit: int, conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT id, order_id, agent_id, trade_date, event_type, status_from, status_to,
                  reason, payload_json, created_at
           FROM agent_order_trace
           WHERE agent_id=?
           ORDER BY created_at DESC, id DESC LIMIT ?""",
        (agent_id, max(1, int(limit or 50))),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except Exception:
            item["payload"] = {}
        result.append(item)
    return result


def refresh_decision_batch_status(conn: sqlite3.Connection, batch_id: str) -> None:
    if not batch_id:
        return
    stats = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN status='filled' THEN 1 ELSE 0 END) AS filled
           FROM agent_order WHERE decision_batch_id=?""",
        (batch_id,),
    ).fetchone()
    if not stats or int(stats["total"] or 0) <= 0:
        return
    total = int(stats["total"] or 0)
    pending = int(stats["pending"] or 0)
    filled = int(stats["filled"] or 0)
    status_text = "pending" if pending else ("filled" if filled == total else "completed")
    conn.execute(
        "UPDATE agent_decision_batch SET status=?, updated_at=datetime('now') WHERE id=?",
        (status_text, batch_id),
    )


def update_order_status(
    order_id: int,
    status: str,
    conn: sqlite3.Connection,
    fail_reason: str = "",
    event_type: str | None = None,
    payload: dict | None = None,
):
    before = conn.execute("SELECT status, decision_batch_id FROM agent_order WHERE id=?", (order_id,)).fetchone()
    old_status = before["status"] if before else ""
    if status == "filled":
        conn.execute(
            "UPDATE agent_order SET status=?, filled_at=datetime('now'), fail_reason=? WHERE id=?",
            (status, fail_reason, order_id),
        )
    elif status in ("cancelled", "expired"):
        conn.execute(
            "UPDATE agent_order SET status=?, expired_at=datetime('now'), fail_reason=? WHERE id=?",
            (status, fail_reason, order_id),
        )
    else:
        conn.execute(
            "UPDATE agent_order SET status=?, fail_reason=? WHERE id=?",
            (status, fail_reason, order_id),
        )
    record_order_trace(
        conn,
        order_id,
        event_type or status,
        fail_reason,
        status_from=old_status,
        status_to=status,
        payload=payload,
    )
    batch_id = before["decision_batch_id"] if before else ""
    refresh_decision_batch_status(conn, batch_id)


# ─── Trade Log CRUD ───

def create_trade(data: AgentTradeLog, conn: sqlite3.Connection) -> int:
    c = conn.cursor()
    c.execute(
        """INSERT INTO agent_trade_log (order_id, agent_id, ts_code, stock_name, direction, quantity, price, total_value, commission, stamp_tax, trade_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data.order_id, data.agent_id, data.ts_code, data.stock_name, data.direction, data.quantity, data.price, data.total_value, data.commission, data.stamp_tax, data.trade_date),
    )
    return c.lastrowid


def list_trades(agent_id: int, limit: int = 50, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    c = (conn or get_conn()).cursor()
    c.execute("SELECT * FROM agent_trade_log WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?", (agent_id, limit))
    return [dict(r) for r in c.fetchall()]


# ─── Daily Report CRUD ───

def upsert_daily_report(data: AgentDailyReport, conn: sqlite3.Connection):
    existing = conn.execute("SELECT id FROM agent_daily_report WHERE agent_id = ? AND trade_date = ?", (data.agent_id, data.trade_date)).fetchone()
    if existing:
        conn.execute(
            """UPDATE agent_daily_report SET cash=?, market_value=?, total_assets=?, daily_pnl=?, daily_return=?,
               cumulative_pnl=?, cumulative_return=?, position_count=?, factor_weight_log=?,
               risk_adjust_log=?, updated_at=datetime('now') WHERE id=?""",
            (data.cash, data.market_value, data.total_assets, data.daily_pnl, data.daily_return,
             data.cumulative_pnl, data.cumulative_return, data.position_count, data.factor_weight_log,
             data.risk_adjust_log, existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO agent_daily_report (agent_id, trade_date, cash, market_value, total_assets, daily_pnl,
               daily_return, cumulative_pnl, cumulative_return, position_count, factor_weight_log,
               risk_adjust_log, report_md_path, think_log_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.agent_id, data.trade_date, data.cash, data.market_value, data.total_assets,
             data.daily_pnl, data.daily_return, data.cumulative_pnl, data.cumulative_return,
             data.position_count, data.factor_weight_log, data.risk_adjust_log,
             data.report_md_path, data.think_log_path),
        )


# ─── Strategy CRUD ───

def list_strategies(conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    c = (conn or get_conn()).cursor()
    c.execute("SELECT * FROM strategy_repository ORDER BY category, id")
    return [dict(r) for r in c.fetchall()]


def create_strategy(data: StrategyInfo, conn: Optional[sqlite3.Connection] = None) -> int:
    close = conn is None
    conn = conn or get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO strategy_repository (name, description, category, params_json, code) VALUES (?, ?, ?, ?, ?)",
        (data.name, data.description, data.category, data.params_json, data.code),
    )
    if close:
        conn.commit()
    return c.lastrowid


def delete_strategy(strategy_id: int, conn: Optional[sqlite3.Connection] = None):
    close = conn is None
    conn = conn or get_conn()
    conn.execute("DELETE FROM strategy_repository WHERE id = ? AND category = 'custom'", (strategy_id,))
    if close:
        conn.commit()
