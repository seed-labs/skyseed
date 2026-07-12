# r111: route evidence cache for clean BPF edge routes

Date: 2026-07-09
Status: fixed

## Goal

- Run the 4H visualization-backed operational-clean BPF profile after the
  route/ping/worker cleanup work, without hidden fallback or runtime repair.

## Findings

- `r111-4h-viz-clean-route-evidence` launched the intended 4H profile and
  prepared a 96-generation window, so it was not the earlier capped-entry
  mistake.
- Startup remained clean but heavy: compose about 383s, host prewarm about
  433s, current-horizon host install about 186s.
- Runtime completed 3 epochs, then failed closed at
  `initial:starlink-shell-slice-0004`.
- The failure was not a container issue and not a Linux fallback:
  `backend_apply_requested_commands=0`, fallback counters stayed at 0, and
  cleanup completed.
- Exact missing baseline:
  `ut2021:gw_bpf_local_return_route` and
  `ut2022:gw_bpf_local_return_route`.
- Root cause: pre-runtime edge-route baseline prepared route identities, but
  runtime lookup only retained evidence from the previous active session
  contexts. A GW local return route that first appeared in epoch 4 could be
  prepared correctly yet absent from the previous active-session evidence.

## Changes

- Added compact prepared route-identity evidence for BPF-owned UT default
  routes and GW local-origin return routes.
- Runtime now consumes that immutable prepared evidence in addition to the
  current/previous session context evidence.
- Pre-runtime route baseline scope now covers the current runtime queue route
  contexts, while the active session still represents only the current slice.
- The first runtime session carries the prepared route evidence in backend
  metadata so later epochs do not depend on historical active context presence.
- Added an independent runtime queue producer/consumer coverage audit:
  `runtime_queue_coverage_audit.py` and
  `apps/audit_runtime_queue_coverage.py`.
- The audit now covers three offline contracts: runtime queue entries must have
  frozen context, BPF generation assignments must match the three-slot physical
  buffer, and route identity producers must cover runtime consumers.
- The audit reads the frozen topology once per replay and reuses it across
  runtime queue entries, so the gate does not grow by repeatedly parsing the
  same large artifact.
- The operational-clean runner now writes
  `runtime_queue_route_identity_coverage.json` before compose and blocks
  precompose when route identity producers do not cover frozen queue
  consumers.
- Profile summary artifact contracts now surface failed route identity coverage
  as a blocker label.
- `known_issues_runbook.md` now records this as the standard pre-run
  producer/consumer coverage flow for future frozen lifecycles, not an H4-only
  check.
- No live probe, route reinstall, fallback, full reload, or hidden repair was
  added.

## Validation

- `py_compile` passed for the touched runner/runtime/test modules.
- Focused route evidence tests passed, including the r111 case where prepared
  evidence exists without a matching active context.
- Runtime queue coverage audit tests passed, including planned producer
  coverage, missing actual evidence, future first-consumer identity misses,
  missing frozen context, BPF generation slot mismatch, combined pass, and
  one-topology-read queue replay.
- Offline artifact replay on r109, r110, and r111 expands 68 route identities:
  28 UT default routes and 40 GW local-origin return routes.
- Current r111 combined replay passes in about 1.7s with 25
  runtime-applicable entries, 25 BPF generation slot records, generation slot
  width 32, 884 route context records, and no blockers.
- Requiring actual prepared evidence on old r111 artifacts fails before compose
  with `PREPARED_ROUTE_IDENTITY_EVIDENCE_MISSING`, which is the desired cheap
  failure for the old missing-evidence class.
- Broader regression passed: 436 tests across runtime defaults, event timeline,
  horizon maintenance, topology, profile/session/preflight, and rollback
  cleanup.
- Post-run session check reports the operational-clean environment clean:
  containers/networks/pins/locks are 0 and long-run environment readiness is
  true.

## Rerun Gate

- Previous failed run/stage:
  r111 runtime epoch 4,
  `bpf_owned_edge_route_baseline_missing_before_runtime`.
- Cheap check or targeted replay that now catches it:
  `test_bpf_owned_route_identity_reuses_prepared_evidence_without_active_context`
  and `test_actual_evidence_must_cover_future_first_consumer_identity`.
- Adjacent lifecycle states covered:
  pre-runtime route baseline report, first-session backend evidence carry,
  runtime lookup consumption, frozen queue producer/consumer coverage,
  profile contract surfacing, and post-run cleanup readiness.
- Forbidden-work expectations:
  runtime apply commands remain 0 for this baseline check; no fallback,
  no live route probe, no route self-repair.
- Remaining late-failure risks:
  H1/H2/H3 worker boundary and ping observation still need live runtime
  evidence; startup physical preparation remains too heavy and should be
  optimized after a successful clean run establishes the required path.

## Watch Next

- Confirm r112 or the next long run passes the epoch-4 route evidence point.
- At H1 boundary, verify worker-prepared physical/attach/preload artifacts are
  consumed without active context drift.
- Separate startup physical preparation cost from runtime epoch cost in the
  next report.
