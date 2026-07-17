# Static GroundStation Validation Evidence

> Historical slice evidence: the scenario and names below record the state at
> the time of that validation. The current combined-AS scenario and public
> boundaries are validated in
> `skyseed-structure-convergence-validation.md`.

## 1. Environment

Validation date: 2026-07-17.

- Python: 3.10.20
- editable SEED: 0.0.7 from `/home/seed/seed-emulator-upstream-dev`
- SEED commit: `61acdf4c9699b25b06843ec4aad747108b05924e`
- Docker client/server: 19.03.8 / 19.03.8
- Compose: `docker-compose 1.27.4`; Compose v2 is unavailable
- formal output: `/tmp/skyseed-v2-ground-station-validation.q9Btlg/output`
- Compose project: `skyseed_v2_ground_station`

No upstream SEED file was modified.

## 2. Tests and package evidence

Commands:

```bash
python -m compileall -q src tests examples
pytest -q tests/unit/test_ground_station.py \
  tests/integration/test_ground_station_integration.py \
  tests/integration/test_seed_satellite.py \
  tests/integration/test_compiler_output.py
pytest -q
python -m pip wheel . --no-deps --no-build-isolation \
  --wheel-dir /tmp/skyseed-v2-gs-wheels-final
```

Results:

```text
focused tests: 19 passed
full tests: 32 passed
wheel: skyseed_v2-0.1.0-py3-none-any.whl
wheel sha256: 22eb1d45bc939d975b3e62b1a5c0926eaa225697b047b8d8decca329098cf6b0
```

`python -m build --wheel` was unavailable because the optional `build`
frontend is not installed. `pip wheel` successfully exercised the same
`pyproject.toml` PEP 517 setuptools backend.

Tests cover static validation and immutability, one-object identity, Router
inheritance, duplicate stable IDs, absence of `GroundStationNode`, Descriptor
exclusions, standard `rnode` and border-router Registry identity, one IX
interface, exact eBGP-only BIRD configuration, GroundStation metadata and the
complete isolated-Satellite regression.

## 3. Compile and configuration evidence

Commands:

```bash
PYTHONPATH=src python examples/static_ground_station.py \
  --output /tmp/skyseed-v2-ground-station-validation.q9Btlg/output
docker-compose -p skyseed_v2_ground_station \
  -f /tmp/skyseed-v2-ground-station-validation.q9Btlg/output/docker-compose.yml \
  config --quiet
```

Generated GS BIRD configuration contained:

```text
protocol bgp blocks: 1
protocol ospf blocks: 0
iBGP marker/session: absent
neighbor 10.100.0.152 as 152: present
```

The raw Compose document passed a recursive duplicate-key traversal. Within
the GS service, `role`, `type`, `skyseed.entity_type`,
and `skyseed.ground_station_id` each occur once. No Simulator logical identity
is serialized as a Compose label. The SEED metadata identity is
`GroundStation/rnode`; neither Host nor Satellite identity occurs in that
service.

## 4. Build and startup

Commands:

```bash
docker-compose -p skyseed_v2_ground_station -f docker-compose.yml build
docker-compose -p skyseed_v2_ground_station -f docker-compose.yml up -d
docker-compose -p skyseed_v2_ground_station -f docker-compose.yml ps
```

The two Satellites, GroundStation, ordinary ground Router and InternetMap all
reported `Up`. Image dummy/cache containers exited 0 as expected.

## 5. Runtime routing and packet evidence

Both BIRD peers reported:

```text
GS:            x_as152 BGP up Established
ground Router: x_as151 BGP up Established
```

The GS runtime routing table learned the ground prefix through BIRD:

```text
10.152.0.0/24 via 10.100.0.152 dev ix100 proto bird metric 32
```

Packet check from GS:

```text
ping 10.152.0.254: 3 transmitted, 3 received, 0% packet loss
```

This is packet-level reachability through the learned eBGP route, not a
static-route substitute. `net.ipv4.ip_forward` returned `1`.

GS interfaces were:

```text
lo
dummy0  10.0.0.1/32
ix100   10.100.0.151/24
```

`dummy0` is the standard Routing loopback. `ix100` is the only data-plane
interface and only configured ground interface. The GS joined no satellite
network and had no GS–Satellite link.

## 6. Satellite regression

Both Satellite containers reported:

```text
status=running
HostConfig.NetworkMode=none
role=Satellite
type=satnode
interfaces=lo
```

The Compose default support network contained only InternetMap. Neither
Satellite joined the IX, ground network or project default network.

## 7. InternetMap evidence

Endpoints:

```text
http://127.0.0.1:8080/api/v1/container
http://127.0.0.1:8080/pro/map
```

The API returned `ok=true` and four emulator nodes:

```text
satellite-0    role=Satellite    nets=[]
satellite-1    role=Satellite    nets=[]
gs-new-york    role=GroundStation nets=[ix100]
ground-router  role=BorderRouter  nets=[ground-net, ix100]
```

Graph input evidence:

```text
node vertices: 4
container-to-network edges: 3
Satellite vertices: 2
Satellite edges: 0
GroundStation vertices: 1
GroundStation edges: 1
```

The GS and ordinary Router both attach to the real IX network; no fake edge or
network was introduced. `/pro/map` returned HTTP 200. Manual browser visual
confirmation was not performed and is not claimed.

## 8. Cleanup

Commands:

```bash
docker-compose -p skyseed_v2_ground_station -f docker-compose.yml \
  down --remove-orphans
docker image rm \
  skyseed_v2_ground_station_satnode_65000_satellite-0:latest \
  skyseed_v2_ground_station_satnode_65000_satellite-1:latest \
  skyseed_v2_ground_station_brdnode_151_gs-new-york:latest \
  skyseed_v2_ground_station_brdnode_152_ground-router:latest
```

Post-cleanup project filters returned zero containers, zero networks and zero
project-prefixed images. Shared upstream images and the pre-existing named
InternetMap cache were retained.

## 9. Result and remaining debt

No functional blocker remains. Direct Router inheritance is accepted for this
static slice. The custom AS lifecycle exists because upstream has no public
custom Router factory/insertion hook; it uses no private AS fields. The older
Satellite Compiler's single private service-accumulator access remains the
documented upstream-hook debt, and GroundStation adds no new access to it.

GroundStation–Satellite links, dynamic interfaces, UT and Simulator remain
unimplemented.

## 10. Logical identity contract convergence

Satellite and GroundStation Descriptors now contain the same public,
backend-independent `SimulationNodeId` from
`src/skyseed_v2/contracts/identity.py`. The logical value contains ASN, entity
kind and entity ID; it contains no SEED Registry, node-name, container,
namespace or Compose-service locator. Joining the IX and becoming a `brdnode`
does not change the GroundStation Descriptor or logical ID.

Correction validation commands and results:

```text
python -m compileall -q src tests examples: PASS
focused pytest: 47 passed
full pytest: 61 passed
pip wheel --no-deps --no-build-isolation: PASS
wheel sha256: 5934fa2f943405447df24c7485690d89daf40a101e97bdbae1d6c2755956d917
```

Because the superseded locator had also been emitted as Compose metadata, the
combined Satellite/GroundStation/eBGP/InternetMap smoke was rebuilt and rerun.
Current runtime results are recorded in
`skyseed-structure-convergence-validation.md`.
