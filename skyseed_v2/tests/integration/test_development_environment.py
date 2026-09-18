from __future__ import annotations

import ast
from pathlib import Path
from subprocess import run

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_canonical_environment_declaration_is_small_and_portable() -> None:
    declaration = yaml.safe_load(
        (PROJECT_ROOT / "environment.yml").read_text(encoding="utf-8")
    )

    assert declaration == {
        "name": "skyseed_dev",
        "channels": ["conda-forge", "nodefaults"],
        "dependencies": ["python=3.12", "pip"],
    }


def test_environment_setup_and_check_scripts_define_one_canonical_path() -> None:
    setup = (PROJECT_ROOT / "scripts/setup_dev_environment.sh").read_text(
        encoding="utf-8"
    )
    check = (PROJECT_ROOT / "scripts/check_dev_environment.py").read_text(
        encoding="utf-8"
    )

    assert "skyseed_dev" in setup
    assert '"$CONDA_COMMAND" env create' in setup
    assert '"$CONDA_COMMAND" env update' in setup
    assert '$HOME/miniconda3/bin/conda' in setup
    assert '$HOME/anaconda3/bin/conda' in setup
    assert "SEED_EMULATOR_CHECKOUT" in setup
    assert "python -m pip install --editable" in setup
    assert "python -m pip --version" in setup
    assert "skyseed_dev" in check
    assert "CONDA_DEFAULT_ENV" in check
    assert "CONDA_PREFIX" in check
    assert "skyseed_v2" in check
    assert "seedemu" in check
    assert "pytest" in check
    assert "astropy" in check
    assert (
        run(
            ("bash", "-n", str(PROJECT_ROOT / "scripts/setup_dev_environment.sh")),
            check=False,
        ).returncode
        == 0
    )
    ast.parse(check)


def test_vscode_settings_activate_but_do_not_pin_a_machine_path() -> None:
    settings = yaml.safe_load(
        (PROJECT_ROOT / ".vscode/settings.json").read_text(encoding="utf-8")
    )

    assert settings == {
        "python.terminal.activateEnvironment": True,
        "python.interpreter.infoVisibility": "always",
    }
    assert not any("pythonPath" in key for key in settings)
    assert "/home/" not in (
        PROJECT_ROOT / ".vscode/settings.json"
    ).read_text(encoding="utf-8")
