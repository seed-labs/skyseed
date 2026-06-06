# Worktree Map

This note records the current repository and worktree layout for the satellite
prototype after the first-stage `skyseed` migration.

## Active Repositories

- Active satellite project repo:
  - path: `/home/seed/skyseed`
  - remote: `origin https://github.com/seed-labs/skyseed.git`
  - active code path: `/home/seed/skyseed/satellite`
- Local `seedemu` dependency repo:
  - path: `/home/seed/seed-emulator-upstream-dev`
  - remotes:
    - `origin https://github.com/seed-labs/seed-emulator.git`
    - `upstream https://github.com/seed-labs/seed-emulator.git`
  - active branch: `development2`
  - role: local editable dependency only for the satellite project
- Non-active sibling mirror / old worktree:
  - path: `/home/seed/seed-emulator`
  - role: do not use for active satellite migration or development

## Where To Work

- Do active satellite feature work in:
  - `/home/seed/skyseed/satellite`
- Do not continue active satellite development in:
  - `/home/seed/seed-emulator-upstream-dev/examples/satellite`
  - `/home/seed/seed-emulator/examples/satellite`
- Treat `seed-emulator-upstream-dev/examples/satellite` as a historical copy
  unless a later task explicitly asks to edit or retire it.

## Dependency Setup

Install the local `seedemu` dependency from the development worktree:

```bash
cd /home/seed/seed-emulator-upstream-dev
git remote set-url origin https://github.com/seed-labs/seed-emulator.git
git fetch origin development2
git switch development2
pip install -e .
```

## Runtime Caveats

- Some shells on this machine export:
  - `PYTHONPATH=/home/seed/seed-emulator`
- Supported Skyseed app entrypoints prepend
  `/home/seed/seed-emulator-upstream-dev` to their import path.
- For one-off Python commands or direct `seedemu` imports, prefer:

```bash
env -u PYTHONPATH python3 ...
```

- This machine uses `docker-compose`, not `docker compose`.

## Command Roots

From `/home/seed/skyseed`:

```bash
python3 satellite/build_topology.py
python3 satellite/controller.py ...
```

From `/home/seed/skyseed/satellite`:

```bash
python3 build_topology.py
python3 controller.py ...
```

## Current Satellite Baseline

- `skyseed` is now the main satellite repository.
- The current mainline baseline keeps:
  - `CGNAT-like` outbound Internet access
  - `UT -> miniInternet` reachability
  - static/dynamic split for `UT egress` policy
  - dynamic install reduced from `49` commands to `15`
- The current baseline does not include:
  - `UT-to-UT`
  - `anchor/public inbound`
  - `same-anchor hairpin`
  - `SEED core` modifications

## Recent Skyseed Commits

- `5cfa032` `import satellite CGNAT outbound prototype from seed-emulator`
- `4e807e3` `docs: update satellite paths after skyseed migration`

## Current Cleanliness

- `/home/seed/skyseed` is the repo that should stay clean for satellite work.
- Runtime artifacts under `satellite/configs/`, `satellite/output/`,
  `__pycache__/`, and `*.pyc` are ignored and should not be committed.
- `/home/seed/seed-emulator` currently contains unrelated local changes and is
  not a safe place for incidental satellite edits.
