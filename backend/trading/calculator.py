"""仓位 / 盈亏计算器

所有计算封装为独立函数，禁止 LLM 自行算数。
"""

from typing import Optional


def calc_position_ratio(position_value: float, total_assets: float) -> float:
    """计算仓位占比"""
    if total_assets <= 0:
        return 0.0
    return round(position_value / total_assets * 100, 2)


def calc_max_buy_quantity(cash: float, price: float) -> int:
    """计算最大可买股数（基于可用资金）

    Args:
        cash: 可用资金
        price: 买入价格

    Returns:
        最大可买股数（100股整数倍）
    """
    return int(cash / price / 100) * 100


def check_position_count(current_count: int, max_count: int = 5) -> tuple[bool, str]:
    """检查持仓数量是否超限

    Args:
        current_count: 当前持仓股票数
        max_count: 最大持仓股票数

    Returns:
        (是否允许开新仓, 提示信息)
    """
    if current_count >= max_count:
        return False, f"当前持仓{current_count}只，已达上限{max_count}只，无法开新仓"
    return True, ""


def calc_weighted_avg_cost(old_quantity: int, old_avg_cost: float,
                           new_quantity: int, new_price: float) -> float:
    """计算加权平均成本（买入加仓时）"""
    total_shares = old_quantity + new_quantity
    if total_shares == 0:
        return 0.0
    total_cost = old_quantity * old_avg_cost + new_quantity * new_price
    return round(total_cost / total_shares, 4)


def calc_realized_pnl(sell_quantity: int, sell_price: float, avg_cost: float,
                      commission: float, stamp_tax: float) -> float:
    """计算已实现盈亏"""
    revenue = sell_quantity * sell_price
    cost = sell_quantity * avg_cost
    return round(revenue - cost - commission - stamp_tax, 2)


def calc_unrealized_pnl(quantity: int, current_price: float, avg_cost: float) -> float:
    """计算未实现盈亏（浮动盈亏）"""
    if quantity == 0:
        return 0.0
    return round((current_price - avg_cost) * quantity, 2)


def calc_total_assets(cash: float, positions: list[dict]) -> float:
    """计算总资产 = 现金 + 持仓市值"""
    market_value = sum(p.get("market_value", 0) for p in positions)
    return round(cash + market_value, 2)


def calc_daily_return(daily_pnl: float, prev_total_assets: float) -> float:
    """计算日收益率"""
    if prev_total_assets <= 0:
        return 0.0
    return round(daily_pnl / prev_total_assets * 100, 4)


def calc_cumulative_return(total_assets: float, initial_capital: float) -> float:
    """计算累计收益率"""
    if initial_capital <= 0:
        return 0.0
    return round((total_assets - initial_capital) / initial_capital * 100, 2)


def calc_risk_stop(max_daily_loss_pct: float, daily_loss: float,
                   total_assets: float) -> tuple[bool, str]:
    """风控检查：是否触发当日最大亏损阈值"""
    if total_assets <= 0:
        return False, ""
    loss_ratio = abs(daily_loss) / total_assets
    if loss_ratio >= max_daily_loss_pct:
        return True, f"当日亏损{loss_ratio*100:.2f}%触发阈值{max_daily_loss_pct*100:.2f}%，停止新开仓"
    return False, ""
