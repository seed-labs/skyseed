# Expensive-Run Policy

This runbook adapts the durable parts of the stabilization experience to the
post-refactor Skyseed v1 architecture. It is deliberately shorter than the old
incident-by-incident gate surface.

## Permanent invariants

These protect network correctness and remain fail-closed:

- one visible execution route per canonical example;
- no hidden Linux/core fallback for strict `bpf_owned` forwarding;
- future state is prepared inactive and becomes visible only at its boundary;
- partial worker, preload, write, or cleanup success is never recorded ready;
- audits report evidence and never repair a run into PASS;
- first-failure evidence survives cleanup;
- owned resources have explicit ownership and zero-residue verification;
- console, observation, and artifact presentation cannot backpressure network
  activation.

## Cheap gates before a real run

Use checks that correspond to a plausible late failure:

1. direct example `inspect`;
2. operational `preflight`;
3. profile `preview` and catalog/capacity contract;
4. focused unit/contract/integration tests;
5. the broader cheap regression suite when runtime, worker, backend, loader, or
   acceptance behavior changed;
6. bounded producer/consumer replay only when prepared lifecycle semantics
   changed.

Passing all historical scripts is not a gate. A check stays here only while it
protects a current invariant or a plausible failure surface.

## Evidence, not automatic blockers

- total phase time and comparison with the nearest successful run;
- activation lag when strict deadlines are disabled;
- disk pressure above the actual required capacity;
- startup duration while progress continues, and cleanup duration when the
  independent zero-residue lifecycle gate succeeds;
- report size and observation freshness.

These measurements trigger investigation. They do not kill a progressing run
or silently change its route.

## Retired incident guards

The following are intentionally absent from the active surface:

- S4-named implicit operational defaults;
- profile inheritance from one example into another;
- fallback profile discovery under the legacy `satellite/configs` tree;
- old executable runner aliases and capped/short compatibility profiles;
- audit readers bound to one historical artifact location;
- repeated global checks inside the live epoch path when a preparation-time or
  focused pending-record check protects the same invariant.

History may describe these mechanisms, but current code, CLI choices, profiles,
and primary documentation must not offer them as executable alternatives.

## Revision rule

When a guard blocks valid progress, identify the invariant it was intended to
protect. Replace it with the cheapest correctly scoped contract test or
preparation check, then delete the obsolete guard. Do not work around it with a
new flag, fallback, compatibility branch, or second profile route.

Before a user-operated long run, record only: exact example/command and output
directory, cheap gates just passed, prepared horizon coverage, forbidden work,
expected timing reference, and explicit remaining risks that could fail late.
