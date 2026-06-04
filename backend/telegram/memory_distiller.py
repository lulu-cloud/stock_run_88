"""Background LLM distillation for Telegram scoped memory."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from backend.db.repository import get_conn
from backend.telegram.memory import (
    DEFAULT_THREAD_ID,
    profile_scope_id,
    short_term_message_limit,
    thread_scope_id,
    upsert_memory_item,
)


MIN_USER_MESSAGES = int(os.environ.get("TELEGRAM_MEMORY_DISTILL_MIN_USER_MESSAGES", "3") or 3)
MAX_CONTEXT_MESSAGES = int(os.environ.get("TELEGRAM_MEMORY_DISTILL_MAX_MESSAGES", "24") or 24)
CONFIDENCE_THRESHOLD = float(os.environ.get("TELEGRAM_MEMORY_DISTILL_CONFIDENCE", "0.72") or 0.72)
DISTILL_ENABLED = os.environ.get("TELEGRAM_MEMORY_DISTILL_ENABLED", "1").lower() not in {"0", "false", "off", "no"}

_running: set[str] = set()
_lock = threading.Lock()


SYSTEM_PROMPT = """你是 Telegram 股票推荐助手的长期记忆提炼器。

任务：从最近对话中提炼稳定、可复用、值得长期保存的信息。不要保存隐私、一次性闲聊、临时情绪、无明确复用价值的信息。

输出必须是 JSON，不要 Markdown 代码块，结构如下：
{
  "user_preferences":[{"content":"用户偏好短线右侧交易","confidence":0.82,"importance":0.75}],
  "risk_profile":[{"content":"用户风险偏好偏高，但不喜欢无理由追高","confidence":0.78,"importance":0.75}],
  "stock_interests":[{"ts_code":"000725.SZ","name":"京东方A","content":"用户多次询问京东方A走势","confidence":0.8,"importance":0.8}],
  "chat_norms":[{"content":"本群默认讨论A股，回答要简洁","confidence":0.76,"importance":0.7}],
  "thread_summary":[{"content":"本话题围绕多头均线回踩策略选股","confidence":0.78,"importance":0.68}],
  "do_not_remember":["一次性闲聊或低置信内容"]
}

