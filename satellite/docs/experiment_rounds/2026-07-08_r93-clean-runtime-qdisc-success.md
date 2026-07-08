# r93: Clean Runtime Qdisc Batch Success

Date: 2026-07-08
Status: validated

## Goal

- Close the qdisc-batch and clean-runtime round with a full 3H/240s
  `operational_clean` / `bpf_owned` validation.
- Preserve the rolling-horizon contract:
  - H1/H2 ready artifacts consumed only at runtime boundaries
  - no fallback, full reload, full scan, live full-map dump/diff, hidden
    reconcile, future qdisc preapply, or legacy controller-backed runtime route
- Validate the current direct-netns qdisc batch executor under real runtime
  structural and parameter update load.
- Keep UT-to-selected-GW ping observation nonblocking and raw-fact-only.

## Changes Validated

- Direct netns qdisc batching covers structural runtime lifecycle qdisc work
  and parameter qdisc updates.
- Reporting distinguishes qdisc shell/process command count from qdisc
  sub-update count.
- Clean/BPF-owned validation target semantics now point runtime contexts at the
  selected gateway access IP rather than a Mini Internet host.
- Initial readiness no longer gates on Mini Internet validation host command
  channels.
- Runtime ping observation writes `runtime_ping_observation.jsonl` and does not
  affect epoch success/failure.
- Async/raw artifact writing keeps epoch execution details, raw events, and
  ping observations append-only while the runtime proceeds.

## Validation

- Gate before r93:
  - `py_compile` passed for touched runtime/report/test modules.
  - Focused suite passed: `258 tests OK`.
  - `satellite.test_skyseed_session_check`: `4 tests OK`.
  - `git diff --check`: passed.
  - `skyseed_session_check.py --format text/json`:
    `operational_iteration_ready=True`.
- Run:
  `s4-full-h3-240s-runtime-20260708-r93-clean-ut-gw-qdisc-batch`
- Output:
  `satellite/output/validation_profiles/s4-full-h3-240s-runtime-20260708-r93-clean-ut-gw-qdisc-batch`
- Result:
  - `aggregate_runtime_succeeded=True`
  - 75/75 runtime epochs completed
  - H0/H1/H2 each completed 25 runtime epochs
  - H1 ready artifact consumed at the H1 boundary
  - H2 ready artifact consumed at the H2 boundary
  - `linux_gz_fallback_command_count=0`
  - `eligible_core_fallback_to_linux=0`
  - post-run cleanup succeeded; compose containers and networks returned to 0

## Timing And Qdisc Evidence

- All 75 epochs:
  - transition average: 6.600457s
  - transition p50: 5.415679s
  - transition p95: 14.048218s
  - transition worst: 21.451660s
  - qdisc batch commands: 189
  - qdisc sub-updates: 17,908
  - qdisc wall total: 3.491481s
- Per horizon:
  - H0: 25 epochs, transition avg 5.717257s, 61 qdisc batches,
    5,596 sub-updates, qdisc wall 1.147262s
  - H1: 25 epochs, transition avg 7.119858s, 63 qdisc batches,
    6,104 sub-updates, qdisc wall 1.126851s
  - H2: 25 epochs, transition avg 6.964257s, 65 qdisc batches,
    6,208 sub-updates, qdisc wall 1.217368s

This validates that qdisc command churn is no longer the runtime hot-path
bottleneck. The remaining transition time is mostly host physical mutation,
network mutation planning/accounting, node-local edge work, and cleanup rather
than `tc` process churn.

## Clean Runtime Lessons

- `operational_clean` should be treated as the only correct route for the
  current BPF-owned operational iteration, not as a best-effort profile with
  legacy fallback.
- Old controller-backed, Linux-MPLS, Mini Internet validation-host, full
  reload, full scan, live dump/diff, and hidden reconcile code may remain until
  broader refactoring, but must be unreachable from the clean profile.
- If the clean profile unexpectedly selects a legacy route, fail closed instead
  of silently falling back.
- Runtime live checks must be scoped to current operations or pending records.
  Global historical checks belong in preparation, tests, or offline reports so
  normal consumption/retirement does not self-block a valid lifecycle.
- Raw timing marks and append-only operation/observation logs are the durable
  facts. Derived summaries are useful, but should not define or block the
  data-plane update path.

## Remaining

- UT-to-selected-GW ping observer is correctly nonblocking and targets
  `selected_gateway_access`, but r93 recorded 432/432 failed observations.
  This is now the next semantic/debugging item for return-path or gateway
  access reachability, not a blocker for the r93 runtime success claim.
- Some legacy warning labels remain in post-run artifacts, such as
  `host_mpls_preflight_skipped`. They are nonblocking for the BPF-owned clean
  path, but should be removed or renamed during the next cleanup/refactor pass.
- Runtime cleanup and final compose down still take several minutes after the
  data-plane run completes. This does not affect epoch success, but remains
  worth simplifying after the main functional work is complete.

## Watch Next

- Do not treat `ping_success` legacy fields as the UT-to-selected-GW observer
  success rate. Use `runtime_ping_observation.jsonl` for that question.
- Next work should start from the r93 evidence, not from r90/r91 partial
  failures, unless comparing a specific failure mode.
- Before the next expensive run, preserve the same gate discipline: cheap
  lifecycle tests first, then session check, then a single intentional long
  run.
