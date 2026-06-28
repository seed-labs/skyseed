# Skyseed Parameterized Ground Model And S4 Workload Redesign

Date: 2026-06-21

Recommendation: PASS for the next bounded 300+ preview gate under `shell_aware_consistent`; do not treat this as approval to skip the bounded real-runtime readiness gate.

No S4/300+ real runtime was run. No long S5 A/B was run. No G/Z, FEC, MPLS-like, Linux fallback, qdisc, or TheaterQ semantics were changed. No `satellite/output` artifact was committed.

## Ground Model Contract

The workload now has an explicit coordinate policy instead of an S4-specific coordinate repair.

Supported policies:

- `real_geographic`: original catalog coordinates are the runtime coordinates. No shell-aware relocation is applied.
- `shell_aware_consistent`: original catalog coordinates are preserved as intent/provenance, and UT/GW/zone effective runtime coordinates are regenerated together from shell-aware representative points.
- `synthetic_shell_aware`: shell-aware effective coordinates are explicitly synthetic; any synthetic GW fallback is labeled synthetic in provenance.

The old `--ground-placement` flag remains for compatibility:

- `--ground-placement catalog` maps to `real_geographic`;
- `--ground-placement shell-aware` maps to `shell_aware_consistent` unless `--coordinate-policy` is supplied.

Mixing relocated UT/GW coordinates with fixed zone centers is not allowed. Artifacts record both original/catalog and effective/runtime coordinates.

## Zone And UT Semantics

Zone is a service-region / geographic-region / coverage-region abstraction. UTs are workload probes and do not define the zone.

The preview pipeline now separates three states:

- `zone_anchor_resolved`: the zone representative point has a responsible satellite and gateway path.
- `ut_attach_resolved`: assigned workload-probe UTs have visible serving satellites.
- `zone_service_resolved`: combined service state for active service zones.

The zone anchor satellite is no longer required to be the same satellite as every UT probe attachment. This removes the old false failure mode where a wide service area could not resolve unless one satellite covered both the zone center and every probe UT.

## Gateway Policy

Gateways remain catalog-first:

- each major service region has default and secondary gateways in the input catalog;
- `source`, `provenance`, and `version` metadata are preserved in effective gateway records;
- shell-aware effective coordinates may relocate runtime placement, but the catalog intent remains visible;
- if a future policy uses synthetic GW fallback, the effective gateway record must set synthetic provenance explicitly.

## Workload Probe Roles

UTs are workload probes with role metadata, not zone definitions. The full catalog still covers:

- default-GW-near probes;
- secondary-GW-near probes;
- boundary probes;
- remote-return probes;
- low-margin / handover-sensitive probes through zone coverage margin and anchor handover summaries.

The S4 preview used 32 UT probes, including 6 boundary probes and 6 remote-return probes.

## Hybrid Region Coverage

For shell-aware policies with a zone catalog, the generator uses `hybrid_region_coverage`:

- begin from major region / service-role catalog entries;
- choose shell-aware representative points for the effective zone centers;
- keep required / optional / observation classification unchanged;
- cluster effective UT probes and default/secondary gateways consistently around the effective region;
- emit coverage margin summary and unresolved reason breakdown.

## S4 Preview Evidence

Preview command shape: S4 planned shell, 348 satellites, 32 UTs, 12 gateways, 16 zones, `--coordinate-policy shell_aware_consistent`, 10s sample/evaluation interval, 120s horizon, no SEED render, no real runtime.

| Field | Value |
| --- | ---: |
| Satellites | 348 |
| Layout | 6 x 58 |
| UTs / GWs / zones | 32 / 12 / 16 |
| Active zones | 10 |
| Required zones | 6 |
| Samples | 13 |
| Required zones service-resolved | 6 / 6 for all 13 samples |
| Required zone anchors resolved | 6 / 6 for all 13 samples |
| Required UT attach resolved | 6 / 6 zones for all 13 samples |
| G/Z FEC last fwd/ret | 12 / 12 |
| Expected BPF-owned scope | 268 |
| RuntimeTransaction count | 10 |
| ParameterUpdateBatch count | 3 |

Required zones resolved across the full preview window; unresolved reason maps are empty.

## Required Zone Summary

| Zone | Service resolved | Anchor resolved | UT attach resolved | Min margin deg |
| --- | ---: | ---: | ---: | ---: |
| zone-north-america-west-wide | 13 / 13 | 13 / 13 | 13 / 13 | 50.153 |
| zone-north-america-east-wide | 13 / 13 | 13 / 13 | 13 / 13 | 54.263 |
| zone-europe-west-wide | 13 / 13 | 13 / 13 | 13 / 13 | 50.115 |
| zone-middle-east-south-asia-wide | 13 / 13 | 13 / 13 | 13 / 13 | 51.197 |
| zone-east-asia-pacific-wide | 13 / 13 | 13 / 13 | 13 / 13 | 50.115 |
| zone-south-america-wide | 13 / 13 | 13 / 13 | 13 / 13 | 50.115 |

Default/secondary gateway behavior exists for each required zone. Boundary and remote-return cases remain present in the UT workload probes.

## Fallback Decision

No S3/720 fallback preview was needed for this goal because the parameterized S4 preview passed required zone service resolution, G/Z FEC, expected BPF-owned scope, default/secondary GW behavior, and boundary/remote-return workload probe checks.

## Output Summary

Compact machine summary:

`satellite/baselines/skyseed_coordinate_consistent_s4_preview_summary.json`

Primary preview root:

`/tmp/skyseed_s4_parameterized_ground_model_preview_120s_10s`

Effective workload catalog:

`/tmp/skyseed_s4_parameterized_ground_model_preview_120s_10s/config/effective_workload_catalog.json`
