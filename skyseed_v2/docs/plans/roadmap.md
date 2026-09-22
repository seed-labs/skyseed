# Future work and status

| Status | Direction | Boundary |
|---|---|---|
| Implemented | Retained calculation, completed-range publication, independent runtime controls and joined cleanup | [Current scope](../CURRENT.md); request acceptance is separate from completed drainage |
| Implemented | Ordinary node payloads, BPF C/ELF authoring and structured/local route arrangements | [Payload guide](../user-guides/node-payloads.md), [BPF customization](../development/dataplane.md); no mandatory plugin layer |
| Implemented within recorded limits | Native IPv4 forwarding with authored protocol software and explicit route arrangements | [BIRD S4 workflow](../../examples/Sky06_s4_native_ospf/README.md); BPF S3 remains a precomputed OSPF-like Model, not a protocol daemon |
| Completed within dated scope | BPF closing campaign and native S3/S4 experiments, including manual S4 acceptance | [Evidence index](../history/README.md); does not imply lossless or all-pairs convergence |
| Next conversation | Runtime feedback and future-decision revision | Not implemented by this closeout; no new interface promised here |
| Prospective | Hybrid forwarding, IPv6/SRv6, process-restart recovery, public backend plugins | Existing native/BPF support is not a plugin API or recovery system |
| Prospective | AI assistance for authoring/explaining Models | No mandatory agent framework |
| Motivation only | Multi-machine separation/distributed computation | Research rationale, not implementation approval |

Arbitrary external material import, mid-session stage promotion, automatic rollback and
an exhaustive continuous live-observation UI are not current capabilities. Dynamic Map
support is a bounded local SEED adaptation. Research proposals remain in
[research/history](../history/paper-discussion.md) under their original source conditions.
The project is SkySEED; existing technical/API names and version `0.1.0` remain, with no
release tag or public API rename introduced for documentation cleanup.
