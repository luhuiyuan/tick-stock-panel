"""Factory function for Cost classes."""

from ..base import BaseCost


def cost_factory(model: str, *args, **kwargs) -> BaseCost:
    for cls in BaseCost.__subclasses__():
        if cls.model == model:
            return cls(*args, **kwargs)
    raise ValueError("Not such model: {}".format(model))
