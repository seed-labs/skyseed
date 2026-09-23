# Experiment-owned output records

`history_writer.py` persists formal frames, published Planner evidence and Compiler
lineage in their producer subdirectories. `history.py` owns the versioned manifest
and bounded-prefix readers. `physical_links.py` persists optional candidate evidence
and its on-disk timestamp index. Tools read these formats; they do not define them.

At a requested endpoint Producer drains accepted writes and closes compressed members.
Controller atomically publishes `results/experiment/completed.json`, binding history,
candidate and performance prefixes to that completed range. New calculation may append
a suffix while a reader consumes the previous publication. Hashing occurs once during
consumption; no full-history replay is required to publish the next request.
Terminal sealing records session termination separately.

Compiler batches publish independently for runtime delivery. This directory is not
a global artifact registry or recovery framework. [Experiment contract](../../../../docs/development/experiment-control.md#output-ownership)
defines publication order and [output guide](../../../../docs/user-guides/outputs-and-tools.md)
locates user-facing files.

`generation.py` prepares the file layout; `provenance.py` captures authored inputs.
`occupancy.py` owns output/runtime claims used by calculation, execution, replacement
and service stop. It does not operate Etcd. `observation.py` records experiment
timing/events, not request results. The [parent module map](../README.md#interaction-and-revision-owners)
locates the service, consumers, selected material catalog and revision calculation.
Etcd state/request/user records stay separate from these file streams. Completed
publications pin selected candidate evidence rather than overwriting original history.
