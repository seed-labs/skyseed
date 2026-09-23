# Shared Runner

`Runner` consumes sealed material paths, binds the declared backend, activates B0
in `prepare()`, and runs subsequent boundaries on its logical clock. Lifecycle,
future delivery, waiting, pause/resume, failure reporting and cleanup live here.
The selected adapter owns BPF or native kernel operations. `deployment.py` discovers
exact container/process/namespace identities; `command_channel.py` owns one helper
and command stream, selected by `runtime_command.py`. It never compiles or imports
author modules. BPF telemetry remains in the BPF package.

See [execution contracts](../../../../docs/development/compiler-and-runner.md).
