"""Agent 工具函数集

所有工具封装为独立函数，供 LLM Agent 调用。
"""

import os
import json
import math
from typing import Optional
import pandas as pd
from langchain.tools import tool

from backend.data.loader import (
    load_daily, load_index_daily, compute_mas, compute_limit_status,
    list_main_board_stocks
)
from backend.data.indicators import (
    compute_market_breadth,
    compute_market_strength_by_sector,
    compute_sector_heat,
    compute_sector_temperature,
)
from backend.strategies.registry import StrategyRegistry
from backend.search_agent.searcher import get_cached, is_cached
from backend.policy.reader import extract_policy_signals, read_recent_policies
from backend.config import DAILY_DIR, COMPANY_BUSINESS_DIR
from backend.trading.rules import normalize_ts_code
from backend.db.repository import get_conn, get_read_conn
from backend.evolution.engine import prepare_evolution_context, format_evolution_prompt
from backend.evolution.skills import get_skill as _get_skill, strategy_param_schema


def _format_kline_brief(df, lookback: int = 5) -> str:
    """格式化K线数据摘要"""
    recent = df.tail(lookback)
    lines = []
    for _, row in recent.iterrows():
        turn = row.get("turnover_rate", 0) or 0
        ma5 = row.get("ma5")
        ma5_text = f"MA5={ma5:.2f}" if ma5 is not None and not pd.isna(ma5) else ""
        lines.append(
            f"{row['trade_date']}: O={row['open']:.2f} H={row['high']:.2f} "
            f"L={row['low']:.2f} C={row['close']:.2f} "
            f"涨跌={row.get('pct_chg', 0):.2f}% 换手={turn:.2f}% "
            f"{ma5_text}"
        )
    return "\n".join(lines)


@tool
def search_stocks_by_strategy(strategy_name: str, params_json: str = "{}") -> str:
    """使用指定策略筛选股票。

    Args:
        strategy_name: 策略名称 (momentum/trend/ma_pullback)
        params_json: 策略参数 JSON 字符串，如 '{"min_limit_up_days": 3}'

    Returns:
        JSON 格式的筛选结果
    """
    try:
        params = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError:
        params = {}

    strategy = StrategyRegistry.create(strategy_name, **params)
    if strategy is None:
        return json.dumps({"error": f"未知策略: {strategy_name}"})

    main_board = list_main_board_stocks()
    results = []

    for _, row in main_board.iterrows():
        ts_code = row["ts_code"]
        name = row["name"]
        df = load_daily(ts_code)
        if df is None or len(df) < 30:
            continue
        df = compute_mas(df)
        df = compute_limit_status(df)
        try:
            result = strategy.filter(ts_code, name, df)
            if result:
                results.append({
                    "ts_code": result.ts_code,
                    "name": result.name,
                    "reason": result.reason,
                    "score": result.score,
                    "extra": result.extra,
                })
        except Exception as e:
            continue

    results.sort(key=lambda r: r["score"], reverse=True)
    return json.dumps(results[:20], ensure_ascii=False)


