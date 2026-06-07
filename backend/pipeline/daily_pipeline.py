"""每日流水线 — 数据检查 / 重试 / 复盘 / 撮合 / 报告"""

import json
import multiprocessing as mp
import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from chinese_calendar import is_holiday
from backend.db.repository import (
    get_conn,
    list_agents,
    get_positions,
    get_pending_orders,
    record_order_trace,
    refresh_decision_batch_status,
)
from backend.data.loader import load_daily, load_index_daily
from backend.trading.calculator import calc_total_assets, calc_cumulative_return
from backend.trading.rules import match_order_price, calc_buy_fee, calc_sell_fee, can_trade_today, normalize_ts_code
from backend.agents.base import AgentContext
from backend.agents.llm_agent import run_agent_review
from backend.agents.tools import filter_tools_by_names
from backend.pipeline.order_executor import execute_orders
from backend.logs.thinking_logger import log_thinking
from backend.logs.report_generator import generate_daily_report
from backend.evolution.engine import prepare_evolution_context, run_post_daily_evolution
from backend.evolution.race import compute_and_apply_race
from backend.evolution.reflection import maybe_schedule_reflection
from backend.evaluation import upsert_agent_eval_metric

# 重试间隔（分钟）：23:00首次 → 23:10 → 23:30 → 00:00 → 00:30 → 01:30 → 次日08:00 → 放弃
RETRY_BACKOFF_MINUTES = [10, 20, 30, 30, 60]
FALLBACK_RETRY_HOUR = 8
DEFAULT_REVIEW_TIME = os.environ.get("AGENT_REVIEW_TIME", "23:00")
DEFAULT_PUSH_TIME = os.environ.get("AGENT_PUSH_TIME", "23:00")
DATA_FETCH_TIME = os.environ.get("MARKET_DATA_FETCH_TIME", "18:00")
DATA_FRESHNESS_MIN_RATIO = float(os.environ.get("DATA_FRESHNESS_MIN_RATIO", "0.95"))
AGENT_REVIEW_TIMEOUT_SECONDS = int(os.environ.get("AGENT_REVIEW_TIMEOUT_SECONDS", "600"))

# 每日数据更新追踪（内存变量，进程重启重置也 OK，CSV freshness 会兜底）
_last_data_fetch_date: str = ""
_data_fetch_running = False
_data_fetch_state = {
    "running": False,
    "last_start": "",
    "last_end": "",
    "last_date": "",
    "last_result": None,
    "last_error": "",
}


def _agent_review_worker(queue, agent_id: int, agent_name: str, context: AgentContext,
                         thinking_log_path: str, reasoning_effort: str, max_tool_turns: int,
                         allowed_tools: list[str] | None):
    try:
        tools = filter_tools_by_names(allowed_tools)
        decision = run_agent_review(
            agent_id, agent_name, context, thinking_log_path,
            reasoning_effort=reasoning_effort,
            max_tool_turns=max_tool_turns,
            tools=tools,
        )
        queue.put({"ok": True, "decision": decision})
    except Exception as e:
        queue.put({"ok": False, "error": str(e)})


def run_agent_review_with_timeout(agent_id: int, agent_name: str, context: AgentContext,
                                  thinking_log_path: str, reasoning_effort: str,
                                  max_tool_turns: int = 8,
                                  allowed_tools: list[str] | None = None):
    """Run one Agent review in a child process so hung LLM calls cannot block the chain."""
    queue = mp.Queue(maxsize=1)
    proc = mp.Process(
        target=_agent_review_worker,
        args=(queue, agent_id, agent_name, context, thinking_log_path, reasoning_effort, max_tool_turns, allowed_tools),
        daemon=True,
    )
    proc.start()
    proc.join(AGENT_REVIEW_TIMEOUT_SECONDS)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        return None, f"Agent review timed out after {AGENT_REVIEW_TIMEOUT_SECONDS}s"
    if queue.empty():
        return None, "Agent review exited without result"
    payload = queue.get()
    if payload.get("ok"):
        return payload.get("decision"), ""
    return None, payload.get("error", "Agent review failed")


def _infer_market_regime(text: str) -> str:
    lowered = (text or "").lower()
    risk_off_words = ("risk-off", "退潮", "谨慎", "防守", "弱势", "分歧", "降低仓位", "控制仓位")
    risk_on_words = ("risk-on", "进攻", "强势", "主升", "情绪回暖", "放量", "主线", "高开")
    off_score = sum(1 for word in risk_off_words if word in lowered)
    on_score = sum(1 for word in risk_on_words if word in lowered)
    if off_score > on_score:
        return "risk-off"
    if on_score > off_score:
        return "risk-on"
    return "range-bound"


