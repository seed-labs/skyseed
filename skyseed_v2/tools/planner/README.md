# Planner Tool

This detached Tool reads canonical `PublishedPlanningEvidence` values or the
producer-owned `planning.jsonl.gz` stream. It never runs Planner Models.

```python
from tools.planner import read_planner_history, write_planner_tool

evidence = read_planner_history(path)
write_planner_tool(
    evidence,
    output_directory,
    physical_frames_path=physical_frames_path,
)
```

The durable reader requires the sibling terminal
`domain-history-manifest.json`, streams and hashes every owned JSONL, and
rejects incomplete cadence, missing final publication, mixed provenance, or
cross-stream lineage mismatch before producing Tool data.

The compact root `planner-inspection.json` indexes every formal timestamp and
publication. Exact per-boundary data lives in deterministic `data/frame-*.js`
chunks; Route histories use stable SHA-256 keys across fixed local shards.
Together they expose:

- sparse physical candidate summaries and work counts;
- exact focused physical candidate identities and rank/quality fields from the
  separate verified inspection stream;
- selected ISLs with acquisition start/reference/remaining/operational state;
- selected GroundStation accesses;
- selected/served Zone candidate facts;
- the canonical Plan transition delta for Connection establishment/withdrawal,
  parameter updates, Zone service changes, and reachable/unreachable Route
  outcomes with reasons;
- compact replayed state counts plus the exact transition/state fingerprints;
- derived semantic event counts and publication precedence;
- physical-observation/planning/publication frontiers;
- Plan cadence plus Plan, world, transition, and state lineage/fingerprints.

The machine artifact retains one exact final `PlannedOperationalState` for
detached replay verification. It does not duplicate complete Connection, Zone,
or Route state at every formal timestamp.

The renderer keeps linked 3D/2D views primary while a compact stage selector
walks the accepted order. Only one stage's overlay and drawer are visible.
GroundStation/Zone focus loads exact relevant candidate and selected facts;
parameter focus shows previous → current values; Route search loads one exact
path/unreachable timeline. Browser-side display replay applies retained deltas
only and invokes no Planner Model. Concrete descriptors come from the same
maintained construction values used by the example. Candidate,
selected/acquiring, operational, routed/unreachable, compiled, and realized
meanings remain separate.

The equal-width maps share timestamp, entity, relation, stage, and Route focus.
The 3D globe supports drag rotation, wheel zoom, reset, and focus-selected; the
2D map supports drag pan, cursor-centred wheel zoom, reset, and the same focus.
Empty-space click, repeated selection, Escape, and Clear Focus all clear it.
Stage rendering keeps earlier accepted network context faint, current inputs
dashed, decisions solid, and the current formal-boundary delta strongest.

Focused ISL evidence lists same-plane and cyclic cross-plane Δ1/Δ2/Δ3
relationships individually, with rank, distance/delay, and selected state; a
clicked relationship is highlighted in both maps. GroundStation validation is
presented as the proposal/feasibility/identity/capacity correctness gate it is,
not as a second selection Model. Route focus starts from a selected source and
its concise demands, while opaque Node-id text search remains an advanced
option.
