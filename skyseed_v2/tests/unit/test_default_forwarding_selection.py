"""Backend defaults are an assembly choice, never a later Model replacement."""
import pytest
from skyseed_v2 import experiment, simulator
from tests.unit.test_default_simulator import definition
from tests._support import drain_simulator


@pytest.mark.parametrize('backend', ['linux', 'bpf'])
def test_backend_defaults_preserve_topology_and_services(definition, backend):
    sim = simulator.create_default_simulator(definition, forwarding_backend=backend)
    plans, _, _ = drain_simulator(sim)
    state = None; states = []
    for plan in plans:
        for transition in plan.transitions:
            state = simulator.apply_plan_transition(state, transition); states.append(state)
    assert len(states) == 4
    assert any(s.operational_connections for s in states)
    assert any(s.zone_service_assignments for s in states)
    assert any(s.route_outcomes for s in states) is (backend == 'bpf')


@pytest.mark.parametrize('backend', ['linux', 'bpf'])
@pytest.mark.parametrize('mode', ['full', 'partial', 'empty'])
def test_explicit_model_is_kept_at_assembly(definition, backend, mode):
    class Partial(simulator.GatewayHopCountRoutePlanningModel):
        def plan_route_outcomes(self, *args, **kwargs):
            result = super().plan_route_outcomes(*args, **kwargs)
            return simulator.RoutePlanningResult(result.outcomes[:1])
    class Empty:
        def __bool__(self):
            return False

        def plan_route_outcomes(self, *args, **kwargs):
            return simulator.RoutePlanningResult()

    model = {'full': simulator.GatewayHopCountRoutePlanningModel, 'partial': Partial, 'empty': Empty}[mode]()
    sim = simulator.create_default_simulator(definition, forwarding_backend=backend, route_model=model)
    assert sim.get_planner().get_route_model() is model
    plans, _, _ = drain_simulator(sim)
    state = None
    for plan in plans:
        for transition in plan.transitions:
            state = simulator.apply_plan_transition(state, transition)
    assert bool(state.route_outcomes) is (mode != 'empty')
    if mode == 'partial':
        assert len(state.route_outcomes) == 1


def test_generated_linux_default_has_identities_links_services_but_no_business_routes(definition, tmp_path):
    from contextlib import closing
    from examples.Sky00_tiny_network import tiny_network as tiny
    from skyseed_v2.backend.execution_materials.codec import _open_runner_artifact
    from pathlib import Path
    sim = simulator.create_default_simulator(definition, forwarding_backend='linux')
    model = sim.get_planner().get_route_model()
    output = experiment.ExperimentController(tiny.build_emulator(), sim, tmp_path/'linux',
        entry_path=Path(__file__), forwarding_backend='linux', internet_map_enabled=False,
        traffic_enabled=False).generate()
    assert sim.get_planner().get_route_model() is model
    with closing(_open_runner_artifact(output/'results/compiler/runner-artifact/manifest.json')) as artifact:
        assert artifact.dataplane['nodes']
        actions = [a for b in artifact.boundaries() for a in b.executable['request']['actions']]
        assert any(a['op']=='create' for a in actions)
        assert any(a['op']=='address_add' for a in actions)
        assert not any(a['op']=='route' for a in actions)


def test_omitted_backend_matches_explicit_bpf_defaults(definition):
    omitted = simulator.create_default_simulator(definition)
    explicit = simulator.create_default_simulator(definition, forwarding_backend='bpf')
    assert isinstance(omitted.get_planner().get_route_model(), simulator.GatewayHopCountRoutePlanningModel)
    assert drain_simulator(omitted)[:2] == drain_simulator(explicit)[:2]


@pytest.mark.parametrize('value', [None, '', 'LINUX', 'native', object()])
def test_invalid_backend_uses_canonical_validation(definition, value):
    with pytest.raises(ValueError, match="forwarding_backend must be 'bpf' or 'linux'"):
        simulator.create_default_simulator(definition, forwarding_backend=value)


def test_backend_export_preserves_canonical_type_and_validator():
    from skyseed_v2.backend import ForwardingBackend
    from skyseed_v2.backend.selection import _backend
    from skyseed_v2.configuration import forwarding
    assert ForwardingBackend is forwarding.ForwardingBackend
    assert _backend is forwarding._backend
    assert _backend('linux') is ForwardingBackend.LINUX
    assert not hasattr(experiment, 'create_simulator')
