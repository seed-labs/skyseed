# Maintenance map

Change the owning contract, tests, examples, and docs together.

## Public package classification

| Public package | Owner | Local README |
|---|---|---|
| `skyseed_v2` | narrow entity facade | `src/skyseed_v2/README.md` |
| `skyseed_v2.configuration` | declaration namespace | `src/skyseed_v2/configuration/README.md` |
| `skyseed_v2.configuration.emulator` | Emulator declarations/YAML | `src/skyseed_v2/configuration/emulator/README.md` |
| `skyseed_v2.configuration.simulator` | Zone declarations/YAML | `src/skyseed_v2/configuration/simulator/README.md` |
| `skyseed_v2.emulator` | static Emulator construction | `src/skyseed_v2/emulator/README.md` |
| `skyseed_v2.emulator.entities` | Emulator entity tree | `src/skyseed_v2/emulator/entities/README.md` |
| `skyseed_v2.emulator.seedemu` | upstream SEED extension boundary | `src/skyseed_v2/emulator/seedemu/README.md` |
| `skyseed_v2.shared` | narrow neutral values/mechanics | `src/skyseed_v2/shared/README.md` |
| `skyseed_v2.simulator` | Simulator composition and public facade | `src/skyseed_v2/simulator/README.md` |
| `skyseed_v2.simulator.input` | receiver-owned immutable DTOs | `src/skyseed_v2/simulator/input/README.md` |
| `skyseed_v2.simulator.geometry` | Earth-fixed values/calculations | `src/skyseed_v2/simulator/geometry/README.md` |
| `skyseed_v2.simulator.physical` | PhysicalWorld, frame, candidates, Zone | `src/skyseed_v2/simulator/physical/README.md` |
| `skyseed_v2.simulator.models` | built-in physical Models | `src/skyseed_v2/simulator/models/README.md` |
| `skyseed_v2.simulator.planning` | Planner, Plan, Models, replay, evidence | `src/skyseed_v2/simulator/planning/README.md` |
| `skyseed_v2.backend.bpf` | Compiler, compiled intended state, Runner | `src/skyseed_v2/backend/bpf/README.md` |
| `skyseed_v2.compose` | compiled Compose project utility | `src/skyseed_v2/compose/README.md` |

## Path-to-verification map

| Changed area | Required focused verification |
|---|---|
| `simulator/physical/`, geometry, physical Models | physical-world/frame/link facts, capability, Zone, geometry, Tool boundaries |
| `simulator/planning/` | public imports, Model injection, lifecycle, replay, window/publication, Route workers, evidence codecs |
| `simulator/{timeline,simulator}.py` | Timeline and one-owner lifecycle tests |
| `experiment/_realization_producer.py` | cursor/prehistory/counts/buffering/cleanup and canonical producer qualification |
| `backend/bpf/compiler.py`, compiled realization | bootstrap/steady lineage, semantic lowering, no-op activation, wrong-source tests |
| Runner artifact/execution | typed seals/lineage, mmap open, continuous-vs-batched semantics, bounded admission/reclaim, READY deadline and Main overlap |
| Traffic | explicit planned-unreachable and reachable-loss classification |
| `tools/` | detached reader/projection/schema tests and browser visual gate |
| maintained examples | complete, Mini, single-Shell and S1–S5 shared-network composition, packaging, stage progression |
| public `__init__.py` | package import, `__all__`, import graph, this map |
| current architecture docs | documentation gate, Mermaid/browser gate, link audit |

## Growth rules

New production code belongs to an existing owner unless an approved new
ownership or lifecycle boundary requires a package. A public package needs a
local README, public-import mapping, maintenance row, examples/tests mapping,
and architecture review in the same change.

Do not create empty future directories, forwarding modules, duplicate public
imports, runtime selectors, or blanket documentation duplication. Exact
qualification evidence stays outside the repository.

## Private computation implementation locations

`simulator/models/_batched_geometry.py` and `_contact_prediction.py` implement
array queries and compact contact proof inside the existing physical Model.
`planning/models.py` retains selection/preparation policy, delay memory and
Zone/Route reuse; `_route_computation.py` owns the existing grouped worker engine.
Compiler's private indexes remain in backend realization/compiler code. Ordered
serialization in `shared/_durable_evidence.py` is authority-free I/O used by the
existing Producer and inspection writer. Domain-history v2 extends the same
seal to physical-only cutoff; it creates no new public component. `_runner_artifact.py`
still packages/reads backend input, kernel_netlink.py executes private Linux work,
and process_incarnation.py captures startup identity. No dependency direction or
Runner lifecycle changed. The owner and dependency diagrams therefore keep their
existing ownership edges; member inventories track the changed public contracts.

The final optimization round keeps these responsibilities in their existing
owners: `_ground_station_allocation.py` certifies retained coverage and seeds an
exact global residual solve; `_route_computation.py` retains only current-request
paths with explicit ISL/GS dependencies. `_runner_artifact.py` groups batch facts
once, while `_runner/runtime_realization.py::_BoundaryStateIndexes` supports the
finite capacity and Preparation passes. Capacity destinations use reference
counts so shared forwarding does not inflate demand. Tool projection and bundle
writers own their direct compact records, input detachment and payload hashes.
These helpers are private derived representations, not additions to public
owner/class diagrams.

The generated shared-image representation and unique image checks remain private
in `compose/launcher.py`; the optional public launcher still consumes an
already-generated project. Traffic's semantic-only history record is private to
`tools/planner/reader.py` and reused by `tools/traffic/reader.py`; it does not add
a domain contract. `tools/_html.py` owns atomic streaming JSON output, including
record-wise compact Traffic encoding. Starlink's existing `main.py`/`simu.py`
expose the authorized shell/cadence choices and derive counts from Timeline.
