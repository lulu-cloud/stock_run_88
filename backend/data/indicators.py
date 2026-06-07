"""指标计算：板块热度、均线偏离等

所有数学计算封装为独立函数，禁止 LLM 自行算数推理
"""

import os
import pandas as pd
from typing import Optional
from collections import Counter
from backend.config import DAILY_DIR, MA_PERIODS
from backend.trading.rules import is_main_board, is_st_stock, is_st_value
from backend.data.tags import load_tag_map


NUMERIC_DAILY_COLUMNS = ["open", "high", "low", "close", "pre_close", "vol", "amount", "turnover_rate", "pct_chg", "is_st"]


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _coerce_daily_numeric(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in NUMERIC_DAILY_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _row_is_st(row) -> bool:
    return is_st_value(row.get("is_st", 0))


def _truthy_flag(value) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y"}


def _row_is_limit_up(row) -> bool:
    if "is_limit_up" in row and str(row.get("is_limit_up")) not in ("", "nan", "None"):
        return _truthy_flag(row.get("is_limit_up"))
    pct = _safe_float(row.get("pct_chg", 0))
    return pct >= (4.9 if _row_is_st(row) else 9.8)


def _row_is_limit_down(row) -> bool:
    if "is_limit_down" in row and str(row.get("is_limit_down")) not in ("", "nan", "None"):
        return _truthy_flag(row.get("is_limit_down"))
    pct = _safe_float(row.get("pct_chg", 0))
    return pct <= (-4.9 if _row_is_st(row) else -9.8)


def compute_sector_heat(trade_date: str) -> list[dict]:
    """计算每日板块热度分值。

    维度：
    - 板块涨停家数
    - 连板高度（最高连板数）
    - 成交量放量程度（相对 20 日均量）

    返回：按热度降序排列的板块列表
    """
    sector_stats = _aggregate_sector_data(trade_date)
    if not sector_stats:
        return []

    # 分别归一化各维度
    max_limit_up = max(s["limit_up_count"] for s in sector_stats) or 1
    max_consecutive = max(s["max_consecutive_boards"] for s in sector_stats) or 1
    max_volume_ratio = max(s["avg_volume_ratio"] for s in sector_stats) or 1

    for s in sector_stats:
        score = (
            0.5 * (s["limit_up_count"] / max_limit_up)
            + 0.3 * (s["max_consecutive_boards"] / max_consecutive)
            + 0.2 * (s["avg_volume_ratio"] / max_volume_ratio)
        )
        s["heat_score"] = round(score * 100, 2)

    sector_stats.sort(key=lambda x: x["heat_score"], reverse=True)
    return sector_stats


def compute_market_strength_by_sector(trade_date: str, lookback_days: int = 3,
                                      top_n: int = 10) -> dict:
    """按近 N 个交易日股价表现统计强势/弱势板块。

    只使用本地日线 CSV 和已落地的行业/板块 tag，不调用 LLM 算数。
    """
    lookback_days = max(2, min(int(lookback_days or 3), 10))
    tag_map = load_tag_map()
    basic = pd.read_csv(os.path.join(os.path.dirname(DAILY_DIR), "stock_basic_cache.csv"))
    main_board = basic[
        basic.apply(
            lambda r: is_main_board(r.get("ts_code", ""), r.get("name", ""), r.get("market", ""), r.get("status", ""), r.get("is_st", None)),
            axis=1,
        )
    ]
    sectors: dict[str, dict] = {}
    for _, row in main_board.iterrows():
        ts_code = row["ts_code"]
        name = row.get("name", ts_code)
        df = _load_single_daily(ts_code)
        if df is None or df.empty:
            continue
        upto = df[df["trade_date"] <= trade_date].tail(lookback_days)
        if len(upto) < 2:
            continue
        first_close = _safe_float(upto.iloc[0].get("close", 0))
        last = upto.iloc[-1]
        if first_close <= 0:
            continue
        pct = (_safe_float(last.get("close", 0)) - first_close) / first_close * 100
        today_pct = _safe_float(last.get("pct_chg", 0))
        turnover = _safe_float(last.get("turnover_rate", 0))
        if pd.isna(pct):
            continue
        if pd.isna(today_pct):
            today_pct = 0.0
        if pd.isna(turnover):
            turnover = 0.0
        pct_series = pd.to_numeric(upto.get("pct_chg", pd.Series(dtype=float)), errors="coerce").fillna(0)
        limit_up_days = int(sum(1 for _, recent_row in upto.iterrows() if _row_is_limit_up(recent_row)))
        tag = tag_map.get(ts_code, {})
        sector = tag.get("sector_tag") or tag.get("industry_tag") or "其他"
        stat = sectors.setdefault(sector, {
            "sector": sector,
            "stock_count": 0,
            "total_pct": 0.0,
            "total_today_pct": 0.0,
            "total_turnover": 0.0,
            "limit_up_count": 0,
            "leaders": [],
        })
        stat["stock_count"] += 1
        stat["total_pct"] += pct
        stat["total_today_pct"] += today_pct
        stat["total_turnover"] += turnover
        stat["limit_up_count"] += limit_up_days
        stat["leaders"].append({
            "ts_code": ts_code,
            "name": name,
            "pct": round(pct, 2),
            "today_pct": round(today_pct, 2),
            "turnover_rate": round(turnover, 2),
            "limit_up_days": limit_up_days,
        })

    rows = []
    for stat in sectors.values():
        count = stat["stock_count"] or 1
        avg_pct = stat["total_pct"] / count
        avg_today_pct = stat["total_today_pct"] / count
        avg_turnover = stat["total_turnover"] / count
        stat["avg_pct"] = round(avg_pct, 2)
        stat["avg_today_pct"] = round(avg_today_pct, 2)
        stat["avg_turnover"] = round(avg_turnover, 2)
        stat["leaders"] = sorted(
            stat["leaders"],
            key=lambda x: (x["limit_up_days"], x["pct"], x["turnover_rate"]),
            reverse=True,
        )[:8]
        stat["strength_score"] = round(avg_pct * 2 + avg_today_pct + stat["limit_up_count"] * 3 + avg_turnover * 0.2, 2)
        rows.append(stat)

    strong = sorted(rows, key=lambda x: x["strength_score"], reverse=True)[:top_n]
    weak = sorted(rows, key=lambda x: x["strength_score"])[:top_n]
    return {
        "trade_date": trade_date,
        "lookback_days": lookback_days,
        "strong": strong,
        "weak": weak,
        "total": len(rows),
    }


def _resolve_trade_date(trade_date: str = "") -> str:
    if trade_date:
        return str(trade_date)
    index_path = os.path.join(os.path.dirname(DAILY_DIR), "index", "000001.SH_daily.csv")
    if os.path.exists(index_path):
        try:
            df = pd.read_csv(index_path)
            if not df.empty:
                return str(df.iloc[-1]["trade_date"])
        except Exception:
            pass
    return ""


def compute_market_breadth(trade_date: str = "") -> dict:
    """统计指定交易日全 A 市场宽度。"""
    effective_date = _resolve_trade_date(trade_date)
    tag_map = load_tag_map()
    basic = pd.read_csv(os.path.join(os.path.dirname(DAILY_DIR), "stock_basic_cache.csv"))
    main_board = basic[
        basic.apply(
            lambda r: is_main_board(r.get("ts_code", ""), r.get("name", ""), r.get("market", ""), r.get("status", ""), r.get("is_st", None)),
            axis=1,
        )
    ]
    rows = []
    sector_stats: dict[str, dict] = {}
    for _, row in main_board.iterrows():
        ts_code = row["ts_code"]
        name = row.get("name", ts_code)
        df = _load_single_daily(ts_code)
        if df is None or df.empty:
            continue
        upto = df[df["trade_date"] <= effective_date] if effective_date else df
        if upto.empty:
            continue
        day = upto.iloc[-1]
        pct = _safe_float(day.get("pct_chg", 0))
        amount = _safe_float(day.get("amount", 0))
        turnover = _safe_float(day.get("turnover_rate", 0))
        item = {
            "ts_code": ts_code,
            "name": name,
            "pct_chg": round(pct, 2),
            "amount": round(amount, 2),
            "turnover_rate": round(turnover, 2),
            "is_st": _row_is_st(day),
            "is_limit_up": _row_is_limit_up(day),
            "is_limit_down": _row_is_limit_down(day),
        }
        rows.append(item)
        tag = tag_map.get(ts_code, {})
        sector = tag.get("sector_tag") or tag.get("industry_tag") or "其他"
        stat = sector_stats.setdefault(sector, {
            "sector": sector,
            "stock_count": 0,
            "limit_up_count": 0,
            "big_up_count": 0,
            "limit_down_count": 0,
            "big_down_count": 0,
            "up_count": 0,
            "down_count": 0,
            "pct_sum": 0.0,
            "amount_sum": 0.0,
            "leaders": [],
            "laggards": [],
        })
        stat["stock_count"] += 1
        stat["pct_sum"] += pct
        stat["amount_sum"] += amount
        stat["up_count"] += 1 if pct > 0 else 0
        stat["down_count"] += 1 if pct < 0 else 0
        stat["limit_up_count"] += 1 if item["is_limit_up"] else 0
        stat["big_up_count"] += 1 if pct >= 5.0 else 0
        stat["limit_down_count"] += 1 if item["is_limit_down"] else 0
        stat["big_down_count"] += 1 if pct <= -5.0 else 0
        stat["leaders"].append(item)
        stat["laggards"].append(item)

    pct_values = [r["pct_chg"] for r in rows]
    total = len(rows)
    up_count = sum(1 for x in pct_values if x > 0)
    down_count = sum(1 for x in pct_values if x < 0)
    flat_count = total - up_count - down_count
    limit_up_count = sum(1 for x in rows if x.get("is_limit_up"))
    big_up_count = sum(1 for x in pct_values if x >= 5.0)
    limit_down_count = sum(1 for x in rows if x.get("is_limit_down"))
    big_down_count = sum(1 for x in pct_values if x <= -5.0)
    avg_pct = sum(pct_values) / total if total else 0.0
    median_pct = float(pd.Series(pct_values).median()) if pct_values else 0.0
    risk_on_score = (
        (up_count / total * 45 if total else 0)
        + min(limit_up_count, 120) / 120 * 25
        + min(big_up_count, 500) / 500 * 20
        - min(big_down_count, 500) / 500 * 15
    )
    market_regime = "risk_on" if risk_on_score >= 58 else ("risk_off" if risk_on_score <= 35 else "neutral")
    return {
        "trade_date": effective_date,
        "total": total,
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "limit_up_count": limit_up_count,
        "big_up_count": big_up_count,
        "limit_down_count": limit_down_count,
        "big_down_count": big_down_count,
        "avg_pct": round(avg_pct, 2),
        "median_pct": round(median_pct, 2),
        "risk_on_score": round(max(0, min(100, risk_on_score)), 2),
        "market_regime": market_regime,
        "leaders": sorted(rows, key=lambda x: x["pct_chg"], reverse=True)[:20],
        "laggards": sorted(rows, key=lambda x: x["pct_chg"])[:20],
        "sector_stats": list(sector_stats.values()),
    }


def compute_sector_temperature(trade_date: str = "", top_n: int = 20) -> dict:
    """按市场宽度聚合板块温度。"""
    breadth = compute_market_breadth(trade_date)
    sectors = []
    for stat in breadth.get("sector_stats", []):
        count = int(stat.get("stock_count") or 1)
        avg_pct = float(stat.get("pct_sum") or 0) / count
        up_ratio = float(stat.get("up_count") or 0) / count
        limit_up = int(stat.get("limit_up_count") or 0)
        big_up = int(stat.get("big_up_count") or 0)
        limit_down = int(stat.get("limit_down_count") or 0)
        big_down = int(stat.get("big_down_count") or 0)
        heat_score = avg_pct * 8 + up_ratio * 35 + limit_up * 6 + big_up * 1.5 - limit_down * 6 - big_down * 1.8
        risk_score = limit_down * 8 + big_down * 2 + max(0.0, -avg_pct) * 6
        leaders = sorted(stat.get("leaders") or [], key=lambda x: x["pct_chg"], reverse=True)[:6]
        laggards = sorted(stat.get("laggards") or [], key=lambda x: x["pct_chg"])[:4]
        sectors.append({
            "sector": stat.get("sector") or "其他",
            "stock_count": count,
            "avg_pct": round(avg_pct, 2),
            "up_ratio": round(up_ratio, 4),
            "limit_up_count": limit_up,
            "big_up_count": big_up,
            "limit_down_count": limit_down,
            "big_down_count": big_down,
            "amount": round(float(stat.get("amount_sum") or 0), 2),
            "heat_score": round(heat_score, 2),
            "risk_score": round(risk_score, 2),
            "leaders": leaders,
            "laggards": laggards,
        })
    sectors.sort(key=lambda x: (x["heat_score"], x["limit_up_count"], x["big_up_count"]), reverse=True)
    return {
        "trade_date": breadth.get("trade_date"),
        "market_regime": breadth.get("market_regime"),
        "risk_on_score": breadth.get("risk_on_score"),
        "sectors": sectors[:max(1, int(top_n or 20))],
        "weak_sectors": sorted(sectors, key=lambda x: (x["heat_score"], -x["risk_score"]))[:max(1, min(int(top_n or 20), 20))],
        "breadth": {k: v for k, v in breadth.items() if k not in {"sector_stats", "leaders", "laggards"}},
    }


def _aggregate_sector_data(trade_date: str) -> list[dict]:
    """聚合同一板块下所有股票的涨停和成交量数据"""
    # 读取公司业务板块映射
    business_dir = os.path.join(os.path.dirname(DAILY_DIR), "company_business")
    sector_map = _load_sector_map(business_dir)

    sectors: dict[str, dict] = {}
    basic = pd.read_csv(os.path.join(os.path.dirname(DAILY_DIR), "stock_basic_cache.csv"))
    main_board = basic[
        basic.apply(
            lambda r: is_main_board(r.get("ts_code", ""), r.get("name", ""), r.get("market", ""), r.get("status", ""), r.get("is_st", None)),
            axis=1,
        )
    ]

    for _, row in main_board.iterrows():
        ts_code = row["ts_code"]
        sectors_list = sector_map.get(ts_code, ["其他"])

        df = _load_single_daily(ts_code)
        if df is None or df.empty:
            continue

        day_data = df[df["trade_date"] == trade_date]
        if day_data.empty:
            continue

        recent = df[df["trade_date"] <= trade_date].tail(20)
        if recent.empty:
            continue

        avg_vol = pd.to_numeric(recent["vol"], errors="coerce").mean() if "vol" in recent.columns else 1
        day_vol = _safe_float(day_data.iloc[0].get("vol", 0))
        volume_ratio = day_vol / avg_vol if avg_vol > 0 else 1.0

        is_limit_up = _row_is_limit_up(day_data.iloc[0])
        consecutive = _count_consecutive_boards(df, day_data.index[0])

        for sector in sectors_list:
            if sector not in sectors:
                sectors[sector] = {
                    "sector": sector,
                    "limit_up_count": 0,
                    "max_consecutive_boards": 0,
                    "total_volume_ratio": 0.0,
                    "stock_count": 0,
                }
            s = sectors[sector]
            if is_limit_up:
                s["limit_up_count"] += 1
            s["max_consecutive_boards"] = max(s["max_consecutive_boards"], consecutive)
            s["total_volume_ratio"] += volume_ratio
            s["stock_count"] += 1

    result = []
    for s in sectors.values():
        if s["stock_count"] > 0:
            s["avg_volume_ratio"] = s["total_volume_ratio"] / s["stock_count"]
        result.append(s)

    return result


def _load_sector_map(business_dir: str) -> dict[str, list[str]]:
    """从 company_business MD 文件中加载股票-板块映射"""
    sector_map: dict[str, list[str]] = {}
    if not os.path.exists(business_dir):
        return sector_map

    for filename in os.listdir(business_dir):
        if not filename.endswith(".md"):
            continue
        # 文件名格式: ts_code_name.md
        parts = filename.split("_", 1)
        ts_code = parts[0] + "_" + parts[1].split("_")[0] if len(parts) > 1 else filename.replace(".md", "")
        # 简单格式: 600000.SH_浦发银行.md
        ts_code = filename.split("_")[0]
        # 实际上是 ts_code.md 格式或者 ts_code_name.md
        # 从文件名提取 ts_code (如 600000.SH)
        base = filename.replace(".md", "")
        # ts_code 格式: 6位数字.SH 或 6位数字.SZ
        if "." in base[:10]:
            ts_code = base[:9]  # e.g. 600000.SH
        else:
            ts_code = base

        filepath = os.path.join(business_dir, filename)
        try:
            with open(filepath, "r") as f:
                content = f.read()
            sectors = _parse_sectors_from_md(content)
            if sectors:
                sector_map[ts_code] = sectors
        except Exception:
            pass

    return sector_map


def _parse_sectors_from_md(content: str) -> list[str]:
    """从 MD 内容中解析所属板块"""
    sectors = []
    in_section = False
    for line in content.split("\n"):
        if line.startswith("## 所属板块"):
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            stripped = line.strip().lstrip("- ").strip()
            if stripped:
                sectors.append(stripped)
    return sectors


def _load_single_daily(ts_code: str) -> Optional[pd.DataFrame]:
    """加载单只股票日线"""
    filepath = os.path.join(DAILY_DIR, f"{ts_code}_daily.csv")
    if not os.path.exists(filepath):
        return None
    df = pd.read_csv(filepath)
    df["trade_date"] = df["trade_date"].astype(str)
    return _coerce_daily_numeric(df)


def _count_consecutive_boards(df: pd.DataFrame, idx: int) -> int:
    """计算连板数量"""
    count = 0
    i = idx
    while i >= 0:
        row = df.iloc[i]
        if _row_is_limit_up(row):
            count += 1
            i -= 1
        else:
            break
    return count


def compute_ma_deviation(close_price: float, ma_value: float) -> float:
    """计算股价相对均线的偏离百分比"""
    if ma_value == 0:
        return 0.0
    return round((close_price - ma_value) / ma_value * 100, 2)


# ---------------------------------------------------------------------------
# MACD / EMA 技术指标
# ---------------------------------------------------------------------------

def compute_ema(series: "pd.Series", period: int) -> "pd.Series":
    """指数移动平均线 (EMA)

    EMA_t = price_t * alpha + EMA_{t-1} * (1-alpha)
    alpha = 2 / (period + 1)
    """
    return series.ewm(span=period, adjust=False).mean()


def compute_macd(df: "pd.DataFrame", fast: int = 12, slow: int = 26,
                 signal: int = 9) -> "pd.DataFrame":
    """计算 MACD 指标

    添加列:
        dif:     EMA(fast) - EMA(slow)    快慢线差值
        dea:     EMA(dif, signal)          DIF 的信号线
        macd_histogram:  2 * (dif - dea)   柱状线
        macd_buy_cross:  金叉 (dif 上穿 dea)
        macd_sell_cross: 死叉 (dif 下穿 dea)
    """
    close = df["close"].values
    dif = compute_ema(pd.Series(close), fast) - compute_ema(pd.Series(close), slow)
    dea = compute_ema(dif, signal)
    macd_hist = 2 * (dif - dea)

    df = df.copy()
    df["dif"] = dif.values
    df["dea"] = dea.values
    df["macd_histogram"] = macd_hist.values
    df["dif_above_dea"] = df["dif"] > df["dea"]
    shifted = df["dif_above_dea"].shift(1).fillna(False)
    df["macd_buy_cross"] = df["dif_above_dea"] & (~shifted)
    df["macd_sell_cross"] = (~df["dif_above_dea"]) & shifted
    return df
