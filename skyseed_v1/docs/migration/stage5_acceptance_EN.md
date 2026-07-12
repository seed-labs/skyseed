# Stage 5 Acceptance: Pure CLI and Canonical Example

Status: **PASS**

No Compose operation, host-network mutation, BPF attach, or long real-runtime
experiment was started for this gate.

## Entry boundary

The installed command is now:

```bash
skyseed-v1 run --example s4_operational_clean [options]
```

`skyseed_v1.cli.main` owns only argument parsing, example selection, and option
forwarding. `skyseed_v1.cli.experiment` is a thin executable wrapper. The
experiment and profile orchestration implementations live in `runtime/`.

The two old executable paths under `satellite/apps/` are temporary bridges of
at most 25 lines and contain no classes or business policy. They are not the
documented user entry.

## Command equivalence

The canonical dry-run selected `rich_s4_operational_clean_bpf` and passed its
catalog and Docker address-capacity gates.

The only intentional underlying-command change is argument 1, the runner file:

| Evidence | SHA-256 | Result |
|---|---|---|
| baseline command snapshot | `a1e9c4459a50aa4806411d6e899ce8669f660381b5bbadc2f968e385ebb9de82` | reference |
| new command with runner path restored to baseline path | `a1e9c4459a50aa4806411d6e899ce8669f660381b5bbadc2f968e385ebb9de82` | exact match |
| new canonical command | `2eb439de8b68f0b496a944e428553e07fa67e4f979ef077e8ed53ea07ea15a66` | expected path-only change |
| semantic argv after Python and runner | `4fa5af570001d632b3c77280f85c911a556da95bb5ab22f4d35312438fac7aeb` | frozen for later gates |

Profile hash `c4efe9d2...` and catalog-gate hash `d1290ba4...` remain unchanged.

## Ordered consumer replay

The complete frozen r139/r140-equivalent consumer corpus was replayed after
the entry cutover:

- `ok=true`, `passed=true`;
- 100/100 records, H0-H3 each 25;
- four prepared horizons and 1005 consumer binding references;
- 100 `shared_gz_noop_hold` transitions;
- zero blockers and zero retired IX references;
- wall time 131.79 seconds.

## Automated checks

- new package discovery: 23/23 pass, including four CLI boundary tests;
- focused entry/profile/runtime regression: 299/299 pass;
- full legacy discovery: 791/791 pass;
- installed `skyseed-v1 list-examples`: pass;
- canonical and legacy-bridge profile dry-runs: pass.

## Accepted boundary

Stage 5 establishes one clean user-facing route without changing the generated
runtime request. Observability, audit, SEED adapters, operational tools, data
ownership, and the final retirement of temporary flat-module bridges remain
for stages 6 and 7.
