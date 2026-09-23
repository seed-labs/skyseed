"""Generic GS endpoint/transit rules, independent of routing algorithms."""
from skyseed.shared import NodeId

def _route_step_allows_transit(
    *,
    source: NodeId,
    current: NodeId,
    next_hop: NodeId,
    terminal: NodeId,
    ground_station_target: bool,
) -> bool:
    if ground_station_target:
        return (
            source.entity_kind == "satellite"
            and current.entity_kind == "satellite"
            and (
                next_hop.entity_kind == "satellite" or next_hop == terminal
            )
        )
    if current.entity_kind == "ground-station":
        return current == source and next_hop.entity_kind == "satellite"
    return (
        current.entity_kind == "satellite"
        and next_hop.entity_kind == "satellite"
    )
