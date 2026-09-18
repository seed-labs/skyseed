# Simulator geometry

These helpers implement physical coordinate, Earth visibility and orbital math.
They contain no connection selection, route policy or runtime ownership. Default
physical Models use them; custom Models are free to use another implementation
while returning the documented coordinate and query values.

[Physical domain](../physical/README.md) owns their callers. They are intentionally
not placed in neutral shared code because their meaning belongs to Simulator.
