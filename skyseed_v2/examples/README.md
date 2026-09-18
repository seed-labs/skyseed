# Maintained examples

Each entry visibly builds an Emulator, adapts its inputs and assembles a Simulator.
By default it computes the initial range, publishes results/views and waits in the
foreground. `--operation generate` exits after that range; `--through` selects
`physical-world`, `planner` or `compiler`. Use `--help` for entry-specific controls.

| Example | Teaching purpose | Entry |
|---|---|---|
| [Sky00 Tiny](Sky00_tiny_network/README.md) | Small, visible construction and fast offline checks | `Sky00_tiny_network/tiny_network.py` |
| [Sky01 S5](Sky01_s5_custom_models/README.md) | Three independently authored physical/selection Models | `Sky01_s5_custom_models/s5.py` |
| [Sky02 S4](Sky02_s4_default/README.md) | Recommended explicit construction with default assembly | `Sky02_s4_default/s4.py` |
| [Sky03 S3](Sky03_s3_ospf/README.md) | Local OSPF computational Route Model and Areas | `Sky03_s3_ospf/s3_ospf.py` |
| [Sky04 single Shell](Sky04_single_shell_continuation/README.md) | Bounded delivery and retained continuation | `Sky04_single_shell_continuation/single_shell.py` |
| [Sky05 five Shells](Sky05_five_shells/README.md) | Independently scoped multi-Shell planning | `Sky05_five_shells/five_shells.py` |

Normal results are in each directory's `output/`, with `index.html`, a short guide,
controls and `visualizations/index.html`. Shared [operation instructions](../docs/user-guides/computation-and-controls.md)
explain continuation, separate deployment/execution and cancellation.
[Inputs](../docs/user-guides/inputs-and-scenes.md), [Models](../docs/development/model-interfaces.md)
and [demos](../docs/demos/index.html) apply across examples.
