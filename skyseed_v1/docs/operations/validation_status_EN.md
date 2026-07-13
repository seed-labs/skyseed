# Current Validation Status

This is the living status page for the code that exists now. Update it after
cheap suites, environment qualification, or a completed real run. Do not put
current test counts or later UX/runtime changes into `docs/migration/`: those
files are frozen records of the structural migration gates.

Last updated: 2026-07-13.

## Canonical repository surface

The current repository tree contains only the standalone `skyseed_v1/`
implementation plus the repository README and ignore policy. The pre-v1
`satellite/` implementation, prototype `ai-code/`, and migration-era
`WORKTREE_MAP.md` were removed in a normal follow-up commit after v1 cutover;
their history remains available in Git and the archival backup remote. The
69/69 v1 suite and both direct example inspections pass with those directories
physically absent.

## Current cheap gates

- latest completed full v1 suite: 85/85 pass;
- latest completed migration regression suite: 792/792 pass;
- focused runtime-validation regression suite: 204/204 pass;
- affected-module compilation and `git diff --check`: pass.

The added v1 tests preserve the BPF no-prepare/attach-reuse proof across the
operational-clean session compaction boundary and passes the resulting steady
map-only epoch through the fail-closed hot-path audit. Console heartbeat tests
also cover bounded operator-facing command summaries alongside semantic
activity labels; the projection remains outside the network-update path.

They also enforce one shared physical-link timeline across rolling horizons,
aligned offsets, complete endpoint coverage, identical Compose materialization
identity, exact frozen visualization consumption, and compact worker attach
handoff compatibility.

## Current continuous-physical-time S4 baseline

The direct-example run
`rich_s4_operational_clean_bpf-20260713T033419Z` is the current semantic and
operational baseline:

- it started with plain `python examples/s4_operational_clean/run.py` after a
  hard VM reboot;
- S4 used 348 satellites, 28 UTs, 12 GWs, 14 zones, four 240-second horizons,
  and 10-second sampling;
- one 97-frame physical-link timeline covered the inclusive 0--960 second
  boundary; H0/H1/H2/H3 consumed offsets 0/24/48/72;
- all four horizon topology materialization hashes were identical, and all
  orbit/snapshot/physical-time alignment checks passed before Compose;
- the prepared-runtime consumer gate accepted 100 records, 25 per horizon,
  with 1,246 consumer binding references and no blockers;
- all 100/100 runtime epochs and all three real horizon transitions succeeded;
- worker H1/H2/H3 preparation took 63.960, 61.948, and 60.043 seconds, leaving
  about 176--180 seconds before each 240-second boundary;
- transition wall time averaged 3.339 seconds, p95 was 6.902, and the maximum
  was 9.335; data-plane-ready lag averaged 2.296, p95 was 3.254, and the
  maximum was 7.080 seconds; no transition or ready lag exceeded 10 seconds;
- return code was 0 with `RICH_S4_OPERATIONAL_CLEAN_PASS`; rollback, Compose
  teardown, and the independent post-run preflight left zero owned residue.

The worker attach handoff reports were 112--194 MiB because they duplicated
compiler payloads in diagnostic branches. A post-run structural fix projects
only the immutable interface/role/binding capability. Replay of the successful
H1/H2/H3 artifacts reduces them to 1.51--1.67 MiB while preserving all
interfaces, roles, and complete ifindex/MAC binding caches. The 85/85 suite
passes; no additional long run was spent on this report-only change.

The lower runtime wall time is real for this run, but it is not evidence that
the physical-time correction optimized the epoch algorithm. The earlier S4
run averaged 46.23 applied operations per epoch; this run averaged 46.64, so
less useful work is ruled out as the explanation. No epoch hot-path code was
changed by the correction. The improvement is a broad tail-latency reduction
(mean 7.418 to 3.339 seconds, p95 16.466 to 6.902, maximum 21.320 to 9.335)
after a hard reboot and removal of accumulated outputs. Lower host contention
and storage/I/O tail latency are therefore the leading explanation. The old
detailed artifacts were intentionally deleted, so a single component-level
cause cannot be proven retrospectively; this result must not be promoted to an
algorithmic performance claim without repeated controlled runs.

The continuous-timeline repair is in the common resolved-profile runner, not
an S4-only branch. Both canonical S4 and S5 profiles now have a regression
test proving one shared physical base timestamp and offsets 0/24/48/72 across
their four 240-second horizons. S5 therefore receives the same fail-closed
time-alignment and Compose-materialization identity gates. S5 has not been
rerun under the corrected continuous-physical-time semantics; its older run
remains runtime-mechanics evidence only.

