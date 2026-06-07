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
from backend.data.loader import load_daily, load_index_daily
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
  "limit_board_quality": "基于封板资金、封板时间、炸板次数、连板数的板质量摘要",
  "limit_promotion_signal": "昨日涨停晋级率、炸板率、闷杀率和短线情绪周期判断",
  "northbound_signal": "沪股通/深股通北向资金方向和强弱判断",
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


def _limit_code(item: dict) -> str:
    code = str(item.get("代码") or item.get("股票代码") or item.get("ts_code") or "").strip()
    return code[:6] if code else ""


def _limit_name(item: dict) -> str:
    return str(item.get("名称") or item.get("股票名称") or item.get("name") or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or pd.isna(value):
            return default
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def _parse_limit_time(value: Any) -> int:
    """Return HHMMSS as an int-like sortable value."""
    if value is None or pd.isna(value):
        return 0
    text = str(value).strip()
    if not text:
        return 0
    if ":" in text:
        parts = [p.zfill(2) for p in text.split(":")]
        return _safe_int("".join(parts[:3]), 0)
    digits = re.sub(r"\D", "", text)
    if len(digits) <= 4:
        digits = digits.zfill(4) + "00"
    return _safe_int(digits[:6], 0)


def _format_limit_time(value: Any) -> str:
    parsed = _parse_limit_time(value)
    if parsed <= 0:
        return ""
    text = f"{parsed:06d}"
    return f"{text[:2]}:{text[2:4]}:{text[4:6]}"


def _board_count(item: dict, previous: bool = False) -> int:
    value = item.get("昨日连板数") if previous else item.get("连板数")
    count = _safe_int(value, 0)
    if count > 0:
        return count
    stat = str(item.get("涨停统计") or "")
    match = re.search(r"(\d+)\s*/\s*(\d+)", stat)
    if match:
        return _safe_int(match.group(2), 0)
    return 1 if _safe_float(item.get("涨跌幅"), 0) >= 9.8 else 0


def _board_stage(board_count: int) -> str:
    if board_count <= 1:
        return "首板"
    if board_count == 2:
        return "二板"
    if board_count == 3:
        return "三板"
    return "高位连板"


def _board_quality(item: dict) -> dict:
    first_time = _parse_limit_time(item.get("首次封板时间"))
    last_time = _parse_limit_time(item.get("最后封板时间"))
    broken_count = _safe_int(item.get("炸板次数", item.get("开板次数", 0)), 0)
    seal_amount = _safe_float(item.get("封板资金", item.get("封单资金", 0)), 0.0)
    board_count = _board_count(item)
    score = 45.0
    if first_time and first_time <= 100000:
        score += 16
    elif first_time and first_time <= 113000:
        score += 10
    elif first_time:
        score += 3
    if last_time and last_time <= 100000:
        score += 10
    elif last_time and last_time <= 143000:
        score += 5
    score += min(18.0, max(0.0, seal_amount) / 1e8 * 8)
    score += min(12.0, max(0, board_count - 1) * 4)
    score -= min(24.0, broken_count * 8)
    score = max(0.0, min(100.0, score))
    return {
        "ts_code": _limit_code(item),
        "name": _limit_name(item),
        "pct_chg": _safe_float(item.get("涨跌幅"), 0),
        "turnover_rate": _safe_float(item.get("换手率"), 0),
        "first_seal_time": _format_limit_time(item.get("首次封板时间")),
        "last_seal_time": _format_limit_time(item.get("最后封板时间")),
        "seal_amount": seal_amount,
        "broken_count": broken_count,
        "board_count": board_count,
        "board_stage": _board_stage(board_count),
        "industry": item.get("所属行业") or "",
        "quality_score": round(score, 2),
    }


def _limit_pool_analytics(result: dict) -> dict:
    limit_items = result.get("limit_up_pool", {}).get("items") or result.get("limit_up_pool", {}).get("top") or []
    previous_items = result.get("previous_limit_up_pool", {}).get("items") or result.get("previous_limit_up_pool", {}).get("top") or []
    broken_items = result.get("broken_limit_up_pool", {}).get("items") or result.get("broken_limit_up_pool", {}).get("top") or []
    limit_codes = {_limit_code(item) for item in limit_items if _limit_code(item)}
    broken_codes = {_limit_code(item) for item in broken_items if _limit_code(item)}
    qualities = [_board_quality(item) for item in limit_items]
    qualities.sort(key=lambda x: (x["quality_score"], x["board_count"], -x["broken_count"]), reverse=True)
    stage_counts: dict[str, int] = {}
    for item in qualities:
        stage_counts[item["board_stage"]] = stage_counts.get(item["board_stage"], 0) + 1
    promoted = []
    eliminated = []
    killed = []
    broken_from_previous = []
    for item in previous_items:
        code = _limit_code(item)
        if not code:
            continue
        pct = _safe_float(item.get("涨跌幅"), 0)
        row = {
            "ts_code": code,
            "name": _limit_name(item),
            "pct_chg": pct,
            "previous_board_count": _board_count(item, previous=True),
            "industry": item.get("所属行业") or "",
        }
        if code in limit_codes or pct >= 9.8:
            promoted.append(row)
        else:
            eliminated.append(row)
        if code in broken_codes:
            broken_from_previous.append(row)
        if pct <= -5:
            killed.append(row)
    previous_count = len({_limit_code(item) for item in previous_items if _limit_code(item)})
    promoted_count = len({x["ts_code"] for x in promoted})
    broken_count = len({x["ts_code"] for x in broken_from_previous})
    killed_count = len({x["ts_code"] for x in killed})
    return {
        "board_quality_top": qualities[:30],
        "board_stage_counts": stage_counts,
        "broken_rate": round((len(broken_items) / (len(limit_items) + len(broken_items)) * 100) if (limit_items or broken_items) else 0, 2),
        "promotion": {
            "previous_limit_up_count": previous_count,
            "promoted_count": promoted_count,
            "promoted_rate": round((promoted_count / previous_count * 100) if previous_count else 0, 2),
            "broken_from_previous_count": broken_count,
            "broken_from_previous_rate": round((broken_count / previous_count * 100) if previous_count else 0, 2),
            "killed_count": killed_count,
            "killed_rate": round((killed_count / previous_count * 100) if previous_count else 0, 2),
            "promoted": promoted[:20],
            "eliminated": eliminated[:20],
            "killed": killed[:20],
            "broken_from_previous": broken_from_previous[:20],
        },
    }


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
                "items": _safe_records(df, 500, sort_by=sort_by, reverse=True),
            }
            status.append(_source_ok(func_name, len(df)))
        except Exception as exc:
            result[key] = {"count": 0, "top": [], "error": str(exc)}
            status.append(_source_error(func_name, exc))
    result["analytics"] = _limit_pool_analytics(result)
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


