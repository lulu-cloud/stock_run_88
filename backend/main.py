"""FastAPI 应用入口"""

import os
import time
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="A股多Agent智能投顾系统", version="0.1.0")

# 调度器状态
_scheduler_state = {"running": False, "last_check": "", "last_result": None, "interval_minutes": 5}
_policy_state = {
    "running": False,
    "last_check": "",
    "last_run_date": "",
    "last_result": None,
    "interval_minutes": 30,
    "crawl_time": os.environ.get("POLICY_CRAWL_TIME", "21:00"),
}
_stock_universe_state = {
    "running": False,
    "last_check": "",
    "last_run_at": "",
    "last_result": None,
    "last_error": "",
    "interval_minutes": 60,
    "refresh_time": os.environ.get("STOCK_UNIVERSE_REFRESH_TIME", "20:30"),
    "min_interval_days": int(os.environ.get("STOCK_UNIVERSE_REFRESH_DAYS", "7")),
}


def _scheduler_loop():
    """后台调度线程：每隔 N 分钟检查并运行到点的 Agent"""
    _scheduler_state["running"] = True
    print(f"[Scheduler] 启动，每 {_scheduler_state['interval_minutes']} 分钟检查一次")
    while _scheduler_state["running"]:
        try:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            _scheduler_state["last_check"] = now.strftime("%Y-%m-%d %H:%M:%S")
            from backend.pipeline.daily_pipeline import run_due_agents
            result = run_due_agents(now)
            _scheduler_state["last_result"] = result
            if result.get("due", 0) > 0:
                statuses = [
                    str(v.get("status", "ok")) if isinstance(v, dict) else "ok"
                    for v in (result.get("agents") or {}).values()
                ]
                suffix = f" ({', '.join(statuses)})" if statuses else ""
                print(f"[Scheduler] {now.strftime('%H:%M')} 处理了 {result['due']} 个 agent{suffix}")
        except Exception as e:
            print(f"[Scheduler] 错误: {e}")
            _scheduler_state["last_result"] = {"error": str(e)}
        time.sleep(_scheduler_state["interval_minutes"] * 60)


def _policy_crawler_loop():
    """后台政策爬虫：每天固定时间后自动抓取一次。"""
    _policy_state["running"] = True
    print(f"[PolicyCrawler] 启动，每日 {_policy_state['crawl_time']} 后抓取一次")
    while _policy_state["running"]:
        try:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            _policy_state["last_check"] = now.strftime("%Y-%m-%d %H:%M:%S")
            today = now.strftime("%Y%m%d")
            if now.strftime("%H:%M") >= _policy_state["crawl_time"] and _policy_state["last_run_date"] != today:
                from backend.policy.crawler import run_policy_crawler
                result = run_policy_crawler(None, 10, True)
                _policy_state["last_result"] = {"count": len(result)}
                _policy_state["last_run_date"] = today
                print(f"[PolicyCrawler] {today} 自动抓取完成")
        except Exception as e:
            print(f"[PolicyCrawler] 错误: {e}")
            _policy_state["last_result"] = {"error": str(e)}
        time.sleep(_policy_state["interval_minutes"] * 60)


