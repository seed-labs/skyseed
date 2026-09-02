# Maintained examples

| Example | Role | Entry |
|---|---|---|
| `complete_static_emulator` | smallest public construction and Plan-to-Compiler teaching chain | `python -m examples.complete_static_emulator.main` |
| `mini_satellite_internet` | bounded generated runtime with common producer, persisted handoff, Runner and Traffic integration | `python -m examples.mini_satellite_internet.main` |
| `starlink_gen1_satellite_internet` | canonical S3 offline physical, Planner, and Compiler qualification | `python -m examples.starlink_gen1_satellite_internet.main --through compiler` |

All maintained examples construct the same domain chain:

```text
SimulationTimeline
→ PhysicalWorld / PhysicalFrame
→ PhysicalLinkObservation
→ Planner / PlannedOperationalState
→ Plan
→ BPF Compiler / BpfCompiledRealization
```

The deployment-capable examples persist compiled realizations before Runner
execution. No example contains a separate batch algorithm or synchronous heavy
semantic callback from Runner maintenance.

Each entry anchors its default output to its own source directory, independent
of the caller's current working directory:

```text
examples/<example>/output/
├── inputs/                 generated input snapshots where supported
├── domain-history/         generated semantic history
├── inspection/             generated human-facing Tools
├── inspection-evidence/    detached non-authoritative Tool evidence
└── runtime/                generated runtime package and realization handoff
```

These directories are generated output, not source-controlled example
content. For normal human inspection, open `output/inspection/index.html`.

Run either from the repository root with `python -m examples.<example>.main`
or from that example directory with `python main.py`. Use `--output PATH` for
an explicit alternate root. Generation replaces only that owned output root
when override is enabled. For deployment-capable examples, run the generated
`cleanup.sh` before regenerating an output whose Compose project still exists.

`starlink_gen1_satellite_internet` preserves the full canonical scenario: 720
Satellites, 355 GroundStations, 732 Zones, 121 five-second formal timestamps,
K3 candidate scope, 2+2 selection target, acquisition reference 20 seconds,
planning horizon 50 seconds, and publication horizon 20 seconds.
Its Compiler-mode output is also the maintained human inspection dataset:
map-first Physical World and Planner views use compact exact timestamp chunks
(including Physical World inspection of exact `PhysicalFrame` facts), Compiler
uses a trend dashboard, and Observation explains
coarse outer orchestration time. These detached artifacts record facts already
computed by the chain and are not inputs to domain semantics.

`reference/` is labelled offline provenance material and is not imported by a
maintained runtime entry.
