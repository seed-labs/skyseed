#!/usr/bin/env python3
# encoding: utf-8

"""Topology production pipeline composed from replaceable core interfaces."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Sequence

from skyseed_v1.core.ir.contracts import (
    TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
    TopologySnapshot,
)
from skyseed_v1.core.interfaces.topology import (
    OrbitTopologyProducer,
    TopologySlicer,
    VisibilityModel,
    ZoneGenerator,
)
from skyseed_v1.core.ir.topology import (
    OrbitTopologySlice,
    VisibilitySlice,
    ZoneGenerationInput,
    ZoneProjection,
)


def _fingerprint(payload: Dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


class MockTimeSliceOrbitProducer(OrbitTopologyProducer):
    """Static multi-slice producer that preserves the future orbit boundary."""

    producer_name = "mock_time_slice_orbit_producer"

    def iter_orbit_slices(self) -> Sequence[OrbitTopologySlice]:
        return tuple(_demo_orbit_slices())


class StaticVisibilityModel(VisibilityModel):
    """Pass-through visibility model for explicit active-link demo slices."""

    model_name = "static_visibility_model"

    def evaluate(self, orbit_slice: OrbitTopologySlice) -> VisibilitySlice:
        active_links: List[Dict[str, Any]] = []
        for link in orbit_slice.candidate_links:
            if not bool(link.get("is_active", False)):
                continue
            active_links.append(
                {
                    key: value
                    for key, value in link.items()
                    if key != "is_active"
                }
            )
        return VisibilitySlice(
            active_links=sorted(active_links, key=lambda entry: str(entry.get("link_id"))),
            visibility_model=self.model_name,
        )


class ExplicitZoneGenerator(ZoneGenerator):
    """Explicit zone projection used only for the first control-plane demo."""

    generator_name = "explicit_zone_generator"

    def generate(
        self,
        orbit_slice: OrbitTopologySlice,
        visibility_slice: VisibilitySlice,
    ) -> ZoneProjection:
        zone_input = orbit_slice.zone_generation_input
        return ZoneProjection(
            zones={zone_id: dict(zone) for zone_id, zone in zone_input.zones.items()},
            ut_zone_membership=dict(zone_input.ut_zone_membership),
            zone_generator=self.generator_name,
        )


class StaticTopologySlicer(TopologySlicer):
    """Convert upstream slice artifacts into stable TopologySnapshot instances."""

    slicer_name = "static_topology_slicer"

    def build_snapshot(
        self,
        orbit_slice: OrbitTopologySlice,
        visibility_slice: VisibilitySlice,
        zone_projection: ZoneProjection,
    ) -> TopologySnapshot:
        snapshot_seed = {
            "slice_id": orbit_slice.time_slice.get("slice_id"),
            "active_links": visibility_slice.active_links,
            "zone_serving": {
                zone_id: zone.get("active_serving_satellite_id")
                for zone_id, zone in sorted(zone_projection.zones.items())
            },
            "ut_zone_membership": zone_projection.ut_zone_membership,
        }
        snapshot_id = f"topsnap-{_fingerprint(snapshot_seed)}"
        return TopologySnapshot(
            schema_version=TOPOLOGY_SNAPSHOT_SCHEMA_VERSION,
            snapshot_id=snapshot_id,
            time_slice=dict(orbit_slice.time_slice),
            provenance={
                "source": self.slicer_name,
                "orbit_producer": orbit_slice.provenance.get("orbit_producer"),
                "visibility_model": visibility_slice.visibility_model,
                "zone_generator": zone_projection.zone_generator,
                "zone_membership_mode": orbit_slice.zone_generation_input.membership_mode,
                "slice_profile": orbit_slice.provenance.get("slice_profile"),
            },
            nodes={node_id: dict(node) for node_id, node in orbit_slice.nodes.items()},
            active_links=[dict(link) for link in visibility_slice.active_links],
            zones={zone_id: dict(zone) for zone_id, zone in zone_projection.zones.items()},
            ut_zone_membership=dict(zone_projection.ut_zone_membership),
            planner_hints=dict(orbit_slice.planner_hints),
        )


class TopologySnapshotPipeline:
    """Compose orbit, visibility, zone, and topology-slicer boundaries."""

    def __init__(
        self,
        *,
        orbit_producer: OrbitTopologyProducer,
        visibility_model: VisibilityModel,
        zone_generator: ZoneGenerator,
        topology_slicer: TopologySlicer,
    ) -> None:
        self.orbit_producer = orbit_producer
        self.visibility_model = visibility_model
        self.zone_generator = zone_generator
        self.topology_slicer = topology_slicer

    def produce_snapshots(self) -> List[TopologySnapshot]:
        snapshots: List[TopologySnapshot] = []
        for orbit_slice in self.orbit_producer.iter_orbit_slices():
            visibility_slice = self.visibility_model.evaluate(orbit_slice)
            zone_projection = self.zone_generator.generate(orbit_slice, visibility_slice)
            snapshots.append(
                self.topology_slicer.build_snapshot(
                    orbit_slice,
                    visibility_slice,
                    zone_projection,
                )
            )
        return snapshots

    def metadata(self) -> Dict[str, str]:
        return {
            "orbit_producer": self.orbit_producer.producer_name,
            "visibility_model": self.visibility_model.model_name,
            "zone_generator": self.zone_generator.generator_name,
            "topology_slicer": self.topology_slicer.slicer_name,
        }


def build_demo_topology_snapshot_pipeline() -> TopologySnapshotPipeline:
    return TopologySnapshotPipeline(
        orbit_producer=MockTimeSliceOrbitProducer(),
        visibility_model=StaticVisibilityModel(),
        zone_generator=ExplicitZoneGenerator(),
        topology_slicer=StaticTopologySlicer(),
    )


def _demo_orbit_slices() -> Iterable[OrbitTopologySlice]:
    base_nodes = {
        "sat-a1": {
            "node_id": "sat-a1",
            "node_type": "satellite",
            "orbit_id": "orbit-a",
            "plane_index": 1,
            "orbital_slot": 1,
        },
        "sat-b1": {
            "node_id": "sat-b1",
            "node_type": "satellite",
            "orbit_id": "orbit-b",
            "plane_index": 2,
            "orbital_slot": 1,
        },
        "gw-east": {
            "node_id": "gw-east",
            "node_type": "gateway",
            "gateway_region": "east",
        },
        "ut-alpha": {
            "node_id": "ut-alpha",
            "node_type": "user_terminal",
            "region": "east",
            "fixed_coordinates": {
                "latitude_deg": 42.36,
                "longitude_deg": -71.06,
            },
        },
    }
    planner_hints = {
        "gateway_ids": ["gw-east"],
        "satellite_ids": ["sat-a1", "sat-b1"],
        "planner_examples": [
            "default_gz_mpls",
            "future_ip_routing",
            "future_custom_research_planner",
        ],
    }
    common_zone = {
        "zone-east": {
            "zone_id": "zone-east",
            "membership_mode": "explicit_demo_zone",
            "ut_ids": ["ut-alpha"],
            "candidate_serving_satellite_ids": ["sat-a1", "sat-b1"],
        }
    }

    slice_a_nodes = {
        **base_nodes,
        "ut-alpha": {
            **base_nodes["ut-alpha"],
            "attachment": {
                "state": "attached",
                "slot_name": "ut-alpha-access-slice-a",
                "active_serving_satellite_id": "sat-a1",
            },
        },
    }
    yield OrbitTopologySlice(
        time_slice={
            "slice_id": "demo-slice-0001",
            "slice_index": 1,
            "state_model": "full_state",
            "time_semantics": "demo_relative_time",
            "relative_time_seconds": 0,
            "duration_seconds": 60,
        },
        nodes=slice_a_nodes,
        candidate_links=[
            {
                "link_id": "gw-east-access-sat-a1",
                "slot_type": "gw_access",
                "subnet": "10.210.1.0/30",
                "occupied": True,
                "binding": {
                    "gateway_id": "gw-east",
                    "satellite_id": "sat-a1",
                },
                "endpoints": [
                    {"node_id": "gw-east", "interface_name": "ga0"},
                    {"node_id": "sat-a1", "interface_name": "ga0"},
                ],
                "is_active": True,
            },
            {
                "link_id": "ut-alpha-access-sat-a1",
                "slot_type": "ut_access",
                "subnet": "10.210.2.0/30",
                "occupied": True,
                "binding": {
                    "ut_id": "ut-alpha",
                    "satellite_id": "sat-a1",
                },
                "endpoints": [
                    {"node_id": "ut-alpha", "interface_name": "ua0"},
                    {"node_id": "sat-a1", "interface_name": "ua0"},
                ],
                "is_active": True,
            },
            {
                "link_id": "isl-sat-a1-sat-b1",
                "slot_type": "fixed_isl",
                "subnet": "10.210.3.0/30",
                "occupied": True,
                "binding": {
                    "left_node_id": "sat-a1",
                    "right_node_id": "sat-b1",
                },
                "endpoints": [
                    {"node_id": "sat-a1", "interface_name": "xp0"},
                    {"node_id": "sat-b1", "interface_name": "xp0"},
                ],
                "is_active": True,
            },
        ],
        zone_generation_input=ZoneGenerationInput(
            membership_mode="explicit_demo_zone",
            zones={
                "zone-east": {
                    **common_zone["zone-east"],
                    "active_serving_satellite_id": "sat-a1",
                }
            },
            ut_zone_membership={"ut-alpha": "zone-east"},
        ),
        planner_hints=planner_hints,
        provenance={
            "orbit_producer": MockTimeSliceOrbitProducer.producer_name,
            "slice_profile": "baseline_attachment_to_sat_a1",
        },
    )

    slice_b_nodes = {
        **base_nodes,
        "ut-alpha": {
            **base_nodes["ut-alpha"],
            "attachment": {
                "state": "attached",
                "slot_name": "ut-alpha-access-slice-b",
                "active_serving_satellite_id": "sat-b1",
            },
        },
    }
    yield OrbitTopologySlice(
        time_slice={
            "slice_id": "demo-slice-0002",
            "slice_index": 2,
            "state_model": "full_state",
            "time_semantics": "demo_relative_time",
            "relative_time_seconds": 60,
            "duration_seconds": 60,
        },
        nodes=slice_b_nodes,
        candidate_links=[
            {
                "link_id": "gw-east-access-sat-b1",
                "slot_type": "gw_access",
                "subnet": "10.210.11.0/30",
                "occupied": True,
                "binding": {
                    "gateway_id": "gw-east",
                    "satellite_id": "sat-b1",
                },
                "endpoints": [
                    {"node_id": "gw-east", "interface_name": "ga0"},
                    {"node_id": "sat-b1", "interface_name": "ga0"},
                ],
                "is_active": True,
            },
            {
                "link_id": "ut-alpha-access-sat-b1",
                "slot_type": "ut_access",
                "subnet": "10.210.12.0/30",
                "occupied": True,
                "binding": {
                    "ut_id": "ut-alpha",
                    "satellite_id": "sat-b1",
                },
                "endpoints": [
                    {"node_id": "ut-alpha", "interface_name": "ua0"},
                    {"node_id": "sat-b1", "interface_name": "ua0"},
                ],
                "is_active": True,
            },
            {
                "link_id": "isl-sat-a1-sat-b1",
                "slot_type": "fixed_isl",
                "subnet": "10.210.3.0/30",
                "occupied": True,
                "binding": {
                    "left_node_id": "sat-a1",
                    "right_node_id": "sat-b1",
                },
                "endpoints": [
                    {"node_id": "sat-a1", "interface_name": "xp0"},
                    {"node_id": "sat-b1", "interface_name": "xp0"},
                ],
                "is_active": True,
            },
        ],
        zone_generation_input=ZoneGenerationInput(
            membership_mode="explicit_demo_zone",
            zones={
                "zone-east": {
                    **common_zone["zone-east"],
                    "active_serving_satellite_id": "sat-b1",
                }
            },
            ut_zone_membership={"ut-alpha": "zone-east"},
        ),
        planner_hints=planner_hints,
        provenance={
            "orbit_producer": MockTimeSliceOrbitProducer.producer_name,
            "slice_profile": "handover_attachment_to_sat_b1",
        },
    )
