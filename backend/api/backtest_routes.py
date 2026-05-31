"""策略回测 API"""

import json
import sqlite3
from datetime import date, timedelta
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
from backend.backtest.engine import run_backtest
from backend.strategies.registry import StrategyRegistry
from backend.config import DATABASE_PATH

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    strategy_name: str
    params: dict = {}
    start_date: str
    end_date: str
    initial_capital: float = 150000.0
    stop_loss_pct: float = -8.0


PRESET_PERIODS = {
    "3d": "近3天",
    "1w": "近1周",
    "1m": "近1月",
    "1q": "近一季度",
    "ytd": "今年以来",
}


def _get_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------

def _save_backtest(strategy_name: str, params: dict, start_date: str, end_date: str,
                   initial_capital: float, stop_loss_pct: float,
                   metrics: dict, equity_curve: list, trades: list,
                   log: list, log_file: str) -> int:
    conn = _get_conn()
    c = conn.cursor()
    c.execute(
        """INSERT INTO backtest_task
           (strategy_name, params_json, start_date, end_date, initial_capital, stop_loss_pct,
            metrics_json, equity_curve_json, trades_json, log_json, log_file)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            strategy_name,
            json.dumps(params, ensure_ascii=False),
            start_date, end_date,
            initial_capital, stop_loss_pct,
            json.dumps(metrics, ensure_ascii=False),
            json.dumps(equity_curve, ensure_ascii=False),
            json.dumps(trades, ensure_ascii=False),
            json.dumps(log, ensure_ascii=False),
            log_file,
        ),
    )
    conn.commit()
    task_id = c.lastrowid
    conn.close()
    return task_id


def _serialize_row(row):
    if row is None:
        return None
    d = dict(row)
    for field in ["metrics_json", "equity_curve_json", "trades_json", "log_json", "params_json"]:
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field.rstrip("_json").replace("_json", "") if "_json" in field else field.replace("_json","")] = json.loads(d[field])
            except:
                pass
    # Parse JSON fields into their non-JSON counterparts
    if isinstance(d.get("params_json"), str):
        try: d["params"] = json.loads(d["params_json"])
        except: d["params"] = {}
    if isinstance(d.get("metrics_json"), str):
        try: d["metrics"] = json.loads(d["metrics_json"])
        except: d["metrics"] = {}
    if isinstance(d.get("equity_curve_json"), str):
        try: d["equity_curve"] = json.loads(d["equity_curve_json"])
        except: d["equity_curve"] = []
    if isinstance(d.get("trades_json"), str):
        try: d["trades"] = json.loads(d["trades_json"])
        except: d["trades"] = []
    if isinstance(d.get("log_json"), str):
        try: d["log"] = json.loads(d["log_json"])
        except: d["log"] = []
    return d


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/periods")
async def get_preset_periods():
    return {"periods": PRESET_PERIODS}


@router.post("/run")
async def run_backtest_api(req: BacktestRequest):
    result = run_backtest(
        strategy_name=req.strategy_name,
        params=req.params,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
        stop_loss_pct=req.stop_loss_pct,
    )
    if "error" in result:
        return {"error": result["error"]}

    # Auto-save to DB
    task_id = _save_backtest(
        req.strategy_name, req.params, req.start_date, req.end_date,
        req.initial_capital, req.stop_loss_pct,
        result["metrics"], result["equity_curve"], result["trades"],
        result["log"], result["log_file"],
    )

    return {
        "id": task_id,
        "strategy": req.strategy_name,
        "params": req.params,
        "start_date": req.start_date,
        "end_date": req.end_date,
        "metrics": result["metrics"],
        "equity_curve": result["equity_curve"],
        "trades": result["trades"],
        "log": result["log"],
        "log_file": result["log_file"],
    }


@router.get("/quick/{strategy_name}")
async def quick_backtest(
    strategy_name: str,
    period: str = Query(default="1m", description="3d/1w/1m/1q/ytd"),
):
    today = date.today()
    if period == "3d":
        start = (today - timedelta(days=5)).strftime("%Y%m%d")
    elif period == "1w":
        start = (today - timedelta(days=10)).strftime("%Y%m%d")
    elif period == "1q":
        start = (today - timedelta(days=95)).strftime("%Y%m%d")
    elif period == "ytd":
        start = f"{today.year}0101"
    else:
        start = (today - timedelta(days=35)).strftime("%Y%m%d")

    end = today.strftime("%Y%m%d")

    result = run_backtest(strategy_name, {}, start, end)
    if "error" in result:
        return {"error": result["error"]}

    # Auto-save to DB
    task_id = _save_backtest(
        strategy_name, {}, start, end,
        150000.0, -8.0,
        result["metrics"], result["equity_curve"], result["trades"],
        result["log"], result["log_file"],
    )

    return {
        "id": task_id,
        "strategy": strategy_name,
        "period": period,
        "start_date": start,
        "end_date": end,
        "metrics": result["metrics"],
        "equity_curve": result["equity_curve"],
        "trades": result.get("trades", []),
        "log": result.get("log", []),
        "log_file": result.get("log_file", ""),
    }


@router.get("/tasks")
async def list_backtest_tasks(limit: int = Query(default=20, le=100)):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, strategy_name, start_date, end_date, initial_capital, metrics_json, created_at "
        "FROM backtest_task ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    tasks = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("metrics_json"), str):
            try: d["metrics"] = json.loads(d["metrics_json"])
            except: d["metrics"] = {}
        tasks.append(d)
    return {"tasks": tasks}


@router.get("/task/{task_id}")
async def get_backtest_task(task_id: int):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM backtest_task WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": "任务不存在"}
    return {"task": _serialize_row(row)}


@router.delete("/task/{task_id}")
async def delete_backtest_task(task_id: int):
    conn = _get_conn()
    conn.execute("DELETE FROM backtest_task WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
