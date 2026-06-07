"""LLM 驱动 Agent — DeepSeek v4 pro

使用手动 ReAct 循环替代 LangChain create_agent，
以正确处理 DeepSeek 的 reasoning_content 多轮回传。
"""

import json
import os
import re
from langchain_openai import ChatOpenAI
from langchain_core.tools import StructuredTool

from backend.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from backend.agents.tools import AGENT_TOOLS
from backend.agents.base import AgentContext, AgentDecision
from backend.agents.react_loop import ReActLoop
from backend.data.loader import load_daily
from backend.trading.rules import normalize_ts_code
from backend.macro.report import get_macro_daily_report_text

MAX_TOOL_TURNS = int(os.environ.get("AGENT_MAX_TOOL_TURNS", "8"))


def _build_llm() -> ChatOpenAI:
    """构建 DeepSeek ChatOpenAI 实例"""
    return build_deepseek_chat_openai()


def build_deepseek_chat_openai(
    temperature: float = 0.3,
    thinking: bool = False,
    reasoning_effort: str = "high",
) -> ChatOpenAI:
    """构建 DeepSeek ChatOpenAI。

    DeepSeek V4 thinking mode requires `reasoning_content` to be replayed after
    tool calls. LangChain does not reliably preserve that provider field, so
    agent tool loops use non-thinking mode by default while keeping real
    multi-turn function calling.
    """
    extra_body = {"thinking": {"type": "enabled" if thinking else "disabled"}}
    kwargs = {
        "model": DEEPSEEK_MODEL,
        "api_key": DEEPSEEK_API_KEY,
        "base_url": DEEPSEEK_BASE_URL,
        "temperature": temperature,
        "extra_body": extra_body,
        "timeout": int(os.environ.get("LLM_REQUEST_TIMEOUT", "90")),
    }
    if thinking:
        kwargs["reasoning_effort"] = reasoning_effort
    return ChatOpenAI(**kwargs)


