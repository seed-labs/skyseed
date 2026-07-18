# Emulator Self-Contained Migration Validation

Validation date: 2026-07-18 (America/New_York)

Baseline before this slice:

```text
5081a291ee6cc6dc791a3869ce2a7aa6b9a7c2ca
Migrate materialization to Docker Compose v2
```

This evidence covers the self-contained formal examples, the v2-owned static
scenario path, the large compile-only audit and one minimal complete runtime
regression. It does not revalidate the earlier logical node-identity contract
and does not start Simulator work.

## 1. Host and dependency baseline

```text
Python:                 3.10.20
seedemu distribution:  0.0.7
upstream SEED commit:  61acdf4c9699b25b06843ec4aad747108b05924e
Docker Engine:          19.03.8
Docker Compose:         v2.40.2
```

The independent upstream SEED checkout was clean before and after validation.
Docker 19.03.8 requires `DOCKER_BUILDKIT=0` for builds on this host. Compose
commands use Compose v2 and never fall back to the legacy `docker-compose`
binary.

## 2. Static checks, tests and wheel

Commands:

```bash
python -m compileall -q src tests examples
pytest -q
python -m pip wheel . --no-deps --no-build-isolation \
  --wheel-dir /tmp/skyseed-v2-emulator-baseline-wheels
```

Results:

```text
compileall: PASS
pytest:     133 passed in 8.62s
wheel:      skyseed_v2-0.1.0-py3-none-any.whl
SHA-256:    643a55552e777e5ea916662b33e1fca0f495509b6317623c72ec18adda6b9612
```

The wheel was installed with `python -m pip install --no-deps` before testing
the module-based example entrypoints. This is an explicit migration
prerequisite, not an example fallback.

## 3. Formal examples with v1 hidden

The complete `/home/seed/skyseed/skyseed_v1` directory was moved into a fresh
temporary directory, guarded by a shell trap, while all three formal examples
were compiled. It was restored before the command exited.

```bash
python -m examples.isolated_satellites.main \
  --output <temporary>/isolated-output --no-internet-map
python -m examples.complete_static_emulator.main \
  --output <temporary>/complete-output --no-internet-map
python -m examples.large_static_network.main \
  --output <temporary>/large-output
```

All three passed while `skyseed_v1` was absent. A production-source scan also
found none of `V1StaticInputAdapter`, `default_reference_root`,
`--reference-root`, `reference_shell_label`, or a `skyseed_v1` path in formal
examples. The remaining adapter under `examples/reference/` is offline
historical investigation/conversion tooling only.

## 4. Large static compile-only audit

The large example read only its adjacent v2 YAML files. No large Docker build
or `up` was run.

```text
Shells:                    1
Orbits:                    6
Satellites:              348
GroundStations:           12
ordinary ASes:             2
IXes:                      2
explicit eBGP peerings:    2
Catalog nodes:           360
Compose services:        365
Compose bytes:        448312
```

`docker compose config --quiet` passed. The generated Compose SHA-256 was:

```text
659f1f875a5c1c7d3e4873e58028b7776329c4f255e7b6c8f98958909b57fe31
```

The raw inventory contained 348 `Satellite`/`satnode` services with
`network_mode: none`, 12 `GroundStation`/`rnode` services, two ordinary border
routers and the three support/helper services. The only declared emulation
networks were the two ordinary service networks and two IX networks.

## 5. Minimal complete runtime regression

The only real runtime in this slice was
`examples/complete_static_emulator/`, using the fixed Compose project
`skyseed_v2_complete_static_emulator`.

```bash
examples/complete_static_emulator/start.sh --override
docker compose --parallel 16 -p skyseed_v2_complete_static_emulator \
  -f examples/complete_static_emulator/output/docker-compose.yml config --quiet
DOCKER_BUILDKIT=0 docker compose --parallel 16 \
  -p skyseed_v2_complete_static_emulator \
  -f examples/complete_static_emulator/output/docker-compose.yml build
docker compose --parallel 16 -p skyseed_v2_complete_static_emulator \
  -f examples/complete_static_emulator/output/docker-compose.yml up -d
```

Runtime results:

```text
Satellite containers running:       2
Satellite NetworkMode:              none / none
Satellite interfaces:               loopback only / loopback only
GroundStation IPv4 interfaces:      lo, dummy0, ix100
GroundStation BGP session:          Established
learned ground prefix:              10.202.0.0/24 via 10.100.0.102
GS -> 10.202.0.254 ICMP:            3/3, 0% loss
GET /api/v1/container:              HTTP 200
GET /pro/map:                       HTTP 200
InternetMap entity vertices:        4
container-to-network edges:         3
Satellite vertices / edges:         2 / 0
GroundStation vertices / edges:     1 / 1
ordinary Router vertices / edges:   1 / 2
```

No GS-Satellite network, dynamic route, fake link or Satellite interface was
introduced.

## 6. Scoped cleanup

Command:

```bash
examples/complete_static_emulator/cleanup.sh
```

Post-cleanup audit:

```text
project-labelled containers: 0
project-labelled networks:   0
TCP 8080 listeners:          0
example output directory:    absent
```

The cleanup did not use global prune and did not delete shared images.

## 7. Scope conclusion

The formal Emulator examples now have a self-contained v2 input and build
path. The large scene is validated only through load, construction, Catalog,
compile, Compose configuration and inventory audit. No 365-service build/up,
Simulator, Runner, time model, dynamic link or v1 migration was performed.
