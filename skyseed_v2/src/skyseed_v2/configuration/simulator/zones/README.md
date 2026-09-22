# Zone declarations and generation

Zone IDs and latitude/longitude boundaries are input facts. The band generator
varies longitude cell counts with latitude and handles polar caps and the chosen
longitude alignment. YAML reading and generation produce the same definition type;
neither selects service satellites.

Use `python -m tools.observation.zone.generate --check data/global_zones.yaml` to check the
frozen global layout. The [Zone Tool](../../../../../tools/observation/zone/README.md) browses it;
[input documentation](../../../../../docs/user-guides/inputs-and-scenes.md) explains
layout selection. Membership belongs to the PhysicalWorld Model and service to Planner.
