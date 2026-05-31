"""Evaluation and cost metrics for trading and recommendation agents."""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime

import pandas as pd

from backend.data.loader import load_daily, load_index_daily


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def summarize_tool_trace(tool_trace: list[dict] | None) -> dict:
    trace = tool_trace or []
    llm_rows = [x for x in trace if x.get("type") == "llm"]
    tool_rows = [x for x in trace if x.get("type", "tool") == "tool"]
    failures = [
        x for x in tool_rows
        if x.get("error") or "错误" in str(x.get("result_preview", "")) or "未知工具" in str(x.get("result_preview", ""))
    ]
    prompt_tokens = sum(_safe_int(x.get("prompt_tokens")) for x in llm_rows) or None
    completion_tokens = sum(_safe_int(x.get("completion_tokens")) for x in llm_rows) or None
    total_tokens = sum(_safe_int(x.get("total_tokens")) for x in llm_rows) or None
    llm_latency_ms = sum(_safe_float(x.get("latency_ms")) for x in llm_rows)
    return {
        "llm_calls": len(llm_rows),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "tool_calls": len(tool_rows),
        "tool_failures": len(failures),
        "tool_failure_rate": round(len(failures) / len(tool_rows) * 100, 4) if tool_rows else 0.0,
        "llm_latency_ms": round(llm_latency_ms, 2),
    }


def _daily_returns(conn, agent_id: int, trade_date: str, limit: int = 90) -> list[dict]:
    rows = conn.execute(
        """SELECT trade_date, total_assets, daily_return, cumulative_return
           FROM agent_daily_report
           WHERE agent_id=? AND trade_date<=?
           ORDER BY trade_date DESC LIMIT ?""",
        (agent_id, trade_date, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _max_drawdown(assets: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in assets:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak * 100)
    return round(worst, 4)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(len(ordered) * percentile)))
    return ordered[idx]


def _benchmark_return(trade_date: str, days: int = 1) -> float:
    df = load_index_daily()
    if df is None or df.empty:
        return 0.0
    frame = df[df["trade_date"] <= str(trade_date)].sort_values("trade_date")
    if len(frame) <= days:
        return 0.0
    latest = _safe_float(frame.iloc[-1].get("close"))
    prev = _safe_float(frame.iloc[-days - 1].get("close"))
    return (latest - prev) / prev * 100 if prev else 0.0


def _holding_days(conn, agent_id: int, trade_date: str) -> float:
    rows = conn.execute(
        "SELECT buy_date FROM agent_position WHERE agent_id=? AND quantity>0 AND buy_date IS NOT NULL",
        (agent_id,),
    ).fetchall()
    if not rows:
        return 0.0
    try:
        end = datetime.strptime(str(trade_date), "%Y%m%d")
        days = []
        for row in rows:
            start = datetime.strptime(str(row["buy_date"]), "%Y%m%d")
            days.append(max(0, (end - start).days))
        return round(sum(days) / len(days), 2) if days else 0.0
    except Exception:
        return 0.0


