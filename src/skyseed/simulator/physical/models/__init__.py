"""Replaceable physical algorithms, grouped by their role."""
from importlib import import_module
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .position import PositionModel
    from .circular_orbit import CircularOrbitPositionModel
    from .zone_membership import ZoneMembershipModel, DefaultZoneMembershipModel
    from .links import PhysicalLinkModel, GeometricPhysicalLinkModel

_MODULES = {
    "PositionModel": ".position", "CircularOrbitPositionModel": ".circular_orbit",
    "ZoneMembershipModel": ".zone_membership", "DefaultZoneMembershipModel": ".zone_membership",
    "PhysicalLinkModel": ".links", "GeometricPhysicalLinkModel": ".links",
}
__all__ = list(_MODULES)
def __getattr__(name):
    if name not in _MODULES:
        raise AttributeError(name)
    value = getattr(import_module(_MODULES[name], __name__), name)
    globals()[name] = value
    return value

def __dir__():
    return sorted(set(globals()) | set(__all__))
