"""Bounded trading memory files.

The memory is intentionally small and deterministic. LLM decisions read a frozen
snapshot made before review; post-review evolution updates the live files for
the next trading day.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from backend.config import ROOT_DIR


FACT_LIMIT = 2200
PREFER_LIMIT = 1375
SHORT_LIMIT = 1800
SYSTEM_DOC_LIMIT = 6000
TELEGRAM_MEMORY_LIMIT = 2200
COMPRESS_THRESHOLD_RATIO = 1.05


def _agent_dir(agent_id: int) -> str:
    return os.path.join(ROOT_DIR, "agent_memory", str(agent_id))


def _path(agent_id: int, name: str) -> str:
    return os.path.join(_agent_dir(agent_id), name)


def _trim(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    lines = [line for line in text.splitlines() if line.strip()]
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        size = len(line) + 1
        if total + size > limit:
            break
        kept.append(line)
        total += size
    return "\n".join(reversed(kept)).strip()


def _compress_with_llm(text: str, limit: int, title: str) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    prompt = (
        "你是A股交易Agent记忆压缩器。请把输入重写为固定大小以内的高密度Markdown。"
        "只保留已验证、可执行、可复用的规律/偏好/风控边界；删除流水账、重复句、空泛判断。"
        "不要新增事实，不要输出解释。"
    )
    user = f"""文件: {title}
字符上限: {limit}

原文:
{text[: max(limit * 4, 8000)]}

请输出压缩后的正文，必须不超过 {limit} 个中文字符。"""
    try:
        from backend.llm.client import chat

        compressed = (chat(prompt, user, temperature=0.1) or "").strip()
        if compressed and len(compressed) <= limit:
            return compressed
        if compressed:
            return _trim(compressed, limit)
    except Exception:
        pass
    return _trim(text, limit)


def _compress_file_if_needed(path: str, limit: int, title: str) -> dict:
    if not os.path.exists(path):
        return {"path": path, "changed": False, "reason": "missing"}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if len(text.strip()) <= int(limit * COMPRESS_THRESHOLD_RATIO):
        return {"path": path, "changed": False, "chars": len(text.strip()), "limit": limit}
    compressed = _compress_with_llm(text, limit, title)
    with open(path, "w", encoding="utf-8") as f:
        f.write(compressed.rstrip() + "\n")
    try:
        from backend.db.repository import get_conn

        rel = os.path.relpath(path, ROOT_DIR)
        parts = rel.split(os.sep)
        agent_id = int(parts[1]) if len(parts) > 1 and parts[0] == "agent_memory" and parts[1].isdigit() else None
        conn = get_conn()
        conn.execute(
            """INSERT INTO memory_compression_audit
               (scope, agent_id, file_path, reason, before_chars, after_chars)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "telegram" if "telegram_bot" in rel else "agent",
                agent_id,
                rel,
                f"超过 {limit} 字符上限阈值",
                len(text.strip()),
                len(compressed.strip()),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return {
        "path": path,
        "changed": True,
        "before_chars": len(text.strip()),
        "after_chars": len(compressed.strip()),
        "limit": limit,
    }


def ensure_memory_files(agent_id: int) -> dict:
    os.makedirs(_agent_dir(agent_id), exist_ok=True)
    defaults = {
        "trade_fact.md": "尚无已验证市场规律。每日复盘后只保留可复用结论。",
        "trade_prefer.md": "默认偏好：控制单票仓位，避免连续复制失败挂单价格。",
        "short_ring.md": "近3日极端行情缓冲区为空。",
    }
    for filename, content in defaults.items():
        path = _path(agent_id, filename)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
    return read_memory(agent_id)


def compress_agent_memory(agent_id: int) -> dict:
    ensure_memory_files(agent_id)
    results = {
        "trade_fact": _compress_file_if_needed(_path(agent_id, "trade_fact.md"), FACT_LIMIT, "trade_fact.md"),
        "trade_prefer": _compress_file_if_needed(_path(agent_id, "trade_prefer.md"), PREFER_LIMIT, "trade_prefer.md"),
        "short_ring": _compress_file_if_needed(_path(agent_id, "short_ring.md"), SHORT_LIMIT, "short_ring.md"),
    }
    system_path = os.path.join(_agent_dir(agent_id), "system", "current.md")
    if os.path.exists(system_path):
        results["system_doc"] = _compress_file_if_needed(system_path, SYSTEM_DOC_LIMIT, "system/current.md")
    return results


