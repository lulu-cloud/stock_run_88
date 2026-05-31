"""模拟 Agent 决策运行器。

默认使用真实 ReAct 工具调用：模型自主选择工具，工具内部通过
trade_date 做时间隔离。单次 LLM 调用仅作为显式 fallback。
"""

import json
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agents.base import AgentContext, AgentDecision
from backend.agents.llm_agent import build_deepseek_chat_openai, run_react_agent_decision
from backend.simulation.sim_tools import create_sim_tools
from backend.data.loader import list_main_board_stocks, load_daily, compute_mas, compute_limit_status
from backend.strategies.registry import StrategyRegistry
from backend.trading.rules import normalize_ts_code


SIM_SYSTEM_PROMPT = """你是一个A股量化交易Agent，负责分析市场、选股并制定交易计划。

下面是系统为你预先准备的数据，请基于这些数据进行分析和决策。

## 交易约束
1. 仅交易60/00开头A股主板股票
2. T+1制度：今日买入的股票明日才可卖出
3. 一字涨停/跌停当日禁止买卖
4. 费率：佣金万0.854双向，印花税万5卖出单向
5. 持仓股票数不超过5只

## 输出要求
分析完成后，在最后用以下JSON格式输出交易计划（必须是合法JSON）：
```json
{
  "market_analysis": "大盘分析",
  "selected_stocks": [{"ts_code": "xxx", "reason": "xxx"}],
  "orders": [
    {"ts_code": "xxx", "stock_name": "xxx", "direction": "buy或sell", "quantity": 100, "price": 0.0, "order_type": "limit", "reason": "交易理由"}
  ],
  "risk_assessment": "风险评估"
}
```

如果没有好的交易机会，orders 可以为空数组 []。
"""


def _fetch_tool_data(trade_date: str, strategy_name: str = "",
                     preloaded_data: dict = None) -> dict:
    """预取工具数据（使用预加载数据加速）"""
    tools = create_sim_tools(trade_date, preloaded_data)
    tool_map = {t.name: t for t in tools}

    data = {}

    # 大盘概况 (快速)
    try:
        data["market_overview"] = tool_map["get_market_overview"].invoke({})
    except Exception as e:
        data["market_overview"] = f"获取失败: {e}"

    # 政策信号 (快速)
    try:
        data["policy_signals"] = tool_map["get_policy_signals"].invoke({})
    except Exception as e:
        data["policy_signals"] = f"获取失败: {e}"

    # 策略选股 (仅运行 agent 绑定的策略)
    all_signals = {}
    strategies_to_run = [strategy_name] if strategy_name else ["ma_pullback", "momentum"]
    for sname in strategies_to_run:
        try:
            result = tool_map["search_stocks_by_strategy"].invoke({
                "strategy_name": sname, "params_json": "{}"
            })
            parsed = json.loads(result)
            if isinstance(parsed, list) and len(parsed) > 0 and "error" not in str(parsed):
                all_signals[sname] = parsed[:10]
        except Exception:
            pass
    data["strategy_signals"] = all_signals

    return data


