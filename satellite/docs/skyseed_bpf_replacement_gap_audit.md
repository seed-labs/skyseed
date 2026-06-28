# Skyseed BPF Replacement Gap Audit

Date: 2026-06-21

Scope: audit only. This pass did not run long real-runtime, did not change G/Z
semantics, did not change runtime behavior, did not write `satellite/output`,
and did not push.

## Executive Finding

The current experimental BPF backend is an overlay/canary dataplane probe, not a
BPF-owned host-apply replacement.

This explains the Goal 20 S5 A/B result:

- A and B both executed the same canonical host network mutation path.
- A and B both executed the same canonical node-local Linux/MPLS path.
- BPF was attached after the epoch loop had already completed, then exercised by
  a bounded ping and rolled back.
- Therefore host typed command counts are expected to be identical:
  - Runtime steady-state host command count: 14331 in A and 14331 in B.
  - ParameterEpoch host qdisc command count: 3122 in A and 3122 in B.

The small runtime host-apply timing difference in B is not evidence of command
elision. It is wall-time variance on the same command surface.

## Evidence

### Runner ordering

`satellite/apps/run_starlink_shell_experiment.py` applies the canonical host
mutation plan first:

- `network_executor.apply(...)` is called for every non-noop epoch
  ([run_starlink_shell_experiment.py](/home/seed/skyseed/satellite/apps/run_starlink_shell_experiment.py:4948)).
- ParameterEpoch qdisc time is computed from canonical
  `update_slot_parameters` apply results
  ([run_starlink_shell_experiment.py](/home/seed/skyseed/satellite/apps/run_starlink_shell_experiment.py:5001)).
- RuntimeEpoch node-local Linux/MPLS apply then calls
  `real_installer.apply_and_validate(...)`
  ([run_starlink_shell_experiment.py](/home/seed/skyseed/satellite/apps/run_starlink_shell_experiment.py:5052)).

Only after the epoch loop and final connectivity validation does the runner call
`apply_bpf_experimental_backend(...)`
([run_starlink_shell_experiment.py](/home/seed/skyseed/satellite/apps/run_starlink_shell_experiment.py:5658)).
That call attaches BPF, pings, records counters, and rolls back.

### BPF report explicitly says canonical remains

`apply_bpf_experimental_backend()` initializes:

- `canonical_runtime_replaced=false`
- `linux_direct_backend_replaced=false`
- `canonical_runtime_unchanged=true`
- `qdisc_timeline_backend_status=blocked_or_trace_only`

See [bpf_dataplane_canary.py](/home/seed/skyseed/satellite/bpf_dataplane_canary.py:122).

The compiled BPF plan also records:

- `shadow_only=true`
- `canonical_runtime_changed=false`
- `linux_direct_backend_replaced=false`
- `real_dataplane_touched=false`
- `qdisc_timeline_landing=false`

See [bpf_backend.py](/home/seed/skyseed/satellite/bpf_backend.py:627).

### BPF C program is designed as fall-through overlay

The C source comment says userspace loads a bounded selector-derived subset while
the canonical Linux/direct backend remains in place, and packets outside loaded
BPF scope fall through
([skyseed_tc_dataplane_canary.c](/home/seed/skyseed/satellite/experimental/bpf/skyseed_tc_dataplane_canary.c:3)).

The actual classifier:

- non-IP packets count `canonical_fallback` and return `TC_ACT_OK`
  ([skyseed_tc_dataplane_canary.c](/home/seed/skyseed/satellite/experimental/bpf/skyseed_tc_dataplane_canary.c:205)).
- IP packets with no `fec_forwarding` entry also count `canonical_fallback` and
  return `TC_ACT_OK`
  ([skyseed_tc_dataplane_canary.c](/home/seed/skyseed/satellite/experimental/bpf/skyseed_tc_dataplane_canary.c:223)).
- only packets that pass generation/FEC/parameter checks and have a route
  `ifindex` return `bpf_redirect(...)`
  ([skyseed_tc_dataplane_canary.c](/home/seed/skyseed/satellite/experimental/bpf/skyseed_tc_dataplane_canary.c:280)).

## Current BPF-Owned Scope

Current BPF ownership is best described as map/counter ownership, not canonical
host-apply ownership.

It owns:

- Compiled G/Z RuntimeEpoch generation metadata:
  `active_generation`, `generation_status`.
- Compiled G/Z FEC intent metadata:
  `fec_forwarding` entries in full-scope mode.
- Compiled ParameterEpoch metadata:
  `parameter_generation`, `param_status`, `parameter_values`.
- Runtime packet decisions for packets that hit the live tc lookup model:
  active generation, active FEC, parameter active/loss/drop, skb mark, redirect,
  and counters.
- A bounded smoke dataplane route for selected live ping target IPs.

It does not own:

