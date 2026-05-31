"""一阳夹三阴 K线形态策略

大阳线后连续3根小阴线（缩量），再出阳线确认，趋势延续信号。
短线策略，回溯 20 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class YangThreeYinStrategy(BaseStrategy):
    """一阳夹三阴 K线形态"""

    name = "yang_three_yin"
    description = "一阳夹三阴：大阳线后3根缩量小阴线调整，再出阳线确认，趋势延续"
    recommended_lookback = 20

    def __init__(self, yang_pct_min: float = 3.0, yin_pct_max: float = 2.0,
                 vol_shrink_ratio: float = 0.6, confirm_yang_min: float = 1.5,
                 **kwargs):
        super().__init__(
            yang_pct_min=yang_pct_min, yin_pct_max=yin_pct_max,
            vol_shrink_ratio=vol_shrink_ratio, confirm_yang_min=confirm_yang_min,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        yang_min = self.get_param("yang_pct_min", 3.0)
        yin_max = self.get_param("yin_pct_max", 2.0)
        vol_shrink = self.get_param("vol_shrink_ratio", 0.6)
        conf_min = self.get_param("confirm_yang_min", 1.5)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 20:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(10)
        if len(recent) < 6:
            return None

        # 形态结构：位置 [0]=大阳, [1][2][3]=小阴, [4]=确认阳
        for offset in range(len(recent) - 5):
            d0 = recent.iloc[offset]      # 大阳
            d1 = recent.iloc[offset + 1]  # 小阴1
            d2 = recent.iloc[offset + 2]  # 小阴2
            d3 = recent.iloc[offset + 3]  # 小阴3
            d4 = recent.iloc[offset + 4]  # 确认阳

            # 大阳线
            if d0["pct_chg"] < yang_min:
                continue
            # 三根小阴线（跌幅不超过 yin_max，且不能是阳线）
            if (d1["pct_chg"] > 0 or d1["pct_chg"] < -yin_max or
                d2["pct_chg"] > 0 or d2["pct_chg"] < -yin_max or
                d3["pct_chg"] > 0 or d3["pct_chg"] < -yin_max):
                continue
            # 缩量（三阴均量 < 大阳量 × vol_shrink）
            yin_avg_vol = (d1["vol"] + d2["vol"] + d3["vol"]) / 3
            if d0["vol"] <= 0:
                continue
            if yin_avg_vol / d0["vol"] > vol_shrink:
                continue
            # 确认阳线
            if d4["pct_chg"] < conf_min:
                continue
            # 收盘价不低于大阳线最低价（形态有效）
            if d3["close"] < d0["low"] * 0.98:
                continue

            score = min(95, 65 + d4["pct_chg"] * 3 + d0["pct_chg"] * 2)
            return StrategyResult(
                ts_code=ts_code, name=name,
                reason=f"一阳夹三阴形态：{d0['trade_date']}大阳{d0['pct_chg']:.1f}%→"
                       f"三阴缩量→{d4['trade_date']}确认阳{d4['pct_chg']:.1f}%，趋势延续",
                score=round(score, 1),
                extra={
                    "close": round(float(d4["close"]), 2),
                    "yang_pct": round(float(d0["pct_chg"]), 2),
                    "yin_vol_ratio": round(float(yin_avg_vol / d0["vol"]), 2),
                    "confirm_pct": round(float(d4["pct_chg"]), 2),
                    "pattern_date": str(d4["trade_date"]),
                },
            )

        return None
