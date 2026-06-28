import unittest

from experiment_topology import (
    EARTH_RADIUS_KM,
    ExperimentGatewayProfile,
    ExperimentPrefixOverridePolicy,
    ExperimentScenario,
    ExperimentTerminalProfile,
    ExperimentTimeSlice,
    ExperimentZoneProfile,
    GroundLocation,
    ZoneAnchorQualityGenerator,
    _scenario_planner_hints,
)
from topology_pipeline import OrbitTopologySlice, VisibilitySlice, ZoneGenerationInput


class ExperimentTopologyHintTests(unittest.TestCase):
    def test_special_prefix_target_exchange_tracks_shifted_gateway_ix(self) -> None:
        scenario = ExperimentScenario(
            scenario_name="test",
            satellite_ids=("stl-a",),
            terminal_profiles=(
                ExperimentTerminalProfile(
                    ut_id="ut201",
                    slot_name="ut201-access-slot0",
                    zone_id="zone-a",
                    candidate_satellite_ids=("stl-a",),
                ),
            ),
            gateway_profiles=(
                ExperimentGatewayProfile(
                    gateway_id="gw201",
                    slot_name="gw201-gateway-slot0",
                    candidate_satellite_ids=("stl-a",),
                ),
                ExperimentGatewayProfile(
                    gateway_id="gw202",
                    slot_name="gw202-gateway-slot0",
                    candidate_satellite_ids=("stl-a",),
                ),
            ),
            ping_target_ip="10.154.0.129",
            target_host_node_id="as154-host-new",
            default_validation_target_node_ids=("as154-host-new", "as160-host1"),
            special_prefix_override_policies=(
                ExperimentPrefixOverridePolicy(
                    policy_name="asn160_via_selected_secondary_gateway",
                    destination_prefix="10.160.0.0/24",
                    preferred_gateway_id=None,
                    target_node_id="as160-host1",
                    selection_mode="target_affinity_shortest_path",
                    fallback_to_shortest_path=False,
                ),
            ),
            time_slices=(ExperimentTimeSlice("slice-1", 0.0, 10.0),),
            orbital_reference_time_seconds=0.0,
            min_elevation_deg=5.0,
            ground_locations={
                "ut201": GroundLocation(64.1, -21.9),
                "gw201": GroundLocation(64.1, -21.9),
                "gw202": GroundLocation(64.2, -21.8),
            },
            orbital_elements={},
            mini_internet_ix_offset=100,
            zone_profiles=(
                ExperimentZoneProfile(
                    zone_id="zone-a",
                    display_name="Zone A",
                    region="override-a",
                    latitude_deg=64.1,
                    longitude_deg=-21.9,
                    candidate_satellite_ids=("stl-a",),
                ),
            ),
        )
        topology = {
            "mini_internet": {
                "external_test_targets": [
                    {
                        "node_id": "as154-host-new",
                        "ip_address": "10.154.0.129",
                        "asn": 154,
                    },
                    {
                        "node_id": "as160-host1",
                        "ip_address": "10.160.0.72",
                        "asn": 160,
                    },
                ],
                "gateway_ix_peerings": [
                    {"gateway_id": "gw201", "ix_id": 200, "ip_address": "10.200.0.250"},
                    {"gateway_id": "gw202", "ix_id": 203, "ip_address": "10.203.0.250"},
                ],
            },
            "gateways": [
                {
                    "gateway_id": "gw201",
                    "gateway_region": "override-a",
                    "catalog_coordinates": {
                        "latitude_deg": 64.1,
                        "longitude_deg": -21.9,
                        "altitude_km": 0.0,
                    },
                },
                {
                    "gateway_id": "gw202",
                    "gateway_region": "override-b",
                    "catalog_coordinates": {
                        "latitude_deg": 64.2,
                        "longitude_deg": -21.8,
                        "altitude_km": 0.0,
                    },
                },
            ],
            "user_terminals": [
                {
                    "ut_id": "ut201",
                    "region": "override-a",
                }
            ],
        }

        hints = _scenario_planner_hints(scenario, topology)
        override = hints["special_prefix_overrides"][0]

        self.assertEqual(override["preferred_gateway_ids"], ["gw202"])
        self.assertEqual(override["target_exchange_id"], 203)
        self.assertEqual(override["raw_target_exchange_id"], 103)
        self.assertEqual(override["gateway_ix_id_offset"], 100)

    def test_special_prefix_explicit_gateway_preference_wins_over_target_ix(
        self,
    ) -> None:
        scenario = ExperimentScenario(
            scenario_name="test",
            satellite_ids=("stl-a",),
            terminal_profiles=(
                ExperimentTerminalProfile(
                    ut_id="ut201",
                    slot_name="ut201-access-slot0",
                    zone_id="zone-a",
                    candidate_satellite_ids=("stl-a",),
                ),
            ),
            gateway_profiles=(
                ExperimentGatewayProfile(
                    gateway_id="gw201",
                    slot_name="gw201-gateway-slot0",
                    candidate_satellite_ids=("stl-a",),
                ),
                ExperimentGatewayProfile(
                    gateway_id="gw202",
                    slot_name="gw202-gateway-slot0",
                    candidate_satellite_ids=("stl-a",),
                ),
            ),
            ping_target_ip="10.154.0.129",
            target_host_node_id="as154-host-new",
            default_validation_target_node_ids=("as154-host-new", "as160-host1"),
            special_prefix_override_policies=(
                ExperimentPrefixOverridePolicy(
                    policy_name="catalog_override",
                    destination_prefix="10.160.0.0/24",
                    preferred_gateway_id="gw201",
                    target_node_id="as160-host1",
                    preferred_gateway_ids=("gw201",),
                    selection_mode="target_affinity_shortest_path",
                    fallback_to_shortest_path=False,
                ),
            ),
            time_slices=(ExperimentTimeSlice("slice-1", 0.0, 10.0),),
            orbital_reference_time_seconds=0.0,
            min_elevation_deg=5.0,
            ground_locations={
                "ut201": GroundLocation(64.1, -21.9),
                "gw201": GroundLocation(64.1, -21.9),
                "gw202": GroundLocation(64.2, -21.8),
            },
            orbital_elements={},
            mini_internet_ix_offset=100,
            zone_profiles=(
                ExperimentZoneProfile(
                    zone_id="zone-a",
                    display_name="Zone A",
                    region="override-a",
                    latitude_deg=64.1,
                    longitude_deg=-21.9,
                    candidate_satellite_ids=("stl-a",),
                ),
            ),
        )
        topology = {
            "mini_internet": {
                "external_test_targets": [
                    {
                        "node_id": "as160-host1",
                        "ip_address": "10.160.0.72",
                        "asn": 160,
                    },
                ],
                "gateway_ix_peerings": [
                    {"gateway_id": "gw201", "ix_id": 200, "ip_address": "10.200.0.250"},
                    {"gateway_id": "gw202", "ix_id": 203, "ip_address": "10.203.0.250"},
                ],
            },
            "gateways": [
                {
                    "gateway_id": "gw201",
                    "gateway_region": "override-a",
                    "catalog_coordinates": {
                        "latitude_deg": 64.1,
                        "longitude_deg": -21.9,
                        "altitude_km": 0.0,
                    },
                },
                {
                    "gateway_id": "gw202",
                    "gateway_region": "override-b",
                    "catalog_coordinates": {
                        "latitude_deg": 64.2,
                        "longitude_deg": -21.8,
                        "altitude_km": 0.0,
                    },
                },
            ],
            "user_terminals": [
                {
                    "ut_id": "ut201",
                    "region": "override-a",
                }
            ],
        }

        hints = _scenario_planner_hints(scenario, topology)
        override = hints["special_prefix_overrides"][0]

        self.assertEqual(override["preferred_gateway_ids"], ["gw201"])
        self.assertEqual(override["target_exchange_id"], 203)


