"""Scoped conversation memory for the Telegram recommendation assistant."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from backend.db.repository import get_conn
from backend.telegram.stock_analysis import extract_stock_mentions, lookup_stock_name


DEFAULT_THREAD_ID = "default"
MEMORY_LIMIT = 80
SHORT_TERM_MIN_TURNS = 5
SHORT_TERM_MAX_TURNS = 8
SHORT_TERM_DEFAULT_TURNS = max(
    SHORT_TERM_MIN_TURNS,
    min(int(os.environ.get("TELEGRAM_SHORT_TERM_TURNS", "6") or 6), SHORT_TERM_MAX_TURNS),
)


def normalize_thread_id(thread_id: str | int | None) -> str:
    text = str(thread_id or "").strip()
    return text or DEFAULT_THREAD_ID


def thread_scope_id(chat_id: str, thread_id: str | int | None) -> str:
    return f"{chat_id or 'local'}:{normalize_thread_id(thread_id)}"


def profile_scope_id(chat_id: str, user_id: str = "") -> str:
    return str(user_id or chat_id or "local")


def _json_dumps(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, default=str)


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _keywords(text: str, max_items: int = 16) -> str:
    raw = text or ""
    codes = extract_stock_mentions(raw)
    english = re.findall(r"[A-Za-z0-9_.-]{2,}", raw)
    cn_terms = re.findall(r"[\u4e00-\u9fff]{2,8}", raw)
    important = []
    for token in (
        "短线", "中线", "长线", "低风险", "中等风险", "高风险", "龙头", "回踩", "均线",
        "多头", "政策", "半导体", "AI", "机器人", "新能源", "券商", "军工", "消费",
        "持有", "上车", "买了", "关注", "不喜欢", "偏好",
    ):
        if token in raw:
            important.append(token)
    items: list[str] = []
    for token in [*codes, *important, *english, *cn_terms]:
        clean = str(token).strip()
        if clean and clean not in items:
            items.append(clean)
        if len(items) >= max_items:
            break
    return " ".join(items)


def record_message(
    chat_id: str,
    user_id: str = "",
    thread_id: str | int | None = DEFAULT_THREAD_ID,
    chat_type: str = "",
    role: str = "user",
    content: str = "",
    intent: str = "",
    metadata: dict | None = None,
    platform: str = "telegram",
) -> int:
    """Persist a Telegram conversation message and return its id."""
    if not (content or "").strip():
        return 0
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO telegram_conversation_message
           (platform, chat_id, user_id, thread_id, chat_type, role, content, intent, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            platform,
            chat_id or "local",
            str(user_id or ""),
            normalize_thread_id(thread_id),
            chat_type or "",
            role,
            content,
            intent or "",
            _json_dumps(metadata),
        ),
    )
    conn.commit()
    message_id = int(cur.lastrowid or 0)
    conn.close()
    return message_id


def get_recent_context(chat_id: str, thread_id: str | int | None = DEFAULT_THREAD_ID, limit: int = 20) -> list[dict]:
    """Return recent chat/thread messages in chronological order."""
    safe_limit = max(1, min(int(limit or 20), 60))
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, user_id, role, content, intent, created_at
           FROM telegram_conversation_message
           WHERE chat_id=? AND thread_id=?
           ORDER BY created_at DESC, id DESC
           LIMIT ?""",
        (chat_id or "local", normalize_thread_id(thread_id), safe_limit),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in reversed(rows)]


def short_term_message_limit(turns: int | None = None) -> int:
    safe_turns = SHORT_TERM_DEFAULT_TURNS if turns is None else int(turns or SHORT_TERM_DEFAULT_TURNS)
    safe_turns = max(SHORT_TERM_MIN_TURNS, min(safe_turns, SHORT_TERM_MAX_TURNS))
    return safe_turns * 2


def _scope_filters(user_id: str, chat_id: str, thread_id: str | int | None) -> list[tuple[str, str]]:
    scopes: list[tuple[str, str]] = []
    if user_id:
        scopes.append(("user", str(user_id)))
    if chat_id:
        scopes.append(("chat", str(chat_id)))
        tid = normalize_thread_id(thread_id)
        if tid != DEFAULT_THREAD_ID:
            scopes.append(("thread", thread_scope_id(chat_id, tid)))
    scopes.append(("global", "telegram"))
    return scopes


