"""交易硬约束规则（不可改动）

- T+1 制度
- 涨跌停约束
- 交易费率计算
- 标的限制（仅 60/00 主板）
"""

import re
from datetime import date, timedelta
from backend.config import (
    COMMISSION_RATE, STAMP_TAX_RATE,
    T1_ENABLED, VALID_STOCK_PREFIXES
)


MAIN_BOARD_CODE_RE = re.compile(r"^(60[0-9]{4}|601[0-9]{3}|603[0-9]{3}|605[0-9]{3})\.SH$|^(000[0-9]{3}|001[0-9]{3}|002[0-9]{3})\.SZ$")
MAIN_BOARD_MARKETS = {"主板", "中小板"}
INDEX_NAME_KEYWORDS = ("指数", "上证", "深证", "中证", "国证", "基金", "债", "转债")


def normalize_ts_code(ts_code: str) -> str:
    """Normalize bare A-share codes to baostock-style codes."""
    code = str(ts_code or "").strip().upper()
    if "." in code:
        return code
    if code.startswith("60"):
        return f"{code}.SH"
    if code.startswith(("000", "001", "002")):
        return f"{code}.SZ"
    return code


def is_index_like_name(name: str) -> bool:
    """Return True for index/fund/bond-like names that must not enter stock signals."""
    text = str(name or "")
    return any(k in text for k in INDEX_NAME_KEYWORDS)


def is_st_value(value) -> bool:
    """Return True when an official isST/is_st field marks the stock as ST."""
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "st"}


def is_main_board(
    ts_code: str,
    name: str = "",
    market: str = "",
    status: str = "",
    is_st=None,
    allow_st: bool = False,
) -> bool:
    """检查是否为可交易主板个股，排除指数/基金/债券等非股票代码。"""
    code = normalize_ts_code(ts_code)
    if not MAIN_BOARD_CODE_RE.match(code):
        return False
    if name and is_index_like_name(name):
        return False
    if not allow_st and is_st is not None and is_st_value(is_st):
        return False
    if not allow_st and name and is_st_stock(name):
        return False
    if market and str(market) not in MAIN_BOARD_MARKETS:
        return False
    if status and str(status) not in ("上市", "1"):
        return False
    return True


def is_st_stock(name: str) -> bool:
    """检查是否为 ST 股"""
    text = str(name or "").upper()
    return "ST" in text or "*ST" in text


def limit_threshold_pct(is_st: bool = False) -> float:
    return 5.0 if is_st else 10.0


def can_trade_today(buy_date: str, trade_date: str) -> bool:
    """T+1 检查：今日买入的股票明日才可卖出

    Args:
        buy_date: 买入日期 (YYYYMMDD)
        trade_date: 交易日期 (YYYYMMDD)

    Returns:
        True 如果可以卖出
    """
    if not T1_ENABLED:
        return True

    if buy_date >= trade_date:
        return False  # 当天买入不能当天卖

    return True


def is_limit_up(pct_chg: float, is_st: bool = False) -> bool:
    """是否涨停；ST 按 5%，普通主板按 10%。"""
    return float(pct_chg or 0) >= (4.9 if is_st else 9.8)


def is_limit_down(pct_chg: float, is_st: bool = False) -> bool:
    """是否跌停；ST 按 5%，普通主板按 10%。"""
    return float(pct_chg or 0) <= (-4.9 if is_st else -9.8)


def is_one_side_limit(open: float, high: float, low: float, close: float, pct_chg: float, is_st: bool = False) -> bool:
    """是否一字涨停/跌停（开盘价=最高价=最低价=收盘价，且涨跌停）"""
    if open == high == low == close:
        if is_limit_up(pct_chg, is_st) or is_limit_down(pct_chg, is_st):
            return True
    return False


def can_buy(ts_code: str, name: str, open: float, high: float, low: float,
            close: float, pct_chg: float) -> tuple[bool, str]:
    """判断是否可以买入"""
    if not is_main_board(ts_code):
        return False, "非主板股票，禁止交易"

    if is_st_stock(name):
        return False, "ST 股票，禁止交易"

    st_flag = is_st_stock(name)
    if is_one_side_limit(open, high, low, close, pct_chg, st_flag):
        direction = "涨停" if pct_chg > 0 else "跌停"
        return False, f"一字{direction}，当日禁止买入"

    return True, ""


def can_sell(ts_code: str, buy_date: str, trade_date: str, open: float,
             high: float, low: float, close: float, pct_chg: float) -> tuple[bool, str]:
    """判断是否可以卖出"""
    if not is_main_board(ts_code):
        return False, "非主板股票"

    if not can_trade_today(buy_date, trade_date):
        return False, f"T+1限制：{buy_date}买入，{trade_date}不可卖出"

    if is_one_side_limit(open, high, low, close, pct_chg, False):
        direction = "涨停" if pct_chg > 0 else "跌停"
        return False, f"一字{direction}，当日禁止卖出"

    return True, ""


def calc_buy_fee(total_value: float) -> dict:
    """计算买入费用

    Returns:
        {"commission": 佣金, "total_cost": 总成本}
    """
    commission = round(total_value * COMMISSION_RATE, 2)
    return {
        "commission": commission,
        "total_cost": commission,
    }


def calc_sell_fee(total_value: float) -> dict:
    """计算卖出费用

    Returns:
        {"commission": 佣金, "stamp_tax": 印花税, "total_cost": 总成本}
    """
    commission = round(total_value * COMMISSION_RATE, 2)
    stamp_tax = round(total_value * STAMP_TAX_RATE, 2)
    return {
        "commission": commission,
        "stamp_tax": stamp_tax,
        "total_cost": round(commission + stamp_tax, 2),
    }


def match_order_price(order_price: float, day_low: float, day_high: float) -> tuple[bool, float]:
    """撮合条件单价格

    条件单价格落在当日最低价~最高价区间内，按设定价格成交

    Returns:
        (是否成交, 成交价格)
    """
    if day_low <= order_price <= day_high:
        return True, order_price
    return False, 0.0


def get_available_shares(total_quantity: int, buy_date: str, trade_date: str) -> int:
    """获取 T+1 制度下可卖出的股份数

    今日买入的股份不可卖出
    """
    if not T1_ENABLED:
        return total_quantity
    if buy_date < trade_date:
        return total_quantity
    return 0
