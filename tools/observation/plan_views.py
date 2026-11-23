"""Save drawing context once; inspect saved plans without running their models."""
from datetime import datetime, timezone
from collections.abc import Mapping
import base64
import gzip
import html
import json
from pathlib import Path

from skyseed import Scene, load_plans
from skyseed.pc.link import LinkPlan
from skyseed.pc.network.forwarding_rules import indexed_rules, frozen_rules
from tools.observation.scene import inspection as scene_view
from tools.observation.physical import inspection as physical_view
from tools.observation.link import inspection as link_view
from tools.observation.network import inspection as network_view
from tools.observation.compiler import inspection as compiler_view
from tools.observation._presentation.inspection_page import write_page
from tools.observation._presentation.plan_details import write_plan_details


def link_observations(evaluations):
    return [{"timestamp": value.timestamp.isoformat(),
             "candidates": [link_view._candidate(item) for item in value.candidates],
             "selected": [list(identity) for identity in value.selected],
             "pending": [{"id": identity, "ready_at": ready.isoformat()}
                         for identity, ready in value.pending.items()]}
            for value in evaluations]


def route_observations(evaluations):
    return [{"timestamp": value.timestamp.isoformat(),
             "assignments": dict(value.assignments), "associations": dict(value.associations)}
            for value in evaluations]


def save_context(path, physical, *, link_plans, route_plans, link_evaluations=(),
                 route_evaluations=(), physical_times=(), position_times=()):
    """Explicitly sample display geometry, using the caller's configured Physical.

    Plans remain separate archives. Optional default-model evaluations describe
    decisions that cannot be recovered from the final plans alone.
    """
    physical_data = physical_view.inspection_data(physical, physical_times)
    positions = {frame["timestamp"]: {node: state["position_m"] for node, state in frame["states"].items()}
                 for frame in physical_data["frames"]}
    times = set(position_times)
    for plan in (*link_plans, *route_plans):
        times.update((plan.start_time, plan.end_time))
        operations = plan.operations if isinstance(plan, LinkPlan) else plan.iter_state_operations()
        times.update(operation.timestamp for operation in operations)
    times.update(value.timestamp for value in (*link_evaluations, *route_evaluations))
    for timestamp in sorted(times):
        timestamp = timestamp.astimezone(timezone.utc)
        key = timestamp.isoformat()
        if key not in positions:
            snapshot = physical.snapshot_at(timestamp)
            positions[key] = {node: state.position_m for node, state in snapshot.states.items()}
    data = {"format": "skyseed.plan-view-context/v1", "positions": positions,
            "physical": physical_data, "link_evaluations": link_observations(link_evaluations),
            "route_evaluations": route_observations(route_evaluations)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, allow_nan=False) + "\n")
    return path


def load_context(source):
    """Read saved drawing data, or reuse a mapping already loaded by the caller."""
    if isinstance(source, Mapping):
        return source
    return json.loads(Path(source).read_text())


def write_scene(scene_file, output):
    scene = Scene.load(scene_file)
    return scene_view.write_inspection(scene_view.inspection_data(scene), output)


def write_physical(context_file, output):
    return physical_view.write_inspection(load_context(context_file)["physical"], output)


def _windows(plan, boundaries):
    """Walk operations once; initial state is strictly before the window start."""
    is_link = isinstance(plan, LinkPlan)
    state = dict(plan.initial_links) if is_link else indexed_rules(plan.initial_rules)
    operations = iter(plan.operations)
    operation = next(operations, None)
    for start, end in boundaries:
        while operation is not None and operation.timestamp < start:
            operation.apply(state)
            operation = next(operations, None)
        initial = dict(state) if is_link else frozen_rules(state)
        selected = []
        while operation is not None and operation.timestamp < end:
            selected.append(operation)
            operation.apply(state)
            operation = next(operations, None)
        stop = min(end, plan.end_time) if start < plan.end_time else end
        yield type(plan)(start, stop, initial, tuple(selected))


