"""Daily evolution orchestration."""

from __future__ import annotations

import json
import os
from datetime import datetime

from backend.config import LOGS_DIR
from backend.evolution.memory import snapshot_memory, update_memory
from backend.evolution.minute_replay import build_intraday_replay
from backend.evolution.skills import ensure_default_skills, list_skill_index, update_skill_confidence


def prepare_evolution_context(agent_id: int, agent_name: str, trade_date: str, conn) -> dict:
    ensure_default_skills(agent_id, agent_name, conn)
    memory_snapshot = snapshot_memory(agent_id, trade_date)
    skills = list_skill_index(agent_id, conn)
    last_event = conn.execute(
        """SELECT trade_date, summary, payload_json
           FROM agent_evolution_event
           WHERE agent_id=? AND event_type='daily_evolution'
           ORDER BY trade_date DESC, id DESC LIMIT 1""",
        (agent_id,),
    ).fetchone()
    previous = {}
    if last_event:
        previous = {
            "trade_date": last_event["trade_date"],
            "summary": last_event["summary"],
            "payload": _loads(last_event["payload_json"], {}),
        }
    system_doc = ""
    try:
        from backend.evolution.reflection import get_public_system_summary
        system_doc = (get_public_system_summary(agent_id, conn).get("system_doc") or "")[:6000]
    except Exception:
        system_doc = ""
    return {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "trade_date": trade_date,
        "memory_snapshot": memory_snapshot,
        "skills": skills,
        "system_doc": system_doc,
        "previous_evolution": previous,
    }


def format_evolution_prompt(context: dict | None) -> str:
    if not context:
        return "暂无进化上下文。"
    memory = context.get("memory_snapshot") or {}
    skills = context.get("skills") or []
    skill_lines = []
    for skill in skills[:6]:
        params = skill.get("dynamic_params") or {}
        invalid = "、".join(skill.get("invalid_scene") or [])
        skill_lines.append(
            f"- {skill['skill_id']}({skill['skill_name']}): 置信度{float(skill.get('confidence_score') or 0):.2f}，"
            f"失败率{float(skill.get('recent_fail_rate') or 0):.2f}，参数{json.dumps(params, ensure_ascii=False)}，"
            f"失效场景[{invalid}]"
        )
    previous = context.get("previous_evolution") or {}
    agent_config = context.get("agent_config") or {}
    stage_prompts = agent_config.get("stage_prompts") or {}
    stage_lines = []
    for key, label in (
        ("market_scan", "行情感知"),
        ("stock_selection", "选股择时"),
        ("risk_control", "风控仓位"),
        ("order_plan", "订单规划"),
        ("reflection", "复盘反思"),
    ):
        value = (stage_prompts.get(key) or "").strip()
        if value:
            stage_lines.append(f"- {label}: {value}")
    allowed_tools = agent_config.get("allowed_tools") or []
    preferred_strategies = agent_config.get("preferred_strategies") or []
    style_prompt = (agent_config.get("style_prompt") or "").strip()
    board_permissions = agent_config.get("board_permissions") or {}
    return "\n".join([
        "## 进化记忆快照（今日决策必须基于此快照，盘后才允许更新）",
        "### 客观规律 trade_fact",
        memory.get("trade_fact", ""),
        "### 主观偏好 trade_prefer",
        memory.get("trade_prefer", ""),
        "### 近3日极端行情",
        memory.get("short_ring", ""),
        "## 交易体系文档摘要（固定大小压缩版）",
        context.get("system_doc") or "暂无体系文档。",
        "## 可调用交易技能索引",
        "\n".join(skill_lines) if skill_lines else "暂无技能，按默认谨慎模式。",
        "## Agent配置约束",
        f"- Agent风格提示词: {style_prompt or '未配置'}",
        f"- 优先选股策略: {', '.join(preferred_strategies) if preferred_strategies else '未配置，按风格自主选择'}",
        f"- 严格工具白名单: {', '.join(allowed_tools) if allowed_tools else '未配置，允许默认工具集'}",
        f"- 买入板块权限: {json.dumps(board_permissions, ensure_ascii=False)}",
        "- 赛马指标仅作为评价和提示词输入，不会触发代码层面的仓位强制限制。",
        "\n".join(stage_lines) if stage_lines else "- 阶段提示词未配置。",
        "## 上次进化结果",
        previous.get("summary", "无"),
        "## 决策要求",
        "- 每笔订单尽量填写 skill_id、skill_confidence、evolution_mark。",
        "- 如计划先卖A再买B，必须明确这不是原子操作；买单触达早于卖单时可能只卖不买或买不成。",
        "- 优先调用 get_strategy_param_schema 查看策略可调参数，再用 search_stocks_by_strategy 的 params_json 定制敏感值。",
        "- 打板与多头均线右侧趋势并重；决策前参考 get_market_breadth/get_sector_temperature 判断是否可以加大进攻仓位。",
        "- 如果市场 risk-on 且热点明确，过度低仓位会被视为复盘反思项；如果 risk-off，轻仓或空仓需要说明证据。",
    ])


