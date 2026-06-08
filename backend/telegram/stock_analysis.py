"""Stock analysis reports for the Telegram entry Agent."""

import os
import re
from functools import lru_cache
from statistics import mean

import pandas as pd

from backend.config import DATA_DIR
from backend.data.indicators import compute_market_strength_by_sector
from backend.data.loader import compute_limit_status, compute_mas, load_daily, load_index_daily
from backend.db.repository import get_conn
from backend.policy.reader import extract_policy_signals
from backend.search_agent.searcher import get_cached, get_freshness, refresh_company_business_cache
from backend.trading.rules import is_index_like_name, normalize_ts_code


CODE_RE = re.compile(r"(?<!\d)(?:60\d{4}|000\d{3}|001\d{3}|002\d{3})(?:\.(?:SH|SZ))?(?!\d)", re.I)


def extract_stock_codes(text: str) -> list[str]:
    codes = [normalize_ts_code(m.group(0)) for m in CODE_RE.finditer(text or "")]
    return list(dict.fromkeys(codes))


def _normalize_stock_text(text: str) -> str:
    return re.sub(r"[\s,，。.!！?？:：;；、/\\-_*()（）【】\\[\\]{}<>《》\"'“”‘’]+", "", str(text or "").lower())


@lru_cache(maxsize=1)
def _stock_basic_records() -> tuple[dict, ...]:
    path = os.path.join(DATA_DIR, "stock_basic_cache.csv")
    if not os.path.exists(path):
        return tuple()
    try:
        basic = pd.read_csv(path)
    except Exception:
        return tuple()
    records = []
    for _, row in basic.iterrows():
        ts_code = str(row.get("ts_code") or "")
        name = str(row.get("name") or "")
        if not ts_code or not name or is_index_like_name(name):
            continue
        records.append({
            "ts_code": ts_code,
            "name": name,
            "name_key": _normalize_stock_text(name),
            "market": str(row.get("market") or ""),
            "status": str(row.get("status") or ""),
        })
    return tuple(records)


def lookup_stock_name(ts_code: str) -> str:
    code = normalize_ts_code(ts_code)
    for item in _stock_basic_records():
        if item["ts_code"] == code:
            return item["name"]
    return ""


def lookup_stock_code_by_name(name: str) -> str:
    """Resolve a Chinese stock name or alias to ts_code."""
    key = _normalize_stock_text(name)
    if not key:
        return ""
    exact = [item for item in _stock_basic_records() if item["name_key"] == key]
    if exact:
        return exact[0]["ts_code"]
    prefix = [item for item in _stock_basic_records() if item["name_key"].startswith(key) or key.startswith(item["name_key"])]
    prefix.sort(key=lambda x: len(x["name_key"]), reverse=True)
    return prefix[0]["ts_code"] if prefix else ""


def extract_stock_mentions(text: str, max_results: int = 5) -> list[str]:
    """Extract stock codes from explicit codes and Chinese stock names in text."""
    codes = extract_stock_codes(text)
    seen = set(codes)
    key = _normalize_stock_text(text)
    candidates = sorted(_stock_basic_records(), key=lambda x: len(x["name_key"]), reverse=True)
    for item in candidates:
        name_key = item["name_key"]
        if len(name_key) < 2:
            continue
        if name_key in key and item["ts_code"] not in seen:
            codes.append(item["ts_code"])
            seen.add(item["ts_code"])
            if len(codes) >= max_results:
                break
    return codes


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _pct(value: float) -> str:
    return f"{'+' if value > 0 else ''}{value:.2f}%"


def _latest_trade_date() -> str:
    df = load_index_daily()
    if df is not None and not df.empty:
        return str(df.iloc[-1]["trade_date"])
    return ""


