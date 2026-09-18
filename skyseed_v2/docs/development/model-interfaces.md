# Model interfaces and extension points

Use this reference when implementing or reviewing a Model. The role table locates
minimum interfaces, templates and concrete implementations; the sections below separate
required output semantics from optional capabilities and default policies.

A Model is an independently injected algorithm. Constructors, representation and research
assumptions belong to its author. Structural Protocols require only the calls below; there
is no inheritance, registry, plugin discovery, worker, geometry or optimality requirement.
Default assembly remains in Simulator. The actual S3/S5 algorithms remain example-local.


| Role and minimum result | Copyable template | Default implementation | Example |
|---|---|---|---|
| Position: `compute_positions(world, timestamp)` → complete NodeId/ECEF mapping | [position](../../templates/models/position.py) | [CircularOrbitPositionModel](../../src/skyseed_v2/simulator/physical/models/circular_orbit.py) | [minimum fixture](../../tests/unit/test_me1_models.py) |
| PhysicalLink: `compute_observation(world, frame, request)` → matching PhysicalLinkQueryResult | [physical link](../../templates/models/physical_link.py) | [GeometricPhysicalLinkModel](../../src/skyseed_v2/simulator/physical/models/links/geometric.py) | [S5 current geometry](../../examples/Sky01_s5_custom_models/model.py) |
| ISL: `create_inter_satellite_candidate_pairs(world)` → canonical potential pairs; `select_inter_satellite_links(world, candidates, state)` → InterSatelliteSelection | [ISL](../../templates/models/inter_satellite.py) | [retain-first](../../src/skyseed_v2/simulator/planner/inter_satellite.py) | [S5 greedy](../../examples/Sky01_s5_custom_models/model.py) |
| GS: `select_ground_station_accesses(world, candidates, state)` → candidate tuple | [GS](../../templates/models/ground_station.py) | [coverage-first](../../src/skyseed_v2/simulator/planner/ground_station/model.py) | [S5 greedy](../../examples/Sky01_s5_custom_models/model.py) |
| Zone service: `assign_zone_service(world, candidates, previous_assignments, *, timestamp)` → ZoneServicePlanningResult | [service](../../templates/models/zone_service.py) | [highest elevation](../../src/skyseed_v2/simulator/planner/zone_service.py) | [Five Shells scopes](../../examples/Sky05_five_shells/five_shells.py) |
| Parameters: `plan_effective_parameters(timestamp, observations, previous_memory)` → ParameterPlanningResult | [parameters](../../templates/models/parameters.py) | [deadband](../../src/skyseed_v2/simulator/planner/parameters.py) | [Tiny settings](../../examples/Sky00_tiny_network/tiny_network.py) |
| Routing: `plan_route_outcomes(world, operational_connections, zone_service_assignments, demands, previous_outcomes, *, timestamp)` → RoutePlanningResult | [routing](../../templates/models/routing.py) | [gateway hop count](../../src/skyseed_v2/simulator/planner/routing/gateway.py) | [S3 local OSPF](../../examples/Sky03_s3_ospf/ospf_model.py) |

## Lower-level optional Zone membership

ZoneMembership remains independently replaceable through `zone_membership_model`.
It answers geographic membership inside ZoneLayout; ZoneService is the primary
Planner role that chooses which satellite serves a Zone.

| Minimum contract | Interface | Template | Default |
|---|---|---|---|
| `find_zones(zones, latitude_deg, longitude_deg)` and `resolve_zone(matches, latitude_deg, longitude_deg)` | [ZoneMembershipModel](../../src/skyseed_v2/simulator/physical/models/zone_membership.py) | [membership](../../templates/models/zone_membership.py) | [DefaultZoneMembershipModel](../../src/skyseed_v2/simulator/physical/models/zone_membership.py) |