规则：
- confidence 低于 0.7 的内容不要放入长期记忆字段，可以放入 do_not_remember。
- user_preferences/risk_profile/stock_interests 只写当前用户的稳定偏好或关注标的。
- chat_norms 只写当前群聊的群规则、共同偏好或长期背景。
- thread_summary 只写当前 topic/话题的稳定上下文。
- content 必须短、具体、可复用。
"""


def _scope_key(chat_id: str, user_id: str, thread_id: str) -> str:
    return f"{chat_id or 'local'}|{user_id or ''}|{thread_id or DEFAULT_THREAD_ID}"


def _build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.1,
        extra_body={"thinking": {"type": "disabled"}},
        timeout=90,
    )


def _extract_json(text: str) -> dict:
    raw = (text or "").strip()
    candidates = re.findall(r"```json\s*(\{.*?\})\s*```", raw, flags=re.S | re.I)
    candidates.append(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start:end + 1])
    for item in reversed(candidates):
        try:
            data = json.loads(item)
            return data if isinstance(data, dict) else {}
        except Exception:
            continue
    return {}


def _recent_messages(chat_id: str, user_id: str, thread_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, user_id, role, content, intent, created_at
           FROM telegram_conversation_message
           WHERE chat_id=? AND thread_id=?
             AND (role!='user' OR user_id=? OR ?='')
           ORDER BY id DESC
           LIMIT ?""",
        (chat_id or "local", thread_id or DEFAULT_THREAD_ID, user_id or "", user_id or "", MAX_CONTEXT_MESSAGES),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def _distill_state(chat_id: str, user_id: str, thread_id: str) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM telegram_memory_distill_state
               WHERE chat_id=? AND user_id=? AND thread_id=?""",
            (chat_id or "local", user_id or "", thread_id or DEFAULT_THREAD_ID),
        ).fetchone()
        return dict(row) if row else {}
    except sqlite3.OperationalError:
        return {"status": "schema_missing"}
    finally:
        conn.close()


def _message_counts(chat_id: str, user_id: str, thread_id: str, after_id: int = 0) -> dict:
    conn = get_conn()
    params = (chat_id or "local", thread_id or DEFAULT_THREAD_ID, int(after_id or 0), user_id or "", user_id or "")
    try:
        row = conn.execute(
            """SELECT
                 COUNT(*) AS total_count,
                 SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) AS user_count,
                 MAX(id) AS latest_message_id
               FROM telegram_conversation_message
               WHERE chat_id=? AND thread_id=? AND id>?
                 AND (role!='user' OR user_id=? OR ?='')""",
            params,
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if not row:
        return {"total_count": 0, "user_count": 0, "latest_message_id": 0}
    return {
        "total_count": int(row["total_count"] or 0),
        "user_count": int(row["user_count"] or 0),
        "latest_message_id": int(row["latest_message_id"] or 0),
    }


def should_distill(chat_id: str, user_id: str, thread_id: str) -> tuple[bool, dict]:
    state = _distill_state(chat_id, user_id, thread_id)
    last_distilled = int(state.get("last_distilled_message_id") or 0)
    counts = _message_counts(chat_id, user_id, thread_id, last_distilled)
    detail = {"state": state, **counts, "min_user_messages": MIN_USER_MESSAGES}
    if not DISTILL_ENABLED:
        detail["reason"] = "disabled"
        return False, detail
    if state.get("status") == "schema_missing":
        detail["reason"] = "schema_missing"
        return False, detail
    if not DEEPSEEK_API_KEY:
        detail["reason"] = "missing_llm_key"
        return False, detail
    if str(state.get("status") or "") == "running":
        detail["reason"] = "already_running"
        return False, detail
    if counts["user_count"] < MIN_USER_MESSAGES:
        detail["reason"] = "not_enough_user_messages"
        return False, detail
    return True, detail


def _mark_state(
    chat_id: str,
    user_id: str,
    thread_id: str,
    chat_type: str,
    status: str,
    latest_message_id: int = 0,
    last_distilled_message_id: int | None = None,
    message_count: int = 0,
    last_error: str = "",
    result: dict | None = None,
) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT INTO telegram_memory_distill_state
           (chat_id, user_id, thread_id, chat_type, last_message_id, last_distilled_message_id,
            message_count, status, last_error, last_result_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(chat_id, user_id, thread_id) DO UPDATE SET
             chat_type=excluded.chat_type,
             last_message_id=max(telegram_memory_distill_state.last_message_id, excluded.last_message_id),
             last_distilled_message_id=COALESCE(excluded.last_distilled_message_id, telegram_memory_distill_state.last_distilled_message_id),
             message_count=excluded.message_count,
             status=excluded.status,
             last_error=excluded.last_error,
             last_result_json=excluded.last_result_json,
             updated_at=datetime('now')""",
        (
            chat_id or "local",
            user_id or "",
            thread_id or DEFAULT_THREAD_ID,
            chat_type or "",
            int(latest_message_id or 0),
            int(last_distilled_message_id) if last_distilled_message_id is not None else None,
            int(message_count or 0),
            status,
            last_error or "",
            json.dumps(result or {}, ensure_ascii=False, default=str),
        ),
    )
    conn.commit()
    conn.close()


def _memory_items(payload: dict, chat_id: str, user_id: str, thread_id: str) -> list[tuple[str, str, str, str, float]]:
    user_scope = profile_scope_id(chat_id or "local", user_id or "")
    chat_scope = chat_id or "local"
    thread_scope = thread_scope_id(chat_id or "local", thread_id or DEFAULT_THREAD_ID)
    items: list[tuple[str, str, str, str, float]] = []

    def add_many(field: str, scope: str, scope_id: str, memory_type: str):
        for item in payload.get(field) or []:
            if isinstance(item, str):
                content, confidence, importance = item, 1.0, 0.6
            elif isinstance(item, dict):
                confidence = float(item.get("confidence") or 0)
                importance = float(item.get("importance") or confidence or 0.5)
                code = item.get("ts_code") or ""
                name = item.get("name") or ""
                prefix = f"{code} {name}: ".strip() if code or name else ""
                content = prefix + str(item.get("content") or "").strip()
            else:
                continue
            if confidence >= CONFIDENCE_THRESHOLD and content:
                items.append((scope, scope_id, memory_type, content, max(importance, confidence)))

    add_many("user_preferences", "user", user_scope, "preference")
    add_many("risk_profile", "user", user_scope, "risk_profile")
    add_many("stock_interests", "user", user_scope, "stock_interest")
    add_many("chat_norms", "chat", chat_scope, "chat_norm")
    add_many("thread_summary", "thread", thread_scope, "summary")
    return items


