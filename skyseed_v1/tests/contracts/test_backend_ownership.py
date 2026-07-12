from __future__ import annotations

import unittest

from skyseed_v1.compilers.backend_ownership import (
    normalize_forwarding_backend_config,
    plan_backend_apply_contract,
)
from skyseed_v1.runtime.execution import RuntimeCommandSpec


class BackendOwnershipCompilerTests(unittest.TestCase):
    def test_bpf_owned_missing_structured_proof_fails_closed(self) -> None:
        specs = [
            RuntimeCommandSpec(
                node_id="sat-a",
                phase="apply",
                description="install forward LFIB on sat-a for label 16001",
                command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
            )
        ]

        kept, contract = plan_backend_apply_contract(
            specs,
            backend_config=normalize_forwarding_backend_config(
                forwarding_backend="bpf_owned",
                bpf_owned_readiness={"ok": True},
            ),
            contract_phase="apply",
        )

        self.assertEqual(kept, [])
        self.assertEqual(contract["bpf_owned_planner_error_count"], 1)
        self.assertEqual(contract["linux_gz_fallback_command_count"], 0)
        self.assertFalse(contract["bpf_owned_strict_no_linux_gz_fallback_guard"])

    def test_linux_backend_keeps_linux_mpls_command(self) -> None:
        spec = RuntimeCommandSpec(
            node_id="sat-a",
            phase="apply",
            description="install forward LFIB on sat-a for label 16001",
            command=("ip", "-f", "mpls", "route", "replace", "16001", "dev", "lo"),
        )

        kept, contract = plan_backend_apply_contract(
            [spec],
            backend_config=normalize_forwarding_backend_config(
                forwarding_backend="linux_mpls"
            ),
            contract_phase="apply",
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(contract["linux_owned_count"], 1)
        self.assertEqual(contract["fallback_to_linux_count"], 0)


if __name__ == "__main__":
    unittest.main()
