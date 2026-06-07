"""Telegram long polling service.

This intentionally does not use webhooks. It polls getUpdates with the bot
token configured in TELEGRAM_BOT_TOKEN and answers text messages in-place.
"""

import json
import threading
import time
import urllib.parse
import urllib.request

from backend.config import TELEGRAM_BOT_TOKEN
from backend.telegram.gateway import (
    _api_url,
    delete_message,
    edit_html_message_text,
    edit_message_text,
    send_chat_action,
    send_html_message,
    send_rich_message,
    send_message,
)
from backend.telegram.recommender import handle_text_message
from backend.auth import generate_login_code, format_login_code_message, is_allowed_telegram_user


_state = {
    "running": False,
    "offset": 0,
    "last_error": "",
    "last_update_id": 0,
    "handled": 0,
    "last_send_error": "",
    "last_message": "",
}
_thread: threading.Thread | None = None


class _TelegramProgress:
    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.message_id = 0
        self.lines: list[str] = []
        self.last_edit_at = 0.0

    def start(self):
        send_chat_action(self.chat_id, "typing")
        result = send_message(self.chat_id, "正在处理你的问题...")
        if result.get("ok"):
            self.message_id = int((result.get("result") or {}).get("message_id") or 0)
        else:
            _state["last_send_error"] = result.get("error", "")
        self.lines = ["正在处理你的问题..."]

    def on_event(self, event: dict):
        send_chat_action(self.chat_id, "typing")
        line = self._format_event(event)
        if line:
            self.lines.append(line)
        now = time.time()
        if self.message_id and (now - self.last_edit_at >= 0.8 or line):
            text = "\n".join(self.lines[-8:])
            result = edit_html_message_text(self.chat_id, self.message_id, text[:3500])
            if not result.get("ok"):
                _state["last_send_error"] = result.get("error", "")
            self.last_edit_at = now

    def finish(self):
        if self.message_id:
            result = delete_message(self.chat_id, self.message_id)
            if not result.get("ok"):
                _state["last_send_error"] = result.get("error", "")

    def _format_event(self, event: dict) -> str:
        event_type = event.get("type")
        if event_type == "rule_start":
            return "已识别为规则选股请求，准备调用选股工具。"
        if event_type == "strategy_parse":
            strategy = event.get("strategy") or "custom"
            explanation = event.get("explanation") or ""
            return f"解析策略: {strategy} {explanation}".strip()
        if event_type == "tool_start":
            tool = event.get("tool") or "unknown_tool"
            return f"调用工具: {tool}"
        if event_type == "tool":
            tool = event.get("tool") or "unknown_tool"
            suffix = "失败" if event.get("error") else "完成"
            return f"工具{suffix}: {tool}"
        if event_type == "llm_turn":
            turn = event.get("turn") or ""
            return f"推荐助手思考中: 第 {turn} 轮"
        return ""


def get_polling_status() -> dict:
    return {
        **_state,
        "token_configured": bool(TELEGRAM_BOT_TOKEN),
        "thread_alive": bool(_thread and _thread.is_alive()),
    }


def _telegram_get_updates(offset: int) -> list[dict]:
    params = urllib.parse.urlencode({
        "timeout": 25,
        "offset": offset,
        "allowed_updates": json.dumps(["message"]),
    })
    with urllib.request.urlopen(f"{_api_url('getUpdates')}?{params}", timeout=35) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "getUpdates failed")
    return payload.get("result") or []


def _handle_update(update: dict):
    message = update.get("message") or {}
    text = message.get("text")
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    chat_id = str(chat.get("id") or "")
    user_id = str(from_user.get("id") or "")
    username = from_user.get("username") or from_user.get("first_name") or chat.get("username") or chat.get("first_name") or ""
    thread_id = str(message.get("message_thread_id") or "default")
    chat_type = str(chat.get("type") or "")
    if not text or not chat_id:
        return
    lower = text.strip().lower()
    if lower.startswith("/whoami"):
        allowed = "是" if is_allowed_telegram_user(user_id, username) else "否"
        send_message(
            chat_id,
            "Telegram 身份\n\n"
            f"user_id: {user_id}\n"
            f"chat_id: {chat_id}\n"
            f"username: {username or '-'}\n"
            f"看板白名单: {allowed}",
        )
        _state["last_message"] = text[:200]
        return
    if lower.startswith("/login"):
        result = generate_login_code(user_id, chat_id, username)
        send_message(chat_id, format_login_code_message(result, user_id))
        _state["last_message"] = text[:200]
        return
    progress = _TelegramProgress(chat_id)
    try:
        progress.start()
        response = handle_text_message(
            text,
            chat_id,
            username,
            progress_callback=progress.on_event,
            user_id=user_id,
            thread_id=thread_id,
            chat_type=chat_type,
        )
        progress.finish()
        result = send_rich_message(chat_id, response, "推荐助手回复")
        if not result.get("ok"):
            _state["last_send_error"] = result.get("error", "")
        else:
            _state["last_send_error"] = ""
        _state["last_message"] = text[:200]
    except Exception as exc:
        progress.finish()
        _state["last_send_error"] = str(exc)
        send_message(chat_id, f"处理失败: {exc}")


def _poll_loop():
    _state["running"] = True
    while _state["running"]:
        if not TELEGRAM_BOT_TOKEN:
            _state["last_error"] = "TELEGRAM_BOT_TOKEN is not configured"
            time.sleep(30)
            continue
        try:
            updates = _telegram_get_updates(int(_state.get("offset") or 0))
            for update in updates:
                update_id = int(update.get("update_id") or 0)
                _state["last_update_id"] = update_id
                _state["offset"] = update_id + 1
                _handle_update(update)
                _state["handled"] += 1
            _state["last_error"] = ""
        except Exception as e:
            _state["last_error"] = str(e)
            time.sleep(5)


def start_polling() -> dict:
    global _thread
    if _thread and _thread.is_alive():
        _state["running"] = True
        return get_polling_status()
    _thread = threading.Thread(target=_poll_loop, daemon=True, name="telegram-polling")
    _thread.start()
    return get_polling_status()


def stop_polling() -> dict:
    _state["running"] = False
    return get_polling_status()
