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
docker-compose -p skyseed_v2_s4_scale_static \
  -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml config --quiet
docker-compose -p skyseed_v2_s4_scale_static \
  -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml build
```

On the validated Docker Compose 1.27.4 host, one 365-service `up -d` fanout
stalls inside the old client. Materialize the complete, unchanged service
inventory with the generic bounded launcher and an explicit batch size:

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
    batch_size=16,
).materialize()
print(result)
PY
```

This is a Compose materialization optimization, not a smaller scenario,
fallback, or alternate compilation path. The launcher uses the same one
Compose file and project for every batch, verifies explicit long-running and
successful-exit helper sets, and keeps all 365 declared services in scope.
Batch size is configurable; 16 is only the value validated on this host.

The deterministic summary is written to `scenario-summary.json`. Runtime
acceptance additionally audits all Satellite containers, both peering GS,
ordinary Routers, eBGP/ICMP and <http://127.0.0.1:8080/pro/map>.

After collecting evidence, clean only this project and its locally built node
images. On the old Compose client, resolve the same service list and remove it
in bounded groups before `down`; do not use a global Docker prune:

```bash
compose_file=/tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml
project_name=skyseed_v2_s4_scale_static
mapfile -t services < <(
  docker-compose -p "$project_name" -f "$compose_file" config --services | sort
)
for ((offset=0; offset<${#services[@]}; offset+=16)); do
  docker-compose -p "$project_name" -f "$compose_file" \
    rm -f -s -v "${services[@]:offset:16}"
done
docker-compose -p "$project_name" -f "$compose_file" \
  down --remove-orphans
```

The shared upstream Router/Base images and the named InternetMap image must be
retained. The experiment output under `/tmp` may then be removed.

## Complete static Emulator

`complete_static_emulator.py` is the formal combined Emulator example. It
creates two isolated Satellites and one GroundStation in Skyseed AS65001, an
ordinary ground Router in AS65002, one real IX/eBGP relationship, an immutable
`SimulationEntityCatalog`, and an enabled runtime InternetMap.

From `skyseed_v2/`:

```bash
PYTHONPATH=src python examples/complete_static_emulator.py \
  --output /tmp/skyseed-v2-complete-static-emulator/output --override
docker-compose -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml config
docker-compose -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml build
docker-compose -p skyseed_v2_complete_static \
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
docker-compose -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml \
  down --remove-orphans
docker image rm \
  skyseed_v2_complete_static_satnode_65001_satellite-0:latest \
  skyseed_v2_complete_static_satnode_65001_satellite-1:latest \
  skyseed_v2_complete_static_brdnode_65001_gs-new-york:latest \
  skyseed_v2_complete_static_brdnode_65002_ground-router:latest
rm -rf /tmp/skyseed-v2-complete-static-emulator
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
docker-compose config
docker-compose build
docker-compose up -d
```

This host currently provides Compose v1 as `docker-compose`. On a Compose v2
host, use the equivalent `docker compose` commands.

The two Satellite services use `network_mode: none`. The project default
network is used only by the SEED build helper and runtime InternetMap service;
neither Satellite joins it. Open <http://127.0.0.1:8080/pro/map> after startup.

Cleanup only this example's resources by using its Compose project directory:

```bash
docker-compose down --remove-orphans
```