def build_technical_snapshot(ts_code: str, days: int = 60) -> dict:
    code = normalize_ts_code(ts_code)
    df = load_daily(code)
    if df is None or df.empty:
        return {"ok": False, "error": f"未找到 {code} 的行情数据"}
    df = compute_mas(compute_limit_status(df)).sort_values("trade_date")
    recent = df.tail(max(20, days))
    latest = recent.iloc[-1]
    close = _safe_float(latest.get("close"))
    prev20 = recent.iloc[-20] if len(recent) >= 20 else recent.iloc[0]
    prev60 = recent.iloc[-60] if len(recent) >= 60 else recent.iloc[0]
    pct_20 = (close - _safe_float(prev20.get("close"), close)) / (_safe_float(prev20.get("close"), close) or close or 1) * 100
    pct_60 = (close - _safe_float(prev60.get("close"), close)) / (_safe_float(prev60.get("close"), close) or close or 1) * 100
    vol = _safe_float(latest.get("vol"))
    avg_vol20 = _safe_float(recent.tail(20)["vol"].mean() if "vol" in recent.columns else 0)
    volume_ratio = vol / avg_vol20 if avg_vol20 > 0 else 1.0
    turnover = _safe_float(latest.get("turnover_rate"))
    ma_values = {f"ma{n}": _safe_float(latest.get(f"ma{n}")) for n in (5, 10, 20, 60)}
    ma_dev = {
        k: ((close - v) / v * 100 if v else 0.0)
        for k, v in ma_values.items()
    }
    pct_series = pd.to_numeric(recent.tail(10).get("pct_chg", pd.Series(dtype=float)), errors="coerce").fillna(0)
    limit_up_count = int((pct_series >= 9.8).sum())

    trend_parts = []
    if close > ma_values["ma5"] > ma_values["ma10"] > ma_values["ma20"]:
        trend_parts.append("短期均线多头")
    elif close < ma_values["ma5"] < ma_values["ma10"] < ma_values["ma20"]:
        trend_parts.append("短期均线空头")
    else:
        trend_parts.append("均线结构混合")
    if volume_ratio >= 1.5:
        trend_parts.append("明显放量")
    elif volume_ratio <= 0.7:
        trend_parts.append("缩量")
    else:
        trend_parts.append("量能平稳")
    if limit_up_count:
        trend_parts.append(f"近10日{limit_up_count}次涨停")

    return {
        "ok": True,
        "ts_code": code,
        "trade_date": str(latest.get("trade_date")),
        "close": close,
        "pct_chg": _safe_float(latest.get("pct_chg")),
        "pct_20": pct_20,
        "pct_60": pct_60,
        "turnover_rate": turnover,
        "volume_ratio": volume_ratio,
        "ma": ma_values,
        "ma_deviation": ma_dev,
        "limit_up_count_10d": limit_up_count,
        "summary": "，".join(trend_parts),
    }


def _business_brief(ts_code: str, max_chars: int = 520) -> tuple[str, str]:
    name = lookup_stock_name(ts_code)
    freshness = get_freshness(ts_code)
    if not freshness or not freshness.get("is_fresh") or freshness.get("is_bad"):
        refresh = refresh_company_business_cache(ts_code, name)
        if refresh.get("ok"):
            freshness = get_freshness(ts_code)
        else:
            content = get_cached(ts_code)
            if content:
                clean = re.sub(r"#+\s*", "", content)
                clean = re.sub(r"\n{2,}", "\n", clean).strip()
                status = (
                    f"可靠缓存日期 {freshness.get('date')}，刷新失败: {refresh.get('error')}"
                    if freshness else f"缓存可用但刷新失败: {refresh.get('error')}"
                )
                return clean[:max_chars], status
            return (
                f"暂无可靠公司业务缓存；已尝试 MiniMax 刷新但失败: {refresh.get('error')}",
                "无可靠缓存",
            )
    content = get_cached(ts_code)
    if not content:
        return "暂无可靠公司业务缓存。", "无可靠缓存"
    clean = re.sub(r"#+\s*", "", content)
    clean = re.sub(r"\n{2,}", "\n", clean).strip()
    status = f"缓存日期 {freshness.get('date')}" if freshness else "有缓存"
    return clean[:max_chars], status


def _policy_match_text(business_text: str) -> str:
    try:
        signals = extract_policy_signals()
    except Exception:
        return "政策信号读取失败。"
    top = signals.get("top_industries") or []
    hits = []
    for item in top:
        industry = item.get("industry") or ""
        if industry and industry in business_text:
            hits.append(f"{industry}({item.get('count', 0)})")
    if not hits and top:
        hits = [f"{x.get('industry')}({x.get('count', 0)})" for x in top[:5]]
        return "近期政策高频方向: " + "、".join(hits)
    if hits:
        return "公司业务与政策方向可能相关: " + "、".join(hits[:5])
    return signals.get("summary") or "近期无明显政策匹配。"


def _agent_reference(ts_code: str) -> str:
    conn = get_conn()
    rows = conn.execute(
        """SELECT a.display_name, p.quantity, p.avg_cost, p.current_price, p.unrealized_pnl
           FROM agent_position p
           JOIN agent_info a ON a.id = p.agent_id
           WHERE p.ts_code=? AND p.quantity > 0
           ORDER BY a.id""",
        (normalize_ts_code(ts_code),),
    ).fetchall()
    trades = conn.execute(
        """SELECT a.display_name, t.direction, t.quantity, t.price, t.trade_date
           FROM agent_trade_log t
           JOIN agent_info a ON a.id = t.agent_id
           WHERE t.ts_code=?
           ORDER BY t.trade_date DESC, t.id DESC LIMIT 5""",
        (normalize_ts_code(ts_code),),
    ).fetchall()
    conn.close()
    lines = []
    if rows:
        lines.append("当前持仓参考:")
        for r in rows:
            lines.append(
                f"- {r['display_name']}: {r['quantity']}股 成本{_safe_float(r['avg_cost']):.2f} "
                f"现价{_safe_float(r['current_price']):.2f} 浮盈{_safe_float(r['unrealized_pnl']):.2f}"
            )
    if trades:
        lines.append("近期交易参考:")
        for t in trades:
            lines.append(f"- {t['trade_date']} {t['display_name']} {t['direction']} {t['quantity']}股 @{_safe_float(t['price']):.2f}")
    return "\n".join(lines) if lines else "当前交易 Agent 无该股持仓或近期交易。"


