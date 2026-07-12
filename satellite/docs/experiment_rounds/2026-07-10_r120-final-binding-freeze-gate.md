# r120: final binding freeze gate

Date: 2026-07-10
Status: stopped (H1 parent prepared-state handoff failure)

## Goal

- Complete the canonical 4H `operational_clean` / `bpf_owned` validation with
  100/100 runtime epochs and clean post-run teardown.
- Replace the r119 multi-source binding merge with one ordered handoff: active
  base generation cache, additive physical creation facts, exact final targeted
  capture, then 25 strict compiles sharing the final frozen cache.

## Findings

- r119 H1 failed on `netns:stl-940018:fs0018`: the active base generation MAC
  was `02:46:6d:44:a4:be`, while the post-attach readiness detail recorded
  `ba:06:f5:28:42:6f` for the same ifindex `19459`.
- The real r119 H1 replay finds 409 explicit final interface refs across 25
  generation contexts. Historical readiness conflicts with the base cache for
  121 required MACs, so readiness cannot be an initial cache source.
- The runtime queue coverage artifact intentionally owns compact counts and
  retains only 50 detail rows; its authoritative scope is 100 generation-slot
  records across H0-H3.

## Changes

- Horizon maintenance now initializes bindings only from the active base
  generation's complete frozen cache, merges additive creation facts, derives
  the exact 25-context requirement union offline, captures that explicit ref
  set after physical prepare, and lets final capture overwrite temporary or
  historical values before strict compilation.
- Exact final-capture scope fails closed on either a missing or unexpected ref.
  Final artifacts keep count/supersession summaries and do not embed the full
  final binding payload.
- The companion gate now replays r119's old conflict and new final boundary,
  while retaining r116 worker, r117 real-veth, r118 bootstrap producer, and
  r113 cleanup-failure evidence in the same chain.
- Standalone session-check tests now establish their own app import path rather
  than depending on another test module mutating `sys.path` first.

## Validation

- Compile checks pass for the worker, companion gate, and worker tests.
- Focused worker/gate tests: 39/39 pass.
- Broad requested regression set: 570/570 pass in a clean BPF-pin environment.
- Companion gate: 12/12 pass. r116 and r119 each strict-compile 25/25 real work
  items with live ifindex/MAC lookup patched to fail; r117 creates, captures,
  and removes a real veth; r118 H0 bootstrap producer completes strict compile.
- r119 final replay captures exactly 409/409 explicit refs, performs no scan,
  supersedes 121 MACs, and leaves `fs0018` at the recorded final MAC.
- H1-H5 lifecycle replay has no H4 special case: cleanup is `not_required` for
  the first worker, then targets H0/H1/H2/H3 respectively; cleanup generations
  are disjoint from current and target generations.
- Preflight is clean: zero containers, networks, direct interfaces, locks, and
  BPF pins. Host resources show about 17 GiB available memory, 2 GiB free swap,
  and 33.66 GiB free workspace disk.
- `git diff --check` passes.

## Rerun Gate

- Previous failed run/stage: r119, H1 maintenance final binding handoff before
  strict generation compile.
- Cheap check now catching it: `final_binding_conflict_replay` positively
  reproduces the old `fs0018` conflict, proves readiness is not premerged,
  applies exact final capture, and compiles all 25 H1 generations offline.
- Adjacent lifecycle states covered: r118 H0 bootstrap producer/consumer; r119
  H1 physical prepare, exact binding freeze, and 25 first consumers; H2/H3
  retired cleanup; H4/H5 three-slot reuse; missing/extra final refs and worker
  failure publication.
- Command source and run id: session-check
  `operational_iteration_profile.run_command`, replacing only `<run_id>` with
  `r120-4h-final-binding-freeze`.
- Forbidden work: Linux MPLS/LFIB forwarding, fallback, full reload, full scan,
  live dump/diff or binding discovery during compile, hidden reconcile,
  runtime attach, future qdisc/link exposure, early active flip, and synchronous
  observer/report work.
- Remaining late-failure risk: only real kernel/container execution time and
  external resource behavior remain; there is no known unresolved prepared
  input or lifecycle contract gap. Host prepare must resolve the current bridge
  netfilter warning before compose.

## Watch Next

- Confirm H0 final binding capture and 25 initial generation preloads succeed.
- Confirm the H1 ready artifact exists before the boundary, H2 worker cleans H0,
  and H3 worker cleans H1.
- Require 100/100 epochs, `aggregate_runtime_succeeded=true`, all fallback
  counters zero, real nonblocking ping observations, and clean post-run
  preflight.

## Run Result

- `r120-4h-final-binding-freeze` completed all 25 H0 runtime epochs. Its H1
  process worker completed 25/25 generation preloads and published ready before
  the boundary with all forbidden-work counters at zero.
- After the H1 ready artifact was consumed, the first host apply failed closed.
  The worker had physically prepared 91 additive resources, but the parent
  installer's prepared-owned index was still the H0-only index.
- The historical ready artifact made the adjacent contract error explicit: it
  published the 676-resource H1 target window as `prepared_resource_scope`,
  while the parent already owned 681 resources and the correct additive union
  was 772. Replacing the parent index with 676 would also drop still-owned
  resources, so the durable handoff must publish and accept the full union.
- Cleanup completed naturally. Final post-run preflight was clean with zero
  containers, compose networks, direct interfaces, runtime locks, and BPF
  program/map residue.
