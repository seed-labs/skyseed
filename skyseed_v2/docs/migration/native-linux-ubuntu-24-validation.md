# Native Ubuntu 24.04 Migration Validation

Validation date: 2026-07-18 (Asia/Shanghai)

## Result

The Skyseed v2 static Emulator workspace was restored and accepted on native
Ubuntu 24.04. Source provenance, the pinned SEED checkout, the Python package,
all three formal static examples, a minimal runtime and a separate medium
runtime passed. Default BuildKit is the supported build path on this host.

The large 348-Satellite scene was compiled and checked with
`docker compose config`; it was deliberately not built or started. No
Simulator, dynamic-link, Runner or recovery work was started. No upstream SEED
file was modified, and no legacy Skyseed runtime or input was used.

## Source and workspace provenance

```text
workspace root:        /home/lionheart/workspace
Skyseed checkout:      /home/lionheart/workspace/skyseed
Skyseed branch:        feature/skyseed-v2
accepted source base:  cf5b49c2d5787b72979caa6c68b674b863a8c343
BuildKit repair:       6ea109d564e6f0e1f61e98ace91544a9ad17e30e
SEED checkout:         /home/lionheart/workspace/seed-emulator
SEED commit:           61acdf4c9699b25b06843ec4aad747108b05924e
seedemu version:       0.0.7
```

The Skyseed baseline commit was checked out exactly before host changes were
made. The SEED checkout is detached at the required commit, that commit is
contained by the upstream `master`, `development` and `example-new` remote
branches, and the SEED worktree remained clean. Both Python imports resolve to
the sibling editable checkouts shown above rather than unrelated installed
copies.

The documentation commit following the BuildKit repair records this validation
document and related active example documentation; identify it as the commit
containing this file. The immutable source baseline remains documented in
[`native-linux-baseline.md`](native-linux-baseline.md).

## Host audit

### Hardware and operating system

```text
OS:                 Ubuntu 24.04.4 LTS (noble)
kernel:             Linux 6.17.0-35-generic x86_64
CPU:                Intel Core i9-14900K, 32 logical CPUs
memory:             60 GiB
swap:               8 GiB
workspace storage:  3.6 TiB total, about 3.4 TiB available at acceptance
```

### Docker and networking substrate

```text
Docker Engine/client:  29.6.2
docker compose plugin: v5.3.1
Buildx:                v0.35.0
containerd.io:         2.2.6
storage driver:        overlayfs
cgroup driver:         systemd
cgroup version:        2
iptables frontend:     1.8.10 (nf_tables)
nftables:              1.0.9
ip/tc:                 iproute2 6.1.0, libbpf 1.3.0
```

The installed Compose package provides the maintained plugin-based `docker
compose` CLI. Its current release reports major version 5; this is the modern
CLI descended from the Compose v2 migration, not the legacy `docker-compose`
binary.

The host uses `cgroup2fs` and exposes `cpuset`, `cpu`, `io`, `memory`,
`hugetlb`, `pids`, `rdma`, `misc` and `dmem` controllers. Kernel BTF is present
at `/sys/kernel/btf/vmlinux`, bpffs is mounted at `/sys/fs/bpf`, and bpftool
v7.7.0 is available. `clang` and `llvm-config` are not installed. This is a
capability inventory only: no future tc/eBPF feature was implemented or tested
during this acceptance.

Docker was absent at the initial audit. Installation used Docker's official
Ubuntu `noble stable` apt source and these packages:

```text
docker-ce=5:29.6.2-1~ubuntu.24.04~noble
docker-ce-cli=5:29.6.2-1~ubuntu.24.04~noble
containerd.io=2.2.6-1~ubuntu.24.04~noble
docker-buildx-plugin=0.35.0-1~ubuntu.24.04~noble
docker-compose-plugin=5.3.1-1~ubuntu.24.04~noble
```

The daemon is active and enabled. The user was added to the `docker` group;
existing terminals need a new login session before they inherit that group.
No Docker containers or Compose projects existed before validation.

## Python environment