def _capital_flow_direction(net_buy: float) -> str:
    if net_buy >= 20:
        return "大幅净流入"
    if net_buy >= 5:
        return "小幅净流入"
    if net_buy <= -20:
        return "大幅净流出"
    if net_buy <= -5:
        return "小幅净流出"
    return "中性/休市或数据接近零"


def collect_capital_flow_snapshot(trade_date: str) -> tuple[dict, list[dict]]:
    """Collect latest 沪深港通 fund flow summary, with 北向资金 highlighted."""
    try:
        ak = _load_akshare()
    except Exception as exc:
        return {}, [_source_error("akshare_hsgt", exc)]
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        records = _safe_records(df, 20)
        north_rows = [row for row in records if str(row.get("资金方向") or "") == "北向"]
        south_rows = [row for row in records if str(row.get("资金方向") or "") == "南向"]
        north_net_buy = sum(_safe_float(row.get("成交净买额"), 0) for row in north_rows)
        north_net_inflow = sum(_safe_float(row.get("资金净流入"), 0) for row in north_rows)
        south_net_buy = sum(_safe_float(row.get("成交净买额"), 0) for row in south_rows)
        source_dates = sorted({str(row.get("交易日") or "") for row in records if row.get("交易日")})
        source_date = source_dates[-1] if source_dates else ""
        north_channels = []
        for row in north_rows:
            north_channels.append({
                "channel": row.get("板块") or row.get("类型") or "",
                "net_buy": round(_safe_float(row.get("成交净买额"), 0), 3),
                "net_inflow": round(_safe_float(row.get("资金净流入"), 0), 3),
                "up_count": _safe_int(row.get("上涨数"), 0),
                "down_count": _safe_int(row.get("下跌数"), 0),
                "related_index": row.get("相关指数") or "",
                "index_pct_chg": round(_safe_float(row.get("指数涨跌幅"), 0), 2),
                "trade_status": row.get("交易状态"),
            })
        result = {
            "source": "stock_hsgt_fund_flow_summary_em",
            "source_date": source_date,
            "requested_trade_date": trade_date,
            "rows": records,
            "northbound": {
                "count": len(north_rows),
                "net_buy": round(north_net_buy, 3),
                "net_inflow": round(north_net_inflow, 3),
                "direction": _capital_flow_direction(north_net_buy),
                "channels": north_channels,
            },
            "southbound": {
                "count": len(south_rows),
                "net_buy": round(south_net_buy, 3),
            },
        }
        return result, [_source_ok("stock_hsgt_fund_flow_summary_em", len(df))]
    except Exception as exc:
        return {"error": str(exc)}, [_source_error("stock_hsgt_fund_flow_summary_em", exc)]


