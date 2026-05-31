"""Public trading knowledge for Telegram recommendations."""

from __future__ import annotations

import json

from backend.db.repository import get_conn
from backend.evolution.memory import ensure_telegram_memory, read_telegram_memory, update_telegram_memory
from backend.evolution.reflection import get_public_system_summary


def ensure_recommend_skills(conn=None) -> None:
    close = conn is None
    conn = conn or get_conn()
    ensure_telegram_memory()
    defaults = [
        ("nl_intent_parse", "自然语言意图识别", 0.62, ["泛用"]),
        ("strategy_stock_pick", "选股推荐", 0.60, ["选股"]),
        ("trader_memory_grounding", "交易员体系引用", 0.58, ["实战"]),
        ("user_profile_match", "用户画像匹配", 0.60, ["画像"]),
        ("risk_disclosure", "风险提示", 0.56, ["风控"]),
        ("feedback_repair", "反馈修正推荐风格", 0.52, ["反馈"]),
    ]
    for skill_id, name, confidence, tags in defaults:
        conn.execute(
            """INSERT INTO telegram_recommend_skill
               (skill_id, skill_name, confidence_score, recent_hit_rate, user_fit_tags, prompt_template, evolution_record)
               VALUES (?, ?, ?, 0, ?, '', '初始推荐技能')
               ON CONFLICT(skill_id) DO NOTHING""",
            (skill_id, name, confidence, json.dumps(tags, ensure_ascii=False)),
        )
    if close:
        conn.commit()
        conn.close()


def best_public_agent_context(conn=None) -> dict:
    close = conn is None
    conn = conn or get_conn()
    ensure_recommend_skills(conn)
    row = conn.execute(
        """SELECT a.id, a.display_name, m.race_score, m.win_rate, m.profit_factor, m.style_tag, m.trade_date
           FROM agent_info a
           LEFT JOIN agent_race_metric m ON m.agent_id=a.id
           WHERE a.status IN ('active','paused')
           ORDER BY COALESCE(m.race_score, 0) DESC, a.id ASC LIMIT 1"""
    ).fetchone()
    if not row:
        if close:
            conn.close()
        return {"recommend_memory": read_telegram_memory()}
    summary = get_public_system_summary(row["id"], conn)
    skills = [
        dict(r) for r in conn.execute(
            "SELECT * FROM telegram_recommend_skill ORDER BY confidence_score DESC LIMIT 5"
        ).fetchall()
    ]
    if close:
        conn.close()
    return {
        "agent_id": row["id"],
        "agent_name": row["display_name"],
        "race": dict(row),
        "system": summary,
        "recommend_skills": skills,
        "recommend_memory": read_telegram_memory(),
    }


def compact_trace_text(context: dict) -> str:
    if not context:
        return "暂无可引用的交易员公开体系。"
    race = context.get("race") or {}
    system = context.get("system") or {}
    skills = system.get("skills") or []
    memory = (context.get("recommend_memory") or "").replace("\n", " ")[:180]
    skill_text = "、".join(
        f"{s.get('skill_name')}({float(s.get('confidence_score') or 0):.2f})"
        for s in skills[:3]
    ) or "暂无高置信技能"
    return (
        f"引用交易员: {context.get('agent_name')}；"
        f"赛马分{float(race.get('race_score') or 0):.1f}，"
        f"胜率{float(race.get('win_rate') or 0):.1f}%，风格{race.get('style_tag') or '未标记'}；"
        f"高置信技能: {skill_text}。推荐助手记忆: {memory or '暂无'}。"
    )


def trace_payload(context: dict, item: dict, strategy: str) -> dict:
    system = context.get("system") or {}
    race = context.get("race") or {}
    skills = system.get("skills") or []
    top_skill = skills[0] if skills else {}
    return {
        "source_agent_id": context.get("agent_id"),
        "source_agent_name": context.get("agent_name"),
        "source_section": "交易体系公开摘要",
        "source_summary": compact_trace_text(context),
        "strategy": strategy,
        "matched_stock": {"ts_code": item.get("ts_code"), "name": item.get("name")},
        "skill_id": top_skill.get("skill_id") or "trader_memory_grounding",
        "skill_confidence": float(top_skill.get("confidence_score") or 0.58),
        "system_excerpt": (system.get("system_doc") or "")[:1200],
        "race": dict(race),
    }


def update_recommend_skill_feedback(feedback_type: str, conn=None) -> None:
    close = conn is None
    conn = conn or get_conn()
    ensure_recommend_skills(conn)
    delta = 0.02 if feedback_type == "positive" else -0.03 if feedback_type in ("negative", "risk_too_high") else -0.01
    rows = conn.execute("SELECT skill_id, confidence_score FROM telegram_recommend_skill").fetchall()
    for row in rows:
        old = float(row["confidence_score"] or 0.5)
        new = max(0.1, min(0.9, old + delta))
        conn.execute(
            """UPDATE telegram_recommend_skill
               SET confidence_score=?, evolution_record=?, updated_at=datetime('now')
               WHERE skill_id=?""",
            (new, f"用户反馈 {feedback_type}: {old:.2f}->{new:.2f}", row["skill_id"]),
        )
    if close:
        conn.commit()
        conn.close()
    update_telegram_memory(f"用户反馈 {feedback_type}，推荐技能置信度整体调整 {delta:+.2f}。")


def update_recommend_skill_outcome(delta: float, reason: str, conn=None) -> None:
    close = conn is None
    conn = conn or get_conn()
    ensure_recommend_skills(conn)
    rows = conn.execute("SELECT skill_id, confidence_score FROM telegram_recommend_skill").fetchall()
    for row in rows:
        old = float(row["confidence_score"] or 0.5)
        new = max(0.1, min(0.9, old + delta))
        conn.execute(
            """UPDATE telegram_recommend_skill
               SET confidence_score=?, evolution_record=?, updated_at=datetime('now')
               WHERE skill_id=?""",
            (new, f"{reason}: {old:.2f}->{new:.2f}", row["skill_id"]),
        )
    if close:
        conn.commit()
        conn.close()
    update_telegram_memory(f"后验收益反馈 {reason}，推荐技能置信度整体调整 {delta:+.2f}。")


def recommendation_trace(recommendation_id: int, conn=None) -> dict:
    close = conn is None
    conn = conn or get_conn()
    row = conn.execute("SELECT * FROM telegram_recommend_feedback WHERE id=?", (recommendation_id,)).fetchone()
    if not row:
        if close:
            conn.close()
        return {}
    result = dict(row)
    try:
        result["trace"] = json.loads(result.get("trace_json") or "{}")
    except Exception:
        result["trace"] = {}
    try:
        outcome = conn.execute(
            "SELECT * FROM telegram_recommend_outcome WHERE recommendation_id=?",
            (recommendation_id,),
        ).fetchone()
        result["outcome"] = dict(outcome) if outcome else {}
        eval_row = conn.execute(
            """SELECT * FROM telegram_recommend_eval
               WHERE recommendation_ids LIKE ?
               ORDER BY id DESC LIMIT 1""",
            (f"%{recommendation_id}%",),
        ).fetchone()
        result["eval"] = dict(eval_row) if eval_row else {}
        cost = conn.execute(
            "SELECT * FROM telegram_recommend_cost WHERE eval_id=? ORDER BY id DESC LIMIT 1",
            (result["eval"].get("id") if result.get("eval") else 0,),
        ).fetchone()
        result["cost"] = dict(cost) if cost else {}
    except Exception:
        result.setdefault("outcome", {})
        result.setdefault("eval", {})
        result.setdefault("cost", {})
    if close:
        conn.close()
    return result
