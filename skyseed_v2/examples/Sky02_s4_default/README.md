# S4: recommended default assembly

[s4.py](s4.py) independently loads shared Shell and GroundStation files, selects S4,
then explicitly creates Base, AS, Shell, Orbits, Satellites and stations. It adapts
pure Simulator input and uses default Models. Physical and planning settings remain
those of the shared dataset and default assembly.

```bash
python examples/Sky02_s4_default/s4.py
python examples/Sky02_s4_default/s4.py --operation generate --through planner
```

The default computes 600 seconds at five-second intervals, publishes results and optional views, then
waits for explicit continuation. `DEFAULT_OPERATION` and `DEFAULT_BATCH_DURATION`, plus the duration passed
to Simulator construction, are Python configuration points in the entry; CLI options
include `--duration-seconds`, `--operation`, `--through`, `--batch-seconds`,
`--no-tools`, `--output` and `--override`. See `--help` for scenario options and
[common controls](../../docs/user-guides/computation-and-controls.md) for complete operations.

Normal output is `examples/Sky02_s4_default/output/`. Open `output/index.html`;
Compiler output also has `deploy.sh`, `run.sh` and `cleanup.sh` for explicit operation.
[Inputs and Zones](../../docs/user-guides/inputs-and-scenes.md),
[Model role navigation](../../docs/development/model-interfaces.md) and
[interactive demos](../../docs/demos/index.html) provide the next steps.

[18 September 2026 live campaign](../../docs/history/2026-09-18-closing-campaign.md#s4): Full default 600-second Docker/BPF run plus pause/resume and 30-second reset replay: main Traffic WARN; cleanup complete.
The dated record gives exact coverage, loss classifications and reference conditions.
