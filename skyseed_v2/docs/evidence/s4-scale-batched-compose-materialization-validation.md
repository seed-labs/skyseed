# S4-Scale Bounded Compose Materialization Validation

> This document preserves the explicit Docker Compose 1.27.4 fixed-batch
> validation. Fixed batching is now an optional tooling mode. The supported
> Docker Compose v2 single-invocation default is validated in
> `docs/evidence/s4-scale-compose-v2-single-materialization-validation.md`.

## 1. Scope and result

Validation date: 2026-07-17 (America/New_York)

```text
Reference snapshot audit:             PASS
Generic launcher contract tests:      PASS
Small real-Compose launcher smoke:    PASS
S4-scale batched materialization:     PASS
Full inventory/routing/ICMP/map:      PASS
Dynamic Simulator work:               NOT STARTED
```

This revalidation uses the same formal static scene introduced by v2 commit
`a64d3a2`: one Shell, six Orbits, 348 Satellites, 12 GroundStations, two
ordinary ASes, two IXes and two explicit eBGP peerings. It validates the new
generic Compose materialization layer. It does not reproduce dynamic v1 S4,
change the Emulator domain model, or begin Simulator/Runner design.

## 2. v1 reference commit audit

The investigation cited
`06b19eb718a79a9113ee95bc045f10953c1d8ab7`; the implementation adapter had
been exercised from a worktree descended from
`ad331b9c15e3a5fb522af9ff18a06434f99c0860`.

Git audit results:

```text
06b19eb date/subject: 2026-07-13, Correct continuous horizon semantics and demo tooling
ad331b9 date/subject: 2026-07-17, Document v1 S4 static scenario facts
merge-base:           06b19eb718a79a9113ee95bc045f10953c1d8ab7
06b19eb ancestor:     yes
reverse ancestry:     no
commits after old through new: 12
```

The reason is repository topology, not a second v1 release: `skyseed_v1/` is a
directory in the same monorepo, while the intervening commits created and
validated v2. Commit `ad331b9` itself records the v1 static investigation. The
implementation worktree therefore naturally had a newer monorepo HEAD.

The three files actually read by the adapter have identical Git blob IDs at
both commits and at the validated worktree:

| Reference file under `skyseed_v1/` | Git blob ID |
| --- | --- |
| `data/orbits/planned_shell_orbit_data_s1_s5.json` | `f24c6a4c938c8a6552116459ddf54fa7405bdf89` |
| `data/ground/starlink_gateways.json` | `8ca4726919c982cfd34a26d62ced9c3ac26a9583` |
| `data/ground/s4_visualization_gateway_binding_12.json` | `b54ecec87c3f5454afab15d8d1f789f5f51fa129` |

All three last changed in `3b33c36 Add standalone Skyseed v1 architecture`;
`git diff 06b19eb ad331b9 -- <three files>` is empty. Loading either audited
snapshot yields one Shell, six Orbits, 58 Satellites per Orbit, 348 total
Satellites, 12 selected GS, altitude 560 km, and inclination 97.6 degrees.
There is no key-input drift and no mixed-version input.

The final implementation pin is `ad331b9c15e3a5fb522af9ff18a06434f99c0860`.
It is retained because it is the immutable commit that records the completed
investigation and its selected files are byte-identical to the earlier v1
reference. `examples/reference/v1_reference_snapshot.py` requires that full
commit, requires it to be an ancestor of the current HEAD, and recomputes each
worktree file's Git blob ID against the pinned blob before every load. A bad or
missing commit, non-descendant HEAD, missing file, or changed file fails closed.
The adapter then independently validates the selected JSON facts and counts.

## 3. Gates before the formal run

The validation environment was:

```text
Python:         3.10.20
pytest:         9.1.1
Docker:         19.03.8
docker-compose: 1.27.4
filesystem:     42 GiB free
memory:         16 GiB available, no swap used
TCP 8080:       free
running containers before formal run: 0
```

