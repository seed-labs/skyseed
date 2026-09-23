"""Compose the Planner Tool's detached inspection stages."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from skyseed.simulator import PublishedPlanningEvidence
from tools.observation._history import read_sealed_physical_frames
from tools.observation._presentation.html import write_text

from .artifact_writer import write_planner_artifacts
from .projection import project_planner_data
from .reader import (
    PlannerInspectionRead,
    iter_planner_history,
    _iter_projection_planner_history,
    read_planner_inspection,
)
from .renderer import render_planner_page


def collect_planner_data(
    evidence: tuple[PublishedPlanningEvidence, ...],
    *,
    physical_frames_path: Path | None = None,
    physical_link_inspection_path: Path | None = None,
    model_descriptors: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Project already-published immutable Planner evidence."""

    physical_frames = (
        ()
        if physical_frames_path is None
        else read_sealed_physical_frames(Path(physical_frames_path))
    )
    return project_planner_data(
        read_planner_inspection(evidence),
        physical_frames=physical_frames,
        physical_link_inspection_path=physical_link_inspection_path,
        model_descriptors=model_descriptors,
    )


def write_planner_tool(
    evidence: tuple[PublishedPlanningEvidence, ...],
    directory: Path,
    *,
    physical_frames_path: Path | None = None,
    physical_link_inspection_path: Path | None = None,
    model_descriptors: dict[str, object] | None = None,
) -> dict[str, object]:
    """Write one detached Planner semantic inspection bundle."""

    data = collect_planner_data(
        evidence,
        physical_frames_path=physical_frames_path,
        physical_link_inspection_path=physical_link_inspection_path,
        model_descriptors=model_descriptors,
    )
    return write_planner_artifacts(data, render_planner_page(data), directory)


def write_planner_history_tool(
    planner_history_path: Path,
    directory: Path,
    *,
    physical_frames_path: Path | None = None,
    physical_link_inspection_path: Path | None = None,
    model_descriptors: dict[str, object] | None = None,
) -> dict[str, object]:
    """Stream sealed history into chunks without retaining publication batches."""

    output = Path(directory)
    chunk_directory = output / "data"
    chunk_directory.mkdir(parents=True, exist_ok=True)

    def write_chunk(value: dict[str, Any]) -> Path:
        path = chunk_directory / f"frame-{value['frame_index']:03d}.js"
        write_text(
            path,
            "window.SKYSEED_PLANNER_FRAME_CHUNK="
            + json.dumps(value, sort_keys=True, separators=(",", ":"))
            + ";\n",
        )
        return path

    observation = PlannerInspectionRead(
        evidence=_iter_projection_planner_history(Path(planner_history_path)),
        snapshot={
            "format_version": 2,
            "tool": "planner-inspection",
            "authority": {
                "status": "non-authoritative-view",
                "source": "streamed sealed PublishedPlanningEvidence",
                "planner_models": "not invoked by this Tool",
                "operational_state": "canonical transition replay",
                "physical_candidates": "feasible facts, never operational",
                "compiled": "not inferred from Planner evidence",
                "realized": "not inferred from Planner evidence",
            },
        },
    )
    physical_frames = (
        ()
        if physical_frames_path is None
        else read_sealed_physical_frames(Path(physical_frames_path))
    )
    data = project_planner_data(
        observation,
        physical_frames=physical_frames,
        physical_link_inspection_path=physical_link_inspection_path,
        model_descriptors=model_descriptors,
        frame_chunk_sink=write_chunk,
        retain_internal_detail=False,
    )
    provenance = Path(planner_history_path).parents[2] / 'inputs/provenance.json'
    if provenance.is_file():
        labels = json.loads(provenance.read_text()).get('scenario_parameters', {}).get('node_labels', {})
        for node in data['spatial_index']['nodes']:
            label = labels.get(node['node_id'])
            if isinstance(label, str) and label:
                node['display_name'] = label
    return write_planner_artifacts(data, render_planner_page(data), output)


__all__ = [
    "collect_planner_data",
    "write_planner_history_tool",
    "write_planner_tool",
]
