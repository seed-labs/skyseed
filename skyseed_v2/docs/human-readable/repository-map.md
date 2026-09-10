# Repository map

```text
skyseed_v2/
├── src/skyseed_v2/
│   ├── configuration/              immutable input declarations
│   ├── emulator/                   static Emulator construction
│   ├── simulator/
│   │   ├── physical/                 PhysicalWorld, PhysicalFrame, physical-link contracts
│   │   ├── planning/                 Planner, Plan, Models, replay, evidence
│   │   ├── geometry/                 positions and geometric calculations
│   │   ├── models/                   built-in physical Models
│   │   ├── input/                    immutable Simulator inputs
│   │   ├── simulator.py              composition root
│   │   └── timeline.py               finite formal timestamp authority
│   ├── backend/bpf/               Compiler, compiled realization, Runner
│   ├── experiment/                producer, generation binding, execution, Traffic
│   ├── compose/                   deployment utility
│   └── shared/                    narrow neutral values and private mechanics
├── tools/                         detached inspection readers/projectors/renderers
├── examples/                      maintained human-review and scenario entry points
├── tests/                         unit, integration, Tool, and architecture contracts
├── docs/                          accepted design, current status, references, guides
└── scripts/                       deterministic repository gates
```

## Planning package

```text
simulator/planning/
├── planner.py       authoritative public Planner
├── values.py        operational state, transitions, Plan, replay, fingerprints
├── models.py        public Protocols and reference Models
├── coordination.py  precedence policy and validation
├── evidence.py      versioned detached codecs
└── README.md        local contract
```

## Physical package

```text
simulator/physical/
├── world.py           PhysicalWorld and static entities
├── physical_frame.py  immutable timestamped positions
├── physical_link.py   request, observation, candidates, Model Protocol
├── position.py        PositionModel Protocol
├── capability.py      ISL capability
├── zone.py            Zone geometry/layout/membership
└── README.md          local contract
```

## Private BPF artifact and execution files

These files implement existing backend responsibilities; they introduce no
public domain component or independent lifecycle owner.

| File under `src/skyseed_v2/backend/bpf/` | Responsibility |
|---|---|
| `_runner_artifact.py` | Backend artifact packaging and indexed reading; normalize compiled facts, accumulate capacity, and validate/seal the finite sequence. |
| `_runner/runtime_realization.py` | Derive and prepare immutable finite deltas and wire commands, prewrite generations, and apply current-boundary results. |
| `_runner/privileged_runtime_command.py` | Existing persistent helper, retained namespace sockets/BPF descriptors, bounded deletion workers and owned retirement ledger. |
| `_runner/kernel_netlink.py` | Private Linux netlink execution and kernel acknowledgments. |
| `_runner/process_incarnation.py` | Startup process and namespace identity capture. |
| `_runner/event_history.py`, `_runner/traffic_telemetry.py` | Existing asynchronous event and identity observation facilities. |

The old `experiment/_realization_handoff.py` is removed. Experiment orchestration
uses the backend artifact writer and records generated source/input binding;
Runner reads and fully decodes the artifact during Preparation. Prepared and
derived records, capacity accumulation, logical ports and live bindings remain
private representations. All deletion jobs for a boundary join inside Main;
the helper's ledger is cleanup responsibility, not a cross-boundary service.
These files remain inside the BPF owner group in the dependency graph below.

## Exact collapsed production import graph

Legend: `-->` is one direct production import after modules are collapsed to
their documented owner group. The graph is generated conceptually from parsed
Python imports; every edge is checked for exact equality by the documentation
gate.

```mermaid
flowchart LR
    BPF --> SHARED
    BPF --> SIM
    BRIDGE --> ENTITIES
    BRIDGE --> INPUT
    BRIDGE --> SEED
    BRIDGE --> SHARED
    CONFIG --> CONFIG_EMU
    CONFIG --> CONFIG_SIM
    CONFIG --> SHARED
    CONFIG_EMU --> CONFIG
    CONFIG_EMU --> SHARED
    CONFIG_SIM --> CONFIG
    CONFIG_SIM --> SHARED
    EMU --> CONFIG_EMU
    EMU --> ENTITIES
    EMU --> SEED
    ENTITIES --> SHARED
    EXPERIMENT --> BPF
    EXPERIMENT --> COMPOSE
    EXPERIMENT --> PLANNING
    EXPERIMENT --> SHARED
    EXPERIMENT --> SIM
    GEOMETRY --> SHARED
    INPUT --> SHARED
    MODELS --> GEOMETRY
    MODELS --> PHYSICAL
    MODELS --> SHARED
    PHYSICAL --> CONFIG_SIM
    PHYSICAL --> GEOMETRY
    PHYSICAL --> INPUT
    PHYSICAL --> SHARED
    PLANNING --> PHYSICAL
    PLANNING --> SHARED
    PLANNING --> SIM
    ROOT --> EMU
    ROOT --> ENTITIES
    SEED --> ENTITIES
    SEED --> SHARED
    SIM --> CONFIG_SIM
    SIM --> GEOMETRY
    SIM --> INPUT
    SIM --> MODELS
    SIM --> PHYSICAL
    SIM --> PLANNING
    SIM --> SHARED
```

## Inspection outputs

Maintained output roots are `inspection/physical-world`, `zone`,
`planner`, `bpf-compiler`, `runner`, `observation`, and
`traffic`. Producer-owned durable inputs live under `domain-history`.

No package directory or output root is retained solely to forward an obsolete
contract.

The standalone `examples/starlink_multi_shell_satellite_internet` composes the
existing S1–S5 definitions and shared policy/packaging support; it adds no
production package. `backend/bpf/_runner_artifact.py` carries sealed delivery
metadata, while the existing Runner and private helper own loading/prewriting/
reclaim and Main activation. The dependency arrows above remain unchanged.
The lightweight retained-output entry links original Tool portals, without an
additional application or payload owner.
