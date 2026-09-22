# Shared execution materials

`actions.py` defines Compiler intentions; `boundaries.py` orders them.
`sealing.py` incrementally invokes the selected backend translator, writes indexed
binary records and publishes the manifest last. `codec.py` reads v8 sealed records
without importing the Compiler or author modules. `mechanics.py` contains common
identity allocation, interface naming and state-application rules. The selected
backend owns its capacity accounting and validation: BPF in
`../bpf/dataplane/materials.py`, Linux in `../linux/materials.py`.

The manifest binds the backend, static foundation, integrity, lineage and delivery
continuity. BPF carries prepared ELF and changed table versions; Linux carries
native link/parameter/Zone and changed route actions. Runtime supplies only actual resource bindings.
See [format and lifecycle contracts](../../../../docs/development/compiler-and-runner.md).
