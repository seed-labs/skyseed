"""Render performance from one completed experiment publication and runtime record."""
import argparse
import json
from pathlib import Path

from skyseed_v2.experiment.output.history import _reading_completed_range
from . import write_performance_tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generated', type=Path, required=True)
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    publication = json.loads((args.generated / 'results/experiment/completed.json').read_text())
    with _reading_completed_range(args.generated, publication):
        write_performance_tool(args.observations, args.output)


if __name__ == '__main__':
    main()
