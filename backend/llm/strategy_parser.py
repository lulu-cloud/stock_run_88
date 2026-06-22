"""自然语言策略解析引擎

将用户自然语言描述解析为策略筛选条件，调用对应策略函数执行选股。
"""

import pandas as pd
from typing import Optional
from backend.llm.client import chat
from backend.llm.json_repair import extract_json_object
import backend.strategies  # noqa: F401 - import registers built-in strategies
from backend.strategies.registry import StrategyRegistry
from backend.data.loader import load_daily, compute_mas, compute_limit_status, list_main_board_stocks


STRATEGY_PARSER_PROMPT = """你是一个A股量化策略解析器。用户会用自然语言描述选股条件，你需要将其解析为结构化的策略调用。

可用的内置策略：
1. momentum: 龙头打板战法 - 追踪涨停龙头，分析连板阶段和换手率变化
   参数: min_limit_up_days(最小连板天数,默认2), lookback_days(回溯天数,默认15), healthy_turnover_min(健康换手率下限%,默认3.0), healthy_turnover_max(健康换手率上限%,默认25.0)

2. trend: 动量趋势策略 - 识别多波趋势上涨行情，捕捉健康回调后的二次启动点
   参数: lookback_days(回溯天数,默认60), min_waves(最小波次数,默认2), max_pullback_pct(最大回调%,默认15.0)

3. ma_pullback: 20/60均线回调企稳 - 股价回调至关键均线附近，缩量企稳后放量反弹
   参数: pullback_within_pct(均线偏离%,默认5.0), volume_shrink_ratio(缩量比例,默认0.5)

4. ma_bullish: 均线多头发散向上 - MA5>MA10>MA20>MA30 且四条均线同步上行
   参数: slope_lookback(均线上行检查天数,默认5), min_spread_pct(最小发散幅度%,默认1.5), min_spread_expand_pct(发散扩大幅度%,默认0.2), max_deviation_pct(相对MA30最大偏离%,默认25.0)

5. ma_bullish_pullback: 多头均线发散回踩 - MA5>MA10>MA20>MA30 且回踩 MA5/10/20 附近
   参数: ma_periods(回踩均线列表,默认[5,10,20]), pullback_within_pct(偏离%,默认3.0), slope_lookback(均线上行检查天数,默认5)

你需要判断用户意图，选择合适的策略和参数。如果用户的描述无法匹配任何内置策略，返回 use_custom=true 并提供筛选条件描述。

重要：max_results 参数表示用户期望的结果数量。如果用户说"最猛的5只"则 max_results=5，"涨停最多的10只"则 max_results=10。默认 max_results=20。如果用户期望更多结果，策略参数应适当放宽（如降低 min_limit_up_days）以覆盖足够候选。

请只返回JSON，格式如下：
{
  "strategy": "策略名称",
  "params": {"参数名": 值},
  "max_results": 20,
  "explanation": "解析说明",
  "use_custom": false,
  "custom_filters": []
}
"""


def _heuristic_parse(user_input: str) -> dict | None:
    raw = user_input or ""
    count = 20
    import re
    m = re.search(r"(\d+)\s*[只个支]?", raw)
    if m:
        count = max(1, min(int(m.group(1)), 20))
    else:
        cn_counts = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "几": 5}
        m_cn = re.search(r"([一两二三四五几])\s*[只个支]", raw)
        if m_cn:
            count = cn_counts.get(m_cn.group(1), count)
    has_bull_ma = (
        ("均线" in raw and any(k in raw for k in ("多头", "向上", "上行", "发散", "排列")))
        or "多头排列" in raw
        or "均线多头" in raw
        or "ma多头" in raw.lower()
    )
    if has_bull_ma and any(k in raw for k in ("回踩", "踩线", "靠近", "贴近")):
        ma_periods = []
        for period in (5, 10, 20):
            if f"回踩{period}" in raw or f"{period}线" in raw or f"{period}日线" in raw or f"{period}日均线" in raw:
                ma_periods.append(period)
        return {
            "strategy": "ma_bullish_pullback",
            "params": {"ma_periods": ma_periods or [5, 10, 20], "pullback_within_pct": 3.5, "slope_lookback": 5},
            "max_results": count,
            "explanation": "按多头均线发散并回踩5/10/20日均线附近的右侧策略筛选。",
            "use_custom": False,
            "custom_filters": [],
        }
    if any(k in raw for k in ("回踩20", "20线", "20日线", "20日均线")):
        return {
            "strategy": "ma_pullback",
            "params": {"ma_periods": [20], "pullback_within_pct": 5.0, "volume_shrink_ratio": 0.5},
            "max_results": count,
            "explanation": "按20日均线回踩企稳策略筛选。",
            "use_custom": False,
            "custom_filters": [],
        }
    if any(k in raw for k in ("回踩60", "60线", "60日线", "60日均线")):
        return {
            "strategy": "ma_pullback",
            "params": {"ma_periods": [60], "pullback_within_pct": 5.0, "volume_shrink_ratio": 0.5},
            "max_results": count,
            "explanation": "按60日均线回踩企稳策略筛选。",
            "use_custom": False,
            "custom_filters": [],
        }
    if any(k in raw for k in ("均线回踩", "回踩均线")):
        return {
            "strategy": "ma_pullback",
            "params": {"ma_periods": [20, 60], "pullback_within_pct": 5.0, "volume_shrink_ratio": 0.5},
            "max_results": count,
            "explanation": "按20/60日均线回踩企稳策略筛选。",
            "use_custom": False,
            "custom_filters": [],
        }
    if has_bull_ma:
        return {
            "strategy": "ma_bullish",
            "params": {
                "slope_lookback": 5,
                "min_spread_pct": 1.5,
                "min_spread_expand_pct": 0.2,
                "max_deviation_pct": 25.0,
            },
            "max_results": count,
            "explanation": "按5/10/20/30日均线多头排列且发散向上策略筛选。",
            "use_custom": False,
            "custom_filters": [],
        }
    if any(k in raw for k in ("龙头", "强势", "涨停", "连板", "打板", "最猛", "领涨")):
        return {
            "strategy": "momentum",
            "params": {"min_limit_up_days": 1, "lookback_days": 15},
            "max_results": count,
            "explanation": "按强势龙头/涨停动量策略筛选。",
            "use_custom": False,
            "custom_filters": [],
        }
    if any(k in raw for k in ("趋势", "趋势股", "波段", "二次启动")):
        return {
            "strategy": "trend",
            "params": {},
            "max_results": count,
            "explanation": "按趋势动量策略筛选。",
            "use_custom": False,
            "custom_filters": [],
        }
    return None


