# Maintained examples

| Example | Role | Entry |
|---|---|---|
| `complete_static_emulator` | smallest public construction and Plan-to-Compiler teaching chain | `python -m examples.complete_static_emulator.main` |
| `mini_satellite_internet` | bounded generated runtime with common producer, sealed Runner artifact, Runner and Traffic integration | `python -m examples.mini_satellite_internet.main` |
| `starlink_multi_shell_satellite_internet` | independently planned S1–S5 with 355 shared GS containers, five-second samples and two sealed deliveries in one session | `python -m examples.starlink_multi_shell_satellite_internet.main` |
| `starlink_gen1_satellite_internet` | S3-default physical, Planner and Compiler generation; explicit S1/cadence qualification | `python -m examples.starlink_gen1_satellite_internet.main --through compiler` |

All maintained examples construct the same domain chain:

```text
SimulationTimeline
→ PhysicalWorld / PhysicalFrame
→ PhysicalLinkObservation
→ Planner / PlannedOperationalState
→ Plan
→ BPF Compiler / BpfCompiledRealization
```

Mini and Starlink's Compiler mode completely drain compilation and seal a typed
packed artifact before Runner execution. No producer or semantic callback
exists during Runner Main. Preparation decodes and prepares the first finite
delivery and physically activates B0; formal run executes B1 onward. The
multiple-shell Example registers a sealed successor for bounded Maintenance
loading/prewrite, deadline-bound activation and eligible runtime reclamation.

Mini and Starlink Compiler mode use the same operating sequence:

| Entry/control | Responsibility |
|---|---|
| `main.py` | Generate complete output, compiled realizations, sealed finite Runner artifact and controls. |
| generated `start.sh` | Deploy the generated Compose project. |
| generated `run.sh` | Start Runner/Traffic against that deployment and write invocation evidence and detached runtime inspection. |
| generated `launch.sh` | Combine start and run. |
| generated `cleanup.sh` | Independently tear down the owned deployment. |

The smaller teaching example and the Starlink physical-world/Planner stages
retain their narrower generation scope. They do not run the finite Runner.

The [optimization explanation](../docs/human-readable/optimization-decisions.md)
and [deployment decision record](../docs/provenance/optimization-decision-history.md#d4)
explain build-context reuse, the actual concurrency settings of each entry,
and this host's configuration history.

Each entry anchors its default output to its own source directory, independent
of the caller's current working directory:

```text
examples/<example>/output/
├── inputs/                 generated input snapshots where supported
├── domain-history/         generated semantic history
├── inspection/             generated human-facing Tools
├── inspection-evidence/    detached non-authoritative Tool evidence
└── runtime/                generated package and runner-artifact/
```

These directories are generated output, not source-controlled example
content. For normal human inspection, open `output/inspection/index.html`.

Run either from the repository root with `python -m examples.<example>.main`
or from that example directory with `python main.py`. Use `--output PATH` for
an explicit alternate root. Generation replaces only that owned output root
when override is enabled. For any deployed example, run the generated
`cleanup.sh` before regenerating an output whose Compose project still exists.

The multi-Shell example has 4,408 satellites and 355 GS containers. Each GS
reserves two preparing-or-operational slots per Shell, ten total without
borrowing. One geographic layout of 732 Zones supports 3,660 scoped service
assignments and distinct IPv4 destinations. The Models configure independent
allocation and routes; one Compiler/Runner owns all shared resources.

`starlink_gen1_satellite_internet` preserves the full canonical scenario: 720
Satellites, 355 GroundStations, 732 Zones, 121 five-second formal timestamps,
K1 candidate scope and 2+2 selection target (the earlier comparison matrix used K3). The current S3 reference uses
5-second ISL/GS preparation, 180/60-second future useful service, 10% relative
delay threshold, 120-second refresh and 32-frame Plan batches. Mini preserves
its 10-second cadence and rounds preparation to one sample. Both expose physical,
Planner and Compiler cutoffs of the same finite producer; Compiler materials are
ready before optional Tools finish.
Starlink also accepts explicit shell and observation cadence: the maintained
600-second matrix uses S3/5 s, S1/5 s and S1/1 s. The latter has 601 boundaries
and 19 Plans; policy durations and Traffic sending intervals remain in seconds.
See the [Starlink operating guide](starlink_gen1_satellite_internet/README.md),
[Framework diagrams](../docs/human-readable/framework-construction-execution.md)
and [qualified performance view](../docs/human-readable/qualification-performance.md).
Its Compiler-mode output is also the maintained human inspection dataset:
map-first Physical World and Planner views use compact exact timestamp chunks
(including Physical World inspection of exact `PhysicalFrame` facts), Compiler
uses a trend dashboard, and Observation explains
coarse outer orchestration time. These detached artifacts record facts already
computed by the chain and are not inputs to domain semantics.

`reference/` is labelled offline provenance material and is not imported by a
maintained runtime entry.

The retained normal outputs below predate the scoped five-Shell configuration.

The latest [five-Shell Tool portal](starlink_multi_shell_satellite_internet/output/inspection/index.html)
contains stable Zone–GS pair selection, Shell → Orbit focus and continuous Traffic
replay from the accepted runtime. [S3's normal portal](starlink_gen1_satellite_internet/output/inspection/index.html)
is the latest generation-only output. [Mini's output](mini_satellite_internet/output/)
remains a historical partial subset without a complete runtime artifact or Tool
portal; the teaching Example currently has no normal output. No missing output
was regenerated for closure.

Each normal output is Git-ignored and may be replaced by future normal runs.
[Retained measurements](starlink_multi_shell_satellite_internet/output/qualification/README.md)
include the latest runtime and compact historical comparison summaries. Obsolete
external experiment bundles and aggregate portals have been deleted; summaries do
not imply their original raw archives remain available. The current Tools were
refreshed offline after the frozen runtime, whose Traffic remains WARN and final
teardown was manually accelerated. See [CURRENT](../docs/CURRENT.md) for the
accepted scope, source distinction and remaining Model coordination limitation.
