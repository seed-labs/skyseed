# One Shell: delivery and continuation

The Emulator joins all GSs to the ordinary [shared business LAN](../README.md).
Only ground-interface IPv4 connectivity is added; original Model/input/timing choices
and BPF default remain. Historical measurements use their original scene.

[single_shell.py](single_shell.py) explicitly constructs one shared-data Shell and
uses the same default Simulator assembly. This entry emphasizes delivery boundaries
and continuation in the original process. `--shell` selects the authored Shell.

```bash
python examples/Sky04_single_shell_continuation/single_shell.py
python examples/Sky04_single_shell_continuation/single_shell.py --operation generate --through planner
```

The default computes the entry’s configured initial duration, publishes results and optional views, then
waits for explicit continuation. `DEFAULT_OPERATION` and `DEFAULT_BATCH_DURATION`, plus the duration passed
to Simulator construction, are Python configuration points in the entry; CLI options
include `--duration-seconds`, `--operation`, `--through`, `--batch-seconds`,
`--no-tools`, `--output` and `--override`. See `--help` for scenario options and
[common controls](../../docs/user-guides/computation-and-controls.md) for complete operations.

Normal output is `examples/Sky04_single_shell_continuation/output/`. Open `output/index.html`;
Compiler output also has `deploy.sh`, `run.sh` and `cleanup.sh` for explicit operation.
[Inputs and Zones](../../docs/user-guides/inputs-and-scenes.md),
[Model role navigation](../../docs/development/model-interfaces.md) and
[interactive demos](../../docs/demos/index.html) provide the next steps.

[18 September 2026 live campaign](../../docs/history/2026-09-18-closing-campaign.md#single-shell-continuation): default S4 scene, 300 seconds plus early and late 300-second extensions through 900.
The run verified continuous supply, a real material hold, unchanged old materials and cleanup; Traffic WARN.