The fake-subprocess contract suite verifies stable sorted service order,
configurable batch size, a non-divisible tail, empty and duplicate inventories,
unknown/unclassified services, middle-batch stop/no later execution, the same
project/file for every command, helper exit-zero acceptance, abnormal
long-running exit rejection, final missing/extra project services, Compose
digest drift, and absence of Compiler calls or another Compose output.

Gate results before the formal scale run:

```text
compileall:                         PASS
focused launcher/reference tests:  31 passed in 9.43s
full suite:                         117 passed in 9.49s
wheel build/inspection:             PASS, materialization package included,
                                    reference examples excluded
```

A real seven-service smoke then compiled one Compose file and materialized it
with batch size 2. Four batches completed in 20.102148 seconds; five services
were running and two helpers had exited zero. Its project containers, networks,
images and output were removed by exact scope before the formal run.

After runtime evidence and documentation were finalized, the complete suite
again passed **117 tests in 9.54s**. The final wheel was 33,858 bytes, contained
both `skyseed_v2.materialization` modules, and still excluded all reference
examples and v1 adapter code.

## 4. Fixed formal target and single compilation

```text
Compose project: skyseed_v2_s4_batched
Formal output:   /tmp/skyseed-v2-s4-batched-materialization/output
Compose file:    /tmp/skyseed-v2-s4-batched-materialization/output/docker-compose.yml
Batch size:      16 (explicit)
```

