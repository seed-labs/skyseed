# Skyseed v2 Terminology

This glossary is normative for public names and current architecture records.
A type can be public from its owning package without being re-exported from the
package root.

| Term | Unique meaning | Layer | Must not mean | Real type(s) | Public exposure |
| --- | --- | --- | --- | --- | --- |
| Shell | Emulator entity owned by one `SkyseedAutonomousSystem`; owns Orbits | entities | constellation, population or input record | `Shell` | root and `skyseed_v2.entities` |
| Orbit | Emulator entity owned by one Shell; owns Satellites | entities | generated input record or runtime trajectory | `Orbit` | root and `skyseed_v2.entities` |
| Satellite | The single satellite entity and the same SEED Node instance | entities + SEED capability | Simulator input, container, Registry entry or specification | `Satellite` | root and `skyseed_v2.entities` |
| GroundStation | The single ground-station entity and the same SEED Router instance | entities + SEED capability | Simulator input, ordinary Router, service or specification | `GroundStation` | root and `skyseed_v2.entities` |
| Specification | Frozen, backend-independent construction input; use a qualified `*Specification` name | input specification | entity, runtime state or Simulator input | classes in `skyseed_v2.specifications` | owning package |
| Loader | YAML-to-specification serialization adapter; use the complete name | serialization | entity factory, Applier, Compiler or Registry loader | `StaticEmulatorScenarioLoader` | `skyseed_v2.serialization` |
| Applier | Optional application service that applies one complete static specification through public create methods | application | entity, required facade for handwritten callers or lifecycle owner | `StaticEmulatorSpecificationApplier` | `skyseed_v2.application` |
| SatelliteSimulationInput | Frozen static scalar data copied from one Satellite for future Simulator use | Emulator → Simulator input | Satellite entity, SEED Node, runtime position or execution target | `SatelliteSimulationInput` | root and `skyseed_v2.simulator_input` |
| GroundStationSimulationInput | Frozen static scalar data copied from one GroundStation for future Simulator use | Emulator → Simulator input | GroundStation entity, SEED Router or execution target | `GroundStationSimulationInput` | root and `skyseed_v2.simulator_input` |
| SimulatorStaticInput | Frozen deterministic aggregate of all supported static entity inputs keyed by `SimulationNodeId` | Emulator → Simulator input | scenario, Registry, container inventory, persistence format or runtime model | `SimulatorStaticInput` | root and `skyseed_v2.simulator_input` |
| SimulationNodeId | Stable logical identity shared by static Simulator inputs | boundary value object | Registry, container, namespace or execution-target identity | `SimulationNodeId` | root and `skyseed_v2.simulator_input` |
| Compiler | SEED integration that lowers a rendered Emulator into Compose output | integration | entity creator, Simulator execution compiler or materializer | `SkyseedDockerCompiler` | `skyseed_v2.seedemu_integration` |
| Launcher | Tool that materializes an already compiled Compose project | materialization integration | entity service, Compiler, Runner or Simulator | `ComposeProjectLauncher` | `skyseed_v2.materialization` |
| ApplyResult | Flat result of applying one static scenario specification | operation result | entity parent, Simulator input member or root convenience API | `StaticEmulatorApplyResult` | `skyseed_v2.application` only |
| SkyseedAutonomousSystem | Owner of Shells and GroundStations plus the narrow SEED lifecycle extension | entity owner + integration | scenario facade or Simulator input owner | `SkyseedAutonomousSystem` | `skyseed_v2.seedemu_integration` |
| SatelliteBase | SEED Base Layer integration for the Satellite lifecycle | integration | Emulator entity or scenario | `SatelliteBase` | `skyseed_v2.seedemu_integration` |

## Qualified-name rule

Do not introduce bare public names such as `Scenario`, `Catalog`, `Builder`,
`Population`, `Result`, `Context`, `Manager`, `Coordinator`, `Factory` or
`State`. Qualify an unavoidable operation type to one documented
responsibility. `StaticEmulatorApplyResult` remains public only because one
application operation truthfully returns four independently owned values.

Static input conversion is expressed by classmethods on the immutable input
types. Do not reintroduce a stateless projection class or a copying-only
conversion service.
