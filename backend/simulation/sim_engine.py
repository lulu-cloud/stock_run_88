"""模拟交易引擎

在历史数据上回放 LLM Agent 决策流程。
每个模拟交易日，agent 使用时间隔离工具分析市场、生成订单，
按当日收盘价撮合，记录完整决策日志。
"""

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from backend.data.loader import load_daily, load_index_daily, compute_mas, compute_limit_status
from backend.trading.rules import (
    is_one_side_limit, calc_buy_fee, calc_sell_fee, can_buy, can_sell,
)
from backend.trading.calculator import calc_cumulative_return
from backend.backtest.metrics import compute_metrics
from backend.agents.base import AgentContext
from backend.simulation.sim_agent_runner import run_sim_agent_review
from backend.simulation.sim_tools import create_sim_tools
from backend.config import LOGS_DIR, REPORTS_DIR


@dataclass
class SimAgentState:
    """单个模拟 Agent 的运行状态"""
    agent_id: int
    display_name: str
    strategy_name: str
    initial_capital: float
    reasoning_effort: str
    cash: float
    positions: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)


@dataclass
class SimulationResult:
    sim_id: int
    status: str
    agents: list[dict]     # per-agent: {name, equity_curve, trades, decisions, metrics}
    error: str = ""


def _get_day_price(stock_data: dict, ts_code: str, trade_date: str) -> Optional[dict]:
    """获取某只股票在交易日的 OHLCV"""
    data = stock_data.get(ts_code)
    if not data:
        return None
    row = data.get("by_date", {}).get(trade_date)
    if row is None:
        day = data["df"][data["df"]["trade_date"] == trade_date]
        if day.empty:
            return None
        row = day.iloc[0]
    if row is None:
        return None
    return {
        "open": row["open"], "high": row["high"],
        "low": row["low"], "close": row["close"],
        "pct_chg": row.get("pct_chg", 0), "vol": row.get("vol", 0),
    }