- The canonical NetworkMutationPlan host mutation surface.
- Linux MPLS route/LFIB/rule/iptables generation.
- qdisc delay/rate/bandwidth.
- veth/netns/interface/address topology realization.
- Runtime rollback/cleanup verification semantics.

Important implementation nuance: full-scope mode loads semantic FEC keys using
`fec_key_u32`, but the tc program currently looks up `fec_forwarding` by
destination IPv4. The live redirect path is therefore mainly the extra
`scope.entries[*].target_ip` smoke route loaded after semantic entries, not a
complete semantic G/Z FEC forwarding replacement. See
[bpf_dataplane_canary.py](/home/seed/skyseed/satellite/bpf_dataplane_canary.py:1229)
and [bpf_dataplane_canary.py](/home/seed/skyseed/satellite/bpf_dataplane_canary.py:1259).

## Repeated Canonical Operations

These operations still generate and execute normally under
`--bpf-backend experimental --bpf-scope full_eligible_scope`.

Host/direct runtime:

- veth create/move/rename/up/down.
- bridge membership changes.
- container namespace interface address flush/add/up.
- direct host qdisc delete/replace.
- link-state metadata persistence.

Examples are in [network_runtime.py](/home/seed/skyseed/satellite/network_runtime.py:2064),
[network_runtime.py](/home/seed/skyseed/satellite/network_runtime.py:2137), and
[network_runtime.py](/home/seed/skyseed/satellite/network_runtime.py:2364).

Node-local Linux/MPLS runtime:

- `sysctl net.mpls.platform_labels`.
- `sysctl net.mpls.conf.<if>.input=1`.
- serving-satellite `ip rule add`.
- serving-satellite policy-table default route with `encap mpls`.
- serving-satellite UT delivery route.
- forward LFIB `ip -f mpls route replace`.
- gateway forward pop LFIB.
- gateway `iptables` FORWARD rules.
- gateway SNAT rule.
- UT default route.
- shared G/Z reverse compatibility route and LFIB installs.
- verify and rollback probes.

Examples are in [linux_mpls_runtime.py](/home/seed/skyseed/satellite/linux_mpls_runtime.py:2527)
and shared reverse installs are in
[linux_mpls_runtime.py](/home/seed/skyseed/satellite/linux_mpls_runtime.py:1018).

## Existing Skip / Elision Logic

No BPF-owned skip/elision hook was found in the audited paths.

Present skips are unrelated to BPF:

- noop ParameterEpoch skip in the runner.
- parameter safety filtering to keep ParameterEpochs parameter-only.
- direct backend prewarm/trusted cached-state skips.
- Linux/MPLS incremental cleanup and shared G/Z return preservation.

Missing BPF hooks:

- No semantic intent `backend_owner=bpf` marking before host or node-local apply.
- No BackendCompilePlan output consumed by `network_executor.apply`.
- No BPF-owned filter in `MinimalRealLinuxMplsInstaller._apply_specs`.
- No BPF-owned filter in `_shared_zone_return_apply_specs`.
- No BPF-owned fallback policy in `DirectNetnsVethSlotLinkNamespaceQdiscInstaller`.
- No audit field that reports canonical commands elided because BPF owns them.

## What Can Be Safely Elided Later

Safe means "safe after BPF replacement has equivalent ownership, validation,
fallback, rollback, and audit." It is not safe in current code.

Likely elidable for BPF-owned G/Z forwarding:

- Forward-path Linux MPLS route/LFIB installs for BPF-owned FECs:
  serving-satellite policy route with `encap mpls`, transit LFIB swaps, gateway
  forward pop.
- Shared G/Z reverse route/LFIB installs for BPF-owned reverse forwarding, if
  the BPF program also owns reverse delivery and NAT/return behavior or the
  design explicitly keeps reverse on Linux.
- Per-FEC route/rule installs that exist only to select the MPLS forwarding path.
- Node-local verify probes for elided MPLS entries, replaced by BPF map/counter
  verification and dataplane probes.
- Rollback deletes for entries that were never installed by Linux in BPF-owned
  mode.

Conditionally elidable:

- serving-satellite source `ip rule`: only if BPF attach point sees the packet
  before the policy route is needed and has enough information to select the
  correct egress.
- gateway FORWARD/SNAT rules: only if BPF replacement also owns NAT/access
  delivery semantics or if the traffic exits through a preserved Linux gateway
  path.
- UT default route: only if an alternative BPF-owned UT ingress/egress attach
  path is installed. Usually this remains needed for host stack routing.
- MPLS sysctls/platform labels/MPLS input: elidable only when no Linux MPLS
  path remains in use for the selected scope.

## What Cannot Be Elided Yet

Should remain canonical in the minimum replacement:

- veth/netns/interface/address realization. BPF needs real links and namespaces
  to attach to and to preserve topology.
