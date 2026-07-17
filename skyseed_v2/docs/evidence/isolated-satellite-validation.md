# Isolated Satellite Validation Evidence

> Historical slice evidence: commands and public class names below record the
> implementation at the time of that validation. The current public AS and
> Compiler are `SkyseedAutonomousSystem` and `SkyseedDockerCompiler`; current
> structural evidence is recorded in
> `skyseed-structure-convergence-validation.md`.

## 1. Validation environment

Validation date: 2026-07-17.

- Python: `3.10.20`
- SEED: editable `seedemu 0.0.7` from
  `/home/seed/seed-emulator-upstream-dev`
- SEED commit: `61acdf4c9699b25b06843ec4aad747108b05924e`
- Docker client/server: `19.03.8` / `19.03.8`
- Compose: `docker-compose 1.27.4`
- Compose v2 command: unavailable on this host
- Runtime InternetMap base image:
  `handsonsecurity/seedemu-internetmap:2.0`

Because this host has no `docker compose` plugin, every requested Compose
operation was executed with the semantically equivalent Compose v1 command and
that environment difference is retained as evidence rather than hidden.

## 2. Domain and SEED integration tests

Command:

```bash
cd /home/seed/skyseed/skyseed_v2
pytest -q
```

Current corrected-slice result:

```text
24 passed
```

Coverage includes:

- Shell/Orbit create and query;
- batch Satellite creation;
- Shell, Orbit, Satellite ID and orbit-index uniqueness;
- fixed orbit parameter validation;
- read-only payload and frozen Descriptor;
- direct Satellite inheritance from SEED Node;
- one-object identity across Orbit, AS Registry and Compiler;
- explicit SatelliteDescriptorProjector projection from that unique object;
- Descriptor exclusion of Satellite/Node, Registry and container objects;
- dedicated `satnode` and non-Host role;
- Registry, Base render, and option lifecycle;
- formal Compiler dispatch and software grouping;
- explicit no-network output;
- raw Compose metadata uniqueness, explicit collision replacement, and no
  fabricated `net.*` labels;
- fail-closed private SEED service accumulator contract;
- generated runtime InternetMap adapter.

`python3 -m compileall -q src examples tests` also completed successfully.

## 3. Independent Compose no-network probe

Probe service:

```yaml
services:
  probe:
    image: alpine:3.20
    network_mode: none
```

Evidence:

```text
docker-compose config: PASS
container start: PASS
HostConfig.NetworkMode: none
project-labelled network count: 0
container interfaces: lo
```

The probe container and its project resources were removed after inspection.

## 4. Formal example compile and Compose validation

The actual example was compiled with:

```bash
PYTHONPATH=src python3 examples/isolated_satellites.py \
  --output /tmp/skyseed-v2-formal-run.KqG5eR/output
docker-compose -p skyseed_v2_satellite_slice config
docker-compose -p skyseed_v2_satellite_slice build
```

Results:

- exactly two `satnode` services;
- stable names `as65000sat-satellite-0` and
  `as65000sat-satellite-1`;
- both services use `network_mode: none`;
- neither service contains a `networks` field;
- no SEED Network is generated;
- Compose configuration passed;
- both Satellite images and the derived InternetMap image built successfully.

An initial compile correctly exposed upstream's invalid empty top-level
`networks: null`; the Compiler now removes that empty declaration rather than
creating a fake network.

## 5. Formal container runtime

Command:

```bash
docker-compose -p skyseed_v2_satellite_slice up -d
```

Both Satellite containers reported `Up`. Per-container inspection returned:

```text
status=running
HostConfig.NetworkMode=none
role=Satellite
interfaces=lo
```

Raw Docker 19.03 inspect represents the special no-network namespace as
`NetworkSettings.Networks["none"]`, with no address, gateway, or MAC. Neither
Satellite appears in the members of
`skyseed_v2_satellite_slice_default`; that default network contains only the
runtime InternetMap service. It exists for support services and is not a SEED
network or a Satellite dependency.

## 6. Metadata

Both running containers exposed:

```text
org.seedsecuritylabs.seedemu.meta.role=Satellite
org.seedsecuritylabs.seedemu.meta.type=satnode
org.seedsecuritylabs.seedemu.meta.skyseed.entity_type=Satellite
org.seedsecuritylabs.seedemu.meta.skyseed.satellite_id=<stable ID>
org.seedsecuritylabs.seedemu.meta.skyseed.shell_id=shell-1
org.seedsecuritylabs.seedemu.meta.skyseed.orbit_id=orbit-1
```

No Host identity or `meta.net.*` label was emitted.