def _weighted_quantile_from_dist(dist: dict[float, float], quantiles: list[float]) -> list[float]:
    rows = sorted((float(price), float(weight)) for price, weight in dist.items() if float(weight) > 0)
    total = sum(weight for _, weight in rows)
    if not rows or total <= 0:
        return [0.0 for _ in quantiles]
    result = []
    acc = 0.0
    qi = 0
    sorted_q = sorted((q, idx) for idx, q in enumerate(quantiles))
    out = [0.0 for _ in quantiles]
    for price, weight in rows:
        acc += weight
        pct = acc / total
        while qi < len(sorted_q) and pct >= sorted_q[qi][0]:
            out[sorted_q[qi][1]] = price
            qi += 1
    for i, value in enumerate(out):
        if value <= 0:
            out[i] = rows[-1][0]
    return out


def _chip_bucket(price: float, bin_size: float = 0.05) -> float:
    if price >= 100:
        bin_size = 0.10
    if price < 10:
        bin_size = 0.02
    return round(round(float(price) / bin_size) * bin_size, 2)


def _add_chip_weight(dist: dict[float, float], price: float, weight: float) -> None:
    price = _safe_float(price)
    weight = _safe_float(weight)
    if price <= 0 or weight <= 0:
        return
    key = _chip_bucket(price)
    dist[key] = dist.get(key, 0.0) + weight


def _daily_chip_points(row: dict | pd.Series) -> list[tuple[float, float]]:
    open_p = _safe_float(row.get("open"))
    high = _safe_float(row.get("high"))
    low = _safe_float(row.get("low"))
    close = _safe_float(row.get("close"))
    typical = (high + low + close) / 3 if high > 0 and low > 0 and close > 0 else close
    points = [(open_p, 0.12), (high, 0.10), (low, 0.10), (typical, 0.28), (close, 0.40)]
    points = [(p, w) for p, w in points if p > 0]
    total = sum(w for _, w in points) or 1.0
    return [(p, w / total) for p, w in points]


