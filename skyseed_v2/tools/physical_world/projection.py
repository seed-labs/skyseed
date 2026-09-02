"""Project a detached PhysicalWorld read snapshot into final Tool view data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .display_analysis import add_physical_world_display_analysis
from .reader import PhysicalWorldRead
from tools._inspection_evidence import PhysicalLinkInspectionIndex


def project_physical_world_data(
    observation: PhysicalWorldRead,
    *,
    candidate_index: PhysicalLinkInspectionIndex | None = None,
) -> dict[str, Any]:
    """Copy Tool-owned read values and add non-authoritative display data."""

    if not isinstance(observation, PhysicalWorldRead):
        raise TypeError("observation must be a PhysicalWorldRead")
    data = deepcopy(observation.snapshot)
    add_physical_world_display_analysis(data)
    frames = data.pop("physical_frames")
    candidate_overlay = data.pop("candidate_overlay")
    timestamps = tuple(value["timestamp"] for value in frames)
    if candidate_index is not None and candidate_index.timestamps != timestamps:
        raise ValueError(
            "physical-link inspection and PhysicalFrame timestamps differ"
        )
    if candidate_index is None:
        node_ids = tuple(
            value["node_id"]
            for value in (() if not frames else frames[0]["positions"])
        )
        nodes = tuple(
            {"node_id": value, "kind": next(
                item["kind"] for item in frames[0]["positions"]
                if item["node_id"] == value
            )}
            for value in node_ids
        ) if frames else ()
    else:
        nodes = candidate_index.nodes
        node_ids = tuple(str(value["node_id"]) for value in nodes)
    data["spatial_index"] = {
        "nodes": nodes,
        "node_ids": node_ids,
        "zones": tuple(value["zone_id"] for value in data["zones"]),
    }
    data["frame_index"] = tuple(
        {
            "frame_index": index,
            "timestamp": value["timestamp"],
            "chunk": f"data/frame-{index:03d}.js",
        }
        for index, value in enumerate(frames)
    )
    data["temporal_inventory"] = {
        "formal_frames": len(frames),
        "first_timestamp": None if not frames else frames[0]["timestamp"],
        "last_timestamp": None if not frames else frames[-1]["timestamp"],
        "candidate_overlay_timestamp": (
            None
            if candidate_index is not None
            else None if candidate_overlay is None else candidate_overlay["timestamp"]
        ),
        "exact_candidate_frames": (
            len(candidate_index.timestamps)
            if candidate_index is not None
            else int(candidate_overlay is not None)
        ),
    }
    data["candidate_evidence"] = (
        {
            "status": "sealed-exact-formal-observations",
            "manifest": candidate_index.manifest,
            "browser_loading": "one timestamp chunk loaded on demand",
        }
        if candidate_index is not None
        else {
            "status": "single-caller-supplied-overlay"
            if candidate_overlay is not None
            else "not supplied",
            "browser_loading": "only the retained matching frame has candidates",
        }
    )
    data["_candidate_overlay"] = candidate_overlay
    data["_physical_frames"] = frames
    data["display_notes"] = {
        "positions": (
            "Timestamped points are sealed formal ECEF positions. The globe "
            "normalizes ECEF directly; the linked map uses a Tool-private "
            "spherical subpoint conversion for display only."
        ),
        "candidate_overlay": (
            "Dashed candidate relationships are physically feasible input "
            "facts from the retained frame only; they are not selected, "
            "operational, routed, compiled, or realized."
        ),
        "history_limit": (
            "Only formal PhysicalFrames sealed in the supplied history are "
            "shown. Exact candidate identities live in per-frame Tool chunks; "
            "the browser loads only the selected timestamp and draws only the "
            "selected entity's adjacency."
        ),
    }
    return data


__all__ = ["project_physical_world_data"]
