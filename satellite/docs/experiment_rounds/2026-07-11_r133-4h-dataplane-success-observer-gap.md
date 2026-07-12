# r133: 4H dataplane success, observer gap

Date: 2026-07-11
Status: dataplane validated; full experiment incomplete

## Goal

- Validate the corrected generation-zero consumer through a complete canonical
  4H operational-clean run.

## Findings

- `aggregate_runtime_succeeded=true`; 100/100 epochs completed with H0-H3 each
  at 25/25 and zero runtime exceptions.
- Per-horizon average epoch wall time was H0 6.259s, H1 6.328s, H2 6.746s,
  and H3 5.720s. H3 physical generation 0 and its successors consumed normally.
- Rolling worker process wall times were 92.03s, 81.06s, and 89.80s. Retired
  cleanup completed 25/25 from H1 onward with zero blocked records.
- Linux/fallback counters, classifier misses, and worker forbidden-work counters
  were zero. Rollback and post-run preflight were clean.
- The canonical validation profile omitted `--runtime-ping-observer-seconds`.
  Runtime correctly left `ping_success=null`, but the observer was disabled and
  `runtime_ping_observation.jsonl` contained zero rows. This does not invalidate
  the 4H dataplane result, but it fails the requested complete experiment
  evidence contract.

## Changes

- The single active profile now explicitly enables the proven r93 nonblocking
  observer at 30-second intervals.
- Profile command construction, session-check contract, profile tests, and the
  companion command-shape gate require the positive observer interval.
- The companion gate replays r133 as the real evidence that 100/100 success can
  otherwise coexist with an empty observer artifact.

## Rerun Gate

- Previous incomplete run/stage: r133 post-run observation audit; dataplane
  100/100 passed, observer disabled.
- Cheap check now catching it: profile command shape requires
  `--runtime-ping-observer-seconds 30`; r133 gap replay requires enabled=false
  and observation_count=0 in the historical evidence.
- Adjacent lifecycle states covered: 4H generation-zero producer/consumers,
  H0-H5 cleanup replay, observer CLI propagation, nonblocking parser semantics,
  and post-run clean teardown.
- Command source and run id: canonical `rich_s4_operational_clean_bpf`;
  `r134-4h-nonblocking-ping-observer`.
- Forbidden work: observer results remain raw facts and never gate epochs or
  trigger fallback, repair, Mini-host validation, full scan, or reconcile.
- Remaining late-failure risk: real observer thread execution alongside the
  already validated 4H dataplane; no runtime-structure change is included.

## Watch Next

- Require 100/100 and aggregate success again.
- Require observer enabled, observation count greater than zero, target role
  `selected_gateway_access`, and raw success/failure facts without affecting
  epoch outcomes.
- Require zero forbidden counters and clean teardown.
