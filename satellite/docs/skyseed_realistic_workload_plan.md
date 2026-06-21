# Skyseed Realistic Workload Preview Plan

Goal 18 prepares a larger, more realistic preview workload for later S5 A/B and
larger-shell acceptance work. It does not replace the canonical runtime route:
planned-shell `S5`, 172 satellites, 12UT/6GW valid6 catalogs, and the 6-zone
metadata catalog remain the stable real-runtime path.

The new workload is preview-only and experimental. It is intentionally not wired
into `run_starlink_shell_experiment.py`, not run as real-runtime, and not part of
the BPF backend A/B path.

## Generated Preview Catalogs

The generator is:

```bash
python satellite/apps/plan_realistic_skyseed_workload.py
```

It writes compact catalog and summary files under
`satellite/configs/starlink_shell_ground/`:

- `realistic_preview_gateway_catalog_12.json`
- `realistic_preview_ut_catalog_32.json`
- `realistic_preview_zone_catalog_16.json`
- `realistic_preview_workload_summary.json`

Every generated entry has `preview_only: true` and source
`skyseed_goal18_realistic_preview_catalog`.

## Shell / Profile

The preview analysis uses the existing planned-shell `S1` profile as a
high-pressure preview / upper-tier target:

- planes: 72
- satellites per plane: 22
- satellites: 1584
- altitude: 550 km
- inclination: 53 degrees

`S1` is not the immediate next real-runtime acceptance target. Its cardinality
is retained as an upper-bound pressure estimate for the realistic workload.
The next real-runtime step should first use an intermediate shell tier after a
coverage/cardinality preview.

This is an existing repository profile, so Goal 18 does not invent a canonical
larger-shell profile. The sampling window is 2 hours from
`2026-01-01T00:00:00Z`, sampled every 300 seconds at 10 degrees minimum
elevation. The coverage model follows the current Skyseed planned-shell preview
geometry convention.

The existing planned-shell metadata currently available in `starlink_shell.py`
is:

| Shell | Planes | Sats / Plane | Total Sats | Suggested Use |
| --- | ---: | ---: | ---: | --- |
| S1 | 72 | 22 | 1584 | Tier 2 high-pressure preview and final 1000+ satellite stress target |
| S2 | 72 | 22 | 1584 | Tier 2 alternate 1000+ satellite pressure profile |
| S3 | 36 | 20 | 720 | Tier 1 fallback intermediate profile if 300+ satellite coverage is insufficient |
| S4 | 6 | 58 | 348 | Tier 1 preferred intermediate 300+ satellite larger-shell preview target |
| S5 | 4 | 43 | 172 | Tier 0 canonical baseline and BPF experimental backend A/B target |

## Staged Shell Acceptance

Larger-shell acceptance should be staged instead of jumping directly from
canonical `S5` to `S1`.

| Tier | Shell | Role |
| --- | --- | --- |
| Tier 0 | `S5` 172-sat | Canonical baseline and BPF experimental backend A/B target. This remains the stable route with the 6-zone metadata catalog. |
| Tier 1 | Prefer `S4` 348-sat; fall back to `S3` 720-sat | Next larger-shell coverage/cardinality preview and first real-runtime acceptance candidate. If 300+ satellites cannot satisfy required-zone coverage or availability, move to 700+. |
| Tier 2 | `S1` / `S2` 1000+ sat | Final pressure/stress tier. Use only when 700+ is still insufficient or when the objective is explicit upper-tier pressure testing. |

The immediate next real-runtime target should therefore be an intermediate
Tier 1 shell, not `S1`.

## Ground Placement Plan

The major-region gateway plan places two gateways per region:

| Region | Default GW | Secondary / Exception GW |
| --- | --- | --- |
| north-america-west | Seattle `gw201` | Denver `gw202` |
| north-america-east | New York `gw203` | Atlanta `gw204` |
| europe-west | Frankfurt `gw205` | Madrid `gw206` |
| middle-east-south-asia | Dubai `gw207` | Mumbai `gw208` |
| east-asia-pacific | Tokyo `gw209` | Singapore `gw210` |
| south-america | Sao Paulo `gw211` | Santiago `gw212` |

Each required major region has four UT roles:

- default gateway near UT
- secondary gateway near UT
- zone boundary UT
- remote return-path UT

Optional zones have two service UTs each, one default-affinity and one
secondary-affinity. Observation zones have no service UTs and are present only
for coverage and boundary behavior.

