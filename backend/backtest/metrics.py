"""回测绩效指标计算"""

import numpy as np


def compute_metrics(equity_curve: list[dict], trades: list[dict],
                    initial_capital: float) -> dict:
    """计算回测指标

    Returns:
        {annual_return, max_drawdown, win_rate, sharpe_ratio,
         total_return, total_trades, profit_factor}
    """
    if not equity_curve:
        return _empty_metrics()

    returns = [e["return_pct"] for e in equity_curve]

    # 累计收益
    total_return = returns[-1] if returns else 0

    # 年化收益
    n_days = len(equity_curve)
    years = n_days / 252
    if years > 0:
        final_value = equity_curve[-1]["total_assets"]
        annual_return = (final_value / initial_capital) ** (1 / years) - 1
    else:
        annual_return = 0

    # 最大回撤
    max_drawdown = _calc_max_drawdown(equity_curve)

    # 胜率
    sell_trades = [t for t in trades if t["direction"] == "sell"]
    winning = sum(1 for t in sell_trades if t["pnl"] > 0)
    win_rate = (winning / len(sell_trades) * 100) if sell_trades else 0

    # 盈亏比
    avg_win = np.mean([t["pnl"] for t in sell_trades if t["pnl"] > 0]) if winning > 0 else 0
    avg_loss = abs(np.mean([t["pnl"] for t in sell_trades if t["pnl"] <= 0])) if len(sell_trades) - winning > 0 else 1
    profit_factor = avg_win / avg_loss if avg_loss > 0 else 0

    # 日收益率统计
    daily_returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]["total_assets"]
        curr = equity_curve[i]["total_assets"]
        if prev > 0:
            daily_returns.append((curr - prev) / prev * 100)

    # 夏普比率（简化）
    if daily_returns:
        avg_daily = np.mean(daily_returns)
        std_daily = np.std(daily_returns) or 1
        sharpe = (avg_daily / std_daily) * np.sqrt(252) if std_daily > 0 else 0
    else:
        sharpe = 0

    return {
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return * 100, 2),
        "max_drawdown": round(max_drawdown, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 2),
        "total_trades": len(trades),
        "winning_trades": winning,
        "initial_capital": initial_capital,
        "final_assets": round(equity_curve[-1]["total_assets"], 2),
        "final_profit": round(equity_curve[-1]["total_assets"] - initial_capital, 2),
    }


def _calc_max_drawdown(equity_curve: list[dict]) -> float:
    """计算最大回撤百分比"""
    peak = equity_curve[0]["total_assets"]
    max_dd = 0

    for e in equity_curve:
        val = e["total_assets"]
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    return max_dd


def _empty_metrics() -> dict:
    return {
        "total_return": 0,
        "annual_return": 0,
        "max_drawdown": 0,
        "win_rate": 0,
        "profit_factor": 0,
        "sharpe_ratio": 0,
        "total_trades": 0,
        "winning_trades": 0,
        "initial_capital": 0,
        "final_assets": 0,
        "final_profit": 0,
    }
