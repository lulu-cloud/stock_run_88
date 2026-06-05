"""Daily macro market report generation.

The macro report centralizes public market facts before trading agents wake up.
Trading agents can read one report instead of repeatedly calling market,
policy, LHB and limit-up tools.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from backend.config import REPORTS_DIR
from backend.data.indicators import (
    compute_market_breadth,
    compute_market_strength_by_sector,
    compute_sector_temperature,
)
from backend.data.loader import load_index_daily
from backend.db.repository import get_conn, get_read_conn
from backend.llm.client import chat
from backend.policy.reader import extract_policy_signals
from backend.trading.rules import normalize_ts_code


MACRO_SYSTEM_PROMPT = """你是A股市场宏观分析Agent。

你只能基于用户提供的结构化数据做归纳，不要编造行情、政策、席位或业绩数据。
输出必须是合法 JSON，不要输出 Markdown 代码块。

JSON 字段：
{
  "market_regime": "risk_on/neutral/risk_off",
  "summary": "200字以内市场摘要",
  "hot_sectors": ["板块1", "板块2"],
  "risk_sectors": ["板块1", "板块2"],
  "limit_up_summary": "涨停/炸板/跌停情绪摘要",
  "lhb_summary": "龙虎榜和机构/营业部资金摘要",
  "institution_signal": "机构席位信号",
  "policy_signal": "政策方向",
  "fundamental_events": "业绩快报/预告事件",
  "trade_agent_guidance": "给交易Agent的仓位和选股建议",
  "risk_warnings": ["风险1", "风险2"]
}
"""


def resolve_trade_date(trade_date: str = "") -> str:
    if trade_date:
        return str(trade_date).replace("-", "")[:8]
    df = load_index_daily()
    if df is not None and not df.empty:
        return str(df.iloc[-1]["trade_date"]).replace("-", "")[:8]
    return datetime.now().strftime("%Y%m%d")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _safe_records(df: pd.DataFrame | None, limit: int = 10, sort_by: str = "", reverse: bool = True) -> list[dict]:
    if df is None or df.empty:
        return []
    frame = df.copy()
    if sort_by and sort_by in frame.columns:
        frame[sort_by] = pd.to_numeric(frame[sort_by], errors="coerce")
        frame = frame.sort_values(sort_by, ascending=not reverse)
    return _jsonable(frame.head(limit).to_dict(orient="records"))


def _source_error(name: str, exc: Exception) -> dict:
    return {"source": name, "ok": False, "error": str(exc)}


def _source_ok(name: str, count: int = 0) -> dict:
    return {"source": name, "ok": True, "count": count}


def _load_akshare():
    import akshare as ak
    return ak


def collect_market_snapshot(trade_date: str) -> tuple[dict, list[dict]]:
    status = []
    market = {}
    try:
        breadth_raw = compute_market_breadth(trade_date)
        breadth = {
            k: v for k, v in breadth_raw.items()
            if k not in {"sector_stats"}
        }
        breadth["leaders"] = (breadth.get("leaders") or [])[:20]
        breadth["laggards"] = (breadth.get("laggards") or [])[:20]
        temperature = compute_sector_temperature(trade_date, 20)
        strength = compute_market_strength_by_sector(trade_date, 3, 10)
        index_df = load_index_daily()
        index_recent = []
        if index_df is not None and not index_df.empty:
            upto = index_df[index_df["trade_date"] <= trade_date].tail(5)
            index_recent = _safe_records(upto, 5)
        market = {
            "breadth": breadth,
            "sector_temperature": temperature,
            "sector_strength": strength,
            "index_recent": index_recent,
        }
        status.append(_source_ok("local_market", len(temperature.get("sectors", []))))
    except Exception as exc:
        status.append(_source_error("local_market", exc))
    return market, status


def collect_limit_up_snapshot(trade_date: str) -> tuple[dict, list[dict]]:
    status = []
    result = {}
    try:
        ak = _load_akshare()
    except Exception as exc:
        return {}, [_source_error("akshare", exc)]

    calls = {
        "limit_up_pool": ("stock_zt_pool_em", "涨跌幅"),
        "previous_limit_up_pool": ("stock_zt_pool_previous_em", "涨跌幅"),
        "strong_pool": ("stock_zt_pool_strong_em", "涨跌幅"),
        "broken_limit_up_pool": ("stock_zt_pool_zbgc_em", "涨跌幅"),
        "limit_down_pool": ("stock_zt_pool_dtgc_em", "涨跌幅"),
    }
    for key, (func_name, sort_by) in calls.items():
        try:
            df = getattr(ak, func_name)(date=trade_date)
            result[key] = {
                "count": int(len(df)),
                "top": _safe_records(df, 12, sort_by=sort_by, reverse=True),
            }
            status.append(_source_ok(func_name, len(df)))
        except Exception as exc:
            result[key] = {"count": 0, "top": [], "error": str(exc)}
            status.append(_source_error(func_name, exc))
    return result, status


def collect_lhb_snapshot(trade_date: str) -> tuple[dict, list[dict]]:
    status = []
    result = {}
    try:
        ak = _load_akshare()
    except Exception as exc:
        return {}, [_source_error("akshare_lhb", exc)]

    calls = {
        "daily_detail": ("stock_lhb_detail_daily_sina", {"date": trade_date}, "成交额"),
        "stock_stats_5d": ("stock_lhb_ggtj_sina", {"symbol": "5"}, "净额"),
        "broker_stats_5d": ("stock_lhb_yytj_sina", {"symbol": "5"}, "累积购买额"),
        "institution_track_5d": ("stock_lhb_jgzz_sina", {"symbol": "5"}, "净额"),
        "institution_detail": ("stock_lhb_jgmx_sina", {}, "机构席位买入额(万)"),
    }
    for key, (func_name, kwargs, sort_by) in calls.items():
        try:
            df = getattr(ak, func_name)(**kwargs)
            if key == "institution_detail" and "交易日期" in df.columns:
                date_text = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
                filtered = df[df["交易日期"].astype(str) == date_text]
                if not filtered.empty:
                    df = filtered
            result[key] = {
                "count": int(len(df)),
                "top": _safe_records(df, 12, sort_by=sort_by, reverse=True),
            }
            status.append(_source_ok(func_name, len(df)))
        except Exception as exc:
            result[key] = {"count": 0, "top": [], "error": str(exc)}
            status.append(_source_error(func_name, exc))
    return result, status


def collect_policy_snapshot(recency_days: int = 14) -> tuple[dict, list[dict]]:
    try:
        signals = extract_policy_signals(recency_days)
        return signals, [_source_ok("policy_signals", len(signals.get("top_industries", [])))]
    except Exception as exc:
        return {}, [_source_error("policy_signals", exc)]


def _bs_code(ts_code: str) -> str:
    code = normalize_ts_code(ts_code)
    if code.endswith(".SH"):
        return "sh." + code[:6]
    if code.endswith(".SZ"):
        return "sz." + code[:6]
    return code


def _collect_bs_rows(bs, query_fn: str, code: str, start_date: str, end_date: str) -> list[dict]:
    rs = getattr(bs, query_fn)(code, start_date=start_date, end_date=end_date)
    rows = []
    while (rs.error_code == "0") and rs.next():
        rows.append(dict(zip(rs.fields, rs.get_row_data())))
    return rows


def _candidate_codes(snapshot: dict) -> list[str]:
    codes = []
    for item in (snapshot.get("market", {}).get("breadth", {}).get("leaders") or [])[:10]:
        codes.append(item.get("ts_code", ""))
    for pool in ("limit_up_pool", "strong_pool", "broken_limit_up_pool"):
        for item in (snapshot.get("limit_up", {}).get(pool, {}).get("top") or [])[:8]:
            code = item.get("代码") or item.get("股票代码") or ""
            if code:
                codes.append(normalize_ts_code(str(code)))
    for item in (snapshot.get("lhb", {}).get("daily_detail", {}).get("top") or [])[:8]:
        code = item.get("股票代码") or ""
        if code:
            codes.append(normalize_ts_code(str(code)))
    return [c for c in dict.fromkeys(codes) if c][:25]


def collect_fundamental_event_snapshot(trade_date: str, snapshot: dict) -> tuple[dict, list[dict]]:
    try:
        import baostock as bs
    except Exception as exc:
        return {"events": []}, [_source_error("baostock", exc)]

    end_dt = datetime.strptime(trade_date, "%Y%m%d")
    start_date = (end_dt - timedelta(days=120)).strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")
    codes = _candidate_codes(snapshot)
    status = []
    events = []
    lg = bs.login()
    if getattr(lg, "error_code", "") != "0":
        return {"events": [], "error": getattr(lg, "error_msg", "baostock login failed")}, [
            {"source": "baostock_login", "ok": False, "error": getattr(lg, "error_msg", "baostock login failed")}
        ]
    try:
        for code in codes[:20]:
            bs_code = _bs_code(code)
            try:
                express = _collect_bs_rows(bs, "query_performance_express_report", bs_code, start_date, end_date)
                forecast = _collect_bs_rows(bs, "query_forecast_report", bs_code, start_date, end_date)
                for row in express[-2:]:
                    events.append({"ts_code": code, "type": "performance_express", "data": row})
                for row in forecast[-2:]:
                    events.append({"ts_code": code, "type": "forecast", "data": row})
            except Exception as exc:
                status.append(_source_error(f"baostock_fundamental_{code}", exc))
        status.append(_source_ok("baostock_fundamental", len(events)))
    finally:
        bs.logout()
    return {"events": _jsonable(events[:30]), "candidate_count": len(codes)}, status


def _extract_json(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return {}
    return {}


def _fallback_structured(snapshot: dict) -> dict:
    breadth = snapshot.get("market", {}).get("breadth", {})
    temp = snapshot.get("market", {}).get("sector_temperature", {})
    hot = [s.get("sector") for s in (temp.get("sectors") or [])[:6] if s.get("sector")]
    weak = [s.get("sector") for s in (temp.get("weak_sectors") or [])[:5] if s.get("sector")]
    return {
        "market_regime": breadth.get("market_regime") or temp.get("market_regime") or "unknown",
        "summary": (
            f"市场宽度: 上涨{breadth.get('up_count', 0)}家、下跌{breadth.get('down_count', 0)}家，"
            f"涨停{breadth.get('limit_up_count', 0)}家，risk-on分数{breadth.get('risk_on_score', 0)}。"
        ),
        "hot_sectors": hot,
        "risk_sectors": weak,
        "limit_up_summary": _format_limit_summary(snapshot.get("limit_up", {})),
        "lhb_summary": _format_lhb_summary(snapshot.get("lhb", {})),
        "institution_signal": "机构席位数据见龙虎榜摘要。",
        "policy_signal": (snapshot.get("policy", {}) or {}).get("summary", "暂无政策信号摘要。"),
        "fundamental_events": f"候选股业绩事件 {len(snapshot.get('fundamental', {}).get('events', []))} 条。",
        "trade_agent_guidance": "先按市场状态控制仓位，再在热点板块中结合趋势、基本面和流动性筛选。",
        "risk_warnings": ["外部数据可能部分缺失，交易Agent需要对个股继续核验。"],
    }


def _format_limit_summary(limit_up: dict) -> str:
    return (
        f"涨停{limit_up.get('limit_up_pool', {}).get('count', 0)}家，"
        f"强势池{limit_up.get('strong_pool', {}).get('count', 0)}家，"
        f"炸板{limit_up.get('broken_limit_up_pool', {}).get('count', 0)}家，"
        f"跌停{limit_up.get('limit_down_pool', {}).get('count', 0)}家。"
    )


def _format_lhb_summary(lhb: dict) -> str:
    return (
        f"当日龙虎榜{lhb.get('daily_detail', {}).get('count', 0)}条，"
        f"近5日个股统计{lhb.get('stock_stats_5d', {}).get('count', 0)}条，"
        f"机构追踪{lhb.get('institution_track_5d', {}).get('count', 0)}条。"
    )


def _build_markdown(trade_date: str, structured: dict, snapshot: dict, data_status: list[dict]) -> str:
    status_text = " / ".join(
        f"{s.get('source')}:{'ok' if s.get('ok') else 'fail'}" for s in data_status[:18]
    )
    hot = "、".join(structured.get("hot_sectors") or []) or "暂无"
    weak = "、".join(structured.get("risk_sectors") or []) or "暂无"
    warnings = "\n".join(f"- {x}" for x in (structured.get("risk_warnings") or [])) or "- 暂无"
    leaders = snapshot.get("market", {}).get("breadth", {}).get("leaders", [])[:8]
    leader_text = "、".join(f"{x.get('name')}({x.get('pct_chg')}%)" for x in leaders) or "暂无"
    return f"""# 每日宏观市场报告 {trade_date}

