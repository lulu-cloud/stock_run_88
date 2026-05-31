"""Adaptive skill registry and confidence updates."""

from __future__ import annotations

import inspect
import json
from typing import Any

from backend.db.repository import get_conn


DEFAULT_SKILLS = {
    "momentum": [
        {
            "skill_id": "momentum_hunt",
            "skill_name": "情绪周期龙头追涨",
            "market_scene": "连板高度抬升、赚钱效应扩散、强势板块集中",
            "confidence_score": 0.66,
            "recent_fail_rate": 0.0,
            "dynamic_params": {
                "max_single_position": 0.15,
                "hard_stop_loss": 0.03,
                "min_turnover_ratio": 1.4,
                "min_limit_up_days": 2,
                "lookback_days": 8,
                "avoid_high_daily_limit": True,
            },
            "invalid_scene": ["大盘跌破MA20", "连板高度<2", "集体炸板潮"],
            "evolution_record": "初始短线情绪技能。",
        },
        {
            "skill_id": "risk_exit",
            "skill_name": "退潮风险压制与离场",
            "market_scene": "炸板率上升、持仓回撤扩大、主线断层",
            "confidence_score": 0.60,
            "recent_fail_rate": 0.0,
            "dynamic_params": {"max_total_position": 0.35, "profit_take_pct": 0.06, "stop_loss_pct": 0.03},
            "invalid_scene": ["指数放量突破", "持仓连续强势封板"],
            "evolution_record": "初始风险压制技能。",
        },
        {
            "skill_id": "ma_right_side_attack",
            "skill_name": "多头均线右侧进攻",
            "market_scene": "市场宽度转强、板块温度集中、趋势股回踩MA5/10/20企稳",
            "confidence_score": 0.62,
            "recent_fail_rate": 0.0,
            "dynamic_params": {
                "max_single_position": 0.16,
                "risk_on_total_position": 0.65,
                "neutral_total_position": 0.42,
                "pullback_ma_periods": [5, 10, 20],
                "require_sector_temperature": True,
            },
            "invalid_scene": ["市场宽度risk_off", "板块温度退潮", "跌破MA20"],
            "evolution_record": "补充打板之外的右侧趋势进攻技能。",
        },
    ],
    "balanced": [
        {
            "skill_id": "balanced_factor",
            "skill_name": "全因子均衡选股",
            "market_scene": "指数震荡或温和上行，政策、技术、资金共同确认",
            "confidence_score": 0.62,
            "recent_fail_rate": 0.0,
            "dynamic_params": {
                "max_single_position": 0.12,
                "policy_weight": 0.25,
                "technical_weight": 0.30,
                "capital_weight": 0.25,
                "sentiment_weight": 0.20,
            },
            "invalid_scene": ["系统性暴跌", "流动性急剧萎缩"],
            "evolution_record": "初始多因子技能。",
        },
        {
            "skill_id": "position_rotate",
            "skill_name": "非原子换仓与资金顺序控制",
            "market_scene": "需要先卖出持仓再买入新标的",
            "confidence_score": 0.58,
            "recent_fail_rate": 0.0,
            "dynamic_params": {"max_rotate_ratio": 0.30, "require_sell_before_buy": True},
            "invalid_scene": ["持仓一字跌停", "目标股一字涨停"],
            "evolution_record": "初始换仓顺序技能。",
        },
        {
            "skill_id": "ma_hot_sector_pick",
            "skill_name": "多头均线与热点板块共振",
            "market_scene": "均线发散、回踩支撑、政策或板块温度确认",
            "confidence_score": 0.62,
            "recent_fail_rate": 0.0,
            "dynamic_params": {
                "max_single_position": 0.14,
                "risk_on_total_position": 0.60,
                "neutral_total_position": 0.42,
                "pullback_ma_periods": [5, 10, 20],
                "factor_mix": {"technical": 0.35, "sector_heat": 0.25, "policy": 0.20, "fundamental": 0.20},
            },
            "invalid_scene": ["市场宽度risk_off", "候选股与热门板块无关", "成交额不足"],
            "evolution_record": "补充多头均线、热点和基本面二次筛选技能。",
        },
    ],
}


