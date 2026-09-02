# Complete static emulator

This is the smallest maintained human-review example. It uses two Satellites,
one GroundStation, a short Timeline, explicit Models, one PhysicalWorld, one
Planner, and the common Plan-to-Compiler producer chain.

Run from the repository root:

```bash
python -m examples.complete_static_emulator.main
```

Or from this directory:

```bash
python main.py
```

Both write by default to
`examples/complete_static_emulator/output/`, regardless of the caller's current
working directory. `--output PATH` selects another owned root and
`--no-override` refuses replacement. Use the generated `cleanup.sh` for any
started Compose project before regenerating; remove review output only after
human review.

Or inspect only the readable Simulator chain:

```bash
python -m examples.complete_static_emulator.simu
```

`emu.py` constructs the upstream static Emulator. `simu.py` projects immutable
Simulator input, creates PhysicalWorld and Planner with direct Model injection,
and drains the shared incremental producer. The default Timeline has four
10-second formal boundaries; the example publishes two Plans and compiles two
realizations while retaining all four activation timestamps.

`main.py` additionally compiles the Docker project and writes:

```text
output/
├── runtime/
├── domain-history/                 terminal manifest + three owned JSONL streams
├── inspection/
├── start.sh
└── cleanup.sh
```

Inspection covers linked temporal Physical World, Zone, one exact Physical
Frame, staged Planner, compact Compiler, and System Observation views.
Inspection failure is reported separately and does not invalidate valid
runtime or semantic output.

This example is intentionally small; canonical window/count qualification
belongs to the Starlink Gen1 example.
