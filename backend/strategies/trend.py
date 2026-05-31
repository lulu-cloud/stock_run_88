"""动量趋势策略

识别多波趋势上涨行情，捕捉健康回调后的二次启动点。
扫描60天内涨停波次，评估趋势质量和回调机会。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class TrendStrategy(BaseStrategy):
    """动量趋势策略"""

    name = "trend"
    description = "动量趋势策略：识别多波趋势上涨行情，捕捉健康回调后的二次启动点"
    recommended_lookback = 60  # 中线：默认 lookback_days 即为60

    def __init__(self,
                 lookback_days: int = 60,
                 limit_up_threshold: float = 9.5,
                 min_waves: int = 2,
                 max_pullback_pct: float = 15.0,
                 healthy_turnover_min: float = 3.0,
                 healthy_turnover_max: float = 25.0,
                 min_total_gain: float = 20.0,
                 **kwargs):
        super().__init__(
            lookback_days=lookback_days,
            limit_up_threshold=limit_up_threshold,
            min_waves=min_waves,
            max_pullback_pct=max_pullback_pct,
            healthy_turnover_min=healthy_turnover_min,
            healthy_turnover_max=healthy_turnover_max,
            min_total_gain=min_total_gain,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        lookback = self.get_param("lookback_days")
        threshold = self.get_param("limit_up_threshold")
        min_waves = self.get_param("min_waves")
        max_pb = self.get_param("max_pullback_pct")
        tr_min = self.get_param("healthy_turnover_min")
        tr_max = self.get_param("healthy_turnover_max")
        min_gain = self.get_param("min_total_gain")

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < lookback + 5:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(lookback)

        # 找出所有涨停日并聚类成波
        lu_indices = [i for i in range(len(recent)) if recent.iloc[i]["pct_chg"] >= threshold]

        if not lu_indices:
            return None

        waves = self._cluster_waves(lu_indices)
        if len(waves) < min_waves:
            return None

        # 评估每波
        wave_data = []
        for wave in waves:
            w_df = recent.iloc[wave[0]:wave[-1] + 1]
            turnover_vals = [float(w_df.iloc[j].get("turnover_rate", 0) or 0) for j in range(len(w_df))]
            avg_tr = np.mean(turnover_vals) if turnover_vals else 0
            gain = (w_df.iloc[-1]["close"] / w_df.iloc[0]["close"] - 1) * 100
            wave_data.append({
                "start_idx": wave[0],
                "end_idx": wave[-1],
                "limit_up_count": len(wave),
                "avg_turnover": avg_tr,
                "gain": gain,
            })

        # 波间回调检查
        pullbacks = []
        for i in range(1, len(wave_data)):
            prev_high = recent.iloc[wave_data[i - 1]["end_idx"]]["high"]
            curr_start = recent.iloc[wave_data[i]["start_idx"]]["low"]
            pb_pct = (prev_high - curr_start) / prev_high * 100
            pullbacks.append(pb_pct)

        all_healthy_pb = all(pb <= max_pb for pb in pullbacks)
        all_healthy_tr = all(tr_min <= w["avg_turnover"] <= tr_max for w in wave_data)
        total_gain = (recent.iloc[wave_data[-1]["end_idx"]]["close"] / recent.iloc[wave_data[0]["start_idx"]]["close"] - 1) * 100

        if not all_healthy_pb or total_gain < min_gain:
            return None

        # 当前是否在波间回调期
        last_wave_end = wave_data[-1]["end_idx"]
        remaining = recent.iloc[last_wave_end + 1:]
        in_pullback = len(remaining) > 0 and len(remaining) < 10

        current_close = recent.iloc[-1]["close"]
        tr_val = float(recent.iloc[-1].get("turnover_rate", 0) or 0)

        if in_pullback:
            return StrategyResult(
                ts_code=ts_code,
                name=name,
                reason=f"{len(wave_data)}波趋势，累计涨{total_gain:.1f}%，回调{remaining.iloc[-1]['pct_chg']:.1f}%，等待二次启动",
                score=min(total_gain, 100),
                extra={
                    "waves": len(wave_data),
                    "total_gain": round(total_gain, 2),
                    "current_pullback": round(float(remaining.iloc[-1]["pct_chg"]), 2),
                    "avg_turnover": round(tr_val, 2),
                    "latest_close": round(current_close, 2),
                    "state": "pullback",
                },
            )

        if total_gain >= min_gain and all_healthy_tr:
            return StrategyResult(
                ts_code=ts_code,
                name=name,
                reason=f"{len(wave_data)}波动量趋势，累计涨{total_gain:.1f}%，均线多头排列，趋势延续中",
                score=min(total_gain * 0.8, 100),
                extra={
                    "waves": len(wave_data),
                    "total_gain": round(total_gain, 2),
                    "avg_turnover": round(tr_val, 2),
                    "latest_close": round(current_close, 2),
                    "state": "trending",
                },
            )

        return None

    def _cluster_waves(self, lu_indices: list[int]) -> list[list[int]]:
        """聚类涨停日为波次"""
        if not lu_indices:
            return []
        waves = [[lu_indices[0]]]
        for idx in lu_indices[1:]:
            if idx - waves[-1][-1] <= 3:
                waves[-1].append(idx)
            else:
                waves.append([idx])
        return [w for w in waves if len(w) >= 1]