def persist_distilled_memory(payload: dict, chat_id: str, user_id: str = "", thread_id: str = DEFAULT_THREAD_ID) -> list[dict]:
    """Persist high-confidence distilled memory items."""
    results: list[dict] = []
    for scope, scope_id, memory_type, content, importance in _memory_items(payload, chat_id, user_id, thread_id):
        results.append(upsert_memory_item(scope, scope_id, memory_type, content, importance))
    return results


def distill_now(chat_id: str, user_id: str = "", thread_id: str = DEFAULT_THREAD_ID, chat_type: str = "") -> dict:
    """Run one LLM distillation synchronously. Intended for background threads/tests."""
    chat_id = chat_id or "local"
    thread_id = thread_id or DEFAULT_THREAD_ID
    state = _distill_state(chat_id, user_id, thread_id)
    last_distilled = int(state.get("last_distilled_message_id") or 0)
    counts = _message_counts(chat_id, user_id, thread_id, last_distilled)
    latest_message_id = counts["latest_message_id"]
    _mark_state(chat_id, user_id, thread_id, chat_type, "running", latest_message_id, None, counts["total_count"])
    try:
        messages = _recent_messages(chat_id, user_id, thread_id)
        context_lines = [
            f"chat_id={chat_id}",
            f"user_id={user_id or 'unknown'}",
            f"thread_id={thread_id}",
            f"chat_type={chat_type or 'unknown'}",
            f"短期上下文目标: 最近 {short_term_message_limit() // 2} 轮；本次提炼上下文最多 {MAX_CONTEXT_MESSAGES} 条消息。",
            "",
            "最近对话:",
        ]
        for msg in messages:
            content = re.sub(r"\s+", " ", msg.get("content") or "").strip()[:500]
            context_lines.append(f"- #{msg.get('id')} {msg.get('role')}({msg.get('user_id') or ''}): {content}")
        llm = _build_llm()
        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content="\n".join(context_lines)),
        ])
        payload = _extract_json(str(getattr(response, "content", "") or ""))
        if not payload:
            raise ValueError("memory distiller did not return valid JSON")
        persisted = persist_distilled_memory(payload, chat_id, user_id, thread_id)
        result = {"persisted": persisted, "payload": payload}
        _mark_state(
            chat_id,
            user_id,
            thread_id,
            chat_type,
            "ok",
            latest_message_id,
            latest_message_id,
            counts["total_count"],
            "",
            result,
        )
        return {"ok": True, **result}
    except Exception as exc:
        _mark_state(
            chat_id,
            user_id,
            thread_id,
            chat_type,
            "error",
            latest_message_id,
            None,
            counts["total_count"],
            str(exc),
            {},
        )
        return {"ok": False, "error": str(exc)}


def maybe_schedule_memory_distillation(
    chat_id: str,
    user_id: str = "",
    thread_id: str = DEFAULT_THREAD_ID,
    chat_type: str = "",
) -> dict:
    """Schedule memory distillation after enough new conversation turns."""
    chat_id = chat_id or "local"
    thread_id = thread_id or DEFAULT_THREAD_ID
    key = _scope_key(chat_id, user_id, thread_id)
    ok, detail = should_distill(chat_id, user_id, thread_id)
    if not ok:
        return {"scheduled": False, **detail}
    with _lock:
        if key in _running:
            return {"scheduled": False, "reason": "already_running_in_process", **detail}
        _running.add(key)

    def _worker():
        try:
            distill_now(chat_id, user_id, thread_id, chat_type)
        finally:
            with _lock:
                _running.discard(key)

    thread = threading.Thread(target=_worker, daemon=True, name=f"telegram-memory-distill:{key[:40]}")
    thread.start()
    return {"scheduled": True, **detail}


def get_distill_state(chat_id: str = "local", user_id: str = "", thread_id: str = DEFAULT_THREAD_ID) -> dict:
    return _distill_state(chat_id or "local", user_id or "", thread_id or DEFAULT_THREAD_ID)
