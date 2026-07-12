#!/usr/bin/env python3
# encoding: utf-8

import json
import tempfile
import unittest
from pathlib import Path

from apps.audit_runtime_ping_observer import audit_runtime_ping_observer


class RuntimePingObserverAuditTests(unittest.TestCase):
    def test_superseded_rows_do_not_pollute_valid_path_counts(self) -> None:
        rows = [
            {
                "observation_status": "valid",
                "superseded": False,
                "success": True,
                "returncode": 0,
                "ut_id": "ut-a",
                "selected_gateway_id": "gw201",
                "target_role": "gateway_service_loopback",
                "traffic_scope": "default",
            },
            {
                "observation_status": "valid",
                "superseded": False,
                "success": False,
                "returncode": 1,
                "stdout": "100% packet loss",
                "ut_id": "ut-a",
                "selected_gateway_id": "gw202",
                "target_role": "gateway_service_loopback",
                "traffic_scope": "special",
            },
            {
                "observation_status": "superseded",
                "superseded": True,
                "success": False,
                "returncode": 0,
                "ut_id": "ut-a",
                "selected_gateway_id": "gw201",
                "target_role": "gateway_service_loopback",
                "traffic_scope": "default",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime_ping_observation.jsonl"
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            report = audit_runtime_ping_observer(path)

        self.assertEqual(report["observation_count"], 3)
        self.assertEqual(report["valid_observation_count"], 2)
        self.assertEqual(report["superseded_observation_count"], 1)
        self.assertEqual(report["path_success_count"], 1)
        self.assertEqual(report["path_failure_count"], 1)
        self.assertEqual(report["failure_count"], 1)
        self.assertEqual(report["bucket_counts"]["superseded"], 1)
        self.assertEqual(
            report["policy_scope_counts"],
            {"default": 1, "special": 1},
        )


if __name__ == "__main__":
    unittest.main()
