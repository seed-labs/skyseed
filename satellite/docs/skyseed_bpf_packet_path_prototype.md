# Skyseed BPF Packet-Path Prototype

Goal 7.5 reran the isolated RuntimeEpoch packet-path probe after enabling a
minimal BPF toolchain. It does not change the
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

Evidence from this VM on 2026-06-20 after Goal 7.5:

| item | status |
| --- | --- |
| `clang` | available: `/usr/bin/clang`, version 10.0.0 |
| `llc` | available: `/usr/bin/llc`, LLVM 10.0.0 |
| `bpftool` | available via linux-tools real binary: `/usr/lib/linux-tools/5.4.0-216-generic/bpftool`, v5.4.291 |
| `tc` | available: `/usr/sbin/tc`, `iproute2-ss200127` |
| `/sys/fs/bpf` | exists and is mounted as bpffs |
| `/sys/kernel/btf/vmlinux` | absent |
| passwordless sudo | available (`sudo -n true` succeeds) |
| Python runner | `/home/seed/miniconda3/envs/seed310/bin/python`, Python 3.10.20 |

Installed through Ubuntu apt official sources for Goal 7.5:

- `clang`
- `llvm`
- `linux-tools-common`
- `linux-tools-generic`
- `linux-tools-5.4.0-216`
- `linux-tools-5.4.0-216-generic`

Already present before or after dependency resolution:

- `gcc`
- `make`
- `pkg-config`

Not installed:

- `libbpf-dev`: package name was unavailable in the configured focal apt
  sources.

The Ubuntu `/usr/sbin/bpftool` wrapper still warns that a matching
`linux-tools-5.4.0-54-generic` package is missing for the running kernel. The
runner resolves and uses the actual newer linux-tools binary directly. This is
adequate for map show/update/dump in the isolated test.

`/sys/kernel/btf/vmlinux` remains absent. The prototype compiles without `-g`
and uses classic tc ELF maps, so BTF is not required for this attach smoke.

## Current Results

The summary JSON for the final Goal 7.5 run reports:

- tc-BPF attach attempted: yes.
- tc-BPF attach success: yes for generation, empty, lookup, and counter
  sections.
- Map access: yes. This old tc/kernel path did not expose map names or pinned
  map paths, so the runner falls back from pinned path/name lookup to
  `bpftool map show` shape matching and updates maps by id.
- pass/drop/pass validation: success.
- packet-path overhead: measured with 5 ping probes per case.
- throughput smoke: skipped because `iperf3` is not installed and is outside
  the authorized install set for this goal.
- residue: clean for Goal 7-owned namespace/veth names and pinned map names.

Pass/drop/pass:

| generation | expected | observed | ping |
| --- | --- | --- | --- |
| gen0 | pass | pass | 5/5 received, avg 0.077 ms |
| gen1 | drop | drop | 0/5 received, 100% loss |
| gen2 | pass | pass | 5/5 received, avg 0.077 ms |

BPF counters after the generation section:

| counter key | meaning | value |
| --- | --- | ---: |
| 0 | pass counter | 18 |
| 1 | drop counter | 10 |

The counter values are packet-path counters, not ping request counts; they
include tc-visible egress packets such as ARP and ICMP traffic around the ping
phases.

Ping RTT overhead smoke:

| case | avg RTT |
| --- | ---: |
| no BPF baseline | 0.061 ms |
| empty tc-BPF | 0.070 ms |
| 1 map lookup | 0.086 ms |
| 3 map lookups | 0.048 ms |
| counter update | 0.271 ms |

These are tiny 5-ping smoke measurements on a veth pair, so they should be read
as feasibility evidence, not as a stable performance distribution. The result
does show that map lookup and counter sections can run on the real tc packet
path.

No canonical runtime file was changed and no `satellite/output/` artifact was
created.

## Readiness For G/Z Forwarding BPF Shadow

Ready to enter the next isolated G/Z forwarding BPF shadow prototype, but not
ready to replace or alter canonical G/Z runtime behavior.

Evidence now proven:

- tc-BPF verifier/load path accepts the prototype sections.
- tc-BPF attaches to a test veth in an isolated namespace.
- BPF programs read `active_generation` and `generation_policy` maps.
- Userspace map updates change packet-path behavior.
- gen0 pass, gen1 drop, gen2 pass works.
- Packet counters update in the packet path.
- Residue cleanup is clean.

Still unproven:

- redirect or forwarding behavior;
- G/Z route/neighbor semantics;
- inactive future-state hiding for a real forwarding table;
- multi-link or multi-zone scale;
- integration with canonical observer/audit semantics.
