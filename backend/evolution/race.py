"""Agent racing metrics and permission-based capital policy."""

from __future__ import annotations

import json
import math
from statistics import mean, pstdev

from backend.data.loader import load_index_daily


def compute_and_apply_race(agent_id: int, trade_date: str, conn) -> dict:
    reports = [
        dict(r) for r in conn.execute(
            """SELECT trade_date, total_assets, daily_return, cumulative_return
               FROM agent_daily_report
               WHERE agent_id=? AND trade_date<=?
               ORDER BY trade_date DESC LIMIT 20""",
            (agent_id, trade_date),
        ).fetchall()
    ]
    reports = list(reversed(reports))
    agent = conn.execute("SELECT * FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    if not agent:
        return {}

    daily_returns = [float(r.get("daily_return") or 0) for r in reports]
    latest_return = daily_returns[-1] if daily_returns else 0.0
    benchmark_return = _benchmark_return(trade_date)
    excess_return = latest_return - benchmark_return
    max_dd = _max_drawdown([float(r.get("total_assets") or 0) for r in reports])
    sharpe = _sharpe(daily_returns)
    cumulative = float(reports[-1].get("cumulative_return") or 0) if reports else 0.0
    calmar = round(cumulative / max(max_dd, 0.1), 2)
    win_rate, profit_factor = _trade_stats(agent_id, conn)
    beta_score = _beta_score(daily_returns, trade_date)
    alpha_score = max(-20.0, min(20.0, cumulative - beta_score))
    race_score = _race_score(excess_return, max_dd, win_rate, profit_factor, sharpe, alpha_score)
    style_tag = _style_tag(agent, beta_score, alpha_score, max_dd)
    race_advice = _race_advice(race_score, max_dd, daily_returns)

    detail = {
        "window_days": len(reports),
        "daily_returns": daily_returns[-20:],
        "cumulative_return": cumulative,
        "policy_reason": race_advice,
    }
    conn.execute(
        """INSERT INTO agent_race_metric
           (agent_id, trade_date, benchmark_return, excess_return, max_drawdown,
            sharpe_ratio, calmar_ratio, win_rate, profit_factor, beta_score,
            alpha_score, race_score, risk_cap, style_tag, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(agent_id, trade_date) DO UPDATE SET
           benchmark_return=excluded.benchmark_return, excess_return=excluded.excess_return,
           max_drawdown=excluded.max_drawdown, sharpe_ratio=excluded.sharpe_ratio,
           calmar_ratio=excluded.calmar_ratio, win_rate=excluded.win_rate,
           profit_factor=excluded.profit_factor, beta_score=excluded.beta_score,
           alpha_score=excluded.alpha_score, race_score=excluded.race_score,
           risk_cap=excluded.risk_cap, style_tag=excluded.style_tag,
           detail_json=excluded.detail_json, updated_at=datetime('now')""",
        (
            agent_id, trade_date, benchmark_return, excess_return, max_dd, sharpe,
            calmar, win_rate, profit_factor, beta_score, alpha_score, race_score,
            1.0, style_tag, json.dumps(detail, ensure_ascii=False),
        ),
    )
    conn.execute(
        """INSERT INTO agent_capital_policy
           (agent_id, trade_date, max_total_position, max_single_position, disabled_reason, updated_by)
           VALUES (?, ?, ?, ?, ?, 'race_engine')
           ON CONFLICT(agent_id, trade_date) DO UPDATE SET
           max_total_position=excluded.max_total_position,
           max_single_position=excluded.max_single_position,
           disabled_reason=excluded.disabled_reason, updated_at=datetime('now')""",
        (agent_id, trade_date, 1.0, 1.0, race_advice),
    )
    return {
        "benchmark_return": benchmark_return,
        "excess_return": excess_return,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "calmar_ratio": calmar,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "beta_score": beta_score,
        "alpha_score": alpha_score,
        "race_score": race_score,
        "race_advice": race_advice,
        "style_tag": style_tag,
    }


def latest_race_panel(conn, days: int = 90) -> dict:
    agents = [dict(r) for r in conn.execute("SELECT * FROM agent_info ORDER BY id").fetchall()]
    panel = []
    for agent in agents:
        latest = conn.execute(
            """SELECT * FROM agent_race_metric WHERE agent_id=?
               ORDER BY trade_date DESC LIMIT 1""",
            (agent["id"],),
        ).fetchone()
        skills = conn.execute(
            """SELECT skill_id, skill_name, confidence_score
               FROM agent_evolution_skill
               WHERE agent_id=? AND enabled=1
               ORDER BY confidence_score DESC LIMIT 3""",
            (agent["id"],),
        ).fetchall()
        panel.append({
            "agent_id": agent["id"],
            "display_name": agent["display_name"],
            "agent_type": agent["agent_type"],
            "status": agent["status"],
            "metric": dict(latest) if latest else {},
            "skills": [dict(s) for s in skills],
        })
    return {"agents": panel, "days": days}


def agent_race_detail(agent_id: int, conn, days: int = 90) -> dict:
    metrics = [
        dict(r) for r in conn.execute(
            """SELECT * FROM agent_race_metric WHERE agent_id=?
               ORDER BY trade_date ASC LIMIT ?""",
            (agent_id, days),
        ).fetchall()
    ]
    reports = [
        dict(r) for r in conn.execute(
            """SELECT trade_date, total_assets, daily_return, cumulative_return
               FROM agent_daily_report WHERE agent_id=?
               ORDER BY trade_date ASC LIMIT ?""",
            (agent_id, days),
        ).fetchall()
    ]
    policies = [
        dict(r) for r in conn.execute(
            """SELECT * FROM agent_capital_policy WHERE agent_id=?
               ORDER BY trade_date DESC LIMIT 20""",
            (agent_id,),
        ).fetchall()
    ]
    return {"metrics": metrics, "equity_curve": reports, "policies": policies}


def _benchmark_return(trade_date: str) -> float:
    df = load_index_daily()
    if df is None or df.empty:
        return 0.0
    df = df[df["trade_date"].astype(str) <= str(trade_date)].sort_values("trade_date")
    if len(df) < 2:
        return 0.0
    latest, prev = df.iloc[-1], df.iloc[-2]
    if "pct_chg" in latest:
        return round(float(latest.get("pct_chg") or 0), 4)
    prev_close = float(prev.get("close") or 0)
    return round((float(latest.get("close") or 0) - prev_close) / prev_close * 100, 4) if prev_close else 0.0


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_dd = max(max_dd, (peak - value) / peak * 100)
    return round(max_dd, 2)


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sd = pstdev(returns) or 0
    return round((mean(returns) / sd) * math.sqrt(252), 2) if sd else 0.0


def _trade_stats(agent_id: int, conn) -> tuple[float, float]:
    trades = [dict(r) for r in conn.execute(
        """SELECT t.*, o.direction AS order_direction
           FROM agent_trade_log t
           LEFT JOIN agent_order o ON o.id=t.order_id
           WHERE t.agent_id=? ORDER BY t.trade_date DESC, t.id DESC LIMIT 80""",
        (agent_id,),
    ).fetchall()]
    sells = [t for t in trades if str(t.get("direction")) == "sell"]
    if not sells:
        return 0.0, 0.0
    wins = 0
    gains = []
    losses = []
    for sell in sells:
        row = conn.execute(
            """SELECT price FROM agent_trade_log
               WHERE agent_id=? AND ts_code=? AND direction='buy' AND trade_date<=?
               ORDER BY trade_date DESC, id DESC LIMIT 1""",
            (agent_id, sell["ts_code"], sell["trade_date"]),
        ).fetchone()
        cost = float(row["price"] if row else sell["price"])
        pnl = (float(sell["price"]) - cost) * float(sell["quantity"] or 0)
        if pnl > 0:
            wins += 1
            gains.append(pnl)
        else:
            losses.append(abs(pnl))
    win_rate = round(wins / len(sells) * 100, 2)
    profit_factor = round((sum(gains) / max(sum(losses), 1)), 2) if gains or losses else 0.0
    return win_rate, profit_factor


def _beta_score(returns: list[float], trade_date: str) -> float:
    if not returns:
        return 0.0
    # Simple stable proxy: positive score when returns move with benchmark.
    bench = _benchmark_return(trade_date)
    same_direction = sum(1 for r in returns if (r >= 0 and bench >= 0) or (r < 0 and bench < 0))
    return round((same_direction / len(returns)) * 10, 2)


def _style_tag(agent, beta_score: float, alpha_score: float, max_dd: float) -> str:
    if beta_score >= 7 and alpha_score < 3:
        return "贝塔依赖型"
    if alpha_score >= 5 and max_dd <= 8:
        return "Alpha稳健型"
    if max_dd >= 12:
        return "高回撤型"
    if "追高" in str(agent["display_name"]):
        return "情绪进攻型"
    return "均衡观察型"


def _race_score(excess: float, max_dd: float, win_rate: float, profit_factor: float,
                sharpe: float, alpha_score: float) -> float:
    score = 50
    score += max(-15, min(15, excess * 3))
    score += max(-10, 10 - max_dd)
    score += max(-8, min(8, (win_rate - 50) / 5))
    score += max(-8, min(8, (profit_factor - 1) * 8))
    score += max(-5, min(5, sharpe))
    score += max(-5, min(5, alpha_score / 2))
    return round(max(0, min(100, score)), 2)


def _race_advice(score: float, max_dd: float, returns: list[float]) -> str:
    recent_losses = returns[-3:] if len(returns) >= 3 else []
    if len(recent_losses) == 3 and all(r < 0 for r in recent_losses) and sum(recent_losses) <= -6:
        return "连续3日亏损且累计回撤超过6%，仅作为提示：建议 Agent 自主评估是否降频、降仓或暂停激进技能。"
    if max_dd >= 15:
        return "最大回撤超过15%，仅作为提示：建议复盘策略失效场景，系统不强制限制仓位。"
    if score >= 75:
        return "赛马表现强，允许 Agent 保持自身交易风格；系统不强制放大仓位。"
    if score >= 60:
        return "赛马表现中上，建议继续验证当前交易假设。"
    if score >= 45:
        return "赛马表现一般，建议在提示词中提醒 Agent 解释仓位与风险边界。"
    return "赛马表现偏弱，仅作为提示：建议 Agent 复盘亏损来源，但系统不强制限制仓位。"
