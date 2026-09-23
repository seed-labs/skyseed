"""Offline native facts and backward reads through the existing observation Tools."""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from skyseed.backend import compile_plan
from skyseed.backend.execution_materials.codec import _open_runner_artifact
from skyseed.backend.runner import lifecycle
from skyseed.experiment import ExperimentController
from skyseed.experiment.output.observation import _ExecutionObserver
from tools.observation.bpf_compiler import collect_bpf_compiler_data
from tools.observation.bpf_compiler.projection import project_compact_bpf_compiler_data
from tools.observation.bpf_compiler.reader import read_compact_bpf_compiler_evidence
from tools.observation.bpf_compiler.renderer import render_bpf_compiler_page
from tools.observation.performance.inspection import collect_performance_data
from tools.observation.runner import collect_runner_data, write_runner_tool
from tests.unit.test_linux_runtime import produce
from tests.unit.test_linux_routes import LocalSnapshots, runtime
from tests.tools.test_runner_tool import _snapshot, _outcome


@pytest.mark.parametrize("backend", ["linux", "bpf"])
def test_small_generated_tools_use_recorded_backend_and_keep_navigation(tmp_path, backend):
    from examples.Sky00_tiny_network import tiny_network as tiny
    from tools import write_experiment_tools
    emulator = tiny.build_emulator()
    simulator = tiny.create_simulator(emulator, duration=timedelta(seconds=10))
    output = tmp_path / backend
    ExperimentController(emulator, simulator, output, entry_path=Path(tiny.__file__),
        forwarding_backend=backend, internet_map_enabled=False,
        **({"traffic_enabled": False} if backend == "bpf" else {})).generate()
    results = write_experiment_tools(output, physical_world=simulator.get_physical_world())
    assert all(r["status"] == "PASS" for r in results.values()), results
    assert {"physical-world", "zone", "planner", "bpf-compiler", "performance"} <= results.keys()
    assert "traffic" not in results and not (output / "results/traffic/probe-plan.json").exists()
    compiler = output / "visualizations/bpf-compiler"
    data = json.loads((compiler / "bpf-compiler-inspection.json").read_text())
    assert data["forwarding_backend"] == backend
    page = (compiler / "index.html").read_text()
    assert "Compiler Inspection" in page and "BPF Compiler Inspection" not in page
    assert f'<strong>{"Linux IPv4" if backend == "linux" else "BPF"}</strong>' in page
    assert 'data-src="../physical-world/index.html"' in page
    assert "not installed protocol routes or observed packet paths" in page
    index = (output / "visualizations/index.html").read_text()
    assert 'href="bpf-compiler/index.html"' in index and "Compiler Tool" in index
    assert "BPF Compiler Tool" not in index
    # Historical compact v3 histories did not record a backend. Standalone
    # reading keeps them useful without classifying them from route coverage.
    history = read_compact_bpf_compiler_evidence(output / "results/compiler/compiler.jsonl")
    assert history["header"]["format_version"] == 3
    standalone = project_compact_bpf_compiler_data(history)
    assert standalone["forwarding_backend"] is None
    assert "backend not recorded" in render_bpf_compiler_page(standalone)
    assert standalone["inventory"] == data["inventory"]
    stages = {row["stage"] for row in map(json.loads, (output / "results/experiment/generation-performance.jsonl").read_text().splitlines()[1:])}
    assert "semantic-to-backend-lowering" in stages and "semantic-to-bpf-lowering" not in stages


@pytest.mark.parametrize("backend", ["linux", "bpf"])
def test_live_compiler_local_outcomes_are_not_unreachable(tmp_path, backend):
    simulator, plans, _ = produce(tmp_path, ends=(5,), model=LocalSnapshots())
    results, previous = [], None
    for plan in plans:
        previous = compile_plan(plan, simulator.get_physical_world(), previous, forwarding_backend=backend)
        results.append(previous)
    data = collect_bpf_compiler_data(tuple(plans), tuple(results))
    assert data["forwarding_backend"] == backend
    assert data["inventory"]["forwarding_intents"] > 0
    assert data["inventory"]["planned_routes_unreachable_samples"] == 0
    assert data["inventory"]["planned_routes_reachable_samples"] == 0