def run_post_daily_evolution(agent_id: int, agent_name: str, trade_date: str,
                             trades: list[dict], decision, daily_metrics: dict,
                             conn) -> dict:
    expired_rows = conn.execute(
        """SELECT id, ts_code, stock_name, direction, quantity, price, status, fail_reason, skill_id
           FROM agent_order
           WHERE agent_id=? AND trade_date=? AND status IN ('expired','cancelled')""",
        (agent_id, trade_date),
    ).fetchall()
    expired = [dict(r) for r in expired_rows]
    replay = build_intraday_replay(agent_id, trade_date, conn)
    skill_updates = update_skill_confidence(agent_id, trade_date, conn)

    factor_weight_log = json.dumps(_factor_weights(agent_id, agent_name, daily_metrics, skill_updates, conn), ensure_ascii=False)
    risk_adjust_log = _risk_log(daily_metrics, expired, skill_updates, replay)
    memory_payload = {
        "trades": trades,
        "expired_orders": expired,
        "skill_updates": skill_updates,
        "intraday_replay": replay,
        "risk_adjust_log": risk_adjust_log,
        "market_scene": replay.get("summary", ""),
    }
    memory_update = update_memory(agent_id, trade_date, memory_payload)
    payload = {
        "factor_weight_log": _loads(factor_weight_log, {}),
        "risk_adjust_log": risk_adjust_log,
        "intraday_replay": replay,
        "skill_updates": skill_updates,
        "memory_update": memory_update,
    }
    conn.execute(
        """INSERT INTO agent_evolution_event (agent_id, trade_date, event_type, summary, payload_json)
           VALUES (?, ?, 'daily_evolution', ?, ?)""",
        (agent_id, trade_date, risk_adjust_log, json.dumps(payload, ensure_ascii=False, default=str)),
    )
    _write_evolution_log(agent_name, trade_date, payload)
    return payload


def _factor_weights(agent_id: int, agent_name: str, metrics: dict, skill_updates: list[dict], conn) -> dict:
    text = agent_name or ""
    if any(token in text for token in ("追高", "打板", "情绪")):
        weights = {"emotion": 0.50, "technical": 0.25, "capital": 0.15, "policy": 0.10}
    else:
        weights = {"policy": 0.25, "technical": 0.30, "capital": 0.25, "emotion": 0.20}
    source = "style_default"
    historical = _learn_factor_weights_from_history(agent_id, weights, conn)
    if historical:
        weights = historical["weights"]
        source = historical["source"]
    daily_return = float(metrics.get("daily_return") or 0)
    if daily_return < -2:
        weights["capital"] = round(weights.get("capital", 0) + 0.05, 2)
        weights["emotion"] = round(max(0.10, weights.get("emotion", 0) - 0.03), 2)
    if skill_updates and any(u.get("recent_fail_rate", 0) > 0.4 for u in skill_updates):
        weights["technical"] = round(max(0.10, weights.get("technical", 0) - 0.03), 2)
        weights["capital"] = round(weights.get("capital", 0) + 0.03, 2)
    weights = _normalize_weights(weights)
    weights["_meta"] = {
        "source": source,
        "note": "历史收益按因子权重加权回看后微调；仍保留风格先验和当日风控修正。",
    }
    return weights


