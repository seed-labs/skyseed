# Skyseed BPF Parameter Map Prototype

Goal 11 builds an isolated tc-BPF prototype for the ParameterEpoch packet-path
piece that is suitable for BPF maps. It does not change the canonical runtime,
does not change RuntimeEpoch or ParameterEpoch scheduling, does not attach to
the real Skyseed dataplane, and only operates in temporary netns/veth
namespaces.

Artifacts:

- `satellite/apps/bench_skyseed_bpf_parameter_map.py`
- `satellite/experimental/bpf/skyseed_tc_parameter_map.c`
- `satellite/baselines/skyseed_bpf_parameter_map_summary.json`

## Scope

This prototype proves that a BPF parameter map can provide immediate packet
decisions for a ParameterEpoch when topology is unchanged:

- read the selected `parameter_generation`;
- check that the selected parameter generation is installed;
- lookup link/flow parameters by `(generation, edge, flow)`;
- pass packets when the edge is active and loss does not fire;
- drop packets when the edge is inactive;
- drop packets when `loss_ppm` fires;
- fail closed when generation or parameter entries are missing;
- expose counters for later audit.

This BPF map is only for active/loss/drop/counter/mark style decisions. Delay,
bandwidth, rate limiting, reordering, and queue behavior remain qdisc timeline
work. Goal 11 is not the landing of an optimized ParameterEpoch backend; it is
an isolated prototype that establishes the packet-decision state shape.

## Test Topology

The runner creates two temporary namespaces connected by one veth pair:

| namespace | address | role |
| --- | --- | --- |
| client | `10.92.0.1/24` | sends ICMP probes |
| server | `10.92.0.2/24` | receives primary edge probes |
| server | `10.92.0.99/32` | receives missing-parameter probes |

The tc-BPF classifier attaches to ingress on the server-side veth. ARP and
unrelated traffic pass through; IPv4 probes to the two test addresses exercise
the parameter map.

## BPF Map Schema

Maps are classic tc ELF maps pinned in the global tc namespace when supported by
the local iproute2/kernel path. The runner can also find them by BPF map id or
shape when pin names are not exposed.

| map | type | key | value | purpose |
| --- | --- | --- | --- | --- |
| `parameter_generation` | array | `u32` slot | `u32 generation` | selected ParameterEpoch generation |
| `param_status` | array | `u32 generation` | `u32 installed` | installed-generation guard |
| `parameter_values` | hash | `{u32 generation, u32 edge, u32 flow}` | active/loss_ppm/mark | link/flow parameter decision |
| `parameter_counters` | array | `u32 counter` | `u64 count` | packet-path audit counters |
| `future_param_gen` | array | `u32` slot | `u32 generation` | records preloaded future generation for audit |

The test uses ICMP protocol as the flow id and destination IP as the smallest
edge stand-in:

| edge | destination | meaning |
| --- | --- | --- |
| `EDGE_PRIMARY` | `10.92.0.2` | normal link parameter lookup |
| `EDGE_MISSING` | `10.92.0.99` | known packet target with no parameter entry |

## Loaded Parameters

The runner preloads all tested generations before probes:

| generation | edge | active | loss_ppm | expected result |
| --- | --- | --- | --- | --- |
| gen0 | primary | true | 0 | pass |
| gen1 | primary | false | 0 | drop inactive |
| gen2 | primary | true | 1,000,000 | drop loss |

Gen1 is preloaded while `parameter_generation[0]` remains gen0. The packet path
only consults the active parameter generation, so the future inactive entry does
not affect gen0 traffic. The `future_generation_not_used` counter records this
audit condition.

## Fail Closed Behavior

The BPF program drops these cases:

- selected generation missing or not marked installed;
- selected `(generation, edge, flow)` parameter entry missing;
- parameter entry present but inactive;
- loss decision fires;
- malformed short Ethernet/IP packet.

Non-IP traffic and unrelated IPv4 destinations are passed so namespace setup and
neighbor discovery are not modeled as ParameterEpoch traffic.

## Counters And Audit

The packet counter map records:

| counter | purpose |
| --- | --- |
| `parameter_generation_hit` | selected generation was present and installed |
| `parameter_entry_hit` | selected `(generation, edge, flow)` parameter was found |
| `pass_count` | packet passed the BPF parameter decision |
| `drop_inactive` | packet dropped because active gate was false |
| `drop_loss` | packet dropped because `loss_ppm` fired |
| `drop_missing_generation` | generation guard failed |
| `drop_missing_parameter` | parameter map lookup failed |
| `future_generation_not_used` | future generation was preloaded but not active |

The baseline run reported nonzero values for generation hits, parameter hits,
passes, inactive drops, loss drops, missing generation, missing parameter, and
future-generation-not-used audit counters.

## Current Result

On this VM on 2026-06-20:

- tc-BPF attach succeeded for `classifier/parameter`.
- gen0 primary-edge traffic passed with gen1 preloaded.
- gen1 primary-edge traffic dropped via inactive gate.
- gen2 primary-edge traffic dropped via controlled `loss_ppm=1,000,000`.
- missing generation failed closed.
- missing parameter entry failed closed.
- residue was clean for Goal 11-owned namespaces, veths, pinned maps, and tc
  filter residue possibility.

No `satellite/output` artifact is created.

## Next Step

This is ready to feed a qdisc timeline or TheaterQ-style prototype for delay,
bandwidth, rate, and queue semantics. That next step should keep BPF parameter
maps focused on immediate packet decisions while modeling timeline-dependent
queue behavior outside this BPF map.
