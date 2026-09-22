"""Capture source files and actual assembly parameters for one generation."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
import json
from os.path import commonpath
from pathlib import Path

from yaml import safe_load


def _json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return {"seconds": value.total_seconds()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(getattr(key, "value", key)): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value) if not item.name.startswith("_")}
    raise TypeError(f"unsupported provenance value: {type(value).__name__}")


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_json_value(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _capture_inputs(paths):
    """Read each source once; follow only existing declarative file references.

    The configuration loader owns schema validation. This pass retains bytes and
    references for provenance; it does not construct or validate a second Config.
    """
    captured = {}
    pending = [Path(path).expanduser().resolve() for path in paths]
    while pending:
        source = pending.pop()
        if source in captured:
            continue
        content = source.read_bytes()
        captured[source] = content
        if source.suffix.lower() not in {".yaml", ".yml"} or b"skyseed_autonomous_systems" not in content:
            continue
        payload = safe_load(content)
        if isinstance(payload, dict):
            for system in payload.get("skyseed_autonomous_systems", ()):
                for field in ("shells_file", "ground_stations_file"):
                    if field in system:
                        pending.append((source.parent / system[field]).resolve())
    return captured


def _write_inputs(output, captured, parameters, descriptors, *, source_root=None):
    inputs = output / "inputs"
    roots = [str(path.parent) for path in captured]
    if source_root is not None:
        roots.append(str(source_root))
    root = Path(commonpath(roots))
    records = []
    for source, content in sorted(captured.items()):
        relative = Path("sources") / source.relative_to(root)
        destination = inputs / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        records.append({"source": str(source), "snapshot": relative.as_posix(),
                        "sha256": sha256(content).hexdigest()})
    _write_json(inputs / "provenance.json", {"sources": records, "scenario_parameters": parameters})
    _write_json(inputs / "effective-simulation.json", descriptors)
    return records


def _model_description(model):
    cls = type(model)
    description = {"implementation": f"{cls.__module__}.{cls.__qualname__}"}
    describe = getattr(model, "describe_configuration", None)
    if callable(describe):
        description["configuration"] = _json_value(describe())
        description["configuration_source"] = "Model.describe_configuration"
    else:
        description["configuration_source"] = "unavailable: Model does not provide describe_configuration"
    return description


def _describe_simulator(simulator):
    world = simulator.get_physical_world()
    planner = simulator.get_planner()
    roles = {
        "position": world.get_position_model(),
        "physical_link": simulator.get_physical_link_model(),
        **{name: getattr(planner, "get_" + name + "_model")() for name in (
            "inter_satellite", "ground_station", "zone_service", "parameter", "route")},
    }
    descriptions = {name: _model_description(model) for name, model in roles.items()}
    try:
        layout = world.get_zone_layout()
    except RuntimeError:
        zones = None
    else:
        zones = {
            "allow_overlap": layout.allow_overlap,
            "zones": [{"zone_id": z.zone_id, "priority": z.priority,
                       "south_latitude_deg": z.geometry.south_latitude_deg,
                       "north_latitude_deg": z.geometry.north_latitude_deg,
                       "west_longitude_deg": z.geometry.west_longitude_deg,
                       "east_longitude_deg": z.geometry.east_longitude_deg} for z in layout.zones],
        }
        descriptions["zone_membership"] = _model_description(layout.get_membership_model())
    descriptions["planner_configuration"] = _json_value(planner.configuration)
    descriptions.update(source="actual constructed Models before generation",
                        timeline=_json_value(simulator.get_timeline()), zones=_json_value(zones))
    return descriptions
