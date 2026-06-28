#!/usr/bin/env python3
# encoding: utf-8

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from satellite.bpf_dataplane_canary import ipv4_little_endian_u32
from satellite.bpf_mpls_live import (
    BPF_MAP_GENERATION_CAPACITY_BLOCKER,
    DEFAULT_MPLS_SOURCE,
    G_TO_Z,
    MPLS_KERNEL_MAP_NAMES,
    MPLS_MAP_SHAPES,
    MPLS_INTERFACE_ROLE_CLASSIFIER,
    MPLS_INTERFACE_ROLE_MPLS,
    Z_TO_G,
    attach_mpls,
    build_minimal_live_maps,
    compile_mpls_bpf,
    load_mpls_maps,
    mpls_map_state,
    pack_u32s,
    read_mpls_interface_roles,
    read_mpls_tc_filter_stats,
    verify_mpls_clsact,
)
from linux_mpls_runtime import (
    DirectGatewayExperimentContext,
    MplsPathSegment,
    _bpf_mpls_context_fec_ids,
    _bpf_owned_compile_mpls_maps_for_contexts,
    _bpf_owned_runtime_generation_id,
)
from contracts import RUNTIME_ACTION_MODEL_VERSION, RUNTIME_UPDATE_PLAN_SCHEMA_VERSION, RuntimeUpdatePlan


