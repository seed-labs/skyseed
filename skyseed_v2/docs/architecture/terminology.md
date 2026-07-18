# Skyseed v2 Terminology

This glossary is normative for public names and architecture records. A type
can be public from its owning submodule without being re-exported from the
package root.

| Term | Unique meaning | Layer | Must not mean | Real type(s) | Public exposure |
| --- | --- | --- | --- | --- | --- |
| Shell | Domain entity owned by one `SkyseedAutonomousSystem`; owns Orbits | domain | constellation, population or input record | `Shell` | root |
| Orbit | Domain entity owned by one Shell; owns Satellites | domain | generated orbit record or runtime trajectory | `Orbit` | root |
| Satellite | The single domain entity for one satellite and the same SEED Node instance | domain + SEED capability | Descriptor, container, Registry entry or specification | `Satellite` | root |
| GroundStation | The single domain entity for one ground station and the same SEED Router instance | domain + SEED capability | Descriptor, ordinary Router, service or specification | `GroundStation` | root |
| Specification | Frozen, backend-independent construction input; a qualified `*Specification` name is required | input specification | entity, runtime state, Descriptor or Compiler input artifact | classes in `skyseed_v2.specifications` | owning submodule |
| Loader | File-to-specification serialization adapter; use the complete name | serialization adapter | entity factory, Applier, Compiler or Registry loader | `StaticEmulatorScenarioLoader` | owning submodule |
| Applier | Optional application service that applies one complete static specification through public create methods | application | domain entity, facade required by handwritten callers or lifecycle owner | `StaticEmulatorSpecificationApplier` | owning submodule |
| Descriptor | Frozen cross-layer read-only projection of one approved entity | cross-layer contract | entity, SEED Node, Registry identity or execution target | `SatelliteDescriptor`, `GroundStationDescriptor` | root |
| SimulationEntityCatalog | Frozen deterministic collection of approved Descriptors keyed by `SimulationNodeId` | cross-layer contract | scenario, container catalog, Registry or persistence format | `SimulationEntityCatalog` | root |
| Projector | Stateless application service that copies approved entity scalars into one Descriptor | application | entity factory, serializer or Compiler | `SatelliteDescriptorProjector`, `GroundStationDescriptorProjector` | owning submodule |
| Compiler | SEED integration that lowers a rendered Emulator into Compose output | integration | entity creator, Simulator execution compiler or materializer | `SkyseedDockerCompiler` | `skyseed_v2.seed` |
| Launcher | Integration tool that materializes an already-compiled Compose project | materialization integration | domain service, Compiler, Runner or Simulator | `ComposeProjectLauncher` | `skyseed_v2.materialization` |
| ApplyResult / BuildResult | `StaticEmulatorApplyResult` is the flat operation result of specification application; `BuildResult` is retired | operation result | entity parent, Catalog entry or root convenience API | `StaticEmulatorApplyResult` | owning submodule only |
| SkyseedAutonomousSystem | Approved owner of Shells and GroundStations plus the narrow SEED lifecycle extension | domain owner + integration | scenario facade or Descriptor factory | `SkyseedAutonomousSystem` | `skyseed_v2.seed` |
| SimulationNodeId | Stable logical cross-layer identity for a Descriptor | value object / cross-layer contract | Registry, container, namespace or execution target identity | `SimulationNodeId` | root |
| SatelliteBase | SEED Base Layer integration for the Satellite lifecycle | integration | Emulator domain entity or scenario | `SatelliteBase` | `skyseed_v2.seed` |

## Qualified-name rule

Do not introduce bare public names such as `Scenario`, `Catalog`, `Builder`,
`Population`, `Result`, `Context`, `Manager`, `Coordinator` or `State`. If one
is unavoidable, qualify it to one documented responsibility, for example
`StaticEmulatorScenarioSpecification`, `SimulationEntityCatalog`,
`SimulationEntityCatalogBuilder` or `StaticEmulatorApplyResult`.

`SimulationEntityCatalogBuilder` retains `Builder` because its sole output is
the approved immutable Catalog and its input/output boundary is already
public. It creates no entity. `StaticEmulatorApplyResult` retains `Result`
because it is explicitly an operation result and remains outside the root API.

