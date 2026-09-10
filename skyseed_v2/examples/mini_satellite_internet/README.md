# Mini satellite Internet

Mini is the bounded end-to-end generated-runtime reference. It creates the
static Emulator, projects one Simulator, drains the common incremental
realization producer, seals a finite Runner artifact, and packages Runner and
Traffic execution.

```bash
python -m examples.mini_satellite_internet.main
```

The same entry can be run as `python main.py` from this directory. Both forms
default to `examples/mini_satellite_internet/output/`, anchored to this source
directory rather than the shell's current directory. Use `--output PATH` for
an alternate owned root and `--no-override` to refuse replacement. If runtime
containers were started, run the generated `cleanup.sh` before regeneration.

`--through physical-world|planner|compiler` selects the cutoff of the same
finite producer (Compiler is the default). Physical mode computes contact
prediction and all formal physical observations; Planner mode also seals Plans.
Options select scenario/Zone input, output root, replacement behavior, and an
InternetMap fault-isolation switch. The caller-selected Python interpreter is
used throughout generation.

The currently retained [normal output](output/) is a **historical partial subset**:
inputs, domain history, raw invocation evidence and original manifests/checksum
records remain, but the complete Runner binary and current Tool portal are absent.
Those original checksums describe the former complete bundle, not a fresh passing
integrity check of this subset. Closure preserves this state without running Mini.
Future normal generation may replace the Git-ignored output.

## Domain chain

Mini uses a 10-second observation interval and 64 formal boundaries. Its
Simulator is constructed with:

- `CircularOrbitPositionModel`;
- `GeometricPhysicalLinkModel`;
- explicit maximum-4 ISL capability;
- K3 / desired 2+2 retain-first ISL planning;
- 5-second ISL/GS preparation, completed at the next 10-second formal sample;
- 180/60-second ISL/GS future-service admission;
- GS coverage then continuity then quality; independent highest-elevation Zone planning;
- 10% relative delay threshold and 120-second refresh, without target persistence;
- gateway hop-count Route planning;
- 32-frame batches: two Plans for the unchanged 64 formal boundaries.

The producer prepares compact physical contact proof, primes one 10-second
prehistory sample, advances every formal frame exactly
once, publishes self-contained Plans, compiles them incrementally, and shuts
down Route workers.

## Generated output

```text
output/
├── inputs/                         immutable generation snapshots
├── runtime/                        Docker project and runner-artifact/
├── domain-history/
│   ├── physical-frames.jsonl
│   ├── planning.jsonl.gz
│   ├── compiler.jsonl
│   └── domain-history-manifest.json
├── generation-status.json          materials-ready and Tool-completion status
├── inspection/                     detached physical/planning/compiler/observation views
├── start.sh
├── run.sh
├── launch.sh
└── cleanup.sh
```

Generation's writer validates the finite sequence relationships before sealing.
Generated execution validates source/input identity, manifest schema/seal,
binary length/hash and timeline metadata. BpfRunner opens the binary with
read-only mmap, checks its header/index bounds and decodes the entire indexed
sequence with per-record checks during Preparation. Preparation builds all deltas,
prewrites semantic generations and physically activates B0. Formal `run()` starts at B1 and completes the
remaining 63 boundaries. Planning and compilation do not run during Runner
execution.

`main.py` generates all output, realizations, the artifact and controls before
deployment. Generated `start.sh` deploys; `run.sh` starts Runner/Traffic;
`launch.sh` combines start and run. `cleanup.sh` remains independent. The common
[example operating sequence](../README.md) describes these responsibilities.

Mini delegates `run.sh` and `launch.sh` publication to the common generated
runtime-control writer used by canonical production output. Each live run owns
one immutable `inspection/runtime-runs/<INVOCATION_ID>/` root for raw Runner,
Traffic, and Observation evidence; top-level inspection pages remain detached
derived views. Mini supplies only its distinct required-input inventory and
optional Traffic workload path to that common owner.

After runtime, Runner and Traffic Tools consume durable records. Traffic uses
explicit planned Route outcomes to distinguish planned-unreachable probes from
activation-time transience and unexplained loss.

Every Tool is best-effort relative to the valid Runner artifact: a Tool failure
is reported but cannot invalidate runtime input.

Preparation reserves both endpoint capacities, persists across samples, and
cancels on loss. Minimum useful service is checked forward from completion;
full-port replacement has a real preparation gap. Finite proof endpoints are
not disappearance events. Mini's rounded preparation yields physical tails of
190 seconds (ISL) and 70 seconds (GS), with no extra formal transitions.

In Compiler mode, all required runtime inputs and controls are ready before
optional Tools. `generation-status.json` records that boundary and later Tool
completion/failure separately. Mini's CLI collects the Tool results; it does not
start generated controls. Static teaching examples retain their own start/cleanup
flow and do not acquire this Runner chain.
