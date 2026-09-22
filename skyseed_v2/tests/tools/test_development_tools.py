"""Opt-in author checks share real Model interfaces, owners and preparation."""
from datetime import timedelta
from pathlib import Path
import subprocess
import sys

from tools.development import check_model, check_scene, check_program, check_diagnostic
from skyseed_v2.simulator import RoutePlanningModel
from templates.dataplane.threshold.scene import create_scene, ThresholdRoutes, START
from skyseed_v2.backend.bpf.dataplane import BpfProgram
from templates.dataplane.threshold.definition import DATA

ROOT = Path(__file__).parents[2]


def test_all_minimum_roles_use_actual_protocols_without_optional_hooks():
    from test_me1_models import assembly
    from skyseed_v2.simulator import physical, planner
    _, _, models = assembly(create=False)
    roles = (physical.PositionModel, physical.ZoneMembershipModel, physical.PhysicalLinkModel,
        planner.InterSatelliteConnectionPlanningModel, planner.GroundStationAccessPlanningModel,
        planner.ZoneServicePlanningModel, planner.ParameterPlanningModel, planner.RoutePlanningModel)
    for model, role in zip(models.values(), roles):
        assert all(c.status == 'pass' for c in check_model(model, role))
    class Bad:
        def plan_route_outcomes(self, a, *, wrong): pass
    assert check_model(Bad(), RoutePlanningModel)[0].status == 'error'
    assert check_model(object(), RoutePlanningModel)[0].status == 'error'
    assert check_model(ThresholdRoutes, RoutePlanningModel)[0].status == 'error'


def test_scene_cutoffs_continuation_and_cleanup(tmp_path):
    closed = []
    class Owned(ThresholdRoutes):
        def shutdown(self, *, wait=True): closed.append(id(self))
    for cutoff in ('position', 'physical-link', 'planner', 'compiler'):
        result = check_scene(lambda: create_scene(Owned()), stop_at=cutoff,
            output=tmp_path / cutoff, continuation_end=START + timedelta(seconds=15))
        assert result[0].status == 'pass', result
    assert len(closed) == 4
    assert check_scene(lambda: create_scene(), stop_at='position')[0].status == 'pass'


def test_basic_warnings_and_executed_failure():
    def empty():
        scene = create_scene()
        scene.get_planner().set_route_demands(())
        return scene
    assert any(c.status == 'warning' and 'no demands' in c.explanation for c in check_scene(empty))
    assert check_diagnostic('statistics', lambda: 1/0).status == 'warning'
    class Bad(ThresholdRoutes):
        def plan_route_outcomes(self, *a, **kw): raise ValueError('required handoff failed')
    result = check_scene(lambda: create_scene(Bad()))
    assert result[0].status == 'error' and 'handoff' in result[0].explanation


def test_runnable_drivers_and_generation_before_c_or_model(tmp_path):
    original = ROOT / 'templates/dataplane/threshold/develop.py'
    copied = tmp_path / 'copy' / 'a' / 'b' / 'develop.py'
    copied.parent.mkdir(parents=True)
    copied.write_text(original.read_text().replace("ROOT = Path(__file__).resolve().parents[3]", f"ROOT = Path({str(ROOT)!r})"))
    # There is no handler or Model in the copied directory; header operation needs neither.
    result = subprocess.run([sys.executable, str(copied), 'headers', '--output', str(tmp_path / 'headers')], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'headers/generated/threshold.h').exists()
    for operation in ('model', 'scene', 'continue', 'c', 'materials'):
        result = subprocess.run([sys.executable, str(original), operation, '--output', str(tmp_path / operation)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'pass:' in result.stdout
    assert check_program(BpfProgram(tmp_path / 'missing.c'))[0].status == 'error'


def test_concrete_downstream_warning_and_import_has_no_side_effects(tmp_path):
    from templates.models.routing import SimpleRouteModel
    selected = BpfProgram(ROOT / 'templates/dataplane/threshold/handler.bpf.c', data=DATA,
                          authoring_directory=tmp_path / 'generated')
    checks = check_scene(lambda: create_scene(SimpleRouteModel()), dataplane_program=selected)
    assert checks[0].status == 'pass'
    assert any(c.status == 'warning' and c.operation == 'Planner → DataPlane' for c in checks)
    code = 'import tools.development, templates.dataplane.threshold.develop; print("import-only")'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0 and result.stdout == 'import-only\n' and not result.stderr
