# r115: frozen direct maintenance

Date: 2026-07-09
Status: failed at H0 -> H1; root fix gated for r116

## Result

- The 4H command, four frozen horizons, compose startup, H0 BPF preparation,
  and all 25 H0 runtime epochs were valid.
- Runtime fallback, Linux G/Z fallback, eligible fallback, and full reload
  remained zero.
- H1 maintenance missed the 240-second boundary.
- At 268 seconds the worker was still running an exact live interface lookup:
  `ip -o link show dev xp29a` inside a satellite namespace.

## Root Cause

- The r114 fix correctly froze container PIDs, but each of 25 future-generation
  compiles still created fresh ifindex and MAC caches.
- The same resident interfaces were therefore queried up to 25 times.
- The worker also inherited a reused-clsact guard that probed every reused
  interface before executing the real delta attach.
- r93 proves that maintenance does require real physical work: H1 used 23
  delta attaches. Replacing attach with validation-only readiness would violate
  the boundary rather than fix it.

## Fix

- Process work consumes one complete frozen PID/ifindex/MAC binding set.
- All 25 generation compiles share those exact cache objects and strict misses
  fail without Docker or interface lookup.
- Frozen compile now carries deterministic control-plane IPv4 addresses per
  attached interface.
- Process maintenance uses a dedicated frozen direct-delta executor. It does
  not call the general live prepare path, probe clsact, discover addresses, or
  read filter stats. Existing interfaces receive direct filter operations; new
  delta interfaces receive a direct clsact add followed by filter operations.
- H1 generation activation preserves the union of attach directions, roles,
  control-plane addresses, ifindex, and MAC for H2/H3 workers.
- The standard preflight now has one operational-clean BPF-owned profile and no
  Linux native MPLS readiness output.

## Cheap Gate

- The companion check executes a real mocked delta attach and fails if live
  clsact/address/stats functions are reached or if attach becomes
  validation-only.
- It replays all 25 r115 process work items, rejects r115's incomplete frozen
  binding, and verifies four-horizon queue coverage.
- Cleanup lifecycle is H-boundary based: H2 maintenance cleans H0, H3 cleans
  H1, and triple-buffer bases rotate `0, 32, 64, 0, 32` through H4.
- Focused and adjacent tests cover direct command sequences, frozen cache
  sharing, H1 flip handoff, process ready publication, cleanup-before-ready,
  profile command shape, and clean preflight.

## r116 Watch Points

- H1 maintenance must publish ready well before the H0 boundary.
- Attach work may contain direct delta operations but no reused-interface
  discovery.
- During H1, retired H0 generation work must queue; H2 maintenance must consume
  it before publishing ready.
- All fallback/full-reload/full-scan/live-diff counters remain zero.