AGENT_SYSTEM_PROMPT = """你是一个A股量化交易Agent，负责分析市场、选股并制定交易计划。

## 你的能力
你可以使用以下工具：
- search_stocks_by_strategy: 使用内置策略筛选股票
- search_stocks_by_strategy_combo: 多策略加权选股，如动量 × 0.4 + 多头均线回踩 × 0.6
- get_stock_kline: 获取个股K线数据
- get_multi_period_trend: 获取日线/周线/月线/60分钟趋势背景
- get_market_overview: 获取大盘指数概况
- get_company_business: 获取公司主营业务信息
- compute_sector_heat_tool: 获取板块热度排行
- get_market_strength_sectors: 获取近3日价格行为强势/弱势板块
- get_market_breadth: 获取全市场涨跌宽度、涨停/大涨/跌停/大跌分布和 risk-on 分数
- get_sector_temperature: 获取当日板块温度、热门板块、风险板块和领涨股
- suggest_adaptive_strategy_params: 根据市场状态建议策略参数
- detect_strategy_crowding: 检测多个 Agent 是否拥挤在同一批股票/技能/板块
- get_agent_signal_committee: 读取多 Agent 共享研判和订单信号，形成投委会式综合意见
- get_global_position_exposure: 查看所有交易员的全局个股/板块仓位暴露
- get_macro_daily_report: 获取每日宏观市场报告（大盘、板块温度、龙虎榜、涨停池、政策、基本面事件）
- get_limit_up_board_quality: 获取涨停板质量（封板资金、首封/末封、炸板次数、首板/连板）
- get_limit_up_promotion_stats: 获取昨日涨停晋级/炸板/闷杀统计
- get_policy_signals: 获取近期国家宏观政策信号
- get_shared_stock_report: 读取推荐助手沉淀的用户关注股共享研究报告
- get_agent_stock_pool: 读取当前 Agent 的前端配置股票池
- search_stocks_in_agent_pool: 只在当前 Agent 的股票池内执行策略筛选
- calculate_price_by_pct: 用参考价格和涨跌幅计算目标挂单价
- validate_order_price_limit: 校验挂单价是否落在参考价 ±10% 涨跌停范围内
- calculate_position_size: 按总资产、止损距离、风险预算估算建议买入股数
- get_recent_order_history: 查询最近挂单、成交、过期和失败原因
- get_portfolio_risk_metrics: 查询当前组合集中度、VaR、行业暴露和持仓风险
- get_correlation_info: 估算候选股票之间以及与持仓之间的近 N 日相关性
- get_evolution_context: 查询进化记忆、技能索引和上次进化结果
- get_skill_params: 查询指定进化技能的完整参数
- get_strategy_param_schema: 查询选股策略可自定义敏感参数
- place_order_draft: 在 ReAct 循环内创建订单草稿
- cancel_order_draft: 在 ReAct 循环内撤销订单草稿
- list_order_drafts: 查看当前订单草稿队列

## 交易约束（严格遵守）
1. 只能买入当前 Agent 配置允许的交易板块；未开启创业板/科创板/北交所权限时不得买入对应股票
2. 若 Agent 启用前端股票池约束且未允许池外探索，买入订单只能选择股票池内标的；卖出现有持仓不受股票池限制
3. 若允许池外探索，买入池外股票必须在 reason 中明确说明脱离股票池的理由
4. T+1制度：今日买入的股票明日才可卖出
5. 一字涨停/跌停当日禁止买卖
6. 费率：佣金万0.854双向，印花税万5卖出单向
7. 初始资金150,000元
8. 持仓股票数不超过5只，建议3只左右

## 工作方式
- 你是 ReAct 风格 Agent，自主决定调用哪些工具、调用顺序和调用深度。
- 工具调用必须克制：先看大盘/市场宽度/板块温度/政策，再筛选候选，只深入 2-4 只股票；系统会强制把最后几轮限制为挂单价计算、价格校验和订单草稿工具。
- 每日决策第一步应优先调用 get_macro_daily_report 读取公共宏观报告；只有报告缺失、partial 或需要深挖时，再调用原始大盘/板块/政策工具。
- 追高打板、强势接力或右侧突破买入前，必须结合 get_limit_up_board_quality 或 get_limit_up_promotion_stats 判断板质量与晋级率；不要只因为涨停或涨幅高就买入。
- 不要机械调用全部工具；只调用对本次交易判断有价值的工具。
- 下单必须能追溯到工具返回的数据；不要自行编造行情、板块、业务或政策信息。
- 不要默认用今日收盘价作为明日挂单价。必须先判断明日买入/卖出的预期涨跌幅或价格区间，再调用 calculate_price_by_pct 计算挂单价。
- 买入前建议调用 calculate_position_size 估算股数；理由中说明止损价、单笔风险预算和单票仓位上限。
- 最终输出 orders 前，必须对每一笔订单调用 validate_order_price_limit 校验，挂单价必须在参考收盘价 ±10% 内。
- 支持 order_type=limit/stop_loss/stop_profit/condition；stop_loss/stop_profit/condition 可设置 trigger_price。
- 支持 OCO：同一持仓的止盈和止损可以设置相同 oco_group，触发一个后系统自动取消同组其他 pending 单。
- 支持追价：chase_enabled=true 且 chase_pct>0 时，限价未触达可在当日区间内尝试一次追价成交。
- 支持分批：split_total>1 时系统会拆成多笔 split_seq 订单，必须每笔至少100股。
- 推荐使用 place_order_draft/cancel_order_draft 管理订单草稿；如果你调用了订单草稿工具，系统会优先采用草稿队列入库，最终 JSON 只需保持一致并解释逻辑。
- 追高打板风格尤其要考虑高开场景：若预测强势股明日高开 2%-8%，限价买单应按预期可成交价计算，而不是简单等于前收盘价。
- 若近期订单过期或未成交，必须调用 get_recent_order_history 参考失败价格与原因，重新选择更合理的涨跌幅。
- 打板不是唯一进攻方式：追高打板风格也必须把“多头均线发散 + 回踩 MA5/10/20 + 右侧趋势确认”纳入候选；优先选好板、好趋势、热点共振的票，不要机械追逐所有涨停。
- 买入前应参考 get_market_breadth 判断 risk-on/neutral/risk-off：risk-on 可更积极，neutral 控制节奏，risk-off 轻仓或空仓；仓位理由必须写清楚。
- 买入前应调用 get_global_position_exposure 或 get_agent_signal_committee 观察全局组合是否已在同一板块/标的上过度集中；若存在拥挤，必须降低仓位或解释为什么仍值得交易。
- 选股后应调用 get_sector_temperature 或 get_market_strength_sectors 做二次筛选，说明候选是否属于当前热板块，是否存在板块退潮风险。
- 可用 search_stocks_by_strategy_combo 做策略组合，而不是只跑单一策略；追高打板 Agent 至少考虑 momentum 与 ma_bullish_pullback 的组合，避免只盯涨停。
- 可调用 suggest_adaptive_strategy_params 在 risk-on/risk-off 不同环境下调整策略参数；参数建议不是硬规则，最终仍要结合工具证据。
- 候选进入下单前建议调用 get_multi_period_trend 确认日线/周线/月线背景，短线入场再看 60分钟趋势。
- 多 Agent 同时命中同类标的时，调用 detect_strategy_crowding 判断是否策略拥挤，避免总组合回撤相关性过高。
- 用户或推荐助手关注过的标的，可以调用 get_shared_stock_report 读取共享研究，但不能替代你自己的行情和风控判断。
- 若配置中存在“用户原始交易策略”，它是风格锚点；进化记忆只能补充执行细节，不能覆盖原始策略。
- 若股票池约束开启，应优先调用 get_agent_stock_pool 和 search_stocks_in_agent_pool；除非 allow_out_of_pool=true，否则不要计划池外买入。
- 决策前应参考进化上下文中的 trade_fact/trade_prefer 和技能置信度；低置信度技能只能轻仓或不用。
- 决策前应调用 get_portfolio_risk_metrics 观察集中度、行业暴露和组合风险；多标的买入前应调用 get_correlation_info，避免看似分散但高度相关。
- 若同伴 Agent 已输出共享研判，可参考但不要盲从；若观点冲突，必须在风险评估中说明你的差异化理由。
- search_stocks_by_strategy 支持 params_json 自定义敏感值；例如 momentum 可调整 lookback_days、min_limit_up_days 等。必要时先调用 get_strategy_param_schema。
- 若希望集合竞价/开盘直接冲入或卖出，可在订单中设置 "open_get_in": true；其语义是买单开盘价<=限价则按开盘价成交，否则继续普通限价撮合；卖单开盘价>=限价则按开盘价成交，否则继续普通限价撮合。
- A股换仓不是原子操作：若计划卖出A再用资金买入B，必须在 reason 中说明顺序风险；如果买入条件先触达而卖出未成交，买入可能失败。
- 如果 Agent 是“自主决策Agent”，可以追强势股，但不要简单复制追高打板逻辑；需给出更综合的政策/基本面/技术/仓位理由，且优先控制集中度。
- 若用户或 Agent 配置存在偏好，应按偏好筛选和解释推荐理由。

## 输出格式
分析完成后，在最后用以下JSON格式输出交易计划：
```json
{
  "market_analysis": "大盘分析",
  "selected_stocks": [{"ts_code": "xxx", "reason": "xxx"}],
  "orders": [
    {"ts_code": "xxx", "stock_name": "xxx", "direction": "buy/sell", "quantity": 100, "price": 0.0, "trigger_price": 0.0, "order_type": "limit", "open_get_in": false, "oco_group": "", "chase_enabled": false, "chase_pct": 0.0, "split_total": 1, "skill_id": "momentum_hunt", "skill_confidence": 0.66, "evolution_mark": "#情绪回暖#", "reason": "xxx"}
  ],
  "risk_assessment": "风险评估"
}
```
"""


