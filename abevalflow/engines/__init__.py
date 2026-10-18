"""AEH scorecard adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from abevalflow.engines.base import EvalEngine
_ENGINE_REGISTRY: dict[str, type[EvalEngine]] = {}


def register_engine(name: str):
    def decorator(cls: type[EvalEngine]) -> type[EvalEngine]:
        _ENGINE_REGISTRY[name] = cls
        return cls

    return decorator


def get_engine(name: str) -> EvalEngine:
    if name not in _ENGINE_REGISTRY:
        raise KeyError(f"Unknown engine '{name}'. Available: aeh")
    return _ENGINE_REGISTRY[name]()


def get_all_engines() -> list[str]:
    return list(_ENGINE_REGISTRY)


from abevalflow.engines.aeh import AEHEngine

__all__ = ["get_engine", "get_all_engines", "register_engine", "AEHEngine"]
