# S4-Scale Static Emulator Validation

> This document preserves the first scale run and its Compose 1.27.4 diagnosis.
> The subsequent formal revalidation of the generic fail-closed launcher,
> reference commit pin, per-batch timings and single-output materialization is
> recorded in
> `docs/evidence/s4-scale-batched-compose-materialization-validation.md`.
> The current supported default and final Compose v2 single-invocation result
> are recorded in
> `docs/evidence/s4-scale-compose-v2-single-materialization-validation.md`.

## 1. Scope and result

Validation date: 2026-07-17 (America/New_York)

Result:

```text
Generic static construction gates: PASS
Full-scale container materialization: PASS
Static ground-network integration: PASS
InternetMap automated audit: PASS
Dynamic satellite-network reproduction: NOT STARTED
```

The validated scenario uses S4 only as a scale and reference-input label. It
does not reproduce the dynamic v1 S4 experiment. There is no epoch, position
propagation, visibility, ISL, GS-Satellite link, Zone, Simulator, runtime
resource locator, UT, BPF, MPLS or qdisc behavior in this slice.

The reference checkout was at commit:

```text
ad331b9c15e3a5fb522af9ff18a06434f99c0860
```

The v2 core does not import that checkout. Only
`examples/reference/v1_static_input_adapter.py` understands the selected
historical JSON shapes, and that adapter is excluded from the wheel.

## 2. Environment

```text
Python:         3.10.20
pytest:         9.1.1
Docker:         19.03.8
docker-compose: 1.27.4
Filesystem:     42 GiB free before build
Memory:         16 GiB available before build, swap unused
TCP 8080:       unoccupied before startup
```

The project name and formal output were fixed before the costly run:

```text
Compose project: skyseed_v2_s4_scale_static
Formal output:   /tmp/skyseed-v2-s4-scale-static/output
```

There were no pre-existing containers, networks or project-prefixed images
for that Compose project.

## 3. Layered gates

The following gates ran before Docker build or startup:

```bash
python -m compileall -q src examples tests
pytest -q \
  tests/unit/test_static_specifications.py \
  tests/unit/test_constellation_builder.py \
  tests/unit/test_ground_station_builder.py \
  tests/integration/test_reference_v1_static_adapter.py \
  tests/integration/test_s4_scale_static_emulator.py \
  tests/unit/test_architecture_boundaries.py
pytest -q
python -m pip wheel . --no-deps --wheel-dir /tmp/skyseed-v2-wheel-audit
```

Results:

```text
compileall:     PASS
focused pytest: 30 passed in 2.54s
full pytest:    100 passed in 2.72s
wheel:          skyseed_v2-0.1.0-py3-none-any.whl, 29,074 bytes
```

The environment did not contain the optional `build` frontend, so the wheel
gate used the installed pip frontend with the same `pyproject.toml` build
backend. Inspection of the wheel showed only `skyseed_v2` package files; it
did not contain `examples/` or the reference adapter.

Additional pre-run assertions passed for:

- typed reference input loading and fail-closed validation;
- two identical reference/specification/ID snapshots from the same input;
- construction independent of input order;
- one Shell, six Orbits, 348 Satellites and 12 GroundStations;
- 360 unique Catalog entries;
- Compose YAML uniqueness and `docker-compose config --quiet`;
- all 348 Satellite services using `network_mode: none` with no declared
  Compose network;
- six GS memberships on `ix100` and six on `ix101`;
- two standard ordinary Routers, two IXes and two explicit eBGP peerings;
- no epoch, historical service `/32`, dynamic link or runtime locator in the
  static contracts or generated scenario.

## 4. Generation and Compose inventory

The formal output was generated with:

```bash
PYTHONPATH=src:. python examples/s4_scale_static_emulator.py \
  --output /tmp/skyseed-v2-s4-scale-static/output
docker-compose -p skyseed_v2_s4_scale_static \
  -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml \
  config --quiet
```

Deterministic summary:

