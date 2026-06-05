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
  "chip_signal": "候选强势股筹码分布摘要",
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


def _plain_code(ts_code: str) -> str:
    return normalize_ts_code(ts_code)[:6]


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


def collect_chip_distribution(ts_code: str) -> tuple[dict, list[dict]]:
    """Collect qfq chip distribution for one stock from Eastmoney/AkShare."""
    code = normalize_ts_code(ts_code)
    try:
        ak = _load_akshare()
    except Exception as exc:
        return {"ok": False, "ts_code": code, "error": str(exc)}, [_source_error("akshare_cyq", exc)]
    try:
        df = ak.stock_cyq_em(symbol=_plain_code(code), adjust="qfq")
        if df is None or df.empty:
            return {"ok": False, "ts_code": code, "error": "筹码分布为空"}, [_source_ok("stock_cyq_em", 0)]
        frame = df.copy()
        latest = frame.iloc[-1].to_dict()
        prev = frame.iloc[-6].to_dict() if len(frame) >= 6 else frame.iloc[0].to_dict()
        avg_cost = _safe_float(latest.get("平均成本"))
        prev_avg_cost = _safe_float(prev.get("平均成本"))
        profit_ratio = _safe_float(latest.get("获利比例"))
        concentration_90 = _safe_float(latest.get("90集中度"))
        concentration_70 = _safe_float(latest.get("70集中度"))
        result = {
            "ok": True,
            "ts_code": code,
            "source_adjust": "qfq",
            "trade_date": str(latest.get("日期") or ""),
            "profit_ratio": round(profit_ratio, 4),
            "profit_ratio_pct": round(profit_ratio * 100, 2),
            "avg_cost": round(avg_cost, 3),
            "avg_cost_change_5d": round(avg_cost - prev_avg_cost, 3),
            "cost_90_low": _safe_float(latest.get("90成本-低")),
            "cost_90_high": _safe_float(latest.get("90成本-高")),
            "concentration_90": round(concentration_90, 4),
            "cost_70_low": _safe_float(latest.get("70成本-低")),
            "cost_70_high": _safe_float(latest.get("70成本-高")),
            "concentration_70": round(concentration_70, 4),
            "recent": _safe_records(frame.tail(8), 8),
        }
        return result, [_source_ok("stock_cyq_em", len(df))]
    except Exception as exc:
        return {"ok": False, "ts_code": code, "error": str(exc)}, [_source_error("stock_cyq_em", exc)]


def collect_chip_snapshot(snapshot: dict, max_codes: int = 12) -> tuple[dict, list[dict]]:
    """Collect qfq chip distribution for a compact list of hot candidates."""
    status: list[dict] = []
    rows = []
    for code in _candidate_codes(snapshot)[:max(1, int(max_codes or 12))]:
        item, item_status = collect_chip_distribution(code)
        status.extend(item_status)
        if item.get("ok"):
            rows.append(item)
    return {"items": rows, "candidate_count": len(rows)}, status


def collect_policy_snapshot(recency_days: int = 14) -> tuple[dict, list[dict]]:
    try:
        signals = extract_policy_signals(recency_days)
        return signals, [_source_ok("policy_signals", len(signals.get("top_industries", [])))]
    except Exception as exc:
        return {}, [_source_error("policy_signals", exc)]


def collect_stock_fundamental_events(ts_code: str, trade_date: str = "", days: int = 365) -> tuple[dict, list[dict]]:
    """Collect baostock express/forecast events for one stock."""
    code = normalize_ts_code(ts_code)
    try:
        import baostock as bs
    except Exception as exc:
        return {"events": [], "ts_code": code}, [_source_error("baostock", exc)]
    effective_date = resolve_trade_date(trade_date)
    end_dt = datetime.strptime(effective_date, "%Y%m%d")
    start_date = (end_dt - timedelta(days=max(30, int(days or 365)))).strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")
    lg = bs.login()
    if getattr(lg, "error_code", "") != "0":
        return {"events": [], "ts_code": code, "error": getattr(lg, "error_msg", "baostock login failed")}, [
            {"source": "baostock_login", "ok": False, "error": getattr(lg, "error_msg", "baostock login failed")}
        ]
    events = []
    status: list[dict] = []
    try:
        bs_code = _bs_code(code)
        express = _collect_bs_rows(bs, "query_performance_express_report", bs_code, start_date, end_date)
        forecast = _collect_bs_rows(bs, "query_forecast_report", bs_code, start_date, end_date)
        for row in express[-5:]:
            events.append({"ts_code": code, "type": "performance_express", "data": row})
        for row in forecast[-5:]:
            events.append({"ts_code": code, "type": "forecast", "data": row})
        status.append(_source_ok("baostock_stock_fundamental", len(events)))
    except Exception as exc:
        status.append(_source_error(f"baostock_stock_fundamental_{code}", exc))
    finally:
        bs.logout()
    return {"events": _jsonable(events), "ts_code": code, "start_date": start_date, "end_date": end_date}, status


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


