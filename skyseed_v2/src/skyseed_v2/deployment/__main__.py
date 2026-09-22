"""Independent controls for an already generated deployment."""
import argparse
import json
from pathlib import Path
from . import Deployer
from contextlib import nullcontext
from skyseed_v2.experiment.output.occupancy import claim_output, claim_runtime
from skyseed_v2.shared._console import _console


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('operation', choices=('build', 'start', 'status', 'cleanup'))
    args = parser.parse_args()
    cleanup = args.operation == 'cleanup'
    if cleanup:
        _console(f'Deployment cleanup starting: {args.output}')
    try:
        with claim_output(args.output), (claim_runtime(args.output) if args.operation != "status" else nullcontext()):
            result = getattr(Deployer(args.output), args.operation)()
    except BaseException as error:
        if cleanup:
            _console(f'Deployment cleanup failed: {type(error).__name__}: {error}')
        raise
    if cleanup:
        _console('Deployment cleanup succeeded.')
    if result is not None:
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
