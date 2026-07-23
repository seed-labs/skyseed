"""Private runtime preparation shared by directly executed examples."""

from __future__ import annotations

from importlib.util import find_spec


def prepare_direct_file_execution(script_file: str) -> None:
    """Fail clearly when the selected interpreter lacks project dependencies."""

    if find_spec("seedemu") is not None and find_spec("skyseed_v2") is not None:
        return

    raise SystemExit(
        f"seedemu and skyseed_v2 are unavailable to the Python interpreter "
        f"selected for {script_file}. The recommended local environment is "
        "skyseed_dev. From the repository root, run "
        "scripts/setup_dev_environment.sh, then run "
        "conda activate skyseed_dev. A correctly configured venv or CI "
        "environment is also supported."
    )
