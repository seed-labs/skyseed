# Future work and status

| Status | Direction | Boundary |
|---|---|---|
| Implemented | Retained calculation, completed-range publication, independent runtime controls and joined cleanup | [Current scope](../CURRENT.md); request acceptance is separate from completed drainage |
| Implemented | Ordinary node payloads, BPF C/ELF authoring and structured/local route arrangements | [Payload guide](../user-guides/node-payloads.md), [BPF customization](../development/dataplane.md); no mandatory plugin layer |
| Implemented within recorded limits | Native IPv4 forwarding with authored protocol software and explicit route arrangements | [BIRD S4 workflow](../../examples/Sky06_s4_native_ospf/README.md); BPF S3 remains a precomputed OSPF-like Model, not a protocol daemon |
| Completed within dated scope | BPF closing campaign and native S3/S4 experiments, including manual S4 acceptance | [Evidence index](../history/README.md); does not imply lossless or all-pairs convergence |
| Implemented and independently reviewed within dated scope | Etcd feedback, replaceable future material revision and fixed Traffic | [Feedback evidence](../history/2026-09-23-feedback-implementation.md); ordinary and failure experiments retain their stated limits |
| Independently reviewed and accepted for publication | Normal host/container Feedback usage, Etcd lifecycle guides and generated help under the canonical package name | [Usage guide](../user-guides/computation-and-controls.md#interaction-setup-and-independent-programs), [migration/closeout evidence](../history/2026-09-23-naming-migration.md); offline checks do not repeat live qualification |
| Prospective | Hybrid forwarding, IPv6/SRv6, process-restart recovery, public backend plugins | Existing native/BPF support is not a plugin API or recovery system |
| Prospective | AI assistance for authoring/explaining Models | No mandatory agent framework |
| Motivation only | Multi-machine separation/distributed computation | Research rationale, not implementation approval |

Arbitrary external material import, mid-session stage promotion, automatic rollback and
an exhaustive continuous live-observation UI are not current capabilities. Dynamic Map
support is a bounded local SEED adaptation. Research proposals remain in
[research/history](../history/paper-discussion.md) under their original source conditions.
The project, distribution and import package use SkySEED/`skyseed`; version `0.1.0`
and the `skyseed_dev` environment remain. The [migration guide](../user-guides/getting-started.md#migrating-an-existing-development-installation)
describes the installation and old-output boundary. The user accepted the final independent
review and authorized publication on 23 September 2026; dated validation limits remain.
