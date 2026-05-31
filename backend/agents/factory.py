"""Agent 工厂 — 动态管理 Agent 实例"""

import json
import sqlite3
from typing import Optional
from backend.config import DATABASE_PATH, INITIAL_CAPITAL
from backend.db.repository import get_conn
from backend.agents.tools import get_tool_catalog


DEFAULT_STAGE_PROMPTS = {
    "market_scan": "先判断大盘、情绪周期、板块强弱，再决定是否降低交易频率。",
    "stock_selection": "优先使用配置的选股策略；若候选与风格不匹配，需要说明放弃原因。",
    "risk_control": "参考赛马指标、技能置信度和失败订单记录，自主决定仓位；系统不做盈亏驱动的强制仓位限制。",
    "order_plan": "挂单价必须先计算涨跌幅并校验；换仓必须说明非原子顺序风险。",
    "reflection": "复盘时把已验证规律写入记忆，删除没有证据的判断。",
}

DEFAULT_STYLE_PROMPTS = {
    "chaser": (
        "追高打板情绪猎手：偏短线强势与情绪周期，允许研究连板、高开、主线加速和开盘抢入。"
        "必须严格解释情绪周期、封板质量、换手、炸板风险和次日离场条件。"
    ),
    "autonomous": (
        "全因子自主决策交易者：综合政策、基本面、技术、资金与情绪，不简单复制追高打板候选。"
        "需要说明与其他风格的差异化理由，仓位更分散，避免单一题材过度集中。"
    ),
    "custom": (
        "自定义交易员：按前端配置的策略偏好、工具白名单和阶段提示词执行。"
        "若配置不足，采用稳健均衡风格并清楚说明假设。"
    ),
    "user_style": (
        "用户风格交易员：以用户写入的原始交易策略为最高风格锚点，在指定股票池内模拟执行。"
        "进化系统只能补充和修正执行细节，不能覆盖用户原始策略；若允许池外探索，必须解释脱离股票池的理由。"
    ),
}


