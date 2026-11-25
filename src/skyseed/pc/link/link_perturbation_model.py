"""Execution-branch transformation; domain interference algorithms are external."""
from typing import TYPE_CHECKING, Protocol
from .link_plan import LinkPlan

if TYPE_CHECKING:
    from .link_layer import LinkLayer


class LinkPerturbationModel(Protocol):
    layer: "LinkLayer"  # Bound by the layer after construction.

    def perturb(self, plan: LinkPlan) -> LinkPlan: ...
