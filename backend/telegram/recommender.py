"""Telegram conversational recommendation helpers."""

import json
import re
import time
from datetime import date, timedelta

from langchain.tools import tool
from langchain_openai import ChatOpenAI

from backend.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from backend.agents.react_loop import ReActLoop
from backend.db.repository import get_conn, get_positions, list_trades
from backend.llm.json_repair import extract_json_object as _extract_json_object_repaired
from backend.llm.strategy_parser import natural_language_select, parse_strategy_request
from backend.trading.calculator import calc_cumulative_return
from backend.telegram.profile import (
    add_watch,
    apply_inferred_preferences,
    format_profile,
    format_watchlist,
    get_profile,
    list_watch,
    parse_profile_set,
    remove_watch,
    set_daily_push,
    update_profile,
)
from backend.telegram.stock_analysis import (
    build_technical_snapshot,
    compare_stocks,
    extract_stock_codes,
    extract_stock_mentions,
    generate_stock_report,
    lookup_stock_name,
)
from backend.telegram.stock_interest import get_shared_stock_report, record_stock_interest
from backend.telegram.memory import (
    build_memory_prompt,
    delete_memory_item,
    forget_memories_by_keyword,
    format_memory_overview,
    profile_scope_id,
    record_message,
    update_memories_from_text,
    upsert_memory_item,
)
from backend.telegram.memory_distiller import maybe_schedule_memory_distillation
from backend.policy.reader import extract_policy_signals
from backend.macro.report import (
    collect_stock_fundamental_events,
    format_chip_distribution,
    format_macro_topic,
    get_macro_daily_report_text,
    refresh_macro_intelligence,
)
from backend.telegram.knowledge import (
    best_public_agent_context,
    compact_trace_text,
    trace_payload,
    update_recommend_skill_feedback,
)
from backend.telegram.evaluation import record_recommend_eval
from backend.data.loader import load_daily
from backend.telegram.message_gate import is_lightweight_action, preflight_route
from backend.telegram.partnership_account import (
    dispatch_partnership_command,
    is_partnership_account_message,
    partnership_daily_report,
    partnership_history,
    partnership_init_account,
    partnership_status,
)


RECOMMEND_DAILY_LIMIT = 20
RECOMMEND_COOLDOWN_SECONDS = 15


def _context_key(chat_id: str, user_id: str = "") -> str:
    return profile_scope_id(chat_id or "local", user_id or "")


def _memory_progress_payload(memory_context: dict) -> dict:
    session_summary = memory_context.get("session_summary") or {}
    memories = memory_context.get("long_term_memories") or memory_context.get("memories") or []
    recent_messages = memory_context.get("short_term_messages") or []
    preview = []
    for item in memories[:3]:
        content = item.get("content") if isinstance(item, dict) else item
        content = str(content or "").strip()
        if content:
            preview.append(content[:120])
    recent_preview = []
    for item in recent_messages[-4:]:
        role = item.get("role") if isinstance(item, dict) else ""
        content = item.get("content") if isinstance(item, dict) else item
        content = str(content or "").strip()
        if content:
            recent_preview.append(f"{role or 'message'}: {content[:90]}")
    return {
        "type": "memory_context",
        "short_count": len(recent_messages),
        "memory_count": len(memories),
        "has_session_summary": bool(session_summary.get("summary")),
        "session_summary": str(session_summary.get("summary") or "")[:160],
        "memory_preview": preview,
        "recent_preview": recent_preview,
    }


def _is_position_advice_query(text: str) -> bool:
    raw = text or ""
    position_terms = (
        "持仓", "重仓", "满仓", "半仓", "仓位", "均价", "成本", "成本价", "买入价",
        "亏损", "亏了", "浮亏", "盈利", "赚了", "浮盈", "回本",
    )
    action_terms = (
        "清仓", "割肉", "止损", "减仓", "加仓", "补仓", "卖出", "卖吗", "要卖",
        "怎么办", "怎么处理", "扛", "死扛", "一键",
    )
    has_position = any(token in raw for token in position_terms)
    has_action = any(token in raw for token in action_terms)
    return has_position and has_action


def _is_emotional_risk_help_query(text: str) -> bool:
    raw = text or ""
    if not raw:
        return False
    emotional_terms = (
        "想赚钱想疯", "给我点冷水", "冷水", "上头", "冲动", "翻本", "心态",
        "控制不住", "停不下来", "管不住手", "想一把梭", "梭哈",
    )
    help_terms = ("咋办", "怎么办", "建议", "劝", "骂醒", "冷静")
    return any(token in raw for token in emotional_terms) and any(token in raw for token in help_terms)


def _is_stock_selection_query(text: str) -> bool:
    raw = text or ""
    lower = raw.lower()
    if _is_position_advice_query(raw):
        return False
    return (
        lower.startswith("/recommend")
        or any(token in raw for token in ("推荐几", "推荐一", "选几支", "找几只", "筛选", "选股", "股票池"))
        or any(token in raw for token in ("龙头", "强势", "连板", "回踩5", "回踩10", "回踩20", "多头均线", "均线发散"))
    )