def search_memories(
    user_id: str = "",
    chat_id: str = "local",
    thread_id: str | int | None = DEFAULT_THREAD_ID,
    query: str = "",
    top_k: int = 8,
) -> list[dict]:
    """Search scoped memories. SQLite LIKE is enough for this lightweight deployment."""
    limit = max(1, min(int(top_k or 8), 30))
    terms = [x for x in _keywords(query, 8).split() if x]
    scopes = _scope_filters(user_id, chat_id or "local", thread_id)
    conn = get_conn()
    found: list[dict] = []
    seen: set[int] = set()
    for scope, scope_id in scopes:
        if terms:
            clauses = " OR ".join(["content LIKE ? OR keywords LIKE ?" for _ in terms])
            params: list[Any] = [scope, scope_id]
            for term in terms:
                like = f"%{term}%"
                params.extend([like, like])
            params.append(limit)
            rows = conn.execute(
                f"""SELECT * FROM telegram_memory_item
                    WHERE scope=? AND scope_id=? AND ({clauses})
                    ORDER BY importance DESC, updated_at DESC, id DESC
                    LIMIT ?""",
                tuple(params),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM telegram_memory_item
                   WHERE scope=? AND scope_id=?
                   ORDER BY importance DESC, updated_at DESC, id DESC
                   LIMIT ?""",
                (scope, scope_id, limit),
            ).fetchall()
        for row in rows:
            item = _row_to_dict(row)
            if int(item["id"]) in seen:
                continue
            seen.add(int(item["id"]))
            found.append(item)
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
    if found:
        conn.execute(
            f"""UPDATE telegram_memory_item SET last_used_at=datetime('now')
                WHERE id IN ({','.join('?' for _ in found)})""",
            tuple(int(x["id"]) for x in found),
        )
        conn.commit()
    conn.close()
    return found


def upsert_memory_item(
    scope: str,
    scope_id: str,
    memory_type: str,
    content: str,
    importance: float = 0.5,
    source_message_id: int = 0,
) -> dict:
    """Insert or refresh a long-term memory item."""
    text = re.sub(r"\s+", " ", content or "").strip()
    if not text:
        return {"ok": False, "error": "empty memory"}
    if scope not in {"user", "chat", "thread", "global"}:
        return {"ok": False, "error": f"invalid scope {scope}"}
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO telegram_memory_item
           (scope, scope_id, memory_type, content, keywords, importance, source_message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(scope, scope_id, memory_type, content) DO UPDATE SET
             keywords=excluded.keywords,
             importance=max(telegram_memory_item.importance, excluded.importance),
             source_message_id=COALESCE(excluded.source_message_id, telegram_memory_item.source_message_id),
             updated_at=datetime('now')""",
        (
            scope,
            scope_id,
            memory_type or "fact",
            text[:1000],
            _keywords(text),
            max(0.0, min(float(importance or 0.5), 1.0)),
            int(source_message_id or 0) or None,
        ),
    )
    conn.commit()
    row = conn.execute(
        """SELECT * FROM telegram_memory_item
           WHERE scope=? AND scope_id=? AND memory_type=? AND content=?""",
        (scope, scope_id, memory_type or "fact", text[:1000]),
    ).fetchone()
    conn.close()
    return {"ok": True, "id": int(row["id"] if row else cur.lastrowid or 0)}


def extract_memory_candidates(text: str, role: str = "user", intent: str = "") -> list[dict]:
    """Heuristic memory extraction. Conservative by design to avoid noisy memories."""
    if role != "user":
        return []
    raw = re.sub(r"\s+", " ", text or "").strip()
    if len(raw) < 4:
        return []
    candidates: list[dict] = []
    stock_codes = extract_stock_mentions(raw)
    if any(k in raw for k in ("记住", "记一下", "以后", "我的偏好", "我偏好", "我喜欢", "我不喜欢", "不要推荐")):
        candidates.append({"scope_hint": "user", "memory_type": "preference", "content": raw, "importance": 0.75})
    if any(k in raw for k in ("短线", "中线", "长线", "低风险", "稳健", "激进", "打板", "回踩", "均线")) and any(
        k in raw for k in ("我", "偏好", "喜欢", "不喜欢", "希望", "想要")
    ):
        candidates.append({"scope_hint": "user", "memory_type": "preference", "content": raw, "importance": 0.65})
    if any(k in raw for k in ("本群", "这个群", "群里", "我们群")):
        candidates.append({"scope_hint": "chat", "memory_type": "fact", "content": raw, "importance": 0.7})
    if any(k in raw for k in ("这个话题", "本话题", "这个topic", "这个 Topic")):
        candidates.append({"scope_hint": "thread", "memory_type": "summary", "content": raw, "importance": 0.65})
    if stock_codes and any(k in raw for k in ("持有", "上车", "买了", "关注", "看好", "想买", "推荐", "评价")):
        for code in stock_codes[:5]:
            name = lookup_stock_name(code)
            candidates.append({
                "scope_hint": "user",
                "memory_type": "stock_interest",
                "content": f"{code} {name}: {raw}",
                "importance": 0.8,
            })
    return candidates[:8]


