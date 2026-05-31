"""MACD 策略

DIF 上穿 DEA 金叉买入，结合价格趋势确认。
中线策略，回溯 120 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry
from backend.data.indicators import compute_macd


@StrategyRegistry.register
class MACDStrategy(BaseStrategy):
    """MACD 金叉/死叉策略"""

    name = "macd"
    description = "MACD策略：DIF上穿DEA金叉信号，结合均线趋势确认，中线操作"
    recommended_lookback = 120

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9,
                 min_hist_strength: float = 0.05, **kwargs):
        super().__init__(
            fast=fast, slow=slow, signal=signal,
            min_hist_strength=min_hist_strength,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        fast = self.get_param("fast", 12)
        slow = self.get_param("slow", 26)
        signal_p = self.get_param("signal", 9)
        min_strength = self.get_param("min_hist_strength", 0.05)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < max(slow + signal_p + 10, 60):
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        df = compute_macd(df, fast, slow, signal_p)

        recent = df.tail(10)
        latest = recent.iloc[-1]

        # 金叉检测: 最近 5 天内出现 macd_buy_cross
        cross_idx = -1
        for i in range(len(recent) - 1, max(len(recent) - 6, -1), -1):
            if recent.iloc[i].get("macd_buy_cross"):
                cross_idx = i
                break

        if cross_idx < 0:
            return None

        cross_row = recent.iloc[cross_idx]
        dif_val = cross_row.get("dif", 0)
        dea_val = cross_row.get("dea", 0)
        if pd.isna(dif_val) or pd.isna(dea_val) or dea_val == 0:
            return None

        hist_strength = abs(dif_val - dea_val) / abs(dea_val)
        if hist_strength < min_strength:
            return None

        # 价格趋势确认: close > ma20
        ma20 = latest.get("ma20")
        close = latest["close"]
        if pd.isna(ma20) or close <= ma20:
            return None

        # DIF 在 DEA 上方 (金叉后持续)
        if dif_val <= dea_val:
            return None

        score = min(95, 50 + hist_strength * 200 + (close / ma20 - 1) * 100)
        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=f"MACD金叉，DIF({dif_val:.3f})上穿DEA({dea_val:.3f})，"
                   f"柱强{hist_strength:.2%}，价格在MA20之上，中线看多",
            score=round(score, 1),
            extra={
                "dif": round(float(dif_val), 4),
                "dea": round(float(dea_val), 4),
                "hist_strength": round(float(hist_strength), 4),
                "close": round(float(close), 2),
                "ma20": round(float(ma20), 2),
                "cross_offset": len(recent) - 1 - cross_idx,
            },
        )