def read_memory(agent_id: int) -> dict:
    ensure_dir = _agent_dir(agent_id)
    os.makedirs(ensure_dir, exist_ok=True)
    result = {}
    for key, filename in (
        ("trade_fact", "trade_fact.md"),
        ("trade_prefer", "trade_prefer.md"),
        ("short_ring", "short_ring.md"),
    ):
        path = _path(agent_id, filename)
        result[key] = open(path, "r", encoding="utf-8").read() if os.path.exists(path) else ""
    return result


def snapshot_memory(agent_id: int, trade_date: str) -> dict:
    compress_agent_memory(agent_id)
    memory = ensure_memory_files(agent_id)
    snapshot = {
        "agent_id": agent_id,
        "trade_date": trade_date,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **memory,
    }
    snap_dir = os.path.join(_agent_dir(agent_id), "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    with open(os.path.join(snap_dir, f"{trade_date}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return snapshot


def update_memory(agent_id: int, trade_date: str, payload: dict) -> dict:
    memory = ensure_memory_files(agent_id)
    trades = payload.get("trades") or []
    expired = payload.get("expired_orders") or []
    skill_updates = payload.get("skill_updates") or []
    replay_summary = payload.get("intraday_replay", {}).get("summary", "")

    fact_lines = [memory.get("trade_fact", "").strip()]
    if trades or expired:
        fact_lines.append(f"- {trade_date}: 成交{len(trades)}笔，过期/失败{len(expired)}笔。{replay_summary}".strip())
    for item in skill_updates[:4]:
        fact_lines.append(
            f"- {trade_date}: 技能{item.get('skill_id')} 置信度 "
            f"{item.get('old_confidence'):.2f}->{item.get('new_confidence'):.2f}，"
            f"失败率{item.get('recent_fail_rate'):.2f}。"
        )

    prefer_lines = [memory.get("trade_prefer", "").strip()]
    if expired:
        codes = "、".join(sorted({o.get("ts_code", "") for o in expired if o.get("ts_code")})[:6])
        prefer_lines.append(f"- {trade_date}: {codes} 等挂单失败，次日应先复盘价格区间和成交优先级。")
    if payload.get("risk_adjust_log"):
        prefer_lines.append(f"- {trade_date}: {payload['risk_adjust_log']}")

    short_lines = [
        line for line in memory.get("short_ring", "").splitlines()
        if line.strip() and not line.strip().startswith("近3日极端行情缓冲区为空")
    ]
    short_lines.append(f"- {trade_date}: {payload.get('market_scene') or replay_summary or '未发现可复用极端行情。'}")
    short_text = "\n".join(short_lines[-3:])

    updates = {
        "trade_fact": _trim("\n".join(filter(None, fact_lines)), FACT_LIMIT),
        "trade_prefer": _trim("\n".join(filter(None, prefer_lines)), PREFER_LIMIT),
        "short_ring": _trim(short_text, SHORT_LIMIT),
    }
    for key, filename in (
        ("trade_fact", "trade_fact.md"),
        ("trade_prefer", "trade_prefer.md"),
        ("short_ring", "short_ring.md"),
    ):
        with open(_path(agent_id, filename), "w", encoding="utf-8") as f:
            f.write(updates[key].rstrip() + "\n")
    compress_agent_memory(agent_id)
    return updates


def _telegram_dir() -> str:
    return os.path.join(ROOT_DIR, "agent_memory", "telegram_bot")


def _telegram_path() -> str:
    return os.path.join(_telegram_dir(), "recommend_memory.md")


def ensure_telegram_memory() -> str:
    os.makedirs(_telegram_dir(), exist_ok=True)
    path = _telegram_path()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("暂无已验证推荐偏好。仅保留用户反馈、话术偏好和推荐失效边界。\n")
    return path


def read_telegram_memory() -> str:
    path = ensure_telegram_memory()
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if len(text.strip()) > int(TELEGRAM_MEMORY_LIMIT * COMPRESS_THRESHOLD_RATIO):
        _compress_file_if_needed(path, TELEGRAM_MEMORY_LIMIT, "telegram_bot/recommend_memory.md")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    return text.strip()


def update_telegram_memory(event: str) -> dict:
    path = ensure_telegram_memory()
    with open(path, "r", encoding="utf-8") as f:
        old = f.read().strip()
    line = f"- {datetime.now().strftime('%Y-%m-%d %H:%M')}: {(event or '').strip()}"
    text = "\n".join(filter(None, [old, line]))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    return _compress_file_if_needed(path, TELEGRAM_MEMORY_LIMIT, "telegram_bot/recommend_memory.md")
