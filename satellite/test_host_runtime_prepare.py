#!/usr/bin/env python3
# encoding: utf-8

import unittest
from unittest.mock import patch

from apps.run_starlink_shell_experiment import (
    _frozen_container_pid_cache_for_maintenance_worker,
    _prepare_real_runtime_host_environment,
)
from mpls_host import (
    BRIDGE_NETFILTER_PASSTHROUGH_SYSCTLS,
    prepare_bridge_netfilter_passthrough,
)


class HostRuntimePrepareTests(unittest.TestCase):
    def test_maintenance_worker_pid_cache_is_frozen_and_required(self) -> None:
        class Executor:
            def __init__(self, snapshot):
                self.snapshot = snapshot

            def frozen_container_pid_cache_snapshot(self):
                return self.snapshot

        self.assertEqual(
            _frozen_container_pid_cache_for_maintenance_worker(
                Executor({"stl-1": 101, "gw-1": 202})
            ),
            {"stl-1": 101, "gw-1": 202},
        )
        with self.assertRaisesRegex(RuntimeError, "frozen_container_pid_cache_invalid"):
            _frozen_container_pid_cache_for_maintenance_worker(Executor({}))

    def test_bridge_passthrough_writes_all_required_sysctls(self) -> None:
        with patch(
            "mpls_host._run_jsonable",
            side_effect=lambda command: {
                "command": list(command),
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "ok": True,
            },
        ) as run:
            report = prepare_bridge_netfilter_passthrough()

        self.assertTrue(report["prepared"])
        self.assertEqual(run.call_count, len(BRIDGE_NETFILTER_PASSTHROUGH_SYSCTLS))
        self.assertEqual(
            [call.args[0][-1] for call in run.call_args_list],
            [f"{key}=0" for key in BRIDGE_NETFILTER_PASSTHROUGH_SYSCTLS],
        )

    def test_operational_clean_prepare_skips_mpls_but_prepares_bridge(self) -> None:
        with patch(
            "apps.run_starlink_shell_experiment.ensure_host_mpls_ready"
        ) as ensure_mpls, patch(
            "apps.run_starlink_shell_experiment.prepare_bridge_netfilter_passthrough",
            return_value={"prepared": True},
        ) as prepare_bridge:
            report = _prepare_real_runtime_host_environment(require_mpls=False)

        ensure_mpls.assert_not_called()
        prepare_bridge.assert_called_once_with()
        self.assertTrue(report["prepared"])
        self.assertTrue(report["host_mpls"]["skipped"])
        self.assertTrue(report["bridge_netfilter"]["prepared"])


if __name__ == "__main__":
    unittest.main()
