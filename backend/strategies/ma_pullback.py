"""20/60 日均线回调企稳策略

股价回调至20日或60日均线附近企稳，成交量萎缩后放量反弹。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class MAPullbackStrategy(BaseStrategy):
    """20/60 日均线回调企稳"""

    name = "ma_pullback"
    description = "20/60均线回调企稳：股价回调至关键均线附近，缩量企稳后放量反弹"
    recommended_lookback = 30  # 中线：需要均线+缩量/放量窗口

    def __init__(self,
                 ma_periods: tuple = (20, 60),
                 pullback_within_pct: float = 5.0,
                 volume_shrink_ratio: float = 0.5,
                 volume_expand_ratio: float = 1.5,
                 lookback_days: int = 30,
                 **kwargs):
        super().__init__(
            ma_periods=ma_periods,
            pullback_within_pct=pullback_within_pct,
            volume_shrink_ratio=volume_shrink_ratio,
            volume_expand_ratio=volume_expand_ratio,
            lookback_days=lookback_days,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        ma_periods = self.get_param("ma_periods")
        pullback_pct = self.get_param("pullback_within_pct")
        shrink_ratio = self.get_param("volume_shrink_ratio")
        expand_ratio = self.get_param("volume_expand_ratio")
        lookback = self.get_param("lookback_days")

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < max(ma_periods) + 10:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(lookback)

        # 确保均线已计算
        for period in ma_periods:
            col = f"ma{period}"
            if col not in recent.columns:
                return None

        latest = recent.iloc[-1]
        close = latest["close"]

        # 检查每根均线的偏离度
        best_ma = None
        best_deviation = float("inf")

        for period in ma_periods:
            ma_col = f"ma{period}"
            ma_val = latest[ma_col]
            if pd.isna(ma_val) or ma_val == 0:
                continue
            deviation = abs((close - ma_val) / ma_val * 100)
            if deviation <= pullback_pct and deviation < best_deviation:
                best_deviation = deviation
                best_ma = period

        if best_ma is None:
            return None

        ma_val = latest[f"ma{best_ma}"]

        # 成交量分析：最近3天平均量 vs 前20天平均量
        if len(recent) < 25:
            return None

        recent_3_vol = recent.iloc[-3:]["vol"].mean()
        prior_20_vol = recent.iloc[-23:-3]["vol"].mean()

        if prior_20_vol == 0:
            return None

        vol_ratio = recent_3_vol / prior_20_vol

        # 放量反弹信号
        if vol_ratio >= expand_ratio:
            signal = "放量反弹"
            score = 80
        elif vol_ratio <= shrink_ratio:
            signal = "缩量企稳中"
            score = 60
        else:
            signal = "均线附近震荡"
            score = 50

        # 趋势确认：短期均线方向
        ma5 = latest.get("ma5")
        ma10 = latest.get("ma10")
        trend = "震荡"
        if ma5 is not None and ma10 is not None and not pd.isna(ma5) and not pd.isna(ma10):
            if ma5 > ma10:
                trend = "短多"
            else:
                trend = "短空"

        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=f"股价{close}回调至{best_ma}日均线{ma_val:.2f}附近(偏离{best_deviation:.1f}%)，"
                   f"成交量{signal}（量比{vol_ratio:.2f}），{trend}",
            score=score,
            extra={
                "ma_period": best_ma,
                "ma_value": round(ma_val, 2),
                "deviation_pct": round(best_deviation, 2),
                "close": round(close, 2),
                "vol_ratio": round(vol_ratio, 2),
                "signal": signal,
                "trend": trend,
            },
        )