def _format_chip_summary(chip: dict) -> str:
    items = chip.get("items") or []
    if not items:
        return "暂无筹码分布数据。"
    high_profit = [x for x in items if _safe_float(x.get("profit_ratio")) >= 0.7]
    concentrated = [x for x in items if 0 < _safe_float(x.get("concentration_70")) <= 0.16]
    leaders = sorted(items, key=lambda x: _safe_float(x.get("profit_ratio")), reverse=True)[:5]
    leader_text = "、".join(
        f"{x.get('ts_code')}获利{_safe_float(x.get('profit_ratio_pct')):.1f}%/成本{x.get('avg_cost')}"
        for x in leaders
    )
    return (
        f"候选股筹码样本{len(items)}只，高获利比例{len(high_profit)}只，"
        f"70成本集中较紧{len(concentrated)}只。代表: {leader_text or '暂无'}。"
    )


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
        "chip_signal": _format_chip_summary(snapshot.get("chip_distribution", {})),
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
- 筹码分布: {structured.get("chip_signal", "")}

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

    chip, status = collect_chip_snapshot(snapshot, 12)
    snapshot["chip_distribution"] = chip
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


def format_chip_distribution(ts_code: str) -> str:
    item, status = collect_chip_distribution(ts_code)
    code = item.get("ts_code") or normalize_ts_code(ts_code)
    if not item.get("ok"):
        return f"{code} 筹码分布读取失败: {item.get('error') or status[0].get('error') if status else '未知错误'}"
    recent = item.get("recent") or []
    trend = "平均成本上移" if _safe_float(item.get("avg_cost_change_5d")) > 0 else ("平均成本下移" if _safe_float(item.get("avg_cost_change_5d")) < 0 else "平均成本稳定")
    lines = [
        f"{code} 前复权筹码分布",
        f"日期: {item.get('trade_date')}",
        f"- 获利比例: {item.get('profit_ratio_pct')}%",
        f"- 平均成本: {item.get('avg_cost')}，近5日变化 {item.get('avg_cost_change_5d')}，{trend}",
        f"- 90%成本区间: {item.get('cost_90_low')}-{item.get('cost_90_high')}，集中度 {item.get('concentration_90')}",
        f"- 70%成本区间: {item.get('cost_70_low')}-{item.get('cost_70_high')}，集中度 {item.get('concentration_70')}",
    ]
    if recent:
        lines.append("最近样本:")
        for row in recent[-5:]:
            lines.append(
                f"  {row.get('日期')} 获利{_safe_float(row.get('获利比例')) * 100:.1f}% "
                f"平均成本{_safe_float(row.get('平均成本')):.2f} "
                f"70集中{_safe_float(row.get('70集中度')):.4f}"
            )
    lines.append("解读: 获利比例越高代表上方套牢盘越少，但高位高获利也可能积累兑现压力；集中度越低通常代表筹码更集中。")
    return "\n".join(lines)