def _style_key(agent: dict | None = None, agent_name: str = "") -> str:
    text = " ".join([
        str((agent or {}).get("agent_type", "")),
        str((agent or {}).get("name", "")),
        str((agent or {}).get("display_name", "")),
        agent_name or "",
    ])
    if any(token in text for token in ("追高", "打板", "momentum", "情绪")):
        return "momentum"
    return "balanced"


def ensure_default_skills(agent_id: int, agent_name: str = "", conn=None) -> None:
    close = conn is None
    conn = conn or get_conn()
    agent = conn.execute("SELECT * FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    style = _style_key(dict(agent) if agent else None, agent_name)
    for skill in DEFAULT_SKILLS[style]:
        conn.execute(
            """INSERT INTO agent_evolution_skill
               (agent_id, skill_id, skill_name, market_scene, confidence_score, recent_fail_rate,
                dynamic_params, invalid_scene, evolution_record, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(agent_id, skill_id) DO NOTHING""",
            (
                agent_id,
                skill["skill_id"],
                skill["skill_name"],
                skill["market_scene"],
                skill["confidence_score"],
                skill["recent_fail_rate"],
                json.dumps(skill["dynamic_params"], ensure_ascii=False),
                json.dumps(skill["invalid_scene"], ensure_ascii=False),
                skill["evolution_record"],
            ),
        )
    if close:
        conn.commit()
        conn.close()


def list_skill_index(agent_id: int, conn=None) -> list[dict]:
    close = conn is None
    conn = conn or get_conn()
    rows = conn.execute(
        """SELECT skill_id, skill_name, market_scene, confidence_score, recent_fail_rate,
                  dynamic_params, invalid_scene, evolution_record, enabled
           FROM agent_evolution_skill
           WHERE agent_id=? AND enabled=1
           ORDER BY confidence_score DESC, skill_id""",
        (agent_id,),
    ).fetchall()
    if close:
        conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["dynamic_params"] = _loads(item.get("dynamic_params"), {})
        item["invalid_scene"] = _loads(item.get("invalid_scene"), [])
        result.append(item)
    return result


def get_skill(agent_id: int, skill_id: str, conn=None) -> dict:
    close = conn is None
    conn = conn or get_conn()
    row = conn.execute(
        "SELECT * FROM agent_evolution_skill WHERE agent_id=? AND skill_id=?",
        (agent_id, skill_id),
    ).fetchone()
    if close:
        conn.close()
    if not row:
        return {}
    item = dict(row)
    item["dynamic_params"] = _loads(item.get("dynamic_params"), {})
    item["invalid_scene"] = _loads(item.get("invalid_scene"), [])
    return item


def update_skill_confidence(agent_id: int, trade_date: str, conn) -> list[dict]:
    rows = conn.execute(
        """SELECT id, skill_id, status, failure_attribution
           FROM agent_order
           WHERE agent_id=? AND skill_id IS NOT NULL AND skill_id!=''
           ORDER BY trade_date DESC, id DESC
           LIMIT 80""",
        (agent_id,),
    ).fetchall()
    by_skill: dict[str, list[dict]] = {}
    for row in rows:
        by_skill.setdefault(row["skill_id"], []).append(dict(row))

    updates: list[dict] = []
    for skill_id, orders in by_skill.items():
        sample = orders[:10]
        if not sample:
            continue
        failures = sum(1 for o in sample if o.get("status") in ("expired", "cancelled"))
        fail_rate = failures / len(sample)
        recent_win_rate = 1 - fail_rate
        long_fail_rate = _ema_fail_rate(orders)
        long_win_rate = 1 - long_fail_rate
        row = conn.execute(
            "SELECT confidence_score, dynamic_params FROM agent_evolution_skill WHERE agent_id=? AND skill_id=?",
            (agent_id, skill_id),
        ).fetchone()
        if not row:
            continue
        old_conf = float(row["confidence_score"] or 0.5)
        params = _loads(row["dynamic_params"], {})
        prev_ema = float((params.get("skill_ema") or {}).get("win_rate", old_conf) or old_conf)
        ema_win_rate = round(0.72 * prev_ema + 0.28 * long_win_rate, 4)
        new_conf = max(0.1, min(0.92, 0.55 * old_conf + 0.25 * recent_win_rate + 0.20 * ema_win_rate))
        params["skill_ema"] = {
            "win_rate": ema_win_rate,
            "long_fail_rate": round(long_fail_rate, 4),
            "sample_size": len(orders),
            "updated_at": trade_date,
        }
        if fail_rate >= 0.4:
            if "max_single_position" in params:
                params["max_single_position"] = round(max(0.05, float(params["max_single_position"]) * 0.85), 4)
            if "hard_stop_loss" in params:
                params["hard_stop_loss"] = round(max(0.015, float(params["hard_stop_loss"]) * 0.9), 4)
            record = f"{trade_date}: 近10笔失败率{fail_rate:.2f}，EMA长期失败率{long_fail_rate:.2f}，收紧仓位和止损参数。"
        elif fail_rate <= 0.15 and len(sample) >= 5:
            if "max_single_position" in params:
                params["max_single_position"] = round(min(0.20, float(params["max_single_position"]) * 1.08), 4)
            if "risk_on_total_position" in params:
                params["risk_on_total_position"] = round(min(0.70, float(params["risk_on_total_position"]) * 1.04), 4)
            record = f"{trade_date}: 近10笔表现稳定，EMA长期失败率{long_fail_rate:.2f}，轻微上调技能置信度。"
        else:
            record = f"{trade_date}: 近10笔失败率{fail_rate:.2f}，EMA长期失败率{long_fail_rate:.2f}，保持参数，仅更新置信度。"
        conn.execute(
            """UPDATE agent_evolution_skill
               SET confidence_score=?, recent_fail_rate=?, dynamic_params=?,
                   evolution_record=?, updated_at=datetime('now')
               WHERE agent_id=? AND skill_id=?""",
            (new_conf, fail_rate, json.dumps(params, ensure_ascii=False), record, agent_id, skill_id),
        )
        updates.append({
            "skill_id": skill_id,
            "old_confidence": old_conf,
            "new_confidence": new_conf,
            "recent_fail_rate": fail_rate,
            "ema_win_rate": ema_win_rate,
            "long_fail_rate": long_fail_rate,
            "record": record,
        })
    return updates


def _ema_fail_rate(orders: list[dict], alpha: float = 0.18) -> float:
    """Calculate a long-horizon failure EMA over most-recent-first orders."""
    ema = None
    for order in reversed(orders):
        failed = 1.0 if order.get("status") in ("expired", "cancelled") else 0.0
        ema = failed if ema is None else alpha * failed + (1 - alpha) * ema
    return float(ema or 0.0)


def strategy_param_schema(strategy_name: str = "") -> dict[str, Any]:
    import backend.strategies  # noqa: F401 - import registers builtin strategies
    from backend.strategies.registry import StrategyRegistry

    names = [strategy_name] if strategy_name else StrategyRegistry.list_all()
    schema: dict[str, Any] = {}
    for name in names:
        cls = StrategyRegistry.get(name)
        if not cls:
            continue
        sig = inspect.signature(cls.__init__)
        params = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            params[param_name] = {
                "default": None if param.default is inspect._empty else param.default,
                "annotation": "" if param.annotation is inspect._empty else str(param.annotation),
            }
        schema[name] = {
            "description": getattr(cls, "description", ""),
            "recommended_lookback": getattr(cls, "recommended_lookback", 60),
            "params": params,
        }
    return schema


def _loads(value: Any, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default
