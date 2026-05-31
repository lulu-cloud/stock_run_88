"""Telegram user profile and watchlist persistence."""

import json
import re

from backend.db.repository import get_conn
from backend.trading.rules import normalize_ts_code


DEFAULT_PROFILE = {
    "risk_level": "中等",
    "horizon": "短线",
    "preferred_sectors": [],
    "excluded_sectors": [],
    "max_results": 5,
    "daily_push_enabled": False,
}


SECTOR_KEYWORDS = [
    "AI", "人工智能", "算力", "半导体", "芯片", "新能源", "光伏", "锂电池", "风电",
    "医药", "医疗器械", "机器人", "军工", "汽车", "消费", "白酒", "金融", "银行",
    "证券", "地产", "化工", "有色", "钢铁", "煤炭", "电力", "通信", "软件",
    "传媒", "农业", "环保", "高股息", "低空经济", "核电", "航运",
]


def _json_loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def ensure_profile(chat_id: str, username: str = "") -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM telegram_user_profile WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        conn.execute(
            """INSERT INTO telegram_user_profile
               (chat_id, username, risk_level, horizon, preferred_sectors,
                excluded_sectors, max_results, daily_push_enabled, profile_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                chat_id,
                username,
                DEFAULT_PROFILE["risk_level"],
                DEFAULT_PROFILE["horizon"],
                json.dumps(DEFAULT_PROFILE["preferred_sectors"], ensure_ascii=False),
                json.dumps(DEFAULT_PROFILE["excluded_sectors"], ensure_ascii=False),
                DEFAULT_PROFILE["max_results"],
                0,
                "{}",
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM telegram_user_profile WHERE chat_id=?", (chat_id,)).fetchone()
    elif username and username != row["username"]:
        conn.execute(
            "UPDATE telegram_user_profile SET username=?, updated_at=datetime('now') WHERE chat_id=?",
            (username, chat_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM telegram_user_profile WHERE chat_id=?", (chat_id,)).fetchone()
    result = _row_to_profile(row)
    conn.close()
    return result


def _row_to_profile(row) -> dict:
    if not row:
        return DEFAULT_PROFILE.copy()
    data = dict(row)
    data["preferred_sectors"] = _json_loads(data.get("preferred_sectors"), [])
    data["excluded_sectors"] = _json_loads(data.get("excluded_sectors"), [])
    data["daily_push_enabled"] = bool(data.get("daily_push_enabled"))
    data["profile_json"] = _json_loads(data.get("profile_json"), {})
    return data


def get_profile(chat_id: str) -> dict:
    return ensure_profile(chat_id)


def update_profile(chat_id: str, updates: dict, username: str = "") -> dict:
    current = ensure_profile(chat_id, username)
    merged = {
        "risk_level": updates.get("risk_level", current.get("risk_level", "中等")),
        "horizon": updates.get("horizon", current.get("horizon", "短线")),
        "preferred_sectors": updates.get("preferred_sectors", current.get("preferred_sectors", [])),
        "excluded_sectors": updates.get("excluded_sectors", current.get("excluded_sectors", [])),
        "max_results": int(updates.get("max_results", current.get("max_results", 5)) or 5),
        "daily_push_enabled": bool(updates.get("daily_push_enabled", current.get("daily_push_enabled", False))),
    }
    merged["max_results"] = max(1, min(10, merged["max_results"]))
    conn = get_conn()
    conn.execute(
        """UPDATE telegram_user_profile
           SET username=COALESCE(NULLIF(?, ''), username),
               risk_level=?, horizon=?, preferred_sectors=?, excluded_sectors=?,
               max_results=?, daily_push_enabled=?, updated_at=datetime('now')
           WHERE chat_id=?""",
        (
            username,
            merged["risk_level"],
            merged["horizon"],
            json.dumps(merged["preferred_sectors"], ensure_ascii=False),
            json.dumps(merged["excluded_sectors"], ensure_ascii=False),
            merged["max_results"],
            1 if merged["daily_push_enabled"] else 0,
            chat_id,
        ),
    )
    conn.commit()
    conn.close()
    return get_profile(chat_id)


def infer_preferences(text: str) -> dict:
    """Extract lightweight user preferences from natural language."""
    raw = text or ""
    updates: dict = {}
    if any(k in raw for k in ["低风险", "稳健", "保守", "回撤小"]):
        updates["risk_level"] = "低"
    elif any(k in raw for k in ["高风险", "激进", "弹性", "博弈", "妖股"]):
        updates["risk_level"] = "高"
    elif any(k in raw for k in ["中等风险", "均衡"]):
        updates["risk_level"] = "中等"

    if any(k in raw for k in ["短线", "超短", "打板", "1周", "一周"]):
        updates["horizon"] = "短线"
    elif any(k in raw for k in ["中线", "波段", "一个月", "1个月"]):
        updates["horizon"] = "中线"
    elif any(k in raw for k in ["长线", "长期", "价值", "高股息"]):
        updates["horizon"] = "长线"

    preferred = [kw for kw in SECTOR_KEYWORDS if kw.lower() in raw.lower()]
    if preferred:
        updates["preferred_sectors"] = list(dict.fromkeys(preferred))
    excluded = []
    for kw in SECTOR_KEYWORDS:
        if f"不要{kw}" in raw or f"排除{kw}" in raw or f"不看{kw}" in raw:
            excluded.append(kw)
    if excluded:
        updates["excluded_sectors"] = excluded

    m = re.search(r"(\d+)\s*[只个支]?", raw)
    if m and any(k in raw for k in ["推荐", "选", "股票"]):
        updates["max_results"] = int(m.group(1))
    return updates


def apply_inferred_preferences(chat_id: str, text: str, username: str = "") -> dict:
    current = ensure_profile(chat_id, username)
    inferred = infer_preferences(text)
    if not inferred:
        return current
    updates = current.copy()
    for key in ("risk_level", "horizon", "max_results"):
        if key in inferred:
            updates[key] = inferred[key]
    for key in ("preferred_sectors", "excluded_sectors"):
        if key in inferred:
            updates[key] = list(dict.fromkeys((updates.get(key) or []) + inferred[key]))
    return update_profile(chat_id, updates, username)


def format_profile(chat_id: str) -> str:
    p = ensure_profile(chat_id)
    return "\n".join([
        "当前用户画像",
        f"风险偏好: {p.get('risk_level')}",
        f"持股周期: {p.get('horizon')}",
        f"偏好板块: {', '.join(p.get('preferred_sectors') or []) or '未设置'}",
        f"排除板块: {', '.join(p.get('excluded_sectors') or []) or '未设置'}",
        f"默认推荐数: {p.get('max_results')}",
        f"每日推送: {'开启' if p.get('daily_push_enabled') else '关闭'}",
    ])


def parse_profile_set(text: str) -> dict:
    raw = text.replace("/profile", "").replace("set", "").replace("设置", "")
    updates = infer_preferences(raw)
    risk_match = re.search(r"风险[=:：]?\s*(中等|稳健|激进|高|中|低)", raw)
    if risk_match:
        value = risk_match.group(1)
        updates["risk_level"] = {"稳健": "低", "激进": "高"}.get(value, value)
    horizon_match = re.search(r"(周期|风格)[=:：]?\s*(短线|中线|长线|波段)", raw)
    if horizon_match:
        updates["horizon"] = "中线" if horizon_match.group(2) == "波段" else horizon_match.group(2)
    sector_match = re.search(r"板块[=:：]?\s*([^\n；;]+)", raw)
    if sector_match:
        updates["preferred_sectors"] = [
            x.strip() for x in re.split(r"[,，/、 ]+", sector_match.group(1)) if x.strip()
        ]
    count_match = re.search(r"(数量|推荐数)[=:：]?\s*(\d+)", raw)
    if count_match:
        updates["max_results"] = int(count_match.group(2))
    return updates


def set_daily_push(chat_id: str, enabled: bool, username: str = "") -> dict:
    current = ensure_profile(chat_id, username)
    current["daily_push_enabled"] = enabled
    return update_profile(chat_id, current, username)


def add_watch(chat_id: str, ts_code: str, stock_name: str = "", note: str = "") -> dict:
    code = normalize_ts_code(ts_code)
    conn = get_conn()
    conn.execute(
        """INSERT INTO telegram_watchlist (chat_id, ts_code, stock_name, note)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(chat_id, ts_code) DO UPDATE SET
           stock_name=excluded.stock_name, note=excluded.note""",
        (chat_id, code, stock_name, note),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "chat_id": chat_id, "ts_code": code}


def remove_watch(chat_id: str, ts_code: str) -> dict:
    code = normalize_ts_code(ts_code)
    conn = get_conn()
    conn.execute("DELETE FROM telegram_watchlist WHERE chat_id=? AND ts_code=?", (chat_id, code))
    conn.commit()
    conn.close()
    return {"ok": True, "chat_id": chat_id, "ts_code": code}


def list_watch(chat_id: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM telegram_watchlist WHERE chat_id=? ORDER BY id DESC",
        (chat_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_watchlist(chat_id: str) -> str:
    rows = list_watch(chat_id)
    if not rows:
        return "关注股为空。用法: /watch add 600000.SH"
    lines = ["关注股:"]
    for r in rows:
        lines.append(f"- {r['ts_code']} {r.get('stock_name') or ''} {r.get('note') or ''}".strip())
    return "\n".join(lines)
