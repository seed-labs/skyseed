# Five Shells: independent planning scopes

[five_shells.py](five_shells.py) visibly constructs all five shared-data Shells and
the GroundStation inventory, then uses default assembly. Satellite routing remains
inside each Shell’s network scope; shared stations do not become transit bridges.

```bash
python examples/Sky05_five_shells/five_shells.py
python examples/Sky05_five_shells/five_shells.py --operation generate --through planner
```

The default computes the entry’s configured initial duration, publishes results and optional views, then
waits for explicit continuation. `DEFAULT_OPERATION` and `DEFAULT_BATCH_DURATION`, plus the duration passed
to Simulator construction, are Python configuration points in the entry; CLI options
include `--duration-seconds`, `--operation`, `--through`, `--batch-seconds`,
`--no-tools`, `--output` and `--override`. See `--help` for scenario options and
[common controls](../../docs/user-guides/computation-and-controls.md) for complete operations.

Normal output is `examples/Sky05_five_shells/output/`. Open `output/index.html`;
Compiler output also has `deploy.sh`, `run.sh` and `cleanup.sh` for explicit operation.
[Inputs and Zones](../../docs/user-guides/inputs-and-scenes.md),
[Model role navigation](../../docs/development/model-interfaces.md) and
[interactive demos](../../docs/demos/index.html) provide the next steps.

[Closing campaign, 18 September 2026](../../docs/history/2026-09-18-closing-campaign.md#five-shells):
the full 4,408 satellites, 355 shared GSs and 732 Zones reached all 121 ACTIVE
boundaries through 600 seconds. The campaign exposed and corrected a stale Traffic
source after a slow Docker launch; the full execution replay used unchanged materials.
Replay Traffic: WARN, 1,471/1,489 replies, coverage PASS.
Both attempts completed Runner/Traffic drainage and deployment cleanup. The dated
record retains the original failure and detailed loss/scope classifications.
