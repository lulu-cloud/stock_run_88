"""CSV 数据加载器"""

import os
from functools import lru_cache
import pandas as pd
from typing import Optional
from backend.config import DAILY_DIR, INDEX_DIR, MA_PERIODS, DAILY_PRICE_ADJUSTMENT
from backend.trading.rules import is_main_board, normalize_ts_code, is_st_stock, is_st_value

NUMERIC_DAILY_COLUMNS = ["open", "high", "low", "close", "pre_close", "vol", "amount", "turnover_rate", "pct_chg", "is_st"]


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in NUMERIC_DAILY_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@lru_cache(maxsize=8192)
def latest_is_st(ts_code: str) -> bool:
    """Read the latest official baostock isST flag from local qfq daily CSV."""
    code = normalize_ts_code(ts_code)
    filepath = os.path.join(DAILY_DIR, f"{code}_daily.csv")
    if not os.path.exists(filepath):
        return False
    try:
        df = pd.read_csv(filepath, usecols=["is_st"])
        if df.empty:
            return False
        return is_st_value(df.iloc[-1].get("is_st"))
    except Exception:
        return False


@lru_cache(maxsize=4)
def _list_main_board_stock_records(include_st: bool = False) -> tuple[tuple[tuple[str, object], ...], ...]:
    basic = pd.read_csv(os.path.join(os.path.dirname(DAILY_DIR), "stock_basic_cache.csv"))
    records = []
    for _, row in basic.iterrows():
        official_st = row.get("is_st", row.get("isST", None))
        if official_st is None or (isinstance(official_st, float) and pd.isna(official_st)):
            official_st = latest_is_st(row.get("ts_code", ""))
        st_flag = is_st_value(official_st) or is_st_stock(row.get("name", ""))
        if st_flag and not include_st:
            continue
        if is_main_board(
            row.get("ts_code", ""),
            row.get("name", ""),
            row.get("market", ""),
            row.get("status", ""),
            st_flag,
            allow_st=bool(include_st),
        ):
            records.append(tuple(row.to_dict().items()))
    return tuple(records)


def list_main_board_stocks(include_st: bool = False) -> pd.DataFrame:
    """加载主板股票列表，严格排除指数、基金、债券等非个股代码。

    默认排除 ST。优先使用 stock_basic_cache 里的 is_st/isST 字段；若不存在，
    则使用 baostock 日线 CSV 最新 is_st 字段兜底，最后再用名称中的 ST 兜底。
    """
    rows = [dict(items) for items in _list_main_board_stock_records(bool(include_st))]
    if not rows:
        basic = pd.read_csv(os.path.join(os.path.dirname(DAILY_DIR), "stock_basic_cache.csv"))
        return basic.iloc[0:0].copy()
    return pd.DataFrame(rows).reset_index(drop=True)


def load_daily(ts_code: str) -> Optional[pd.DataFrame]:
    """加载单只股票日线数据。

    价格口径为 baostock 前复权，即 config.DAILY_PRICE_ADJUSTMENT == "qfq"。
    """
    ts_code = normalize_ts_code(ts_code)
    filepath = os.path.join(DAILY_DIR, f"{ts_code}_daily.csv")
    if not os.path.exists(filepath):
        return None
    df = pd.read_csv(filepath)
    df["trade_date"] = df["trade_date"].astype(str)
    return _coerce_numeric_columns(df)


def load_daily_range(ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """加载单只股票指定日期范围的日线数据"""
    df = load_daily(ts_code)
    if df is None:
        return None
    mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
    return df[mask]


def load_index_daily() -> Optional[pd.DataFrame]:
    """加载上证指数日线数据"""
    filepath = os.path.join(INDEX_DIR, "000001.SH_daily.csv")
    if not os.path.exists(filepath):
        return None
    df = pd.read_csv(filepath)
    df["trade_date"] = df["trade_date"].astype(str)
    return _coerce_numeric_columns(df)


def get_latest_date(ts_code: str) -> Optional[str]:
    """获取某只股票 CSV 中的最新交易日"""
    df = load_daily(ts_code)
    if df is None or df.empty:
        return None
    return df["trade_date"].max()


def get_date_range(ts_code: str) -> Optional[tuple]:
    """获取某只股票 CSV 中的日期范围"""
    df = load_daily(ts_code)
    if df is None or df.empty:
        return None
    return df["trade_date"].min(), df["trade_date"].max()


def compute_mas(df: pd.DataFrame) -> pd.DataFrame:
    """计算 5/10/20/60 日均线"""
    df = df.sort_values("trade_date").copy()
    for period in MA_PERIODS:
        col = f"ma{period}"
        df[col] = df["close"].rolling(window=period, min_periods=1).mean()
    return df


def compute_pct_chg(df: pd.DataFrame) -> pd.DataFrame:
    """计算涨跌幅（若未提供）"""
    if "pct_chg" in df.columns:
        return df
    df = df.sort_values("trade_date").copy()
    df["pct_chg"] = ((df["close"] - df["pre_close"]) / df["pre_close"] * 100).round(4)
    return df


def compute_limit_status(df: pd.DataFrame) -> pd.DataFrame:
    """判断涨停/跌停状态。

    普通主板按 ±10%（阈值 9.8%），ST 按 ±5%（阈值 4.9%）。
    数据价格口径为前复权，但 pct_chg/is_st 直接来自 baostock 官方日线字段。
    """
    df = df.copy()
    if "pct_chg" in df.columns:
        df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce").fillna(0)
    else:
        df = compute_pct_chg(df)
    if "is_st" in df.columns:
        st_flags = pd.to_numeric(df["is_st"], errors="coerce").fillna(0).astype(int) == 1
    else:
        st_flags = pd.Series(False, index=df.index)
    up_threshold = st_flags.map(lambda x: 4.9 if x else 9.8)
    down_threshold = st_flags.map(lambda x: -4.9 if x else -9.8)
    df["limit_threshold_pct"] = st_flags.map(lambda x: 5.0 if x else 10.0)
    df["is_limit_up"] = df["pct_chg"] >= up_threshold
    df["is_limit_down"] = df["pct_chg"] <= down_threshold
    return df


def daily_price_adjustment() -> str:
    """Return the daily price adjustment basis used by strategies/backtests."""
    return DAILY_PRICE_ADJUSTMENT
