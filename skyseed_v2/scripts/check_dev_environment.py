"""Report and validate the canonical local Skyseed development environment."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys


EXPECTED_ENVIRONMENT = "skyseed_dev"
REQUIRED_IMPORTS = ("astropy", "skyseed_v2", "seedemu", "yaml", "pytest")


def main() -> int:
    environment = os.environ.get("CONDA_DEFAULT_ENV", "")
    prefix = os.environ.get("CONDA_PREFIX", "")
    print(f"CONDA_DEFAULT_ENV={environment or '<unset>'}")
    print(f"CONDA_PREFIX={prefix or '<unset>'}")
    print(f"python={sys.executable}")
    print(f"python_version={sys.version.split()[0]}")

    failures: list[str] = []
    if environment != EXPECTED_ENVIRONMENT:
        failures.append(
            f"expected CONDA_DEFAULT_ENV={EXPECTED_ENVIRONMENT}, got "
            f"{environment or '<unset>'}"
        )
    if not prefix or Path(prefix).name != EXPECTED_ENVIRONMENT:
        failures.append(
            f"CONDA_PREFIX does not identify {EXPECTED_ENVIRONMENT}: "
            f"{prefix or '<unset>'}"
        )

    for module_name in REQUIRED_IMPORTS:
        try:
            module = importlib.import_module(module_name)
        except Exception as error:
            failures.append(f"{module_name} import failed: {error}")
            continue
        version = getattr(module, "__version__", "unknown")
        location = getattr(module, "__file__", "built-in")
        print(f"{module_name}=importable version={version} path={location}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("environment_status=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
