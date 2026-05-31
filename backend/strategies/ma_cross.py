"""MA 金叉策略

MA5 上穿 MA20 + 成交量放大确认。
短线策略，回溯 30 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class MACrossStrategy(BaseStrategy):
    """MA5/MA20 金叉策略"""

    name = "ma_cross"
    description = "MA5/MA20金叉：MA5上穿MA20形成金叉，成交量放大确认，短线看多"
    recommended_lookback = 30

    def __init__(self, volume_confirm_ratio: float = 1.2,
                 cross_within_days: int = 3, **kwargs):
        super().__init__(
            volume_confirm_ratio=volume_confirm_ratio,
            cross_within_days=cross_within_days,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        vol_ratio = self.get_param("volume_confirm_ratio", 1.2)
        cross_days = self.get_param("cross_within_days", 3)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 30:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(cross_days + 5)

        ma5_vals = recent["ma5"].values
        ma20_vals = recent["ma20"].values
        if pd.isna(ma5_vals[-1]) or pd.isna(ma20_vals[-1]):
            return None

        # 检测金叉: 前几日 MA5 <= MA20 且 最新 MA5 > MA20
        cross_found = False
        cross_offset = 0
        for offset in range(min(cross_days, len(recent) - 1)):
            prev_5 = ma5_vals[-2 - offset]
            prev_20 = ma20_vals[-2 - offset]
            curr_5 = ma5_vals[-1 - offset]
            curr_20 = ma20_vals[-1 - offset]
            if pd.isna(prev_5) or pd.isna(prev_20):
                continue
            if prev_5 <= prev_20 and curr_5 > curr_20:
                cross_found = True
                cross_offset = offset
                break

        if not cross_found:
            return None

        # 确认 MA5 仍在 MA20 上方
        if ma5_vals[-1] <= ma20_vals[-1]:
            return None

        latest = recent.iloc[-1]
        close = latest["close"]

        # 成交量确认: 金叉日及之后的成交量 > 20日均量 * vol_ratio
        recent_vols = recent["vol"].values
        avg_vol_20 = np.mean(recent_vols[:-1]) if len(recent_vols) > 1 else recent_vols[-1]
        if avg_vol_20 <= 0:
            return None

        # 检查金叉位置的成交量
        cross_vol = recent_vols[-1 - cross_offset]
        if cross_vol < avg_vol_20 * vol_ratio:
            return None

        # 过滤死叉后 (MA5 < MA20 且 差距在扩大)
        if len(ma5_vals) >= 3:
            gap_now = ma5_vals[-1] - ma20_vals[-1]
            gap_prev = ma5_vals[-2] - ma20_vals[-2]
            if gap_now < gap_prev and gap_now < 0:
                return None

        score = min(90, 60 + cross_days - cross_offset + 10 * (cross_vol / avg_vol_20 - 1))
        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=f"MA5({ma5_vals[-1]:.2f})上穿MA20({ma20_vals[-1]:.2f})，金叉信号，"
                   f"量比{cross_vol/avg_vol_20:.2f}，短线看多",
            score=round(score, 1),
            extra={
                "ma5": round(float(ma5_vals[-1]), 2),
                "ma20": round(float(ma20_vals[-1]), 2),
                "vol_ratio": round(float(cross_vol / avg_vol_20), 2),
                "close": round(float(close), 2),
                "cross_offset": cross_offset,
            },
        )
