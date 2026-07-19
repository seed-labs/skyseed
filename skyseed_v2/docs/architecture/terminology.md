# Skyseed v2 Terminology

This glossary is normative for public names and current architecture records.
A type can be public from its owning module without being re-exported from the
package root.

| Term | Unique meaning | Owner | Must not mean | Public exposure |
| --- | --- | --- | --- | --- |
| Shell | Emulator-side entity owned by one Emulator-side `SkyseedAutonomousSystem`; owns Emulator Orbits | `entities/shell.py` | Simulator Topology Shell, constellation, population or input record | root and `skyseed_v2.entities` |
| Orbit | Emulator-side entity owned by one Emulator Shell; owns one `OrbitParameters` value and its Emulator Satellites | `entities/orbit.py` | Simulator Topology Orbit, generated input record or runtime trajectory | root and `skyseed_v2.entities` |
| OrbitParameters | Frozen altitude, inclination and right ascension shared by all Satellites in one Orbit | `entities/orbit.py` | Satellite field, dynamic position or trajectory | root and `skyseed_v2.entities` |
| Satellite | Emulator-side Satellite entity and the same SEED Node instance; owns only per-Satellite values | `entities/satellite.py` | Simulator Topology Satellite, Orbit owner, Simulator input, container or Config | root and `skyseed_v2.entities` |
| GroundStation | Emulator-side GroundStation entity and the same SEED Router instance | `entities/ground_station.py` | Simulator Topology GroundStation, Simulator input, ordinary Router, service or Config | root and `skyseed_v2.entities` |
| StaticEmulatorConfig | Frozen, backend-independent top-level file or programmatic configuration | `configuration/static_emulator_config.py` | Emulator entity, runtime state or Simulator input | `skyseed_v2.configuration` |
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
| SimulationTimeline | Frozen explicit time-range value with lazy bounded or unbounded timestamp iteration | `simulator/timeline.py` | clock, timestamp cache, Slice store or pacing service | `skyseed_v2.simulator` |
| SimulationDefinition | Frozen complete construction input pairing `static_input` and `timeline` | `simulator/definition.py` | mutable configuration bag, Emulator input or runtime context | `skyseed_v2.simulator` |
| Simulator | Slice 1 Simu composition root and facade consuming one `SimulationDefinition` and atomically creating one Topology | `simulator/simulator.py` | Emulator wrapper, adapter, planner, compiler or runner | `skyseed_v2.simulator` |
| Topology | Immutable Simulator static-network aggregate root and narrow query facade | `simulator/topology.py` | boundary DTO, global database, dynamic Slice, Planning service or Emulator owner | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.SkyseedAutonomousSystem` | Simulator-owned immutable AS node; owns Simulator Shells and GroundStations | `simulator/topology.py` | Emulator/SEED AS, adapter or Registry | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.Shell` | Simulator-owned immutable Shell; owns Simulator Orbits | `simulator/topology.py` | Emulator Shell, input record or constellation | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.Orbit` | Simulator-owned immutable Orbit; owns shared orbit parameters and Simulator Satellites | `simulator/topology.py` | Emulator Orbit, input record, position or trajectory | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.Satellite` | Simulator-owned immutable static Satellite beneath one Orbit | `simulator/topology.py` | SEED Node, Emulator Satellite, position or runtime resource | `skyseed_v2.simulator` |
| `skyseed_v2.simulator.GroundStation` | Simulator-owned immutable static GroundStation beneath one Simulator AS | `simulator/topology.py` | SEED Router, Emulator GroundStation or runtime resource | `skyseed_v2.simulator` |
| NodeId | Pure stable logical node identity shared across Emulator packaging, Simulator data/state/plans and future dynamic-execution owner-directed lookup | `node_id.py` | entity property, lookup service, Registry key, container ID, Compose service, namespace, interface or Simulator-local number | `skyseed_v2.node_id` |
| Simulator static-input generation | Three direct functions sharing one private NodeId derivation to copy supported entities into pure inputs | `simulator_input_generation.py` | entity index, Projector, Builder, Resolver class, input classmethod or Simulator behavior | `skyseed_v2.simulator_input_generation` |
| SkyseedDockerCompiler | Implemented SEED integration that lowers a rendered Emulator/SEED static model into Dockerfile and Compose deployment configuration | `seedemu_integration/skyseed_docker_compiler.py` | entity creator, dynamic execution compiler, runtime updater or materializer | `skyseed_v2.seedemu_integration` |
| SimulationPlanCompiler | **Retired/corrected** shorthand for the over-compressed `abstract Simulation Plan → compiler → concrete dynamic operations` description; retained only to explain the correction | not implemented | frozen future public contract, semantic Planner, BackendPlanner, conceptual `ExecutionCompiler`, `SkyseedDockerCompiler` or shared compiler parent | not implemented |
| ComposeProjectLauncher | Implemented application/infrastructure service that launches and verifies an already compiled Compose project | `materialization/compose_project_launcher.py` | Emulator execution service, dynamic `SimulationRunner`, compiler, Simulator domain state or generic Launcher abstraction | `skyseed_v2.materialization` |
| SkyseedAutonomousSystem | Owner of Shells and GroundStations plus the narrow SEED lifecycle extension | `seedemu_integration/skyseed_autonomous_system.py` | file facade or Simulator-input owner | `skyseed_v2.seedemu_integration` |
| SatelliteBase | SEED Base Layer integration and real AS owner; provides the narrow Skyseed-AS collection query | `seedemu_integration/satellite_base.py` | Emulator entity, file configuration, global index or Simulator adapter | `skyseed_v2.seedemu_integration` |

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