def _stock_pool_rows(agent_id: int) -> list[dict]:
    conn = get_read_conn()
    rows = conn.execute(
        """SELECT ts_code, stock_name, note, enabled
           FROM agent_stock_pool
           WHERE agent_id=? AND enabled=1
           ORDER BY id ASC""",
        (agent_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@tool
def get_agent_stock_pool(agent_id: int) -> str:
    """读取当前 Agent 的前端配置股票池。"""
    rows = _stock_pool_rows(agent_id)
    return json.dumps({
        "agent_id": agent_id,
        "count": len(rows),
        "stocks": rows,
    }, ensure_ascii=False)


@tool
def search_stocks_in_agent_pool(agent_id: int, strategy_name: str, params_json: str = "{}") -> str:
    """只在当前 Agent 的前端配置股票池内执行指定策略筛选。

    Args:
        agent_id: Agent ID
        strategy_name: 策略名称，如 momentum/trend/ma_pullback/ma_bullish_pullback
        params_json: 策略参数 JSON 字符串

    Returns:
        JSON 格式的池内筛选结果
    """
    try:
        params = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError:
        params = {}
    strategy = StrategyRegistry.create(strategy_name, **params)
    if strategy is None:
        return json.dumps({"error": f"未知策略: {strategy_name}"}, ensure_ascii=False)

    rows = _stock_pool_rows(agent_id)
    results = []
    for item in rows:
        ts_code = normalize_ts_code(item.get("ts_code", ""))
        name = item.get("stock_name") or ts_code
        df = load_daily(ts_code)
        if df is None or len(df) < 30:
            continue
        df = compute_mas(df)
        df = compute_limit_status(df)
        try:
            result = strategy.filter(ts_code, name, df)
            if result:
                results.append({
                    "ts_code": result.ts_code,
                    "name": result.name,
                    "reason": result.reason,
                    "score": result.score,
                    "extra": result.extra,
                    "pool_note": item.get("note") or "",
                })
        except Exception:
            continue
    results.sort(key=lambda r: r["score"], reverse=True)
    return json.dumps(results[:20], ensure_ascii=False)


@tool
def get_stock_kline(ts_code: str, days: int = 30) -> str:
    """获取个股日线K线数据。

    Args:
        ts_code: 股票代码，如 '600000.SH'
        days: 返回最近多少天的数据，默认30

    Returns:
        格式化的K线数据文本
    """
    ts_code = normalize_ts_code(ts_code)
    df = load_daily(ts_code)
    if df is None:
        return f"未找到 {ts_code} 的行情数据"

    df = compute_mas(df)
    df = compute_limit_status(df)
    return _format_kline_brief(df, days)


@tool
def get_market_overview() -> str:
    """获取大盘概况（上证指数走势）。

    Returns:
        格式化的上证指数数据
    """
    df = load_index_daily()
    if df is None or df.empty:
        return "暂无大盘数据"

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    return (
        f"上证指数 最新交易日: {latest['trade_date']}\n"
        f"收盘: {latest['close']:.2f} | 开盘: {latest['open']:.2f}\n"
        f"最高: {latest['high']:.2f} | 最低: {latest['low']:.2f}\n"
        f"涨跌幅: {latest.get('pct_chg', 0):.2f}%\n"
        f"成交额: {latest.get('amount', 0) / 1e8:.2f}亿\n"
        f"---\n最近5天:\n{_format_kline_brief(df, 5)}"
    )


@tool
def get_company_business(ts_code: str) -> str:
    """获取公司主营业务信息（从本地缓存MD文件读取，自动检查时效性）。

    如果缓存文件超过30天未更新，会提示需要刷新。

    Args:
        ts_code: 股票代码，如 '603629.SH'

    Returns:
        公司业务描述文本（含时效性说明）
    """
    from backend.search_agent.searcher import get_freshness

    freshness = get_freshness(ts_code)
    if freshness:
        content = get_cached(ts_code)
        if content:
            if freshness["is_fresh"]:
                return content
            else:
                return (
                    f"[警告] 缓存已过时（{freshness['age_days']}天前），信息可能不准确。\n"
                    f"建议刷新后再用于决策。\n\n"
                    f"---\n{content}"
                )

    return f"暂无 {ts_code} 的公司业务信息。建议联网搜索该公司主营业务后继续分析。"


@tool
def compute_sector_heat_tool(trade_date: str = "") -> str:
    """计算每日板块热度排行。

    Args:
        trade_date: 交易日期，空字符串表示最新

    Returns:
        JSON 格式的板块热度列表
    """
    results = compute_sector_heat(trade_date)
    if not results:
        return "暂无板块热度数据"
    return json.dumps(results[:15], ensure_ascii=False)


@tool
def get_market_strength_sectors(trade_date: str = "", lookback_days: int = 3) -> str:
    """按近 N 个交易日涨幅、涨停家数、换手统计强势/弱势板块。

    Args:
        trade_date: 交易日期，空字符串表示最新
        lookback_days: 统计窗口，默认3个交易日

    Returns:
        JSON 格式的 strong/weak 板块与领涨股
    """
    if not trade_date:
        df = load_index_daily()
        trade_date = str(df.iloc[-1]["trade_date"]) if df is not None and not df.empty else ""
    return json.dumps(compute_market_strength_by_sector(trade_date, lookback_days, 10), ensure_ascii=False)


@tool
def get_market_breadth(trade_date: str = "") -> str:
    """统计全市场涨跌宽度、涨停/大涨/跌停/大跌分布和 risk-on 分数。"""
    return json.dumps(compute_market_breadth(trade_date), ensure_ascii=False, default=str)


@tool
def get_sector_temperature(trade_date: str = "", top_n: int = 20) -> str:
    """统计当日板块温度，返回热门板块、风险板块、领涨股和市场状态。"""
    return json.dumps(compute_sector_temperature(trade_date, top_n), ensure_ascii=False, default=str)


@tool
def get_shared_stock_report(ts_code: str) -> str:
    """读取推荐助手沉淀的用户关注股票共享研究报告。"""
    from backend.telegram.stock_interest import get_shared_stock_report as _get_shared_stock_report
    return _get_shared_stock_report(ts_code)


@tool
def get_policy_signals() -> str:
    """获取近期国家宏观政策信号（发改委/工信部/财政部政策文件分析）。

    从本地缓存的政府政策文件中提取产业政策信号，
    包括政策重点关注的行业板块及政策力度。

    Returns:
        JSON 格式的政策信号分析结果
    """
    signals = extract_policy_signals()
    return json.dumps(signals, ensure_ascii=False)


@tool
def get_macro_daily_report(trade_date: str = "") -> str:
    """读取每日宏观市场报告。

    Args:
        trade_date: 交易日期，空字符串表示最新报告

    Returns:
        文本化宏观报告，包含市场状态、热点板块、龙虎榜、涨停池、政策和交易建议。
    """
    from backend.macro.report import get_macro_daily_report_text
    return get_macro_daily_report_text(trade_date)


@tool
def get_macro_market_topic(topic: str = "report", trade_date: str = "") -> str:
    """读取每日宏观报告里的指定主题。

    Args:
        topic: report/sector/lhb/limit_up/limit_down/broken_limit/strong
        trade_date: 交易日期，空字符串表示最新

    Returns:
        文本化主题摘要，适合用于交易前二次筛选。
    """
    from backend.macro.report import format_macro_topic
    return format_macro_topic(topic, trade_date)


@tool
def get_stock_chip_distribution(ts_code: str) -> str:
    """模拟个股前复权筹码分布。

    Args:
        ts_code: 股票代码，如 600000.SH

    Returns:
        文本化模拟筹码峰/获利比例/成本集中度摘要。
    """
    from backend.macro.report import format_chip_distribution
    return format_chip_distribution(ts_code)


@tool
def get_agent_performance(agent_id: int) -> str:
    """查询实盘 Agent 历史战绩、持仓和近期交易。

    Args:
        agent_id: Agent ID

    Returns:
        JSON 格式的 Agent 绩效摘要
    """
    from backend.telegram.recommender import get_agent_performance as _get_agent_performance
    return json.dumps(_get_agent_performance(agent_id), ensure_ascii=False)


@tool
def get_simulation_performance(sim_id: int) -> str:
    """查询模拟交易任务战绩。

    Args:
        sim_id: simulation_task ID

    Returns:
        JSON 格式的模拟任务绩效摘要
    """
    from backend.telegram.recommender import get_simulation_performance as _get_simulation_performance
    return json.dumps(_get_simulation_performance(sim_id), ensure_ascii=False)


@tool
def get_stock_analysis_report(ts_code: str) -> str:
    """生成单只股票的结构化分析报告。

    Args:
        ts_code: 股票代码，如 600000.SH

    Returns:
        包含技术面、业务、政策、交易 Agent 参考的文本报告
    """
    from backend.telegram.stock_analysis import generate_stock_report as _generate_stock_report
    return _generate_stock_report(ts_code)


@tool
def calculate_price_by_pct(base_price: float, pct_change: float, tick_size: float = 0.01) -> str:
    """按基准价格和涨跌幅计算目标价格，供 Agent 生成挂单价。

    Args:
        base_price: 基准价格，通常为今日收盘价或预测参考价
        pct_change: 涨跌幅百分比，例如高开 3% 传 3，回落 2% 传 -2
        tick_size: A股最小价格单位，默认 0.01

    Returns:
        JSON，包含目标价格、涨跌幅和计算公式
    """
    base = float(base_price or 0)
    pct = float(pct_change or 0)
    tick = float(tick_size or 0.01)
    raw_price = base * (1 + pct / 100)
    target_price = round(round(raw_price / tick) * tick, 2)
    return json.dumps({
        "base_price": round(base, 4),
        "pct_change": pct,
        "target_price": target_price,
        "formula": f"{base:.4f} * (1 + {pct:.4f} / 100)",
    }, ensure_ascii=False)


@tool
def validate_order_price_limit(ts_code: str, order_price: float, base_price: float = 0.0,
                               limit_pct: float = 10.0) -> str:
    """校验挂单价是否落在参考价的涨跌停范围内。

    Args:
        ts_code: 股票代码，如 600000.SH；base_price 为 0 时自动读取该股最新收盘价
        order_price: 计划挂单价
        base_price: 参考价，通常为今日收盘价；0 表示自动读取最新收盘价
        limit_pct: 普通主板默认涨跌停 10%

    Returns:
        JSON，包含是否合法、允许价格区间和修正建议
    """
    code = normalize_ts_code(ts_code)
    base = float(base_price or 0)
    if base <= 0:
        df = load_daily(code)
        if df is None or df.empty:
            return json.dumps({"ok": False, "error": f"未找到 {code} 的行情数据"}, ensure_ascii=False)
        base = float(df.iloc[-1]["close"])
    price = round(float(order_price or 0), 2)
    limit = float(limit_pct or 10)
    lower = round(base * (1 - limit / 100), 2)
    upper = round(base * (1 + limit / 100), 2)
    ok = lower <= price <= upper
    return json.dumps({
        "ok": ok,
        "ts_code": code,
        "base_price": round(base, 2),
        "order_price": price,
        "limit_pct": limit,
        "lower_limit": lower,
        "upper_limit": upper,
        "suggested_price": min(max(price, lower), upper),
        "message": "挂单价合法" if ok else "挂单价超出参考价涨跌停范围，请重新计算后下单",
    }, ensure_ascii=False)


@tool
def calculate_position_size(
    agent_id: int,
    ts_code: str,
    entry_price: float,
    stop_price: float = 0.0,
    risk_budget_pct: float = 1.0,
    max_position_pct: float = 15.0,
) -> str:
    """按风险预算估算建议下单股数。

    Args:
        agent_id: Agent ID
        ts_code: 股票代码
        entry_price: 计划买入价
        stop_price: 止损价；为0时按 entry_price 下方 5% 估算
        risk_budget_pct: 单笔最多亏损占总资产百分比，默认1%
        max_position_pct: 单票最大仓位占总资产百分比，默认15%

    Returns:
        JSON，包含风险预算、止损距离、建议股数和实际仓位。
    """
    code = normalize_ts_code(ts_code)
    entry = float(entry_price or 0)
    if entry <= 0:
        return json.dumps({"ok": False, "error": "entry_price 必须大于0"}, ensure_ascii=False)
    stop = float(stop_price or 0) or entry * 0.95
    risk_per_share = max(0.01, entry - stop)
    conn = get_read_conn()
    agent = conn.execute("SELECT current_cash, initial_capital FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    position_value = conn.execute(
        "SELECT COALESCE(SUM(market_value), 0) FROM agent_position WHERE agent_id=?",
        (agent_id,),
    ).fetchone()[0]
    frozen = conn.execute(
        "SELECT COALESCE(SUM(reserved_cash), 0) FROM agent_order WHERE agent_id=? AND status='pending'",
        (agent_id,),
    ).fetchone()[0]
    conn.close()
    cash = float(agent["current_cash"] or 0) if agent else 0.0
    total_assets = cash + float(position_value or 0) + float(frozen or 0)
    risk_budget = total_assets * max(0.0, float(risk_budget_pct or 0)) / 100.0
    max_position_value = total_assets * max(0.0, float(max_position_pct or 0)) / 100.0
    by_risk = int(risk_budget // risk_per_share)
    by_position = int(max_position_value // entry)
    by_cash = int(cash // entry)
    shares = max(0, min(by_risk, by_position, by_cash) // 100 * 100)
    return json.dumps({
        "ok": True,
        "agent_id": agent_id,
        "ts_code": code,
        "entry_price": round(entry, 2),
        "stop_price": round(stop, 2),
        "risk_per_share": round(risk_per_share, 4),
        "total_assets_est": round(total_assets, 2),
        "cash": round(cash, 2),
        "risk_budget_pct": risk_budget_pct,
        "risk_budget_value": round(risk_budget, 2),
        "max_position_pct": max_position_pct,
        "max_position_value": round(max_position_value, 2),
        "suggested_quantity": shares,
        "suggested_value": round(shares * entry, 2),
        "actual_position_pct": round((shares * entry / total_assets * 100) if total_assets else 0, 2),
    }, ensure_ascii=False)


@tool
def get_recent_order_history(agent_id: int, days: int = 5) -> str:
    """查询 Agent 最近挂单、成交、过期与失败原因。

    Args:
        agent_id: Agent ID
        days: 近 N 个交易日，默认 5

    Returns:
        JSON，包含订单价格、open_get_in、状态、失败原因和成交记录
    """
    conn = get_read_conn()
    rows = conn.execute(
        """SELECT id, ts_code, stock_name, direction, quantity, price, open_get_in, reserved_cash,
                  skill_id, skill_confidence, failure_attribution, evolution_mark,
                  status, trade_date, fail_reason, created_at, filled_at, expired_at
           FROM agent_order
           WHERE agent_id=?
           ORDER BY trade_date DESC, id DESC
           LIMIT ?""",
        (agent_id, max(1, int(days or 5)) * 8),
    ).fetchall()
    trades = conn.execute(
        """SELECT order_id, ts_code, stock_name, direction, quantity, price, total_value, trade_date
           FROM agent_trade_log
           WHERE agent_id=?
           ORDER BY trade_date DESC, id DESC
           LIMIT ?""",
        (agent_id, max(1, int(days or 5)) * 8),
    ).fetchall()
    conn.close()
    return json.dumps({
        "orders": [dict(r) for r in rows],
        "trades": [dict(r) for r in trades],
    }, ensure_ascii=False, default=str)


@tool
def get_portfolio_risk_metrics(agent_id: int, lookback_days: int = 60) -> str:
    """查询 Agent 当前组合风险指标。

    Args:
        agent_id: Agent ID
        lookback_days: 使用最近多少个交易日估算波动率和 VaR，默认60

    Returns:
        JSON，包含持仓集中度、行业暴露、组合波动率、VaR 和风险提示。
    """
    conn = get_read_conn()
    agent = conn.execute(
        "SELECT initial_capital, current_cash FROM agent_info WHERE id=?",
        (agent_id,),
    ).fetchone()
    positions = conn.execute(
        """SELECT p.ts_code, p.stock_name, p.quantity, p.current_price, p.market_value,
                  COALESCE(sb.sector_tag, sb.sector, sb.industry_tag, sb.industry, '未知') AS sector
           FROM agent_position p
           LEFT JOIN stock_basic sb ON sb.ts_code=p.ts_code
           WHERE p.agent_id=? AND p.quantity>0""",
        (agent_id,),
    ).fetchall()
    conn.close()
    cash = float(agent["current_cash"] if agent else 0)
    position_rows = [dict(r) for r in positions]
    position_value = sum(float(r.get("market_value") or 0) for r in position_rows)
    total_assets = cash + position_value
    sector_exposure: dict[str, float] = {}
    weights: dict[str, float] = {}
    returns = []
    for row in position_rows:
        code = normalize_ts_code(row.get("ts_code", ""))
        mv = float(row.get("market_value") or 0)
        weight = mv / total_assets if total_assets else 0.0
        weights[code] = weight
        sector = row.get("sector") or "未知"
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + weight
        df = load_daily(code)
        if df is None or len(df) < 3:
            continue
        pct = pd.to_numeric(df.tail(max(3, int(lookback_days or 60)))["pct_chg"], errors="coerce").fillna(0) / 100
        returns.append((code, pct.reset_index(drop=True)))

    portfolio_returns = []
    if returns:
        min_len = min(len(series) for _, series in returns)
        for i in range(min_len):
            portfolio_returns.append(sum(weights.get(code, 0.0) * float(series.iloc[-min_len + i]) for code, series in returns))
    volatility = 0.0
    var_95 = 0.0
    if portfolio_returns:
        s = pd.Series(portfolio_returns)
        volatility = float(s.std(ddof=0) * math.sqrt(252) * 100)
        var_95 = float(s.quantile(0.05) * 100)

    top_position = max(weights.items(), key=lambda item: item[1], default=("", 0.0))
    top_sector = max(sector_exposure.items(), key=lambda item: item[1], default=("未知", 0.0))
    warnings = []
    if top_position[1] > 0.25:
        warnings.append(f"单票集中度偏高: {top_position[0]} {top_position[1]:.1%}")
    if top_sector[1] > 0.45:
        warnings.append(f"行业/板块暴露偏高: {top_sector[0]} {top_sector[1]:.1%}")
    if var_95 < -3:
        warnings.append(f"VaR 95% 偏高: {var_95:.2f}%")
    return json.dumps({
        "agent_id": agent_id,
        "total_assets_est": round(total_assets, 2),
        "cash": round(cash, 2),
        "position_value": round(position_value, 2),
        "position_count": len(position_rows),
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "sector_exposure": {k: round(v, 4) for k, v in sector_exposure.items()},
        "top_position_weight": {"ts_code": top_position[0], "weight": round(top_position[1], 4)},
        "top_sector_weight": {"sector": top_sector[0], "weight": round(top_sector[1], 4)},
        "annualized_volatility_pct": round(volatility, 4),
        "var_95_daily_pct": round(var_95, 4),
        "warnings": warnings,
    }, ensure_ascii=False, default=str)


@tool
def get_correlation_info(ts_codes_json: str, agent_id: int = 0, lookback_days: int = 60) -> str:
    """估算候选股票和当前持仓之间的收益相关性。

    Args:
        ts_codes_json: 股票代码 JSON 数组或逗号分隔文本，如 '["600000.SH","000001.SZ"]'
        agent_id: 可选 Agent ID；传入后会自动加入当前持仓一起计算
        lookback_days: 使用最近多少个交易日，默认60

    Returns:
        JSON，包含相关性矩阵、高相关配对和分散化提示。
    """
    try:
        parsed = json.loads(ts_codes_json) if ts_codes_json else []
        codes = parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        codes = [x.strip() for x in str(ts_codes_json or "").split(",") if x.strip()]
    normalized = [normalize_ts_code(str(code)) for code in codes if str(code).strip()]
    if agent_id:
        conn = get_read_conn()
        rows = conn.execute(
            "SELECT ts_code FROM agent_position WHERE agent_id=? AND quantity>0",
            (agent_id,),
        ).fetchall()
        conn.close()
        normalized.extend(normalize_ts_code(r["ts_code"]) for r in rows)
    normalized = list(dict.fromkeys(normalized))
    series_map = {}
    for code in normalized:
        df = load_daily(code)
        if df is None or len(df) < 5:
            continue
        s = pd.to_numeric(df.tail(max(5, int(lookback_days or 60)))["pct_chg"], errors="coerce").fillna(0)
        series_map[code] = s.reset_index(drop=True)
    if len(series_map) < 2:
        return json.dumps({
            "codes": normalized,
            "error": "可用行情不足，至少需要两个标的",
        }, ensure_ascii=False)
    min_len = min(len(s) for s in series_map.values())
    frame = pd.DataFrame({code: s.tail(min_len).reset_index(drop=True) for code, s in series_map.items()})
    corr = frame.corr().fillna(0)
    high_pairs = []
    cols = list(corr.columns)
    for i, left in enumerate(cols):
        for right in cols[i + 1:]:
            value = float(corr.loc[left, right])
            if value >= 0.75:
                high_pairs.append({"left": left, "right": right, "correlation": round(value, 4)})
    return json.dumps({
        "codes": cols,
        "lookback_days": min_len,
        "correlation_matrix": {
            left: {right: round(float(corr.loc[left, right]), 4) for right in cols}
            for left in cols
        },
        "high_correlation_pairs": high_pairs,
        "warning": "存在高相关标的，买入前应降低仓位或选择替代方向" if high_pairs else "",
    }, ensure_ascii=False, default=str)


@tool
def get_evolution_context(agent_id: int) -> str:
    """读取 Agent 进化记忆快照、技能索引和上次进化结果。

    Args:
        agent_id: Agent ID

    Returns:
        文本化进化上下文，包含 trade_fact/trade_prefer 和技能置信度。
    """
    conn = get_conn()
    latest = conn.execute(
        "SELECT COALESCE(MAX(trade_date), strftime('%Y%m%d','now')) FROM agent_daily_report WHERE agent_id=?",
        (agent_id,),
    ).fetchone()[0]
    agent = conn.execute("SELECT display_name, name FROM agent_info WHERE id=?", (agent_id,)).fetchone()
    context = prepare_evolution_context(
        agent_id,
        (agent["display_name"] or agent["name"]) if agent else str(agent_id),
        str(latest),
        conn,
    )
    conn.commit()
    conn.close()
    return format_evolution_prompt(context)


@tool
def get_skill_params(agent_id: int, skill_id: str) -> str:
    """读取指定进化技能的完整参数。

    Args:
        agent_id: Agent ID
        skill_id: 技能 ID，如 momentum_hunt/balanced_factor

    Returns:
        JSON，包含动态参数、失效场景和当前置信度。
    """
    return json.dumps(_get_skill(agent_id, skill_id), ensure_ascii=False, default=str)


@tool
def get_strategy_param_schema(strategy_name: str = "") -> str:
    """查询选股策略可自定义敏感参数。

    Args:
        strategy_name: 策略名称；留空返回全部策略。可先查再把参数放入 search_stocks_by_strategy 的 params_json。

    Returns:
        JSON，包含各策略构造参数、默认值和说明。
    """
    return json.dumps(strategy_param_schema(strategy_name), ensure_ascii=False, default=str)


# 所有工具列表
AGENT_TOOLS = [
    search_stocks_by_strategy,
    get_agent_stock_pool,
    search_stocks_in_agent_pool,
    get_stock_kline,
    get_market_overview,
    get_company_business,
    compute_sector_heat_tool,
    get_market_strength_sectors,
    get_market_breadth,
    get_sector_temperature,
    get_macro_daily_report,
    get_macro_market_topic,
    get_stock_chip_distribution,
    get_policy_signals,
    get_agent_performance,
    get_simulation_performance,
    get_stock_analysis_report,
    calculate_price_by_pct,
    validate_order_price_limit,
    calculate_position_size,
    get_recent_order_history,
    get_portfolio_risk_metrics,
    get_correlation_info,
    get_evolution_context,
    get_shared_stock_report,
    get_skill_params,
    get_strategy_param_schema,
]


_TOOL_CATEGORIES = {
    "search_stocks_by_strategy": "选股",
    "get_agent_stock_pool": "选股",
    "search_stocks_in_agent_pool": "选股",
    "get_stock_kline": "行情",
    "get_market_overview": "行情",
    "compute_sector_heat_tool": "行情",
    "get_market_strength_sectors": "行情",
    "get_market_breadth": "行情",
    "get_sector_temperature": "行情",
    "get_macro_daily_report": "行情",
    "get_macro_market_topic": "行情",
    "get_stock_chip_distribution": "行情",
    "get_policy_signals": "政策",
    "get_company_business": "基本面",
    "get_stock_analysis_report": "基本面",
    "get_agent_performance": "业绩",
    "get_simulation_performance": "业绩",
    "calculate_price_by_pct": "订单风控",
    "validate_order_price_limit": "订单风控",
    "calculate_position_size": "订单风控",
    "get_recent_order_history": "订单风控",
    "get_portfolio_risk_metrics": "订单风控",
    "get_correlation_info": "订单风控",
    "get_evolution_context": "进化记忆",
    "get_shared_stock_report": "进化记忆",
    "get_skill_params": "进化记忆",
    "get_strategy_param_schema": "进化记忆",
}

_MANDATORY_TOOL_NAMES = {
    "calculate_price_by_pct",
    "validate_order_price_limit",
    "calculate_position_size",
    "get_recent_order_history",
    "get_portfolio_risk_metrics",
    "get_correlation_info",
    "get_evolution_context",
    "get_market_breadth",
    "get_sector_temperature",
    "get_macro_daily_report",
    "get_macro_market_topic",
    "get_strategy_param_schema",
    "get_agent_stock_pool",
}


def get_tool_catalog() -> list[dict]:
    catalog = []
    for item in AGENT_TOOLS:
        doc = (getattr(item, "description", "") or getattr(item, "__doc__", "") or "").strip()
        first_line = doc.splitlines()[0].strip() if doc else ""
        catalog.append({
            "name": item.name,
            "description": first_line,
            "category": _TOOL_CATEGORIES.get(item.name, "其他"),
            "mandatory": item.name in _MANDATORY_TOOL_NAMES,
        })
    return catalog


def filter_tools_by_names(names: list[str] | None) -> list:
    if not names:
        return AGENT_TOOLS
    allowed = {str(n).strip() for n in names if str(n).strip()}
    allowed.update(_MANDATORY_TOOL_NAMES)
    filtered = [tool_item for tool_item in AGENT_TOOLS if tool_item.name in allowed]
    return filtered or AGENT_TOOLS
