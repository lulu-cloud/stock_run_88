"""Daily Telegram digest generation."""

from backend.data.indicators import compute_market_strength_by_sector
from backend.data.loader import load_index_daily
from backend.db.repository import get_conn
from backend.telegram.profile import get_profile, list_watch
from backend.telegram.recommender import format_agent_performance
from backend.telegram.stock_analysis import watchlist_alerts
from backend.policy.reader import extract_policy_signals


def _latest_trade_date() -> str:
    df = load_index_daily()
    if df is not None and not df.empty:
        return str(df.iloc[-1]["trade_date"])
    return ""


def _setting_bool(key: str, default: bool = True) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if not row or row["value"] is None:
        return default
    return str(row["value"]).lower() in ("1", "true", "yes", "on")


def _truncate(text: str, limit: int = 3800) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n\n..."


def _fmt_money(value) -> str:
    return f"{float(value or 0):,.2f}"


def _agent_asset_table_md(agent_id: int, trade_date: str) -> str:
    conn = get_conn()
    report = conn.execute(
        """SELECT cash, market_value, total_assets, daily_pnl, daily_return,
                  cumulative_pnl, cumulative_return, position_count
           FROM agent_daily_report
           WHERE agent_id=? AND trade_date=?""",
        (agent_id, trade_date),
    ).fetchone()
    frozen = conn.execute(
        "SELECT COALESCE(SUM(reserved_cash), 0) FROM agent_order WHERE agent_id=? AND status='pending'",
        (agent_id,),
    ).fetchone()[0] or 0
    unrealized = conn.execute(
        "SELECT COALESCE(SUM(unrealized_pnl), 0) FROM agent_position WHERE agent_id=? AND quantity>0",
        (agent_id,),
    ).fetchone()[0] or 0
    conn.close()
    if not report:
        return "暂无本交易日复盘报告"
    return "\n".join([
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 可用资金 | {_fmt_money(report['cash'])} |",
        f"| 冻结资金 | {_fmt_money(frozen)} |",
        f"| 持仓市值 | {_fmt_money(report['market_value'])} |",
        f"| 浮动盈亏 | {_fmt_money(unrealized)} |",
        f"| 总资产 | {_fmt_money(report['total_assets'])} |",
        f"| 今日收益 | {_fmt_money(report['daily_pnl'])} / {float(report['daily_return'] or 0):+.2f}% |",
        f"| 累计收益 | {_fmt_money(report['cumulative_pnl'])} / {float(report['cumulative_return'] or 0):+.2f}% |",
        f"| 持仓数量 | {report['position_count']} |",
    ])


def _agent_orders_md(agent_id: int, trade_date: str) -> str:
    conn = get_conn()
    rows = conn.execute(
        """SELECT ts_code, stock_name, direction, quantity, price, order_type, open_get_in,
                  trade_date, status, reason, fail_reason
           FROM agent_order
           WHERE agent_id=? AND status='pending'
           ORDER BY trade_date ASC, id ASC""",
        (agent_id,),
    ).fetchall()
    conn.close()
    if not rows:
        return "无待触发预操作单"

    lines = []
    for r in rows[:8]:
        direction = "买入" if r["direction"] == "buy" else "卖出"
        name = r["stock_name"] or ""
        open_flag = "，开盘抢入/出" if r["open_get_in"] else ""
        reason = f"\n  理由: {r['reason']}" if r["reason"] else ""
        lines.append(
            f"- *{direction}* {r['ts_code']} {name} {r['quantity']}股 @ {float(r['price'] or 0):.2f}"
            f"{open_flag}，交易日 {r['trade_date']}{reason}"
        )
    return "\n".join(lines)


def _agent_report_summary(agent_id: int, trade_date: str) -> str:
    return _agent_asset_table_md(agent_id, trade_date)