def parse_strategy_request(user_input: str) -> dict:
    """解析用户的自然语言选股请求

    Returns:
        {"strategy": str, "params": dict, "explanation": str}
    """
    heuristic = _heuristic_parse(user_input)
    if heuristic:
        return heuristic

    response = chat(STRATEGY_PARSER_PROMPT, user_input, temperature=0.1)

    parsed = extract_json_object(response)
    if parsed:
        parsed.setdefault("params", {})
        parsed.setdefault("max_results", 20)
        parsed.setdefault("explanation", "")
        parsed.setdefault("use_custom", False)
        parsed.setdefault("custom_filters", [])
        return parsed

    heuristic = _heuristic_parse(user_input)
    return heuristic or {
        "strategy": None,
        "params": {},
        "explanation": f"解析失败，原始回复: {response[:200]}",
        "use_custom": True,
        "custom_filters": [],
    }


def run_strategy_selection(strategy_name: str, params: dict, max_results: int = 20) -> list:
    """运行策略筛选，返回命中股票列表

    Args:
        strategy_name: 策略名称 (momentum / trend / ma_pullback)
        params: 策略参数
        max_results: 最大返回数

    Returns:
        [{"ts_code": ..., "name": ..., "reason": ..., "score": ..., "extra": {...}}, ...]
    """
    strategy = StrategyRegistry.create(strategy_name, **params)
    if strategy is None:
        return []

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

        result = strategy.filter(ts_code, name, df)
        if result:
            results.append({
                "ts_code": result.ts_code,
                "name": result.name,
                "reason": result.reason,
                "score": result.score,
                "extra": result.extra,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def natural_language_select(user_input: str, max_results: int = 20) -> dict:
    """自然语言选股入口

    Returns:
        {"strategy": str, "explanation": str, "results": [...], "total": int}
    """
    parsed = parse_strategy_request(user_input)
    strategy_name = parsed.get("strategy")
    params = parsed.get("params", {})

    if not strategy_name or parsed.get("use_custom"):
        return {
            "strategy": "custom",
            "explanation": parsed.get("explanation", "无法解析策略"),
            "results": [],
            "total": 0,
        }

    results = run_strategy_selection(strategy_name, params, max_results)

    # Retry with relaxed params if not enough results
    if len(results) < max_results and strategy_name == "momentum":
        relaxed_params = {k:v for k,v in params.items() if k != 'min_limit_up_days'}
        relaxed = StrategyRegistry.create(strategy_name, min_limit_up_days=1, **relaxed_params)
        if relaxed:
            seen = {r["ts_code"] for r in results}
            main_board = list_main_board_stocks()
            for _, row in main_board.iterrows():
                ts_code = row["ts_code"]
                if ts_code in seen:
                    continue
                name = row["name"]
                df = load_daily(ts_code)
                if df is None or len(df) < 30:
                    continue
                df = compute_mas(df)
                df = compute_limit_status(df)
                result = relaxed.filter(ts_code, name, df)
                if result:
                    results.append({
                        "ts_code": result.ts_code,
                        "name": result.name,
                        "reason": result.reason,
                        "score": result.score,
                        "extra": result.extra,
                    })
            results.sort(key=lambda r: r["score"], reverse=True)
            results = results[:max_results]

    return {
        "strategy": strategy_name,
        "explanation": parsed.get("explanation", ""),
        "results": results,
        "total": len(results),
    }