## Simulator implementation and future conceptual vocabulary

Implemented rows below are normative through the public terms above. Future
rows preserve the design space for later vertical slices and do not add terms
to the current public API.

| Concept or relationship | Status | Meaning at this baseline |
| --- | --- | --- |
| `NodeId`, `SatelliteSimulationInput`, `GroundStationSimulationInput`, `SimulatorStaticInput`, Simulator input-generation functions | **implemented** | Current immutable Emulator-to-Simulator boundary foundation; input values are construction material, not the long-lived Simu domain model |
| Static chain: Emulator → `SkyseedDockerCompiler` → Docker/Compose → `ComposeProjectLauncher` | **implemented** | Static deployment and materialization lifecycle |
| Emulator entities → input generation → immutable boundary data → Simulator construction → Simulator-owned static domain objects | **implemented in Slice 1** | Boundary input materializes a separate immutable Simu graph; it does not grow into one all-purpose input aggregate |
| `Simulator` owns its Definition and explicitly creates one `Topology`; its Timeline is retained by the Definition | **implemented in Slice 1** | Simulator is the current composition root and facade; Planning is not present |
| `Topology` owns/manages Simu AS, Shell, Orbit, Satellite and GroundStation objects | **implemented in Slice 1** | Topology is the immutable static-network aggregate and thin owner-directed query facade, not a central database |
| `SimulationTimeline` | **implemented in Slice 1** | Explicit frozen time rules with lazy bounded/unbounded iteration; it creates no Slice |
| `Topology` produces authoritative state; `Planning` consumes public results and plans transitions | **approved direction** | Topology never depends on Planning; Planning does not own or modify Topology data and may query only a read-only Topology facade |
| `TopologyDifference` → `SemanticPlanner` → `SemanticTransitionPlan` → `BackendPlanner` → `BackendExecutionPlan` | **approved direction** | Backend-neutral meaning and backend-specific execution strategy are separate planning levels |
| Dynamic chain: `BackendExecutionPlan` → `ExecutionCompiler` → `ExecutableBundle` → `SimulationRunner` | **approved direction** | Lowering and execution remain separate from both planning and the implemented static deployment chain |
| `Planning`, topology slices/difference helpers, both Planner levels, backend plans, `ExecutionCompiler`, `ExecutableBundle`, `SimulationRunner` | **conceptual/open** | None is implemented; approved arrows do not freeze final names, types, fields, visibility or module placement |
| Position, precise Slice/state contracts, backend set, executable bundle and continuing-run coordination | **conceptual/open** | Must be investigated and approved by the relevant later vertical slice |
| `SimulationPlanCompiler` as the one future dynamic master stage | **retired/corrected** | Over-compressed semantic planning, backend planning and execution compilation |

`SemanticTransitionPlan` describes only backend-neutral network meaning.
`BackendExecutionPlan` describes one backend's operations, dependencies,
batching, barriers, failure and cleanup policy. The two candidate plan forms do
not share a required schema or parent. `ExecutionCompiler` is only a
conceptual label for lowering an approved backend plan; its final public name
is not decided. `SimulationRunner` is the qualified conceptual label for the
dynamic executor; current architecture records do not use an unqualified
`Runner`, `EmulatorRunner` or a shared Compiler/Runner family.
