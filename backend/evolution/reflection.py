"""Periodic trading-system reflection documents."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

from backend.config import ROOT_DIR
from backend.db.repository import get_conn
from backend.evolution.memory import SYSTEM_DOC_LIMIT, _compress_with_llm
from backend.evolution.skills import list_skill_index


SYSTEM_DIR = "system"


def maybe_schedule_reflection(agent_id: int, agent_name: str, trade_date: str, conn) -> dict:
    triggers = _reflection_triggers(agent_id, trade_date, conn)
    if not triggers:
        return {"scheduled": False}
    scheduled = []
    for task_type, reason in triggers:
        exists = conn.execute(
            """SELECT id FROM agent_reflection_task
               WHERE agent_id=? AND trade_date=? AND task_type=?""",
            (agent_id, trade_date, task_type),
        ).fetchone()
        if exists:
            continue
        payload = build_reflection_input(agent_id, trade_date, conn)
        cur = conn.execute(
            """INSERT INTO agent_reflection_task
               (agent_id, trade_date, task_type, trigger_reason, input_json)
               VALUES (?, ?, ?, ?, ?)""",
            (agent_id, trade_date, task_type, reason, json.dumps(payload, ensure_ascii=False, default=str)),
        )
        scheduled.append(cur.lastrowid)
    if scheduled:
        conn.commit()
        for task_id in scheduled:
            thread = threading.Thread(target=run_reflection_task, args=(task_id,), daemon=True)
            thread.start()
    return {"scheduled": bool(scheduled), "task_ids": scheduled}


def run_reflection_task(task_id: int) -> dict:
    conn = get_conn()
    task = conn.execute("SELECT * FROM agent_reflection_task WHERE id=?", (task_id,)).fetchone()
    if not task:
        conn.close()
        return {"ok": False, "error": "task not found"}
    conn.execute(
        "UPDATE agent_reflection_task SET status='running', started_at=datetime('now') WHERE id=?",
        (task_id,),
    )
    conn.commit()
    try:
        agent = conn.execute("SELECT display_name FROM agent_info WHERE id=?", (task["agent_id"],)).fetchone()
        payload = json.loads(task["input_json"] or "{}")
        doc = _generate_system_doc(agent["display_name"] if agent else str(task["agent_id"]), task, payload)
        version = f"{task['trade_date']}_{task['task_type']}_{task_id}"
        path = _write_system_doc(task["agent_id"], version, doc, task["trigger_reason"] or "")
        conn.execute(
            """UPDATE agent_reflection_task
               SET status='completed', output_md=?, system_doc_path=?, version=?,
                   completed_at=datetime('now')
               WHERE id=?""",
            (doc, path, version, task_id),
        )
        conn.commit()
        return {"ok": True, "path": path, "version": version}
    except Exception as exc:
        conn.execute(
            """UPDATE agent_reflection_task
               SET status='failed', error=?, completed_at=datetime('now')
               WHERE id=?""",
            (str(exc), task_id),
        )
        conn.commit()
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()


def build_reflection_input(agent_id: int, trade_date: str, conn) -> dict:
    reports = [dict(r) for r in conn.execute(
        """SELECT trade_date, daily_pnl, daily_return, cumulative_return,
                  factor_weight_log, risk_adjust_log
           FROM agent_daily_report
           WHERE agent_id=? AND trade_date<=?
           ORDER BY trade_date DESC LIMIT 20""",
        (agent_id, trade_date),
    ).fetchall()]
    orders = [dict(r) for r in conn.execute(
        """SELECT trade_date, ts_code, stock_name, direction, price, status,
                  fail_reason, failure_attribution, skill_id
           FROM agent_order
           WHERE agent_id=? AND trade_date<=?
           ORDER BY trade_date DESC, id DESC LIMIT 50""",
        (agent_id, trade_date),
    ).fetchall()]
    events = [dict(r) for r in conn.execute(
        """SELECT trade_date, summary, payload_json
           FROM agent_evolution_event
           WHERE agent_id=? AND trade_date<=?
           ORDER BY trade_date DESC, id DESC LIMIT 10""",
        (agent_id, trade_date),
    ).fetchall()]
    race = conn.execute(
        """SELECT * FROM agent_race_metric
           WHERE agent_id=? AND trade_date<=?
           ORDER BY trade_date DESC LIMIT 1""",
        (agent_id, trade_date),
    ).fetchone()
    return {
        "reports": reports,
        "orders": orders,
        "events": events,
        "skills": list_skill_index(agent_id, conn),
        "race": dict(race) if race else {},
        "factor_weight_suggestion": _suggest_factor_weights(agent_id, conn),
    }


def get_public_system_summary(agent_id: int, conn=None) -> dict:
    close = conn is None
    conn = conn or get_conn()
    agent = conn.execute("SELECT display_name FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    current = _current_doc_path(agent_id)
    content = ""
    if os.path.exists(current):
        with open(current, "r", encoding="utf-8") as f:
            content = f.read()
    skills = [
        dict(r) for r in conn.execute(
            """SELECT skill_id, skill_name, confidence_score, recent_fail_rate, market_scene, evolution_record
               FROM agent_evolution_skill
               WHERE agent_id=? AND enabled=1 AND confidence_score>=0.55
               ORDER BY confidence_score DESC LIMIT 5""",
            (agent_id,),
        ).fetchall()
    ]
    metric = conn.execute(
        """SELECT trade_date, race_score, win_rate, profit_factor, style_tag, risk_cap
           FROM agent_race_metric WHERE agent_id=? ORDER BY trade_date DESC LIMIT 1""",
        (agent_id,),
    ).fetchone()
    if close:
        conn.close()
    return {
        "agent_id": agent_id,
        "agent_name": agent["display_name"] if agent else str(agent_id),
        "system_doc": _sanitize_public_doc(content),
        "skills": skills,
        "race_metric": dict(metric) if metric else {},
    }


def list_timeline(agent_id: int, conn, limit: int = 50) -> list[dict]:
    events = [
        {"kind": "daily_evolution", **dict(r)}
        for r in conn.execute(
            """SELECT trade_date, summary, payload_json, created_at
               FROM agent_evolution_event WHERE agent_id=?
               ORDER BY trade_date DESC, id DESC LIMIT ?""",
            (agent_id, limit),
        ).fetchall()
    ]
    tasks = [
        {"kind": "reflection", **dict(r)}
        for r in conn.execute(
            """SELECT id, trade_date, task_type, trigger_reason, status, system_doc_path,
                  version, error, created_at, completed_at
               FROM agent_reflection_task WHERE agent_id=?
               ORDER BY trade_date DESC, id DESC LIMIT ?""",
            (agent_id, limit),
        ).fetchall()
    ]
    return sorted(events + tasks, key=lambda x: (str(x.get("trade_date", "")), str(x.get("created_at", ""))), reverse=True)[:limit]


def list_versions(agent_id: int) -> list[dict]:
    folder = os.path.join(_system_dir(agent_id), "versions")
    if not os.path.exists(folder):
        return []
    result = []
    for name in sorted(os.listdir(folder), reverse=True):
        if name.endswith(".md"):
            path = os.path.join(folder, name)
            result.append({"version": name[:-3], "path": path, "updated_at": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")})
    return result


def _reflection_triggers(agent_id: int, trade_date: str, conn) -> list[tuple[str, str]]:
    triggers = []
    rows = conn.execute(
        """SELECT trade_date FROM agent_daily_report
           WHERE agent_id=? ORDER BY trade_date DESC LIMIT 5""",
        (agent_id,),
    ).fetchall()
    if len(rows) >= 5 and str(rows[0]["trade_date"]) == str(trade_date):
        triggers.append(("weekly", "最近5个交易日完成，触发周度体系反思"))
    if str(trade_date).endswith(("28", "29", "30", "31")):
        triggers.append(("monthly", "月末窗口，触发月度体系反思"))
    recent = conn.execute(
        """SELECT status FROM agent_order
           WHERE agent_id=? AND skill_id IS NOT NULL AND skill_id!=''
           ORDER BY trade_date DESC, id DESC LIMIT 10""",
        (agent_id,),
    ).fetchall()
    if len(recent) >= 5:
        fail_rate = sum(1 for r in recent if r["status"] in ("expired", "cancelled")) / len(recent)
        if fail_rate >= 0.4:
            triggers.append(("event", f"近{len(recent)}笔订单失败率{fail_rate:.0%}，触发事件反思"))
    same_direction_expired = conn.execute(
        """SELECT direction, ts_code, stock_name, price, fail_reason
           FROM agent_order
           WHERE agent_id=? AND status IN ('expired','cancelled')
           ORDER BY trade_date DESC, id DESC LIMIT 3""",
        (agent_id,),
    ).fetchall()
    if len(same_direction_expired) >= 3:
        directions = {r["direction"] for r in same_direction_expired}
        if len(directions) == 1:
            direction = next(iter(directions))
            triggers.append((
                f"event_{direction}_expiry",
                f"连续3笔{direction}方向订单过期/取消，触发价格与执行条件事件反思",
            ))
    latest_report = conn.execute(
        """SELECT trade_date, daily_return FROM agent_daily_report
           WHERE agent_id=? AND trade_date<=?
           ORDER BY trade_date DESC LIMIT 1""",
        (agent_id, trade_date),
    ).fetchone()
    if latest_report and float(latest_report["daily_return"] or 0) <= -5:
        triggers.append((
            "event_drawdown",
            f"单日回撤{float(latest_report['daily_return'] or 0):.2f}%超过5%，触发风控事件反思",
        ))
    latest_event = conn.execute(
        """SELECT payload_json FROM agent_evolution_event
           WHERE agent_id=? AND trade_date<=? AND event_type='daily_evolution'
           ORDER BY trade_date DESC, id DESC LIMIT 1""",
        (agent_id, trade_date),
    ).fetchone()
    if latest_event:
        payload = _loads(latest_event["payload_json"], {})
        jumps = []
        for update in payload.get("skill_updates") or []:
            old = float(update.get("old_confidence") or 0)
            new = float(update.get("new_confidence") or 0)
            if abs(new - old) >= 0.2:
                jumps.append(f"{update.get('skill_id')} {old:.2f}->{new:.2f}")
        if jumps:
            triggers.append((
                "event_skill_confidence",
                "技能置信度突变超过0.20: " + ", ".join(jumps[:3]),
            ))
    event_triggers = [item for item in triggers if item[0].startswith("event")]
    periodic_triggers = [item for item in triggers if not item[0].startswith("event")]
    return (event_triggers + periodic_triggers)[:3]


def _loads(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _suggest_factor_weights(agent_id: int, conn) -> dict:
    rows = conn.execute(
        """SELECT daily_return, factor_weight_log
           FROM agent_daily_report
           WHERE agent_id=? AND factor_weight_log IS NOT NULL AND factor_weight_log!=''
           ORDER BY trade_date DESC LIMIT 20""",
        (agent_id,),
    ).fetchall()
    scores = {}
    samples = 0
    for row in rows:
        weights = _loads(row["factor_weight_log"], {})
        if not isinstance(weights, dict):
            continue
        daily_return = max(-5.0, min(5.0, float(row["daily_return"] or 0)))
        for key, value in weights.items():
            if str(key).startswith("_") or not isinstance(value, (int, float)):
                continue
            scores[key] = scores.get(key, 0.0) + float(value) * daily_return
        samples += 1
    if samples < 5:
        return {"sample_size": samples, "status": "observing", "message": "样本不足5日，暂不固化权重建议"}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return {
        "sample_size": samples,
        "top_positive_factor": ranked[0][0] if ranked else "",
        "top_negative_factor": ranked[-1][0] if ranked else "",
        "scores": {k: round(v, 4) for k, v in ranked},
        "message": "建议周度反思重点解释正贡献因子是否应上调、负贡献因子是否应降权。",
    }


def _generate_system_doc(agent_name: str, task, payload: dict) -> str:
    prompt_payload = json.dumps(payload, ensure_ascii=False, default=str)[:18000]
    system_prompt = (
        "你是A股交易体系复盘官。请基于给定的已完成交易、失败归因、技能和赛马数据，"
        "输出结构化《交易体系构建文档》。必须只使用已完成事实，不暴露未成交计划。"
    )
    user = f"""Agent: {agent_name}
