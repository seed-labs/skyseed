# Starlink Gen1 satellite Internet

This Example defaults to the canonical S3 generation and live qualification
scenario and supports explicit shell/cadence selection. Its reference inputs are:

- 720 Satellites in 36 Orbits of 20 at 570 km and 70 degrees;
- 355 evidence-backed GroundStations;
- 732 global Zones;
- ten minutes at a 5-second observation interval;
- 121 formal timestamps;
- maximum 4 simultaneous ISLs per Satellite;
- K1 cyclic adjacent-plane candidate scope, same-plane cyclic predecessor/successor and desired 2 same-plane + 2 cross-plane;
- deterministic retain-first ISL selection and 5-second ISL/GS preparation;
- 180/60-second ISL/GS minimum useful service after preparation;
- GS coverage, distinct satellites, total connections, continuity, then deterministic quality;
- 10% relative delay threshold and 120-second refresh;
- 32 formal frames per Plan batch, with no independent publication wait.

The capability and selection policy are explicit scenario assumptions, not a
claim about deployed Starlink terminal hardware.

The restored [normal Tool portal](output/inspection/index.html) contains the
current default S3 generation and its five generation Tools. It has no attached
historical runtime. Its original outer timing and memory records now live in
[output/qualification](output/qualification/README.md); old aggregate portals were removed.

## Stages

Run from the repository root:

```bash
python -m examples.starlink_gen1_satellite_internet.main \
  --through physical-world

python -m examples.starlink_gen1_satellite_internet.main \
  --through planner

python -m examples.starlink_gen1_satellite_internet.main \
  --through compiler
```

The equivalent direct form is `python main.py --through <stage>` from this
directory. Both forms default to
`examples/starlink_gen1_satellite_internet/output/`, independent of the shell's
current directory. Use `--output PATH` for an alternate owned root. Run the
generated `cleanup.sh` before replacing an output with a live Compose project.

`physical-world` computes finite contact prediction and all Timeline-derived formal physical
observations, seals physical history, and produces physical/Zone inspection. It
does not progress Planner or Compiler. `planner` drains
the same incremental producer through its Planner-only pull seam and seals
planning evidence without invoking Compiler. `compiler` (the default) drains
that producer's complete finite realization sequence, seals the Runner artifact, and packages
generated runtime input. There is no second Timeline cursor or batch planning
path, and no producer survives into runtime.

Counts are derived from the finite Timeline and the 32-frame batch. At 5 seconds,
600 seconds yields 121 transitions and four Plans/realizations; at 1 second it
yields 601 transitions and 19 Plans/realizations. Every timestamp is retained,
including no-op values. To select the qualification conditions explicitly:

```bash
python -m examples.starlink_gen1_satellite_internet.main --shell S3 --observation-interval-seconds 5 --output /path/to/fresh-s3-5s
python -m examples.starlink_gen1_satellite_internet.main --shell S1 --observation-interval-seconds 5 --output /path/to/fresh-s1-5s
python -m examples.starlink_gen1_satellite_internet.main --shell S1 --observation-interval-seconds 1 --output /path/to/fresh-s1-1s
```

`--shell` selects a shell from the same shells input and writes the effective
choice to the output's scenario snapshot. S1 has 1,584 satellites in 72×22
orbits, at 550 km and 53 degrees; S3/S1 changes geometry as well as scale.
S1 and S3 explicitly declare four simultaneous ISLs per satellite for this
experiment; this is a configurable simulation capability, not a hardware claim.
They are the maintained Compiler matrix shells. Other shell inputs retain their
original capabilities; a Planner/Compiler run requires explicit compatible ISL
capabilities rather than inventing a default for missing declarations.
Preparation stays 5 seconds, minimum service 180/60 seconds and refresh 120
seconds. Persistent/activation probes retain 1/0.5-second sending intervals,
independent of observation cadence. Actual episode selections and sends are
reported per condition.
The former 714 cross-plane / 12 underfilled warm oracle belongs to the prior
policy evidence; it is not a required outcome of the newly approved policies.

## Outputs

The selected output root contains detached inspection, compact exact
`inspection-evidence/physical-link-observations.jsonl.gz`,
`domain-history/physical-frames.jsonl`, `domain-history/planning.jsonl.gz`,
`domain-history/compiler.jsonl`, the terminal
`domain-history/domain-history-manifest.json`, and, in Compiler mode, a
checksum-verified sealed typed packed Runner artifact plus runtime package.

Physical World and Planner provide map-first linked 3D/2D workspaces with
per-timestamp chunks, exact focused candidate relationships, and concrete Model
descriptors assembled from this scenario's construction constants. Planner's
stage selector separates candidates, selected/acquiring values, operational
Connections, Zone service, parameter changes, focused Route outcomes,
coordination, publication, and lineage without drawing global candidate or
Route graphs. Physical World visibly highlights a selected Zone and its exact
candidate Satellites.

Compiler inspection is a realization/boundary chart dashboard for workload,
add/update/remove, semantic-precedence folding, and timing, with compact
realization/boundary drill-down rather than raw payload tables. Observation
links coarse outer orchestration timing from
`inspection/orchestration-timing.json` to existing fine-grained component
records. Every inspection surface is detached and never reruns its Model.

