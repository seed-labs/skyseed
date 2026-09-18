"""Write PhysicalWorld projection, summary, and rendered UI artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools._inspection_evidence import iter_physical_link_inspection

from .._presentation.html import write_json, write_text
def write_physical_world_artifacts(
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _write_frame_chunks(data, directory)
    public = {
        key: value for key, value in data.items() if not key.startswith("_")
    }
    write_json(directory / "physical-world.json", public)
    write_text(directory / "index.html", page)
    counts = data["counts"]
    consistency = data["consistency"]
    assert isinstance(counts, dict)
    assert isinstance(consistency, dict)
    write_text(
        directory / "summary.txt",
        "\n".join(
            (
                f"status={data['status']}",
                "physical_world_created=true",
                f"zone_layout_configured={str(data['zone_layout_configured']).lower()}",
                *(f"{key}={value}" for key, value in counts.items()),
                f"consistency_passed={consistency['passed']}",
                f"consistency_failed={consistency['failed']}",
                f"formal_frames={data['temporal_inventory']['formal_frames']}",
                "warning=候选关系仅表示物理可行输入，不表示已选择或已运行。",
                "",
            )
        ),
    )
    return {
        "status": data["status"],
        "message": (
            f"{counts['autonomous_systems']} AS / "
            f"{counts['orbits']} Orbit / {counts['satellites']} Satellite"
        ),
        "counts": counts,
    }


def _write_frame_chunks(data: dict[str, Any], directory: Path) -> None:
    frames = tuple(data.get("_physical_frames", ()))
    candidate_path = data.get("_candidate_evidence_path")
    fallback = data.get("_candidate_overlay")
    node_ids = tuple(data["spatial_index"]["node_ids"])
    node_index = {value: index for index, value in enumerate(node_ids)}
    candidates: dict[str, dict[str, Any]] = {}
    if candidate_path is not None:
        records = iter_physical_link_inspection(Path(str(candidate_path)))
        next(records)
        candidates = {value["timestamp"]: value for value in records}
    elif isinstance(fallback, dict):
        candidates[str(fallback["timestamp"])] = _fallback_candidates(
            fallback, node_index, tuple(data["spatial_index"]["zones"])
        )
    chunk_directory = directory / "data"
    chunk_directory.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(frames):
        value = {
            "frame_index": index,
            "timestamp": frame["timestamp"],
            "positions": [
                [
                    node_index[item["node_id"]],
                    item["ecef_m"]["x"],
                    item["ecef_m"]["y"],
                    item["ecef_m"]["z"],
                ]
                for item in frame["positions"]
            ],
            "candidates": candidates.get(
                frame["timestamp"],
                {
                    "inter_satellite": [],
                    "ground_station": [],
                    "zone_service": [],
                    "work_counts": None,
                },
            ),
        }
        write_text(
            chunk_directory / f"frame-{index:03d}.js",
            "window.SKYSEED_PHYSICAL_FRAME_CHUNK="
            + __import__("json").dumps(
                value, sort_keys=True, separators=(",", ":")
            )
            + ";\n",
        )


def _fallback_candidates(
    value: dict[str, Any], node_index: dict[str, int], zones: tuple[str, ...]
) -> dict[str, Any]:
    zone_index = {zone: index for index, zone in enumerate(zones)}
    return {
        "inter_satellite": [
            [
                node_index[item["endpoint_a"]],
                node_index[item["endpoint_b"]],
                item["distance_m"],
                item["propagation_delay_us"],
                None if item["relation"] is None else int(item["relation"] == "same-plane"),
                item["cyclic_plane_separation"],
            ]
            for item in value["inter_satellite_candidates"]
        ],
        "ground_station": [
            [
                node_index[item["ground_station_id"]],
                node_index[item["satellite_id"]],
                item["distance_m"],
                item["elevation_deg"],
                item["propagation_delay_us"],
            ]
            for item in value["ground_station_access_candidates"]
        ],
        "zone_service": [
            [
                zone_index[item["zone_id"]],
                node_index[item["satellite_id"]],
                item["distance_m"],
                item["elevation_deg"],
                item["propagation_delay_us"],
            ]
            for item in value["zone_service_candidates"]
        ],
        "work_counts": value["work_counts"],
    }


__all__ = ["write_physical_world_artifacts"]
