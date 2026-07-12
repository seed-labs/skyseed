# Skyseed Pre-300 BPF-Owned Readiness Audit

Date: 2026-06-21

Scope: final readiness and hardening audit before any S4 / 300+ satellite run.
This audit did not run S4, did not run a long S5 A/B, did not change G/Z
semantics, and does not claim qdisc or lifecycle optimization.

## Status

PASS for starting the next staged bounded S4 run.

This is not an S4 acceptance result. It says the pre-300 `bpf_owned` mechanism
is now semantically general enough, packet-path validated in a bounded gate, and
honest about non-owned surfaces.

Authoritative bounded gate:

- run id: `bpf-owned-egressreverse-gate-20260621`
- `forwarding_backend=bpf_owned`
- `real_apply_success=true`
- `ping_success=true`
- `rollback_completed=true`
- post-run preflight ready/clean, blocking reasons `[]`

Compact evidence:
`satellite/baselines/skyseed_pre300_bpf_owned_readiness_summary.json`.

## Semantic Audit

`bpf_owned` remains plan-derived and does not special-case the passing ping.

- Logical proof is derived from `RuntimeUpdatePlan` through
  `temporal_planning.gz_fec_plan`.
- The proof includes G->Z and Z->G FEC entries, generation id, map count/hash,
  attached interfaces, fallback reason, and packet-route handoff details.
- Packet-keyed routes are generated from UT/GW/zone flow context:
  `g_to_z`, `g_to_z_access_ingress`, `z_to_g`, and `z_to_g_nat_ingress`.
- NAT return handoff is represented as a general backend-owned return handoff:
  it is derived from `selected_gateway_id`, `gateway_nat_interface`,
  `gateway_nat_source_ip`, `ut_access_ip`, reverse segment, and flow context.
- L2 rewrite, IPv4 rewrite, tc ingress/egress attach, and ifindex selection are
  backend implementation details for delivering the same G/Z intent. They are
  not new G/Z semantics.
- BPF packet map keys include generation, ingress ifindex, and destination IP;
  route values include action, redirect ifindex, optional return rewrite IP,
  and L2 rewrite data. This prevents accidental cross-epoch and many
  cross-interface matches.

Deterministic coverage now includes arbitrary `ut42` / `gw9` route generation
for access, core, reverse, and NAT-return directions, explicitly guarding
against `ut3` / `gw5` one-off behavior.

## Backend Mode Boundary

The four modes remain distinct:

- `linux_mpls`: canonical Linux MPLS/direct backend.
- `bpf_shadow`: emits BPF artifacts/counters only; Linux apply is retained.
- `bpf_canary`: packet-path probe; Linux apply is retained.
- `bpf_owned`: strict replacement for eligible satellite-core G/Z scope after
  pre-apply BPF readiness is proven.

Evidence from deterministic tests:

- `bpf_shadow` has `skipped_by_bpf_backend=0`.
- legacy/unstructured and logical-only BPF readiness proofs fail closed.
- stale/mismatched/partial proofs fail closed.
- proven `bpf_owned` skips only eligible satellite-core scope and leaves
  host-required scopes retained.

Evidence from the passing gate:

- requested node-local apply commands: `42`
- `skipped_by_bpf_backend`: `24`
- eligible core `fallback_to_linux`: `0`
- retained host-required commands: `18`
- G/Z core coverage: `6` G->Z, `6` Z->G, `12` MPLS readiness/sysctl commands.

## Negative Boundary Audit

The following are not claimed as optimized by BPF-owned forwarding:

- qdisc delay/rate/loss/bandwidth remains qdisc/netem owned;
- veth/netns/address lifecycle remains host/direct owned;
- ordinary lifecycle shell batches remain host/direct owned;
- Mini Internet routes remain edge/host-owned except for documented BPF
  return-handoff delivery;
- iptables/SNAT/conntrack remains Linux-owned, while the BPF return handoff is
  a general post-edge delivery handoff derived from flow context;
- fallback/control traffic remains outside BPF-owned satellite-core G/Z scope.

The gate's apply contract preserved non-owned scopes:

- `edge_nat_iptables`: `9`
- `ip_route_rule`: `9`
- `satellite_core_mpls_lfib`: `12`
- `mpls_readiness_sysctl`: `12`

Only eligible satellite-core/MPLS-readiness commands were skipped. Edge NAT and
route/rule commands were retained.

## Cleanup And Rollback

Cleanup remains active and bounded:

- rollback completed in the passing gate;
- final cleanup reported `all_cleanup_success=true`;
- post-run preflight reported ready/clean;
- direct backend host artifacts after run: `[]`;
- BPF clsact and pinned-map cleanup is part of the rollback path for
  `forwarding_backend=bpf_owned`;
- Linux MPLS/LFIB cleanup narrowing applies only to BPF-owned skipped core
  forwarding residue.

Skipped Linux MPLS/LFIB state is not required by the passing ping validation.

## Measurement Readiness

The current reporting distinguishes:

- phase wall time vs cumulative command wall time;
- `skipped_by_bpf_backend` count;
- `fallback_to_linux` count and reasons;
- BPF generation/proof/map/attach evidence;
- packet-route count and directions;
- qdisc command counts/wall time through operation classification;
- lifecycle/veth/shell-batch counts/wall time through operation classification;
- cleanup scope attribution;
- UT/GW/zone workload summaries and G/Z FEC plan counts.

No qdisc or lifecycle optimization is claimed. Those remain large expected cost
surfaces for S4.

## Workload Readiness For S4

The next shell target is S4:

- planned shell id: `S4`
- planes: `6`
- satellites per plane: `58`
- satellite count: `348`
- altitude: `560 km`
- inclination: `97.6 deg`

The current active S4 workload uses the `rich_s4_operational_clean_bpf` profile:
UT and zone semantics come from the S4 rich validation catalogs, while orbit
and gateway/base-station data come from `satellite/apps/visualization/`.

Catalog coverage:

- UT count: `32`
- gateway count: `12`
- zone count: `16`
- active service zones: `10`
- required service zones: `6`
- optional service zones: `4`
- observation zones: `6`
- default gateways: `6`
- secondary gateways: `6`
- prefix overrides / exception gateway behavior: `10`
- UT roles: `10` default-near, `10` secondary-near, `6` zone-boundary,
  `6` remote-return.

S4/S3 staging:

- S4 is the preferred Tier 1 bounded acceptance target.
- S3 / 720 satellites remains the fallback if S4 coverage or availability is
  insufficient.

Expected larger-workload G/Z and BPF-owned scope estimate from the preview
summary:

- gateway classes: `20`
- default gateway flows: `22`
- override flows: `10`
- changed G FEC estimate per runtime epoch: `20`
- changed GZ FEC estimate per runtime epoch: `20`
- expected BPF-owned G/Z FEC scope: about `40` semantic FEC entries per runtime
  epoch before packet-route expansion.

Coverage caveat: the broader catalog metadata is preview-derived and S4 itself
has not been run. The S4 run must still verify Mini Internet capacity,
container/resource pressure, timing, cleanup, and coverage in real runtime.

## Validation

Commands run after hardening:

```bash
python -m py_compile \
  satellite/linux_mpls_runtime.py \
  satellite/bpf_dataplane_canary.py \
  satellite/apps/run_starlink_shell_experiment.py \
  satellite/bpf_backend.py \
  satellite/test_rollback_cleanup_optimization.py \
  satellite/test_bpf_shadow_backend.py

python -m unittest \
  satellite.test_rollback_cleanup_optimization \
  satellite.test_bpf_shadow_backend \
  satellite.test_gz_fec_bpf_generation_compiler
```

Result: `Ran 24 tests`, `OK`.

## Remaining Risks For S4

These are not blockers to starting the next bounded S4 run, but they must be
watched during that run:

- S4 real-runtime cardinality has not been measured yet.
- The 32UT/12GW/16-zone catalog is preview-derived.
- qdisc and lifecycle work are not optimized and may dominate wall time.
- The BPF-owned return handoff is now generalized and tested, but S4 should
  confirm it across broader gateway/zone/UT distributions.
- Post-run preflight must remain clean before any larger or longer run.
