# Examples

## S4-scale static Emulator

`s4_scale_static_emulator.py` is the formal scale experiment for generic static
construction. S4 is a size reference only. The example uses the reference-only
adapter under `examples/reference/`, creates one 6-orbit/348-Satellite Shell and
12 `operator=Starlink` GroundStations through generic v2 builders, then adds two
ordinary ground ASes, two IX networks, two explicit eBGP sessions and two new
ground test prefixes. It does not import an epoch or historical service `/32`,
and it creates no Satellite link.

From `skyseed_v2/`:

```bash
PYTHONPATH=src:. python examples/s4_scale_static_emulator.py \
  --output /tmp/skyseed-v2-s4-scale-static/output --override
docker compose --parallel 16 -p skyseed_v2_s4_scale_static \
  -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml config --quiet
DOCKER_BUILDKIT=0 docker compose --parallel 16 \
  -p skyseed_v2_s4_scale_static \
  -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml build
```

`DOCKER_BUILDKIT=0` is required by the validated Docker Engine 19.03 host; its
Compose v2 default BuildKit path loses the derived images' startup command. It
is a host compatibility setting, not a second Compiler output. Re-evaluate it
after upgrading the Engine.

Materialize the complete unchanged inventory with the default single-project
Docker Compose v2 launcher and an explicit internal parallel limit:

```bash
PYTHONPATH=src:. python - <<'PY'
from pathlib import Path

from examples.s4_scale_static_emulator import materialization_expectations
from skyseed_v2.materialization import ComposeProjectLauncher

compose_file = Path(
    "/tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml"
)
result = ComposeProjectLauncher(
    compose_file=compose_file,
    project_name="skyseed_v2_s4_scale_static",
    expectations=materialization_expectations(compose_file),
    parallel_limit=16,
).materialize()
print(result)
PY
```

The default issues one full-project `docker compose up -d` and then verifies
the explicit long-running/successful-exit helper sets. It is not a smaller
scenario, fallback, or alternate compilation path. `parallel_limit=16` is the
value validated on this host, not an automatically tuned constant. Explicit
`strategy="batched"` with a configurable `batch_size` remains available only
for compatibility and diagnosis; it is never selected after a single-mode
failure.

The deterministic summary is written to `scenario-summary.json`. Runtime
acceptance additionally audits all Satellite containers, both peering GS,
ordinary Routers, eBGP/ICMP and <http://127.0.0.1:8080/pro/map>.

After collecting evidence, clean only this project and its locally built node
image tags; do not use a global Docker prune:

```bash
docker compose --parallel 16 -p skyseed_v2_s4_scale_static \
  -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml \
  down --remove-orphans
```

The shared upstream Router/Base images and the named InternetMap image must be
retained. Resolve and validate only image tags prefixed with the exact Compose
v2 project name before removing them. The experiment output under `/tmp` may
then be removed.

## Complete static Emulator

`complete_static_emulator.py` is the formal combined Emulator example. It
creates two isolated Satellites and one GroundStation in Skyseed AS65001, an
ordinary ground Router in AS65002, one real IX/eBGP relationship, an immutable
`SimulationEntityCatalog`, and an enabled runtime InternetMap.

From `skyseed_v2/`:

```bash
PYTHONPATH=src python examples/complete_static_emulator.py \
  --output /tmp/skyseed-v2-complete-static-emulator/output --override
docker compose --parallel 16 -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml config
DOCKER_BUILDKIT=0 docker compose --parallel 16 \
  -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml build
docker compose --parallel 16 -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml up -d
```

The example prints this deterministic contract summary before compilation:

```text
Catalog summary: 2 SatelliteDescriptor, 1 GroundStationDescriptor, 3 unique SimulationNodeId
- 65001/ground-station/gs-new-york
- 65001/satellite/satellite-0
- 65001/satellite/satellite-1
```

Open <http://127.0.0.1:8080/pro/map> after startup. After manual acceptance,
clean only this example's resources with:

```bash
docker compose --parallel 16 -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml \
  down --remove-orphans
docker image rm \
  skyseed_v2_complete_static-satnode_65001_satellite-0:latest \
  skyseed_v2_complete_static-satnode_65001_satellite-1:latest \
  skyseed_v2_complete_static-brdnode_65001_gs-new-york:latest \
  skyseed_v2_complete_static-brdnode_65002_ground-router:latest
```

The cleanup deliberately retains shared upstream and InternetMap images.

## Static GroundStation and ground eBGP

`static_ground_station.py` is the formal GroundStation slice example. It
creates one `SkyseedAutonomousSystem` (AS65001) containing two still-isolated
Satellites and one GroundStation Router on `ix100`, plus one ordinary SEED
Router in AS65002 with one explicit eBGP session:

```bash
python examples/static_ground_station.py --output examples/output/static_ground_station --override
```

No GS-to-Satellite interface or link is created.

## Isolated Satellites

`isolated_satellites.py` is the executable example for the first vertical
slice:

```text
1 Satellite AS
└── shell-1
    └── orbit-1
        ├── satellite-0
        └── satellite-1
```

From `skyseed_v2/`:

```bash
PYTHONPATH=src python3 examples/isolated_satellites.py \
  --output /tmp/skyseed-isolated-satellites
cd /tmp/skyseed-isolated-satellites
docker compose config
DOCKER_BUILDKIT=0 docker compose build
docker compose up -d
```

The validated Docker 19.03 host needs the shown classic-builder setting. A
current Docker Engine should use its supported builder configuration.

The two Satellite services use `network_mode: none`. The project default
network is used only by the SEED build helper and runtime InternetMap service;
neither Satellite joins it. Open <http://127.0.0.1:8080/pro/map> after startup.

Cleanup only this example's resources by using its Compose project directory:

```bash
docker compose down --remove-orphans
```
