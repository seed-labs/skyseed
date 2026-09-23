# Geometric physical links

`GeometricPhysicalLinkModel.compute_observation(world, frame, request)` evaluates
only requested relationships. Current geometry uses Earth-clear LOS, distance,
GS elevation and Zone representative-point elevation. Delay follows geometry.
Private code separates candidate enumeration, geometry and optional contact proof.

Default circular-orbit prediction can establish bounded contact windows and reuse
prepared geometry. Request limits reduce search when the relevant Models support
that cooperation; they must not silently remove required admission coverage.
S5's [CurrentGeometryModel](../../../../../../examples/Sky01_s5_custom_models/model.py)
demonstrates a current-only author implementation without these optimizations.
See [minimum/optional contracts](../../../../../../docs/development/model-interfaces.md).
