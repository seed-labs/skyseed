# Satellite Internet 1.0 Structural Baseline

Recorded at structural migration start.

## Source identity

| Item | Value |
|---|---|
| Skyseed tag | `satellite-internet-v1.0-baseline` |
| Skyseed commit | `fbb7ff849606b95d2c7ab4399be35656153d5b1c` |
| SEED branch | `master` |
| SEED commit | `61acdf4c9699b25b06843ec4aad747108b05924e` |
| Python | `seed310`, Python 3.10.20 |
| Canonical profile | `rich_s4_operational_clean_bpf` |

The shell may contain `PYTHONPATH=/home/seed/seed-emulator`, which resolves an
obsolete sibling mirror. Supported commands must resolve the editable
dependency at `/home/seed/seed-emulator-upstream-dev`. Dependency identity is
therefore part of every gate, not an operator assumption.

## Successful route

```text
session check / preflight / host prepare
  -> run_skyseed_validation_profile
  -> profile and catalog contract
  -> run_starlink_shell_experiment
  -> prepared frozen bundle
  -> ordered prepared consumer replay
  -> Compose and host materialization
  -> EventTimeline runtime queue
  -> H0 active execution
  -> H1/H2/H3 maintenance worker prepare + inactive preload
  -> compact ready handoff + boundary activation
  -> runtime observation and audit
  -> rollback + Compose cleanup + postflight
```

The validated run was `s4-1_0-compact-handoff-r141-long3-20260712`.

## Workload and lifecycle

- S4 planned shell, 348 satellites
- 28 selected UTs, 12 gateways, 14 zones
- four 240-second horizons
- 25 epochs per horizon; 100 total runtime epoch results
- strict `bpf_owned`, shared G/Z
- no Linux/MPLS/NAT/Mini-Internet forwarding fallback
- three successor workers, each publishing 25 inactive preloads
- nonblocking 30-second gateway service loopback observation

## Required acceptance facts

- profile return code 0 and `aggregate_runtime_succeeded=true`;
- H0-H3 each complete 25/25 with no runtime exception;
- three worker-ready and three ready-consumed events;
- every worker reports 25 inactive preloads, zero blockers, and no forbidden work;
- H3 physical generation zero is consumed;
- runtime BPF miss/drop delta and Linux fallback counts are zero;
- rollback completes;
- postflight reports zero scoped containers, networks, direct host interfaces,
  BPF pins, and runtime locks;
- raw ping timeouts remain evidence and are not repaired with fallback.

## Cheap baseline results

At migration start:

```text
py_compile: PASS
unittest discover satellite/test_*.py: 791/791 PASS
new-package tests: established in stage 1
```

No Compose, host mutation, or long run was used to establish this structural
starting point.

The machine-comparable profile and input fingerprints are stored in
`canonical_profile_snapshot.json`. Absolute run IDs and output directories are
intentionally normalized; the selected profile arguments and committed input
files are not.

## Known documentation drift

The repository-root `README.md` and `WORKTREE_MAP.md` still reference removed
`build_topology.py` and `controller.py` entrypoints and the old SEED
`development2` branch. They are not authoritative for the 1.0 path. They will
be replaced when the new CLI becomes canonical in stage 5; historical commands
must not be copied into the new user guide.
