# Isolated Satellites

This handwritten example directly constructs the approved
`SkyseedAutonomousSystem -> Shell -> Orbit -> Satellite` hierarchy and compiles
two no-network Satellite services. It uses no YAML, Loader, scenario facade,
population helper or dynamic Simulator input.

From `skyseed_v2/`:

```bash
examples/isolated_satellites/start.sh --override
docker compose --parallel 16 \
  -p skyseed_v2_isolated_satellites \
  -f examples/isolated_satellites/output/docker-compose.yml config --quiet
```

The Compiler declares each locally built SEED base image as a Compose
`service:` additional build context. The maintained Docker BuildKit path is
therefore the normal build path; no legacy-builder environment setting is
required:

```bash
docker compose --parallel 16 \
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
