# BPF backend

[Compiler](compiler/README.md) maps domain intent into Linux/BPF activation work.
[Execution materials](execution_materials/README.md) own sealed codecs and delivery
contracts. [Runner](runner/README.md) initializes resources, prepares and activates
that work, then cleans up resources it owns.

[Compiler/Runner reference](../../../../docs/development/compiler-and-runner.md) defines continuity, preparation
and execution semantics. Deployment belongs to the separate Deployer.
