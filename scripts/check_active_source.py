"""Verify the selected checkout before importing application code.

The source root comes from packaging metadata, so the final directory rename
does not require a second source-root convention.
"""
from importlib.util import find_spec
from pathlib import Path
import os
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def verify_source(*, report=False):
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    roots = metadata["tool"]["setuptools"]["packages"]["find"]["where"]
    if len(roots) != 1:
        raise RuntimeError("SkySEED requires exactly one active source root")
    expected = (ROOT / roots[0] / "skyseed").resolve()
    spec = find_spec("skyseed")
    actual = Path(spec.origin).resolve() if spec and spec.origin else None
    if actual != expected / "__init__.py":
        raise RuntimeError(
            f"Wrong SkySEED source: {actual}; expected {expected / '__init__.py'}. "
            "Reinstall this checkout in the selected interpreter; no legacy fallback is allowed."
        )
    for name, module in tuple(sys.modules.items()):
        if name == "skyseed" or name.startswith("skyseed."):
            paths = list(getattr(module, "__path__", ()))
            origin = getattr(module, "__file__", None)
            if origin:
                paths.append(origin)
            if any(not Path(path).resolve().is_relative_to(expected) for path in paths):
                raise RuntimeError(f"Mixed SkySEED source in {name}: {paths}")
    if report:
        print(f"Python: {sys.executable}\nVersion: {sys.version.split()[0]}")
        print(f"Environment: {sys.prefix}; CONDA_PREFIX={os.getenv('CONDA_PREFIX')}")
        print(f"SkySEED source: {actual}")
    return expected


if __name__ == "__main__":
    verify_source(report=True)
