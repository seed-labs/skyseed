"""Pure Skyseed v1 command dispatcher."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


CANONICAL_EXAMPLES = {
    "s4_operational_clean": "rich_s4_operational_clean_bpf",
    "s5_operational_clean": "rich_s5_operational_clean_bpf",
}


def _run_example(args: argparse.Namespace) -> int:
    from skyseed_v1.runtime.profile import main as run_profile

    forwarded = ["--profile", CANONICAL_EXAMPLES[args.example]]
    if args.run_root:
        forwarded.extend(["--run-root", args.run_root])
    forwarded.extend(["--format", args.format])
    if args.run_id:
        forwarded.extend(["--run-id", args.run_id])
    if args.dry_run:
        forwarded.append("--dry-run")
    if args.load_frozen_prepared_bundle is not None:
        forwarded.extend(
            ["--load-frozen-prepared-bundle", str(args.load_frozen_prepared_bundle)]
        )
    if args.progress != "auto":
        forwarded.extend(["--progress", args.progress])
    return run_profile(forwarded)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skyseed-v1")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-examples", help="List canonical examples.")
    list_parser.set_defaults(handler=None)

    run_parser = subparsers.add_parser("run", help="Run a canonical example.")
    run_parser.add_argument("--example", choices=tuple(CANONICAL_EXAMPLES), required=True)
    run_parser.add_argument("--run-root", default=None)
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--load-frozen-prepared-bundle", type=Path, default=None)
    run_parser.add_argument("--format", choices=("text", "json"), default="text")
    run_parser.add_argument(
        "--progress", choices=("auto", "plain", "off"), default="auto"
    )
    run_parser.set_defaults(handler=_run_example)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list-examples":
        for name in sorted(CANONICAL_EXAMPLES):
            print(name)
        return 0
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
