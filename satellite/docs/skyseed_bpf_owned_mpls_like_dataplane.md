# Skyseed BPF-Owned MPLS-Like Dataplane Prototype

## Decision

The strict `bpf_owned` dataplane must stop using packet destination-IP route
entries as the intermediate satellite-core forwarding primitive. The next
dataplane model is G/Z FEC classification at ingress followed by BPF-owned
label/LFIB switching across satellite-core hops.

The selected label carrier is a real MPLS unicast shim header, ethertype
`0x8847`, inserted and removed by tc-BPF:

* ingress tc-BPF classifies the packet into a G/Z FEC and pushes one shim;
* satellite-core tc-BPF reads the shim label and performs LFIB lookup;
* egress tc-BPF pops the shim before UT/GW/Mini Internet handoff.

This is a BPF-owned packet carrier, not Linux MPLS. Linux MPLS LFIB, Linux G/Z
routes, and Linux per-hop fallback remain disabled in strict mode.

`skb->mark` is not the selected carrier. It is not packet-visible, is already
used by parameter/qdisc handling, and would need separate proof that it survives
the exact redirect/veth/netns path without being overwritten. A shim header
keeps the forwarding label in packet bytes across redirects.

## Ingress Classifier

Ingress classification happens only at UT/GW/edge ingress. It may use packet
destination prefix and service class as classification input, but it outputs a
FEC/label action rather than a core route:

* key: generation, ingress ifindex, flow key, target prefix, service class;
* value: `fec_id`, direction, initial label, first out ifindex, rewrite metadata;
* action: push MPLS-like shim, rewrite L2 if required, redirect.

Target prefix and service class are ingress-only classification inputs. They are
not satellite-core LFIB keys.

## Satellite-Core LFIB

Intermediate satellite-core hops use label switching:

* key: generation, ingress ifindex, label, direction;
* value: action, output label, out ifindex, L2 rewrite metadata;
* action: swap or preserve label, redirect to next hop.

The core LFIB does not key on destination IP. A packet missing a label, using an
unknown label, or using a label for the wrong generation is a strict drop with
no Linux fallback.

## Egress And Handoff

Edge exit uses label/FEC context:

* key: generation, ingress ifindex, label, direction;
* value: pop/handoff action, UT/GW/Mini Internet rewrite metadata;
* action: pop shim, perform generated handoff or return rewrite, redirect.

If NAT or return handoff is BPF-owned, its entries must be generated from
`RuntimeUpdatePlan`, G/Z FEC context, UT/GW/zone flow context, and active
generation state. Hardcoded UTs, hardcoded target IPs, and packet-keyed
intermediate shortcuts are not valid strict-mode fixes.

## Maps

Prototype map names:

* `ingress_classifier_map`
* `core_lfib_map`
* `egress_handoff_map`
* `active_generation`

Prototype counters:

* `classifier_hit`
* `label_push`
* `core_lfib_hit`
* `label_swap`
* `egress_handoff_hit`
* `label_pop`
* `miss`
* `drop_missing_label`
* `drop_missing_generation`

The active generation must select which classifier, LFIB, and handoff entries are
eligible. Switching generation must change LFIB behavior without relying on
Linux route replacement.

## Strict Contract

Strict `bpf_owned` keeps these constraints:

* no Linux G/Z/MPLS/LFIB fallback;
* no weakened validation;
* no hardcoded UT or target-IP fixes;
* no packet-keyed `dst_ip + ifindex` entries for intermediate hops;
* qdisc remains Linux-owned;
* lifecycle remains Linux-owned;
* rollback/preflight cleanliness remains required.

## Prototype Status

`satellite/bpf_mpls_dataplane.py` is a deterministic compiler/simulator for the
new map semantics. It proves the desired key shapes before the live tc-BPF C
program and loader are changed.

`satellite/experimental/bpf/skyseed_tc_mpls_dataplane.c` is the live tc-BPF
prototype. It implements:

* IP ingress classifier lookup and MPLS unicast shim push;
* core LFIB lookup on generation, ifindex, label, and direction;
* label swap/preserve and redirect;
* egress handoff lookup and strict pop/drop behavior;
* counters for classifier, label push/swap/pop, LFIB hit, handoff hit, miss, and
  pop failure reasons.

`satellite/bpf_mpls_live.py` is the loader/smoke harness. On this kernel/iproute
path, `tc` creates anonymous map copies per attached interface, so the loader
uses map-shape discovery and fans map updates out to each attached program
instance. This keeps the live prototype strict without relying on Linux G/Z/MPLS
fallback.

The prototype tests cover:

* ingress packet to FEC/initial label;
* intermediate hop lookup by label, not destination IP;
* return/handoff by label/FEC context;
* generation switch changing LFIB behavior;
* missing label causing strict failure with no Linux fallback;
* default and override target classes sharing the same core LFIB model.

The live minimal strict reproduction passes in the bounded netns smoke. The key
kernel detail is that the MPLS shim must be inserted with `BPF_ADJ_ROOM_MAC`.
Using `BPF_ADJ_ROOM_NET` corrupts the first four bytes of the inner IPv4 header
because it does not create room between Ethernet and IPv4 for this use case.

On the current 5.4.0-54 kernel, tc-BPF still rejects negative
`bpf_skb_adjust_room()` for shim removal. The prototype therefore uses a
BPF-owned manual pop fallback for ping-sized frames: copy bytes left across the
shim with `bpf_skb_load_bytes()` / `bpf_skb_store_bytes()`, then trim the tail
with `bpf_skb_change_tail()`. This keeps label pop in BPF and does not use Linux
MPLS or Linux G/Z fallback.

The bounded live smoke proves:

* classifier hit and label push;
* intermediate core LFIB hit using generation, ifindex, label, and direction;
* label swap;
* egress handoff hit;
* label pop via BPF manual fallback;
* ping success across two satellite-core hops;
* rollback and post-run residue clean.

S4 must still not be rerun as acceptance until this live MPLS-like path is wired
into the intended strict `bpf_owned` runtime gate and the bounded runtime
preflight remains clean.