def _run_sim_agent_review_single_call(
    agent_id: int,
    agent_name: str,
    context: AgentContext,
    strategy_name: str = "ma_pullback",
    preloaded_data: dict = None,
) -> AgentDecision:
    """单次 LLM 调用 fallback。不要作为默认 Agent 决策路径。"""
    tool_data = _fetch_tool_data(context.trade_date, strategy_name, preloaded_data)

    # 组装持仓信息
    positions_str = "\n".join(
        f"  {p['ts_code']} {p.get('stock_name', '')}: "
        f"持仓{p['quantity']}股, 成本{p['avg_cost']:.2f}, 现价{p.get('current_price', 0):.2f}"
        for p in context.positions
    ) if context.positions else "  空仓"

    # 组装策略结果
    signals_str = ""
    for sname, stocks in tool_data.get("strategy_signals", {}).items():
        signals_str += f"\n### {sname} (前{len(stocks)}只):\n"
        for s in stocks[:5]:
            signals_str += f"  {s['ts_code']} {s['name']}: score={s['score']:.0f} {s['reason']}\n"

    # 完整 prompt
    prompt = f"""
## 当前状态
- Agent: {agent_name}
- 交易日期: {context.trade_date}
- 可用资金: {context.cash:.2f}元
- 总资产: {context.total_assets:.2f}元
- 初始本金: {context.initial_capital:.2f}元
- 累计收益率: {context.cumulative_return:.2f}%

## 当前持仓
{positions_str}

## 大盘概况
{tool_data.get("market_overview", "无数据")}

## 宏观政策信号
{tool_data.get("policy_signals", "无数据")}

## 板块热度
{tool_data.get("sector_heat", "无数据")}

## 策略选股结果
{signals_str if signals_str else "暂无策略信号"}

## 任务
请基于以上数据，综合分析市场环境，筛选值得关注的股票，制定交易计划。
在最后输出JSON格式的交易计划。
"""

    # 单次 LLM 调用
    llm = build_deepseek_chat_openai(temperature=0.3, thinking=False)

    try:
        response = llm.invoke([
            SystemMessage(content=SIM_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        output = response.content if hasattr(response, 'content') else str(response)
    except Exception as e:
        return AgentDecision(
            agent_id=agent_id,
            trade_date=context.trade_date,
            analysis=f"[错误] LLM 调用失败: {e}",
            selected_stocks=[],
            orders=[],
            market_analysis="",
            risk_assessment="",
        )

    # 解析交易计划
    trade_plan = _parse_trade_plan(output)
    trade_plan, dropped_orders = _normalize_trade_plan(
        trade_plan, context.trade_date, strategy_name, preloaded_data
    )
    if dropped_orders:
        output += "\n\n[系统拦截]\n" + json.dumps(dropped_orders, ensure_ascii=False)

    return AgentDecision(
        agent_id=agent_id,
        trade_date=context.trade_date,
        analysis=output,
        selected_stocks=trade_plan.get("selected_stocks", []),
        orders=trade_plan.get("orders", []),
        market_analysis=trade_plan.get("market_analysis", ""),
        risk_assessment=trade_plan.get("risk_assessment", ""),
    )


SIM_REACT_PROMPT = """你是一个A股模拟交易Agent。你必须使用可用工具逐步分析，而不是猜测或自行编造数据。

## 交易约束
1. 仅交易60/00开头A股主板股票
2. T+1制度：今日买入的股票明日才可卖出
3. 一字涨停/跌停当日禁止买卖
4. 费率：佣金万0.854双向，印花税万5卖出单向
5. 持仓股票数不超过5只，建议3只左右

## 工作方式
- 你可以根据自己的风格决定调用哪些工具、调用顺序和调用深度。
- 不需要每次都调用全部工具；只调用你认为对决策有帮助的工具。
- 绑定了具体策略时，买入订单只能来自该策略候选股票；政策、板块、公司业务只能作为辅助理由，不能替代策略信号。
- momentum 是短线龙头/连板接力 Agent：重点寻找最近约3个交易日启动、连板加速、高换手合力、所属板块共振的龙头或准龙头。
- momentum 允许快进快出；如果持仓弱于新龙头，可以卖出弱势持仓释放资金追涨更强标的。
- momentum 不做银行、白酒、农业等防御配置，除非它们也出现在 momentum 策略候选中且具备短线启动特征。
- 下单前请自行确认候选来源和个股K线；系统不会预先把全部工具结果灌给你。
- 所有数量、费率、T+1和涨跌停约束以系统撮合为准，不要自行假设成交。

## 输出格式
分析完成后，在最后用以下JSON格式输出交易计划：
```json
{
  "market_analysis": "大盘分析",
  "selected_stocks": [{"ts_code": "xxx", "reason": "xxx"}],
  "orders": [
    {"ts_code": "xxx", "stock_name": "xxx", "direction": "buy/sell", "quantity": 100, "price": 0.0, "order_type": "limit", "reason": "xxx"}
  ],
  "risk_assessment": "风险评估"
}
```
没有机会时 orders 输出 []。
"""


@lru_cache(maxsize=1)
def _stock_name_map() -> dict[str, str]:
    try:
        stocks = list_main_board_stocks()
        return {row["ts_code"]: row["name"] for _, row in stocks.iterrows()}
    except Exception:
        return {}


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _strategy_candidates_at(trade_date: str, strategy_name: str, preloaded_data: dict = None) -> dict[str, dict]:
    """Collect strategy candidates for order validation only; not injected into prompt."""
    strategy = StrategyRegistry.create(strategy_name)
    if strategy is None:
        return {}
    candidates: dict[str, dict] = {}
    if preloaded_data:
        iterable = preloaded_data.items()
    else:
        iterable = [(row["ts_code"], {"name": row["name"], "df": None}) for _, row in list_main_board_stocks().iterrows()]
    for ts_code, data in iterable:
        name = data.get("name", ts_code)
        df = data.get("df")
        if df is None:
            df = load_daily(ts_code)
            if df is None:
                continue
            df = compute_limit_status(compute_mas(df))
        df_upto = df[df["trade_date"] <= trade_date]
        if len(df_upto) < 30:
            continue
        try:
            result = strategy.filter(ts_code, name, df_upto)
        except Exception:
            continue
        if result:
            candidates[result.ts_code] = _json_safe({
                "ts_code": result.ts_code,
                "name": result.name,
                "reason": result.reason,
                "score": result.score,
                "extra": result.extra,
            })
    return dict(sorted(candidates.items(), key=lambda item: item[1]["score"], reverse=True))


def _normalize_trade_plan(trade_plan: dict, trade_date: str, strategy_name: str,
                          preloaded_data: dict = None) -> tuple[dict, list[dict]]:
    """Fill stock names and enforce strategy-bound buy orders."""
    names = _stock_name_map()
    strict_strategy = bool(strategy_name and StrategyRegistry.create(strategy_name) is not None)
    candidates = _strategy_candidates_at(trade_date, strategy_name, preloaded_data) if strict_strategy else {}
    dropped = []

    selected = trade_plan.get("selected_stocks") or []
    if candidates:
        selected = list(candidates.values())[:10]

    clean_orders = []
    for order in trade_plan.get("orders", []) or []:
        ts_code = normalize_ts_code(order.get("ts_code", ""))
        direction = str(order.get("direction", "buy")).lower()
        if direction == "买入":
            direction = "buy"
        elif direction == "卖出":
            direction = "sell"

        if direction == "buy" and strict_strategy and ts_code not in candidates:
            dropped.append({
                "ts_code": ts_code,
                "reason": f"非 {strategy_name} 策略候选，已拦截",
                "original": order,
            })
            continue

        stock_name = order.get("stock_name") or names.get(ts_code) or candidates.get(ts_code, {}).get("name") or ts_code
        reason = order.get("reason", "")
        if stock_name and stock_name not in reason:
            reason = f"{stock_name}: {reason}" if reason else stock_name

        clean_orders.append({
            **order,
            "ts_code": ts_code,
            "stock_name": stock_name,
            "direction": direction,
            "quantity": int(order.get("quantity") or 100),
            "price": float(order.get("price") or candidates.get(ts_code, {}).get("extra", {}).get("close") or 0),
            "order_type": order.get("order_type", "limit"),
            "reason": reason,
        })

    trade_plan["selected_stocks"] = selected
    trade_plan["orders"] = clean_orders
    return trade_plan, dropped


def run_sim_agent_review(
    agent_id: int,
    agent_name: str,
    context: AgentContext,
    strategy_name: str = "ma_pullback",
    preloaded_data: dict = None,
    use_fallback_on_error: bool = False,
    log_path: str = "",
) -> AgentDecision:
    """运行模拟 Agent 决策 — 默认真实 ReAct 工具调用。"""
    positions_str = "\n".join(
        f"  {p['ts_code']} {p.get('stock_name', '')}: "
        f"持仓{p['quantity']}股, 成本{p['avg_cost']:.2f}, 现价{p.get('current_price', 0):.2f}"
        for p in context.positions
    ) if context.positions else "  空仓"
    trades_str = "\n".join(
        f"  {t.get('trade_date') or t.get('date', '')} {t.get('direction', '')} "
        f"{t.get('ts_code', '')} {t.get('stock_name') or t.get('name', '')} "
        f"{t.get('quantity', 0)}股 @ {float(t.get('price', 0) or 0):.2f}"
        for t in (context.recent_trades or [])
    ) if context.recent_trades else "  上个交易日无成交"

    prompt = f"""
## 当前状态
- Agent: {agent_name} (ID: {agent_id})
- 绑定策略: {strategy_name or '自主决策'}
- 交易日期: {context.trade_date}
- 可用资金: {context.cash:.2f}元
- 总资产: {context.total_assets:.2f}元
- 初始本金: {context.initial_capital:.2f}元
- 累计收益率: {context.cumulative_return:.2f}%

## 当前持仓
{positions_str}

## 上个交易日撮合结果
{trades_str}

## 任务
请按你的Agent风格自主调用工具，分析当前模拟日期可见的数据，制定下一步交易计划。
如果你要做策略筛选，优先考虑绑定策略 `{strategy_name}`，但可以根据市场情况补充其他工具。
"""

    llm = build_deepseek_chat_openai(temperature=0.3, thinking=False)
    tools = create_sim_tools(context.trade_date, preloaded_data)

    try:
        output, tool_trace = run_react_agent_decision(
            llm, tools, SIM_REACT_PROMPT, prompt, log_path=log_path
        )
        trade_plan = _parse_trade_plan(output)
        trade_plan, dropped_orders = _normalize_trade_plan(
            trade_plan, context.trade_date, strategy_name, preloaded_data
        )
        if dropped_orders:
            output += "\n\n[系统拦截]\n" + json.dumps(dropped_orders, ensure_ascii=False)
        return AgentDecision(
            agent_id=agent_id,
            trade_date=context.trade_date,
            analysis=output,
            selected_stocks=trade_plan.get("selected_stocks", []),
            orders=trade_plan.get("orders", []),
            market_analysis=trade_plan.get("market_analysis", ""),
            risk_assessment=trade_plan.get("risk_assessment", ""),
            tool_trace=tool_trace,
        )
    except Exception as e:
        if not use_fallback_on_error:
            raise
        fallback = _run_sim_agent_review_single_call(
            agent_id, agent_name, context, strategy_name, preloaded_data
        )
        fallback.analysis = f"[ReAct失败，已回退单次调用] {e}\n\n{fallback.analysis}"
        fallback.tool_trace = [{"error": str(e), "fallback": "single_call"}]
        return fallback


def _parse_trade_plan(output: str) -> dict:
    """从输出中解析交易计划 JSON"""
    try:
        if "```json" in output:
            start = output.index("```json") + 7
            end = output.index("```", start)
            return json.loads(output[start:end].strip())
        if "```" in output:
            start = output.index("```") + 3
            end = output.index("```", start)
            return json.loads(output[start:end].strip())
    except (ValueError, json.JSONDecodeError):
        pass
    return {}
