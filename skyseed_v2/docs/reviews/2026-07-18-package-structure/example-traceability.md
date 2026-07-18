# Example Discoverability and Traceability

## Scoring and jump convention

- A **jump** is one source navigation step after the import site. Following a
  package/subpackage `__init__` re-export to the defining module counts as one.
- Following a returned object's type to its next create method is another jump.
- Score 5 means the class name predicts its file; 4 means one clear facade
  jump; 3 means traceable but a directory or filename is generic; 2 means the
  file name actively hides the class; 1 would mean hidden dynamic construction.
- No example scores 1: all construction is statically traceable and contains
  no hidden callback/factory registration chain.

## `examples/isolated_satellites/main.py`

### Imports

```python
from seedemu.core import Emulator
from skyseed_v2 import OrbitParameters
from skyseed_v2.seed import (
    SatelliteBase,
    SkyseedAutonomousSystem,
    SkyseedDockerCompiler,
)
```

### Visible call path

```text
Emulator
→ SatelliteBase
→ SkyseedAutonomousSystem
→ AS.create_shell → Shell
→ Shell.create_orbit → Orbit
→ Orbit.create_satellite → Satellite
→ Emulator.render
→ Emulator.compile(SkyseedDockerCompiler)
```

| Key type/method | Current definition | Jumps from visible import/call | Discoverability |
| --- | --- | ---: | ---: |
| `OrbitParameters` | `src/skyseed_v2/domain/satellite.py:41` | 1 through root facade | 2/5: neither `domain` nor `satellite.py` predicts an Orbit value |
| `SatelliteBase` | `src/skyseed_v2/seed/base.py:11` | 1 through `seed.__init__` | 2/5: `seed/base.py` is doubly generic |
| `SkyseedAutonomousSystem.create_shell` | `src/skyseed_v2/seed/autonomous_system.py:20,39` | 1 through seed facade; method is in the class file | 3/5 |
| `Shell.create_orbit` | `src/skyseed_v2/domain/satellite.py:326,355` | 1 from AS method to returned class definition | 2/5: class/method live in `satellite.py` |
| `Orbit.create_satellite` | `src/skyseed_v2/domain/satellite.py:191,240` | 1 from returned Shell/Orbit call chain | 2/5: Orbit is hidden by filename |
| `Satellite` | `src/skyseed_v2/domain/satellite.py:69` | same defining file as factory method | 5/5 once `satellite.py` is reached |
| `SkyseedDockerCompiler` | `src/skyseed_v2/seed/compiler.py:231` | 1 through seed facade | 3/5: class qualifier is clearer than filename |

**Example score: 3/5.** The call chain is excellent, but navigation to Shell,
Orbit and OrbitParameters is unexpectedly concentrated in `satellite.py`.

## `examples/complete_static_emulator/main.py`

### Imports

```python
from seedemu.core import Emulator
from seedemu.layers import Ebgp, Ibgp, Ospf, PeerRelationship, Routing
from skyseed_v2 import (
    GroundStationLocation,
    OrbitParameters,
    SimulationEntityCatalog,
)
from skyseed_v2.catalog_builder import SimulationEntityCatalogBuilder
from skyseed_v2.seed import (
    SatelliteBase,
    SkyseedAutonomousSystem,
    SkyseedDockerCompiler,
)
```

### Visible call path

The first half is the same direct AS → Shell → Orbit → Satellite path. The file
then calls `SkyseedAutonomousSystem.create_ground_station`, ordinary upstream
AS/Network/Router/IX APIs and eBGP Layer APIs, builds a Catalog, renders and
compiles.

| Key type/method | Current definition | Jumps | Discoverability |
| --- | --- | ---: | ---: |
| `GroundStationLocation` | `src/skyseed_v2/domain/ground_station.py:21` | 1 through root facade | 4/5: entity filename is predictable |
| `SkyseedAutonomousSystem.create_ground_station` | `src/skyseed_v2/seed/autonomous_system.py:20,72` | 1 through seed facade | 3/5: approved domain owner is under `seed` |
| `GroundStation` | `src/skyseed_v2/domain/ground_station.py:51` | 1 from create method/imported return type | 5/5 |
| `SimulationEntityCatalogBuilder` | `src/skyseed_v2/catalog_builder.py:20` | 0: exact direct module import | 4/5: filename is shorter than class but clear |
| both Descriptor Projectors | `src/skyseed_v2/projection.py:23,51` | 1 inside CatalogBuilder, not required reading for basic example | 3/5 |
| `SimulationEntityCatalog` | `src/skyseed_v2/contracts/catalog.py:22` | 1 through root facade | 3/5: `contracts` does not reveal Simulator-facing read-only direction |
| Satellite-side types | same locations as isolated example | same | same 2–3/5 limitations |

**Example score: 3.5/5.** GroundStation and CatalogBuilder are easier to find;
the Satellite hierarchy and `seed` directory remain surprising.

## `examples/large_static_network/main.py`

### Imports

```python
from skyseed_v2.scenario_application import (
    StaticEmulatorApplyResult,
    StaticEmulatorSpecificationApplier,
)
from skyseed_v2.scenario_loader import StaticEmulatorScenarioLoader
from skyseed_v2.seed import SkyseedDockerCompiler
```

### Visible call path

```text
StaticEmulatorScenarioLoader.load
→ StaticEmulatorScenarioSpecification graph
→ StaticEmulatorSpecificationApplier.apply
→ public Skyseed/SEED create and join methods
→ SimulationEntityCatalogBuilder
→ StaticEmulatorApplyResult
→ render + SkyseedDockerCompiler
```

| Key type/method | Current definition | Jumps | Discoverability |
| --- | --- | ---: | ---: |
| `StaticEmulatorScenarioLoader` | `src/skyseed_v2/scenario_loader.py:128` | 0: exact direct module | 4/5, though YAML is not in filename |
| `StaticEmulatorScenarioSpecification` | `src/skyseed_v2/specifications.py:682` | 1 through Loader return/import | 3/5: plural mega-module requires search |
| component Specifications | `src/skyseed_v2/specifications.py:93–682` | 1 from Loader imports, then line search | 2.5/5 because 17 classes share one file |
| `StaticEmulatorSpecificationApplier` | `src/skyseed_v2/scenario_application.py:50` | 0 | 4/5 |
| `StaticEmulatorApplyResult` | `src/skyseed_v2/scenario_application.py:34` | 0 | 4/5 |
| real domain create methods | AS file plus `domain/satellite.py`/`ground_station.py` | 1 from Applier method body, then no hidden facade | 3/5 overall |
| `SkyseedDockerCompiler` | `src/skyseed_v2/seed/compiler.py:231` | 1 through seed facade | 3/5 |

**Example score: 3.5/5.** Public imports are explicit and few, but readers who
want to inspect one input class or the real Shell/Orbit methods encounter large
or unexpectedly named files.

## Five hardest current navigation targets

1. `Shell` / `Shell.create_orbit`: located in `domain/satellite.py`.
2. `Orbit` / `Orbit.create_satellite`: also located in `domain/satellite.py`.
3. `OrbitParameters`: an Orbit-named value in `domain/satellite.py`.
4. `SatelliteBase`: exported from ambiguous `seed`, defined in generic
   `seed/base.py`.
5. `SkyseedAutonomousSystem.create_ground_station`: the approved Shell/GS
   domain owner is defined under the SEED integration directory rather than an
   entity/ownership path.

`SimulationEntityCatalog` is a close sixth: the class/file name is reasonably
direct, but `contracts` does not tell a new reader that it is the read-only
Emulator → Simulator entity view.
