# Skyseed Kernel-Deep Feasibility

Goal 4 was run as an isolated feasibility package. It did not change canonical
runtime behavior, did not attach a new backend, and did not run a long
real-runtime workload.

Evidence artifacts:

- `satellite/apps/skyseed_kernel_feature_probe.py`
- `satellite/apps/bench_skyseed_control_plane_ops.py`
- `satellite/apps/bench_skyseed_bpf_generation.py`
- `satellite/experimental/bpf/skyseed_tc_generation_probe.c`
- `satellite/baselines/skyseed_kernel_deep_feasibility_summary.json`

## Current VM/Kernel Capability Summary

Evidence-backed:

- Kernel: `5.4.0-54-generic` on x86_64 Ubuntu 20.04-era VM.
- `ip` and `tc` are present: `iproute2-ss200127`.
- Current user cannot directly create netns: `ip netns add` fails with
  permission denied.
- Passwordless sudo is available and sufficient for isolated netns/veth/tc
  experiments.
- A sudo smoke test successfully created a test netns, veth pair, moved one
  endpoint into the netns, added `tc qdisc netem delay 1ms`, deleted it, and
  cleaned up.
- `bpftool`, `clang`, and `llc` are not available in `PATH`.
- `/sys/fs/bpf` is mounted, but not writable by the current non-root user.
- `/sys/kernel/btf/vmlinux` is absent.
- `cls_bpf`, `act_bpf`, `sch_netem`, `sch_htb`, `mpls_router`, and
  `mpls_iptunnel` have `modinfo` entries, but were not loaded during the
  feature probe.
- `ip -f mpls route show` succeeds, but MPLS route add/delete inside the test
  netns failed with `Operation not supported`.

Hypothesis:

- This VM is adequate for netns/veth/qdisc/ip-route control-plane timing.
- This VM can benchmark BPF map syscalls as root, but is not currently adequate
  for tc-BPF/XDP attach work without adding toolchain packages and likely BTF or
  header support.
- MPLS route mutation needs separate setup or module/sysctl activation before
  it can be used as a comparable isolated benchmark on this host.

## Control-Plane Microbench Results

All control-plane measurements used 20 iterations through subprocess/iproute2
with passwordless sudo. Each test used isolated names and finished with
`residue.clean=true`.

Evidence-backed timings:

| operation | p50 | p95 | max |
| --- | ---: | ---: | ---: |
| veth create+delete | 49.57 ms | 56.32 ms | 59.99 ms |
| veth move netns + rename + up/down | 91.07 ms | 107.09 ms | 120.19 ms |
| tc netem add | 9.50 ms | 15.21 ms | 22.52 ms |
| tc netem change | 9.23 ms | 13.53 ms | 28.84 ms |
| tc netem delete | 9.35 ms | 13.84 ms | 21.51 ms |
| ip route add | 9.65 ms | 14.14 ms | 15.00 ms |
| ip route replace | 9.53 ms | 13.32 ms | 14.48 ms |
| ip route delete | 9.82 ms | 13.13 ms | 13.14 ms |
| quarantine veth activation | 157.17 ms | 200.26 ms | 217.60 ms |
| quarantine veth deactivation | 149.89 ms | 180.55 ms | 298.99 ms |

Evidence-backed:

- `pyroute2` is not installed, so direct netlink comparison was skipped.
- The subprocess path imposes a visible floor around 9-10 ms for single
  `ip route` and `tc qdisc` operations on this VM.
- veth lifecycle and namespace movement are substantially more expensive than
  route/qdisc mutation.
- Strict quarantine-slot activation/deactivation is the most expensive tested
  path because it serializes multiple namespace moves and renames.

Hypothesis:

- Direct netlink batching is likely a low-risk improvement for route/qdisc
  operations because it targets the repeated subprocess floor without changing
  semantic G/Z behavior.
- Quarantine-slot semantics are safer but too expensive to use as the default
  high-cardinality activation path without batching or a shadow substrate.

## BPF Map / Generation Feasibility

Evidence-backed:

- Running as root, `bpf(BPF_MAP_CREATE)` successfully created an array map.
- `BPF_MAP_UPDATE_ELEM` over 5000 iterations measured:
  - p50: 3.96 us
  - p95: 7.52 us
  - max: 230.27 us
- Single-key `active_generation` update over 5000 iterations measured:
  - p50: 9.43 us
  - p95: 14.44 us
  - max: 436.37 us
- tc-BPF attach was skipped because `clang` is unavailable. `bpftool` and `llc`
  are also unavailable, and BTF vmlinux is absent.
- The experimental C file is not part of canonical runtime. It is only a
  future isolated attach probe.

Hypothesis:

- Active generation switching is promising for a future G/Z forwarding shadow:
  the measured syscall update cost is several orders of magnitude below the
  current subprocess control-plane costs.
- This evidence does not yet prove packet-path correctness, verifier
  compatibility, tc attach viability, XDP viability, or visibility semantics.
- A next BPF goal should first add a reproducible tc-BPF attach/counter smoke
  on an explicitly prepared VM, then test pass/drop/pass generation switching.

## Qdisc / Netem Change Cost

Evidence-backed:

