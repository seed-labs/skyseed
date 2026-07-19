# Static Emulator Creation Module Rename Validation

> Supersession note (2026-07-19): this is a historical snapshot. The current
> Config aggregate module is
> `skyseed_v2.configuration.static_emulator_config`; the top-level operation
> module is `skyseed_v2.static_emulator`.

## Scope

This record validates the approved public module-path rename:

```text
skyseed_v2.static_emulator
→ skyseed_v2.static_emulator_creation
```

The rename distinguishes the Config-to-Emulator creation operation from
`skyseed_v2.configuration.static_emulator`, which continues to define only
immutable `StaticEmulatorConfig` and `SkyseedAutonomousSystemConfig` values.
The public function and result type retain their names and behavior.

## Boundary assertions

- `skyseed_v2.configuration.static_emulator` imports no entity or upstream
  seedemu module and creates no Emulator object.
- `skyseed_v2.static_emulator_creation` owns `create_static_emulator()` and
  `StaticEmulatorCreationResult`.
- The old `skyseed_v2.static_emulator` module is absent; no compatibility alias
  or second import path remains.
- Entity ownership, Config fields, creation order, return fields, Simulator
  input generation and Compiler behavior are unchanged.
- The large file-driven example imports the creation operation from its new
  owning module. The two handwritten examples remain independent of Config
  and the creation operation.

## Automated validation

From `skyseed_v2/`, using the native project virtual environment:

```text
python -m compileall -q src/skyseed_v2 examples tests
focused import/architecture/creation/example tests: 41 passed in 12.11s
full test suite: 147 passed in 13.97s
git diff --check: pass
```

A wheel built with `pip wheel . --no-deps --no-build-isolation` contains:

```text
skyseed_v2/configuration/static_emulator.py
skyseed_v2/static_emulator_creation.py
```

It does not contain `skyseed_v2/static_emulator.py`. No image build, Compose
operation or container start was performed. Simulator remains **NOT STARTED**.

## Architecture Delta

- Public module renamed: `skyseed_v2.static_emulator` →
  `skyseed_v2.static_emulator_creation`.
- Public types and functions: unchanged.
- Root exports: unchanged.
- Entity ownership and data ownership: unchanged.
- Cross-layer contracts and formal execution sequence: unchanged.
- Module dependency diagram, public API record, terminology, CURRENT, design
  record, tests and the large example import were updated to the new path.
