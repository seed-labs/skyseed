# Core Entity and Integration Type Map

All line numbers refer to commit
`0ebd3c6a69e50e8e2cfefb4229558bc213c16510`.

## Exact locations and relationships

### `SkyseedAutonomousSystem`

- **Definition:** `src/skyseed_v2/seed/autonomous_system.py:20`
- **Base:** upstream `seedemu.core.AutonomousSystem`.
- **Primary state:** `_shells`, AS-wide `_satellite_ids`, `_ground_stations`, plus inherited ordinary networks/hosts/routers/options.
- **Public methods defined here:** `shells`, `create_shell`, `get_shell`, `satellites`, `get_satellite`, `create_ground_station`, `ground_stations`, `get_ground_station`, and SEED overrides/extensions `getRouters`, `createRouter`, `getRouter`, `getBorderRouters`, `registerNodes`, `inheritOptions`, `configure`.
- **Creator:** handwritten examples or `StaticEmulatorSpecificationApplier`.
- **Owner:** a `SatelliteBase`/upstream Base layer stores it; the AS itself owns Shell and GroundStation collections.
- **Consumers:** Shell/GS callers, SEED Base/routing lifecycle, Projectors through CatalogBuilder, Compiler through Registry.
- **Direct imports:** upstream `AutonomousSystem`, `Emulator`, `Router`; domain constants, `GroundStation`, `GroundStationLocation`, `Satellite`, `Shell`; identifier validation.
- **Filename match:** partial. `autonomous_system.py` identifies the upstream concept but not the public `Skyseed` specialization.

### `Shell`

- **Definition:** `src/skyseed_v2/domain/satellite.py:326`.
- **Base:** `object`.
- **Primary state:** `_shell_id`, `_orbits`, shared AS-wide `_satellite_ids`, `_asn`.
- **Public methods/properties:** `shell_id`, `orbits`, `create_orbit`, `get_orbit`, `iter_satellites`.
- **Creator:** `SkyseedAutonomousSystem.create_shell`.
- **Owner:** one `SkyseedAutonomousSystem`.
- **Consumers:** handwritten examples, Specification Applier, AS traversal.
- **Direct dependencies:** `Orbit`, `OrbitParameters`, identifier validation, `Iterable` type.
- **Filename match:** poor. A reader looking for `Shell` would not naturally select `satellite.py`.

### `Orbit`

- **Definition:** `src/skyseed_v2/domain/satellite.py:191`.
- **Base:** `object`.
- **Primary state:** IDs for Orbit/Shell, `OrbitParameters`, shared AS-wide Satellite-ID set, ASN, owned Satellite map and used orbit indices.
- **Public methods/properties:** `orbit_id`, `shell_id`, `parameters`, `satellites`, `get_satellite`, `create_satellite`, `create_satellites`.
- **Creator:** `Shell.create_orbit`.
- **Owner:** one `Shell`.
- **Consumers:** handwritten examples, Specification Applier, Shell/AS traversal.
- **Direct dependencies:** runtime construction of `Satellite`, `OrbitParameters`, identifier/angle validation and mapping types.
- **Filename match:** poor for the same reason as Shell.

### `Satellite`

- **Definition:** `src/skyseed_v2/domain/satellite.py:69`.
- **Base:** upstream `seedemu.core.Node`.
- **Primary state:** Satellite/Shell/Orbit IDs, orbit index, `OrbitParameters`, mean anomaly, immutable payload; inherited SEED Node state.
- **Public methods/properties:** `satellite_id`, `shell_id`, `orbit_id`, `orbit_index`, `orbit_parameters`, `mean_anomaly_deg`, `payload`, `registry_type`, `scope`; inherited Node configuration methods remain available.
- **Creator:** `Orbit.create_satellite` (and batch creation delegates to it).
- **Owner:** one Orbit; reachable through Shell and AS; registered into Emulator by the AS.
- **Consumers:** Orbit/AS, SEED Base lifecycle, Satellite Projector, Compiler.
- **Direct imports:** upstream `Node`, `Scope`, `ScopeTier`, `ScopeType`; `OrbitParameters`, `SatelliteRole`, identifier and angle validation.
- **Filename match:** exact for Satellite itself.

### `OrbitParameters`

- **Definition:** `src/skyseed_v2/domain/satellite.py:41`.
- **Base:** frozen/slotted dataclass over `object`.
- **Primary properties:** `altitude_km`, `inclination_deg`, `right_ascension_deg`.
- **Public methods:** generated dataclass value methods; validation in `__post_init__`.
- **Creator:** handwritten callers or Specification Applier.
- **Owner:** logically an Orbit value; the same value is stored by Orbit and each Satellite created by it.
- **Consumers:** Orbit, Satellite, Projector and callers reading static values.
- **Direct imports:** dataclass, finite-number/angle validation.
- **Filename match:** incomplete. It is shared by Orbit and Satellite, so neither entity filename is perfectly exclusive.

### `GroundStation`

- **Definition:** `src/skyseed_v2/domain/ground_station.py:51`.
- **Base:** upstream `seedemu.core.Router`.
- **Primary state:** GS ID, `GroundStationLocation`, station type, operator, immutable service config; inherited Router state.
- **Public methods/properties defined here:** `ground_station_id`, `location`, `station_type`, `operator`, `service_config`, `registry_type`; inherited Router networking/configuration methods are intentionally part of the same instance.
- **Creator:** `SkyseedAutonomousSystem.create_ground_station`.
- **Owner:** one `SkyseedAutonomousSystem`.
- **Consumers:** AS/SEED routing lifecycle, GroundStation Projector, Compiler and examples.
- **Direct imports:** upstream `Router` and `NodeRole`; `GroundStationLocation`, identifier validation, immutable mapping utilities.
- **Filename match:** exact for the entity; the location value is tightly cohesive.

