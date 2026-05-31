"""Pydantic 数据模型"""

from pydantic import BaseModel
from typing import Optional
from datetime import date


class StockBasic(BaseModel):
    ts_code: str
    name: str
    market: Optional[str] = None
    industry: Optional[str] = None
    sector: Optional[str] = None
    is_main_board: bool = False


class KlineDaily(BaseModel):
    ts_code: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    change: float = 0.0
    pct_chg: float = 0.0
    vol: float = 0.0
    amount: float = 0.0
    turnover_rate: float = 0.0
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    is_limit_up: bool = False
    is_limit_down: bool = False


class AgentInfo(BaseModel):
    id: Optional[int] = None
    name: str
    display_name: Optional[str] = None
    agent_type: str = "custom"
    initial_capital: float = 150000.0
    current_cash: float = 150000.0
    strategy_ids: Optional[str] = None
    risk_config: Optional[str] = None
    status: str = "active"


class AgentPosition(BaseModel):
    id: Optional[int] = None
    agent_id: int
    ts_code: str
    stock_name: Optional[str] = None
    quantity: int = 0
    available_shares: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    buy_date: Optional[str] = None


class AgentOrder(BaseModel):
    id: Optional[int] = None
    agent_id: int
    ts_code: str
    stock_name: Optional[str] = None
    direction: str
    order_type: str = "limit"
    quantity: int
    price: float
    open_get_in: bool = False
    reserved_cash: float = 0.0
    decision_batch_id: Optional[str] = None
    fill_probability: Optional[float] = None
    price_aggressiveness: Optional[float] = None
    skill_id: Optional[str] = None
    skill_confidence: float = 0.0
    failure_attribution: Optional[str] = None
    evolution_mark: Optional[str] = None
    reason: Optional[str] = None
    fail_reason: Optional[str] = None
    status: str = "pending"
    trade_date: Optional[str] = None


class AgentTradeLog(BaseModel):
    id: Optional[int] = None
    order_id: Optional[int] = None
    agent_id: int
    ts_code: str
    stock_name: Optional[str] = None
    direction: str
    quantity: int
    price: float
    total_value: float
    commission: float = 0.0
    stamp_tax: float = 0.0
    trade_date: str


class AgentDailyReport(BaseModel):
    id: Optional[int] = None
    agent_id: int
    trade_date: str
    cash: float
    market_value: float
    total_assets: float
    daily_pnl: float = 0.0
    daily_return: float = 0.0
    cumulative_pnl: float = 0.0
    cumulative_return: float = 0.0
    position_count: int = 0
    factor_weight_log: Optional[str] = None
    risk_adjust_log: Optional[str] = None
    report_md_path: Optional[str] = None
    think_log_path: Optional[str] = None


class StrategyInfo(BaseModel):
    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    category: str = "custom"
    params_json: Optional[str] = None
    code: Optional[str] = None
