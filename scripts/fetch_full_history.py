"""全量历史 K 线数据下载 (2019-01-01 至今)

耗时较长（~3400只股票 × ~1700交易日），建议在 screen/tmux 中运行。
每只股票直接全量拉取后覆盖写入，不做增量合并。
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from backend.data.fetcher import (
    login_baostock, logout_baostock,
    fetch_daily_full, _normalize_ts_code,
)
from backend.data.loader import list_main_board_stocks, compute_mas, compute_limit_status
from backend.config import DAILY_DIR, INDEX_DIR

START_DATE = "2019-01-01"
DELAY = 0.3  # 每50只后休息秒数


def save_full(ts_code: str, df: pd.DataFrame):
    """保存全量数据（覆盖写入）"""
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "")
    df = df.sort_values("trade_date").reset_index(drop=True)
    df = compute_mas(df)
    df = compute_limit_status(df)
    filepath = os.path.join(DAILY_DIR, f"{ts_code}_daily.csv")
    df.to_csv(filepath, index=False)
    return len(df)


def main():
    print("=" * 60)
    print("全量历史 K 线下载 (全量覆盖)")
    print(f"起始日期: {START_DATE}")
    print(f"时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    if not login_baostock():
        print("Baostock 登录失败!")
        return

    stocks = list_main_board_stocks()
    total = len(stocks)
    print(f"主板股票数量: {total}")

    success = 0
    failed = 0
    total_rows = 0

    for i, (_, row) in enumerate(stocks.iterrows()):
        ts_code = row["ts_code"]
        name = row["name"]
        try:
            df = fetch_daily_full(ts_code, start_date=START_DATE)
            if df is not None and len(df) > 0:
                n = save_full(ts_code, df)
                total_rows += n
                success += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  [{i+1}/{total}] {ts_code} {name} 失败: {e}")

        if (i + 1) % 200 == 0:
            print(f"  进度: {i+1}/{total} | 成功 {success} 失败 {failed} | 累计行 {total_rows}")

        if DELAY > 0 and (i + 1) % 50 == 0:
            time.sleep(DELAY)

    # 上证指数
    print("\n下载上证指数全量...")
    try:
        df = fetch_daily_full("sh.000001", start_date=START_DATE)
        if df is not None and len(df) > 0:
            ts_code = _normalize_ts_code("sh.000001")
            df = df.copy()
            df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "")
            df = df.sort_values("trade_date")
            filepath = os.path.join(INDEX_DIR, f"{ts_code}_daily.csv")
            df.to_csv(filepath, index=False)
            print(f"  上证指数: {len(df)} 行, {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    except Exception as e:
        print(f"  指数下载失败: {e}")

    logout_baostock()

    print("\n" + "=" * 60)
    print(f"完成! 成功 {success} 失败 {failed} | 累计行数 {total_rows}")
    print("=" * 60)


if __name__ == "__main__":
    main()
