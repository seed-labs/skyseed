# BPF backend

[Compiler](../compiler/README.md) maps domain intent into Linux/BPF activation work.
The [DataPlane](dataplane/README.md) supplies selectable C/ELF programs, shared support, pure translation and the runtime adapter
owned by Runner. [Execution materials](../execution_materials/README.md) own sealed codecs and delivery
contracts. [Runner](runner/README.md) initializes resources, binds and activates
that work, then cleans up resources it owns.

[Compiler/Runner reference](../../../../docs/development/compiler-and-runner.md) defines continuity, preparation
and execution semantics. Deployment belongs to the separate Deployer.

The public BPF compiler/result and Runner imports delegate to the shared backend owners.
