# r134: Shared G/Z architecture offline gate

Date: 2026-07-11
Status: validated offline; no long run started

## Goal

- Replace per-UT or target-IP gateway paths with immutable UT longest-prefix
  policy, stable gateway-selector labels, shared G-to-Z/Z-to-G FECs, explicit
  gateway service loopbacks, and zone-edge delivery.
- Preserve the frozen-horizon, three-slot lifecycle and nonblocking observer
  contract while changing the dataplane ABI.

## Changes

- The v2 compiled ABI adds `ut_gateway_policy_map`,
  `gateway_selector_ingress_map`, and `zone_edge_delivery_map`. Runtime never
  writes the static UT policy map.
- Gateway choice is explicit and fail-closed. The planner has no shortest-path,
  preferred-list, target-IP, Linux MPLS, or compatibility fallback path.
- Return core state is shared by gateway/zone/path. Per-UT state exists only at
  the UT selector edge and responsible-satellite delivery edge.
- Runtime ping requests are frozen with their published session/generation/
  slice context. Superseded observations are reported separately and global
  miss/drop deltas are audit facts, not an epoch gate.
- The companion check replans historical snapshots with the current policy and
  compiler; historical v1 compiled maps are never consumed by the v2 ABI.

## Offline Evidence

- Full unit discovery: 778/778 passed.
- BPF C compile: return code 0 with selector/policy/delivery capacities present.
- 72-satellite, two-UT, two-slice preview: four flow contexts per slice, two
  shared forward and two shared return FECs, with explicit selector and service
  metadata and no fallback/compatibility output.
- r134 frozen-corpus replay: 100/100 generations, H0-H3 each 25/25, including
  H3 physical generation zero; blocker count 0 and map-conflict count 0.
- Companion lifecycle gate: 26/26 passed, including current-plan strict compile,
  explicit final binding capture, generation-zero consumption, observer-gap
  replay, and the new 100-generation shared-G/Z check.
- Canonical profile dry-run remains continuous 4H with no epoch cap and a
  30-second nonblocking runtime observer.

## Stop Boundary

- No canonical real-runtime or long experiment was started for this round.
  The companion's bounded link fixtures completed their scoped cleanup.
- The requested implementation and offline gate are complete. A future real
  run requires a separate authorization and must begin from the recorded
  canonical dry-run plus the 26/26 companion result.