def _minute_chip_points(ts_code: str, trade_date: str) -> tuple[list[tuple[float, float]], str]:
    try:
        from backend.evolution.minute_replay import load_or_fetch_5m

        df, source = load_or_fetch_5m(ts_code, trade_date)
    except Exception as exc:
        return [], f"minute_error:{exc}"
    if df is None or df.empty:
        return [], source
    points = []
    for _, row in df.iterrows():
        vol = _safe_float(row.get("vol"))
        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        close = _safe_float(row.get("close"))
        price = (high + low + close) / 3 if high > 0 and low > 0 and close > 0 else close
        if price > 0 and vol > 0:
            points.append((price, vol))
    total = sum(w for _, w in points) or 0.0
    if total <= 0:
        return [], source
    return [(p, w / total) for p, w in points], source


def _chip_snapshot_from_dist(dist: dict[float, float], close: float) -> dict:
    total = sum(dist.values())
    if total <= 0:
        return {}
    prices = list(dist.keys())
    weights = list(dist.values())
    avg_cost = sum(price * weight for price, weight in zip(prices, weights)) / total
    profit_ratio = sum(weight for price, weight in zip(prices, weights) if price <= close) / total if close > 0 else 0.0
    q05, q15, q85, q95 = _weighted_quantile_from_dist(dist, [0.05, 0.15, 0.85, 0.95])
    concentration_90 = (q95 - q05) / (q95 + q05) if (q95 + q05) > 0 else 0.0
    concentration_70 = (q85 - q15) / (q85 + q15) if (q85 + q15) > 0 else 0.0
    peaks = sorted(dist.items(), key=lambda item: item[1], reverse=True)[:10]
    return {
        "avg_cost": round(avg_cost, 3),
        "profit_ratio": round(profit_ratio, 4),
        "profit_ratio_pct": round(profit_ratio * 100, 2),
        "cost_90_low": round(q05, 3),
        "cost_90_high": round(q95, 3),
        "concentration_90": round(concentration_90, 4),
        "cost_70_low": round(q15, 3),
        "cost_70_high": round(q85, 3),
        "concentration_70": round(concentration_70, 4),
        "top_peaks": [{"price": round(price, 3), "weight": round(weight / total, 4)} for price, weight in peaks],
    }


def simulate_chip_distribution(
    ts_code: str,
    lookback_days: int = 260,
    prefer_minute: bool = True,
    minute_days: int = 8,
) -> dict:
    """Approximate chip distribution from qfq daily/5m K lines."""
    code = normalize_ts_code(ts_code)
    df = load_daily(code)
    if df is None or df.empty:
        return {"ok": False, "ts_code": code, "error": "未找到日线数据"}
    data = df.sort_values("trade_date").tail(max(30, int(lookback_days or 260))).copy()
    minute_dates = set(str(x) for x in data.tail(max(0, int(minute_days or 0)))["trade_date"]) if prefer_minute else set()
    dist: dict[float, float] = {}
    recent = []
    minute_used = 0
    minute_sources: dict[str, int] = {}
    turnover_values = []
    for _, row in data.iterrows():
        trade_date = str(row.get("trade_date"))
        turnover = _safe_float(row.get("turnover_rate")) / 100.0
        if turnover <= 0:
            turnover = 0.005
        turnover = max(0.001, min(turnover, 0.95))
        turnover_values.append(turnover)
        for key in list(dist):
            dist[key] *= (1 - turnover)
            if dist[key] < 1e-10:
                del dist[key]
        points = []
        if trade_date in minute_dates:
            points, source = _minute_chip_points(code, trade_date)
            minute_sources[source] = minute_sources.get(source, 0) + 1
            if points:
                minute_used += 1
        if not points:
            points = _daily_chip_points(row)
        for price, weight in points:
            _add_chip_weight(dist, price, turnover * weight)
        total = sum(dist.values())
        if total > 0:
            for key in list(dist):
                dist[key] /= total
        close = _safe_float(row.get("close"))
        snapshot = _chip_snapshot_from_dist(dist, close)
        if snapshot:
            recent.append({
                "日期": trade_date,
                "获利比例": snapshot["profit_ratio"],
                "平均成本": snapshot["avg_cost"],
                "70集中度": snapshot["concentration_70"],
            })
    latest = data.iloc[-1]
    close = _safe_float(latest.get("close"))
    snapshot = _chip_snapshot_from_dist(dist, close)
    if not snapshot:
        return {"ok": False, "ts_code": code, "error": "无法形成筹码分布"}
    prev_snapshot = recent[-6] if len(recent) >= 6 else (recent[0] if recent else {})
    avg_turnover = sum(turnover_values[-20:]) / max(1, len(turnover_values[-20:]))
    if minute_used >= 3:
        confidence = "high"
    elif avg_turnover >= 0.03 or len(data) >= 180:
        confidence = "medium"
    else:
        confidence = "low"
    source = "simulated_5m_enhanced" if minute_used else "simulated_daily"
    return {
        "ok": True,
        "ts_code": code,
        "source": source,
        "source_adjust": "qfq",
        "model": "turnover_decay_volume_weighted",
        "confidence": confidence,
        "trade_date": str(latest.get("trade_date")),
        "close": round(close, 3),
        "lookback_days": len(data),
        "minute_days_requested": len(minute_dates),
        "minute_days_used": minute_used,
        "minute_sources": minute_sources,
        "bins": len(dist),
        "avg_cost_change_5d": round(snapshot["avg_cost"] - _safe_float(prev_snapshot.get("平均成本")), 3),
        "recent": recent[-8:],
        **snapshot,
    }


