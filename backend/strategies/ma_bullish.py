"""均线多头发散向上策略。"""

import pandas as pd

from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class MABullishStrategy(BaseStrategy):
    """5/10/20/30 日均线多头排列并发散向上。"""

    name = "ma_bullish"
    description = "均线多头发散向上：筛选 MA5>MA10>MA20>MA30 且各均线同步上行的趋势股"
    recommended_lookback = 60

    def __init__(
        self,
        slope_lookback: int = 5,
        min_spread_pct: float = 1.5,
        min_spread_expand_pct: float = 0.2,
        max_deviation_pct: float = 25.0,
        **kwargs,
    ):
        super().__init__(
            slope_lookback=slope_lookback,
            min_spread_pct=min_spread_pct,
            min_spread_expand_pct=min_spread_expand_pct,
            max_deviation_pct=max_deviation_pct,
            **kwargs,
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        slope_lookback = max(2, int(self.get_param("slope_lookback", 5) or 5))
        min_spread_pct = float(self.get_param("min_spread_pct", 1.5) or 1.5)
        min_spread_expand_pct = float(self.get_param("min_spread_expand_pct", 0.2) or 0.2)
        max_deviation_pct = float(self.get_param("max_deviation_pct", 25.0) or 25.0)
        periods = (5, 10, 20, 30)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if df is None or len(df) < max(periods) + slope_lookback + 2:
            return None

        data = df.sort_values("trade_date").copy().reset_index(drop=True)
        for period in periods:
            col = f"ma{period}"
            if col not in data.columns:
                data[col] = data["close"].rolling(window=period, min_periods=period).mean()

        latest = data.iloc[-1]
        prev = data.iloc[-(slope_lookback + 1)]
        close = float(latest.get("close") or 0)
        ma_values = {period: latest.get(f"ma{period}") for period in periods}
        prev_values = {period: prev.get(f"ma{period}") for period in periods}
        if close <= 0:
            return None
        if any(pd.isna(value) or float(value) <= 0 for value in ma_values.values()):
            return None
        if any(pd.isna(value) or float(value) <= 0 for value in prev_values.values()):
            return None

        ma5, ma10, ma20, ma30 = [float(ma_values[p]) for p in periods]
        p_ma5, p_ma10, p_ma20, p_ma30 = [float(prev_values[p]) for p in periods]

        # 当前多头排列，且价格仍站在短均线上方。
        if not (close > ma5 > ma10 > ma20 > ma30):
            return None

        # 四条均线全部抬升，避免只靠短线异动形成假多头。
        if not (ma5 > p_ma5 and ma10 > p_ma10 and ma20 > p_ma20 and ma30 > p_ma30):
            return None

        spread_now = (ma5 - ma30) / ma30 * 100
        spread_prev = (p_ma5 - p_ma30) / p_ma30 * 100
        spread_expand = spread_now - spread_prev
        if spread_now < min_spread_pct or spread_expand < min_spread_expand_pct:
            return None

        deviation = (close - ma30) / ma30 * 100
        if deviation > max_deviation_pct:
            return None

        ma30_slope = (ma30 - p_ma30) / p_ma30 * 100
        ma20_slope = (ma20 - p_ma20) / p_ma20 * 100
        recent_gain = (close - float(data.iloc[-slope_lookback]["close"])) / float(data.iloc[-slope_lookback]["close"]) * 100
        score = 55 + spread_now * 3 + spread_expand * 8 + ma30_slope * 4 + max(0, recent_gain) * 0.8
        score = max(0, min(98, score))

        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=(
                f"MA5/10/20/30 多头排列({ma5:.2f}>{ma10:.2f}>{ma20:.2f}>{ma30:.2f})，"
                f"{slope_lookback}日内四线同步上行，均线发散{spread_now:.1f}%且扩大{spread_expand:.1f}%，"
                f"收盘价高于MA30 {deviation:.1f}%"
            ),
            score=round(score, 1),
            extra={
                "close": round(close, 2),
                "ma5": round(ma5, 2),
                "ma10": round(ma10, 2),
                "ma20": round(ma20, 2),
                "ma30": round(ma30, 2),
                "spread_pct": round(spread_now, 2),
                "spread_expand_pct": round(spread_expand, 2),
                "ma20_slope_pct": round(ma20_slope, 2),
                "ma30_slope_pct": round(ma30_slope, 2),
                "deviation_pct": round(deviation, 2),
                "recent_gain_pct": round(float(recent_gain), 2),
            },
        )