def _list_peer_shared_context(conn: sqlite3.Connection, agent_id: int, trade_date: str) -> list[dict]:
    rows = conn.execute(
        """SELECT c.agent_id, COALESCE(a.display_name, a.name) AS agent_name,
                  c.market_regime, c.confidence, c.summary, c.updated_at
           FROM agent_shared_context c
           JOIN agent_info a ON a.id=c.agent_id
           WHERE c.trade_date=? AND c.agent_id<>?
           ORDER BY c.updated_at DESC, c.agent_id ASC""",
        (trade_date, agent_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _format_peer_shared_context(rows: list[dict]) -> str:
    if not rows:
        return "暂无同伴 Agent 当日共享研判。"
    lines = []
    for row in rows[:6]:
        lines.append(
            f"- {row.get('agent_name') or row.get('agent_id')}: "
            f"{row.get('market_regime') or 'unknown'} "
            f"conf={float(row.get('confidence') or 0):.2f}; "
            f"{(row.get('summary') or '')[:300]}"
        )
    return "\n".join(lines)


def _upsert_shared_context(conn: sqlite3.Connection, agent_id: int, trade_date: str, decision) -> dict:
    summary = ((decision.market_analysis or "") + "\n" + (decision.risk_assessment or "")).strip()
    selected = decision.selected_stocks or []
    orders = decision.orders or []
    payload = {
        "selected_stocks": selected[:8],
        "planned_orders": [
            {
                "ts_code": o.get("ts_code"),
                "direction": o.get("direction"),
                "quantity": o.get("quantity"),
                "price": o.get("price"),
                "skill_id": o.get("skill_id"),
            }
            for o in orders[:12]
        ],
    }
    confidence = 0.5
    if selected:
        confidence += 0.15
    if orders:
        confidence += 0.15
    if summary:
        confidence += min(len(summary), 1200) / 1200 * 0.2
    row = {
        "market_regime": _infer_market_regime(summary),
        "confidence": round(min(confidence, 1.0), 4),
        "summary": summary[:1200] if summary else "Agent 未输出明确市场研判。",
        "payload": payload,
    }
    conn.execute(
        """INSERT INTO agent_shared_context
           (agent_id, trade_date, market_regime, confidence, summary, payload_json)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(agent_id, trade_date) DO UPDATE SET
           market_regime=excluded.market_regime,
           confidence=excluded.confidence,
           summary=excluded.summary,
           payload_json=excluded.payload_json,
           updated_at=datetime('now')""",
        (
            agent_id,
            trade_date,
            row["market_regime"],
            row["confidence"],
            row["summary"],
            json.dumps(payload, ensure_ascii=False, default=str),
        ),
    )
    return row


def _record_cross_agent_order_conflicts(conn: sqlite3.Connection, trade_date: str) -> int:
    rows = conn.execute(
        """SELECT o.id, o.agent_id, COALESCE(a.display_name, a.name) AS agent_name,
                  o.ts_code, o.stock_name, o.direction, o.quantity, o.price, o.decision_batch_id
           FROM agent_order o
           JOIN agent_info a ON a.id=o.agent_id
           WHERE o.trade_date=? AND o.status='pending'
           ORDER BY o.ts_code, o.agent_id, o.id""",
        (trade_date,),
    ).fetchall()
    by_code: dict[str, list[dict]] = {}
    for row in rows:
        by_code.setdefault(row["ts_code"], []).append(dict(row))

    warnings = 0
    for ts_code, orders in by_code.items():
        buys = [o for o in orders if o.get("direction") == "buy"]
        sells = [o for o in orders if o.get("direction") == "sell"]
        if not buys or not sells:
            continue
        for order in buys + sells:
            opposing = sells if order.get("direction") == "buy" else buys
            peers = [o for o in opposing if o.get("agent_id") != order.get("agent_id")]
            if not peers:
                continue
            peer_ids = sorted({int(o["id"]) for o in peers})
            reason = f"跨Agent方向冲突: {ts_code} 同日存在相反方向挂单"
            exists = conn.execute(
                """SELECT 1 FROM agent_order_trace
                   WHERE order_id=? AND event_type='cross_agent_conflict' AND reason=?
                   LIMIT 1""",
                (order["id"], reason),
            ).fetchone()
            if exists:
                continue
            record_order_trace(
                conn,
                int(order["id"]),
                "cross_agent_conflict",
                reason,
                payload={
                    "severity": "warning",
                    "ts_code": ts_code,
                    "current_order": order,
                    "opposing_orders": peers,
                    "opposing_order_ids": peer_ids,
                    "policy": "warn_only",
                },
            )
            warnings += 1
    return warnings


def get_data_fetch_state() -> dict:
    return dict(_data_fetch_state)


def _parse_data_fetch_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    except ValueError:
        return None


def _clear_stale_data_fetch_state(expected_day: str, now: datetime) -> bool:
    """Recover from a daemon thread that left the in-memory running flag stuck."""
    global _data_fetch_running
    if not _data_fetch_running:
        return False
    last_date = str(_data_fetch_state.get("last_date") or "")
    last_start = _parse_data_fetch_time(_data_fetch_state.get("last_start"))
    too_old = bool(last_start and now - last_start > timedelta(hours=4))
    wrong_day = bool(last_date and last_date != expected_day)
    if not (too_old or wrong_day):
        return False
    reason = "wrong_day" if wrong_day else "too_old"
    _data_fetch_running = False
    _data_fetch_state.update({
        "running": False,
        "last_error": f"stale fetch state cleared ({reason}, expected={expected_day}, last_date={last_date or '-'})",
    })
    print(f"[DataFetch] 清理陈旧抓取状态: {reason}, expected={expected_day}, last_date={last_date or '-'}")
    return True


def _latest_index_date() -> str | None:
    df = load_index_daily()
    if df is None or df.empty:
        return None
    return str(df["trade_date"].max())


def _latest_csv_trade_date(filepath: str) -> str | None:
    """Read the latest trade_date from a sorted CSV without loading the full file."""
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return None
            block = min(size, 4096)
            f.seek(-block, os.SEEK_END)
            tail = f.read().decode("utf-8", errors="ignore").strip().splitlines()
        if len(tail) < 2:
            return None
        last = tail[-1]
        return last.split(",", 1)[0].replace("-", "")
    except Exception:
        return None


def _save_index_incremental(new_data):
    """Save sh.000001 data to INDEX_DIR, not DAILY_DIR."""
    import pandas as pd
    from backend.config import INDEX_DIR

    os.makedirs(INDEX_DIR, exist_ok=True)
    filepath = os.path.join(INDEX_DIR, "000001.SH_daily.csv")
    new_data = new_data.copy()
    new_data["trade_date"] = new_data["trade_date"].astype(str).str.replace("-", "")
    if os.path.exists(filepath):
        existing = pd.read_csv(filepath)
        existing["trade_date"] = existing["trade_date"].astype(str)
        combined = pd.concat([existing, new_data], ignore_index=True)
    else:
        combined = new_data
    combined["trade_date"] = combined["trade_date"].astype(str).str.replace("-", "")
    combined = combined.drop_duplicates(subset=["trade_date"], keep="last")
    combined = combined.sort_values("trade_date")
    combined.to_csv(filepath, index=False)
    return len(new_data)


def fetch_todays_market_data() -> dict:
    """从 baostock 拉取最近交易日增量数据（指数 + 全量个股）。

    每天只执行一次。指数必须写入 data/index；个股写入 data/daily。
    """
    global _last_data_fetch_date, _data_fetch_running
    if _data_fetch_running:
        return {"status": "already_running", "date": _last_data_fetch_date}

    expected_day = _latest_trading_day(date.today()).strftime("%Y%m%d")
    if check_data_freshness(expected_day) and _last_data_fetch_date == expected_day:
        return {"status": "already_fetched", "date": expected_day, "data_ready": True}

    from backend.data.fetcher import (
        login_baostock, logout_baostock, fetch_daily_incremental,
        merge_and_save, _fetch_raw,
    )
    from backend.data.loader import list_main_board_stocks

    _data_fetch_running = True
    _data_fetch_state.update({
        "running": True,
        "last_start": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
        "last_end": "",
        "last_date": expected_day,
        "last_result": None,
        "last_error": "",
    })
    print(f"[DataFetch] 开始拉取 {expected_day} 的增量数据...")

    if not login_baostock():
        _data_fetch_running = False
        _data_fetch_state.update({"running": False, "last_error": "baostock login failed"})
        return {"status": "login_failed", "date": expected_day}

    result = {"status": "ok", "date": expected_day, "index": 0, "stocks": 0, "errors": 0}

    try:
        latest_index = _latest_index_date()
        start_date = latest_index or "2019-01-01"
        if len(start_date) == 8:
            start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        idx_data = _fetch_raw("sh.000001", start_date, date.today().strftime("%Y-%m-%d"))
        if idx_data is not None and len(idx_data) > 0:
            result["index"] = _save_index_incremental(idx_data)
            print(f"[DataFetch] 上证指数 +{len(idx_data)} 行")
    except Exception as e:
        print(f"[DataFetch] 指数更新失败: {e}")
        result["errors"] += 1

    try:
        stocks = list_main_board_stocks()
        total = len(stocks)
        print(f"[DataFetch] 检查 {total} 只个股增量...")
        for i, (_, row) in enumerate(stocks.iterrows(), 1):
            ts_code = row["ts_code"]
            try:
                new_data = fetch_daily_incremental(ts_code)
                if new_data is not None and len(new_data) > 0:
                    merge_and_save(ts_code, new_data)
                    result["stocks"] += 1
            except Exception as e:
                result["errors"] += 1
                if result["errors"] <= 5:
                    print(f"[DataFetch] {ts_code} 更新失败: {e}")
            if i % 300 == 0:
                print(f"[DataFetch] 进度 {i}/{total}, 有更新 {result['stocks']}, 错误 {result['errors']}")
        print(f"[DataFetch] 个股: {result['stocks']} 只有新数据")
    except Exception as e:
        print(f"[DataFetch] 个股批量更新失败: {e}")
        result["errors"] += 1
    finally:
        logout_baostock()
        _last_data_fetch_date = expected_day
        _data_fetch_running = False
        result["data_ready"] = check_data_freshness(expected_day)
        _data_fetch_state.update({
            "running": False,
            "last_end": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
            "last_result": result,
            "last_error": "" if result["errors"] == 0 else f"errors={result['errors']}",
        })
    return result


def maybe_start_market_data_fetch(now: datetime | None = None) -> dict:
    """Start market data fetch after DATA_FETCH_TIME on trading days."""
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    expected_day = _latest_trading_day(now.date()).strftime("%Y%m%d")
    current_hm = now.strftime("%H:%M")
    if current_hm < DATA_FETCH_TIME:
        return {"status": "not_due", "fetch_time": DATA_FETCH_TIME, "date": expected_day}
    cleared_stale = _clear_stale_data_fetch_state(expected_day, now)
    if _data_fetch_running:
        return {"status": "already_running", "date": expected_day}
    if check_data_freshness(expected_day):
        global _last_data_fetch_date
        _last_data_fetch_date = expected_day
        return {"status": "data_ready", "date": expected_day}
    threading.Thread(target=fetch_todays_market_data, daemon=True, name="market-data-fetch").start()
    return {"status": "started", "date": expected_day, "cleared_stale": cleared_stale}


def is_stock_trade_day(d: date | str = None) -> bool:
    """精准判断是否 A 股开盘日。

    规则：周末休市 + 法定节假日休市 + 调休上班日股市仍休。
    """
    if d is None:
        d = date.today()
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    if isinstance(d, datetime):
        d = d.date()

    if d.weekday() >= 5:
        return False
    return not is_holiday(d)


def _latest_trading_day(ref_date: date = None) -> date:
    """获取最近（含当天）的 A 股交易日。"""
    d = (ref_date or date.today())
    if isinstance(d, datetime):
        d = d.date()
    while not is_stock_trade_day(d):
        d = d - timedelta(days=1)
    return d


def _next_trading_day(ref_date: date | str) -> date:
    """获取 ref_date 之后的下一个 A 股交易日。"""
    if isinstance(ref_date, str):
        d = datetime.strptime(ref_date, "%Y%m%d").date()
    elif isinstance(ref_date, datetime):
        d = ref_date.date()
    else:
        d = ref_date
    d = d + timedelta(days=1)
    while not is_stock_trade_day(d):
        d = d + timedelta(days=1)
    return d


def check_data_freshness(trade_date: str) -> bool:
    """检查 trade_date 对应的指数和主板个股 CSV 是否已就绪。

    指数和个股都就绪后，才允许触发 Agent 复盘/交易。
    """
    from backend.config import DAILY_DIR, INDEX_DIR
    from backend.data.loader import list_main_board_stocks

    index_latest = _latest_csv_trade_date(os.path.join(INDEX_DIR, "000001.SH_daily.csv"))
    if not index_latest or index_latest < trade_date:
        return False

    try:
        stocks = list_main_board_stocks()
    except Exception:
        return False
    total = len(stocks)
    ready = 0
    missing_sample = []
    for _, row in stocks.iterrows():
        ts_code = row["ts_code"]
        latest = _latest_csv_trade_date(os.path.join(DAILY_DIR, f"{ts_code}_daily.csv"))
        if latest and latest >= trade_date:
            ready += 1
        elif len(missing_sample) < 5:
            missing_sample.append(f"{ts_code}:{latest or '-'}")
    if total == 0:
        return False
    ratio = ready / total
    if ratio < DATA_FRESHNESS_MIN_RATIO:
        print(
            f"[DataFreshness] {trade_date} coverage {ready}/{total}={ratio:.1%}, "
            f"need {DATA_FRESHNESS_MIN_RATIO:.0%}; missing sample: {', '.join(missing_sample)}"
        )
        return False
    if missing_sample:
        print(
            f"[DataFreshness] {trade_date} partial coverage accepted: "
            f"{ready}/{total}={ratio:.1%}; missing sample: {', '.join(missing_sample)}"
        )
    return True


def _parse_retry_at(value: str | None, now: datetime) -> datetime | None:
    """Parse retry timestamp. Supports legacy HH:MM values."""
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=now.tzinfo)
        except ValueError:
            pass
    try:
        hh, mm = text.split(":", 1)
        return now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except Exception:
        return None


def _format_retry_at(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _next_retry_time(now: datetime, retry_count: int) -> datetime | None:
    if retry_count < len(RETRY_BACKOFF_MINUTES):
        return now + timedelta(minutes=RETRY_BACKOFF_MINUTES[retry_count])
    if retry_count == len(RETRY_BACKOFF_MINUTES):
        next_day = now.date() + timedelta(days=1)
        return datetime.combine(next_day, datetime.min.time(), tzinfo=now.tzinfo).replace(
            hour=FALLBACK_RETRY_HOUR
        )
    return None


def snapshot_agent_state(agent_id: int) -> dict:
    """保存 Agent 当前状态快照，用于回滚。"""
    conn = get_conn()
    agent = conn.execute("SELECT * FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    positions = conn.execute(
        "SELECT * FROM agent_position WHERE agent_id=?", (agent_id,)
    ).fetchall()
    orders = conn.execute(
        "SELECT * FROM agent_order WHERE agent_id=?", (agent_id,)
    ).fetchall()
    max_order_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM agent_order WHERE agent_id=?", (agent_id,)
    ).fetchone()[0]
    max_trade_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM agent_trade_log WHERE agent_id=?", (agent_id,)
    ).fetchone()[0]
    max_report_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM agent_daily_report WHERE agent_id=?", (agent_id,)
    ).fetchone()[0]

    snapshot = {
        "agent": dict(agent) if agent else {},
        "positions": [dict(p) for p in positions],
        "orders": [dict(o) for o in orders],
        "max_order_id": max_order_id,
        "max_trade_id": max_trade_id,
        "max_report_id": max_report_id,
    }
    conn.close()
    return snapshot