class BpfMplsLiveTests(unittest.TestCase):
    def _mock_bpf_maps(
        self,
        *,
        active_generation: int,
        entries_by_map: dict[str, list[dict[str, list[str]]]],
    ):
        map_ids = {
            "ingress_classifier_map": 101,
            "core_lfib_map": 102,
            "egress_handoff_map": 103,
            "active_generation": 104,
            "interface_role_map": 105,
        }
        id_to_map = {str(map_id): name for name, map_id in map_ids.items()}

        def prefixed(values: list[str]) -> list[str]:
            return [f"0x{part}" for part in values]

        def map_infos(_args: list[str]) -> list[dict[str, object]]:
            infos = []
            for map_name, map_id in map_ids.items():
                shape = dict(MPLS_MAP_SHAPES[map_name])
                infos.append(
                    {
                        "id": map_id,
                        "name": MPLS_KERNEL_MAP_NAMES.get(map_name, map_name[:15]),
                        **shape,
                        "pinned": f"/sys/fs/bpf/tc/globals/{map_name}",
                    }
                )
            return infos

        def bpftool(args: list[str], *, timeout: float) -> dict[str, object]:
            rendered = [str(part) for part in args]
            if rendered[:3] == ["-j", "map", "dump"]:
                map_name = id_to_map.get(rendered[-1], "")
                entries = list(entries_by_map.get(map_name, []))
                if map_name == "active_generation":
                    entries = [
                        {
                            "key": prefixed(pack_u32s(0)),
                            "value": prefixed(pack_u32s(active_generation)),
                        }
                    ]
                return {
                    "returncode": 0,
                    "stdout": json.dumps(entries),
                    "stderr": "",
                    "args": rendered,
                }
            return {"returncode": 0, "stdout": "{}", "stderr": "", "args": rendered}

        return patch.multiple(
            "satellite.bpf_mpls_live",
            bpftool_json=Mock(side_effect=map_infos),
            bpftool=Mock(side_effect=bpftool),
            privileged_path_exists=Mock(return_value=False),
        )

    def _core_entry(
        self,
        *,
        generation: int,
        ingress_ifindex: int = 21,
        label: int = 100,
        direction: int = 1,
        out_label: int = 101,
        out_ifindex: int = 22,
    ) -> dict[str, list[str]]:
        return {
            "key": [f"0x{part}" for part in pack_u32s(generation, ingress_ifindex, label, direction)],
            "value": [
                f"0x{part}"
                for part in (
                    pack_u32s(1, 1, out_label, out_ifindex)
                    + ["00"] * 12
                )
            ],
        }

    def _interface_role_entry(
        self,
        *,
        ifindex: int = 21,
        role: int = MPLS_INTERFACE_ROLE_MPLS,
    ) -> dict[str, list[str]]:
        return {
            "key": [f"0x{part}" for part in pack_u32s(ifindex)],
            "value": [f"0x{part}" for part in pack_u32s(role)],
        }

    def _compiled_core_generation(
        self,
        *,
        generation: int = 1,
        out_label: int = 101,
    ) -> dict[str, object]:
        return {
            "active_generation": {"value": generation},
            "bpf_runtime_update_phase": "steady_map_only",
            "ingress_classifier_map": [],
            "core_lfib_map": [
                {
                    "key": {
                        "generation": generation,
                        "ingress_ifindex": 21,
                        "label": 100,
                        "direction": G_TO_Z,
                    },
                    "value": {
                        "active": True,
                        "action": "swap",
                        "out_label": out_label,
                        "out_ifindex": 22,
                    },
                }
            ],
            "egress_handoff_map": [],
        }

    def test_delta_attach_new_interface_missing_clsact_is_idempotent(self) -> None:
        calls: list[list[str]] = []

        def fake_run(interface_ref, args, *, timeout):
            rendered = [str(part) for part in args]
            calls.append(rendered)
            if rendered[:4] == ["tc", "qdisc", "show", "dev"]:
                return {"returncode": 0, "stdout": "", "stderr": ""}
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with patch("satellite.bpf_mpls_live.run_mpls_interface_command", side_effect=fake_run):
            results = attach_mpls(
                "netns:stl-940023:ua-u2005s0",
                Path("/tmp/skyseed_tc_mpls_dataplane.o"),
                directions=["ingress"],
                clsact_setup_mode="ensure_new_delta",
            )

        self.assertTrue(all(int(result.get("returncode") or 0) == 0 for result in results))
        self.assertFalse(
            any(call[:4] == ["tc", "qdisc", "del", "dev"] for call in calls)
        )
        self.assertTrue(
            any(
                result.get("clsact_lifecycle_event")
                == "delete_skipped_missing_on_new_delta"
                for result in results
            )
        )
        self.assertTrue(
            any(call[:4] == ["tc", "qdisc", "add", "dev"] for call in calls)
        )
        self.assertTrue(
            any(call[:4] == ["tc", "filter", "add", "dev"] for call in calls)
        )

    def test_delta_attach_expected_reuse_missing_clsact_fails_closed(self) -> None:
        calls: list[list[str]] = []

        def fake_run(interface_ref, args, *, timeout):
            rendered = [str(part) for part in args]
            calls.append(rendered)
            if rendered[:4] == ["tc", "qdisc", "show", "dev"]:
                return {"returncode": 0, "stdout": "", "stderr": ""}
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with patch("satellite.bpf_mpls_live.run_mpls_interface_command", side_effect=fake_run):
            results = attach_mpls(
                "netns:stl-940023:fs0007",
                Path("/tmp/skyseed_tc_mpls_dataplane.o"),
                directions=["egress"],
                clsact_setup_mode="expect_existing",
            )

        self.assertTrue(
            any(
                result.get("clsact_lifecycle_event")
                == "missing_on_expected_reuse"
                and int(result.get("returncode") or 0) != 0
                for result in results
            )
        )
        self.assertFalse(
            any(call[:4] == ["tc", "filter", "add", "dev"] for call in calls)
        )

    def test_verify_mpls_clsact_reports_expected_reuse_loss(self) -> None:
        calls: list[list[str]] = []

        def fake_run(interface_ref, args, *, timeout):
            rendered = [str(part) for part in args]
            calls.append(rendered)
            return {"returncode": 0, "stdout": "qdisc tbf 1: root refcnt 2", "stderr": ""}

        with patch("satellite.bpf_mpls_live.run_mpls_interface_command", side_effect=fake_run):
            results = verify_mpls_clsact("netns:gw205:ix204")

        self.assertEqual(calls, [["tc", "qdisc", "show", "dev", "{iface}"]])
        self.assertTrue(
            any(
                result.get("clsact_lifecycle_event") == "missing_on_expected_reuse"
                and int(result.get("returncode") or 0) != 0
                for result in results
            )
        )

    def test_verify_mpls_clsact_accepts_existing_clsact(self) -> None:
        def fake_run(interface_ref, args, *, timeout):
            return {
                "returncode": 0,
                "stdout": "qdisc clsact ffff: parent ffff:fff1",
                "stderr": "",
            }

        with patch("satellite.bpf_mpls_live.run_mpls_interface_command", side_effect=fake_run):
            results = verify_mpls_clsact("netns:gw205:ix204")

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].get("clsact_lifecycle_event"),
            "show_before_expected_reuse",
        )

    def test_runtime_generation_derives_from_slice_id(self) -> None:
        plan = RuntimeUpdatePlan(
            schema_version=RUNTIME_UPDATE_PLAN_SCHEMA_VERSION,
            plan_id="rtplan-test",
            target_snapshot={
                "snapshot_id": "topsnap-test",
                "slice_id": "starlink-shell-slice-0002",
            },
            planner={"planner_name": "test"},
            apply_model={
                "state_model": "full_state_intent",
                "action_model": RUNTIME_ACTION_MODEL_VERSION,
            },
            node_subplans={},
            replacement_semantics={},
            validation_expectations={},
        )

        self.assertEqual(
            _bpf_owned_runtime_generation_id(plan),
            (1, "target_snapshot.slice_id"),
        )

    def test_core_lfib_c_struct_does_not_key_on_destination_ip(self) -> None:
        source = Path(DEFAULT_MPLS_SOURCE).read_text(encoding="utf-8")
        lfib_struct = source[
            source.index("struct lfib_key") : source.index("struct core_lfib_value")
        ]
        ingress_struct = source[
            source.index("struct ingress_classifier_key") : source.index(
                "struct ingress_classifier_value"
            )
        ]

        self.assertIn("dst_ipv4", ingress_struct)
        self.assertNotIn("dst_ipv4", lfib_struct)
        self.assertIn(".label = label", source)
        self.assertIn(".direction = direction", source)

    def test_c_program_passes_non_gz_ip_only_on_non_classifier_interfaces(self) -> None:
        source = Path(DEFAULT_MPLS_SOURCE).read_text(encoding="utf-8")

        self.assertIn("struct bpf_elf_map SEC(\"maps\") interface_role_map", source)
        self.assertIn("#define IFACE_ROLE_CLASSIFIER 1", source)
        self.assertIn("#define COUNTER_NON_GZ_IP_PASS 18", source)
        self.assertIn("COUNTER_UNKNOWN_ROLE", source)
        self.assertIn("COUNTER_ROLE_MAP_MISSING", source)
        self.assertIn("!(*role & IFACE_ROLE_CLASSIFIER)", source)
        self.assertIn("COUNTER_NON_GZ_IP_PASS", source)

    def test_load_mpls_maps_uses_label_lfib_keys_without_dst_ip(self) -> None:
        compiled = {
            "active_generation": {"value": 3},
            "ingress_classifier_map": [
                {
                    "key": {
                        "generation": 3,
                        "ingress_ifindex": 11,
                        "dst_ipv4": ipv4_little_endian_u32("10.0.0.9"),
                    },
                    "value": {
                        "active": True,
                        "fec_id": "fec-a",
                        "direction": G_TO_Z,
                        "initial_label": 100,
                        "out_ifindex": 12,
                        "dst_mac": "02:00:00:00:00:12",
                        "src_mac": "02:00:00:00:00:11",
                    },
                }
            ],
            "core_lfib_map": [
                {
                    "key": {
                        "generation": 3,
                        "ingress_ifindex": 21,
                        "label": 100,
                        "direction": G_TO_Z,
                    },
                    "value": {
                        "active": True,
                        "action": "swap",
                        "out_label": 101,
                        "out_ifindex": 22,
                    },
                }
            ],
            "egress_handoff_map": [
                {
                    "key": {
                        "generation": 3,
                        "ingress_ifindex": 31,
                        "label": 101,
                        "direction": G_TO_Z,
                    },
                    "value": {"active": True, "out_ifindex": 0},
                }
            ],
        }
        calls: list[tuple[str, list[str], list[str]]] = []

        def fake_update(
            map_name: str,
            key_bytes: list[str],
            value_bytes: list[str],
        ) -> dict[str, object]:
            calls.append((map_name, key_bytes, value_bytes))
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertFalse(result["intermediate_core_lookup_uses_dst_ip"])
        core_call = next(call for call in calls if call[0] == "core_lfib_map")
        role_calls = [call for call in calls if call[0] == "interface_role_map"]
        self.assertEqual(len(core_call[1]), 16)
        self.assertEqual(result["loaded_interface_role_count"], 3)
        self.assertEqual(len(role_calls), 3)
        self.assertEqual(
            sorted(result["loaded_interface_roles"], key=lambda item: item["ifindex"]),
            [
                {
                    "ifindex": 11,
                    "role": MPLS_INTERFACE_ROLE_CLASSIFIER,
                    "classifier": True,
                    "mpls": False,
                },
                {
                    "ifindex": 21,
                    "role": MPLS_INTERFACE_ROLE_MPLS,
                    "classifier": False,
                    "mpls": True,
                },
                {
                    "ifindex": 31,
                    "role": MPLS_INTERFACE_ROLE_MPLS,
                    "classifier": False,
                    "mpls": True,
                },
            ],
        )
        self.assertEqual(
            result["loaded_core_lfib_keys"],
            [
                {
                    "generation": 3,
                    "ingress_ifindex": 21,
                    "label": 100,
                    "direction": G_TO_Z,
                }
            ],
        )
        self.assertEqual(calls[-1][0], "active_generation")
        self.assertIn("ingress_classifier_map", [call[0] for call in calls[:-1]])
        self.assertIn("core_lfib_map", [call[0] for call in calls[:-1]])
        self.assertIn("egress_handoff_map", [call[0] for call in calls[:-1]])
        self.assertEqual(result["active_generation_switch_count"], 1)
        self.assertEqual(result["active_generation_update_count"], 1)
        self.assertEqual(result["map_write_update_count"], 6)
        self.assertEqual(result["total_update_count"], 7)
        self.assertGreaterEqual(result["active_generation_flip_wall_seconds"], 0.0)
        self.assertGreaterEqual(result["map_write_wall_seconds"], 0.0)
        attribution = result["runtime_reload_attribution"]
        self.assertEqual(
            attribution["reload_strategy"],
            "map_load",
        )
        self.assertTrue(attribution["diff_update_enabled"])
        self.assertNotIn("RUNTIME_FULL_GENERATION_RELOAD_BLOCKER", attribution["outcome_labels"])
        self.assertEqual(
            attribution["per_map_operation_summary"]["core_lfib_map"][
                "entries_inserted_or_updated"
            ],
            1,
        )
        self.assertEqual(
            attribution["per_map_operation_summary"]["active_generation"][
                "active_generation_flip_count"
            ],
            1,
        )
        self.assertEqual(
            attribution["per_map_operation_summary"]["ingress_classifier_map"][
                "noop_write_count"
            ],
            0,
        )

    def test_preloaded_inactive_generation_flips_without_reloading_entries(self) -> None:
        compiled = self._compiled_core_generation(generation=1)
        updates: list[str] = []

        def fake_update(map_name: str, key_bytes: list[str], value_bytes: list[str]) -> dict[str, object]:
            updates.append(map_name)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with self._mock_bpf_maps(
            active_generation=0,
            entries_by_map={
                "core_lfib_map": [self._core_entry(generation=1)],
                "interface_role_map": [self._interface_role_entry()],
            },
        ), patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.delete_mpls_map_key",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertEqual(updates, ["active_generation"])
        self.assertEqual(result["map_write_update_count"], 0)
        self.assertEqual(result["active_generation_update_count"], 1)
        labels = result["outcome_labels"]
        self.assertIn("RUNTIME_PRELOADED_GENERATION_USED", labels)
        self.assertIn("RUNTIME_ACTIVE_GENERATION_FLIP_ONLY", labels)
        self.assertIn("RUNTIME_NOOP_MAP_WRITES_SKIPPED", labels)
        proof = result["runtime_reload_attribution"][
            "inactive_preloaded_generation_visibility_proof"
        ]
        self.assertTrue(proof["target_entries_loaded_while_inactive"])

    def test_preactivation_preload_keeps_active_generation_unchanged(self) -> None:
        compiled = {
            **self._compiled_core_generation(generation=1),
            "bpf_runtime_update_phase": "preactivation_preload",
        }
        updates: list[str] = []

        def fake_update(map_name: str, key_bytes: list[str], value_bytes: list[str]) -> dict[str, object]:
            updates.append(map_name)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with self._mock_bpf_maps(
            active_generation=0,
            entries_by_map={"interface_role_map": [self._interface_role_entry()]},
        ), patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.delete_mpls_map_key",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertEqual(updates, ["core_lfib_map"])
        self.assertEqual(result["active_generation_update_count"], 0)
        self.assertTrue(result["active_generation_left_unchanged_by_loader"])
        proof = result["runtime_reload_attribution"][
            "inactive_preloaded_generation_visibility_proof"
        ]
        self.assertTrue(proof["active_generation_flip_deferred"])
        self.assertTrue(proof["ok"])

    def test_preactivation_preload_fails_closed_when_target_already_active(self) -> None:
        compiled = {
            **self._compiled_core_generation(generation=1),
            "bpf_runtime_update_phase": "preactivation_preload",
        }

        with self._mock_bpf_maps(
            active_generation=1,
            entries_by_map={},
        ), patch(
            "satellite.bpf_mpls_live.update_mpls_map",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ), patch(
            "satellite.bpf_mpls_live.delete_mpls_map_key",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ):
            result = load_mpls_maps(compiled)

        self.assertFalse(result["ok"])
        self.assertIn(
            "RUNTIME_PRELOAD_VISIBILITY_PROOF_FAILED",
            result["outcome_labels"],
        )
        self.assertTrue(result["active_generation_left_unchanged_by_loader"])

    def test_changed_entry_generates_only_delta_update(self) -> None:
        compiled = self._compiled_core_generation(generation=1, out_label=101)
        updates: list[str] = []

        def fake_update(map_name: str, key_bytes: list[str], value_bytes: list[str]) -> dict[str, object]:
            updates.append(map_name)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with self._mock_bpf_maps(
            active_generation=0,
            entries_by_map={
                "core_lfib_map": [self._core_entry(generation=1, out_label=999)],
                "interface_role_map": [self._interface_role_entry()],
            },
        ), patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.delete_mpls_map_key",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertEqual(updates, ["core_lfib_map", "active_generation"])
        diff = result["map_diff_trace"]["core_lfib_map"]
        self.assertEqual(diff["update_count"], 1)
        self.assertEqual(diff["insert_count"], 0)
        self.assertIn("RUNTIME_MAP_DIFF_UPDATE_USED", result["outcome_labels"])

    def test_stale_target_entry_generates_delete_only_for_stale_key(self) -> None:
        compiled = self._compiled_core_generation(generation=1)
        deletes: list[list[str]] = []
        updates: list[str] = []

        def fake_update(map_name: str, key_bytes: list[str], value_bytes: list[str]) -> dict[str, object]:
            updates.append(map_name)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        def fake_delete(map_name: str, key_bytes: list[str]) -> dict[str, object]:
            deletes.append(key_bytes)
            key = [f"0x{part}" for part in key_bytes]
            entries_by_map[map_name] = [
                entry
                for entry in entries_by_map.get(map_name, [])
                if entry.get("key") != key
            ]
            return {"returncode": 0, "stdout": "", "stderr": ""}

        entries_by_map = {
            "core_lfib_map": [
                self._core_entry(generation=1),
                self._core_entry(generation=1, label=777),
            ],
            "interface_role_map": [self._interface_role_entry()],
        }

        with self._mock_bpf_maps(
            active_generation=0,
            entries_by_map=entries_by_map,
        ), patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.delete_mpls_map_key",
            side_effect=fake_delete,
        ):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertEqual(len(deletes), 1)
        self.assertEqual(updates, ["active_generation"])
        self.assertEqual(result["map_diff_trace"]["core_lfib_map"]["delete_count"], 1)

    def test_steady_map_only_full_generation_reload_is_blocker(self) -> None:
        compiled = self._compiled_core_generation(generation=1)
        updates: list[str] = []

        def fake_update(map_name: str, key_bytes: list[str], value_bytes: list[str]) -> dict[str, object]:
            updates.append(map_name)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with self._mock_bpf_maps(
            active_generation=0,
            entries_by_map={},
        ), patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.delete_mpls_map_key",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ):
            result = load_mpls_maps(compiled)

        self.assertFalse(result["ok"])
        self.assertEqual(updates, [])
        self.assertTrue(result["full_generation_reload_blocker"])
        self.assertIn("RUNTIME_FULL_GENERATION_RELOAD_BLOCKER", result["outcome_labels"])
        self.assertEqual(result["failed_operation"], "update")

    def test_load_mpls_maps_merges_classifier_and_mpls_roles_on_same_ifindex(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 0},
            "ingress_classifier_map": [
                {
                    "key": {
                        "generation": 0,
                        "ingress_ifindex": 77,
                        "dst_ipv4": ipv4_little_endian_u32("10.0.0.9"),
                    },
                    "value": {
                        "active": True,
                        "fec_id": "fec-a",
                        "direction": G_TO_Z,
                        "initial_label": 100,
                        "out_ifindex": 78,
                    },
                }
            ],
            "core_lfib_map": [
                {
                    "key": {
                        "generation": 0,
                        "ingress_ifindex": 77,
                        "label": 100,
                        "direction": Z_TO_G,
                    },
                    "value": {
                        "active": True,
                        "action": "preserve",
                        "out_label": 100,
                        "out_ifindex": 79,
                    },
                }
            ],
            "egress_handoff_map": [],
        }

        with patch(
            "satellite.bpf_mpls_live.update_mpls_map",
            return_value={"returncode": 0, "stdout": "", "stderr": ""},
        ):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["loaded_interface_roles"],
            [
                {
                    "ifindex": 77,
                    "role": MPLS_INTERFACE_ROLE_CLASSIFIER | MPLS_INTERFACE_ROLE_MPLS,
                    "classifier": True,
                    "mpls": True,
                }
            ],
        )

    def test_load_mpls_maps_seeds_roles_from_attached_interface_details(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 0},
            "ingress_classifier_map": [],
            "core_lfib_map": [],
            "egress_handoff_map": [],
            "attached_interface_details": [
                {
                    "interface_ref": "netns:gw211:ix210",
                    "namespace": "gw211",
                    "interface": "ix210",
                    "ifindex": 323,
                    "expected_roles": ["z_to_g_classifier"],
                }
            ],
        }
        calls: list[tuple[str, list[str], list[str]]] = []

        def fake_update(
            map_name: str,
            key_bytes: list[str],
            value_bytes: list[str],
        ) -> dict[str, object]:
            calls.append((map_name, key_bytes, value_bytes))
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertEqual(result["loaded_interface_role_count"], 1)
        self.assertEqual(
            result["loaded_interface_roles"],
            [
                {
                    "ifindex": 323,
                    "role": MPLS_INTERFACE_ROLE_CLASSIFIER,
                    "classifier": True,
                    "mpls": False,
                }
            ],
        )
        self.assertEqual(
            result["loaded_interface_role_sources"],
            [
                {
                    "ifindex": 323,
                    "sources": ["attached_interface_details"],
                }
            ],
        )
        self.assertEqual(
            [call[0] for call in calls],
            ["interface_role_map", "active_generation"],
        )
        self.assertEqual(result["map_write_update_count"], 1)
        self.assertEqual(result["active_generation_update_count"], 1)
        self.assertEqual(result["total_update_count"], 2)

    def test_load_mpls_maps_reports_first_failed_update_without_generation_flip(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 5},
            "ingress_classifier_map": [],
            "core_lfib_map": [
                {
                    "key": {
                        "generation": 5,
                        "ingress_ifindex": 21,
                        "label": 100,
                        "direction": G_TO_Z,
                    },
                    "value": {
                        "active": True,
                        "action": "swap",
                        "out_label": 101,
                        "out_ifindex": 22,
                    },
                }
            ],
            "egress_handoff_map": [],
        }
        calls: list[str] = []

        def fake_update(
            map_name: str,
            _key_bytes: list[str],
            _value_bytes: list[str],
        ) -> dict[str, object]:
            calls.append(map_name)
            if map_name == "core_lfib_map":
                return {
                    "returncode": 22,
                    "stdout": "",
                    "stderr": "invalid argument",
                }
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.mpls_map_state",
            return_value={
                "map_name": "core_lfib_map",
                "pinned_path": "/sys/fs/bpf/tc/globals/core_lfib_map",
                "fd_state": "resolved",
                "max_entries": 256,
                "current_entries": 0,
                "expected_entries": 1,
                "stale_entries": None,
            },
        ):
            result = load_mpls_maps(compiled)

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_map"], "core_lfib_map")
        self.assertEqual(result["failed_operation"], "update")
        self.assertEqual(result["failed_generation"], 5)
        self.assertTrue(result["map_write_blocked_after_first_failure"])
        self.assertLess(result["total_update_count"], result["expected_total_update_count"])
        self.assertTrue(result["active_generation_flip_skipped"])
        self.assertFalse(result["active_generation_flip_attempted"])
        self.assertFalse(result["active_generation_flip_bpftool_invoked"])
        self.assertTrue(result["active_generation_left_unchanged_by_loader"])
        self.assertNotIn("active_generation", calls)
        first_failed = result["first_failed_update"]
        self.assertEqual(first_failed["direct_error_evidence"]["returncode"], 22)
        self.assertEqual(first_failed["direct_error_evidence"]["stderr"], "invalid argument")
        self.assertEqual(first_failed["map_state"]["expected_entries"], 1)
        self.assertTrue(
            result["schema_consistency"]["bootstrap_and_steady_reload_share_schema"]
        )

    def test_load_mpls_maps_schema_mismatch_fails_before_bpftool_update(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 2},
            "ingress_classifier_map": [
                {
                    "key": {
                        "generation": 2,
                        "ingress_ifindex": 11,
                        "dst_ipv4": ipv4_little_endian_u32("10.0.0.9"),
                    },
                    "value": {
                        "active": True,
                        "fec_id": "fec-a",
                        "direction": G_TO_Z,
                        "initial_label": 100,
                        "out_ifindex": 12,
                        "dst_mac": "02:00:00:00:00:12",
                        "src_mac": "02:00:00:00:00:11",
                    },
                }
            ],
            "core_lfib_map": [],
            "egress_handoff_map": [],
        }
        update_calls: list[str] = []

        def fake_update(
            map_name: str,
            _key_bytes: list[str],
            _value_bytes: list[str],
        ) -> dict[str, object]:
            update_calls.append(map_name)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        def fake_bpftool_json(args):
            self.assertEqual(args, ["-j", "map", "show"])
            return [
                {
                    "id": 17,
                    "name": "ingress_classif",
                    "type": "hash",
                    "bytes_key": 16,
                    "bytes_value": 32,
                    "max_entries": 128,
                    "pinned": "/sys/fs/bpf/tc/globals/ingress_classifier_map",
                }
            ]

        def fake_bpftool(args, **_kwargs):
            self.assertEqual(args[:3], ["-j", "map", "dump"])
            return {"returncode": 0, "stdout": "[]", "stderr": ""}

        with patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.bpftool_json",
            side_effect=fake_bpftool_json,
        ), patch("satellite.bpf_mpls_live.bpftool", side_effect=fake_bpftool):
            result = load_mpls_maps(compiled)

        self.assertFalse(result["ok"])
        self.assertEqual(update_calls, [])
        self.assertEqual(result["failed_map"], "ingress_classifier_map")
        self.assertEqual(result["failed_operation"], "update")
        self.assertEqual(result["failed_generation"], 2)
        self.assertTrue(result["map_write_blocked_after_first_failure"])
        self.assertLess(result["total_update_count"], result["expected_total_update_count"])
        self.assertTrue(result["active_generation_flip_skipped"])
        self.assertFalse(result["active_generation_flip_attempted"])
        self.assertFalse(result["active_generation_flip_bpftool_invoked"])
        first_failed = result["first_failed_update"]
        self.assertIn(
            "bpf_mpls_map_schema_mismatch",
            first_failed["direct_error_evidence"]["stderr"],
        )
        self.assertEqual(
            first_failed["direct_error_evidence"]["source"],
            "loader_schema_preflight",
        )
        self.assertEqual(first_failed["failed_key_byte_length"], 16)
        self.assertEqual(first_failed["failed_value_byte_length"], 36)
        self.assertEqual(first_failed["failed_key_decoded"]["generation"], 2)
        self.assertEqual(first_failed["failed_key_decoded"]["ingress_ifindex"], 11)
        self.assertEqual(first_failed["failed_value_decoded"]["initial_label"], 100)
        self.assertEqual(first_failed["failed_value_decoded"]["out_ifindex"], 12)
        self.assertEqual(
            first_failed["failure_analysis"]["conclusion"],
            "bpf_mpls_map_schema_mismatch",
        )
        self.assertTrue(first_failed["failure_analysis"]["stale_schema_suspected"])
        self.assertFalse(first_failed["map_state"]["schema_matches_expected"])
        self.assertEqual(first_failed["map_state"]["expected_type"], "hash")
        self.assertEqual(first_failed["map_state"]["live_type"], "hash")
        self.assertEqual(first_failed["map_state"]["expected_bytes_key"], 16)
        self.assertEqual(first_failed["map_state"]["live_bytes_key"], 16)
        self.assertEqual(first_failed["map_state"]["live_bytes_value"], 32)
        self.assertEqual(
            first_failed["map_state"]["expected_shape"]["bytes_value"],
            36,
        )
        self.assertEqual(first_failed["map_state"]["expected_bytes_value"], 36)
        self.assertEqual(first_failed["map_state"]["expected_max_entries"], 128)
        self.assertEqual(first_failed["map_state"]["live_max_entries"], 128)
        self.assertEqual(first_failed["map_state"]["map_ids"], [17])
        self.assertEqual(first_failed["map_state"]["selected_map_id"], 17)
        self.assertIsNone(first_failed["map_state"]["map_fd"])
        self.assertEqual(first_failed["map_state"]["current_entries"], 0)
        self.assertEqual(first_failed["map_state"]["stale_entries"], 0)
        self.assertEqual(
            first_failed["map_state"]["pinned_path"],
            "/sys/fs/bpf/tc/globals/ingress_classifier_map",
        )

    def test_load_mpls_maps_schema_match_e2big_is_not_stale_schema(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 2},
            "ingress_classifier_map": [
                {
                    "key": {
                        "generation": 2,
                        "ingress_ifindex": 11,
                        "dst_ipv4": ipv4_little_endian_u32("10.0.0.9"),
                    },
                    "value": {
                        "active": True,
                        "fec_id": "fec-a",
                        "direction": G_TO_Z,
                        "initial_label": 100,
                        "out_ifindex": 12,
                        "dst_mac": "02:00:00:00:00:12",
                        "src_mac": "02:00:00:00:00:11",
                    },
                }
            ],
            "core_lfib_map": [],
            "egress_handoff_map": [],
        }

        def fake_update(
            map_name: str,
            key_bytes: list[str],
            value_bytes: list[str],
        ) -> dict[str, object]:
            self.assertEqual(map_name, "ingress_classifier_map")
            self.assertEqual(len(key_bytes), 16)
            self.assertEqual(len(value_bytes), 36)
            return {
                "args": [
                    "bpftool",
                    "map",
                    "update",
                    "id",
                    "17",
                    "key",
                    "hex",
                    *key_bytes,
                    "value",
                    "hex",
                    *value_bytes,
                ],
                "returncode": 1,
                "errno": "E2BIG",
                "stdout": "",
                "stderr": "Error: update failed: Argument list too long",
            }

        def fake_bpftool_json(args):
            self.assertEqual(args, ["-j", "map", "show"])
            return [
                {
                    "id": 17,
                    "name": "ingress_classif",
                    "type": "hash",
                    "bytes_key": 16,
                    "bytes_value": 36,
                    "max_entries": 128,
                    "pinned": "/sys/fs/bpf/tc/globals/ingress_classifier_map",
                }
            ]

        def fake_bpftool(args, **_kwargs):
            self.assertEqual(args[:3], ["-j", "map", "dump"])
            return {"returncode": 0, "stdout": "[]", "stderr": ""}

        with patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.bpftool_json",
            side_effect=fake_bpftool_json,
        ), patch("satellite.bpf_mpls_live.bpftool", side_effect=fake_bpftool):
            result = load_mpls_maps(compiled)

        first_failed = result["first_failed_update"]
        self.assertFalse(result["ok"])
        self.assertFalse(first_failed["failure_analysis"]["stale_schema_suspected"])
        self.assertTrue(first_failed["failure_analysis"]["schema_match_investigation_required"])
        self.assertEqual(
            first_failed["failure_analysis"]["conclusion"],
            "E2BIG_REQUIRES_NON_SCHEMA_INVESTIGATION",
        )
        self.assertTrue(first_failed["map_state"]["schema_matches_expected"])
        self.assertEqual(first_failed["failed_value_byte_length"], 36)
        self.assertEqual(first_failed["direct_error_evidence"]["errno"], "E2BIG")
        self.assertFalse(result["active_generation_flip_attempted"])
        self.assertFalse(result["active_generation_flip_bpftool_invoked"])

    def test_generation_keyed_ingress_capacity_requires_active_plus_target_entries(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 1},
            "ingress_classifier_map": [
                {
                    "key": {
                        "generation": 1,
                        "ingress_ifindex": 100 + index,
                        "dst_ipv4": ipv4_little_endian_u32(f"10.0.0.{index + 1}"),
                    },
                    "value": {
                        "active": True,
                        "fec_id": f"fec-{index}",
                        "direction": G_TO_Z,
                        "initial_label": 100 + index,
                        "out_ifindex": 200 + index,
                        "dst_mac": "02:00:00:00:00:12",
                        "src_mac": "02:00:00:00:00:11",
                    },
                }
                for index in range(5)
            ],
            "core_lfib_map": [],
            "egress_handoff_map": [],
        }
        update_calls: list[str] = []

        def key_for_generation(generation: int, index: int) -> list[str]:
            return [
                f"{byte:02x}"
                for byte in (
                    int(generation).to_bytes(4, "little")
                    + int(300 + index).to_bytes(4, "little")
                    + int(index).to_bytes(4, "little")
                    + (0).to_bytes(4, "little")
                )
            ]

        def fake_update(map_name, _key_bytes, _value_bytes):
            update_calls.append(map_name)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        def fake_bpftool_json(args):
            self.assertEqual(args, ["-j", "map", "show"])
            return [
                {
                    "id": 11,
                    "name": "active_generati",
                    "type": "array",
                    "bytes_key": 4,
                    "bytes_value": 4,
                    "max_entries": 1,
                    "pinned": "/sys/fs/bpf/tc/globals/active_generation",
                },
                {
                    "id": 17,
                    "name": "ingress_classif",
                    "type": "hash",
                    "bytes_key": 16,
                    "bytes_value": 36,
                    "max_entries": 20,
                    "pinned": "/sys/fs/bpf/tc/globals/ingress_classifier_map",
                },
            ]

        def fake_bpftool(args, **_kwargs):
            if args[:3] == ["-j", "map", "dump"] and "active_generation" in args:
                return {
                    "returncode": 0,
                    "stdout": (
                        '[{"key":["00","00","00","00"],'
                        '"value":["00","00","00","00"]}]'
                    ),
                    "stderr": "",
                }
            if args[:3] == ["-j", "map", "dump"]:
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        [{"key": key_for_generation(0, index), "value": []} for index in range(20)]
                    ),
                    "stderr": "",
                }
            self.fail(f"unexpected bpftool args: {args}")

        with patch("satellite.bpf_mpls_live.update_mpls_map", side_effect=fake_update), patch(
            "satellite.bpf_mpls_live.bpftool_json",
            side_effect=fake_bpftool_json,
        ), patch("satellite.bpf_mpls_live.bpftool", side_effect=fake_bpftool):
            result = load_mpls_maps(compiled)

        self.assertFalse(result["ok"])
        self.assertEqual(update_calls, [])
        self.assertFalse(result["active_generation_flip_attempted"])
        self.assertFalse(result["active_generation_flip_bpftool_invoked"])
        self.assertTrue(result["active_generation_left_unchanged_by_loader"])
        first_failed = result["first_failed_update"]
        self.assertEqual(first_failed["failed_map"], "ingress_classifier_map")
        self.assertEqual(
            first_failed["direct_error_evidence"]["source"],
            "loader_generation_capacity_preflight",
        )
        self.assertEqual(
            first_failed["failure_analysis"]["conclusion"],
            BPF_MAP_GENERATION_CAPACITY_BLOCKER,
        )
        contract = first_failed["map_state"]["generation_capacity_contract"]
        self.assertEqual(contract["active_generation"], 0)
        self.assertEqual(contract["active_generation_entry_count"], 20)
        self.assertEqual(contract["target_expected_entries"], 5)
        self.assertEqual(contract["required_capacity"], 41)
        self.assertEqual(contract["live_max_entries"], 20)
        self.assertFalse(contract["ok"])

    def test_map_state_reads_live_capacity_from_pinned_reference_when_name_scan_misses(
        self,
    ) -> None:
        def fake_bpftool(args, **_kwargs):
            if args[:3] == ["-j", "map", "show"]:
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "id": 17,
                            "name": "ingress_classif",
                            "type": "hash",
                            "bytes_key": 16,
                            "bytes_value": 36,
                            "max_entries": 142,
                            "pinned": "/sys/fs/bpf/tc/globals/ingress_classifier_map",
                        }
                    ),
                    "stderr": "",
                }
            if args[:3] == ["-j", "map", "dump"]:
                return {"returncode": 0, "stdout": "[]", "stderr": ""}
            self.fail(f"unexpected bpftool args: {args}")

        with patch("satellite.bpf_mpls_live.bpftool_json", return_value=[]), patch(
            "satellite.bpf_mpls_live.mpls_map_references",
            return_value=[
                (
                    ["pinned", "/sys/fs/bpf/tc/globals/ingress_classifier_map"],
                    {
                        "kind": "pinned",
                        "path": "/sys/fs/bpf/tc/globals/ingress_classifier_map",
                    },
                )
            ],
        ), patch("satellite.bpf_mpls_live.bpftool", side_effect=fake_bpftool):
            state = mpls_map_state(
                "ingress_classifier_map",
                expected_entries=126,
                active_generation=0,
                target_generation=0,
            )

        self.assertEqual(state["live_max_entries"], 142)
        self.assertEqual(state["max_entries"], 142)
        self.assertTrue(state["type_key_value_matches_expected"])
        self.assertTrue(state["schema_matches_expected"])
        self.assertFalse(state["capacity_matches_static_expected_max"])

    def test_target_generation_cleanup_occurs_before_loading_target_generation(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 1},
            "ingress_classifier_map": [
                {
                    "key": {
                        "generation": 1,
                        "ingress_ifindex": 11,
                        "dst_ipv4": ipv4_little_endian_u32("10.0.0.9"),
                    },
                    "value": {
                        "active": True,
                        "fec_id": "fec-a",
                        "direction": G_TO_Z,
                        "initial_label": 100,
                        "out_ifindex": 12,
                        "dst_mac": "02:00:00:00:00:12",
                        "src_mac": "02:00:00:00:00:11",
                    },
                }
            ],
            "core_lfib_map": [],
            "egress_handoff_map": [],
        }
        order: list[str] = []
        deleted_keys: set[tuple[str, ...]] = set()

        def key_for_generation(generation: int, index: int) -> list[str]:
            return [
                f"{byte:02x}"
                for byte in (
                    int(generation).to_bytes(4, "little")
                    + int(20 + index).to_bytes(4, "little")
                    + int(index).to_bytes(4, "little")
                    + (0).to_bytes(4, "little")
                )
            ]

        def fake_delete(map_name, key_bytes):
            order.append(f"delete:{map_name}:{key_bytes[0]}")
            deleted_keys.add(tuple(str(part) for part in key_bytes))
            return {"returncode": 0, "stdout": "", "stderr": ""}

        def fake_update(map_name, _key_bytes, _value_bytes):
            order.append(f"update:{map_name}")
            return {"returncode": 0, "stdout": "", "stderr": ""}

        def fake_bpftool_json(_args):
            return [
                {
                    "id": 11,
                    "name": "active_generati",
                    "type": "array",
                    "bytes_key": 4,
                    "bytes_value": 4,
                    "max_entries": 1,
                },
                {
                    "id": 17,
                    "name": "ingress_classif",
                    "type": "hash",
                    "bytes_key": 16,
                    "bytes_value": 36,
                    "max_entries": 128,
                },
            ]

        def fake_bpftool(args, **_kwargs):
            if args[:3] == ["-j", "map", "dump"] and "active_generation" in args:
                return {
                    "returncode": 0,
                    "stdout": (
                        '[{"key":["00","00","00","00"],'
                        '"value":["00","00","00","00"]}]'
                    ),
                    "stderr": "",
                }
            if args[:3] == ["-j", "map", "dump"]:
                entries = [
                    {"key": key_for_generation(0, 0), "value": []},
                    {"key": key_for_generation(0, 1), "value": []},
                    {"key": key_for_generation(1, 2), "value": []},
                    {"key": key_for_generation(1, 3), "value": []},
                ]
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        [
                            entry
                            for entry in entries
                            if tuple(entry["key"]) not in deleted_keys
                        ]
                    ),
                    "stderr": "",
                }
            self.fail(f"unexpected bpftool args: {args}")

        with patch(
            "satellite.bpf_mpls_live.read_mpls_active_generation",
            return_value={"ok": True, "active_generation": 0},
        ), patch("satellite.bpf_mpls_live.delete_mpls_map_key", side_effect=fake_delete), patch(
            "satellite.bpf_mpls_live.update_mpls_map",
            side_effect=fake_update,
        ), patch("satellite.bpf_mpls_live.bpftool_json", side_effect=fake_bpftool_json), patch(
            "satellite.bpf_mpls_live.bpftool",
            side_effect=fake_bpftool,
        ):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertEqual(order[:2], ["delete:ingress_classifier_map:01"] * 2)
        self.assertIn("update:ingress_classifier_map", order[2:])
        self.assertEqual(
            result["target_generation_cleanup"]["maps"]["ingress_classifier_map"][
                "stale_target_entry_count"
            ],
            2,
        )
        self.assertEqual(result["active_generation_update_count"], 1)

    def test_generation_gc_deletes_non_active_non_target_before_capacity_gate(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 2},
            "ingress_classifier_map": [
                {
                    "key": {
                        "generation": 2,
                        "ingress_ifindex": 11,
                        "dst_ipv4": ipv4_little_endian_u32("10.0.0.9"),
                    },
                    "value": {
                        "active": True,
                        "fec_id": "fec-a",
                        "direction": G_TO_Z,
                        "initial_label": 100,
                        "out_ifindex": 12,
                        "dst_mac": "02:00:00:00:00:12",
                        "src_mac": "02:00:00:00:00:11",
                    },
                }
            ],
            "core_lfib_map": [],
            "egress_handoff_map": [],
        }
        order: list[str] = []
        deleted_keys: set[tuple[str, ...]] = set()

        def key_for_generation(generation: int, index: int) -> list[str]:
            return [
                f"{byte:02x}"
                for byte in (
                    int(generation).to_bytes(4, "little")
                    + int(20 + index).to_bytes(4, "little")
                    + int(index).to_bytes(4, "little")
                    + (0).to_bytes(4, "little")
                )
            ]

        map_entries = [
            {"key": key_for_generation(0, 0), "value": []},
            {"key": key_for_generation(0, 1), "value": []},
            {"key": key_for_generation(1, 2), "value": []},
            {"key": key_for_generation(1, 3), "value": []},
            {"key": key_for_generation(2, 4), "value": []},
        ]

        def fake_delete(map_name, key_bytes):
            order.append(
                f"delete:{map_name}:{int.from_bytes(bytes(int(part, 16) for part in key_bytes[:4]), 'little')}"
            )
            deleted_keys.add(tuple(str(part) for part in key_bytes))
            return {"returncode": 0, "stdout": "", "stderr": ""}

        def fake_update(map_name, _key_bytes, _value_bytes):
            order.append(f"update:{map_name}")
            return {"returncode": 0, "stdout": "", "stderr": ""}

        def fake_bpftool_json(_args):
            return [
                {
                    "id": 11,
                    "name": "active_generati",
                    "type": "array",
                    "bytes_key": 4,
                    "bytes_value": 4,
                    "max_entries": 1,
                },
                {
                    "id": 17,
                    "name": "ingress_classif",
                    "type": "hash",
                    "bytes_key": 16,
                    "bytes_value": 36,
                    "max_entries": 32,
                },
            ]

        def fake_bpftool(args, **_kwargs):
            if args[:3] == ["-j", "map", "dump"] and "active_generation" in args:
                return {
                    "returncode": 0,
                    "stdout": (
                        '[{"key":["00","00","00","00"],'
                        '"value":["01","00","00","00"]}]'
                    ),
                    "stderr": "",
                }
            if args[:3] == ["-j", "map", "dump"]:
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        [
                            entry
                            for entry in map_entries
                            if tuple(entry["key"]) not in deleted_keys
                        ]
                    ),
                    "stderr": "",
                }
            self.fail(f"unexpected bpftool args: {args}")

        with patch(
            "satellite.bpf_mpls_live.read_mpls_active_generation",
            return_value={"ok": True, "active_generation": 1},
        ), patch("satellite.bpf_mpls_live.delete_mpls_map_key", side_effect=fake_delete), patch(
            "satellite.bpf_mpls_live.update_mpls_map",
            side_effect=fake_update,
        ), patch("satellite.bpf_mpls_live.bpftool_json", side_effect=fake_bpftool_json), patch(
            "satellite.bpf_mpls_live.bpftool",
            side_effect=fake_bpftool,
        ):
            result = load_mpls_maps(compiled)

        self.assertTrue(result["ok"])
        self.assertEqual(order[:3], ["delete:ingress_classifier_map:0"] * 2 + ["delete:ingress_classifier_map:2"])
        self.assertNotIn("delete:ingress_classifier_map:1", order)
        self.assertIn("update:ingress_classifier_map", order[3:])
        gc = result["generation_gc_cleanup"]["maps"]["ingress_classifier_map"]
        self.assertEqual(gc["pre_cleanup_generation_entry_counts"], {"0": 2, "1": 2, "2": 1})
        self.assertEqual(gc["post_cleanup_generation_entry_counts"], {"1": 2})
        self.assertEqual(gc["stale_inactive_generation_entry_count"], 2)
        self.assertEqual(gc["target_generation_stale_entry_count"], 1)
        contract = result["generation_capacity_contracts"]["ingress_classifier_map"]
        self.assertEqual(contract["retained_entry_count_after_gc"], 2)
        self.assertEqual(contract["required_capacity"], 19)
        self.assertTrue(contract["ok"])

    def test_generation_gc_failure_blocks_data_updates_and_active_generation_flip(
        self,
    ) -> None:
        compiled = {
            "active_generation": {"value": 2},
            "ingress_classifier_map": [
                {
                    "key": {
                        "generation": 2,
                        "ingress_ifindex": 11,
                        "dst_ipv4": ipv4_little_endian_u32("10.0.0.9"),
                    },
                    "value": {
                        "active": True,
                        "fec_id": "fec-a",
                        "direction": G_TO_Z,
                        "initial_label": 100,
                        "out_ifindex": 12,
                        "dst_mac": "02:00:00:00:00:12",
                        "src_mac": "02:00:00:00:00:11",
                    },
                }
            ],
            "core_lfib_map": [],
            "egress_handoff_map": [],
        }
        update_calls: list[str] = []

        def key_for_generation(generation: int) -> list[str]:
            return [
                f"{byte:02x}"
                for byte in (
                    int(generation).to_bytes(4, "little")
                    + (11).to_bytes(4, "little")
                    + (9).to_bytes(4, "little")
                    + (0).to_bytes(4, "little")
                )
            ]

        def fake_delete(_map_name, _key_bytes):
            return {"returncode": 1, "stdout": "", "stderr": "delete failed"}

        def fake_update(map_name, _key_bytes, _value_bytes):
            update_calls.append(map_name)
            return {"returncode": 0, "stdout": "", "stderr": ""}

        def fake_bpftool_json(_args):
            return [
                {
                    "id": 11,
                    "name": "active_generati",
                    "type": "array",
                    "bytes_key": 4,
                    "bytes_value": 4,
                    "max_entries": 1,
                },
                {
                    "id": 17,
                    "name": "ingress_classif",
                    "type": "hash",
                    "bytes_key": 16,
                    "bytes_value": 36,
                    "max_entries": 32,
                },
            ]

        def fake_bpftool(args, **_kwargs):
            if args[:3] == ["-j", "map", "dump"] and "active_generation" in args:
                return {
                    "returncode": 0,
                    "stdout": (
                        '[{"key":["00","00","00","00"],'
                        '"value":["01","00","00","00"]}]'
                    ),
                    "stderr": "",
                }
            if args[:3] == ["-j", "map", "dump"]:
                return {
                    "returncode": 0,
                    "stdout": json.dumps(
                        [
                            {"key": key_for_generation(0), "value": []},
                            {"key": key_for_generation(1), "value": []},
                        ]
                    ),
                    "stderr": "",
                }
            self.fail(f"unexpected bpftool args: {args}")

        with patch("satellite.bpf_mpls_live.delete_mpls_map_key", side_effect=fake_delete), patch(
            "satellite.bpf_mpls_live.update_mpls_map",
            side_effect=fake_update,
        ), patch("satellite.bpf_mpls_live.bpftool_json", side_effect=fake_bpftool_json), patch(
            "satellite.bpf_mpls_live.bpftool",
            side_effect=fake_bpftool,
        ):
            result = load_mpls_maps(compiled)

        self.assertFalse(result["ok"])
        self.assertEqual(update_calls, [])
        self.assertFalse(result["active_generation_flip_attempted"])
        self.assertFalse(result["active_generation_flip_bpftool_invoked"])
        self.assertTrue(result["active_generation_left_unchanged_by_loader"])
        self.assertFalse(result["generation_gc_cleanup"]["ok"])

    def test_read_mpls_interface_roles_decodes_live_role_map(self) -> None:
        def fake_bpftool(args, **_kwargs):
            self.assertEqual(args[:3], ["-j", "map", "dump"])
            return {
                "returncode": 0,
                "stdout": (
                    "["
                    "{\"key\":[\"43\",\"01\",\"00\",\"00\"],"
                    "\"value\":[\"03\",\"00\",\"00\",\"00\"]}"
                    "]"
                ),
                "stderr": "",
            }

        with patch(
            "satellite.bpf_mpls_live.mpls_map_references",
            return_value=[
                (
                    ["pinned", "/sys/fs/bpf/tc/globals/interface_role_map"],
                    {"kind": "pinned", "path": "/sys/fs/bpf/tc/globals/interface_role_map"},
                )
            ],
        ), patch("satellite.bpf_mpls_live.bpftool", side_effect=fake_bpftool):
            result = read_mpls_interface_roles()

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["entries"],
            [
                {
                    "ifindex": 323,
                    "role": MPLS_INTERFACE_ROLE_CLASSIFIER | MPLS_INTERFACE_ROLE_MPLS,
                    "classifier": True,
                    "mpls": True,
                    "map_reference": {
                        "kind": "pinned",
                        "path": "/sys/fs/bpf/tc/globals/interface_role_map",
                    },
                }
            ],
        )

    def test_attach_mpls_adds_control_plane_bypass_before_bpf(self) -> None:
        commands: list[list[str]] = []

        def fake_run(interface_ref, args, *, timeout):
            self.assertEqual(interface_ref, "netns:gw205:ix204")
            commands.append([str(item) for item in args])
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with patch(
            "satellite.bpf_mpls_live.run_mpls_interface_command",
            side_effect=fake_run,
        ):
            result = attach_mpls(
                "netns:gw205:ix204",
                Path("/tmp/prog.o"),
                directions=["ingress"],
                control_plane_ipv4_addrs=["10.204.0.250"],
            )

        self.assertEqual(len(result), 4)
        self.assertEqual(commands[2][commands[2].index("prio") + 1], "1")
        self.assertIn("flower", commands[2])
        self.assertEqual(
            commands[2][commands[2].index("dst_ip") + 1],
            "10.204.0.250",
        )
        self.assertEqual(commands[3][commands[3].index("prio") + 1], "10")
        self.assertIn("bpf", commands[3])

    def test_attach_mpls_control_plane_bypass_is_ipv4_exact_local_only(self) -> None:
        commands: list[list[str]] = []

        def fake_run(interface_ref, args, *, timeout):
            commands.append([str(item) for item in args])
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with patch(
            "satellite.bpf_mpls_live.run_mpls_interface_command",
            side_effect=fake_run,
        ):
            attach_mpls(
                "netns:gw205:ix204",
                Path("/tmp/prog.o"),
                directions=["ingress", "egress"],
                control_plane_ipv4_addrs=[
                    "10.204.0.250",
                    "10.204.0.250",
                    "",
                ],
            )

        flower_commands = [command for command in commands if "flower" in command]
        self.assertEqual(len(flower_commands), 2)
        for command in flower_commands:
            self.assertEqual(command[command.index("protocol") + 1], "ip")
            self.assertEqual(command[command.index("prio") + 1], "1")
            self.assertEqual(command[command.index("action") + 1 :], ["goto", "chain", "100"])
            self.assertNotIn("mpls", command)
            self.assertNotIn("10.154.0.0/16", command)
            self.assertNotIn("10.90.0.0/24", command)
        ingress = next(command for command in flower_commands if "ingress" in command)
        egress = next(command for command in flower_commands if "egress" in command)
        self.assertEqual(ingress[ingress.index("dst_ip") + 1], "10.204.0.250")
        self.assertNotIn("src_ip", ingress)
        self.assertEqual(egress[egress.index("src_ip") + 1], "10.204.0.250")
        self.assertNotIn("dst_ip", egress)
        bpf_commands = [command for command in commands if "bpf" in command]
        self.assertEqual(len(bpf_commands), 2)
        self.assertTrue(all(command[command.index("prio") + 1] == "10" for command in bpf_commands))

    def test_read_mpls_tc_filter_stats_exposes_flower_counters(self) -> None:
        commands: list[list[str]] = []

        def fake_run(interface_ref, args, *, timeout):
            self.assertEqual(interface_ref, "netns:gw205:ix204")
            commands.append([str(item) for item in args])
            return {
                "returncode": 0,
                "stdout": "filter protocol ip pref 1 flower chain 0\\n Sent 1 bytes 84 pkt 1",
                "stderr": "",
            }

        with patch(
            "satellite.bpf_mpls_live.run_mpls_interface_command",
            side_effect=fake_run,
        ):
            result = read_mpls_tc_filter_stats(
                "netns:gw205:ix204",
                directions=["ingress"],
            )

        self.assertEqual(
            commands,
            [["tc", "-s", "filter", "show", "dev", "{iface}", "ingress"]],
        )
        self.assertEqual(result["directions"], ["ingress"])
        self.assertIn("Sent 1 bytes", result["stats"]["ingress"]["stdout"])

    def test_compile_mpls_bpf_reuses_cache_for_same_source_and_capacities(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "prog.c"
            source.write_text("int x = 1;\n", encoding="utf-8")
            cache_dir = temp_path / "cache"
            first_object = temp_path / "first.o"
            second_object = temp_path / "second.o"
            calls: list[list[str]] = []

            def fake_run_command(args, **_kwargs):
                calls.append([str(item) for item in args])
                output_path = Path(args[args.index("-o") + 1])
                output_path.write_bytes(b"compiled-object")
                return {"args": list(args), "returncode": 0, "stdout": "", "stderr": ""}

            with patch("satellite.bpf_mpls_live.run_command", side_effect=fake_run_command):
                first = compile_mpls_bpf(
                    source=source,
                    object_path=first_object,
                    cache_dir=cache_dir,
                )
                second = compile_mpls_bpf(
                    source=source,
                    object_path=second_object,
                    cache_dir=cache_dir,
                )

            self.assertEqual(len(calls), 1)
            self.assertFalse(first["cache"]["cache_hit"])
            self.assertTrue(second["cache"]["cache_hit"])
            self.assertEqual(second_object.read_bytes(), b"compiled-object")

    def test_minimal_live_maps_have_two_edge_classifiers_and_label_core(self) -> None:
        names = {"ut": "ut-ns", "s1": "s1-ns", "s2": "s2-ns", "gw": "gw-ns"}
        fake_ifindices = {
            "ipnetns:s1-ns:s1u": 11,
            "ipnetns:s1-ns:s1s2": 12,
            "ipnetns:s2-ns:s2s1": 21,
            "ipnetns:s2-ns:s2gw": 22,
            "ipnetns:gw-ns:gws2": 31,
        }
        fake_macs = {
            "ipnetns:ut-ns:ut0": "02:00:00:00:00:01",
            "ipnetns:s1-ns:s1u": "02:00:00:00:00:11",
            "ipnetns:s1-ns:s1s2": "02:00:00:00:00:12",
            "ipnetns:s2-ns:s2s1": "02:00:00:00:00:21",
            "ipnetns:s2-ns:s2gw": "02:00:00:00:00:22",
            "ipnetns:gw-ns:gws2": "02:00:00:00:00:31",
        }

        with patch(
            "satellite.bpf_mpls_live.interface_ifindex",
            side_effect=lambda ref: fake_ifindices[ref],
        ), patch(
            "satellite.bpf_mpls_live.interface_mac",
            side_effect=lambda ref: fake_macs[ref],
        ):
            compiled = build_minimal_live_maps(names)

        self.assertEqual(len(compiled["ingress_classifier_map"]), 2)
        self.assertEqual(len(compiled["core_lfib_map"]), 2)
        self.assertEqual(len(compiled["egress_handoff_map"]), 2)
        self.assertEqual(
            {entry["value"]["direction"] for entry in compiled["ingress_classifier_map"]},
            {G_TO_Z, Z_TO_G},
        )
        self.assertFalse(
            any("dst_ipv4" in entry["key"] for entry in compiled["core_lfib_map"])
        )

    def test_runtime_contexts_compile_to_mpls_lfib_without_dst_ip_core_keys(
        self,
    ) -> None:
        context = DirectGatewayExperimentContext(
            flow_id="ut-internet-egress:ut42",
            ut_id="ut42",
            zone_id="zone-a",
            serving_satellite_id="sat-a",
            selected_gateway_id="gw-a",
            selected_gateway_satellite_id="sat-c",
            zone_return_prefixes=("10.90.42.2/32",),
            ping_target_ip="10.154.42.129",
            target_host_node_id="mini-a",
            policy_table_id=1042,
            policy_rule_priority=1042,
            satellite_path=("sat-a", "sat-b", "sat-c"),
            forward_labels=(1000, 1001, 1002),
            reverse_labels=(2000, 2001, 2002),
            ut_access_interface="ut0",
            ut_access_ip="10.90.42.2",
            ut_access_peer_ip="10.90.42.1",
            gateway_access_interface="gw0",
            gateway_access_ip="10.80.0.2",
            gateway_access_peer_ip="10.80.0.1",
            gateway_nat_interface="nat0",
            gateway_nat_source_ip="10.80.0.9",
            forward_segments=(
                MplsPathSegment(
                    "ab",
                    "sat-a",
                    "satellite",
                    "ab0",
                    "10.0.1.1",
                    "sat-b",
                    "satellite",
                    "ba0",
                    "10.0.1.2",
                    1000,
                ),
                MplsPathSegment(
                    "bc",
                    "sat-b",
                    "satellite",
                    "bc0",
                    "10.0.2.1",
                    "sat-c",
                    "satellite",
                    "cb0",
                    "10.0.2.2",
                    1001,
                ),
                MplsPathSegment(
                    "cg",
                    "sat-c",
                    "satellite",
                    "cg0",
                    "10.0.3.1",
                    "gw-a",
                    "gateway",
                    "gw0",
                    "10.0.3.2",
                    1002,
                ),
            ),
            reverse_segments=(
                MplsPathSegment(
                    "gc",
                    "gw-a",
                    "gateway",
                    "gw0",
                    "10.0.4.1",
                    "sat-c",
                    "satellite",
                    "cg0",
                    "10.0.4.2",
                    2000,
                ),
                MplsPathSegment(
                    "cb",
                    "sat-c",
                    "satellite",
                    "cb0",
                    "10.0.5.1",
                    "sat-b",
                    "satellite",
                    "bc0",
                    "10.0.5.2",
                    2001,
                ),
                MplsPathSegment(
                    "ba",
                    "sat-b",
                    "satellite",
                    "ba0",
                    "10.0.6.1",
                    "sat-a",
                    "satellite",
                    "ab0",
                    "10.0.6.2",
                    2002,
                ),
            ),
            validation_targets=(
                {"node_id": "mini-a", "ip_address": "10.154.42.129"},
                {"node_id": "mini-b", "ip_address": "10.160.42.72"},
            ),
            traffic_scope="default_gateway_class",
        )

        refs: set[str] = set()

        def fake_ifindex(interface_ref: str) -> int:
            refs.add(interface_ref)
            return abs(hash(interface_ref)) % 100000 + 1

        def fake_mac(interface_ref: str) -> str:
            refs.add(interface_ref)
            value = abs(hash(interface_ref)) % 0xFFFFFF
            return f"02:00:00:{(value >> 16) & 0xff:02x}:{(value >> 8) & 0xff:02x}:{value & 0xff:02x}"

        with patch(
            "satellite.bpf_mpls_live.interface_ifindex",
            side_effect=fake_ifindex,
        ), patch(
            "satellite.bpf_mpls_live.interface_mac",
            side_effect=fake_mac,
        ):
            compiled = _bpf_owned_compile_mpls_maps_for_contexts(
                contexts=[context],
                generation_id=9,
            )

        self.assertEqual(
            compiled["compiled_mpls_map_counts"],
            {
                "ingress_classifier_map": 3,
                "core_lfib_map": 4,
                "egress_handoff_map": 2,
                "active_generation": 1,
            },
        )
        self.assertFalse(compiled["intermediate_core_lookup_uses_dst_ip"])
        self.assertFalse(compiled["map_conflicts"])
        self.assertTrue(
            any(
                entry.get("handoff") == "zone_local_delivery"
                and int(entry.get("out_ifindex") or 0) > 0
                for entry in compiled["resolved_mpls_flow_entries"]
                if entry.get("map_type") == "egress_handoff_map"
            )
        )
        self.assertTrue(refs)
        for entry in compiled["core_lfib_map"]:
            self.assertEqual(entry["key"]["generation"], 9)
            self.assertIn("label", entry["key"])
            self.assertNotIn("dst_ipv4", entry["key"])
            self.assertNotIn("target_ip", entry["key"])
        self.assertEqual(
            {
                entry["direction"]
                for entry in compiled["resolved_mpls_flow_entries"]
                if entry["map_type"] == "core_lfib_map"
            },
            {G_TO_Z, Z_TO_G},
        )
        self.assertEqual(
            compiled["attach_plan"]["netns:sat-a:ut0"],
            ["ingress"],
        )

        override = replace(
            context,
            flow_id="ut-internet-egress:ut42:override:asn160_via_selected_secondary_gateway",
            selected_gateway_id="gw-b",
            selected_gateway_satellite_id="sat-c",
            ping_target_ip="10.160.42.72",
            target_host_node_id="mini-b",
            forward_labels=(3000, 3001, 3002),
            reverse_labels=(4000, 4001, 4002),
            forward_segments=(
                replace(context.forward_segments[0], label=3000),
                replace(context.forward_segments[1], label=3001),
                MplsPathSegment(
                    "cg-b",
                    "sat-c",
                    "satellite",
                    "cg-b0",
                    "10.0.7.1",
                    "gw-b",
                    "gateway",
                    "gw0",
                    "10.0.7.2",
                    3002,
                ),
            ),
            reverse_segments=(
                MplsPathSegment(
                    "gc-b",
                    "gw-b",
                    "gateway",
                    "gw0",
                    "10.0.8.1",
                    "sat-c",
                    "satellite",
                    "cg-b0",
                    "10.0.8.2",
                    4000,
                ),
                replace(context.reverse_segments[1], label=4001),
                replace(context.reverse_segments[2], label=4002),
            ),
            gateway_nat_source_ip="10.80.0.19",
            traffic_scope="special_prefix_override",
        )

        with patch(
            "satellite.bpf_mpls_live.interface_ifindex",
            side_effect=fake_ifindex,
        ), patch(
            "satellite.bpf_mpls_live.interface_mac",
            side_effect=fake_mac,
        ):
            combined = _bpf_owned_compile_mpls_maps_for_contexts(
                contexts=[context, override],
                generation_id=9,
            )

        self.assertFalse(combined["map_conflicts"])
        forward_classifiers = [
            entry
            for entry in combined["resolved_mpls_flow_entries"]
            if entry["map_type"] == "ingress_classifier_map"
            and entry["direction"] == G_TO_Z
        ]
        self.assertEqual(
            {
                (entry["flow_id"], entry["target_ip"])
                for entry in forward_classifiers
            },
            {
                ("ut-internet-egress:ut42", "10.154.42.129"),
                (
                    "ut-internet-egress:ut42:override:asn160_via_selected_secondary_gateway",
                    "10.160.42.72",
                ),
            },
        )
        classifier_labels = {
            entry["initial_label"] for entry in forward_classifiers
        }
        self.assertEqual(len(classifier_labels), 2)
        self.assertTrue(all(label >= 524288 for label in classifier_labels))
        self.assertEqual(
            combined["map_conflict_summary"]["unsafe_conflict_count"],
            0,
        )
        self.assertGreaterEqual(
            len(combined["label_allocation"]["entries"]),
            2,
        )

    def test_reverse_zone_delivery_labels_and_handoffs_are_ut_specific(
        self,
    ) -> None:
        base = DirectGatewayExperimentContext(
            flow_id="ut-internet-egress:ut42",
            ut_id="ut42",
            zone_id="zone-a",
            serving_satellite_id="sat-a",
            selected_gateway_id="gw-a",
            selected_gateway_satellite_id="sat-c",
            zone_return_prefixes=("10.90.0.0/24",),
            ping_target_ip="10.154.42.129",
            target_host_node_id="mini-a",
            policy_table_id=1042,
            policy_rule_priority=1042,
            satellite_path=("sat-a", "sat-c"),
            forward_labels=(1000, 1001),
            reverse_labels=(2000, 2001),
            ut_access_interface="ut0",
            ut_access_ip="10.90.42.2",
            ut_access_peer_ip="10.90.42.1",
            gateway_access_interface="gw0",
            gateway_access_ip="10.80.0.2",
            gateway_access_peer_ip="10.80.0.1",
            gateway_nat_interface="nat0",
            gateway_nat_source_ip="10.80.0.9",
            forward_segments=(
                MplsPathSegment(
                    "ac",
                    "sat-a",
                    "satellite",
                    "ac0",
                    "10.0.1.1",
                    "sat-c",
                    "satellite",
                    "ca0",
                    "10.0.1.2",
                    1000,
                ),
                MplsPathSegment(
                    "cg",
                    "sat-c",
                    "satellite",
                    "cg0",
                    "10.0.3.1",
                    "gw-a",
                    "gateway",
                    "gw0",
                    "10.0.3.2",
                    1001,
                ),
            ),
            reverse_segments=(
                MplsPathSegment(
                    "gc",
                    "gw-a",
                    "gateway",
                    "gw0",
                    "10.0.4.1",
                    "sat-c",
                    "satellite",
                    "cg0",
                    "10.0.4.2",
                    2000,
                ),
                MplsPathSegment(
                    "ca",
                    "sat-c",
                    "satellite",
                    "ca0",
                    "10.0.5.1",
                    "sat-a",
                    "satellite",
                    "ac0",
                    "10.0.5.2",
                    2001,
                ),
            ),
            validation_targets=({"node_id": "mini-a", "ip_address": "10.154.42.129"},),
            traffic_scope="default_gateway_class",
        )
        peer = replace(
            base,
            flow_id="ut-internet-egress:ut43",
            ut_id="ut43",
            ut_access_interface="ut1",
            ut_access_ip="10.90.43.2",
            ut_access_peer_ip="10.90.43.1",
            policy_table_id=1043,
            policy_rule_priority=1043,
            gateway_nat_source_ip="10.80.0.10",
        )
        self.assertEqual(
            _bpf_mpls_context_fec_ids(base, lambda value: 42),
            _bpf_mpls_context_fec_ids(peer, lambda value: 42),
        )

        fake_ifindices = {
            "netns:sat-a:ut0": 101,
            "netns:sat-a:ut1": 102,
        }

        def fake_ifindex(interface_ref: str) -> int:
            if interface_ref in fake_ifindices:
                return fake_ifindices[interface_ref]
            return abs(hash(interface_ref)) % 100000 + 1000

        def fake_mac(interface_ref: str) -> str:
            value = abs(hash(interface_ref)) % 0xFFFFFF
            return f"02:00:00:{(value >> 16) & 0xff:02x}:{(value >> 8) & 0xff:02x}:{value & 0xff:02x}"

        with patch(
            "satellite.bpf_mpls_live.interface_ifindex",
            side_effect=fake_ifindex,
        ), patch(
            "satellite.bpf_mpls_live.interface_mac",
            side_effect=fake_mac,
        ):
            compiled = _bpf_owned_compile_mpls_maps_for_contexts(
                contexts=[base, peer],
                generation_id=11,
            )

        self.assertFalse(compiled["map_conflicts"])
        self.assertFalse(compiled["intermediate_core_lookup_uses_dst_ip"])
        handoffs = [
            entry
            for entry in compiled["resolved_mpls_flow_entries"]
            if entry.get("map_type") == "egress_handoff_map"
            and entry.get("direction") == Z_TO_G
            and entry.get("handoff") == "zone_local_delivery"
        ]
        self.assertEqual(
            {(entry["ut_id"], entry["out_ifindex"]) for entry in handoffs},
            {("ut42", 101), ("ut43", 102)},
        )
        self.assertEqual(
            {(entry["ut_id"], entry["new_dst_ip"]) for entry in handoffs},
            {("ut42", "10.90.42.2"), ("ut43", "10.90.43.2")},
        )
        self.assertEqual(
            len({entry["label"] for entry in handoffs}),
            2,
        )
        core_entries = [
            entry
            for entry in compiled["resolved_mpls_flow_entries"]
            if entry.get("map_type") == "core_lfib_map"
            and entry.get("direction") == Z_TO_G
        ]
        self.assertEqual(
            {entry["fec_id"] for entry in core_entries},
            {handoffs[0]["fec_id"]},
        )
        self.assertTrue(all(entry["ut_id"] in {"ut42", "ut43"} for entry in core_entries))
        self.assertTrue(all(entry.get("uses_dst_ip") is False for entry in core_entries))
        for entry in compiled["core_lfib_map"]:
            self.assertNotIn("dst_ipv4", entry["key"])
            self.assertNotIn("target_ip", entry["key"])
            self.assertNotEqual(entry["value"].get("new_dst_ip"), "10.90.42.2")
            self.assertNotEqual(entry["value"].get("new_dst_ip"), "10.90.43.2")
        handoff_values = [
            entry["value"]
            for entry in compiled["egress_handoff_map"]
            if entry["metadata"].get("handoff") == "zone_local_delivery"
        ]
        self.assertEqual(
            {value.get("new_dst_ip") for value in handoff_values},
            {"10.90.42.2", "10.90.43.2"},
        )
        reverse_allocations = [
            entry
            for entry in compiled["label_allocation"]["entries"]
            if entry.get("direction") == Z_TO_G
        ]
        self.assertTrue(
            all(
                entry.get("allocation_scope") == "zone_local_delivery"
                for entry in reverse_allocations
            )
        )
        self.assertEqual(
            len({entry["allocated_label"] for entry in reverse_allocations}),
            len(reverse_allocations),
        )


if __name__ == "__main__":
    unittest.main()
