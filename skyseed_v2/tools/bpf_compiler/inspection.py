"""Compose the BPF Compiler Tool's read-only inspection stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skyseed_v2.backend.bpf import BpfCompiledRealization
from skyseed_v2.simulator import Plan

from .artifact_writer import write_bpf_compiler_artifacts
from .projection import project_bpf_compiler_data
from .reader import read_bpf_compiler_inspection
from .renderer import render_bpf_compiler_page


def collect_bpf_compiler_data(
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompiledRealization, ...],
) -> dict[str, Any]:
    """Detach and project already-produced Compiler results for inspection."""

    return project_bpf_compiler_data(
        read_bpf_compiler_inspection(plans, realizations)
    )


def write_bpf_compiler_tool(
    plans: tuple[Plan, ...],
    realizations: tuple[BpfCompiledRealization, ...],
    directory: Path,
) -> dict[str, object]:
    """Write one offline view without invoking the BPF Compiler."""

    data = collect_bpf_compiler_data(plans, realizations)
    return write_bpf_compiler_artifacts(
        data,
        render_bpf_compiler_page(data),
        directory,
    )


__all__ = ["collect_bpf_compiler_data", "write_bpf_compiler_tool"]
