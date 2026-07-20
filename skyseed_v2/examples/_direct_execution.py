"""Private runtime preparation shared by directly executed examples."""

from __future__ import annotations

from importlib.util import find_spec
import os
from pathlib import Path
import sys


def prepare_direct_file_execution(script_file: str) -> None:
    """Use the repository environment when dependencies are not installed."""

    if find_spec("seedemu") is not None and find_spec("skyseed_v2") is not None:
        return

    project_root = Path(script_file).resolve().parents[2]
    repository_python = project_root.parent / ".venv" / "bin" / "python"
    if repository_python.is_file() and (
        Path(sys.executable).absolute() != repository_python.absolute()
    ):
        os.execv(
            str(repository_python),
            [
                str(repository_python),
                str(Path(script_file).resolve()),
                *sys.argv[1:],
            ],
        )

    raise SystemExit(
        "seedemu and skyseed_v2 are unavailable; activate an environment "
        "where this project is installed"
    )
