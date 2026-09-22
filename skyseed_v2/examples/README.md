# Maintained examples

Each entry visibly builds an Emulator, adapts its inputs and assembles a Simulator.
By default it computes the initial range, publishes results/views and waits in the
foreground. `--operation generate` exits after that range; `--through` selects
`physical-world`, `planner` or `compiler`. Use `--help` for entry-specific controls.

| Example | Teaching purpose | Entry |
|---|---|---|
| [Sky00 Tiny](Sky00_tiny_network/README.md) | Small, visible construction and fast offline checks | `Sky00_tiny_network/tiny_network.py` |
| [Sky01 S5](Sky01_s5_custom_models/README.md) | Three independently authored physical/selection Models | `Sky01_s5_custom_models/s5.py` |
| [Sky02 S4](Sky02_s4_default/README.md) | Recommended file-driven scene with default assembly | `Sky02_s4_default/s4.py` |
| [Sky03 S3](Sky03_s3_ospf/README.md) | Local OSPF computational Route Model and Areas | `Sky03_s3_ospf/s3_ospf.py` |
| [Sky04 single Shell](Sky04_single_shell_continuation/README.md) | Bounded delivery and retained continuation | `Sky04_single_shell_continuation/single_shell.py` |
| [Sky05 five Shells](Sky05_five_shells/README.md) | Independently scoped multi-Shell planning | `Sky05_five_shells/five_shells.py` |
| [Sky06 native S4](Sky06_s4_native_ospf/README.md) | Full 348-satellite / 355-GS S4, BIRD Area 0, explicit GS↔Zone coverage and generated Traffic | `Sky06_s4_native_ospf/s4_native_ospf.py` |

Sky00–Sky05 use the BPF default; Sky06 selects `forwarding_backend="linux"` at
Emulator construction and Controller inherits it. Sky01–Sky06 load and select
configuration records, then use `create_static_emulator` to expand the scene.
Tiny keeps its manual node/network/payload demonstration. Optional construction
fields are documented in the [input guide](../docs/user-guides/inputs-and-scenes.md).
Each entry supplies its ordinary Models through the existing Simulator assembly.
Sky06 defaults to 600 seconds at five-second cadence; its small deterministic
fixture lives only in test support. Its ordinary workload uses the default Zones,
the existing three-nearest-GS association per Zone, automatic sending and a
predeployment preview. Traffic samples bounded relative near/far pairs from that
independent coverage; it never chooses the Model's associations.
Native transit paths are unknown; Linux supplies actual sender records without BPF
packet-path telemetry.

Sky01–Sky06 visibly create one ordinary SEED `ground-lan` in the existing AS.
Stations sorted by identity receive unique host addresses from `203.0.0.0/16`
(synthetic emulation addressing), starting at `.2` after the Docker bridge gateway.
The range is sufficient for the loaded 10/355-GS inventories.
This prefix is disjoint from node identities, Zone addresses, native link pools and
SEED management/dummy-network ranges. These ordinary interface addresses share a
direct subnet; stable `sv2identity` /32s keep their satellite-network role. The LAN
adds no Planner/Traffic GS–GS edges, dummy advertisement, routing protocol or routes
past a GS. Tiny keeps its single-GS IX100 and ordinary ground AS without an extra LAN.

Older measurement records describe the original scenes before this LAN addition.
The existing S3 is still a computed route model; BIRD and a separate native S4
Area 0 example are available separately in [Sky06](Sky06_s4_native_ospf/README.md).

Normal results are in each directory's `output/`, with `index.html`, a short guide,
controls and `visualizations/index.html`. Shared [operation instructions](../docs/user-guides/computation-and-controls.md)
explain continuation, separate deployment/execution and cancellation.
[Inputs](../docs/user-guides/inputs-and-scenes.md), [Models](../docs/development/model-interfaces.md)
and [demos](../docs/demos/index.html) apply across examples.

Internet Map stays enabled by default, with `--no-internet-map` as the opt-out.
[Live Map and terminal instructions](Sky06_s4_native_ospf/README.md#observe-the-network)
apply to both backends. BPF Examples keep scheduled traffic and packet telemetry on;
`--no-traffic` and `--no-traffic-telemetry` control them independently. The
[traffic preview](../tools/generation/traffic/README.md) appears before deployment.
