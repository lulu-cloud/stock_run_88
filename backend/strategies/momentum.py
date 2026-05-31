"""龙头打板战法

识别连续涨停的龙头股/妖股，通过换手率判断所处阶段：
- 一字板期（换手率<1%）：主力锁仓，无法介入
- 换手板期（换手率3%-25%）：充分换手，最佳接力时机
- 高位风险期（换手率>25%）：可能主力出货

注：不使用量比，一字板时成交量极小量比无效。关注妖股中后期换手率是否打上来。
"""

import pandas as pd
import numpy as np
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class MomentumStrategy(BaseStrategy):
    """龙头打板战法"""

    name = "momentum"
    description = "龙头打板战法：追踪涨停龙头，分析连板阶段和换手率变化，识别妖股接力机会"
    recommended_lookback = 10  # 短线：连板是短期现象

    def __init__(self,
                 min_limit_up_days: int = 2,
                 lookback_days: int = 15,
                 healthy_turnover_min: float = 3.0,
                 healthy_turnover_max: float = 25.0,
                 danger_turnover: float = 25.0,
                 **kwargs):
        super().__init__(
            min_limit_up_days=min_limit_up_days,
            lookback_days=lookback_days,
            healthy_turnover_min=healthy_turnover_min,
            healthy_turnover_max=healthy_turnover_max,
            danger_turnover=danger_turnover,
            **kwargs
        )

    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> StrategyResult | None:
        min_lu = self.get_param("min_limit_up_days")
        lookback = self.get_param("lookback_days")
        healthy_min = self.get_param("healthy_turnover_min")
        healthy_max = self.get_param("healthy_turnover_max")
        danger = self.get_param("danger_turnover")

        if "ST" in name or "*ST" in name or "退" in name or "指数" in name:
            return None
        if len(df) < lookback + 5:
            return None

        df = df.sort_values("trade_date").reset_index(drop=True)
        recent = df.tail(lookback)
        if len(recent) < min_lu + 5:
            return None

        if "pct_chg" not in recent.columns or "close" not in recent.columns:
            return None

        limit_up_threshold = 9.5
        pct_values = recent["pct_chg"].fillna(0).values
        n = len(pct_values)

        # 找连板区间
        streaks = []
        i = 0
        while i < n:
            if pct_values[i] >= limit_up_threshold:
                start = i
                while i < n and pct_values[i] >= limit_up_threshold:
                    i += 1
                count = i - start
                if count >= min_lu:
                    streaks.append((start, i - 1, count))
            else:
                i += 1

        if not streaks:
            return None

        # 优先选择仍在最近3个交易日内的连板，否则说明热度已经明显过期。
        fresh_streaks = [s for s in streaks if (n - 1 - s[1]) <= 2]
        if not fresh_streaks:
            return None
        best = fresh_streaks[-1]
        start_idx, end_idx, lu_count = best

        # 分析换手率
        streak_turnover = []
        for idx in range(start_idx, end_idx + 1):
            tr = recent.iloc[idx].get("turnover_rate")
            streak_turnover.append(float(tr) if not pd.isna(tr) else 0)

        current_turnover = streak_turnover[-1]
        yi_zi_count = sum(1 for t in streak_turnover if t < 1.0)

        # 判断阶段
        stage, stage_reason = self._classify_stage(
            streak_turnover, current_turnover, lu_count,
            healthy_min, healthy_max, danger
        )

        start_close = recent.iloc[start_idx]["close"]
        if pd.isna(start_close) or start_close <= 0:
            return None
        total_pct = (recent.iloc[end_idx]["close"] / start_close - 1) * 100

        # 全一字板但连板少，不推荐
        score = min(lu_count * 18, 100)
        if yi_zi_count == lu_count and lu_count < 5:
            stage = "一字板锁仓期"
            stage_reason = f"连续{lu_count}个一字板，换手率极低({current_turnover:.2f}%)，主力锁仓无法介入"
            score = min(score, 35)
        elif current_turnover > danger * 1.3:
            score = min(score, 45)

        return StrategyResult(
            ts_code=ts_code,
            name=name,
            reason=f"[{stage}] {lu_count}连板，{stage_reason}",
            score=score,
            extra={
                "limit_up_days": lu_count,
                "total_pct": round(total_pct, 2),
                "stage": stage,
                "current_turnover": round(current_turnover, 2),
                "yi_zi_count": yi_zi_count,
                "huan_shou_count": lu_count - yi_zi_count,
                "close": round(recent.iloc[end_idx]["close"], 2),
                "streak_end_date": str(recent.iloc[end_idx]["trade_date"]),
            },
        )

    def _classify_stage(self, streak_turnover, current, lu_count, hmin, hmax, danger):
        if not streak_turnover:
            return "数据不足", "无法判断"

        low_count = sum(1 for t in streak_turnover if t < 1.0)
        healthy_count = sum(1 for t in streak_turnover if hmin <= t <= hmax)
        high_count = sum(1 for t in streak_turnover if t > danger)

        if low_count == len(streak_turnover):
            if lu_count >= 5:
                return "一字板蓄力期", f"连续{lu_count}个一字板，换手率全部<1%，主力高度控盘，开板即是机会"
            return "一字板起步期", f"{lu_count}个一字板刚起步，继续观察开板时机"

        if low_count > 0 and healthy_count > 0 and high_count == 0:
            if current >= hmin:
                return "换手板接力期", f"前{low_count}个一字板→转入换手板，换手{current:.2f}%(健康{hmin}%-{hmax}%)，有望继续走强"
            return "一字转换手过渡期", f"刚从一字板打开，换手{current:.2f}%正在放量"

        if healthy_count >= len(streak_turnover) * 0.7 and high_count == 0:
            return "换手板合力期", f"{lu_count}连板以换手板为主，换手{current:.2f}%，市场合力推动"

        if high_count > 0:
            if current > danger * 1.3:
                return "高位风险期", f"换手率{current:.2f}%过高，主力可能出货，不宜追高"
            return "高位换手期", f"换手率偏高{current:.2f}%，{lu_count}连板后分歧加大"

        return "缩量加速期", f"{lu_count}连板换手率{current:.2f}%偏低，缩量加速中"
