"""Agent 基类与上下文定义"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentContext:
    """Agent 决策上下文"""
    trade_date: str
    cash: float
    total_assets: float
    initial_capital: float
    cumulative_return: float = 0.0
    positions: list[dict] = field(default_factory=list)
    recent_trades: list[dict] = field(default_factory=list)
    recent_orders: list[dict] = field(default_factory=list)
    frozen_cash: float = 0.0
    market_data: Optional[dict] = None
    evolution_context: Optional[dict] = None


@dataclass
class AgentDecision:
    """Agent 决策结果"""
    agent_id: int
    trade_date: str
    analysis: str = ""
    selected_stocks: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    market_analysis: str = ""
    risk_assessment: str = ""
    tool_trace: list[dict] = field(default_factory=list)
