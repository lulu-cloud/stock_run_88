"""Lightweight preflight routing for Telegram messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class GateResult:
    action: str
    reply: str = ""
    reason: str = ""


LIGHTWEIGHT_ACTIONS = {"simple_reply", "boundary_intro", "out_of_scope"}


COMMAND_PREFIXES = (
    "/login", "/whoami", "/watch", "/profile", "/memory", "/recommend",
    "/compare", "/intraday", "/alert", "/analyze", "/backtest", "/bt",
    "/daily", "/init", "/history", "/status", "/sim", "/bind",
    "/render_test", "/settle", "/start", "/help",
)

CONTEXTUAL_STOCK_INTENTS = {
    "recommend", "analyze", "position_advice", "compare", "ma_check",
    "market_data", "policy", "followup", "backtest", "intraday",
    "watchlist", "refresh", "help",
}
CONTEXT_FOLLOWUP_MINUTES = 30

STOCK_TASK_KEYWORDS = (
    "股票", "a股", "A股", "大盘", "上证", "深成", "创业板", "北向", "沪深港通",
    "板块", "热点", "策略", "选股", "推荐", "龙头", "强势", "涨停", "跌停", "炸板",
    "龙虎榜", "均线", "回踩", "突破", "放量", "缩量", "量价", "换手", "筹码", "筹码峰",
    "持仓", "仓位", "重仓", "清仓", "减仓", "加仓", "补仓", "止损", "止盈", "割肉",
    "公告", "财报", "业绩", "业绩预告", "业绩快报", "政策", "复盘", "交易员", "agent",
    "模拟", "回测", "关注股", "条件单", "买", "卖", "上车", "下车",
)

ANALYSIS_WORDS = (
    "怎么看", "如何看", "分析", "点评", "评价", "能不能买", "能上车", "是否推荐",
    "趋势", "风险", "目标价", "支撑", "压力",
)

RANKING_WORDS = (
    "留几个", "留几只", "留三个", "留两只", "留哪", "留吗", "能留", "还能留",
    "保留", "留下", "去掉", "删掉", "剔除", "淘汰", "取舍", "排序", "排名",
    "排个序", "排一下", "优先级", "二选一", "三选二", "四选三", "哪个更好",
    "哪只更好", "更值得", "更强",
)

GREETING_TEXTS = {
    "你好", "您好", "hi", "hello", "hey", "在吗", "在不在", "你是谁", "你能做什么",
    "你会什么", "怎么用", "help", "帮助", "/start", "/help",
}

SHORT_CHAT_TEXTS = {
    "哈哈", "哈哈哈", "嗯", "嗯嗯", "好的", "好", "收到", "ok", "OK", "oK",
    "谢谢", "谢了", "明白", "了解", "可以", "行",
}


BOUNDARY_INTRO = """我是 A 股研究与模拟交易助手。

我能帮你做:
- A 股选股、单股分析、板块热度、龙虎榜、北向资金、政策方向
- 关注股跟踪、持仓处置框架、模拟交易员复盘
- 合伙账户分成记录与查询

边界:
- 不承诺收益，不构成投资建议
- 不实盘下单，不替你做最终买卖决定
- 不做无关长闲聊

你可以这样问:
/recommend 推荐几只多头均线向上的股票
京东方A怎么看
今天龙虎榜和板块热度如何
/status 查看合伙账户状态"""

SHORT_GUIDE = "收到。你可以直接问 A 股、市场、关注股、持仓处置、模拟交易员复盘，或发 /help 查看用法。"

OUT_OF_SCOPE = """这个问题超出我的工作范围。

我是 A 股研究与模拟交易助手，主要处理股票研究、市场数据、策略筛选、关注股、模拟交易员复盘和合伙账户记录。
我不做无关长闲聊，也不承诺收益或实盘下单。"""


def is_lightweight_action(action: str) -> bool:
    return action in LIGHTWEIGHT_ACTIONS


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _is_command(raw: str, lower: str) -> bool:
    if not raw.startswith("/"):
        return False
    command = lower.split(maxsplit=1)[0].split("@", 1)[0]
    return command in COMMAND_PREFIXES


def _has_stock_code(raw: str) -> bool:
    return bool(
        re.search(r"\b(?:sh|sz|bj)?[0368]\d{5}(?:\.(?:sh|sz|bj))?\b", raw, re.I)
        or re.search(r"\b(?:SH|SZ|BJ)\d{6}\b", raw)
    )


def _stock_mentions(raw: str) -> list[str]:
    try:
        from backend.telegram.stock_analysis import extract_stock_mentions

        return extract_stock_mentions(raw, max_results=8)
    except Exception:
        return []


def _looks_like_stock_question(raw: str) -> bool:
    if _has_stock_code(raw):
        return True
    if any(keyword in raw for keyword in STOCK_TASK_KEYWORDS):
        return True
    mentions = _stock_mentions(raw)
    if len(mentions) >= 2:
        return True
    if mentions and any(word in raw for word in ANALYSIS_WORDS + RANKING_WORDS):
        return True
    if any(word in raw for word in ANALYSIS_WORDS) and len(raw) <= 80:
        return True
    return False


def _looks_out_of_scope(raw: str) -> bool:
    if not raw:
        return False
    unrelated = (
        "写诗", "写一首", "讲故事", "翻译", "旅游", "菜谱", "做饭", "电影", "小说", "游戏攻略",
        "感情", "恋爱", "星座", "天气", "数学题", "代码怎么写", "论文",
    )
    return any(token in raw for token in unrelated)


def _parse_created_at(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_recent_stock_context(
    chat_id: str,
    thread_id: str,
    now: datetime | None = None,
) -> bool:
    if not chat_id:
        return False
    try:
        from backend.telegram.memory import get_recent_context

        recent = get_recent_context(chat_id, thread_id, limit=6)
    except Exception:
        return False
    latest_assistant = next(
        (item for item in reversed(recent) if item.get("role") == "assistant"),
        None,
    )
    if not latest_assistant:
        return False
    created_at = _parse_created_at(latest_assistant.get("created_at"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if not created_at or (current - created_at).total_seconds() > CONTEXT_FOLLOWUP_MINUTES * 60:
        return False
    intent = str(latest_assistant.get("intent") or "").strip()
    return intent in CONTEXTUAL_STOCK_INTENTS or _looks_like_stock_question(
        str(latest_assistant.get("content") or "")
    )


def preflight_route(
    text: str,
    chat_id: str = "",
    thread_id: str = "default",
    now: datetime | None = None,
) -> GateResult:
    raw = _clean(text)
    lower = raw.lower()
    compact = re.sub(r"[，。！？!?.、~～\s]+", "", raw)
    if not raw:
        return GateResult("boundary_intro", BOUNDARY_INTRO, "empty")
    if _is_command(raw, lower):
        return GateResult("command_route", reason="known_command")
    if compact in GREETING_TEXTS or lower in GREETING_TEXTS:
        return GateResult("boundary_intro", BOUNDARY_INTRO, "greeting")
    if compact in SHORT_CHAT_TEXTS or lower in SHORT_CHAT_TEXTS:
        if _has_recent_stock_context(chat_id, thread_id, now):
            return GateResult("agent_task", reason="contextual_short_reply")
        return GateResult("simple_reply", SHORT_GUIDE, "short_chat")
    if _looks_like_stock_question(raw):
        return GateResult("agent_task", reason="stock_or_market")
    if _looks_out_of_scope(raw) or len(raw) >= 18:
        return GateResult("out_of_scope", OUT_OF_SCOPE, "out_of_scope")
    return GateResult("simple_reply", SHORT_GUIDE, "default_short")