`python3.12-venv=3.12.3-1ubuntu0.15` was installed because Ubuntu did not yet
have the venv support needed for an isolated project environment. The project
environment is `/home/lionheart/workspace/skyseed/.venv` and has user-site
packages disabled.

```text
Python 3.12.3
pip 26.1.2
setuptools 83.0.0
wheel 0.47.0
build 1.5.0
pytest 9.1.1
PyYAML 6.0.3
geopy 2.5.0
requests 2.34.2
seedemu 0.0.7 (editable sibling checkout)
skyseed-v2 0.1.0 (editable project checkout)
```

SEED was installed editable from the pinned sibling checkout, followed by
`skyseed_v2[test]` and the wheel-build frontend. The remaining transitive
packages at acceptance were `certifi 2026.6.17`, `charset-normalizer 3.4.9`,
`geographiclib 2.1`, `idna 3.18`, `iniconfig 2.3.0`, `packaging 26.2`,
`pluggy 1.6.0`, `Pygments 2.20.0`, `pyproject_hooks 1.2.0`, `urllib3 2.7.0`.

Python 3.12 emitted invalid-escape `SyntaxWarning` messages from pinned SEED
sources on their first import. They did not cause a compile or runtime
failure, and upstream was intentionally left unchanged.

## Automated package acceptance

From `skyseed_v2/`, with the environment active:

```text
python -m compileall -q src tests examples
result: PASS, 0.05 s, 13,372 KiB maximum RSS

PYTHONPATH=. pytest -q
result: 134 passed in 9.77 s
wall:   10.01 s, 76,856 KiB maximum RSS
```

The final wheel was built with `python -m build --wheel` in a temporary
directory:

```text
artifact:     skyseed_v2-0.1.0-py3-none-any.whl
size:         44,536 bytes
entries:      29
SHA-256:      bcb07189976004b181ea18b67a68a92b9538ca4d515ab490bea22fd9e20b3eb0
build time:   3.79 s
maximum RSS:  51,344 KiB
```

The archive contains the v2 package and its distribution metadata. It contains
no examples, tests, docs, reference tools or legacy package tree. Its hash is
not expected to match the old-host baseline because this accepted source adds
the BuildKit repair and the build frontend/toolchain also differs.

## Default BuildKit compatibility repair

The first maintained-Builder build exposed one real host-compatibility gap.
Pinned SEED models locally built hashed base images as runtime `depends_on`
relationships. BuildKit resolves `FROM <local-hash>` concurrently, so without
a build-graph relationship it attempted to pull the hash from a registry.

`SkyseedDockerCompiler` now converts each affected node build to a Compose
build mapping and declares the local image with an `additional_contexts`
`service:<service-name>` reference. It validates that each Dockerfile actually
uses the declared local image and fails closed on unsupported Compose drift.
The transformation preserves the rest of SEED's raw Compose formatting and
metadata.

Integration tests cover isolated Satellites, GroundStation and ordinary Router
builds. No `DOCKER_BUILDKIT=0`, legacy `docker-compose`, upstream SEED edit,
alternate Compiler output or runtime fallback is used.

A cold default-BuildKit build of the minimal complete scene passed in 355.78
seconds. A subsequent compiler-generated build with no override passed in
41.22 seconds using the cache.

## Formal static examples

All three scenarios used their normal v2 loader, immutable specifications,
builder, Catalog and `SkyseedDockerCompiler`. `docker compose config --quiet`
passed for every generated file.

| Example | Generation | Catalog | Compose | Isolated Satellites | Build contexts | Networks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `isolated_satellites` | 1.21 s | 2 | 4 | 2 | 2 | 1 implicit default |
| `complete_static_emulator` | 0.44 s | 3 | 7 | 2 | 4 | 3 including default |
| `large_static_network` | 7.12 s | 360 | 365 | 348 | 362 | 5 including default |

The large scene retained one Shell, six Orbits, 348 Satellites, 12
GroundStations, two ordinary ASes, two IXes and two explicit eBGP peerings.
All Satellite services resolve to `network_mode: none`. Internal timing was
3.634640 seconds to render and 2.377184 seconds to compile. The generated
Compose SHA-256 was:

