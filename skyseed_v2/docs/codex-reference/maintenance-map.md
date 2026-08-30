# Maintenance map

## Authority

Normative file-level synchronization map under `../accepted-design.md`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
Package README files link here and do not duplicate it.

## Scope

Production packages, examples, repository Tools, public contracts, and tests.

## Repository growth rules

- New production code first belongs to an existing owner package.
- A new package is justified only by an approved new public owner, lifecycle,
  or dependency boundary. File count and single-file line count are not
  package-boundary reasons.
- A new top-level package, public subpackage, or cross-layer boundary requires
  prior user/GPT approval.
- Do not create empty directories for future capabilities.
- A new public package must include, in the same change: a local package
  README; a public-import contract row; a maintenance-map entry; an updated
  repository/import map; a local class/member diagram; and an examples/tests
  mapping.
- Adding an ordinary private module inside an existing owner package does not
  trigger repository restructuring.
- Package README files are maintained by long-lived owner responsibility, not
  regrouped around the latest development phase.
- Compatibility forwarding and simultaneous old/new import paths are
  prohibited.
- A package split based only on adding two modules is prohibited. An approved
  owner, lifecycle, or dependency boundary is required regardless of module
  count.

The documentation gate discovers public packages from `__init__.py` files
that declare `__all__`. It requires every discovered package to be approved
and classified below, mapped in `public-imports.md`, and backed by a local
README. It derives public class ownership from each class's implementation
module and requires every newly exported public class to appear in exactly one
owner README detailed diagram. New owner/lifecycle relations must enter that
local map and, when they change a cross-package system relation, the global
object model.

### Public package classification

| Public package | Owner classification | Local README |
|---|---|---|
| `skyseed_v2` | narrow root entity facade | `src/skyseed_v2/README.md` |
| `skyseed_v2.backend.bpf` | symbolic BPF Compiler/immutable handoff and live BpfRunner lifecycle | `src/skyseed_v2/backend/bpf/README.md` |
| `skyseed_v2.compose` | compiled Compose project launcher/evidence | `src/skyseed_v2/compose/README.md` |
| `skyseed_v2.configuration` | declaration namespace boundary | `src/skyseed_v2/configuration/README.md` |
| `skyseed_v2.configuration.emulator` | Emulator declarations and YAML | `src/skyseed_v2/configuration/emulator/README.md` |
| `skyseed_v2.configuration.simulator` | Simulator Zone declarations and YAML | `src/skyseed_v2/configuration/simulator/README.md` |
| `skyseed_v2.emulator` | construction facade | `src/skyseed_v2/emulator/README.md` |
| `skyseed_v2.emulator.entities` | Emulator entity owner tree | `src/skyseed_v2/emulator/entities/README.md` |
| `skyseed_v2.emulator.seedemu` | extractable upstream SEED extension boundary | `src/skyseed_v2/emulator/seedemu/README.md` |
| `skyseed_v2.shared` | narrow neutral values | `src/skyseed_v2/shared/README.md` |
| `skyseed_v2.simulator` | Simulator composition/lifecycle facade | `src/skyseed_v2/simulator/README.md` |
| `skyseed_v2.simulator.geometry` | Earth-fixed values and calculations | `src/skyseed_v2/simulator/geometry/README.md` |
| `skyseed_v2.simulator.input` | receiver-owned immutable DTOs | `src/skyseed_v2/simulator/input/README.md` |
| `skyseed_v2.simulator.models` | built-in Model implementations | `src/skyseed_v2/simulator/models/README.md` |
| `skyseed_v2.simulator.planning` | incremental semantic planning owner | `src/skyseed_v2/simulator/planning/README.md` |
| `skyseed_v2.simulator.slicing` | dynamic contracts, retained slices, Difference | `src/skyseed_v2/simulator/slicing/README.md` |
| `skyseed_v2.simulator.topology` | static aggregate and Zone owner | `src/skyseed_v2/simulator/topology/README.md` |

