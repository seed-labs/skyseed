# Physical domain

[World](world/README.md) holds physical entities, PositionModel and optional Zones.
[Queries](queries/README.md) carry bounded requests and immutable facts.
[Models](models/README.md) compute positions, membership and physical feasibility.
Physical results do not select connections, acquire terminals or choose routes.

[Calculation flow](../../../../docs/architecture/calculation.md) shows how these facts
reach Planner. The [Model table](../../../../docs/development/model-interfaces.md) separates minimum
requirements from optional default optimizations.