def _agent_trades_md(agent_id: int, trade_date: str) -> str:
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.ts_code, t.stock_name, t.direction, t.quantity, t.price, t.total_value,
                  o.reason, o.open_get_in
           FROM agent_trade_log t
           LEFT JOIN agent_order o ON o.id=t.order_id
           WHERE t.agent_id=? AND t.trade_date=?
           ORDER BY t.id ASC""",
        (agent_id, trade_date),
    ).fetchall()
    conn.close()
    if not rows:
        return "今日无成交"
    lines = []
    for r in rows[:10]:
        direction = "买入" if r["direction"] == "buy" else "卖出"
        reason = f"\n  理由: {r['reason']}" if r["reason"] else ""
        lines.append(
            f"- *{direction}* {r['ts_code']} {r['stock_name'] or ''} {r['quantity']}股 @ "
            f"{float(r['price'] or 0):.2f}，金额 {float(r['total_value'] or 0):,.2f}{reason}"
        )
    return "\n".join(lines)


def _agent_failed_orders_md(agent_id: int, trade_date: str) -> str:
    expire_date = ""
    if trade_date and len(str(trade_date)) == 8:
        td = str(trade_date)
        expire_date = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, ts_code, stock_name, direction, quantity, price, status, fail_reason, trade_date
           FROM agent_order
           WHERE agent_id=?
             AND status IN ('expired', 'cancelled')
             AND (trade_date=? OR substr(COALESCE(expired_at, ''), 1, 10)=?)
           ORDER BY id ASC""",
        (agent_id, trade_date, expire_date),
    ).fetchall()
    conn.close()
    if not rows:
        return ""
    lines = ["*未成交/取消订单*（含被新复盘替换的旧预操作单）"]
    for r in rows[:8]:
        direction = "买入" if r["direction"] == "buy" else "卖出"
        reason = r["fail_reason"] or "-"
        if reason == "新复盘替换旧预操作单":
            reason = "旧预操作单被当日新复盘替换，非真实成交失败"
        lines.append(
            f"- #{r['id']} {r['status']} {direction} {r['ts_code']} {r['stock_name'] or ''} "
            f"{r['quantity']}股 @ {float(r['price'] or 0):.2f}: {reason}"
        )
    return "\n".join(lines)


def build_market_digest(chat_id: str, agent_id: int | None = None, trade_date: str | None = None) -> str:
    profile = get_profile(chat_id)
    trade_date = trade_date or _latest_trade_date()
    lines = [f"*每日市场摘要 {trade_date}*"]

    if agent_id:
        lines.append("")
        lines.append("*Agent 战绩*")
        lines.append(format_agent_performance(agent_id))
        lines.append("")
        lines.append("*今日复盘*")
        lines.append(_agent_report_summary(agent_id, trade_date))
        lines.append("")
        lines.append("*今日成交*")
        lines.append(_agent_trades_md(agent_id, trade_date))
        failed = _agent_failed_orders_md(agent_id, trade_date)
        if failed:
            lines.append("")
            lines.append(failed)
        lines.append("")
        lines.append("*当前预操作单*")
        lines.append(_agent_orders_md(agent_id, trade_date))

    if trade_date and _setting_bool("push_sector_strength_enabled", True):
        try:
            strength = compute_market_strength_by_sector(trade_date, 3, 5)
            strong = strength.get("strong") or []
            weak = strength.get("weak") or []
            lines.append("")
            lines.append("*板块强弱*")
            lines.append("- 强势: " + ("、".join(f"{x['sector']}({x['avg_pct']}%)" for x in strong[:5]) or "暂无"))
            lines.append("- 弱势: " + ("、".join(f"{x['sector']}({x['avg_pct']}%)" for x in weak[:3]) or "暂无"))
        except Exception as e:
            lines.append(f"板块摘要读取失败: {e}")

    if _setting_bool("push_policy_enabled", True):
        try:
            signals = extract_policy_signals()
            top = signals.get("top_industries") or []
            if top:
                lines.append("")
                lines.append("*政策方向*")
                lines.append("、".join(
                    f"{x.get('industry')}({x.get('strength', x.get('count', 0))})" for x in top[:5]
                ))
        except Exception as e:
            lines.append(f"政策摘要读取失败: {e}")

    if _setting_bool("push_watchlist_enabled", True):
        lines.append("")
        lines.append("*关注股*")
        lines.append(watchlist_alerts(chat_id, list_watch(chat_id)))
    lines.append("")
    lines.append(
        f"*偏好*: 风险{profile.get('risk_level')} / {profile.get('horizon')} / "
        f"{','.join(profile.get('preferred_sectors') or []) or '未设板块'}"
    )
    lines.append("仅供研究，不构成投资建议。")
    return _truncate("\n".join(lines))
