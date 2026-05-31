"""A-share stock universe refresh utilities."""

from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd

from backend.config import DATA_DIR
from backend.data.fetcher import _normalize_ts_code, login_baostock, logout_baostock
from backend.db.repository import get_conn
from backend.trading.rules import is_index_like_name


CACHE_PATH = os.path.join(DATA_DIR, "stock_basic_cache.csv")
STATUS_LISTED = "上市"
STATUS_MISSING = "不在最新股票池"


def _classify_market(ts_code: str, name: str = "") -> str:
    code = _normalize_ts_code(ts_code)
    prefix = code.split(".", 1)[0]
    suffix = code.split(".", 1)[1] if "." in code else ""
    if name and is_index_like_name(name):
        return "其他"
    if suffix == "SH":
        if prefix.startswith(("688", "689")):
            return "科创板"
        if prefix.startswith(("600", "601", "603", "605")):
            return "主板"
        return "其他"
    if suffix == "SZ":
        if prefix.startswith(("300", "301")):
            return "创业板"
        if prefix.startswith("002"):
            return "中小板"
        if prefix.startswith(("000", "001")):
            return "主板"
        return "其他"
    if suffix == "BJ" or prefix.startswith(("43", "83", "87", "88", "92")):
        return "北交所"
    return "其他"


def _read_existing_cache() -> pd.DataFrame:
    if not os.path.exists(CACHE_PATH):
        return pd.DataFrame(columns=["ts_code", "tradeStatus", "name", "market", "status"])
    df = pd.read_csv(CACHE_PATH, dtype={"ts_code": str})
    for col in ("ts_code", "tradeStatus", "name", "market", "status"):
        if col not in df.columns:
            df[col] = ""
    df["ts_code"] = df["ts_code"].astype(str)
    return df


def _query_baostock_all(date_str: str | None = None) -> pd.DataFrame:
    import baostock as bs

    query_date = date_str or date.today().strftime("%Y-%m-%d")
    rs = bs.query_all_stock(day=query_date)
    if rs.error_code != "0":
        raise RuntimeError(f"baostock query_all_stock failed: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        raise RuntimeError(f"baostock query_all_stock returned no rows for {query_date}")
    df = pd.DataFrame(rows, columns=rs.fields)
    name_col = "code_name" if "code_name" in df.columns else "name" if "name" in df.columns else ""
    result = pd.DataFrame()
    result["ts_code"] = df["code"].apply(_normalize_ts_code)
    result["tradeStatus"] = pd.to_numeric(df.get("tradeStatus", 1), errors="coerce").fillna(1).astype(int)
    result["name"] = df[name_col].astype(str) if name_col else ""
    result["market"] = [
        _classify_market(ts_code, name)
        for ts_code, name in zip(result["ts_code"], result["name"], strict=False)
    ]
    result["status"] = result["tradeStatus"].apply(lambda x: STATUS_LISTED if int(x) == 1 else "停牌")
    return result[["ts_code", "tradeStatus", "name", "market", "status"]].drop_duplicates("ts_code", keep="last")


def _write_cache(df: pd.DataFrame) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = f"{CACHE_PATH}.tmp"
    df.to_csv(tmp_path, index=False)
    os.replace(tmp_path, CACHE_PATH)


def _upsert_stock_basic(df: pd.DataFrame) -> None:
    conn = get_conn()
    for _, row in df.iterrows():
        conn.execute(
            """INSERT INTO stock_basic (ts_code, name, market, industry, sector, is_main_board)
               VALUES (?, ?, ?, '', '', ?)
               ON CONFLICT(ts_code) DO UPDATE SET
               name=excluded.name, market=excluded.market, is_main_board=excluded.is_main_board""",
            (
                row["ts_code"],
                row["name"],
                row["market"],
                1 if row["market"] in ("主板", "中小板") and row["status"] == STATUS_LISTED else 0,
            ),
        )
    conn.execute(
        """INSERT INTO system_settings (key, value, updated_at)
           VALUES ('stock_universe_last_refresh', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()
    conn.close()


def refresh_stock_universe(date_str: str | None = None, dry_run: bool = False) -> dict:
    """Refresh data/stock_basic_cache.csv from baostock and mirror it to SQLite."""
    existing = _read_existing_cache()
    existing_codes = set(existing["ts_code"].astype(str))
    if not login_baostock():
        return {"status": "login_failed", "date": date_str or date.today().strftime("%Y-%m-%d")}
    try:
        fetched = _query_baostock_all(date_str)
    finally:
        logout_baostock()

    fetched_codes = set(fetched["ts_code"].astype(str))
    new_codes = sorted(fetched_codes - existing_codes)
    missing_codes = sorted(existing_codes - fetched_codes)

    old_rows = existing[~existing["ts_code"].isin(fetched_codes)].copy()
    if not old_rows.empty:
        old_rows["status"] = STATUS_MISSING
        old_rows["tradeStatus"] = 0
    merged = pd.concat([fetched, old_rows], ignore_index=True)
    merged = merged.drop_duplicates("ts_code", keep="first").sort_values("ts_code").reset_index(drop=True)

    market_counts = merged["market"].value_counts(dropna=False).to_dict()
    listed = merged[merged["status"] == STATUS_LISTED]
    listed_stock_count = int(listed[listed["market"].isin(["主板", "中小板", "创业板", "科创板", "北交所"])].shape[0])
    summary = {
        "status": "dry_run" if dry_run else "ok",
        "date": date_str or date.today().strftime("%Y-%m-%d"),
        "total": int(len(merged)),
        "listed_stock_count": listed_stock_count,
        "new_count": len(new_codes),
        "missing_count": len(missing_codes),
        "new_codes": new_codes[:50],
        "new_codes_all": new_codes,
        "missing_codes": missing_codes[:50],
        "market_counts": market_counts,
        "cache_path": CACHE_PATH,
    }
    if dry_run:
        return summary

    _write_cache(merged)
    _upsert_stock_basic(merged)
    return summary
