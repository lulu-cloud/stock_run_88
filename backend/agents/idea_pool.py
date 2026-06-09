"""Agent idea pool persistence and outcome tracking."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from backend.data.loader import load_daily
from backend.evaluation import benchmark_horizon_return, price_return
from backend.trading.rules import normalize_ts_code


def _json(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def latest_close(ts_code: str) -> float:
    df = load_daily(normalize_ts_code(ts_code))
    if df is None or df.empty:
        return 0.0
    return _as_float(df.iloc[-1].get("close"))


def idea_candidates_from_decision(decision, source_prefix: str = "") -> list[dict]:
    if not decision:
        return []
    items: list[dict] = []
    for stock in decision.selected_stocks or []:
        code = normalize_ts_code(stock.get("ts_code") or stock.get("code") or "")
        if not code:
            continue
        items.append({
            "ts_code": code,
            "stock_name": stock.get("stock_name") or stock.get("name") or "",
            "source": source_prefix or "selected",
            "score": stock.get("score"),
            "reason": stock.get("reason") or stock.get("summary") or "",
            "status": "candidate",
            "raw": stock,
        })
    for order in decision.orders or []:
        code = normalize_ts_code(order.get("ts_code") or "")
        if not code:
            continue
        items.append({
            "ts_code": code,
            "stock_name": order.get("stock_name") or "",
            "source": "order",
            "score": order.get("skill_confidence"),
            "reason": order.get("reason") or "",
            "status": "promoted" if order.get("direction") == "buy" else "candidate",
            "raw": order,
        })
    return items


def extract_trade_plan_from_text(text: str) -> dict:
    """Parse the last complete trade-plan JSON from a free-form thinking log."""
    if not text:
        return {}
    candidates = []
    candidates.extend(re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.S | re.I))
    candidates.extend(re.findall(r"```\s*(\{.*?\})\s*```", text, flags=re.S))
    start = text.rfind("{")
    while start >= 0:
        snippet = text[start:].strip()
        end = snippet.rfind("}")
        if end >= 0:
            candidates.append(snippet[:end + 1])
        start = text.rfind("{", 0, start)
    for raw in reversed(candidates):
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        if isinstance(data, dict) and ("orders" in data or "selected_stocks" in data):
            return data
    return {}


def upsert_agent_ideas(
    conn: sqlite3.Connection,
    agent_id: int,
    trade_date: str,
    candidates: list[dict],
    *,
    market_context: dict | None = None,
    default_status: str = "candidate",
    reject_reason: str = "",
) -> int:
    count = 0
    seen: set[tuple[str, str]] = set()
    for item in candidates or []:
        code = normalize_ts_code(item.get("ts_code") or "")
        if not code:
            continue
        source = str(item.get("source") or "selected")[:40]
        key = (code, source)
        if key in seen:
            continue
        seen.add(key)
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
        discovery_price = _as_float(item.get("discovery_price") or raw.get("price") or raw.get("close") or latest_close(code))
        status = item.get("status") or default_status
        conn.execute(
            """INSERT INTO agent_idea_pool
               (agent_id, trade_date, ts_code, stock_name, source, score, reason, status,
                reject_reason, discovery_price, market_context_json, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent_id, trade_date, ts_code, source) DO UPDATE SET
               stock_name=excluded.stock_name,
               score=COALESCE(excluded.score, agent_idea_pool.score),
               reason=CASE WHEN excluded.reason!='' THEN excluded.reason ELSE agent_idea_pool.reason END,
               status=excluded.status,
               reject_reason=excluded.reject_reason,
               discovery_price=CASE WHEN excluded.discovery_price>0 THEN excluded.discovery_price ELSE agent_idea_pool.discovery_price END,
               market_context_json=excluded.market_context_json,
               raw_json=excluded.raw_json,
               updated_at=datetime('now')""",
            (
                agent_id,
                trade_date,
                code,
                item.get("stock_name") or raw.get("stock_name") or raw.get("name") or "",
                source,
                item.get("score"),
                item.get("reason") or raw.get("reason") or "",
                status,
                reject_reason or item.get("reject_reason") or "",
                discovery_price,
                _json(market_context or {}),
                _json(raw),
            ),
        )
        row = conn.execute(
            "SELECT id FROM agent_idea_pool WHERE agent_id=? AND trade_date=? AND ts_code=? AND source=?",
            (agent_id, trade_date, code, source),
        ).fetchone()
        if row:
            conn.execute(
                """INSERT INTO agent_idea_outcome
                   (idea_id, ts_code, base_trade_date, base_price, status)
                   VALUES (?, ?, ?, ?, 'pending')
                   ON CONFLICT(idea_id) DO NOTHING""",
                (row["id"], code, trade_date, discovery_price),
            )
        count += 1
    return count


def update_agent_idea_outcomes(conn: sqlite3.Connection, limit: int = 300) -> dict:
    rows = conn.execute(
        """SELECT i.id, i.ts_code, i.trade_date, i.discovery_price, o.status
           FROM agent_idea_pool i
           LEFT JOIN agent_idea_outcome o ON o.idea_id=i.id
           WHERE COALESCE(o.status, 'pending')!='completed'
           ORDER BY i.trade_date DESC, i.id DESC
           LIMIT ?""",
        (max(1, int(limit or 300)),),
    ).fetchall()
    updated = 0
    completed = 0
    for row in rows:
        base_date = str(row["trade_date"] or "")
        base_price = _as_float(row["discovery_price"])
        returns = {}
        bench = {}
        mae = 0.0
        for horizon in (1, 3, 5, 10, 20):
            value, _target_date, adverse = price_return(row["ts_code"], base_date, horizon, base_price)
            returns[horizon] = value
            bench[horizon] = benchmark_horizon_return(base_date, horizon)
            mae = min(mae, adverse)
        if any(v is not None for v in returns.values()):
            updated += 1
        status = "completed" if returns[20] is not None else "pending"
        if status == "completed":
            completed += 1
        conn.execute(
            """INSERT INTO agent_idea_outcome
               (idea_id, ts_code, base_trade_date, base_price,
                return_1d, return_3d, return_5d, return_10d, return_20d,
                benchmark_return_1d, benchmark_return_3d, benchmark_return_5d,
                benchmark_return_10d, benchmark_return_20d,
                beat_benchmark_1d, beat_benchmark_3d, beat_benchmark_5d,
                beat_benchmark_10d, beat_benchmark_20d,
                max_adverse_excursion, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(idea_id) DO UPDATE SET
               return_1d=excluded.return_1d, return_3d=excluded.return_3d,
               return_5d=excluded.return_5d, return_10d=excluded.return_10d,
               return_20d=excluded.return_20d,
               benchmark_return_1d=excluded.benchmark_return_1d,
               benchmark_return_3d=excluded.benchmark_return_3d,
               benchmark_return_5d=excluded.benchmark_return_5d,
               benchmark_return_10d=excluded.benchmark_return_10d,
               benchmark_return_20d=excluded.benchmark_return_20d,
               beat_benchmark_1d=excluded.beat_benchmark_1d,
               beat_benchmark_3d=excluded.beat_benchmark_3d,
               beat_benchmark_5d=excluded.beat_benchmark_5d,
               beat_benchmark_10d=excluded.beat_benchmark_10d,
               beat_benchmark_20d=excluded.beat_benchmark_20d,
               max_adverse_excursion=excluded.max_adverse_excursion,
               status=excluded.status,
               updated_at=datetime('now')""",
            (
                row["id"], row["ts_code"], base_date, base_price,
                returns[1], returns[3], returns[5], returns[10], returns[20],
                bench[1], bench[3], bench[5], bench[10], bench[20],
                _beat(returns[1], bench[1]), _beat(returns[3], bench[3]),
                _beat(returns[5], bench[5]), _beat(returns[10], bench[10]),
                _beat(returns[20], bench[20]), mae, status,
            ),
        )
    return {"scanned": len(rows), "updated": updated, "completed": completed}


def _beat(value, benchmark):
    if value is None or benchmark is None:
        return None
    return 1 if value > benchmark else 0


def list_agent_ideas(conn: sqlite3.Connection, agent_id: int, days: int = 30, status: str = "") -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=max(1, int(days or 30)))).strftime("%Y%m%d")
    params: list = [agent_id, cutoff]
    status_sql = ""
    if status:
        status_sql = " AND i.status=?"
        params.append(status)
    rows = conn.execute(
        f"""SELECT i.*, o.return_1d, o.return_3d, o.return_5d, o.return_10d, o.return_20d,
                  o.benchmark_return_5d, o.beat_benchmark_5d, o.max_adverse_excursion,
                  o.status AS outcome_status
           FROM agent_idea_pool i
           LEFT JOIN agent_idea_outcome o ON o.idea_id=i.id
           WHERE i.agent_id=? AND i.trade_date>=? {status_sql}
           ORDER BY i.trade_date DESC, i.id DESC
           LIMIT 300""",
        tuple(params),
    ).fetchall()
    return [_decode(dict(r)) for r in rows]


def idea_summary(conn: sqlite3.Connection, agent_id: int, days: int = 90) -> dict:
    items = list_agent_ideas(conn, agent_id, days)
    done = [x for x in items if x.get("return_5d") is not None]
    return {
        "idea_count": len(items),
        "evaluated_5d": len(done),
        "avg_return_5d": round(sum(float(x.get("return_5d") or 0) for x in done) / len(done), 4) if done else None,
        "beat_benchmark_5d_rate": round(sum(1 for x in done if x.get("beat_benchmark_5d")) / len(done) * 100, 2) if done else None,
    }


def _decode(item: dict) -> dict:
    for key in ("market_context_json", "raw_json"):
        try:
            item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
        except Exception:
            item[key.replace("_json", "")] = {}
    return item
