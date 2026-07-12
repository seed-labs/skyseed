# Environment and Running Skyseed v1

This is the canonical setup and operating guide for a standalone Skyseed v1
checkout. The retired migration tree is not required.

For the latest cheap-suite, real-run, and reboot evidence, see
`validation_status_EN.md`. Migration acceptance files remain historical snapshots
and are not the current-status dashboard.

## Two environment levels

Cheap development actions need only Python and SEED Emulator:

- import and contract tests;
- `run.py inspect`;
- command/profile preview;
- topology, planning, and visualization export tests that do not request a
  concrete Docker backend.

A real operational-clean run additionally needs Linux host participation:

- a running Docker daemon and Compose;
- `ip`, `tc`, namespace, bridge, and sysctl support;
- Clang with a BPF target and a working `bpftool`;
- root or non-interactive `sudo -n` for owned host links, qdisc, BPF maps, and
  cleanup;
- enough disk, memory, container, subnet, and interface capacity for the
  selected example.

The real-run requirements are checked by `preflight`; they are not imposed on
ordinary unit tests.

## Validated environment identity

Satellite Internet 1.0 was accepted with:

| Component | Validated identity |
|---|---|
| Python | CPython 3.10.20 (`seed310`) |
| SEED Emulator | official `seed-labs/seed-emulator`, branch `master`; migration-start commit `61acdf4c9699b25b06843ec4aad747108b05924e` |
| PyYAML | 6.0.2 |
| requests | 2.22.0 |
| sgp4 | 2.25 |
| Docker Engine | 19.03.8 |
| Compose | `docker-compose` 1.27.4 |
| Clang | 10.0.0 with BPF target support |
| bpftool selected by the canonical examples | `/usr/lib/linux-tools/5.4.0-216-generic/bpftool`, v5.4.291 |
| host family | Linux x86-64 with namespaces, veth/bridge, qdisc/netem, tc-BPF, and BPF filesystem support |

These versions record the successful environment; they are not a claim that
every newer version is incompatible. A changed kernel, Docker, SEED, Clang, or
bpftool identity must pass preflight and the relevant offline/native gates
before a real run.

## Checkout and Python setup

Use Python 3.10. From the v1 project directory—the directory containing
`pyproject.toml`, `examples/`, and `src/`:

```bash
python3.10 -m venv .venv
source .venv/bin/activate

git clone --branch master https://github.com/seed-labs/seed-emulator.git \
  ../seed-emulator-upstream-dev

export SKYSEED_SEEDEMU_ROOT="$(cd ../seed-emulator-upstream-dev && pwd)"
unset PYTHONPATH

python -m pip install --upgrade pip
python -m pip install -e "$SKYSEED_SEEDEMU_ROOT"
python -m pip install -e .
```

In the current migration workspace, v1 is one level below the Git root. The
equivalent final command from that root is `python -m pip install -e
skyseed_v1`.

`SKYSEED_SEEDEMU_ROOT` is optional when `seed-emulator-upstream-dev` is beside
the Skyseed Git checkout. Setting it explicitly makes the selected dependency
auditable. Do not place an obsolete SEED checkout in `PYTHONPATH`; in
particular, remove `/home/seed/seed-emulator`.

Verify the selected imports:

```bash
python - <<'PY'
import seedemu
import skyseed_v1
print("skyseed_v1:", skyseed_v1.__file__)
print("seedemu:", next(iter(seedemu.__path__)))
PY
```

## Host tools for a real run

Package names vary by Linux distribution. The host must provide the equivalent
of:

```text
docker, docker-compose (or a compatible Compose installation)
iproute2: ip, tc
clang/llvm with the BPF backend
bpftool matching a usable host kernel toolset
sudo, sysctl, nsenter
```

Docker must be running and usable by the selected account. Skyseed's current
host backend invokes privileged operations through `sudo -n` when it is not
already root, so an interactive password prompt is not a supported runtime
path. Configure narrowly scoped host permissions appropriate for the machine;
do not treat this document as a generic recommendation to grant unrestricted
sudo access.

The canonical profiles currently bind an explicit bpftool path. Before running
on a different kernel/tool installation, confirm that the configured path in
the resolved profile exists, is executable, and reports a usable version.
Preflight fails or warns on missing tools, an obsolete SEED override, stale
owned state, incompatible bridge-netfilter state, or insufficient disk.

## Cheap verification sequence

From the v1 project directory:

```bash
python tests/run.py
python examples/s4_operational_clean/run.py inspect
python examples/s5_operational_clean/run.py inspect
python examples/s5_operational_clean/run.py preflight
```

From the migration-workspace root, prefix these paths with `skyseed_v1/`.

Current action semantics:

- `inspect`: resolves the Python composition and validates its declared
  counts, intervals, qualification window, and derived profile;