Traffic's persisted probe plan is projected incrementally from each live
Plan/realization pair while Compiler production already owns those values. The
runtime-packaging phase publishes that compact derived artifact; it does not
reread Plan history or reconstruct rich compiled objects. Runner is the live
executor of the validated precomputed artifact. It decodes every indexed boundary
during Preparation, before physical B0 activation and formal execution from B1.

The Traffic builder receives the same typed PhysicalWorld to select Shell
coverage within one global budget: by default 20 episodes, eight concurrent
generators and 3,600 estimated requests. Physical/Planner and Traffic expose
Shell multi-select/solo controls and independent shared GS/Zone visibility.
Traffic keeps raw totals, planned conformance and actual coverage separate;
see the [Traffic Tool usage and criteria](../../tools/traffic/README.md).
The [five-Shell portal](../starlink_multi_shell_satellite_internet/output/inspection/index.html)
retains the latest runtime; its Tools have separate offline-refresh provenance.

## Qualification boundary

This entry performs offline semantic and compilation work only. It does not
start the full S3 Compose deployment. Runtime qualification uses generated
controls after `main.py` has generated complete output, realizations and the
artifact: `start.sh` deploys, `run.sh` starts Runner/Traffic, and `launch.sh`
combines start and run. `cleanup.sh` remains independent. Runtime qualification
also waits for detached Tools; see the [common operating sequence](../README.md).
Normal regeneration may replace the current owned output, including its local
measurement summaries. Use an explicit `--output` when a separate run is needed;
old correction/failed-run archives are not maintained dependencies.
Runner completes B0 in Preparation and all 120 (5 s) or 600 (1 s) formal Main transitions through the
600-second frontier. Physical grouped deletion remains inside Main; fixed-origin
pacing retains actual lateness. Completion and Traffic qualification are separate
outcomes, recorded in invocation-owned evidence.

The Runner milestone's implementation, S3 evidence and documentation have passed
Work review, as tracked in [CURRENT](../../docs/CURRENT.md). This guide preserves
the historical qualification outcomes, including Traffic's WARN classification.

## Computation readiness and assumptions

Both connection kinds reserve endpoint capacity while preparing. A new contact
must be proved usable through preparation plus its minimum service. This is not
an elapsed 180/60-second wait. Finite prediction endpoints do not imply loss;
uncertainty is explicit. Prehistory is 5 seconds; admission tails are at most
185 seconds for ISL and 65 seconds for GS, with analytical stable ISLs requiring
no sampled extra tail. Extra prediction emits no formal transitions.

Five seconds is a simplified ISL setup reference and an assumed GS
preparation duration (one sample only at 5-second cadence), not a measured commercial gateway acquisition time. The 32-frame
batch is an internal starting choice; every formal decision timestamp is preserved.
No-op delay refreshes update memory only; there is no integer target persistence.

`generation-status.json` publishes MATERIALS_READY after complete required
materials and scripts, before optional inspection. It then records TOOLS_COMPLETE
or TOOLS_FAILED. The CLI waits for finite Tool jobs for offline qualification,
while another consumer may use the ready runtime package. Readiness, Tool time
and total elapsed time are separate; evidence/package worker durations overlap
semantic progression and are not additive wall time. No generated deployment or
run script is executed by generation.

The accepted optimization milestone reduces repeated GS solving, candidate
construction, routing, artifact capacity/normalization, finite Preparation and
Tool work inside the existing owners. Historical full-chain S3/5 s, S1/5 s and
S1/1 s comparisons are summarized in the [performance view](../../docs/human-readable/qualification-performance.md).
They preserve their original configurations and WARN findings; they do not qualify
this latest generation-only S3 output. The accepted current runtime is the
five-Shell output linked in [CURRENT](../../docs/CURRENT.md). Selected historical
measurements remain in its qualification directory; full historical raw bundles
and portals were removed at accepted closure.


The [Framework guide](../../docs/human-readable/framework-construction-execution.md)
connects these inputs and Models to actual Runner/BPF forwarding. The
[performance view](../../docs/human-readable/qualification-performance.md)
compares the three conditions with separate timing and resource scopes. Generated
startup shares an image per identical context group while retaining each node's
container, mounts and identity; it checks unique images and project ownership
before Compose up. It does not inherit the optional launcher's parallel limit.

The separate [S1–S5 Example](../starlink_multi_shell_satellite_internet/README.md)
uses the same physical inputs, policies, Compiler packaging and Tools for two
finite deliveries in one shared network. This single-Shell entry remains a
one-delivery generation baseline. Historical S3/S1 comparisons retain compact
measurements in the [multi-Shell qualification directory](../starlink_multi_shell_satellite_internet/output/qualification/README.md).
Their original runtime bundles were deleted after dependency checks. User and Work
review accepted the cumulative milestone; earlier Runner acceptance and each
historical measurement retain their original scope.

The GS fallback of four is an engineering assumption. Explicit station capabilities such as two/four/eight are respected; a separate optional policy ceiling can limit them. The allocation maximizes GS coverage, distinct directly connected satellites, total connections, continuity, then deterministic quality. GS queries start at 32 eligible nearby satellites, retain pending/operational assignments, and expand under competition. This budget is not hardware range or connection capacity. Wider ISL neighborhoods remain configurable.
