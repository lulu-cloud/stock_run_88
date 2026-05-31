"""Telegram gateway API."""

from datetime import date
from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.config import TELEGRAM_BOT_TOKEN
from backend.telegram.gateway import bind_chat, get_bot_info, list_bindings, push_agent_summary
from backend.telegram.polling import get_polling_status, start_polling, stop_polling
from backend.telegram.recommender import (
    format_agent_performance,
    format_recommendation,
    format_simulation_performance,
    handle_text_message,
    run_recommend_react_agent,
)
from backend.telegram.profile import (
    add_watch,
    format_watchlist,
    get_profile,
    list_watch,
    remove_watch,
    update_profile,
)
from backend.telegram.stock_analysis import compare_stocks, generate_stock_report
from backend.telegram.stock_interest import record_stock_interest
from backend.telegram.knowledge import recommendation_trace, update_recommend_skill_feedback
from backend.telegram.evaluation import (
    get_recommend_outcome,
    list_recommend_eval,
    refresh_eval_feedback,
    update_recommend_outcomes,
)
from backend.db.repository import get_conn

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


class BindRequest(BaseModel):
    agent_id: int
    chat_id: str
    username: str = ""


class ChatTestRequest(BaseModel):
    text: str
    chat_id: str = "local"
    username: str = ""


class ProfileUpdateRequest(BaseModel):
    risk_level: str | None = None
    horizon: str | None = None
    preferred_sectors: list[str] | None = None
    excluded_sectors: list[str] | None = None
    max_results: int | None = None
    daily_push_enabled: bool | None = None
    username: str = ""


class WatchRequest(BaseModel):
    ts_code: str
    stock_name: str = ""
    note: str = ""


class AnalyzeRequest(BaseModel):
    ts_code: str
    chat_id: str = "local"


class CompareRequest(BaseModel):
    ts_codes: list[str]
    chat_id: str = "local"


class PushSettingsRequest(BaseModel):
    push_sector_strength_enabled: bool = True
    push_policy_enabled: bool = True
    push_watchlist_enabled: bool = True


class RecommendFeedbackRequest(BaseModel):
    feedback_type: str = "positive"
    feedback_text: str = ""


class StockInterestRefreshRequest(BaseModel):
    ts_code: str
    chat_id: str = "local"
    username: str = ""
    context: str = ""
    intent: str = "manual_refresh"


PUSH_SETTING_KEYS = {
    "push_sector_strength_enabled": True,
    "push_policy_enabled": True,
    "push_watchlist_enabled": True,
}


def _get_push_settings() -> dict:
    conn = get_conn()
    rows = conn.execute(
        f"SELECT key, value FROM system_settings WHERE key IN ({','.join('?' for _ in PUSH_SETTING_KEYS)})",
        tuple(PUSH_SETTING_KEYS.keys()),
    ).fetchall()
    conn.close()
    values = {k: v for k, v in PUSH_SETTING_KEYS.items()}
    for row in rows:
        values[row["key"]] = str(row["value"]).lower() in ("1", "true", "yes", "on")
    return values


@router.get("/bindings")
async def telegram_bindings(agent_id: int | None = Query(default=None)):
    return {"bindings": list_bindings(agent_id)}


@router.post("/bindings")
async def telegram_bind(req: BindRequest):
    return bind_chat(req.agent_id, req.chat_id, req.username)


@router.get("/push/settings")
async def telegram_push_settings():
    return {"settings": _get_push_settings()}


