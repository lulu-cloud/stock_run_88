"""情绪周期策略

通过换手率极值和价格偏离度判断市场情绪高低点，
在情绪低点附近逆情绪布局。
中线策略，回溯 60 天。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class SentimentCycleStrategy(BaseStrategy):
    """情绪周期 — 逆情绪布局"""

    name = "sentiment_cycle"
    description = "情绪周期：换手率极值+价格偏离度判断市场情绪，在低情绪区逆势布局"
    recommended_lookback = 60

    def __init__(self, low_sentiment_percentile: float = 25.0,
                 ma_deviation_max: float = 8.0, sentiment_recovery: bool = True,
                 **kwargs):
        super().__init__(
            low_sentiment_percentile=low_sentiment_percentile,
            ma_deviation_max=ma_deviation_max,
            sentiment_recovery=sentiment_recovery,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        sent_pct = self.get_param("low_sentiment_percentile", 25.0)
        dev_max = self.get_param("ma_deviation_max", 8.0)
        recovery = self.get_param("sentiment_recovery", True)

        if "ST" in name or "*ST" in name or "退" in name:
            return None
        if len(df) < 60:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(60)
        latest = recent.iloc[-1]
        close = latest["close"]

        # 1. 换手率情绪：低换手 = 低情绪 = 无人关注 = 布局时机
        turnover_vals = recent["turnover_rate"].dropna().values
        if len(turnover_vals) < 30:
            return None

        current_turn = float(latest.get("turnover_rate", 0) or 0)
        if current_turn <= 0:
            return None

        # 当前换手率在近期百分位
        turn_percentile = (turnover_vals < current_turn).sum() / len(turnover_vals) * 100
        is_low_sentiment = turn_percentile <= sent_pct

        # 如果不在低情绪区，且没有恢复信号，跳过
        if not is_low_sentiment and not recovery:
            return None

        # 2. 价格偏离度：相对 MA20 的位置
        ma20 = latest.get("ma20")
        ma60 = latest.get("ma60")
        if pd.isna(ma20) or ma20 <= 0:
            return None

        deviation_ma20 = (close - ma20) / ma20 * 100

        # 低情绪 + 价格在均线附近或以下 = 好的逆势布局点
        if is_low_sentiment:
            if abs(deviation_ma20) > dev_max:
                return None  # 偏离太大不安全

            score = max(55, 85 - turn_percentile * 0.8 - abs(deviation_ma20) * 2)
            return StrategyResult(
                ts_code=ts_code, name=name,
                reason=f"换手率{current_turn:.2f}%处于60天{turn_percentile:.0f}分位(低情绪区)，"
                       f"价格偏离MA20{deviation_ma20:.1f}%，逆情绪布局点",
                score=round(score, 1),
                extra={
                    "close": round(float(close), 2),
                    "turnover_rate": round(float(current_turn), 2),
                    "turnover_percentile": round(float(turn_percentile), 1),
                    "deviation_ma20": round(float(deviation_ma20), 2),
                    "sentiment": "low",
                },
            )

        # 恢复信号：之前低情绪，现在换手率回升
        if recovery:
            prev_turn = turnover_vals[-6:-1].mean() if len(turnover_vals) > 5 else current_turn
            if current_turn > prev_turn * 1.2 and turn_percentile < 50:
                return StrategyResult(
                    ts_code=ts_code, name=name,
                    reason=f"情绪回升：换手率从{prev_turn:.2f}%升至{current_turn:.2f}%，"
                           f"价格偏离MA20{deviation_ma20:.1f}%，关注反弹",
                    score=65,
                    extra={
                        "close": round(float(close), 2),
                        "turnover_rate": round(float(current_turn), 2),
                        "turnover_percentile": round(float(turn_percentile), 1),
                        "deviation_ma20": round(float(deviation_ma20), 2),
                        "sentiment": "recovering",
                    },
                )

        return None
