#!/usr/bin/env python3
"""Build or apply a sanitized Xulu index history payload."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.db.schema import init_db
from backend.telegram.xulu_index import replace_xulu_index_history
from backend.telegram.xulu_index_importer import (
    RawCloseProvider,
    backup_database,
    build_index_history,
    parse_gf_events,
    parse_legacy_events,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--legacy")
    parser.add_argument("--gf")
    parser.add_argument("--rows-json")
    parser.add_argument("--output-json")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    init_db(args.db).close()
    if args.rows_json:
        payload = json.loads(Path(args.rows_json).read_text(encoding="utf-8"))
    else:
        if not args.legacy or not args.gf:
            raise SystemExit("--legacy and --gf are required when --rows-json is absent")
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        try:
            with RawCloseProvider() as provider:
                payload = build_index_history(
                    parse_legacy_events(args.legacy),
                    parse_gf_events(args.gf),
                    conn,
                    provider,
                )
        finally:
            conn.close()
    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    latest = payload["rows"][-1]
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "rows": len(payload["rows"]),
        "first_date": payload["rows"][0]["trade_date"],
        "last_date": latest["trade_date"],
        "latest_index": latest["index_value"],
        "cumulative_return": latest["cumulative_return"],
        "checks": payload.get("checks") or [],
    }, ensure_ascii=False, indent=2))
    if not args.apply:
        return

    backup = backup_database(args.db)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        count = replace_xulu_index_history(conn, payload["rows"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(json.dumps({"applied": count, "backup": backup}, ensure_ascii=False))


if __name__ == "__main__":
    main()
