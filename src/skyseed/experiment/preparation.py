"""Captured user preparation functions using the existing readiness process owner."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skyseed.simulator import PlannedOperationalState
import inspect
import importlib
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
import json
from pathlib import Path
import runpy
import sys


@dataclass(frozen=True)
class PreparationContext:
    """Prepared facts for one invocation; no runtime clock or Simulator owner.

    ``inputs`` is the author's JSON-compatible preparation_inputs snapshot.
    ``source_files`` maps original absolute input paths to captured Paths.
    ``runner_snapshot`` contains runtime foundation bindings and ACTIVE frontiers.
    ``initial_state`` is the first planned state; ``runtime_manifest`` describes
    stable addresses, Zone prefixes and backend identity/interface allocation.
    """
    output_directory: Path
    invocation_directory: Path
    inputs: dict
    source_files: dict
    runner_snapshot: dict
    initial_state: PlannedOperationalState
    runtime_manifest: dict


def _function_source(function):
    if (not inspect.isfunction(function) or function.__qualname__ != function.__name__
            or function.__name__.startswith('<') or inspect.iscoroutinefunction(function)
            or inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function)):
        raise TypeError("preparation must be a synchronous module-level Python function (no closure or bound method)")
    source = inspect.getsourcefile(function)
    if source is None or not Path(source).is_file():
        raise ValueError("preparation requires a readable Python source file")
    inspect.signature(function).bind(object())
    return Path(source).resolve(), function.__name__, function.__globals__.get('__package__') or ''


def _package_root(source, package):
    root = source.parent
    for _ in package.split('.') if package else ():
        root = root.parent
    return root


def _captured_function(source, name, package):
    if not package:
        return runpy.run_path(str(source), run_name='_skyseed_preparation')[name]

    root = _package_root(source, package)
    sys.path.insert(0, str(root))
    top = package.split('.')[0]
    # Resolve the author package only within captured inputs, including when its
    # __init__.py was not listed (a namespace package). An installed or already
    # imported original must not override captured relative dependencies.
    for cached in tuple(sys.modules):
        if cached == top or cached.startswith(top + '.'):
            del sys.modules[cached]
    spec = PathFinder.find_spec(top, [str(root)])
    if spec is None:
        raise ImportError(f'captured preparation package is unavailable: {top}')
    module = module_from_spec(spec)
    sys.modules[top] = module
    if spec.loader is not None:
        spec.loader.exec_module(module)
    module_name = package if source.name == '__init__.py' else package + '.' + source.stem
    return getattr(importlib.import_module(module_name), name)


def _read_context(output, invocation):
    import gzip
    from skyseed.simulator import apply_plan_transition, plan_transition_from_data
    provenance = json.loads((output / 'inputs/provenance.json').read_text())
    spec = json.loads((output / 'inputs/preparation.json').read_text())
    handoff = json.loads((invocation / 'runner/prepared.json').read_text())
    if handoff.get('artifact_type') != 'runner-inspection-handoff' or handoff.get('format_version') != 1:
        raise ValueError('unsupported prepared Runner handoff')
    with gzip.open(output / 'results/planner/planning.jsonl.gz', 'rt') as stream:
        header = json.loads(next(stream))
        if header.get('codec') != 'skyseed-v2-planning-evidence' or header.get('format_version') != 2:
            raise ValueError('unsupported initial Planner history')
        first = json.loads(next(stream))
    state = apply_plan_transition(None, plan_transition_from_data(first['data']['plan']['transitions'][0]))
    return PreparationContext(output, invocation, spec['inputs'],
        {row['source']: output / 'inputs' / row['snapshot'] for row in provenance['sources']},
        handoff['runner_snapshot'], state,
        json.loads((output / 'results/compiler/runner-artifact/manifest.json').read_text()))


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--function-file', required=True, type=Path)
    parser.add_argument('--function-name', required=True)
    parser.add_argument('--function-package', default='')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--invocation-directory', required=True, type=Path)
    args = parser.parse_args(argv)
    output, invocation = args.output, args.invocation_directory
    context = _read_context(output, invocation)
    # Captured packages and sibling modules precede live source imports. Authors
    # list local dependencies/data in input_files; installed packages stay external.
    source = args.function_file.resolve()
    sys.path[:0] = [str(source.parent), str(output / 'inputs/sources')]
    _captured_function(source, args.function_name, args.function_package)(context)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