class ZoneAnchorQualityGeneratorTests(unittest.TestCase):
    def _orbit_slice(
        self,
        *,
        slice_id: str,
        sat_a_y_km: float = 0.0,
        sat_b_x_offset_km: float = 500.0,
        sat_b_y_km: float = 500.0,
    ) -> OrbitTopologySlice:
        zone_position = {
            "model": "ecef_cartesian_km",
            "x_km": EARTH_RADIUS_KM,
            "y_km": 0.0,
            "z_km": 0.0,
        }
        return OrbitTopologySlice(
            time_slice={"slice_id": slice_id, "state_model": "full_state"},
            nodes={
                "sat-a": {
                    "node_id": "sat-a",
                    "node_type": "satellite",
                    "position_state": {
                        "model": "ecef_cartesian_km",
                        "x_km": EARTH_RADIUS_KM + 500.0,
                        "y_km": sat_a_y_km,
                        "z_km": 0.0,
                    },
                },
                "sat-b": {
                    "node_id": "sat-b",
                    "node_type": "satellite",
                    "position_state": {
                        "model": "ecef_cartesian_km",
                        "x_km": EARTH_RADIUS_KM + sat_b_x_offset_km,
                        "y_km": sat_b_y_km,
                        "z_km": 0.0,
                    },
                },
            },
            candidate_links=[],
            zone_generation_input=ZoneGenerationInput(
                membership_mode="explicit_test_zone",
                zones={
                    "zone-a": {
                        "zone_id": "zone-a",
                        "membership_mode": "explicit_test_zone",
                        "ut_ids": ["ut-a", "ut-b"],
                        "return_prefixes": ["10.10.0.0/24"],
                        "candidate_serving_satellite_ids": ["sat-a", "sat-b"],
                        "min_elevation_deg": 0.0,
                        "position_state": zone_position,
                        "fixed_coordinates": {
                            "latitude_deg": 0.0,
                            "longitude_deg": 0.0,
                            "altitude_km": 0.0,
                        },
                    }
                },
                ut_zone_membership={"ut-a": "zone-a", "ut-b": "zone-a"},
            ),
            planner_hints={
                "zone_default_gateway_preferences": {
                    "zone-a": {"preferred_gateway_ids": ["gw1"]}
                }
            },
            provenance={"source": "test"},
        )

    def _visibility_slice(
        self, visible_satellite_ids: tuple[str, ...] = ("sat-b",)
    ) -> VisibilitySlice:
        candidate_visibility = {
            ut_id: [
                {"slot_type": "ut_access", "satellite_id": satellite_id}
                for satellite_id in visible_satellite_ids
            ]
            for ut_id in ("ut-a", "ut-b")
        }
        return VisibilitySlice(
            active_links=[
                {
                    "link_id": "gw1-sat-a",
                    "slot_type": "gw_access",
                    "endpoints": [
                        {"node_id": "gw1", "node_type": "gateway"},
                        {"node_id": "sat-a", "node_type": "satellite"},
                    ],
                },
                {
                    "link_id": "sat-a-sat-b",
                    "slot_type": "fixed_isl",
                    "endpoints": [
                        {"node_id": "sat-a", "node_type": "satellite"},
                        {"node_id": "sat-b", "node_type": "satellite"},
                    ],
                }
            ],
            visibility_model="test_visibility",
            candidate_visibility=candidate_visibility,
        )

    def test_service_zone_anchor_rejects_ut_attach_divergence(self) -> None:
        projection = ZoneAnchorQualityGenerator().generate(
            self._orbit_slice(slice_id="slice-1"),
            self._visibility_slice(),
        )

        zone = projection.zones["zone-a"]
        self.assertEqual(zone["active_serving_satellite_id"], "sat-a")
        self.assertEqual(
            zone["anchor_selection"]["algorithm"],
            "zone_anchor_quality_generator_v1",
        )
        self.assertFalse(zone["anchor_selection"]["ut_attach_required"])
        self.assertTrue(zone["zone_anchor_resolved"])
        self.assertFalse(zone["ut_attach_resolved"])
        self.assertFalse(zone["zone_service_resolved"])
        self.assertEqual(
            zone["anchor_selection"]["zone_resolution_action"],
            "mark_unresolved_no_ut_specific_split",
        )
        self.assertTrue(zone["anchor_selection"]["ut_sample_would_force_divergence"])
        self.assertEqual(
            zone["ut_visibility_audit"]["audit_role"],
            "independent_workload_probe_visibility",
        )
        self.assertEqual(
            zone["ut_visibility_audit"]["selected_anchor_visible_to_ut_count"],
            0,
        )

    def test_zone_anchor_hysteresis_can_keep_current_anchor(self) -> None:
        generator = ZoneAnchorQualityGenerator(hysteresis_score_delta=80.0)
        visibility = self._visibility_slice(visible_satellite_ids=("sat-a", "sat-b"))
        first = generator.generate(
            self._orbit_slice(slice_id="slice-1", sat_b_y_km=500.0),
            visibility,
        )
        second = generator.generate(
            self._orbit_slice(slice_id="slice-2", sat_a_y_km=50.0, sat_b_y_km=0.0),
            visibility,
        )

        self.assertEqual(first.zones["zone-a"]["active_serving_satellite_id"], "sat-a")
        self.assertEqual(second.zones["zone-a"]["active_serving_satellite_id"], "sat-a")
        self.assertEqual(
            second.zones["zone-a"]["anchor_selection"]["change_reason"],
            "hysteresis_keep_current_anchor",
        )


if __name__ == "__main__":
    unittest.main()
