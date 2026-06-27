"""Asynchronous Telegram entrypoint for manual Agent settlement."""

from __future__ import annotations

import threading
import time

from backend.auth import is_allowed_telegram_user
from backend.pipeline.daily_pipeline import run_manual_settlement
from backend.telegram.gateway import send_rich_message


_worker_lock = threading.Lock()
_worker: threading.Thread | None = None
RESULT_NOTIFY_DELAY_SECONDS = 0.5


def _format_result(result: dict) -> str:
    status = str(result.get("status") or "unknown")
    message = str(result.get("message") or "").strip()
    trade_date = str(result.get("expected_trading_day") or "").strip()
    lines = ["手动结算结果"]
    if trade_date:
        lines.append(f"交易日: {trade_date}")
    if message:
        lines.append(message)
    agents = result.get("agents") or {}
    if agents:
        lines.extend(("", "交易员状态:"))
        for name, detail in agents.items():
            detail = detail if isinstance(detail, dict) else {}
            agent_status = str(detail.get("status") or ("ok" if not detail.get("error") else "failed"))
            error = str(detail.get("error") or "").strip()
            suffix = f": {error[:160]}" if error else ""
            lines.append(f"- {name}: {agent_status}{suffix}")
    if not message and not agents:
        lines.append(f"状态: {status}")
    return "\n".join(lines)


def _run_and_notify(chat_id: str, agent_id: int | None):
    global _worker
    try:
        time.sleep(RESULT_NOTIFY_DELAY_SECONDS)
        result = run_manual_settlement(
            agent_ids=[agent_id] if agent_id is not None else None,
            push_now=True,
        )
        send_rich_message(chat_id, _format_result(result), "手动结算")
    except Exception as exc:
        send_rich_message(chat_id, f"手动结算失败: {exc}", "手动结算")
    finally:
        with _worker_lock:
            _worker = None


def start_manual_settlement(
    chat_id: str,
    user_id: str = "",
    username: str = "",
    agent_id: int | None = None,
) -> str:
    """Validate and enqueue a manual settlement request."""
    global _worker
    if not is_allowed_telegram_user(user_id, username):
        return "当前 Telegram 用户没有手动结算权限。"
    if not chat_id:
        return "手动结算只能从有效的 Telegram 会话发起。"
    if agent_id is not None and int(agent_id) <= 0:
        return "用法: /settle 或 /settle <agent_id>"

    with _worker_lock:
        if _worker and _worker.is_alive():
            return "已有手动结算请求正在处理，请等待结果推送。"
        _worker = threading.Thread(
            target=_run_and_notify,
            args=(chat_id, int(agent_id) if agent_id is not None else None),
            daemon=True,
            name="telegram-manual-settlement",
        )
        _worker.start()

    target = f"交易员 #{agent_id}" if agent_id is not None else "全部已启用交易员"
    return f"已受理 {target} 的手动结算请求。系统会检查行情数据，并异步推送结果。"
