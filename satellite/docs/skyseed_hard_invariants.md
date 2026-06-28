# Skyseed Hard Invariants

Updated: 2026-06-24.

These invariants are non-negotiable project control rules. If a run or change
cannot satisfy them, the result must expose the blocker instead of hiding it.

## Forwarding Semantics

- G/Z FEC plus MPLS-like label switching is the core forwarding semantic.
- Strict `bpf_owned` mode must not use Linux G/Z, Linux MPLS, Linux LFIB, or
  Linux per-hop fallback for eligible BPF-owned scope.
- Linux MPLS and BPF MPLS-like forwarding are switchable backend
  implementations of the same logical G/Z/FEC intent. They are not mixed
  success paths.
- A packet-keyed per-hop IP-route shortcut is not a valid replacement for
  G/Z/FEC plus label/LFIB switching in strict BPF-owned scope.
- A tc flower local-IP bypass may exist only for exact local control-plane or
  IX diagnostic IPv4 traffic on classifier interfaces. It must be installed as
  `protocol ip` exact local-IP matching before BPF, must not match MPLS shim
  packets, UT/GW data-plane prefixes, or G/Z FEC traffic, and must not be
  counted as Linux G/Z/MPLS/LFIB fallback.
- Strict classifier miss in G/Z scope must still drop. MPLS shim LFIB/handoff
  miss must still drop. Any future failure must preserve packet-level
  first-failure counters and attribution rather than hiding the miss/drop.
- Core G/Z FEC identity is zone/class/anchor based. UT id must not enter the
  core G/Z FEC identity. UT-specific reverse labels are allowed only at the
  zone-local final-delivery handoff after return traffic has reached the zone
  anchor; core LFIB must not become per-UT packet-keyed forwarding.
- Zone-local final delivery may rewrite to the UT access IP only at the final
  handoff edge owned by the responsible zone anchor satellite.

## Update Semantics

- Sampling does not define updates.
- EventTimeline is the update authority and generates `RuntimeTransaction` and
  `ParameterUpdateBatch` work.
- Physical samples, timestamp-only changes, and metadata-only changes must not
  be promoted into runtime updates unless they create semantic forwarding or
  parameter work.
- Replay mode must pin replay-defining inputs from the baseline manifest and
  prepared bundle, not from wall-clock time. This includes
  `source_timestamp_utc` / coordinate epoch and the
  `prepared_update_bundle` selected epoch queue.
- A missing, regenerated, or mismatched replay source timestamp is a blocker.
  Do not normalize it away or weaken replay/equivalence fingerprints.

## Ownership Boundaries

- qdisc delay, rate, loss, and bandwidth remain Linux-owned for now.
- veth, netns, address, compose, and lifecycle setup/cleanup remain
  Linux-owned for now.
- BPF-owned scope is limited to proven eligible G/Z/MPLS-like forwarding work.
  Edge NAT, Mini Internet edge routing, ordinary lifecycle, and qdisc behavior
  are outside strict BPF-owned success claims unless a later gate explicitly
  proves otherwise.
- `operational_clean` plus strict `bpf_owned` is a no-hidden-fallback path:
  Linux MPLS, canary/experimental BPF, packet-route correctness evidence, ping
  success, or cleanup/rollback cannot contribute to PASS unless that module was
  explicitly selected as the authoritative backend.
- BPF map reload failure is a first blocker, not permission for full attach or
  dirty recovery in strict `operational_clean` reuse. The failed map,
  operation, generation, direct bpftool/syscall evidence, and map state must be
  preserved before any broader rerun.
- A BPF data-map update failure must not flip `active_generation`. The inactive
  generation may contain partial writes, but strict runtime must report the
  blocker and leave authoritative active generation unchanged by the loader.
- Every authoritative module must expose both semantic correctness and
  cost/cardinality sanity. In `operational_clean` plus strict `bpf_owned`,
  cleanup cost, steady full attach, unexpected qdisc/cardinality work, and
  unexplained timing buckets are blockers for broad reruns even when the process
  is still making progress.

## Workload And Coordinates

- UT, gateway, and zone inputs must use a consistent coordinate policy.
- Catalog/original coordinates may be preserved as provenance, but coverage,
  UT attach, gateway selection, zone anchor selection, and G/Z FEC planning
  must use the same effective/runtime coordinate policy.

## Runtime Gates

- S4/S3 real runtime must not run if G/Z FEC count is zero.
- S4/S3 real runtime must not run if expected BPF-owned scope is zero for the
  strict BPF-owned gate.
- Replay/equivalence must pass before shortened or long runtime conclusions are
  trusted. The accepted pinned replay gate for the shortened strict S4
  optimization regression was
  `full-s4-bpf-owned-replay-pinned-20260623-gate-only-r2` with
  `mismatch_count=0` and `source_timestamp_utc=2026-06-23T00:48:17Z`.
- Compose cleanup must use the same generated compose file and project name as
  startup, then run post-run preflight after compose down. A clean result
  requires zero compose project containers, zero compose project networks, zero
  direct backend host interfaces, zero BPF pins, and no runtime lock.
- BPF miss/drop acceptance must use validation deltas or an explicitly
  documented runtime-start baseline. Pre-existing cumulative counter residue
  must be accounted for before long-run acceptance.
- A failure must expose a blocker. Do not hide it with fallback, weakened
  validation, disabled checks, or UT/GW/satellite special-case hacks.
- `satellite/output` must not be committed.

## Current Acceptance Boundary

- `full-s4-bpf-owned-strict-opt-regression-20260623-pinned-r1` is accepted as a
  shortened full-S4 strict `bpf_owned` optimization regression PASS only.
- It is not long-run acceptance, final performance acceptance, or S5
  acceptance.
- Full BPF attach reuse is not yet proven. The accepted shortened run had two
  full attach-required epochs and one delta attach epoch.
