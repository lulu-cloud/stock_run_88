"""Telegram Bot gateway for Agent notifications."""

import json
import html
import mimetypes
import os
import re
import uuid
import urllib.parse
import urllib.request

from backend.config import TELEGRAM_API_BASE, TELEGRAM_BOT_TOKEN
from backend.db.repository import get_conn, get_positions, list_trades


def _api_url(method: str) -> str:
    return f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/{method}"


def send_message(chat_id: str, text: str, parse_mode: str = "") -> dict:
    """Send a Telegram message. Returns a small status dict."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(_api_url("sendMessage"), data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def to_telegram_html(text: str) -> str:
    """Convert the project's simple Markdown-ish text to Telegram HTML."""
    escaped = html.escape(text or "")
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?m)^\*([^*\n]+)\*$", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?m)^-\s+\*([^*\n]+)\*", r"- <b>\1</b>", escaped)
    return escaped


def send_html_message(chat_id: str, text: str) -> dict:
    result = send_message(chat_id, to_telegram_html(text), parse_mode="HTML")
    if result.get("ok"):
        return result
    return send_message(chat_id, text)


def _multipart_request(method: str, fields: dict, file_field: str, file_path: str) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    boundary = f"----stockrun{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
    )
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        _api_url(method),
        data=b"".join(chunks),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_document(chat_id: str, file_path: str, caption: str = "") -> dict:
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1000]
    return _multipart_request("sendDocument", fields, "document", file_path)


def send_photo(chat_id: str, file_path: str, caption: str = "") -> dict:
    fields = {"chat_id": chat_id}
    if caption:
        fields["caption"] = caption[:1000]
    return _multipart_request("sendPhoto", fields, "photo", file_path)


def send_rich_message(chat_id: str, text: str, title: str = "分析报告") -> dict:
    """Send simple replies as Telegram HTML and complex Markdown as PNG/HTML artifact."""
    from backend.telegram.rendering import is_complex_markdown, render_markdown_png, write_html_report

    if not is_complex_markdown(text):
        return send_html_message(chat_id, text)
    caption = f"{title}\n复杂 Markdown/表格已渲染为附件。"
    render_mode = os.environ.get("TELEGRAM_RICH_RENDER_MODE", "photo").lower()
    if render_mode != "document":
        try:
            png_path = render_markdown_png(text, title)
            result = send_photo(chat_id, png_path, caption)
            if result.get("ok"):
                return result
        except Exception:
            pass
    try:
        html_path = write_html_report(text, title)
        result = send_document(chat_id, html_path, caption)
        if result.get("ok"):
            return result
    except Exception:
        pass
    return send_html_message(chat_id, text[:3900])


def edit_message_text(chat_id: str, message_id: int, text: str, parse_mode: str = "") -> dict:
    """Edit an existing Telegram message."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(_api_url("editMessageText"), data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def edit_html_message_text(chat_id: str, message_id: int, text: str) -> dict:
    result = edit_message_text(chat_id, message_id, to_telegram_html(text), parse_mode="HTML")
    if result.get("ok"):
        return result
    return edit_message_text(chat_id, message_id, text)


def delete_message(chat_id: str, message_id: int) -> dict:
    """Delete a Telegram message if possible."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    payload = {"chat_id": chat_id, "message_id": message_id}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(_api_url("deleteMessage"), data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_chat_action(chat_id: str, action: str = "typing") -> dict:
    """Show Telegram's transient typing indicator."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    payload = {"chat_id": chat_id, "action": action}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(_api_url("sendChatAction"), data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_bot_info() -> dict:
    """Return Telegram bot metadata without exposing the token."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    try:
        with urllib.request.urlopen(_api_url("getMe"), timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def bind_chat(agent_id: int, chat_id: str, username: str = "") -> dict:
    conn = get_conn()
    conn.execute(
        """INSERT INTO telegram_binding (agent_id, chat_id, username, enabled)
           VALUES (?, ?, ?, 1)
           ON CONFLICT(agent_id, chat_id) DO UPDATE SET
           username=excluded.username, enabled=1, updated_at=datetime('now')""",
        (agent_id, chat_id, username),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "agent_id": agent_id, "chat_id": chat_id}


def list_bindings(agent_id: int | None = None) -> list[dict]:
    conn = get_conn()
    if agent_id is None:
        rows = conn.execute("SELECT * FROM telegram_binding ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM telegram_binding WHERE agent_id=? ORDER BY id DESC",
            (agent_id,),
        ).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return result


def build_agent_daily_summary(agent_id: int, trade_date: str) -> str:
    conn = get_conn()
    agent = conn.execute("SELECT * FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    conn.close()
    if not agent:
        return f"Agent #{agent_id} 不存在"
    positions = get_positions(agent_id)
    trades = [t for t in list_trades(agent_id, 50) if str(t.get("trade_date")) == trade_date]
    lines = [
        f"{agent['display_name']} 每日交易摘要",
        f"日期: {trade_date}",
        f"成交: {len(trades)} 笔",
    ]
    for t in trades[:10]:
        lines.append(
            f"- {t['direction']} {t['ts_code']} {t.get('stock_name','')} "
            f"{t['quantity']}股 @{t['price']:.2f}"
        )
    lines.append(f"持仓: {len(positions)} 只")
    for p in positions[:10]:
        lines.append(
            f"- {p['ts_code']} {p.get('stock_name','')} {p['quantity']}股 "
            f"成本{p['avg_cost']:.2f} 现价{(p.get('current_price') or 0):.2f}"
        )
    return "\n".join(lines)


def push_agent_summary(agent_id: int, trade_date: str) -> dict:
    bindings = [b for b in list_bindings(agent_id) if b.get("enabled")]
    text = build_agent_daily_summary(agent_id, trade_date)
    try:
        from backend.telegram.digest import build_market_digest
        from backend.telegram.profile import get_profile
    except Exception:
        build_market_digest = None
        get_profile = None
    sent = []
    for b in bindings:
        msg = text
        if build_market_digest and get_profile:
            profile = get_profile(b["chat_id"])
            if profile.get("daily_push_enabled"):
                msg = build_market_digest(b["chat_id"], agent_id, trade_date)
        result = send_rich_message(b["chat_id"], msg, "每日交易摘要")
        sent.append({"chat_id": b["chat_id"], "result": result})
    return {
        "sent": sent,
        "message": text,
    }


def push_agent_waiting_data(agent_id: int, trade_date: str, next_retry_at: str = "") -> dict:
    """Notify bound Telegram chats that the daily report is delayed by missing market data."""
    bindings = [b for b in list_bindings(agent_id) if b.get("enabled")]
    conn = get_conn()
    agent = conn.execute("SELECT display_name FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    conn.close()
    agent_name = agent["display_name"] if agent else f"Agent #{agent_id}"
    retry_line = f"\n下一次重试: {next_retry_at}" if next_retry_at else ""
    msg = (
        f"{agent_name} {trade_date} 复盘暂未发送\n"
        f"原因: 当日行情数据还未全部就绪，系统已自动延后复盘和推送。"
        f"{retry_line}"
    )
    sent = []
    for b in bindings:
        try:
            from backend.telegram.profile import get_profile
            profile = get_profile(b["chat_id"])
            if not profile.get("daily_push_enabled"):
                continue
        except Exception:
            pass
        sent.append({"chat_id": b["chat_id"], "result": send_message(b["chat_id"], msg)})
    return {"sent": sent, "message": msg}
