"""策略基类"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class StrategyResult:
    """策略筛选结果"""
    ts_code: str       # 股票代码
    name: str          # 股票名称
    reason: str        # 命中原因
    score: float = 0.0 # 评分
    extra: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """策略基类 — 所有策略必须继承并实现 filter 方法"""

    name: str = "base"
    description: str = ""
    recommended_lookback: int = 60  # 推荐回溯天数（短线10-30，中线30-120，长线120-250）

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def filter(self, ts_code: str, name: str, df: pd.DataFrame) -> Optional[StrategyResult]:
        """判断单只股票是否满足策略条件

        Args:
            ts_code: 股票代码
            name: 股票名称
            df: 日线 DataFrame（按 trade_date 升序，含 ma5/ma10/ma20/ma60 列）

        Returns:
            StrategyResult 如果满足条件，None 如果不满足
        """
        ...

    def get_param(self, key: str, default=None):
        return self.params.get(key, default)
