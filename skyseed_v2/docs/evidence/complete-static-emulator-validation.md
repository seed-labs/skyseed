# Complete Static Emulator Validation Evidence

## 1. Environment and scope

Validation date: 2026-07-17.

- branch before commit: `feature/skyseed-v2`;
- slice base: `667be1a88c590673c38b8933eeb3e8e50c18db41`;
- Python: 3.10.20;
- editable SEED: 0.0.7 from
  `/home/seed/seed-emulator-upstream-dev`;
- SEED commit: `61acdf4c9699b25b06843ec4aad747108b05924e`;
- Docker client/server: 19.03.8 / 19.03.8;
- Compose: docker-compose 1.27.4;
- formal output: `/tmp/skyseed-v2-complete-static-emulator/output`;
- Compose project: `skyseed_v2_complete_static`.

Both the Skyseed and upstream SEED worktrees were clean at the start. No
upstream file was modified. This slice did not implement Simulator, UT,
dynamic links, Catalog persistence or execution-resource resolution.

## 2. Catalog contract evidence

The formal example printed before render/compile:

```text
Catalog summary: 2 SatelliteDescriptor, 1 GroundStationDescriptor, 3 unique SimulationNodeId
- 65001/ground-station/gs-new-york
- 65001/satellite/satellite-0
- 65001/satellite/satellite-1
```

Unit tests prove frozen Catalog state, immutable tuple collections and a
read-only logical-ID index; deterministic ordering; explicit missing,
kind-mismatch and ambiguous-string failures; and fail-closed duplicate IDs.
They also cover mixed, Satellite-only, GS-only and multiple Skyseed AS inputs,
ordinary SEED AS exclusion, Projector delegation and snapshot isolation from
later AS changes.

The Builder operates on AS-owned entities before render. Neither Catalog nor
Builder reads Registry, Compiler output or Compose, and no entity/SEED/runtime
object is present in the Catalog.

## 3. Static checks, tests and wheel

Commands:

```bash
python -m compileall -q src tests examples
pytest -q tests/unit/test_simulation_entity_catalog.py \
  tests/unit/test_catalog_builder.py \
  tests/unit/test_simulation_node_identity.py \
  tests/unit/test_architecture_boundaries.py \
  tests/integration/test_complete_static_emulator.py \
  tests/integration/test_unified_skyseed_as.py \
  tests/integration/test_seed_satellite.py \
  tests/integration/test_ground_station_integration.py \
  tests/integration/test_compiler_output.py
pytest -q
python -m pip wheel . --no-deps --no-build-isolation \
  --wheel-dir /tmp/skyseed-v2-complete-static-wheel.VLhXM7
```

Results:

```text
compileall: PASS
focused tests: 61 passed
full tests: 78 passed
wheel: skyseed_v2-0.1.0-py3-none-any.whl
wheel sha256: 30257d9d124098647c85aa32d093a5513cb5ce38cf73a511017df75fc46a1612
```

The pre-run gate found that a brand-new nested output path needed its parent
created before upstream Compiler execution. The formal CLI now creates only
the selected output parent, and a direct CLI regression test covers that
lifecycle before any container build.

## 4. Formal compile and Compose audit

Commands:

```bash
PYTHONPATH=src python examples/complete_static_emulator.py \
  --output /tmp/skyseed-v2-complete-static-emulator/output
docker-compose -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml \
  config --quiet
```

The raw Compose artifact contains two `satnode` services with
`network_mode: none`, one GroundStation service on only `net_ix_ix100`, one
ordinary ground Router, and an enabled InternetMap service. Integration tests
reject duplicate metadata keys. Neither generated output nor running labels
contain Catalog objects, logical-ID objects, a retired runtime reference or an
execution locator.

The generated GS BIRD configuration has exactly one eBGP protocol
`x_as65002`, no OSPF protocol and no iBGP session. The ordinary ground Router
retains its expected OSPF and `x_as65001` eBGP configuration.

## 5. Build and running containers

Commands:

```bash
docker-compose -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml build
docker-compose -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml up -d
```

