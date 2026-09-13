# S1–S5 continuous finite deliveries

This standalone Example assembles the five shared Starlink Shell definitions
into one Emu: 4,408 satellites, 355 shared GroundStations and 732 Zones.
Each satellite explicitly has four ISL ports. The existing Model enumerates
ISLs within each Shell. Each shared GroundStation has ten total slots,
reserved as two preparing-or-operational connections for each Shell, with no borrowing.

`settings.yaml` exposes the five Shells, five-second sampling, 600-second
session, 300-second delivery coverage, lead fraction 1.0 and capacity factor 2.
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

Generated startup submits shared GS/auxiliaries, then whole S1–S5 groups with
`SKYSEED_DEPLOY_PARALLEL=8` throughout. It prepares one Compose input per group and
preserves real dependencies. Failure records the partial group and retains confirmed
successful groups; explicit deployment retry reconciles configuration, image and
container identities before completing only missing or unsuccessful work.

Generation completes both sealed artifacts, continuous domain/Traffic evidence,
the Tool portal at `output/inspection/index.html`, and deployment controls.
`launch.sh` combines start and run; cleanup remains independent. After Traffic and
the unified Runner finish cleanup, deployment cleanup removes S5, S4, S3, S2, S1,
shared/GS and finally owned project networks. Each whole group uses one native
Docker CLI removal queue (50 operations in CLI 29.6.2); explicit graceful settings,
hooks and real dependencies remain effective. The 60-minute startup and 30-minute
cleanup budgets each cover all groups together. No resident-count curve, arbitrary
service chopping or artificial inter-group waits apply. Group progress is appended
to `runtime/deployment-measurements.jsonl` and `runtime/teardown-measurements.jsonl`.
See the [Compose policy](../../src/skyseed_v2/compose/README.md).

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
0 seconds (`300 − 1.0 × 300`), giving the first successor a full 300-second
preparation window. Every delivery is decoded and constructed incrementally, with
complete preparation and prewrite required before READY. This is a scenario
configuration, not a measured optimum or a promise of meeting the deadline. Fixed reservations derive from the first
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

The user and Work accepted the earlier milestone represented by the normal output;
the current round remains pending independent review. The [normal Tool portal](output/inspection/index.html)
retains the successful runtime with later offline Tool refreshes. Original timing,
manual teardown and compact historical comparison records are consolidated under
[output/qualification](output/qualification/README.md); obsolete external raw
archives and aggregate portals were removed. That output remains historical.
The 2026-09-13/14 external whole-Shell qualification completed all 600 seconds,
crossed the seam with 60.458 seconds READY margin, and confirmed complete Runner
and deployment cleanup. Traffic remains WARN with an S5 Zone-to-GS transition
coverage gap. The original final verifier permission error and its read-only
resolution are preserved in the new evidence. See [CURRENT](../../docs/CURRENT.md)
for exact source identities, retained output scope and deferred work.

This example writes an explicit GS capacity of ten into its own copied input.
The single-Shell example retains its default four and explicit device limits. It is an engineering assumption, not verified Starlink hardware
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

Zone service identity is `(network_scope, zone_id)`. The empty scope retains
unscoped behavior. The five-Shell example explicitly supplies satellite scope
membership to the GS, Zone and Route Models: every shared GS reserves two
preparing-or-operational slots per Shell (ten total), every geographic Zone has
one explicit assignment per Shell, and the geographic three-GS association is
applied separately in each scope. The scoped Route Model excludes other-scope
satellites and GS transit. No cross-Shell communication Model is implemented.
One Compiler/Runner owns shared interfaces, forwarding and cleanup. Distinct
scoped Zone IPv4 destinations prevent shared-GS forwarding collisions. Physical
feasibility, satellite capacities and preparation/handover rules still apply;
association does not promise an operational path. Isolation is configured in
Models, not imposed as a permanent PhysicalWorld rule.

The generated controlled startup checks/builds unique images using an image-only
Compose projection, then submits full services in whole deployment groups. This
keeps image preparation compatible with Compose's document parser limits without
changing host protections or the canonical full service definitions.