def collect_chip_distribution(ts_code: str) -> tuple[dict, list[dict]]:
    """Simulate qfq chip distribution from daily bars and optional 5m bars.

    The old Eastmoney stock_cyq_em endpoint is unstable/blocked. This model
    treats turnover_rate as the replacement ratio of old holders by new traded
    chips. Daily bars provide a coarse OHLC price distribution; for the latest
    touched stocks, 5-minute bars are fetched on demand and weighted by volume.
    """
    code = normalize_ts_code(ts_code)
    try:
        result = simulate_chip_distribution(code, lookback_days=260, prefer_minute=True, minute_days=8)
        return result, [_source_ok(result.get("source", "simulated_chip"), result.get("bins", 0))]
    except Exception as exc:
        return {"ok": False, "ts_code": code, "error": str(exc)}, [_source_error("simulated_chip", exc)]


def collect_chip_snapshot(snapshot: dict, max_codes: int = 12) -> tuple[dict, list[dict]]:
    """Collect daily-only simulated chip distribution for hot candidates."""
    status: list[dict] = []
    rows = []
    for code in _candidate_codes(snapshot)[:max(1, int(max_codes or 12))]:
        try:
            item = simulate_chip_distribution(code, lookback_days=260, prefer_minute=False, minute_days=0)
            status.append(_source_ok("simulated_chip_daily", item.get("bins", 0)))
            rows.append(item)
        except Exception as exc:
            status.append(_source_error(f"simulated_chip_daily_{code}", exc))
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
        "limit_board_quality": _format_limit_quality_summary(snapshot.get("limit_up", {})),
        "limit_promotion_signal": _format_limit_promotion_summary(snapshot.get("limit_up", {})),
        "northbound_signal": _format_capital_flow_summary(snapshot.get("capital_flow", {})),
        "lhb_summary": _format_lhb_summary(snapshot.get("lhb", {})),
        "institution_signal": "机构席位数据见龙虎榜摘要。",
        "policy_signal": (snapshot.get("policy", {}) or {}).get("summary", "暂无政策信号摘要。"),
        "fundamental_events": f"候选股业绩事件 {len(snapshot.get('fundamental', {}).get('events', []))} 条。",
        "chip_signal": _format_chip_summary(snapshot.get("chip_distribution", {})),
        "trade_agent_guidance": "先按市场状态控制仓位，再在热点板块中结合趋势、基本面和流动性筛选。",
        "risk_warnings": ["外部数据可能部分缺失，交易Agent需要对个股继续核验。"],
    }


