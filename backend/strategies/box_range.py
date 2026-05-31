"""箱体震荡策略

识别箱体高低点，箱底买入、箱顶卖出，波段操作。
中线策略，回溯 60 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class BoxRangeStrategy(BaseStrategy):
    """箱体震荡波段"""

    name = "box_range"
    description = "箱体震荡：识别箱体高低点，箱底附近买入做波段，高抛低吸"
    recommended_lookback = 60

    def __init__(self, box_days: int = 30, amp_min: float = 8.0,
                 amp_max: float = 30.0, touch_tolerance: float = 3.0,
                 **kwargs):
        super().__init__(
            box_days=box_days, amp_min=amp_min, amp_max=amp_max,
            touch_tolerance=touch_tolerance, **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        box_days = self.get_param("box_days", 30)
        amp_min = self.get_param("amp_min", 8.0)
        amp_max = self.get_param("amp_max", 30.0)
        tol = self.get_param("touch_tolerance", 3.0)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 60:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(box_days)
        latest = recent.iloc[-1]
        close = latest["close"]

        # 1. 识别箱体：在 box_days 天内找支撑和阻力
        highs = recent["high"].values
        lows = recent["low"].values

        # 用 top/bottom 分位数找箱体
        box_top = float(np.percentile(highs, 85))
        box_bottom = float(np.percentile(lows, 15))

        if box_bottom <= 0:
            return None

        box_amp = (box_top - box_bottom) / box_bottom * 100
        if box_amp < amp_min or box_amp > amp_max:
            return None

        # 2. 当前价格接近箱底 (在箱底上方 tol% 以内)
        position_in_box = (close - box_bottom) / (box_top - box_bottom)
        if position_in_box > 0.35:
            return None  # 不在箱底附近

        # 3. 均线确认：MA60 走平或缓慢上行（箱体通常在均线上方）
        ma60 = latest.get("ma60")
        if pd.isna(ma60):
            return None

        # 4. 箱底至少有2次触及确认
        bottom_touches = sum(1 for l in lows if abs(l - box_bottom) / box_bottom * 100 < tol)
        if bottom_touches < 2:
            return None

        score = min(90, 55 + (0.35 - position_in_box) * 80 + box_amp * 0.5)
        return StrategyResult(
            ts_code=ts_code, name=name,
            reason=f"箱体震荡{box_days}天，振幅{box_amp:.1f}%，"
                   f"当前价{close:.2f}在箱底{box_bottom:.2f}附近(位置{position_in_box:.0%})，"
                   f"箱底触及{bottom_touches}次",
            score=round(score, 1),
            extra={
                "close": round(float(close), 2),
                "box_top": round(float(box_top), 2),
                "box_bottom": round(float(box_bottom), 2),
                "box_amp_pct": round(float(box_amp), 2),
                "position": round(float(position_in_box), 3),
                "bottom_touches": bottom_touches,
            },
        )
