# SkySEED

The active application models satellite networks, computes time-indexed connection
and routing Plans, and translates them into Linux/BPF execution materials for a SEED
network. See the [repository introduction](../README.md) for the short workflow and
scale reference. The project name is **SkySEED**; existing technical names such as
`skyseed_v2` and the `skyseed_dev` environment remain unchanged.

| Intent | Entry |
|---|---|
| Install and produce the first result | [Getting started](docs/user-guides/getting-started.md) |
| Understand the components and their cooperation | [Architecture overview](docs/architecture/overview.md) and [full-size diagram](docs/architecture/system-architecture.svg) |
| Learn the default assembly | [S4](examples/Sky02_s4_default/README.md) |
| Try a smaller scene and edit local algorithms | [S5 custom Models](examples/Sky01_s5_custom_models/README.md) |
| Check an installation | [Tiny](examples/Sky00_tiny_network/README.md) |
| Continue, deploy, run or stop | [Computation and controls](docs/user-guides/computation-and-controls.md) |
| Inspect retained interactive results | [Local demonstrations](docs/demos/README.md) |
| Develop or review | [Developer references](docs/development/README.md), [Model interfaces](docs/development/model-interfaces.md) |

From this directory, after [installation](docs/user-guides/getting-started.md):

```bash
conda activate skyseed_dev
python scripts/check_dev_environment.py
python examples/Sky02_s4_default/s4.py
```

The foreground computation publishes its requested range and optional views, then
retains its owners while waiting for continuation. Open
`examples/Sky02_s4_default/output/index.html`. Calculation does not deploy or execute
a network. Use generated `deploy.sh`, `run.sh`, `cleanup.sh` and
`experiment_control.py` from separate terminals as described in the
[control guide](docs/user-guides/computation-and-controls.md). `--operation generate`
explicitly selects finite generation and exit.

[Linux execution host](docs/user-guides/linux-host.md),
[scale and measured resources](docs/user-guides/scale-and-resources.md),
[all examples](examples/README.md), [current status](docs/CURRENT.md),
[documentation map](docs/README.md).
