# Getting started

## Environment

The canonical local environment is Conda `skyseed_dev`:

```bash
scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
```

The Python package itself also works in another environment with equivalent
dependencies. Examples never create or switch environments.

## Readable examples

Static Emulator, Simulator projection, compiler, Compose launcher, and static
Tools:

```bash
python examples/complete_static_emulator/main.py
```

Mini satellite Internet reference with Topology, Zone, Dynamic Slice, and
Planner Tools:

```bash
python examples/mini_satellite_internet/main.py
```

Other focused entries:

- `examples/isolated_satellites/main.py`: SEED-isolated Satellite compilation;
- `examples/large_static_network/main.py`: larger configuration/construction
  and Compose project flow;
- `examples/reference/`: adapters/investigation helpers, not production API.

Generated example output is ignored under `examples/*/output/`.

## Architecture entry

Read `../CURRENT.md`, `../accepted-design.md`, then the single relevant
contract under `../codex-reference/`. Package navigation is graphical in
`../human-readable/`.
