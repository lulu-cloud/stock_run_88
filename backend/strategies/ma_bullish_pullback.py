"""多头均线发散后的短线回踩策略。"""

import pandas as pd

from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class MABullishPullbackStrategy(BaseStrategy):
    """MA5/10/20/30 多头排列，且价格回踩 MA5/10/20 附近。"""

    name = "ma_bullish_pullback"
    description = "多头均线发散回踩：MA5>MA10>MA20>MA30 且回踩 MA5/10/20 附近"
    recommended_lookback = 60

    def __init__(
        self,
        ma_periods: list[int] | tuple[int, ...] = (5, 10, 20),
        pullback_within_pct: float = 3.0,
        slope_lookback: int = 5,
        max_deviation_pct: float = 18.0,
        **kwargs,
    ):
        super().__init__(
            ma_periods=ma_periods,
            pullback_within_pct=pullback_within_pct,
            slope_lookback=slope_lookback,
            max_deviation_pct=max_deviation_pct,
            **kwargs,
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        if "ST" in name or "*ST" in name or "退" in name:
            return None
        ma_periods = tuple(int(x) for x in (self.get_param("ma_periods") or (5, 10, 20)))
        slope_lookback = max(2, int(self.get_param("slope_lookback", 5) or 5))
        pullback_pct = float(self.get_param("pullback_within_pct", 3.0) or 3.0)
        max_deviation_pct = float(self.get_param("max_deviation_pct", 18.0) or 18.0)
        base_periods = (5, 10, 20, 30)
        if df is None or len(df) < max(base_periods) + slope_lookback + 2:
            return None

        data = df.sort_values("trade_date").copy().reset_index(drop=True)
        for period in set(base_periods) | set(ma_periods):
            col = f"ma{period}"
            if col not in data.columns:
                data[col] = data["close"].rolling(window=period, min_periods=period).mean()

        latest = data.iloc[-1]
        prev = data.iloc[-(slope_lookback + 1)]
        close = float(latest.get("close") or 0)
        if close <= 0:
            return None
        ma = {p: latest.get(f"ma{p}") for p in base_periods}
        prev_ma = {p: prev.get(f"ma{p}") for p in base_periods}
        if any(pd.isna(v) or float(v) <= 0 for v in ma.values()):
            return None
        if any(pd.isna(v) or float(v) <= 0 for v in prev_ma.values()):
            return None
        ma5, ma10, ma20, ma30 = [float(ma[p]) for p in base_periods]
        p_ma5, p_ma10, p_ma20, p_ma30 = [float(prev_ma[p]) for p in base_periods]
        if not (ma5 > ma10 > ma20 > ma30):
            return None
        if not (ma5 > p_ma5 and ma10 > p_ma10 and ma20 > p_ma20 and ma30 > p_ma30):
            return None
        if close < ma20:
            return None

        best_period = None
        best_deviation = float("inf")
        for period in ma_periods:
            value = float(latest.get(f"ma{period}") or 0)
            if value <= 0:
                continue
            deviation = abs((close - value) / value * 100)
            if deviation <= pullback_pct and deviation < best_deviation:
                best_period = period
                best_deviation = deviation
        if best_period is None:
            return None

        deviation_ma30 = (close - ma30) / ma30 * 100
        if deviation_ma30 > max_deviation_pct:
            return None
        vol = float(latest.get("vol") or 0)
        avg_vol20 = float(pd.to_numeric(data.tail(20)["vol"], errors="coerce").mean() or 0)
        volume_ratio = vol / avg_vol20 if avg_vol20 > 0 else 1.0
        spread = (ma5 - ma30) / ma30 * 100
        recent_low = float(data.tail(5)["low"].min())
        support_ok = recent_low <= float(latest.get(f"ma{best_period}") or close) * 1.015
        score = 58 + spread * 2.5 + max(0, 3 - best_deviation) * 5 + min(volume_ratio, 2.0) * 4
        if support_ok:
            score += 6
        score = max(0, min(98, score))
        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=(
                f"MA5/10/20/30 多头排列且同步上行，当前回踩 MA{best_period} 附近"
                f"(偏离{best_deviation:.1f}%)，均线发散{spread:.1f}%，量比{volume_ratio:.2f}"
            ),
            score=round(score, 1),
            extra={
                "close": round(close, 2),
                "pullback_ma": best_period,
                "pullback_deviation_pct": round(best_deviation, 2),
                "spread_pct": round(spread, 2),
                "volume_ratio": round(volume_ratio, 2),
                "ma5": round(ma5, 2),
                "ma10": round(ma10, 2),
                "ma20": round(ma20, 2),
                "ma30": round(ma30, 2),
                "support_ok": support_ok,
            },
        )
