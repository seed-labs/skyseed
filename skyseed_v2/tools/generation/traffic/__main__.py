"""Render the generated traffic preview; this command never sends packets."""
import argparse
from pathlib import Path
from .preview import write_traffic_preview

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='completed Example output')
    args = parser.parse_args()
    write_traffic_preview(args.output)
