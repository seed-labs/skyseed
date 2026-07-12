# Structural Migration Validation Matrix

The default gate is selected by impact. A long run is not a universal
requirement.

| Change scope | Required cheap evidence | Escalation |
|---|---|---|
| package/docs only | import, structure tests, legacy suite | none |
| one pure model | owner unit tests | contract tests if IR changes |
| IR or serialization | unit + contract fixtures + fingerprint comparison | bounded replay |
| topology/planning | unit + contract + preview/frozen producer comparison | full ordered replay at stage 3 |
| compiler | unit + backend-plan comparison; no host writes | offline native compile when BPF-owned |
| runtime lifecycle | unit + integration + ordered replay through successor state | system test if process lifecycle changes |
| backend execution | unit + failure semantics + offline compile/probe | scoped host/system test |
| observability/audit | report truth table + artifact contract tests | replay real immutable artifacts if needed |
| CLI/example | generated selection/command comparison + dry run | no real run until stage 7 |
| multiple runtime/backend modules or scale/timing | all adjacent cheap gates | canonical long run only after explicit gate |

## Test tiers

- `unit`: one module, no Docker/host/network side effects
- `contracts`: stable IR shape, serialization, fingerprints, fail-closed rules
- `integration`: bounded in-process or subprocess module handoffs
- `replay`: immutable prepared data consumed in real lifecycle order
- `system`: explicit Linux/BPF/Compose environment participation
- long-run acceptance: a separately authorized scarce validation surface

Historical incident tests are retained only when they express a current stable
invariant. Duplicate round-specific assertions and old private path assertions
are not migration requirements.

## Long-run gate

Before a canonical long run, record:

1. exact profile, command, output directory, Skyseed commit, and SEED commit;
2. all unit, contract, integration, replay, and offline BPF results;
3. producer, first consumer, next consumer, no-op/consumed/retired/failure coverage;
4. frozen input coverage for all four horizons;
5. blocked stale/contaminated artifacts;
6. forbidden work checks: full runtime regeneration, unexpected attach/rebind,
   Linux forwarding fallback, premature active flip, or hidden repair;
7. accepted remaining risk. Any plausible late correctness failure blocks the run.
