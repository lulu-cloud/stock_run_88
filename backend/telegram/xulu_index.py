"""Flow-adjusted Xulu strategy index calculations and formatting."""

from __future__ import annotations

import json
from typing import Any

from backend.db.repository import get_conn


INDEX_BASE_VALUE = 1000.0
DEFAULT_HISTORY_LIMIT = 10
MAX_HISTORY_LIMIT = 60


def _row_dict(row) -> dict:
    return dict(row) if row else {}


def upsert_xulu_index_daily(
    conn,
    trade_date: str,
    total_asset: float,
    daily_pnl: float,
    net_flow: float = 0.0,
    cash: float | None = None,
    market_value: float | None = None,
    source: str = "daily_history",
    is_estimated: bool = False,
    detail: dict[str, Any] | None = None,
) -> dict:
    """Insert or amend one index day without committing the caller's transaction."""
    previous = conn.execute(
        """SELECT * FROM xulu_index_daily
           WHERE trade_date<? ORDER BY trade_date DESC LIMIT 1""",
        (trade_date,),
    ).fetchone()
    if previous:
        prev_total = float(previous["total_asset"] or 0)
        daily_return = float(daily_pnl or 0) / prev_total if prev_total > 0 else 0.0
        index_value = float(previous["index_value"] or INDEX_BASE_VALUE) * (1.0 + daily_return)
    else:
        daily_return = 0.0
        index_value = INDEX_BASE_VALUE
    cumulative_return = index_value / INDEX_BASE_VALUE - 1.0
    payload = json.dumps(detail or {}, ensure_ascii=False, default=str)
    conn.execute(
        """INSERT INTO xulu_index_daily
           (trade_date, index_value, daily_return, cumulative_return, total_asset,
            daily_pnl, net_flow, cash, market_value, source, is_estimated, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(trade_date) DO UPDATE SET
             index_value=excluded.index_value,
             daily_return=excluded.daily_return,
             cumulative_return=excluded.cumulative_return,
             total_asset=excluded.total_asset,
             daily_pnl=excluded.daily_pnl,
             net_flow=excluded.net_flow,
             cash=excluded.cash,
             market_value=excluded.market_value,
             source=excluded.source,
             is_estimated=excluded.is_estimated,
             detail_json=excluded.detail_json,
             updated_at=datetime('now')""",
        (
            trade_date,
            round(index_value, 6),
            round(daily_return, 10),
            round(cumulative_return, 10),
            round(float(total_asset or 0), 2),
            round(float(daily_pnl or 0), 2),
            round(float(net_flow or 0), 2),
            None if cash is None else round(float(cash), 2),
            None if market_value is None else round(float(market_value), 2),
            source or "daily_history",
            1 if is_estimated else 0,
            payload,
        ),
    )
    return _row_dict(conn.execute(
        "SELECT * FROM xulu_index_daily WHERE trade_date=?",
        (trade_date,),
    ).fetchone())


def replace_xulu_index_history(conn, rows: list[dict]) -> int:
    """Idempotently upsert a precomputed chronological history."""
    if not rows:
        return 0
    dates = [row["trade_date"] for row in rows]
    conn.execute(
        "DELETE FROM xulu_index_daily WHERE trade_date BETWEEN ? AND ?",
        (min(dates), max(dates)),
    )
    for row in sorted(rows, key=lambda item: item["trade_date"]):
        conn.execute(
            """INSERT INTO xulu_index_daily
               (trade_date, index_value, daily_return, cumulative_return, total_asset,
                daily_pnl, net_flow, cash, market_value, source, is_estimated, detail_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(trade_date) DO UPDATE SET
                 index_value=excluded.index_value,
                 daily_return=excluded.daily_return,
                 cumulative_return=excluded.cumulative_return,
                 total_asset=excluded.total_asset,
                 daily_pnl=excluded.daily_pnl,
                 net_flow=excluded.net_flow,
                 cash=excluded.cash,
                 market_value=excluded.market_value,
                 source=excluded.source,
                 is_estimated=excluded.is_estimated,
                 detail_json=excluded.detail_json,
                 updated_at=datetime('now')""",
            (
                row["trade_date"], row["index_value"], row["daily_return"],
                row["cumulative_return"], row["total_asset"], row["daily_pnl"],
                row.get("net_flow", 0.0), row.get("cash"), row.get("market_value"),
                row.get("source", "historical_import"), 1 if row.get("is_estimated") else 0,
                json.dumps(row.get("detail") or {}, ensure_ascii=False, default=str),
            ),
        )
    return len(rows)


def get_xulu_index_summary(conn=None, limit: int = DEFAULT_HISTORY_LIMIT) -> dict:
    own_conn = conn is None
    conn = conn or get_conn()
    safe_limit = max(1, min(int(limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM xulu_index_daily ORDER BY trade_date",
        ).fetchall()]
    finally:
        if own_conn:
            conn.close()
    if not rows:
        return {"available": False, "history": []}

    high = float(rows[0]["index_value"] or INDEX_BASE_VALUE)
    max_drawdown = 0.0
    wins = 0
    observations = 0
    for idx, row in enumerate(rows):
        value = float(row["index_value"] or 0)
        high = max(high, value)
        drawdown = value / high - 1.0 if high > 0 else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        if idx > 0:
            observations += 1
            if float(row["daily_return"] or 0) > 0:
                wins += 1
    latest = rows[-1]
    return {
        "available": True,
        "latest": latest,
        "history": list(reversed(rows[-safe_limit:])),
        "high_watermark": high,
        "max_drawdown": max_drawdown,
        "win_rate": wins / observations if observations else 0.0,
        "observations": observations,
        "base_value": INDEX_BASE_VALUE,
    }


def format_xulu_index(limit: int = DEFAULT_HISTORY_LIMIT, conn=None) -> str:
    summary = get_xulu_index_summary(conn=conn, limit=limit)
    if not summary.get("available"):
        return "Xulu 指数尚未初始化。"
    latest = summary["latest"]
    lines = [
        "Xulu 实盘策略指数",
        f"日期: {latest['trade_date']}",
        f"当前点位: {float(latest['index_value']):,.2f}",
        f"当日涨跌: {float(latest['daily_return'] or 0):+.2%}",
        f"累计收益: {float(latest['cumulative_return'] or 0):+.2%}",
        f"历史最高: {float(summary['high_watermark']):,.2f}",
        f"最大回撤: {float(summary['max_drawdown']):.2%}",
        f"胜率: {float(summary['win_rate']):.2%} ({summary['observations']}个有效指数日)",
        "",
        f"最近 {len(summary['history'])} 条:",
    ]
    for row in summary["history"]:
        estimated = " *" if row.get("is_estimated") else ""
        lines.append(
            f"- {row['trade_date']}: {float(row['index_value']):,.2f} "
            f"({float(row['daily_return'] or 0):+.2%}){estimated}"
        )
    if any(row.get("is_estimated") for row in summary["history"]):
        lines.append("* 含历史收盘价估值记录")
    return "\n".join(lines)


def format_xulu_index_snapshot(row: dict) -> str:
    if not row:
        return "Xulu 指数尚未初始化。"
    return (
        f"Xulu 指数: {float(row['index_value']):,.2f} "
        f"({float(row.get('daily_return') or 0):+.2%})，"
        f"累计 {float(row.get('cumulative_return') or 0):+.2%}"
    )