```text
Shells:                 1
Orbits:                 6
Satellites:             348
GroundStations:         12
ordinary ASes:          2
IXes:                   2
explicit eBGP peerings: 2
Catalog nodes:          360
Compose services:       365
Compose bytes:          448,312
Compose SHA-256:         16e8ef6b5573ef42bf18f7da5dc4073ddfd79f89d288b19b3ce7ad2ee24c0a10
```

The 365 services consist of 348 Satellites, 12 GroundStations, two ordinary
Routers, two cleanly exiting SEED image-cache helpers and one InternetMap.
The Catalog intentionally includes only the 348 Satellites and 12
GroundStations.

Recorded application timings:

```text
reference input parse:  0.010152s
domain construction:    0.004056s
Catalog construction:   0.001354s
Emulator render:         0.274714s
Compiler compile:        0.429251s
whole generate command:  1.24s
peak generator RSS:      72,296 KiB
```

## 5. Build and startup

The complete image build used the formal Compose file:

```bash
docker-compose -p skyseed_v2_s4_scale_static \
  -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml build
```

Result:

```text
exit status: 0
wall time:   62.65s
failure markers in stdout/stderr: 0
```

### Compose 1.27.4 large-fanout finding

One unqualified `docker-compose up -d` attempt did not make progress beyond
50 project containers. It created 380 client threads and approximately 25
Docker daemon sockets, then emitted no new Docker events. The daemon remained
responsive and reported no error. The attempt was interrupted after 4:07.53.

`COMPOSE_PARALLEL_LIMIT=8` was then verified to be present in the client
process environment, but this Compose 1.27.4 `up` path still created about 370
threads and 25 sockets. It also remained at 50 containers and was interrupted
after 1:31.10. No unchanged retry was attempted.

The same Compose file and same complete 365-service project were then started
through Compose in explicit batches of 16 service names:

```bash
mapfile -t services < <(
  docker-compose -p skyseed_v2_s4_scale_static \
    -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml \
    config --services
)
for ((offset=0; offset<${#services[@]}; offset+=16)); do
  docker-compose -p skyseed_v2_s4_scale_static \
    -f /tmp/skyseed-v2-s4-scale-static/output/docker-compose.yml \
    up -d "${services[@]:offset:16}"
done
```

All 365 declared services were included; the scenario was not reduced and no
direct `docker run` or alternate runtime path was used. This phase completed
in 23 batches and 106 seconds. The cumulative measured Compose command time,
including the two diagnosed and interrupted client attempts, was 444.63
seconds, excluding the short read-only diagnostic intervals.

This is a Docker Compose 1.27.4 client scalability limitation in the
validation environment. It is not a v2 construction, Compiler, image,
container-capacity or runtime-topology blocker.

## 6. Full container and network audit

One bulk `docker inspect` covered every container with the Compose project
label. Assertions and results:

```text
project containers:             365
running:                        363
exited:                         2 image-cache helpers, both exit 0
containers with restart > 0:    0
unique Skyseed/Router names:    362 / 362
Satellite containers:           348 / 348 running
Satellite NetworkMode:          none for 348 / 348
Satellite project networks:     0 for 348 / 348
GroundStation containers:       12 / 12 running
GS network distribution:        ix100 x 6, ix101 x 6
ordinary Router containers:     2 / 2 running
ordinary Router network count:  2 each
InternetMap:                    running
```

Docker 19 represents a `NetworkMode=none` namespace with a built-in
`NetworkSettings.Networks["none"]` endpoint. It has no IP or gateway and is
not a Compose/project network. Every Satellite was asserted to have only that
built-in key and no project network. The InternetMap API reports the same
Satellite network mappings as empty.

Six namespace samples, one from each Orbit, were checked with `ip -o link`.
All six contained only `lo`.

The 12 GS interface sets were:

```text
6 x (lo, dummy0, ix100)
6 x (lo, dummy0, ix101)
```

`dummy0` is the standard SEED Router `/32` loopback, not a Compose network or
satellite-side interface. The two ordinary Router interface sets were:

```text
(lo, dummy0, ix100, service-net)
(lo, dummy0, ix101, service-net)
```

Forwarding was `net.ipv4.ip_forward=1` on all 12 GS and both ordinary Routers.

## 7. Routing and packet audit

