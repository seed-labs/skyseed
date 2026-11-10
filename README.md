# SkySEED

SkySEED extends [SEED](https://seedsecuritylabs.org/emulator/) with a reusable
satellite-network **Scene** and an **Emulator** that turns it into SEED nodes.
You can combine them with ground networks and services, generate deployment files,
and start the network with Docker Compose.

This development stage also supports offline PlanningControl, saved logical plans
and PlanCompiler output for MPLS-like BPF forwarding and ordinary IP/Linux forwarding.
Runner and live execution of dynamic satellite plans remain future work.

## Start here

- [Architecture](new_docs/architecture.md): components and how they fit together.
- [Tiny network](new_examples/tiny_network/README.md): build, start and inspect a working example.
- [Offline Emu and P&C](new_examples/offline_planning/README.md) · [中文](new_examples/offline_planning/README.zh-CN.md): generate deployment files, plans and visualizations without running the network.
- [Scene](new_docs/scene.md) and [Emulator](new_docs/emulator.md): usage and key interface rules.
- [Development](new_docs/development.md): source layout, focused checks and migration.

## Installation

Use an ExtensionNode-capable SEED checkout, such as
[the proposal in SEED PR #611](https://github.com/seed-labs/seed-emulator/pull/611).
The package version `seedemu==0.0.7` alone does not establish that capability.
Place the checkout beside this repository, or set `SEED_EMULATOR_CHECKOUT`, then:

```bash
bash scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
```

If `skyseed_dev` is already configured, reuse it; reinstall this checkout only
when needed with `python -m pip install --no-deps --no-build-isolation --editable .`.
Continue with the [example's build and deployment commands](new_examples/tiny_network/README.md#build-and-start).

The active directories are temporarily named `new_src`, `new_tests`, `new_examples`
and `new_docs`; the Python package is still `skyseed`.
[Retired documentation](docs/README.md) and [research](research/README.md) remain
available separately.