### `GroundStationLocation`

- **Definition:** `src/skyseed_v2/domain/ground_station.py:21`.
- **Base:** frozen/slotted dataclass over `object`.
- **Primary properties:** latitude, longitude and optional elevation.
- **Public methods:** generated dataclass value methods; validation in `__post_init__`.
- **Creator:** handwritten callers or Specification Applier.
- **Owner:** value supplied to and retained by a GroundStation.
- **Consumers:** GroundStation, Projector and Compiler metadata.
- **Direct imports:** dataclass and finite-number validation.
- **Filename match:** appropriate to remain with GroundStation unless value objects are systematically separated.

### `SatelliteBase`

- **Definition:** `src/skyseed_v2/seed/base.py:11`.
- **Base:** upstream `seedemu.layers.Base.Base`.
- **Primary state:** inherited Base layer state; no new constructor fields.
- **Public methods:** overrides public `render`; also overrides private lifecycle `_prepare`; callers use inherited Base APIs such as `setAutonomousSystem`, `createAutonomousSystem` and `createInternetExchange`.
- **Creator:** all examples or Specification Applier.
- **Owner:** added to one upstream `Emulator` as a Layer.
- **Consumers:** Emulator render lifecycle and Satellite nodes.
- **Direct imports:** upstream Base templates/core types and Satellite Registry constant.
- **Filename match:** weak. `base.py` is generic; `satellite_base.py` would match the public class more directly.

### `SkyseedDockerCompiler`

- **Definition:** `src/skyseed_v2/seed/compiler.py:231`.
- **Base:** upstream `seedemu.compiler.Docker`.
- **Primary state:** Skyseed InternetMap enable/port settings, inherited Docker compiler state and private `_SatelliteCompilerBoundary`.
- **Public methods:** `getName`; the main `compile` entry is inherited. Private `_doCompile` dispatches Satellites, then delegates ordinary/GS work upstream.
- **Creator:** all formal examples or advanced compile callers.
- **Owner:** operation-scoped object passed to `Emulator.compile`, not an entity-owned child.
- **Consumers:** Emulator compile call; downstream users consume the Compose artifact.
- **Direct imports:** upstream Docker compiler/private templates, Emulator, Satellite/GS and metadata helpers, YAML/path/resource utilities, packaged InternetMap patch.
- **Filename match:** partial. `compiler.py` omits both `Skyseed` and `Docker`.

## Why Shell, Orbit and Satellite currently share one file

Git history provides one hard fact: commit `4995c8f` (“Implement isolated
Satellite emulator”) introduced `SatelliteRole`, `OrbitParameters`, `Satellite`,
`Orbit` and `Shell` together in a new 381-line `domain/satellite.py`. The commit
does not contain a written rationale for the filename. The following rationale
is therefore an inference from the actual dependency shape, not a recovered
author statement:

1. The first vertical slice was the complete Shell → Orbit → Satellite path,
   so keeping the slice together minimized initial module plumbing.
2. Construction references are runtime references, not annotations only:
   `Shell.create_orbit` calls `Orbit(...)`, and `Orbit.create_satellite` calls
   `Satellite(...)`.
3. `Satellite` performs a runtime `isinstance(..., OrbitParameters)` check.
   Co-location makes all three constructor dependencies local and eliminates
   any import-order question.

## Split-induced cycles and mechanical remedies

The safe current dependency direction inside the file is:

```text
Shell → Orbit → Satellite → OrbitParameters
          └──────────────→ OrbitParameters
```

Splitting can preserve this acyclic direction mechanically:

```text
orbit_parameters.py   (leaf value)
satellite.py          → orbit_parameters.py
orbit.py              → satellite.py + orbit_parameters.py
shell.py              → orbit.py + orbit_parameters.py
```

The most likely accidental cycle is produced by placing `OrbitParameters` in
`orbit.py` while keeping the constructor relationship:

```text
orbit.py → satellite.py       # Orbit constructs Satellite
satellite.py → orbit.py       # Satellite imports OrbitParameters
```

- Forward annotations and `from __future__ import annotations` solve annotation
  cycles only; they do not provide the runtime `Satellite(...)` constructor or
  runtime `isinstance(..., OrbitParameters)` target.
- `TYPE_CHECKING` is therefore insufficient by itself for the current method
  bodies. Local imports could break import-time cycles, but make the design less
  discoverable and are unnecessary if dependency direction is chosen first.
- A separate `orbit_parameters.py` is the cleanest mechanical solution.
  Another workable solution is keeping `OrbitParameters` with `Satellite`, then
  importing both from `orbit.py`, although the value's conceptual ownership is
  less obvious.
- `GroundStationLocation` is a better candidate to stay with GroundStation:
  only one entity concept owns it, and GroundStation's runtime type check points
  in a single direction.
- `SatelliteRole` and Satellite Registry/role constants naturally stay with
  Satellite or move to the existing SEED integration metadata area; moving them
  is a classification decision, not required merely to split Shell and Orbit.

No existing strongly connected component prevents the split. The risk is in
creating a new cycle through an unfortunate placement of `OrbitParameters`, not
in an unavoidable current cycle.