def _is_contextual_stock_followup(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or not extract_stock_mentions(raw):
        return False
    if any(token in raw for token in ("选几支", "找几只", "筛选", "股票池", "回测")):
        return False
    return (
        raw.startswith(("那", "那么", "这个", "这只", "那只", "再看", "再说", "再帮我看"))
        or raw.endswith(("呢", "吗", "？", "?"))
        or len(raw) <= 18
    )


def _guess_intent(text: str) -> str:
    raw = text or ""
    lower = raw.lower()
    if _is_position_advice_query(raw):
        return "position_advice"
    if _is_emotional_risk_help_query(raw):
        return "help"
    if lower.startswith("/memory") or raw.startswith("记忆"):
        return "memory"
    if any(token in raw for token in ("你是谁", "你叫什么", "你的名字")):
        return "identity"
    if any(token in raw for token in ("上次推荐", "刚才推荐", "刚刚推荐", "上次那只", "刚才那只", "那几个怎么样")):
        return "followup"
    if _is_contextual_stock_followup(raw):
        return "followup"
    if lower.startswith(("/backtest", "/bt")) or "回测" in raw:
        return "backtest"
    if lower.startswith(("/intraday", "/alert")) or any(token in raw for token in ("盘中提醒", "盘中检查", "盘中异动")):
        return "intraday"
    if is_partnership_account_message(raw):
        return "partnership_account"
    if lower.startswith(("/analyze", "analyze", "分析", "看看", "问问")) or any(
        token in raw for token in ("如何看", "怎么看", "点评", "评价", "分析下")
    ):
        return "analyze"
    if any(token in raw for token in ("政策偏好", "国家政策", "最近政策")):
        return "policy"
    if any(token in raw for token in ("龙虎榜", "涨停板", "跌停板", "炸板", "板块热度", "筹码峰", "筹码分布", "涨停质量", "板质量", "封板质量", "晋级率", "晋级淘汰", "北向资金", "沪深港通")):
        return "market_data"
    if any(token in raw for token in ("刷新最新", "手动刷新", "刷新政策", "刷新基本面", "刷新宏观")):
        return "refresh"
    if any(token in raw for token in ("多头均线", "均线发散", "多头排列", "均线向上")):
        return "ma_check" if extract_stock_mentions(raw) else "recommend"
    if lower.startswith("/compare") or raw.startswith(("对比", "比较")):
        return "compare"
    if lower.startswith("/profile") or raw.startswith("画像"):
        return "profile"
    if lower.startswith("/watch") or raw.startswith("关注"):
        return "watchlist"
    if lower.startswith("/recommend") or any(token in raw for token in ("推荐", "选几支", "找几只", "筛选")):
        return "recommend"
    return "chat"


def _fmt_money(value: float | int | None) -> str:
    return f"{float(value or 0):,.2f}"


def _fmt_pct(value: float | int | None) -> str:
    v = float(value or 0)
    return f"{'+' if v > 0 else ''}{v:.2f}%"


def _parse_int(text: str, default: int = 0) -> int:
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else default


def get_agent_performance(agent_id: int) -> dict:
    """Return live Agent performance summary for chat/tool use."""
    conn = get_conn()
    agent = conn.execute("SELECT * FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    if not agent:
        conn.close()
        return {"ok": False, "error": f"Agent #{agent_id} 不存在"}

    latest = conn.execute(
        """SELECT * FROM agent_daily_report
           WHERE agent_id=? ORDER BY trade_date DESC LIMIT 1""",
        (agent_id,),
    ).fetchone()
    reports = conn.execute(
        """SELECT trade_date, daily_return, cumulative_return, total_assets
           FROM agent_daily_report
           WHERE agent_id=? ORDER BY trade_date DESC LIMIT 20""",
        (agent_id,),
    ).fetchall()
    conn.close()

    positions = get_positions(agent_id)
    trades = list_trades(agent_id, 100)
    sell_trades = [t for t in trades if str(t.get("direction", "")).lower() == "sell"]
    winning_sells = [t for t in sell_trades if float(t.get("total_value") or 0) > 0]

    if latest:
        total_assets = latest["total_assets"]
        cumulative_return = latest["cumulative_return"]
        trade_date = latest["trade_date"]
    else:
        market_value = sum(float(p.get("market_value") or 0) for p in positions)
        total_assets = float(agent["current_cash"] or 0) + market_value
        cumulative_return = calc_cumulative_return(total_assets, agent["initial_capital"])
        trade_date = ""

    return {
        "ok": True,
        "agent": dict(agent),
        "trade_date": trade_date,
        "total_assets": total_assets,
        "cumulative_return": cumulative_return,
        "position_count": len(positions),
        "trade_count": len(trades),
        "win_rate": round(len(winning_sells) / len(sell_trades) * 100, 2) if sell_trades else 0,
        "recent_reports": [dict(r) for r in reports],
        "recent_trades": trades[:10],
        "positions": positions[:10],
    }


def format_agent_performance(agent_id: int) -> str:
    data = get_agent_performance(agent_id)
    if not data.get("ok"):
        return data.get("error", "查询失败")

    agent = data["agent"]
    lines = [
        f"{agent.get('display_name') or agent.get('name')} 战绩",
        f"日期: {data.get('trade_date') or '暂无日报'}",
        f"总资产: {_fmt_money(data['total_assets'])}",
        f"累计收益: {_fmt_pct(data['cumulative_return'])}",
        f"持仓: {data['position_count']} 只",
        f"成交: {data['trade_count']} 笔",
        f"卖出胜率: {_fmt_pct(data['win_rate'])}",
    ]
    if data["positions"]:
        lines.append("当前持仓:")
        for p in data["positions"][:5]:
            lines.append(
                f"- {p['ts_code']} {p.get('stock_name','')} "
                f"{p['quantity']}股 现价{float(p.get('current_price') or 0):.2f}"
            )
    return "\n".join(lines)


def get_simulation_performance(sim_id: int) -> dict:
    """Return simulation task performance summary for chat/tool use."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM simulation_task WHERE id=?", (sim_id,)).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "error": f"模拟任务 #{sim_id} 不存在"}

    data = dict(row)
    results = {}
    if data.get("results_json"):
        try:
            results = json.loads(data["results_json"])
        except json.JSONDecodeError:
            results = {}
    return {"ok": True, "task": data, "results": results}


def format_simulation_performance(sim_id: int) -> str:
    data = get_simulation_performance(sim_id)
    if not data.get("ok"):
        return data.get("error", "查询失败")
    task = data["task"]
    results = data.get("results") or {}
    lines = [
        f"模拟任务 #{task['id']} {task.get('name') or ''}",
        f"状态: {task.get('status')} 进度: {float(task.get('progress') or 0):.1f}%",
        f"区间: {task.get('start_date')} - {task.get('end_date')}",
    ]
    for agent in (results.get("agents") or [])[:5]:
        metrics = agent.get("metrics") or {}
        lines.append(
            f"- {agent.get('display_name')}: 收益 {_fmt_pct(metrics.get('total_return'))}, "
            f"回撤 {_fmt_pct(metrics.get('max_drawdown'))}, "
            f"胜率 {_fmt_pct(metrics.get('win_rate'))}, "
            f"交易 {metrics.get('total_trades', 0)} 笔"
        )
    if not results.get("agents"):
        lines.append("暂无可展示结果")
    return "\n".join(lines)


def _backtest_dates(period: str) -> tuple[str, str]:
    from backend.data.loader import load_index_daily
    idx = load_index_daily()
    today = date.today()
    latest = today.strftime("%Y%m%d")
    if idx is not None and not idx.empty:
        latest = str(idx.iloc[-1]["trade_date"])
        today = date(int(latest[:4]), int(latest[4:6]), int(latest[6:8]))
    p = (period or "1m").lower()
    if p in ("3d", "三天"):
        start_dt = today - timedelta(days=5)
    elif p in ("1w", "一周", "一星期"):
        start_dt = today - timedelta(days=10)
    elif p in ("1q", "3m", "一季度", "季度"):
        start_dt = today - timedelta(days=95)
    elif p in ("ytd", "今年", "今年以来"):
        start_dt = date(today.year, 1, 1)
    else:
        start_dt = today - timedelta(days=35)
    return start_dt.strftime("%Y%m%d"), latest


def _format_backtest_reply(raw: str) -> str:
    from backend.backtest.engine import run_backtest
    from backend.strategies.registry import StrategyRegistry

    lowered = (raw or "").lower()
    period = "1m"
    for token in ("3d", "1w", "1m", "1q", "3m", "ytd"):
        if token in lowered:
            period = token
            break
    if "三天" in raw:
        period = "3d"
    elif "一周" in raw or "一星期" in raw:
        period = "1w"
    elif "季度" in raw:
        period = "1q"
    elif "今年" in raw:
        period = "ytd"

    strategy_name = ""
    for name in sorted(StrategyRegistry.list_all(), key=len, reverse=True):
        if name.lower() in lowered:
            strategy_name = name
            break
    if not strategy_name:
        parsed = parse_strategy_request(raw)
        strategy_name = parsed.get("strategy") or "ma_bullish_pullback"
    start, end = _backtest_dates(period)
    result = run_backtest(strategy_name, {}, start, end)
    if result.get("error"):
        return f"回测失败: {result['error']}\n用法: /backtest ma_bullish_pullback 1m"
    metrics = result.get("metrics") or {}
    trades = result.get("trades") or []
    max_drawdown = abs(float(metrics.get("max_drawdown") or 0))
    lines = [
        f"回测结果: {strategy_name}",
        f"区间: {start} - {end} ({period})",
        f"- 总收益: {_fmt_pct(metrics.get('total_return'))}",
        f"- 最大回撤: {max_drawdown:.2f}%",
        f"- 胜率: {_fmt_pct(metrics.get('win_rate'))}",
        f"- 交易次数: {metrics.get('total_trades', len(trades))}",
        f"- 最终资产: {_fmt_money(metrics.get('final_assets'))}",
    ]
    if trades:
        lines.append("最近成交:")
        for item in trades[-5:]:
            lines.append(
                f"- {item.get('date') or item.get('trade_date','')} "
                f"{item.get('action') or item.get('direction','')} {item.get('ts_code','')} "
                f"@{float(item.get('price') or 0):.2f}"
            )
    lines.append("仅供研究，不构成投资建议。")
    return "\n".join(lines)


def _format_intraday_reply(chat_id: str, raw: str = "") -> str:
    from backend.telegram.profile import list_watch
    from backend.telegram.stock_analysis import watchlist_alerts

    lines = ["盘中检查"]
    watch = list_watch(chat_id or "local")
    lines.append(watchlist_alerts(chat_id or "local", watch))
    try:
        lines.append("")
        lines.append("板块热度:")
        lines.append(format_macro_topic("sector")[:1800])
    except Exception as exc:
        lines.append(f"板块热度读取失败: {exc}")
    conn = get_conn()
    pending = conn.execute(
        """SELECT a.display_name, o.ts_code, o.stock_name, o.direction, o.order_type, o.price, o.trigger_price, o.status
           FROM agent_order o
           JOIN agent_info a ON a.id=o.agent_id
           WHERE o.status IN ('pending','triggered')
           ORDER BY o.trade_date DESC, o.id DESC
           LIMIT 8"""
    ).fetchall()
    conn.close()
    lines.append("")
    lines.append("待触发条件/预操作单:")
    if not pending:
        lines.append("- 暂无")
    for row in pending:
        r = dict(row)
        direction = "买入" if r.get("direction") == "buy" else "卖出"
        trigger = f" 触发{float(r.get('trigger_price') or 0):.2f}" if r.get("trigger_price") else ""
        lines.append(
            f"- {r.get('display_name')} {direction} {r.get('ts_code')} {r.get('stock_name') or ''} "
            f"{r.get('order_type')} @{float(r.get('price') or 0):.2f}{trigger}"
        )
    lines.append("仅供研究，不构成投资建议。")
    return "\n".join(lines)


def set_intraday_push(chat_id: str, enabled: bool) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT INTO system_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
        (f"telegram_intraday_enabled:{chat_id or 'local'}", "1" if enabled else "0"),
    )
    conn.commit()
    conn.close()


def list_intraday_push_chats() -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT key FROM system_settings
           WHERE key LIKE 'telegram_intraday_enabled:%' AND value IN ('1','true','on','yes')"""
    ).fetchall()
    conn.close()
    return [str(r["key"]).split(":", 1)[1] for r in rows if str(r["key"]).split(":", 1)[1]]


def build_intraday_push_message(chat_id: str) -> str:
    return _format_intraday_reply(chat_id or "local", "/intraday")


def _profile_prompt_suffix(profile: dict | None) -> str:
    if not profile:
        return ""
    parts = []
    if profile.get("risk_level"):
        parts.append(f"风险偏好{profile['risk_level']}")
    if profile.get("horizon"):
        parts.append(f"持股周期{profile['horizon']}")
    if profile.get("preferred_sectors"):
        parts.append("偏好板块:" + ",".join(profile["preferred_sectors"]))
    if profile.get("excluded_sectors"):
        parts.append("排除板块:" + ",".join(profile["excluded_sectors"]))
    return "。用户偏好：" + "；".join(parts) if parts else ""


def _recommend_price(ts_code: str) -> float:
    df = load_daily(ts_code)
    if df is None or df.empty:
        return 0.0
    return float(df.iloc[-1].get("close") or 0)


def _record_interest_quietly(
    chat_id: str,
    username: str,
    ts_code: str,
    query: str,
    intent: str,
    profile: dict | None = None,
    user_id: str = "",
    thread_id: str = "default",
) -> None:
    try:
        record_stock_interest(chat_id or "local", username or "", ts_code, query, intent, profile or {})
        name = lookup_stock_name(ts_code)
        scope_id = _context_key(chat_id or "local", user_id)
        upsert_memory_item(
            "user",
            scope_id,
            "stock_interest",
            f"{ts_code} {name}: 用户提及/关注，意图={intent}，上下文={query[:220]}",
            0.82,
        )
    except Exception:
        pass


def _record_recommendation(chat_id: str, username: str, query: str, rows: list[dict], strategy: str, context: dict | None = None) -> list[int]:
    if not chat_id:
        return []
    conn = get_conn()
    ids = []
    for item in rows:
        trace = trace_payload(context or {}, item, strategy)
        cur = conn.execute(
            """INSERT INTO telegram_recommend_feedback
               (chat_id, username, query, ts_code, stock_name, agent_id, source_agent_name,
                source_section, source_summary, skill_id, skill_confidence, recommend_price,
                trace_json, recommendation_json, feedback_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'recommended')""",
            (
                chat_id,
                username,
                query,
                item.get("ts_code", ""),
                item.get("name", ""),
                trace.get("source_agent_id"),
                trace.get("source_agent_name", ""),
                trace.get("source_section", ""),
                trace.get("source_summary", ""),
                trace.get("skill_id") or strategy or "",
                float(trace.get("skill_confidence") or float(item.get("score") or 0) / 100.0),
                _recommend_price(item.get("ts_code", "")),
                json.dumps(trace, ensure_ascii=False, default=str),
                json.dumps(item, ensure_ascii=False, default=str),
            ),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    for item in rows[:5]:
        if item.get("ts_code"):
            _record_interest_quietly(chat_id, username, item.get("ts_code", ""), query, f"recommend:{strategy}")
    return ids


def _recent_recommended_stocks(chat_id: str, limit: int = 5) -> list[dict]:
    if not chat_id:
        return []
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, query, ts_code, stock_name, recommend_price, recommendation_json, created_at
           FROM telegram_recommend_feedback
           WHERE chat_id=? AND feedback_type='recommended'
           ORDER BY created_at DESC, id DESC
           LIMIT ?""",
        (chat_id, max(1, min(int(limit or 5), 20))),
    ).fetchall()
    conn.close()
    results = []
    seen = set()
    for row in rows:
        item = dict(row)
        code = item.get("ts_code") or ""
        if not code or code in seen:
            continue
        seen.add(code)
        try:
            rec = json.loads(item.get("recommendation_json") or "{}")
        except Exception:
            rec = {}
        results.append({
            "id": item.get("id"),
            "query": item.get("query") or "",
            "ts_code": code,
            "name": item.get("stock_name") or rec.get("name") or lookup_stock_name(code),
            "recommend_price": item.get("recommend_price"),
            "reason": rec.get("reason") or "",
            "created_at": item.get("created_at") or "",
        })
    return results


def _is_recommend_followup(raw: str) -> bool:
    text = raw or ""
    return any(token in text for token in (
        "上次推荐", "刚才推荐", "刚刚推荐", "前面推荐", "上回推荐",
        "上次那只", "刚才那只", "刚刚那只", "那只怎么样", "那几个怎么样",
        "继续看", "继续分析", "还有机会吗", "还能买吗", "现在怎么样",
    ))


def _format_recommend_followup(raw: str, chat_id: str, username: str, profile: dict | None = None,
                               user_id: str = "", thread_id: str = "default") -> str:
    explicit = extract_stock_mentions(raw)
    recent = _recent_recommended_stocks(chat_id or "local", 5)
    targets = explicit or [item["ts_code"] for item in recent[:3]]
    if not targets:
        memory_context = build_memory_prompt(chat_id or "local", user_id, thread_id, raw, None, 8)
        prompt = memory_context.get("prompt") or ""
        hinted = extract_stock_mentions(prompt)
        targets = hinted[:3]
    if not targets:
        return "我没找到最近一次推荐的标的。可以直接发股票名或代码，例如：京东方A现在怎么样。"
    lines = ["基于最近推荐/当前追问做延续分析"]
    if recent:
        last = recent[0]
        lines.append(f"最近推荐记录: {last['ts_code']} {last.get('name','')}，来自“{last.get('query','')[:40]}”。")
    for code in targets[:3]:
        lines.append("")
        lines.append(generate_stock_report(code, profile))
        _record_interest_quietly(chat_id or "local", username, code, raw, "recommend_followup", profile, user_id, thread_id)
    lines.append("仅供研究，不构成投资建议。")
    return "\n\n---\n\n".join(lines)


def _infer_previous_stock_task(memory_context: dict) -> str:
    text = " ".join(str((m or {}).get("content") or "") for m in (memory_context.get("short_term_messages") or [])[-8:])
    if any(token in text for token in ("买卖", "能不能买", "要不要卖", "是否推荐", "清仓", "减仓", "止损")):
        return "买卖处置/是否推荐"
    if any(token in text for token in ("对比", "比较")):
        return "对比分析"
    if any(token in text for token in ("多头均线", "均线发散", "回踩")):
        return "均线结构检查"
    return "单股延续分析"


def _format_contextual_stock_followup(raw: str, chat_id: str, username: str, profile: dict | None = None,
                                      user_id: str = "", thread_id: str = "default",
                                      progress_callback=None) -> str:
    memory_context = build_memory_prompt(chat_id or "local", user_id, thread_id, raw, None, 8)
    if progress_callback:
        progress_callback(_memory_progress_payload(memory_context))
    codes = extract_stock_mentions(raw)
    if not codes:
        return _format_recommend_followup(raw, chat_id, username, profile, user_id, thread_id)
    task = _infer_previous_stock_task(memory_context)
    if progress_callback:
        progress_callback({
            "type": "phase",
            "message": f"识别为省略式追问，沿用上轮任务类型: {task}。",
        })
        progress_callback({
            "type": "tool_start",
            "tool": "recommend_analyze_stock",
            "description": "沿用最近对话语境，对新提到的股票做同类分析。",
            "args": {"codes": codes[:3], "context_task": task},
        })
    reports = []
    for code in codes[:3]:
        reports.append(generate_stock_report(code, profile))
        _record_interest_quietly(chat_id or "local", username, code, raw, "contextual_stock_followup", profile, user_id, thread_id)
    if progress_callback:
        progress_callback({
            "type": "tool",
            "tool": "recommend_analyze_stock",
            "description": "沿用最近对话语境，对新提到的股票做同类分析。",
            "args": {"count": len(reports)},
            "result_preview": f"已生成{len(reports)}只股票的{task}报告。",
        })
    return "\n\n".join([
        f"我按上一轮“{task}”的语境，继续看你新提到的股票。",
        "\n\n---\n\n".join(reports),
        "仅供研究，不构成投资建议。",
    ])


def _extract_position_context(raw: str) -> dict:
    text = raw or ""

    def first_number(patterns: tuple[str, ...]) -> float:
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                try:
                    return float(m.group(1))
                except Exception:
                    continue
        return 0.0

    quantity = first_number((
        r"(\d+(?:\.\d+)?)\s*股",
        r"(\d+(?:\.\d+)?)\s*手",
    ))
    if re.search(r"\d+(?:\.\d+)?\s*手", text) and not re.search(r"\d+(?:\.\d+)?\s*股", text):
        quantity *= 100
    avg_cost = first_number((
        r"(?:均价|成本价|成本|买入价)[^\d]{0,8}(\d+(?:\.\d+)?)",
    ))
    current_loss = first_number((
        r"(?:亏损|亏了|浮亏|亏)[^\d]{0,8}(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)[^\d]{0,4}(?:亏损|亏了|浮亏)",
    ))
    prior_profit = first_number((
        r"(?:之前|此前|前面|原来)?[^\d]{0,6}(?:盈利|赚了|赚|利润|收益)[^\d]{0,8}(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)[^\d]{0,6}(?:的)?(?:盈利|利润|收益)",
    ))
    return {
        "quantity": quantity,
        "avg_cost": avg_cost,
        "current_loss": current_loss,
        "prior_profit": prior_profit,
    }


def _resolve_context_stock(raw: str, chat_id: str, user_id: str = "", thread_id: str = "default") -> str:
    explicit = extract_stock_mentions(raw)
    if explicit:
        return explicit[0]
    memory_context = build_memory_prompt(chat_id or "local", user_id, thread_id, raw, None, 8)
    prompt = memory_context.get("prompt") or ""
    hinted = extract_stock_mentions(prompt)
    return hinted[0] if hinted else ""


def _format_position_advice(raw: str, chat_id: str, username: str, profile: dict | None = None,
                            user_id: str = "", thread_id: str = "default", progress_callback=None) -> str:
    memory_context = build_memory_prompt(chat_id or "local", user_id, thread_id, raw, None, 8)
    if progress_callback:
        progress_callback(_memory_progress_payload(memory_context))
    code = _resolve_context_stock(raw, chat_id or "local", user_id, thread_id)
    if not code:
        return "我能做持仓处置分析，但这句没有识别到股票。你可以补一句：利通电子 700股 均价220，要不要减仓/清仓。"

    if progress_callback:
        progress_callback({
            "type": "tool_start",
            "tool": "recommend_analyze_stock",
            "description": "结合最近上下文、个股行情和用户持仓成本做风险处置分析。",
            "args": {"ts_code": code},
        })
    tech = build_technical_snapshot(code)
    if progress_callback:
        progress_callback({
            "type": "tool",
            "tool": "recommend_analyze_stock",
            "description": "结合最近上下文、个股行情和用户持仓成本做风险处置分析。",
            "args": {"ts_code": code},
            "result_preview": (
                f"收盘{tech.get('close')}，当日{tech.get('pct_chg')}%，"
                f"结构: {tech.get('summary') or '-'}"
            ) if tech.get("ok") else "",
            "error": "" if tech.get("ok") else tech.get("error", "行情读取失败"),
        })
    if not tech.get("ok"):
        return tech.get("error", f"{code} 行情读取失败")

    ctx = _extract_position_context(raw)
    name = lookup_stock_name(code)
    close = float(tech.get("close") or 0)
    ma = tech.get("ma") or {}
    qty = float(ctx.get("quantity") or 0)
    avg_cost = float(ctx.get("avg_cost") or 0)
    user_loss = float(ctx.get("current_loss") or 0)
    prior_profit = float(ctx.get("prior_profit") or 0)
    estimated_loss = (avg_cost - close) * qty if qty > 0 and avg_cost > 0 and close > 0 else 0.0
    loss_pct = (close - avg_cost) / avg_cost * 100 if avg_cost > 0 and close > 0 else 0.0
    net_profit = prior_profit - (user_loss or max(0.0, estimated_loss)) if prior_profit else 0.0

    bearish = close < float(ma.get("ma5") or 0) and close < float(ma.get("ma10") or 0) and close < float(ma.get("ma20") or 0)
    severe_drop = float(tech.get("pct_chg") or 0) <= -7
    concentration_hint = "你用了“重仓”描述，先按集中度风险偏高处理。"
    if qty and avg_cost:
        concentration_hint = f"持仓 {qty:.0f} 股，均价 {avg_cost:.2f}，按收盘 {close:.2f} 估算浮亏约 {max(0.0, estimated_loss):,.0f} 元（{loss_pct:.1f}%）。"
    if user_loss and estimated_loss and abs(user_loss - estimated_loss) > max(2000, user_loss * 0.15):
        concentration_hint += f" 你填报亏损 {user_loss:,.0f} 元，与收盘估算有差异，以券商实际为准。"

    conclusion = "倾向先降风险，而不是继续重仓裸扛。"
    if bearish or severe_drop:
        conclusion = "倾向先执行降仓/止损纪律；如果仓位已经影响心态，一键清仓是合理选项之一。"
    if prior_profit:
        conclusion += " 之前在这只票赚过钱不能作为继续扛单的理由，应把当前仓位当成一笔新的风险暴露。"

    lines = [
        f"{code} {name} 持仓处置分析",
        f"数据截至: {tech.get('trade_date')}  收盘: {close:.2f}  当日: {_fmt_pct(tech.get('pct_chg'))}",
        "",
        "一、你的持仓状态",
        f"- {concentration_hint}",
    ]
    if prior_profit:
        lines.append(f"- 你提到此前盈利约 {prior_profit:,.0f} 元；扣除当前亏损后，历史合计仍约 {net_profit:,.0f} 元。这个数字只能帮助你看总账，不能替代当前风控。")
    lines.extend([
        "",
        "二、当前风险信号",
        f"- MA5/10/20/60: {float(ma.get('ma5') or 0):.2f}/{float(ma.get('ma10') or 0):.2f}/{float(ma.get('ma20') or 0):.2f}/{float(ma.get('ma60') or 0):.2f}",
        f"- 技术结构: {tech.get('summary') or '无摘要'}",
        f"- 20日表现 {_fmt_pct(tech.get('pct_20'))}，60日表现 {_fmt_pct(tech.get('pct_60'))}",
        "",
        "三、可执行框架",
        f"- 结论倾向: {conclusion}",
        "- 如果你无法接受明天继续大跌或再跌停，优先把仓位降到自己能承受的水平；不要等情绪崩溃时再被动卖。",
        "- 如果还想保留反弹机会，更稳的做法是先减掉 1/2 或 2/3，剩余仓位设硬止损；反抽到成本密集区再处理，而不是满仓赌反弹。",
        "- 如果你的仓位超过总资产 50%，这已经不是单股观点问题，而是组合风险问题，先降集中度通常比判断明天涨跌更重要。",
        "",
        "四、我不会替你点击按钮",
        "- 但从风控角度看：当前问题的核心不是“这票会不会反弹”，而是“继续重仓错一次还能不能承受”。",
        "- 仅供研究和风控框架参考，不构成投资建议；最终买卖由你自己决定。",
    ])
    _record_interest_quietly(chat_id or "local", username, code, raw, "position_advice", profile, user_id, thread_id)
    return "\n".join(lines)


def _ma_bullish_snapshot(ts_code: str) -> dict:
    from backend.trading.rules import normalize_ts_code

    code = normalize_ts_code(ts_code)
    df = load_daily(code)
    if df is None or len(df) < 35:
        return {"ok": False, "ts_code": code, "error": "行情数据不足"}
    data = df.sort_values("trade_date").copy()
    for p in (5, 10, 20, 30):
        data[f"ma{p}"] = data["close"].rolling(window=p, min_periods=p).mean()
    latest = data.iloc[-1]
    prev = data.iloc[-6] if len(data) >= 6 else data.iloc[0]
    ma = {p: float(latest.get(f"ma{p}") or 0) for p in (5, 10, 20, 30)}
    prev_ma = {p: float(prev.get(f"ma{p}") or 0) for p in (5, 10, 20, 30)}
    close = float(latest.get("close") or 0)
    order_ok = close > ma[5] > ma[10] > ma[20] > ma[30]
    slope_ok = all(ma[p] > prev_ma[p] for p in (5, 10, 20, 30))
    spread = (ma[5] - ma[30]) / ma[30] * 100 if ma[30] else 0.0
    verdict = "是，多头发散较清晰" if order_ok and slope_ok and spread >= 1.0 else "不是标准多头发散"
    return {
        "ok": True,
        "ts_code": code,
        "name": lookup_stock_name(code),
        "trade_date": str(latest.get("trade_date")),
        "close": round(close, 2),
        "ma": {f"ma{p}": round(ma[p], 2) for p in (5, 10, 20, 30)},
        "order_ok": order_ok,
        "slope_ok": slope_ok,
        "spread_pct": round(spread, 2),
        "verdict": verdict,
    }


def _format_ma_bullish_check(codes: list[str], chat_id: str, username: str, query: str, profile: dict | None = None) -> str:
    if not codes:
        return "没识别到具体股票。可以发: 京东方A是多头均线发散吗"
    lines = ["多头均线发散检查"]
    for code in codes[:5]:
        item = _ma_bullish_snapshot(code)
        if not item.get("ok"):
            lines.append(f"- {item.get('ts_code', code)}: {item.get('error')}")
            continue
        _record_interest_quietly(chat_id, username, item["ts_code"], query, "ma_bullish_check", profile)
        ma = item["ma"]
        lines.append(
            f"- {item['ts_code']} {item['name']}: {item['verdict']}。"
            f"收盘{item['close']:.2f}，MA5/10/20/30="
            f"{ma['ma5']:.2f}/{ma['ma10']:.2f}/{ma['ma20']:.2f}/{ma['ma30']:.2f}，"
            f"发散{item['spread_pct']:.2f}%，排列={'是' if item['order_ok'] else '否'}，斜率={'是' if item['slope_ok'] else '否'}。"
        )
    lines.append("仅供研究，不构成投资建议。")
    return "\n".join(lines)


def _format_price_volume_trend(ts_code: str, days: int = 10, chat_id: str = "", username: str = "", query: str = "", profile: dict | None = None) -> str:
    from backend.trading.rules import normalize_ts_code

    code = normalize_ts_code(ts_code)
    df = load_daily(code)
    if df is None or df.empty:
        return f"未找到 {code} 的行情数据。"
    data = df.sort_values("trade_date").tail(max(2, min(int(days or 10), 30))).copy()
    close0 = float(data.iloc[0].get("close") or 0)
    close1 = float(data.iloc[-1].get("close") or 0)
    total_pct = (close1 - close0) / close0 * 100 if close0 else 0.0
    vol = data["vol"].astype(float)
    vol_change = (float(vol.tail(3).mean()) - float(vol.head(3).mean())) / (float(vol.head(3).mean()) or 1) * 100
    up_days = int((data["pct_chg"].astype(float) > 0).sum())
    down_days = int((data["pct_chg"].astype(float) < 0).sum())
    latest = data.iloc[-1]
    _record_interest_quietly(chat_id, username, code, query or f"{code} 量价趋势", "price_volume_trend", profile)
    direction = "价升量增" if total_pct > 0 and vol_change > 10 else ("价升量缩" if total_pct > 0 else ("价跌量增" if vol_change > 10 else "震荡/缩量"))
    lines = [
        f"{code} {lookup_stock_name(code)} 最近{len(data)}日量价趋势",
        f"- 区间涨跌: {_fmt_pct(total_pct)}，上涨{up_days}天，下跌{down_days}天。",
        f"- 最新收盘: {close1:.2f}，当日涨跌 {_fmt_pct(float(latest.get('pct_chg') or 0))}。",
        f"- 近3日均量相对前3日变化: {_fmt_pct(vol_change)}，状态: {direction}。",
        "- 最近交易日:",
    ]
    for _, row in data.tail(5).iterrows():
        lines.append(
            f"  {row['trade_date']} 收{float(row['close']):.2f} "
            f"涨跌{_fmt_pct(float(row.get('pct_chg') or 0))} 量{float(row.get('vol') or 0):.0f}"
        )
    lines.append("仅供研究，不构成投资建议。")
    return "\n".join(lines)


def _format_policy_preference() -> str:
    signals = extract_policy_signals()
    lines = ["近期国家政策偏好"]
    if signals.get("summary"):
        lines.append(signals["summary"])
    top = signals.get("top_industries") or []
    if top:
        lines.append("高频方向:")
        for item in top[:8]:
            lines.append(f"- {item.get('industry')}: 强度{float(item.get('strength') or 0):.2f}，出现{item.get('count', 0)}次")
    else:
        lines.append("本地政策缓存暂未提取到明确产业方向。")
    lines.append("仅供研究，不构成投资建议。")
    return "\n".join(lines)


def _format_stock_fundamental_events(ts_code: str) -> str:
    data, status = collect_stock_fundamental_events(ts_code)
    code = data.get("ts_code") or ts_code
    events = data.get("events") or []
    lines = [f"{code} 最新业绩预告/快报"]
    if not events:
        err = next((s.get("error") for s in status if not s.get("ok")), "")
        lines.append(f"暂未读取到近期开披露事件。{err}".strip())
        return "\n".join(lines)
    for item in events[-8:]:
        row = item.get("data") or {}
        if item.get("type") == "forecast":
            lines.append(
                f"- 业绩预告 {row.get('profitForcastExpPubDate')}: "
                f"{row.get('profitForcastType') or ''} {row.get('profitForcastAbstract') or ''}"
            )
        else:
            lines.append(
                f"- 业绩快报 {row.get('performanceExpPubDate')}: "
                f"EPS增速{row.get('performanceExpressEPSChgPct') or '-'} "
                f"营收同比{row.get('performanceExpressGRYOY') or '-'} "
                f"营业利润同比{row.get('performanceExpressOPYOY') or '-'}"
            )
    return "\n".join(lines)


def _format_refresh_reply(raw: str) -> str:
    refresh_policy = any(token in raw for token in ("政策", "宏观", "全部", "最新")) or "政策" not in raw
    result = refresh_macro_intelligence("", refresh_policy=refresh_policy, force=True)
    report = result.get("report") or {}
    lines = [
        "已触发手动刷新。",
        f"- 宏观报告: {report.get('trade_date') or result.get('trade_date')} {report.get('status') or ('ok' if result.get('ok') else 'failed')}",
    ]
    policy = result.get("policy_refresh")
    if policy is not None:
        lines.append(f"- 政策缓存: {'成功' if policy.get('ok') else '失败'} {policy.get('count', policy.get('error', ''))}")
    lines.append("后续推荐助手和交易员会优先读取新的宏观报告。")
    return "\n".join(lines)


def _render_test_reply() -> str:
    return """# Telegram 渲染测试

**这条消息包含 Markdown 表格，但会被自动转换成手机友好的列表块。**

| 板块 | 温度 | 涨停 | 大涨 | 风险 |
| --- | ---: | ---: | ---: | --- |
| 白酒 | 68.84 | 1 | 2 | 低 |
| 医药 | 53.48 | 4 | 7 | 中 |
| 半导体 | -61.57 | 0 | 1 | 高 |

- 如果你看到的是加粗标题和逐行字段，说明 Telegram 原生 HTML 渲染正常。
- 如果你看到原始 `| --- |` 表格，说明发送链路没有走新的渲染器。
"""


def _identity_reply(chat_id: str, user_id: str = "", thread_id: str = "default") -> str:
    from backend.evolution.memory import read_telegram_memory

    memory = read_telegram_memory()
    profile_key = _context_key(chat_id or "local", user_id)
    profile = get_profile(profile_key)
    scoped_memory = build_memory_prompt(chat_id or "local", user_id, thread_id, "你是谁", None, 4)
    memory_line = (scoped_memory.get("prompt") or "").splitlines()[:2]
    return "\n".join([
        "我是这个项目里的 Telegram 股票推荐助手。",
        "我的职责是把交易员 Agent 的体系、A股行情工具、用户画像和推荐后验评估串起来，回答选股、单股分析、政策偏好、关注股跟踪等问题。",
        f"我记住的推荐偏好: {(memory or '暂无').splitlines()[0][:120]}",
        f"当前会话记忆: {' / '.join(memory_line) if memory_line else '暂无'}",
        f"你的当前画像: 风险{profile.get('risk_level', '中等')}，周期{profile.get('horizon', '短线')}。",
    ])


def _format_emotional_risk_help(raw: str, profile: dict | None = None) -> str:
    risk_level = (profile or {}).get("risk_level", "中等")
    horizon = (profile or {}).get("horizon", "短线")
    return "\n".join([
        "先停手。你现在这句“想赚钱想疯了”本身就是高风险信号，今晚不适合做任何追单决定。",
        "",
        "给你几盆冷水:",
        "1. 想快速赚钱时，最容易把交易做成情绪消费。",
        "2. 想翻本时，大脑会自动忽略止损、仓位和胜率，只盯着回本速度。",
        "3. 市场不会因为你急就给机会，越急越容易买在一致性高点。",
        "",
        "今晚执行规则:",
        "- 不新增买入计划，不临时加自选股，不因为一条消息或一个概念追进去。",
        "- 先写清楚明天最多亏多少钱、单票最多几成仓、跌破哪里必须退出。",
        "- 如果没有买点、止损点、仓位上限这三项，就视为没有交易计划。",
        "",
        f"按你当前画像: 风险{risk_level}，周期{horizon}。这种状态下先把仓位和规则写下来，比找下一只票更重要。",
        "",
        "仅供交易纪律参考，不构成投资建议。",
    ])


def _detect_text_feedback_type(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if _is_emotional_risk_help_query(raw):
        return ""

    recommendation_context_terms = (
        "按你推荐", "按你说", "按你建议", "你推荐", "你说的", "你刚才", "刚才推荐",
        "刚刚推荐", "上次推荐", "上次那只", "推荐的", "采纳", "没采纳", "听你的",
        "照你说", "照你建议",
    )
    has_context = any(token in raw for token in recommendation_context_terms)
    if not has_context:
        return ""

    if any(token in raw for token in ("太激进", "太冒险", "恐高")):
        return "risk_too_high"
    if any(token in raw for token in ("太保守", "机会少")) or "conservative" in lowered:
        return "risk_too_low"
    if any(token in raw for token in ("跌了", "亏了", "不好", "失败", "不喜欢", "没用")):
        return "negative"
    if any(token in raw for token in ("涨了", "赚了", "赚钱了", "不错", "采纳")):
        return "positive"
    return ""


def _record_text_feedback(chat_id: str, username: str, text: str) -> bool:
    if not chat_id:
        return False
    feedback_type = _detect_text_feedback_type(text)
    if not feedback_type:
        return False
    conn = get_conn()
    row = conn.execute(
        """SELECT id FROM telegram_recommend_feedback
           WHERE chat_id=? ORDER BY created_at DESC, id DESC LIMIT 1""",
        (chat_id,),
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE telegram_recommend_feedback
               SET feedback_type=?, feedback_text=?, username=?, updated_at=datetime('now')
               WHERE id=?""",
            (feedback_type, text, username, row["id"]),
        )
        update_recommend_skill_feedback(feedback_type, conn)
    conn.commit()
    conn.close()
    return bool(row)


def format_recommendation(query: str, max_results: int | None = None,
                          profile: dict | None = None, chat_id: str = "",
                          username: str = "", record_eval_enabled: bool = True,
                          progress_callback=None) -> str:
    enriched_query = query + _profile_prompt_suffix(profile)
    parsed = parse_strategy_request(enriched_query)
    if progress_callback:
        progress_callback({
            "type": "strategy_parse",
            "strategy": parsed.get("strategy") or "custom",
            "explanation": parsed.get("explanation", ""),
        })
    requested = int(max_results or parsed.get("max_results") or (profile or {}).get("max_results") or 5)
    requested = max(1, min(requested, 10))
    if progress_callback:
        progress_callback({
            "type": "tool_start",
            "tool": "recommend_search_stocks",
            "description": "按自然语言策略筛选候选股票。",
            "args": {"strategy": parsed.get("strategy"), "max_results": requested},
        })
    result = natural_language_select(enriched_query, requested)
    if progress_callback:
        progress_callback({
            "type": "tool",
            "tool": "recommend_search_stocks",
            "description": "按自然语言策略筛选候选股票。",
            "args": {"strategy": result.get("strategy"), "total": result.get("total")},
            "result_preview": f"筛到{len(result.get('results') or [])}只候选，策略={result.get('strategy') or parsed.get('strategy') or 'custom'}。",
        })
    rows = result.get("results") or []
    public_context = best_public_agent_context()
    if progress_callback:
        progress_callback({
            "type": "tool",
            "tool": "recommend_get_trader_memory",
            "description": "读取交易员体系、赛马表现和推荐技能记忆。",
            "args": {},
            "result_preview": compact_trace_text(public_context),
        })

    if not rows:
        return (
            "没有筛到匹配标的。\n"
            f"解析: {result.get('explanation') or parsed.get('explanation') or '无'}"
        )

    lines = [
        f"选股结果: {result.get('strategy') or parsed.get('strategy')}",
        f"解析: {result.get('explanation') or parsed.get('explanation') or '已按策略筛选'}",
        "实战逻辑: " + compact_trace_text(public_context),
    ]
    for idx, item in enumerate(rows[:requested], 1):
        lines.append(
            f"{idx}. {item['ts_code']} {item.get('name','')} "
            f"评分{float(item.get('score') or 0):.1f}"
        )
        reason = str(item.get("reason") or "")
        if reason:
            lines.append(f"   {reason[:90]}")
    if profile:
        pref = []
        if profile.get("risk_level"):
            pref.append(f"风险{profile['risk_level']}")
        if profile.get("horizon"):
            pref.append(profile["horizon"])
        if profile.get("preferred_sectors"):
            pref.append("偏好" + ",".join(profile["preferred_sectors"][:4]))
        if pref:
            lines.append("已结合用户画像: " + " / ".join(pref))
    recommendation_ids = _record_recommendation(
        chat_id,
        username,
        query,
        rows[:requested],
        str(result.get("strategy") or parsed.get("strategy") or ""),
        public_context,
    )
    lines.append("仅供研究，不构成投资建议。")
    reply = "\n".join(lines)
    if chat_id and record_eval_enabled:
        record_recommend_eval(
            chat_id,
            username,
            query,
            reply,
            recommendation_ids,
            {"trace_summary": compact_trace_text(public_context), "fallback": True},
            [],
            0.0,
            intent="recommend",
            fallback_used=True,
        )
    return reply


@tool
def recommend_search_stocks(query: str, max_results: int = 5, profile_json: str = "{}") -> str:
    """按自然语言选股需求搜索候选股票。"""
    try:
        profile = json.loads(profile_json or "{}")
    except Exception:
        profile = {}
    enriched = query + _profile_prompt_suffix(profile)
    result = natural_language_select(enriched, max(1, min(int(max_results or 5), 10)))
    return json.dumps(result, ensure_ascii=False, default=str)


@tool
def recommend_analyze_stock(ts_code: str, profile_json: str = "{}") -> str:
    """分析单只股票的技术面、业务、政策、Agent持仓参考和风险。"""
    try:
        profile = json.loads(profile_json or "{}")
    except Exception:
        profile = {}
    return generate_stock_report(ts_code, profile)


@tool
def recommend_compare_stocks(ts_codes: str, profile_json: str = "{}") -> str:
    """对比多只股票；ts_codes 可传 JSON 数组或逗号分隔文本。"""
    try:
        codes = json.loads(ts_codes) if isinstance(ts_codes, str) and ts_codes.strip().startswith("[") else ts_codes
    except Exception:
        codes = ts_codes
    if isinstance(codes, str):
        codes = [x.strip() for x in re.split(r"[,，/、\s]+", codes) if x.strip()]
    try:
        profile = json.loads(profile_json or "{}")
    except Exception:
        profile = {}
    return compare_stocks(codes or [], profile)


@tool
def recommend_check_ma_bullish(ts_codes: str, chat_id: str = "local", profile_json: str = "{}") -> str:
    """检查一只或多只股票是否满足 MA5/10/20/30 多头发散向上。"""
    try:
        codes = json.loads(ts_codes) if isinstance(ts_codes, str) and ts_codes.strip().startswith("[") else ts_codes
    except Exception:
        codes = ts_codes
    if isinstance(codes, str):
        codes = extract_stock_mentions(codes) or [x.strip() for x in re.split(r"[,，/、\s]+", codes) if x.strip()]
    try:
        profile = json.loads(profile_json or "{}")
    except Exception:
        profile = {}
    return _format_ma_bullish_check(codes or [], chat_id or "local", "", ts_codes, profile)


@tool
def recommend_price_volume_trend(ts_code: str, days: int = 10, chat_id: str = "local", profile_json: str = "{}") -> str:
    """分析单只股票最近 N 日价格与成交量趋势。"""
    try:
        profile = json.loads(profile_json or "{}")
    except Exception:
        profile = {}
    codes = extract_stock_mentions(ts_code) or [ts_code]
    return _format_price_volume_trend(codes[0], days, chat_id or "local", "", ts_code, profile)


@tool
def recommend_find_ma_bullish_pullback(ma_periods: str = "5,10,20", max_results: int = 8, profile_json: str = "{}") -> str:
    """筛选多头均线发散，且近期回踩 MA5/10/20 附近的候选股票。"""
    periods = [int(x) for x in re.findall(r"\d+", ma_periods or "") if int(x) in (5, 10, 20)] or [5, 10, 20]
    query = "多头均线发散并回踩" + "/".join(str(x) for x in periods) + "日线"
    result = natural_language_select(query, max(1, min(int(max_results or 8), 10)))
    return json.dumps(result, ensure_ascii=False, default=str)


@tool
def recommend_get_policy_preference(recency_days: int = 14) -> str:
    """读取近期政策文件，归纳国家政策偏好的产业方向。"""
    return _format_policy_preference()


@tool
def recommend_get_macro_report(trade_date: str = "") -> str:
    """读取每日宏观市场报告，包含大盘、板块、政策、龙虎榜、涨跌停池和交易建议。"""
    return get_macro_daily_report_text(trade_date)


@tool
def recommend_get_market_topic(topic: str = "report", trade_date: str = "") -> str:
    """读取宏观报告指定主题；topic 可为 lhb/capital_flow/northbound/limit_up/limit_quality/promotion/limit_down/broken_limit/strong/sector/report。"""
    return format_macro_topic(topic, trade_date)


@tool
def recommend_get_stock_chip_distribution(ts_code: str) -> str:
    """模拟个股前复权筹码峰/筹码分布，包括获利比例、平均成本和成本集中度。"""
    codes = extract_stock_mentions(ts_code) or [ts_code]
    return format_chip_distribution(codes[0])


@tool
def recommend_get_stock_fundamental_events(ts_code: str) -> str:
    """读取单股近一年 baostock 业绩预告/业绩快报。"""
    codes = extract_stock_mentions(ts_code) or [ts_code]
    return _format_stock_fundamental_events(codes[0])


@tool
def recommend_refresh_market_intelligence(refresh_policy: bool = True) -> str:
    """手动刷新政策缓存并强制重生成每日宏观市场报告。"""
    return json.dumps(refresh_macro_intelligence("", bool(refresh_policy), True), ensure_ascii=False, default=str)


@tool
def recommend_record_stock_interest(chat_id: str, ts_code: str, context: str = "", intent: str = "mention") -> str:
    """记录用户提到或推荐助手推荐过的股票，并生成共享研究报告。"""
    result = record_stock_interest(chat_id or "local", "", ts_code, context, intent, get_profile(chat_id or "local"))
    return json.dumps(result, ensure_ascii=False, default=str)


@tool
def recommend_get_shared_stock_report(ts_code: str) -> str:
    """读取用户关注股票的共享研究报告，供推荐助手和交易员参考。"""
    return get_shared_stock_report(ts_code)


@tool
def recommend_get_identity(chat_id: str = "local", user_id: str = "", thread_id: str = "default") -> str:
    """回答推荐助手是谁、记住了什么、能做什么。"""
    return _identity_reply(chat_id or "local", user_id or "", thread_id or "default")


@tool
def recommend_get_user_profile(chat_id: str, user_id: str = "") -> str:
    """读取用户画像，包括风险偏好、周期、偏好板块和默认推荐数量。"""
    return json.dumps(get_profile(_context_key(chat_id or "local", user_id)), ensure_ascii=False, default=str)


@tool
def recommend_get_watchlist(chat_id: str) -> str:
    """读取用户关注股列表。"""
    return json.dumps(list_watch(chat_id or "local"), ensure_ascii=False, default=str)


@tool
def recommend_get_trader_memory() -> str:
    """读取公开交易员体系、赛马表现、推荐技能和推荐助手记忆。"""
    context = best_public_agent_context()
    return json.dumps({
        "summary": compact_trace_text(context),
        "context": context,
    }, ensure_ascii=False, default=str)


@tool
def recommend_get_agent_performance(agent_id: int) -> str:
    """查询交易 Agent 战绩、持仓、交易和近期日报。"""
    return json.dumps(get_agent_performance(int(agent_id or 1)), ensure_ascii=False, default=str)


@tool
def recommend_record_feedback(chat_id: str, feedback_type: str, text: str = "") -> str:
    """把用户对最近一次推荐的反馈写入推荐反馈表。"""
    ok = _record_text_feedback(chat_id or "local", "", text or feedback_type)
    return json.dumps({"ok": ok, "feedback_type": feedback_type}, ensure_ascii=False)


@tool
def partnership_init_account_tool(command_text: str) -> str:
    """初始化两人合伙股票账户；必须传入完整 /init 文本或自然语言初始化文本。"""
    return partnership_init_account(command_text)


@tool
def partnership_daily_report_tool(command_text: str) -> str:
    """上报合伙账户今日总资产和双方出入金，并按昨日权益比例分配当日盈亏。"""
    return partnership_daily_report(command_text)


@tool
def partnership_status_tool() -> str:
    """查询合伙账户每个参与人的当前权益、累计净投入和累计盈亏。"""
    return partnership_status()


@tool
def partnership_history_tool(limit: int = 7) -> str:
    """查询合伙账户最近 N 天每日分成明细。"""
    return partnership_history(int(limit or 7))


RECOMMEND_TOOLS = [
    recommend_search_stocks,
    recommend_analyze_stock,
    recommend_compare_stocks,
    recommend_check_ma_bullish,
    recommend_price_volume_trend,
    recommend_find_ma_bullish_pullback,
    recommend_get_policy_preference,
    recommend_get_macro_report,
    recommend_get_market_topic,
    recommend_get_stock_chip_distribution,
    recommend_get_stock_fundamental_events,
    recommend_refresh_market_intelligence,
    recommend_get_user_profile,
    recommend_get_watchlist,
    recommend_get_trader_memory,
    recommend_get_agent_performance,
    recommend_record_stock_interest,
    recommend_get_shared_stock_report,
    recommend_get_identity,
    recommend_record_feedback,
    partnership_init_account_tool,
    partnership_daily_report_tool,
    partnership_status_tool,
    partnership_history_tool,
]


RECOMMEND_SYSTEM_PROMPT = """你是股票推荐助手，使用 ReAct 工具循环回答 Telegram 用户。

要求：
- 先识别 intent：recommend/analyze/position_advice/compare/ma_check/price_volume/policy/profile/watchlist/identity/followup/backtest/intraday/feedback/partnership_account/help。
- 输入 JSON 里 memory_context 已按层分开：short_term_messages 是最近 5-8 轮短期上下文，session_summary 是当前会话中期摘要，long_term_memories 是 user/chat/thread/global 长期画像。回答时优先遵守当前用户记忆，不要把群聊其他人的偏好当成当前用户偏好。
- 用户说“上次推荐的那只/刚才那几个/继续看”时，优先结合 memory_context 中最近推荐标的和上下文回答，不能当成全新无上下文问题。
- 用户围绕上一只股票继续说“重仓/均价/亏损/清仓/割肉/止损/减仓/怎么办”时，这是 position_advice，不是选股请求；必须结合 short_term_messages 里的上一只股票和用户成本回答，recommendations 可以为空。
- 推荐股票时必须调用 recommend_get_user_profile(chat_id,user_id) 和 recommend_get_trader_memory；通常还要调用 recommend_search_stocks。
- 对最终推荐中的主要标的至少抽样调用 recommend_analyze_stock 或 recommend_compare_stocks 获取证据。
- 用户问“是否多头均线发散”时调用 recommend_check_ma_bullish；问“最近十天价格与量趋势”时调用 recommend_price_volume_trend。
- 用户使用 /init、/daily、/status、/history 或询问合伙账户/分成/权益时，必须调用 partnership_* 工具；禁止自行计算账户盈亏、权益和分配。
- 用户问“政策偏好/国家政策”时调用 recommend_get_policy_preference；问“你是谁”时调用 recommend_get_identity。
- 用户问“今天龙虎榜/北向资金/沪深港通资金/涨停板/涨停质量/板质量/封板质量/晋级率/跌停板/炸板/强势股池/板块热度”时调用 recommend_get_market_topic。
- 用户问“筹码峰/筹码分布”时调用 recommend_get_stock_chip_distribution；问“最新业绩预告/业绩快报/基本面事件”时调用 recommend_get_stock_fundamental_events。
- 用户要求“刷新最新政策面/基本面/宏观数据”时调用 recommend_refresh_market_intelligence。
- 评价个股是否推荐时，结合 recommend_analyze_stock、recommend_get_macro_report 或 recommend_get_market_topic("sector")，必要时再看筹码分布。
- 用户提到具体股票、上车、持有、想买或最终推荐股票后，调用 recommend_record_stock_interest 生成共享研究报告。
- 输出必须是 JSON，不要包 Markdown 代码块：
{"intent":"recommend","reply":"给用户看的中文回复","recommendations":[{"ts_code":"600000.SH","name":"浦发银行","reason":"...","risk":"...","score":80}],"risk_notes":["..."],"trace_summary":"..."}
- reply 必须包含研究用途风险提示，不构成投资建议。
"""


def _build_recommend_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=0.2,
        extra_body={"thinking": {"type": "disabled"}},
        timeout=90,
    )


