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
parameter focus shows previous → current values; pair selection loads both exact
directional path/unreachable histories. Browser-side display replay applies retained deltas
only and invokes no Planner Model. Concrete descriptors come from the same
maintained construction values used by the example. Candidate,
selected/acquiring, operational, routed/unreachable, compiled, and realized
meanings remain separate.

The equal-width maps share timestamp, entity, relation, stage, and Route focus.
The 3D globe supports drag rotation, wheel zoom, reset, and focus-selected; the
2D map supports drag pan, cursor-centred wheel zoom, reset, and the same focus.
Empty-space click, repeated selection, Escape, and Clear Focus all clear it.
Stage rendering draws faint context, then subdued thin dashed candidates, then
stronger solid selections; focused relationships are drawn last. Operational
links are green; acquiring/reserved links are amber. The treatment is identical
in 3D/2D ISL, GS and Zone views. Candidate and selection inspection is retained.

Typed Shell multi-select, solo/all controls and stable Satellite colors filter
both maps. GroundStation and Zone visibility remain independent. The same time
cursor, ECEF geometry and actual altitude are retained; link/stage colors keep
their existing meaning. Counts distinguish visible and global content, and the
focus drawer explicitly identifies endpoints hidden by a filter. Filtering adds
no world copy or semantic replay.

Focused ISL evidence lists same-plane and cyclic cross-plane Δ1/Δ2/Δ3
relationships individually, with rank, distance/delay, and selected state; a
clicked relationship is highlighted in both maps. GroundStation validation is
presented as the proposal/feasibility/identity/capacity correctness gate it is,
not as a second selection Model. Stable Zone–GS pairs are the default route interface.
The private `pair_index` derives each pair once from recorded typed GS→Zone
Route demands. It does not infer associations from display names. Each current
frame's global Zone assignment identifies the serving satellite and its Shell;
the return direction resolves that satellite's actual recorded GS-target demand.
Forward and return outcomes remain independent, including asymmetric paths,
unreachable reasons, absent/withdrawn demands and unserved Zones. Selection keeps
the pair key across frame changes and handovers. The current-serving satellite's
Shell/Orbit visibility filters the pair list; shared GS/Zone visibility does not
turn them into Shell-owned entities. Unserved pairs have an explicit filter.

Pair search uses counted pages of 20; raw demand identity/history and paged entity
search remain in an expandable secondary view. Current outcomes use the latest
recorded timeline entry at/before the frame. Full paths remain in the existing
32 deterministic shards, with at most two focused shards retained. Cyan forward
and dashed-orange return paths are drawn independently with selected-line emphasis.
A missing operational GS access to the serving Shell is explained only when the
complete retained operational graph and typed Shell membership support it and
no cross-Shell satellite connection is present; otherwise the recorded reason
stands. No Model is run and no path is fabricated.

The shared compact Shell → Orbit dropdown uses `(asn, shell_id, orbit_id)` from
recorded metadata; names never determine membership. All-orbits/reset and existing
multiple-Shell/shared-GS/Zone controls remain available. Hidden path endpoints
are labelled in complete path text and counts, without changing the underlying
network. No routing policy changed: the reference example chooses three GS
candidates per Zone by static great-circle distance from its representative
point, and creates serving-satellite→GS and GS→Zone demands for those associations.


Streaming projection validates sealed publications and replays transitions once.
It projects native selected/lifecycle values directly into compact browser rows,
serializes each demand's display identity once per invocation, and only builds
full internal frame dictionaries for callers that request them. Payload hashes
are shared by the bundle inventory and checksum file. Required facts and public
input isolation remain intact.

The offline `python -m tools.planner.export` entry consumes sealed formal history
and completed Tool outputs to write versioned full network snapshots and compact
observed traffic outside the repository. Network and Traffic remain explicitly
separate; no internal deltas, runtime timing, host resource traces or bulk hop
telemetry enter this temporary delivery. Chinese READMEs describe all fields.

Zone service is globally unique across the world: one Zone has at most one current
serving satellite, not one per Shell. In the current example, ISLs stay within
Shells and GSs cannot be transit nodes. Geographic three-GS associations and
independent GS allocation do not guarantee those GSs have operational access to
the Zone's serving Shell. Stable logical pairs therefore differ from the underlying
GS→Zone and changing serving-satellite→GS demands. Coordinated Zone/GS selection
and cross-Shell communication research are deferred; this Tool correction changes
neither allocation nor routing or topology.
