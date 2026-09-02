"""Compose the Physical World Tool's explicit read-only stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyseed_v2.simulator import PhysicalFrame, PhysicalWorld
from skyseed_v2.simulator.physical.physical_link import PhysicalLinkObservation

from tools._history import read_sealed_physical_frames
from tools._inspection_evidence import read_physical_link_inspection_index

from .artifact_writer import write_physical_world_artifacts
from .projection import project_physical_world_data
from .reader import read_physical_world
from .renderer import render_physical_world_page


def collect_physical_world_data(
    physical_world: PhysicalWorld,
    *,
    physical_frames_path: Path | None = None,
    physical_frame: PhysicalFrame | None = None,
    physical_link_observation: PhysicalLinkObservation | None = None,
    physical_link_model: object | None = None,
    physical_link_inspection_path: Path | None = None,
    model_descriptors: dict[str, object] | None = None,
) -> dict[str, Any]:
    frames = (
        ()
        if physical_frames_path is None
        else read_sealed_physical_frames(Path(physical_frames_path))
    )
    candidate_index = (
        None
        if physical_link_inspection_path is None
        else read_physical_link_inspection_index(
            Path(physical_link_inspection_path)
        )
    )
    data = project_physical_world_data(
        read_physical_world(
            physical_world,
            physical_frames=frames,
            physical_frame=physical_frame,
            physical_link_observation=physical_link_observation,
            physical_link_model=physical_link_model,
            model_descriptors=model_descriptors,
        ),
        candidate_index=candidate_index,
    )
    if frames:
        data["_physical_frames"] = frames
    data["_candidate_evidence_path"] = (
        None
        if physical_link_inspection_path is None
        else str(Path(physical_link_inspection_path).resolve())
    )
    return data


def write_physical_world_tool(
    physical_world: PhysicalWorld,
    directory: Path,
    *,
    physical_frames_path: Path | None = None,
    physical_frame: PhysicalFrame | None = None,
    physical_link_observation: PhysicalLinkObservation | None = None,
    physical_link_model: object | None = None,
    physical_link_inspection_path: Path | None = None,
    model_descriptors: dict[str, object] | None = None,
) -> dict[str, object]:
    data = collect_physical_world_data(
        physical_world,
        physical_frames_path=physical_frames_path,
        physical_frame=physical_frame,
        physical_link_observation=physical_link_observation,
        physical_link_model=physical_link_model,
        physical_link_inspection_path=physical_link_inspection_path,
        model_descriptors=model_descriptors,
    )
    return write_physical_world_artifacts(
        data,
        render_physical_world_page(data),
        directory,
    )


__all__ = ["collect_physical_world_data", "write_physical_world_tool"]
