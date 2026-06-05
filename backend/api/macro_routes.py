"""Macro daily report API."""

from fastapi import APIRouter, Query

from backend.macro.report import (
    generate_macro_report,
    get_effective_macro_report_time,
    get_macro_report_row,
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


@router.get("/status")
async def macro_status():
    trade_date = resolve_trade_date("")
    row = get_macro_report_row(trade_date)
    return {
        "trade_date": trade_date,
        "report_time": get_effective_macro_report_time(),
        "report": _row_payload(row),
    }
