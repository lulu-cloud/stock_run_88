"""模拟交易 API"""

import json
import sqlite3
import threading
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
from backend.config import DATABASE_PATH

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


class SimAgentConfig(BaseModel):
    display_name: str = "Agent"
    strategy_name: str = "ma_pullback"
    initial_capital: float = 150000.0
    reasoning_effort: str = "high"


class StartSimRequest(BaseModel):
    name: str = ""
    agents: list[SimAgentConfig]
    start_date: str
    end_date: str


def _get_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/start")
async def start_simulation(req: StartSimRequest):
    """启动模拟交易任务 (后台线程运行)"""
    conn = _get_conn()
    c = conn.cursor()
    agents_json = json.dumps([a.model_dump() for a in req.agents], ensure_ascii=False)
    c.execute(
        """INSERT INTO simulation_task (name, start_date, end_date, agents_config, status)
           VALUES (?, ?, ?, ?, 'running')""",
        (req.name or f"模拟_{req.start_date}_{req.end_date}", req.start_date, req.end_date, agents_json),
    )
    sim_id = c.lastrowid
    conn.commit()
    conn.close()

    # 将 agents_config 转为 list[dict] 供引擎使用
    agents_cfg = [a.model_dump() for a in req.agents]

    def _run_and_save():
        from backend.simulation.sim_engine import run_simulation
        import json as _json
        _partial_results = {"agents": []}

        def _progress(value: float, partial=None):
            connp = _get_conn()
            progress_val = round(float(value), 2)
            if partial:
                _partial_results["agents"] = partial
                connp.execute(
                    "UPDATE simulation_task SET progress=?, results_json=? WHERE id=? AND status='running'",
                    (progress_val, _json.dumps(_partial_results, ensure_ascii=False), sim_id),
                )
            else:
                connp.execute(
                    "UPDATE simulation_task SET progress=? WHERE id=? AND status='running'",
                    (progress_val, sim_id),
                )
            connp.commit()
            connp.close()

        try:
            result = run_simulation(
                agents_cfg, req.start_date, req.end_date, req.name,
                progress_callback=_progress,
            )
            conn2 = _get_conn()
            if "error" in result:
                conn2.execute(
                    "UPDATE simulation_task SET status='failed', progress=100, results_json=? WHERE id=?",
                    (_json.dumps({"error": result["error"]}, ensure_ascii=False), sim_id),
                )
            else:
                conn2.execute(
                    "UPDATE simulation_task SET status='completed', progress=100, results_json=?, completed_at=datetime('now') WHERE id=?",
                    (_json.dumps(result, ensure_ascii=False), sim_id),
                )
            conn2.commit()
            conn2.close()
        except Exception as e:
            conn2 = _get_conn()
            conn2.execute(
                "UPDATE simulation_task SET status='failed', progress=100, results_json=? WHERE id=?",
                (_json.dumps({"error": str(e)}, ensure_ascii=False), sim_id),
            )
            conn2.commit()
            conn2.close()

    threading.Thread(target=_run_and_save, daemon=True).start()
    return {"id": sim_id, "status": "running", "message": "模拟任务已启动"}


@router.get("/status/{sim_id}")
async def simulation_status(sim_id: int):
    """查询模拟进度"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, name, status, progress, start_date, end_date, created_at FROM simulation_task WHERE id=?",
        (sim_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {"error": "任务不存在"}
    return dict(row)


@router.get("/result/{sim_id}")
async def simulation_result(sim_id: int):
    """获取模拟完整结果"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM simulation_task WHERE id=? AND status='completed'", (sim_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"error": "任务不存在或未完成"}

    d = dict(row)
    if d.get("results_json") and isinstance(d["results_json"], str):
        try:
            d["results"] = json.loads(d["results_json"])
        except json.JSONDecodeError:
            d["results"] = {}
    return d


@router.get("/tasks")
async def list_simulation_tasks(limit: int = Query(20, le=50)):
    """获取模拟任务历史列表"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, name, status, progress, start_date, end_date, created_at, completed_at "
        "FROM simulation_task ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return {"tasks": [dict(r) for r in rows]}


@router.delete("/task/{sim_id}")
async def delete_simulation_task(sim_id: int):
    """删除模拟任务"""
    conn = _get_conn()
    conn.execute("DELETE FROM simulation_task WHERE id=?", (sim_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
