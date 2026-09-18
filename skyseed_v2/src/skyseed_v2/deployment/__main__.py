"""Independent controls for an already generated deployment."""
import argparse
import json
from pathlib import Path
from . import Deployer
from contextlib import nullcontext
from skyseed_v2.experiment.output.occupancy import claim_output, claim_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('operation', choices=('build', 'start', 'status', 'cleanup'))
    args = parser.parse_args()
    with claim_output(args.output), (claim_runtime(args.output) if args.operation != "status" else nullcontext()):
        result = getattr(Deployer(args.output), args.operation)()
    if result is not None:
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