Running services:

```text
as65001sat-satellite-0                 Up
as65001sat-satellite-1                 Up
as65001brd-New_York_Ground_Station     Up
as65002brd-ground-router               Up
seedemu_internet_map                   Up, 0.0.0.0:8080->8080/tcp
```

The two image-cache helper containers exited 0 as expected. The formal
environment remained running through user visual acceptance and was removed
after acceptance as recorded in section 9.

## 6. Satellite and GroundStation runtime checks

Both Satellites report `HostConfig.NetworkMode=none`, only interface `lo`, and
no membership in the project default, IX or ground network. Their SEED labels
remain `role=Satellite` and `type=satnode`.

GroundStation has exactly:

```text
lo
dummy0
ix100
```

It joins only the real IX network, has no satellite-side interface, reports
`net.ipv4.ip_forward=1`, and retains `role=GroundStation` / `type=rnode`.
Its runtime BIRD protocol table has one `x_as65002` BGP session in
`Established` state and no OSPF or iBGP protocol. The ordinary Router's
`x_as65001` session is also `Established`.

GroundStation learned:

```text
10.202.0.0/24 via 10.100.0.102 on ix100
BGP.as_path: 65002
```

Packet validation:

```text
ping 10.202.0.254: 3 transmitted, 3 received, 0% packet loss
```

## 7. InternetMap automated checks

Endpoints:

```text
GET http://127.0.0.1:8080/api/v1/container -> HTTP 200
GET http://127.0.0.1:8080/pro/map          -> HTTP 200
```

API-derived graph summary:

```text
nodes: 4
container-to-network edges: 3
Satellite vertices: 2
Satellite edges: 0
GroundStation vertices: 1
GroundStation edges: 1 (ix100)
ordinary Router vertices: 1
ordinary Router edges: 2 (ix100 and ground-net)
```

The API exposes the expected node names, roles and ASNs. These HTTP and
graph-data checks are automated evidence; the separate manual browser result
is recorded below.

## 8. Manual visual acceptance

```text
Automated verification: completed
Browser visual acceptance: PASS (user-confirmed 2026-07-17)
```

The following URLs were available during acceptance and are now offline after
the scoped cleanup:

- localhost: `http://127.0.0.1:8080/pro/map`
- detected host address: `http://10.0.2.15:8080/pro/map`

No firewall or host-network setting was changed. The second URL is reachable
only if the user's host/VM network permits access to that detected address.

Accepted visual check:

1. Confirm `satellite-0` and `satellite-1` are visible and each has zero edge.
2. Confirm `New York Ground Station` has one real IX edge.
3. Confirm `ground-router` connects to the IX and `ground-net`.
4. Confirm names, roles and ASNs are distinguishable, with no duplicate node,
   Host masquerade or fabricated satellite link.

## 9. Cleanup evidence

After visual acceptance, cleanup was restricted to the formal output, Compose
project and four project images. The executed operations were:

```bash
docker-compose -p skyseed_v2_complete_static \
  -f /tmp/skyseed-v2-complete-static-emulator/output/docker-compose.yml \
  down --remove-orphans
docker image rm \
  skyseed_v2_complete_static_satnode_65001_satellite-0:latest \
  skyseed_v2_complete_static_satnode_65001_satellite-1:latest \
  skyseed_v2_complete_static_brdnode_65001_gs-new-york:latest \
  skyseed_v2_complete_static_brdnode_65002_ground-router:latest
find /tmp/skyseed-v2-complete-static-emulator -depth -delete
```

Post-cleanup verification:

```text
project containers: 0
project networks: 0
project images: 0
temporary output present: no
TCP port 8080 listeners: 0
upstream SEED worktree: clean
```

Seven project containers (five services and two exited image-cache helpers),
three project networks and four project images were removed. Shared upstream
SEED images and the named InternetMap image were intentionally retained.

Final phase state:

```text
Emulator automated acceptance: PASS
Emulator manual visual acceptance: PASS
Static Emulator core excluding UT: COMPLETE
```
