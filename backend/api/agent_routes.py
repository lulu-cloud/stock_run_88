"""Agent 管理 API"""

import json
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
from backend.agents.factory import AgentManager
from backend.agents.tools import get_tool_catalog
from backend.db.repository import (
    get_conn, get_positions, list_agent_order_trace, list_order_trace, list_trades, get_pending_orders,
)
from backend.data.loader import load_daily, compute_mas
from backend.trading.calculator import calc_total_assets, calc_cumulative_return
from backend.trading.rules import normalize_ts_code
from backend.pipeline.daily_pipeline import simulate_day, _effective_board_permissions
import backend.strategies  # noqa: F401 - import registers built-in strategies
from backend.strategies.registry import StrategyRegistry
from backend.evolution.race import latest_race_panel, agent_race_detail
from backend.evolution.reflection import (
    build_reflection_input,
    get_public_system_summary,
    list_timeline,
    list_versions,
    maybe_schedule_reflection,
    run_reflection_task,
)
from backend.agents.llm_agent import AGENT_SYSTEM_PROMPT
from backend.agents.idea_pool import idea_summary, list_agent_ideas, update_agent_idea_outcomes
from backend.evolution.engine import format_evolution_prompt, prepare_evolution_context
from backend.evaluation import latest_agent_eval, list_agent_cost, list_agent_eval

router = APIRouter(prefix="/api/agent", tags=["agent"])


class CreateAgentRequest(BaseModel):
    name: str
    display_name: str
    agent_type: str = "custom"
    strategy_ids: str = ""
    risk_config: Optional[dict] = None
    initial_capital: float = 150000.0


class ConfigureAgentRequest(BaseModel):
    risk_config: dict
    strategy_ids: str = ""


class StockPoolItemRequest(BaseModel):
    ts_code: str
    stock_name: str = ""
    note: str = ""
    enabled: bool = True


class StockPoolReplaceRequest(BaseModel):
    items: list[StockPoolItemRequest] = []


class StatusRequest(BaseModel):
    status: str


class ScheduleRequest(BaseModel):
    enabled: bool = False
    review_time: str = "23:00"
    push_time: str = "23:00"


