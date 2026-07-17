# Examples

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
