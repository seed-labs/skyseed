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
a global artifact registry or recovery framework. [Experiment contract](../../../../docs/design/experiments.md)
defines publication order and [output guide](../../../../docs/user-guides/outputs-and-tools.md)
locates user-facing files.