def generate_stock_report(ts_code: str, profile: dict | None = None) -> str:
    code = normalize_ts_code(ts_code)
    name = lookup_stock_name(code)
    tech = build_technical_snapshot(code)
    if not tech.get("ok"):
        return tech.get("error", f"{code} 分析失败")
    business, freshness = _business_brief(code)
    policy = _policy_match_text(business)
    agent_ref = _agent_reference(code)
    p = profile or {}

    close = tech["close"]
    ma = tech["ma"]
    dev = tech["ma_deviation"]
    risk_notes = []
    if tech["pct_chg"] <= -7:
        risk_notes.append("当日跌幅较大，短线波动风险高")
    if tech["volume_ratio"] >= 2:
        risk_notes.append("放量显著，需要确认是突破还是高位分歧")
    if abs(dev.get("ma20", 0)) >= 12:
        risk_notes.append("偏离20日均线较大，追涨/杀跌性价比下降")
    if "ST" in name:
        risk_notes.append("ST 标的风险较高")
    if not risk_notes:
        risk_notes.append("未触发极端技术风险，但仍需结合仓位控制")

    view = "观察"
    if close > ma.get("ma20", 0) and tech["pct_20"] > 0 and tech["volume_ratio"] >= 0.8:
        view = "偏强观察"
    if close < ma.get("ma20", 0) and tech["pct_20"] < 0:
        view = "谨慎观察"

    return "\n".join([
        f"{code} {name} 结构化分析报告",
        f"日期: {tech['trade_date']}  结论: {view}",
        "",
        "一、技术面",
        f"- 收盘 {close:.2f}，当日涨跌 {_pct(tech['pct_chg'])}，20日 {_pct(tech['pct_20'])}，60日 {_pct(tech['pct_60'])}",
        f"- MA5/10/20/60: {ma['ma5']:.2f}/{ma['ma10']:.2f}/{ma['ma20']:.2f}/{ma['ma60']:.2f}",
        f"- 换手 {tech['turnover_rate']:.2f}%，量比约 {tech['volume_ratio']:.2f}，{tech['summary']}",
        "",
        "二、业务与基本面",
        f"- {freshness}",
        business,
        "",
        "三、政策与板块",
        f"- {policy}",
        "",
        "四、交易 Agent 参考",
        agent_ref,
        "",
        "五、风险与建议",
        *[f"- {x}" for x in risk_notes],
        f"- 用户偏好参考: 风险 {p.get('risk_level', '中等')}，周期 {p.get('horizon', '短线')}",
        "- 仅供研究，不构成投资建议。",
    ])


def compare_stocks(ts_codes: list[str], profile: dict | None = None) -> str:
    codes = [normalize_ts_code(c) for c in ts_codes][:5]
    rows = []
    for code in codes:
        tech = build_technical_snapshot(code)
        if tech.get("ok"):
            rows.append({**tech, "name": lookup_stock_name(code)})
    if not rows:
        return "没有可比较的有效股票代码。"
    rows.sort(key=lambda x: (x["pct_20"], x["volume_ratio"], -abs(x["ma_deviation"].get("ma20", 0))), reverse=True)
    lines = ["多股对比", "排序依据: 20日表现、量能、均线偏离"]
    for idx, r in enumerate(rows, 1):
        lines.append(
            f"{idx}. {r['ts_code']} {r['name']} 收盘{r['close']:.2f} "
            f"当日{_pct(r['pct_chg'])} 20日{_pct(r['pct_20'])} "
            f"换手{r['turnover_rate']:.2f}% 量比{r['volume_ratio']:.2f} {r['summary']}"
        )
    lines.append("仅供研究，不构成投资建议。")
    return "\n".join(lines)


def watchlist_alerts(chat_id: str, watchlist: list[dict]) -> str:
    if not watchlist:
        return "关注股: 未设置"
    lines = ["关注股提醒:"]
    for item in watchlist[:8]:
        tech = build_technical_snapshot(item["ts_code"])
        if not tech.get("ok"):
            continue
        flags = []
        if abs(tech["pct_chg"]) >= 5:
            flags.append(f"当日{_pct(tech['pct_chg'])}")
        if tech["volume_ratio"] >= 1.8:
            flags.append(f"放量{tech['volume_ratio']:.1f}x")
        if tech["limit_up_count_10d"]:
            flags.append(f"近10日{tech['limit_up_count_10d']}涨停")
        if flags:
            lines.append(f"- {item['ts_code']} {item.get('stock_name') or lookup_stock_name(item['ts_code'])}: {'，'.join(flags)}")
    return "\n".join(lines) if len(lines) > 1 else "关注股: 今日无显著异动"
