"""横盘突破策略

价格在 15-25 天窄幅区间内横盘 (振幅<5%)，成交量逐步萎缩，
随后某日放量 (2x+) 大阳线 (涨幅>4%) 突破。
中线策略，回溯 60 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class ConsolidationStrategy(BaseStrategy):
    """横盘突破策略"""

    name = "consolidation"
    description = "横盘突破：价格窄幅盘整后放量大阳线突破，捕捉趋势启动点"
    recommended_lookback = 60

    def __init__(self, amp_max_pct: float = 5.0, window_min: int = 12,
                 window_max: int = 25, vol_expand: float = 2.0,
                 breakout_pct: float = 4.0, **kwargs):
        super().__init__(
            amp_max_pct=amp_max_pct, window_min=window_min,
            window_max=window_max, vol_expand=vol_expand,
            breakout_pct=breakout_pct, **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        amp_max = self.get_param("amp_max_pct", 5.0)
        win_min = self.get_param("window_min", 12)
        win_max = self.get_param("window_max", 25)
        vol_exp = self.get_param("vol_expand", 2.0)
        brk_pct = self.get_param("breakout_pct", 4.0)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 60:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(win_max + 10)
        latest = recent.iloc[-1]
        close = latest["close"]
        pct_chg = latest.get("pct_chg", 0)

        # 突破日: 大阳线
        if pct_chg < brk_pct:
            return None

        # 检查是否在 MA60 上方 (健康的盘整应在中期均线上方)
        ma60 = latest.get("ma60")
        if pd.isna(ma60) or close < ma60:
            return None

        # 在突破日之前找一个横盘区间 (跳过突破日本身)
        pre_breakout = recent.iloc[-(win_max + 2):-1]
        best_window = None
        best_shrink = 999

        for w_size in range(win_min, min(win_max + 1, len(pre_breakout))):
            window = pre_breakout.iloc[-w_size:]
            high_max = window["high"].max()
            low_min = window["low"].min()
            if low_min <= 0:
                continue
            amplitude = (high_max - low_min) / low_min * 100
            if amplitude > amp_max:
                continue

            # 成交量递减检查: 后 1/3 < 前 2/3 * 0.7
            vols = window["vol"].values
            split = int(len(vols) * 2 / 3)
            if split < 2:
                continue
            front_vol = np.mean(vols[:split])
            back_vol = np.mean(vols[split:])
            if front_vol <= 0:
                continue
            shrink = back_vol / front_vol

            if shrink < best_shrink:
                best_shrink = shrink
                best_window = {
                    "size": w_size, "high_max": high_max, "low_min": low_min,
                    "amplitude": amplitude, "shrink": shrink, "avg_vol": front_vol,
                }

        if best_window is None:
            return None

        # 突破日成交量 vs 横盘期均量
        today_vol = latest["vol"]
        if today_vol < best_window["avg_vol"] * vol_exp:
            return None

        break_strength = pct_chg / amp_max  # 突破力度
        score = min(95, 60 + break_strength * 15 + (today_vol / best_window["avg_vol"] - vol_exp) * 5)
        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=f"横盘{best_window['size']}天(振幅{best_window['amplitude']:.1f}%)后放量突破，"
                   f"涨幅{pct_chg:.1f}%，量比{today_vol/best_window['avg_vol']:.1f}x",
            score=round(score, 1),
            extra={
                "consolidation_days": best_window["size"],
                "amplitude_pct": round(best_window["amplitude"], 2),
                "vol_expand_ratio": round(float(today_vol / best_window["avg_vol"]), 2),
                "breakout_pct": round(float(pct_chg), 2),
                "close": round(float(close), 2),
                "ma60": round(float(ma60), 2),
            },
        )
