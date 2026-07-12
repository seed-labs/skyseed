# Stage 3 Acceptance: Topology, Planning, and S4 Example

Status: **PASS**

No Compose, host-network mutation, BPF attach, or long real-runtime experiment
was started for this gate.

## Structural result

The single active implementations of the following responsibilities now live
under `skyseed_v1/src/skyseed_v1/`:

- stable contract and topology-pipeline IR in `core/ir/`;
- replaceable topology and planner interfaces in `core/interfaces/`;
- orbit and ground catalog models in `models/`;
- topology pipeline, shell propagation, experiment topology, and Starlink
  topology construction in `topology/`;
- planner validation, routing/G/Z planning, and temporal planning in
  `planning/`.

The old top-level module names are temporary aliases to these package modules.
They contain no policy, planning, or runtime fallback implementation. Their
removal belongs to the canonical import cutover.

At this historical stage,
`examples/s4_operational_clean/scenario.py` owned the explicit, user-facing
selection of the validated S4 workload. The later example-composition cutover
replaced it with the single editable `experiment.py`; `scenario.py` is no
longer an active or retained route.

## Profile equivalence

The post-migration values match
`docs/migration/canonical_profile_snapshot.json`:

| Evidence | SHA-256 | Result |
|---|---|---|
| normalized profile | `c4efe9d2f319dd37342018ba342a7a3b44e8c7d70a6e21b1a6eacd9f56b52755` | match |
| catalog selection gate | `d1290ba403641488745778c099139c848edcb965a65a75288659c5b1a5f4e084` | match |
| generated underlying command | `a1e9c4459a50aa4806411d6e899ce8669f660381b5bbadc2f968e385ebb9de82` | match |

The example independently confirms 348 satellites, 28 selected UTs, 12
gateways, 14 zones, four 240-second horizons, 10-second sampling,
`operational_clean`, and strict `bpf_owned`.

## Ordered consumer replay

Source:
`satellite/output/validation_profiles/r139-4h-shared-gz-consumer-contract`.
This is the complete frozen bundle reused by the successful r140 route; the
r140 output directory itself retains only the non-frozen alias.

Command family:

```bash
python satellite/apps/replay_prepared_runtime_consumer_contracts.py \
  --run-dir <r139-run-dir> --output <temporary-report>
```

Result:

- `ok=true`, `passed=true`;
- 100/100 records;
- H0-H3 each 25;
- four prepared horizons;
- 1005 consumer binding references;
- zero retired IX binding references;
- 100 valid `shared_gz_noop_hold` transitions;
- zero blockers;
- wall time 172.60 seconds.

## Shared G/Z generation replay

Command family:

```bash
python satellite/apps/replay_r134_shared_gz_generations.py \
  --run-dir <r139-run-dir> --output <temporary-report>
```

Result:

- `ok=true`;
- 100/100 generations replayed from current code;
- H0-H3 each 25;
- current compiled schema `skyseed-bpf-owned-compiled-runtime-inputs-v2`;
- historical compiled v1 maps were not consumed;
- zero blockers and zero reported map conflicts;
- H3 physical-generation wrap, including generation zero in the corpus, was
  exercised by the complete sequence.

## Automated tests

- new package tests: 13/13 pass;
- focused topology/profile tests: 101/101 pass;
- focused planning/event/runtime-default tests: 352/352 pass;
- full legacy discovery: 791/791 pass;
- migrated module compile/import and alias-identity checks: pass;
- new-package scan for imports of migrated flat module names: zero findings;
- `git diff --check`: pass.

## Issues caught cheaply

The new tests caught and resolved:

1. an incorrect initial fixture fingerprint;
2. an incorrectly assumed profile selector shape;
3. a dynamic example-loader registration error;
4. a remaining flat `from contracts` import that collided with the new
   `tests/contracts` package.

None required a container or long run.

## Accepted boundary

Stage 3 proves the topology/planning move and frozen producer/consumer
semantics. It does not claim that runtime/backend separation or the new CLI is
complete. `network_mutation`, SEED integration, horizon maintenance, Linux/BPF
execution, observability, and app orchestration remain to be migrated in later
stages.