## Owners and consumers

| Changed path | Owner/contract | Human diagram | Example/Tool consumer | Required focused verification |
|---|---|---|---|---|
| `backend/` | non-facade downstream backend namespace; BPF is the primary implemented backend | `src/skyseed_v2/backend/README.md` | package navigation only | import/architecture gates; another backend requires prior architecture approval |
| `backend/bpf/` | downstream symbolic BPF lowering plus live Runner lifecycle | `src/skyseed_v2/backend/bpf/README.md` | canonical mini Emulator/Simulator/Planner/Compiler/Runner chain | focused Compiler/Runner contracts, architecture gates, canonical main-chain and real runtime smoke |
| `backend/bpf/_runner/` | private BpfRunner runtime-realization, privilege-command and telemetry implementation grouping | `src/skyseed_v2/backend/bpf/_runner/README.md` | no direct example/public consumer; used only by BpfRunner | focused Runner/telemetry contracts and privilege-boundary tests |
| `configuration/emulator/` | Emulator declarations/YAML | `src/skyseed_v2/configuration/emulator/README.md` | all three maintained runnable examples | static configuration and YAML tests |
| `configuration/simulator/` | Zone declarations/YAML | `src/skyseed_v2/configuration/simulator/README.md` | canonical Mini, static Starlink Gen1, Zone Tool | Zone and YAML tests |
| `emulator/entities/` | Emulator entity owner tree | `src/skyseed_v2/emulator/entities/README.md` | all three maintained runnable examples, compiler/projector | entity, identity, integration tests |
| `emulator/seedemu/` | upstream SEED extension boundary | `src/skyseed_v2/emulator/seedemu/README.md` | all three maintained runnable examples | SEED lifecycle, query helper, standard compiler tests |
| `emulator/construction.py` | config-driven Emulator entry | `src/skyseed_v2/emulator/README.md` | all three maintained runnable examples | creation/equivalence tests |
| `emulator_to_simulator.py` | sole cross-layer projection | `human-readable/system-overview.md` | all Simulator examples | static-input construction and boundary tests |
| `simulator/input/` | receiver-owned immutable DTO | `src/skyseed_v2/simulator/input/README.md` | bridge/definition | static-input and dependency tests |
| `simulator/topology/` | static aggregate/Zone owner | `src/skyseed_v2/simulator/topology/README.md` | Models and all Tools | topology and Zone tests |
| `simulator/geometry/` | ECEF/geodetic/distance/LOS | `src/skyseed_v2/simulator/geometry/README.md` | built-in Models, dynamic Starlink Gen1, Slice / Difference Tool views | geometry/model tests and SCC |
| `simulator/slicing/` | dynamic contracts, Slicer, Slice, Difference | `src/skyseed_v2/simulator/slicing/README.md` | Models/Slice / Difference Tool | main-chain, Difference, Tool boundaries |
| `simulator/models/` | built-in Position/Connection/Route algorithms | `src/skyseed_v2/simulator/models/README.md` | canonical Mini and stage-bounded Starlink Gen1 | focused main-chain/model/Starlink Gen1 tests |
| `simulator/planning/` | semantic Planner, Plan and planning algorithms | `src/skyseed_v2/simulator/planning/README.md` | complete canonical `simu.py` | planning value/algorithm/main-chain tests |
| `simulator/{definition,timeline,simulator}.py` | Simulator composition/lifecycle | `src/skyseed_v2/simulator/README.md` | Simulator examples | lifecycle/timeline/Planner tests |
| `compose/launcher.py` | compiled Compose project lifecycle | `src/skyseed_v2/compose/README.md` | `complete_static_emulator` and canonical Mini | launcher and integration tests |
| `shared/` | neutral identity/time plus authority-free durable-evidence and optional observation mechanics | `src/skyseed_v2/shared/README.md` | configuration, Emulator, Simulator, generated execution, BpfRunner, Observation Tool | identity/time, continuous-progression/Runner history, and observation-record tests |
| `tools/topology/` | read-only static projection/UI/artifacts | `human-readable/traceability-map.md` | all three maintained runnable examples | Topology Tool and tooling-boundary tests |
| `tools/zone/` | static Zone layout read/projection/direct-selection UI/artifacts | `human-readable/traceability-map.md` | all three maintained runnable examples | Zone Tool and tooling-boundary tests |
| `tools/slice/` | formal Slicer reads + display/UI/artifacts | `human-readable/traceability-map.md` | canonical Mini | Slice / Difference Tool tests and browser smoke |
| `tools/planner/` | formal Slicer/Plan/decision reads + private comparison UI/artifacts | `human-readable/traceability-map.md` | dynamic Planner inspection entry | Planner Tool and tooling-boundary tests |
| `tools/observation/` | detached execution-record parsing/aggregation/UI/artifacts | `human-readable/traceability-map.md` | canonical Mini and stage-bounded Starlink Gen1 | Observation record/Tool tests and page review |
| `tools/bpf_compiler/` | immutable Plan/compiled-result reads + private lowering UI/artifacts | `human-readable/traceability-map.md` | Mini backend branch and inspection entry | BPF Compiler Tool tests and browser smoke |
| `tools/runner/` | detached post-run Runner event projection/UI/artifacts | `human-readable/traceability-map.md` | generated Mini automatic runtime | Runner Tool, runtime-finalization and tooling-boundary tests plus controlled-interruption page review |
| `tools/traffic/` | durable traffic/Slice / Difference/Compiler-evidence read plus optional planned-workload comparison context, exact-generation network validation, ICMP sustained-series/path projection, offline simulation/packet replay UI/artifacts | `human-readable/traceability-map.md` | generated Mini automatic runtime | Traffic replay focused tests plus canonical page review |
| `tools/_detached_inspection.py` | private bounded process scheduling, temporary detached snapshots, checksum-audited staging, atomic publication, and control-state cleanup | `accepted-design.md` | normal stage-bounded Starlink Gen1 execution | detached process/PID/cleanup tests and rolling-order integration test |
| `scripts/generate_zone_layout.py` | offline deterministic whole-globe Zone YAML authoring through formal configuration definitions | Starlink Gen1 example README | explicit configuration maintenance only | Zone generator exact-reproduction/check-mode tests |
| `scripts/refresh_starlink_gen1_ground_stations.py` | SHA-pinned reviewed-evidence refresh for the frozen Starlink Gen1 GroundStation YAML | Starlink Gen1 example README | explicit input maintenance only | frozen-input/source-SHA tests |
| public package `__init__.py` | import surface | `human-readable/repository-map.md` | all users | public import/export + wheel checks |
| `docs/accepted-design.md` | architecture anchor | system diagram links | all maintenance | focused anchor consistency |
| `docs/CURRENT.md` | implementation state | none | all maintenance | focused status consistency |

## Contract

The changed path selects the owner contract and verification; it does not make
every documentation file a required edit. Update a human diagram only when
its represented relation changes.

## Invariants

- one global fact has one diagram home;
- package README remains local and is the sole detailed class/member source;
- public API changes always update `public-imports.md`;
- state changes only update `CURRENT.md`;
- candidate/provenance records never satisfy a current contract update.

## Failure semantics

Focused documentation checks fail if an owner README or mapped contract is
missing, a public package is unclassified, a public class lacks one detailed
owner source, or a critical local owner/reference relation disappears.
Architecture tests fail on forbidden dependencies or stale public paths.

## Prohibited behavior

No search-keyword identity guessing, duplicate global diagram, or blanket
“update every Markdown” rule.

## Extension points

Add a classification and owner row only when a real production/Tool ownership
area is added through an approved architecture change.

## Maintenance

Keep paths exact. Public-package and public-class coverage is discovered from
code; the explicit approved-package set in `scripts/check_documentation.py`
changes only with the prior approval required above.
