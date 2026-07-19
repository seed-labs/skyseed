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
| StaticEmulatorConfig | Frozen, backend-independent top-level file or programmatic configuration | `configuration/static_emulator.py` | Emulator entity, runtime state or Simulator input | `skyseed_v2.configuration` |
| load_static_emulator_config | Direct function that validates versioned YAML and returns `StaticEmulatorConfig` | `configuration/yaml.py` | entity factory, `SkyseedDockerCompiler` or Registry reader | `skyseed_v2.configuration` |
| create_static_emulator | Direct function that creates Emulator entities and routing layers from Config | `static_emulator_creation.py` | Simulator-input generation, `SkyseedDockerCompiler` or required handwritten facade | `skyseed_v2.static_emulator_creation` |
| StaticEmulatorCreationResult | Frozen three-field result containing Emulator, Skyseed ASes and ordinary ASes | `static_emulator_creation.py` | entity parent, Simulator input or root convenience API | `skyseed_v2.static_emulator_creation` |
| SatelliteSimulationInput | Simulator-owned frozen static scalar data combined from one Satellite and its owning Orbit parameters | `simulator/static_input/satellite.py` | Satellite entity, SEED Node, runtime position or execution target | `skyseed_v2.simulator.static_input` |
| GroundStationSimulationInput | Simulator-owned frozen static scalar data copied from one GroundStation | `simulator/static_input/ground_station.py` | GroundStation entity, SEED Router or execution target | `skyseed_v2.simulator.static_input` |
| SimulatorStaticInput | Simulator-owned frozen deterministic aggregate of pure static inputs keyed by `NodeId` | `simulator/static_input/static_input.py` | AS traversal, Config, entity/resource index, Registry, container inventory or runtime model | `skyseed_v2.simulator.static_input` |
| NodeId | Pure stable logical node identity shared across Emulator packaging, Simulator data/state/plans and future dynamic-execution owner-directed lookup | `node_id.py` | entity property, lookup service, Registry key, container ID, Compose service, namespace, interface or Simulator-local number | `skyseed_v2.node_id` |
| Simulator static-input generation | Three direct functions sharing one private NodeId derivation to copy supported entities into pure inputs | `simulator_input_generation.py` | entity index, Projector, Builder, Resolver class, input classmethod or Simulator behavior | `skyseed_v2.simulator_input_generation` |
| SkyseedDockerCompiler | Implemented SEED integration that lowers a rendered Emulator/SEED static model into Dockerfile and Compose deployment configuration | `seedemu_integration/skyseed_docker_compiler.py` | entity creator, dynamic execution compiler, runtime updater or materializer | `skyseed_v2.seedemu_integration` |
| SimulationPlanCompiler | **Retired/corrected** shorthand for the over-compressed `abstract Simulation Plan → compiler → concrete dynamic operations` description; retained only to explain the correction | not implemented | frozen future public contract, semantic Planner, BackendPlanner, conceptual `ExecutionCompiler`, `SkyseedDockerCompiler` or shared compiler parent | not implemented |
| Launcher | Tool that materializes an already compiled Compose project | `materialization/compose_project_launcher.py` | entity service, either compilation stage, Runner or Simulator | `skyseed_v2.materialization` |
| SkyseedAutonomousSystem | Owner of Shells and GroundStations plus the narrow SEED lifecycle extension | `seedemu_integration/skyseed_autonomous_system.py` | file facade or Simulator-input owner | `skyseed_v2.seedemu_integration` |
| SatelliteBase | SEED Base Layer integration for the Satellite lifecycle | `seedemu_integration/satellite_base.py` | Emulator entity or file configuration | `skyseed_v2.seedemu_integration` |

## Qualified-name rule

Do not introduce bare public names such as `Scenario`, `Catalog`, `Builder`,
`Population`, `Result`, `Context`, `Manager`, `Coordinator`, `Factory` or
`State`. Qualify an unavoidable operation type to one documented
responsibility.

The file-driven flow is three explicit operations:
`load_static_emulator_config(path)`, `create_static_emulator(config)` and
`create_simulator_static_input(creation.skyseed_autonomous_systems)`. The
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
| `NodeId`, `SatelliteSimulationInput`, `GroundStationSimulationInput`, `SimulatorStaticInput`, Simulator input-generation functions | **implemented** | Current immutable static-input foundation |
| Static chain: Emulator → `SkyseedDockerCompiler` → Docker/Compose → `ComposeProjectLauncher` | **implemented** | Static deployment and materialization lifecycle |
| `Simulator` and `Topology` as coordination shells; Zone independent from Topology | **approved direction** | Responsibility/ownership direction, not a class/API freeze |
| Semantic planning → backend planning → executable-form lowering → Runner | **approved direction** | Separate network meaning, backend strategy, lowering and execution stages |
| `Simulator`, `SimulationTimeline`, `ZoneModel`, `Topology`, `Planning`, topology models/slices/store/differ, both Planner levels, backend planners/plans, `ExecutionCompiler`, `ExecutableBundle`, `Runner`, `Observation` | **conceptual candidate** | Names and objects available for later slice design; none is implemented |
| Final public names, visibility, module placement, slice/time/state contracts, backend set and Runner/materialization relationship | **open question** | Must be designed and approved per vertical slice |
| `SimulationPlanCompiler` as the one future dynamic master stage | **retired/corrected** | Over-compressed semantic planning, backend planning and execution compilation |

`SemanticTransitionPlan` describes only backend-neutral network meaning.
`BackendExecutionPlan` describes one backend's operations, dependencies,
batching, barriers, failure and cleanup policy. The two candidate plan forms do
not share a required schema or parent. `ExecutionCompiler` is only a
conceptual label for lowering an approved backend plan; its final public name
is not decided.
