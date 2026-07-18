# Current Public Import Inventory

Basis: declared `__all__` surfaces plus non-underscore top-level definitions in their defining modules at commit `0ebd3c6a69e50e8e2cfefb4229558bc213c16510`.
Counts are whole-symbol lexical occurrences, so repository counts include definitions and documentation, while the Examples/Tests columns are a practical impact signal rather than a runtime call count.

| Public name | Defining location | Root export | Declared/reachable import surfaces | Example imports | Test occurrences | Doc occurrences | Repository occurrences | Compatibility signal |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |
| `ComposeBatchResult` | `src/skyseed_v2/materialization/compose_project_launcher.py:95` (class) | no | `skyseed_v2.materialization`<br>`skyseed_v2.materialization.compose_project_launcher` | - | 0 | 0 | 8 | declared subpackage API; possible external use |
| `ComposeLaunchStrategy` | `src/skyseed_v2/materialization/compose_project_launcher.py:21` (class) | no | `skyseed_v2.materialization`<br>`skyseed_v2.materialization.compose_project_launcher` | - | 6 | 1 | 16 | declared subpackage API; possible external use |
| `ComposeMaterializationError` | `src/skyseed_v2/materialization/compose_project_launcher.py:135` (class) | no | `skyseed_v2.materialization`<br>`skyseed_v2.materialization.compose_project_launcher` | - | 12 | 1 | 38 | declared subpackage API; possible external use |
| `ComposeMaterializationResult` | `src/skyseed_v2/materialization/compose_project_launcher.py:111` (class) | no | `skyseed_v2.materialization`<br>`skyseed_v2.materialization.compose_project_launcher` | - | 0 | 0 | 5 | declared subpackage API; possible external use |
| `ComposeProjectLauncher` | `src/skyseed_v2/materialization/compose_project_launcher.py:163` (class) | no | `skyseed_v2.materialization`<br>`skyseed_v2.materialization.compose_project_launcher` | - | 7 | 13 | 27 | declared subpackage API; possible external use |
| `ComposeServiceExpectations` | `src/skyseed_v2/materialization/compose_project_launcher.py:63` (class) | no | `skyseed_v2.materialization`<br>`skyseed_v2.materialization.compose_project_launcher` | - | 6 | 0 | 12 | declared subpackage API; possible external use |
| `EbgpPeeringSpecification` | `src/skyseed_v2/specifications.py:644` (class) | no | `skyseed_v2.specifications` | - | 2 | 0 | 10 | declared module API; review is still pending |
| `EbgpRouterReferenceSpecification` | `src/skyseed_v2/specifications.py:625` (class) | no | `skyseed_v2.specifications` | - | 3 | 0 | 13 | declared module API; review is still pending |
| `GROUND_STATIONS_SCHEMA_VERSION` | `src/skyseed_v2/scenario_loader.py:35` (constant) | no | `skyseed_v2.scenario_loader` | - | 0 | 0 | 3 | declared module API; review is still pending |
| `GROUND_STATION_ENTITY_KIND` | `src/skyseed_v2/contracts/identity.py:11` (constant) | no | `skyseed_v2.contracts.identity` | - | 0 | 0 | 7 | direct defining-module path; no aggregate export |
| `GROUND_STATION_REGISTRY_TYPE` | `src/skyseed_v2/domain/ground_station.py:16` (constant) | no | `skyseed_v2.domain`<br>`skyseed_v2.domain.ground_station`<br>`skyseed_v2.seed` | - | 0 | 0 | 10 | declared subpackage API; possible external use |
| `GROUND_STATION_ROLE_LABEL` | `src/skyseed_v2/domain/ground_station.py:17` (constant) | no | `skyseed_v2.domain`<br>`skyseed_v2.domain.ground_station`<br>`skyseed_v2.seed` | - | 0 | 0 | 8 | declared subpackage API; possible external use |
| `GroundStation` | `src/skyseed_v2/domain/ground_station.py:51` (class) | yes | `skyseed_v2`<br>`skyseed_v2.domain`<br>`skyseed_v2.domain.ground_station` | - | 18 | 154 | 207 | root public API; highest migration impact |
| `GroundStationDescriptor` | `src/skyseed_v2/contracts/ground_station.py:11` (class) | yes | `skyseed_v2`<br>`skyseed_v2.contracts`<br>`skyseed_v2.contracts.ground_station` | - | 18 | 28 | 68 | root public API; highest migration impact |
| `GroundStationDescriptorProjector` | `src/skyseed_v2/projection.py:23` (class) | no | `skyseed_v2.projection` | - | 11 | 19 | 35 | declared module API; review is still pending |
| `GroundStationLocation` | `src/skyseed_v2/domain/ground_station.py:21` (class) | yes | `skyseed_v2`<br>`skyseed_v2.domain`<br>`skyseed_v2.domain.ground_station` | `examples/complete_static_emulator/main.py:11` from `skyseed_v2` | 15 | 6 | 36 | root public API; highest migration impact |
| `GroundStationSpecification` | `src/skyseed_v2/specifications.py:315` (class) | no | `skyseed_v2.specifications` | - | 7 | 0 | 20 | declared module API; review is still pending |
| `INTERNET_MAP_DOCKERFILE` | `src/skyseed_v2/seed/compiler.py:64` (constant) | no | `skyseed_v2.seed.compiler` | - | 0 | 0 | 2 | direct defining-module path; no aggregate export |
| `INTERNET_MAP_SERVICE` | `src/skyseed_v2/seed/compiler.py:54` (constant) | no | `skyseed_v2.seed.compiler` | - | 0 | 0 | 2 | direct defining-module path; no aggregate export |
| `InOrbitPhaseDistributionSpecification` | `src/skyseed_v2/specifications.py:114` (class) | no | `skyseed_v2.specifications` | - | 5 | 0 | 13 | declared module API; review is still pending |
| `InternetExchangeAttachmentSpecification` | `src/skyseed_v2/specifications.py:400` (class) | no | `skyseed_v2.specifications` | - | 3 | 0 | 15 | declared module API; review is still pending |
| `InternetExchangeSpecification` | `src/skyseed_v2/specifications.py:440` (class) | no | `skyseed_v2.specifications` | - | 2 | 0 | 10 | declared module API; review is still pending |
| `LocalNetworkAttachmentSpecification` | `src/skyseed_v2/specifications.py:419` (class) | no | `skyseed_v2.specifications` | - | 2 | 0 | 10 | declared module API; review is still pending |
| `NO_NETWORK_COMPOSE_SERVICE` | `src/skyseed_v2/seed/compiler.py:36` (constant) | no | `skyseed_v2.seed.compiler` | - | 0 | 0 | 2 | direct defining-module path; no aggregate export |
| `Orbit` | `src/skyseed_v2/domain/satellite.py:191` (class) | yes | `skyseed_v2`<br>`skyseed_v2.domain`<br>`skyseed_v2.domain.satellite` | - | 7 | 97 | 134 | root public API; highest migration impact |
| `OrbitGenerationSpecification` | `src/skyseed_v2/specifications.py:144` (class) | no | `skyseed_v2.specifications` | - | 8 | 0 | 17 | declared module API; review is still pending |
| `OrbitParameters` | `src/skyseed_v2/domain/satellite.py:41` (class) | yes | `skyseed_v2`<br>`skyseed_v2.domain`<br>`skyseed_v2.domain.satellite` | `examples/complete_static_emulator/main.py:11` from `skyseed_v2`<br>`examples/isolated_satellites/main.py:10` from `skyseed_v2` | 23 | 7 | 51 | root public API; highest migration impact |
| `OrbitSpecification` | `src/skyseed_v2/specifications.py:236` (class) | no | `skyseed_v2.specifications` | - | 7 | 0 | 15 | declared module API; review is still pending |
| `OrdinaryAutonomousSystemSpecification` | `src/skyseed_v2/specifications.py:534` (class) | no | `skyseed_v2.specifications` | - | 2 | 0 | 12 | declared module API; review is still pending |
| `OrdinaryNetworkSpecification` | `src/skyseed_v2/specifications.py:460` (class) | no | `skyseed_v2.specifications` | - | 2 | 0 | 10 | declared module API; review is still pending |
| `OrdinaryRouterSpecification` | `src/skyseed_v2/specifications.py:479` (class) | no | `skyseed_v2.specifications` | - | 2 | 0 | 10 | declared module API; review is still pending |
| `RaanDistributionSpecification` | `src/skyseed_v2/specifications.py:93` (class) | no | `skyseed_v2.specifications` | - | 3 | 0 | 11 | declared module API; review is still pending |
| `SATELLITE_ENTITY_KIND` | `src/skyseed_v2/contracts/identity.py:10` (constant) | no | `skyseed_v2.contracts.identity` | - | 0 | 0 | 7 | direct defining-module path; no aggregate export |
| `SATELLITE_REGISTRY_TYPE` | `src/skyseed_v2/domain/satellite.py:16` (constant) | no | `skyseed_v2.domain`<br>`skyseed_v2.domain.satellite`<br>`skyseed_v2.seed` | - | 10 | 0 | 25 | declared subpackage API; possible external use |
| `SATELLITE_ROLE_LABEL` | `src/skyseed_v2/domain/satellite.py:17` (constant) | no | `skyseed_v2.domain`<br>`skyseed_v2.domain.satellite`<br>`skyseed_v2.seed` | - | 0 | 0 | 9 | declared subpackage API; possible external use |
| `SCENARIO_SCHEMA_VERSION` | `src/skyseed_v2/scenario_loader.py:34` (constant) | no | `skyseed_v2.scenario_loader` | - | 2 | 0 | 5 | declared module API; review is still pending |
| `SEED_METADATA_PREFIX` | `src/skyseed_v2/seed/metadata.py:6` (constant) | no | `skyseed_v2.seed.metadata` | - | 2 | 0 | 6 | direct defining-module path; no aggregate export |
| `SKYSEED_METADATA_PREFIX` | `src/skyseed_v2/seed/metadata.py:7` (constant) | no | `skyseed_v2.seed.metadata` | - | 2 | 0 | 4 | direct defining-module path; no aggregate export |
| `Satellite` | `src/skyseed_v2/domain/satellite.py:69` (class) | yes | `skyseed_v2`<br>`skyseed_v2.domain`<br>`skyseed_v2.domain.satellite` | - | 16 | 297 | 378 | root public API; highest migration impact |
| `SatelliteBase` | `src/skyseed_v2/seed/base.py:11` (class) | no | `skyseed_v2.seed`<br>`skyseed_v2.seed.base` | `examples/complete_static_emulator/main.py:17` from `skyseed_v2.seed`<br>`examples/isolated_satellites/main.py:11` from `skyseed_v2.seed` | 12 | 22 | 46 | declared subpackage API; possible external use |
| `SatelliteDescriptor` | `src/skyseed_v2/contracts/satellite.py:11` (class) | yes | `skyseed_v2`<br>`skyseed_v2.contracts`<br>`skyseed_v2.contracts.satellite` | - | 18 | 30 | 70 | root public API; highest migration impact |
| `SatelliteDescriptorProjector` | `src/skyseed_v2/projection.py:51` (class) | no | `skyseed_v2.projection` | - | 11 | 23 | 39 | declared module API; review is still pending |
| `SatelliteRole` | `src/skyseed_v2/domain/satellite.py:20` (class) | no | `skyseed_v2.domain`<br>`skyseed_v2.domain.satellite` | - | 2 | 2 | 8 | declared subpackage API; possible external use |
| `SatelliteSpecification` | `src/skyseed_v2/specifications.py:194` (class) | no | `skyseed_v2.specifications` | - | 8 | 0 | 16 | declared module API; review is still pending |
| `Shell` | `src/skyseed_v2/domain/satellite.py:326` (class) | yes | `skyseed_v2`<br>`skyseed_v2.domain`<br>`skyseed_v2.domain.satellite` | - | 16 | 75 | 119 | root public API; highest migration impact |
| `ShellSpecification` | `src/skyseed_v2/specifications.py:272` (class) | no | `skyseed_v2.specifications` | - | 9 | 1 | 20 | declared module API; review is still pending |
| `SimulationEntityCatalog` | `src/skyseed_v2/contracts/catalog.py:22` (class) | yes | `skyseed_v2`<br>`skyseed_v2.contracts`<br>`skyseed_v2.contracts.catalog` | `examples/complete_static_emulator/main.py:11` from `skyseed_v2` | 18 | 39 | 72 | root public API; highest migration impact |
| `SimulationEntityCatalogBuilder` | `src/skyseed_v2/catalog_builder.py:20` (class) | no | `skyseed_v2.catalog_builder` | `examples/complete_static_emulator/main.py:16` from `skyseed_v2.catalog_builder` | 16 | 35 | 57 | declared module API; review is still pending |
| `SimulationNodeId` | `src/skyseed_v2/contracts/identity.py:18` (class) | yes | `skyseed_v2`<br>`skyseed_v2.contracts`<br>`skyseed_v2.contracts.identity` | - | 42 | 53 | 124 | root public API; highest migration impact |
| `SkyseedAutonomousSystem` | `src/skyseed_v2/seed/autonomous_system.py:20` (class) | no | `skyseed_v2.seed`<br>`skyseed_v2.seed.autonomous_system` | `examples/complete_static_emulator/main.py:17` from `skyseed_v2.seed`<br>`examples/isolated_satellites/main.py:11` from `skyseed_v2.seed` | 34 | 65 | 118 | declared subpackage API; possible external use |
| `SkyseedAutonomousSystemSpecification` | `src/skyseed_v2/specifications.py:575` (class) | no | `skyseed_v2.specifications` | - | 6 | 0 | 16 | declared module API; review is still pending |
| `SkyseedDockerCompiler` | `src/skyseed_v2/seed/compiler.py:231` (class) | no | `skyseed_v2.seed`<br>`skyseed_v2.seed.compiler` | `examples/complete_static_emulator/main.py:17` from `skyseed_v2.seed`<br>`examples/isolated_satellites/main.py:11` from `skyseed_v2.seed`<br>`examples/large_static_network/main.py:13` from `skyseed_v2.seed` | 23 | 33 | 66 | declared subpackage API; possible external use |
| `StaticEmulatorApplyResult` | `src/skyseed_v2/scenario_application.py:34` (class) | no | `skyseed_v2.scenario_application` | `examples/large_static_network/main.py:8` from `skyseed_v2.scenario_application` | 8 | 19 | 33 | declared module API; review is still pending |
| `StaticEmulatorScenarioLoadError` | `src/skyseed_v2/scenario_loader.py:45` (class) | no | `skyseed_v2.scenario_loader` | - | 9 | 0 | 27 | declared module API; review is still pending |
| `StaticEmulatorScenarioLoader` | `src/skyseed_v2/scenario_loader.py:128` (class) | no | `skyseed_v2.scenario_loader` | `examples/large_static_network/main.py:12` from `skyseed_v2.scenario_loader` | 23 | 17 | 46 | declared module API; review is still pending |
| `StaticEmulatorScenarioSpecification` | `src/skyseed_v2/specifications.py:682` (class) | no | `skyseed_v2.specifications` | - | 12 | 10 | 33 | declared module API; review is still pending |
| `StaticEmulatorSpecificationApplier` | `src/skyseed_v2/scenario_application.py:50` (class) | no | `skyseed_v2.scenario_application` | `examples/large_static_network/main.py:8` from `skyseed_v2.scenario_application` | 17 | 21 | 44 | declared module API; review is still pending |
| `ground_station_id_for` | `src/skyseed_v2/specifications.py:387` (function) | no | `skyseed_v2.specifications` | - | 0 | 1 | 8 | declared module API; review is still pending |
| `skyseed_metadata_key` | `src/skyseed_v2/seed/metadata.py:10` (function) | no | `skyseed_v2.seed.metadata` | - | 0 | 0 | 13 | direct defining-module path; no aggregate export |
| `validate_identifier` | `src/skyseed_v2/domain/validation.py:6` (function) | no | `skyseed_v2.domain`<br>`skyseed_v2.domain.validation` | - | 3 | 0 | 23 | declared subpackage API; possible external use |

## Compatibility facts

- The project metadata version is `0.1.0`.
- The repository contains built-wheel validation evidence, but no changelog, deprecation policy, package-registry publication record, or statement promising compatibility for these import paths.
- `docs/CURRENT.md` explicitly keeps the Emulator public construction API at `IMPLEMENTED, PENDING USER REVIEW`.
- Therefore the repository itself does not prove a compatibility obligation. A direct migration without aliases is technically available if the user approves it, but absence of repository evidence cannot prove that no outside checkout imports a submodule path.
- Root imports have the strongest intentional stability signal. Subpackage and root-module `__all__` paths are also deliberately public in code and must be treated as migration impacts, even if the project has not made a release promise.

## Example import impact

- `isolated_satellites`: affected by changes to `skyseed_v2` root and `skyseed_v2.seed`.
- `complete_static_emulator`: affected by changes to root, `skyseed_v2.seed`, and `skyseed_v2.catalog_builder`.
- `large_static_network`: affected by changes to `skyseed_v2.scenario_loader`, `skyseed_v2.scenario_application`, and `skyseed_v2.seed`.
