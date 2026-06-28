# S4 v11 strict BPF-owned lifecycle fix

## Diagnosis

v11 reached real EventTimeline execution with Linux G/Z/MPLS/LFIB fallback disabled, but final pre-rollback connectivity failed after BPF counters could not be read:

```text
/sys/fs/bpf/tc/globals/packet_counters: No such file or directory
```

The relevant runtime ordering in `DirectGatewayRuntimeInstaller.apply_and_validate` prepared and attached the new BPF-owned backend before incremental cleanup. For incremental epochs, `rollback_session` then cleaned the old BPF-owned session with `remove_maps=True`, which can detach clsact and unpin global BPF maps after the new backend has already been prepared. Final validation could therefore run after the strict BPF dataplane had been removed.

## Fixed Ordering

The strict `bpf_owned` runtime now uses this order:

1. Select changed, held, and cleanup flow sets.
2. Run incremental cleanup of removed/replaced flows.
3. Prepare BPF-owned backend: compile, cleanup-before-attach, attach, create/pin maps, populate maps, set active generation.
4. Run preflight.
5. Apply RuntimeTransaction and ParameterUpdateBatch node-local operations.
6. Run epoch/canary validation.
7. Run final pre-rollback lifecycle guard.
8. Run final pre-rollback connectivity validation.
9. Allow BPF cleanup/rollback only after final validation.

## Guard

Before final connectivity validation, strict `bpf_owned` now asserts:

- attached BPF/clsact count is greater than zero;
- `packet_counters` exists and is readable;
- `active_generation` exists, is readable, and matches the expected final generation;
- expected forward and return route entries exist for validation flows;
- attached interface ifindices still match current runtime interfaces.

If any check fails, validation returns `bpf_lifecycle_not_ready_for_validation` instead of running misleading pings.

## Override Flow Gap

The two v11 secondary-gateway override gaps were caused by duplicate packet-route selection for the same BPF map key shape, not by missing return entries. The BPF route key is effectively `(generation, ingress_ifindex, dst_ipv4)`, so an ordinary flow's secondary validation target could occupy the same source-UT ingress and destination as the override flow's primary target.

Route generation now resolves those conflicts by preferring primary validation targets and `special_prefix_override` contexts. This keeps the fix general and avoids UT/IP special cases.

## New Artifact Fields

Final validation now carries:

- `bpf_attached_before_final_validation`
- `bpf_maps_present_before_final_validation`
- `bpf_packet_counters_readable_before_final_validation`
- `bpf_active_generation_before_final_validation`
- `expected_final_generation`
- `bpf_cleanup_started_after_final_validation`
- `bpf_validation_lifecycle_order`

## Verification

Targeted unit coverage was added for:

- strict final-validation lifecycle guard blocking missing counters;
- secondary-gateway override primary target winning packet-route conflicts;
- existing BPF-owned packet route generation behavior.