def _format_limit_summary(limit_up: dict) -> str:
    analytics = limit_up.get("analytics") or {}
    promotion = analytics.get("promotion") or {}
    stages = analytics.get("board_stage_counts") or {}
    return (
        f"涨停{limit_up.get('limit_up_pool', {}).get('count', 0)}家，"
        f"强势池{limit_up.get('strong_pool', {}).get('count', 0)}家，"
        f"炸板{limit_up.get('broken_limit_up_pool', {}).get('count', 0)}家，"
        f"跌停{limit_up.get('limit_down_pool', {}).get('count', 0)}家。"
        f"结构: 首板{stages.get('首板', 0)}、二板{stages.get('二板', 0)}、三板{stages.get('三板', 0)}、高位{stages.get('高位连板', 0)}。"
        f"昨日涨停晋级率{promotion.get('promoted_rate', 0)}%，炸板率{analytics.get('broken_rate', 0)}%，闷杀率{promotion.get('killed_rate', 0)}%。"
    )


def _format_limit_quality_summary(limit_up: dict) -> str:
    analytics = limit_up.get("analytics") or {}
    top = analytics.get("board_quality_top") or []
    if not top:
        return "暂无可用涨停板质量数据。"
    return "、".join(
        f"{x.get('name')}({x.get('board_stage')},评分{x.get('quality_score')},炸板{x.get('broken_count')})"
        for x in top[:8]
    )


def _format_limit_promotion_summary(limit_up: dict) -> str:
    analytics = limit_up.get("analytics") or {}
    promotion = analytics.get("promotion") or {}
    if not promotion:
        return "暂无晋级/淘汰统计。"
    return (
        f"昨日涨停{promotion.get('previous_limit_up_count', 0)}只，"
        f"晋级{promotion.get('promoted_count', 0)}只({promotion.get('promoted_rate', 0)}%)，"
        f"炸板{promotion.get('broken_from_previous_count', 0)}只({promotion.get('broken_from_previous_rate', 0)}%)，"
        f"闷杀{promotion.get('killed_count', 0)}只({promotion.get('killed_rate', 0)}%)。"
    )


def _format_lhb_summary(lhb: dict) -> str:
    return (
        f"当日龙虎榜{lhb.get('daily_detail', {}).get('count', 0)}条，"
        f"近5日个股统计{lhb.get('stock_stats_5d', {}).get('count', 0)}条，"
        f"机构追踪{lhb.get('institution_track_5d', {}).get('count', 0)}条。"
    )