def _extract_json_object(text: str) -> dict:
    return _extract_json_object_repaired(text)


def _coerce_markdown_react_reply(raw_output: str, user_input: str) -> dict:
    """Accept a substantial final Markdown answer when the model forgot JSON wrapping."""
    text = (raw_output or "").strip()
    if len(text) < 120:
        return {}
    markers = ("📌", "##", "一、", "结论", "风险提示", "仅供研究", "不构成投资建议")
    if not any(marker in text for marker in markers):
        return {}
    starts = [idx for marker in ("---\n\n📌", "\n\n📌", "📌", "##", "一、") if (idx := text.find(marker)) >= 0]
    if starts:
        text = text[min(starts):].lstrip("- \n")
    text = re.sub(r"^(现在|我需要|让我|用户).{0,140}\n", "", text).strip()
    if not any(marker in text for marker in ("结论", "风险提示", "仅供研究", "不构成投资建议", "建议")):
        return {}
    if "不构成投资建议" not in text:
        text = text.rstrip() + "\n\n⚠️ 风险提示：以上分析仅供研究参考，不构成投资建议。"
    return {
        "intent": _guess_intent(user_input),
        "reply": text,
        "recommendations": [],
        "risk_notes": ["模型未按 JSON 输出，已保留其最终 Markdown 正文。"],
        "trace_summary": "ReAct 已完成工具分析，但最终未包成 JSON；系统将 Markdown 正文转为有效回复。",
        "_coerced_from_markdown": True,
    }