def _make_order_draft_tools(state: dict) -> list:
    state.setdefault("order_drafts", [])

    def place_order_draft(
        ts_code: str,
        stock_name: str = "",
        direction: str = "buy",
        quantity: int = 100,
        price: float = 0.0,
        trigger_price: float = 0.0,
        order_type: str = "limit",
        open_get_in: bool = False,
        oco_group: str = "",
        chase_enabled: bool = False,
        chase_pct: float = 0.0,
        split_total: int = 1,
        condition_expr: str = "",
        skill_id: str = "",
        skill_confidence: float = 0.0,
        evolution_mark: str = "",
        reason: str = "",
    ) -> str:
        """创建一笔订单草稿，供 ReAct 循环内动态调整。"""
        code = normalize_ts_code(ts_code)
        side = str(direction or "").lower()
        if side not in {"buy", "sell"}:
            return json.dumps({"ok": False, "error": "direction must be buy or sell"}, ensure_ascii=False)
        try:
            qty = int(quantity or 0)
            px = round(float(price or 0), 2)
        except Exception:
            return json.dumps({"ok": False, "error": "quantity/price parse failed"}, ensure_ascii=False)
        if qty <= 0 or px <= 0:
            return json.dumps({"ok": False, "error": "quantity and price must be positive"}, ensure_ascii=False)
        split_n = max(1, min(10, int(split_total or 1)))
        drafts = state.setdefault("order_drafts", [])
        draft = {
            "draft_id": len(drafts) + 1,
            "ts_code": code,
            "stock_name": stock_name or "",
            "direction": side,
            "quantity": qty,
            "price": px,
            "trigger_price": round(float(trigger_price or 0), 2) if trigger_price else 0.0,
            "order_type": order_type or "limit",
            "condition_expr": condition_expr or "",
            "open_get_in": bool(open_get_in),
            "oco_group": oco_group or "",
            "chase_enabled": bool(chase_enabled),
            "chase_pct": float(chase_pct or 0),
            "split_total": split_n,
            "skill_id": skill_id or "",
            "skill_confidence": float(skill_confidence or 0),
            "evolution_mark": evolution_mark or "",
            "reason": reason or "",
        }
        drafts.append(draft)
        return json.dumps({"ok": True, "draft": draft, "draft_count": len(drafts)}, ensure_ascii=False, default=str)

    def cancel_order_draft(draft_id: int = 0, ts_code: str = "", reason: str = "") -> str:
        """撤销订单草稿；优先按 draft_id，未传时按 ts_code。"""
        drafts = state.setdefault("order_drafts", [])
        code = normalize_ts_code(ts_code) if ts_code else ""
        removed = []
        kept = []
        for draft in drafts:
            match_id = draft_id and int(draft.get("draft_id") or 0) == int(draft_id)
            match_code = code and draft.get("ts_code") == code
            if match_id or match_code:
                removed.append(draft)
            else:
                kept.append(draft)
        state["order_drafts"] = kept
        return json.dumps({
            "ok": True,
            "removed": removed,
            "remaining_count": len(kept),
            "reason": reason or "",
        }, ensure_ascii=False, default=str)

    def list_order_drafts() -> str:
        """查看当前 ReAct 会话的订单草稿队列。"""
        return json.dumps({"order_drafts": state.get("order_drafts", [])}, ensure_ascii=False, default=str)

    return [
        StructuredTool.from_function(place_order_draft, name="place_order_draft"),
        StructuredTool.from_function(cancel_order_draft, name="cancel_order_draft"),
        StructuredTool.from_function(list_order_drafts, name="list_order_drafts"),
    ]