## Zone Classification

The catalog has 16 zones:

- required: 6
- optional: 4
- observation: 6

Required zones are wide regional service areas rather than small points next to
gateways. Radius values range from 1150 km to 1900 km for required zones.
Optional service zones cover Africa West, Africa East, Australia East, and
Central Asia. Observation zones cover Arctic Atlantic, North Atlantic, North
Pacific, South Pacific, Indian Ocean, and Southern Ocean windows.

Each active service zone has:

- default gateway
- secondary gateway
- preferred gateway pair
- one secondary-gateway prefix override

There are 10 prefix overrides total, one per active service zone. The preview
flow split is 22 default-gateway flows and 10 override flows.

## Preview Analysis

Compact output from
`realistic_preview_workload_summary.json`:

| Metric | Estimate |
| --- | ---: |
| UT count | 32 |
| GW count | 12 |
| zone count | 16 |
| required / optional / observation zones | 6 / 4 / 6 |
| active edge count | 3190 |
| access edges | 44 |
| fixed ISL edges | 1584 |
| cross-plane edges | 1562 |
| changed parameter links per ParameterEpoch | 265 |
| qdisc updates per ParameterEpoch | 265 |
| qdisc full-refresh upper bound | 3190 |
| RuntimeEpoch transition count in preview window | 23 |
| affected nodes per RuntimeEpoch | 64 min / 64 avg / 64 max |
| changed G FEC per RuntimeEpoch | 20 min / 20 avg / 20 max |
| skipped G FEC per RuntimeEpoch | 0 min / 0 avg / 0 max |
| changed GZ FEC per RuntimeEpoch | 20 min / 20 avg / 20 max |
| skipped GZ FEC per RuntimeEpoch | 0 min / 0 avg / 0 max |
| cold-start changed G FEC | 20 |
| cold-start changed GZ FEC | 20 |
| default / override flow count | 22 / 10 |

The skipped FEC estimate is zero because, under the sampled `S1` 2-hour window,
all active service zones changed anchors at every 5-minute transition. That is a
useful risk signal: this preview workload is deliberately more demanding than
the current 6-zone canonical route. Treat these S1 cardinality numbers as an
upper-tier pressure estimate; the next runtime-oriented preview and A/B should
start with Tier 1 (`S4`, then `S3` only if required coverage is insufficient).

## Coverage / Dwell

Required-zone unresolved duration is 0 seconds for every required zone in the
sampled window. All zones, including observation zones, had availability ratio
1.0 under the sampled S1 geometry.

The average zone anchor dwell is 300 seconds. Because the sampling cadence is
300 seconds, this means the responsible satellite changed on every sampled
boundary. A finer preview cadence should be run before real-runtime acceptance
criteria are set, but this is enough to show that the larger shell and wider
zones can drive much higher update cardinality than the canonical 6-zone route.

The spatial window spans latitude -50.0 to 64.2 degrees and longitude -160.0 to
153.03 degrees. UT placement is intentionally not clustered only next to
gateways:

- default gateway near UTs: 10
- secondary gateway near UTs: 10
- zone boundary UTs: 6
- remote return-path UTs: 6

## Default / Secondary Gateway Policy

Default GW is the normal region exit. Secondary GW is the exception path used to
exercise prefix override, non-default exit selection, and G/Z return-path cases.

The active service zones have 20 gateway classes total:

- 10 default classes
- 10 secondary / override classes

This preserves the G/Z semantic split: gateway-class forward FECs and
gateway-zone return FECs remain logical planner groups, not a new runtime table
semantics.

## Real-Runtime Blockers

These gaps must be closed before using this workload as real-runtime input:

- The 12-GW preview IX layout is synthetic and has not been validated against
  the current SEED Mini Internet startup capacity.
- Coverage, dwell, and update cardinality are sampled estimates, not timing or
  dataplane evidence from real-runtime.
- Observation zones intentionally have no service UTs and must stay out of
  required runtime gates.
- A finer cadence preview is needed to set RuntimeEpoch and ParameterEpoch
  acceptance thresholds.
- Tier 1 S4/S3 coverage/cardinality preview and A/B criteria need to be
  defined before any canonical promotion.
- `S1` is an upper-tier pressure target, not the first larger-shell
  real-runtime target.
- The canonical S5 172-sat + 6-zone metadata path must remain the stable route
  until the above checks pass.
