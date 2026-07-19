# Static Emulator Configuration Module Rename Validation

Date: 2026-07-19

## Scope

This record validates the purely mechanical module rename:

```text
skyseed_v2.configuration.static_emulator
→ skyseed_v2.configuration.static_emulator_config
```

The two responsibilities remain unchanged:

```text
configuration/static_emulator_config.py
→ define and validate StaticEmulatorConfig

static_emulator.py
→ create_static_emulator(config) -> seedemu.core.Emulator
```

`simulator_input_generation.py` is byte-for-byte unchanged and remains the
separate Emulator-entity-tree to `SimulatorStaticInput` adapter.

## Public contract

The supported imports remain:

```python
from skyseed_v2.configuration import (
    StaticEmulatorConfig,
    load_static_emulator_config,
)
from skyseed_v2.static_emulator import create_static_emulator
```

No compatibility module remains at the old configuration path. No type,
function, field, YAML key, schema version, return value or root export changed.

## Validation

The completion run produced these results:

| Scope | Command or check | Outcome |
| --- | --- | --- |
| Python compilation | `../.venv/bin/python -m compileall -q src tests examples` | passed |
| Focused configuration/import/architecture tests | focused pytest selection | 55 passed |
| Complete suite | `PYTHONPATH=. ../.venv/bin/pytest -q` | 151 passed |
| Source paths | `find_spec` plus source scan | new path present; old path absent |
| Content preservation | SHA-256 before/after comparison | Config module unchanged across the move; top-level creation and Simulator-input adapter unchanged |
| Wheel | clean `pip wheel` plus archive inspection | new Config module and top-level operation present; old Config module absent |
| Isolated install | non-editable Skyseed wheel against pinned upstream source | public imports, return annotation and minimal real construction passed |
| Markdown | relative-link scan | 54 files passed |
| Mermaid | complete renderer pass | 25 diagrams rendered |
| Patch hygiene | `git diff --check` | passed |

The wheel contains:

```text
skyseed_v2/configuration/static_emulator_config.py
skyseed_v2/static_emulator.py
skyseed_v2/simulator_input_generation.py
```

It does not contain
`skyseed_v2/configuration/static_emulator.py`. The isolated check proves that
`StaticEmulatorConfig.__module__` is the new internal path while its supported
package-level import is unchanged. It also proves that
`create_static_emulator()` remains in the top-level operation module, retains
the upstream `Emulator` return annotation and returns an actual upstream
`Emulator` for a minimal valid Config.

## Architecture Delta

None. This change only disambiguates the filename of an existing private
module path while preserving its package-level public exports and all runtime
behavior.