def _draft_trade_plan(output: str, state: dict) -> dict:
    parsed = _parse_trade_plan(output)
    orders = []
    for draft in state.get("order_drafts", []) or []:
        order = dict(draft)
        order.pop("draft_id", None)
        orders.append(order)
    if not orders:
        return parsed
    return {
        "market_analysis": parsed.get("market_analysis", ""),
        "selected_stocks": parsed.get("selected_stocks", []),
        "orders": orders,
        "risk_assessment": parsed.get("risk_assessment", ""),
    }


def _trade_stage_config(max_turns: int, tools: list) -> list[dict]:
    names = {getattr(t, "name", "") for t in tools}
    query_tools = {
        "get_market_overview",
        "compute_sector_heat_tool",
        "get_market_strength_sectors",
        "get_market_breadth",
        "get_sector_temperature",
        "get_macro_daily_report",
        "get_limit_up_board_quality",
        "get_limit_up_promotion_stats",
        "get_policy_signals",
        "search_stocks_by_strategy",
        "search_stocks_by_strategy_combo",
        "get_agent_stock_pool",
        "search_stocks_in_agent_pool",
        "search_stocks_in_agent_pool_combo",
        "get_recent_order_history",
        "get_evolution_context",
        "get_strategy_param_schema",
        "suggest_adaptive_strategy_params",
        "get_agent_signal_committee",
        "get_global_position_exposure",
        "detect_strategy_crowding",
    }
    deep_tools = query_tools | {
        "get_stock_kline",
        "get_multi_period_trend",
        "get_company_business",
        "get_stock_analysis_report",
        "get_shared_stock_report",
        "get_agent_performance",
        "get_simulation_performance",
        "get_portfolio_risk_metrics",
        "get_correlation_info",
        "get_skill_params",
        "calculate_position_size",
    }
    final_tools = {
        "calculate_price_by_pct",
        "validate_order_price_limit",
        "calculate_position_size",
        "get_recent_order_history",
        "place_order_draft",
        "cancel_order_draft",
        "list_order_drafts",
    }
    turns = max(1, int(max_turns or MAX_TOOL_TURNS))
    if turns <= 3:
        return [{"start": 0, "end": turns, "tools": sorted(names & (deep_tools | final_tools))}]

    # Stage boundaries are based on the intended workflow, not on the configured
    # max-turn ceiling. If max_turns is 30/50, order tools still need to become
    # available early enough for the model to validate prices instead of looping.
    scan_end = 1
    final_start = min(turns, 5)
    final_stage_tools = deep_tools | final_tools
    config = [
        {"start": 0, "end": scan_end, "tools": sorted(names & query_tools)},
        {"start": scan_end, "end": final_start, "tools": sorted(names & deep_tools)},
        {"start": final_start, "end": turns, "tools": sorted(names & final_stage_tools)},
    ]
    return [stage for stage in config if stage["start"] < stage["end"]]


