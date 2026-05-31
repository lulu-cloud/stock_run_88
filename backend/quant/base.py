"""ML/DL 量化模型基类

定义统一的训练、预测、持久化接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class QuantPrediction:
    """量化模型预测输出"""

    ts_code: str            # 股票代码
    trade_date: str         # 预测生成日期
    direction: str          # "buy" | "hold" | "sell"
    confidence: float       # 置信度 0.0-1.0
    timeframe: str          # "short" | "medium" | "long"
    target_return: float    # 预期收益百分比
    risk_score: float       # 风险评分 0.0(低风险)-1.0(高风险)
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ts_code": self.ts_code,
            "trade_date": self.trade_date,
            "direction": self.direction,
            "confidence": self.confidence,
            "timeframe": self.timeframe,
            "target_return": self.target_return,
            "risk_score": self.risk_score,
            "extra": self.extra,
        }


class BaseQuantModel(ABC):
    """量化模型基类

    子类需实现 train / predict / get_features 三个抽象方法。

    使用示例:
        class LSTMPriceModel(BaseQuantModel):
            name = "lstm_price"

            def train(self, data, **kwargs):
                # 训练 LSTM 模型
                ...

            def predict(self, ts_code, historical_data):
                # 返回 QuantPrediction
                ...

            def get_features(self):
                return ["close", "vol", "ma5", "ma20", ...]
    """

    name: str = "base_quant"

    @abstractmethod
    def train(self, data: pd.DataFrame, **kwargs) -> None:
        """在历史数据上训练模型

        Args:
            data: 包含特征列和目标列的 DataFrame (多股票拼接)
            kwargs: 模型特定超参数 (learning_rate, epochs, batch_size 等)
        """
        ...

    @abstractmethod
    def predict(self, ts_code: str, historical_data: pd.DataFrame) -> QuantPrediction:
        """对单只股票生成预测

        Args:
            ts_code: 股票代码
            historical_data: 截至预测日的 OHLCV + 特征数据 (升序)

        Returns:
            QuantPrediction (方向 + 置信度 + 风险)
        """
        ...

    @abstractmethod
    def get_features(self) -> list[str]:
        """返回模型需要的特征列名列表"""
        ...

    def save(self, path: str) -> None:
        """保存模型到磁盘 (默认使用 joblib，子类可覆盖)"""
        import joblib
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: str) -> "BaseQuantModel":
        """从磁盘加载模型 (默认使用 joblib，子类可覆盖)"""
        import joblib
        return joblib.load(path)
