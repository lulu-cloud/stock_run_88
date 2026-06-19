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
from backend.telegram.message_gate import is_lightweight_action, preflight_route
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
        self.last_line = ""

    def start(self):
        send_chat_action(self.chat_id, "typing")
        result = send_message(self.chat_id, "⏳ 正在处理你的问题...")
        if result.get("ok"):
            self.message_id = int((result.get("result") or {}).get("message_id") or 0)
        else:
            _state["last_send_error"] = result.get("error", "")
        self.lines = ["⏳ 正在处理你的问题..."]

    def on_event(self, event: dict):
        send_chat_action(self.chat_id, "typing")
        line = self._format_event(event)
        if line and line != self.last_line:
            self.lines.append(line)
            self.last_line = line
        now = time.time()
        if self.message_id and (now - self.last_edit_at >= 0.8 or line):
            text = "\n".join(self.lines[-10:])
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
        if event_type == "phase":
            return "🔎 " + str(event.get("message") or "").strip()
        if event_type == "intent":
            intent = event.get("intent") or "chat"
            labels = {
                "recommend": "推荐/选股",
                "analyze": "单股分析",
                "market_data": "市场数据查询",
                "policy": "政策偏好",
                "ma_check": "均线判断",
                "followup": "上下文追问",
                "position_advice": "持仓处置",
                "identity": "身份介绍",
                "partnership_account": "合伙账户",
                "chat": "普通对话",
            }
            return f"🎯 识别意图: {labels.get(intent, intent)}"
        if event_type == "memory_context":
            short_count = int(event.get("short_count") or 0)
            memory_count = int(event.get("memory_count") or 0)
            has_summary = "有" if event.get("has_session_summary") else "无"
            lines = [f"🧠 加载记忆: 短期{short_count}条 / 长期{memory_count}条 / 会话摘要{has_summary}"]
            summary = self._clip(event.get("session_summary") or "", 90)
            if summary:
                lines.append(f"   摘要: {summary}")
            memories = [self._clip(x, 72) for x in (event.get("memory_preview") or []) if str(x).strip()]
            if memories:
                lines.append("   相关: " + "；".join(memories[:2]))
            recent = [self._clip(x, 82) for x in (event.get("recent_preview") or []) if str(x).strip()]
            if recent:
                lines.append("   最近: " + " / ".join(recent[-2:]))
            return "\n".join(lines)
        if event_type == "rule_start":
            return "🧭 已识别为规则选股请求，准备调用选股工具。"
        if event_type == "strategy_parse":
            strategy = event.get("strategy") or "custom"
            explanation = event.get("explanation") or ""
            return f"🧩 解析策略: {strategy} {explanation}".strip()
        if event_type == "tool_start":
            tool = event.get("tool") or "unknown_tool"
            args = self._format_args(event.get("args") or {})
            desc = self._clip(event.get("description") or self._known_tool_description(tool), 110)
            lines = [f"🛠️ 调用工具: {tool}"]
            if args:
                lines.append(f"   参数: {args}")
            if desc:
                lines.append(f"   用途: {desc}")
            return "\n".join(lines)
        if event_type == "tool":
            tool = event.get("tool") or "unknown_tool"
            suffix = "失败" if event.get("error") else "完成"
            desc = self._clip(event.get("description") or self._known_tool_description(tool), 90)
            preview = self._clip(event.get("result_preview") or "", 120)
            lines = [f"{'❌' if event.get('error') else '✅'} 工具{suffix}: {tool}"]
            if desc and not event.get("error"):
                lines.append(f"   来源: {desc}")
            if preview and not event.get("error"):
                lines.append(f"   摘要: {preview}")
            if event.get("error"):
                lines.append(f"   错误: {self._clip(event.get('error'), 100)}")
            return "\n".join(lines)
        if event_type == "llm_turn":
            turn = event.get("turn") or ""
            stage_tools = event.get("stage_tools") or []
            sample = "、".join(str(x) for x in stage_tools[:3])
            return f"🧭 生成计划: 第 {turn} 轮，可用工具{len(stage_tools)}个" + (f"\n   可用: {sample}" if sample else "")
        if event_type == "llm_decision":
            tools = [x for x in (event.get("tools") or []) if x]
            if tools:
                descriptions = event.get("tool_descriptions") or {}
                lines = ["🤖 决策: 需要调用 " + "、".join(tools[:4])]
                for name in tools[:3]:
                    desc = self._clip(descriptions.get(name) or self._known_tool_description(name), 72)
                    if desc:
                        lines.append(f"   - {name}: {desc}")
                return "\n".join(lines)
            return "🤖 决策: 已有证据足够，准备组织回复"
        if event_type == "finalizing":
            return "✍️ 正在整合证据，生成最终回复"
        return ""

    def _format_args(self, args: dict) -> str:
        if not args:
            return ""
        parts = []
        for key, value in list(args.items())[:4]:
            if any(secret in str(key).lower() for secret in ("token", "key", "secret", "password")):
                display = "***"
            else:
                display = self._clip(value, 42)
            parts.append(f"{key}={display}")
        return ", ".join(parts)

    def _clip(self, text: str, limit: int) -> str:
        value = " ".join(str(text or "").split())
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 1)] + "…"

    def _known_tool_description(self, tool_name: str) -> str:
        descriptions = {
            "recommend_search_stocks": "按自然语言策略筛选候选股票。",
            "recommend_analyze_stock": "分析单只股票的技术面、趋势、风险和推荐理由。",
            "recommend_compare_stocks": "对比多只股票的强弱、风险和适配度。",
            "recommend_check_ma_bullish": "判断股票是否符合多头均线发散或多头排列。",
            "recommend_price_volume_trend": "分析最近价格和成交量趋势。",
            "recommend_get_market_topic": "读取宏观报告中的指定主题，如北向资金、龙虎榜、涨停板质量。",
            "recommend_get_macro_report": "读取每日宏观市场报告。",
            "recommend_get_trader_memory": "读取交易员体系、赛马表现和推荐技能记忆。",
            "recommend_get_user_profile": "读取当前 Telegram 用户画像。",
            "recommend_get_watchlist": "读取当前用户关注股列表。",
            "recommend_get_stock_chip_distribution": "模拟个股筹码峰和成本分布。",
            "recommend_get_stock_fundamental_events": "读取个股业绩预告/业绩快报等基本面事件。",
            "recommend_find_ma_bullish_pullback": "筛选多头均线发散且回踩均线的股票。",
            "recommend_get_policy_preference": "读取近期政策偏好的产业方向。",
            "recommend_record_stock_interest": "记录用户提及或推荐过的股票，沉淀共享研究报告。",
            "partnership_account_tool": "管理合伙股票账户，计算每日盈亏分配、权益和历史记录。",
            "partnership_init_account_tool": "初始化两人合伙股票账户。",
            "partnership_daily_report_tool": "上报合伙账户总资产和出入金，并按昨日权益比例分配盈亏。",
            "partnership_status_tool": "查询合伙账户当前权益和累计盈亏。",
            "partnership_history_tool": "查询合伙账户最近分成历史。",
        }
        return descriptions.get(str(tool_name or ""), "")


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
    gate = preflight_route(text)
    if is_lightweight_action(gate.action):
        result = send_rich_message(chat_id, gate.reply, "推荐助手")
        if not result.get("ok"):
            _state["last_send_error"] = result.get("error", "")
        else:
            _state["last_send_error"] = ""
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