def _stock_universe_loop():
    """后台股票池刷新：每周更新一次全 A 股票基础列表。"""
    _stock_universe_state["running"] = True
    print(
        "[StockUniverse] 启动，每 "
        f"{_stock_universe_state['min_interval_days']} 天 "
        f"{_stock_universe_state['refresh_time']} 后刷新一次"
    )
    while _stock_universe_state["running"]:
        try:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            _stock_universe_state["last_check"] = now.strftime("%Y-%m-%d %H:%M:%S")
            if now.strftime("%H:%M") >= _stock_universe_state["refresh_time"]:
                from backend.db.repository import get_conn
                conn = get_conn()
                row = conn.execute(
                    "SELECT value FROM system_settings WHERE key='stock_universe_last_refresh'"
                ).fetchone()
                conn.close()
                last_run = None
                if row and row["value"]:
                    try:
                        last_run = datetime.strptime(str(row["value"])[:19], "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        last_run = None
                due = (
                    last_run is None
                    or now.replace(tzinfo=None) - last_run
                    >= timedelta(days=_stock_universe_state["min_interval_days"])
                )
                if due:
                    from backend.data.stock_universe import refresh_stock_universe
                    result = refresh_stock_universe()
                    _stock_universe_state["last_result"] = result
                    _stock_universe_state["last_run_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    _stock_universe_state["last_error"] = "" if result.get("status") == "ok" else str(result)
                    print(
                        "[StockUniverse] 刷新完成: "
                        f"total={result.get('total')} new={result.get('new_count')} "
                        f"missing={result.get('missing_count')}"
                    )
        except Exception as e:
            print(f"[StockUniverse] 错误: {e}")
            _stock_universe_state["last_error"] = str(e)
            _stock_universe_state["last_result"] = {"error": str(e)}
        time.sleep(_stock_universe_state["interval_minutes"] * 60)


@app.on_event("startup")
async def startup_init_db():
    """Ensure idempotent SQLite migrations are applied before APIs run."""
    from backend.db.schema import init_db
    init_db().close()

    # 启动后台调度线程
    if os.environ.get("SCHEDULER_ENABLED", "1") == "1" and not _scheduler_state["running"]:
        t = threading.Thread(target=_scheduler_loop, daemon=True, name="agent-scheduler")
        t.start()

    if os.environ.get("POLICY_CRAWLER_ENABLED", "1") == "1" and not _policy_state["running"]:
        t = threading.Thread(target=_policy_crawler_loop, daemon=True, name="policy-crawler")
        t.start()

    if os.environ.get("STOCK_UNIVERSE_REFRESH_ENABLED", "1") == "1" and not _stock_universe_state["running"]:
        t = threading.Thread(target=_stock_universe_loop, daemon=True, name="stock-universe")
        t.start()

    if os.environ.get("TELEGRAM_POLLING_ENABLED", "1") == "1":
        from backend.config import TELEGRAM_BOT_TOKEN
        if TELEGRAM_BOT_TOKEN:
            from backend.telegram.polling import start_polling
            start_polling()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/scheduler/status")
async def scheduler_status():
    """查看内置调度器状态"""
    return _scheduler_state


@app.get("/api/policy-scheduler/status")
async def policy_scheduler_status():
    """查看政策自动爬虫状态"""
    return _policy_state


@app.get("/api/automation/status")
async def automation_status():
    """查看每日链式自动化状态：行情、政策、Agent 调度。"""
    from backend.pipeline.daily_pipeline import get_data_fetch_state
    return {
        "scheduler": _scheduler_state,
        "policy": _policy_state,
        "stock_universe": _stock_universe_state,
        "market_data": get_data_fetch_state(),
    }


@app.get("/api/stock-universe/status")
async def stock_universe_status():
    return _stock_universe_state


@app.get("/api/shared-stock-report/{ts_code}")
async def shared_stock_report(ts_code: str):
    from backend.telegram.stock_interest import get_shared_stock_report
    return {"ts_code": ts_code, "report": get_shared_stock_report(ts_code)}


# 注册路由
from backend.api.strategy_routes import router as strategy_router
from backend.api.agent_routes import router as agent_router
from backend.api.market_routes import router as market_router
from backend.api.backtest_routes import router as backtest_router
from backend.api.company_routes import router as company_router
from backend.api.policy_routes import router as policy_router
from backend.api.simulation_routes import router as simulation_router
from backend.api.telegram_routes import router as telegram_router

app.include_router(strategy_router)
app.include_router(agent_router)
app.include_router(market_router)
app.include_router(backtest_router)
app.include_router(company_router)
app.include_router(policy_router)
app.include_router(simulation_router)
app.include_router(telegram_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18000)
