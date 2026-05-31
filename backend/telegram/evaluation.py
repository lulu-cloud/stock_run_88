"""Evaluation persistence for Telegram recommendation requests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from backend.config import DEEPSEEK_MODEL
from backend.db.repository import get_conn
from backend.evaluation import benchmark_horizon_return, price_return, summarize_tool_trace
from backend.telegram.knowledge import update_recommend_skill_outcome


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def record_recommend_eval(
    chat_id: str,
    username: str,
    query: str,
    response_text: str,
    recommendation_ids: list[int],
    trace: dict,
    tool_trace: list[dict] | None,
    latency_ms: float,
    intent: str = "",
    status: str = "ok",
    fallback_used: bool = False,
    json_parse_ok: bool = True,
    conn=None,
) -> int:
    close = conn is None
    conn = conn or get_conn()
    summary = summarize_tool_trace(tool_trace or [])
    tool_calls = summary["tool_calls"]
    trace_complete = bool(recommendation_ids) and bool((trace or {}).get("trace_summary") or (trace or {}).get("tools"))
    cur = conn.execute(
        """INSERT INTO telegram_recommend_eval
           (chat_id, username, query, recommendation_ids, intent, response_text, trace_json,
            trace_complete, status, fallback_used, llm_calls, prompt_tokens, completion_tokens,
            total_tokens, tool_calls, tool_failures, tool_failure_rate, response_latency_ms,
            json_parse_ok)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            chat_id,
            username,
            query,
            _json(recommendation_ids),
            intent,
            response_text,
            _json(trace or {}),
            1 if trace_complete else 0,
            status,
            1 if fallback_used else 0,
            summary["llm_calls"],
            summary["prompt_tokens"],
            summary["completion_tokens"],
            summary["total_tokens"],
            tool_calls,
            summary["tool_failures"],
            summary["tool_failure_rate"],
            round(float(latency_ms or 0), 2),
            1 if json_parse_ok else 0,
        ),
    )
    eval_id = cur.lastrowid
    conn.execute(
        """INSERT INTO telegram_recommend_cost
           (eval_id, chat_id, model, llm_calls, prompt_tokens, completion_tokens, total_tokens,
            tool_calls, tool_failures, response_latency_ms, cost_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            eval_id,
            chat_id,
            DEEPSEEK_MODEL,
            summary["llm_calls"],
            summary["prompt_tokens"],
            summary["completion_tokens"],
            summary["total_tokens"],
            tool_calls,
            summary["tool_failures"],
            round(float(latency_ms or 0), 2),
            _json({"provider_usage_available": summary["total_tokens"] is not None}),
        ),
    )
    for recommendation_id in recommendation_ids:
        row = conn.execute(
            "SELECT ts_code, recommend_price, created_at FROM telegram_recommend_feedback WHERE id=?",
            (recommendation_id,),
        ).fetchone()
        if not row:
            continue
        base_date = str(row["created_at"] or "")[:10].replace("-", "")
        conn.execute(
            """INSERT INTO telegram_recommend_outcome
               (recommendation_id, ts_code, base_trade_date, base_price, status)
               VALUES (?, ?, ?, ?, 'pending')
               ON CONFLICT(recommendation_id) DO NOTHING""",
            (recommendation_id, row["ts_code"], base_date, float(row["recommend_price"] or 0)),
        )
    if close:
        conn.commit()
        conn.close()
    return eval_id


def refresh_eval_feedback(recommendation_id: int, conn=None) -> None:
    close = conn is None
    conn = conn or get_conn()
    eval_rows = conn.execute(
        "SELECT id, recommendation_ids FROM telegram_recommend_eval WHERE recommendation_ids LIKE ?",
        (f"%{recommendation_id}%",),
    ).fetchall()
    for eval_row in eval_rows:
        try:
            ids = json.loads(eval_row["recommendation_ids"] or "[]")
        except Exception:
            ids = [recommendation_id]
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT feedback_type FROM telegram_recommend_feedback WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall() if ids else []
        counts = {
            "positive": sum(1 for r in rows if r["feedback_type"] == "positive"),
            "negative": sum(1 for r in rows if r["feedback_type"] == "negative"),
            "risk_too_high": sum(1 for r in rows if r["feedback_type"] == "risk_too_high"),
            "risk_too_low": sum(1 for r in rows if r["feedback_type"] == "risk_too_low"),
        }
        total = len(rows) or 1
        adoption = (counts["positive"] + sum(1 for r in rows if r["feedback_type"] == "recommended")) / total * 100
        conn.execute(
            """UPDATE telegram_recommend_eval
               SET positive_count=?, negative_count=?, risk_too_high_count=?,
                   risk_too_low_count=?, adoption_rate=?, updated_at=datetime('now')
               WHERE id=?""",
            (
                counts["positive"],
                counts["negative"],
                counts["risk_too_high"],
                counts["risk_too_low"],
                round(adoption, 4),
                eval_row["id"],
            ),
        )
    if close:
        conn.commit()
        conn.close()


def list_recommend_eval(chat_id: str = "", days: int = 90) -> list[dict]:
    conn = get_conn()
    cutoff = (datetime.now() - timedelta(days=max(1, int(days or 90)))).strftime("%Y-%m-%d %H:%M:%S")
    if chat_id:
        rows = conn.execute(
            """SELECT * FROM telegram_recommend_eval
               WHERE chat_id=? AND created_at>=?
               ORDER BY created_at DESC LIMIT 200""",
            (chat_id, cutoff),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM telegram_recommend_eval
               WHERE created_at>=?
               ORDER BY created_at DESC LIMIT 200""",
            (cutoff,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        trace = {}
        try:
            trace = json.loads(item.get("trace_json") or "{}")
        except Exception:
            trace = {}
        item["mode"] = trace.get("mode") or ("fallback" if item.get("fallback_used") else "react")
        item["fallback_error"] = trace.get("fallback_error") or ""
        item["failed_react_events"] = len(trace.get("failed_react_trace") or [])
        result.append(item)
    conn.close()
    return result


def update_recommend_outcomes(limit: int = 200) -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT f.id, f.ts_code, f.recommend_price, f.created_at,
                  o.status
           FROM telegram_recommend_feedback f
           LEFT JOIN telegram_recommend_outcome o ON o.recommendation_id=f.id
           WHERE COALESCE(o.status, 'pending')!='completed'
           ORDER BY f.id DESC LIMIT ?""",
        (max(1, int(limit or 200)),),
    ).fetchall()
    updated = 0
    completed = 0
    for row in rows:
        base_date = str(row["created_at"] or "")[:10].replace("-", "")
        base_price = float(row["recommend_price"] or 0)
        returns = {}
        dates = {}
        mae = 0.0
        status = "pending"
        for horizon in (1, 3, 5):
            value, target_date, adverse = price_return(row["ts_code"], base_date, horizon, base_price)
            returns[horizon] = value
            dates[horizon] = target_date
            mae = min(mae, adverse)
        bench = {h: benchmark_horizon_return(base_date, h) for h in (1, 3, 5)}
        if returns[1] is not None or returns[3] is not None or returns[5] is not None:
            updated += 1
        if returns[5] is not None:
            status = "completed"
            completed += 1
        conn.execute(
            """INSERT INTO telegram_recommend_outcome
               (recommendation_id, ts_code, base_trade_date, base_price, return_1d, return_3d,
                return_5d, benchmark_return_1d, benchmark_return_3d, benchmark_return_5d,
                beat_benchmark_1d, beat_benchmark_3d, beat_benchmark_5d,
                max_adverse_excursion, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(recommendation_id) DO UPDATE SET
               return_1d=excluded.return_1d, return_3d=excluded.return_3d,
               return_5d=excluded.return_5d, benchmark_return_1d=excluded.benchmark_return_1d,
               benchmark_return_3d=excluded.benchmark_return_3d, benchmark_return_5d=excluded.benchmark_return_5d,
               beat_benchmark_1d=excluded.beat_benchmark_1d, beat_benchmark_3d=excluded.beat_benchmark_3d,
               beat_benchmark_5d=excluded.beat_benchmark_5d, max_adverse_excursion=excluded.max_adverse_excursion,
               status=excluded.status, updated_at=datetime('now')""",
            (
                row["id"],
                row["ts_code"],
                base_date,
                base_price,
                returns[1],
                returns[3],
                returns[5],
                bench[1],
                bench[3],
                bench[5],
                1 if returns[1] is not None and bench[1] is not None and returns[1] > bench[1] else 0 if returns[1] is not None and bench[1] is not None else None,
                1 if returns[3] is not None and bench[3] is not None and returns[3] > bench[3] else 0 if returns[3] is not None and bench[3] is not None else None,
                1 if returns[5] is not None and bench[5] is not None and returns[5] > bench[5] else 0 if returns[5] is not None and bench[5] is not None else None,
                mae,
                status,
            ),
        )
        conn.execute(
            "UPDATE telegram_recommend_feedback SET return_1d=?, return_3d=?, return_5d=?, updated_at=datetime('now') WHERE id=?",
            (returns[1], returns[3], returns[5], row["id"]),
        )
        if returns[5] is not None and bench[5] is not None:
            update_recommend_skill_outcome(0.015 if returns[5] > bench[5] else -0.02, "T+5跑赢大盘" if returns[5] > bench[5] else "T+5跑输大盘", conn)
    conn.commit()
    conn.close()
    return {"scanned": len(rows), "updated": updated, "completed": completed}


def get_recommend_outcome(recommendation_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM telegram_recommend_outcome WHERE recommendation_id=?",
        (recommendation_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}