## 7. Runtime InternetMap and graph data

Endpoint:

```text
http://127.0.0.1:8080/api/v1/container
```

Verified response summary:

```text
ok=True
node_count=2
satellite-0 role=Satellite nets=[] docker_networks={} state=running
satellite-1 role=Satellite nets=[] docker_networks={} state=running
derived_vertex_count=2
derived_edge_count=0
```

The vertex and edge counts use the same fields consumed by the current
InternetMap frontend: every returned container becomes a vertex, and entries
of `NetworkSettings.Networks` become edges. The generic backend adapter removes
only Docker's `none` pseudo-network representation, producing two vertices and
zero edges without creating a link or network.

The map URL returned HTTP 200 and the InternetMap HTML/application assets:

```text
http://127.0.0.1:8080/pro/map
```

Verified:

- Satellite containers running: yes;
- backend API returns two Satellite nodes: yes;
- graph input has two vertices and zero edges: yes;
- map route and frontend application served: yes;
- manual browser visual confirmation: **not performed**.

Shortest manual check: open the URL above, confirm `satellite-0` and
`satellite-1` are visible as two generic non-Router nodes and confirm that no
edge is attached to either node.

## 8. SEED extension contract changes

No file in `/home/seed/seed-emulator-upstream-dev` was modified.

All integration is versioned under `skyseed_v2`:

1. `SatelliteAutonomousSystem`: registers and configures the same Satellite
   instances owned by Orbit, with no second node collection or binding map;
2. `SatelliteBase`: `satnode` option and render participation;
3. `SatelliteDockerCompiler`: delegates Satellite recognition, software
   grouping, metadata, stable naming, and zero-interface Compose support to
   one internal Satellite compiler boundary;
4. empty top-level network normalization;
5. pinned InternetMap derived image with generic `network_mode:none` API
   normalization.

Known coupling: upstream Docker has no public additional-node dispatch hook,
so the subclass uses its private service accumulator through exactly one
fail-closed method while retaining inherited compilation. Missing or non-string
upstream state raises explicitly; there is no fallback generation path.

## 9. Cleanup

Only the formal example project is owned by this validation. Its containers,
default support network, and Compose resources are removed with:

```bash
docker-compose -p skyseed_v2_satellite_slice down --remove-orphans
```

Generated build images may remain in the local Docker cache; no unrelated
container, network, image, or volume is removed.

Cleanup was executed. Post-cleanup filters for both the formal project and the
independent probe returned no containers and no project-labelled networks.

## 10. Blockers and incomplete items

- No functional blocker remains for this slice.
- Compose v2 command syntax was not executable because only Compose v1 is
  installed; equivalent config/build/up/down operations passed.
- Manual browser visual confirmation remains intentionally unclaimed.
- GroundStation and every later slice remain unstarted.

## 11. Design-correction validation

This section supersedes the implementation-sensitive results above for the
Descriptor projection, Compiler boundary, and metadata correction. The
validation used output directory
`/tmp/skyseed-v2-design-correction.KmHyHd/output` and Compose project
`skyseed_v2_satellite_correction`.

### Cheap test evidence

```bash
python3 -m compileall -q src examples tests
pytest -q tests/unit/test_satellite_domain.py \
  tests/unit/test_satellite_projection.py \
  tests/integration/test_seed_satellite.py \
  tests/integration/test_compiler_output.py
pytest -q
python3 -m pip wheel --no-deps --no-build-isolation \
  --wheel-dir /tmp/skyseed-v2-wheel-validation .
```

Results:

```text
focused tests: 24 passed
full tests: 25 passed
wheel: skyseed_v2-0.1.0-py3-none-any.whl
wheel sha256: b3f088a0e6c603b8efb6908b7d6e7c4e72c7ea6efbcc7458abe07250d1781790
```

`python3 -m build --wheel` could not run because the environment does not
contain the optional `build` frontend. The recorded `pip wheel` command built
the same `pyproject.toml` setuptools backend without downloading dependencies
or using build isolation.

### Formal compile and raw metadata evidence

```bash
PYTHONPATH=src python3 examples/isolated_satellites.py \
  --output /tmp/skyseed-v2-design-correction.KmHyHd/output
docker-compose -p skyseed_v2_satellite_correction config --quiet
```

A pre-load traversal of the raw YAML node tree rejected duplicate mapping keys
instead of relying on `safe_load()`'s final values. Per Satellite service:

```text
role label count: 1
type label count: 1
skyseed.satellite_id label count: 1
skyseed.shell_id label count: 1
skyseed.orbit_id label count: 1
duplicate YAML keys: 0
Host metadata: 0
net.* metadata: 0
```

