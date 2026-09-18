# Execution materials

`actions.py` defines backend intents and resource states. `sealing.py` appends
ordered compilation results, closes data and publishes the manifest last.
`codec.py` reads the sealed artifact; `mechanics.py` implements capacity/allocation
mechanics shared by compilation and consumption.

A manifest binds stream hashes, sequence lineage, timeline and the successor
delivery contract. It lives with payloads under `results/compiler/`; Producer's
unpublished staging is internal. Runner receives a manifest path, not live Compiler
objects or a calculation callback. [Backend contracts](../../../../../docs/development/compiler-and-runner.md)
define continuity and fixed reservation semantics. These immutable values have a
construction/publication handoff, not a runtime process lifecycle.
