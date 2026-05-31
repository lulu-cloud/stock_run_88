"""thinking.log — Agent 决策过程记录。

按日期 + Agent 名称记录可读文本日志：LLM 输入输出、工具调用、最终订单。
"""

import os
import json
from datetime import datetime
from backend.agents.base import AgentDecision
from backend.config import LOGS_DIR


def log_thinking(agent_id: int, agent_name: str, trade_date: str,
                 decision: AgentDecision, log_path: str = ""):
    """记录 Agent 决策过程

    Args:
        agent_id: Agent ID
        agent_name: Agent 名称
        trade_date: 交易日
        decision: Agent 决策结果
        log_path: 日志文件路径
    """
    if not log_path:
        log_dir = os.path.join(LOGS_DIR, trade_date, agent_name)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "thinking.log")

    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n" + "=" * 88 + "\n")
        f.write(f"Decision summary {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Agent: {agent_name} ({agent_id})  Trade date: {trade_date}\n")
        f.write("-" * 88 + "\n")
        f.write("[Market Analysis]\n")
        f.write((decision.market_analysis or "").strip() + "\n\n")
        f.write("[Selected Stocks]\n")
        for s in decision.selected_stocks or []:
            f.write(f"- {s.get('ts_code', '')} {s.get('name') or s.get('stock_name', '')}: {s.get('reason', '')}\n")
        if not decision.selected_stocks:
            f.write("- none\n")
        f.write("\n[Orders]\n")
        for o in decision.orders or []:
            f.write(
                f"- {o.get('direction', '')} {o.get('ts_code', '')} {o.get('stock_name', '')} "
                f"{o.get('quantity', 0)}股 @ {o.get('price', 0)} | {o.get('reason', '')}\n"
            )
        if not decision.orders:
            f.write("- none\n")
        f.write("\n[Risk Assessment]\n")
        f.write((decision.risk_assessment or "").strip() + "\n")

    trace_path = os.path.join(os.path.dirname(log_path), "thinking_trace.jsonl")
    tool_trace = decision.tool_trace or []
    summary = {
        "type": "decision_summary",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "trade_date": trade_date,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "selected_stock_count": len(decision.selected_stocks or []),
        "order_count": len(decision.orders or []),
        "tool_call_count": len([x for x in tool_trace if x.get("type") == "tool"]),
        "tool_failure_count": len([x for x in tool_trace if x.get("type") == "tool" and x.get("error")]),
        "llm_call_count": len([x for x in tool_trace if x.get("type") == "llm"]),
        "invalid_tool_call_count": sum(int(x.get("count") or 0) for x in tool_trace if x.get("type") == "invalid_tool_calls"),
        "json_parse_repair_count": len([x for x in tool_trace if x.get("type") == "parse" and x.get("error")]),
    }
    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")


def read_thinking_logs(agent_name: str = None, trade_date: str = None,
                       keyword: str = None) -> list[dict]:
    """检索 thinking.log

    Args:
        agent_name: 按 Agent 名称过滤
        trade_date: 按日期过滤
        keyword: 按关键词搜索
    """
    results = []

    if not os.path.exists(LOGS_DIR):
        return results

    for date_dir in os.listdir(LOGS_DIR):
        if trade_date and date_dir != trade_date:
            continue

        date_path = os.path.join(LOGS_DIR, date_dir)
        if not os.path.isdir(date_path):
            continue

        for agent_dir in os.listdir(date_path):
            if agent_name and agent_dir != agent_name:
                continue

            log_file = os.path.join(date_path, agent_dir, "thinking.log")
            if not os.path.exists(log_file):
                continue

            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
            if keyword and keyword.lower() not in content.lower():
                continue
            results.append({
                "agent_name": agent_dir,
                "trade_date": date_dir,
                "content": content,
                "log_path": log_file,
            })

    return results
