"""Fresh-process module selection, canonical objects and import isolation."""
import subprocess
import sys
import textwrap

import pytest


def fresh(code, cwd):
    result = subprocess.run(
        [sys.executable, '-c', textwrap.dedent(code)],
        cwd=cwd, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_root_import_and_discovery_do_not_load_the_pipeline(tmp_path):
    fresh('''
        import sys
        import skyseed_v2 as sky
        names = {'emulator', 'simulator', 'experiment', 'configuration',
                 'adapters', 'backend', 'deployment'}
        assert names <= set(dir(sky))
        assert not any(n.startswith('skyseed_v2.') for n in sys.modules)
        assert 'seedemu' not in sys.modules
        try:
            sky.missing_owner
        except AttributeError:
            pass
        else:
            raise AssertionError('unknown owner was accepted')
    ''', tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_role_groups_have_canonical_exports_and_retired_paths_are_absent(tmp_path):
    fresh('''
        import importlib.util
        import skyseed_v2 as sky
        from skyseed_v2 import adapters
        from skyseed_v2.adapters import emulator_to_simulator
        from skyseed_v2.simulator import physical
        from skyseed_v2.simulator.physical import world, queries, models
        from skyseed_v2.configuration.simulator import zones
        assert adapters.create_simulator_static_input is emulator_to_simulator.create_simulator_static_input
        assert sky.configuration.simulator.build_global_zone_layout is zones.build_global_zone_layout
        for name in ('Satellite', 'Orbit', 'Shell', 'GroundStation', 'AutonomousSystem'):
            assert getattr(physical, 'Physical' + name) is getattr(world, 'Physical' + name)
        for name in queries.__all__:
            assert getattr(physical, name) is getattr(queries, name)
        for name in models.__all__:
            assert getattr(sky.simulator, name) is getattr(models, name)
        for module in (sky, sky.emulator.entities, sky.emulator.seedemu, sky.simulator, physical, world):
            for old in ('Satellite', 'Orbit', 'Shell', 'GroundStation', 'SkyseedAutonomousSystem',
                        'PhysicalFrame', 'PhysicalLinkObservation'):
                assert not hasattr(module, old), (module, old)
        for old in ('skyseed_v2.emulator_to_simulator', 'skyseed_v2.simulator.models',
                    'skyseed_v2.emulator.seedemu.skyseed_autonomous_system',
                    'skyseed_v2.configuration.emulator.satellite',
                    'skyseed_v2.configuration.simulator.zone', 'skyseed_v2.configuration.simulator.yaml',
                    'skyseed_v2.configuration.simulator.global_layout',
                    'skyseed_v2.simulator.physical.physical_frame',
                    'skyseed_v2.simulator.physical.physical_link',
                    'skyseed_v2.simulator.physical.position', 'skyseed_v2.simulator.physical.zone'):
            assert importlib.util.find_spec(old) is None, old
    ''', tmp_path)


@pytest.mark.parametrize('statement, forbidden', [
    ('import skyseed_v2 as sky; physical = sky.simulator.physical', ('skyseed_v2.simulator.planner', 'skyseed_v2.backend', 'seedemu')),
    ('from skyseed_v2.simulator import physical', ('skyseed_v2.simulator.planner', 'skyseed_v2.backend', 'seedemu')),
    ('from skyseed_v2.simulator.physical import PhysicalWorld', ('skyseed_v2.simulator.planner', 'skyseed_v2.backend', 'seedemu')),
    ('from skyseed_v2.simulator import planner', ('skyseed_v2.backend', 'seedemu')),
    ('from skyseed_v2 import backend; bpf = backend.bpf', ('skyseed_v2.backend.compiler', 'skyseed_v2.backend.bpf.runner')),
    ('from skyseed_v2.backend.bpf import compiler', ('skyseed_v2.backend.bpf.runner', 'skyseed_v2.experiment', 'seedemu')),
    ('from skyseed_v2.backend.compiler import compile_bpf', ('skyseed_v2.backend.bpf.runner', 'skyseed_v2.experiment', 'seedemu')),
    ('from skyseed_v2.backend.bpf import compile_bpf', ('skyseed_v2.backend.bpf.runner', 'skyseed_v2.experiment', 'seedemu')),
    ('from skyseed_v2.backend.bpf import runner', ('skyseed_v2.experiment', 'seedemu')),
])
def test_selected_import_does_not_load_unrelated_owners(statement, forbidden, tmp_path):
    fresh(f'''
        import sys, threading, multiprocessing
        from pathlib import Path
        threads = tuple(threading.enumerate())
        {statement}
        for prefix in {forbidden!r}:
            assert not any(n == prefix or n.startswith(prefix + '.') for n in sys.modules), prefix
        assert tuple(threading.enumerate()) == threads
        assert not multiprocessing.active_children()
        assert list(Path.cwd().iterdir()) == []
    ''', tmp_path)


def test_umbrella_and_selected_modules_preserve_canonical_identity(tmp_path):
    fresh('''
        import importlib
        import skyseed_v2 as sky
        from skyseed_v2 import emulator, simulator, experiment, configuration, adapters, backend, deployment
        for name in ('emulator', 'simulator', 'experiment', 'configuration', 'adapters', 'backend', 'deployment'):
            assert getattr(sky, name) is importlib.import_module('skyseed_v2.' + name)
        from skyseed_v2.simulator import physical, planner
        from skyseed_v2.backend.bpf import compiler, runner
        assert physical is simulator.physical is importlib.import_module('skyseed_v2.simulator.physical')
        assert planner is simulator.planner is importlib.import_module('skyseed_v2.simulator.planner')
        assert compiler is sky.backend.bpf.compiler is importlib.import_module('skyseed_v2.backend.bpf.compiler')
        shared_compiler = importlib.import_module('skyseed_v2.backend.compiler')
        assert compiler.compile_bpf is shared_compiler.compile_bpf
        assert compiler.BpfCompilationResult is shared_compiler.BpfCompilationResult
        assert compiler.BpfCompiledActivationBoundary is shared_compiler.BpfCompiledActivationBoundary
        assert runner is sky.backend.bpf.runner is importlib.import_module('skyseed_v2.backend.bpf.runner')
        for name in ('EmulatorGroundStation', 'GroundStationLocation', 'EmulatorOrbit', 'OrbitParameters', 'EmulatorSatellite', 'EmulatorShell'):
            assert getattr(sky, name) is getattr(emulator.entities, name)
        for name in ('GroundStation', 'Orbit', 'Satellite', 'Shell'):
            assert getattr(simulator, 'Physical' + name) is getattr(physical, 'Physical' + name)
            assert getattr(sky, 'Emulator' + name) is not getattr(physical, 'Physical' + name)
        assert sky.backend.bpf.compile_bpf is compiler.compile_bpf
        assert sky.backend.bpf.Runner is runner.Runner
        values = importlib.import_module('skyseed_v2.backend.compiler.result')
        assert sky.backend.bpf.BpfCompilationResult is values.BpfCompilationResult
        assert sky.backend.bpf.BpfCompiledActivationBoundary is values.BpfCompiledActivationBoundary
        assert simulator.Planner is planner.Planner
        execution = importlib.import_module('skyseed_v2.experiment.execution')
        assert experiment.run_generated_experiment is execution.run_generated_experiment
        assert 'Runner' in dir(sky.backend.bpf)
    ''', tmp_path)
    assert list(tmp_path.iterdir()) == []