The static example generated once and `SkyseedDockerCompiler` compiled once.
`docker-compose config --quiet` passed before build. There was exactly one
`docker-compose.yml` throughout generation, build, all batches, final audit,
and cleanup.

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
long-running services:  363
successful-exit helpers: 2
Compose bytes:          448,312
Compose SHA-256:         961def755f16a0e1fbbd8c51df18ed9e1dedc7a2d2846e1c758bdf127b16969c
```

Application timing was 0.092135s input parse, 0.009482s domain construction,
0.002954s Catalog construction, 0.783340s render, and 1.135971s compile. The
whole generation process took 3.52s with 74,408 KiB peak RSS. The exact Compose
project build exited zero in 185.19s with no failure marker; the Compose digest
remained unchanged.

## 5. Formal launcher run

The formal path invoked `ComposeProjectLauncher` directly. It did not first
attempt a complete-project one-shot `up`; consequently there was no fallback,
batch-size switch, per-service retry, alternate project, or second Compose
file. All batches used project `skyseed_v2_s4_batched` and the exact file/hash
listed above.

| Batch | Services | Seconds | First service | Last service | Result |
| ---: | ---: | ---: | --- | --- | --- |
| 1 | 16 | 40.428491 | `072d7840db6a44d06e5c5fd4d7ea8a7d` | `brdnode_65102_ground-b-router` | PASS |
| 2 | 16 | 30.512697 | `satnode_65000_scale-shell-orbit-001-satellite-0001` | `satnode_65000_scale-shell-orbit-001-satellite-0016` | PASS |
| 3 | 16 | 33.448711 | `satnode_65000_scale-shell-orbit-001-satellite-0017` | `satnode_65000_scale-shell-orbit-001-satellite-0032` | PASS |
| 4 | 16 | 38.733447 | `satnode_65000_scale-shell-orbit-001-satellite-0033` | `satnode_65000_scale-shell-orbit-001-satellite-0048` | PASS |
| 5 | 16 | 36.886928 | `satnode_65000_scale-shell-orbit-001-satellite-0049` | `satnode_65000_scale-shell-orbit-002-satellite-0006` | PASS |
| 6 | 16 | 35.046181 | `satnode_65000_scale-shell-orbit-002-satellite-0007` | `satnode_65000_scale-shell-orbit-002-satellite-0022` | PASS |
| 7 | 16 | 37.485178 | `satnode_65000_scale-shell-orbit-002-satellite-0023` | `satnode_65000_scale-shell-orbit-002-satellite-0038` | PASS |
| 8 | 16 | 37.763003 | `satnode_65000_scale-shell-orbit-002-satellite-0039` | `satnode_65000_scale-shell-orbit-002-satellite-0054` | PASS |
| 9 | 16 | 32.098623 | `satnode_65000_scale-shell-orbit-002-satellite-0055` | `satnode_65000_scale-shell-orbit-003-satellite-0012` | PASS |
| 10 | 16 | 34.174512 | `satnode_65000_scale-shell-orbit-003-satellite-0013` | `satnode_65000_scale-shell-orbit-003-satellite-0028` | PASS |
| 11 | 16 | 34.490175 | `satnode_65000_scale-shell-orbit-003-satellite-0029` | `satnode_65000_scale-shell-orbit-003-satellite-0044` | PASS |
| 12 | 16 | 37.205711 | `satnode_65000_scale-shell-orbit-003-satellite-0045` | `satnode_65000_scale-shell-orbit-004-satellite-0002` | PASS |
| 13 | 16 | 33.126757 | `satnode_65000_scale-shell-orbit-004-satellite-0003` | `satnode_65000_scale-shell-orbit-004-satellite-0018` | PASS |
| 14 | 16 | 32.551702 | `satnode_65000_scale-shell-orbit-004-satellite-0019` | `satnode_65000_scale-shell-orbit-004-satellite-0034` | PASS |
| 15 | 16 | 35.304909 | `satnode_65000_scale-shell-orbit-004-satellite-0035` | `satnode_65000_scale-shell-orbit-004-satellite-0050` | PASS |
| 16 | 16 | 35.503567 | `satnode_65000_scale-shell-orbit-004-satellite-0051` | `satnode_65000_scale-shell-orbit-005-satellite-0008` | PASS |
| 17 | 16 | 34.539343 | `satnode_65000_scale-shell-orbit-005-satellite-0009` | `satnode_65000_scale-shell-orbit-005-satellite-0024` | PASS |
| 18 | 16 | 34.802322 | `satnode_65000_scale-shell-orbit-005-satellite-0025` | `satnode_65000_scale-shell-orbit-005-satellite-0040` | PASS |
| 19 | 16 | 39.132575 | `satnode_65000_scale-shell-orbit-005-satellite-0041` | `satnode_65000_scale-shell-orbit-005-satellite-0056` | PASS |
| 20 | 16 | 37.094364 | `satnode_65000_scale-shell-orbit-005-satellite-0057` | `satnode_65000_scale-shell-orbit-006-satellite-0014` | PASS |
| 21 | 16 | 37.931201 | `satnode_65000_scale-shell-orbit-006-satellite-0015` | `satnode_65000_scale-shell-orbit-006-satellite-0030` | PASS |
| 22 | 16 | 37.585525 | `satnode_65000_scale-shell-orbit-006-satellite-0031` | `satnode_65000_scale-shell-orbit-006-satellite-0046` | PASS |
| 23 | 13 | 34.865884 | `satnode_65000_scale-shell-orbit-006-satellite-0047` | `seedemu_internet_map` | PASS |

Launcher-measured total was **824.795012s**. Whole launcher process wall time,
including Python startup and report serialization, was **13:48.46**, with
81,004 KiB peak RSS and exit status zero. The long time reflects sequential
Compose 1.27.4 command and state-verification overhead; this run is contract
validation, not a throughput benchmark or evidence that batch size 16 is
optimal.

## 6. Full inventory and namespace audit

One bulk inspection covered every container with the exact project label:

```text
project containers/services:       365 / 365 unique
missing / extra / duplicate:       0 / 0 / 0
long-running:                      363 / 363 running
successful-exit helpers:           2 / 2 exited zero
containers with restart > 0:       0
unique emulated node names:        362 / 362
Satellite:                         348 / 348 running
Satellite NetworkMode=none:        348 / 348
Satellite only built-in none net:  348 / 348
GroundStation:                     12 / 12 running
GS project networks:               ix100 x 6, ix101 x 6
ordinary BorderRouter:             2 / 2 running, two networks each
InternetMap:                       running
exact Compose config-file label:   365 / 365
```

One Satellite from each of six Orbits had only `lo`. Every GS had
`lo,dummy0,ix100` or `lo,dummy0,ix101`; each ordinary Router had
`lo,dummy0,ix,service-net`. IPv4 forwarding was `1` on all 12 GS and both
ordinary Routers. No Satellite acquired a project or invented network.

## 7. Routing and ICMP audit

Exactly the two selected GS had external BGP, both Established:

```text
ground-station-06d6349a92742a2d9fce -> x_as65101 Established
ground-station-248d0c844a96df6f4728 -> x_as65102 Established
```

They learned:

```text
10.210.0.0/24 via 10.100.0.254 on ix100, BGP AS path 65101
10.220.0.0/24 via 10.101.0.254 on ix101, BGP AS path 65102
```

`ping -c 3` to `10.210.0.254` and `10.220.0.254` returned three of three
packets with 0% loss. The remaining ten GS exposed no external BGP protocol;
20 route-absence checks confirmed neither test prefix was present. No GS had
OSPF or iBGP. Both ordinary Routers had their standard OSPF process and
Established eBGP to AS65000, with no iBGP.

## 8. InternetMap audit

```text
GET /pro/map          -> HTTP 200
GET /api/v1/container -> HTTP 200
GET /api/v1/network   -> HTTP 200
```

```text
emulated node vertices:        362
Satellite vertices / edges:    348 / 0
GroundStation vertices / edges: 12 / 12
ordinary Router vertices/edges: 2 / 4
container-to-network edges:     16
scenario network vertices:      4
```

The four graph networks are `ix100`, `ix101`, and the two independently owned
`service-net` networks. All 362 graph nodes carry the exact formal project
label. InternetMap therefore confirms the complete static entity inventory
without inventing a satellite edge.

## 9. Stable resource observation

```text
running project containers:       363
cleanly exited helpers:            2
project node image tags:           362
unique project image IDs:          15
Compose project networks:          5
host memory used / available:      5.3 GiB / 14 GiB
host swap used:                    0
filesystem free:                   42 GiB
docker-stats memory lower bound:   1,198.116 MiB
docker-stats PIDs:                 708
sampled CPU-percentage sum:        2.61%
failed or restarted containers:    0
```

Docker 19 returned zero memory for 14 Router rows in this single 363-row
snapshot, so the aggregate is a lower-bound observation, not a capacity model.

## 10. Bounded cleanup

Cleanup is restricted to the exact project, its five project-labelled
networks, its 362 project-prefixed image tags, and the reproducible formal
output. Services are stopped/removed through the same Compose project/file in
bounded groups before `down`; no global prune or unrelated-container removal is
allowed.

Cleanup completed in the same 23 service groups. Each group used the exact
project/file and stopped then removed only its reported services; all groups
passed. Project `down` ran only after those containers were gone, and exact
project image tags were validated before deletion in eight bounded groups.

```text
removed project containers:      365
remaining project containers:    0
removed project networks:        5
remaining project networks:      0
removed project image tags:      362
remaining project image tags:    0
bounded service cleanup time:     492.702s
host running containers:          0
host unrelated stopped containers: 1 (retained)
host unique image IDs:            5,385 (pre-run baseline restored)
TCP 8080 listeners:               0
```

The shared Router image remained at
`sha256:f025b30be886027214a95f4b9142b24bb5cf0e3c4be80583d434a1d52e677b5a`;
the named InternetMap image was also retained. No Docker prune was used. The
formal `/tmp/skyseed-v2-s4-batched-materialization` output and packaging-only
temporary artifacts were removed after evidence capture.

## 11. Interpretation

The run validates batching as a Compose materialization optimization. It used
the complete 365-service inventory from the only generated Compose output; it
was neither a reduced scene nor a fallback after an attempted one-shot launch.
The evidence does not establish 12 or 16 as universally optimal and introduces
no automatic tuning, retry framework, Simulator, dynamic Runner, topology, or
satellite-network execution semantics.