- `preflight`: inspects host readiness and owned residue without launching the
  experiment; the example entry prints a short human summary by default, while
  `preflight --format json` emits the full audit payload;
- `clean`: explicitly scans and removes only residue owned by Skyseed's scoped
  labels/names, fixed BPF pin names, direct-backend interface patterns, and
  project runtime locks; it refuses to act while a Skyseed process is active
  and finishes with the same scoped post-clean assessment;
- `preview`: resolves the canonical underlying command and writes its effective
  profile/capacity evidence, but does not create the real network;
- no action argument: performs the real preparation, Compose, host/BPF runtime,
  observation, audit, and cleanup route.

A real run acknowledges the command immediately with its run ID, output path,
resolved experiment shape, initial phase, and Ctrl-C behavior. Until the first
durable progress snapshot exists, a rate-limited heartbeat reports elapsed
preview time. Afterwards, phase transitions and completed epochs are projected
from durable evidence. Long unchanged phases retain a rate-limited heartbeat
with elapsed time, the active child operation, and available Compose state.
Even the initial acknowledgement is emitted by this daemon observer. Its
output is coalesced and bounded, and it does not participate in epoch
scheduling, runtime locks, or network-update completion.

Treat `ready=false` or any blocking reason as a stop. Warnings such as low disk
or an obsolete `PYTHONPATH` require operator review even when preflight remains
technically ready. For a full S4/S5 run, keep generous free disk headroom; the
four-horizon prepared evidence alone can exceed 1 GB before Docker images and
containers are counted.

The direct example entries automatically remove the retired
`/home/seed/seed-emulator` value from their child import environment and select
the configured current SEED checkout. A user does not need to run
`unset PYTHONPATH`; unrelated custom entries are preserved.

## Running an example

S4, the accepted 960-second baseline:

```bash
python examples/s4_operational_clean/run.py
```

S5, the smaller 172-satellite example with its own ground preset:

```bash
python examples/s5_operational_clean/run.py
```

Each example writes only beneath its ignored `output/<run-id>/`. The run also
uses ignored project-owned `.runtime/` for the global active lock and `.cache/`
for reusable native objects. Normal completion and failure cleanup must leave
zero owned containers, networks, direct interfaces, BPF pins, locks, and
worker processes. Delete an example's `output/` only after cleanup has
completed and retained evidence is no longer needed.

## After a reboot or before a live demonstration

Do not reboot during a Skyseed run. If the VM is already stable and no system
update requires a reboot, keeping the validated boot is lower risk than
rebooting immediately before a demonstration. If reboot behavior must be
qualified, do it with enough time for cleanup and a rehearsal—not at the venue.

After reboot, from the repository root:

```bash
systemctl is-active docker
findmnt -n -o FSTYPE /sys/fs/bpf
/home/seed/miniconda3/envs/seed310/bin/python \
  -m skyseed_v1.operations.prepare_host_mpls --format json
python skyseed_v1/examples/s5_operational_clean/run.py clean
python skyseed_v1/examples/s5_operational_clean/run.py preflight
python skyseed_v1/examples/s5_operational_clean/run.py preview
```

Expected results are `active`, filesystem type `bpf`, successful standard host
preparation, a successful scoped clean, and preflight `ready=True` with no
blockers. `preview` must resolve the
intended S5 shape without creating the real network. If bpffs is absent, do not
start the demonstration run; on the currently validated Ubuntu host it can be
mounted explicitly with:

```bash
sudo mount -t bpf bpf /sys/fs/bpf
```

Then repeat `findmnt`, host preparation, and preflight. This mount is currently present on the
validated VM but is not declared in `/etc/fstab` or a dedicated systemd mount
unit, so it is one known reboot-sensitive item. On the 2026-07-12 hard reboot,
bridge-netfilter values also returned to their host defaults; the standard
`prepare_host_mpls` command restored the required passthrough/MPLS state before
preflight. Operators should use that command rather than editing individual
sysctls. The real-run host prepare repeats the required preparation before
materialization.
Avoid package, kernel, Docker, Compose, SEED, or bpftool upgrades immediately
before a demonstration.

## Current duration boundaries

- S4's committed ground preset is qualified by the accepted 960-second run.
  Increasing its four 240-second horizons fails configuration validation until
  a longer-window preset is deliberately qualified.
- S5's committed 9-UT/5-GW/6-zone preset is qualified for 7200 seconds from
  `2026-07-09T00:00:00Z`; with 240-second horizons, 30 is the current maximum.
  The two-hour qualification is an explicit analysis window, not a periodicity
  claim.
- `continuous=True` currently means a finite sequence of `loop_count`
  horizons. Indefinite replenish-until-Ctrl-C operation is not implemented;
  Ctrl-C is an early interruption that must enter cleanup.