def _learn_factor_weights_from_history(agent_id: int, base_weights: dict, conn) -> dict | None:
    rows = conn.execute(
        """SELECT daily_return, factor_weight_log
           FROM agent_daily_report
           WHERE agent_id=? AND factor_weight_log IS NOT NULL AND factor_weight_log!=''
           ORDER BY trade_date DESC LIMIT 20""",
        (agent_id,),
    ).fetchall()
    samples = []
    for row in rows:
        weights = _loads(row["factor_weight_log"], {})
        if not isinstance(weights, dict):
            continue
        weights = {k: float(v) for k, v in weights.items() if k in base_weights and isinstance(v, (int, float))}
        if weights:
            samples.append((float(row["daily_return"] or 0), weights))
    if len(samples) < 5:
        return None
    scores = {k: 0.0 for k in base_weights}
    total_abs_return = 0.0
    for daily_return, weights in samples:
        clipped_return = max(-5.0, min(5.0, daily_return))
        total_abs_return += abs(clipped_return)
        for key in scores:
            scores[key] += float(weights.get(key, 0)) * clipped_return
    if total_abs_return <= 0:
        return None
    learned = dict(base_weights)
    for key, score in scores.items():
        tilt = max(-0.06, min(0.06, score / total_abs_return * 0.12))
        learned[key] = round(max(0.08, min(0.60, float(base_weights.get(key, 0)) + tilt)), 4)
    return {"weights": _normalize_weights(learned), "source": f"history_fit_{len(samples)}d"}


def _normalize_weights(weights: dict) -> dict:
    keys = [k for k, v in weights.items() if not str(k).startswith("_") and isinstance(v, (int, float))]
    total = sum(max(0.0, float(weights[k])) for k in keys)
    if total <= 0:
        return {k: 0 for k in keys}
    normalized = {k: round(max(0.0, float(weights[k])) / total, 4) for k in keys}
    drift = round(1.0 - sum(normalized.values()), 4)
    if keys and abs(drift) >= 0.0001:
        normalized[keys[0]] = round(normalized[keys[0]] + drift, 4)
    return normalized


def _risk_log(metrics: dict, expired: list[dict], updates: list[dict], replay: dict) -> str:
    parts = []
    daily_return = float(metrics.get("daily_return") or 0)
    position_ratio = float(metrics.get("position_ratio") or metrics.get("total_position_ratio") or 0)
    if daily_return <= -3:
        parts.append(f"账户当日回撤{daily_return:.2f}%，次日降低激进技能优先级。")
    if expired:
        parts.append(f"当日失败/过期订单{len(expired)}笔，复盘价格与开盘抢入条件。")
    if 0 < position_ratio < 0.10 and daily_return >= 0:
        parts.append("当日仓位低于10%，若市场宽度和板块温度为risk-on，需反思是否过度保守错失进攻窗口。")
    if updates:
        changed = ", ".join(f"{u['skill_id']} {u['old_confidence']:.2f}->{u['new_confidence']:.2f}" for u in updates[:4])
        parts.append("技能置信度更新: " + changed)
    if replay.get("summary"):
        parts.append("分钟复盘: " + replay["summary"])
    return " ".join(parts) or "未触发风控收紧，仅保留常规技能置信度。"


def _write_evolution_log(agent_name: str, trade_date: str, payload: dict) -> None:
    folder = os.path.join(LOGS_DIR, trade_date, agent_name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "evolution_evolve.log")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n")
        f.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        f.write("\n")


def _loads(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default
