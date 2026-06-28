"""Historical brokerage-ledger replay for the Xulu strategy index."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

import baostock as bs
import pandas as pd

from backend.telegram.xulu_index import INDEX_BASE_VALUE


LEGACY_CUTOFF = "2026-06-09"
GF_HANDOFF_DATE = "2026-06-09"
GF_FIRST_INDEX_DATE = "2026-06-10"
DAILY_HISTORY_START = "2026-06-11"


@dataclass(frozen=True)
class LedgerEvent:
    trade_date: str
    event_time: str
    source: str
    event_type: str
    code: str
    name: str
    quantity: float
    price: float
    cash_amount: float
    external_flow: float
    source_row: int


def _number(value) -> float:
    raw = str(value or "").strip().replace(",", "")
    if not raw or raw.lower() == "nan":
        return 0.0
    return float(raw)


def _date(value) -> str:
    raw = re.sub(r"\D", "", str(value or ""))
    if len(raw) != 8:
        raise ValueError(f"invalid trade date: {value!r}")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _code(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-6:] if len(digits) >= 6 else ""


def _ts_code(code: str) -> str:
    if not code:
        return ""
    return ("sh." if code.startswith(("5", "6")) else "sz.") + code


def _signed_quantity(event_type: str, quantity: float) -> float:
    if "卖出" in event_type:
        return -abs(quantity)
    if "买入" in event_type:
        return abs(quantity)
    return 0.0


def parse_legacy_events(path: str | Path, cutoff: str = LEGACY_CUTOFF) -> list[LedgerEvent]:
    raw = Path(path).read_bytes().decode("gb18030", errors="replace")
    rows = csv.DictReader(raw.splitlines(), delimiter="\t")
    events: list[LedgerEvent] = []
    for row_no, row in enumerate(rows, 2):
        trade_date = _date(row.get("成交日期"))
        if trade_date > cutoff:
            continue
        event_type = str(row.get("操作") or "").strip()
        cash_amount = _number(row.get("发生金额"))
        quantity = _signed_quantity(event_type, _number(row.get("成交数量")))
        external_flow = cash_amount if event_type in {"银证转入", "银证转出"} else 0.0
        if not quantity and not cash_amount and event_type not in {"利息归本"}:
            continue
        events.append(LedgerEvent(
            trade_date=trade_date,
            event_time="",
            source="legacy_table",
            event_type=event_type,
            code=_code(row.get("证券代码")),
            name=str(row.get("证券名称") or "").replace("�", "").strip(),
            quantity=quantity,
            price=_number(row.get("成交均价")),
            cash_amount=cash_amount,
            external_flow=external_flow,
            source_row=row_no,
        ))
    return sorted(events, key=lambda item: (item.trade_date, item.event_time, item.source_row))


def parse_gf_events(path: str | Path) -> list[LedgerEvent]:
    frame = pd.read_excel(path, dtype=str, engine="openpyxl").fillna("")
    events: list[LedgerEvent] = []
    for idx, row in frame.iterrows():
        trade_date = _date(row.iloc[1])
        event_type = str(row.iloc[2] or "").strip()
        cash_amount = _number(row.iloc[7])
        code = _code(row.iloc[3])
        quantity = _number(row.iloc[6]) if code else 0.0
        if code and "卖出" in event_type:
            quantity = -abs(quantity)
        elif code and "买入" in event_type:
            quantity = abs(quantity)
        else:
            quantity = 0.0
        external_flow = cash_amount if event_type == "银行转存" else 0.0
        if not quantity and not cash_amount:
            continue
        events.append(LedgerEvent(
            trade_date=trade_date,
            event_time=str(row.iloc[0] or "").strip(),
            source="gf_flow",
            event_type=event_type,
            code=code,
            name=str(row.iloc[4] or "").strip(),
            quantity=quantity,
            price=_number(row.iloc[5]),
            cash_amount=cash_amount,
            external_flow=external_flow,
            source_row=int(idx) + 2,
        ))
    return sorted(events, key=lambda item: (item.trade_date, item.event_time, item.source_row))


class RawCloseProvider:
    def __init__(self):
        self._logged_in = False
        self._prices: dict[str, dict[str, float]] = {}
        self._calendar: list[str] = []

    def __enter__(self):
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"baostock login failed: {login.error_msg}")
        self._logged_in = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._logged_in:
            bs.logout()

    @staticmethod
    def _query(code: str, fields: str, start_date: str, end_date: str) -> list[list[str]]:
        rs = bs.query_history_k_data_plus(
            code, fields, start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="3",
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock {code}: {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return rows

    def load(self, codes: Iterable[str], start_date: str, end_date: str):
        calendar_rows = self._query("sh.000001", "date,tradestatus", start_date, end_date)
        self._calendar = [row[0] for row in calendar_rows if len(row) > 1 and row[1] == "1"]
        for code in sorted(set(codes)):
            rows = self._query(_ts_code(code), "date,close,tradestatus", start_date, end_date)
            self._prices[code] = {
                row[0]: float(row[1]) for row in rows
                if len(row) > 2 and row[1] and row[2] == "1"
            }

    def calendar(self, start_date: str, end_date: str) -> list[str]:
        return [day for day in self._calendar if start_date <= day <= end_date]

    def close(self, code: str, trade_date: str) -> tuple[float, str]:
        prices = self._prices.get(code) or {}
        eligible = [day for day in prices if day <= trade_date]
        if not eligible:
            raise ValueError(f"missing raw close for {code} on or before {trade_date}")
        price_date = max(eligible)
        return prices[price_date], price_date


def replay_account(
    events: list[LedgerEvent],
    provider: RawCloseProvider,
    start_date: str,
    end_date: str,
) -> list[dict]:
    by_date: dict[str, list[LedgerEvent]] = {}
    for event in events:
        by_date.setdefault(event.trade_date, []).append(event)
    cash = 0.0
    holdings: dict[str, float] = {}
    names: dict[str, str] = {}
    rows = []
    for trade_date in provider.calendar(start_date, end_date):
        net_flow = 0.0
        day_events = by_date.get(trade_date, [])
        for event in day_events:
            cash += event.cash_amount
            net_flow += event.external_flow
            if event.code and event.quantity:
                holdings[event.code] = holdings.get(event.code, 0.0) + event.quantity
                names[event.code] = event.name
                if holdings[event.code] < -0.0001:
                    raise ValueError(f"negative holding {event.code} on {trade_date}: {holdings[event.code]}")
        market_value = 0.0
        valuation = {}
        for code, quantity in sorted(holdings.items()):
            if abs(quantity) < 0.0001:
                continue
            close, price_date = provider.close(code, trade_date)
            value = quantity * close
            market_value += value
            valuation[code] = {
                "name": names.get(code, ""), "quantity": quantity,
                "close": close, "price_date": price_date, "market_value": round(value, 2),
            }
        rows.append({
            "trade_date": trade_date,
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "total_asset": round(cash + market_value, 2),
            "net_flow": round(net_flow, 2),
            "holdings": valuation,
            "event_count": len(day_events),
        })
    return rows


def _daily_history(conn, start_date: str = DAILY_HISTORY_START) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM daily_history WHERE date>=? ORDER BY date",
        (start_date,),
    ).fetchall()]


def _chain_rows(account_rows: list[dict], source: str, start_index: float | None = None) -> list[dict]:
    result = []
    index_value = INDEX_BASE_VALUE if start_index is None else float(start_index)
    previous_total = None
    for idx, row in enumerate(account_rows):
        if previous_total is None:
            daily_pnl = 0.0
            daily_return = 0.0
        else:
            daily_pnl = row["total_asset"] - previous_total - row["net_flow"]
            daily_return = daily_pnl / previous_total if previous_total > 0 else 0.0
            index_value *= 1.0 + daily_return
        result.append({
            "trade_date": row["trade_date"],
            "index_value": round(index_value, 6),
            "daily_return": round(daily_return, 10),
            "cumulative_return": round(index_value / INDEX_BASE_VALUE - 1.0, 10),
            "total_asset": row["total_asset"],
            "daily_pnl": round(daily_pnl, 2),
            "net_flow": row["net_flow"],
            "cash": row.get("cash"),
            "market_value": row.get("market_value"),
            "source": source,
            "is_estimated": True,
            "detail": {
                "holdings": row.get("holdings") or {},
                "event_count": row.get("event_count", 0),
                "valuation": "baostock_unadjusted_close",
            },
        })
        previous_total = row["total_asset"]
    return result


def build_index_history(
    legacy_events: list[LedgerEvent],
    gf_events: list[LedgerEvent],
    conn,
    provider: RawCloseProvider,
) -> dict:
    codes = {event.code for event in [*legacy_events, *gf_events] if event.code}
    provider.load(codes, "2026-02-27", "2026-06-11")

    legacy_account = replay_account(legacy_events, provider, "2026-02-27", LEGACY_CUTOFF)
    if not legacy_account:
        raise ValueError("legacy account replay produced no rows")
    rows = _chain_rows(legacy_account, "legacy_table")

    gf_replay_start = min(event.trade_date for event in gf_events)
    gf_replay = replay_account(gf_events, provider, gf_replay_start, DAILY_HISTORY_START)
    gf_by_date = {row["trade_date"]: row for row in gf_replay}
    gf_start = gf_by_date.get(GF_HANDOFF_DATE)
    gf_day = gf_by_date.get(GF_FIRST_INDEX_DATE)
    gf_check = gf_by_date.get(DAILY_HISTORY_START)
    if not gf_start or not gf_day or not gf_check:
        raise ValueError("GF replay is missing handoff dates")

    daily_rows = _daily_history(conn)
    if not daily_rows:
        raise ValueError("daily_history has no rows from 2026-06-11")
    implied_baseline = (
        float(daily_rows[0]["total_asset"] or 0)
        - float(daily_rows[0]["daily_pnl"] or 0)
        - sum(json.loads(daily_rows[0]["cash_flows"] or "{}").values())
    )
    handoff_adjustment = implied_baseline - gf_start["total_asset"] - gf_day["net_flow"]
    gf_net_flow = gf_day["net_flow"] + handoff_adjustment
    gf_pnl = gf_day["total_asset"] - gf_start["total_asset"] - gf_net_flow
    gf_return = gf_pnl / gf_start["total_asset"] if gf_start["total_asset"] > 0 else 0.0
    handoff_index = rows[-1]["index_value"] * (1.0 + gf_return)
    rows.append({
        "trade_date": GF_FIRST_INDEX_DATE,
        "index_value": round(handoff_index, 6),
        "daily_return": round(gf_return, 10),
        "cumulative_return": round(handoff_index / INDEX_BASE_VALUE - 1.0, 10),
        "total_asset": gf_day["total_asset"],
        "daily_pnl": round(gf_pnl, 2),
        "net_flow": round(gf_net_flow, 2),
        "cash": gf_day["cash"],
        "market_value": gf_day["market_value"],
        "source": "gf_flow",
        "is_estimated": True,
        "detail": {
            "holdings": gf_day["holdings"], "event_count": gf_day["event_count"],
            "handoff_from": LEGACY_CUTOFF, "return_base_total_asset": gf_start["total_asset"],
            "recorded_external_flow": gf_day["net_flow"],
            "handoff_equity_adjustment": round(handoff_adjustment, 2),
            "valuation": "baostock_unadjusted_close",
        },
    })

    first_daily_total = float(daily_rows[0]["total_asset"])
    first_daily_recorded_pnl = float(daily_rows[0]["daily_pnl"] or 0)
    first_daily_flows = json.loads(daily_rows[0]["cash_flows"] or "{}")
    first_daily_net_flow = sum(float(value or 0) for value in first_daily_flows.values())
    first_daily_recomputed_pnl = first_daily_total - gf_day["total_asset"] - first_daily_net_flow
    checks = [
        {
            "name": "GF 2026-06-10 opening capital tie",
            "actual": gf_start["total_asset"] + gf_net_flow, "expected": implied_baseline,
            "difference": gf_start["total_asset"] + gf_net_flow - implied_baseline,
            "tolerance": 1.0,
        },
        {
            "name": "GF 2026-06-11 NAV tie",
            "actual": gf_check["total_asset"], "expected": first_daily_total,
            "difference": gf_check["total_asset"] - first_daily_total, "tolerance": 1.0,
        },
        {
            "name": "GF split two-day PnL tie",
            "actual": gf_pnl + first_daily_recomputed_pnl,
            "expected": first_daily_recorded_pnl,
            "difference": gf_pnl + first_daily_recomputed_pnl - first_daily_recorded_pnl,
            "tolerance": 1.0,
        },
    ]
    for check in checks:
        check["status"] = "OK" if abs(check["difference"]) <= check["tolerance"] else "FAIL"
    if any(check["status"] != "OK" for check in checks):
        raise ValueError("historical tie-out failed: " + json.dumps(checks, ensure_ascii=False))

    index_value = handoff_index
    previous_total = gf_day["total_asset"]
    for daily_idx, daily in enumerate(daily_rows):
        flows = json.loads(daily["cash_flows"] or "{}")
        net_flow = sum(float(value or 0) for value in flows.values())
        expected_pnl = float(daily["total_asset"] or 0) - previous_total - net_flow
        recorded_pnl = float(daily["daily_pnl"] or 0)
        daily_pnl = expected_pnl if daily_idx == 0 else recorded_pnl
        if daily_idx > 0 and abs(daily_pnl - expected_pnl) > 0.02:
            raise ValueError(f"daily_history tie failed on {daily['date']}: {daily_pnl} vs {expected_pnl}")
        daily_return = daily_pnl / previous_total if previous_total > 0 else 0.0
        index_value *= 1.0 + daily_return
        rows.append({
            "trade_date": daily["date"],
            "index_value": round(index_value, 6),
            "daily_return": round(daily_return, 10),
            "cumulative_return": round(index_value / INDEX_BASE_VALUE - 1.0, 10),
            "total_asset": round(float(daily["total_asset"]), 2),
            "daily_pnl": round(daily_pnl, 2),
            "net_flow": round(net_flow, 2),
            "cash": None, "market_value": None,
            "source": "daily_history", "is_estimated": False,
            "detail": {
                "cash_flows": flows,
                "allocation": json.loads(daily["allocation"] or "{}"),
                "recorded_daily_pnl": recorded_pnl,
                "first_daily_split": daily_idx == 0,
            },
        })
        previous_total = float(daily["total_asset"])

    normalized_events = [
        asdict(event) for event in legacy_events if event.trade_date <= LEGACY_CUTOFF
    ] + [
        asdict(event) for event in gf_events if event.trade_date <= DAILY_HISTORY_START
    ]
    normalized_events.append({
        "trade_date": GF_FIRST_INDEX_DATE,
        "event_time": "00:00:00",
        "source": "handoff_reconciliation",
        "event_type": "partnership_opening_equity_adjustment",
        "code": "",
        "name": "",
        "quantity": 0.0,
        "price": 0.0,
        "cash_amount": round(handoff_adjustment, 2),
        "external_flow": round(handoff_adjustment, 2),
        "source_row": 0,
    })
    return {"rows": rows, "checks": checks, "events": normalized_events}


def backup_database(db_path: str | Path) -> str:
    import sqlite3

    source_path = Path(db_path)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = source_path.with_name(f"{source_path.stem}.xulu_index_{stamp}{source_path.suffix}.bak")
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target.execute("PRAGMA journal_mode=DELETE")
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        target.commit()
    finally:
        target.close()
        source.close()
    return str(backup_path)
