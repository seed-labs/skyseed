"""Copy/edit this driver; every operation is opt-in and independent of main."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from templates.dataplane.threshold.definition import DATA
from skyseed_v2.backend.bpf.dataplane import BpfProgram
from tools.development import Check, check_model, check_program, check_scene, show

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('headers', 'model', 'scene', 'continue', 'c', 'materials'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generated = args.output / 'generated'
    program = BpfProgram(HERE / 'handler.bpf.c', data=DATA, authoring_directory=generated)
    if args.operation == 'headers':
        # This branch does not import the unfinished Model or read C sources.
        checks = (Check('pass', 'headers', str(DATA.generate_headers(generated))),)
    elif args.operation == 'c':
        checks = check_program(program)
    else:
        from templates.dataplane.threshold.scene import ThresholdRoutes, create_scene, START
        from skyseed_v2.simulator import RoutePlanningModel
        from datetime import timedelta
        if args.operation == 'model':
            checks = check_model(ThresholdRoutes(128), RoutePlanningModel)
        else:
            checks = check_scene(lambda: create_scene(ThresholdRoutes(128)),
                stop_at='materials' if args.operation == 'materials' else 'planner', output=args.output,
                continuation_end=START + timedelta(seconds=15) if args.operation == 'continue' else None,
                dataplane_program=program)
    raise SystemExit(0 if show(checks) else 1)


if __name__ == '__main__':
    main()