def _chunk(output, index, page):
    directory = Path(output) / f"{index:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    page["plan_details"] = "plans/index.html"
    payload = json.dumps(page, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # A single local script carries the compressed JSON. Browsers can load it
    # from file:// without a server or a second, uncompressed export.
    encoded = base64.b64encode(gzip.compress(payload.encode(), compresslevel=1, mtime=0)).decode("ascii")
    (directory / "frames.js").write_text(f'document.currentScript.plan="{encoded}";')
    # Old bookmarks still open the selected window in the continuous inspector.
    (directory / "index.html").write_text(
        f'<meta charset="utf-8"><script>const q=new URLSearchParams(location.search);'
        f'q.set("window","{index}");location.replace("../index.html?"+q)</script>')
    return {"start": page["start"], "end": page["end"], "src": f"{index:03d}/frames.js",
            "times": [f["timestamp"] for f in page["frames"]]}


def _history(output, name, windows):
    index = write_page(None, output, template=Path(__file__).parent / name / "viewer.html",
                       data_name=name, windows=windows)
    for directory in Path(output).iterdir():
        if not directory.is_dir() or not directory.name.isdecimal():
            continue
        legacy = directory / f"{name}.json"
        frames = directory / "frames.js"
        if not (legacy.is_file() or frames.is_file()):
            continue
        legacy.unlink(missing_ok=True)
        if int(directory.name) >= len(windows):
            frames.unlink(missing_ok=True)
            (directory / "index.html").unlink(missing_ok=True)
            if not any(directory.iterdir()):
                directory.rmdir()
    return index


def write_link(scene_file, base_file, execution_file, *, context, output):
    """Read LinkPlan files and saved geometry; preserve both branches and cuts."""
    scene = Scene.load(scene_file)
    data = load_context(context)
    base_plans, execution_plans = list(load_plans(base_file)), list(load_plans(execution_file))
    base, execution = link_view._joined(base_plans), link_view._joined(execution_plans)
    if (base.start_time, base.end_time) != (execution.start_time, execution.end_time):
        raise ValueError("link branches must cover the same interval")
    boundaries = [(plan.start_time, plan.end_time) for plan in base_plans]
    windows = []
    for index, (left, right) in enumerate(zip(_windows(base, boundaries), _windows(execution, boundaries), strict=True)):
        page = link_view.inspection_data(scene, [left], [right], positions=data["positions"],
                                         evaluations=data["link_evaluations"])
        windows.append(_chunk(output, index, page))
    write_plan_details(output, kind="link", streams={"base": base_plans, "execution": execution_plans},
                       files={"base": base_file, "execution": execution_file})
    return _history(output, "link", windows)


def write_network(scene_file, base_file, delivery_file, link_file, *, context, output):
    """Read RoutePlan files, including overlapping delivery and its final tail."""
    scene = Scene.load(scene_file)
    data = load_context(context)
    base_plans, delivery_plans = list(load_plans(base_file)), list(load_plans(delivery_file))
    base, delivery = network_view._joined(base_plans), network_view._joined(delivery_plans)
    links = network_view._joined(load_plans(link_file), links=True)
    if not base.start_time == delivery.start_time == links.start_time:
        raise ValueError("plan histories must share their start")
    starts = sorted({plan.start_time for plan in base_plans})
    ends = [*starts[1:], max(base.end_time, delivery.end_time)]
    boundaries = list(zip(starts, ends, strict=True))
    windows = []
    for index, (left, right, topology) in enumerate(zip(
            _windows(base, boundaries), _windows(delivery, boundaries), _windows(links, boundaries), strict=True)):
        page = network_view.inspection_data(scene, [left], [right], [topology], positions=data["positions"],
                                            evaluations=data["route_evaluations"])
        for frame in page["frames"]:
            timestamp = datetime.fromisoformat(frame["timestamp"])
            frame["base_held"] = timestamp >= base.end_time
            frame["links_held"] = timestamp >= links.end_time
        windows.append(_chunk(output, index, page))
    write_plan_details(output, kind="route", streams={"base": base_plans, "delivery": delivery_plans},
                       files={"base": base_file, "delivery": delivery_file})
    return _history(output, "network", windows)


def write_compiler(plan_file, output):
    windows = []
    for index, plan in enumerate(load_plans(plan_file)):
        compiler_view.write_inspection([plan], Path(output) / f"{index:03d}")
        windows.append((index, f"{plan.stream} {plan.start_time.isoformat()}", plan.end_time.isoformat()))
    path = Path(output) / "index.html"
    entries = "".join(f'<li><a href="{i:03d}/index.html">{html.escape(start)} – {html.escape(end)}</a></li>'
                      for i, start, end in windows)
    path.write_text('<!doctype html><meta charset="utf-8"><style>body{font:18px system-ui;'
                    'max-width:900px;margin:3em auto}li{margin:.8em}</style>'
                    '<h1>Compiler</h1><p>按计划时段浏览 / inspect by plan window</p><ul>' + entries + '</ul>')
    return path
