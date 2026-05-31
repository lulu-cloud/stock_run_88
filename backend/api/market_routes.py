"""行情 & 板块 API"""

import pandas as pd
from fastapi import APIRouter, Query
from backend.data.loader import load_index_daily, load_daily, compute_mas, compute_limit_status, list_main_board_stocks
from backend.data.indicators import (
    compute_market_breadth,
    compute_market_strength_by_sector,
    compute_sector_heat,
    compute_sector_temperature,
)
from backend.data.tags import load_tag_map

router = APIRouter(prefix="/api/market", tags=["market"])

# Cache stock list for search
_stock_search_cache = None

def _get_stock_list():
    global _stock_search_cache
    if _stock_search_cache is None:
        stocks = list_main_board_stocks()
        tag_map = load_tag_map()
        _stock_search_cache = [
            {
                "ts_code": r["ts_code"],
                "name": r["name"],
                "sector_tag": tag_map.get(r["ts_code"], {}).get("sector_tag", "其他"),
                "industry_tag": tag_map.get(r["ts_code"], {}).get("industry_tag", "其他"),
            }
            for _, r in stocks.iterrows()
        ]
    return _stock_search_cache


@router.get("/stocks/search")
async def search_stocks(
    q: str = Query(default="", description="搜索关键词（代码或名称）"),
    sector_tag: str = Query(default=""),
    industry_tag: str = Query(default=""),
):
    """股票代码/名称模糊搜索"""
    if not q and not sector_tag and not industry_tag:
        return {"results": []}
    all_stocks = _get_stock_list()
    q_lower = q.lower()
    matches = []
    for s in all_stocks:
        if sector_tag and s.get("sector_tag") != sector_tag:
            continue
        if industry_tag and s.get("industry_tag") != industry_tag:
            continue
        if not q or q_lower in s["ts_code"].lower() or q_lower in s["name"].lower():
            matches.append(s)
        if len(matches) >= 20:
            break
    return {"results": matches, "total": len(matches)}


@router.get("/tags")
async def get_market_tags():
    """获取股票板块/行业 Tag 列表"""
    stocks = _get_stock_list()
    sectors = sorted({s.get("sector_tag") for s in stocks if s.get("sector_tag")})
    industries = sorted({s.get("industry_tag") for s in stocks if s.get("industry_tag")})
    return {"sector_tags": sectors, "industry_tags": industries}


