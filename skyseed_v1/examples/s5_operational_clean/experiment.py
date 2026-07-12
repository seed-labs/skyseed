"""Human-editable composition for the canonical full-S5 experiment."""

from pathlib import Path

from skyseed_v1.configuration import (
    ExperimentConfig,
    RuntimeConfig,
    planned_shell,
    s5_operational_ground_network,
)

EXAMPLE_ROOT = Path(__file__).resolve().parent

EXPERIMENT = ExperimentConfig(
    profile_name="rich_s5_operational_clean_bpf",
    example_name="s5_operational_clean",
    description="Full S5 operational_clean strict bpf_owned validation experiment.",
    constellation=planned_shell("S5"),
    ground=s5_operational_ground_network(),
    runtime=RuntimeConfig(
        horizon_seconds=240,
        horizon_count=4,
        sample_interval_seconds=10,
    ),
    start_time_utc="2026-07-09T00:00:00Z",
    run_root=EXAMPLE_ROOT / "output",
)