def _format_capital_flow_summary(capital_flow: dict) -> str:
    if not capital_flow:
        return "暂无沪深港通资金数据。"
    if capital_flow.get("error"):
        return f"沪深港通资金读取失败: {capital_flow.get('error')}"
    north = capital_flow.get("northbound") or {}
    channels = north.get("channels") or []
    source_date = capital_flow.get("source_date") or capital_flow.get("requested_trade_date") or ""
    channel_text = "；".join(
        f"{x.get('channel')}: 净买{x.get('net_buy')}亿, {x.get('related_index')}{x.get('index_pct_chg')}%"
        for x in channels
    ) or "暂无北向分通道数据"
    return (
        f"{source_date} 北向资金{north.get('direction', '未知')}，"
        f"成交净买额合计{north.get('net_buy', 0)}亿，资金净流入{north.get('net_inflow', 0)}亿。"
        f"{channel_text}"
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
    limit_analytics = snapshot.get("limit_up", {}).get("analytics", {})
    quality_top = limit_analytics.get("board_quality_top") or []
    quality_text = "、".join(
        f"{x.get('name')}({x.get('board_stage')}/{x.get('quality_score')})"
        for x in quality_top[:8]
    ) or "暂无"
    promotion = limit_analytics.get("promotion") or {}
    capital_flow = snapshot.get("capital_flow") or {}
    northbound_text = str(structured.get("northbound_signal") or "").strip()
    capital_fallback = _format_capital_flow_summary(capital_flow)
    missing_northbound = any(token in northbound_text for token in ("未提供", "无北向资金", "暂无北向", "没有北向"))
    if (not northbound_text) or (missing_northbound and not capital_fallback.startswith(("暂无", "沪深港通资金读取失败"))):
        northbound_text = capital_fallback
    return f"""# 每日宏观市场报告 {trade_date}

## 市场状态

- 状态: {structured.get("market_regime", "unknown")}
- 摘要: {structured.get("summary", "")}
- 热点板块: {hot}
- 风险板块: {weak}
- 领涨股: {leader_text}

## 情绪与资金

- 涨停/炸板/跌停: {structured.get("limit_up_summary", "")}
- 板质量Top: {quality_text}
- 晋级/淘汰: 昨日涨停{promotion.get('previous_limit_up_count', 0)}只，晋级{promotion.get('promoted_count', 0)}只({promotion.get('promoted_rate', 0)}%)，炸板{promotion.get('broken_from_previous_count', 0)}只，闷杀{promotion.get('killed_count', 0)}只。
- 北向资金: {northbound_text}
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

    capital_flow, status = collect_capital_flow_snapshot(effective_date)
    snapshot["capital_flow"] = capital_flow
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
    source_text = "5分钟增强" if item.get("source") == "simulated_5m_enhanced" else "日线近似"
    confidence = {"high": "高", "medium": "中", "low": "低"}.get(item.get("confidence"), item.get("confidence") or "未知")
    lines = [
        f"{code} 模拟筹码分布",
        f"日期: {item.get('trade_date')}，数据源: {source_text}，置信度: {confidence}",
        f"- 模型: 换手率衰减 + 成交价格加权；前复权口径；近{item.get('lookback_days')}个交易日",
        f"- 5分钟K线: 请求{item.get('minute_days_requested', 0)}天，实际使用{item.get('minute_days_used', 0)}天",
        f"- 获利比例: {item.get('profit_ratio_pct')}%",
        f"- 平均成本: {item.get('avg_cost')}，近5日变化 {item.get('avg_cost_change_5d')}，{trend}",
        f"- 90%成本区间: {item.get('cost_90_low')}-{item.get('cost_90_high')}，集中度 {item.get('concentration_90')}",
        f"- 70%成本区间: {item.get('cost_70_low')}-{item.get('cost_70_high')}，集中度 {item.get('concentration_70')}",
    ]
    peaks = item.get("top_peaks") or []
    if peaks:
        peak_text = "、".join(f"{p.get('price')}({float(p.get('weight') or 0) * 100:.1f}%)" for p in peaks[:6])
        lines.append(f"- 主要筹码峰: {peak_text}")
    if recent:
        lines.append("最近样本:")
        for row in recent[-5:]:
            lines.append(
                f"  {row.get('日期')} 获利{_safe_float(row.get('获利比例')) * 100:.1f}% "
                f"平均成本{_safe_float(row.get('平均成本')):.2f} "
                f"70集中{_safe_float(row.get('70集中度')):.4f}"
            )
    lines.append("解读: 获利比例越高代表上方套牢盘越少，但高位高获利也可能积累兑现压力；集中度越低通常代表筹码更集中。")
    lines.append("注意: 这是基于日线/5分钟K线的模拟筹码，不是东方财富真实筹码分布，只能作为辅助证据。")
    return "\n".join(lines)


def format_macro_topic(topic: str = "report", trade_date: str = "") -> str:
    effective_date = resolve_trade_date(trade_date)
    raw = (topic or "report").lower()
    try:
        row = get_macro_report_row(effective_date)
    except Exception:
        row = None
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
    if raw in {"capital_flow", "northbound", "hsgt", "北向资金", "沪深港通", "沪深港通资金"}:
        capital_flow = snapshot.get("capital_flow") if snapshot else collect_capital_flow_snapshot(effective_date)[0]
        if not capital_flow:
            capital_flow = collect_capital_flow_snapshot(effective_date)[0]
        lines = [f"{effective_date} 沪深港通资金", _format_capital_flow_summary(capital_flow)]
        north = capital_flow.get("northbound") or {}
        if north.get("channels"):
            lines.append("北向分通道:")
            for item in north["channels"]:
                lines.append(
                    f"- {item.get('channel')}: 成交净买{item.get('net_buy')}亿，"
                    f"资金净流入{item.get('net_inflow')}亿，"
                    f"{item.get('related_index')}{item.get('index_pct_chg')}%，"
                    f"上涨{item.get('up_count')}家/下跌{item.get('down_count')}家"
                )
        south = capital_flow.get("southbound") or {}
        lines.append(f"南向参考: 成交净买额合计{south.get('net_buy', 0)}亿。")
        return "\n".join(lines)
    if raw in {"limit_quality", "board_quality", "涨停质量", "板质量", "封板质量"}:
        limit_up = snapshot.get("limit_up") if snapshot else collect_limit_up_snapshot(effective_date)[0]
        if not limit_up.get("analytics"):
            limit_up = collect_limit_up_snapshot(effective_date)[0]
        analytics = limit_up.get("analytics") or _limit_pool_analytics(limit_up)
        limit_up["analytics"] = analytics
        lines = [f"{effective_date} 涨停板质量", _format_limit_summary(limit_up)]
        lines.append("质量Top:")
        for item in (analytics.get("board_quality_top") or [])[:20]:
            seal = item.get("seal_amount") or 0
            lines.append(
                f"- {item.get('ts_code')} {item.get('name')} {item.get('board_stage')} "
                f"评分{item.get('quality_score')} 首封{item.get('first_seal_time') or '-'} "
                f"末封{item.get('last_seal_time') or '-'} 炸板{item.get('broken_count')} "
                f"封单/封板资金{float(seal):.0f} 行业{item.get('industry') or ''}"
            )
        return "\n".join(lines)
    if raw in {"promotion", "advance", "晋级率", "涨停晋级", "晋级淘汰"}:
        limit_up = snapshot.get("limit_up") if snapshot else collect_limit_up_snapshot(effective_date)[0]
        if not limit_up.get("analytics"):
            limit_up = collect_limit_up_snapshot(effective_date)[0]
        analytics = limit_up.get("analytics") or _limit_pool_analytics(limit_up)
        limit_up["analytics"] = analytics
        promotion = analytics.get("promotion") or {}
        lines = [
            f"{effective_date} 涨停晋级/淘汰",
            f"昨日涨停{promotion.get('previous_limit_up_count', 0)}只，"
            f"晋级{promotion.get('promoted_count', 0)}只({promotion.get('promoted_rate', 0)}%)，"
            f"炸板{promotion.get('broken_from_previous_count', 0)}只({promotion.get('broken_from_previous_rate', 0)}%)，"
            f"闷杀{promotion.get('killed_count', 0)}只({promotion.get('killed_rate', 0)}%)。",
            f"当日炸板率: {analytics.get('broken_rate', 0)}%",
        ]
        if promotion.get("promoted"):
            lines.append("晋级标的:")
            for item in promotion["promoted"][:12]:
                lines.append(f"- {item.get('ts_code')} {item.get('name')} 昨{item.get('previous_board_count')}板 今日{item.get('pct_chg')}% {item.get('industry') or ''}")
        if promotion.get("killed"):
            lines.append("闷杀/退潮样本:")
            for item in promotion["killed"][:10]:
                lines.append(f"- {item.get('ts_code')} {item.get('name')} {item.get('pct_chg')}% 昨{item.get('previous_board_count')}板 {item.get('industry') or ''}")
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
