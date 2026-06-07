"""回测引擎 v2

基于历史日线K线+均线模拟交易，考虑T+1/费率/涨跌停约束。
v2：预加载数据 + 结构化日志 + 止损机制
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from backend.data.loader import (
    load_daily, load_index_daily, compute_mas, compute_limit_status,
    list_main_board_stocks,
)
from backend.strategies.registry import StrategyRegistry
from backend.trading.rules import (
    is_one_side_limit, calc_buy_fee, calc_sell_fee, is_st_value,
)
from backend.trading.calculator import calc_cumulative_return
from backend.backtest.metrics import compute_metrics
from backend.config import INITIAL_CAPITAL as DEFAULT_CAPITAL, LOGS_DIR


# ---------------------------------------------------------------------------
# 日志系统
# ---------------------------------------------------------------------------

@dataclass
class LogEntry:
    ts: str
    level: str       # SIGNAL | BUY | SELL | SKIP | STOP | INFO | SUMMARY | ERROR
    date: str
    message: str
    details: dict = field(default_factory=dict)


class BacktestLogger:
    """回测日志收集器，同时打印到控制台"""

    def __init__(self):
        self.entries: list[LogEntry] = []
        self._t0 = time.time()

    def _add(self, level: str, date: str, message: str, **details):
        entry = LogEntry(
            ts=datetime.now().strftime("%H:%M:%S.%f")[:-3],
            level=level, date=date, message=message, details=details,
        )
        self.entries.append(entry)
        # 同步打印到控制台
        tag = f"[{level}]".ljust(8)
        print(f"{entry.ts} {tag} {date} {message}")

    def info(self, date: str, message: str, **details):
        self._add("INFO", date, message, **details)

    def signal(self, date: str, count: int, top5: list[dict]):
        self._add("SIGNAL", date, f"共 {count} 个信号",
                  count=count, top5=top5)

    def buy(self, date: str, ts_code: str, name: str, price: float,
            quantity: int, value: float, fee: dict, score: float, reason: str):
        self._add("BUY", date, f"买入 {ts_code} {name} {quantity}股 @{price:.2f} 金额{value:,.0f}",
                  ts_code=ts_code, name=name, price=round(price, 2),
                  quantity=quantity, value=round(value, 2),
                  commission=fee["commission"], score=score, reason=reason)

    def sell(self, date: str, ts_code: str, name: str, price: float,
             quantity: int, value: float, fee: dict, pnl: float, reason: str):
        self._add("SELL", date, f"卖出 {ts_code} {name} {quantity}股 @{price:.2f} 盈亏{pnl:+,.0f}",
                  ts_code=ts_code, name=name, price=round(price, 2),
                  quantity=quantity, value=round(value, 2),
                  commission=fee["commission"], stamp_tax=fee["stamp_tax"],
                  pnl=round(pnl, 2), reason=reason)

    def stop_loss(self, date: str, ts_code: str, name: str, price: float,
                  quantity: int, value: float, fee: dict, pnl: float,
                  loss_pct: float):
        self._add("STOP", date, f"止损 {ts_code} {name} @{price:.2f} 亏损{loss_pct:.1f}% 盈亏{pnl:+,.0f}",
                  ts_code=ts_code, name=name, price=round(price, 2),
                  quantity=quantity, value=round(value, 2),
                  commission=fee["commission"], stamp_tax=fee["stamp_tax"],
                  pnl=round(pnl, 2), loss_pct=round(loss_pct, 2))

    def skip(self, date: str, ts_code: str, reason: str, **details):
        self._add("SKIP", date, f"跳过 {ts_code}: {reason}",
                  ts_code=ts_code, reason=reason, **details)

    def summary(self, date: str, total_assets: float, cash: float,
                market_value: float, return_pct: float, holding: Optional[str]):
        self._add("SUMMARY", date,
                  f"总资产 {total_assets:,.0f} 现金{cash:,.0f} 市值{market_value:,.0f} 收益{return_pct:+.2f}%",
                  total_assets=round(total_assets, 2), cash=round(cash, 2),
                  market_value=round(market_value, 2),
                  return_pct=round(return_pct, 4), holding=holding)

    def error(self, date: str, message: str, **details):
        self._add("ERROR", date, message, **details)

    def to_list(self) -> list[dict]:
        return [asdict(e) for e in self.entries]

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_list(), f, ensure_ascii=False, indent=2)

    @property
    def elapsed(self) -> float:
        return time.time() - self._t0


# ---------------------------------------------------------------------------
# 数据预加载
# ---------------------------------------------------------------------------

def _preload_data(start_date: str, end_date: str,
                  logger: BacktestLogger) -> dict[str, dict]:
    """预加载所有主板股票的 K 线数据并预计算均线

    一次性加载全部 CSV，避免逐日重复加载。
    返回 {ts_code: {"name": str, "df": DataFrame(全部历史+均线), "df_bt": DataFrame(仅回测区间)}}
    """
    # 往前扩展 120 天以确保 MA60 有足够历史数据
    extended_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")

    main_board = list_main_board_stocks()
    total = len(main_board)
    stock_data: dict[str, dict] = {}
    loaded = 0

    logger.info("", f"预加载 {total} 只股票数据 (扩展起始 {extended_start})...")

    for _, row in main_board.iterrows():
        ts_code = row["ts_code"]
        name = row["name"]
        df = load_daily(ts_code)
        if df is None or len(df) < 60:
            continue

        # 截取扩展起始之后的全部数据
        df = df[df["trade_date"] >= extended_start]
        if len(df) < 60:
            continue

        df = compute_mas(df)
        df = compute_limit_status(df)

        # 回测区间内的交易日
        df_bt = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]
        if len(df_bt) == 0:
            continue

        stock_data[ts_code] = {"name": name, "df": df, "df_bt": df_bt}
        loaded += 1

    logger.info("", f"预加载完成: {loaded}/{total} 只股票可用")
    return stock_data


# ---------------------------------------------------------------------------
# 信号生成（使用预加载缓存）
# ---------------------------------------------------------------------------

def _get_signals_cached(strategy, stock_data: dict, trade_date: str) -> list[dict]:
    """使用预加载数据生成当日信号"""
    signals = []

    for ts_code, data in stock_data.items():
        df_all = data["df"]
        # 只用 trade_date 及之前的数据
        df_upto = df_all[df_all["trade_date"] <= trade_date]
        if len(df_upto) < 30:
            continue

        result = strategy.filter(ts_code, data["name"], df_upto)
        if result:
            signals.append({
                "ts_code": result.ts_code,
                "name": result.name,
                "score": result.score,
                "reason": result.reason,
                "extra": result.extra,
            })

    signals.sort(key=lambda s: s["score"], reverse=True)
    return signals


# ---------------------------------------------------------------------------
# 交易执行
# ---------------------------------------------------------------------------

def _get_day_row(stock_data: dict, ts_code: str, trade_date: str):
    """获取某只股票在某交易日的行情数据"""
    data = stock_data.get(ts_code)
    if not data:
        return None
    day = data["df"][data["df"]["trade_date"] == trade_date]
    if day.empty:
        return None
    return day.iloc[0]


def _process_sell_with_log(position: dict, cash: float, stock_data: dict,
                            trade_date: str, signals: list[dict],
                            trades: list[dict], logger: BacktestLogger,
                            stop_loss_pct: float = -8.0) -> float:
    """处理卖出：信号卖出 + 止损，返回更新后的现金"""
    ts_code = position["ts_code"]
    name = stock_data.get(ts_code, {}).get("name", ts_code)
    row = _get_day_row(stock_data, ts_code, trade_date)
    if row is None:
        return cash

    open_p, high_p, low_p, close_p = row["open"], row["high"], row["low"], row["close"]
    pct = row.get("pct_chg", 0)
    qty = position["quantity"]
    avg_cost = position["avg_cost"]

    should_sell = False
    sell_reason = ""

    # 检查止损
    current_loss_pct = (close_p - avg_cost) / avg_cost * 100
    if current_loss_pct <= stop_loss_pct:
        should_sell = True
        sell_reason = f"止损触发 (亏损{current_loss_pct:.1f}%)"
    # 检查信号卖出
    elif ts_code not in [s["ts_code"] for s in signals[:5]]:
        should_sell = True
        sell_reason = "不再位于信号前5"

    if not should_sell:
        return cash

    # 一字板跌停无法卖出
    if is_one_side_limit(open_p, high_p, low_p, close_p, pct, is_st_value(row.get("is_st", 0))):
        logger.skip(trade_date, ts_code, f"一字板无法卖出 ({sell_reason})", pct=pct)
        return cash

    sell_price = close_p
    value = qty * sell_price
    fee = calc_sell_fee(value)
    pnl = (sell_price - avg_cost) * qty - fee["total_cost"]

    trades.append({
        "date": trade_date, "ts_code": ts_code, "name": name, "direction": "sell",
        "quantity": qty, "price": round(sell_price, 2),
        "total_value": round(value, 2),
        "commission": fee["commission"], "stamp_tax": fee["stamp_tax"],
        "pnl": round(pnl, 2), "reason": sell_reason,
    })

    if current_loss_pct <= stop_loss_pct:
        logger.stop_loss(trade_date, ts_code, name, sell_price, qty, value, fee, pnl, current_loss_pct)
    else:
        logger.sell(trade_date, ts_code, name, sell_price, qty, value, fee, pnl, sell_reason)

    position["quantity"] = 0
    cash += value - fee["total_cost"]
    return cash


def _process_buy_with_log(cash: float, stock_data: dict, trade_date: str,
                           signals: list[dict], trades: list[dict],
                           logger: BacktestLogger) -> tuple[float, dict]:
    """处理买入"""
    position = {"ts_code": "", "quantity": 0, "avg_cost": 0.0}

    for signal in signals[:3]:
        ts_code = signal["ts_code"]
        name = signal.get("name", ts_code)
        row = _get_day_row(stock_data, ts_code, trade_date)
        if row is None:
            continue

        open_p, high_p, low_p, close_p = row["open"], row["high"], row["low"], row["close"]
        pct = row.get("pct_chg", 0)

        if is_one_side_limit(open_p, high_p, low_p, close_p, pct, is_st_value(row.get("is_st", 0))):
            logger.skip(trade_date, ts_code, "一字板涨停，无法买入", pct=pct)
            continue

        buy_price = close_p
        max_shares = int(cash * 0.95 / buy_price / 100) * 100
        if max_shares < 100:
            logger.skip(trade_date, ts_code,
                        f"资金不足: 现金{cash:,.0f} 需>{buy_price*100:,.0f}",
                        cash=cash, need=buy_price * 100)
            continue

        quantity = max_shares
        value = quantity * buy_price
        fee = calc_buy_fee(value)
        total_cost = value + fee["total_cost"]

        if total_cost > cash:
            quantity = int((cash - fee["total_cost"]) / buy_price / 100) * 100
            if quantity < 100:
                continue
            value = quantity * buy_price
            fee = calc_buy_fee(value)
            total_cost = value + fee["total_cost"]

        trades.append({
            "date": trade_date, "ts_code": ts_code, "name": name, "direction": "buy",
            "quantity": quantity, "price": round(buy_price, 2),
            "total_value": round(value, 2),
            "commission": fee["commission"], "stamp_tax": 0, "pnl": 0,
            "score": signal["score"], "reason": signal.get("reason", ""),
        })

        logger.buy(trade_date, ts_code, name, buy_price, quantity, value, fee,
                   score=signal["score"], reason=signal.get("reason", ""))

        cash -= total_cost
        position = {"ts_code": ts_code, "quantity": quantity, "avg_cost": buy_price}
        break

    return cash, position


# ---------------------------------------------------------------------------
# 交易日列表
# ---------------------------------------------------------------------------

def _get_trading_dates_from_df(index_df, start_date: str, end_date: str) -> list[str]:
    if index_df is None or index_df.empty:
        return []
    mask = (index_df["trade_date"] >= start_date) & (index_df["trade_date"] <= end_date)
    return index_df[mask]["trade_date"].tolist()


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_backtest(strategy_name: str, params: dict,
                 start_date: str, end_date: str,
                 initial_capital: float = DEFAULT_CAPITAL,
                 stop_loss_pct: float = -8.0) -> dict:
    """运行策略回测

    Args:
        strategy_name: 策略名称
        params: 策略参数
        start_date / end_date: 回测区间 (YYYYMMDD)
        initial_capital: 初始资金
        stop_loss_pct: 止损线（负数百分比，默认 -8%）

    Returns:
        {metrics, equity_curve, trades, log, log_file}
    """
    logger = BacktestLogger()
    logger.info("", f"══════ 回测开始 ══════")
    logger.info("", f"策略: {strategy_name}  参数: {params}")
    logger.info("", f"区间: {start_date} - {end_date}")
    logger.info("", f"初始资金: {initial_capital:,.0f}  止损线: {stop_loss_pct}%")

    strategy = StrategyRegistry.create(strategy_name, **params)
    if strategy is None:
        return {"error": f"未知策略: {strategy_name}"}

    # 1. 预加载数据
    stock_data = _preload_data(start_date, end_date, logger)

    # 2. 加载指数
    index_df = load_index_daily()
    trading_dates = _get_trading_dates_from_df(index_df, start_date, end_date)
    if not trading_dates:
        return {"error": "无可用交易日期"}

    logger.info("", f"交易日: {trading_dates[0]} → {trading_dates[-1]}  ({len(trading_dates)}天)")

    # 3. 逐日模拟
    cash = initial_capital
    position: Optional[dict] = None
    equity_curve: list[dict] = []
    trades: list[dict] = []

    for day_idx, trade_date in enumerate(trading_dates):
        # 获取信号
        signals = _get_signals_cached(strategy, stock_data, trade_date)
        top5 = [{"ts_code": s["ts_code"], "name": s["name"], "score": round(s["score"], 1)}
                for s in signals[:5]]
        logger.signal(trade_date, len(signals), top5)

        # 卖出
        if position and position.get("quantity", 0) > 0:
            cash = _process_sell_with_log(position, cash, stock_data, trade_date,
                                          signals, trades, logger, stop_loss_pct)

        # 买入
        if not position or position.get("quantity", 0) == 0:
            cash, new_pos = _process_buy_with_log(cash, stock_data, trade_date,
                                                   signals, trades, logger)
            if new_pos["quantity"] > 0:
                position = new_pos

        # 计算当日市值和净值
        market_value = 0.0
        holding_code = None
        if position and position.get("quantity", 0) > 0:
            holding_code = position["ts_code"]
            row = _get_day_row(stock_data, holding_code, trade_date)
            if row is not None:
                market_value = position["quantity"] * row["close"]

        total_assets = cash + market_value

        index_close = 0.0
        if index_df is not None:
            idx_day = index_df[index_df["trade_date"] == trade_date]
            if not idx_day.empty:
                index_close = float(idx_day.iloc[0]["close"])

        return_pct = calc_cumulative_return(total_assets, initial_capital)

        equity_curve.append({
            "date": trade_date,
            "total_assets": round(total_assets, 2),
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "return_pct": round(return_pct, 4),
            "index_close": round(index_close, 2),
        })

        logger.summary(trade_date, total_assets, cash, market_value,
                       return_pct, holding_code)

    # 4. 计算指标
    metrics = compute_metrics(equity_curve, trades, initial_capital)

    # 5. 保存日志到文件
    log_dir = os.path.join(LOGS_DIR, "backtest")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"{strategy_name}_{start_date}_{end_date}_{ts}.json"
    log_path = os.path.join(log_dir, log_filename)
    logger.save(log_path)

    logger.info("", f"══════ 回测完成 ══════")
    logger.info("", f"耗时: {logger.elapsed:.1f}s")
    logger.info("", f"最终资金: {metrics['final_assets']:,.0f}  收益: {metrics['total_return']}%")
    logger.info("", f"交易次数: {metrics['total_trades']}  胜率: {metrics['win_rate']}%")
    logger.info("", f"日志文件: {log_path}")

    return {
        "metrics": metrics,
        "equity_curve": equity_curve,
        "trades": trades,
        "log": logger.to_list(),
        "log_file": log_path,
    }
