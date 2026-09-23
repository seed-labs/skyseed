# YAML input adapter

Loaders decode separate Shell and GroundStation inventories or the aggregate AS
format. They validate declarations before construction and return configuration
values owned by the receiving configuration module. Omitted capabilities remain
`None`; a Model may supply its own documented fallback later.

See [Emulator configuration](../README.md) and the checked-in
[data](../../../../../data/README.md) for supported input examples and regeneration.
