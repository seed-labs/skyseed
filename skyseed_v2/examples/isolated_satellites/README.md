# Isolated Satellites

This self-contained v2 example loads `scenario.yaml`, constructs one approved
`SkyseedAutonomousSystem -> Shell -> Orbit -> Satellite` hierarchy, and
compiles two no-network Satellite services. It has no v1 or dynamic Simulator
input.

From `skyseed_v2/`:

```bash
examples/isolated_satellites/start.sh --override
docker compose --parallel 16 \
  -p skyseed_v2_isolated_satellites \
  -f examples/isolated_satellites/output/docker-compose.yml config --quiet
```

On the validated Docker 19.03 host, build with `DOCKER_BUILDKIT=0`. A modern
native Linux host should first use its normal supported Docker builder:

```bash
DOCKER_BUILDKIT=0 docker compose --parallel 16 \
  -p skyseed_v2_isolated_satellites \
  -f examples/isolated_satellites/output/docker-compose.yml build
docker compose --parallel 16 \
  -p skyseed_v2_isolated_satellites \
  -f examples/isolated_satellites/output/docker-compose.yml up -d
```

Cleanup is scoped to this project and output:

```bash
examples/isolated_satellites/cleanup.sh
```