Semantic event ordering is framework-owned [Transition behavior](planner-state-and-plans.md#plans-and-dependencies),
not a replaceable Model. Explicit dependency values remain part of the Plan contract.

## Values and author freedom

Results refer to the supplied World's identities. Selections resolve to supplied candidates;
identity, timestamps, complete output correspondence, endpoint capacity and executable route
representation remain checked. No geometric plausibility, stability or “best algorithm”
proof is required. ISL pair enumeration supplies the potential query envelope, not current
selection. `NO_ELIGIBLE_PATH` describes the chosen algorithm's eligibility rules.

Candidate distance, delay, elevation, plane metadata, work counts and selection diagnostics
may be unavailable. Tools preserve null/unknown rather than fabricate zero or rerun Models.
Compatible role combinations are the author's responsibility: default ISL needs plane/distance,
default GS/Zone need elevation, and default parameters need delay. A simpler Model can supply
complete effective parameters directly. `ConnectionParameterObservation` passes the original
candidate and nullable physical delay. Parameter memory is immutable, bounded, opaque to
Planner, initially None, and thereafter the last successfully adopted Model value.

`describe_configuration()` is optional provenance. Position/membership's optional semantic
identity hook is defined in [physical ownership](component-boundaries.md#physicalworld-and-queries).
Custom Model convenience must never become a Tool-driven identity or serialization requirement.

## Preparation, prediction and search

`ConnectionSelectionState` contains timestamp, cadence, acquisition duration, original
`selected_since` times (including preparing reservations), and operational identities.
[Planner](planner-state-and-plans.md) owns acquisition, not the Models' private settings. Optional
`required_contact_duration(*, acquisition_duration, observation_interval)` requests a
nonnegative prediction horizon; absence means no prediction requirement, not no acquisition.
Default Models round preparation and add their minimum-service preference. Operational
incumbents remain eligible while currently feasible; pending selection uses its original start.

Optional physical `predict_contacts(...)` supplies bounded proofs. A finite contact-window
end limits proven coverage, not physical existence. `end_timestamp=None` proves all future;
no window means unknown, including custom positions without motion bounds. Default admission
and pruning preserve unknown current feasibility. Models may choose different policies.

Only explicit `supports_ground_station_candidate_limits() -> True` enables limited GS
queries. `ground_station_search_complete` is independent of work statistics: True means no
eligible candidate was omitted by the count budget, False means some were, None makes no claim.
Unknown limited feedback widens once to unbounded. GS `initial_candidate_search` and
`expand_candidate_search` may advise wider counts, unbounded or no further search under their
own policy. Optional ISL `candidate_neighbor_count(world)` advises nearest-neighbor scope.
Planner adds retained/preparing pairs irrespective of rank. Missing capabilities cause one
unbounded GS query; they never require default algorithm internals.

## Routing and retry

Routing computes on every formal step unless `can_reuse_unchanged_inputs()` explicitly
returns True. Within that permission, missing `depends_on_connection_parameters()` means
True; explicit False permits reuse for parameter-only changes. Topology, service and demand
changes invalidate reuse. Automatic `create_route_demands(world, assignments, *, timestamp)`
runs before reuse; explicit demands, including empty, bypass it.

Position, physical facts and selections may be evaluated during prehistory. Query expansion
can call physical computation repeatedly at the same timestamp. Failed planning restores
Planner's state/frontiers/acquisition/adopted parameter memory, but cannot roll back arbitrary
Model effects. Authors own retry behavior; the framework does not promise one algorithm call
per timestamp. Retained continuation never replays previously completed formal progression.

## Optional diagnostics and resources

`selection_diagnostics`, `selection_statistics`, `allocation_statistics`,
`route_work_statistics` and `route_resource_statistics` are optional read-only observations.
Statistics are requested only when recording; route work only after route computation.
Ordinary diagnostic failures degrade auxiliary evidence while preserving valid Plans and
independent capabilities. Computation errors and cancellation propagate.

Use distinct Model instances per role and run. Optional idempotent `shutdown(*, wait=True)`
is called by the existing owner: Planner closes its five algorithm roles;
PhysicalWorld closes Position/membership; Simulator integrates them and PhysicalLink.
Producer drains evidence before releasing these same owners. Controller covers failure
before Producer construction. Retained waiting keeps Models open; explicit exit closes them.
Resource-free Models omit shutdown. Published immutable facts remain readable after cleanup.

Default optimizations and their costs are explained in [mechanisms](../architecture/mechanisms.md).
Use the [authoring guide](../user-guides/model-extension.md) for injection and finite verification.
