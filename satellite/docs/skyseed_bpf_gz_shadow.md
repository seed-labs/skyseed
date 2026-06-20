# Skyseed BPF G/Z Forwarding Shadow Prototype

Goal 8 builds an isolated tc-BPF shadow of RuntimeEpoch G/Z-style forwarding.
It does not change the canonical runtime, does not change G/Z semantics, does
not attach to the real Skyseed dataplane, and only operates in temporary
netns/veth namespaces.

Artifacts:

- `satellite/apps/bench_skyseed_bpf_gz_shadow.py`
- `satellite/experimental/bpf/skyseed_tc_gz_shadow.c`
- `satellite/baselines/skyseed_bpf_gz_shadow_summary.json`

## RuntimeEpoch Correspondence

The prototype models a RuntimeEpoch switch as one BPF array slot:

- `active_generation[0]` is the currently selected generation.
- `generation_status[generation]` marks which generations are installed and
  usable.
- `fec_forwarding[(generation, fec)]` holds the generation-specific forwarding
  decision.

The test uses destination IP as the smallest FEC stand-in:

| FEC | destination | meaning |
| --- | --- | --- |
| `FEC_G` | `10.91.0.100` | G-like forwarding entry |
| `FEC_Z` | `10.91.0.200` | local-delivery-like Z counter path |
| missing FEC | `10.91.0.250` | fail-closed unknown FEC |

The loaded policy is:

| generation | FEC | decision |
| --- | --- | --- |
| gen0 | `FEC_G` | forward to `gw1` |
| gen1 | `FEC_G` | forward to `gw2` |
| gen2 | `FEC_G` | inactive/drop |
| gen0 | `FEC_Z` | local delivery |

Gen1 is preloaded while `active_generation` is still gen0. The packet path only
consults the active generation, so the future entry does not affect gen0
forwarding.

## BPF Map Schema

Maps are classic tc ELF maps pinned in the global tc namespace when supported by
the local iproute2/kernel path. The runner can also find them by BPF map id or
shape when pin names are not exposed.

| map | type | key | value | purpose |
| --- | --- | --- | --- | --- |
| `active_generation` | array | `u32` slot | `u32 generation` | selected RuntimeEpoch generation |
| `generation_status` | array | `u32 generation` | `u32 active` | installed-generation guard |
| `fec_forwarding` | hash | `{u32 generation, u32 fec}` | active/action/ifindex/next-hop/MAC rewrite | forwarding decision |
| `packet_counters` | array | `u32 counter` | `u64 count` | packet-path audit counters |
| `future_generation` | array | `u32` slot | `u32 generation` | records preloaded future generation for audit |

`fec_forwarding` values contain enough data for shadow decisions and tc
redirect attempts: active bit, action, egress ifindex, logical next-hop id,
destination MAC, and source MAC.

## Generation Switching

Userspace preloads all tested generation entries through `bpftool map update`.
Switching is only:

```text
active_generation[0] = new_generation
```

No packet-path table mutation is needed during the switch. In the isolated run,
gen0 chooses gw1, gen1 chooses gw2, and gen2 drops because its edge is inactive.

## Fail-Closed Behavior

The BPF program drops these cases:

- active generation missing or not marked installed;
- known generation with unknown FEC;
- known generation/FEC with missing or inactive forwarding edge;
- malformed short Ethernet/IP packet.

Non-IP traffic is passed so ARP and namespace setup traffic can function. The
fail-closed checks are for IP packets that represent G/Z FEC traffic.

## Redirect Result

On this VM on 2026-06-20:

- tc-BPF attach succeeded for both `classifier/redirect` and
  `classifier/shadow`.
- BPF `active_generation` lookup succeeded.
- BPF `(generation, FEC)` lookup succeeded.
- tc-BPF redirect to the expected next-hop interface succeeded:
  - gen0 `FEC_G` packets reached only `gw1`;
  - gen1 `FEC_G` packets reached only `gw2`.
- End-to-end ICMP reply did not succeed for redirected `FEC_G` packets.
  The recorded blocker is
  `end_to_end_ping_reply_failed_after_expected_next_hop_rx`.

This means the prototype validates tc redirect as a next-hop steering mechanism,
but it does not claim a complete L3 forwarding dataplane.

## Counters And Audit

The packet counter map records:

| counter | purpose |
| --- | --- |
| `generation_hit` | active generation was present and installed |
| `fec_hit` | packet matched a known FEC |
| `redirect_pass` | BPF selected a forward/local pass action |
| `drop_missing_generation` | active generation missing or inactive |
| `drop_missing_fec` | destination did not map to a known FEC |
| `drop_inactive` | route entry missing or inactive |
| `future_generation_not_used` | future generation was installed but not active |
| `local_delivery` | `FEC_Z` local-delivery-like path |
| `next_hop_gw1` | decision selected gw1 |
| `next_hop_gw2` | decision selected gw2 |
| `shadow_pass` | shadow section selected pass without redirect |

The final run reported nonzero values for generation hits, FEC hits,
redirect/pass decisions, all required fail-closed drops, future generation not
used, and both next-hop decisions. This is enough for a later audit layer to
compare intended generation/FEC decisions with BPF map state and packet-path
counters.

## Current Result

The baseline JSON reports:

- `experiment.ok`: true.
- `tc_bpf_attach.redirect_attach_ok`: true.
- `tc_bpf_attach.shadow_attach_ok`: true.
- `redirect_success`: true for next-hop redirect.
- `redirect_end_to_end_ping_success`: false.
- `redirect_blocker`: `end_to_end_ping_reply_failed_after_expected_next_hop_rx`.
- residue: clean for Goal 8-owned namespaces, veths, pinned maps, and tc filter
  residue possibility.

No `satellite/output` artifact is created.

## Next Step

This is ready to enter a Skyseed G/Z FEC intent to BPF generation map compiler
prototype. The next step should compile semantic FEC artifacts into the
`active_generation`, `generation_status`, and `fec_forwarding` map updates used
here, while keeping this isolated from the canonical runtime until the compiler
and audit story are explicit.
