"""Small checks over declared call shapes and normal framework execution paths."""
from dataclasses import dataclass
import inspect
from pathlib import Path


@dataclass(frozen=True)
class Check:
    status: str
    operation: str
    explanation: str

    def __str__(self):
        return f'{self.status}: {self.operation}: {self.explanation}'


def show(checks):
    for check in checks:
        print(check)
    return not any(c.status == 'error' for c in checks)


def check_model(model, protocol):
    """Bind the actual minimum Protocol call shapes; never instantiate a class.

    Constructors, inheritance, annotations and optional capabilities are irrelevant.
    Pass e.g. RoutePlanningModel, PositionModel or ZoneMembershipModel as protocol.
    This checks invocation compatibility, not results; use a fresh scene for that.
    """
    if inspect.isclass(model):
        return (Check('error', protocol.__name__, 'supply a constructed instance'),)
    results = []
    for name, member in vars(protocol).items():
        if name.startswith('_') or not inspect.isfunction(member):
            continue
        operation = f'{protocol.__name__}.{name}'
        method = getattr(model, name, None)
        if not callable(method):
            results.append(Check('error', operation, 'required method is missing or not callable'))
            continue
        contract = inspect.signature(member)
        arguments, keywords = [], {}
        for parameter in tuple(contract.parameters.values())[1:]:
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
                keywords[parameter.name] = object()
            elif parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                arguments.append(object())
        try:
            signature = inspect.signature(method)
        except (ValueError, TypeError) as error:
            results.append(Check('not checked', operation, f'call signature unavailable: {error}'))
            continue
        try:
            signature.bind(*arguments, **keywords)
        except TypeError as error:
            results.append(Check('error', operation, str(error)))
        else:
            results.append(Check('pass', operation, 'accepts the framework call shape'))
    if not results:
        results.append(Check('error', getattr(protocol, '__name__', 'model'), 'no required Protocol methods supplied'))
    return tuple(results)


def check_program(program):
    """Reuse normal offline C/ELF preparation; no kernel load."""
    from skyseed.backend.bpf.dataplane import prepare_program
    try:
        prepared = prepare_program(program)
        return (Check('pass', 'C/object', f'compiled/inspected {prepared["sha256"][:12]}; kernel acceptance not checked'),)
    except Exception as error:
        return (Check('error', 'C/object', str(error)),)


def check_diagnostic(operation, diagnostic):
    """An explicitly requested optional observation cannot invalidate a core result."""
    try:
        diagnostic()
        return Check('pass', operation, 'optional diagnostic completed')
    except Exception as error:
        return Check('warning', operation, f'optional diagnostic failed: {error}')


def check_scene(factory, *, stop_at='planner', output=None, continuation_end=None, dataplane_program=None):
    """Construct a fresh user-selected Simulator and run the normal finite flow.

    Cutoffs: position, physical-link, planner, compiler, materials. Output, when
    provided, retains ordinary evidence. Only materials needs an output directory.
    The factory owns cleanup if construction itself fails. Once returned, the
    existing Simulator/Producer owners close even when checks fail.
    """
    from skyseed.experiment.producer import Producer
    from skyseed.backend.execution_materials.sealing import _RunnerArtifactWriter
    from skyseed.simulator import ReachableRouteOutcome, LocalRouteOutcome
    if stop_at not in {'position', 'physical-link', 'planner', 'compiler', 'materials'}:
        raise ValueError('unsupported scene cutoff')
    if stop_at == 'materials' and output is None:
        raise ValueError('materials check requires an explicit output directory')
    results, simulator, producer, writer = [], None, None, None
    try:
        simulator = factory()
        timeline = simulator.get_timeline()
        world = simulator.get_physical_world()
        count = 0
        exercised_routing = complete_paths = False
        if stop_at == 'position':
            for timestamp in timeline.iter_timestamps():
                world.frame_at(timestamp)
                count += 1
            if continuation_end is not None:
                from skyseed.simulator import SimulationTimeline
                for timestamp in SimulationTimeline(timeline.end_time + timeline.observation_interval, continuation_end, timeline.observation_interval).iter_timestamps():
                    world.frame_at(timestamp)
                    count += 1
        else:
            planner = simulator.get_planner()
            producer = Producer(timeline, world, simulator.get_physical_link_model(), planner,
                evidence_path=None if output is None else Path(output) / 'history', retain=continuation_end is not None)
            pull = (producer.produce_next_observation if stop_at == 'physical-link' else
                    producer.produce_next_plan if stop_at == 'planner' else producer.produce_next_realization)
            if stop_at == 'materials':
                writer = _RunnerArtifactWriter(Path(output) / 'materials', dataplane_program=dataplane_program)
            for endpoint in (None, continuation_end) if continuation_end is not None else (None,):
                if endpoint is not None:
                    producer.extend(endpoint)
                while (value := pull()) is not None:
                    count += 1
                    if stop_at != 'physical-link':
                        outcomes = planner.get_state().route_outcomes
                        exercised_routing |= bool(planner.get_route_demands()) or any(isinstance(v, LocalRouteOutcome) for v in outcomes)
                        complete_paths |= any(isinstance(v, ReachableRouteOutcome) for v in outcomes)
                    if writer is not None:
                        writer.append(value)
            if stop_at != 'physical-link':
                if not exercised_routing:
                    results.append(Check('warning', 'routing', 'scene supplied no demands or local arrangements; demand logic was not exercised'))
                definition = getattr(dataplane_program, 'data', None)
                if stop_at == 'planner' and definition is not None and complete_paths:
                    results.append(Check('warning', 'Planner → DataPlane', 'selected structured program requires local data; this scene supplies complete paths'))
            if writer is not None:
                writer.finalize()
        results.insert(0, Check('pass', stop_at, f'{count} completed results through the normal validation path'))
        if count == 0:
            results.append(Check('warning', stop_at, 'no results exercised the requested component'))
    except Exception as error:
        results.insert(0, Check('error', stop_at, str(error)))
    finally:
        # Producer takes ownership of these same Models once constructed.
        owners = [writer, producer] if producer is not None else []
        if producer is None and simulator is not None:
            owners.append(simulator)
        for owner in owners:
            if owner is not None:
                try:
                    (owner.shutdown if owner is simulator else owner.close)()
                except Exception as error:
                    results.append(Check('error', f'{stop_at} cleanup', str(error)))
    return tuple(results)
