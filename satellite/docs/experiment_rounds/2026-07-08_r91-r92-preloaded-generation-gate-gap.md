# r91-r92: Preloaded Generation Gate Gap

Date: 2026-07-08
Status: resolved by lifecycle gate and r93 validation

## Goal

- Validate the qdisc batch executor in the 3H/240s
  `operational_clean` / `bpf_owned` profile.
- Preserve the rolling-horizon contract: prepared/frozen BPF generations are
  consumed only at runtime boundaries and no fallback/full reload path is used.

## Findings

- r91 verified useful qdisc-batch behavior but failed after 52 runtime epochs:
  `H2:starlink-shell-slice-0003` had a prepared preloaded record with matching
  generation/source, while the runtime-derived BPF intent hash drifted from the
  frozen record.
- The local r91 fix made frozen preloaded records authoritative when
  generation/source/horizon and epoch identity match. That was a reasonable
  semantic direction, but the validation gate was incomplete.
- r92 was started too soon. It failed after only one runtime epoch:
  `H0:starlink-shell-slice-0002` expected preloaded flip-only activation, but
  the runtime reused existing readiness (`existing_readiness_reused_no_prepare`)
  and did not consume the next preloaded generation.
- This exposed a process gap: the cheap checks covered H2-style intent drift,
  but did not cover immediate-successor consumption after a successful
  preloaded/active generation transition or the full preloaded-record
  lifecycle.

## Changes

- qdisc batch executor changes remain separate from this failure and should not
  be blamed for the BPF handoff issues.
- A narrow BPF readiness change was added so a frozen preloaded record can be
  authoritative when the live intent hash drifts but epoch identity still
  matches.
- That change is not yet validated as a complete fix. It must not be treated as
  sufficient for another long run until adjacent lifecycle tests pass.

## Validation

- Focused tests passed before r92, but the suite was insufficient:
  - it covered source-state mismatch fail-closed
  - it covered live intent hash drift with matching frozen epoch identity
  - it did not cover a successful generation transition followed immediately by
    the next epoch consuming the next preloaded generation
  - it did not cover bootstrap/current-horizon preloaded records after the first
    successful runtime epoch or the equivalent successor behavior after a
    rolling-horizon boundary

## Remaining

- The immediate rerun blocker from this note was cleared before r93 by adding
  cheap lifecycle coverage for the preloaded-generation producer/consumer path,
  then validating the result with the full r93 3H run.
- Keep the following as durable coverage expectations for future changes:
  - the first consumer after bootstrap/current-horizon activation leaves the
    correct remaining preloaded records
  - the immediate successor epoch after any successful preloaded/active
    generation transition must consume the next preloaded generation rather
    than reuse existing readiness
  - consumed record removal advances `current_source_state_id` without erasing
    the remaining current-horizon records
  - rolling boundary handoff still consumes H1/H2 ready artifacts only at the
    runtime boundary

## Watch Next

- Treat r91/r92 as evidence of a missing preloaded-generation lifecycle gate,
  not as evidence that r88 was accidental.
- Future long runs must still be justified by a gate that would catch both:
  r91 H2 frozen/live intent drift and r92 immediate-successor non-consumption.
- r93 is the next success baseline for this line.
