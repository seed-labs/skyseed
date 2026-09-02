# Starlink Gen1 satellite Internet

This is the canonical S3 offline qualification scenario. Its frozen inputs are:

- 720 Satellites in 36 Orbits of 20 at 570 km and 70 degrees;
- 355 evidence-backed GroundStations;
- 732 global Zones;
- ten minutes at a 5-second observation interval;
- 121 formal timestamps;
- maximum 4 simultaneous ISLs per Satellite;
- K3 cross-plane candidate scope and desired 2 same-plane + 2 cross-plane;
- deterministic retain-first selection and 20-second acquisition reference;
- 50-second planning horizon and 20-second publication horizon.

The capability and selection policy are explicit scenario assumptions, not a
claim about deployed Starlink terminal hardware.

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

`physical-world` creates static and first-frame inspection. `planner` drains
the same incremental producer through its Planner-only pull seam and seals
planning evidence without invoking Compiler. `compiler` drains that producer's
realization pull seam, persists realizations, and packages generated runtime
input. There is no second Timeline cursor or batch planning path.

The Planner stage requires exactly 30 Plans and 121 transitions. Compiler stage
requires 121 formal frames, 30 realizations, and 121 activation boundaries.
Every transition retains its exact 5-second timestamp, including no-op values.
Canonical offline qualification also compares the defined warm boundary with
the accepted 714 cross-plane / 12 underfilled oracle.

## Outputs

The selected output root contains detached inspection, compact exact
`inspection-evidence/physical-link-observations.jsonl.gz`,
`domain-history/{physical-frames,planning,compiler}.jsonl`, the terminal
`domain-history-manifest.json`, and, in Compiler mode, a checksum-verified
persisted realization handoff plus runtime package.

Physical World and Planner provide map-first linked 3D/2D workspaces with
per-timestamp chunks, exact focused candidate relationships, and concrete Model
descriptors assembled from this scenario's construction constants. Planner's
stage selector separates candidates, selected/acquiring values, operational
Connections, Zone service, parameter changes, focused Route outcomes,
coordination, publication, and lineage without drawing global candidate or
Route graphs. Physical World visibly highlights a selected Zone and its exact
candidate Satellites.

Compiler inspection is a 30-realization chart dashboard for workload,
add/update/remove, semantic-precedence folding, and timing, with compact
realization/boundary drill-down rather than raw payload tables. Observation
links coarse outer orchestration timing from
`inspection/orchestration-timing.json` to existing fine-grained component
records. Every inspection surface is detached and never reruns its Model.

Traffic's persisted probe plan is projected incrementally from each live
Plan/realization pair while Compiler production already owns those values. The
runtime-packaging phase publishes that compact derived artifact; it does not
reread the persisted Plan history or eagerly decode the Runner realization
handoff. Runner remains the sole consumer of that validated handoff.

## Qualification boundary

This entry performs offline semantic and compilation work only. It does not
start the full S3 Compose deployment. Full persisted Runner/Traffic
qualification has not yet been rerun after Round B and must be reported only
after its dedicated stage.
