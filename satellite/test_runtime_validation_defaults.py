#!/usr/bin/env python3
# encoding: utf-8

import inspect
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from linux_mpls_runtime import (
    DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
    MinimalRealLinuxMplsInstaller,
    _select_runtime_ping_validation_contexts,
)
from run_starlink_shell_experiment import parse_args


class RuntimeValidationDefaultTests(unittest.TestCase):
    def test_cli_and_installer_share_runtime_validation_scope_default(self) -> None:
        with patch.object(sys, "argv", ["run_starlink_shell_experiment.py"]):
            args = parse_args()

        signature = inspect.signature(MinimalRealLinuxMplsInstaller.apply_and_validate)

        self.assertEqual(
            args.runtime_epoch_validation_scope,
            DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        )
        self.assertEqual(
            signature.parameters["ping_validation_scope"].default,
            DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        )

    def test_empty_validation_scope_falls_back_to_gateway_class_sample(self) -> None:
        selection = _select_runtime_ping_validation_contexts(
            all_contexts=(),
            reapplied_contexts=(),
            validation_scope="",
        )

        self.assertEqual(
            selection["validation_scope"],
            DEFAULT_RUNTIME_EPOCH_VALIDATION_SCOPE,
        )
        self.assertEqual(selection["active_flow_count"], 0)
        self.assertEqual(selection["reapplied_flow_count"], 0)
        self.assertEqual(selection["selected_flow_count"], 0)
        self.assertEqual(selection["selected_to_sample_source_flow_ratio"], 0.0)

    def test_connectivity_probe_warmup_is_opt_in(self) -> None:
        context = SimpleNamespace(
            flow_id="flow-a",
            serving_satellite_id="stl-950001",
            ut_access_ip="10.10.0.101",
            ut_access_peer_ip="10.10.0.1",
            ut_id="ut101",
        )
        session = SimpleNamespace(contexts=(context,), target_slice={"slice_id": "slice-1"})

        with (
            patch("linux_mpls_runtime._warm_direct_access_link") as warm,
            patch(
                "linux_mpls_runtime._ping_validation_targets",
                return_value=(
                    [
                        {
                            "flow_id": "flow-a",
                            "success": True,
                        }
                    ],
                    0.0,
                ),
            ),
        ):
            result = MinimalRealLinuxMplsInstaller().probe_connectivity(session)

        self.assertTrue(result["ping_success"])
        warm.assert_not_called()

    def test_connectivity_probe_can_warm_active_access_links(self) -> None:
        context = SimpleNamespace(
            flow_id="flow-a",
            serving_satellite_id="stl-950001",
            ut_access_ip="10.10.0.101",
            ut_access_peer_ip="10.10.0.1",
            ut_id="ut101",
        )
        session = SimpleNamespace(contexts=(context,), target_slice={"slice_id": "slice-1"})

        with (
            patch(
                "linux_mpls_runtime._warm_direct_access_link",
                return_value={"wall_seconds": 0.25},
            ) as warm,
            patch(
                "linux_mpls_runtime._ping_validation_targets",
                return_value=(
                    [
                        {
                            "flow_id": "flow-a",
                            "success": True,
                        }
                    ],
                    0.0,
                ),
            ),
        ):
            result = MinimalRealLinuxMplsInstaller().probe_connectivity(
                session,
                warm_access_links=True,
            )

        self.assertTrue(result["ping_success"])
        self.assertEqual(result["access_link_warmup_wall_seconds"], 0.25)
        warm.assert_called_once_with(
            node_id="ut101",
            target_ip="10.10.0.1",
            phase="observe",
            description="warm direct UT access link before connectivity probe",
        )


if __name__ == "__main__":
    unittest.main()
