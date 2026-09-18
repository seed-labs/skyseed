# Tiny: visible small-scene construction

A small hand-built network exposes Shell, Orbit, Satellite and GroundStation construction.
[tiny_network.py](tiny_network.py) creates the Simulator through its concise factory;
[zones.yaml](zones.yaml) is a separately authored input. Intentional settings include
5,000 km ISLs, horizon-visible GS access without a distance cap, 30° Zone visibility,
one same-plane/no cross-plane link target, one fallback GS access, unrestricted GS
candidate search and zero extra minimum-service duration. It disables periodic
parameter refresh while retaining threshold-driven delay changes. These are Tiny’s
teaching settings; the complete default assembly supplies omitted roles.

```bash
python examples/Sky00_tiny_network/tiny_network.py
python examples/Sky00_tiny_network/tiny_network.py --operation generate --through planner
```

The default computes 30 seconds at ten-second intervals, publishes results and optional views, then
waits for explicit continuation. `DEFAULT_OPERATION` and `DEFAULT_BATCH_DURATION`, plus the duration passed
to Simulator construction, are Python configuration points in the entry; CLI options
include `--duration-seconds`, `--operation`, `--through`, `--batch-seconds`,
`--no-tools`, `--output` and `--override`. See `--help` for scenario options and
[common controls](../../docs/user-guides/computation-and-controls.md) for complete operations.

Normal output is `examples/Sky00_tiny_network/output/`. Open `output/index.html`;
Compiler output also has `deploy.sh`, `run.sh` and `cleanup.sh` for explicit operation.
[Inputs and Zones](../../docs/user-guides/inputs-and-scenes.md),
[Model role navigation](../../docs/development/model-interfaces.md) and
[interactive demos](../../docs/demos/index.html) provide the next steps.

[18 September 2026 live campaign](../../docs/history/2026-09-18-closing-campaign.md#tiny): Default 30-second Docker/BPF run: Traffic PASS (148/148 replies); cleanup complete.
The dated record gives exact coverage, loss classifications and reference conditions.
