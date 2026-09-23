"""routing role contracts and implementations."""
from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._computation import (
        validate_route_worker_limit,
    )
    from .gateway import (
        GatewayHopCountRoutePlanningModel,
    )
    from .model import (
        RoutePlanningModel,
        RoutePlanningResult,
    )

_EXPORTS = {
    "validate_route_worker_limit": "._computation",
    "GatewayHopCountRoutePlanningModel": ".gateway",
    "RoutePlanningModel": ".model",
    "RoutePlanningResult": ".model",
}

__all__ = [
    'validate_route_worker_limit',
    'GatewayHopCountRoutePlanningModel',
    'RoutePlanningModel',
    'RoutePlanningResult',
]

def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(_import_module(module, __name__), name)
    globals()[name] = value
    return value

def __dir__():
    return sorted(set(globals()) | set(__all__))
