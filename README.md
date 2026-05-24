# skyseed

First-stage home for the satellite CGNAT outbound prototype migrated out of
`seed-emulator/examples/satellite`.

## Local dependency

Install the local `seedemu` dependency from the development worktree first:

```bash
cd /home/seed/seed-emulator-upstream-dev
pip install -e .
```

## Environment note

Some shells on this machine export:

```bash
PYTHONPATH=/home/seed/seed-emulator
```

To make sure `skyseed` imports the editable dependency from
`/home/seed/seed-emulator-upstream-dev`, run commands with one of:

```bash
env -u PYTHONPATH python3 ...
```

or:

```bash
unset PYTHONPATH
```

## Docker note

This machine uses `docker-compose`, not `docker compose`.

## Running from the repo root

From `/home/seed/skyseed`:

```bash
env -u PYTHONPATH python3 satellite/build_topology.py
cd satellite/output
docker-compose up -d
cd ..
env -u PYTHONPATH python3 satellite/controller.py --init-slot-runtime
env -u PYTHONPATH python3 satellite/controller.py --prepare-ut-egress-policy ut1
env -u PYTHONPATH python3 satellite/controller.py --apply-scenario t0
env -u PYTHONPATH python3 satellite/controller.py --install-ut-egress-mpls ut1
env -u PYTHONPATH python3 satellite/controller.py --verify-ut-egress-mpls ut1
```

## Running from the satellite directory

From `/home/seed/skyseed/satellite`:

```bash
env -u PYTHONPATH python3 build_topology.py
cd output
docker-compose up -d
cd ..
env -u PYTHONPATH python3 controller.py --init-slot-runtime
env -u PYTHONPATH python3 controller.py --prepare-ut-egress-policy ut1
env -u PYTHONPATH python3 controller.py --apply-scenario t0
env -u PYTHONPATH python3 controller.py --install-ut-egress-mpls ut1
env -u PYTHONPATH python3 controller.py --verify-ut-egress-mpls ut1
```

## Status

The first migration stage keeps the project in its original script-oriented
layout under `satellite/`. It does not yet introduce package refactors,
console scripts, or workspace restructuring.
