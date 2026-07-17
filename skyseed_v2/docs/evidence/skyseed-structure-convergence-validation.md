# Skyseed Structural Convergence Validation Evidence

## 1. Environment and scope

Validation date: 2026-07-17.

- branch before commit: `feature/skyseed-v2`;
- slice base: `c276b1d`;
- Python: 3.10.20;
- editable SEED: 0.0.7 from
  `/home/seed/seed-emulator-upstream-dev`;
- SEED commit: `61acdf4c9699b25b06843ec4aad747108b05924e`;
- Docker client/server: 19.03.8 / 19.03.8;
- Compose: docker-compose 1.27.4;
- formal output:
  `/tmp/skyseed-v2-logical-id-smoke.WeSABt/output`;
- Compose project: `skyseed_v2_logical_identity`.

The upstream SEED worktree was clean before and after validation. This run did
not implement Catalog, Simulator, UT, a GS–Satellite link or any dynamic
network behavior.

## 2. Static checks, tests and package

Commands:

```bash
python -m compileall -q src tests examples
pytest -q tests/unit/test_architecture_boundaries.py \
  tests/unit/test_simulation_node_identity.py \
  tests/unit/test_satellite_projection.py \
  tests/unit/test_ground_station.py \
  tests/integration/test_seed_satellite.py \
  tests/integration/test_ground_station_integration.py \
  tests/integration/test_compiler_output.py
pytest -q
python -m pip wheel . --no-deps --no-build-isolation \
  --wheel-dir /tmp/skyseed-v2-structure-wheels
```

Results:

```text
compileall: PASS
focused tests: 47 passed
full tests: 61 passed
wheel: skyseed_v2-0.1.0-py3-none-any.whl
wheel sha256: 5934fa2f943405447df24c7485690d89daf40a101e97bdbae1d6c2755956d917
```

The tests cover the one-AS mixed ownership path, Satellite-only and GS-only
instances, two independent ASNs, inherited standard Network/Host/Router
objects, exact Registry object identity, absence of Shell/Orbit from Registry,
stable `satnode`/`rnode` Registry identity, explicit Layer masks, Compiler
dispatch, logical `SimulationNodeId` projection, raw metadata uniqueness and
the one fail-closed private accumulator access.

## 3. Formal compile and Compose checks

Commands:

```bash
PYTHONPATH=src python examples/static_ground_station.py \
  --output /tmp/skyseed-v2-logical-id-smoke.WeSABt/output
docker-compose -p skyseed_v2_logical_identity \
  -f /tmp/skyseed-v2-logical-id-smoke.WeSABt/output/docker-compose.yml \
  config --quiet
```

The scenario compiled two Satellites and one GroundStation owned by the same
`SkyseedAutonomousSystem(65001)`. The ordinary ground Router belongs to
AS65002. Render logs showed OSPF and iBGP processing for AS65002 and no such
processing for the explicitly masked/disabled Skyseed AS.

A recursive traversal of the formal raw `docker-compose.yml` found no
duplicate YAML keys. For each Satellite service, `role`, `type`,
`skyseed.entity_type`, `skyseed.satellite_id`, `skyseed.shell_id` and
`skyseed.orbit_id` each occurred exactly once. The GS service had one
occurrence each of `role`, `type`, `skyseed.entity_type` and
`skyseed.ground_station_id`. No logical node ID or execution locator is emitted
as Compose metadata.

SEED metadata identities and separate logical IDs were:

```text
satellite-0: SEED Satellite / satnode; logical 65001/satellite/satellite-0
satellite-1: SEED Satellite / satnode; logical 65001/satellite/satellite-1
gs-new-york: SEED GroundStation / rnode; logical 65001/ground-station/gs-new-york
```

There was no Host identity or fabricated Satellite `net.*` metadata.

## 4. Build and running containers

Commands:

```bash
docker-compose -p skyseed_v2_logical_identity \
  -f /tmp/skyseed-v2-logical-id-smoke.WeSABt/output/docker-compose.yml build
docker-compose -p skyseed_v2_logical_identity \
  -f /tmp/skyseed-v2-logical-id-smoke.WeSABt/output/docker-compose.yml up -d
docker-compose -p skyseed_v2_logical_identity \
  -f /tmp/skyseed-v2-logical-id-smoke.WeSABt/output/docker-compose.yml ps
```

The two Satellite containers, GS, ordinary ground Router and InternetMap all
reported `Up`. The two expected image-cache dummy containers exited 0.

Both Satellites reported `HostConfig.NetworkMode=none`. Docker exposed only
its `none` pseudo-network in raw inspect data; neither Satellite joined the
project default, IX or ground network. `ip -o link show` returned only `lo` in
each Satellite.

The GS joined only the real IX Docker network and had exactly:

```text
lo
dummy0
ix100
```

There was no satellite-side GS interface. IPv4 forwarding was `1`. The GS
service name used SEED's border-router path while its logical Descriptor ID
remained `65001/ground-station/gs-new-york`; tests also proved the same Python
object was registered as both `rnode` and `brdnode`.

## 5. Routing and packet evidence

The generated GS BIRD configuration contained exactly one `protocol bgp`, no
`protocol ospf`, no iBGP marker/session, and this explicit peer:

```text
neighbor 10.100.0.102 as 65002;
```

Runtime protocol state:

```text
GS:            x_as65002 BGP up Established
ground Router: x_as65001 BGP up Established
```

The GS learned the intended prefix through that session:

```text
10.202.0.0/24 via 10.100.0.102 on ix100
BGP.as_path: 65002
```

Packet validation from the GS to the ordinary Router's ground interface:

```text
ping 10.202.0.254: 3 transmitted, 3 received, 0% packet loss
```

No static route or compatibility link replaced eBGP.

## 6. InternetMap evidence

Endpoints:

```text
GET http://127.0.0.1:8080/api/v1/container -> HTTP 200
GET http://127.0.0.1:8080/pro/map          -> HTTP 200
```

The API returned four emulation containers. Deriving graph input from each
container's normalized `NetworkSettings.Networks` produced:

```text
node vertices: 4
container-to-network edges: 3
Satellite vertices: 2
Satellite edges: 0
GroundStation vertices: 1
GroundStation edges: 1 (ix100)
ordinary Router edges: 2 (ix100 and ground-net)
```

Both Satellite API entries had `NetworkSettings.Networks={}` and
`meta.emulatorInfo.nets=[]`. The GS had exactly the real `ix100` network.
This is API and HTTP evidence only; no manual browser visual confirmation is
claimed.

## 7. Cleanup

Command:

```bash
docker-compose -p skyseed_v2_logical_identity \
  -f /tmp/skyseed-v2-logical-id-smoke.WeSABt/output/docker-compose.yml \
  down --remove-orphans
docker image rm skyseed_v2_logical_identity_brdnode_65002_ground-router:latest \
  skyseed_v2_logical_identity_brdnode_65001_gs-new-york:latest \
  skyseed_v2_logical_identity_satnode_65001_satellite-0:latest \
  skyseed_v2_logical_identity_satnode_65001_satellite-1:latest
```

Post-cleanup checks found zero project-labelled containers, zero project
networks, zero project-named images and no listener on port 8080. The
pre-existing shared InternetMap image tag was retained.

## 8. Remaining technical debt

- SEED Docker has no public custom-node dispatch hook. Satellite service
  insertion therefore retains exactly one fail-closed access to the private
  `_Docker__services` accumulator.
- SEED AS has no public custom Router factory/insertion hook. The unified AS
  retains a narrow GroundStation lifecycle extension without private AS-member
  access.

How Backend Planner, Execution Compiler or Runner will map a
`SimulationNodeId` to an actual SEED Node, container, namespace or kernel
resource is a future design scope, not current technical debt. No Catalog,
Simulator or execution-resource resolver is implemented.