def _clamp_int(value, default: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = default
    return max(min_value, min(max_value, number))


def _clamp_float(value, default: float, min_value: float, max_value: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return max(min_value, min(max_value, number))


def default_risk_config(agent_type: str = "custom", strategy_ids: str = "") -> dict:
    preferred = [s.strip() for s in str(strategy_ids or "").split(",") if s.strip()]
    return {
        "max_position_count": 5,
        "max_daily_loss": 0.05,
        "reasoning_effort": "high",
        "max_tool_turns": 8,
        "style_prompt": DEFAULT_STYLE_PROMPTS.get(agent_type, DEFAULT_STYLE_PROMPTS["custom"]),
        "preferred_strategies": preferred,
        "allowed_tools": [item["name"] for item in get_tool_catalog()],
        "board_permission_mode": "auto",
        "board_permissions": {
            "main_sme": True,
            "chinext": False,
            "star": False,
            "bj": False,
        },
        "stock_pool_enabled": False,
        "allow_out_of_pool": False,
        "user_strategy_original": "",
        "stage_prompts": DEFAULT_STAGE_PROMPTS,
    }


def normalize_risk_config(agent_type: str = "custom", strategy_ids: str = "", risk_config: dict = None) -> dict:
    """Merge and validate user-editable Agent config before persisting."""
    base = default_risk_config(agent_type, strategy_ids)
    incoming = risk_config or {}
    merged = {**base, **incoming}
    merged["max_position_count"] = _clamp_int(merged.get("max_position_count"), 5, 1, 20)
    merged["max_daily_loss"] = _clamp_float(merged.get("max_daily_loss"), 0.05, 0.0, 0.5)
    merged["max_tool_turns"] = _clamp_int(merged.get("max_tool_turns"), 8, 1, 50)
    if merged.get("reasoning_effort") not in {"high", "max"}:
        merged["reasoning_effort"] = "high"
    if merged.get("board_permission_mode") not in {"auto", "manual"}:
        merged["board_permission_mode"] = "auto"

    allowed_names = {item["name"] for item in get_tool_catalog()}
    configured_tools = merged.get("allowed_tools")
    if isinstance(configured_tools, list):
        merged["allowed_tools"] = [str(name) for name in configured_tools if str(name) in allowed_names]
    else:
        merged["allowed_tools"] = list(allowed_names)
    if not merged["allowed_tools"]:
        merged["allowed_tools"] = list(allowed_names)

    default_boards = base["board_permissions"]
    raw_boards = incoming.get("board_permissions") if isinstance(incoming, dict) else {}
    merged["board_permissions"] = {
        key: bool((raw_boards or {}).get(key, default_value))
        for key, default_value in default_boards.items()
    }

    raw_stage_prompts = incoming.get("stage_prompts") if isinstance(incoming, dict) else {}
    merged["stage_prompts"] = {
        key: str((raw_stage_prompts or {}).get(key, default_value) or "")[:2000]
        for key, default_value in DEFAULT_STAGE_PROMPTS.items()
    }
    if not str(merged.get("style_prompt") or "").strip():
        merged["style_prompt"] = DEFAULT_STYLE_PROMPTS.get(agent_type, DEFAULT_STYLE_PROMPTS["custom"])
    else:
        merged["style_prompt"] = str(merged.get("style_prompt"))[:4000]
    if isinstance(merged.get("preferred_strategies"), list):
        merged["preferred_strategies"] = [str(s).strip() for s in merged["preferred_strategies"] if str(s).strip()]
    else:
        merged["preferred_strategies"] = [s.strip() for s in str(strategy_ids or "").split(",") if s.strip()]
    merged["stock_pool_enabled"] = bool(merged.get("stock_pool_enabled"))
    merged["allow_out_of_pool"] = bool(merged.get("allow_out_of_pool"))
    merged["user_strategy_original"] = str(merged.get("user_strategy_original") or "")[:12000]
    return merged


def _normalize_stock_code(ts_code: str) -> str:
    text = str(ts_code or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        return text
    if text.startswith("6"):
        return f"{text}.SH"
    return f"{text}.SZ"


def _lookup_stock_name(conn: sqlite3.Connection, ts_code: str, fallback: str = "") -> str:
    row = conn.execute("SELECT name FROM stock_basic WHERE ts_code=?", (ts_code,)).fetchone()
    if row and row["name"]:
        return str(row["name"])
    return str(fallback or "")


def _record_user_strategy_version(conn: sqlite3.Connection, agent_id: int, strategy_text: str) -> None:
    text = str(strategy_text or "").strip()
    if not text:
        return
    latest = conn.execute(
        """SELECT strategy_text FROM agent_user_strategy_version
           WHERE agent_id=? ORDER BY version_no DESC LIMIT 1""",
        (agent_id,),
    ).fetchone()
    if latest and str(latest["strategy_text"] or "") == text:
        return
    next_no = conn.execute(
        "SELECT COALESCE(MAX(version_no), 0) + 1 FROM agent_user_strategy_version WHERE agent_id=?",
        (agent_id,),
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO agent_user_strategy_version (agent_id, version_no, strategy_text)
           VALUES (?, ?, ?)""",
        (agent_id, int(next_no or 1), text),
    )


class AgentManager:
    """Agent 管理器：增删改查"""

    @staticmethod
    def create(name: str, display_name: str, agent_type: str = "custom",
               strategy_ids: str = "", risk_config: dict = None,
               initial_capital: float = INITIAL_CAPITAL) -> int:
        """创建新 Agent"""
        conn = get_conn()
        risk_config = normalize_risk_config(agent_type, strategy_ids, risk_config)

        c = conn.cursor()
        c.execute(
            """INSERT INTO agent_info (name, display_name, agent_type, initial_capital, current_cash, strategy_ids, risk_config)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, display_name, agent_type, initial_capital, initial_capital, strategy_ids, json.dumps(risk_config)),
        )
        conn.commit()
        agent_id = c.lastrowid
        _record_user_strategy_version(conn, agent_id, risk_config.get("user_strategy_original", ""))
        conn.commit()
        conn.close()
        return agent_id

    @staticmethod
    def delete(agent_id: int) -> bool:
        """删除 Agent 及其关联数据"""
        conn = get_conn()
        conn.execute("DELETE FROM agent_position WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_order WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_trade_log WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_daily_report WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_evolution_skill WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_evolution_event WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_race_metric WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_capital_policy WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_reflection_task WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_schedule WHERE agent_id = ?", (agent_id,))
        conn.execute("DELETE FROM agent_info WHERE id = ?", (agent_id,))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def rename(agent_id: int, new_display_name: str) -> bool:
        """重命名 Agent"""
        conn = get_conn()
        conn.execute("UPDATE agent_info SET display_name = ?, updated_at = datetime('now') WHERE id = ?",
                     (new_display_name, agent_id))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def configure(agent_id: int, risk_config: dict, strategy_ids: str = "") -> bool:
        """更新 Agent 配置"""
        conn = get_conn()
        agent = conn.execute("SELECT agent_type, strategy_ids FROM agent_info WHERE id=?", (agent_id,)).fetchone()
        agent_type = agent["agent_type"] if agent else "custom"
        effective_strategy_ids = strategy_ids if strategy_ids != "" else (agent["strategy_ids"] if agent else "")
        risk_config = normalize_risk_config(agent_type, effective_strategy_ids, risk_config)
        conn.execute(
            "UPDATE agent_info SET risk_config = ?, strategy_ids = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(risk_config), effective_strategy_ids, agent_id),
        )
        _record_user_strategy_version(conn, agent_id, risk_config.get("user_strategy_original", ""))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def list_stock_pool(agent_id: int, active_only: bool = False) -> list[dict]:
        conn = get_conn()
        sql = "SELECT * FROM agent_stock_pool WHERE agent_id=?"
        args: list = [agent_id]
        if active_only:
            sql += " AND enabled=1"
        rows = conn.execute(sql + " ORDER BY id ASC", args).fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return result

    @staticmethod
    def replace_stock_pool(agent_id: int, items: list[dict]) -> list[dict]:
        conn = get_conn()
        exists = conn.execute("SELECT id FROM agent_info WHERE id=?", (agent_id,)).fetchone()
        if not exists:
            conn.close()
            return []
        conn.execute("DELETE FROM agent_stock_pool WHERE agent_id=?", (agent_id,))
        seen: set[str] = set()
        for item in items or []:
            code = _normalize_stock_code(item.get("ts_code", ""))
            if not code or code in seen:
                continue
            seen.add(code)
            name = _lookup_stock_name(conn, code, item.get("stock_name", ""))
            conn.execute(
                """INSERT INTO agent_stock_pool (agent_id, ts_code, stock_name, note, enabled)
                   VALUES (?, ?, ?, ?, ?)""",
                (agent_id, code, name, str(item.get("note") or "")[:500], 1 if item.get("enabled", True) else 0),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM agent_stock_pool WHERE agent_id=? ORDER BY id ASC",
            (agent_id,),
        ).fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return result

    @staticmethod
    def upsert_stock_pool_item(agent_id: int, item: dict) -> dict | None:
        conn = get_conn()
        exists = conn.execute("SELECT id FROM agent_info WHERE id=?", (agent_id,)).fetchone()
        if not exists:
            conn.close()
            return None
        code = _normalize_stock_code(item.get("ts_code", ""))
        if not code:
            conn.close()
            return None
        name = _lookup_stock_name(conn, code, item.get("stock_name", ""))
        conn.execute(
            """INSERT INTO agent_stock_pool (agent_id, ts_code, stock_name, note, enabled)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(agent_id, ts_code) DO UPDATE SET
               stock_name=excluded.stock_name, note=excluded.note, enabled=excluded.enabled,
               updated_at=datetime('now')""",
            (agent_id, code, name, str(item.get("note") or "")[:500], 1 if item.get("enabled", True) else 0),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM agent_stock_pool WHERE agent_id=? AND ts_code=?",
            (agent_id, code),
        ).fetchone()
        result = dict(row) if row else None
        conn.close()
        return result

    @staticmethod
    def delete_stock_pool_item(agent_id: int, ts_code: str) -> bool:
        conn = get_conn()
        code = _normalize_stock_code(ts_code)
        conn.execute("DELETE FROM agent_stock_pool WHERE agent_id=? AND ts_code=?", (agent_id, code))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def list_user_strategy_versions(agent_id: int, limit: int = 20) -> list[dict]:
        conn = get_conn()
        rows = conn.execute(
            """SELECT id, agent_id, version_no, strategy_text, created_at
               FROM agent_user_strategy_version
               WHERE agent_id=?
               ORDER BY version_no DESC
               LIMIT ?""",
            (agent_id, max(1, int(limit or 20))),
        ).fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return result

    @staticmethod
    def list_all() -> list[dict]:
        """列出所有 Agent"""
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM agent_info ORDER BY id")
        result = [dict(r) for r in c.fetchall()]
        conn.close()
        return result

    @staticmethod
    def get(agent_id: int) -> Optional[dict]:
        """获取单个 Agent 信息"""
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM agent_info WHERE id = ?", (agent_id,))
        r = c.fetchone()
        conn.close()
        return dict(r) if r else None

    @staticmethod
    def toggle_status(agent_id: int) -> Optional[str]:
        """切换 Agent 启用/禁用状态，返回新状态"""
        agent = AgentManager.get(agent_id)
        if not agent:
            return None
        new_status = "disabled" if agent["status"] == "active" else "active"
        return AgentManager.set_status(agent_id, new_status)

    @staticmethod
    def set_status(agent_id: int, status: str) -> Optional[str]:
        """设置 Agent 状态：active / paused / disabled。"""
        if status not in ("active", "paused", "disabled"):
            raise ValueError("status must be active, paused, or disabled")
        agent = AgentManager.get(agent_id)
        if not agent:
            return None
        conn = get_conn()
        conn.execute("UPDATE agent_info SET status = ?, updated_at = datetime('now') WHERE id = ?",
                     (status, agent_id))
        conn.commit()
        conn.close()
        return status

    @staticmethod
    def get_schedule(agent_id: int) -> dict:
        conn = get_conn()
        row = conn.execute("SELECT * FROM agent_schedule WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            agent = conn.execute(
                "SELECT schedule_enabled, review_time, push_time FROM agent_info WHERE id=?",
                (agent_id,),
            ).fetchone()
            conn.close()
            return {
                "agent_id": agent_id,
                "enabled": bool(agent["schedule_enabled"]) if agent else False,
                "review_time": agent["review_time"] if agent else "23:00",
                "push_time": agent["push_time"] if agent else "23:00",
                "timezone": "Asia/Shanghai",
            }
        result = dict(row)
        result["enabled"] = bool(result.get("enabled"))
        result.pop("trade_time", None)
        conn.close()
        return result

    @staticmethod
    def configure_schedule(agent_id: int, enabled: bool, review_time: str,
                           push_time: str) -> bool:
        conn = get_conn()
        exists = conn.execute("SELECT id FROM agent_info WHERE id=?", (agent_id,)).fetchone()
        if not exists:
            conn.close()
            return False
        conn.execute(
            """INSERT INTO agent_schedule (agent_id, enabled, review_time, push_time)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET
               enabled=excluded.enabled, review_time=excluded.review_time,
               push_time=excluded.push_time, updated_at=datetime('now')""",
            (agent_id, 1 if enabled else 0, review_time, push_time),
        )
        conn.execute(
            "UPDATE agent_info SET schedule_enabled=?, review_time=?, push_time=?, updated_at=datetime('now') WHERE id=?",
            (1 if enabled else 0, review_time, push_time, agent_id),
        )
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def get_context(agent_id: int, trade_date: str) -> Optional[dict]:
        """获取 Agent 决策上下文（含持仓）"""
        agent = AgentManager.get(agent_id)
        if not agent:
            return None

        conn = get_conn()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM agent_position WHERE agent_id = ? AND quantity > 0", (agent_id,))
        positions = [dict(r) for r in c.fetchall()]
        conn.close()

        total_market_value = sum(p.get("market_value", 0) for p in positions)
        total_assets = agent["current_cash"] + total_market_value

        return {
            "agent": agent,
            "trade_date": trade_date,
            "cash": agent["current_cash"],
            "total_assets": total_assets,
            "initial_capital": agent["initial_capital"],
            "positions": positions,
        }