def rollback_agent_state(agent_id: int, snapshot: dict):
    """回滚 Agent 状态到快照点。"""
    conn = get_conn()
    agent = snapshot.get("agent", {})
    if agent:
        conn.execute(
            "UPDATE agent_info SET current_cash=?, updated_at=datetime('now') WHERE id=?",
            (agent.get("current_cash", 0), agent_id),
        )
    # 删除快照后新增的订单、成交和日报，避免半完成状态残留。
    conn.execute("DELETE FROM agent_trade_log WHERE agent_id=? AND id>?", (agent_id, snapshot.get("max_trade_id", 0)))
    conn.execute("DELETE FROM agent_order WHERE agent_id=? AND id>?", (agent_id, snapshot.get("max_order_id", 0)))
    conn.execute("DELETE FROM agent_daily_report WHERE agent_id=? AND id>?", (agent_id, snapshot.get("max_report_id", 0)))

    # 恢复持仓
    conn.execute("DELETE FROM agent_position WHERE agent_id=?", (agent_id,))
    for p in snapshot.get("positions", []):
        conn.execute(
            """INSERT INTO agent_position (agent_id, ts_code, stock_name, quantity, available_shares,
               avg_cost, current_price, market_value, unrealized_pnl, realized_pnl, buy_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent_id, p["ts_code"], p.get("stock_name", ""), p["quantity"],
             p.get("available_shares", p["quantity"]), p["avg_cost"],
             p.get("current_price", 0), p.get("market_value", 0),
             p.get("unrealized_pnl", 0), p.get("realized_pnl", 0),
             p.get("buy_date", "")),
        )
    # 恢复订单状态和关键字段。保留原 id，便于 trade_log 外键仍一致。
    conn.execute("DELETE FROM agent_order WHERE agent_id=? AND id<=?", (agent_id, snapshot.get("max_order_id", 0)))
    for o in snapshot.get("orders", []):
        conn.execute(
            """INSERT INTO agent_order (id, agent_id, ts_code, stock_name, direction, order_type,
               quantity, price, trigger_price, condition_expr, open_get_in, reserved_cash,
               parent_order_id, oco_group, chase_enabled, chase_pct,
               split_group, split_seq, split_total, risk_control,
               decision_batch_id, fill_probability, price_aggressiveness, skill_id, skill_confidence,
               failure_attribution, evolution_mark, reason, fail_reason, status, trade_date,
               created_at, triggered_at, filled_at, expired_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (o["id"], agent_id, o["ts_code"], o.get("stock_name", ""), o["direction"],
             o.get("order_type", "limit"), o["quantity"], o.get("price", 0),
             o.get("trigger_price"), o.get("condition_expr", ""), o.get("open_get_in", 0),
             o.get("reserved_cash", 0), o.get("parent_order_id"), o.get("oco_group", ""),
             o.get("chase_enabled", 0), o.get("chase_pct", 0), o.get("split_group", ""),
             o.get("split_seq", 1), o.get("split_total", 1), o.get("risk_control", 0),
             o.get("decision_batch_id", ""), o.get("fill_probability"), o.get("price_aggressiveness"),
             o.get("skill_id", ""),
             o.get("skill_confidence", 0), o.get("failure_attribution", ""),
             o.get("evolution_mark", ""), o.get("reason", ""), o.get("fail_reason", ""),
             o.get("status", "pending"), o.get("trade_date", ""), o.get("created_at"),
             o.get("triggered_at"), o.get("filled_at"), o.get("expired_at")),
        )
    conn.commit()
    conn.close()


def _set_next_retry(agent_id: int, retry_count: int, next_at: str):
    conn = get_conn()
    conn.execute(
        """UPDATE agent_schedule SET retry_count=?, next_retry_at=?, updated_at=datetime('now')
           WHERE agent_id=?""",
        (retry_count, next_at, agent_id),
    )
    conn.commit()
    conn.close()


def _notify_waiting_data_once(agent_id: int, trade_date: str, next_retry: str):
    key = f"agent_waiting_data_notice:{agent_id}:{trade_date}"
    conn = get_conn()
    row = conn.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
    if row and row["value"] == "1":
        conn.close()
        return
    conn.execute(
        """INSERT INTO system_settings (key, value, updated_at)
           VALUES (?, '1', datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value='1', updated_at=datetime('now')""",
        (key,),
    )
    conn.commit()
    conn.close()
    try:
        from backend.telegram.gateway import push_agent_waiting_data
        push_agent_waiting_data(agent_id, trade_date, next_retry)
    except Exception as e:
        print(f"[Scheduler] waiting-data Telegram notice failed for agent {agent_id}: {e}")


def _setting_exists(key: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT value FROM system_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return bool(row)


def _mark_setting(key: str, value: str = "1"):
    conn = get_conn()
    conn.execute(
        """INSERT INTO system_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
        (key, value),
    )
    conn.commit()
    conn.close()


def _claim_setting(key: str, value: str = "in_progress") -> bool:
    conn = get_conn()
    cur = conn.execute(
        """INSERT OR IGNORE INTO system_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))""",
        (key, value),
    )
    conn.commit()
    claimed = cur.rowcount == 1
    conn.close()
    return claimed


def _push_agent_once(agent_id: int, trade_date: str) -> dict:
    key = f"telegram_pushed:{agent_id}:{trade_date}"
    if not _claim_setting(key):
        return {"ok": True, "skipped": True, "reason": "already_pushed_or_in_progress"}
    from backend.telegram.gateway import push_agent_summary
    result = push_agent_summary(agent_id, trade_date)
    sent = result.get("sent") or []
    if any((s.get("result") or {}).get("ok") for s in sent):
        _mark_setting(key, "1")
    else:
        _mark_setting(key, "failed")
    return result


def mark_to_market(agent_id: int, trade_date: str, conn: sqlite3.Connection):
    """更新持仓市值"""
    positions = get_positions(agent_id, conn)
    for pos in positions:
        df = load_daily(pos["ts_code"])
        if df is not None and not df.empty:
            latest = df[df["trade_date"] <= trade_date]
            if not latest.empty:
                current_price = latest.iloc[-1]["close"]
                market_value = current_price * pos["quantity"]
                unrealized = (current_price - pos["avg_cost"]) * pos["quantity"]
                conn.execute(
                    "UPDATE agent_position SET current_price=?, market_value=?, unrealized_pnl=?, updated_at=datetime('now') WHERE id=?",
                    (current_price, market_value, unrealized, pos["id"]),
                )


def _reserved_cash(agent_id: int, conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(reserved_cash), 0) FROM agent_order WHERE agent_id=? AND status='pending'",
        (agent_id,),
    ).fetchone()
    return float(row[0] or 0)


def _recent_orders(agent_id: int, trade_date: str, conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT id, ts_code, stock_name, direction, order_type, quantity, price, open_get_in, reserved_cash,
                  trigger_price, condition_expr, parent_order_id, oco_group, chase_enabled, chase_pct,
                  split_group, split_seq, split_total, risk_control,
                  decision_batch_id, fill_probability, price_aggressiveness,
                  skill_id, skill_confidence, failure_attribution, evolution_mark,
                  reason, status, trade_date, fail_reason, created_at, filled_at, expired_at
           FROM agent_order
           WHERE agent_id=? AND trade_date<=?
           ORDER BY trade_date DESC, id DESC LIMIT ?""",
        (agent_id, trade_date, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _release_pending_orders(agent_id: int, trade_date: str, conn: sqlite3.Connection, reason: str):
    rows = conn.execute(
        "SELECT * FROM agent_order WHERE agent_id=? AND trade_date=? AND status='pending'",
        (agent_id, trade_date),
    ).fetchall()
    for row in rows:
        reserved = float(row["reserved_cash"] or 0)
        if row["direction"] == "buy" and reserved > 0:
            agent = conn.execute("SELECT current_cash FROM agent_info WHERE id=?", (agent_id,)).fetchone()
            if agent:
                conn.execute(
                    "UPDATE agent_info SET current_cash=?, updated_at=datetime('now') WHERE id=?",
                    (float(agent["current_cash"] or 0) + reserved, agent_id),
                )
        conn.execute(
            """UPDATE agent_order
               SET status='expired', reserved_cash=0, fail_reason=?, failure_attribution='timing',
                   expired_at=datetime('now')
               WHERE id=?""",
            (reason, row["id"]),
        )
        record_order_trace(
            conn,
            row["id"],
            "replaced",
            reason,
            status_from=row["status"],
            status_to="expired",
            payload={"replaced_trade_date": trade_date},
        )
        refresh_decision_batch_status(conn, row["decision_batch_id"] if "decision_batch_id" in row.keys() else "")


def _expire_stale_pending_orders(agent_id: int, trade_date: str, conn: sqlite3.Connection,
                                 reason: str = "上一交易日未成交，复盘前自动取消"):
    rows = conn.execute(
        """SELECT * FROM agent_order
           WHERE agent_id=? AND status='pending' AND trade_date<?""",
        (agent_id, trade_date),
    ).fetchall()
    for row in rows:
        reserved = float(row["reserved_cash"] or 0)
        if row["direction"] == "buy" and reserved > 0:
            agent = conn.execute("SELECT current_cash FROM agent_info WHERE id=?", (agent_id,)).fetchone()
            if agent:
                conn.execute(
                    "UPDATE agent_info SET current_cash=?, updated_at=datetime('now') WHERE id=?",
                    (float(agent["current_cash"] or 0) + reserved, agent_id),
                )
        conn.execute(
            """UPDATE agent_order
               SET status='expired', reserved_cash=0, fail_reason=?, failure_attribution='timing',
                   expired_at=datetime('now')
               WHERE id=?""",
            (reason, row["id"]),
        )
        record_order_trace(
            conn,
            row["id"],
            "stale_expired",
            reason,
            status_from=row["status"],
            status_to="expired",
            payload={"current_trade_date": trade_date},
        )
        refresh_decision_batch_status(conn, row["decision_batch_id"] if "decision_batch_id" in row.keys() else "")


def _reserve_order_cash(agent_id: int, order_data: dict, conn: sqlite3.Connection) -> tuple[bool, float, str]:
    if order_data.get("direction", "buy") != "buy":
        pos = conn.execute(
            "SELECT quantity, available_shares, buy_date FROM agent_position WHERE agent_id=? AND ts_code=?",
            (agent_id, order_data.get("ts_code", "")),
        ).fetchone()
        quantity = int(order_data.get("quantity") or 0)
        available = int(pos["available_shares"] or 0) if pos else 0
        buy_date = str(pos["buy_date"] or "") if pos else ""
        if not pos or available < quantity:
            return False, 0.0, f"可卖股份不足，需{quantity}，可卖{available}"
        if buy_date and not can_trade_today(buy_date, order_data.get("trade_date") or ""):
            return False, 0.0, f"T+1限制：{buy_date}买入，当前不可卖出"
        return True, 0.0, ""
    quantity = int(order_data.get("quantity") or 0)
    price = float(order_data.get("price") or 0)
    reserve = price * quantity + calc_buy_fee(price * quantity)["total_cost"]
    agent = conn.execute("SELECT current_cash FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    cash = float(agent["current_cash"] or 0) if agent else 0.0
    if cash < reserve:
        return False, reserve, f"可用资金不足，需冻结{reserve:.2f}，当前{cash:.2f}"
    conn.execute(
        "UPDATE agent_info SET current_cash=?, updated_at=datetime('now') WHERE id=?",
        (cash - reserve, agent_id),
    )
    return True, reserve, ""


def _bool_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "是"}


def _expand_split_orders(order_data: dict) -> list[dict]:
    split_total = max(1, min(10, int(order_data.get("split_total") or 1)))
    quantity = int(order_data.get("quantity") or 0)
    if split_total <= 1 or quantity < 200:
        item = dict(order_data)
        item["split_total"] = 1
        item["split_seq"] = 1
        return [item]
    lot_count = quantity // 100
    split_total = min(split_total, lot_count)
    base_lots = lot_count // split_total
    extra = lot_count % split_total
    split_group = order_data.get("split_group") or f"split-{int(time.time() * 1000)}"
    rows = []
    for seq in range(1, split_total + 1):
        lots = base_lots + (1 if seq <= extra else 0)
        if lots <= 0:
            continue
        item = dict(order_data)
        item["quantity"] = lots * 100
        item["split_group"] = split_group
        item["split_seq"] = seq
        item["split_total"] = split_total
        rows.append(item)
    return rows or [dict(order_data)]


def _latest_position_price(pos: dict, trade_date: str) -> float:
    current = float(pos.get("current_price") or 0)
    if current > 0:
        return current
    df = load_daily(pos.get("ts_code", ""))
    if df is None or df.empty:
        return float(pos.get("avg_cost") or 0)
    latest = df[df["trade_date"] <= trade_date]
    if latest.empty:
        latest = df
    return float(latest.iloc[-1]["close"] or pos.get("avg_cost") or 0)


def _position_holding_days(pos: dict, trade_date: str) -> int:
    buy_date = str(pos.get("buy_date") or "")
    if not buy_date:
        return 0
    try:
        start = datetime.strptime(buy_date, "%Y%m%d").date()
        end = datetime.strptime(str(trade_date), "%Y%m%d").date()
        return max(0, (end - start).days)
    except Exception:
        return 0


def _previous_total_assets(agent_id: int, trade_date: str, initial_capital: float, conn: sqlite3.Connection) -> float:
    row = conn.execute(
        """SELECT total_assets FROM agent_daily_report
           WHERE agent_id=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1""",
        (agent_id, trade_date),
    ).fetchone()
    return float(row["total_assets"] if row else initial_capital)


def _risk_control_orders(
    agent_id: int,
    agent_name: str,
    trade_date: str,
    next_order_date: str,
    risk_cfg: dict,
    positions: list[dict],
    total_assets: float,
    initial_capital: float,
    conn: sqlite3.Connection,
) -> tuple[list[dict], dict]:
    """Create protective sell orders from hard risk rules."""
    max_daily_loss = float(risk_cfg.get("max_daily_loss") or 0)
    max_holding_days = int(risk_cfg.get("max_holding_days") or risk_cfg.get("max_position_days") or 0)
    stop_loss_pct = abs(float(risk_cfg.get("stop_loss_pct") or 0))
    stop_profit_pct = abs(float(risk_cfg.get("stop_profit_pct") or 0))
    prev_assets = _previous_total_assets(agent_id, trade_date, initial_capital, conn)
    daily_return = ((total_assets - prev_assets) / prev_assets) if prev_assets else 0.0
    circuit = bool(max_daily_loss > 0 and daily_return <= -max_daily_loss)
    orders: list[dict] = []
    meta = {
        "daily_return": round(daily_return * 100, 4),
        "max_daily_loss_pct": round(max_daily_loss * 100, 4),
        "circuit_breaker": circuit,
        "max_holding_days": max_holding_days,
    }
    existing = conn.execute(
        """SELECT ts_code, direction, order_type, oco_group, risk_control FROM agent_order
           WHERE agent_id=? AND trade_date=? AND status='pending'""",
        (agent_id, next_order_date),
    ).fetchall()
    existing_keys = {
        (r["ts_code"], r["order_type"], r["oco_group"] or "", int(r["risk_control"] or 0))
        for r in existing
    }
    existing_sell_codes = {r["ts_code"] for r in existing if r["direction"] == "sell"}
    for pos in positions:
        qty = int(pos.get("available_shares") or pos.get("quantity") or 0)
        if qty <= 0:
            continue
        price = _latest_position_price(pos, trade_date)
        if price <= 0:
            continue
        holding_days = _position_holding_days(pos, trade_date)
        reasons = []
        if circuit:
            reasons.append(f"日内亏损{daily_return*100:.2f}%触发熔断阈值{max_daily_loss*100:.2f}%")
        if max_holding_days > 0 and holding_days >= max_holding_days:
            reasons.append(f"持仓{holding_days}天达到最大持仓天数{max_holding_days}")
        if not reasons:
            continue
        key = (pos["ts_code"], "limit", "", 1)
        if key in existing_keys or pos["ts_code"] in existing_sell_codes:
            continue
        sell_price = round(price * 0.98, 2)
        orders.append({
            "ts_code": pos["ts_code"],
            "stock_name": pos.get("stock_name") or "",
            "direction": "sell",
            "order_type": "limit",
            "quantity": qty // 100 * 100,
            "price": sell_price,
            "open_get_in": True,
            "risk_control": True,
            "trade_date": next_order_date,
            "reason": "；".join(reasons) + "，系统生成保护性卖单。",
            "evolution_mark": "#risk_control#",
        })
    if stop_loss_pct > 0 or stop_profit_pct > 0:
        for pos in positions:
            qty = int(pos.get("available_shares") or pos.get("quantity") or 0)
            avg_cost = float(pos.get("avg_cost") or 0)
            if qty <= 0 or avg_cost <= 0:
                continue
            group = f"risk-oco-{agent_id}-{pos['ts_code']}-{next_order_date}"
            if stop_loss_pct > 0:
                trigger = round(avg_cost * (1 - stop_loss_pct / 100), 2)
                key = (pos["ts_code"], "stop_loss", group, 1)
                if key not in existing_keys:
                    orders.append({
                        "ts_code": pos["ts_code"],
                        "stock_name": pos.get("stock_name") or "",
                        "direction": "sell",
                        "order_type": "stop_loss",
                        "quantity": qty // 100 * 100,
                        "price": trigger,
                        "trigger_price": trigger,
                        "oco_group": group,
                        "risk_control": True,
                        "trade_date": next_order_date,
                        "reason": f"系统风险单：成本{avg_cost:.2f}，止损{stop_loss_pct:.2f}%触发价{trigger:.2f}。",
                        "evolution_mark": "#stop_loss#",
                    })
            if stop_profit_pct > 0:
                trigger = round(avg_cost * (1 + stop_profit_pct / 100), 2)
                key = (pos["ts_code"], "stop_profit", group, 1)
                if key not in existing_keys:
                    orders.append({
                        "ts_code": pos["ts_code"],
                        "stock_name": pos.get("stock_name") or "",
                        "direction": "sell",
                        "order_type": "stop_profit",
                        "quantity": qty // 100 * 100,
                        "price": trigger,
                        "trigger_price": trigger,
                        "oco_group": group,
                        "risk_control": True,
                        "trade_date": next_order_date,
                        "reason": f"系统风险单：成本{avg_cost:.2f}，止盈{stop_profit_pct:.2f}%触发价{trigger:.2f}。",
                        "evolution_mark": "#stop_profit#",
                    })
    return [o for o in orders if int(o.get("quantity") or 0) >= 100], meta


def _insert_planned_orders(
    conn: sqlite3.Connection,
    agent_id: int,
    agent_name: str,
    orders: list[dict],
    trade_date: str,
    decision_batch_id: str,
    source: str,
    stock_pool_policy: dict | None = None,
) -> int:
    inserted_order_count = 0
    for original in orders:
        for order_data in _expand_split_orders(original):
            order_data["trade_date"] = trade_date
            if stock_pool_policy is not None:
                pool_ok, pool_meta = _stock_pool_order_policy(order_data, stock_pool_policy)
                if not pool_ok:
                    print(
                        f"Agent {agent_name} order skipped: {order_data.get('ts_code')} "
                        f"{pool_meta.get('out_of_pool_reason')}"
                    )
                    continue
            else:
                pool_meta = {}
            ok, reserved, fail_reason = _reserve_order_cash(agent_id, order_data, conn)
            if not ok:
                print(f"Agent {agent_name} order skipped: {fail_reason}")
                continue
            cur = conn.execute(
                """INSERT INTO agent_order (agent_id, ts_code, stock_name, direction, order_type,
                   quantity, price, trigger_price, condition_expr, open_get_in, reserved_cash,
                   parent_order_id, oco_group, chase_enabled, chase_pct,
                   split_group, split_seq, split_total, risk_control,
                   decision_batch_id, fill_probability, price_aggressiveness, skill_id,
                   skill_confidence, evolution_mark, reason, trade_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (agent_id, order_data.get("ts_code", ""),
                 order_data.get("stock_name", ""),
                 order_data.get("direction", "buy"),
                 order_data.get("order_type", "limit"),
                 order_data.get("quantity", 100),
                 order_data.get("price", 0.0),
                 order_data.get("trigger_price") or None,
                 order_data.get("condition_expr", ""),
                 1 if _bool_value(order_data.get("open_get_in")) else 0,
                 reserved,
                 order_data.get("parent_order_id"),
                 order_data.get("oco_group", ""),
                 1 if _bool_value(order_data.get("chase_enabled")) else 0,
                 float(order_data.get("chase_pct") or 0),
                 order_data.get("split_group", ""),
                 int(order_data.get("split_seq") or 1),
                 int(order_data.get("split_total") or 1),
                 1 if _bool_value(order_data.get("risk_control")) else 0,
                 order_data.get("decision_batch_id") or decision_batch_id,
                 order_data.get("fill_probability"),
                 order_data.get("price_aggressiveness"),
                 order_data.get("skill_id", ""),
                 float(order_data.get("skill_confidence") or 0),
                 order_data.get("evolution_mark", ""),
                 order_data.get("reason", ""),
                 trade_date),
            )
            record_order_trace(
                conn,
                cur.lastrowid,
                "created",
                order_data.get("reason", ""),
                status_from="",
                status_to="pending",
                payload={
                    "source": source,
                    "decision_batch_id": decision_batch_id,
                    "direction": order_data.get("direction", "buy"),
                    "quantity": order_data.get("quantity", 100),
                    "price": order_data.get("price", 0.0),
                    "trigger_price": order_data.get("trigger_price") or None,
                    "condition_expr": order_data.get("condition_expr", ""),
                    "order_type": order_data.get("order_type", "limit"),
                    "open_get_in": bool(_bool_value(order_data.get("open_get_in"))),
                    "oco_group": order_data.get("oco_group", ""),
                    "chase_enabled": bool(_bool_value(order_data.get("chase_enabled"))),
                    "chase_pct": float(order_data.get("chase_pct") or 0),
                    "split_group": order_data.get("split_group", ""),
                    "split_seq": int(order_data.get("split_seq") or 1),
                    "split_total": int(order_data.get("split_total") or 1),
                    "risk_control": bool(_bool_value(order_data.get("risk_control"))),
                    "fill_probability": order_data.get("fill_probability"),
                    "price_aggressiveness": order_data.get("price_aggressiveness"),
                    "fill_estimate_sample_size": order_data.get("fill_estimate_sample_size"),
                    "skill_id": order_data.get("skill_id", ""),
                    **pool_meta,
                },
            )
            inserted_order_count += 1
    if inserted_order_count:
        refresh_decision_batch_status(conn, decision_batch_id)
    return inserted_order_count


def _estimate_order_execution(order_data: dict, asof_trade_date: str, lookback: int = 60) -> dict:
    ts_code = order_data.get("ts_code", "")
    df = load_daily(ts_code)
    price = float(order_data.get("price") or 0)
    if df is None or df.empty or price <= 0:
        return {"fill_probability": None, "price_aggressiveness": None, "sample_size": 0}
    recent = df[df["trade_date"].astype(str) <= str(asof_trade_date)].tail(lookback).copy()
    if recent.empty:
        return {"fill_probability": None, "price_aggressiveness": None, "sample_size": 0}
    for col in ("open", "high", "low", "close"):
        recent[col] = recent[col].astype(float)
    direction = order_data.get("direction", "buy")
    open_get_in = bool(order_data.get("open_get_in"))
    if direction == "buy":
        hits = recent["open"] <= price if open_get_in else recent["low"] <= price
    else:
        hits = recent["open"] >= price if open_get_in else recent["high"] >= price
    latest_close = float(recent.iloc[-1]["close"] or 0)
    aggressiveness = ((price / latest_close - 1) * 100) if latest_close else None
    return {
        "fill_probability": round(float(hits.mean() * 100), 2) if len(hits) else None,
        "price_aggressiveness": round(float(aggressiveness), 2) if aggressiveness is not None else None,
        "sample_size": int(len(recent)),
    }


def _classify_stock_board(ts_code: str) -> str:
    code = normalize_ts_code(ts_code)
    prefix = code.split(".", 1)[0]
    suffix = code.split(".", 1)[1] if "." in code else ""
    if suffix == "BJ" or prefix.startswith(("43", "83", "87", "88", "92")):
        return "bj"
    if prefix.startswith("688"):
        return "star"
    if prefix.startswith(("300", "301")):
        return "chinext"
    return "main_sme"


def _agent_total_assets(agent_id: int, conn: sqlite3.Connection) -> float:
    agent = conn.execute("SELECT current_cash FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    cash = float(agent["current_cash"] or 0) if agent else 0.0
    market_value = float(conn.execute(
        "SELECT COALESCE(SUM(market_value), 0) FROM agent_position WHERE agent_id=?",
        (agent_id,),
    ).fetchone()[0] or 0)
    frozen = _reserved_cash(agent_id, conn)
    return cash + market_value + frozen


def _days_since_first_trade(agent_id: int, trade_date: str, conn: sqlite3.Connection) -> int:
    first = conn.execute(
        "SELECT MIN(trade_date) AS first_date FROM agent_trade_log WHERE agent_id=? AND trade_date IS NOT NULL AND trade_date!=''",
        (agent_id,),
    ).fetchone()
    first_date = first["first_date"] if first else None
    if not first_date:
        return 0
    try:
        start = datetime.strptime(str(first_date), "%Y%m%d").date()
        end = datetime.strptime(str(trade_date), "%Y%m%d").date()
        return max(0, (end - start).days)
    except Exception:
        return 0


def _effective_board_permissions(agent_id: int, trade_date: str, risk_cfg: dict,
                                 conn: sqlite3.Connection) -> dict:
    mode = risk_cfg.get("board_permission_mode") or "auto"
    configured = risk_cfg.get("board_permissions") or {}
    base = {
        "main_sme": True,
        "chinext": False,
        "star": False,
        "bj": False,
    }
    if mode == "manual":
        base.update({k: bool(configured.get(k)) for k in base})
        base["main_sme"] = True
        base["mode"] = "manual"
        return base
    total_assets = _agent_total_assets(agent_id, conn)
    days = _days_since_first_trade(agent_id, trade_date, conn)
    base.update({
        "chinext": total_assets >= 200000 and days >= 60,
        "star": total_assets >= 600000 and days >= 60,
        "bj": total_assets >= 600000 and days >= 60,
        "mode": "auto",
        "total_assets": round(total_assets, 2),
        "days_since_first_trade": days,
    })
    return base


def _validate_decision_order_prices(agent_id: int, decision, trade_date: str,
                                    risk_cfg: dict, conn: sqlite3.Connection) -> list[dict]:
    invalid = []
    if not decision:
        return invalid
    board_permissions = _effective_board_permissions(agent_id, trade_date, risk_cfg, conn)
    for order in decision.orders or []:
        ts_code = order.get("ts_code", "")
        if order.get("direction", "buy") == "buy":
            board = _classify_stock_board(ts_code)
            if not board_permissions.get(board, False):
                invalid.append({
                    "order": order,
                    "board": board,
                    "board_permissions": board_permissions,
                    "error": "当前 Agent 未开启该板块买入权限",
                })
                continue
        df = load_daily(ts_code)
        price = float(order.get("price") or 0)
        if df is None or df.empty or price <= 0:
            invalid.append({"order": order, "error": "缺少行情或挂单价"})
            continue
        close = float(df.iloc[-1]["close"] or 0)
        lower = round(close * 0.9, 2)
        upper = round(close * 1.1, 2)
        if not (lower <= price <= upper):
            invalid.append({
                "order": order,
                "latest_close": round(close, 2),
                "lower_limit": lower,
                "upper_limit": upper,
                "error": "挂单价超出参考收盘价 ±10%",
            })
    return invalid


def _load_stock_pool_policy(agent_id: int, risk_cfg: dict, conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """SELECT ts_code, stock_name, note
           FROM agent_stock_pool
           WHERE agent_id=? AND enabled=1
           ORDER BY id ASC""",
        (agent_id,),
    ).fetchall()
    stocks = [dict(r) for r in rows]
    return {
        "enabled": bool(risk_cfg.get("stock_pool_enabled")),
        "allow_out_of_pool": bool(risk_cfg.get("allow_out_of_pool")),
        "stocks": stocks,
        "codes": {normalize_ts_code(r["ts_code"]) for r in stocks},
    }


def _stock_pool_order_policy(order_data: dict, policy: dict) -> tuple[bool, dict]:
    direction = str(order_data.get("direction") or "buy")
    code = normalize_ts_code(order_data.get("ts_code", ""))
    if direction == "sell":
        return True, {
            "pool_status": "position_exit",
            "stock_pool_enabled": bool(policy.get("enabled")),
            "out_of_pool_reason": "",
        }
    if not policy.get("enabled"):
        return True, {
            "pool_status": "disabled",
            "stock_pool_enabled": False,
            "out_of_pool_reason": "",
        }
    if code in (policy.get("codes") or set()):
        return True, {
            "pool_status": "in_pool",
            "stock_pool_enabled": True,
            "out_of_pool_reason": "",
        }
    reason = str(order_data.get("reason") or "").strip()
    if policy.get("allow_out_of_pool"):
        return True, {
            "pool_status": "out_of_pool_explore",
            "stock_pool_enabled": True,
            "out_of_pool_reason": reason[:500],
        }
    return False, {
        "pool_status": "blocked_out_of_pool",
        "stock_pool_enabled": True,
        "out_of_pool_reason": "股票池硬限制开启，且未允许池外探索",
    }


def run_daily_pipeline(trade_date: str = None, agent_ids: list[int] | None = None,
                       fail_fast: bool = False):
    """运行每日流水线

    Args:
        trade_date: 交易日 (YYYYMMDD)，默认今天
    """
    if trade_date is None:
        trade_date = date.today().strftime("%Y%m%d")

    conn = get_conn()
    all_agents = list_agents(conn)
    if agent_ids is not None:
        allowed = set(agent_ids)
        all_agents = [a for a in all_agents if a["id"] in allowed]

    next_order_date = _next_trading_day(trade_date).strftime("%Y%m%d")
    results = {}
    for agent in all_agents:
        if agent.get("status") in ("disabled", "paused"):
            results[agent["display_name"]] = {"skipped": True, "reason": agent.get("status")}
            continue

        agent_id = agent["id"]
        agent_name = agent["display_name"]

        _expire_stale_pending_orders(agent_id, trade_date, conn)
        mark_to_market(agent_id, trade_date, conn)
        positions = get_positions(agent_id, conn)

        price_data = {}
        watch_codes = {p["ts_code"] for p in positions}
        watch_codes.update(o["ts_code"] for o in get_pending_orders(agent_id, trade_date, conn))
        for ts_code in watch_codes:
            df = load_daily(ts_code)
            if df is not None and not df.empty:
                latest = df[df["trade_date"] <= trade_date]
                if not latest.empty:
                    row = latest.iloc[-1]
                    price_data[ts_code] = {
                        "open": row["open"], "high": row["high"],
                        "low": row["low"], "close": row["close"],
                        "pct_chg": row.get("pct_chg", 0),
                        "is_st": row.get("is_st", 0),
                    }

        conn.commit()
        trades = execute_orders(agent_id, trade_date, price_data)
        mark_to_market(agent_id, trade_date, conn)
        agent_after = conn.execute("SELECT * FROM agent_info WHERE id=?", (agent_id,)).fetchone()
        positions = get_positions(agent_id, conn)
        frozen_cash = _reserved_cash(agent_id, conn)
        total_assets = calc_total_assets(agent_after["current_cash"], positions) + frozen_cash
        cumulative_return = calc_cumulative_return(total_assets, agent_after["initial_capital"])

        context = AgentContext(
            trade_date=trade_date,
            cash=agent_after["current_cash"],
            total_assets=total_assets,
            initial_capital=agent_after["initial_capital"],
            cumulative_return=cumulative_return,
            positions=positions,
            recent_trades=trades,
            recent_orders=_recent_orders(agent_id, trade_date, conn),
            frozen_cash=frozen_cash,
            evolution_context=prepare_evolution_context(agent_id, agent_name, trade_date, conn),
        )
        peer_context = _list_peer_shared_context(conn, agent_id, trade_date)
        context.evolution_context["peer_shared_context"] = peer_context
        conn.commit()

        thinking_log_path = f"logs/{trade_date}/{agent['name']}/thinking.log"
        risk_cfg = {}
        try:
            risk_cfg = json.loads(agent.get("risk_config", "{}"))
            reasoning_effort = risk_cfg.get("reasoning_effort", "high")
            if reasoning_effort not in ("high", "max"):
                reasoning_effort = "high"
            max_tool_turns = max(2, min(50, int(risk_cfg.get("max_tool_turns", 8) or 8)))
            board_permissions = _effective_board_permissions(agent_id, trade_date, risk_cfg, conn)
            stock_pool_policy = _load_stock_pool_policy(agent_id, risk_cfg, conn)
            context.evolution_context["agent_config"] = {
                "agent_type": agent.get("agent_type"),
                "style_prompt": risk_cfg.get("style_prompt") or "",
                "user_strategy_original": risk_cfg.get("user_strategy_original") or "",
                "preferred_strategies": risk_cfg.get("preferred_strategies") or [
                    s.strip() for s in str(agent.get("strategy_ids") or "").split(",") if s.strip()
                ],
                "allowed_tools": risk_cfg.get("allowed_tools") or [],
                "stage_prompts": risk_cfg.get("stage_prompts") or {},
                "board_permissions": board_permissions,
                "stock_pool_enabled": stock_pool_policy["enabled"],
                "allow_out_of_pool": stock_pool_policy["allow_out_of_pool"],
                "stock_pool": stock_pool_policy["stocks"],
                "peer_shared_context": _format_peer_shared_context(peer_context),
            }
            review_started = time.perf_counter()
            decision, review_error = run_agent_review_with_timeout(
                agent_id, agent_name, context, thinking_log_path, reasoning_effort, max_tool_turns,
                risk_cfg.get("allowed_tools") or None,
            )
            decision_latency_ms = (time.perf_counter() - review_started) * 1000
            if decision:
                log_thinking(agent_id, agent_name, trade_date, decision, thinking_log_path)
            elif review_error:
                print(f"Agent {agent_name} review skipped: {review_error}")
        except Exception as e:
            decision = None
            decision_latency_ms = 0.0
            print(f"Agent {agent_name} LLM call failed: {e}")
            if fail_fast:
                raise

        if decision:
            shared_context = _upsert_shared_context(conn, agent_id, trade_date, decision)
            invalid_orders = _validate_decision_order_prices(agent_id, decision, next_order_date, risk_cfg, conn)
            if invalid_orders:
                raise ValueError(f"Agent {agent_name} generated invalid order prices: {invalid_orders}")
            _release_pending_orders(agent_id, next_order_date, conn, "新复盘替换旧预操作单")
        else:
            shared_context = None

        if decision and decision.orders:
            decision_batch_id = f"{agent_id}-{trade_date}-{int(time.time())}"
            planned_orders = list(decision.orders or [])
            fill_values: list[float] = []
            for order_data in planned_orders:
                estimate = _estimate_order_execution(order_data, trade_date)
                order_data["decision_batch_id"] = decision_batch_id
                order_data["fill_probability"] = estimate.get("fill_probability")
                order_data["price_aggressiveness"] = estimate.get("price_aggressiveness")
                order_data["fill_estimate_sample_size"] = estimate.get("sample_size")
                if estimate.get("fill_probability") is not None:
                    fill_values.append(float(estimate["fill_probability"]))
            conn.execute(
                """INSERT INTO agent_decision_batch
                   (id, agent_id, trade_date, next_trade_date, order_count, buy_count, sell_count,
                    avg_fill_probability, summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   order_count=excluded.order_count, buy_count=excluded.buy_count,
                   sell_count=excluded.sell_count, avg_fill_probability=excluded.avg_fill_probability,
                   summary=excluded.summary, updated_at=datetime('now')""",
                (
                    decision_batch_id,
                    agent_id,
                    trade_date,
                    next_order_date,
                    len(planned_orders),
                    sum(1 for o in planned_orders if o.get("direction", "buy") == "buy"),
                    sum(1 for o in planned_orders if o.get("direction") == "sell"),
                    round(sum(fill_values) / len(fill_values), 2) if fill_values else None,
                    (decision.risk_assessment or decision.market_analysis or "")[:1200],
                ),
            )
            stock_pool_policy = _load_stock_pool_policy(agent_id, risk_cfg, conn)
            inserted_order_count = _insert_planned_orders(
                conn,
                agent_id,
                agent_name,
                planned_orders,
                next_order_date,
                decision_batch_id,
                "daily_review",
                stock_pool_policy,
            )
            if inserted_order_count:
                refresh_decision_batch_status(conn, decision_batch_id)
            else:
                conn.execute(
                    "UPDATE agent_decision_batch SET status='skipped', updated_at=datetime('now') WHERE id=?",
                    (decision_batch_id,),
                )

        risk_orders, risk_meta = _risk_control_orders(
            agent_id,
            agent_name,
            trade_date,
            next_order_date,
            risk_cfg,
            positions,
            total_assets,
            agent_after["initial_capital"],
            conn,
        )
        if risk_orders:
            risk_batch_id = f"risk-{agent_id}-{trade_date}-{int(time.time())}"
            conn.execute(
                """INSERT INTO agent_decision_batch
                   (id, agent_id, trade_date, next_trade_date, order_count, buy_count, sell_count,
                    avg_fill_probability, summary)
                   VALUES (?, ?, ?, ?, ?, 0, ?, NULL, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   order_count=excluded.order_count, sell_count=excluded.sell_count,
                   summary=excluded.summary, updated_at=datetime('now')""",
                (
                    risk_batch_id,
                    agent_id,
                    trade_date,
                    next_order_date,
                    len(risk_orders),
                    len(risk_orders),
                    json.dumps(risk_meta, ensure_ascii=False),
                ),
            )
            _insert_planned_orders(
                conn,
                agent_id,
                agent_name,
                risk_orders,
                next_order_date,
                risk_batch_id,
                "system_risk_control",
                None,
            )

        agent_after_orders = conn.execute("SELECT * FROM agent_info WHERE id=?", (agent_id,)).fetchone()
        frozen_cash = _reserved_cash(agent_id, conn)
        total_assets = calc_total_assets(agent_after_orders["current_cash"], positions) + frozen_cash
        cumulative_return = calc_cumulative_return(total_assets, agent_after_orders["initial_capital"])
        context.cash = agent_after_orders["current_cash"]
        context.total_assets = total_assets
        context.frozen_cash = frozen_cash

        prev_report = conn.execute(
            """SELECT total_assets FROM agent_daily_report
               WHERE agent_id=? AND trade_date<? ORDER BY trade_date DESC LIMIT 1""",
            (agent_id, trade_date),
        ).fetchone()
        prev_assets = float(prev_report["total_assets"] if prev_report else agent["initial_capital"])
        daily_pnl = total_assets - prev_assets
        daily_return = (daily_pnl / prev_assets * 100) if prev_assets else 0.0
        cumulative_pnl = total_assets - agent["initial_capital"]

        report_path = generate_daily_report(
            agent_id, agent_name, trade_date, context, trades, decision,
            daily_pnl=daily_pnl, daily_return=daily_return, cumulative_pnl=cumulative_pnl,
        )

        conn.execute(
            """INSERT INTO agent_daily_report (agent_id, trade_date, cash, market_value, total_assets,
               daily_pnl, daily_return, cumulative_pnl, cumulative_return, position_count, report_md_path, think_log_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent_id, trade_date) DO UPDATE SET
               cash=excluded.cash, market_value=excluded.market_value, total_assets=excluded.total_assets,
               daily_pnl=excluded.daily_pnl, daily_return=excluded.daily_return, cumulative_pnl=excluded.cumulative_pnl,
               cumulative_return=excluded.cumulative_return, position_count=excluded.position_count,
               report_md_path=excluded.report_md_path, think_log_path=excluded.think_log_path,
               updated_at=datetime('now')""",
            (agent_id, trade_date, context.cash,
             sum(float(p.get("market_value") or 0) for p in positions), total_assets,
             daily_pnl, daily_return, cumulative_pnl,
             cumulative_return, len(positions),
             report_path, thinking_log_path),
        )

        evolution = run_post_daily_evolution(
            agent_id, agent_name, trade_date, trades, decision,
            {
                "daily_pnl": daily_pnl,
                "daily_return": daily_return,
                "cumulative_pnl": cumulative_pnl,
                "cumulative_return": cumulative_return,
                "position_ratio": (
                    sum(float(p.get("market_value") or 0) for p in positions) / total_assets
                    if total_assets else 0.0
                ),
            },
            conn,
        )
        conn.execute(
            """UPDATE agent_daily_report
               SET factor_weight_log=?, risk_adjust_log=?, updated_at=datetime('now')
               WHERE agent_id=? AND trade_date=?""",
            (
                json.dumps(evolution.get("factor_weight_log", {}), ensure_ascii=False),
                evolution.get("risk_adjust_log", ""),
                agent_id,
                trade_date,
            ),
        )
        race = compute_and_apply_race(agent_id, trade_date, conn)
        reflection = maybe_schedule_reflection(agent_id, agent_name, trade_date, conn)
        eval_metric = upsert_agent_eval_metric(conn, agent_id, trade_date, decision, decision_latency_ms)

        results[agent_name] = {
            "trades": len(trades),
            "next_order_date": next_order_date,
            "new_orders": len(decision.orders) if decision and decision.orders else 0,
            "total_assets": total_assets,
            "cumulative_return": cumulative_return,
            "positions": len(positions),
            "evolution": evolution.get("risk_adjust_log", ""),
            "race": race,
            "reflection": reflection,
            "eval": eval_metric,
            "shared_context": shared_context,
        }

    conflict_warnings = _record_cross_agent_order_conflicts(conn, next_order_date)
    if conflict_warnings:
        results["_coordination"] = {
            "cross_agent_conflict_warnings": conflict_warnings,
            "policy": "warn_only",
            "trade_date": next_order_date,
        }
    conn.commit()
    conn.close()
    return results


def run_due_agents(now: datetime | None = None) -> dict:
    """定时调度入口：由系统 cron 高频调用（如每5分钟）。

    流程：
    1. 找到到点且今日未运行的 agent
    2. 检查最新K线数据是否就绪
    3. 未就绪 → 按退避策略等待重试
    4. 就绪 → 快照 → 运行流水线 → 失败则回滚
    """
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    current_hm = now.strftime("%H:%M")

    expected_trading_day = _latest_trading_day(now.date())
    expected_date_str = expected_trading_day.strftime("%Y%m%d")

    # 0. 21:00 后先触发行情数据更新，23:00 Agent 只消费已就绪的数据。
    try:
        maybe_start_market_data_fetch(now)
    except Exception as e:
        print(f"[run_due_agents] 数据拉取启动异常: {e}")

    conn = get_conn()
    rows = conn.execute(
        """SELECT a.*, s.enabled AS sched_enabled, s.review_time AS sched_review_time,
                  s.push_time AS sched_push_time, s.last_run_date, s.retry_count, s.next_retry_at
           FROM agent_info a
           LEFT JOIN agent_schedule s ON s.agent_id = a.id
           WHERE a.status='active'
             AND (s.enabled=1 OR a.schedule_enabled=1)"""
    ).fetchall()
    conn.close()

    results = {"date": now.strftime("%Y%m%d"), "due": 0, "agents": {}, "data_ready": False}

    # 检查数据是否就绪
    data_ready = check_data_freshness(expected_date_str)
    results["data_ready"] = data_ready
    results["expected_trading_day"] = expected_date_str

    if not data_ready:
        # 数据未就绪：为到点的 agent 设置重试时间
        for row in rows:
            r = dict(row)
            review_time = r.get("sched_review_time") or r.get("review_time") or DEFAULT_REVIEW_TIME
            if review_time > current_hm:
                continue  # 还没到复盘时间
            if r.get("last_run_date") == expected_date_str:
                continue  # 该交易日已经跑过了

            retry_count = r.get("retry_count") or 0
            next_at = _parse_retry_at(r.get("next_retry_at"), now)

            # 还在等待重试间隔
            if next_at and now < next_at:
                continue

            next_retry_dt = _next_retry_time(now, retry_count)
            if next_retry_dt is not None:
                next_retry = _format_retry_at(next_retry_dt)
                _set_next_retry(r["id"], retry_count + 1, next_retry)
                push_time = r.get("sched_push_time") or r.get("push_time") or DEFAULT_PUSH_TIME
                if current_hm >= push_time:
                    _notify_waiting_data_once(r["id"], expected_date_str, next_retry)
                results["agents"][r["display_name"]] = {
                    "status": "waiting_data",
                    "retry": retry_count + 1,
                    "next_retry_at": next_retry,
                }
            else:
                conn2 = get_conn()
                conn2.execute(
                    "UPDATE agent_schedule SET retry_count=0, next_retry_at=NULL, updated_at=datetime('now') WHERE agent_id=?",
                    (r["id"],),
                )
                conn2.commit()
                conn2.close()
                results["agents"][r["display_name"]] = {
                    "status": "data_unavailable",
                    "message": f"数据 {expected_date_str} 多次重试后仍未就绪，已跳过本轮",
                }

        results["due"] = len(results["agents"])
        return results

    # 数据已就绪：运行到点的 agent
    due = []
    push_due = []
    for row in rows:
        r = dict(row)
        review_time = r.get("sched_review_time") or r.get("review_time") or DEFAULT_REVIEW_TIME
        push_time = r.get("sched_push_time") or r.get("push_time") or DEFAULT_PUSH_TIME
        if r.get("last_run_date") == expected_date_str and current_hm >= push_time:
            push_due.append(r)
            continue
        if review_time > current_hm:
            continue
        if r.get("last_run_date") == expected_date_str:
            continue
        due.append(r)

    results["due"] = len(due) + len(push_due)

    if due:
        try:
            from backend.macro.report import generate_macro_report, has_usable_macro_report
            if not has_usable_macro_report(expected_date_str):
                results["macro_report"] = generate_macro_report(expected_date_str, force=False)
            else:
                results["macro_report"] = {"ok": True, "skipped": True, "trade_date": expected_date_str}
        except Exception as macro_error:
            results["macro_report"] = {"ok": False, "error": str(macro_error), "trade_date": expected_date_str}

    for agent in push_due:
        agent_id = agent["id"]
        agent_name = agent["display_name"]
        try:
            results["agents"][agent_name] = {
                "status": "push_only",
                "telegram": _push_agent_once(agent_id, expected_date_str),
            }
        except Exception as push_error:
            results["agents"][agent_name] = {"status": "push_failed", "error": str(push_error)}

    for agent in due:
        agent_id = agent["id"]
        agent_name = agent["display_name"]

        # 快照
        snapshot = snapshot_agent_state(agent_id)

        try:
            print(f"[Scheduler] running agent {agent_id} {agent_name} for {expected_date_str}")
            pipeline_result = run_daily_pipeline(expected_date_str, [agent_id], fail_fast=True)
            results["agents"][agent_name] = pipeline_result.get(agent_name, {"status": "ok"})

            push_time = agent.get("sched_push_time") or agent.get("push_time") or DEFAULT_PUSH_TIME
            if current_hm >= push_time:
                try:
                    results["agents"][agent_name]["telegram"] = _push_agent_once(agent_id, expected_date_str)
                except Exception as push_error:
                    results["agents"][agent_name]["telegram"] = {"ok": False, "error": str(push_error)}

            # 清除重试状态
            conn2 = get_conn()
            conn2.execute(
                """INSERT INTO agent_schedule (agent_id, enabled, last_run_date, retry_count, next_retry_at)
                   VALUES (?, 1, ?, 0, NULL)
                   ON CONFLICT(agent_id) DO UPDATE SET
                   last_run_date=excluded.last_run_date, retry_count=0, next_retry_at=NULL,
                   updated_at=datetime('now')""",
                (agent_id, expected_date_str),
            )
            conn2.commit()
            conn2.close()
        except Exception as e:
            # 回滚
            rollback_agent_state(agent_id, snapshot)
            print(f"[Scheduler] agent {agent_id} {agent_name} rolled back: {e}")
            results["agents"][agent_name] = {"status": "rolled_back", "error": str(e)}

    return results


def simulate_day(trade_date: str):
    """模拟撮合（不调用 LLM）"""
    conn = get_conn()
    agents = list_agents(conn)
    results = {}

    for agent in agents:
        if agent.get("status") in ("disabled", "paused"):
            continue
        agent_id = agent["id"]
        mark_to_market(agent_id, trade_date, conn)
        positions = get_positions(agent_id, conn)
        price_data = {}
        for pos in positions:
            df = load_daily(pos["ts_code"])
            if df is not None and not df.empty:
                latest = df[df["trade_date"] <= trade_date]
                if not latest.empty:
                    row = latest.iloc[-1]
                    price_data[pos["ts_code"]] = {
                        "open": row["open"], "high": row["high"],
                        "low": row["low"], "close": row["close"],
                        "pct_chg": row.get("pct_chg", 0),
                        "is_st": row.get("is_st", 0),
                    }
        trades = execute_orders(agent_id, trade_date, price_data)
        results[agent["display_name"]] = {"trades": len(trades)}

    conn.commit()
    conn.close()
    return results