## 市场状态

- 状态: {structured.get("market_regime", "unknown")}
- 摘要: {structured.get("summary", "")}
- 热点板块: {hot}
- 风险板块: {weak}
- 领涨股: {leader_text}

## 情绪与资金

- 涨停/炸板/跌停: {structured.get("limit_up_summary", "")}
- 龙虎榜: {structured.get("lhb_summary", "")}
- 机构席位: {structured.get("institution_signal", "")}

## 政策与基本面

- 政策方向: {structured.get("policy_signal", "")}
- 业绩事件: {structured.get("fundamental_events", "")}

## 给交易 Agent 的指引

{structured.get("trade_agent_guidance", "")}

## 风险提示

{warnings}

## 数据质量

{status_text}
"""


def _report_path(trade_date: str) -> str:
    return os.path.join(REPORTS_DIR, "macro", trade_date, "macro_review.md")


def get_macro_report_row(trade_date: str = "") -> dict | None:
    effective_date = resolve_trade_date(trade_date)
    conn = get_read_conn()
    try:
        if trade_date:
            row = conn.execute(
                "SELECT * FROM macro_daily_report WHERE trade_date=?",
                (effective_date,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM macro_daily_report ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    return dict(row) if row else None


def has_usable_macro_report(trade_date: str) -> bool:
    row = get_macro_report_row(trade_date)
    return bool(row and row.get("status") in ("complete", "partial"))


def get_macro_daily_report_text(trade_date: str = "") -> str:
    row = get_macro_report_row(trade_date)
    if not row:
        return "暂无每日宏观市场报告。"
    structured = json.loads(row.get("structured_json") or "{}")
    return "\n".join([
        f"宏观报告日期: {row.get('trade_date')} 状态: {row.get('status')}",
        f"市场状态: {row.get('market_regime')} risk-on: {row.get('risk_on_score')}",
        f"热点板块: {', '.join(structured.get('hot_sectors') or []) or '暂无'}",
        f"风险板块: {', '.join(structured.get('risk_sectors') or []) or '暂无'}",
        "",
        row.get("report_md") or "",
    ]).strip()


def generate_macro_report(trade_date: str = "", force: bool = False) -> dict:
    effective_date = resolve_trade_date(trade_date)
    existing = get_macro_report_row(effective_date)
    if existing and existing.get("status") in ("complete", "partial") and not force:
        return {"ok": True, "skipped": True, "report": existing}

    started = time.perf_counter()
    snapshot: dict[str, Any] = {"trade_date": effective_date}
    data_status: list[dict] = []

    market, status = collect_market_snapshot(effective_date)
    snapshot["market"] = market
    data_status.extend(status)

    limit_up, status = collect_limit_up_snapshot(effective_date)
    snapshot["limit_up"] = limit_up
    data_status.extend(status)

    lhb, status = collect_lhb_snapshot(effective_date)
    snapshot["lhb"] = lhb
    data_status.extend(status)

    policy, status = collect_policy_snapshot(14)
    snapshot["policy"] = policy
    data_status.extend(status)

    fundamental, status = collect_fundamental_event_snapshot(effective_date, snapshot)
    snapshot["fundamental"] = fundamental
    data_status.extend(status)

    llm_error = ""
    structured = {}
    try:
        user_payload = json.dumps({
            "trade_date": effective_date,
            "snapshot": snapshot,
            "data_status": data_status,
        }, ensure_ascii=False, default=str)[:24000]
        structured = _extract_json(chat(MACRO_SYSTEM_PROMPT, user_payload, temperature=0.15))
    except Exception as exc:
        llm_error = str(exc)
    if not structured:
        structured = _fallback_structured(snapshot)
    if llm_error:
        data_status.append({"source": "macro_llm", "ok": False, "error": llm_error})
    else:
        data_status.append(_source_ok("macro_llm", 1))

    report_md = _build_markdown(effective_date, structured, snapshot, data_status)
    path = _report_path(effective_date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_md)

    failed = [s for s in data_status if not s.get("ok")]
    status_value = "complete" if not failed else ("failed" if len(failed) == len(data_status) else "partial")
    breadth = snapshot.get("market", {}).get("breadth", {})
    market_regime = structured.get("market_regime") or breadth.get("market_regime") or "unknown"
    risk_on_score = _safe_float(breadth.get("risk_on_score"), 0.0)
    latency_ms = (time.perf_counter() - started) * 1000

    conn = get_conn()
    conn.execute(
        """INSERT INTO macro_daily_report
           (trade_date, status, market_regime, risk_on_score, summary, report_md,
            report_path, raw_json, structured_json, data_status_json, latency_ms, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(trade_date) DO UPDATE SET
             status=excluded.status,
             market_regime=excluded.market_regime,
             risk_on_score=excluded.risk_on_score,
             summary=excluded.summary,
             report_md=excluded.report_md,
             report_path=excluded.report_path,
             raw_json=excluded.raw_json,
             structured_json=excluded.structured_json,
             data_status_json=excluded.data_status_json,
             latency_ms=excluded.latency_ms,
             updated_at=datetime('now')""",
        (
            effective_date,
            status_value,
            market_regime,
            risk_on_score,
            structured.get("summary", ""),
            report_md,
            path,
            json.dumps(snapshot, ensure_ascii=False, default=str),
            json.dumps(structured, ensure_ascii=False, default=str),
            json.dumps(data_status, ensure_ascii=False, default=str),
            round(latency_ms, 2),
        ),
    )
    conn.commit()
    conn.close()
    row = get_macro_report_row(effective_date)
    return {
        "ok": status_value in ("complete", "partial"),
        "status": status_value,
        "trade_date": effective_date,
        "latency_ms": round(latency_ms, 2),
        "failed_sources": failed,
        "report": row,
    }


def _parse_hm(value: str) -> tuple[int, int] | None:
    try:
        hour, minute = str(value).split(":", 1)
        return int(hour), int(minute)
    except Exception:
        return None


def _format_hm(total_minutes: int) -> str:
    total_minutes %= 24 * 60
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def get_effective_macro_report_time(conn=None) -> str:
    explicit = os.environ.get("MACRO_REPORT_TIME", "").strip()
    if explicit and _parse_hm(explicit):
        return explicit
    close_conn = False
    if conn is None:
        conn = get_read_conn()
        close_conn = True
    try:
        rows = conn.execute(
            """SELECT COALESCE(s.review_time, a.review_time, '23:00') AS review_time
               FROM agent_info a
               LEFT JOIN agent_schedule s ON s.agent_id=a.id
               WHERE a.status='active' AND (s.enabled=1 OR a.schedule_enabled=1)"""
        ).fetchall()
    finally:
        if close_conn:
            conn.close()
    minutes = []
    for row in rows:
        parsed = _parse_hm(row["review_time"])
        if parsed:
            minutes.append(parsed[0] * 60 + parsed[1])
    if not minutes:
        return "22:30"
    return _format_hm(min(minutes) - 30)
