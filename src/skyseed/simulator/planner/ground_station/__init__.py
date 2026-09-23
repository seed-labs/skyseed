"""ground station role contracts and implementations."""
from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import (
        CoverageFirstGroundStationAccessPlanningModel,
        GroundStationAccessPlanningModel,
        GroundStationCandidateSearch,
        GroundStationCandidateSearchModel,
    )

_EXPORTS = {
    "CoverageFirstGroundStationAccessPlanningModel": ".model",
    "GroundStationAccessPlanningModel": ".model",
    "GroundStationCandidateSearch": ".model",
    "GroundStationCandidateSearchModel": ".model",
}

__all__ = [
    'CoverageFirstGroundStationAccessPlanningModel',
    'GroundStationAccessPlanningModel',
    'GroundStationCandidateSearch',
    'GroundStationCandidateSearchModel',
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
