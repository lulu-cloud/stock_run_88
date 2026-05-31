"""K 线回踩 MA20 策略

股价从近期高点回落至 MA20 附近 (偏离 3% 以内)，
成交量先缩后放，形成企稳反弹信号。
区别于 ma_pullback (同时检查 MA20+MA60)，本策略仅关注 MA20。
中线策略，回溯 30 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class KlinePullbackStrategy(BaseStrategy):
    """K线回踩 MA20 策略"""

    name = "kline_pullback"
    description = "K线回踩MA20：股价从高点回落至20日均线附近企稳，缩量后放量反弹"
    recommended_lookback = 30

    def __init__(self, pullback_pct: float = 3.0, peak_lookback: int = 15,
                 shrink_ratio: float = 0.7, expand_ratio: float = 1.5,
                 **kwargs):
        super().__init__(
            pullback_pct=pullback_pct, peak_lookback=peak_lookback,
            shrink_ratio=shrink_ratio, expand_ratio=expand_ratio,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        pullback_pct = self.get_param("pullback_pct", 3.0)
        peak_lb = self.get_param("peak_lookback", 15)
        shrink_r = self.get_param("shrink_ratio", 0.7)
        expand_r = self.get_param("expand_ratio", 1.5)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 30:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(peak_lb + 10)

        latest = recent.iloc[-1]
        close = latest["close"]
        ma20 = latest.get("ma20")
        if pd.isna(ma20) or ma20 == 0:
            return None

        # 当前价格在 MA20 附近 (偏离 3% 内)
        deviation = abs((close - ma20) / ma20 * 100)
        if deviation > pullback_pct:
            return None

        # 找近期高点: 前 peak_lb 天内最高收盘价
        peak_window = recent.iloc[-(peak_lb + 5):-1]
        if len(peak_window) < 5:
            return None

        peak_close = peak_window["close"].max()
        peak_ma20 = peak_window.loc[peak_window["close"].idxmax(), "ma20"]
        if pd.isna(peak_ma20) or peak_ma20 == 0:
            return None

        # 从高点回落幅度 > 5% (才是真正的回调)
        pullback_from_peak = (peak_close - close) / peak_close * 100
        if pullback_from_peak < 5:
            return None

        # MA20 方向检查: 走平或向上 (MA20 5天前 <= MA20 今天)
        if len(recent) >= 6:
            ma20_5d_ago = recent.iloc[-6].get("ma20")
            if not pd.isna(ma20_5d_ago) and ma20 < ma20_5d_ago:
                return None

        # 成交量分析: 前段缩量 + 今日放量
        vols = recent["vol"].values
        if len(vols) < 20:
            return None

        prior_vol = np.mean(vols[-15:-3]) if len(vols) > 15 else np.mean(vols[:-3])
        recent_vol = np.mean(vols[-3:])
        if prior_vol <= 0:
            return None

        vol_ratio = recent_vol / prior_vol

        # 缩量确认: 前段成交量小于正常
        # 放量反弹: 最近3天成交量放大
        if vol_ratio < expand_r:
            # 缩量企稳信号, 分数较低
            score = 55
            signal_type = "缩量企稳"
        else:
            score = 75
            signal_type = "放量反弹"

        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=f"从高点{peak_close:.2f}回落{pullback_from_peak:.1f}%至MA20({ma20:.2f})附近"
                   f"(偏离{deviation:.1f}%)，量比{vol_ratio:.2f}，{signal_type}",
            score=round(min(score + deviation * 2, 95), 1),
            extra={
                "close": round(float(close), 2),
                "ma20": round(float(ma20), 2),
                "deviation_pct": round(float(deviation), 2),
                "pullback_pct": round(float(pullback_from_peak), 2),
                "vol_ratio": round(float(vol_ratio), 2),
                "signal": signal_type,
            },
        )
