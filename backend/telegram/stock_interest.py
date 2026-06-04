"""Shared stock research memory written by the Telegram assistant."""

import json
import os
import re
from datetime import datetime

from backend.config import ROOT_DIR
from backend.data.tags import load_tag_map
from backend.db.repository import get_conn
from backend.telegram.stock_analysis import generate_stock_report, lookup_stock_name
from backend.trading.rules import normalize_ts_code


def _shared_dir() -> str:
    path = os.path.join(ROOT_DIR, "agent_memory", "shared_stocks")
    os.makedirs(path, exist_ok=True)
    return path


def _report_path(ts_code: str) -> str:
    return os.path.join(_shared_dir(), f"{normalize_ts_code(ts_code)}.md")


def _infer_view(report_md: str) -> str:
    text = report_md or ""
    if "谨慎观察" in text:
        return "谨慎观察"
    if "偏强观察" in text:
        return "偏强观察"
    return "观察"


def _safe_context(text: str, max_chars: int = 260) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:max_chars]


def record_stock_interest(
    chat_id: str,
    username: str,
    ts_code: str,
    context: str = "",
    intent: str = "mention",
    profile: dict | None = None,
) -> dict:
    """Generate and persist a shared report for a user-mentioned stock."""
    code = normalize_ts_code(ts_code)
    name = lookup_stock_name(code)
    report = generate_stock_report(code, profile or {})
    tags = load_tag_map().get(code, {})
    sector = tags.get("sector_tag") or tags.get("industry_tag") or ""
    path = _report_path(code)
    header = "\n".join([
        f"# {code} {name} 共享研究报告",
        "",
        f"- 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 来源: Telegram 推荐助手",
        f"- 用户: {username or chat_id or 'local'}",
        f"- 意图: {intent}",
        f"- 用户上下文: {_safe_context(context)}",
        f"- 板块: {sector or '未知'}",
        "",
    ])
    md = header + report.rstrip() + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)

    conn = get_conn()
    conn.execute(
        """INSERT INTO shared_stock_report
           (ts_code, stock_name, chat_id, username, source, user_intent, user_preference,
            sector, report_md, report_path, recommend_view, mention_count)
           VALUES (?, ?, ?, ?, 'telegram', ?, ?, ?, ?, ?, ?, 1)
           ON CONFLICT(ts_code, chat_id) DO UPDATE SET
             stock_name=excluded.stock_name,
             username=excluded.username,
             user_intent=excluded.user_intent,
             user_preference=excluded.user_preference,
             sector=excluded.sector,
             report_md=excluded.report_md,
             report_path=excluded.report_path,
             recommend_view=excluded.recommend_view,
             mention_count=shared_stock_report.mention_count + 1,
             last_mentioned_at=datetime('now'),
             updated_at=datetime('now')""",
        (
            code,
            name,
            chat_id or "local",
            username or "",
            intent,
            json.dumps(profile or {}, ensure_ascii=False, default=str),
            sector,
            md,
            path,
            _infer_view(report),
        ),
    )
    conn.commit()
    conn.close()
    try:
        from backend.telegram.memory import upsert_memory_item

        upsert_memory_item(
            "chat",
            chat_id or "local",
            "stock_interest",
            f"{code} {name}: Telegram 用户关注/提及，意图={intent}，板块={sector or '未知'}，上下文={_safe_context(context)}",
            0.72,
        )
    except Exception:
        pass
    return {"ok": True, "ts_code": code, "stock_name": name, "report_path": path}


def get_shared_stock_report(ts_code: str) -> str:
    code = normalize_ts_code(ts_code)
    conn = get_conn()
    row = conn.execute(
        """SELECT report_md, report_path FROM shared_stock_report
           WHERE ts_code=? ORDER BY updated_at DESC, id DESC LIMIT 1""",
        (code,),
    ).fetchone()
    conn.close()
    if row and row["report_md"]:
        return row["report_md"]
    path = _report_path(code)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return f"暂无 {code} 的共享研究报告。"
