# NodeId and Emulator Node Index Validation

Date: 2026-07-18  
Baseline HEAD: `05fa94d35392b7bd58317568c3fdfce23e95c93f`  
Python: `3.12.3`  
pytest: `9.1.1`

## Scope and result

This slice establishes only the static cross-layer identity and packaging
contract. Simulator algorithms, positions, visibility, links, routing plans,
runtime updates, Compiler update lowering and container startup remain
unimplemented. Simulator status is **NOT STARTED**.

The former `SimulationNodeId` definition and import path were removed. The
current public value is:

```python
from skyseed_v2.node_id import NodeId

NodeId(
    asn=65001,
    entity_kind="satellite",
    entity_id="satellite-0",
)
```

Its field structure and deterministic `value` format are unchanged. No root
export and no compatibility alias were added.

## Authoritative generation and ownership

`simulator_input_generation.py` contains the only derivation implementation,
the private `_derive_node_id(...)`, and the one supported-entity traversal,
the private `_iter_skyseed_nodes(...)`. The traversal accepts only
`SkyseedAutonomousSystem` values and follows:

```text
AS → Shell → Orbit → Satellite
AS → GroundStation
```

It covers no ordinary Router, Host or arbitrary SEED node. Unsupported AS or
owned objects fail with `TypeError`; duplicate complete IDs fail with
`ValueError`.

Satellite and GroundStation constructors, creation methods and properties were
not extended with `NodeId`. The value is a cross-layer contract rather than an
Emulator entity property.

## Two consistent outputs

The same traversal and derivation feed two deliberately separate results:

```text
NodeId → SatelliteSimulationInput / GroundStationSimulationInput
NodeId → exact original Satellite / GroundStation entity
```

`create_simulator_static_input(...)` produces only immutable pure data.
`create_emulator_node_index(...)` produces a `MappingProxyType` read-only
mapping whose values are the original entity instances. The entity mapping is
not stored in or passed to `SimulatorStaticInput`.

Unit, handwritten-example and YAML-path tests prove:

- every static-input `NodeId` equals the Emulator node-index key set;
- same local IDs remain distinct across ASN and entity kind;
- index lookup returns the same object instance created by the Emulator path;
- duplicate IDs, ordinary AS values, unknown values and unsupported owned
  objects fail closed;
- a formatted string is not a key and is never parsed to recover an entity;
- entities do not accept or expose `node_id`.

The complete and large examples create both outputs explicitly, compare their
ID sets and only then compile. The isolated example remains a pure handwritten
Emulator construction path and does not use either output.

## Dependency boundary

AST and guarded-import tests prove:

- `node_id.py` depends only on the standard library and
  `skyseed_v2.identifier`;
- `simulator_input` depends on `skyseed_v2.node_id` but not entities,
  seedemu integration, upstream `seedemu`, Compiler, Registry, Docker or
  Compose;
- importing `NodeId` and an empty `SimulatorStaticInput` succeeds while every
  upstream `seedemu` import is blocked;
- `simulator_input_generation` owns the Emulator/SEED-facing traversal and has
  no Config, YAML, materialization or Docker dependency.

## Fail-closed contract for future work

Future Simulator state and abstract plans must reference `NodeId`. A future
Compiler update path must use the Emulator node index to recover the real
entity and fail on a missing or duplicate ID, wrong node kind, mismatched input
and index sets, or a plan built from a different entity collection. It must not
guess from display, Registry, container, service, namespace or interface
strings. No plan or update-path type was created in this slice.

## Commands and results

```text
PYTHONPATH=src:. ../.venv/bin/python -m compileall -q src tests examples
PASS

focused identity/boundary/example pytest
64 passed in 1.04s

focused architecture/large-network pytest
30 passed in 8.54s

PYTHONPATH=src:. ../.venv/bin/pytest -q
147 passed in 9.73s
```

The wheel check used:

```text
../.venv/bin/python -m pip wheel . --no-deps --no-build-isolation
python3 -m venv <isolated-import-env>
<isolated-import-env>/bin/python -m pip install --no-deps <wheel>
```

An initial wheel audit exposed an ignored stale `build/lib` cache containing
already deleted package paths. The generated `build/` directory was removed
and regenerated. The clean wheel then contained `skyseed_v2/node_id.py`, did
not contain `simulator_input/identity.py` or other retired packages, and passed
root/NodeId/simulator-input imports from outside the source tree without
installing dependencies.

No container build, Compose `up` or Simulator execution was run.

## Architecture Delta

- Public type renamed: `SimulationNodeId` → `NodeId` with no alias.
- Public type owner moved: `simulator_input/identity.py` → `node_id.py`.
- Public operation added: `create_emulator_node_index(...)`.
- Public root exports: unchanged at the six entity/value names.
- Entity attributes, constructors and ownership: unchanged.
- NodeId derivation: centralized in the cross-layer generation module.
- Module dependency: `simulator_input → node_id → identifier`; no back-edge to
  entities or seedemu.
- Cross-layer contract: static input maps NodeId to pure data; the Emulator
  index maps NodeId to the original entity.
- Class diagrams: NodeId is now a neutral contract value; no index, resolver,
  plan or Simulator class was added.
- Example calls: complete and large add explicit index generation and ID-set
  validation; isolated is unchanged at the identity boundary.
- Formal execution path: no new runtime path; compilation and generated output
  controls remain unchanged.
