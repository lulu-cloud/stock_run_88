"""策略模块 — 导入即注册"""
from backend.strategies.base import BaseStrategy, StrategyResult
from backend.strategies.registry import StrategyRegistry
from backend.strategies.momentum import MomentumStrategy
from backend.strategies.trend import TrendStrategy
from backend.strategies.ma_pullback import MAPullbackStrategy
from backend.strategies.ma_cross import MACrossStrategy
from backend.strategies.macd import MACDStrategy
from backend.strategies.kline_pullback import KlinePullbackStrategy
from backend.strategies.consolidation import ConsolidationStrategy
from backend.strategies.uptrend import UptrendStrategy
from backend.strategies.bottom_reversal import BottomReversalStrategy
from backend.strategies.box_range import BoxRangeStrategy
from backend.strategies.sentiment_cycle import SentimentCycleStrategy
from backend.strategies.volume_pullback import VolumePullbackStrategy
from backend.strategies.yang_three_yin import YangThreeYinStrategy
from backend.strategies.ma_bullish import MABullishStrategy
from backend.strategies.ma_bullish_pullback import MABullishPullbackStrategy
