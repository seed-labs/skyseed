# Planning inputs and results

Most Model inputs are immutable values supplied to a hook, rather than objects to
query through Planner. Read those arguments directly. The
[interface table](../development/model-interfaces.md) specifies each hook's complete
inputs and required output; this page explains useful data inside them.

## Physical candidates

Import candidate/result types from `skyseed.simulator.physical`.
[`PhysicalLinkRequest` and `PhysicalLinkQueryResult`](../../src/skyseed/simulator/physical/queries/physical_link.py)
describe one requested observation, not the network's selected connections.

| Value | Useful fields |
|---|---|
| `PhysicalLinkRequest` | `inter_satellite_pairs` (canonical NodeId pairs), `include_ground_station_access`, `include_zone_service`; optional search/prediction settings are in the [capability contract](../development/model-interfaces.md#preparation-prediction-and-search) |
| `InterSatelliteLinkCandidate` | `endpoint_a`, `endpoint_b`; optional `same_plane`, `cyclic_plane_separation`, `distance_m`, `propagation_delay_us`, `contact_window` |
| `GroundStationAccessCandidate` | `ground_station_id`, `satellite_id`; optional `distance_m`, `elevation_deg`, `propagation_delay_us`, `contact_window` |
| `ZoneServiceCandidate` | `zone_id`, `satellite_id`; optional `distance_m`, `elevation_deg`, `propagation_delay_us` |
| `PhysicalLinkQueryResult` | `timestamp`, the three candidate tuples, optional `work_counts` and `ground_station_search_complete` |

`distance_m` is metres, `elevation_deg` degrees and `propagation_delay_us` microseconds.
Optional measurements may be `None`, which means unavailable, not zero. A selected
default Model may need a measurement; a custom Model need not use it. All identities
refer to the supplied World. The physical result timestamp matches the supplied frame.

`PhysicalContactWindow(start_timestamp, end_timestamp)` records proven temporal coverage;
`covers(start, end)` queries that coverage. A missing window means unknown;
`end_timestamp=None` means proven unbounded coverage, not unknown. See the linked
contract before implementing optional prediction or limited candidate search.

## Selection state

ISL and GS selection receive a
[`ConnectionSelectionState`](../../src/skyseed/simulator/planner/selection.py).
Planner creates it; custom Models do not need to read Planner's internal tables.

| Field | Use |
|---|---|
| `timestamp` | Current selection datetime |
| `observation_interval` | Formal cadence as a timedelta |
| `acquisition_duration` | Framework-owned preparation duration as a timedelta |
| `selected_since` | Read-only mapping from OperationalConnectionId to original selection time, including preparing reservations |
| `operational_connections` | Frozen set of connection identities already operational |

Use World queries for static endpoint capacities, and this state for retained/preparing
selections. A selected connection is not necessarily operational yet. Your policy may
retain, replace or poorly choose candidates; Planner still owns acquisition progression.

## Effective parameters

The parameter Model receives no PhysicalWorld. Its
[`ConnectionParameterObservation`](../../src/skyseed/simulator/planner/parameters.py)
provides `connection_id`, nullable `physical_delay_us` and nullable `physical_candidate`.
The candidate is the supplied ISL/GS physical value, not a second calculation.

[`OperationalConnection`](../../src/skyseed/simulator/planner/values.py) has
`connection_id`, `endpoint_a_to_b` and `endpoint_b_to_a`. Each direction contains
`EffectiveConnectionParameters(bandwidth_bps, delay_us, packet_loss_ppm)`:
bits per second, microseconds and parts per million. The identity's `kind`,
`endpoint_a` and `endpoint_b` use canonical endpoint ordering; directional parameters
refer to that ordering.

Return `ParameterPlanningResult(connections=..., memory=...)`. `previous_memory` is
initially `None`, then the last successfully adopted Model memory. Its representation
belongs to the Model; keep it immutable and bounded as described in the
[memory contract](../development/model-interfaces.md#values-and-author-freedom).

## Service and routing

Import these values from `skyseed.simulator.planner`; their definitions live in
[`values.py`](../../src/skyseed/simulator/planner/values.py) and the role modules
linked by the [Model table](../development/model-interfaces.md).

| Value | Useful fields / interpretation |
|---|---|
| `ZoneServiceAssignment` | `zone_id`, `satellite_id`, `network_scope`; `satellite_id=None` is explicitly unserved; `service_key` is `(network_scope, zone_id)`; `is_served` reports service |
| `RouteDemand` | `source` NodeId and `target` |
| `GroundStationRouteDemandTarget` | `ground_station_id`, `network_scope` |
| `SatelliteRouteDemandTarget` | `satellite_id` (satellite `NodeId`), `network_scope`; existing stable node /32 |
| `ZoneRouteDemandTarget` | `zone_id`, `network_scope`, `service_key` |
| `ReachableRouteOutcome` | `demand`, `next_hop`, `path` (source-to-terminal NodeId tuple) |
| `UnreachableRouteOutcome` | `demand`, `reason` (RouteUnreachableReason) |
| `LocalRouteOutcome` | `demand`, `disposition`, `candidates`, `data`; local choices make no complete-path claim |
| `RouteCandidate` | `exit: LogicalExit`, `data: PolicyRecord` |
| `LogicalExit` | `connection: OperationalConnectionId`, `source: NodeId`; `peer` is derived |
| `PolicyRecord` | immutable named integer/boolean/reference/nested-record fields |
| `RouteDisposition` | `FORWARD`, `DROP`, `PASS`; PASS requests Linux processing |
| `RoutePlanningResult` | replacement `outcomes` tuple; omitted entries withdraw previous arrangements |

Routing receives operational connections, service assignments, demands and previous
outcomes, plus World and timestamp. Build your graph from those connections; physical
candidates and preparing selections are not usable edges yet. For a Zone target the
terminal is its serving satellite; a Satellite target terminates at that named node.
A GS may be an endpoint but cannot be an intermediate shortcut. Preserve
scope when looking up service. Required route representation and destination consistency
are in the [planning contract](../development/planner-state-and-plans.md), with a
small working algorithm in the [routing template](../../templates/models/routing.py).

## Reading Planner results

Scenario code gets Planner from `simulation.get_planner()`. Model authors normally
use hook arguments instead of querying the owner mid-update.
[`Planner`](../../src/skyseed/simulator/planner/planner.py) exposes:

| Read | Result |
|---|---|
| `get_state()` | Current PlannedOperationalState: timestamp, operational connections, service assignments and route outcomes |
| `get_last_transition()` | Last PlanTransition: established/withdrawn/parameter-updated connections, service/route changes and semantic event dependencies |
| `get_frontiers()` | Physical-observation, planning and publication datetimes; individual fields can be `None` before progress |
| `get_route_demands()` | Demands supplied at the latest completed formal step |
| `get_route_recomputation_count()` | Count of actual route computations |

State and last-transition reads raise `RuntimeError` before a formal state exists.
They do not advance calculation. Computed state is not the deployed Runner's ACTIVE state.

A `Plan` groups transitions and records its identity, predecessor, time range, cadence
and input/output state identities. `planner.apply_plan(previous_state, plan,
physical_world_fingerprint=...)` returns an updated value without modifying Planner or
Linux. For standalone value serialization,
[`planner.evidence`](../../src/skyseed/simulator/planner/evidence.py) supplies
`plan_to_data(plan)` / `plan_from_data(mapping)` and
`planned_operational_state_to_data(state)` / `planned_operational_state_from_data(mapping)`.
These convert values and mappings; they do not load a live owner's state or publish a
completed experiment range. Normal output reading follows the
[results and publication guide](../user-guides/outputs-and-tools.md).
