from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
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
    assert "skyseed" in check
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


def test_setup_resolves_default_seed_checkout_as_repository_sibling(tmp_path):
    project = tmp_path / 'skyseed'
    scripts = project / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / 'scripts/setup_dev_environment.sh', scripts)
    seed = tmp_path / 'seed-emulator'
    seed.mkdir()
    (seed / 'setup.py').touch()
    record = tmp_path / 'seed-paths'
    conda = tmp_path / 'record-conda'
    conda.write_text('#!/bin/bash\n'
        'printf "%s\\n" "$SEED_EMULATOR_CHECKOUT" >> "$MIGRATION_SEED_RECORD"\n'
        'if [[ "$1 $2" == "env list" ]]; then echo skyseed_dev; fi\n')
    conda.chmod(0o755)
    env = {key: value for key, value in os.environ.items()
           if key not in {'SEED_EMULATOR_CHECKOUT', 'SKYSEED_ENV_PREFIX'}}
    env.update(PATH='/usr/bin:/bin', CONDA_EXE=str(conda), MIGRATION_SEED_RECORD=str(record))
    completed = run(['bash', str(scripts/'setup_dev_environment.sh')],
                    cwd=tmp_path, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert record.read_text().splitlines()
    assert set(record.read_text().splitlines()) == {str(seed)}


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