任务: {task['task_type']}
触发原因: {task['trigger_reason']}

数据:
{prompt_payload}

请固定输出五章：
1. 核心投资哲学
2. 选股与择时标准
3. 风控与仓位管理
4. 能力圈边界
5. 因子权重调整建议

每章包含可执行规则和证据摘要。"""
    try:
        from backend.llm.client import chat
        doc = chat(system_prompt, user, temperature=0.2)
        if doc and len(doc.strip()) >= 200:
            return doc.strip()
    except Exception as exc:
        return _fallback_doc(agent_name, task, payload, str(exc))
    return _fallback_doc(agent_name, task, payload, "LLM输出为空")


def _fallback_doc(agent_name: str, task, payload: dict, error: str = "") -> str:
    skills = payload.get("skills") or []
    race = payload.get("race") or {}
    orders = payload.get("orders") or []
    failed = [o for o in orders if o.get("status") in ("expired", "cancelled")]
    return f"""# {agent_name} 交易体系构建文档

> 版本由 {task['task_type']} 反思生成。触发原因：{task['trigger_reason']}。

## 1. 核心投资哲学

当前体系以已验证技能为核心，优先使用高置信技能，避免重复执行近期失败率高的挂单模式。赛马标签：{race.get('style_tag', '暂无')}，赛马分：{race.get('race_score', '暂无')}。