def run_react_agent_decision(
    llm: ChatOpenAI,
    tools: list,
    system_prompt: str,
    user_input: str,
    max_tool_turns: int = MAX_TOOL_TURNS,
    log_path: str = "",
    reset_log: bool = True,
    stage_config: list[dict] | None = None,
    state: dict | None = None,
) -> tuple[str, list[dict]]:
    """兼容旧调用签名的共享 ReActLoop 包装。"""
    result = ReActLoop(llm, tools, metadata={"agent_loop": "trading"}).run(
        system_prompt,
        user_input,
        max_turns=max_tool_turns,
        log_path=log_path,
        reset_log=reset_log,
        stage_config=stage_config,
        state=state,
        final_instruction=(
            "工具调用轮次已用完。请停止调用工具，基于已有数据直接输出最终交易计划。"
            "输出前必须说明每笔订单的预测涨跌幅、计算后的挂单价和 ±10% 校验结果，"
            "最后必须给出符合要求的 JSON。"
        ),
    )
    return result.output, result.trace


def _run_react_loop(llm: ChatOpenAI, tools: list, system_prompt: str,
                    user_input: str) -> str:
    output, _ = run_react_agent_decision(llm, tools, system_prompt, user_input)
    return output


def run_agent_review(
    agent_id: int,
    agent_name: str,
    context: AgentContext,
    thinking_log_path: str = "",
    reasoning_effort: str = "high",
    max_tool_turns: int = MAX_TOOL_TURNS,
    tools: list = None,
) -> AgentDecision:
    """运行 Agent 每日复盘与分析

    Args:
        reasoning_effort: 推理深度，"high" 或 "max" (目前未使用，预留)
        tools: 自定义工具列表，为 None 时使用默认 AGENT_TOOLS
    """
    llm = _build_llm()
    loop_state = {"order_drafts": []}
    effective_tools = list(tools if tools is not None else AGENT_TOOLS)
    existing_tool_names = {getattr(t, "name", "") for t in effective_tools}
    effective_tools.extend(t for t in _make_order_draft_tools(loop_state) if t.name not in existing_tool_names)

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
    orders_str = "\n".join(
        f"  {o.get('trade_date', '')} #{o.get('id')} {o.get('status')} {o.get('direction')} "
        f"{o.get('ts_code', '')} {o.get('stock_name', '')} {o.get('quantity', 0)}股 "
        f"@ {float(o.get('price', 0) or 0):.2f} open_get_in={bool(o.get('open_get_in'))} "
        f"batch={o.get('decision_batch_id') or '-'} "
        f"fill_prob={o.get('fill_probability') if o.get('fill_probability') is not None else '-'}% "
        f"aggr={o.get('price_aggressiveness') if o.get('price_aggressiveness') is not None else '-'}% "
        f"skill={o.get('skill_id') or '-'} 原因: {o.get('fail_reason') or '-'}"
        for o in (context.recent_orders or [])[:12]
    ) if context.recent_orders else "  暂无近期挂单记录"
    failed_orders = [
        o for o in (context.recent_orders or [])
        if o.get("status") in ("expired", "cancelled") or o.get("fail_reason")
    ][:8]
    failed_orders_str = "\n".join(
        f"  - {o.get('trade_date', '')} {o.get('direction')} {o.get('ts_code', '')} "
        f"@ {float(o.get('price', 0) or 0):.2f}: {o.get('fail_reason') or o.get('status')}; "
        f"上次成交概率估计={o.get('fill_probability') if o.get('fill_probability') is not None else '-'}%, "
        f"价格偏离={o.get('price_aggressiveness') if o.get('price_aggressiveness') is not None else '-'}%"
        for o in failed_orders
    ) if failed_orders else "  暂无明确失败挂单"
    try:
        from backend.evolution.engine import format_evolution_prompt
        evolution_str = format_evolution_prompt(context.evolution_context)
    except Exception as exc:
        evolution_str = f"进化上下文加载失败: {exc}"
    config = (context.evolution_context or {}).get("agent_config") or {}
    allowed_tool_names = [getattr(t, "name", "") for t in effective_tools]
    preferred_strategies = config.get("preferred_strategies") or []
    stage_prompts = config.get("stage_prompts") or {}
    board_permissions = config.get("board_permissions") or {}
    style_prompt = (config.get("style_prompt") or "").strip()
    user_strategy = (config.get("user_strategy_original") or "").strip()
    stock_pool = config.get("stock_pool") or []
    stock_pool_enabled = bool(config.get("stock_pool_enabled"))
    allow_out_of_pool = bool(config.get("allow_out_of_pool"))
    stock_pool_str = "\n".join(
        f"  - {item.get('ts_code')} {item.get('stock_name') or ''}"
        f"{'：' + item.get('note') if item.get('note') else ''}"
        for item in stock_pool
    ) if stock_pool else "  未配置股票池"
    stage_prompt_str = "\n".join(
        f"- {key}: {value}" for key, value in stage_prompts.items() if str(value).strip()
    ) or "  未配置"
    try:
        macro_report_text = get_macro_daily_report_text(context.trade_date)
        if len(macro_report_text) > 6000:
            macro_report_text = macro_report_text[:6000] + "\n...[宏观报告过长，已截断；如需细节请调用 get_macro_daily_report 或 get_macro_market_topic]"
    except Exception as exc:
        macro_report_text = f"宏观报告读取失败: {exc}"

    input_text = f"""
## 当前状态
- Agent: {agent_name} (ID: {agent_id})
- 交易日期: {context.trade_date}
- 可用资金: {context.cash:.2f}元
- 冻结资金: {context.frozen_cash:.2f}元
- 总资产: {context.total_assets:.2f}元
- 初始本金: {context.initial_capital:.2f}元
- 累计收益率: {context.cumulative_return:.2f}%

## 当前持仓
{positions_str}

## 上个交易日撮合结果
{trades_str}

## 近期挂单状态
{orders_str}

## 近期失败反哺
{failed_orders_str}

## 当前Agent配置
- Agent风格模板: {config.get("agent_type") or "custom"}
- 风格提示词: {style_prompt or "未配置"}
- 用户原始交易策略: {user_strategy or "未配置"}
- 优先选股策略: {", ".join(preferred_strategies) if preferred_strategies else "未配置"}
- 可用工具白名单: {", ".join(allowed_tool_names)}
- 买入板块权限: {json.dumps(board_permissions, ensure_ascii=False)}
- 股票池约束: {"启用" if stock_pool_enabled else "未启用"}；池外探索: {"允许" if allow_out_of_pool else "不允许"}
- 前端配置股票池:
{stock_pool_str}
- 赛马指标只作为复盘和风险提示，系统不会因盈亏强制限制你的仓位；你需要像真实交易员一样自主解释仓位选择。

## 阶段提示词
{stage_prompt_str}

## 当日公共宏观报告（系统硬注入）
{macro_report_text}

## 多Agent共享研判
{config.get("peer_shared_context") or "暂无同伴 Agent 当日共享研判。"}

{evolution_str}

## 任务
请按你的Agent风格自主调用工具，分析市场、持仓与候选机会，制定明天的条件单计划。
若近期同一股票或同类策略出现未触达、替换、资金不足、T+1限制等失败记录，必须在下单理由中说明你如何调整价格、open_get_in、仓位或放弃该方向。
"""

    output, tool_trace = run_react_agent_decision(
        llm, effective_tools, AGENT_SYSTEM_PROMPT, input_text,
        max_tool_turns=max_tool_turns,
        log_path=thinking_log_path,
        stage_config=_trade_stage_config(max_tool_turns, effective_tools),
        state=loop_state,
    )
    trade_plan = _draft_trade_plan(output, loop_state)
    invalid_orders = _validate_trade_plan_orders(trade_plan)
    if invalid_orders:
        repair_state = {"order_drafts": []}
        repair_tools = list(tools if tools is not None else AGENT_TOOLS)
        repair_tool_names = {getattr(t, "name", "") for t in repair_tools}
        repair_tools.extend(t for t in _make_order_draft_tools(repair_state) if t.name not in repair_tool_names)
        repair_input = f"""
上一轮交易计划存在非法挂单价，不能入库。

## 原始输出
{output[-8000:]}

## 校验失败
{json.dumps(invalid_orders, ensure_ascii=False)}

## 修复要求
请仅基于上一轮已有分析修复 orders：
1. 对每笔订单先说明预测涨跌幅。
2. 调用 calculate_price_by_pct 计算新的挂单价。
3. 调用 validate_order_price_limit 校验新挂单价。
4. 最后输出完整 JSON，orders 中只能保留校验通过的订单。
"""
        repaired_output, repair_trace = run_react_agent_decision(
            llm, repair_tools, AGENT_SYSTEM_PROMPT, repair_input,
            max_tool_turns=3,
            log_path=thinking_log_path,
            reset_log=False,
            stage_config=_trade_stage_config(3, repair_tools),
            state=repair_state,
        )
        repaired_plan = _draft_trade_plan(repaired_output, repair_state)
        if repaired_plan and not _validate_trade_plan_orders(repaired_plan):
            output = output + "\n\n" + repaired_output
            tool_trace.extend(repair_trace)
            trade_plan = repaired_plan

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


