# Simulator-facing Read-only Data Boundary

## What the directory contains now

`src/skyseed_v2/contracts/` contains exactly the four approved public data
types below, their two entity-kind constants and an internal descriptor union.
It contains no executable Simulator, Planner, Runner, container locator,
Registry wrapper or serialization adapter.

| Type | Definition | Produced by | Read by now | Intended later consumer | Read-only mechanism |
| --- | --- | --- | --- | --- | --- |
| `SimulationNodeId` | `src/skyseed_v2/contracts/identity.py:18` | Satellite/GS Projectors or explicit contract callers | Descriptors, Catalog and identity tests | future Simulator | frozen/slotted dataclass; value is ASN + entity kind + entity ID, not a runtime resource ID |
| `SatelliteDescriptor` | `src/skyseed_v2/contracts/satellite.py:11` | `SatelliteDescriptorProjector` | Catalog, complete/contract tests and summary readers | future Simulator | frozen/slotted dataclass containing approved static scalar values only |
| `GroundStationDescriptor` | `src/skyseed_v2/contracts/ground_station.py:11` | `GroundStationDescriptorProjector` | Catalog, complete/contract tests and summary readers | future Simulator | frozen/slotted dataclass containing approved static scalar values only |
| `SimulationEntityCatalog` | `src/skyseed_v2/contracts/catalog.py:22` | `SimulationEntityCatalogBuilder` or explicit callers | ApplyResult, complete/large examples, catalog/architecture tests | future Simulator | frozen/slotted object; tuple snapshots plus `MappingProxyType` identity index |

All four are therefore currently the **Emulator → future Simulator read-only
data boundary**. The direction is architectural: entities are projected out of
the Emulator; nothing in this directory reaches into Simulator code because
Simulator does not exist yet.

They also have current non-Simulator consumers:

- Projectors and CatalogBuilder construct them.
- `StaticEmulatorApplyResult` carries the Catalog to file-driven callers.
- examples read Catalog counts and IDs.
- unit/integration/architecture tests verify identity, immutability,
  determinism and construction-path equivalence.
- documentation names them as the approved future boundary.

These consumers do not change the boundary's meaning; they exercise or display
the same read-only projection.

## Are there mixed responsibilities inside `contracts`?

No independent responsibility is present. The support symbols are:

- `SATELLITE_ENTITY_KIND` and `GROUND_STATION_ENTITY_KIND`, which constrain
  `SimulationNodeId` and Descriptor validation;
- internal `SimulationDescriptor`, a union used only by Catalog typing.

One cross-layer dependency deserves visibility:
`contracts/identity.py` imports `domain.validation.validate_identifier`. The
helper itself has no SEED dependency, so this does not make the contract
runtime-aware, but the path makes the Simulator-facing boundary depend on a
directory named `domain`. If directories are reorganized, a neutral shared
validation location or local contract validation would remove that naming
coupling. No new shared package is required merely for the current code.

## Why `contracts` can be misunderstood

The name can plausibly mean any of the following to a new reader:

- legal/business contracts;
- abstract interfaces or Python protocols;
- schema/version contracts for YAML;
- API request/response models;
- contracts shared equally by every subsystem;
- the specific one-way Emulator-to-Simulator data view actually implemented.

It also does not reveal that the contents are data-only, immutable, static and
produced by Emulator projection.

## Candidate directory semantics

No candidate is selected here.

| Candidate | What it communicates well | Misunderstanding/cost |
| --- | --- | --- |
| `simulation_input` | Indicates data consumed by simulation and avoids generic “contracts” | “simulation” may be confused with the whole product; direction from Emulator and ownership by future Simulator remain unclear; may imply runtime scenario input rather than entity descriptors |
| `simulator_api` | Names the intended consumer directly and reads well in imports | “API” suggests callable services or a stable implemented Simulator API; Simulator is not started; could incorrectly imply these types belong to Simulator rather than being produced by Emulator |
| `descriptors` | Immediately finds `SatelliteDescriptor` and `GroundStationDescriptor`; concise | Does not accurately cover `SimulationNodeId` or `SimulationEntityCatalog`; encourages treating Catalog as merely another Descriptor |
| `simulator_facing` | Expresses orientation without claiming ownership; covers ID, Descriptors and Catalog | Architectural adjective rather than a concrete object name; still requires readers to know what “facing” means |
| `emulator_output` | Makes producer direction explicit and fits projection/Catalog creation | Too broad: Compiler Compose output is also Emulator-related output, but must not enter this boundary |
| `simulation_entity_view` | Accurately describes a restricted read model for entities | Singular/package wording is less conventional; “view” can be confused with UI or database views |
| keep `contracts` | Zero migration cost; already normative in current docs | Retains all discovery ambiguities above and requires architectural context before the files make sense |

File naming can be considered separately from directory naming. For example,
`satellite_descriptor.py` and `ground_station_descriptor.py` are more directly
discoverable than the current entity-only filenames regardless of which parent
directory is chosen. That is a path proposal only, not a class rename.
