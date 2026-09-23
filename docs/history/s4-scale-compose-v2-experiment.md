# S4-Scale Docker Compose v2 Single-Invocation Experiment

> Historical record, retained under its original dates, parameters and source revisions.
> Commands and paths below describe that work; use the current guides for new operations.
> This record does not validate phase 4b-2.

> Non-authoritative scale provenance. Counts, timings and host observations
> record one historical run; current architecture/status comes from the
> authoritative document tree.

## 1. Scope and result

Validation date: 2026-07-18 (America/New_York)

```text
Environment and Compose v2 audit:       PASS
Minimal complete-scene v2 regression:   PASS
Launcher fake-subprocess contracts:     PASS
S4-scale v2 single invocation:          PASS
Full inventory/routing/ICMP/map audit:  PASS
Bounded cleanup:                         PASS
Dynamic Simulator work:                 NOT STARTED
```

This run changes only the integration-layer materialization path. It uses the
same static scale scene as the preceding formal experiment: one Shell, six
Orbits, 348 Satellites, 12 GroundStations, two ordinary ASes, two IXes and two
explicit eBGP peerings. No Emulator entity, Descriptor/Catalog, Compiler
output, scene semantics, upstream SEED code, dynamic Runner, or Simulator was
changed.

## 2. Host and installation audit

The pre-install host facts were:

```text
OS:                         Ubuntu 20.04.1 LTS
kernel:                     Linux 5.4.0-54-generic
Docker CLI / Engine:        19.03.8 / 19.03.8
Docker client/server API:   1.40 / 1.40
Docker package:             docker.io 19.03.8-0ubuntu1.20.04.1
containerd package:         1.3.3-0ubuntu2
legacy docker-compose:      1.27.4 standalone at /usr/local/bin
Compose CLI plugin:         absent
user:                       seed, member of docker and sudo groups
daemon access:              direct and successful
```

Docker Engine and containerd came from Ubuntu packages. There was no
`download.docker.com` apt source, no installed `docker-ce`, and no apt candidate
for `docker-compose-plugin`. Installing a Docker-official apt package beside
the Ubuntu Engine would therefore have mixed package sources.

The plugin was instead installed with Docker's documented per-user manual CLI
plugin layout, using the official Compose release asset
`docker-compose-linux-x86_64` from release `v2.40.2`:

```text
path:    /home/seed/.docker/cli-plugins/docker-compose
owner:   seed:seed
mode:    0755
size:    76,584,394 bytes
SHA-256: 6c964d9655cd629ef43c5dc75d9612c2da319237debee54a7aef217e9f362b88
```