def _preload_stock_data(start_date: str, end_date: str) -> dict:
    """预加载回测区间内的股票数据"""
    from backend.data.loader import list_main_board_stocks
    from datetime import timedelta

    extended_start = (datetime.strptime(start_date, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
    main_board = list_main_board_stocks()
    stock_data = {}
    total = len(main_board)

    for _, row in main_board.iterrows():
        ts_code = row["ts_code"]
        name = row.get("name", ts_code)
        df = load_daily(ts_code)
        if df is None or len(df) < 60:
            continue
        df = df[df["trade_date"] >= extended_start]
        if len(df) < 60:
            continue
        df = compute_mas(df)
        df = compute_limit_status(df)
        by_date = {str(r["trade_date"]): r for _, r in df.iterrows()}
        stock_data[ts_code] = {"name": name, "df": df, "by_date": by_date}

    return stock_data


def _execute_order(agent: SimAgentState, order: dict, stock_data: dict,
                   trade_date: str, all_trades: list):
    """撮合单个订单"""
    ts_code = order.get("ts_code", "")
    # 规范化代码：LLM 可能输出 "601398" 而非 "601398.SH"
    if "." not in ts_code:
        if ts_code.startswith("60"):
            ts_code = ts_code + ".SH"
        elif ts_code.startswith("00"):
            ts_code = ts_code + ".SZ"
    direction = order.get("direction", "buy")
    quantity = max(order.get("quantity", 100) or 100, 100)  # 最少100股
    order_price = order.get("price", 0)

    price_info = _get_day_price(stock_data, ts_code, trade_date)
    if price_info is None:
        # 尝试在 stock_data 中模糊匹配
        for key in stock_data:
            if key.startswith(ts_code.split(".")[0]):
                ts_code = key
                price_info = _get_day_price(stock_data, ts_code, trade_date)
                break
    if price_info is None:
        return

    open_p, high_p, low_p, close_p = price_info["open"], price_info["high"], price_info["low"], price_info["close"]
    pct = price_info["pct_chg"]

    # 一字板检查
    if is_one_side_limit(open_p, high_p, low_p, close_p, pct):
        return

    # 使用收盘价成交 (模拟按收盘价执行)
    exec_price = close_p

    if direction == "buy":
        if order_price > 0 and exec_price > order_price * 1.05:
            return  # 价格偏离太大，不成交
        value = quantity * exec_price
        fee = calc_buy_fee(value)
        total_cost = value + fee["total_cost"]
        if total_cost > agent.cash:
            # 调整数量
            affordable_qty = int((agent.cash - fee["total_cost"]) / exec_price / 100) * 100
            if affordable_qty < 100:
                return
            quantity = affordable_qty
            value = quantity * exec_price
            fee = calc_buy_fee(value)
            total_cost = value + fee["total_cost"]

        agent.cash -= total_cost

        # 更新持仓
        existing = next((p for p in agent.positions if p["ts_code"] == ts_code), None)
        if existing:
            old_qty = existing["quantity"]
            old_cost = existing["avg_cost"]
            new_qty = old_qty + quantity
            new_cost = (old_qty * old_cost + quantity * exec_price) / new_qty
            existing["quantity"] = new_qty
            existing["avg_cost"] = round(new_cost, 4)
            existing["current_price"] = exec_price
        else:
            agent.positions.append({
                "ts_code": ts_code,
                "stock_name": order.get("stock_name", ts_code),
                "quantity": quantity,
                "avg_cost": exec_price,
                "current_price": exec_price,
                "buy_date": trade_date,
            })

        all_trades.append({
            "date": trade_date, "ts_code": ts_code, "name": order.get("stock_name", ts_code),
            "direction": "buy", "quantity": quantity, "price": round(exec_price, 2),
            "total_value": round(value, 2),
            "commission": fee["commission"], "stamp_tax": 0, "pnl": 0,
            "reason": order.get("reason", ""),
        })

    elif direction == "sell":
        pos = next((p for p in agent.positions if p["ts_code"] == ts_code), None)
        if not pos or pos["quantity"] <= 0:
            return

        sell_qty = min(quantity, pos["quantity"])
        # T+1 检查
        buy_date = pos.get("buy_date", "")
        if buy_date and buy_date >= trade_date:
            return

        value = sell_qty * exec_price
        fee = calc_sell_fee(value)
        pnl = (exec_price - pos["avg_cost"]) * sell_qty - fee["total_cost"]

        pos["quantity"] -= sell_qty
        if pos["quantity"] <= 0:
            agent.positions = [p for p in agent.positions if p["ts_code"] != ts_code]

        agent.cash += value - fee["total_cost"]

        all_trades.append({
            "date": trade_date, "ts_code": ts_code, "name": order.get("stock_name", ts_code),
            "direction": "sell", "quantity": sell_qty, "price": round(exec_price, 2),
            "total_value": round(value, 2),
            "commission": fee["commission"], "stamp_tax": fee["stamp_tax"],
            "pnl": round(pnl, 2), "reason": order.get("reason", ""),
        })


def _sim_log_path(name: str, agent_name: str, trade_date: str) -> str:
    safe_name = name or "simulation"
    return os.path.join(LOGS_DIR, "simulation", safe_name, agent_name, f"{trade_date}.log")


def _write_sim_report(name: str, agent: SimAgentState, trade_date: str,
                      context: AgentContext, decision, day_trades: list[dict]) -> str:
    report_dir = os.path.join(REPORTS_DIR, "simulation", name or "simulation", agent.display_name)
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"{trade_date}.md")
    trades_md = ""
    for t in day_trades:
        trades_md += (
            f"| {t.get('ts_code', '')} | {t.get('name', '')} | {t.get('direction', '')} | "
            f"{t.get('quantity', 0)} | {float(t.get('price', 0) or 0):.2f} | {float(t.get('pnl', 0) or 0):.2f} | {t.get('reason', '')} |\n"
        )
    orders_md = ""
    for o in decision.orders or []:
        orders_md += (
            f"| {o.get('ts_code', '')} | {o.get('stock_name', '')} | {o.get('direction', '')} | "
            f"{o.get('quantity', 0)} | {float(o.get('price', 0) or 0):.2f} | {o.get('reason', '')} |\n"
        )
    positions_md = ""
    for p in agent.positions:
        positions_md += (
            f"| {p.get('ts_code', '')} | {p.get('stock_name', '')} | {p.get('quantity', 0)} | "
            f"{float(p.get('avg_cost', 0) or 0):.2f} | {float(p.get('current_price', 0) or 0):.2f} | "
            f"{float(p.get('market_value', 0) or 0):.2f} |\n"
        )
    content = f"""# {agent.display_name} 模拟复盘报告

**模拟任务**: {name or 'simulation'}
**日期**: {trade_date}
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 资产概览

| 指标 | 数值 |
|------|------|
| 可用资金 | {context.cash:.2f} |
| 总资产 | {context.total_assets:.2f} |
| 初始本金 | {context.initial_capital:.2f} |
| 累计收益率 | {context.cumulative_return:.2f}% |

## 上个交易日撮合结果

| 代码 | 名称 | 方向 | 数量 | 价格 | 盈亏 | 理由 |
|------|------|------|------|------|------|------|
{trades_md if trades_md else '| - | 无成交 | - | - | - | - | - |'}

## 当前持仓

| 代码 | 名称 | 数量 | 成本 | 现价 | 市值 |
|------|------|------|------|------|------|
{positions_md if positions_md else '| - | 空仓 | - | - | - | - |'}

## Agent 分析

{decision.market_analysis or ''}

## 新生成订单

| 代码 | 名称 | 方向 | 数量 | 价格 | 理由 |
|------|------|------|------|------|------|
{orders_md if orders_md else '| - | 无订单 | - | - | - | - |'}

## 风险评估

{decision.risk_assessment or ''}

## 原始输出

```text
{decision.analysis or ''}
```
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def run_simulation(agents_config: list[dict], start_date: str, end_date: str,
                   name: str = "", progress_callback=None) -> dict:
    """运行 LLM Agent 模拟交易

    Args:
        agents_config: [{display_name, strategy_name, initial_capital, reasoning_effort}]
        start_date / end_date: 模拟区间 (YYYYMMDD)
        name: 模拟任务名称

    Returns:
        {sim_id, status, agents: [{equity_curve, trades, decisions, metrics}]}
    """
    # 1. 预加载数据
    stock_data = _preload_stock_data(start_date, end_date)
    index_df = load_index_daily()
    if index_df is not None:
        trading_dates = index_df[
            (index_df["trade_date"] >= start_date) & (index_df["trade_date"] <= end_date)
        ]["trade_date"].tolist()
    else:
        return {"error": "无法加载指数数据"}

    if not trading_dates:
        return {"error": "无可用交易日期"}

    # 2. 初始化 Agent 状态
    sim_agents = []
    for i, cfg in enumerate(agents_config):
        sim_agents.append(SimAgentState(
            agent_id=i + 1,
            display_name=cfg.get("display_name", f"Agent{i+1}"),
            strategy_name=cfg.get("strategy_name", "ma_pullback"),
            initial_capital=cfg.get("initial_capital", 150000.0),
            reasoning_effort=cfg.get("reasoning_effort", "high"),
            cash=cfg.get("initial_capital", 150000.0),
        ))

    # 3. 逐日模拟循环
    total_steps = max(len(trading_dates) * max(len(sim_agents), 1), 1)
    completed_steps = 0

    def _maybe_save_progress(pct: float):
        """更新进度并保存中间结果到 DB"""
        if not progress_callback:
            return
        partial = []
        for a in sim_agents:
            partial.append({
                "agent_id": a.agent_id,
                "display_name": a.display_name,
                "strategy_name": a.strategy_name,
                "initial_capital": a.initial_capital,
                "final_assets": round(a.cash + sum(
                    p.get("quantity", 0) * p.get("current_price", 0) for p in a.positions
                ), 2),
                "equity_curve": list(a.equity_curve),
                "trades": list(a.trades),
                "decisions": list(a.decisions),
            })
        progress_callback(pct, partial)

    _maybe_save_progress(1.0)

    for day_idx, trade_date in enumerate(trading_dates):
        for agent in sim_agents:
            trade_count_before = len(agent.trades)
            # 计算当前资产
            market_value = 0.0
            for pos in agent.positions:
                price_info = _get_day_price(stock_data, pos["ts_code"], trade_date)
                if price_info:
                    pos["current_price"] = price_info["close"]
                    pos["market_value"] = pos["quantity"] * price_info["close"]
                    market_value += pos["market_value"]

            total_assets = agent.cash + market_value
            cumulative_return = calc_cumulative_return(total_assets, agent.initial_capital)

            # 组装 context
            context = AgentContext(
                trade_date=trade_date,
                cash=agent.cash,
                total_assets=total_assets,
                initial_capital=agent.initial_capital,
                cumulative_return=cumulative_return,
                positions=agent.positions,
                recent_trades=agent.trades[-10:],
                market_data={"index_close": float(index_df[index_df["trade_date"] == trade_date].iloc[0]["close"])
                             if index_df is not None and len(index_df[index_df["trade_date"] == trade_date]) > 0 else 0},
            )

            # 调用 LLM Agent (单次调用，数据预取)
            try:
                decision = run_sim_agent_review(
                    agent_id=agent.agent_id,
                    agent_name=agent.display_name,
                    context=context,
                    strategy_name=agent.strategy_name,
                    preloaded_data=stock_data,
                    log_path=_sim_log_path(name, agent.display_name, trade_date),
                )
            except Exception as e:
                # Agent 调用失败，记录错误并跳过该日
                agent.decisions.append({
                    "trade_date": trade_date,
                    "error": str(e),
                    "analysis": f"[错误] LLM 调用失败: {e}",
                    "selected_stocks": [],
                    "orders": [],
                    "market_analysis": "",
                    "risk_assessment": "",
                })
                completed_steps += 1
                _maybe_save_progress(min(99.0, completed_steps / total_steps * 100))
                continue

            # 撮合订单
            for order in decision.orders:
                _execute_order(agent, order, stock_data, trade_date, agent.trades)
            day_trades = agent.trades[trade_count_before:]

            # 记录决策
            agent.decisions.append({
                "trade_date": trade_date,
                "analysis": decision.analysis,
                "selected_stocks": decision.selected_stocks,
                "orders": decision.orders,
                "market_analysis": getattr(decision, "market_analysis", ""),
                "risk_assessment": getattr(decision, "risk_assessment", ""),
                "tool_trace": getattr(decision, "tool_trace", []),
                "log_path": _sim_log_path(name, agent.display_name, trade_date),
            })
            report_path = _write_sim_report(name, agent, trade_date, context, decision, day_trades)
            agent.decisions[-1]["report_path"] = report_path

            # 记录净值
            market_value_after = sum(
                p.get("quantity", 0) * (_get_day_price(stock_data, p["ts_code"], trade_date) or {}).get("close", p.get("current_price", 0))
                for p in agent.positions
            )
            total_assets_after = agent.cash + market_value_after

            agent.equity_curve.append({
                "date": trade_date,
                "total_assets": round(total_assets_after, 2),
                "cash": round(agent.cash, 2),
                "market_value": round(market_value_after, 2),
                "return_pct": round(calc_cumulative_return(total_assets_after, agent.initial_capital), 4),
            })
            completed_steps += 1
            _maybe_save_progress(min(99.0, completed_steps / total_steps * 100))

    # 4. 计算指标
    result_agents = []
    for agent in sim_agents:
        metrics = compute_metrics(agent.equity_curve, agent.trades, agent.initial_capital)
        result_agents.append({
            "agent_id": agent.agent_id,
            "display_name": agent.display_name,
            "strategy_name": agent.strategy_name,
            "initial_capital": agent.initial_capital,
            "final_assets": round(agent.cash + sum(
                p.get("quantity", 0) * p.get("current_price", 0) for p in agent.positions
            ), 2),
            "equity_curve": agent.equity_curve,
            "trades": agent.trades,
            "decisions": agent.decisions,
            "metrics": metrics,
        })

    return {
        "agents": result_agents,
        "start_date": start_date,
        "end_date": end_date,
        "trading_days": len(trading_dates),
    }
