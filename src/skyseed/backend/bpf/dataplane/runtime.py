"""Runner-owned default BPF runtime adapter; consumes prepared execution materials."""

from __future__ import annotations

from .materials import (
    _MaterialCursor, _validate_foundation, _validate_runtime_capacity_demand,
)

from collections import ChainMap
from dataclasses import dataclass, replace
from ipaddress import IPv4Network
import json
from threading import RLock
from types import MappingProxyType
from time import monotonic_ns
from typing import Any, Mapping, Protocol
from skyseed.shared import NodeId
from skyseed.simulator import OperationalConnectionId
from .materials import _RETIREMENT_GROUP_BASE
from .object_contract import _DEFAULT_MAPS
from skyseed.backend.execution_materials.mechanics import (
    _CapacityAuditError,
    _node_key,
    _connection_key,
    _cached_interface_names,
)


_NO_ACTIVE_GENERATION = 0xFFFFFFFF


def _optional_duration_seconds(
    started_monotonic_ns: object,
    ended_monotonic_ns: object,
) -> float | None:
    """Return truthful timing evidence without making it semantic authority."""

    if (
        isinstance(started_monotonic_ns, bool)
        or not isinstance(started_monotonic_ns, int)
        or isinstance(ended_monotonic_ns, bool)
        or not isinstance(ended_monotonic_ns, int)
        or ended_monotonic_ns < started_monotonic_ns
    ):
        return None
    return (ended_monotonic_ns - started_monotonic_ns) / 1_000_000_000


