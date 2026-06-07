"""条件单撮合引擎

根据当日行情判定条件单是否触发、撮合成交。
"""

import sqlite3
from typing import Optional
from backend.trading.rules import (
    match_order_price, can_buy, can_sell,
    calc_buy_fee, calc_sell_fee, is_one_side_limit, is_st_value,
)
from backend.trading.calculator import calc_weighted_avg_cost
from backend.db.repository import (
    get_conn, get_positions, get_pending_orders,
    record_order_trace, update_order_status, upsert_position, update_agent_cash,
)


def execute_orders(agent_id: int, trade_date: str,
                   price_data: dict[str, dict]) -> list[dict]:
    """撮合执行 Agent 的所有待触发条件单

    Args:
        agent_id: Agent ID
        trade_date: 交易日
        price_data: {ts_code: {open, high, low, close, pct_chg}}

    Returns:
        成交记录列表
    """
    conn = get_conn()
    trades = []

    orders = get_pending_orders(agent_id, trade_date, conn)
    if not orders:
        conn.close()
        return trades

    # 获取当前持仓和资金
    positions = {p["ts_code"]: p for p in get_positions(agent_id, conn)}
    agent = conn.execute("SELECT * FROM agent_info WHERE id = ?", (agent_id,)).fetchone()
    cash = agent["current_cash"]

    def refresh_cash() -> float:
        row = conn.execute("SELECT current_cash FROM agent_info WHERE id = ?", (agent_id,)).fetchone()
        return float(row["current_cash"] or 0) if row else 0.0

    for order in orders:
        current_status = conn.execute("SELECT status FROM agent_order WHERE id=?", (order["id"],)).fetchone()
        if current_status and current_status["status"] != "pending":
            continue
        ts_code = order["ts_code"]
        if ts_code not in price_data:
            _expire_order(order, "缺少当日行情数据", conn)
            cash = refresh_cash()
            continue

        price_info = price_data[ts_code]
        open_p = price_info["open"]
        high_p = price_info["high"]
        low_p = price_info["low"]
        close_p = price_info["close"]
        pct = price_info.get("pct_chg", 0)
        is_st = is_st_value(price_info.get("is_st", 0))

        # 一字板检查
        if is_one_side_limit(open_p, high_p, low_p, close_p, pct, is_st):
            direction_text = "涨停" if pct > 0 else "跌停"
            _expire_order(order, f"一字{direction_text}，当日禁止交易", conn)
            cash = refresh_cash()
            continue

        # 条件单/限价单撮合检查
        matched, exec_price, match_reason = _match_order(order, open_p, low_p, high_p)
        if not matched:
            _expire_order(order, match_reason, conn)
            cash = refresh_cash()
            continue
        if order.get("order_type") in ("stop_loss", "stop_profit", "condition"):
            conn.execute(
                "UPDATE agent_order SET status='triggered', triggered_at=datetime('now') WHERE id=?",
                (order["id"],),
            )
            record_order_trace(
                conn,
                order["id"],
                "triggered",
                match_reason,
                status_from="pending",
                status_to="triggered",
                payload={
                    "order_type": order.get("order_type"),
                    "trigger_price": order.get("trigger_price"),
                    "condition_expr": order.get("condition_expr") or "",
                },
            )
        record_order_trace(
            conn,
            order["id"],
            "matched",
            match_reason,
            payload={
                "exec_price": exec_price,
                "open": open_p,
                "low": low_p,
                "high": high_p,
                "close": close_p,
            },
        )

        direction = order["direction"]
        quantity = order["quantity"]
        total_value = exec_price * quantity

        if direction == "buy":
            # 买入检查
            ok, reason = can_buy(ts_code, order.get("stock_name", ""),
                                 open_p, high_p, low_p, close_p, pct)
            if not ok:
                _expire_order(order, reason, conn)
                cash = refresh_cash()
                continue

            fee = calc_buy_fee(total_value)
            total_cost = total_value + fee["total_cost"]
            reserved_cash = float(order.get("reserved_cash") or 0)
            available_cash = cash + reserved_cash
            if available_cash < total_cost:
                _expire_order(order, "冻结资金不足，无法成交", conn)
                cash = refresh_cash()
                continue

            # 买单入库时已冻结限价成本；成交后只释放未使用差额。
            cash = available_cash - total_cost
            update_agent_cash(agent_id, cash, conn)

            # 更新持仓
            pos = positions.get(ts_code)
            if pos:
                new_qty = pos["quantity"] + quantity
                new_cost = calc_weighted_avg_cost(
                    pos["quantity"], pos["avg_cost"], quantity, exec_price
                )
                upsert_position(agent_id, ts_code, order.get("stock_name", ""),
                               new_qty, new_cost, trade_date, conn)
                positions[ts_code] = dict(pos)
                positions[ts_code]["quantity"] = new_qty
                positions[ts_code]["avg_cost"] = new_cost
            else:
                upsert_position(agent_id, ts_code, order.get("stock_name", ""),
                               quantity, exec_price, trade_date, conn)
                positions[ts_code] = {
                    "ts_code": ts_code,
                    "stock_name": order.get("stock_name", ""),
                    "quantity": quantity,
                    "available_shares": quantity,
                    "avg_cost": exec_price,
                    "buy_date": trade_date,
                }

        else:  # sell
            # 卖出检查
            pos = positions.get(ts_code)
            if not pos or pos["available_shares"] < quantity:
                _expire_order(order, "可卖股份不足", conn)
                cash = refresh_cash()
                continue

            ok, reason = can_sell(ts_code, pos.get("buy_date", trade_date),
                                  trade_date, open_p, high_p, low_p, close_p, pct)
            if not ok:
                _expire_order(order, reason, conn)
                cash = refresh_cash()
                continue

            fee = calc_sell_fee(total_value)
            cash += total_value - fee["total_cost"]
            update_agent_cash(agent_id, cash, conn)

            # 更新持仓
            new_qty = pos["quantity"] - quantity
            if new_qty <= 0:
                conn.execute("DELETE FROM agent_position WHERE id = ?", (pos["id"],))
                positions.pop(ts_code, None)
            else:
                upsert_position(agent_id, ts_code, order.get("stock_name", ""),
                               new_qty, pos["avg_cost"], pos.get("buy_date", trade_date), conn)
                positions[ts_code] = dict(pos)
                positions[ts_code]["quantity"] = new_qty

        # 记录交易
        trade_row = {
            "order_id": order["id"],
            "agent_id": agent_id,
            "ts_code": ts_code,
            "stock_name": order.get("stock_name", ""),
            "direction": direction,
            "quantity": quantity,
            "price": exec_price,
            "total_value": total_value,
            "commission": fee.get("commission", 0),
            "stamp_tax": fee.get("stamp_tax", 0),
            "trade_date": trade_date,
            "reason": order.get("reason", ""),
            "open_get_in": bool(order.get("open_get_in")),
        }

        conn.execute(
            """INSERT INTO agent_trade_log (order_id, agent_id, ts_code, stock_name, direction, quantity, price, total_value, commission, stamp_tax, trade_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade_row["order_id"], trade_row["agent_id"], trade_row["ts_code"], trade_row["stock_name"],
             trade_row["direction"], trade_row["quantity"], trade_row["price"], trade_row["total_value"],
             trade_row["commission"], trade_row["stamp_tax"], trade_row["trade_date"]),
        )

        # 更新状态
        conn.execute("UPDATE agent_order SET reserved_cash=0 WHERE id=?", (order["id"],))
        update_order_status(
            order["id"],
            "filled",
            conn,
            event_type="filled",
            payload={"exec_price": exec_price, "total_value": total_value},
        )
        _cancel_oco_siblings(order, conn)
        trades.append(trade_row)

    conn.commit()
    conn.close()
    return trades


def _match_order(order: dict, open_p: float, low_p: float, high_p: float) -> tuple[bool, float, str]:
    order_price = float(order["price"] or 0)
    direction = order.get("direction")
    order_type = str(order.get("order_type") or "limit")
    trigger_price = float(order.get("trigger_price") or order_price or 0)
    if order_type in ("stop_loss", "stop_profit", "condition"):
        triggered = False
        exec_price = trigger_price
        if order_type == "stop_loss":
            if direction == "sell":
                triggered = low_p <= trigger_price
                exec_price = open_p if open_p <= trigger_price else trigger_price
            else:
                triggered = high_p >= trigger_price
                exec_price = open_p if open_p >= trigger_price else trigger_price
        elif order_type == "stop_profit":
            if direction == "sell":
                triggered = high_p >= trigger_price
                exec_price = open_p if open_p >= trigger_price else trigger_price
            else:
                triggered = low_p <= trigger_price
                exec_price = open_p if open_p <= trigger_price else trigger_price
        else:
            triggered = low_p <= trigger_price <= high_p
            exec_price = trigger_price
        if triggered:
            return True, round(float(exec_price), 2), f"{order_type} 触发价{trigger_price:.2f}成交"
        return False, 0.0, f"{order_type} 触发价{trigger_price:.2f}未触发，当日区间{low_p:.2f}-{high_p:.2f}"

    if int(order.get("open_get_in") or 0):
        if direction == "buy" and open_p <= order_price:
            return True, open_p, "open_get_in 开盘买入成交"
        if direction == "sell" and open_p >= order_price:
            return True, open_p, "open_get_in 开盘卖出成交"
    matched, exec_price = match_order_price(order_price, low_p, high_p)
    if matched:
        return True, exec_price, "限价触达成交"
    if int(order.get("chase_enabled") or 0):
        chase_pct = max(0.0, float(order.get("chase_pct") or 0.0))
        if chase_pct > 0:
            chase_price = order_price * (1 + chase_pct / 100) if direction == "buy" else order_price * (1 - chase_pct / 100)
            chase_price = round(chase_price, 2)
            matched, exec_price = match_order_price(chase_price, low_p, high_p)
            if matched:
                return True, exec_price, f"原限价{order_price:.2f}未触达，追价{chase_price:.2f}触达成交"
    return False, 0.0, f"限价{order_price:.2f}未触达，当日区间{low_p:.2f}-{high_p:.2f}"


def _cancel_oco_siblings(order: dict, conn: sqlite3.Connection):
    group = str(order.get("oco_group") or "").strip()
    if not group:
        return
    rows = conn.execute(
        """SELECT * FROM agent_order
           WHERE agent_id=? AND oco_group=? AND id<>? AND status='pending'""",
        (order["agent_id"], group, order["id"]),
    ).fetchall()
    for row in rows:
        reserved_cash = float(row["reserved_cash"] or 0)
        if row["direction"] == "buy" and reserved_cash > 0:
            agent = conn.execute("SELECT current_cash FROM agent_info WHERE id=?", (row["agent_id"],)).fetchone()
            if agent:
                update_agent_cash(row["agent_id"], float(agent["current_cash"] or 0) + reserved_cash, conn)
            conn.execute("UPDATE agent_order SET reserved_cash=0 WHERE id=?", (row["id"],))
        update_order_status(
            row["id"],
            "cancelled",
            conn,
            "OCO同组订单已成交，自动取消",
            event_type="oco_cancelled",
            payload={"oco_group": group, "filled_order_id": order["id"]},
        )


def _expire_order(order: dict, reason: str, conn: sqlite3.Connection):
    if order.get("direction") == "buy":
        reserved_cash = float(order.get("reserved_cash") or 0)
        if reserved_cash > 0:
            agent = conn.execute("SELECT current_cash FROM agent_info WHERE id=?", (order["agent_id"],)).fetchone()
            if agent:
                update_agent_cash(order["agent_id"], float(agent["current_cash"] or 0) + reserved_cash, conn)
            conn.execute("UPDATE agent_order SET reserved_cash=0 WHERE id=?", (order["id"],))
    conn.execute(
        "UPDATE agent_order SET failure_attribution=? WHERE id=?",
        (_classify_failure(reason), order["id"]),
    )
    update_order_status(order["id"], "expired", conn, reason, event_type="expired")


def _classify_failure(reason: str) -> str:
    text = reason or ""
    if any(token in text for token in ("行情", "指数", "一字", "跌停", "涨停")):
        return "market"
    if any(token in text for token in ("未触达", "开盘", "区间")):
        return "timing"
    if any(token in text for token in ("资金", "冻结", "现金")):
        return "strategy"
    if any(token in text for token in ("T+1", "股份不足", "可卖")):
        return "timing"
    return "strategy"
