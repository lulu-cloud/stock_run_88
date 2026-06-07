"""FastAPI 应用入口"""

import os
import time
import threading
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.auth import DashboardAuthMiddleware

app = FastAPI(title="A股多Agent智能投顾系统", version="0.1.0")
logger = logging.getLogger(__name__)


def _cors_allow_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOW_ORIGINS", "")
    if raw.strip():
        return [origin.strip() for origin in raw.replace(";", ",").split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:18000",
        "http://127.0.0.1:18000",
    ]

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
_macro_report_state = {
    "running": False,
    "last_check": "",
    "last_run_date": "",
    "last_result": None,
    "last_error": "",
    "interval_minutes": int(os.environ.get("MACRO_REPORT_INTERVAL_MINUTES", "10")),
}
_telegram_intraday_state = {
    "running": False,
    "last_check": "",
    "last_push_bucket": "",
    "last_result": None,
    "last_error": "",
    "interval_minutes": int(os.environ.get("TELEGRAM_INTRADAY_INTERVAL_MINUTES", "30")),
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


def _macro_report_loop():
    """后台宏观报告：在交易员复盘前生成公共市场日报。"""
    _macro_report_state["running"] = True
    print("[MacroReport] 启动，自动推导交易员最早复盘前30分钟运行")
    while _macro_report_state["running"]:
        try:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            _macro_report_state["last_check"] = now.strftime("%Y-%m-%d %H:%M:%S")
            from backend.macro.report import generate_macro_report, get_effective_macro_report_time
            from backend.pipeline.daily_pipeline import _latest_trading_day, check_data_freshness

            expected_date = _latest_trading_day(now.date()).strftime("%Y%m%d")
            report_time = get_effective_macro_report_time()
            _macro_report_state["report_time"] = report_time
            if now.strftime("%H:%M") >= report_time and _macro_report_state["last_run_date"] != expected_date:
                if not check_data_freshness(expected_date):
                    _macro_report_state["last_result"] = {
                        "status": "waiting_data",
                        "trade_date": expected_date,
                    }
                else:
                    result = generate_macro_report(expected_date, force=False)
                    _macro_report_state["last_result"] = result
                    _macro_report_state["last_run_date"] = expected_date
                    _macro_report_state["last_error"] = ""
                    print(f"[MacroReport] {expected_date} 生成完成: {result.get('status')}")
        except Exception as e:
            print(f"[MacroReport] 错误: {e}")
            _macro_report_state["last_error"] = str(e)
            _macro_report_state["last_result"] = {"error": str(e)}
        time.sleep(_macro_report_state["interval_minutes"] * 60)


def _is_trade_time(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    hm = now.strftime("%H:%M")
    return ("09:30" <= hm <= "11:30") or ("13:00" <= hm <= "15:05")


def _telegram_intraday_loop():
    """Telegram 盘中提醒：关注股异动、板块摘要和待触发条件单。"""
    _telegram_intraday_state["running"] = True
    print(f"[TelegramIntraday] 启动，每 {_telegram_intraday_state['interval_minutes']} 分钟检查一次")
    while _telegram_intraday_state["running"]:
        try:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            _telegram_intraday_state["last_check"] = now.strftime("%Y-%m-%d %H:%M:%S")
            if _is_trade_time(now):
                bucket = now.strftime("%Y%m%d-%H") + f"-{now.minute // max(1, _telegram_intraday_state['interval_minutes'])}"
                if bucket != _telegram_intraday_state.get("last_push_bucket"):
                    from backend.telegram.gateway import send_rich_message
                    from backend.telegram.recommender import build_intraday_push_message, list_intraday_push_chats

                    chats = list_intraday_push_chats()
                    sent = 0
                    errors = []
                    for chat_id in chats:
                        result = send_rich_message(chat_id, build_intraday_push_message(chat_id), "盘中提醒")
                        if result.get("ok"):
                            sent += 1
                        else:
                            errors.append({"chat_id": chat_id, "error": result.get("error", "")})
                    _telegram_intraday_state["last_push_bucket"] = bucket
                    _telegram_intraday_state["last_result"] = {"bucket": bucket, "chats": len(chats), "sent": sent, "errors": errors}
                    _telegram_intraday_state["last_error"] = ""
        except Exception as e:
            print(f"[TelegramIntraday] 错误: {e}")
            _telegram_intraday_state["last_error"] = str(e)
            _telegram_intraday_state["last_result"] = {"error": str(e)}
        time.sleep(_telegram_intraday_state["interval_minutes"] * 60)


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

    if os.environ.get("MACRO_REPORT_ENABLED", "1") == "1" and not _macro_report_state["running"]:
        t = threading.Thread(target=_macro_report_loop, daemon=True, name="macro-report")
        t.start()

    if os.environ.get("TELEGRAM_INTRADAY_ENABLED", "1") == "1" and not _telegram_intraday_state["running"]:
        from backend.config import TELEGRAM_BOT_TOKEN
        if TELEGRAM_BOT_TOKEN:
            t = threading.Thread(target=_telegram_intraday_loop, daemon=True, name="telegram-intraday")
            t.start()

    if os.environ.get("TELEGRAM_POLLING_ENABLED", "1") == "1":
        from backend.config import TELEGRAM_BOT_TOKEN
        if TELEGRAM_BOT_TOKEN:
            from backend.telegram.polling import start_polling
            start_polling()


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(DashboardAuthMiddleware)


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
        "macro_report": _macro_report_state,
        "telegram_intraday": _telegram_intraday_state,
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
from backend.api.macro_routes import router as macro_router
from backend.api.backtest_routes import router as backtest_router
from backend.api.company_routes import router as company_router
from backend.api.policy_routes import router as policy_router
from backend.api.simulation_routes import router as simulation_router
from backend.api.telegram_routes import router as telegram_router
from backend.auth import router as auth_router

app.include_router(auth_router)
app.include_router(strategy_router)
app.include_router(agent_router)
app.include_router(market_router)
app.include_router(macro_router)
app.include_router(backtest_router)
app.include_router(company_router)
app.include_router(policy_router)
app.include_router(simulation_router)
app.include_router(telegram_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18000)
