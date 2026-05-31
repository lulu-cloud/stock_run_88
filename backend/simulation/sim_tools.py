"""时间感知工具包装器

为模拟交易创建工具集，确保 agent 在模拟日期 T 只能看到 T 及之前的数据。
"""

import os
import json
from datetime import datetime
from langchain.tools import tool

from backend.data.loader import (
    load_daily, load_index_daily, compute_mas, compute_limit_status,
    list_main_board_stocks,
)
from backend.data.indicators import compute_sector_heat, compute_market_strength_by_sector
from backend.strategies.registry import StrategyRegistry
from backend.search_agent.searcher import get_cached, get_freshness
from backend.policy.reader import POLICY_DIR
from backend.config import DAILY_DIR
from backend.trading.rules import normalize_ts_code


def _format_kline_brief(df, lookback: int = 5) -> str:
    """格式化K线数据摘要 (同 agents/tools.py)"""
    recent = df.tail(lookback)
    lines = []
    for _, row in recent.iterrows():
        turn = row.get("turnover_rate", 0) or 0
        ma5_str = f"MA5={row['ma5']:.2f}" if row.get('ma5') and not pd.isna(row.get('ma5')) else ""
        lines.append(
            f"{row['trade_date']}: O={row['open']:.2f} H={row['high']:.2f} "
            f"L={row['low']:.2f} C={row['close']:.2f} "
            f"涨跌={row.get('pct_chg', 0):.2f}% 换手={turn:.2f}% {ma5_str}"
        )
    return "\n".join(lines)


import pandas as pd