- `tc netem change` has p50 9.23 ms and p95 13.53 ms through subprocess/tc.
- The cost is similar to `tc netem add/delete` and single `ip route` changes,
  suggesting the dominant cost in this microbench is userspace command
  invocation and netlink round-trip, not netem-specific computation.

Hypothesis:

- ParameterEpoch qdisc updates will suffer when update cardinality grows,
  because each changed link currently pays this per-command cost.
- A qdisc timeline backend could reduce high-frequency ParameterEpoch churn,
  but only if it preserves active-state visibility and avoids introducing a
  kernel-module operational burden.

## Visibility Semantics Risk

Evidence-backed:

- Strict quarantine-slot activation/deactivation was tested by creating veths
  in a quarantine namespace, moving endpoints into two node namespaces on
  activation, and moving them back on deactivation.
- The measured p50 activation/deactivation costs were 157.17 ms and 149.89 ms.
- The residue check was clean after the benchmark.

Semantic observations:

- Strict quarantine-slot mode prevents node namespaces from seeing future edge
  interfaces before activation, but it is expensive.
- A radio-port or optimized-substrate mode can precreate substrate artifacts and
  make activation cheaper, but it risks visibility leakage if inactive ports,
  qdiscs, routes, or BPF map slots are visible to packet path or node-local
  observers.
- A BPF/default policy must fail closed for unknown generation, missing policy,
  or missing map slot.
- Skyseed-style observer/audit should continue to read semantic active state,
  not raw substrate state, when an optimized substrate contains inactive future
  slots.

Hypothesis:

- Optimized-substrate mode is feasible only with an explicit inactive-slot
  contract: inactive slots must be unreachable from packet path, hidden from
  semantic observer output, and fail-closed if accidentally selected.
- Strict quarantine-slot mode remains useful as a reference implementation and
  semantic oracle, but should not be assumed cheap enough for large-scale churn.

## TheaterQ / Qdisc Timeline Feasibility

Evidence-backed from public sources:

- TheaterQ has public source at `https://github.com/cs7org/TheaterQ`.
- The repository describes TheaterQ as a Linux qdisc for dynamic link emulation
  with CSV trace files and includes a kernel module directory plus an iproute2
  tc plugin directory.
- The repository license is GPL-2.0.
- Build/use requires kernel headers, build tools, `make`, `insmod
  sch_theaterq.ko`, and building a tc plugin with the iproute2 submodule.
- The trace format can express latency, jitter, rate, loss, queue limit,
  duplicate probability/delay, route ID for reordering, and queue ID.
- TheaterQ supports syncgroups for synchronizing multiple qdisc instances.
- The paper page says TheaterQ targets dynamic network emulation, supports
  delays, bandwidth limits, packet loss, duplication, and reordering, and is
  open source under GPLv2.

Sources:

- GitHub: https://github.com/cs7org/TheaterQ
- Paper: https://arxiv.org/abs/2510.27057

Fit for Skyseed ParameterEpoch:

- Delay: yes.
- Bandwidth/rate: yes.
- Loss: yes.
- Duplication: yes.
- Reordering: yes, via route IDs / delay behavior.
- Per-link timeline: yes, via trace files per qdisc instance.
- Multi-qdisc synchronization: yes, via syncgroups.

Risk:

- TheaterQ is an out-of-tree kernel module and tc plugin, so it is not suitable
  for canonical runtime without a separate operational/security decision.
- This VM was not used to build or load TheaterQ. No kernel module was loaded.
- Ubuntu 20.04 suitability is plausible but unproven; it depends on kernel
  header availability and source compatibility with this 5.4 kernel.

Hypothesis:

- TheaterQ-style qdisc timeline is worth a separate isolated demo only after
  rollback/cleanup instrumentation and netlink batching clarify the current
  bottleneck shape.
- If TheaterQ itself is not adopted, Skyseed can still borrow its design ideas:
  per-link trace files, qdisc-local replay, sync groups, active position stats,
  and clear load/arm/run stages.

## Recommended Next Priority

Evidence-backed recommendation:

1. Add rollback/cleanup typed instrumentation first.
2. Add netlink batch experiments next.
3. Prepare a BPF shadow attach VM and run pass/drop/pass generation switching.
4. Keep qdisc timeline as a separate goal, not part of canonical runtime.

Rationale:

- Rollback/cleanup was already identified as the largest tail cost, and this
  round confirms namespace movement is expensive. More typed counters are
  needed before optimizing blindly.
- Netlink batching is low risk because it can target `ip route` and `tc qdisc`
  command overhead below the existing semantic contract.
- BPF active_generation is promising, but this round only proves map syscall
  cost. It does not yet prove verifier, attach, packet-path behavior, or
  semantic isolation.
- TheaterQ-style qdisc timeline addresses future ParameterEpoch cardinality,
  but it carries out-of-tree kernel-module risk and should remain isolated.

Unsupported conclusions:

- This round does not prove tc-BPF or XDP attach works on the current VM.
- This round does not prove TheaterQ builds on Ubuntu 20.04 / kernel 5.4.
- This round does not compare subprocess iproute2 against pyroute2/direct
  netlink because `pyroute2` is absent.
- This round does not run real Skyseed runtime A/B and cannot quantify total
  end-to-end speedup.
