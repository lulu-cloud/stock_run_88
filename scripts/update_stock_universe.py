#!/usr/bin/env python3
"""Refresh the A-share universe cache.

Usage:
  .venv/bin/python scripts/update_stock_universe.py --dry-run
  .venv/bin/python scripts/update_stock_universe.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.db.schema import init_db
from backend.data.fetcher import fetch_daily_full, login_baostock, logout_baostock, merge_and_save
from backend.data.stock_universe import refresh_stock_universe
from backend.db.repository import get_conn


LISTED_A_MARKETS = {"主板", "中小板", "创业板", "科创板", "北交所"}


def _fetch_new_daily(codes: list[str], start_date: str, limit: int, delay: float) -> dict:
    selected = []
    if codes:
        conn = get_conn()
        placeholders = ",".join("?" for _ in codes)
        rows = conn.execute(
            f"""SELECT ts_code, name, market FROM stock_basic
                WHERE ts_code IN ({placeholders}) AND market IN ({",".join("?" for _ in LISTED_A_MARKETS)})""",
            tuple(codes) + tuple(LISTED_A_MARKETS),
        ).fetchall()
        conn.close()
        selected = [dict(r) for r in rows]
    if limit > 0:
        selected = selected[:limit]
    result = {"requested": len(codes), "selected": len(selected), "fetched": {}, "failed": {}}
    if not selected:
        return result
    if not login_baostock():
        result["error"] = "baostock login failed"
        return result
    try:
        for idx, row in enumerate(selected, start=1):
            ts_code = row["ts_code"]
            try:
                df = fetch_daily_full(ts_code, start_date=start_date)
                if df is None or df.empty:
                    result["failed"][ts_code] = "no data"
                else:
                    saved = merge_and_save(ts_code, df)
                    result["fetched"][ts_code] = int(len(saved))
            except Exception as exc:
                result["failed"][ts_code] = str(exc)
            if delay > 0 and idx < len(selected):
                time.sleep(delay)
    finally:
        logout_baostock()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="", help="baostock query date, YYYY-MM-DD; default today")
    parser.add_argument("--dry-run", action="store_true", help="fetch and compare without writing files/db")
    parser.add_argument("--fetch-new-daily", action="store_true", help="after refreshing the universe, fetch full daily bars for newly listed A shares")
    parser.add_argument("--daily-start-date", default="2024-01-01", help="start date for --fetch-new-daily")
    parser.add_argument("--daily-limit", type=int, default=0, help="limit new stocks fetched; 0 means no limit")
    parser.add_argument("--daily-delay", type=float, default=0.2, help="seconds to sleep between daily fetches")
    args = parser.parse_args()
    init_db().close()
    result = refresh_stock_universe(args.date or None, dry_run=args.dry_run)
    if args.fetch_new_daily:
        if args.dry_run:
            result["new_daily"] = {"status": "skipped", "reason": "dry-run does not write stock_basic"}
        elif result.get("status") == "ok":
            result["new_daily"] = _fetch_new_daily(
                result.get("new_codes_all") or result.get("new_codes") or [],
                args.daily_start_date,
                args.daily_limit,
                args.daily_delay,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
