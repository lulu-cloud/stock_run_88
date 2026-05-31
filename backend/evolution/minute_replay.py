"""5-minute intraday replay for stocks touched by an agent."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from backend.config import DATA_DIR
from backend.db.repository import get_positions


FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


def _compact_to_iso(value: str) -> str:
    value = str(value)
    if len(value) == 8:
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _to_baostock_code(ts_code: str) -> str:
    code = str(ts_code).upper()
    if code.endswith(".SH"):
        return "sh." + code[:6]
    if code.endswith(".SZ"):
        return "sz." + code[:6]
    return code


def _from_baostock_code(code: str) -> str:
    if code.startswith("sh."):
        return code[3:] + ".SH"
    if code.startswith("sz."):
        return code[3:] + ".SZ"
    return code


def _minute_path(ts_code: str, trade_date: str) -> str:
    folder = os.path.join(DATA_DIR, "minute", trade_date)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{ts_code}_5m.csv")


def load_or_fetch_5m(ts_code: str, trade_date: str) -> tuple[pd.DataFrame | None, str]:
    path = _minute_path(ts_code, trade_date)
    if os.path.exists(path):
        df = pd.read_csv(path)
        return df, "cache"
    try:
        import baostock as bs
    except Exception as exc:
        return None, f"baostock不可用: {exc}"

    lg = bs.login()
    if getattr(lg, "error_code", "1") != "0":
        return None, f"baostock登录失败: {getattr(lg, 'error_msg', '')}"
    try:
        rs = bs.query_history_k_data_plus(
            _to_baostock_code(ts_code),
            FIELDS,
            start_date=_compact_to_iso(trade_date),
            end_date=_compact_to_iso(trade_date),
            frequency="5",
            adjustflag="2",
        )
        if rs.error_code != "0":
            return None, f"分钟线查询失败: {rs.error_msg}"
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return None, "无分钟线数据"
        df = pd.DataFrame(rows, columns=rs.fields)
        df["ts_code"] = df["code"].apply(_from_baostock_code)
        df["trade_date"] = df["date"].astype(str).str.replace("-", "")
        for col in ("open", "high", "low", "close", "volume", "amount"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.rename(columns={"volume": "vol"})
        df = df[["trade_date", "time", "ts_code", "open", "high", "low", "close", "vol", "amount", "adjustflag"]]
        df.to_csv(path, index=False)
        return df, "fetched"
    finally:
        bs.logout()


def collect_relevant_codes(agent_id: int, trade_date: str, conn) -> list[str]:
    codes = {p["ts_code"] for p in get_positions(agent_id, conn)}
    order_rows = conn.execute(
        """SELECT ts_code FROM agent_order
           WHERE agent_id=? AND trade_date>=? AND trade_date<=?""",
        (agent_id, trade_date, trade_date),
    ).fetchall()
    trade_rows = conn.execute(
        "SELECT ts_code FROM agent_trade_log WHERE agent_id=? AND trade_date=?",
        (agent_id, trade_date),
    ).fetchall()
    codes.update(r["ts_code"] for r in order_rows)
    codes.update(r["ts_code"] for r in trade_rows)
    return sorted(c for c in codes if c)


def _first_touch(df: pd.DataFrame, price: float, direction: str, open_get_in: bool = False) -> dict[str, Any] | None:
    if df is None or df.empty:
        return None
    if open_get_in:
        first = df.iloc[0]
        if (direction == "buy" and float(first["open"]) <= price) or (direction == "sell" and float(first["open"]) >= price):
            return {"time": str(first["time"]), "price": float(first["open"]), "reason": "open_get_in"}
    mask = (df["low"] <= price) & (df["high"] >= price)
    hit = df[mask]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return {"time": str(row["time"]), "price": float(price), "reason": "limit_touch"}


def build_intraday_replay(agent_id: int, trade_date: str, conn) -> dict:
    codes = collect_relevant_codes(agent_id, trade_date, conn)
    data: dict[str, pd.DataFrame] = {}
    sources: dict[str, str] = {}
    for code in codes:
        df, source = load_or_fetch_5m(code, trade_date)
        sources[code] = source
        if df is not None:
            data[code] = df

    orders = conn.execute(
        """SELECT id, ts_code, stock_name, direction, quantity, price, open_get_in, status,
                  fail_reason, trade_date, skill_id
           FROM agent_order
           WHERE agent_id=? AND trade_date=?
           ORDER BY id""",
        (agent_id, trade_date),
    ).fetchall()
    events = []
    for row in orders:
        order = dict(row)
        touch = _first_touch(data.get(order["ts_code"]), float(order["price"] or 0), order["direction"], bool(order["open_get_in"]))
        events.append({**order, "estimated_touch": touch})

    sell_times = [
        e["estimated_touch"]["time"] for e in events
        if e["direction"] == "sell" and e.get("estimated_touch")
    ]
    first_sell = min(sell_times) if sell_times else ""
    for event in events:
        if event["direction"] != "buy" or not event.get("estimated_touch"):
            continue
        if first_sell and first_sell <= event["estimated_touch"]["time"]:
            event["cash_sequence"] = "possible_after_sell"
        elif first_sell:
            event["cash_sequence"] = "buy_touched_before_sell_non_atomic_risk"
        else:
            event["cash_sequence"] = "no_prior_sell"

    summary = f"触及{sum(1 for e in events if e.get('estimated_touch'))}/{len(events)}笔条件单。"
    if any(e.get("cash_sequence") == "buy_touched_before_sell_non_atomic_risk" for e in events):
        summary += " 存在先买后卖的非原子换仓风险。"
    if any("失败" in s or "不可用" in s for s in sources.values()):
        summary += " 部分分钟线获取失败，结果仅作提示。"
    return {"trade_date": trade_date, "codes": codes, "sources": sources, "events": events, "summary": summary}

