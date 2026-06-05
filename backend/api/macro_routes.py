"""Macro daily report API."""

from fastapi import APIRouter, Query

from backend.macro.report import (
    collect_stock_fundamental_events,
    format_chip_distribution,
    format_macro_topic,
    generate_macro_report,
    get_effective_macro_report_time,
    get_macro_report_row,
    refresh_macro_intelligence,
    resolve_trade_date,
)

router = APIRouter(prefix="/api/macro", tags=["macro"])


def _row_payload(row: dict | None) -> dict:
    if not row:
        return {"exists": False}
    return {
        "exists": True,
        "trade_date": row.get("trade_date"),
        "status": row.get("status"),
        "market_regime": row.get("market_regime"),
        "risk_on_score": row.get("risk_on_score"),
        "summary": row.get("summary"),
        "report_md": row.get("report_md"),
        "report_path": row.get("report_path"),
        "structured_json": row.get("structured_json"),
        "data_status_json": row.get("data_status_json"),
        "latency_ms": row.get("latency_ms"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/report")
async def get_macro_report(trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新")):
    return _row_payload(get_macro_report_row(trade_date))


@router.post("/report/generate")
async def generate_report(
    trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新"),
    force: bool = Query(default=True),
):
    result = generate_macro_report(trade_date, force=force)
    payload = dict(result)
    payload["report"] = _row_payload(result.get("report"))
    return payload


@router.post("/refresh")
async def refresh_report(
    trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新"),
    refresh_policy: bool = Query(default=True, description="是否先刷新政策缓存"),
    force: bool = Query(default=True),
):
    result = refresh_macro_intelligence(trade_date, refresh_policy=refresh_policy, force=force)
    report = (result.get("report") or {}).get("report")
    if report:
        result["report"]["report"] = _row_payload(report)
    return result


@router.get("/topic")
async def macro_topic(
    topic: str = Query(default="report", description="report/sector/lhb/limit_up/limit_down/broken_limit/strong"),
    trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新"),
):
    return {"topic": topic, "trade_date": resolve_trade_date(trade_date), "message": format_macro_topic(topic, trade_date)}


@router.get("/chip/{ts_code}")
async def macro_chip(ts_code: str):
    return {"message": format_chip_distribution(ts_code)}


@router.get("/fundamental/{ts_code}")
async def macro_fundamental(
    ts_code: str,
    trade_date: str = Query(default="", description="交易日 YYYYMMDD，空为最新"),
    days: int = Query(default=365),
):
    data, status = collect_stock_fundamental_events(ts_code, trade_date, days)
    return {"data": data, "status": status}


@router.get("/status")
async def macro_status():
    trade_date = resolve_trade_date("")
    row = get_macro_report_row(trade_date)
    return {
        "trade_date": trade_date,
        "report_time": get_effective_macro_report_time(),
        "report": _row_payload(row),
    }
