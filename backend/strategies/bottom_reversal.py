"""底部放量反转策略

长期下跌后出现放量企稳信号，识别潜在趋势反转。
中线策略，回溯 120 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class BottomReversalStrategy(BaseStrategy):
    """底部放量反转"""

    name = "bottom_reversal"
    description = "底部放量反转：长期下跌后放量企稳，识别潜在趋势反转点"
    recommended_lookback = 120

    def __init__(self, decline_pct: float = 20.0, decline_days: int = 60,
                 vol_expand_ratio: float = 1.3, stabilize_days: int = 5,
                 **kwargs):
        super().__init__(
            decline_pct=decline_pct, decline_days=decline_days,
            vol_expand_ratio=vol_expand_ratio, stabilize_days=stabilize_days,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        decline_pct = self.get_param("decline_pct", 20.0)
        decline_days = self.get_param("decline_days", 60)
        vol_expand = self.get_param("vol_expand_ratio", 1.3)
        stab_days = self.get_param("stabilize_days", 5)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 120:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(decline_days)
        latest = recent.iloc[-1]
        close = latest["close"]

        # 1. 一段时期内大幅下跌
        peak_idx = recent["close"].idxmax()
        peak = recent.loc[peak_idx, "close"]
        trough = recent["close"].min()
        if peak <= 0:
            return None
        total_decline = (peak - trough) / peak * 100
        if total_decline < decline_pct:
            return None

        # 2. 当前价格从底部回升但仍在相对低位
        decline_from_peak = (peak - close) / peak * 100
        if decline_from_peak < decline_pct * 0.3:
            return None  # 跌幅不够
        if decline_from_peak < 5:
            return None  # 已经涨回来了

        # 3. 近期企稳：最后 stab_days 天价格不再创新低
        tail = recent.tail(stab_days)
        if tail["low"].min() <= (trough * 0.98):
            return None

        # 4. 成交量放大：最近量 > 前期均量 × vol_expand
        all_vol = recent["vol"].values
        split = len(all_vol) - stab_days
        if split < 10:
            return None
        prior_vol = np.mean(all_vol[:split])
        recent_vol = np.mean(all_vol[split:])
        if prior_vol <= 0:
            return None
        vol_ratio = recent_vol / prior_vol
        if vol_ratio < vol_expand:
            return None

        # 5. MA5 走平或上翘
        ma5_vals = tail["ma5"].values
        if pd.isna(ma5_vals[-1]) or pd.isna(ma5_vals[0]):
            return None
        if ma5_vals[-1] < ma5_vals[0]:
            return None

        score = min(95, 50 + total_decline * 0.5 + vol_ratio * 10)
        return StrategyResult(
            ts_code=ts_code, name=name,
            reason=f"{decline_days}天跌幅{total_decline:.1f}%，近期企稳回升，"
                   f"量比{vol_ratio:.1f}x，潜在底部反转",
            score=round(score, 1),
            extra={
                "close": round(float(close), 2),
                "total_decline_pct": round(total_decline, 2),
                "decline_from_peak_pct": round(decline_from_peak, 2),
                "vol_ratio": round(float(vol_ratio), 2),
                "peak": round(float(peak), 2),
                "trough": round(float(trough), 2),
            },
        )
