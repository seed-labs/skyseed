# Native Linux Emulator Migration Baseline

> Historical record, retained under its original dates, parameters and source revisions.
> Commands and paths below describe that work; use the current guides for new operations.
> This record does not validate phase 4b-2.

Historical path note (Phase 4b-1): `skyseed_v1/` and
`skyseed_v2/examples/reference/` were removed from the working tree. References
below describe Git paths at revision `375501cfba5ee385486105f382324729914dc3b1`
(or an explicitly recorded older revision), not current runnable locations.

> Non-authoritative environment provenance. Version and path statements are
> historical reproduction facts, not current architecture.

This baseline identifies the self-contained Skyseed v2 static Emulator state
that is ready to move to another native Linux host.

## Source baseline

```text
branch: feature/skyseed-v2
baseline parent: 5081a291ee6cc6dc791a3869ce2a7aa6b9a7c2ca
migration commit subject: Make Emulator examples self-contained and migration-ready
```

The exact immutable migration commit is the commit containing this document
with the subject above. Record and verify it with `git rev-parse HEAD` after
checkout; the completion report and backup remote ref record its literal SHA.
This avoids placing a self-invalidating commit hash inside the commit itself.

Required source dependency:

```text
upstream SEED commit: 61acdf4c9699b25b06843ec4aad747108b05924e
seedemu distribution version: 0.0.7
```

The formal examples and project wheel do not depend on `skyseed_v1`. The
offline adapter under `examples/reference/` is not part of the formal runtime
or package path.

## Verified host

```text
Python:         3.10.20
Docker Engine:  19.03.8
Docker Compose: v2.40.2
```

These are verified versions, not a recommendation to install an obsolete
Docker Engine. Docker 19.03 on the old host requires
`DOCKER_BUILDKIT=0` for image builds. A new host should use a maintained Docker
Engine and Compose v2, first trying its supported default BuildKit path. Do
not make the old compatibility setting a permanent cross-host default.

## Installation

Use a dedicated Python environment. From the pinned upstream SEED checkout
and this repository checkout:

```bash
python -m pip install -e <seed-emulator-checkout>
cd <skyseed-checkout>/skyseed_v2
python -m pip install -e '.[test]'
```

For an immutable artifact check, build and install the wheel instead:

```bash
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir <wheel-dir>
python -m pip install --no-deps <wheel-dir>/skyseed_v2-0.1.0-py3-none-any.whl
```

Validated wheel SHA-256:

```text
643a55552e777e5ea916662b33e1fca0f495509b6317623c72ec18adda6b9612
```

## Baseline acceptance

The final suite result was:

```text
133 passed in 8.62s
```

The formal examples compiled while the complete `skyseed_v1` directory was
temporarily absent. The large local-v2 scene produced 360 Catalog nodes and
365 Compose services; `docker compose config --quiet` passed and the Compose
SHA-256 was:

```text
659f1f875a5c1c7d3e4873e58028b7776329c4f255e7b6c8f98958909b57fe31
```

The large scene was not built or started. The minimal complete example was
built and started instead. It proved two no-network/loopback-only Satellites,
one ground-only GS, established eBGP, learned-prefix ICMP reachability and an
InternetMap graph of four entity vertices, three container-to-network edges
and zero Satellite edges. Scoped cleanup left zero project containers,
networks, output and TCP 8080 listeners. Full evidence is in
The deleted implementation-era evidence formerly named
`emulator-self-contained-migration-validation.md`.

## New-host acceptance order

1. Clone or pull the backup branch and check out the exact migration commit.
2. Check out and install the pinned upstream SEED commit.
3. Install project/test dependencies in a dedicated Python environment.
4. Install a maintained Docker Engine and Compose v2; verify daemon access.
5. Run `python -m compileall -q src tests examples` and `pytest -q`.
6. Build the wheel and compare its hash for the same source/toolchain, while
   treating a different build-tool version as an explicit reproducibility
   variable rather than silently accepting it.
7. Compile and run `isolated_satellites`, then the minimal
   `complete_static_emulator`; verify Compose config and scoped cleanup.
8. Exercise a medium static scenario: two Orbits, 5-10 Satellites per Orbit,
   2-3 GroundStations and 1-2 ordinary ground ASes.
9. Review the evidence before beginning any Simulator design.

Running the complete 348-Satellite scene is not required for host acceptance.
Do not add recovery, Runner, dynamic link or Simulator behavior to bypass a
migration failure; diagnose the failed layer directly.
