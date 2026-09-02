# Mini satellite Internet

Mini is the bounded end-to-end generated-runtime reference. It creates the
static Emulator, projects one Simulator, drains the common incremental
realization producer, persists compiled realizations, and packages Runner and
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
├── runtime/                        compiled Docker project and realization handoff
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

Generated execution validates the format-version-3 manifest and its
`deterministic-gzip-json-v1` realization records, then supplies only persisted
realizations to BpfRunner. Planning and compilation do not run during Runner
maintenance.

After runtime, Runner and Traffic Tools consume durable records. Traffic uses
explicit planned Route outcomes to distinguish planned-unreachable probes from
activation-time transience and unexplained loss.

Every Tool is best-effort relative to the valid compute/runtime handoff: a Tool
failure is reported but cannot invalidate the persisted realization sequence.
