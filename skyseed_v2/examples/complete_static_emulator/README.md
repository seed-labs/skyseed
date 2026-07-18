# Complete Static Emulator

This is the minimal complete self-contained v2 scene: two isolated
Satellites, one GroundStation, one ordinary Router, one IX and one explicit
eBGP peering. `operator` is descriptive metadata only. No GS–Satellite link or
dynamic Simulator field exists.

Generate and validate from `skyseed_v2/`:

```bash
examples/complete_static_emulator/start.sh --override
docker compose --parallel 16 \
  -p skyseed_v2_complete_static_emulator \
  -f examples/complete_static_emulator/output/docker-compose.yml config --quiet
```

The validated Docker 19.03 host requires `DOCKER_BUILDKIT=0` for its classic
builder compatibility. A modern Docker host should use its supported default:

```bash
DOCKER_BUILDKIT=0 docker compose --parallel 16 \
  -p skyseed_v2_complete_static_emulator \
  -f examples/complete_static_emulator/output/docker-compose.yml build
docker compose --parallel 16 \
  -p skyseed_v2_complete_static_emulator \
  -f examples/complete_static_emulator/output/docker-compose.yml up -d
```

Cleanup is project/output scoped and idempotent:

```bash
examples/complete_static_emulator/cleanup.sh
```
