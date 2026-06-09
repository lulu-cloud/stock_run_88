"""每日 Markdown 复盘文档自动生成"""

import os
from datetime import datetime
from backend.agents.base import AgentContext, AgentDecision
from backend.config import REPORTS_DIR


def _no_action_note(trades: list[dict], decision: AgentDecision | None) -> str:
    if trades or (decision and decision.orders):
        return ""
    if not decision:
        return "本轮没有成交且 Agent 未生成有效决策，需检查 LLM 调用、工具链或超时日志。"
    source = " ".join(filter(None, [decision.risk_assessment or "", decision.market_analysis or ""])).strip()
    if source:
        return (
            "本轮没有成交、没有新条件单。Agent 给出的观望依据摘要: "
            + source[:500]
        )
    return "本轮没有成交、没有新条件单，但 Agent 未给出充分观望理由；已标记为复盘改进项。"


def generate_daily_report(agent_id: int, agent_name: str, trade_date: str,
                          context: AgentContext, trades: list[dict],
                          decision: AgentDecision | None = None,
                          daily_pnl: float = 0.0,
                          daily_return: float = 0.0,
                          cumulative_pnl: float = 0.0,
                          review_error: str = "") -> str:
    """生成每日 Markdown 复盘文档

    Returns:
        报告文件路径
    """
    report_dir = os.path.join(REPORTS_DIR, trade_date, agent_name)
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, "review.md")

    positions_md = ""
    market_value = 0.0
    unrealized_pnl = 0.0
    for p in context.positions:
        market_value += float(p.get("market_value", 0) or 0)
        unrealized_pnl += float(p.get("unrealized_pnl", 0) or 0)
        positions_md += (
            f"| {p['ts_code']} | {p.get('stock_name', '')} | {p['quantity']} | "
            f"{(p.get('current_price', 0) or 0):.2f} | {p['avg_cost']:.2f} | "
            f"{(p.get('unrealized_pnl', 0) or 0):.2f} |\n"
        )

    trades_md = ""
    for t in trades:
        reason = t.get("reason", "")
        trades_md += (
            f"| {t.get('ts_code', '')} | {t.get('stock_name', '')} | {t.get('direction', '')} | {t.get('quantity', 0)} | "
            f"{t.get('price', 0):.2f} | {t.get('total_value', 0):.2f} | "
            f"{t.get('commission', 0):.2f} | {t.get('stamp_tax', 0):.2f} | {reason} |\n"
        )

    orders_md = ""
    if decision:
        for o in decision.orders or []:
            orders_md += (
                f"| {o.get('ts_code', '')} | {o.get('stock_name', '')} | {o.get('direction', '')} | "
                f"{o.get('quantity', 0)} | {float(o.get('price', 0) or 0):.2f} | "
                f"{'是' if o.get('open_get_in') else '否'} | {o.get('skill_id', '')} | "
                f"{float(o.get('skill_confidence') or 0):.2f} | {o.get('reason', '')} |\n"
            )
    no_action_note = _no_action_note(trades, decision)
    if review_error and not (trades or (decision and decision.orders)):
        no_action_note = (
            f"本轮没有成交、没有新条件单。复盘异常: {review_error}。"
            "若 thinking.log 中存在候选或完整 JSON，系统会写入 Idea Pool 做后验观察。"
        )
    evolution_md = ""
    evo = context.evolution_context or {}
    for skill in (evo.get("skills") or [])[:6]:
        evolution_md += (
            f"| {skill.get('skill_id', '')} | {skill.get('skill_name', '')} | "
            f"{float(skill.get('confidence_score') or 0):.2f} | "
            f"{float(skill.get('recent_fail_rate') or 0):.2f} | "
            f"{skill.get('evolution_record', '')} |\n"
        )

    report = f"""# {agent_name} 每日复盘报告

**日期**: {trade_date}
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 1. 资产概览

| 指标 | 数值 |
|------|------|
| 可用资金 | {context.cash:.2f} 元 |
| 冻结资金 | {context.frozen_cash:.2f} 元 |
| 持仓市值 | {market_value:.2f} 元 |
| 浮动盈亏 | {unrealized_pnl:+.2f} 元 |
| 总资产 | {context.total_assets:.2f} 元 |
| 初始本金 | {context.initial_capital:.2f} 元 |
| 今日收益 | {daily_pnl:+.2f} 元 / {daily_return:+.2f}% |
| 累计收益 | {cumulative_pnl:+.2f} 元 / {context.cumulative_return:+.2f}% |
| 累计收益率 | {context.cumulative_return:.2f}% |

## 2. 当前持仓

| 股票代码 | 名称 | 数量 | 现价 | 成本 | 浮动盈亏 |
|----------|------|------|------|------|----------|
{positions_md if positions_md else '| - | 空仓 | - | - | - | - |'}

## 3. 今日成交

| 股票代码 | 名称 | 方向 | 数量 | 价格 | 金额 | 佣金 | 印花税 | 理由 |
|----------|------|------|------|------|------|------|--------|------|
{trades_md if trades_md else '| - | - | 无成交 | - | - | - | - | - | - |'}

## 4. 操作总结

{decision.market_analysis if decision else ('Agent 未生成分析。' + (f' 复盘异常: {review_error}' if review_error else ''))}

{('### 无操作说明\n\n' + no_action_note) if no_action_note else ''}

## 5. 新生成条件单

| 股票代码 | 名称 | 方向 | 数量 | 价格 | 开盘抢入/出 | 技能 | 置信度 | 理由 |
|----------|------|------|------|------|--------------|------|--------|------|
{orders_md if orders_md else '| - | 无新订单 | - | - | - | - | - | - | - |'}

## 6. 风险评估

{decision.risk_assessment if decision else (f'复盘异常: {review_error}' if review_error else '无')}

## 7. 进化技能快照

| 技能ID | 技能名 | 置信度 | 近因失败率 | 进化记录 |
|--------|--------|--------|------------|----------|
{evolution_md if evolution_md else '| - | 暂无 | - | - | - |'}

## 8. Agent 原始输出

```text
{decision.analysis if decision else '无'}
```
"""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    return report_path
