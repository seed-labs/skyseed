# Skyseed v2 Terminology

This glossary is normative for public names and current architecture records.
A type can be public from its owning module without being re-exported from the
package root.

| Term | Unique meaning | Owner | Must not mean | Public exposure |
| --- | --- | --- | --- | --- |
| Shell | Emulator-side entity owned by one Emulator-side `SkyseedAutonomousSystem`; owns Emulator Orbits | `entities/shell.py` | Simulator Topology Shell, constellation, population or input record | root and `skyseed_v2.entities` |
| Orbit | Emulator-side entity owned by one Emulator Shell; owns one `OrbitParameters` value and its Emulator Satellites | `entities/orbit.py` | Simulator Topology Orbit, generated input record or runtime trajectory | root and `skyseed_v2.entities` |
| OrbitParameters | Orbit-owned frozen altitude, inclination, right ascension and UTC reference epoch; the first configuration version copies one explicit common Shell epoch into each Orbit | `entities/orbit.py` | Satellite field, Timeline start, per-Orbit Shell override, dynamic position or trajectory | root and `skyseed_v2.entities` |
| Satellite | Emulator-side Satellite entity and the same SEED Node instance; owns only per-Satellite values | `entities/satellite.py` | Simulator Topology Satellite, Orbit owner, Simulator input, container or Config | root and `skyseed_v2.entities` |
| GroundStationLocation | Emulator-side GroundStation geodetic location with finite WGS84 ellipsoidal height in metres | `entities/ground_station.py` | ECEF Position, service selection or generic elevation evidence | root and `skyseed_v2.entities` |
| GroundStation | Emulator-side GroundStation entity and the same SEED Router instance | `entities/ground_station.py` | Simulator Topology GroundStation, Simulator input, ordinary Router, service or Config | root and `skyseed_v2.entities` |
| StaticEmulatorConfig | Frozen, backend-independent top-level file or programmatic configuration | `configuration/static_emulator_config.py` | Emulator entity, runtime state or Simulator input | `skyseed_v2.configuration` |
| LatitudeLongitudeRectangleDefinition | Frozen construction input for one rectangular Zone geometry | `configuration/zone.py` | runtime geometry, coordinate entity or generic geometry abstraction | `skyseed_v2.configuration` |
| ZoneDefinition | Frozen construction input carrying one Zone ID, priority and rectangle definition | `configuration/zone.py` | runtime Zone, owner, registry or service selection | `skyseed_v2.configuration` |
| ZoneLayoutDefinition | Frozen complete Zone layout materialization input, produced programmatically or from YAML | `configuration/zone.py` | query-owning layout, runtime state or Topology-owned object | `skyseed_v2.configuration` |
| load_zone_layout_definition | Direct function that validates Zone YAML and returns `ZoneLayoutDefinition` | `configuration/yaml.py` | entity factory, YAML-specific Zone model or runtime layout constructor | `skyseed_v2.configuration` |
| GeneratedOrbitLayout | Frozen rules for expanding one Shell's generated Orbits and Satellites | `configuration/satellite.py` | independent Config, entity population, factory or runtime trajectory | `skyseed_v2.configuration` |
| RightAscensionDistribution | Frozen right-ascension distribution nested in `GeneratedOrbitLayout` | `configuration/satellite.py` | Orbit, Config root or runtime position | `skyseed_v2.configuration` |
| InOrbitPhaseDistribution | Frozen in-orbit phase distribution nested in `GeneratedOrbitLayout` | `configuration/satellite.py` | Satellite state, Config root or runtime position | `skyseed_v2.configuration` |
| InternetExchangeAttachment / LocalNetworkAttachment | Frozen relations connecting a declared node to an existing IX or local Network | `configuration/attachments.py` | GroundStation geography, SEED interface or global reference system | `skyseed_v2.configuration` |
| EbgpRouterReference | Frozen reference to one declared Router-like eBGP endpoint | `configuration/ground_network.py` | Router entity, global locator or Simulator identity | `skyseed_v2.configuration` |
| load_static_emulator_config | Direct function that validates versioned YAML and returns `StaticEmulatorConfig` | `configuration/yaml.py` | entity factory, `SkyseedDockerCompiler` or Registry reader | `skyseed_v2.configuration` |
| create_static_emulator | Stateless function that creates and directly returns an upstream Emulator from `StaticEmulatorConfig` | `static_emulator.py` | result wrapper, Emulator subclass, Simulator-input generation or `SkyseedDockerCompiler` | `skyseed_v2.static_emulator` |
| SatelliteSimulationInput | Receiving-side frozen boundary data combining approved static facts from one Emulator Satellite and its owning Orbit parameters | `simulator/static_input/satellite.py` | Simu Satellite domain object, SEED Node, runtime position or execution target | `skyseed_v2.simulator.static_input` |
| GroundStationSimulationInput | Receiving-side frozen boundary data copying approved static facts from one Emulator GroundStation | `simulator/static_input/ground_station.py` | Simu GroundStation domain object, SEED Router or execution target | `skyseed_v2.simulator.static_input` |
| SimulatorStaticInput | Frozen deterministic cross-layer construction boundary aggregating pure static inputs by `NodeId` | `simulator/static_input/static_input.py` | complete or long-lived Simulator domain model, AS traversal, policy bag, entity/resource index, Registry or container inventory | `skyseed_v2.simulator.static_input` |
| create_simulator_static_input | Thin external adapter function that traverses Emulator-side AS owners and returns one `SimulatorStaticInput` | `simulator_input_generation.py` | Simulator-core behavior, factory class, entity index or compatibility alias | `skyseed_v2.simulator_input_generation` |
| SimulationTimeline | Frozen Slice timestamp authority with lazy bounded/unbounded iteration; accepted Slice A behavior is aware-only input normalized and stored as UTC | `simulator/timeline.py` | clock, arbitrary-timestamp gateway, timestamp cache, Slice store or pacing service | `skyseed_v2.simulator` |
| SimulationDefinition | Frozen complete construction input pairing `static_input` and `timeline` | `simulator/definition.py` | mutable configuration bag, Emulator input or runtime context | `skyseed_v2.simulator` |
| Simulator | Simu composition root retaining one Definition, atomically creating at most one Topology and, after it, at most one explicit-Model Slicer | `simulator/simulator.py` | Emulator wrapper, adapter, Slice producer, planner, compiler or runner | `skyseed_v2.simulator` |
| Topology | Immutable Simulator static-network aggregate root owning at most one `ZoneLayout` and exposing narrow owner-delegating queries | `simulator/topology.py` | boundary DTO, global database, dynamic Slice, Planning service or Emulator owner | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.SkyseedAutonomousSystem` | Simulator-owned immutable AS node; owns Simulator Shells and GroundStations | `simulator/topology.py` | Emulator/SEED AS, adapter or Registry | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.Shell` | Simulator-owned immutable Shell; owns Simulator Orbits | `simulator/topology.py` | Emulator Shell, input record or constellation | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.Orbit` | Simulator-owned immutable Orbit; owns shared orbit parameters and Simulator Satellites | `simulator/topology.py` | Emulator Orbit, input record, position or trajectory | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.Satellite` | Simulator-owned immutable static Satellite beneath one Orbit | `simulator/topology.py` | SEED Node, Emulator Satellite, position or runtime resource | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.GroundStation` | Simulator-owned immutable static GroundStation beneath one Simulator AS | `simulator/topology.py` | SEED Router, Emulator GroundStation or runtime resource | `skyseed_v2.simulator` |
| LatitudeLongitudeRectangle | Immutable canonical latitude/longitude rectangle with closed-boundary `contains` and deterministic representative center | `simulator/zone.py` | coordinate value, Earth model, spherical area centroid or extensible geometry interface | `skyseed_v2.simulator` |
| Zone | Immutable Simulator domain value carrying one `zone_id`, priority and `LatitudeLongitudeRectangle` | `simulator/zone.py` | membership algorithm, configuration input, owner, runtime state or service selection | `skyseed_v2.simulator` |
| ZoneLayout | Topology-owned immutable aggregate that solely owns the Zone tuple and ID index, retains one fixed membership Model and validates Model output | `simulator/zone.py` | Definition, mutable registry, Model implementation or user-constructed layout | `skyseed_v2.simulator` |
| ZoneMembershipModel | Runtime-checkable Protocol for physical-membership and authoritative-resolution algorithms invoked with layout-owned Zones | `simulator/zone.py` | entity, configuration model, Zone owner, position source, service selector or Planning component | `skyseed_v2.simulator` |
| DefaultZoneMembershipModel | Stateless deterministic implementation of `ZoneMembershipModel` | `simulator/zone.py` | Zone collection, mutable runtime state, previous-state holder or service selector | `skyseed_v2.simulator` |
| physical membership | Every layout-owned Zone whose closed-boundary `contains` operation accepts the query point | `ZoneMembershipModel.find_zones` | one authoritative answer, service selection or positive-area overlap | query result from `skyseed_v2.simulator` |
| authoritative Zone | The single physical match selected by the active membership Model | `ZoneMembershipModel.resolve_zone` | all physical matches, Satellite choice or dynamic topology state | query result from `skyseed_v2.simulator` |
| NodeId | Pure stable logical node identity shared across Emulator packaging, Simulator data/state/plans and future dynamic-execution owner-directed lookup | `node_id.py` | entity property, lookup service, Registry key, container ID, Compose service, namespace, interface or Simulator-local number | `skyseed_v2.node_id` |
| Simulator static-input generation | Three direct functions sharing one private NodeId derivation to copy supported entities into pure inputs | `simulator_input_generation.py` | entity index, Projector, Builder, Resolver class, input classmethod or Simulator behavior | `skyseed_v2.simulator_input_generation` |
| SkyseedDockerCompiler | Implemented SEED integration that lowers a rendered Emulator/SEED static model into Dockerfile and Compose deployment configuration | `seedemu_integration/skyseed_docker_compiler.py` | entity creator, dynamic execution compiler, runtime updater or materializer | `skyseed_v2.seedemu_integration` |
| SimulationPlanCompiler | **Retired/corrected** shorthand for the over-compressed `abstract Simulation Plan → compiler → concrete dynamic operations` description; retained only to explain the correction | not implemented | frozen future public contract, semantic Planner, BackendPlanner, conceptual `ExecutionCompiler`, `SkyseedDockerCompiler` or shared compiler parent | not implemented |
| ComposeProjectLauncher | Implemented application/infrastructure service that launches and verifies an already compiled Compose project | `materialization/compose_project_launcher.py` | Emulator execution service, dynamic `SimulationRunner`, compiler, Simulator domain state or generic Launcher abstraction | `skyseed_v2.materialization` |
| SkyseedAutonomousSystem | Owner of Shells and GroundStations plus the narrow SEED lifecycle extension | `seedemu_integration/skyseed_autonomous_system.py` | file facade or Simulator-input owner | `skyseed_v2.seedemu_integration` |
| SatelliteBase | SEED Base Layer integration and real AS owner; provides the narrow Skyseed-AS collection query | `seedemu_integration/satellite_base.py` | Emulator entity, file configuration, global index or Simulator adapter | `skyseed_v2.seedemu_integration` |

## Dynamic terms and implementation boundaries

The following names and meanings are frozen by
[`dynamic-slice-public-contract.md`](dynamic-slice-public-contract.md), but no
corresponding code or public export exists yet. “Planned exposure” does not
authorize package-root export.

| Term | Unique accepted meaning | Owner | Must not mean | Planned exposure |
| --- | --- | --- | --- | --- |
| EarthFixedPosition | Frozen finite WGS84 Earth-fixed geocentric Cartesian XYZ, equivalent to EPSG:4978 semantics, in metres; the sole authoritative Position | approved Simulator Position boundary | geographic cache, inertial coordinate, trajectory or second Position authority | `skyseed_v2.simulator` or explicit submodule |
| geographic Position view | Deterministic read-only WGS84 geodetic latitude, longitude and ellipsoidal height derived from EarthFixedPosition | fixed coordinate capability | independently stored authoritative state or Tool conversion | query from Simulator Position boundary |
| SphericalCap | Frozen Earth-surface circle with geographic center and angular radius in `[0, 180]` | approved Simulator Zone boundary | generic geometry hierarchy, Satellite coverage owner or service assignment | `skyseed_v2.simulator` or explicit submodule |
| PositionModel | Structural Protocol mapping exact Topology plus one Timeline timestamp to a complete readonly NodeId→EarthFixedPosition mapping | one Slicer running context | previous-Slice consumer, history owner, Tool propagation hook or trajectory store | `skyseed_v2.simulator` |
| ConnectionModel | Structural Protocol producing one complete ConnectionSlice from exact Topology, timestamp, current positions and previous ConnectionSlice | one Slicer running context | ConnectionSlice owner, Position propagator, Route Model, Planner or fallback selector | `skyseed_v2.simulator` |
| RouteModel | Structural Protocol producing one complete RouteSlice from exact Topology, timestamp, current ConnectionSlice and previous RouteSlice | one Slicer running context | visibility selector, path store, label/resource owner or backend planner | `skyseed_v2.simulator` |
| CircularOrbitPositionModel | Explicit built-in planned-circular-Orbit PositionModel | approved Simulator Model implementation | hidden default, TLE/SGP4, velocity or precise ephemeris Model | explicit Simulator Model submodule |
| GeometricConnectionModel | Explicit built-in ConnectionModel with immutable configuration, stable private ISL candidates, geometric activation, GroundStation access and Zone service assignment | approved Simulator Model implementation | hidden default, dynamic matching, history owner or ConnectionSlice evidence store | explicit Simulator Model submodule |
| HopCountRouteModel | Explicit built-in RouteModel using current bidirectional Connections, reverse BFS, hop count and structural NodeId tie-break | approved Simulator Model implementation | hidden default, delay-weighted routing, full-path store or previous-state stabilizer | explicit Simulator Model submodule |
| ConnectionKind | Initial connection category enum: INTER_SATELLITE or GROUND_STATION_ACCESS | approved Connection boundary | same-plane/cross-plane candidate source or backend resource kind | `skyseed_v2.simulator` |
| ConnectionId | Stable kind plus two distinct endpoints normalized by structural NodeId key | approved Connection boundary | temporary number, resource slot, direction or mutable link state | `skyseed_v2.simulator` |
| DirectionalConnectionParameters | Complete one-direction integer bandwidth_bps, delay_us and packet_loss_ppm values | approved Connection boundary | unitless float, candidate score or geometry evidence | `skyseed_v2.simulator` |
| Connection | One ConnectionId plus complete endpoint-a-to-b and endpoint-b-to-a parameters | approved Connection boundary | candidate, visibility record, unidirectional edge or backend operation | `skyseed_v2.simulator` |
| ConnectionSlice | Deeply immutable current effective Connections plus partial ZoneId→service-Satellite mapping | approved dynamic Slice boundary | candidate/evidence store, Position owner, history or independent Slice identity | `skyseed_v2.simulator` |
| GroundStationRouteTarget | Frozen tagged logical G target carrying a GroundStation NodeId | approved Route boundary | terminal next hop, service Satellite, interface or backend resource | `skyseed_v2.simulator` |
| ZoneRouteTarget | Frozen tagged logical Z target carrying a ZoneId | approved Route boundary | service Satellite, current Zone assignment or backend resource | `skyseed_v2.simulator` |
| RouteTarget | Public union of GroundStationRouteTarget and ZoneRouteTarget | approved Route boundary | arbitrary node endpoint, UT target or untagged string | `skyseed_v2.simulator` |
| RouteKey | Stable source NodeId plus logical RouteTarget | approved Route boundary | next hop, timestamp, full path, label or backend generation | `skyseed_v2.simulator` |
| RouteSlice | Deeply immutable sparse source→RouteTarget→next-hop table over the current Topology source universe | approved dynamic Slice boundary | full path, metric/candidate store, label table or independent Slice identity | `skyseed_v2.simulator` |
| TopologySlice | Deeply immutable synthetic-baseline or real-timestamp aggregate of positions, ConnectionSlice and RouteSlice | Slicer-published approved dynamic state | Topology owner/reference, full-path resolver, Model holder, previous-Slice chain, Planner or backend state | `skyseed_v2.simulator` |
| Slicer | Exact-Topology/Timeline/Model coordinator that validates and atomically publishes ordered TopologySlices and resolves retained-Slice paths; adjacent Difference creation remains approved F scope | Simulator-owned dynamic component | static Topology owner, Model algorithm, Planner, backend runner or arbitrary timestamp service | `skyseed_v2.simulator` |
| TopologyDifference | Independent immutable adjacent-Slice connection/service/route patch contract | fixed pure comparison invoked by Slicer | complete Slice, Position diff, old-value log, rollback Plan or Planner-owned state | `skyseed_v2.simulator` |
| TopologyDifferenceWindow | Deferred finite base-Slice plus ordered-adjacent-Difference planning input | future Planning boundary | implemented Slicer retention, current Slice contract or accepted Planner implementation | not implemented; deferred |

## Qualified-name rule

Do not introduce bare public names such as `Scenario`, `Catalog`, `Builder`,
`Population`, `Result`, `Context`, `Manager`, `Coordinator`, `Factory` or
`State`. Qualify an unavoidable operation type to one documented
responsibility.

The file-driven flow is three explicit operations:
`load_static_emulator_config(path)`, `create_static_emulator(config)` and
`create_simulator_static_input(base.get_skyseed_autonomous_systems())`, where
`base` is the `SatelliteBase` obtained from the returned Emulator. The
immutable input types do not read Emulator entities or SEED objects. Do not
reintroduce a stateless projection/conversion class or input classmethod.

`NodeId` is a pure cross-layer identity value, not an Emulator entity
attribute, lookup service or Simulator-private sequence number. Static input
maps it only to pure data and accepts only `NodeId` for node lookup. Topology's
unified private node index maps the same value to its own static domain nodes;
neither mapping exposes Emulator entities, services or containers. Any future
Emulator-side dynamic-execution resolution will resolve ASN through Base, then
call the actual AS/Shell/Orbit owners' narrow lookup methods. No plan or
runtime-resource resolution path exists yet.
`SkyseedDockerCompiler` is a separate implemented static-deployment stage and
does not compile dynamic execution plans.

Zone construction has one converged vocabulary and path:

```text
zones.yaml / programmatic construction
→ ZoneLayoutDefinition
→ Simulator.create_topology(...)
→ Topology-owned ZoneLayout
→ Zone[]
→ fixed ZoneMembershipModel
→ Topology facade
```

The YAML loader directly returns `ZoneLayoutDefinition`; it introduces no
file-only Zone type. Programmatic and YAML construction therefore converge
before Simulator materialization. Definition values are construction material
and are not retained by Topology. `Zone`, `ZoneLayout` and the Model types are
public only from `skyseed_v2.simulator`, not the package root.

The default authoritative rule is: all physical matches → maximum priority →
minimum haversine central angle to the rectangle's deterministic
representative center → lexicographically smallest `zone_id` within `1e-12`
radians. Higher priority wins and defaults to `0`. The representative center
is not a spherical area centroid, no Earth radius is required for a central
angle, and the tie tolerance does not change closed-boundary containment.
`allow_overlap=False` rejects only simultaneous positive latitude and
longitude overlap; shared edges, corners, poles and date-line boundaries have
zero area even though their points may have multiple physical memberships.

## Simulator implementation and approved/deferred vocabulary

Implemented and implemented-and-accepted rows describe accepted current code.
Accepted/not-implemented rows freeze the next public contracts without
claiming exports or implementation. Conceptual/open rows preserve only
deferred Planning/backend design space.

| Concept or relationship | Status | Meaning at this baseline |
| --- | --- | --- |
| `NodeId`, `SatelliteSimulationInput`, `GroundStationSimulationInput`, `SimulatorStaticInput`, Simulator input-generation functions | **implemented** | Current immutable Emulator-to-Simulator boundary foundation; input values are construction material, not the long-lived Simu domain model |
| Static chain: Emulator → `SkyseedDockerCompiler` → Docker/Compose → `ComposeProjectLauncher` | **implemented** | Static deployment and materialization lifecycle |
| Emulator entities → input generation → immutable boundary data → Simulator construction → Simulator-owned static domain objects | **implemented in accepted/frozen Iteration 1** | Boundary input materializes a separate immutable Simu graph; it does not grow into one all-purpose input aggregate |
| `Simulator` owns its Definition and explicitly creates one `Topology`; its Timeline is retained by the Definition | **implemented in accepted/frozen Iteration 1; A–E extension implemented and accepted** | Simulator is the composition root and facade and explicitly creates at most one Slicer after Topology |
| `Topology` owns/manages Simu AS, Shell, Orbit, Satellite and GroundStation objects | **implemented in accepted/frozen Iteration 1** | Topology is the immutable static-network aggregate and thin owner-directed query facade, not a central database |
| `SimulationTimeline` A–E semantics | **implemented and accepted** | Aware-only, UTC-normalized frozen time rules with lazy bounded/unbounded iteration; it creates no Slice |
| `ZoneLayoutDefinition` → atomic Topology materialization → optional Topology-owned `ZoneLayout` | **implemented in Iteration 2** | A valid Topology may omit the layout; when present, the layout solely owns its Zone tuple/index and fixed membership Model |
| physical membership versus authoritative Zone | **implemented in Iteration 2** | `find_zones` retains all closed-boundary matches; `resolve_zone` returns exactly one match selected by the active Model |
| Simulator → explicit Slicer → PositionModel → ConnectionModel → RouteModel → TopologySlice | **implemented and accepted** | Slicer, not Topology, owns ordered dynamic generation and atomic publication using exact fixed Model instances |
| EarthFixedPosition, Connection/Route values and synthetic Slice 0 | **implemented and accepted** | Frozen deeply immutable dynamic result contracts |
| adjacent TopologyDifference | **accepted/frozen design; not implemented** | Difference excludes Position changes and full Slices and remains engineering group F |
| Timeline UTC normalization, Orbit epoch, GroundStation ellipsoidal height and SphericalCap Zone query | **implemented and accepted Architecture Delta** | A–E applies the accepted time/epoch/Position and Zone-range corrections |
| `TopologyDifferenceWindow` → future Planner | **approved future direction; deferred** | Planner consumes a finite validated Difference window; Topology does not plan and Slicer does not own Planning policy |
| Dynamic chain: `BackendExecutionPlan` → `ExecutionCompiler` → `ExecutableBundle` → `SimulationRunner` | **approved direction** | Lowering and execution remain separate from both planning and the implemented static deployment chain |
| Planner/Plan types, DifferenceWindow implementation, backend plans, `ExecutionCompiler`, `ExecutableBundle`, `SimulationRunner` | **conceptual/open** | None is implemented; current approval freezes no detailed Planner/backend public schema |
| Position, Connection, Route, Slicer and TopologySlice implementation | **implemented and accepted** | A–E is accepted; F remains Difference and G remains Dynamic Tool/final synchronization, both unimplemented, so the full Dynamic Slice stage is not complete |
| `SimulationPlanCompiler` as the one future dynamic master stage | **retired/corrected** | Over-compressed semantic planning, backend planning and execution compilation |

Earlier candidate records used `SemanticTransitionPlan` for backend-neutral
meaning and `BackendExecutionPlan` for one backend's operations, dependencies,
batching, barriers, failure and cleanup policy. That decomposition is retained
as provenance, not a frozen Planner/Plan schema. `ExecutionCompiler` remains a
conceptual label for lowering a future approved backend plan; its final public
name is not decided. `SimulationRunner` is the qualified conceptual label for
the dynamic executor; current architecture records do not use an unqualified
`Runner`, `EmulatorRunner` or a shared Compiler/Runner family.
