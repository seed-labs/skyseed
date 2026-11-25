"""Optional joint property adjustment; the layer prepares the base values."""
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, Sequence
from skyseed.scene import Scene
from .link_values import LinkConfiguration

if TYPE_CHECKING:
    from .link_layer import LinkLayer


class LinkPropertyModel(Protocol):
    layer: "LinkLayer"  # Bound by the layer after construction.

    def adjust_properties(
        self, scene: Scene, timestamp: datetime, links: Sequence[LinkConfiguration]
    ) -> Sequence[LinkConfiguration]: ...