@pytest.mark.parametrize("fail", [False, True])
def test_native_runner_view_reports_real_completed_and_partial_facts(tmp_path, monkeypatch, fail):
    path = produce(tmp_path / "materials", ends=(10,), model=LocalSnapshots())[2][0]
    with closing(_open_runner_artifact(path)) as artifact:
        kernel, _ = runtime(monkeypatch, artifact)
    monkeypatch.setattr(lifecycle._Deployment, "discover", lambda *a: kernel.bindings)
    monkeypatch.setattr(lifecycle._Deployment, "foundation_identity", lambda *a: "native-tool-offline")
    class Helper:
        def __init__(self, **kwargs): assert kwargs["forwarding_backend"] == "linux"
        def start(self): pass
        def stop(self): pass
        def _set_main_deadline(self, deadline): pass
        def execute(self, action, request): return kernel.execute(action, request)
    monkeypatch.setattr(lifecycle, "_PrivilegedHelper", Helper)
    runner = lifecycle.Runner(compose_project_name="native", runtime_artifact=path)
    events = tmp_path / "events.jsonl"
    runner._enable_event_history(events)
    try:
        runner.prepare()
        if fail:
            kernel.fail = "route_add"
            with pytest.raises(OSError): runner.activate_next()
        else:
            runner.activate_next()
            runner.activate_next()
    finally:
        runner.cleanup()
    kernel.assert_clean()
    snapshot = runner.inspection_snapshot()
    outcome = _outcome("failed" if fail else "completed")
    data = collect_runner_data(snapshot, outcome, event_history_path=events)
    assert data["overview"]["forwarding_backend"] == "linux"
    assert data["default_dataplane"]["kind"] == "linux-ipv4"
    native = data["default_dataplane"]["native_activations"]
    assert [v["activation_ordinal"] for v in native] == ([0, 1] if fail else [0, 1, 2])
    assert native[0]["installed_routes"] == native[0]["target_transition_complete"] == 1
    assert native[-1]["target_transition_complete"] is (not fail)
    assert native[-1]["live_state_may_be_partial"] is fail
    if fail:
        assert data["scale_safety"]["main_subphases"][-1]["live_state_may_be_partial"] is True
        assert data["overview"]["frontiers"]["last_active"] == native[0]["boundary_timestamp"]
    else:
        assert native[-1]["withdrawn_route_bindings"] == 1
    assert set(data["default_dataplane"]["availability"].values()) == {"not applicable"}
    assert data["default_dataplane"]["latest_capacity_audit"]["capacity"] is None
    assert data["delivery"]["reclamation"]["deleted_map_entries"] is None
    assert data["scale_safety"]["batching"]["bpf_syscalls"] is None
    assert data["scale_safety"]["workers"]["parallel_phase_count"] is None
    assert data["detailed_timing"]["operations"]["active_generation_update"]["seconds"]["total"] is None
    write_runner_tool(snapshot, outcome, tmp_path / "runner", event_history_path=events)
    page = (tmp_path / "runner/index.html").read_text()
    assert "BPF Runner" not in page and 'id="native-activation-table"' in page
    assert "not a BPF generation" in page and "not applicable" in page


def test_runner_legacy_and_explicit_bpf_keep_recorded_detail():
    legacy = _snapshot()
    old = collect_runner_data(legacy, _outcome())
    assert old["default_dataplane"]["kind"] == "unrecorded"
    assert old["default_dataplane"]["latest_capacity_audit"]["capacity"]["headroom"] == 2
    snapshot = deepcopy(legacy)
    snapshot["runner"]["artifact"]["backend"] = "bpf"
    current = collect_runner_data(snapshot, _outcome())
    assert current["default_dataplane"]["kind"] == "tc-bpf"
    assert current["default_dataplane"]["latest_activation"] == old["default_dataplane"]["latest_activation"]
    assert current["detailed_timing"] == old["detailed_timing"]


@pytest.mark.parametrize("stage", ["semantic-to-bpf-lowering", "semantic-to-backend-lowering"])
def test_performance_keeps_historical_and_new_lowering_labels(tmp_path, stage):
    observer = _ExecutionObserver()
    observer.record(component="compiler", stage=stage, phase="one-shot", duration_seconds=.2)
    path = observer.write_jsonl(tmp_path / "performance.jsonl")
    data = collect_performance_data(path)
    compiler = next(c for c in data["components"] if c["component"] == "compiler")
    assert compiler["stages"][0]["label"] == stage
    compiler_group = next(g for g in data["pipeline_groups"] if g["label"] == "Compiler")
    assert compiler_group["stages"] == [{"component": "compiler", "stage": stage}]


@pytest.mark.parametrize("backend", ["linux", "bpf", None])
def test_runner_renderer_executes_without_visual_inspection(tmp_path, backend):
    from tools.observation.runner.renderer import render_runner_page
    from tests.tools.test_runner_tool import _event
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable for nonvisual JavaScript verification")
    snapshot = _snapshot()
    if backend is not None:
        snapshot["runner"]["artifact"]["backend"] = backend
    if backend == "linux":
        snapshot["events"] = (
            _event("preparation_start", 1),
            _event("preparation_end", 2, duration_seconds=1),
            _event("activation_end", 3, backend="linux", activation_semantics="ordered-linux-operations",
                activation_ordinal=1, boundary_timestamp="2026-01-01T00:00:05+00:00", main_seconds=.1,
                completed_actions=2, created_connections=0, deleted_connections=0, installed_routes=1,
                withdrawn_route_bindings=1, target_transition_complete=True, live_state_may_be_partial=False),
        )
    page = render_runner_page(collect_runner_data(snapshot, _outcome()))
    script = page.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    path = tmp_path / "runner.js"
    path.write_text(script)
    harness = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',hidden:false,
 classList:{toggle(){},add(){}},setAttribute(){},addEventListener(){},querySelectorAll(){return[]},
 querySelector(s){return element(id+s)},scrollIntoView(){}});return elements.get(id)}
const document={documentElement:{},getElementById:element,querySelector:element,querySelectorAll(){return[]}};
const context=vm.createContext({document,setTimeout(){}});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const native=process.argv[2]==='linux';
assert.equal(element('native-activation-section').hidden,!native);
vm.runInContext('currentLanguage="en";renderAll();focusTimelineItem(1)',context);
assert.equal(document.title,'Runner Inspection Tool');
if(native){assert.match(element('native-activation-table').innerHTML,/Withdrawn route bindings/);
 assert.match(element('overview').innerHTML,/Native Linux IPv4 forwarding/);
 vm.runInContext('renderLocalDetail({item:DATA.graphical_timeline.groups.flatMap(g=>g.rows).find(r=>r.row==="main_activation").items[0]})',context);
 assert.match(element('graph-local-detail').innerHTML,/Activation ordinal/);}
else assert.match(element('dataplane').innerHTML,/Latest capacity audit/);
console.log('Renderer language, backend fields and timeline navigation: PASS');
'''
    result = subprocess.run([node, "-e", harness, str(path), backend or "unknown"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