## Reclassification of earlier multi-horizon runs

Earlier S4/S5 and structural-cutover runs advanced orbit/snapshot time but
reused H0 physical-link frames in successor horizons. They remain useful
evidence for runtime mechanics, BPF generation switching, Compose, rollback,
cleanup, and structural equivalence, but they are not evidence of correct
continuous multi-horizon physical topology or route semantics. The 2026-07-13
run above supersedes them for that claim.

## Earlier reboot-qualified S5 runtime-mechanics evidence

The direct-example S5 run
`rich_s5_operational_clean_bpf-20260712T182300Z` completed the real network:

- four horizons and 100/100 runtime epochs succeeded;
- real apply, connectivity, strict BPF map-only reuse, rollback, and scoped
  zero-residue cleanup succeeded;
- transition mean was 2.810 seconds, p95 5.993 seconds, maximum 10.395 seconds;
- the profile wrapper then failed while finalizing its report because
  `normal_path_blockers` was read before assignment.

The report-layer ordering defect is repaired and covered by an AST regression
test. Offline finalization replay over the immutable successful S5 artifacts
returns 0 with `RICH_S5_OPERATIONAL_CLEAN_PASS`. No extra long rerun was used.

## Earlier reboot-qualified S4 runtime-mechanics evidence

The direct-example S4 run
`rich_s4_operational_clean_bpf-20260712T193444Z` completed four horizons and
100/100 runtime epochs. Real apply, BPF reuse, rollback, Compose cleanup, and
postflight all succeeded; a subsequent direct preflight reported `ready=True`
with no blockers, warnings, or cleanup suggestions.

Runtime transitions averaged 7.418 seconds (p50 6.267, p95 16.466, maximum
21.320). S4 averaged 46.23 applied operations per epoch versus S5's 19.36;
the 2.64x time ratio and 2.39x operation ratio support workload scaling as the
current leading explanation, not a strict linear-scaling proof.

The original final return code was 2 because the cost gate converted slow
startup and one 12.187-second unattributed timing sample into functional
failure even though semantic and cleanup reports passed. The policy now keeps
elapsed-time anomalies visible and blocks an unattended broad rerun, but does
not rewrite a successful network result without a semantic failure. Replaying
the policy over this immutable run yields two warnings and zero blockers. No
additional long run was started.

Startup remains a performance backlog: the S4 normal-runtime input artifact
was about 1.38 GB, and preparation/externalization/Compose phases were visibly
expensive. Stable submission took priority over a late rewrite.

## Historical accepted structural-cutover baseline

The historical structural-migration acceptance remains the S4 structural
cutover recorded in `../migration/stage7_acceptance_EN.md`:

- run ID: `s4-v1-structural-cutover-fresh2-20260712T124412Z`;
- four 240-second horizons, 100/100 runtime epochs;
- return code 0 and `RICH_S4_OPERATIONAL_CLEAN_PASS`;
- zero final owned containers, networks, direct interfaces, BPF pins, locks,
  and worker processes.

It remains a valid frozen record of structural cutover against its stated
baseline, but not a continuous-physical-time validation. Current semantic
real-run evidence is recorded separately above.

## Verified reboot evidence

The current semantic S4 run was collected after another hard reboot:

- current systemd boot ID: `02a62489-8564-4a00-ac37-49f18ee77132`;
- boot start: 2026-07-12 23:30 EDT;
- kernel 5.4, Docker 19.03.8, Compose 1.27.4, and Python 3.10.20;
- standard host MPLS preparation restored reboot-reset bridge-netfilter/MPLS
  state; preflight then became ready;
- the current continuous-physical-time S4 completed the real network and
  cleanup route on this boot.

This is positive evidence that the validated boot restored Docker, Python/SEED
selection, host preparation, Compose, BPF, runtime, and cleanup sufficiently
for the complete S4 route. It does not guarantee a future boot after package,
kernel, Docker, or configuration drift. Use the short post-reboot/demo sequence
in `environment_and_running_EN.md` before spending another real run.

## Update rule

- Cheap test completion updates this page, not migration acceptance files.
- A real run is recorded only after its final outcome and zero-residue evidence
  are available.
- A failed or interrupted run remains evidence; do not describe it as the new
  baseline.
- Environment facts include the boot/kernel/dependency identity under which
  they were observed.
- Historical migration records change only to correct a factual error, with an
  explicit correction note—not to reflect ongoing development.
