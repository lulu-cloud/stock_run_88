"""Baostock 增量数据获取"""

import os
import time
import pandas as pd
import baostock as bs
from typing import Optional
from datetime import date

from backend.config import DAILY_DIR, INDEX_DIR, MA_PERIODS
from backend.data.loader import get_latest_date, compute_mas, compute_limit_status


def login_baostock() -> bool:
    """登录 baostock"""
    lg = bs.login()
    return lg.error_code == "0"


def logout_baostock():
    """登出 baostock"""
    bs.logout()


def _fetch_raw(ts_code: str, start_date: str, end_date_fmt: str) -> Optional[pd.DataFrame]:
    """底层 baostock 查询，返回清洗后的 DataFrame"""
    fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST"
    rs = bs.query_history_k_data_plus(
        ts_code, fields,
        start_date=start_date, end_date=end_date_fmt,
        frequency="d", adjustflag="2",
    )
    if rs.error_code != "0":
        return None

    data_list = []
    while rs.next():
        data_list.append(rs.get_row_data())
    if not data_list:
        return None

    result = pd.DataFrame(data_list, columns=rs.fields)
    if "code" in result.columns:
        result["code"] = result["code"].apply(_normalize_ts_code)

    col_map = {
        "date": "trade_date", "code": "ts_code",
        "open": "open", "high": "high", "low": "low", "close": "close",
        "preclose": "pre_close", "volume": "vol", "amount": "amount",
        "turn": "turnover_rate", "tradestatus": "trade_status",
        "pctChg": "pct_chg", "isST": "is_st",
    }
    result = result.rename(columns=col_map)
    result["trade_date"] = result["trade_date"].astype(str)

    numeric_cols = ["open", "high", "low", "close", "pre_close", "vol", "amount", "turnover_rate", "pct_chg"]
    for col in numeric_cols:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")
    if "turnover_rate" in result.columns:
        result["turnover_rate"] = result["turnover_rate"].fillna(0)

    return result


def fetch_daily_incremental(ts_code: str, start_date: str = "2026-01-01") -> Optional[pd.DataFrame]:
    """增量获取单只股票日线数据"""
    end_date_fmt = date.today().strftime("%Y-%m-%d")
    end_date_compact = date.today().strftime("%Y%m%d")

    # 检查本地最新日期（CSV 中存的是 YYYYMMDD 格式，需要统一比较）
    latest_local = get_latest_date(ts_code)
    if latest_local and latest_local >= end_date_compact:
        return None  # 数据已最新

    # actual_start 需要转换为 YYYY-MM-DD 格式给 baostock
    actual_start = max(latest_local or start_date, start_date)
    if len(actual_start) == 8:
        actual_start = f"{actual_start[:4]}-{actual_start[4:6]}-{actual_start[6:8]}"
    if actual_start >= end_date_fmt:
        return None

    return _fetch_raw(ts_code, actual_start, end_date_fmt)


def fetch_daily_full(ts_code: str, start_date: str = "2019-01-01") -> Optional[pd.DataFrame]:
    """全量获取日线数据（忽略本地缓存）"""
    end_date_fmt = date.today().strftime("%Y-%m-%d")
    return _fetch_raw(ts_code, start_date, end_date_fmt)


def _normalize_ts_code(code: str) -> str:
    """将 baostock 代码格式转为项目格式。

    sh.600001 -> 600001.SH, sz.000001 -> 000001.SZ
    """
    if code.startswith("sh."):
        return f"{code[3:]}.SH"
    elif code.startswith("sz."):
        return f"{code[3:]}.SZ"
    return code


def merge_and_save(ts_code: str, new_data: pd.DataFrame):
    """合并新数据到本地 CSV"""
    ts_code = _normalize_ts_code(ts_code)
    filepath = os.path.join(DAILY_DIR, f"{ts_code}_daily.csv")

    if os.path.exists(filepath):
        existing = pd.read_csv(filepath)
        existing["trade_date"] = existing["trade_date"].astype(str)
        combined = pd.concat([existing, new_data], ignore_index=True)
    else:
        combined = new_data

    combined["trade_date"] = combined["trade_date"].astype(str).str.replace("-", "")
    combined = combined.drop_duplicates(subset=["trade_date"], keep="last")
    combined = combined.sort_values("trade_date")

    # 计算均线和涨跌停
    combined = compute_mas(combined)
    combined = compute_limit_status(combined)

    combined.to_csv(filepath, index=False)
    return combined


def fetch_index_incremental() -> Optional[pd.DataFrame]:
    """增量获取上证指数日线"""
    return fetch_daily_incremental("sh.000001")


def batch_fetch(stock_list: list[str], delay: float = 0.5) -> dict[str, int]:
    """批量增量获取日线数据

    Args:
        stock_list: ts_code 列表
        delay: 每次请求间隔（秒）

    Returns:
        {ts_code: new_rows_count}
    """
    if not login_baostock():
        return {}

    results = {}
    for i, ts_code in enumerate(stock_list):
        try:
            new_data = fetch_daily_incremental(ts_code)
            if new_data is not None and len(new_data) > 0:
                merge_and_save(ts_code, new_data)
                results[ts_code] = len(new_data)
        except Exception as e:
            print(f"Failed to fetch {ts_code}: {e}")

        if delay > 0 and i % 10 == 9:
            time.sleep(delay)

    logout_baostock()
    return results