Changing the start time or exceeding a ground preset's qualified duration must
fail before network creation. Rebuild or define a suitable shell-specific
ground preset instead of disabling that gate.

## After a run

Run preflight again and retain the run directory until its outcome and cleanup
evidence have been reviewed:

```bash
python examples/s5_operational_clean/run.py preflight
```

For costly-run evidence and rerun policy, see `expensive_runs_EN.md`.

## AI/operator handoff protocol

This guide is also the first handoff document for a Codex or maintainer moving
v1 to another host. Do not begin by editing profiles until the environment
difference is measured.

For subsequent design work, also read `../architecture/design_backlog_EN.md` so
discussed-but-deferred directions are neither forgotten nor mistaken for
already accepted architecture.

First collect evidence into a temporary directory:

```bash
mkdir -p /tmp/skyseed-environment-audit
unset PYTHONPATH

python -m skyseed_v1.operations.kernel_probe \
  --json-out /tmp/skyseed-environment-audit/kernel.json
python examples/s5_operational_clean/run.py inspect \
  > /tmp/skyseed-environment-audit/s5-inspect.json
python examples/s5_operational_clean/run.py preflight --format json \
  > /tmp/skyseed-environment-audit/preflight.json
python tests/run.py \
  > /tmp/skyseed-environment-audit/tests.stdout 2>&1
```

`kernel_probe` creates and removes a temporary namespace/veth/qdisc probe when
permissions allow. Run it only while no Skyseed experiment is active. If
preflight exits nonzero, preserve its JSON and diagnose the reported blocker;
do not append `|| true` to an automated acceptance decision.

The receiving agent should report:

1. exact Git commit for Skyseed and SEED;
2. Python executable/version and resolved `skyseed_v1`/`seedemu` paths;
3. OS, kernel, architecture, cgroup mode, Docker and Compose identities;
4. Clang/LLVM BPF target, bpftool identity, BTF and bpffs state;
5. `ip`/`tc`, namespaces, netem, bridge-netfilter, MPLS and noninteractive
   privilege results;
6. free disk/memory and any stale owned resources;
7. which cheap gates passed, failed, or were deliberately not run.

Do not copy generated output, caches, locks, virtual environments, Docker
state, BPF pins, or absolute paths from the old host. Copy committed source and
data, install dependencies afresh, and let the new example create its own
`output/`.

## Example Ubuntu 20.04 to 24.04 risk audit

An OS upgrade is not automatically a Skyseed incompatibility, but several
changes cross real backend contracts:

| Likely change | Why it matters | Minimum evidence before a real run |
|---|---|---|
| default Python becomes newer than 3.10 | syntax/import success does not prove dependency or serialized behavior equivalence | use Python 3.10 first; otherwise run the complete v1 test suite and explicitly qualify the new interpreter |
| Docker/Compose v2 replaces legacy Compose | generated Compose interpretation, project naming, network naming, readiness, and cleanup may differ | compile a preview, inspect generated Compose, then perform a bounded create/down/zero-residue system gate |
| cgroup v2 and newer container defaults | resource accounting, privilege and namespace behavior may change | preflight plus bounded container/readiness and cleanup verification |
| newer kernel, BTF and BPF verifier | map limits, helper availability, tc attach, verifier acceptance and pinning can change | kernel probe, cold native BPF compile, loader/map ABI tests, attach/detach/zero-pin system gate |
| newer Clang/LLVM | BPF code generation and accepted flags may change | compile both committed native BPF sources and compare loader-visible map contracts |
| bpftool moves or no longer matches the configured path | the strict profile requires an explicit executable and probes it before preparation | update the declared environment/profile path explicitly; run feature, map and cleanup probes; never fall back silently to another tool |
| newer iproute2/tc/netem | batch syntax, qdisc/filter output and deletion behavior may differ | namespace/veth/netem probe plus bounded qdisc apply/rollback test |
| nftables/bridge-netfilter or sysctl defaults change | packets may traverse host filtering differently | inspect all bridge-netfilter probes and retain the intended passthrough semantics |
| AppArmor, sudo or Docker permission policy changes | host operations may block late or wait for an interactive prompt | prove `sudo -n`/root capability and Docker access before preparation |
| newer SEED `master` | layer/compiler/container output can legitimately change | record its commit, pass adapter/topology tests, inspect Compose differences, then use a bounded SEED compile gate |

Classification rule for the receiving agent:

- Python-only/library drift starts with import, unit, contract and integration
  tests.
- Planner/model/data drift additionally needs ordered offline consumer replay.
- Docker, kernel, BPF, tc, namespace, privilege or SEED compiler drift needs a
  bounded system gate before S4/S5 long execution.
- A failure must be fixed in the owning layer. Do not make the new host pass by
  disabling preflight, weakening cleanup, changing forwarding ownership, or
  adding a legacy fallback route.