## 2. 选股与择时标准

高置信技能：{', '.join(f"{s.get('skill_id')}({float(s.get('confidence_score') or 0):.2f})" for s in skills[:5]) or '暂无'}。入场前必须确认近期失败原因，尤其是限价未触达、开盘抢入失败和非原子换仓顺序风险。

## 3. 风控与仓位管理

赛马指标仅作为评价与提示词输入，不做代码层面的强制仓位限制。若连续亏损或最大回撤扩大，Agent 应自主解释是否降低交易频率、仓位或激进技能优先级。

## 4. 能力圈边界

近期失败订单 {len(failed)} 笔。对未触达限价、成交顺序依赖卖出资金、以及高波动开盘抢入场景保持谨慎。

## 5. 因子权重调整建议

当前建议：{json.dumps(payload.get('factor_weight_suggestion') or {}, ensure_ascii=False)}。周度反思只给出建议，实际每日权重仍由历史收益回看、风格先验和当日风控共同决定。

<!-- fallback_reason: {error} -->
"""


def _write_system_doc(agent_id: int, version: str, doc: str, reason: str) -> str:
    folder = _system_dir(agent_id)
    versions = os.path.join(folder, "versions")
    os.makedirs(versions, exist_ok=True)
    version_path = os.path.join(versions, f"{version}.md")
    current_path = _current_doc_path(agent_id)
    doc = _compress_with_llm(doc, SYSTEM_DOC_LIMIT, "system/current.md")
    with open(version_path, "w", encoding="utf-8") as f:
        f.write(doc.rstrip() + "\n")
    with open(current_path, "w", encoding="utf-8") as f:
        f.write(doc.rstrip() + "\n")
    with open(os.path.join(folder, "changelog.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"version": version, "reason": reason, "created_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
    return current_path


def _system_dir(agent_id: int) -> str:
    return os.path.join(ROOT_DIR, "agent_memory", str(agent_id), SYSTEM_DIR)


def _current_doc_path(agent_id: int) -> str:
    return os.path.join(_system_dir(agent_id), "current.md")


def _sanitize_public_doc(content: str) -> str:
    if not content:
        return "暂无交易体系文档。"
    lines = []
    for line in content.splitlines():
        if any(token in line for token in ("条件单", "pending", "未成交计划", "明日买入")):
            continue
        lines.append(line)
    return "\n".join(lines)[:5000]