The integration test additionally injected conflicting `role=Host`,
`type=hnode`, and `skyseed.entity_type=Host` node labels. Keyed replacement
still emitted one `Satellite`, one `satnode`, and one namespaced entity type.

### Formal runtime evidence

```bash
docker-compose -p skyseed_v2_satellite_correction build
docker-compose -p skyseed_v2_satellite_correction up -d
```

Both Satellite containers reported:

```text
status=running
HostConfig.NetworkMode=none
interfaces=lo
role=Satellite
type=satnode
```

The project default network contained only `seedemu_internet_map`; zero
Satellite containers joined it. InternetMap returned:

```text
satellite nodes: 2
emulatorInfo.nets=[]: 2
derived vertices: 2
derived edges: 0
map route: HTTP 200
```

The first graph-check script expected the response list under `data`; the
saved response actually uses `result`. That check stopped explicitly after
the already-successful container checks. Reading the recorded response with
the correct field completed graph validation without rebuilding or restarting
the environment.

Cleanup used:

```bash
docker-compose -p skyseed_v2_satellite_correction down --remove-orphans
```

Post-cleanup project filters returned zero containers, zero networks, and zero
images carrying the correction run's Compose project prefix. Shared upstream
base images and the pre-existing named InternetMap cache were not removed.
No file in the independent upstream SEED worktree was modified, and no
GroundStation, UT, or Simulator functionality was started.

## 12. Unique-Satellite model convergence

This section supersedes all earlier implementation-sensitive object-model
results. The formal output was
`/tmp/skyseed-v2-satellite-convergence.tmsWxA/output`, and the Compose project
was `skyseed_v2_satellite_convergence`.

### Object identity and test evidence

The retired two-object path was removed rather than retained behind an alias
or fallback. Tests prove the same object identity across the complete bounded
path:

```text
Orbit.create_satellites() result
    is SatelliteAutonomousSystem.get_satellite() result
    is Registry satnode object
    is object receiving Base options/files
    is object receiving Compiler software tiers
```

Commands and results:

```bash
python3 -m compileall -q src examples tests
pytest -q tests/unit/test_satellite_domain.py \
  tests/unit/test_satellite_projection.py \
  tests/integration/test_seed_satellite.py \
  tests/integration/test_compiler_output.py
pytest -q
python3 -m pip wheel --no-deps --no-build-isolation \
  --wheel-dir /tmp/skyseed-v2-wheel-convergence.bIjHZj .
```

```text
compileall: PASS
focused tests: 22 passed
full tests: 24 passed
wheel: skyseed_v2-0.1.0-py3-none-any.whl
wheel sha256: d52dac7e167a2eace3be430218eb360faaa2c2fcfd610d8b4a3bdbabd6c355e5
```

The Descriptor projector accepts the unique Satellite and constructs its
immutable `SimulationNodeId` from ASN, the `satellite` entity kind and the
satellite ID. The resulting frozen Descriptor contains no Satellite/Node,
Registry, software, file, command, Compiler, Docker or Compose object.

### Formal compile and raw Compose evidence

```bash
PYTHONPATH=src python3 examples/isolated_satellites.py \
  --output /tmp/skyseed-v2-satellite-convergence.tmsWxA/output
docker-compose -p skyseed_v2_satellite_convergence config --quiet
```

```text
Satellite services: 2
service networks fields: 0
role labels per service: 1
type labels per service: 1
satellite_id labels per service: 1
shell_id labels per service: 1
orbit_id labels per service: 1
duplicate YAML keys: 0
Host metadata: 0
net.* metadata: 0
```

### Formal runtime evidence

```bash
docker-compose -p skyseed_v2_satellite_convergence build
docker-compose -p skyseed_v2_satellite_convergence up -d
```

```text
running Satellite containers: 2
HostConfig.NetworkMode=none: 2
interfaces=lo: 2
role=Satellite: 2
type=satnode: 2
Satellites in project default network: 0
project default network members: seedemu_internet_map
InternetMap Satellite nodes: 2
emulatorInfo.nets=[]: 2
derived vertices: 2
derived edges: 0
/pro/map: HTTP 200
```

The support-service default network is part of the existing InternetMap path;
no Satellite joins it. No fake SEED Network or link was generated.

Cleanup removed this run's containers, network and project-prefixed image
tags. Post-cleanup counts were:

```text
containers: 0
networks: 0
project-prefixed images: 0
```

The independent upstream SEED worktree remained unchanged. GroundStation, UT
and Simulator implementation remain unstarted.