@router.get("/list")
async def list_agents_api():
    """获取所有 Agent 列表"""
    agents = AgentManager.list_all()
    result = []
    conn = get_conn()
    for a in agents:
        positions = get_positions(a["id"], conn)
        total_value = sum(p.get("market_value", 0) or 0 for p in positions)
        unrealized_pnl = sum(p.get("unrealized_pnl", 0) or 0 for p in positions)
        frozen_cash = conn.execute(
            "SELECT COALESCE(SUM(reserved_cash), 0) FROM agent_order WHERE agent_id=? AND status='pending'",
            (a["id"],),
        ).fetchone()[0] or 0
        total_assets = a["current_cash"] + total_value + frozen_cash
        race = conn.execute(
            "SELECT race_score, max_drawdown, sharpe_ratio, risk_cap, style_tag FROM agent_race_metric WHERE agent_id=? ORDER BY trade_date DESC LIMIT 1",
            (a["id"],),
        ).fetchone()
        skill = conn.execute(
            "SELECT skill_id, skill_name, confidence_score FROM agent_evolution_skill WHERE agent_id=? AND enabled=1 ORDER BY confidence_score DESC LIMIT 1",
            (a["id"],),
        ).fetchone()
        risk_config = json.loads(a["risk_config"]) if a.get("risk_config") else {}
        latest_trade_date = conn.execute(
            "SELECT COALESCE(MAX(trade_date), strftime('%Y%m%d','now')) FROM agent_daily_report WHERE agent_id=?",
            (a["id"],),
        ).fetchone()[0]
        result.append({
            "id": a["id"],
            "name": a["name"],
            "display_name": a["display_name"],
            "agent_type": a["agent_type"],
            "initial_capital": a["initial_capital"],
            "current_cash": round(a["current_cash"], 2),
            "frozen_cash": round(frozen_cash, 2),
            "market_value": round(total_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_assets": round(total_assets, 2),
            "cumulative_return": round(calc_cumulative_return(total_assets, a["initial_capital"]), 2),
            "position_count": len(positions),
            "status": a["status"],
            "schedule_enabled": bool(a.get("schedule_enabled", 0)),
            "review_time": a.get("review_time", "23:00"),
            "push_time": a.get("push_time", "23:00"),
            "race_metric": dict(race) if race else {},
            "top_skill": dict(skill) if skill else {},
            "strategy_ids": a.get("strategy_ids", ""),
            "risk_config": risk_config,
            "board_permissions_effective": _effective_board_permissions(a["id"], str(latest_trade_date), risk_config, conn),
        })
    conn.close()
    return {"agents": result, "total": len(result)}


@router.get("/race")
async def agent_race_panel(days: int = Query(90, description="对比天数")):
    conn = get_conn()
    data = latest_race_panel(conn, days)
    conn.close()
    return data


@router.get("/tools")
async def agent_tool_catalog():
    strategies = []
    try:
        for item in StrategyRegistry.list_strategies():
            strategies.append(item if isinstance(item, dict) else {"name": str(item), "description": ""})
    except Exception:
        try:
            strategies = StrategyRegistry.list_with_info()
        except Exception:
            strategies = []
    return {"tools": get_tool_catalog(), "strategies": strategies}


@router.get("/comparison")
async def agent_comparison(days: int = Query(90, description="对比天数")):
    """获取所有 Agent 的绩效对比数据（净值曲线 + 每日盈亏）"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, display_name, agent_type, initial_capital FROM agent_info ORDER BY id")
    agents = [dict(r) for r in c.fetchall()]
    result = {"agents": [], "pnl_calendar": {}}
    for a in agents:
        c.execute(
            """SELECT trade_date, total_assets, daily_pnl, daily_return, cumulative_return
               FROM agent_daily_report WHERE agent_id = ?
               ORDER BY trade_date ASC LIMIT ?""",
            (a["id"], days),
        )
        reports = [dict(r) for r in c.fetchall()]
        equity = [{"date": r["trade_date"], "total_assets": r["total_assets"],
                    "return_pct": r["cumulative_return"], "daily_pnl": r["daily_pnl"]}
                  for r in reports]
        for r in reports:
            d = r["trade_date"]
            if d not in result["pnl_calendar"]:
                result["pnl_calendar"][d] = {}
            result["pnl_calendar"][d][a["display_name"]] = r["daily_pnl"]
        result["agents"].append({
            "id": a["id"], "display_name": a["display_name"],
            "agent_type": a["agent_type"], "initial_capital": a["initial_capital"],
            "equity_curve": equity,
        })
    conn.close()
    return result


@router.get("/positions")
async def agent_positions_overview():
    """获取每个 Agent 的仓位和个股权重。"""
    conn = get_conn()
    rows = conn.execute("SELECT id, display_name, current_cash, initial_capital FROM agent_info ORDER BY id").fetchall()
    agents = []
    for row in rows:
        positions = get_positions(row["id"], conn)
        market_value = sum(float(p.get("market_value") or 0) for p in positions)
        cash = float(row["current_cash"] or 0)
        total_assets = cash + market_value
        pos_rows = []
        for p in positions:
            mv = float(p.get("market_value") or 0)
            pos_rows.append({
                "ts_code": p.get("ts_code"),
                "stock_name": p.get("stock_name"),
                "quantity": p.get("quantity"),
                "avg_cost": p.get("avg_cost"),
                "current_price": p.get("current_price"),
                "market_value": round(mv, 2),
                "unrealized_pnl": round(float(p.get("unrealized_pnl") or 0), 2),
                "weight": round(mv / total_assets * 100, 2) if total_assets else 0.0,
            })
        pos_rows.sort(key=lambda x: x["weight"], reverse=True)
        agents.append({
            "agent_id": row["id"],
            "display_name": row["display_name"],
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "total_assets": round(total_assets, 2),
            "position_ratio": round(market_value / total_assets * 100, 2) if total_assets else 0.0,
            "positions": pos_rows,
        })
    conn.close()
    return {"agents": agents}


@router.get("/{agent_id}")
async def get_agent_detail(agent_id: int):
    """获取 Agent 详情"""
    agent = AgentManager.get(agent_id)
    if not agent:
        return {"error": "Agent not found"}

    conn = get_conn()
    positions = get_positions(agent_id, conn)
    trades = list_trades(agent_id, 50, conn)
    pending_orders = [
        dict(r) for r in conn.execute(
            "SELECT * FROM agent_order WHERE agent_id=? AND status='pending' ORDER BY trade_date ASC, id ASC",
            (agent_id,),
        ).fetchall()
    ]
    race = conn.execute(
        "SELECT * FROM agent_race_metric WHERE agent_id=? ORDER BY trade_date DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    policy = conn.execute(
        "SELECT * FROM agent_capital_policy WHERE agent_id=? ORDER BY trade_date DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    eval_summary = latest_agent_eval(conn, agent_id)
    discovery_summary = idea_summary(conn, agent_id, 90)
    skills = [dict(r) for r in conn.execute(
        "SELECT skill_id, skill_name, confidence_score, recent_fail_rate, evolution_record FROM agent_evolution_skill WHERE agent_id=? AND enabled=1 ORDER BY confidence_score DESC",
        (agent_id,),
    ).fetchall()]
    latest_trade_date = conn.execute(
        "SELECT COALESCE(MAX(trade_date), strftime('%Y%m%d','now')) FROM agent_daily_report WHERE agent_id=?",
        (agent_id,),
    ).fetchone()[0]

    total_market_value = sum(p.get("market_value", 0) or 0 for p in positions)
    total_unrealized_pnl = sum(p.get("unrealized_pnl", 0) or 0 for p in positions)
    frozen_cash = sum(float(o.get("reserved_cash") or 0) for o in pending_orders)
    total_assets = agent["current_cash"] + total_market_value + frozen_cash
    risk_config = json.loads(agent["risk_config"]) if agent["risk_config"] else {}
    board_permissions_effective = _effective_board_permissions(agent_id, str(latest_trade_date), risk_config, conn)
    stock_pool = AgentManager.list_stock_pool(agent_id, active_only=True)
    stock_pool_codes = {normalize_ts_code(item.get("ts_code", "")) for item in stock_pool}
    order_ids = [t.get("order_id") for t in trades if t.get("order_id")]
    order_ids.extend([o.get("id") for o in pending_orders if o.get("id")])
    order_map = {}
    trace_map = {}
    if order_ids:
        placeholders = ",".join("?" for _ in order_ids)
        order_rows = conn.execute(
            f"""SELECT id, reason, fail_reason, skill_id, skill_confidence, evolution_mark, order_type, open_get_in,
                       decision_batch_id, fill_probability, price_aggressiveness
                FROM agent_order WHERE id IN ({placeholders})""",
            tuple(order_ids),
        ).fetchall()
        order_map = {r["id"]: dict(r) for r in order_rows}
        trace_rows = conn.execute(
            f"""SELECT * FROM agent_order_trace
                WHERE order_id IN ({placeholders})
                ORDER BY created_at ASC, id ASC""",
            tuple(order_ids),
        ).fetchall()
        for trace_row in trace_rows:
            item = dict(trace_row)
            try:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            except Exception:
                item["payload"] = {}
            trace_map.setdefault(item["order_id"], []).append(item)
    conn.close()

    def pool_meta(order_id, ts_code: str, direction: str) -> dict:
        for ev in trace_map.get(order_id, []):
            payload = ev.get("payload") or {}
            if payload.get("pool_status"):
                return {
                    "pool_status": payload.get("pool_status"),
                    "out_of_pool_reason": payload.get("out_of_pool_reason", ""),
                    "stock_pool_enabled": bool(payload.get("stock_pool_enabled", False)),
                }
        if direction == "sell":
            return {"pool_status": "position_exit", "out_of_pool_reason": "", "stock_pool_enabled": bool(risk_config.get("stock_pool_enabled"))}
        code = normalize_ts_code(ts_code)
        if code in stock_pool_codes:
            return {"pool_status": "in_pool", "out_of_pool_reason": "", "stock_pool_enabled": bool(risk_config.get("stock_pool_enabled"))}
        return {"pool_status": "unknown", "out_of_pool_reason": "", "stock_pool_enabled": bool(risk_config.get("stock_pool_enabled"))}

    return {
        "agent": {
            "id": agent["id"],
            "name": agent["name"],
            "display_name": agent["display_name"],
            "agent_type": agent["agent_type"],
            "initial_capital": agent["initial_capital"],
            "current_cash": round(agent["current_cash"], 2),
            "frozen_cash": round(frozen_cash, 2),
            "market_value": round(total_market_value, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_assets": round(total_assets, 2),
            "cumulative_return": round(calc_cumulative_return(total_assets, agent["initial_capital"]), 2),
            "risk_config": risk_config,
            "board_permissions_effective": board_permissions_effective,
            "strategy_ids": agent.get("strategy_ids") or "",
            "status": agent.get("status", "active"),
            "schedule": AgentManager.get_schedule(agent_id),
            "race_metric": dict(race) if race else {},
            "capital_policy": dict(policy) if policy else {},
            "eval_summary": eval_summary,
            "idea_summary": discovery_summary,
            "skills": skills,
            "stock_pool": stock_pool,
            "strategy_versions": AgentManager.list_user_strategy_versions(agent_id, 20),
        },
        "positions": [
            {
                "ts_code": p["ts_code"],
                "stock_name": p.get("stock_name", ""),
                "quantity": p["quantity"],
                "avg_cost": p["avg_cost"],
                "current_price": p.get("current_price") or 0,
                "market_value": p.get("market_value") or 0,
                "unrealized_pnl": round(p.get("unrealized_pnl", 0) or 0, 2),
                "buy_date": p.get("buy_date", ""),
            }
            for p in positions
        ],
        "trades": [
            {
                "id": t["id"],
                "ts_code": t["ts_code"],
                "stock_name": t.get("stock_name", ""),
                "direction": t["direction"],
                "quantity": t["quantity"],
                "price": t["price"],
                "total_value": t["total_value"],
                "trade_date": t["trade_date"],
                "order_id": t.get("order_id"),
                "reason": (order_map.get(t.get("order_id")) or {}).get("reason", ""),
                "fail_reason": (order_map.get(t.get("order_id")) or {}).get("fail_reason", ""),
                "skill_id": (order_map.get(t.get("order_id")) or {}).get("skill_id", ""),
                "skill_confidence": (order_map.get(t.get("order_id")) or {}).get("skill_confidence", 0),
                "evolution_mark": (order_map.get(t.get("order_id")) or {}).get("evolution_mark", ""),
                "order_type": (order_map.get(t.get("order_id")) or {}).get("order_type", ""),
                "open_get_in": bool((order_map.get(t.get("order_id")) or {}).get("open_get_in", 0)),
                "decision_batch_id": (order_map.get(t.get("order_id")) or {}).get("decision_batch_id", ""),
                "fill_probability": (order_map.get(t.get("order_id")) or {}).get("fill_probability"),
                "price_aggressiveness": (order_map.get(t.get("order_id")) or {}).get("price_aggressiveness"),
                "order_trace": trace_map.get(t.get("order_id"), []),
                **pool_meta(t.get("order_id"), t["ts_code"], t["direction"]),
            }
            for t in trades
        ],
        "pending_orders": [
            {
                "id": o["id"],
                "ts_code": o["ts_code"],
                "stock_name": o.get("stock_name", ""),
                "direction": o["direction"],
                "order_type": o["order_type"],
                "quantity": o["quantity"],
                "price": o["price"],
                "open_get_in": bool(o.get("open_get_in", 0)),
                "reserved_cash": o.get("reserved_cash", 0),
                "decision_batch_id": o.get("decision_batch_id", ""),
                "fill_probability": o.get("fill_probability"),
                "price_aggressiveness": o.get("price_aggressiveness"),
                "skill_id": o.get("skill_id", ""),
                "skill_confidence": o.get("skill_confidence", 0),
                "evolution_mark": o.get("evolution_mark", ""),
                "reason": o.get("reason", ""),
                "fail_reason": o.get("fail_reason", ""),
                "status": o["status"],
                "trade_date": o["trade_date"],
                "order_trace": trace_map.get(o["id"], []),
                **pool_meta(o["id"], o["ts_code"], o["direction"]),
            }
            for o in pending_orders
        ],
    }


@router.get("/{agent_id}/orders/trace")
async def get_agent_order_trace(agent_id: int, order_id: int | None = Query(default=None), limit: int = Query(80)):
    conn = get_conn()
    if order_id:
        data = list_order_trace(order_id, conn)
    else:
        data = list_agent_order_trace(agent_id, limit, conn)
    conn.close()
    return {"items": data}


@router.get("/{agent_id}/decision-batches")
async def get_agent_decision_batches(agent_id: int, limit: int = Query(30)):
    conn = get_conn()
    rows = conn.execute(
        """SELECT b.*,
                  COALESCE(SUM(CASE WHEN o.status='filled' THEN 1 ELSE 0 END), 0) AS filled_count,
                  COALESCE(SUM(CASE WHEN o.status IN ('expired','cancelled') THEN 1 ELSE 0 END), 0) AS failed_count,
                  COALESCE(AVG(CASE WHEN o.status='filled' THEN 1.0 WHEN o.id IS NOT NULL THEN 0.0 END), 0) * 100 AS fill_rate
           FROM agent_decision_batch b
           LEFT JOIN agent_order o ON o.decision_batch_id=b.id
           WHERE b.agent_id=?
           GROUP BY b.id
           ORDER BY b.trade_date DESC, b.created_at DESC
           LIMIT ?""",
        (agent_id, max(1, int(limit or 30))),
    ).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


@router.get("/{agent_id}/eval")
async def get_agent_eval(agent_id: int, days: int = Query(90, description="返回天数")):
    conn = get_conn()
    data = list_agent_eval(conn, agent_id, days)
    conn.close()
    return {"items": data}


@router.get("/{agent_id}/cost")
async def get_agent_cost(agent_id: int, days: int = Query(30, description="返回天数")):
    conn = get_conn()
    data = list_agent_cost(conn, agent_id, days)
    conn.close()
    return {"items": data}


@router.get("/{agent_id}/ideas")
async def get_agent_ideas(
    agent_id: int,
    days: int = Query(30, description="返回天数"),
    status: str = Query("", description="candidate/watchlist/promoted/rejected/traded/expired"),
):
    conn = get_conn()
    data = list_agent_ideas(conn, agent_id, days, status)
    summary = idea_summary(conn, agent_id, days)
    conn.close()
    return {"items": data, "summary": summary}


@router.get("/{agent_id}/idea-outcomes")
async def get_agent_idea_outcomes(agent_id: int, days: int = Query(90, description="返回天数")):
    conn = get_conn()
    data = list_agent_ideas(conn, agent_id, days)
    summary = idea_summary(conn, agent_id, days)
    conn.close()
    return {"items": data, "summary": summary}


@router.post("/ideas/outcome/update")
async def update_agent_ideas_outcome(limit: int = Query(300)):
    conn = get_conn()
    result = update_agent_idea_outcomes(conn, limit)
    conn.commit()
    conn.close()
    return result


@router.get("/{agent_id}/prompt-preview")
async def get_agent_prompt_preview(agent_id: int):
    conn = get_conn()
    agent = conn.execute("SELECT * FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    if not agent:
        conn.close()
        return {"error": "Agent not found"}
    trade_date = conn.execute(
        "SELECT COALESCE(MAX(trade_date), strftime('%Y%m%d','now')) FROM agent_daily_report WHERE agent_id=?",
        (agent_id,),
    ).fetchone()[0]
    risk_config = json.loads(agent["risk_config"] or "{}")
    board_permissions = _effective_board_permissions(agent_id, str(trade_date), risk_config, conn)
    stock_pool = AgentManager.list_stock_pool(agent_id, active_only=True)
    context = prepare_evolution_context(agent_id, agent["display_name"] or agent["name"], str(trade_date), conn)
    context["agent_config"] = {
        "agent_type": agent["agent_type"],
        "style_prompt": risk_config.get("style_prompt") or "",
        "user_strategy_original": risk_config.get("user_strategy_original") or "",
        "preferred_strategies": risk_config.get("preferred_strategies") or [
            s.strip() for s in str(agent["strategy_ids"] or "").split(",") if s.strip()
        ],
        "allowed_tools": risk_config.get("allowed_tools") or [],
        "stage_prompts": risk_config.get("stage_prompts") or {},
        "board_permissions": board_permissions,
        "stock_pool_enabled": bool(risk_config.get("stock_pool_enabled")),
        "allow_out_of_pool": bool(risk_config.get("allow_out_of_pool")),
        "stock_pool": stock_pool,
    }
    system_doc = get_public_system_summary(agent_id, conn)
    conn.close()
    return {
        "system_prompt": AGENT_SYSTEM_PROMPT,
        "trade_date": str(trade_date),
        "daily_context": {
            "agent": dict(agent),
            "board_permissions": board_permissions,
            "strategy_ids": agent["strategy_ids"],
            "stock_pool": stock_pool,
        },
        "evolution_memory": context.get("memory_snapshot") or context.get("memory") or {},
        "evolution_prompt": format_evolution_prompt(context),
        "system_doc": system_doc.get("system_doc") or "",
        "agent_config": context["agent_config"],
    }


@router.get("/{agent_id}/stock-pool")
async def get_agent_stock_pool_api(agent_id: int):
    return {
        "items": AgentManager.list_stock_pool(agent_id),
        "strategy_versions": AgentManager.list_user_strategy_versions(agent_id, 20),
    }


@router.put("/{agent_id}/stock-pool")
async def replace_agent_stock_pool_api(agent_id: int, req: StockPoolReplaceRequest):
    if not AgentManager.get(agent_id):
        return {"error": "Agent not found"}
    rows = AgentManager.replace_stock_pool(agent_id, [item.model_dump() for item in req.items])
    return {"items": rows}


@router.post("/{agent_id}/stock-pool/item")
async def upsert_agent_stock_pool_item_api(agent_id: int, req: StockPoolItemRequest):
    if not AgentManager.get(agent_id):
        return {"error": "Agent not found"}
    item = AgentManager.upsert_stock_pool_item(agent_id, req.model_dump())
    if not item:
        return {"error": "invalid ts_code"}
    return {"item": item}


@router.delete("/{agent_id}/stock-pool/{ts_code}")
async def delete_agent_stock_pool_item_api(agent_id: int, ts_code: str):
    AgentManager.delete_stock_pool_item(agent_id, ts_code)
    return {"ok": True}


@router.post("/create")
async def create_agent_api(req: CreateAgentRequest):
    """创建新 Agent"""
    agent_id = AgentManager.create(
        name=req.name,
        display_name=req.display_name,
        agent_type=req.agent_type,
        strategy_ids=req.strategy_ids,
        risk_config=req.risk_config,
        initial_capital=req.initial_capital,
    )
    return {"id": agent_id, "name": req.name}


@router.get("/{agent_id}/race")
async def get_agent_race(agent_id: int, days: int = Query(90, description="返回天数")):
    conn = get_conn()
    data = agent_race_detail(agent_id, conn, days)
    conn.close()
    return data


@router.get("/{agent_id}/evolution/timeline")
async def get_agent_evolution_timeline(agent_id: int, limit: int = Query(50, description="返回条数")):
    conn = get_conn()
    data = list_timeline(agent_id, conn, limit)
    conn.close()
    return {"timeline": data}


@router.get("/{agent_id}/system-doc")
async def get_agent_system_doc(agent_id: int):
    conn = get_conn()
    data = get_public_system_summary(agent_id, conn)
    conn.close()
    return data


@router.get("/{agent_id}/system-doc/versions")
async def get_agent_system_doc_versions(agent_id: int):
    return {"versions": list_versions(agent_id)}


@router.post("/{agent_id}/reflection/run")
async def run_agent_reflection(agent_id: int, task_type: str = Query("manual")):
    conn = get_conn()
    agent = conn.execute("SELECT display_name FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    if not agent:
        conn.close()
        return {"error": "Agent not found"}
    trade_date = conn.execute(
        "SELECT COALESCE(MAX(trade_date), strftime('%Y%m%d','now')) FROM agent_daily_report WHERE agent_id=?",
        (agent_id,),
    ).fetchone()[0]
    cur = conn.execute(
        """INSERT INTO agent_reflection_task
           (agent_id, trade_date, task_type, trigger_reason, input_json)
           VALUES (?, ?, ?, '手动触发', '{}')""",
        (agent_id, str(trade_date), task_type),
    )
    task_id = cur.lastrowid
    payload = build_reflection_input(agent_id, str(trade_date), conn)
    conn.execute(
        "UPDATE agent_reflection_task SET input_json=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False, default=str), task_id),
    )
    conn.commit()
    conn.close()
    result = run_reflection_task(task_id)
    return {"task_id": task_id, "result": result}


@router.delete("/{agent_id}")
async def delete_agent_api(agent_id: int):
    """删除 Agent"""
    AgentManager.delete(agent_id)
    return {"status": "deleted", "id": agent_id}


@router.put("/{agent_id}/rename")
async def rename_agent_api(agent_id: int, display_name: str = Query(...)):
    """重命名 Agent"""
    AgentManager.rename(agent_id, display_name)
    return {"status": "ok"}


@router.put("/{agent_id}/configure")
async def configure_agent_api(agent_id: int, req: ConfigureAgentRequest):
    """配置 Agent 风控参数和策略"""
    AgentManager.configure(agent_id, req.risk_config, req.strategy_ids)
    return {"status": "ok"}


@router.post("/{agent_id}/simulate")
async def simulate_agent_day(agent_id: int, trade_date: str = Query(..., description="交易日 YYYYMMDD")):
    """模拟 Agent 一天的交易撮合"""
    conn = get_conn()
    # 获取行情并撮合
    from backend.data.loader import load_daily
    from backend.pipeline.order_executor import execute_orders
    from backend.db.repository import get_positions, get_pending_orders

    positions = get_positions(agent_id, conn)
    price_data = {}
    for pos in positions:
        df = load_daily(pos["ts_code"])
        if df is not None and not df.empty:
            latest = df[df["trade_date"] <= trade_date]
            if not latest.empty:
                row = latest.iloc[-1]
                price_data[pos["ts_code"]] = {
                    "open": row["open"], "high": row["high"],
                    "low": row["low"], "close": row["close"],
                    "pct_chg": row.get("pct_chg", 0),
                }

    trades = execute_orders(agent_id, trade_date, price_data)
    conn.commit()
    conn.close()
    return {"trades": len(trades), "date": trade_date}


@router.put("/{agent_id}/status")
async def set_or_toggle_agent_status(agent_id: int, req: Optional[StatusRequest] = None):
    """设置 Agent 状态；无 body 时兼容旧逻辑切换启用/禁用。"""
    if req and req.status:
        try:
            new_status = AgentManager.set_status(agent_id, req.status)
        except ValueError as e:
            return {"error": str(e)}
    else:
        new_status = AgentManager.toggle_status(agent_id)
    if new_status is None:
        return {"error": "Agent not found"}
    return {"status": new_status, "id": agent_id}


@router.get("/{agent_id}/schedule")
async def get_agent_schedule(agent_id: int):
    """获取 Agent 定时配置"""
    return {"schedule": AgentManager.get_schedule(agent_id)}


@router.put("/{agent_id}/schedule")
async def configure_agent_schedule(agent_id: int, req: ScheduleRequest):
    """配置 Agent cron 唤醒后的实际运行时间"""
    ok = AgentManager.configure_schedule(
        agent_id, req.enabled, req.review_time, req.push_time
    )
    if not ok:
        return {"error": "Agent not found"}
    return {"status": "ok", "schedule": AgentManager.get_schedule(agent_id)}


@router.post("/run-due")
async def run_due_agents_api():
    """由系统 cron 调用：运行到点且启用的 Agent。"""
    from backend.pipeline.daily_pipeline import run_due_agents
    return run_due_agents()


@router.get("/{agent_id}/reports")
async def list_agent_reports(agent_id: int, limit: int = Query(20, description="返回条数")):
    """获取 Agent 每日报告列表"""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT trade_date, total_assets, daily_return, cumulative_return, position_count, report_md_path "
        "FROM agent_daily_report WHERE agent_id = ? ORDER BY trade_date DESC LIMIT ?",
        (agent_id, limit),
    )
    reports = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"reports": reports, "total": len(reports)}


@router.get("/{agent_id}/reports/{trade_date}")
async def get_agent_report_content(agent_id: int, trade_date: str):
    """读取 Agent 某日的复盘报告 MD 内容"""
    import os
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT report_md_path, think_log_path FROM agent_daily_report WHERE agent_id = ? AND trade_date = ?",
        (agent_id, trade_date),
    )
    row = c.fetchone()
    conn.close()

    if not row:
        return {"error": "报告不存在"}

    result = {"report_content": "", "think_log_content": "", "trade_date": trade_date}

    for key, path in [("report_content", row["report_md_path"]), ("think_log_content", row["think_log_path"])]:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                result[key] = f.read()

    return result
