"""Compose the BPF Compiler Tool's read-only inspection stages."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterable, Mapping
from typing import Any

from skyseed_v2.backend.bpf import BpfCompiledRealization
from skyseed_v2.simulator import Plan

from .artifact_writer import write_bpf_compiler_artifacts
from .projection import project_bpf_compiler_data
from .reader import read_bpf_compiler_inspection
from .renderer import render_bpf_compiler_page


def _compiler_observation_timing(path: Path) -> dict[str, object]:
    from tools.observation import (
        read_execution_observations,
    )

    from tools.observation.projection import project_observations
    read = read_execution_observations(Path(path))
    observations = project_observations(read)
    component = next(
        (
            value
            for value in observations["components"]
            if value["component"] == "compiler"
        ),
        None,
    )
    records = read.records
    samples = tuple(
        {
            "compile_index": value["scope"].get("compile_index"),
            "plan_id": value["scope"].get("plan_id"),
            "start_seconds": value.get("run_relative_start_seconds"),
            "end_seconds": value.get("run_relative_end_seconds"),
            "duration_seconds": value["duration_seconds"],
        }
        for value in records
        if value["component"] == "compiler"
        and value["stage"] == "compile-total"
        and value["scope"].get("compile_index") is not None
    )
    return {
        "source": "System Observation detached execution records",
        "stages": () if component is None else component["stages"],
        "compile_samples": samples,
        "note": (
            "rollups overlap child stages and are never summed with them"
        ),
    }


def collect_bpf_compiler_data(
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompiledRealization, ...],
    *,
    compile_evidence: Iterable[Mapping[str, object]] = (),
    observation_path: Path | None = None,
) -> dict[str, Any]:
    """Detach and project already-produced Compiler results for inspection."""

    return project_bpf_compiler_data(
        read_bpf_compiler_inspection(plans, realizations),
        compile_evidence=compile_evidence,
        observation_timing=(
            None
            if observation_path is None
            else _compiler_observation_timing(Path(observation_path))
        ),
    )


def write_bpf_compiler_tool(
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompiledRealization, ...],
    directory: Path,
    *,
    compile_evidence: Iterable[Mapping[str, object]] = (),
    observation_path: Path | None = None,
) -> dict[str, object]:
    """Write one offline view without invoking the BPF Compiler."""

    data = collect_bpf_compiler_data(
        plans,
        realizations,
        compile_evidence=compile_evidence,
        observation_path=observation_path,
    )
    return write_bpf_compiler_artifacts(
        data,
        render_bpf_compiler_page(data),
        directory,
    )


__all__ = ["collect_bpf_compiler_data", "write_bpf_compiler_tool"]