@router.put("/push/settings")
async def telegram_push_settings_update(req: PushSettingsRequest):
    conn = get_conn()
    for key, value in req.model_dump().items():
        if key in PUSH_SETTING_KEYS:
            conn.execute(
                """INSERT INTO system_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
                (key, "1" if value else "0"),
            )
    conn.commit()
    conn.close()
    return {"settings": _get_push_settings()}


@router.post("/push/test")
async def telegram_push_test(agent_id: int, trade_date: str = ""):
    effective_date = trade_date or date.today().strftime("%Y%m%d")
    return push_agent_summary(agent_id, effective_date)


@router.get("/status")
async def telegram_status():
    """查看 Bot Token、长轮询和 Bot 账号状态。"""
    bot_info = get_bot_info() if TELEGRAM_BOT_TOKEN else {"ok": False, "error": "token not configured"}
    return {
        "token_configured": bool(TELEGRAM_BOT_TOKEN),
        "bot": bot_info,
        "polling": get_polling_status(),
    }


@router.post("/polling/start")
async def telegram_polling_start():
    return start_polling()


@router.post("/polling/stop")
async def telegram_polling_stop():
    return stop_polling()


@router.post("/chat/test")
async def telegram_chat_test(req: ChatTestRequest):
    """本地测试 Telegram 文本解析，不需要真实 Telegram 消息。"""
    return {"reply": handle_text_message(req.text, req.chat_id, req.username)}


@router.get("/agent-performance/{agent_id}")
async def telegram_agent_performance(agent_id: int):
    return {"message": format_agent_performance(agent_id)}


@router.get("/simulation-performance/{sim_id}")
async def telegram_simulation_performance(sim_id: int):
    return {"message": format_simulation_performance(sim_id)}


@router.post("/recommend")
async def telegram_recommend(req: ChatTestRequest):
    return run_recommend_react_agent(req.text, req.chat_id, req.username)


@router.get("/recommend/trace/{recommendation_id}")
async def telegram_recommend_trace(recommendation_id: int):
    data = recommendation_trace(recommendation_id)
    if not data:
        return {"error": "recommendation not found"}
    return data


@router.get("/recommend/eval")
async def telegram_recommend_eval(chat_id: str = Query(""), days: int = Query(90)):
    return {"items": list_recommend_eval(chat_id, days)}


@router.get("/recommend/outcome/{recommendation_id}")
async def telegram_recommend_outcome(recommendation_id: int):
    data = get_recommend_outcome(recommendation_id)
    if not data:
        return {"error": "outcome not found"}
    return data


@router.post("/recommend/outcome/update")
async def telegram_recommend_outcome_update(limit: int = Query(200)):
    return update_recommend_outcomes(limit)


@router.post("/stock-interest/refresh")
async def telegram_stock_interest_refresh(req: StockInterestRefreshRequest):
    return record_stock_interest(req.chat_id, req.username, req.ts_code, req.context, req.intent, get_profile(req.chat_id))


@router.get("/recommend/latest")
async def telegram_recommend_latest(chat_id: str = Query("local")):
    conn = get_conn()
    row = conn.execute(
        """SELECT id FROM telegram_recommend_feedback
           WHERE chat_id=? ORDER BY created_at DESC, id DESC LIMIT 1""",
        (chat_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {"error": "recommendation not found"}
    return recommendation_trace(row["id"])


@router.post("/recommend/{recommendation_id}/feedback")
async def telegram_recommend_feedback(recommendation_id: int, req: RecommendFeedbackRequest):
    conn = get_conn()
    row = conn.execute("SELECT id FROM telegram_recommend_feedback WHERE id=?", (recommendation_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": "recommendation not found"}
    conn.execute(
        """UPDATE telegram_recommend_feedback
           SET feedback_type=?, feedback_text=?, updated_at=datetime('now')
           WHERE id=?""",
        (req.feedback_type, req.feedback_text, recommendation_id),
    )
    refresh_eval_feedback(recommendation_id, conn)
    update_recommend_skill_feedback(req.feedback_type, conn)
    conn.commit()
    conn.close()
    return {"ok": True, "id": recommendation_id}


@router.get("/profile/{chat_id}")
async def telegram_profile(chat_id: str):
    return {"profile": get_profile(chat_id)}


@router.put("/profile/{chat_id}")
async def telegram_profile_update(chat_id: str, req: ProfileUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None and k != "username"}
    return {"profile": update_profile(chat_id, updates, req.username)}


@router.get("/watchlist/{chat_id}")
async def telegram_watchlist(chat_id: str):
    return {"watchlist": list_watch(chat_id), "message": format_watchlist(chat_id)}


@router.post("/watchlist/{chat_id}")
async def telegram_watchlist_add(chat_id: str, req: WatchRequest):
    return add_watch(chat_id, req.ts_code, req.stock_name, req.note)


@router.delete("/watchlist/{chat_id}/{ts_code}")
async def telegram_watchlist_remove(chat_id: str, ts_code: str):
    return remove_watch(chat_id, ts_code)


@router.post("/analyze")
async def telegram_analyze(req: AnalyzeRequest):
    return {"message": generate_stock_report(req.ts_code, get_profile(req.chat_id))}


@router.post("/compare")
async def telegram_compare(req: CompareRequest):
    return {"message": compare_stocks(req.ts_codes, get_profile(req.chat_id))}
