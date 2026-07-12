"""Paths owned by the standalone Skyseed v1 project."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _git_checkout_root() -> Path:
    for candidate in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        if (candidate / ".git").exists():
            return candidate
    return PROJECT_ROOT


REPOSITORY_ROOT = _git_checkout_root()
SOURCE_ROOT = PROJECT_ROOT / "src"
PROJECT_RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
PROJECT_CACHE_ROOT = PROJECT_ROOT / ".cache"
PROJECT_OUTPUT_ROOT = PROJECT_ROOT / "output"
SEED_EMULATOR_DEPENDENCY_ROOT = Path(
    os.environ.get(
        "SKYSEED_SEEDEMU_ROOT",
        str(REPOSITORY_ROOT.parent / "seed-emulator-upstream-dev"),
    )
).expanduser().resolve()
LEGACY_PYTHONPATH_ENTRIES = {str(Path("/home/seed/seed-emulator").resolve())}


def bootstrap_project_imports() -> Path:
    """Prefer the selected SEED checkout and the canonical v1 source root."""

    for path in (
        SEED_EMULATOR_DEPENDENCY_ROOT,
        SOURCE_ROOT,
        PROJECT_ROOT,
    ):
        rendered = str(path)
        if not path.exists():
            continue
        if rendered in sys.path:
            sys.path.remove(rendered)
        sys.path.insert(0, rendered)
    return PROJECT_ROOT


def prepare_user_entry_environment() -> tuple[str, ...]:
    """Select canonical imports and remove only known retired path overrides."""

    removed: list[str] = []
    retained: list[str] = []
    for raw_entry in str(os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        entry = raw_entry.strip()
        if not entry:
            continue
        normalized = str(Path(entry).expanduser().resolve(strict=False))
        if normalized in LEGACY_PYTHONPATH_ENTRIES:
            removed.append(entry)
            continue
        if entry not in retained:
            retained.append(entry)
    selected: list[str] = []
    for entry in (str(SOURCE_ROOT), str(SEED_EMULATOR_DEPENDENCY_ROOT), *retained):
        if entry and entry not in selected:
            selected.append(entry)
    os.environ["PYTHONPATH"] = os.pathsep.join(selected)
    os.environ["PYTHONNOUSERSITE"] = "1"
    bootstrap_project_imports()
    return tuple(removed)
