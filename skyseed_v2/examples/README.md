# Examples

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
