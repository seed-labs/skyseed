# r138-r140: Consumer replay, MPLS pop/IX repair, and 4H success

Date: 2026-07-11
Status: validated

## Findings

- r138 completed startup, then rejected the valid empty reapplied scope of the
  first shared-G/Z no-op transaction.
- r139 passed the repaired transaction boundary, but H1 maintenance recomputed
  a retired `ix205` binding even though mini-internet was explicitly disabled.
- r139 ping evidence reached classifier/LFIB/handoff, then dropped at MPLS pop:
  kernel 5.4 rejected negative `bpf_skb_adjust_room()` and the shared-G/Z
  refactor had accidentally removed the existing BPF manual-pop fallback.

## Changes

- Empty reapplied scope plus empty intents is now the explicit
  `shared_gz_noop_hold` transition; empty scope plus nonempty intents still
  fails closed.
- Prepared inputs are replayed through the ordered runtime consumer sequence
  before compose. The gate covers all 100 H0-H3 records and the same binding-ref
  derivation used by the maintenance process worker.
- Internal-only topology realization leaves `gateway_nat_interface` empty and
  requires zero retired `ix*` consumer binding references.
- The kernel-5.4 BPF manual MPLS pop fallback and its success/failure counters
  were restored. No Linux MPLS, NAT, forwarding, or mini-internet fallback was
  added.
- Fixed whole-build/startup timeout defaults remain zero; explicit progress and
  lifecycle evidence supervise expensive work.

## Validation

- Current full unit discovery: 785/785.
- Current BPF source compiles with a new source hash/cache key.
- Frozen consumer replay: 100/100, H0-H3 25 each, 1005 binding refs, zero IX
  refs, zero blockers.
- Companion v16: 28/28.
- r140 reused the immutable r139 prepared bundle and completed all 100 runtime
  transactions across H0-H3 with zero transaction failures. All three rolling
  maintenance workers published ready, each with 25 preloads and no blockers.
- BPF counters proved the real path: final cumulative evidence included 73
  egress handoffs, 73 label pops, and 73 successful manual-pop fallbacks with
  zero manual-pop failures and zero Linux fallback commands.
- The nonblocking observer produced 39 raw records. Four were superseded;
  among 35 valid observations, 32 succeeded and three isolated timeouts later
  recovered on the same gateways. It covered 20 UTs, all 12 gateways, and both
  default and special-prefix policy classes. There were no consecutive or
  fixed-gateway failures.
- r140 returned zero, reported aggregate runtime success, completed rollback,
  and passed clean post-run preflight with zero containers, project networks,
  host interfaces, BPF pins, or runtime locks.

## Rerun Gate

- The active gate tests current semantics rather than retaining every historical
  implementation assumption: complete ordered consumer replay, explicit
  internal-only binding scope, manual-pop source/observability contract, and
  adjacent lifecycle states.
- Historical r121 multicast/IX replay is retired from the active companion;
  old labels may remain only as provenance for current checks.

## Remaining

- Ping was not perfect: three of 35 valid samples timed out during an
  interval-zero, continuously transitioning run. All were isolated and each
  affected gateway later succeeded. Future paced experiments should preserve
  raw timeout evidence and compare the transient rate rather than hiding it
  with fallback or retry-only success.
