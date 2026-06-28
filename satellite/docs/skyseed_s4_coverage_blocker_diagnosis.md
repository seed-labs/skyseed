# Skyseed S4 Coverage Blocker Diagnosis

Date: 2026-06-21

Recommendation: workload catalog must be redesigned before any 300+ run.

No S4/300+ real runtime was run. No long S5 A/B was run. No G/Z, FEC, MPLS-like semantics, strict `bpf_owned` fallback behavior, qdisc/TheaterQ behavior, or `satellite/output` artifacts were changed.

## Decision

The current S4 preview is not a meaningful runtime gate because all six required service zones remain unresolved, G/Z FEC generation stays at 0/0, and `expected_bpf_owned_scope_count` stays 0. A real runtime would mostly exercise lifecycle and parameter churn, not the G/Z forwarding or BPF-owned path the gate is supposed to prove.

The root cause is a workload/catalog compatibility problem, not simply S4 density or a 120s timing miss. The required-zone policy needs one satellite that clears the zone-center coverage threshold and is also visible to every UT assigned to that zone. In this preview, `--ground-placement shell-aware` relocates UTs and gateways to effective shell-aware coordinates while the required service-zone centers remain at their catalog coordinates. That makes member UTs thousands of kilometers away from their zone centers, so the common zone-plus-all-UT candidate set is empty.

## Preview Evidence

| Probe | Satellites | Layout | Samples | Runtime tx | Param batches | BPF-owned scope | G/Z FEC fwd/ret | Longest all-required interval |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| S4 120s | 348 | 6 x 58 | 13 | 10 | 3 | 0 | 0 / 0 | None |
| S4 600s | 348 | 6 x 58 | 61 | 55 | 6 | 0 | 0 / 0 | None |
| S3/720 120s | 720 | 36 x 20 | 13 | 9 | 4 | 0 | 0 / 0 | None |

Current S4 120s window: start `2026-06-21T17:26:11Z`, horizon 120s, step 10s, 13 samples. S4 shell artifact: 348 satellites, 6 planes x 58 satellites, inclination bucket 97.5 deg, altitude bucket 550 km, record inclination 97.6 deg.

The S4 600s preview changed the short-window diagnosis for North America East from center-margin failure to UT attach mismatch, but it still resolved 0 required zones across 61 samples. The S3/720 120s fallback preview also resolved 0 required zones, despite denser center coverage.

## Required-Zone Diagnosis

| Required zone | Max best elevation deg | Max center candidates above threshold | Max common zone+UT candidates | Effective UT avg/max distance from zone km | Diagnosed reasons over S4 120s |
| --- | ---: | ---: | ---: | ---: | --- |
| zone-east-asia-pacific-wide | 55.062 | 11 | 0 | 10313.9 / 12371.2 | zone anchor selection failed: UT attach mismatch: 13 |
| zone-europe-west-wide | 74.102 | 11 | 0 | 9399.4 / 12788.8 | zone anchor selection failed: UT attach mismatch: 13 |
| zone-middle-east-south-asia-wide | 83.589 | 12 | 0 | 9177.3 / 12173.6 | zone anchor selection failed: UT attach mismatch: 13 |
| zone-north-america-east-wide | 6.949 | 0 | 0 | 8880.5 / 13189.9 | coverage margin below threshold: 13 |
| zone-north-america-west-wide | 45.346 | 11 | 0 | 9279.5 / 14644.8 | zone anchor selection failed: UT attach mismatch: 13 |
| zone-south-america-wide | 54.824 | 11 | 0 | 11561.9 / 13965.9 | zone anchor selection failed: UT attach mismatch: 13 |

The compact JSON contains all 78 required-zone/sample rows under `required_zone_sample_diagnostics`; each row includes zone id/name, center, required flag, candidate count, best satellite/elevation/margin, threshold, gateway IDs, UT IDs, planner state, and diagnosed reason.

Key interpretation:

- North America East is below the 7 deg zone-center threshold in the 120s S4 window, with max elevation 6.949 deg. Over the 600s S4 probe, it does clear the center threshold, but still has zero common zone/UT candidates.
- The other five required zones have good zone-center candidates in all 13 S4 samples, but zero candidates that are simultaneously visible to all four zone UTs.
- No required-zone sample reaches the gateway-path scoring stage with a usable common candidate, so G/Z FEC generation failure is downstream rather than the first cause.
- Default and secondary gateway assignments exist in the catalog for all required zones; the blocker is not missing gateway metadata.

## Catalog Compatibility

The realistic catalog itself groups UTs near regional zones: catalog UT average distance from zone center is 691 km for North America West, 792 km for North America East, 851 km for Europe West, 925 km for Middle East/South Asia, 1,985 km for East Asia/Pacific, and 2,079 km for South America.

The preview effective coordinates are not region-coherent under shell-aware placement. Required-zone effective UT average/max distances from the fixed zone center are:

| Required zone | Effective UT avg km | Effective UT max km | Effective gateway max km |
| --- | ---: | ---: | ---: |
| zone-east-asia-pacific-wide | 10313.9 | 12371.2 | 6289.2 |
| zone-europe-west-wide | 9399.4 | 12788.8 | 12828.9 |
| zone-middle-east-south-asia-wide | 9177.3 | 12173.6 | 17901.8 |
| zone-north-america-east-wide | 8880.5 | 13189.9 | 7923.8 |
| zone-north-america-west-wide | 9279.5 | 14644.8 | 14644.8 |
| zone-south-america-wide | 11561.9 | 13965.9 | 8939.3 |

This is why neither a longer S4 preview nor S3/720 density fixes the gate with the current inputs: the workload requires a single zone anchor to cover an effectively global UT set.

## Fix Options

- Adjust preview time window: helps center coverage for some zones, but S4 600s still has zero common zone/UT candidates and zero G/Z FEC.
- Adjust required/optional zone selection: can create a bounded gate sooner by requiring only spatially coherent zones, but reduces broad workload coverage.
- Adjust zone anchors: split wide regions or anchor per coherent UT cluster. This preserves service semantics better than silently relaxing the all-UT attach rule.
- Adjust GW placement/default-secondary mapping: useful after anchors resolve, but current failures happen before gateway-path scoring for the required-zone set.
- Use S3/720 fallback: worth keeping as a later density comparison, but S3/720 120s still fails with this catalog and should not be the next real-runtime gate by itself.
- Reduce unrealistic required constraints: explicitly reclassify remote-return/boundary cases or use smaller per-zone UT sets. This changes the gate workload and should be documented.
- Keep S4 with a different workload catalog: preferred if S4 remains the intended first 300+ run. Use catalog/effective coordinates consistently, or disable shell-aware relocation for realistic catalog previews so zone membership, UT placement, and gateway placement share the same geography.

## BPF Evidence Note

A separate gate issue remains: the latest strict BPF artifact has global counters but lacks successful-path per-interface seen/redirect counters required by the readiness gate. I did not change BPF artifacts or tests in this diagnosis, because the coverage workload blocker prevents the S4 gate from exercising BPF-owned G/Z scope at all.

## Output Pointers

- Primary S4 preview: `/tmp/skyseed_pre_s4_preview_final_full`
- S4 600s coverage probe: `/tmp/skyseed_s4_coverage_probe_600s_10s`
- S3/720 120s fallback probe: `/tmp/skyseed_s3_720_coverage_probe_120s_10s`
- Compact machine summary: `satellite/baselines/skyseed_s4_coverage_blocker_diagnosis_summary.json`
