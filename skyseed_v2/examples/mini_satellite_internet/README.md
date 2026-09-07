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

Options select scenario/Zone input, output root, replacement behavior, and an
InternetMap fault-isolation switch. The caller-selected Python interpreter is
used throughout generation.

## Domain chain

Mini uses a 10-second observation interval and 64 formal boundaries. Its
Simulator is constructed with:

- `CircularOrbitPositionModel`;
- `GeometricPhysicalLinkModel`;
- explicit maximum-4 ISL capability;
- K3 / desired 2+2 retain-first ISL planning;
- 20-second acquisition reference;
- independent GroundStation and Zone planning;
- accumulated parameter deadband;
- gateway hop-count Route planning;
- 50-second planning and 20-second publication horizons.

The producer primes bounded prehistory, advances every formal frame exactly
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