def upsert_agent_eval_metric(conn, agent_id: int, trade_date: str, decision=None, decision_latency_ms: float = 0.0) -> dict:
    rows = _daily_returns(conn, agent_id, trade_date, 90)
    latest = rows[-1] if rows else {}
    returns = [_safe_float(r.get("daily_return")) for r in rows]
    assets = [_safe_float(r.get("total_assets")) for r in rows]
    negatives = [x for x in returns if x < 0]
    var_95 = _percentile(returns, 0.05)
    cvar_values = [x for x in returns if x <= var_95]
    benchmark = _benchmark_return(trade_date, 1)
    daily_return = _safe_float(latest.get("daily_return"))
    cumulative_return = _safe_float(latest.get("cumulative_return"))
    trace_summary = summarize_tool_trace(getattr(decision, "tool_trace", []) if decision else [])

    orders = conn.execute(
        "SELECT status, open_get_in, reserved_cash FROM agent_order WHERE agent_id=? AND trade_date=?",
        (agent_id, trade_date),
    ).fetchall()
    order_count = len(orders)
    filled = sum(1 for r in orders if r["status"] == "filled")
    expired = sum(1 for r in orders if r["status"] == "expired")
    open_orders = [r for r in orders if r["open_get_in"]]
    open_filled = sum(1 for r in open_orders if r["status"] == "filled")
    trades = conn.execute(
        "SELECT direction, total_value FROM agent_trade_log WHERE agent_id=? AND trade_date=?",
        (agent_id, trade_date),
    ).fetchall()
    buys = sum(_safe_float(t["total_value"]) for t in trades if t["direction"] == "buy")
    total_assets = _safe_float(latest.get("total_assets"))
    turnover_rate = buys / total_assets * 100 if total_assets else 0.0

    skill_rows = conn.execute(
        "SELECT confidence_score FROM agent_evolution_skill WHERE agent_id=?",
        (agent_id,),
    ).fetchall()
    confidence_avg = statistics.mean([_safe_float(r["confidence_score"], 0.5) for r in skill_rows]) if skill_rows else 0.0
    memory_compressions = conn.execute(
        "SELECT COUNT(*) FROM memory_compression_audit WHERE agent_id=? AND strftime('%Y%m%d', created_at)=?",
        (agent_id, trade_date),
    ).fetchone()[0]
    system_doc_updates = conn.execute(
        "SELECT COUNT(*) FROM agent_reflection_task WHERE agent_id=? AND trade_date=? AND status='completed'",
        (agent_id, trade_date),
    ).fetchone()[0]
    reflection_triggers = conn.execute(
        "SELECT COUNT(*) FROM agent_reflection_task WHERE agent_id=? AND trade_date=?",
        (agent_id, trade_date),
    ).fetchone()[0]

    analysis = getattr(decision, "analysis", "") if decision else ""
    json_ok = 1 if decision and isinstance(getattr(decision, "orders", []), list) else 0
    price_repair_count = analysis.count("修复") + analysis.count("非法挂单价")
    quality_required_tools = 1 if trace_summary["tool_calls"] > 0 else 0
    quality_tool_evidence = 1 if any(k in analysis for k in ("工具", "行情", "板块", "政策", "订单")) else 0
    quality_risk_explained = 1 if any(k in analysis for k in ("风险", "回撤", "仓位", "止损")) else 0

    metric = {
        "daily_return": daily_return,
        "cumulative_return": cumulative_return,
        "benchmark_return": benchmark,
        "excess_return": daily_return - benchmark,
        "alpha_score": cumulative_return - _benchmark_return(trade_date, max(1, min(len(rows) - 1, 20))),
        "max_drawdown": _max_drawdown(assets),
        "volatility": round(statistics.pstdev(returns), 4) if len(returns) > 1 else 0.0,
        "downside_volatility": round(statistics.pstdev(negatives), 4) if len(negatives) > 1 else 0.0,
        "var_95": round(var_95, 4),
        "cvar_95": round(sum(cvar_values) / len(cvar_values), 4) if cvar_values else 0.0,
        "win_rate": round(sum(1 for x in returns if x > 0) / len(returns) * 100, 4) if returns else 0.0,
        "profit_factor": round(sum(x for x in returns if x > 0) / abs(sum(x for x in returns if x < 0)), 4) if sum(x for x in returns if x < 0) else 0.0,
        "avg_holding_days": _holding_days(conn, agent_id, trade_date),
        "turnover_rate": round(turnover_rate, 4),
        "order_fill_rate": round(filled / order_count * 100, 4) if order_count else 0.0,
        "pending_expire_rate": round(expired / order_count * 100, 4) if order_count else 0.0,
        "open_get_in_success_rate": round(open_filled / len(open_orders) * 100, 4) if open_orders else 0.0,
        "memory_compressions": memory_compressions,
        "skill_confidence_delta": round(confidence_avg, 4),
        "system_doc_updates": system_doc_updates,
        "reflection_triggers": reflection_triggers,
        "decision_latency_ms": round(decision_latency_ms, 2),
        "json_parse_failures": 0 if json_ok else (1 if decision else 0),
        "price_repair_count": price_repair_count,
        "quality_json_ok": json_ok,
        "quality_required_tools": quality_required_tools,
        "quality_tool_evidence": quality_tool_evidence,
        "quality_risk_explained": quality_risk_explained,
        **trace_summary,
    }
    detail = {
        "orders": {"count": order_count, "filled": filled, "expired": expired},
        "quality": {
            "json_ok": json_ok,
            "required_tools": quality_required_tools,
            "tool_evidence": quality_tool_evidence,
            "risk_explained": quality_risk_explained,
        },
    }
    columns = list(metric.keys()) + ["detail_json"]
    values = [metric[k] for k in metric] + [_json(detail)]
    assignments = ", ".join(f"{c}=excluded.{c}" for c in columns)
    conn.execute(
        f"""INSERT INTO agent_eval_metric
            (agent_id, trade_date, {', '.join(columns)})
            VALUES (?, ?, {', '.join('?' for _ in columns)})
            ON CONFLICT(agent_id, trade_date) DO UPDATE SET
            {assignments}, updated_at=datetime('now')""",
        (agent_id, trade_date, *values),
    )
    return metric