def _parse_trade_plan(output: str) -> dict:
    """从 Agent 输出中解析交易计划 JSON"""
    candidates = []
    candidates.extend(re.findall(r"```json\s*(\{.*?\})\s*```", output, flags=re.S | re.I))
    candidates.extend(re.findall(r"```\s*(\{.*?\})\s*```", output, flags=re.S))
    start = output.rfind("{")
    while start >= 0:
        snippet = output[start:].strip()
        end = snippet.rfind("}")
        if end >= 0:
            candidates.append(snippet[:end + 1])
        start = output.rfind("{", 0, start)

    for text in reversed(candidates):
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "orders" in data:
            return data
    return {}


def _latest_close(ts_code: str) -> float:
    df = load_daily(normalize_ts_code(ts_code))
    if df is None or df.empty:
        return 0.0
    return float(df.iloc[-1]["close"] or 0)


def _validate_trade_plan_orders(trade_plan: dict) -> list[dict]:
    invalid = []
    for order in trade_plan.get("orders", []) if isinstance(trade_plan, dict) else []:
        ts_code = order.get("ts_code", "")
        price = float(order.get("price") or 0)
        close = _latest_close(ts_code)
        if close <= 0 or price <= 0:
            invalid.append({"order": order, "error": "缺少行情或挂单价"})
            continue
        lower = round(close * 0.9, 2)
        upper = round(close * 1.1, 2)
        if not (lower <= price <= upper):
            invalid.append({
                "order": order,
                "latest_close": round(close, 2),
                "lower_limit": lower,
                "upper_limit": upper,
                "error": "挂单价超出参考收盘价 ±10%",
            })
    return invalid
