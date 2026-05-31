"""策略选股 API"""

import json
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from backend.strategies.registry import StrategyRegistry
from backend.llm.strategy_parser import (
    natural_language_select, run_strategy_selection,
    STRATEGY_PARSER_PROMPT, parse_strategy_request,
)
from backend.llm.client import chat_stream

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


class NaturalLanguageRequest(BaseModel):
    query: str
    max_results: int = 20


class StrategyRunRequest(BaseModel):
    strategy_name: str
    params: dict = {}
    max_results: int = 20


@router.get("/builtin")
async def list_builtin_strategies():
    """获取内置策略列表"""
    return {
        "strategies": StrategyRegistry.list_with_info(),
        "total": len(StrategyRegistry.list_all()),
    }


@router.post("/select")
async def select_by_natural_language(req: NaturalLanguageRequest):
    """自然语言选股"""
    parsed = parse_strategy_request(req.query)
    parsed_max = parsed.get("max_results", req.max_results)
    target = max(parsed_max, req.max_results)
    result = natural_language_select(req.query, target)
    result["parsed_max"] = parsed_max
    return result


@router.post("/run")
async def run_strategy(req: StrategyRunRequest):
    """直接运行指定策略"""
    results = run_strategy_selection(req.strategy_name, req.params, req.max_results)
    return {
        "strategy": req.strategy_name,
        "params": req.params,
        "results": results,
        "total": len(results),
    }


@router.get("/results/{strategy_name}")
async def get_strategy_results(
    strategy_name: str,
    limit: int = Query(default=20, le=100),
):
    """快速获取策略选股结果（使用默认参数）"""
    results = run_strategy_selection(strategy_name, {}, limit)
    return {
        "strategy": strategy_name,
        "results": results,
        "total": len(results),
    }


@router.post("/select-stream")
async def select_by_natural_language_stream(req: NaturalLanguageRequest):
    """自然语言选股 - 流式输出思考过程 + 工具调用 + 结果"""

    async def event_stream():
        # Phase 1: 流式 LLM 策略解析
        yield f"data: {json.dumps({'type': 'phase', 'content': 'LLM 正在解析你的选股意图...'})}\n\n"

        full_response = ""
        for token in chat_stream(STRATEGY_PARSER_PROMPT, req.query, temperature=0.1):
            full_response += token
            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

        # Phase 2: 解析结果
        parsed = parse_strategy_request(req.query)
        strategy_name = parsed.get("strategy")
        explanation = parsed.get("explanation", "")
        params = parsed.get("params", {})
        parsed_max = parsed.get("max_results", req.max_results)

        yield f"data: {json.dumps({'type': 'parsed', 'strategy': strategy_name, 'params': params, 'explanation': explanation, 'max_results': parsed_max})}\n\n"

        if not strategy_name or parsed.get("use_custom"):
            yield f"data: {json.dumps({'type': 'error', 'content': f'无法解析策略: {explanation}'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        # Phase 3: 运行策略扫描
        from backend.data.loader import list_main_board_stocks
        main_board = list_main_board_stocks()
        total = len(main_board)
        target_count = max(parsed_max, req.max_results)
        yield f"data: {json.dumps({'type': 'phase', 'content': f'正在扫描 {total} 只主板股票，目标{target_count}只...'})}\n\n"

        strategy = StrategyRegistry.create(strategy_name, **params)
        results = []
        from backend.data.loader import load_daily, compute_mas, compute_limit_status

        for i, (_, row) in enumerate(main_board.iterrows()):
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
            if i % 200 == 0:
                yield f"data: {json.dumps({'type': 'progress', 'current': i, 'total': total, 'hits': len(results)})}\n\n"

        results.sort(key=lambda r: r["score"], reverse=True)

        # If not enough results, try with relaxed params
        if len(results) < target_count and strategy_name == "momentum":
            yield f"data: {json.dumps({'type': 'phase', 'content': f'首轮仅命中{len(results)}只，放宽参数重新扫描...'})}\n\n"
            relaxed_params = {k:v for k,v in params.items() if k != 'min_limit_up_days'}
            relaxed = StrategyRegistry.create(strategy_name, min_limit_up_days=1, **relaxed_params)
            if relaxed:
                seen = {r["ts_code"] for r in results}
                for i, (_, row) in enumerate(main_board.iterrows()):
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
                    if i % 200 == 0:
                        yield f"data: {json.dumps({'type': 'progress', 'current': i, 'total': total, 'hits': len(results)})}\n\n"
                results.sort(key=lambda r: r["score"], reverse=True)

        final = results[:target_count]

        yield f"data: {json.dumps({'type': 'results', 'data': final, 'total': len(final), 'strategy': strategy_name})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
