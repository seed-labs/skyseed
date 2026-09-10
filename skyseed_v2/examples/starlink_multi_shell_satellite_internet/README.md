# S1–S5 continuous finite deliveries

This standalone Example assembles the five shared Starlink Shell definitions
into one Emu: 4,408 satellites, 355 shared GroundStations and 732 Zones.
Each satellite explicitly has four ISL ports. The existing Model enumerates
ISLs within each Shell; GroundStation capacity is shared across all Shells.

`settings.yaml` exposes the five Shells, five-second sampling, 600-second
session, 300-second delivery coverage, lead fraction 0.75 and capacity factor 2.
`emu.py` writes the existing static input schema and creates the Emulator;
`simu.py` assembles the same Starlink Models and durations in seconds.
`main.py` runs the existing Producer, Compiler packaging and every generation
Tool. There is no new public configuration type or execution authority.
Execution source identity retains the shared Starlink policy scope and adds
this Example's `main.py`, `emu.py` and `simu.py` through the existing source-scope
metadata. Normalized settings are retained with the generated input hashes.

From `skyseed_v2`, in the canonical `skyseed_dev` environment:

```bash
python -m examples.starlink_multi_shell_satellite_internet.main
bash examples/starlink_multi_shell_satellite_internet/output/start.sh
bash examples/starlink_multi_shell_satellite_internet/output/run.sh
bash examples/starlink_multi_shell_satellite_internet/output/cleanup.sh
```

Generated startup admits shared GS/auxiliaries, then S1–S5. Defaults remain at
most 64 services / eight concurrent requests at every resident count. Configure
these independently with `SKYSEED_DEPLOY_BATCH_SIZE` and `SKYSEED_DEPLOY_PARALLEL`.
`SKYSEED_DEPLOY_RESIDENT_STEP` is the cleanup-only count threshold; it does not
reduce startup concurrency. Shell groups are not equal resource units.
Lightweight checkpoints are written to `runtime/deployment-measurements.jsonl`.
Exploratory memory predictions and pressure pause/resume/abort gates are retired.
Normal system-call, ownership and successor-readiness checks remain. Late Main
boundaries execute sequentially against the fixed origin and record actual lag.

Generation completes both sealed artifacts, continuous domain/Traffic evidence,
the Tool portal at `output/inspection/index.html`, and deployment controls.
`launch.sh` combines start and run; cleanup remains independent.
After Runner/Traffic evidence and executor cleanup complete, generated cleanup
force-removes explicitly marked disposable network container IDs in reverse
dependency order. Explicit stop settings/hooks preserve graceful shutdown.
`SKYSEED_CLEANUP_BATCH_SIZE` defaults to 16 actual IDs per command, halved to eight
at/above 2000 remaining. Failure stops later batches; retries inventory remaining
owned resources before network removal. Records remain in
`runtime/teardown-measurements.jsonl`; there are no inter-batch sleeps or pressure
gates. The cleanup defaults have isolated smoke/command validation only. The saved
five-Shell run used startup 64/eight throughout and **manually accelerated
teardown**, so it does not qualify uninterrupted default cleanup. See the
[Compose policy](../../src/skyseed_v2/compose/README.md).

Traffic selection uses typed Shell membership and actual bilateral planned paths
within one default budget of 20 episodes, eight concurrent generators and 3,600
estimated echo requests. The plan records eligibility and stable/transition
coverage in both directions; runtime Traffic reports observed coverage separately
from raw loss and reachability conformance. Missing eligible cases remain visible.
Physical/Planner and Traffic offer Shell multi-select, solo/all and independent
GS/Zone controls in their shared world/time view; filtering preserves altitude.
See the [Traffic Tool contract](../../tools/traffic/README.md) for uncertainty,
qualification bounds and display behavior.

The first delivery owns timestamps 0–295 seconds (`[0,300)`); the final
delivery owns 300–600 seconds inclusive. Together these are 121 timestamps,
one B0 and 120 Main boundaries. Planner prediction covers the continuous
horizon plus policy tails. Delivery flush does not finish Planner memory or
manufacture contact endings. Compiler state and logical ports continue.

Preparation loads only the first delivery and activates B0 once. Runner
registers the sealed successor, then Maintenance begins loading at nominal
75 seconds (`300 − 0.75 × 300`), leaving 225 seconds before activation.
This is a bounded qualification choice with margin, not an optimum or a
claim about satellite calibration. Fixed reservations derive from the first
batch with the configured factor; per-node entry envelopes use first-batch
peaks of the same typed entity kind so forwarding work may move between peers.
This reserves more memory than each node's own initial demand. A successor
exceeding the fixed envelope is rejected.
One helper, deployment, Traffic session and fixed clock span the seam.
Main alone activates and completes required physical withdrawals. Missing
READY at the seam fails the run without waiting or moving nominal time.

Maintenance releases old plans and safely reclaims retired map versions after
actual activation and reader/reference checks. Retained connections survive.
Compiler and Runner Tools show the seam and lifecycle evidence; immutable
historical artifacts remain available after runtime reclamation. The generated
controls cover two deliveries; `BpfRunner.append_batch` can register one next
sealed delivery at a time in a longer fixed-node session.

The user and Work have accepted this milestone. The [normal Tool portal](output/inspection/index.html)
retains the successful runtime with later offline Tool refreshes. Original timing,
manual teardown and compact historical comparison records are consolidated under
[output/qualification](output/qualification/README.md); obsolete external raw
archives and aggregate portals were removed. Traffic remains WARN, and subsequent
cleanup defaults have no new full-scale qualification. See [CURRENT](../../docs/CURRENT.md)
for exact source identities, retained output scope and deferred work.

The default GS capability assumption is four, with explicit per-station 2/4/8
values respected. It is an engineering assumption, not verified Starlink hardware
or a universal optimum. The satellite-side limit remains two. Allocation orders
GS coverage, distinct satellites, total connections, continuity, then quality.
ISL uses cyclic same-plane neighbors and K1 with bounded current cross-plane
neighbors; wider choices remain configurable. GS search starts at 32 candidates,
keeps incumbents and expands under competition. Pending capacity is not service.

Two long-duration controls share the global workload budget with sampled short
flows (seed 20260909). Stable/transition labels require actual eligible paths;
unavailable cases and observed coverage gaps remain visible.

Offline external visualization delivery after the runtime Tools complete:

```bash
python -m tools.planner.export --generated output --output /home/lionheart/workspace/skyseed-local/exports/EXPERIMENT_ID
```

This produces Chinese documentation, complete formal planned-network JSON/GeoJSON
and compact observed traffic, without Runner timing or bulk per-hop records.

Zone service is globally unique across the world: one Zone has at most one current
serving satellite, not one per Shell. In the current example, ISLs stay within
Shells and GSs cannot be transit nodes. Geographic three-GS associations and
independent GS allocation do not guarantee those GSs have operational access to
the Zone's serving Shell. Stable logical pairs therefore differ from the underlying
GS→Zone and changing serving-satellite→GS demands. Coordinated Zone/GS selection
and cross-Shell communication research are deferred; this Tool correction changes
neither allocation nor routing or topology.

Global unique Zone service and shared GS admission can select incompatible,
disconnected Shell components. Resolving this is future Model coordination work.
Per-Shell serving/routing contexts over shared GS entities and Zone geometry are
only a recorded alternative, not an adopted design; shared GS physical capacity
must not be duplicated. This closeout changes no routing or Model policy.
