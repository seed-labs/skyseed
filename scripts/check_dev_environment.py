"""Report the selected environment, import sources and required SEED capability."""

from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import sys


EXPECTED_ENVIRONMENT = "skyseed_dev"
REQUIRED_IMPORTS = ("astropy", "skyseed", "seedemu", "yaml", "pytest")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=os.environ.get("SKYSEED_ENV_PREFIX"),
                        help="explicit isolated Conda prefix (default: skyseed_dev)")
    args = parser.parse_args()
    environment = os.environ.get("CONDA_DEFAULT_ENV", "")
    prefix = os.environ.get("CONDA_PREFIX", "")
    print(f"CONDA_DEFAULT_ENV={environment or '<unset>'}")
    print(f"CONDA_PREFIX={prefix or '<unset>'}")
    print(f"python={sys.executable}")
    print(f"python_version={sys.version.split()[0]}")

    failures: list[str] = []
    if args.prefix:
        expected = Path(args.prefix).expanduser().resolve()
        if not prefix or Path(prefix).resolve() != expected or Path(sys.prefix).resolve() != expected:
            failures.append(f"expected interpreter and CONDA_PREFIX at {expected}")
    else:
        if environment != EXPECTED_ENVIRONMENT:
            failures.append(f"expected CONDA_DEFAULT_ENV={EXPECTED_ENVIRONMENT}, got {environment or '<unset>'}")
        if not prefix or Path(prefix).name != EXPECTED_ENVIRONMENT:
            failures.append(f"CONDA_PREFIX does not identify {EXPECTED_ENVIRONMENT}: {prefix or '<unset>'}")
        if not prefix or Path(sys.prefix).resolve() != Path(prefix).resolve():
            failures.append("Python does not belong to the active Conda prefix")

    for module_name in REQUIRED_IMPORTS:
        try:
            module = importlib.import_module(module_name)
        except Exception as error:
            failures.append(f"{module_name} import failed: {error}")
            continue
        version = getattr(module, "__version__", "unknown")
        location = getattr(module, "__file__", "built-in")
        print(f"{module_name}=importable version={version} path={location}")

    try:
        from seedemu.core import AutonomousSystem, ExtensionNode, Node, ScopeType
        from seedemu.core.enums import NodeRole

        assert issubclass(ExtensionNode, Node)
        node = ExtensionNode("environment-check", 65001)
        system = AutonomousSystem(65001)
        assert system.addExtensionNode(node) is node
        assert system.getExtensionNode(node.getName()) is node
        assert node.getRole() == NodeRole.ExtensionNode
        assert node.scope().type == ScopeType.EXTNODE
        print("seedemu_extension_node=PASS")
    except Exception as error:
        failures.append(f"required SEED ExtensionNode capability unavailable: {error}; "
                        "install EMLionHeart/seed-emulator development")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("environment_status=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
