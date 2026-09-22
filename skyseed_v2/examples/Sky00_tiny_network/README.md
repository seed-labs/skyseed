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

The currently retained normal output opens the 21 September 3C + 3D BPF result:
`output/visualizations/traffic-generation/index.html` previews the sealed schedule;
`output/visualizations/traffic/index.html` shows the completed reset invocation
(145/145 sent/received). `output/linux-traffic/index.html` opens the separate Linux
result (144/144) without BPF telemetry. Each has source/invocation provenance in
`restoration.json`, and retains its matching inputs and raw execution records.
The pages were regenerated from existing evidence without another network run.
Generate a fresh output through the entry above before starting a new experiment.

## Node startup payload

[hello.sh](hello.sh) is enabled by default on both satellites, the ground station and
the ordinary ground router. `build_emulator()` uses their existing SEED `importFile`
and `appendStartCommand` APIs; the script is captured with the experiment inputs.
See [node payloads](../../docs/user-guides/node-payloads.md) for AS, Shell, Orbit and
individual-node selections, files, packages and foreground/background commands.

From `skyseed_v2/` with `skyseed_dev` active and the
[Linux deployment requirements](../../docs/user-guides/linux-host.md) met:

```bash
# Finite generation prepares files and exits; it does not print the greetings.
# Choose a fresh output path to preserve an earlier experiment.
python examples/Sky00_tiny_network/tiny_network.py --operation generate --output /tmp/skyseed-tiny-payload
cd /tmp/skyseed-tiny-payload
./deploy.sh
# Repeat this log query until all four greetings appear, then clean up.
docker compose -p "$(cat deployment/compose-project-name)" \
  -f deployment/docker-compose.yml logs --no-color --no-log-prefix \
  | grep '^Hello World from '
./cleanup.sh
```

Deployment builds the images and starts the containers. Their logs contain these
four lines (ordering may differ). Log delivery can lag deployment completion, so
wait for all four before cleanup:

```text
Hello World from satellite-0
Hello World from satellite-1
Hello World from gs-new-york
Hello World from ground-router
```

Each node prints once per container startup, so restarting a container prints its
line again. Runner (`./run.sh`) does not trigger the greetings and is unnecessary
for this check. The script does not change topology, precomputed routing or simulation
behavior. When using the default retained computation instead, cancel it with
`python experiment_control.py cancel-computation` and wait for the calculation
terminal to return before finishing cleanup.

[18 September 2026 live campaign](../../docs/history/2026-09-18-closing-campaign.md#tiny): Default 30-second Docker/BPF run: Traffic PASS (148/148 replies); cleanup complete.
The dated record gives exact coverage, loss classifications and reference conditions.
