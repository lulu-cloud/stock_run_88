"""量化模型注册中心

类比 StrategyRegistry，支持按名称创建模型实例。
"""

from typing import Type, Optional
from backend.quant.base import BaseQuantModel


class QuantModelRegistry:
    """量化模型注册表"""

    _registry: dict[str, Type[BaseQuantModel]] = {}

    @classmethod
    def register(cls, model_cls: Type[BaseQuantModel]):
        """装饰器：注册量化模型"""
        cls._registry[model_cls.name] = model_cls
        return model_cls

    @classmethod
    def get(cls, name: str) -> Optional[Type[BaseQuantModel]]:
        return cls._registry.get(name)

    @classmethod
    def create(cls, name: str, **kwargs) -> Optional[BaseQuantModel]:
        model_cls = cls.get(name)
        return model_cls(**kwargs) if model_cls else None

    @classmethod
    def list_all(cls) -> list[str]:
        return list(cls._registry.keys())