```text
be252d141e28b4b8962ad05fd38ea900d2357a5fc48a85c4718b63139ada27ec
```

The 365-service scene was not built or started. Its output was removed after
configuration validation.

## Minimal runtime smoke

The formal `complete_static_emulator` project built with default BuildKit and
started with one `docker compose up -d` call. Startup took 29.79 seconds. Five
long-running services were running and both image-helper services exited zero.

Runtime checks proved:

- both Satellite containers use Docker network mode `none` and contain only
  loopback;
- the GroundStation has only loopback, its explicit IX interface and `dummy0`;
- the ordinary Router has its ground and IX interfaces plus loopback/dummy;
- both directions of the explicit eBGP session are `Established` under BIRD
  2.17.2;
- the GroundStation learned `10.202.0.0/24` through AS 65002 and reached
  `10.202.0.254` with three of three ICMP replies;
- IPv4 forwarding is enabled in the Router;
- InternetMap container and map endpoints returned HTTP 200;
- InternetMap reported four entity nodes and three container/network edges:
  two Satellites had zero edges, the GroundStation had one and the ordinary
  Router had two.

Scoped cleanup removed the project's containers, networks and generated
output, and released TCP 8080.

## Medium runtime smoke

The reusable input is
[`native-linux-medium-smoke.yaml`](native-linux-medium-smoke.yaml). It is a
v2-owned static scenario containing two Orbits with six Satellites each, three
GroundStations, two ordinary ground ASes, two IXes and two explicit eBGP
peerings. It uses no alternate API or dynamic behavior.

The formal complete-example entrypoint compiled it to 15 Catalog entities and
20 Compose services: 12 Satellites, three GroundStations, two ordinary
Routers, two image helpers and InternetMap. Configuration validation found 12
`network_mode: none` Satellite services, 17 node build contexts and five
networks including the implicit default.

The default-BuildKit build passed in 401.43 seconds. One full-project `up -d`
passed in 69.12 seconds, leaving 18 long-running services and two helpers that
exited zero. Runtime checks proved:

- all 12 Satellites had no real Docker network and only loopback, with zero
  InternetMap edges;
- all three GroundStations had only their explicit IX, dummy and loopback
  interfaces;
- both ordinary Routers had only their ground, IX, dummy and loopback
  interfaces;
- both explicit GS-to-ground eBGP sessions were `Established` in both
  directions;
- GS A learned `10.211.0.0/24` through AS 65201 and reached
  `10.211.0.254` with three of three replies;
- GS B learned `10.212.0.0/24` through AS 65202 and reached
  `10.212.0.254` with three of three replies;
- both InternetMap endpoints returned HTTP 200;
- the graph contained 17 entity nodes and seven edges: 12 Satellites/zero
  edges, three GroundStations/three edges and two ordinary Routers/four edges.

The exact medium project was then taken down. Its containers, networks,
generated output and TCP 8080 listener were all absent after cleanup.

To regenerate the medium scene from `skyseed_v2/`:

```bash
python -m examples.complete_static_emulator.main \
  --scenario docs/migration/native-linux-medium-smoke.yaml \
  --output /tmp/skyseed-native-medium-smoke/output \
  --override
docker compose --parallel 16 \
  -p skyseed_v2_native_medium_smoke \
  -f /tmp/skyseed-native-medium-smoke/output/docker-compose.yml config --quiet
docker compose --parallel 16 \
  -p skyseed_v2_native_medium_smoke \
  -f /tmp/skyseed-native-medium-smoke/output/docker-compose.yml build
docker compose --parallel 16 \
  -p skyseed_v2_native_medium_smoke \
  -f /tmp/skyseed-native-medium-smoke/output/docker-compose.yml up -d
```

## Final cleanup and stop point

At acceptance there are zero Skyseed Compose projects, zero Skyseed project
containers, zero generated formal-example output directories and zero TCP
8080 listeners. Newly built images remain only as local current-host build
cache; no old-host Docker state was copied.

The Skyseed worktree is clean after the two acceptance commits, and the pinned
SEED worktree is clean. The next authorized action is review. Simulator work
remains explicitly **NOT STARTED**.