def _is_simple_recommend_query(text: str) -> bool:
    raw = text or ""
    if not _is_stock_selection_query(raw):
        return False
    return any(k in raw for k in (
        "龙头", "强势", "涨停", "连板", "回踩5", "回踩10", "回踩20",
        "5线", "10线", "20线", "5日线", "10日线", "20日线", "20日均线", "均线回踩",
        "均线多头", "多头均线", "多头排列", "均线向上", "均线上行", "均线发散",
    ))


def _requires_recommendations(text: str, intent: str) -> bool:
    if (intent or "") != "recommend":
        return False
    return _is_stock_selection_query(text)


def _run_rule_recommendation(
    query: str,
    chat_id: str,
    username: str,
    context: dict,
    reason: str = "rule",
    failed_react_trace: list[dict] | None = None,
    fallback_error: str = "",
    progress_callback=None,
    user_id: str = "",
    thread_id: str = "default",
) -> dict:
    started = time.perf_counter()
    if progress_callback:
        progress_callback({"type": "rule_start", "query": query, "mode": reason})
    memory_context = build_memory_prompt(chat_id or "local", user_id, thread_id, query, None, 6)
    if progress_callback:
        progress_callback(_memory_progress_payload(memory_context))
    profile = get_profile(_context_key(chat_id or "local", user_id))
    reply = format_recommendation(
        query,
        profile=profile,
        chat_id=chat_id or "local",
        username=username,
        record_eval_enabled=False,
        progress_callback=progress_callback,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    conn = get_conn()
    rows = conn.execute(
        """SELECT id FROM telegram_recommend_feedback
           WHERE chat_id=? AND query=?
           ORDER BY created_at DESC, id DESC LIMIT 10""",
        (chat_id or "local", query),
    ).fetchall()
    recommendation_ids = [int(r["id"]) for r in rows]
    eval_id = record_recommend_eval(
        chat_id or "local",
        username,
        query,
        reply,
        recommendation_ids,
        {
            "trace_summary": compact_trace_text(context),
            "mode": reason,
            "fallback_error": fallback_error,
            "failed_react_trace": failed_react_trace or [],
            "memory": memory_context,
        },
        failed_react_trace or [],
        latency_ms,
        intent="recommend",
        status="ok" if reason == "rule" else "fallback",
        fallback_used=reason != "rule",
    )
    conn.close()
    return {
        "message": reply,
        "trace_id": recommendation_ids[0] if recommendation_ids else None,
        "recommendation_ids": recommendation_ids,
        "eval_id": eval_id,
        "mode": reason,
    }


def _check_recommend_rate_limit(chat_id: str) -> dict:
    """Limit expensive Telegram recommendation LLM calls per user."""
    user_key = str(chat_id or "local")
    today = date.today().strftime("%Y%m%d")
    setting_key = f"telegram_recommend_rate:{user_key}:{today}"
    now = time.time()
    conn = get_conn()
    row = conn.execute("SELECT value FROM system_settings WHERE key=?", (setting_key,)).fetchone()
    try:
        payload = json.loads(row["value"]) if row else {}
    except Exception:
        payload = {}
    count = int(payload.get("count") or 0)
    last_ts = float(payload.get("last_ts") or 0)
    if count >= RECOMMEND_DAILY_LIMIT:
        conn.close()
        return {"ok": False, "reason": "daily_limit", "count": count, "limit": RECOMMEND_DAILY_LIMIT}
    cooldown_left = RECOMMEND_COOLDOWN_SECONDS - (now - last_ts)
    if cooldown_left > 0:
        conn.close()
        return {
            "ok": False,
            "reason": "cooldown",
            "cooldown_left": round(cooldown_left, 1),
            "count": count,
            "limit": RECOMMEND_DAILY_LIMIT,
        }
    next_payload = {"count": count + 1, "last_ts": now}
    conn.execute(
        """INSERT INTO system_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
        (setting_key, json.dumps(next_payload, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "count": count + 1, "limit": RECOMMEND_DAILY_LIMIT}


def _run_recommend_loop(
    user_input: str,
    chat_id: str,
    username: str,
    max_tool_turns: int = 6,
    progress_callback=None,
    user_id: str = "",
    thread_id: str = "default",
    chat_type: str = "",
) -> tuple[dict, list[dict], float, bool]:
    llm = _build_recommend_llm()
    profile_key = _context_key(chat_id or "local", user_id)
    if progress_callback:
        progress_callback({"type": "phase", "message": "读取用户画像，匹配风险偏好和常用周期。"})
    profile = apply_inferred_preferences(profile_key, user_input, username)
    if progress_callback:
        progress_callback({"type": "phase", "message": "加载短期上下文、中期会话摘要和长期记忆。"})
    memory_context = build_memory_prompt(chat_id or "local", user_id, thread_id, user_input, None, 8)
    if progress_callback:
        progress_callback(_memory_progress_payload(memory_context))
        progress_callback({"type": "phase", "message": "启动 ReAct 工具循环，按证据生成回复。"})
    result = ReActLoop(
        llm,
        RECOMMEND_TOOLS,
        metadata={
            "agent_loop": "telegram_recommender",
            "chat_id": chat_id or "local",
            "user_id": user_id or "",
            "thread_id": thread_id or "default",
        },
    ).run(
        RECOMMEND_SYSTEM_PROMPT,
        json.dumps({
            "text": user_input,
            "chat_id": chat_id or "local",
            "user_id": user_id or "",
            "thread_id": thread_id or "default",
            "chat_type": chat_type or "",
            "username": username,
            "profile": profile,
            "memory_context": memory_context,
        }, ensure_ascii=False),
        max_turns=max_tool_turns,
        final_instruction="请停止调用工具，直接输出符合要求的 JSON 回复。",
        event_callback=progress_callback,
    )
    raw_output = result.output
    data = _extract_json_object(raw_output)
    json_parse_ok = bool(data)
    if not data:
        result.trace.append({
            "type": "parse",
            "turn": len([x for x in result.trace if x.get("type") == "llm"]),
            "error": "invalid_json",
            "output_part_count": 1 if raw_output else 0,
            "raw_output_preview": raw_output[:3000],
        })
        data = _coerce_markdown_react_reply(raw_output, user_input)
        if data:
            result.trace.append({
                "type": "parse",
                "turn": len([x for x in result.trace if x.get("type") == "llm"]),
                "error": "",
                "coerced_from_markdown": True,
            })
    return data, result.trace, result.latency_ms, json_parse_ok


def run_recommend_react_agent(
    text: str,
    chat_id: str = "local",
    username: str = "",
    progress_callback=None,
    user_id: str = "",
    thread_id: str = "default",
    chat_type: str = "",
) -> dict:
    raw = (text or "").strip()
    query = raw.split(maxsplit=1)[1] if raw.lower().startswith("/recommend") and len(raw.split(maxsplit=1)) > 1 else raw
    context = best_public_agent_context()
    if _is_simple_recommend_query(query):
        if progress_callback:
            progress_callback({"type": "phase", "message": "问题较明确，使用快速规则链先筛选候选股票。"})
        return _run_rule_recommendation(
            query,
            chat_id or "local",
            username,
            context,
            "rule",
            progress_callback=progress_callback,
            user_id=user_id,
            thread_id=thread_id,
        )
    tool_trace: list[dict] = []
    rate = _check_recommend_rate_limit(_context_key(chat_id or "local", user_id))
    if not rate.get("ok"):
        if progress_callback:
            progress_callback({"type": "phase", "message": "触发频率限制，切换到低成本规则链回复。"})
        result = _run_rule_recommendation(
            query,
            chat_id or "local",
            username,
            context,
            "rate_limited",
            failed_react_trace=[{"type": "rate_limit", **rate}],
            fallback_error=rate.get("reason", "rate_limited"),
            progress_callback=progress_callback,
            user_id=user_id,
            thread_id=thread_id,
        )
        result["rate_limited"] = True
        return result
    try:
        data, tool_trace, latency_ms, json_ok = _run_recommend_loop(
            query,
            chat_id or "local",
            username,
            progress_callback=progress_callback,
            user_id=user_id,
            thread_id=thread_id,
            chat_type=chat_type,
        )
        if not data or not data.get("reply"):
            raise ValueError("recommend ReAct did not produce valid reply JSON")
        reply = data.get("reply")
        recommendations = data.get("recommendations") or []
        if _requires_recommendations(query, data.get("intent", "recommend")) and not recommendations:
            raise ValueError("recommend ReAct reply has no recommendations")
        rows = []
        for item in recommendations:
            if item.get("ts_code"):
                rows.append({
                    "ts_code": item.get("ts_code"),
                    "name": item.get("name", ""),
                    "reason": item.get("reason", ""),
                    "score": item.get("score", 0),
                    "risk": item.get("risk", ""),
                })
        recommendation_ids = _record_recommendation(chat_id or "local", username, query, rows, data.get("intent") or "recommend_react", context)
        trace = {
            "intent": data.get("intent") or "recommend",
            "trace_summary": data.get("trace_summary") or compact_trace_text(context),
            "tools": [x for x in tool_trace if x.get("type") == "tool"],
            "risk_notes": data.get("risk_notes") or [],
            "recommendations": recommendations,
        }
        eval_id = record_recommend_eval(
            chat_id or "local",
            username,
            query,
            reply,
            recommendation_ids,
            trace,
            tool_trace,
            latency_ms,
            intent=data.get("intent") or "recommend",
            json_parse_ok=json_ok,
        )
        return {
            "message": reply,
            "trace_id": recommendation_ids[0] if recommendation_ids else None,
            "recommendation_ids": recommendation_ids,
            "eval_id": eval_id,
            "trace": trace,
        }
    except Exception as exc:
        if progress_callback:
            progress_callback({"type": "phase", "message": "ReAct 输出不可用，切换到规则链兜底。"})
        if _is_position_advice_query(query):
            reply = _format_position_advice(
                query,
                chat_id or "local",
                username,
                get_profile(_context_key(chat_id or "local", user_id)),
                user_id,
                thread_id,
                progress_callback,
            )
            eval_id = record_recommend_eval(
                chat_id or "local",
                username,
                query,
                reply,
                [],
                {
                    "trace_summary": "position_advice_fallback",
                    "fallback_error": str(exc),
                    "failed_react_trace": tool_trace,
                },
                tool_trace,
                0,
                intent="position_advice",
                status="fallback",
                fallback_used=True,
            )
            return {
                "message": reply,
                "trace_id": None,
                "recommendation_ids": [],
                "eval_id": eval_id,
                "error": str(exc),
                "fallback": True,
            }
        result = _run_rule_recommendation(
            query,
            chat_id or "local",
            username,
            context,
            "fallback",
            failed_react_trace=tool_trace,
            fallback_error=str(exc),
            progress_callback=progress_callback,
            user_id=user_id,
            thread_id=thread_id,
        )
        result["error"] = str(exc)
        result["fallback"] = True
        return result


def handle_text_message(
    text: str,
    chat_id: str = "",
    username: str = "",
    progress_callback=None,
    user_id: str = "",
    thread_id: str = "default",
    chat_type: str = "",
) -> str:
    """Record scoped memory around the existing Telegram text parser."""
    raw = (text or "").strip()
    gate = preflight_route(raw)
    if is_lightweight_action(gate.action):
        return gate.reply
    intent = _guess_intent(raw)
    if progress_callback:
        progress_callback({"type": "intent", "intent": intent})
        progress_callback({"type": "phase", "message": "记录当前对话，并更新可复用记忆。"})
    message_id = record_message(
        chat_id or "local",
        user_id=user_id or "",
        thread_id=thread_id,
        chat_type=chat_type,
        role="user",
        content=raw,
        intent=intent,
        metadata={"username": username or ""},
    )
    update_memories_from_text(
        chat_id or "local",
        user_id=user_id or "",
        thread_id=thread_id,
        chat_type=chat_type,
        text=raw,
        source_message_id=message_id,
        intent=intent,
    )
    if progress_callback:
        progress_callback({"type": "phase", "message": "根据问题类型选择规则链或 ReAct 工具链。"})
    reply = _handle_text_message_inner(
        text,
        chat_id,
        username,
        progress_callback=progress_callback,
        user_id=user_id,
        thread_id=thread_id,
        chat_type=chat_type,
    )
    record_message(
        chat_id or "local",
        user_id=user_id or "",
        thread_id=thread_id,
        chat_type=chat_type,
        role="assistant",
        content=reply,
        intent=intent,
        metadata={"reply_to": message_id, "username": username or ""},
    )
    maybe_schedule_memory_distillation(chat_id or "local", user_id or "", thread_id, chat_type)
    return reply


def _handle_text_message_inner(
    text: str,
    chat_id: str = "",
    username: str = "",
    progress_callback=None,
    user_id: str = "",
    thread_id: str = "default",
    chat_type: str = "",
) -> str:
    """Parse a Telegram text message and return a plain text response."""
    raw = (text or "").strip()
    lower = raw.lower()
    profile_key = _context_key(chat_id or "local", user_id)
    profile = apply_inferred_preferences(profile_key, raw, username) if chat_id else None
    if _is_position_advice_query(raw):
        return _format_position_advice(raw, chat_id or "local", username, profile, user_id, thread_id, progress_callback)
    if _is_emotional_risk_help_query(raw):
        return _format_emotional_risk_help(raw, profile)
    if _record_text_feedback(chat_id or "local", username, raw):
        if any(token in raw for token in ("太激进", "太冒险", "恐高")):
            update_profile(profile_key, {"risk_level": "低"}, username)
        elif any(token in raw for token in ("太保守", "机会少")):
            update_profile(profile_key, {"risk_level": "高"}, username)
        return "已记录这次反馈，后续推荐会降低同类低胜率逻辑的权重。"
    if not raw or lower in {"/start", "/help", "help", "帮助"}:
        return (
            "可用指令:\n"
            "/recommend 帮我推荐3只强势科技股\n"
            "/analyze 600000.SH\n"
            "/compare 600000.SH 600036.SH\n"
            "/profile 或 /profile set 风险=中等 周期=短线 板块=AI,半导体\n"
            "/watch add 600000.SH / /watch list / /watch remove 600000.SH\n"
            "/daily on / /daily off\n"
            "/intraday 盘中检查关注股、板块和待触发条件单\n"
            "/backtest ma_bullish_pullback 1m\n"
            "/memory 查看记忆 / /memory forget 关键词\n"
            "/init xulu hsw 150000 100000 初始化合伙账户\n"
            "/daily 256000 0 5000 上报合伙账户今日总资产和出入金\n"
            "/daily amend 465759.29 更正最近一天录错的合伙账户总资产\n"
            "/history 查看最近7天合伙账户分成记录\n"
            "/login 获取看板登录验证码 / /whoami 查看 Telegram 身份\n"
            "/status 查看合伙账户状态，/status 1 查看交易员战绩\n"
            "/sim 1\n"
            "/bind 1\n"
            "也可以直接发自然语言选股需求。"
        )

    if is_partnership_account_message(raw):
        if progress_callback:
            progress_callback({
                "type": "tool_start",
                "tool": "partnership_account_tool",
                "description": "解析合伙账户命令，并通过 SQLite 工具完成初始化、每日分成、状态或历史查询。",
                "args": {"command": raw[:120]},
            })
        reply = dispatch_partnership_command(raw)
        if progress_callback:
            progress_callback({
                "type": "tool",
                "tool": "partnership_account_tool",
                "description": "解析合伙账户命令，并通过 SQLite 工具完成初始化、每日分成、状态或历史查询。",
                "args": {},
                "result_preview": reply[:180],
            })
        return reply

    if lower.startswith("/memory") or raw.startswith("记忆"):
        if "forget" in lower or "删除" in raw or "忘记" in raw:
            m = re.search(r"#(\d+)", raw)
            if m:
                ok = delete_memory_item(int(m.group(1)))
                return "已删除这条记忆。" if ok else "没有找到这条记忆。"
            keyword = re.sub(r"^/memory\s*forget", "", raw, flags=re.I).replace("删除", "").replace("忘记", "").strip()
            deleted = forget_memories_by_keyword(keyword, chat_id or "local", user_id, thread_id)
            return f"已删除 {deleted} 条匹配记忆。" if keyword else "用法: /memory forget 关键词，或 /memory forget #12"
        return format_memory_overview(chat_id or "local", user_id, thread_id)

    if lower.startswith("/render_test") or raw in {"渲染测试", "telegram渲染测试"}:
        return _render_test_reply()

    if any(token in raw for token in ("记住", "记一下", "帮我记")):
        return "已写入当前用户/会话记忆。你可以用 /memory 查看，或 /memory forget 关键词 删除。"

    if raw in {"你是谁", "你叫什么", "你是干嘛的"} or any(token in raw for token in ("你是谁", "你叫什么", "你的名字")):
        return _identity_reply(chat_id or "local", user_id, thread_id)

    if lower.startswith("/bind"):
        agent_id = _parse_int(raw, 0)
        if not agent_id:
            return "用法: /bind 1"
        from backend.telegram.gateway import bind_chat
        result = bind_chat(agent_id, chat_id, username)
        return "绑定成功" if result.get("ok") else f"绑定失败: {result.get('error', '')}"

    if lower.startswith(("/status", "status", "状态", "战绩")):
        agent_id = _parse_int(raw, 1)
        return format_agent_performance(agent_id)

    if lower.startswith(("/sim", "sim", "模拟")):
        sim_id = _parse_int(raw, 0)
        if not sim_id:
            return "用法: /sim 1"
        return format_simulation_performance(sim_id)

    if lower.startswith(("/backtest", "/bt", "backtest", "bt")) or "回测" in raw:
        return _format_backtest_reply(raw)

    if lower.startswith(("/intraday", "/alert", "intraday")) or any(token in raw for token in ("盘中提醒", "盘中检查", "盘中异动")):
        if "off" in lower or "关闭" in raw:
            set_intraday_push(chat_id or "local", False)
            return "盘中提醒已关闭。"
        if "on" in lower or "开启" in raw:
            set_intraday_push(chat_id or "local", True)
            return "盘中提醒已开启。交易时段会按配置间隔推送关注股、板块和待触发条件单。"
        return _format_intraday_reply(chat_id or "local", raw)

    if any(token in raw for token in ("国家政策偏好", "政策偏好", "最近政策", "政策方向", "国家政策")):
        return _format_policy_preference()

    if any(token in raw for token in ("刷新最新", "手动刷新", "刷新政策", "刷新基本面", "刷新宏观", "刷新市场")):
        return _format_refresh_reply(raw)

    if any(token in raw for token in ("今天龙虎榜", "龙虎榜")):
        return format_macro_topic("lhb")

    if any(token in raw for token in ("北向资金", "沪深港通资金", "沪深港通", "外资流入", "外资流出")):
        return format_macro_topic("capital_flow")

    if any(token in raw for token in ("涨停质量", "板质量", "封板质量", "板好不好")):
        return format_macro_topic("limit_quality")

    if any(token in raw for token in ("晋级率", "晋级淘汰", "涨停晋级", "连板晋级")):
        return format_macro_topic("promotion")

    if any(token in raw for token in ("今天涨停板", "涨停板", "涨停股池")) and "炸" not in raw:
        return format_macro_topic("limit_up")

    if any(token in raw for token in ("今天跌停板", "跌停板", "跌停股池")):
        return format_macro_topic("limit_down")

    if any(token in raw for token in ("涨停炸板", "炸板", "炸板股池")):
        return format_macro_topic("broken_limit")

    if any(token in raw for token in ("强势股池", "强势池")):
        return format_macro_topic("strong")

    if any(token in raw for token in ("板块热度", "今天板块", "当前板块", "热点板块")) and not extract_stock_mentions(raw):
        return format_macro_topic("sector")

    stock_codes = extract_stock_mentions(raw)
    if stock_codes and _is_contextual_stock_followup(raw):
        return _format_contextual_stock_followup(raw, chat_id or "local", username, profile, user_id, thread_id, progress_callback)

    if stock_codes and any(token in raw for token in ("筹码峰", "筹码分布", "筹码")):
        lines = [format_chip_distribution(code) for code in stock_codes[:3]]
        for code in stock_codes[:3]:
            _record_interest_quietly(chat_id or "local", username, code, raw, "chip_distribution", profile, user_id, thread_id)
        return "\n\n---\n\n".join(lines)

    if stock_codes and any(token in raw for token in ("业绩预告", "业绩快报", "基本面事件", "最新基本面")):
        return "\n\n---\n\n".join(_format_stock_fundamental_events(code) for code in stock_codes[:3])

    if stock_codes and any(token in raw for token in ("结合今天板块", "结合板块", "所属板块", "板块热度")) and any(token in raw for token in ("是否推荐", "推荐吗", "能不能买", "如何看", "怎么看", "评价")):
        reports = []
        sector_text = format_macro_topic("sector")
        for code in stock_codes[:3]:
            reports.append("\n".join([
                generate_stock_report(code, profile),
                "",
                "六、结合今日板块热度",
                sector_text[:1600],
            ]))
            _record_interest_quietly(chat_id or "local", username, code, raw, "stock_with_sector", profile, user_id, thread_id)
        return "\n\n---\n\n".join(reports)

    if stock_codes and any(token in raw for token in ("多头均线", "均线发散", "多头排列", "均线向上", "均线上行")) and any(token in raw for token in ("是", "吗", "是不是", "判断", "检查")):
        return _format_ma_bullish_check(stock_codes, chat_id or "local", username, raw, profile)

    if stock_codes and any(token in raw for token in ("量价", "价格与量", "价格和量", "成交量趋势", "十天", "10天", "近10日", "最近10日")):
        return _format_price_volume_trend(stock_codes[0], 10, chat_id or "local", username, raw, profile)

    if _is_recommend_followup(raw):
        return _format_recommend_followup(raw, chat_id or "local", username, profile, user_id, thread_id)

    single_stock_intent = (
        lower.startswith(("/analyze", "analyze", "分析", "看看", "问问"))
        or any(token in raw for token in ("如何看", "怎么看", "怎么操作", "点评", "评价", "大牛股", "分析下", "看一下", "看下"))
    )
    if single_stock_intent:
        codes = extract_stock_mentions(raw)
        if not codes:
            return "没识别到具体股票。可以发: 京东方A如何看，或 /analyze 000725.SZ"
        if progress_callback:
            progress_callback({
                "type": "tool_start",
                "tool": "recommend_analyze_stock",
                "description": "分析单只股票的技术面、趋势、风险和推荐理由。",
                "args": {"codes": codes[:3]},
            })
        reports = [generate_stock_report(code, profile) for code in codes[:3]]
        for code in codes[:3]:
            _record_interest_quietly(chat_id or "local", username, code, raw, "single_stock_analysis", profile, user_id, thread_id)
        if progress_callback:
            progress_callback({
                "type": "tool",
                "tool": "recommend_analyze_stock",
                "description": "分析单只股票的技术面、趋势、风险和推荐理由。",
                "args": {"count": len(reports)},
                "result_preview": f"已生成{len(reports)}份单股结构化分析。",
            })
        return "\n\n---\n\n".join(reports)

    if lower.startswith(("/compare", "compare", "对比", "比较")):
        codes = extract_stock_mentions(raw)
        if len(codes) < 2:
            return "用法: /compare 600000.SH 600036.SH"
        return compare_stocks(codes, profile)

    if lower.startswith("/profile") or raw.startswith("画像"):
        if "set" in lower or "设置" in raw:
            updates = parse_profile_set(raw)
            if not updates:
                return "未识别到画像设置。示例: /profile set 风险=中等 周期=短线 板块=AI,半导体"
            profile = update_profile(profile_key, updates, username)
            return "画像已更新。\n" + format_profile(profile_key)
        return format_profile(profile_key)

    if lower.startswith("/daily") or raw.startswith("每日"):
        if "off" in lower or "关闭" in raw:
            set_daily_push(chat_id or "local", False, username)
            return "每日摘要推送已关闭。"
        if "on" in lower or "开启" in raw:
            set_daily_push(chat_id or "local", True, username)
            return "每日摘要推送已开启，将推送战绩+市场摘要。"
        from backend.telegram.digest import build_market_digest
        return build_market_digest(chat_id or "local", _parse_int(raw, 1))

    if lower.startswith("/watch") or raw.startswith("关注"):
        codes = extract_stock_mentions(raw)
        if "list" in lower or "列表" in raw:
            return format_watchlist(chat_id or "local")
        if "remove" in lower or "del" in lower or "删除" in raw or "移除" in raw:
            if not codes:
                return "用法: /watch remove 600000.SH"
            remove_watch(chat_id or "local", codes[0])
            return f"已移除关注: {codes[0]}"
        if "add" in lower or "添加" in raw or "关注" in raw:
            if not codes:
                return "用法: /watch add 600000.SH"
            name = lookup_stock_name(codes[0])
            add_watch(chat_id or "local", codes[0], name)
            return f"已加入关注: {codes[0]} {name}"
        return format_watchlist(chat_id or "local")

    if lower.startswith("/recommend"):
        raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else "推荐3只强势股"

    return run_recommend_react_agent(
        raw,
        chat_id or "local",
        username,
        progress_callback=progress_callback,
        user_id=user_id,
        thread_id=thread_id,
        chat_type=chat_type,
    ).get("message", "")
