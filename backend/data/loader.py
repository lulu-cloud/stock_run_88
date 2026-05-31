"""CSV 数据加载器"""

import os
import pandas as pd
from typing import Optional
from backend.config import DAILY_DIR, INDEX_DIR, MA_PERIODS
from backend.trading.rules import is_main_board, normalize_ts_code

NUMERIC_DAILY_COLUMNS = ["open", "high", "low", "close", "pre_close", "vol", "amount", "turnover_rate", "pct_chg"]


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in NUMERIC_DAILY_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def list_main_board_stocks() -> pd.DataFrame:
    """加载主板股票列表，严格排除指数、基金、债券等非个股代码。"""
    basic = pd.read_csv(os.path.join(os.path.dirname(DAILY_DIR), "stock_basic_cache.csv"))
    rows = []
    for _, row in basic.iterrows():
        if is_main_board(
            row.get("ts_code", ""),
            row.get("name", ""),
            row.get("market", ""),
            row.get("status", ""),
        ):
            rows.append(row)
    if not rows:
        return basic.iloc[0:0].copy()
    return pd.DataFrame(rows).reset_index(drop=True)


def load_daily(ts_code: str) -> Optional[pd.DataFrame]:
    """加载单只股票的日线数据"""
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
    """判断涨停/跌停状态（±10% 或 ST ±5%）"""
    df = df.copy()
    if "pct_chg" in df.columns:
        df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce").fillna(0)
    df["is_limit_up"] = df["pct_chg"] >= 9.9
    df["is_limit_down"] = df["pct_chg"] <= -9.9
    return df
