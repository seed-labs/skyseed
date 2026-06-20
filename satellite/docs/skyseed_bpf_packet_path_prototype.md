# Skyseed BPF Packet-Path Prototype

Goal 7 is an isolated RuntimeEpoch packet-path probe. It does not change the
canonical runtime, does not change G/Z semantics, does not connect to the real
Skyseed dataplane, and only operates inside test netns/veth namespaces when the
local tc-BPF toolchain is present.

Artifacts:

- `satellite/apps/bench_skyseed_bpf_packet_path.py`
- `satellite/experimental/bpf/skyseed_tc_generation_path.c`
- `satellite/baselines/skyseed_bpf_packet_path_summary.json`

## Why This Is Separate From Goal 4

Goal 4 measured userspace writes to a BPF array map. The important result was
that updating an `active_generation` slot from userspace was fast: p50 about
9.4 us and p95 about 14.4 us on this VM.

That is not packet-path evidence. Map update cost measures the control-plane
side of switching the active generation. Packet-path lookup cost measures the
work done for each packet after a tc-BPF program is attached: verifier-accepted
program execution, map lookup, optional counter updates, and tc action return
cost. A G/Z forwarding BPF shadow backend needs both facts. Goal 4 only proved
the first one.

## Prototype Shape

The C prototype exposes several tc classifier sections:

| section | purpose |
| --- | --- |
| `classifier/empty` | tc-BPF attach and pass-through baseline |
| `classifier/lookup1` | one `active_generation` map lookup, then pass |
| `classifier/lookup3` | active generation, generation policy, and padding lookup |
| `classifier/counter` | pass-through with packet counter update |
| `classifier/generation` | active_generation-driven gen0 pass, gen1 drop, gen2 pass |

Maps are pinned under `/sys/fs/bpf/tc/globals/` when iproute2 tc supports the
classic `bpf_elf_map` pinning path:

- `active_generation`: key 0 contains the selected generation.
- `generation_policy`: generation id to pass/drop policy.
- `packet_counters`: pass/drop packet counters.
- `lookup_pad`: extra lookup target for overhead comparison.

The Python runner creates two namespaces, a veth pair, assigns `10.77.0.1/24`
and `10.77.0.2/24`, attaches the requested tc section to egress on the first
namespace's veth, updates maps through `bpftool`, runs short ping checks, and
cleans all namespaces, veths, qdiscs, and pinned maps in a `finally` path.

## Current Toolchain Status

Evidence from this VM on 2026-06-20:

| item | status |
| --- | --- |
| `clang` | missing from `PATH` |
| `llc` | missing from `PATH` |
| `bpftool` | missing from `PATH` |
| `tc` | available: `/usr/sbin/tc`, `iproute2-ss200127` |
| `/sys/fs/bpf` | exists and is mounted as bpffs |
| `/sys/kernel/btf/vmlinux` | absent |
| passwordless sudo | available (`sudo -n true` succeeds) |

Because `clang` and `bpftool` are missing, the isolated attach experiment is
blocked on this host. The runner records this as
`toolchain_or_privilege_blocked` and does not fabricate attach, pass/drop, or
overhead results.

Minimum package set to unblock this VM class:

- `clang`
- `llvm` or an equivalent package that provides `llc`
- `bpftool`
- `iproute2`
- `iputils-ping`
- optional: `iperf3` for throughput smoke tests

`/sys/kernel/btf/vmlinux` is useful for CO-RE workflows, but this prototype is
written against classic tc ELF maps and does not require BTF for the first
attach smoke.

## Current Results

The summary JSON for this run reports:

- tc-BPF attach attempted: no.
- Blocked reason: missing `clang`, missing `bpftool`; `llc` also missing and
  recorded.
- pass/drop/pass validation: skipped because attach is unavailable.
- packet-path overhead: skipped because attach is unavailable.
- throughput smoke: skipped because attach is unavailable.
- residue: clean for Goal 7-owned namespace/veth names and pinned map names.

No canonical runtime file was changed and no `satellite/output/` artifact was
created.

## Readiness For G/Z Forwarding BPF Shadow

Not ready on this VM yet. The control-plane map write evidence is promising,
but the missing toolchain means this run still does not prove:

- tc-BPF verifier acceptance for the generation classifier;
- attach to a veth packet path;
- runtime map updates visible to the classifier;
- gen0 pass, gen1 drop, gen2 pass behavior;
- per-packet map lookup/counter overhead;
- redirect or forwarding behavior needed by a future G/Z shadow backend.

The source and runner are ready to rerun on a prepared host. A positive next
result should be treated as a shadow-backend prerequisite, not as permission to
replace canonical G/Z semantics.
