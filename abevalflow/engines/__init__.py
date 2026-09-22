"""Engine registry for unified scorecard.

Provides a factory pattern for evaluation engines. Each engine can
read its results and convert them to a standardized GateResult.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from abevalflow.engines.base import EvalEngine

_ENGINE_REGISTRY: dict[str, type[EvalEngine]] = {}


def register_engine(name: str):
    """Decorator to register an engine class."""

    def decorator(cls: type[EvalEngine]) -> type[EvalEngine]:
        _ENGINE_REGISTRY[name] = cls
        return cls

    return decorator


def get_engine(name: str) -> EvalEngine:
    """Get an engine instance by name.

    Args:
        name: Engine name (harbor, ase, a2a, mcpchecker)

    Returns:
        Engine instance

    Raises:
        KeyError: If engine is not registered
    """
    if name not in _ENGINE_REGISTRY:
        available = ", ".join(_ENGINE_REGISTRY.keys())
        raise KeyError(f"Unknown engine '{name}'. Available: {available}")
    return _ENGINE_REGISTRY[name]()


def get_all_engines() -> list[str]:
    """Get all registered engine names."""
    return list(_ENGINE_REGISTRY.keys())


from abevalflow.engines.a2a import A2AEngine
from abevalflow.engines.aeh import AEHEngine
from abevalflow.engines.aeh_openshell_openclaw import AEHOpenShellOpenClawEngine
from abevalflow.engines.ase import ASEEngine
from abevalflow.engines.harbor import HarborEngine
from abevalflow.engines.mcpchecker import MCPCheckerEngine

__all__ = [
    "register_engine",
    "get_engine",
    "get_all_engines",
    "HarborEngine",
    "ASEEngine",
    "A2AEngine",
    "AEHEngine",
    "AEHOpenShellOpenClawEngine",
    "MCPCheckerEngine",
]