def create_sim_tools(trade_date: str, preloaded_data: dict = None) -> list:
    """创建时间隔离的工具集

    Args:
        trade_date: 当前模拟交易日 (YYYYMMDD)
        preloaded_data: 预加载的股票数据 {ts_code: {"name": str, "df": DataFrame}}

    Returns:
        LangChain tool 列表
    """
    _preloaded = preloaded_data or {}

    def _load_daily_upto(ts_code: str):
        """加载截至 trade_date 的 K 线数据 (优先使用预加载，不复 cop)"""
        ts_code = normalize_ts_code(ts_code)
        if ts_code in _preloaded:
            df = _preloaded[ts_code]["df"]
            # 直接引用，无需 copy — 只读取不修改
            df = df[df["trade_date"] <= trade_date]
            return df if len(df) >= 30 else None
        df = load_daily(ts_code)
        if df is None:
            return None
        df = df[df["trade_date"] <= trade_date]
        if len(df) < 30:
            return None
        df = compute_mas(df)
        df = compute_limit_status(df)
        return df

    def _load_index_upto():
        """加载截至 trade_date 的上证指数数据"""
        df = load_index_daily()
        if df is None or df.empty:
            return None
        df = df[df["trade_date"] <= trade_date]
        return df if len(df) > 0 else None

    # ================================================================
    # 工具 1: 策略选股 (时间隔离版)
    # ================================================================
    @tool
    def search_stocks_by_strategy(strategy_name: str, params_json: str = "{}") -> str:
        """使用指定策略筛选股票 (仅能看到当前模拟日期及之前的数据)。

        Args:
            strategy_name: 策略名称
            params_json: 策略参数 JSON 字符串

        Returns:
            JSON 格式筛选结果
        """
        try:
            params = json.loads(params_json) if params_json else {}
        except json.JSONDecodeError:
            params = {}

        strategy = StrategyRegistry.create(strategy_name, **params)
        if strategy is None:
            return json.dumps({"error": f"未知策略: {strategy_name}"})

        results = []

        if _preloaded:
            # 快速路径：直接迭代预加载数据
            for ts_code, data in _preloaded.items():
                name = data["name"]
                df = data["df"]
                df_upto = df[df["trade_date"] <= trade_date]
                if len(df_upto) < 30:
                    continue
                try:
                    result = strategy.filter(ts_code, name, df_upto)
                    if result:
                        results.append({
                            "ts_code": result.ts_code,
                            "name": result.name,
                            "reason": result.reason,
                            "score": result.score,
                            "extra": result.extra,
                        })
                except Exception:
                    continue
        else:
            main_board = list_main_board_stocks()
            for _, row in main_board.iterrows():
                ts_code = row["ts_code"]
                name = row["name"]
                df = _load_daily_upto(ts_code)
                if df is None:
                    continue
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
                except Exception:
                    continue

        results.sort(key=lambda r: r["score"], reverse=True)
        return json.dumps(results[:20], ensure_ascii=False)

    # ================================================================
    # 工具 2: 个股 K 线 (时间隔离版)
    # ================================================================
    @tool
    def get_stock_kline(ts_code: str, days: int = 30) -> str:
        """获取个股日线K线数据 (仅能看到当前模拟日期及之前的数据)。

        Args:
            ts_code: 股票代码，如 '600000.SH'
            days: 返回最近多少天的数据，默认30

        Returns:
            格式化的K线数据文本
        """
        ts_code = normalize_ts_code(ts_code)
        df = _load_daily_upto(ts_code)
        if df is None:
            return f"未找到 {ts_code} 在 {trade_date} 之前的行情数据 (或数据不足30天)"
        return _format_kline_brief(df, min(days, len(df)))

    # ================================================================
    # 工具 3: 大盘概况 (时间隔离版)
    # ================================================================
    @tool
    def get_market_overview() -> str:
        """获取大盘概况 (上证指数，仅能看到当前模拟日期及之前的数据)。

        Returns:
            格式化的上证指数数据
        """
        df = _load_index_upto()
        if df is None or df.empty:
            return "暂无大盘数据"

        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        return (
            f"上证指数 当前模拟日期: {trade_date}\n"
            f"最新可见交易日: {latest['trade_date']}\n"
            f"收盘: {latest['close']:.2f} | 开盘: {latest['open']:.2f}\n"
            f"最高: {latest['high']:.2f} | 最低: {latest['low']:.2f}\n"
            f"涨跌幅: {latest.get('pct_chg', 0):.2f}%\n"
            f"成交额: {latest.get('amount', 0) / 1e8:.2f}亿\n"
            f"---\n最近5天:\n{_format_kline_brief(df, 5)}"
        )

    # ================================================================
    # 工具 4: 公司业务 (时间隔离版)
    # ================================================================
    @tool
    def get_company_business(ts_code: str) -> str:
        """获取公司主营业务信息 (从本地缓存读取，以模拟日期为参考检查时效性)。

        Args:
            ts_code: 股票代码，如 '603629.SH'

        Returns:
            公司业务描述文本
        """
        # 使用模拟日期进行时效性检查
        freshness = get_freshness(ts_code)
        if freshness:
            content = get_cached(ts_code)
            if content:
                # 以模拟日期为参考检查缓存时效
                try:
                    cache_date = datetime.strptime(freshness["date"], "%Y%m%d")
                    sim_date = datetime.strptime(trade_date, "%Y%m%d")
                    age_days = (sim_date - cache_date).days
                    is_fresh = age_days <= 30
                except (ValueError, KeyError):
                    age_days = freshness.get("age_days", 0)
                    is_fresh = freshness.get("is_fresh", True)

                if is_fresh:
                    return content
                else:
                    return (
                        f"[警告] 缓存日期({freshness['date']})早于模拟日期{trade_date}"
                        f"({age_days}天前)，信息可能不准确。\n"
                        f"---\n{content}"
                    )

        return f"暂无 {ts_code} 的公司业务信息。"

    # ================================================================
    # 工具 5: 板块热度 (时间隔离版 — 透传 trade_date)
    # ================================================================
    @tool
    def compute_sector_heat_tool(query_date: str = "") -> str:
        """计算每日板块热度排行 (仅能看到指定日期及之前的数据)。

        Args:
            query_date: 交易日期 (YYYYMMDD)，空字符串表示使用当前模拟日期

        Returns:
            JSON 格式的板块热度列表
        """
        effective_date = query_date if query_date else trade_date
        results = compute_sector_heat(effective_date)
        if not results:
            return f"暂无 {effective_date} 的板块热度数据"
        return json.dumps(results[:15], ensure_ascii=False)

    # ================================================================
    # 工具 5b: 强势/弱势板块 (价格行为版)
    # ================================================================
    @tool
    def get_market_strength_sectors(query_date: str = "", lookback_days: int = 3) -> str:
        """按近 N 个交易日涨幅、涨停家数、换手统计强势/弱势板块。

        Args:
            query_date: 交易日期 (YYYYMMDD)，空字符串表示当前模拟日期
            lookback_days: 统计窗口，默认3个交易日

        Returns:
            JSON，包含 strong/weak 板块及领涨股
        """
        effective_date = query_date if query_date else trade_date
        return json.dumps(
            compute_market_strength_by_sector(effective_date, lookback_days, top_n=10),
            ensure_ascii=False,
            default=str,
        )

    # ================================================================
    # 工具 6: 政策信号 (时间隔离版)
    # ================================================================
    @tool
    def get_policy_signals() -> str:
        """获取宏观政策信号 (仅包含模拟日期之前发布的政策文件)。

        Returns:
            JSON 格式的政策信号分析
        """
        if not os.path.exists(POLICY_DIR):
            return json.dumps({"top_industries": [], "summary": "暂无宏观政策数据"}, ensure_ascii=False)

        # 手动扫描政策文件，过滤 publish_date <= trade_date
        import re
        from backend.policy.reader import KEYWORD_INDUSTRY_MAP

        all_files = []
        for source in os.listdir(POLICY_DIR):
            source_dir = os.path.join(POLICY_DIR, source)
            if not os.path.isdir(source_dir):
                continue
            for fname in os.listdir(source_dir):
                if not fname.endswith(".md"):
                    continue
                date_str = fname[:8] if len(fname) >= 8 else ""
                if date_str.isdigit() and date_str <= trade_date:
                    all_files.append({
                        "source": source,
                        "filepath": os.path.join(source_dir, fname),
                        "filename": fname,
                    })

        if not all_files:
            return json.dumps({"top_industries": [], "summary": f"截至 {trade_date} 暂无政策数据"}, ensure_ascii=False)

        # 关键词匹配 (简化版信号提取)
        industry_signals = {}
        recent_policies = []

        for f in all_files:
            try:
                with open(f["filepath"], "r", encoding="utf-8") as fh:
                    content = fh.read()
            except Exception:
                continue

            lines = content.strip().split("\n")
            title = lines[0].lstrip("#").strip() if lines else f["filename"]
            date_str = f["filename"][:8]
            date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if len(date_str) >= 8 else ""

            recent_policies.append({"title": title, "source": f["source"], "date": date})

            for keyword, industry in KEYWORD_INDUSTRY_MAP.items():
                count = len(re.findall(re.escape(keyword), content, re.IGNORECASE))
                if count > 0:
                    if industry not in industry_signals:
                        industry_signals[industry] = {"strength": 0.0, "keywords": [], "doc_count": 0}
                    industry_signals[industry]["strength"] += count
                    if keyword not in industry_signals[industry]["keywords"]:
                        industry_signals[industry]["keywords"].append(keyword)
                    industry_signals[industry]["doc_count"] += 1

        max_s = max((s["strength"] for s in industry_signals.values()), default=1)
        if max_s > 0:
            for s in industry_signals.values():
                s["strength"] = round(s["strength"] / max_s, 2)

        sorted_industries = sorted(industry_signals.items(), key=lambda x: x[1]["strength"], reverse=True)
        top = [{"industry": ind, **data} for ind, data in sorted_industries[:10]]

        if top:
            top_names = [t["industry"] for t in top[:5]]
            summary = f"近期宏观政策重点关注: {'、'.join(top_names)}等板块"
        else:
            summary = "近期暂无明确的产业政策信号"

        return json.dumps({
            "top_industries": top,
            "recent_policies": recent_policies[:20],
            "summary": summary,
            "analyzed_count": len(all_files),
        }, ensure_ascii=False)

    return [
        search_stocks_by_strategy,
        get_stock_kline,
        get_market_overview,
        get_company_business,
        compute_sector_heat_tool,
        get_market_strength_sectors,
        get_policy_signals,
    ]
