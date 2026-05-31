"""长期上升趋势筛选策略

筛选处于稳定上升趋势的股票，不依赖涨停。
条件: close > ma60 > ma120, higher highs & higher lows。
长线策略，回溯 250 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class UptrendStrategy(BaseStrategy):
    """长期上升趋势策略"""

    name = "uptrend"
    description = "长期上升趋势：筛选MA多头排列+高点上移+低点上移的慢牛股，不依赖涨停"
    recommended_lookback = 250

    def __init__(self, trend_check_days: int = 60, ma_deviation_max: float = 30.0,
                 min_trend_slope: float = 5.0, **kwargs):
        super().__init__(
            trend_check_days=trend_check_days,
            ma_deviation_max=ma_deviation_max,
            min_trend_slope=min_trend_slope,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        check_days = self.get_param("trend_check_days", 60)
        dev_max = self.get_param("ma_deviation_max", 30.0)
        min_slope = self.get_param("min_trend_slope", 5.0)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 250:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)

        # 计算 MA120 (本地计算，不依赖全局 MA_PERIODS)
        df["ma120"] = df["close"].rolling(window=120, min_periods=60).mean()

        latest = df.iloc[-1]
        close = latest["close"]
        ma60 = latest.get("ma60")
        ma120 = latest.get("ma120")

        if pd.isna(ma60) or pd.isna(ma120) or ma60 <= 0 or ma120 <= 0:
            return None

        # 1. 多头排列: close > ma60 > ma120
        if not (close > ma60 > ma120):
            return None

        # 2. 价格不过热: 偏离 MA60 在合理范围 (2%-30%)
        deviation = (close - ma60) / ma60 * 100
        if deviation < 2 or deviation > dev_max:
            return None

        # 3. MA60 方向向上: 当前 MA60 > 30 天前 MA60
        if len(df) < 90:
            return None
        ma60_30d_ago = df.iloc[-31].get("ma60")
        if pd.isna(ma60_30d_ago) or ma60 <= ma60_30d_ago:
            return None

        # 4. Higher highs & higher lows 形态
        recent = df.tail(check_days)
        mid = len(recent) // 2
        first_half = recent.iloc[:mid]
        second_half = recent.iloc[mid:]

        first_high = first_half["high"].max()
        first_low = first_half["low"].min()
        second_high = second_half["high"].max()
        second_low = second_half["low"].min()

        if first_low <= 0:
            return None

        # 后段高点 > 前段高点, 后段低点 > 前段低点
        if not (second_high > first_high and second_low > first_low):
            return None

        # 5. 趋势斜率: 最近 check_days 天的价格上升幅度
        start_close = recent.iloc[0]["close"]
        if start_close <= 0:
            return None
        trend_pct = (close - start_close) / start_close * 100
        if trend_pct < min_slope:
            return None

        score = min(95, 50 + trend_pct * 0.5 + (deviation - 2) * 1.5)
        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=f"多头排列(close{close:.1f}>MA60{ma60:.1f}>MA120{ma120:.1f})，"
                   f"{check_days}天趋势+{trend_pct:.1f}%，高/低点上移，慢牛形态",
            score=round(score, 1),
            extra={
                "close": round(float(close), 2),
                "ma60": round(float(ma60), 2),
                "ma120": round(float(ma120), 2),
                "deviation_pct": round(float(deviation), 2),
                "trend_pct": round(float(trend_pct), 2),
                "first_high": round(float(first_high), 2),
                "second_high": round(float(second_high), 2),
                "first_low": round(float(first_low), 2),
                "second_low": round(float(second_low), 2),
            },
        )
