# Current Design State

## Current phase

Initial architecture baseline.

## Current workflow step

The initial whole-system design has been recorded. No functional vertical slice has started.

## Confirmed direction

- The system is divided into Emulator and Simulator.
- Emulator owns static entities, SEED integration and container-related configuration.
- Simulator owns time-dependent topology and execution planning.
- The Emulator exposes only a read-only view containing information required by the Simulator.
- The Emulator–Simulator boundary is a domain contract, not a specific file format.
- Topology is a subsystem coordinator rather than a giant implementation class.
- Planning is separated into backend-independent semantic planning and backend-specific planning.
- Compiler lowers an approved backend plan into executable content.
- Runner executes approved content and emits structured observations.
- UT is temporarily outside the current main slice.
- v1 is referenced only when a current design problem requires a focused investigation.

## Open design questions

- The minimal base-node model and its relationship with SEED.
- The first Emulator vertical slice.
- The exact read-only contract between Emulator and Simulator.
- Which parts of SEED require focused investigation during the first slice.
- The detailed object responsibilities inside Emulator.

## Next single objective

Design the first Emulator vertical slice before implementing business functionality.
