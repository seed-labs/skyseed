# Complete Static Emulator

This handwritten example shows the complete public composition: two isolated
Satellites, one GroundStation, one ordinary SEED AS/Network/Router, one IX, one
explicit eBGP peering, immutable Simulator static input and the Compiler.
Static input is produced explicitly with
`application.create_simulator_static_input((skyseed_as,))`; the ordinary AS is
not passed to that fail-closed boundary. `operator` is descriptive metadata
only. No YAML facade, GS–Satellite link or dynamic Simulator field exists.

Generate and validate from `skyseed_v2/`:

```bash
examples/complete_static_emulator/start.sh --override
docker compose --parallel 16 \
  -p skyseed_v2_complete_static_emulator \
  -f examples/complete_static_emulator/output/docker-compose.yml config --quiet
```

The Compiler expresses locally built SEED base images as Compose `service:`
additional build contexts, so a maintained Docker installation can use its
default BuildKit builder directly:

```bash
docker compose --parallel 16 \
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