class _IncompleteActivationError(RuntimeError):
    """Carry truthful evidence for a Execution that did not reach formal ACTIVE."""

    def __init__(self, message: str, *, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


class _PrivilegedCommandExecutor(Protocol):
    def execute(
        self, action: str, request: Mapping[str, Any] | str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _PreparedBoundary:
    """A finite immutable Execution delta; it owns no full network state."""

    nominal_time: object
    generation: int
    expected_active_generation: int
    generation_changed: bool
    request: str
    created: tuple[OperationalConnectionId, ...]
    removed: tuple[OperationalConnectionId, ...]
    withdrawn_identities: tuple[tuple[str, str], ...]
    telemetry_identities: tuple[Mapping[str, Any], ...]
    zone_changes: tuple = ()


def _batch_entry_limits(demand, node_by_key, *, factor, continued):
    fields = ("cumulative_active_link_entries", "cumulative_route_entries", "physical_interface_high_water")
    peaks = {}
    if continued:
        # A fixed node can acquire its peers' forwarding work in a later batch.
        # Reserve from first-batch peers of the same typed entity kind, never
        # from the future session. Keep the exact sealed demand unchanged.
        for node in demand["per_node"]:
            group = peaks.setdefault(node_by_key[node["key"]].entity_kind, dict.fromkeys(fields, 1))
            for field in fields:
                group[field] = max(group[field], node[field])
    return {
        node["key"]: {
            field: factor * max(1, (peaks[node_by_key[node["key"]].entity_kind] if continued else node)[field])
            for field in fields
        }
        for node in demand["per_node"]
    }


class _TcBpfRuntimeRealization:
    """Bind prepared deliveries and own BPF runtime resources."""

    def __init__(
        self,
        *,
        executor: _PrivilegedCommandExecutor,
        pin_root: str,
        bindings: Mapping[NodeId, object],
        telemetry_enabled: bool = False,
        worker_count: int = 1,
        continuation_enabled: bool = False,
        batch_capacity_factor: int = 2,
    ) -> None:
        self._executor = executor
        self._pin_root = pin_root
        self._bindings = dict(bindings)
        self._telemetry_enabled = telemetry_enabled
        self._worker_count = worker_count
        self._continuation_enabled = continuation_enabled
        self._batch_capacity_factor = batch_capacity_factor if continuation_enabled else 1
        self._previous_build = None
        self._material_cursor = None
        self._node_versions = {}
        self._batch_prewrite_work = {}
        self._generation_reservations = {}
        self._table_reservations = {}
        self._resident_table_totals = {}
        self._resident_active_totals = {}
        self._resident_route_totals = {}
        self._telemetry_kernel: dict[str, Any] = {}
        self._lock = RLock()
        self._nodes: tuple[dict[str, Any], ...] = ()
        self._node_addresses: dict[NodeId, str] = {}
        self._node_address_range: IPv4Network | None = None
        self._owned_node_addresses: set[NodeId] = set()
        self._all_zone_addresses: dict[NodeId, set[str]] = {}
        self._prepared_resources: set[OperationalConnectionId] = set()
        self._all_prepared_resources: set[OperationalConnectionId] = set()
        self._interface_indexes: dict[tuple[str, str], int] = {}
        self._resident_generations: set[int] = set()
        self._max_resident_generations = 0
        self._map_capacities: dict[str, tuple[int, int]] = {}
        self._capacity_plan: dict[str, Any] = {}
        self._capacity_demand: dict[str, Any] = {}
        self._capacity_demand_by_key: dict[str, dict[str, Any]] = {}
        self._observed_boundary_count = 0
        self._observed_generation_count = 0
        self._observed_active_entry_totals: dict[str, int] = {}
        self._observed_route_entry_totals: dict[str, int] = {}
        self._physical_resource_high_water = 0
        self._bpf_batch_update_supported = False
        self._active: _PreparedBoundary | None = None
        self._initialized = False
        self._cleanup_required = False
        self._initialization_outcome = "no-mutation"
        self._plans_prepared = False
        self._logical_ports: dict[OperationalConnectionId, tuple[int, int]] = {}
        self._port_by_interface: dict[tuple[str, str], int] = {}
        self._preparation_timings: dict[str, Any] = {}
        self._node_ids = tuple(sorted(self._bindings, key=_node_key))
        self._node_by_key = {node.value: node for node in self._node_ids}
        self._node_slots = {node.value: slot for slot, node in enumerate(self._node_ids)}
        self._connection_names = {}
        self._live_interface_names = {}
        self._physical_interface_counts = {node.value: 0 for node in self._node_ids}
        self._telemetry_interfaces = {}
        self._telemetry_node_routes = {}

    def initialize(
        self,
        physical_world_projection: object,
        *,
        zone_prefixes: tuple[str, ...],
        runtime_capacity_demand: Mapping[str, Any],
        dataplane: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._initialized or self._cleanup_required:
            raise RuntimeError("tc-BPF runtime realization is already initialized")
        parent_before_capacity_started_ns = monotonic_ns()
        if set(getattr(physical_world_projection, "node_ids")) != self._bindings.keys():
            raise ValueError("runtime bindings do not match compiled PhysicalWorld")
        physical_world_nodes = self._node_ids
        self._dataplane = _validate_foundation(dataplane, tuple(n.value for n in physical_world_nodes))
        foundation_nodes = {node["key"]: node for node in dataplane["nodes"]}
        self._material_cursor = _MaterialCursor(physical_world_nodes, self._dataplane)
        self._node_addresses = {node: foundation_nodes[node.value]["node_address"] for node in physical_world_nodes}
        self._node_address_range = IPv4Network(dataplane["node_address_range"])
        components = {node: foundation_nodes[node.value]["pin_component"] for node in physical_world_nodes}
        self._nodes = tuple(
            {
                "key": node_id.value,
                "node_slot": node_slot,
                "pid": int(getattr(self._bindings[node_id], "pid")),
                "process_start_time_ticks": int(
                    getattr(self._bindings[node_id], "process_start_time_ticks")
                ),
                "netns_inode": int(getattr(self._bindings[node_id], "netns_inode")),
                "pin_component": components[node_id],
                "node_address": self._node_addresses[node_id],
                "program": foundation_nodes[node_id.value]["program"],
            }
            for node_slot, node_id in enumerate(physical_world_nodes)
        )
        self._all_zone_addresses = {
            node_id: set() for node_id in physical_world_nodes
        }
        capacity_plan_started_ns = monotonic_ns()
        sealed_demand = _validate_runtime_capacity_demand(
            runtime_capacity_demand,
            expected_node_keys=tuple(node["key"] for node in self._nodes),
            expected_boundary_count=int(runtime_capacity_demand.get(
                "boundary_count", -1
            )),
        )
        entry_limits = _batch_entry_limits(
            sealed_demand, self._node_by_key,
            factor=self._batch_capacity_factor, continued=self._continuation_enabled,
        )
        self._batch_admission_limits = entry_limits
        per_node = []
        for demand in sealed_demand["per_node"]:
            required_active = int(demand["cumulative_active_link_entries"])
            required_routes = int(demand["cumulative_route_entries"])
            limits = entry_limits[demand["key"]]
            per_node.append(
                {
                    "key": demand["key"],
                    "capacity_basis": "first-batch-entity-kind-peak" if self._continuation_enabled else "sealed-artifact-exact-demand",
                    "batch_admission_limits": limits,
                    "per_generation_active_link_upper_bound": demand[
                        "maximum_generation_active_link_entries"
                    ],
                    "per_generation_route_upper_bound": demand[
                        "maximum_generation_route_entries"
                    ],
                    "required_active_link_entries": required_active,
                    "configured_active_link_entries": limits["cumulative_active_link_entries"] * (2 if self._continuation_enabled else 1),
                    "required_route_entries": required_routes,
                    "configured_route_entries": limits["cumulative_route_entries"] * (2 if self._continuation_enabled else 1),
                    "physical_interface_high_water": demand[
                        "physical_interface_high_water"
                    ],
                    "configured_binding_entries": limits["physical_interface_high_water"] + int(foundation_nodes[demand["key"]]["program"] is not None),
                    "terminal_active_link_entries": demand[
                        "terminal_active_link_entries"
                    ],
                    "terminal_route_entries": demand[
                        "terminal_route_entries"
                    ],
                }
            )
        # _initialize pins the entry program and managed profile maps for each
        # selected instance. Object-owned maps/globals are loaded, not pinned.
        program_pins = [len(p['declaration']['programs'])
                        + (len(_DEFAULT_MAPS) if p['declaration']['managed_tables'] else 0)
                        for p in self._dataplane['programs']]
        capacity_plan = {
            "mode": "fixed-resident-batch-reservation" if self._continuation_enabled else "sealed-artifact-exact-demand",
            "batch_capacity_factor": self._batch_capacity_factor,
            "resident_boundary_capacity": sealed_demand["boundary_count"] * self._batch_capacity_factor * (2 if self._continuation_enabled else 1),
            "known_boundary_count": sealed_demand["boundary_count"],
            "known_generation_count": sealed_demand["generation_count"],
            "resident_generation_capacity": (sealed_demand["generation_count"] * self._batch_capacity_factor * (2 if self._continuation_enabled else 1)),
            "per_node": per_node,
            "minimum_active_link_headroom": min(
                value["configured_active_link_entries"]
                - value["required_active_link_entries"]
                for value in per_node
            ),
            "minimum_route_headroom": min(
                value["configured_route_entries"]
                - value["required_route_entries"]
                for value in per_node
            ),
            "known_physical_resource_high_water": sealed_demand[
                "physical_connection_high_water"
            ],
            "known_interface_binding_high_water": sealed_demand[
                "interface_binding_high_water"
            ],
            "interface_identity_derivation": sealed_demand[
                "interface_identity_derivation"
            ],
            "sealed_collision_audit": sealed_demand["collision_audit"],
            "terminal": sealed_demand["terminal"],
            "node_address_requirement": len(self._nodes),
            "node_address_capacity": (
                0
                if self._node_address_range is None
                else self._node_address_range.num_addresses - 2
            ),
            "node_address_headroom": (
                0
                if self._node_address_range is None
                else self._node_address_range.num_addresses - 2 - len(self._nodes)
            ),
            "selected_node_address_range": (
                None
                if self._node_address_range is None
                else str(self._node_address_range)
            ),
            "node_address_selection": "smallest-prefix-for-topology-requirement",
            "per_node_interface_high_water": [
                {
                    "key": value["key"],
                    "interfaces": value["physical_interface_high_water"],
                }
                for value in per_node
            ],
            "pinned_object_count": (
                1 + int(self._continuation_enabled) + 2 * int(self._telemetry_enabled)
                + sum(2 + (0 if n['program'] is None else program_pins[n['program']]) for n in self._nodes)
            ),
        }
        self._max_resident_generations = int(
            capacity_plan["resident_generation_capacity"]
        )
        capacity_by_key = {
            value["key"]: value for value in capacity_plan["per_node"]
        }
        self._map_capacities = {
            key: (
                int(value["configured_active_link_entries"]),
                int(value["configured_route_entries"]),
            )
            for key, value in capacity_by_key.items()
        }
        self._capacity_plan = capacity_plan
        self._capacity_demand = sealed_demand
        self._batch_capacity_basis = sealed_demand
        self._capacity_demand_by_key = {
            node["key"]: node for node in sealed_demand["per_node"]
        }
        self._observed_active_entry_totals = {
            node["key"]: 0 for node in sealed_demand["per_node"]
        }
        self._observed_route_entry_totals = {
            node["key"]: 0 for node in sealed_demand["per_node"]
        }
        self._resident_active_totals = dict(self._observed_active_entry_totals)
        self._resident_route_totals = dict(self._observed_route_entry_totals)
        self._table_capacities_by_key = self._table_limits(sealed_demand)
        self._nodes = tuple(
            {
                **node,
                "active_links_capacity": self._map_capacities[node["key"]][0],
                "routes_capacity": self._map_capacities[node["key"]][1],
                "bindings_capacity": capacity_by_key[node["key"]]["configured_binding_entries"],
                "table_capacities": list(self._table_capacities_by_key[node["key"]].values()),
            }
            for node in self._nodes
        )
        capacity_plan_ended_ns = monotonic_ns()
        privileged_foundation_started_ns = monotonic_ns()
        # Dispatch can mutate the remote foundation even if its response is lost.
        self._cleanup_required = True
        self._initialization_outcome = "uncertain"
        try:
            result = self._executor.execute(
                "initialize",
                {
                    "pin_root": self._pin_root,
                    "dataplane": self._dataplane,
                    "nodes": self._nodes,
                    "retirement_group_end": _RETIREMENT_GROUP_BASE + (
                        2 * len(self._nodes) ** 2 + 1 if self._continuation_enabled
                        else sealed_demand["boundary_count"] + 1),
                    "resident_generation_capacity": self._max_resident_generations,
                    "continuation_enabled": self._continuation_enabled,
                    "inactive_generation": _NO_ACTIVE_GENERATION,
                    "telemetry_enabled": self._telemetry_enabled,
                    "node_address_range": str(self._node_address_range),
                    "zone_prefixes": tuple(zone_prefixes),
                    "worker_count": self._worker_count,
                },
            )
        except Exception as error:
            evidence = getattr(error, "remote_evidence", getattr(error, "evidence", None))
            if isinstance(evidence, Mapping):
                outcome = evidence.get("initialization_outcome")
                if outcome in {"no-mutation", "cleaned", "partial"}:
                    self._initialization_outcome = outcome
                    self._cleanup_required = outcome == "partial"
                installed = set(evidence.get("installed_node_addresses", ()))
                self._owned_node_addresses = {
                    node for node in physical_world_nodes if node.value in installed
                }
            raise _CapacityAuditError(
                "Runner node address collision audit or initialization failed during "
                f"preparation: {error}"
            ) from error
        finally:
            privileged_foundation_ended_ns = monotonic_ns()
        parent_after_privileged_started_ns = monotonic_ns()
        collision_audit = result.get("collision_audit")
        installed_keys = result.get("installed_node_addresses")
        telemetry_kernel = result.get("traffic_telemetry")
        batch_update_supported = result.get("bpf_batch_update_supported")
        expected_keys = {node["key"] for node in self._nodes}
        try:
            self._validate_collision_audit(collision_audit)
            if not isinstance(installed_keys, list) or set(installed_keys) != expected_keys:
                raise RuntimeError(
                    "privileged runtime command node address ownership evidence is incomplete"
                )
            if not isinstance(batch_update_supported, bool):
                raise RuntimeError(
                    "privileged runtime command omitted BPF batch capability evidence"
                )
            if self._telemetry_enabled and (
                not isinstance(telemetry_kernel, dict)
                or telemetry_kernel.get("enabled") is not True
                or telemetry_kernel.get("shared_ring_buffer") is not True
                or not isinstance(telemetry_kernel.get("event_schema"), dict)
            ):
                raise RuntimeError(
                    "privileged runtime command omitted traffic telemetry foundation"
                )
        except BaseException:
            self.cleanup()
            raise
        self._owned_node_addresses = set(physical_world_nodes)
        self._bpf_batch_update_supported = batch_update_supported
        if self._telemetry_enabled:
            assert isinstance(telemetry_kernel, dict)
            self._telemetry_kernel = dict(telemetry_kernel)
        capacity_plan = {
            **capacity_plan,
            "collision_audit": collision_audit,
        }
        self._capacity_plan = capacity_plan
        self._initialized = True
        self._initialization_outcome = "initialized"
        parent_after_privileged_ended_ns = monotonic_ns()
        parent_before_capacity_seconds = _optional_duration_seconds(
            parent_before_capacity_started_ns,
            capacity_plan_started_ns,
        )
        parent_after_privileged_seconds = _optional_duration_seconds(
            parent_after_privileged_started_ns,
            parent_after_privileged_ended_ns,
        )
        capacity_to_privileged_seconds = _optional_duration_seconds(
            capacity_plan_ended_ns,
            privileged_foundation_started_ns,
        )
        privileged_to_parent_after_seconds = _optional_duration_seconds(
            privileged_foundation_ended_ns,
            parent_after_privileged_started_ns,
        )
        remaining_parent_parts = (
            parent_before_capacity_seconds,
            capacity_to_privileged_seconds,
            privileged_to_parent_after_seconds,
            parent_after_privileged_seconds,
        )
        remaining_parent_seconds = (
            None
            if any(value is None for value in remaining_parent_parts)
            else sum(value for value in remaining_parent_parts if value is not None)
        )
        child_phases = result.get("preparation_phases")
        preparation_phases = (
            dict(child_phases) if isinstance(child_phases, Mapping) else {}
        )
        preparation_phases.update(
            {
                "sealed_capacity_demand_validation_seconds": (
                    _optional_duration_seconds(
                        capacity_plan_started_ns,
                        capacity_plan_ended_ns,
                    )
                ),
                "full_range_capacity_plan_materialization_seconds": None,
                "privileged_foundation_initialization_seconds": (
                    _optional_duration_seconds(
                        privileged_foundation_started_ns,
                        privileged_foundation_ended_ns,
                    )
                ),
                "remaining_parent_bookkeeping_validation_seconds": (
                    remaining_parent_seconds
                ),
            }
        )
        return {
            **result,
            "preparation_phases": preparation_phases,
            "capacity_audit": capacity_plan,
        }

    def prepare_all(
        self, boundaries, *,
        logical_ports: Mapping[OperationalConnectionId, tuple[int, int]],
        runtime_capacity_demand: Mapping[str, Any] | None = None,
        retirements, work_checkpoint=None, progress=None,
    ) -> tuple[_PreparedBoundary, ...]:
        """Validate and bind one prepared delivery, then prewrite its encoded data."""
        if not self._initialized or (self._plans_prepared and not self._continuation_enabled):
            raise RuntimeError("finite runtime preparation may run only once")
        if runtime_capacity_demand is not None:
            self.admit_batch(runtime_capacity_demand)
        checkpoint = work_checkpoint or (lambda: None)
        milestone = progress or (lambda *args, **kwargs: None)
        construction_started = monotonic_ns()
        construction_ns = prewrite_ns = prewrite_count = 0
        prewrite_helper_seconds = 0.0
        prewrite_helper_count = 0
        self._plans_prepared = True
        self._batch_prewrite_work = {"node_versions_written": 0, "node_versions_reused": 0,
                                     "active_entries_written": 0, "route_entries_written": 0,
                                     "prewrite_work_units": 0}
        if any(logical_ports.get(connection) != ports for connection, ports in self._logical_ports.items()):
            raise ValueError("continued batch changed a logical port identity")
        self._logical_ports = dict(logical_ports)
        for connection, ports in logical_ports.items():
            for endpoint, name, port in zip(
                (connection.endpoint_a, connection.endpoint_b),
                _cached_interface_names(connection, self._connection_names), ports,
            ):
                self._port_by_interface[(endpoint.value, name)] = port
            if self._telemetry_enabled and connection not in self._telemetry_interfaces:
                self._telemetry_interfaces[connection] = tuple(MappingProxyType({
                    "node_slot": self._node_slots[endpoint.value], "node_id": endpoint.value,
                    "logical_port_id": port, "interface": name,
                    "connection_id": _connection_key(connection), "peer_node_id": peer.value,
                    "validity": "exact-generation",
                }) for endpoint, peer, name, port in zip(
                    (connection.endpoint_a, connection.endpoint_b), (connection.endpoint_b, connection.endpoint_a),
                    _cached_interface_names(connection, self._connection_names), ports))
        self._material_cursor.port_by_interface = self._port_by_interface
        previous = self._previous_build
        plans = []
        requests = []
        seen_connections = set()
        decode_ns = 0
        iterator = iter(boundaries)
        milestone('decoding-start')
        while True:
            checkpoint()
            decode_started = monotonic_ns()
            construction_ns += decode_started - construction_started
            try:
                boundary = next(iterator)
            except StopIteration:
                construction_started = monotonic_ns()
                break
            construction_started = monotonic_ns()
            decode_ns += construction_started - decode_started
            milestone('boundary-decoded', boundary_timestamp=boundary.nominal_time.isoformat(),
                      boundary_count=len(plans)+1, decode_seconds=decode_ns/1e9)
            draft = self._material_cursor.apply(boundary)
            for node in draft.request["operation_nodes"]:
                self._all_zone_addresses[self._node_by_key[node["key"]]].update(node["zone_address_add"])
            self._audit_boundary_capacity(draft)
            milestone('boundary-constructed', boundary_timestamp=boundary.nominal_time.isoformat(),
                      generation=draft.generation)
            seen_connections.update(draft.new_resources)
            if any(connection not in logical_ports for connection in draft.new_resources):
                raise ValueError("Runner artifact omitted a logical port identity")
            if draft.generation_changed:
                prewrite_started = monotonic_ns()
                construction_ns += prewrite_started - construction_started
                milestone('prewrite-start', boundary_timestamp=boundary.nominal_time.isoformat(),
                          generation=draft.generation)
                result = self._prewrite_nodes(draft, checkpoint)
                construction_started = monotonic_ns()
                prewrite_ns += construction_started - prewrite_started
                prewrite_count += 1
                prewrite_helper_seconds += result["helper_seconds"]
                prewrite_helper_count += int(result["helper_complete"])
                milestone('prewrite-complete', boundary_timestamp=boundary.nominal_time.isoformat(),
                          generation=draft.generation, prewrite_seconds=prewrite_ns / 1e9)
                self._resident_generations.add(draft.generation)
                reservation = tuple((len(node.active_interfaces), len(node.routes)) for node in draft.generation_nodes)
                self._generation_reservations[draft.generation] = reservation
                table_reservation = {(n.key, i): tuple(counts) for n in draft.generation_nodes for i, counts in n.table_counts.items()}
                self._table_reservations[draft.generation] = table_reservation
                for identity, counts in table_reservation.items():
                    resident = self._resident_table_totals.setdefault(identity, [0, 0])
                    for i in range(2): resident[i] += counts[i]
                for node, (active, routes) in zip(self._nodes, reservation):
                    self._resident_active_totals[node["key"]] += active
                    self._resident_route_totals[node["key"]] += routes
            self._commit_observed_capacity(draft)
            request = draft.request
            telemetry = []
            if self._telemetry_enabled and draft.generation_changed:
                for node in draft.generation_nodes:
                    cached = self._telemetry_node_routes.get(node.key)
                    if cached is None or cached[0] is not node:
                        routes = {}
                        for destination, interface in node.routes:
                            routes.setdefault(interface, []).append(destination)
                        self._telemetry_node_routes[node.key] = node, {key: tuple(value) for key, value in routes.items()}
                association = {"generation": draft.generation, "boundary_timestamp": boundary.nominal_time.isoformat()}
                for connection in draft.resources:
                    for identity in self._telemetry_interfaces[connection]:
                        routes = self._telemetry_node_routes[identity['node_id']][1].get(identity['interface'], ())
                        telemetry.append(MappingProxyType(ChainMap(
                            MappingProxyType({'route_destinations': routes}),
                            MappingProxyType(association), identity)))
            requests.append(request)
            plans.append(_PreparedBoundary(
                boundary.nominal_time, draft.generation, draft.expected_active_generation,
                draft.generation_changed, "", draft.new_resources,
                draft.removed_resources,
                tuple((endpoint.value, name) for connection in draft.removed_resources
                      for endpoint, name in zip((connection.endpoint_a, connection.endpoint_b), _cached_interface_names(connection, self._connection_names))),
                tuple(telemetry),
                tuple((op, node['key'], address) for node in request.get('operation_nodes', ())
                    for field, op in (('zone_address_delete', 'address_delete'), ('zone_address_add', 'address_add'))
                    for address in node.get(field, ())),
            ))
            previous = draft
            milestone('boundary-prepared', boundary_timestamp=boundary.nominal_time.isoformat(),
                      boundary_count=len(plans), generation=draft.generation,
                      construction_seconds=(construction_ns + monotonic_ns() - construction_started) / 1e9,
                      prewrite_seconds=prewrite_ns / 1e9)
        milestone('decoding-complete', boundary_count=len(plans), decode_seconds=decode_ns/1e9)
        if not self._continuation_enabled and seen_connections != logical_ports.keys():
            raise ValueError("Runner artifact logical port inventory differs from finite resources")
        if len(plans) != self._capacity_demand["boundary_count"]:
            raise _CapacityAuditError("Runner finite plan count differs from sealed demand")
        for node in self._capacity_demand["per_node"]:
            key = node["key"]
            if (self._observed_active_entry_totals[key] != node["cumulative_active_link_entries"]
                or self._observed_route_entry_totals[key] != node["cumulative_route_entries"]):
                raise _CapacityAuditError("finite semantic entry totals differ from sealed demand")
        for request in requests:
            for connection in request["connections"]:
                reference = connection.pop("retirement_binding")
                key, group = retirements["bindings"][reference]
                connection["retirement_node"] = key
                connection["retirement_group"] = group
            request["retirement_batches"] = retirements["batches"].get(request["boundary_timestamp"], [])
        encoding_started = monotonic_ns()
        plans = [replace(plan, request=json.dumps(
            {"action": "activate_boundary", "request": request}, separators=(",", ":")) + "\n")
            for plan, request in zip(plans, requests)]
        encoding_ns = monotonic_ns() - encoding_started
        construction_ns += monotonic_ns() - construction_started
        self._preparation_timings = {
            **self._batch_prewrite_work,
            "finite_plan_construction_seconds": construction_ns / 1e9,
            "boundary_decode_seconds": decode_ns / 1e9,
            "activation_wire_encoding_seconds": encoding_ns / 1e9,
            "semantic_prewrite_roundtrip_seconds": prewrite_ns / 1e9,
            "semantic_prewrite_helper_seconds": prewrite_helper_seconds if prewrite_helper_count == prewrite_count else None,
            "semantic_prewrite_count": prewrite_count,
            "boundary_count": len(plans),
        }
        self._previous_build = previous
        milestone('delivery-prepared', **self._preparation_timings)
        return tuple(plans)

    def admit_boundary_count(self, boundary_count: int) -> None:
        if boundary_count > self._batch_capacity_basis["boundary_count"] * self._batch_capacity_factor:
            raise RuntimeError("batch exceeds resident boundary admission before decoding")

    def preparation_timings(self) -> dict[str, Any]:
        return dict(self._preparation_timings)

    def admit_batch(self, demand):
        demand = _validate_runtime_capacity_demand(
            demand, expected_node_keys=tuple(node["key"] for node in self._nodes),
            expected_boundary_count=int(demand.get("boundary_count", -1)))
        basis = self._batch_capacity_basis
        factor = self._batch_capacity_factor
        carry = self._previous_build
        if (demand["boundary_count"] > factor * basis["boundary_count"]
            or demand["generation_count"] > factor * basis["generation_count"]
            or len(self._resident_generations) + demand["generation_count"] - int(carry is not None) > self._max_resident_generations):
            raise _CapacityAuditError("batch exceeds the fixed resident boundary/generation reservation")
        by_key = {node["key"]: node for node in demand["per_node"]}
        for key, limits in self._batch_admission_limits.items():
            value = by_key[key]
            for field, limit in limits.items():
                if value[field] > limit:
                    raise _CapacityAuditError(f"batch exceeds fixed per-node admission: {key}.{field}")
            capacity_tables = self._table_capacities_by_key[key]
            for table in value['tables']:
                program = table['program']
                capacity = capacity_tables.get(program)
                if capacity is None:
                    raise _CapacityAuditError('batch references an uncovered table instance')
                resident = self._resident_table_totals.get((key, program), (0, 0))
                if any(resident[i] + table[field] > capacity[field] for i, field in enumerate(('active', 'routes'))):
                    raise _CapacityAuditError(f'batch resident table reservation exhausted: {key}/{program}')
            active_cap, route_cap = self._map_capacities[key]
            # The carried generation is already reserved; counting it again is
            # conservative and leaves room for delayed in-flight retirement.
            if (self._resident_active_totals[key] + value["cumulative_active_link_entries"] > active_cap
                or self._resident_route_totals[key] + value["cumulative_route_entries"] > route_cap):
                raise _CapacityAuditError(f"batch resident entry reservation exhausted: {key}")
        self._capacity_demand = demand
        self._capacity_demand_by_key = by_key
        self._observed_boundary_count = 0
        self._observed_generation_count = int(carry is not None)
        self._observed_active_entry_totals = {node["key"]: 0 for node in self._nodes}
        self._observed_route_entry_totals = dict(self._observed_active_entry_totals)
        if carry is not None:
            for node in carry.generation_nodes:
                self._observed_active_entry_totals[node.key] = len(node.active_interfaces)
                self._observed_route_entry_totals[node.key] = len(node.routes)

    def _table_limits(self, demand):
        rows = {n['key']: {t['program']: t for t in n['tables']} for n in demand['per_node']}
        peaks = {}
        if self._continuation_enabled:
            for key, tables in rows.items():
                kind = self._node_by_key[key].entity_kind
                for program, table in tables.items():
                    peak = peaks.setdefault((kind, program), {'active': 0, 'routes': 0})
                    for field in peak:
                        peak[field] = max(peak[field], table[field])
        multiplier = self._batch_capacity_factor * (2 if self._continuation_enabled else 1)
        result = {}
        for node in self._nodes:
            key = node['key']
            result[key] = {}
            if node['program'] is not None:
                program = node['program']
                basis = (peaks.get((self._node_by_key[key].entity_kind, program), {})
                         if self._continuation_enabled else rows[key].get(program, {}))
                result[key][program] = {'program': program,
                    **{field: multiplier * max(1, basis.get(field, 0)) for field in ('active', 'routes')}}
        return result

    def _prewrite_nodes(self, draft, checkpoint):
        chunks, records, entries = [], [], 0
        for node in draft.generation_nodes:
            prior = self._node_versions.get(node.key)
            reuse = prior is not None and prior[0] is node
            version = prior[1] if reuse else draft.generation
            active = [] if reuse else [(i, node.prefix + data) for (i, _), data in node.active.items()]
            routes = [] if reuse else [(i, node.prefix + entry[1]) for (i, _), entry in node.forwarding.items()]
            self._node_versions[node.key] = node, version
            self._batch_prewrite_work["node_versions_reused" if reuse else "node_versions_written"] += 1
            pending = [(True, value) for value in active] + [(False, value) for value in routes]
            for offset in range(0, max(1, len(pending)), 2048):
                values = pending[offset:offset + 2048]
                if records and (len(records) == 64 or entries + len(values) > 2048):
                    chunks.append(records)
                    records, entries = [], 0
                records.append({"key": node.key, "version": version,
                    "active_entries": [[value[0], value[1].hex()] for is_active, value in values if is_active],
                    "route_entries": [[value[0], value[1].hex()] for is_active, value in values if not is_active],
                    "complete": offset + 2048 >= len(pending)})
                entries += len(values)
        if records:
            chunks.append(records)
        helper_seconds = 0.0
        helper_complete = True
        for index, records in enumerate(chunks):
            checkpoint()
            result = self._executor.execute("prewrite_generation", {
                "pin_root": self._pin_root, "generation": draft.generation,
                "generation_nodes": records, "seal": index + 1 == len(chunks),
                "bpf_batch_update_supported": self._bpf_batch_update_supported,
            })
            expected_active = sum(len(node["active_entries"]) for node in records)
            expected_routes = sum(len(node["route_entries"]) for node in records)
            self._batch_prewrite_work["active_entries_written"] += expected_active
            self._batch_prewrite_work["route_entries_written"] += expected_routes
            self._batch_prewrite_work["prewrite_work_units"] += 1
            if (result.get("preloaded_active_links") != expected_active
                or result.get("preloaded_routes") != expected_routes):
                raise RuntimeError("finite BPF prewrite returned an incomplete entry count")
            helper = (result.get("command_timing") or {}).get("helper") or {}
            seconds = helper.get("dispatch_seconds")
            helper_complete &= seconds is not None
            helper_seconds += seconds or 0
        return {"helper_seconds": helper_seconds, "helper_complete": helper_complete}

    def reclaim(self):
        active = self._active
        if active is None:
            return {"reclaimed_generations": [], "deleted_entries": 0}
        result = self._executor.execute("reclaim_generations", {
            "before_generation": active.generation, "maximum_entries": 1024})
        for generation in result["reclaimed_generations"]:
            self._resident_generations.remove(generation)
            reservation = self._generation_reservations.pop(generation)
            for identity, counts in self._table_reservations.pop(generation).items():
                for i in range(2): self._resident_table_totals[identity][i] -= counts[i]
            for node, (active_entries, routes) in zip(self._nodes, reservation):
                self._resident_active_totals[node["key"]] -= active_entries
                self._resident_route_totals[node["key"]] -= routes
        return result

    def activate(self, plan: _PreparedBoundary) -> dict[str, Any]:
        """Submit only an immutable current delta and handle real mutation results."""
        request_started = monotonic_ns()
        actual = _NO_ACTIVE_GENERATION if self._active is None else self._active.generation
        if plan.expected_active_generation != actual:
            raise RuntimeError("prepared boundary predecessor is stale")
        # Retain conservative ownership even when transport dies after a mutation.
        self._all_prepared_resources.update(plan.created)
        request_ended = monotonic_ns()
        try:
            result = self._executor.execute("activate_boundary", plan.request)
        except BaseException as error:
            evidence = getattr(error, "remote_evidence", getattr(error, "evidence", None))
            if not isinstance(evidence, Mapping):
                evidence = {
                    "failure_phase": "executor-result",
                    "target_transition_complete": False,
                    "generation_publication_occurred": None,
                    "live_state_may_be_partial": True,
                    "cleanup_ownership_retained": True,
                }
            raise _IncompleteActivationError(
                "live activation did not complete the target formal boundary",
                evidence={**evidence, "command_timing": getattr(error, "command_timing", None),
                          "parent_request_preparation_seconds": (request_ended - request_started) / 1e9},
            ) from error
        ledger_started = monotonic_ns()
        if (result.get("created_connections") != len(plan.created)
            or result.get("deleted_connections") != len(plan.removed)
            or result.get("target_transition_complete") is not True
            or result.get("boundary_timestamp") != plan.nominal_time.isoformat()
            or bool(result.get("generation_publication_occurred")) != plan.generation_changed):
            raise _IncompleteActivationError(
                "Execution kernel mutation result is incomplete",
                evidence={**result, "target_transition_complete": False,
                          "live_state_may_be_partial": True,
                          "cleanup_ownership_retained": True},
            )
        self._prepared_resources.update(plan.created)
        self._prepared_resources.difference_update(plan.removed)
        self._all_prepared_resources.difference_update(plan.removed)
        for identity in plan.withdrawn_identities:
            self._interface_indexes.pop(identity, None)
        for value in result.get("interface_identities", ()):
            if int(value["ifindex"]) <= 0:
                raise RuntimeError("Execution returned an invalid live ifindex")
            self._interface_indexes[(value["key"], value["interface"])] = int(value["ifindex"])
        self._active = plan
        result['connection_changes'] = tuple(
            {'op': op, 'connection': _connection_key(c), 'node_a': c.endpoint_a.value,
             'node_b': c.endpoint_b.value, 'interface_a': _cached_interface_names(c, self._connection_names)[0],
             'interface_b': _cached_interface_names(c, self._connection_names)[1]}
            for op, connections in (('remove', plan.removed), ('create', plan.created)) for c in connections)
        result["parent_runtime_timing"] = {
            "request_preparation_seconds": (request_ended - request_started) / 1e9,
            "result_ledger_seconds": (monotonic_ns() - ledger_started) / 1e9,
        }
        return result

    def working_set_counts(self) -> dict[str, int]:
        return {
            "resident_generation_keys": len(self._resident_generations),
            "resident_generation_capacity": self._max_resident_generations,
            "resident_reserved_active_entries": sum(self._resident_active_totals.values()),
            "resident_reserved_route_entries": sum(self._resident_route_totals.values()),
            "prepared_physical_resources": len(self._prepared_resources),
            "all_owned_physical_resources": len(self._all_prepared_resources),
        }


    def telemetry_foundation(self, *, foundation_token: str) -> dict[str, Any]:
        """Return detached collector and durable identity facts after initialize."""

        if not self._telemetry_enabled or not self._initialized:
            raise RuntimeError("traffic telemetry foundation is not initialized")
        return {
            "foundation_token": foundation_token,
            "pin_root": self._pin_root,
            "ring_capacity_bytes": int(
                self._telemetry_kernel["ring_capacity_bytes"]
            ),
            "event_schema": dict(self._telemetry_kernel["event_schema"]),
            "nodes": tuple(
                {
                    "node_slot": int(node["node_slot"]),
                    "node_id": str(node["key"]),
                    "runtime_ipv4": str(node["node_address"]),
                    "pin_component": str(node["pin_component"]),
                }
                for node in self._nodes
            ),
            "collector_request": {
                "pin_root": self._pin_root,
                "nodes": tuple(
                    {
                        "node_slot": int(node["node_slot"]),
                        "node_id": str(node["key"]),
                    }
                    for node in self._nodes
                ),
            },
        }

    def runtime_node_addresses(self) -> dict[NodeId, str]:
        """Return detached Runner-owned node addresses already frozen at init."""

        with self._lock:
            return dict(self._node_addresses)

    def _validate_collision_audit(self, audit: object) -> None:
        if (
            not isinstance(audit, dict)
            or audit.get("result") != "passed"
            or audit.get("selected_node_address_range")
            != str(self._node_address_range)
            or audit.get("audited_node_namespaces") != len(self._nodes)
        ):
            raise _CapacityAuditError(
                "privileged runtime command node address collision evidence "
                "is incomplete during preparation"
            )

    def _audit_boundary_capacity(
        self, draft: object
    ) -> dict[str, Any]:
        """Fail before helper work if decoded demand exceeds the sealed envelope."""

        with self._lock:
            observed_boundaries = self._observed_boundary_count
            observed_generations = self._observed_generation_count
            active_totals = self._observed_active_entry_totals
            route_totals = self._observed_route_entry_totals
            demand = self._capacity_demand
        if observed_boundaries + 1 > int(demand["boundary_count"]):
            raise _CapacityAuditError(
                "decoded boundary count exceeds sealed runtime capacity demand"
            )
        by_key = self._capacity_demand_by_key
        if draft.generation_changed:
            if observed_generations + 1 > int(demand["generation_count"]):
                raise _CapacityAuditError(
                    "decoded generation count exceeds sealed runtime capacity demand"
                )
            for node in draft.generation_nodes:
                key = node.key
                active_count = len(node.active_interfaces)
                route_count = len(node.routes)
                sealed = by_key[key]
                sealed_tables = {t['program']: t for t in sealed['tables']}
                for program, counts in node.table_counts.items():
                    table = sealed_tables.get(program)
                    if table is None or counts[0] > table['active'] or counts[1] > table['routes']:
                        raise _CapacityAuditError('decoded table exceeds sealed instance demand')
                if (
                    active_count
                    > sealed["maximum_generation_active_link_entries"]
                    or route_count > sealed["maximum_generation_route_entries"]
                    or active_totals[key] + active_count
                    > sealed["cumulative_active_link_entries"]
                    or route_totals[key] + route_count
                    > sealed["cumulative_route_entries"]
                ):
                    raise _CapacityAuditError(
                        "decoded generation exceeds sealed runtime capacity demand: "
                        f"node={key}"
                    )
        transition_count = len(draft.previous_resources) + len(draft.new_resources)
        if transition_count > demand['physical_connection_high_water']:
            raise _CapacityAuditError('decoded transition physical demand exceeds sealed envelope')
        # Maintain the add-before-withdraw proof from connection deltas. Unchanged
        # interfaces need no repeated collision scan or recount.
        touched = set()
        for connection in draft.new_resources:
            for endpoint, name in zip((connection.endpoint_a, connection.endpoint_b),
                                       _cached_interface_names(connection, self._connection_names)):
                prior = self._live_interface_names.setdefault(name, connection)
                if prior != connection:
                    raise _CapacityAuditError('Runner interface-name capacity collision during preparation')
                self._physical_interface_counts[endpoint.value] += 1
                touched.add(endpoint.value)
        if any(self._physical_interface_counts[key] > by_key[key]['physical_interface_high_water'] for key in touched):
            raise _CapacityAuditError('decoded transition interface demand exceeds sealed envelope')
        for connection in draft.removed_resources:
            for endpoint, name in zip((connection.endpoint_a, connection.endpoint_b),
                                       _cached_interface_names(connection, self._connection_names)):
                del self._live_interface_names[name]
                self._physical_interface_counts[endpoint.value] -= 1
        terminal_checked = observed_boundaries + 1 == demand["boundary_count"]
        if terminal_checked:
            terminal = demand["terminal"]
            if (
                len(draft.resources) != terminal["active_connection_count"]
                or observed_generations + int(draft.generation_changed)
                != demand["generation_count"]
                or any(
                    len(draft.generation_by_node[self._node_by_key[key]].active_interfaces)
                    != by_key[key]["terminal_active_link_entries"]
                    or len(draft.generation_by_node[self._node_by_key[key]].routes)
                    != by_key[key]["terminal_route_entries"]
                    for key in by_key
                )
            ):
                raise _CapacityAuditError(
                    "terminal decoded state differs from sealed capacity demand"
                )
        return {
            "result": "passed",
            "boundary_count_after": observed_boundaries + 1,
            "generation_count_after": (
                observed_generations + int(draft.generation_changed)
            ),
            "transition_physical_connections": transition_count,
            "terminal_reconciled": terminal_checked,
        }

    def _commit_observed_capacity(self, draft: object) -> None:
        self._observed_boundary_count += 1
        if draft.generation_changed:
            self._observed_generation_count += 1
            for node in draft.generation_nodes:
                self._observed_active_entry_totals[node.key] += len(
                    node.active_interfaces
                )
                self._observed_route_entry_totals[node.key] += len(node.routes)

    def _connection_identity(
        self, connection_id: OperationalConnectionId
    ) -> dict[str, Any]:
        interface_a, interface_b = _cached_interface_names(connection_id, self._connection_names)
        return {
            "connection": _connection_key(connection_id),
            "node_a": connection_id.endpoint_a.value,
            "node_b": connection_id.endpoint_b.value,
            "interface_a": interface_a,
            "interface_b": interface_b,
        }

    def _release_preparation(self) -> None:
        self._previous_build = None
        self._material_cursor = None
        self._active = None
        for values in (
            self._node_versions, self._batch_prewrite_work, self._generation_reservations,
            self._table_reservations, self._resident_table_totals,
            self._resident_active_totals, self._resident_route_totals,
            self._capacity_demand_by_key, self._logical_ports, self._port_by_interface,
            self._connection_names,
            self._live_interface_names, self._physical_interface_counts,
            self._telemetry_interfaces, self._telemetry_node_routes,
            self._node_by_key, self._node_slots,
        ):
            values.clear()
        self._nodes = ()
        self._node_ids = ()
        for name in ("_batch_capacity_basis", "_batch_admission_limits", "_table_capacities_by_key"):
            if hasattr(self, name):
                getattr(self, name).clear()

    def cleanup(self) -> dict[str, Any]:
        if not self._cleanup_required:
            self._release_preparation()
            return {"deleted_connections": 0,
                    "pin_root_removed": True if self._initialization_outcome == "cleaned" else None,
                    "cleanup_confirmed": True,
                    "initialization_outcome": self._initialization_outcome, "errors": []}
        construction_started = monotonic_ns()
        request = {
                "pin_root": self._pin_root,
                "nodes": [
                    {
                        **node,
                        "node_address_owned": self._node_by_key[node["key"]] in self._owned_node_addresses,
                        "zone_addresses": sorted(self._all_zone_addresses[self._node_by_key[node["key"]]]),
                    }
                    for node in self._nodes
                ],
                "connections": [
                    self._connection_identity(value)
                    for value in sorted(
                        self._all_prepared_resources, key=_connection_key
                    )
                ],
                "worker_count": self._worker_count,
            }
        construction_ended = monotonic_ns()
        result = self._executor.execute("cleanup", request)
        ledger_started = monotonic_ns()
        known_no_mutation = (result.get("cleanup_confirmed") is True
                             and result.get("initialization_outcome") == "no-mutation")
        if (result.get("pin_root_removed") is not True and not known_no_mutation) or result.get("errors"):
            raise RuntimeError(
                "Runner teardown evidence is incomplete; ownership retained for retry"
            )
        self._initialized = False
        self._cleanup_required = False
        self._initialization_outcome = "no-mutation" if known_no_mutation else "cleaned"
        self._telemetry_kernel.clear()
        self._prepared_resources.clear()
        self._all_prepared_resources.clear()
        self._interface_indexes.clear()
        self._resident_generations.clear()
        self._map_capacities.clear()
        self._capacity_plan.clear()
        self._capacity_demand.clear()
        self._observed_boundary_count = 0
        self._observed_generation_count = 0
        self._observed_active_entry_totals.clear()
        self._observed_route_entry_totals.clear()
        self._physical_resource_high_water = 0
        self._bpf_batch_update_supported = False
        self._active = None
        self._all_zone_addresses.clear()
        self._owned_node_addresses.clear()
        self._release_preparation()
        result["parent_cleanup_timing"] = {
            "request_construction_seconds": (construction_ended - construction_started) / 1e9,
            "result_ledger_seconds": (monotonic_ns() - ledger_started) / 1e9,
        }
        return result
