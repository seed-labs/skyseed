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
| load_static_emulator_config | Direct function that validates versioned YAML and returns `StaticEmulatorConfig` | `configuration/yaml.py` | entity factory, Compiler or Registry reader | `skyseed_v2.configuration` |
| create_static_emulator | Direct function that creates Emulator entities and routing layers from Config | `static_emulator.py` | Simulator-input generation, Compiler or required handwritten facade | `skyseed_v2.static_emulator` |
| StaticEmulatorCreationResult | Frozen three-field result containing Emulator, Skyseed ASes and ordinary ASes | `static_emulator.py` | entity parent, Simulator input or root convenience API | `skyseed_v2.static_emulator` |
| SatelliteSimulationInput | Frozen static scalar data combined from one Satellite and its owning Orbit parameters | `simulator_input/satellite.py` | Satellite entity, SEED Node, runtime position or execution target | `skyseed_v2.simulator_input` |
| GroundStationSimulationInput | Frozen static scalar data copied from one GroundStation | `simulator_input/ground_station.py` | GroundStation entity, SEED Router or execution target | `skyseed_v2.simulator_input` |
| SimulatorStaticInput | Frozen deterministic aggregate of supported static inputs keyed by `SimulationNodeId` | `simulator_input/static_input.py` | AS traversal, Config, Registry, container inventory or runtime model | `skyseed_v2.simulator_input` |
| SimulationNodeId | Stable logical identity shared by static Simulator inputs | `simulator_input/identity.py` | Registry, container, namespace or execution-target identity | `skyseed_v2.simulator_input` |
| Simulator static-input generation | Three direct functions that traverse approved entities, copy values and assemble `SimulatorStaticInput` | `simulator_input_generation.py` | Projector, Builder, input classmethod or Simulator behavior | `skyseed_v2.simulator_input_generation` |
| Compiler | SEED integration that lowers a rendered Emulator into Compose output | `seedemu_integration/skyseed_docker_compiler.py` | entity creator, Simulator execution compiler or materializer | `skyseed_v2.seedemu_integration` |
| Launcher | Tool that materializes an already compiled Compose project | `materialization/compose_project_launcher.py` | entity service, Compiler, Runner or Simulator | `skyseed_v2.materialization` |
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
