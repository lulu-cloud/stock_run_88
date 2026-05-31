"""策略注册中心"""

from typing import Type, Optional
from backend.strategies.base import BaseStrategy


class StrategyRegistry:
    """策略注册表 — 装饰器模式"""

    _registry: dict[str, Type[BaseStrategy]] = {}

    @classmethod
    def register(cls, strategy_cls: Type[BaseStrategy]):
        cls._registry[strategy_cls.name] = strategy_cls
        return strategy_cls

    @classmethod
    def get(cls, name: str) -> Optional[Type[BaseStrategy]]:
        return cls._registry.get(name)

    @classmethod
    def create(cls, name: str, **params) -> Optional[BaseStrategy]:
        cls_ = cls.get(name)
        return cls_(**params) if cls_ else None

    @classmethod
    def list_all(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def list_with_info(cls) -> list[dict]:
        return [
            {"name": s.name, "description": s.description}
            for s in cls._registry.values()
        ]
