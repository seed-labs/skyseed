# Pure NodeId Contract and Owner-Directed Lookup Validation

Date: 2026-07-19  
Baseline HEAD: `23450bf4c505fb4dd8be6aa4929470b72ee55feb`  
Python: `3.12.3`  
pytest: `9.1.1`

## Scope and result

This slice supersedes the previously proposed public Emulator node index. It
keeps the `NodeId` rename and deterministic static-input derivation, but removes
all public/global `NodeId → Emulator entity` mapping behavior.

Simulator algorithms, positions, links, routing, plans, Compiler resolution,
runtime updates and container startup remain unimplemented. Simulator status
is **NOT STARTED**.

## Pure NodeId contract

`NodeId` remains defined only in `skyseed_v2.node_id` with the existing fields:

```text
asn
entity_kind
entity_id
```

It is frozen, hashable and contains no `resolve`, `get_entity`, `get_container`,
Registry, Compose, service, namespace or interface capability. Satellite and
GroundStation do not accept or store it, and the root package does not export
it. No `SimulationNodeId` alias or old module remains.

`simulator_input_generation._derive_node_id(...)` is the only implementation
used by all three public input-generation functions. The private traversal
covers only:

```text
SkyseedAutonomousSystem → Shell → Orbit → Satellite
SkyseedAutonomousSystem → GroundStation
```

Ordinary Routers, Hosts and other SEED nodes are outside this contract.

## No global node index

The public `create_emulator_node_index(...)` proposal was removed from source,
`__all__`, examples, normative documentation and wheel contents. No
`BoundaryResult`, `NodeBindingRegistry`, `NodeResolverManager`,
`GlobalNodeRegistry`, resolver class or global NodeId/entity mapping exists.

`SimulatorStaticInput` still has its own private read-only NodeId lookup over
pure `SatelliteSimulationInput` and `GroundStationSimulationInput` values. That
is a data-aggregate query, not an entity/resource index. It contains no
Emulator, SEED Node, Registry, Compiler, Compose or container object.

## Existing owner API audit

No new lookup method was needed or added.

| Owner | Existing lookup | Uniqueness/ownership | Unknown value |
| --- | --- | --- | --- |
| `SatelliteBase` / upstream `Base` | `getAutonomousSystem(asn)` | Base-owned ASN table | `AssertionError` |
| `SkyseedAutonomousSystem` | `get_shell(shell_id)` | AS-owned Shell dictionary | `KeyError` |
| `SkyseedAutonomousSystem` | `get_satellite(satellite_id)` | IDs are unique across every Shell/Orbit in one AS through the shared ID set | `KeyError` |
| `SkyseedAutonomousSystem` | `get_ground_station(ground_station_id)` | AS-owned GroundStation dictionary | `KeyError` |
| `Shell` | `get_orbit(orbit_id)` | Shell-owned Orbit dictionary | `KeyError` |
| `Orbit` | `get_satellite(satellite_id)` | Orbit-owned Satellite dictionary | `KeyError` |

Tests exercise each successful lookup and unknown-ID failure. Existing tests
also prove duplicate Satellite IDs fail across Orbits/Shells of one AS and
duplicate GroundStation IDs fail in their AS.

## Future Compiler contract

No resolution code was implemented. The documented future path is:

```text
plan NodeId.asn
→ SatelliteBase.getAutonomousSystem(asn)
→ require SkyseedAutonomousSystem
→ dispatch NodeId.entity_kind
→ get_satellite(NodeId.entity_id) or
  get_ground_station(NodeId.entity_id)
→ real owned entity
→ later Compiler-specific resource lowering
```

Hierarchy-specific lookup may instead continue through existing
`get_shell()` → `get_orbit()` → `get_satellite()` calls. A future implementation
must fail closed on missing ASN/ID, non-Skyseed AS, unsupported kind, kind/type
mismatch or ambiguous ownership. It must not parse names, scan containers,
retain a global registry or use a fallback query path.

## Simulator and dependency boundary

The future Simulator receives only `SimulatorStaticInput`; within that boundary
a `NodeId` can retrieve only pure static input data. The `simulator_input`
package and neutral `node_id` module import no entities, seedemu integration,
upstream `seedemu`, Compiler, Registry, Compose or container code. No Simulator
module exists that could import owner lookup or entity resolution.

## Commands and results

```text
PYTHONPATH=src:. ../.venv/bin/python -m compileall -q src tests examples
PASS

focused identity/input/owner/example tests
67 passed in 9.15s

focused architecture/documentation tests
34 passed in 0.51s

PYTHONPATH=src:. ../.venv/bin/pytest -q
147 passed in 9.84s
```

The wheel was built with `pip wheel --no-deps --no-build-isolation`, installed
without dependencies into a new venv and imported outside the source tree.
The wheel contains `node_id.py`, `simulator_input_generation.py` and
`simulator_input/static_input.py`; generation contains no index function or
`MappingProxyType`, the old identity module is absent, and pure NodeId/input
imports pass.

No container build, Compose `up` or Simulator execution was run.

## Architecture Delta

- Public type: `NodeId` remains the alias-free replacement for
  `SimulationNodeId`; module and fields are unchanged.
- Public operation removed: `create_emulator_node_index(...)`.
- Public operation added: none.
- Entity lookup methods added: none; existing owner APIs are sufficient.
- Entity attributes, constructors and ownership: unchanged.
- Root exports: unchanged at six entity/value names.
- Static input: remains pure immutable data with private pure-data lookup.
- Cross-layer resolution: changed from a proposed shared entity Mapping to a
  documented future sequence of narrow Base/AS/Shell/Orbit owner calls.
- Dependencies: no Simulator-to-Emulator or input-to-entity back-edge.
- Class diagrams: no index/resolver type; existing owner query methods are now
  shown explicitly.
- Examples: complete and large generate only `SimulatorStaticInput`; isolated
  remains free of NodeId/static-input/resolution concepts.
- Formal runtime path: unchanged; no plan, resolution or update execution path
  was added.
