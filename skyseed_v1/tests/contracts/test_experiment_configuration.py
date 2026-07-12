from __future__ import annotations

import unittest
from pathlib import Path

from skyseed_v1.configuration import (
    ExperimentConfig,
    RuntimeConfig,
    planned_shell,
    s4_rich_ground_network,
    s5_operational_ground_network,
)


def experiment(
    shell: str,
    *,
    sample_interval: int = 10,
    horizon_count: int = 4,
) -> ExperimentConfig:
    return ExperimentConfig(
        profile_name=f"rich_{shell.lower()}_operational_clean_bpf",
        example_name=f"{shell.lower()}_operational_clean",
        description="contract fixture",
        constellation=planned_shell(shell),
        ground=(
            s5_operational_ground_network()
            if shell == "S5"
            else s4_rich_ground_network()
        ),
        runtime=RuntimeConfig(240, horizon_count, sample_interval),
        run_root=Path("/tmp/not-executed"),
    )


class ExperimentConfigurationTests(unittest.TestCase):
    def test_s4_and_s5_derive_validated_capacity(self) -> None:
        s4 = experiment("S4").resolve_profile()
        s5 = experiment("S5").resolve_profile()
        self.assertEqual(s4["runner_args"]["max_satellites"], 348)
        self.assertEqual(s4["docker_dummy_network_capacity"]["required_dummy_networks"], 631)
        self.assertEqual(s4["startup_materialization"]["expected_containers"], 416)
        self.assertEqual(s5["runner_args"]["max_satellites"], 172)
        self.assertEqual(s5["docker_dummy_network_capacity"]["required_dummy_networks"], 605)
        self.assertEqual(s5["startup_materialization"]["expected_containers"], 195)

    def test_one_sample_change_updates_every_derived_consumer(self) -> None:
        profile = experiment("S5", sample_interval=20).resolve_profile()
        args = profile["runner_args"]
        visualization = profile["visualization_physical_links"]
        self.assertEqual(args["sample_interval"], 20)
        self.assertEqual(args["event_evaluation_interval"], 20)
        self.assertEqual(args["visualization_link_interval"], "20s")
        self.assertEqual(visualization["interval"], "20s")
        self.assertEqual(visualization["duration_seconds"], 960)

    def test_invalid_interval_fails_before_profile_or_network_work(self) -> None:
        config = experiment("S5", sample_interval=17)
        self.assertIn("sample_interval_must_divide_horizon", config.validate())
        with self.assertRaises(ValueError):
            config.resolve_profile()

    def test_s5_two_hour_ground_qualification_bounds_horizon_count(self) -> None:
        self.assertEqual(experiment("S5", horizon_count=30).validate(), ())
        config = experiment("S5", horizon_count=31)
        self.assertIn(
            "runtime_duration_exceeds_ground_qualification_window",
            config.validate(),
        )
        with self.assertRaises(ValueError):
            config.resolve_profile()


if __name__ == "__main__":
    unittest.main()