def format_macro_topic(topic: str = "report", trade_date: str = "") -> str:
    effective_date = resolve_trade_date(trade_date)
    raw = (topic or "report").lower()
    row = get_macro_report_row(effective_date)
    snapshot = {}
    if row and row.get("raw_json"):
        try:
            snapshot = json.loads(row["raw_json"] or "{}")
        except Exception:
            snapshot = {}
    if raw in {"report", "macro", "daily"}:
        return get_macro_daily_report_text(effective_date)
    if raw in {"sector", "sector_heat", "板块", "板块热度"}:
        market = snapshot.get("market") if snapshot else collect_market_snapshot(effective_date)[0]
        temp = market.get("sector_temperature") or {}
        sectors = temp.get("sectors") or []
        weak = temp.get("weak_sectors") or []
        lines = [f"{effective_date} 板块热度", f"市场状态: {temp.get('market_regime', 'unknown')} risk-on {temp.get('risk_on_score', 0)}"]
        lines.append("热门板块:")
        for item in sectors[:12]:
            heat = item.get("temperature_score", item.get("heat_score", 0))
            avg_pct = item.get("avg_pct_chg", item.get("avg_pct", 0))
            lines.append(f"- {item.get('sector')}: 温度{heat} 涨停{item.get('limit_up_count')} 大涨{item.get('big_up_count')} 平均涨幅{avg_pct}%")
        lines.append("风险板块:")
        for item in weak[:8]:
            heat = item.get("temperature_score", item.get("heat_score", 0))
            avg_pct = item.get("avg_pct_chg", item.get("avg_pct", 0))
            lines.append(f"- {item.get('sector')}: 温度{heat} 跌停{item.get('limit_down_count')} 大跌{item.get('big_down_count')} 平均涨幅{avg_pct}%")
        return "\n".join(lines)
    if raw in {"lhb", "龙虎榜"}:
        lhb = snapshot.get("lhb") if snapshot else collect_lhb_snapshot(effective_date)[0]
        lines = [f"{effective_date} 龙虎榜摘要", _format_lhb_summary(lhb)]
        for title, key in (("当日详情", "daily_detail"), ("近5日个股净买入", "stock_stats_5d"), ("机构席位追踪", "institution_track_5d")):
            lines.append(title + ":")
            for item in (lhb.get(key, {}).get("top") or [])[:8]:
                code = item.get("股票代码") or ""
                name = item.get("股票名称") or ""
                net = item.get("净额") or item.get("成交额") or item.get("累积买入额") or ""
                reason = item.get("指标") or item.get("类型") or ""
                lines.append(f"- {code} {name} {net} {reason}".strip())
        return "\n".join(lines)
    limit_map = {
        "limit_up": ("limit_up_pool", "涨停板"),
        "涨停": ("limit_up_pool", "涨停板"),
        "zt": ("limit_up_pool", "涨停板"),
        "limit_down": ("limit_down_pool", "跌停板"),
        "跌停": ("limit_down_pool", "跌停板"),
        "broken_limit": ("broken_limit_up_pool", "涨停炸板"),
        "炸板": ("broken_limit_up_pool", "涨停炸板"),
        "strong": ("strong_pool", "强势股池"),
        "强势": ("strong_pool", "强势股池"),
    }
    if raw in limit_map:
        key, title = limit_map[raw]
        limit_up = snapshot.get("limit_up") if snapshot else collect_limit_up_snapshot(effective_date)[0]
        pool = limit_up.get(key) or {}
        lines = [f"{effective_date} {title}", f"数量: {pool.get('count', 0)}"]
        for item in (pool.get("top") or [])[:15]:
            line = (
                f"- {item.get('代码') or item.get('股票代码') or ''} {item.get('名称') or item.get('股票名称') or ''} "
                f"涨跌{item.get('涨跌幅', '')}% 换手{item.get('换手率', '')}% 连板{item.get('连板数') or item.get('昨日连板数') or ''} "
                f"{item.get('所属行业') or ''} {item.get('入选理由') or ''}"
            )
            lines.append(line.strip())
        return "\n".join(lines)
    return get_macro_daily_report_text(effective_date)


def refresh_macro_intelligence(trade_date: str = "", refresh_policy: bool = True, force: bool = True) -> dict:
    """Manual refresh entry for UI/Telegram tools."""
    policy_result = None
    if refresh_policy:
        try:
            from backend.policy.crawler import run_policy_crawler
            result = run_policy_crawler(None, 10, True)
            policy_result = {"ok": True, "count": len(result)}
        except Exception as exc:
            policy_result = {"ok": False, "error": str(exc)}
    report_result = generate_macro_report(trade_date, force=force)
    return {
        "ok": bool(report_result.get("ok")),
        "trade_date": report_result.get("trade_date"),
        "policy_refresh": policy_result,
        "report": report_result,
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