Exactly two GS had an eBGP process, and both were `Established`:

```text
ground-station-06d6349a92742a2d9fce -> x_as65101 Established
ground-station-248d0c844a96df6f4728 -> x_as65102 Established
```

The other ten GS had no BGP process and no learned external test prefix. No GS
had OSPF or iBGP.

Learned routes:

```text
10.210.0.0/24 via 10.100.0.254 on ix100, BGP AS path 65101
10.220.0.0/24 via 10.101.0.254 on ix101, BGP AS path 65102
```

Packet checks from the corresponding peering GS:

```text
ping 10.210.0.254: 3 transmitted, 3 received, 0% loss
ping 10.220.0.254: 3 transmitted, 3 received, 0% loss
```

The two ordinary Routers retained their unmodified standard SEED path. Both
had established eBGP and the explicitly added ordinary-AS OSPF process was up;
no iBGP process was present. OSPF was masked for AS65000, so it did not leak
into the GS AS.

## 8. InternetMap audit

```text
GET http://127.0.0.1:8080/pro/map          -> HTTP 200
GET http://127.0.0.1:8080/api/v1/container -> HTTP 200
GET http://127.0.0.1:8080/api/v1/network   -> HTTP 200
```

API-derived graph assertions:

```text
node vertices:                 362
Satellite vertices:           348
Satellite edges:               0
GroundStation vertices:        12
GroundStation edges:           12, all ix100 or ix101
ordinary Router vertices:      2
ordinary Router edges:         4
container-to-network edges:    16
scenario network vertices:     4
```

The four graph networks were `ix100`, `ix101`, AS65101 `service-net` and
AS65102 `service-net`. There was no Satellite edge or invented satellite
network. The fifth Compose network was the project default used by build
helpers and InternetMap itself; it is not exposed as an emulated graph
network.

## 9. Stable-state resource observations

```text
running project containers:    363
cleanly exited helpers:         2
project node image tags:        362
unique project node image IDs:  15
Compose project networks:       5 (4 scenario + project default)
host memory used/available:     5.5 GiB / 14 GiB
host swap used:                 0
host filesystem free:          42 GiB
docker-stats reported memory:   1,163.844 MiB
docker-stats reported PIDs:     708
sum of sampled CPU percentages: 0.27%
failed or restarted containers: 0
```

`docker stats` returned 363 rows. Docker 19 returned zero stats for 14 Router
rows during that single snapshot, so the aggregate memory/PID values are a
lower-bound observation rather than a capacity model. This experiment is not
a performance benchmark.

## 10. Cleanup

Cleanup was restricted to the named Compose project. Because the same old
client fanout affects large service operations, the 365 resolved service names
were stopped and removed through Compose in batches of 16. Compose `down` then
removed the empty project networks, and only image tags matching the exact
`skyseed_v2_s4_scale_static_` prefix were removed.

Results before temporary-file deletion:

```text
removed project containers:    365
remaining project containers:  0
removed project networks:      5
remaining project networks:    0
removed project image tags:    362
remaining project image tags:  0
running containers on host:    0
TCP 8080 listeners:            0
```

The named InternetMap image remained at
`sha256:1df6bf96a0ef37fbb7efb340f748e20c8c2665b261033506ffcb889c9f57ef68`
and the shared Router base remained at
`sha256:f025b30be886027214a95f4b9142b24bb5cf0e3c4be80583d434a1d52e677b5a`.
Docker returned to its pre-run inventory of 5,385 images, one unrelated
stopped container and no running container. No global prune was used.

The formal `/tmp/skyseed-v2-s4-scale-static` output, the wheel audit directory
and packaging-generated local `build`/egg-info directories were then removed.
All are reproducible; the commands and measured results needed for review are
preserved in this document.

## 11. Interpretation

The run proves that current v2 generic builders can construct, catalog,
compile and materialize the requested static scale, while preserving isolated
Satellite network namespaces and integrating GS with an ordinary static
ground Internet.

It does not prove or begin dynamic S4 reproduction. No v1 class, helper,
Runner, Planner, Compiler, mutable runtime context or control flow was copied
or migrated.