def update_memories_from_text(
    chat_id: str,
    user_id: str = "",
    thread_id: str | int | None = DEFAULT_THREAD_ID,
    chat_type: str = "",
    text: str = "",
    source_message_id: int = 0,
    intent: str = "",
) -> list[dict]:
    """Extract and upsert memory candidates from a user message."""
    results: list[dict] = []
    for item in extract_memory_candidates(text, "user", intent):
        scope_hint = item.get("scope_hint") or "user"
        if scope_hint == "thread":
            scope, scope_id = "thread", thread_scope_id(chat_id or "local", thread_id)
        elif scope_hint == "chat":
            scope, scope_id = "chat", chat_id or "local"
        elif scope_hint == "global":
            scope, scope_id = "global", "telegram"
        else:
            scope, scope_id = "user", profile_scope_id(chat_id or "local", user_id)
        results.append(upsert_memory_item(
            scope,
            scope_id,
            item.get("memory_type") or "fact",
            item.get("content") or "",
            float(item.get("importance") or 0.5),
            source_message_id,
        ))
    return results


def build_memory_prompt(
    chat_id: str,
    user_id: str = "",
    thread_id: str | int | None = DEFAULT_THREAD_ID,
    query: str = "",
    recent_limit: int | None = None,
    memory_limit: int = 8,
) -> dict:
    """Build context payload for the recommender prompt."""
    recent = get_recent_context(chat_id or "local", thread_id, recent_limit or short_term_message_limit())
    memories = search_memories(user_id, chat_id or "local", thread_id, query, memory_limit)
    lines: list[str] = []
    if memories:
        lines.append("相关长期记忆:")
        for item in memories:
            lines.append(f"- [{item.get('scope')}/{item.get('memory_type')}] {item.get('content')}")
    if recent:
        lines.append("最近对话:")
        for msg in recent[-recent_limit:]:
            content = re.sub(r"\s+", " ", msg.get("content") or "").strip()[:220]
            lines.append(f"- {msg.get('role')}: {content}")
    return {"prompt": "\n".join(lines), "memories": memories, "recent_messages": recent}


def list_memory_items(
    chat_id: str = "local",
    user_id: str = "",
    thread_id: str | int | None = DEFAULT_THREAD_ID,
    scope: str = "",
    limit: int = MEMORY_LIMIT,
) -> list[dict]:
    """List memories visible to the current Telegram context."""
    safe_limit = max(1, min(int(limit or MEMORY_LIMIT), 200))
    scopes = [(scope, "")] if scope else _scope_filters(user_id, chat_id or "local", thread_id)
    conn = get_conn()
    items: list[dict] = []
    for sc, sid in scopes:
        if scope:
            rows = conn.execute(
                """SELECT * FROM telegram_memory_item
                   WHERE scope=?
                   ORDER BY importance DESC, updated_at DESC, id DESC
                   LIMIT ?""",
                (sc, safe_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM telegram_memory_item
                   WHERE scope=? AND scope_id=?
                   ORDER BY importance DESC, updated_at DESC, id DESC
                   LIMIT ?""",
                (sc, sid, safe_limit),
            ).fetchall()
        items.extend(_row_to_dict(r) for r in rows)
        if len(items) >= safe_limit:
            break
    conn.close()
    return items[:safe_limit]


def delete_memory_item(memory_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM telegram_memory_item WHERE id=?", (int(memory_id),))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def forget_memories_by_keyword(
    keyword: str,
    chat_id: str = "local",
    user_id: str = "",
    thread_id: str | int | None = DEFAULT_THREAD_ID,
) -> int:
    key = (keyword or "").strip()
    if not key:
        return 0
    scopes = _scope_filters(user_id, chat_id or "local", thread_id)
    conn = get_conn()
    deleted = 0
    for scope, scope_id in scopes:
        cur = conn.execute(
            """DELETE FROM telegram_memory_item
               WHERE scope=? AND scope_id=? AND (content LIKE ? OR keywords LIKE ?)""",
            (scope, scope_id, f"%{key}%", f"%{key}%"),
        )
        deleted += cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def format_memory_overview(
    chat_id: str = "local",
    user_id: str = "",
    thread_id: str | int | None = DEFAULT_THREAD_ID,
    limit: int = 20,
) -> str:
    items = list_memory_items(chat_id, user_id, thread_id, "", limit)
    if not items:
        return "当前还没有沉淀长期记忆。你可以说“记住我偏好短线右侧交易”。"
    lines = ["当前可用记忆:"]
    for item in items[:limit]:
        lines.append(
            f"- #{item['id']} [{item['scope']}/{item['memory_type']}] "
            f"{item['content'][:160]}"
        )
    lines.append("删除示例: /memory forget 关键词，或 /memory forget #12")
    return "\n".join(lines)