- qdisc delay/rate/bandwidth. Current BPF only models active/loss/drop/counters;
  `delay_ms`, `rate_bps`, and `bandwidth_mbps` are explicitly excluded.
- Direct host metadata/link-state persistence.
- Preflight, cleanup, and residue checks.
- Control/non-target traffic fallback unless the replacement explicitly owns it.
- Any Linux route/rule/iptables/NAT behavior outside the marked BPF-owned G/Z
  forwarding scope.

## Fallback / Pass Counters

Goal 20 B reported:

- `canonical_fallback=650`
- `pass=650`
- `redirect=3`
- `runtime_generation_hit=4`
- `fec_hit=4`
- `parameter_generation_hit=4`
- `parameter_entry_hit=3`
- `drop=1`

The counter shape means many observed packets fell through the BPF classifier
with `TC_ACT_OK`; it does not by itself classify whether those packets were
control/non-target traffic or eligible traffic that missed the current lookup.

Evidence-supported interpretation:

- Some fallback is expected and acceptable: non-IP, ARP/control, and traffic
  outside selected target IPs are designed to fall through.
- The run has positive BPF signal: redirects and generation/FEC/parameter hits.
- The run does not prove that eligible traffic is mostly BPF-handled, because
  fallback packets are not tagged with eligibility/miss reason.

Given the lookup mismatch noted above, a large fallback/pass count should be
treated as a coverage caveat, not only as harmless control traffic.

## Minimum BPF-Owned Replacement Design

No runtime behavior should change until this is implemented behind an explicit
opt-in replacement mode.

1. Semantic ownership marking

Add an ownership pass after G/Z FEC planning and before backend apply:

- Mark runtime forwarding intents with `backend_owner=linux_mpls|bpf`.
- Mark scope precisely: generation, FEC id, zone id, UT/GW, forward/reverse
  direction, route prefix/target IP, and fallback owner.
- Preserve G/Z semantic objects unchanged; add backend ownership metadata around
  them.

2. BackendCompilePlan

Promote the existing report-only `BackendCompilePlan` into an apply contract:

- `bpf_owned_forwarding_intents`
- `linux_owned_forwarding_intents`
- `direct_owned_link_operations`
- `qdisc_owned_parameter_operations`
- `fallback_policy`
- `expected_elided_linux_specs`
- `must_preserve_linux_specs`
- `counter_expectations`

3. LinuxMplsBackend skip

Add skip at the spec-building layer, not by deleting semantic G/Z intent:

- Filter `_apply_specs()` and `_shared_zone_return_apply_specs()` by
  `backend_owner`.
- Emit `SkippedRuntimeCommandSpec` audit records for elided Linux routes/LFIBs.
- Keep verify/rollback aligned: do not verify/delete Linux entries that were not
  installed; verify BPF maps/counters instead.

4. DirectNetnsBackend fallback

Keep direct topology operations by default:

- veth/netns/addr/link state remain direct-owned.
- qdisc remains direct-owned for delay/rate/bandwidth.
- allow only targeted qdisc elision for fields BPF truly owns, such as loss,
  after qdisc/BPF parameter semantics are reconciled.

5. Audit/report fields

Add fields to `epoch_execution_details.jsonl`, BPF report, and A/B summary:

- `backend_ownership_summary`
- `bpf_owned_intent_count`
- `linux_owned_intent_count`
- `elided_linux_mpls_command_count`
- `elided_ip_route_count`
- `elided_ip_rule_count`
- `elided_iptables_count`
- `elided_qdisc_count`
- `preserved_direct_command_count`
- `bpf_eligible_packet_count`
- `bpf_eligible_redirect_count`
- `bpf_eligible_fallback_count`
- `fallback_by_reason`

6. BPF dataplane model

Before eliding Linux MPLS, the tc program needs a real replacement lookup model:

- key route lookups by actual packet fields and scope metadata, not only
  semantic `fec_key_u32`.
- classify miss reasons: non-IP, non-target, no active generation, missing FEC,
  unsupported direction, unsupported protocol, fallback-to-Linux.
- cover forward and chosen reverse behavior or explicitly keep reverse Linux
  owned.
- load attach points for every BPF-owned ingress/egress needed by the selected
  scope.

## Readiness To Enter Replacement

Yes, the next phase can enter BPF-owned G/Z forwarding replacement design, but it
should be treated as a new opt-in mode rather than a small extension of the
current canary.

Minimum recommended first target:

- BPF-owned forward G/Z FEC for selected UT-to-gateway target IPs.
- Linux-owned reverse/NAT/qdisc/veth preserved.
- Explicit command elision only for forward MPLS route/LFIB installs that map to
  the selected BPF-owned forward path.
- Counters must distinguish eligible redirect from eligible fallback.

Do not claim host-apply replacement until the A/B report shows nonzero elided
Linux/MPLS command counts and reduced canonical host/node-local command counts
for the BPF-owned scope.
