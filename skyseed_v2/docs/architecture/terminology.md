# Skyseed v2 Terminology

This glossary is normative for public names and current architecture records.
A type can be public from its owning module without being re-exported from the
package root.

| Term | Unique meaning | Owner | Must not mean | Public exposure |
| --- | --- | --- | --- | --- |
| Shell | Emulator entity owned by one `SkyseedAutonomousSystem`; owns Orbits | `entities/shell.py` | constellation, population or input record | root and `skyseed_v2.entities` |
| Orbit | Emulator entity owned by one Shell; owns one `OrbitParameters` value and its Satellites | `entities/orbit.py` | generated input record or runtime trajectory | root and `skyseed_v2.entities` |
| OrbitParameters | Frozen altitude, inclination and right ascension shared by all Satellites in one Orbit | `entities/orbit.py` | Satellite field, dynamic position or trajectory | root and `skyseed_v2.entities` |
| Satellite | The single Satellite entity and the same SEED Node instance; owns only per-Satellite values | `entities/satellite.py` | Orbit owner, Simulator input, container or Config | root and `skyseed_v2.entities` |
| GroundStation | The single GroundStation entity and the same SEED Router instance | `entities/ground_station.py` | Simulator input, ordinary Router, service or Config | root and `skyseed_v2.entities` |
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
maps it only to pure data. No unified `NodeId → entity/service/container`
mapping exists. Any future dynamic-execution resolution will resolve ASN
through Base, then call the actual AS/Shell/Orbit owners' narrow lookup
methods. No plan, resolution path or Simulator runtime type exists yet.
`SkyseedDockerCompiler` is a separate implemented static-deployment stage and
does not compile dynamic execution plans.

## Simulator future conceptual vocabulary

This section is non-normative for public names. It preserves the design space
and status needed for later vertical slices; it does not add terms to the
current public API.

| Concept or relationship | Status | Meaning at this baseline |
| --- | --- | --- |
| `NodeId`, `SatelliteSimulationInput`, `GroundStationSimulationInput`, `SimulatorStaticInput`, Simulator input-generation functions | **implemented** | Current immutable Emulator-to-Simulator boundary foundation; input values are construction material, not the long-lived Simu domain model |
| Static chain: Emulator → `SkyseedDockerCompiler` → Docker/Compose → `ComposeProjectLauncher` | **implemented** | Static deployment and materialization lifecycle |
| Emulator entities → input generation → immutable boundary data → Simulator construction → Simulator-owned domain objects | **approved direction** | Boundary input materializes a separate Simu domain graph; it does not grow into one all-purpose input aggregate |
| `Simulator` owns `SimulationTimeline`, `Topology` and `Planning` | **approved direction** | Simulator is the Simu composition root and facade; these three peer subsystems are its domain state |
| `Topology` owns/manages Simu Shell, Orbit, Satellite, GroundStation, future Zone definitions, future Slice states and internal indexes | **approved direction** | Topology is the static-network and dynamic-topology aggregate root plus a thin query/comparison facade, not a mega-class |
| `Topology` produces authoritative state; `Planning` consumes public results and plans transitions | **approved direction** | Topology never depends on Planning; Planning does not own or modify Topology data and may query only a read-only Topology facade |
| `TopologyDifference` → `SemanticPlanner` → `SemanticTransitionPlan` → `BackendPlanner` → `BackendExecutionPlan` | **approved direction** | Backend-neutral meaning and backend-specific execution strategy are separate planning levels |
| Dynamic chain: `BackendExecutionPlan` → `ExecutionCompiler` → `ExecutableBundle` → `SimulationRunner` | **approved direction** | Lowering and execution remain separate from both planning and the implemented static deployment chain |
| `Simulator`, `SimulationTimeline`, `Topology`, `Planning`, topology domain objects/slices/difference helpers, both Planner levels, backend plans, `ExecutionCompiler`, `ExecutableBundle`, `SimulationRunner` | **conceptual/open** | None is implemented; the approved arrows do not freeze final names, types, fields, visibility or module placement |
| Input renaming, separate Simu entity types, precise time/slice/state contracts, Topology query return forms, backend set, executable bundle and continuing-run semantics | **conceptual/open** | Must be investigated and approved by the relevant vertical slice |
| `SimulationPlanCompiler` as the one future dynamic master stage | **retired/corrected** | Over-compressed semantic planning, backend planning and execution compilation |

`SemanticTransitionPlan` describes only backend-neutral network meaning.
`BackendExecutionPlan` describes one backend's operations, dependencies,
batching, barriers, failure and cleanup policy. The two candidate plan forms do
not share a required schema or parent. `ExecutionCompiler` is only a
conceptual label for lowering an approved backend plan; its final public name
is not decided. `SimulationRunner` is the qualified conceptual label for the
dynamic executor; current architecture records do not use an unqualified
`Runner`, `EmulatorRunner` or a shared Compiler/Runner family.
