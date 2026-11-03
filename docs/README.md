# Find an answer

> **Historical implementation reference.** This tree describes the retired
> implementation. Current code, examples and checks are documented in
> [Scene and Emulator development](../new_docs/README.md). The old commands below
> are not active migration entry points, and their validation does not apply to
> the new implementation.

| Question | Start here |
|---|---|
| What does SkySEED own, and how does it work? | [Architecture](architecture/overview.md) |
| What does live execution need, and how large are the scenes? | [Linux host](user-guides/linux-host.md), [scale and resources](user-guides/scale-and-resources.md) |
| Which forwarding mode fits, and what differs? | [Shared pipeline and BPF/Linux navigation](architecture/overview.md#choose-a-forwarding-mode) |
| How do I run an example? | [Getting started](user-guides/getting-started.md) |
| How do I build entities, choose Zones or replace a Model? | [Inputs and scenes](user-guides/inputs-and-scenes.md), [Model authoring](user-guides/model-extension.md) |
| Which methods and data can my code use? | [Public API reference](api/README.md) |
| How do I generate, continue or control execution? | [Computation and controls](user-guides/computation-and-controls.md) |
| How do my host/container programs use Feedback and Etcd? | [Connect, query, publish and control](user-guides/computation-and-controls.md#interaction-setup-and-independent-programs), [Model-owned reading](user-guides/model-extension.md#read-user-data-in-a-model) |
| Where are my results and visualizations? | [Outputs and Tools](user-guides/outputs-and-tools.md), [interactive demos](demos/index.html) |
| What exactly is guaranteed at a boundary? | [Developer references](development/README.md) |
| How do I develop and check a change? | [Development](development/workflow.md) |
| What has been checked, and what remains? | [Current status](CURRENT.md), [future work](plans/roadmap.md) |
| Why were earlier choices made? | [Historical evidence](history/README.md) |
| How do others simulate satellite networks, and what can we study? | [Research archive](../research/README.md) |

Developer reference topics own precise current semantics. Architecture explains them, guides
exercise them, and source READMEs locate their implementations. Historical paths,
measurements and proposals describe their original conditions, not fresh validation.
The API reference explains useful public calls and values, with source links for exact signatures.

The [complete system diagram](architecture/system-architecture.svg) is a zoomable vector;
[overview](architecture/overview.md) explains its ownership, control and data notation.

Packet processing: [C/eBPF DataPlane contract](development/dataplane.md) and
[copyable template](../templates/dataplane/README.md).
