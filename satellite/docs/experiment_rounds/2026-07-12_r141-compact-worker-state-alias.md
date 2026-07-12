# r141: Compact worker handoff and frozen/live state alias

Date: 2026-07-12
Status: validated

The durable reproduction and refactor checkpoint is
`docs/skyseed_1_0_baseline.md`.

## Goal

- Remove scheduler-side duplicate successor-horizon preparation and keep the
  rolling-horizon boundary compact: referenced immutable context in, compact
  ready result out, detailed attach/preload evidence in external artifacts.
- Attribute bootstrap, scheduler blocking, worker work, live epochs, and final
  cleanup separately without adding a new runtime gate.

## Findings

- The compact preview and frozen consumer replay passed, and the real run
  `s4-1_0-compact-handoff-long2-20260712` crossed the former 900-second startup
  point. It completed H0 25/25 and consumed an H1 worker result containing all
  25 inactive generations.
- The first H1 consumer then failed closed with
  `bpf_preloaded_generation_missing`. Generation 32, its intent hash, horizon,
  topology snapshot, and source epoch all matched the real target.
- The remaining mismatch was representational: the worker record used
  `frozen_prepare_host_state:<snapshot>:<epoch>`, while the live scheduler used
  `live_state:<snapshot>:<epoch>`. The host-plan boundary already treated these
  namespaces as aliases; the lower BPF preload selector still required raw
  string equality.

## Changes

- BPF preload lookup now compares frozen/live host-state IDs by exact snapshot
  and epoch identity. Unknown forms and any snapshot/epoch mismatch remain
  fail-closed.
- The long-run companion check now exercises both alias acceptance and stale
  identity rejection. This is an in-process semantic check and adds no
  containers, network setup, or long timeout.

## Validation

- The real H1 handoff artifact replay selects the expected generation-32 record
  from all 25 records after the fix.
- Focused alias and fail-closed tests: 4/4.
- Runtime/worker/event-queue/companion tests: 381/381.
- Full discovery: 791/791.
- BPF source compilation: return code 0 (valid cache hit for the current source
  hash).
- Post-failure and fresh preflight: clean; zero Compose containers/networks,
  direct host interfaces, BPF pins, and runtime locks. Disk remains a warning,
  not a blocker.
- The r141 rerun completed H0-H3 100/100 with aggregate runtime success,
  successful rollback, return code 0, and clean post-run preflight.
- All three maintenance workers published 25/25 preloads with zero blockers in
  120.71s, 93.47s, and 99.30s. H2/H3 each completed all 25 retired-generation
  cleanup records. Scheduler registration cost was 6.31s, 2.42s, and 1.63s;
  successor physical preparation and preload did not block an epoch.
- Runtime BPF counter delta contained 56 egress handoffs, 56 label pops, and 56
  successful kernel-5.4 manual-pop fallbacks with zero manual-pop failures.
  Runtime miss/drop delta, missing-generation/label drops, role-map misses, and
  Linux fallback commands were all zero.
- The nonblocking observer produced 30 raw records: 27 valid and three
  superseded. Of the valid records, 25 succeeded and two isolated probes timed
  out on different UT/gateway/policy tuples. Both affected gateways have valid
  successes elsewhere, and the following ut2013 observation succeeded; no
  fixed-gateway or same-flow consecutive outage was established.
- Steady runtime transition average was 7.51s (p95 14.31s, worst 18.15s).
  Activation lag averaged 24.13s and peaked at 48.08s before recovering to
  0.30s by the final epoch. About 1.5-3s per epoch remains incompletely
  attributed outside current transition submetrics; this is a reporting and
  performance follow-up, not a correctness gate.

## Rerun Gate

- Previous failed stage: H0 complete, H1 first generation activation.
- Cheap replay: actual H1 worker record must match the live alias; a stale
  snapshot must not match.
- Adjacent states: exact ID equality, frozen/live alias equality, and true
  source-state mismatch are covered.
- Forbidden work: no runtime full-generation reload, Linux forwarding fallback,
  worker live discovery, or new hard timeout.

## Remaining

- Optionally externalize the remaining frozen base-generation/readiness payload
  so scheduler registration publishes only content references. Current costs
  amortize to at most 0.252s per epoch and are acceptable for the 1.0 baseline.
- Improve epoch timing attribution for loop/reporting overhead without adding a
  new runtime gate or changing the accepted scheduling semantics.
