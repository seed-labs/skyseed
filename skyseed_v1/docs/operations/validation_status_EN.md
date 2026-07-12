# Current Validation Status

This is the living status page for the code that exists now. Update it after
cheap suites, environment qualification, or a completed real run. Do not put
current test counts or later UX/runtime changes into `docs/migration/`: those
files are frozen records of the structural migration gates.

Last updated: 2026-07-12.

## Current cheap gates

- latest completed full v1 suite: 69/69 pass;
- latest completed migration regression suite: 792/792 pass;
- focused runtime-validation regression suite: 204/204 pass;
- affected-module compilation and `git diff --check`: pass.

The added v1 test preserves the BPF no-prepare/attach-reuse proof across the
operational-clean session compaction boundary and passes the resulting steady
map-only epoch through the fail-closed hot-path audit. Console heartbeat tests
also cover bounded operator-facing command summaries alongside semantic
activity labels; the projection remains outside the network-update path.

## Reboot-qualified S5 real-run evidence

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

## Reboot-qualified S4 real-run evidence

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

The latest completed canonical real-run acceptance remains the S4 structural
cutover recorded in `../migration/stage7_acceptance_EN.md`:

- run ID: `s4-v1-structural-cutover-fresh2-20260712T124412Z`;
- four 240-second horizons, 100/100 runtime epochs;
- return code 0 and `RICH_S4_OPERATIONAL_CLEAN_PASS`;
- zero final owned containers, networks, direct interfaces, BPF pins, locks,
  and worker processes.

Later entry, cleanup, progress-display, and S5-example work does not rewrite
that historical migration acceptance. Current real-run evidence is recorded
separately above.

## Verified reboot evidence

The historical accepted S4 run occurred after a real VM reboot. The latest S4
and S5 evidence above was collected after another hard reboot:

- current systemd boot ID: `d6030363-0c5f-4bc1-a332-b72c01a39e10`;
- boot start: 2026-07-12 14:17 EDT (approximately 18:17 UTC);
- kernel 5.4, Docker 19.03.8, Compose 1.27.4, and Python 3.10.20;
- standard host MPLS preparation restored reboot-reset bridge-netfilter/MPLS
  state; preflight then became ready;
- both current S5 and S4 completed the real network and cleanup route on this
  boot.

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
