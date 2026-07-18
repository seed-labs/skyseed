# Simulator Static-Input Ownership and Compiler Terminology Validation

## Scope

This bounded change started from clean HEAD `3d50cf0` and moved only the pure
Simulator static-input types:

```text
skyseed_v2.simulator_input
→ skyseed_v2.simulator.static_input
```

It also freezes the distinction between implemented static deployment
compilation and future Simulation Plan compilation. Simulator algorithms,
engine, topology and Simulation Plan compilation remain unimplemented.

## Ownership and dependency assertions

- `SatelliteSimulationInput`, `GroundStationSimulationInput` and
  `SimulatorStaticInput` are public from
  `skyseed_v2.simulator.static_input`.
- `skyseed_v2.simulator` exports no engine or algorithm type.
- The input types import only standard-library modules, their peer input
  modules and neutral `skyseed_v2.node_id`.
- `simulator_input_generation.py` remains outside the Simulator package and is
  the only adapter that depends on both Emulator entities/SEED integration and
  Simulator-owned input types.
- The old `skyseed_v2.simulator_input` package is absent; no compatibility
  module or alias remains.
- `NodeId` remains in `skyseed_v2.node_id` with unchanged fields, validation,
  equality and serialized `value` format. It has no resolution ability.
- Satellite and GroundStation construction APIs and ownership are unchanged.

## Distinct compilation stages

`SkyseedDockerCompiler` remains the implemented seedemu integration that lowers
a rendered Emulator/SEED static model into Dockerfile and Compose deployment
configuration.

`SimulationPlanCompiler` is only the reserved name for a future independent
stage that would lower an abstract Simulation Plan into concrete dynamic
network update operations. No class, module, interface, test skeleton,
inheritance relationship or shared compiler parent was created.

## Automated validation

From `skyseed_v2/`, using the native project virtual environment:

```text
python -m compileall -q src/skyseed_v2 examples tests
focused static-input/import/architecture/example tests: 90 passed in 13.05s
full test suite: 148 passed in 13.59s
git diff --check: pass
```

A wheel built with `pip wheel . --no-deps --no-build-isolation` has SHA-256:

```text
d59c65f9dbcfe6e23fd392b541012827991615761be1a4bdf5a36dd0232fb896
```

Archive inspection confirmed all five expected
`skyseed_v2/simulator/static_input` package files and no
`skyseed_v2/simulator_input` or `SimulationPlanCompiler` implementation file.
An isolated wheel import with upstream `seedemu` imports blocked successfully
loaded `NodeId`, `skyseed_v2.simulator` and `SimulatorStaticInput`; the old
package was not discoverable.

No image build, Compose operation or container start was performed.

## Architecture Delta

- Public package moved: `skyseed_v2.simulator_input` →
  `skyseed_v2.simulator.static_input`.
- Public type names, fields and methods: unchanged.
- Input-generation function names and owning module: unchanged.
- `NodeId` module and behavioral contract: unchanged.
- Root exports: unchanged at six entity/value names.
- Entity ownership and construction APIs: unchanged.
- Module dependency direction now visibly records Simulator ownership without
  adding an Emulator back-dependency.
- `SkyseedDockerCompiler` and future `SimulationPlanCompiler` are documented as
  independent stages with different inputs and outputs.
- Class diagrams retain only implemented classes; flow/call diagrams add the
  future Simulator/Plan/plan-compilation boundary as non-implemented nodes.
- Example behavior is unchanged; only the complete example's input-type import
  path moved.
- Simulator static-input contract: **AVAILABLE**.
- Simulator algorithms and engine: **NOT STARTED**.