@router.get("/index")
async def get_index_data(days: int = Query(default=30, le=2000)):
    """获取上证指数最近 N 天数据"""
    df = load_index_daily()
    if df is None or df.empty:
        return {"data": [], "error": "暂无指数数据"}

    recent = df.tail(days)
    return {
        "data": [
            {
                "trade_date": str(row["trade_date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "pct_chg": float(row.get("pct_chg", 0)),
                "vol": float(row.get("vol", 0)),
            }
            for _, row in recent.iterrows()
        ],
        "latest": {
            "trade_date": str(df.iloc[-1]["trade_date"]),
            "close": float(df.iloc[-1]["close"]),
            "pct_chg": float(df.iloc[-1].get("pct_chg", 0)),
        },
    }


@router.get("/sector-heat")
async def get_sector_heat(trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新")):
    """获取板块热度排行"""
    results = compute_sector_heat(trade_date)
    return {"sectors": results, "total": len(results)}


# 板块强弱缓存（避免每次加载 N 千个 CSV）
import time
_sector_strength_cache: dict = {"ts": 0, "data": None}


@router.get("/sector-strength")
async def get_sector_strength(
    trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新"),
    lookback_days: int = Query(default=3, ge=2, le=10),
    refresh: bool = Query(default=False, description="跳过缓存"),
):
    """按近 N 日价格行为统计强势/弱势板块（缓存 5 分钟）"""
    effective_date = trade_date
    if not effective_date:
        df = load_index_daily()
        effective_date = str(df.iloc[-1]["trade_date"]) if df is not None and not df.empty else ""

    cache_key = f"{effective_date}_{lookback_days}"
    now = time.time()
    if not refresh and _sector_strength_cache.get("key") == cache_key and (now - _sector_strength_cache["ts"]) < 300:
        return _sector_strength_cache["data"]

    result = compute_market_strength_by_sector(effective_date, lookback_days, top_n=10)
    _sector_strength_cache["key"] = cache_key
    _sector_strength_cache["ts"] = now
    _sector_strength_cache["data"] = result
    return result


@router.get("/breadth")
async def get_market_breadth(trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新")):
    """获取全市场涨跌宽度、涨停/大涨/跌停/大跌分布。"""
    return compute_market_breadth(trade_date)


@router.get("/sector-temperature")
async def get_sector_temperature(
    trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新"),
    top_n: int = Query(default=20, ge=5, le=50),
):
    """获取按当日涨跌分布计算的板块温度。"""
    return compute_sector_temperature(trade_date, top_n)


# ---- Stock K-line for frontend ----

@router.get("/stock/kline/{ts_code}")
async def get_stock_kline(ts_code: str, days: int = Query(default=400, le=2000)):
    """获取个股K线数据（含均线、成交量）"""
    df = load_daily(ts_code)
    if df is None or df.empty:
        return {"error": "未找到数据", "data": []}

    df = compute_mas(df)
    df = compute_limit_status(df)
    recent = df.tail(days)

    # Look up name
    name = ""
    tag_map = load_tag_map()
    tags = tag_map.get(ts_code, {"sector_tag": "其他", "industry_tag": "其他"})
    try:
        stocks = list_main_board_stocks()
        match = stocks[stocks["ts_code"] == ts_code]
        if len(match) > 0:
            name = match.iloc[0]["name"]
    except Exception:
        pass

    # Related agent positions
    related = []
    agent_trades = []
    try:
        from backend.db.repository import get_conn, get_positions
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT id, display_name FROM agent_info WHERE status='active'")
        for agent_row in c.fetchall():
            positions = get_positions(agent_row["id"], conn)
            for p in positions:
                if p["ts_code"] == ts_code:
                    related.append({
                        "agent_name": agent_row["display_name"],
                        "quantity": p["quantity"],
                        "avg_cost": p["avg_cost"],
                        "current_price": p.get("current_price", 0),
                        "unrealized_pnl": p.get("unrealized_pnl", 0),
                    })
        trade_rows = conn.execute(
            """SELECT t.agent_id, a.display_name AS agent_name, t.direction, t.quantity,
                      t.price, t.trade_date, t.total_value
               FROM agent_trade_log t
               JOIN agent_info a ON a.id=t.agent_id
               WHERE t.ts_code=?
               ORDER BY t.trade_date ASC, t.id ASC""",
            (ts_code,),
        ).fetchall()
        agent_trades = [dict(r) for r in trade_rows]
        conn.close()
    except Exception:
        pass

    return {
        "ts_code": ts_code,
        "name": name,
        "sector_tag": tags.get("sector_tag", "其他"),
        "industry_tag": tags.get("industry_tag", "其他"),
        "data": [
            {
                "trade_date": str(row["trade_date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "vol": float(row.get("vol", 0)),
                "amount": float(row.get("amount", 0)),
                "pct_chg": float(row.get("pct_chg", 0)),
                "turnover_rate": float(row.get("turnover_rate", 0) or 0),
                "ma5": float(row["ma5"]) if row.get("ma5") and not pd.isna(row["ma5"]) else None,
                "ma10": float(row["ma10"]) if row.get("ma10") and not pd.isna(row["ma10"]) else None,
                "ma20": float(row["ma20"]) if row.get("ma20") and not pd.isna(row["ma20"]) else None,
                "ma60": float(row["ma60"]) if row.get("ma60") and not pd.isna(row["ma60"]) else None,
            }
            for _, row in recent.iterrows()
        ],
        "related_positions": related,
        "agent_trades": agent_trades,
    }
