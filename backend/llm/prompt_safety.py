"""Prompt formatting helpers for agent configuration text."""

from __future__ import annotations


def trusted_strategy_block(label: str, text: str, *, empty: str = "未配置", max_chars: int = 12000) -> str:
    value = str(text or "").strip()
    if not value:
        return empty
    if len(value) > max_chars:
        value = value[:max_chars] + "\n...[用户配置过长，已截断]"
    return "\n".join([
        f"<trusted_{label}>",
        "以下内容是用户为该 Agent 明确配置的核心交易策略基准，决策与进化必须围绕它展开。",
        "进化记忆和每日复盘只能在该策略上修补执行细节、参数、风控边界和失败案例，不能把它改写成另一套风格。",
        value,
        f"</trusted_{label}>",
    ])