The digest matched the release's official `.sha256` asset. Docker documents
the [manual Compose plugin location and update limitation](https://docs.docker.com/compose/install/linux/#install-the-plugin-manually),
and the binary came from the official
[Docker Compose v2.40.2 release](https://github.com/docker/compose/releases/tag/v2.40.2).
No Engine, CLI, containerd, apt source, or legacy Compose package was replaced.

Post-install verification:

```text
docker compose version:  Docker Compose version v2.40.2
docker-compose version:  1.27.4
docker version:          client/server 19.03.8, API 1.40
```

The v1 executable remains available only for manual historical comparison. It
is not an automatic or supported Skyseed materialization path.

## 3. Launcher migration and contract gates

`ComposeProjectLauncher` now:

- requires `docker compose version --short` to report major version 2 or later;
- centralizes every project operation under `docker compose --parallel N -p
  <project> -f <file>`;
- defaults to one complete-project `up -d` with `parallel_limit=16`;
- performs one strict final state/inventory audit;
- retains fixed batching only through explicit `strategy=batched`;
- never retries single mode through batching or legacy `docker-compose`;
- records version, strategy, parallel limit, Compose CLI time and stable time.

Fake-subprocess tests cover v2 construction and parallel forwarding, absent or
unsupported v2, no v1 fallback, default single invocation, explicit batching,
configurable batch/tail ordering, one project/file, intermediate failure and no
later calls, helper success, abnormal long-running exit, missing/extra service,
Compose drift, and the no-Compiler/no-second-output boundary.

Pre-run engineering gates:

```text
focused launcher/architecture tests:  27 passed
full suite:                            121 passed in 10.69s
compileall:                            PASS
wheel inspection:                     PASS
wheel size:                           34,544 bytes
materialization modules in wheel:     yes
reference examples/v1 adapter:        excluded
```

After runtime evidence and documentation were finalized, the full suite again
passed **121 tests in 10.42s** and `compileall` passed. The final wheel was
34,544 bytes with SHA-256
`64608c379e0a0a5c5f119a21857b7d9a15dd9d3a3d0a8686b08f0f1c1b56c35b`;
it contained both materialization modules and excluded the reference adapter.
Packaging-only output was removed after inspection.

## 4. Minimal complete-scene regression

The existing complete static Emulator scene was generated and compiled once
to one Compose output under project `skyseed_v2_compose_v2_smoke`. Its seven
services were two Satellites, one GroundStation, one ordinary Router,
InternetMap, and two image-cache helpers.

```text
generation + compilation:   0.60s
Compose SHA-256:             34af074f... (unchanged throughout the smoke)
docker compose config:       PASS, 7 exact services
```

The first Compose v2 default build used BuildKit and completed in 24.70s, but
inspection showed every derived image had lost its final
`Config.Cmd`/`Entrypoint` against this Docker 19.03 daemon. The one subsequent
single `up` failed closed after 33.09s at InternetMap with `No command
specified`; it created no project container. No batched or v1 fallback was
attempted.

This was a real v2/old-Engine compatibility failure. The same Compose file was
built once with the Engine's classic builder selected by
`DOCKER_BUILDKIT=0`. That build completed in 81.90s. Before rerunning `up`,
inspection verified `/start.sh` on all four node images, `sh /start.sh` on
InternetMap, and `/bin/bash` on both helpers. No source, Compiler output,
service, network, label, project, or scene semantics changed.

The default launcher then executed one full-project Compose v2 invocation:

```text
parallel limit:                 16
Compose CLI return:             48.418084s
final stable confirmation:      49.252097s from up start
whole launcher process:         50.18s
long-running:                   5 / 5 running
successful-exit helpers:        2 / 2 exited zero
missing / extra / duplicate:    0 / 0 / 0
restarts:                       0
```

Both Satellites remained `network_mode:none`; the GS and Router kept their
expected interfaces and forwarding; eBGP was Established; the GS learned
`10.202.0.0/24` and completed 3/3 ICMP with zero loss. InternetMap returned
HTTP 200 for `/pro/map`, `/api/v1/container`, and `/api/v1/network`, with four
emulated nodes, two isolated Satellite vertices and the expected three ground
edges. The exact project containers, two project networks and four project
image tags were removed. Shared images were retained and no prune was used.

## 5. Fixed formal target and one build

```text
Compose project: skyseed_v2_s4_compose_v2
formal output:   /tmp/skyseed-v2-s4-compose-v2-single/output
Compose file:    /tmp/skyseed-v2-s4-compose-v2-single/output/docker-compose.yml
parallel limit:  16 (explicit)
strategy:        single (default)
```

Generation ran once and `SkyseedDockerCompiler` compiled once. There was one
`docker-compose.yml`, one project, one `docker compose config`, one formal
build, and one formal `up`. The formal path did not run v1, try batching,
change N, rebuild, reduce the scene, or generate a second output.

```text
Shells:                         1
Orbits:                         6
Satellites:                     348
GroundStations:                 12
ordinary ASes:                  2
IXes:                           2
explicit eBGP peerings:         2
Catalog nodes:                  360
Compose services:               365
long-running services:          363
successful-exit helpers:        2
Compose bytes:                  448,312
Compose SHA-256:                961def755f16a0e1fbbd8c51df18ed9e1dedc7a2d2846e1c758bdf127b16969c
```

Application timing was 0.097249s input parsing, 0.009608s domain construction,
0.003012s Catalog construction, 0.889511s render, and 1.290818s compile. The
whole generate process was 3.64s with 74,512 KiB peak RSS.

The one formal build used Compose v2.40.2, `--parallel 16`, and the already
proven `DOCKER_BUILDKIT=0` compatibility setting. It completed successfully in
7:55.28. The classic-builder output contained 3,585 `Using cache` records; this
is a cache observation, not a precise count of distinct cached images. Before
startup, all 362 project node images had `Cmd=["/start.sh"]`, InternetMap had
`Entrypoint=["sh","/start.sh"]`, and both helpers had `Cmd=["/bin/bash"]`.
The Compose digest remained unchanged.

## 6. Formal single-invocation timing

The launcher issued exactly one service-start command with no appended service
subset:

```text
docker compose --parallel 16 \
  -p skyseed_v2_s4_compose_v2 \
  -f /tmp/skyseed-v2-s4-compose-v2-single/output/docker-compose.yml \
  up -d
```

Unified timing from the start of that `up` call:

```text
Compose CLI returned:              638.434038s
Docker final stable confirmation:  640.021360s
CLI-return to final audit:           1.587322s
whole launcher process:            642.33s
launcher process peak RSS:          89,692 KiB
exit status:                        0
```

`whole launcher process` additionally includes Python startup, Compose version
and service discovery before the timed `up`, and report serialization. The
formal startup total is therefore 640.021360s on the requested up-to-stable
scope. Compose did not stall: all 365 containers were first created in the one
project, then advanced under the selected internal parallel limit to the exact
expected states.

## 7. Full inventory and namespace audit

One bulk inspection used only the exact Compose project label:

```text
project containers/services:       365 / 365 unique
missing / extra / duplicate:       0 / 0 / 0
long-running:                      363 / 363 running
successful-exit helpers:           2 / 2 exited zero
containers with restart > 0:       0
Compose version label:             2.40.2 on 365 / 365
exact Compose config-file label:   365 / 365
unique emulated node names:        362 / 362
Satellite:                         348 / 348 running
Satellite NetworkMode=none:        348 / 348
Satellite only built-in none net:  348 / 348
Satellite with IP or gateway:      0
GroundStation:                     12 / 12 running
GS IX distribution:               ix100 x 6, ix101 x 6
ordinary BorderRouter:             2 / 2 running, two networks each
InternetMap:                       1 / 1 running
```

One Satellite from each Orbit had only `lo`. Every GS had
`lo,dummy0,ix100` or `lo,dummy0,ix101`; each ordinary Router had
`lo,dummy0,ix,service-net`. IPv4 forwarding was `1` on all 12 GS and both
ordinary Routers. The five Compose project networks were the two IXes, two
ordinary-AS `service-net` networks, and the support default network. No
Satellite acquired a project network.

## 8. Routing and ICMP audit

Exactly the two selected GS had external BGP, both Established:

```text
ground-station-06d6349a92742a2d9fce -> x_as65101 Established
ground-station-248d0c844a96df6f4728 -> x_as65102 Established
```

They learned and reached:

```text
10.210.0.0/24 via 10.100.0.254 on ix100, BGP AS path 65101
  ping 10.210.0.254: 3 transmitted, 3 received, 0% loss
10.220.0.0/24 via 10.101.0.254 on ix101, BGP AS path 65102
  ping 10.220.0.254: 3 transmitted, 3 received, 0% loss
```

The other ten GS had no BGP process; 20 prefix-absence checks found neither
test prefix. No GS had OSPF or iBGP. Both ordinary Routers had their expected
OSPF process, Established eBGP to AS65000, and no iBGP.

## 9. InternetMap and stable resource audit

```text
GET /pro/map:          HTTP 200
GET /api/v1/container: HTTP 200
GET /api/v1/network:   HTTP 200

emulated node vertices:          362
Satellite vertices / edges:      348 / 0
GroundStation vertices / edges:   12 / 12
ordinary Router vertices / edges: 2 / 4
container-to-network edges:       16
scenario network vertices:         4
project-label mismatch:             0
```

The four graph networks were `ix100`, `ix101`, AS65101 `service-net` and
AS65102 `service-net`. InternetMap excluded its support default network and did
not invent a Satellite edge.

Stable resource snapshot:

```text
running project containers:       363
cleanly exited helpers:            2
project node image tags:           362
unique project node image IDs:      57
Compose project networks:            5
host memory used / available:      5,598 / 15,191 MiB
host swap used:                     0 MiB
filesystem free:                   42 GiB
docker-stats memory lower bound:   1,195.981 MiB
docker-stats rows / zero rows:     363 / 14
docker-stats PIDs:                 708
sampled CPU-percentage sum:        1.46%
Docker daemon CPU / RSS:           3.5% / 1,226,920 KiB
```

Docker 19 reported zero memory for 14 Router rows in this snapshot, so the
aggregate is a lower bound, not a capacity model. Swap remained unused during
startup and stable auditing.

## 10. Comparison with the two v1 timings

The earlier `106s` number measures only a hand-written loop of 23 Compose v1
`up -d <service subset>` calls after the large-fanout diagnosis. It does not
include the two earlier interrupted attempts, which bring cumulative Compose
command time to 444.63s, and it does not perform the launcher's cumulative
state audit after every batch.

The later `824.795012s` number is the formal Compose v1 launcher scope. Every
one of 23 batches includes a v1 `up`, digest checks, and verification of the
growing completed inventory. Its observed batches are mostly 30–40s, so
repeated CLI startup/project loading plus repeated `compose ps` and growing
`docker inspect` work dominate that contract-validation run.

The v2 result uses one project-loading/start call and one final audit. It
finishes at 640.021360s without the v1 50-container stall and without 23
launcher verification cycles. It is lower than the formal 824.795012s scope,
but higher than the narrower 106s shell-loop scope. Because the Compose
versions, prior partial state, command counts and verification scopes differ,
these numbers do not support a speedup multiplier. The demonstrated result is
reliability and a simpler default execution contract, not a universal
throughput claim.

## 11. Bounded cleanup

Cleanup used the exact Compose v2 project/file and `--parallel 16`:

```text
docker compose ... down --remove-orphans:  exit 0 in 5:00.97
removed project containers:                365
remaining project containers:                0
removed project networks:                    5
remaining project networks:                  0
removed project image tags:                362
remaining project image tags:                0
image-tag cleanup:                         11.62s
host running containers:                     0
unrelated stopped containers retained:       1
host visible image-ID baseline:           5,388 restored
TCP 8080 listeners:                           0
```

Docker 19 returned successful `down` after removing every container but left
the deleted AS65101 Router's endpoint recorded on two project-labelled
networks. Inspection proved the container no longer existed and both networks
belonged to the exact project. Disconnecting that endpoint by its exact former
container name allowed only those two networks to be removed. This was a
bounded old-daemon cleanup correction, not runtime reconciliation.

The shared Router image remained at
`sha256:f025b30be886027214a95f4b9142b24bb5cf0e3c4be80583d434a1d52e677b5a`;
the named InternetMap image remained at `sha256:3c3d9731f11b...`. No Docker
prune or unrelated container/network/image deletion was used. The reproducible
formal output and runtime logs were removed after evidence capture.

## 12. Conclusion and boundary

Docker Compose v2.40.2 single-project materialization with explicit internal
parallelism 16 is now the validated launcher default. Explicit fixed batching
remains an optional tooling mode and is not an automatic fallback. There is no
remaining static materialization blocker on the audited host, subject to its
documented `DOCKER_BUILDKIT=0` build compatibility requirement.

The run used one Compose output, one project, one formal build and one formal
`up`; it did not use legacy Compose, a reduced scene, automatic tuning,
reconciliation, dynamic satellite networking, a dynamic Runner, or Simulator.
