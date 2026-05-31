"""缩量回踩均线策略

缩量回踩 MA5/MA10（短周期），趋势延续的入场信号。
比 kline_pullback(MA20) 更短线。
短线策略，回溯 30 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class VolumePullbackStrategy(BaseStrategy):
    """缩量回踩 MA5/MA10"""

    name = "volume_pullback"
    description = "缩量回踩短均：缩量回踩MA5/MA10，趋势延续入场，短线操作"
    recommended_lookback = 30

    def __init__(self, pullback_pct: float = 2.0, shrink_ratio: float = 0.7,
                 ma_period: int = 10, min_trend_days: int = 5, **kwargs):
        super().__init__(
            pullback_pct=pullback_pct, shrink_ratio=shrink_ratio,
            ma_period=ma_period, min_trend_days=min_trend_days, **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        pb_pct = self.get_param("pullback_pct", 2.0)
        shrink_r = self.get_param("shrink_ratio", 0.7)
        ma_p = self.get_param("ma_period", 10)
        trend_d = self.get_param("min_trend_days", 5)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 30:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(30)
        latest = recent.iloc[-1]
        close = latest["close"]

        ma_col = f"ma{ma_p}"
        ma_val = latest.get(ma_col)
        if pd.isna(ma_val) or ma_val <= 0:
            return None

        # 1. 价格回踩均线（在均线上方 pb_pct% 以内）
        deviation = (close - ma_val) / ma_val * 100
        if deviation < 0 or deviation > pb_pct:
            return None

        # 2. 均线方向向上（趋势延续）
        if len(recent) < trend_d + 2:
            return None
        ma_ago = recent.iloc[-(trend_d + 1)].get(ma_col)
        if pd.isna(ma_ago) or ma_val <= ma_ago:
            return None

        # 3. 缩量特征：最近3天成交量 < 前20天均量 × shrink_ratio
        vols = recent["vol"].values
        recent_3 = np.mean(vols[-3:])
        prior_20 = np.mean(vols[-23:-3]) if len(vols) > 23 else np.mean(vols[:-3])
        if prior_20 <= 0:
            return None
        vol_ratio = recent_3 / prior_20
        if vol_ratio > shrink_r:
            return None

        # 4. 均线多头：MA5 > MA10 > MA20
        ma5 = latest.get("ma5")
        ma10 = latest.get("ma10")
        ma20 = latest.get("ma20")
        if not pd.isna(ma5) and not pd.isna(ma10) and not pd.isna(ma20):
            if not (ma5 > ma10 > ma20):
                return None

        score = min(95, 70 + (1 - vol_ratio) * 30 + deviation * 5)
        return StrategyResult(
            ts_code=ts_code, name=name,
            reason=f"缩量回踩MA{ma_p}({ma_val:.2f})，偏离{deviation:.1f}%，"
                   f"量萎缩至{vol_ratio:.0%}，均线多头，短线入场",
            score=round(score, 1),
            extra={
                "close": round(float(close), 2),
                "ma_val": round(float(ma_val), 2),
                "deviation_pct": round(float(deviation), 2),
                "vol_ratio": round(float(vol_ratio), 2),
                "ma_period": ma_p,
            },
        )