def list_agent_eval(conn, agent_id: int, days: int = 90) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM agent_eval_metric
           WHERE agent_id=? ORDER BY trade_date DESC LIMIT ?""",
        (agent_id, max(1, min(int(days or 90), 365))),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_agent_eval(conn, agent_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM agent_eval_metric WHERE agent_id=? ORDER BY trade_date DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    return dict(row) if row else {}


def list_agent_cost(conn, agent_id: int, days: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT trade_date, llm_calls, prompt_tokens, completion_tokens, total_tokens,
                  tool_calls, tool_failures, tool_failure_rate, llm_latency_ms, decision_latency_ms,
                  json_parse_failures, price_repair_count
           FROM agent_eval_metric
           WHERE agent_id=? ORDER BY trade_date DESC LIMIT ?""",
        (agent_id, max(1, min(int(days or 30), 365))),
    ).fetchall()
    return [dict(r) for r in rows]


def price_return(ts_code: str, base_date: str, horizon: int, base_price: float = 0.0) -> tuple[float | None, str | None, float]:
    df = load_daily(ts_code)
    if df is None or df.empty:
        return None, None, 0.0
    frame = df[df["trade_date"] >= str(base_date)].sort_values("trade_date").reset_index(drop=True)
    if frame.empty or len(frame) <= horizon:
        return None, None, 0.0
    base = base_price or _safe_float(frame.iloc[0].get("close"))
    target = _safe_float(frame.iloc[horizon].get("close"))
    if base <= 0:
        return None, None, 0.0
    lows = [_safe_float(x) for x in frame.iloc[:horizon + 1].get("low", pd.Series(dtype=float)).tolist()]
    mae = min(((low - base) / base * 100 for low in lows), default=0.0)
    return round((target - base) / base * 100, 4), str(frame.iloc[horizon].get("trade_date")), round(mae, 4)


def benchmark_horizon_return(base_date: str, horizon: int) -> float | None:
    df = load_index_daily()
    if df is None or df.empty:
        return None
    frame = df[df["trade_date"] >= str(base_date)].sort_values("trade_date").reset_index(drop=True)
    if frame.empty or len(frame) <= horizon:
        return None
    base = _safe_float(frame.iloc[0].get("close"))
    target = _safe_float(frame.iloc[horizon].get("close"))
    return round((target - base) / base * 100, 4) if base else None
