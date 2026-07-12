"""Human-editable composition for the canonical full-S4 experiment."""

from pathlib import Path

from skyseed_v1.configuration import (
    ExperimentConfig,
    RuntimeConfig,
    planned_shell,
    s4_rich_ground_network,
)

EXAMPLE_ROOT = Path(__file__).resolve().parent

EXPERIMENT = ExperimentConfig(
    profile_name="rich_s4_operational_clean_bpf",
    example_name="s4_operational_clean",
    description="Full rich S4 operational_clean strict bpf_owned validation experiment.",
    constellation=planned_shell("S4"),
    ground=s4_rich_ground_network(),
    runtime=RuntimeConfig(
        horizon_seconds=240,
        horizon_count=4,
        sample_interval_seconds=10,
    ),
    start_time_utc="2026-07-09T00:00:00Z",
    run_root=EXAMPLE_ROOT / "output",
)
