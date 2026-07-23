#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENVIRONMENT_NAME="skyseed_dev"
DEFAULT_SEED_EMULATOR_ROOT="$(cd -- "$PROJECT_ROOT/../.." && pwd)/seed-emulator"
SEED_EMULATOR_ROOT="${SEED_EMULATOR_CHECKOUT:-$DEFAULT_SEED_EMULATOR_ROOT}"

cd "$PROJECT_ROOT"

if command -v conda >/dev/null 2>&1; then
    CONDA_COMMAND="$(command -v conda)"
elif [[ -n "${CONDA_EXE:-}" && -x "$CONDA_EXE" ]]; then
    CONDA_COMMAND="$CONDA_EXE"
elif [[ -x "$HOME/miniconda3/bin/conda" ]]; then
    CONDA_COMMAND="$HOME/miniconda3/bin/conda"
elif [[ -x "$HOME/anaconda3/bin/conda" ]]; then
    CONDA_COMMAND="$HOME/anaconda3/bin/conda"
else
    echo "conda is unavailable; install Conda and make its shell command available" >&2
    exit 1
fi

"$CONDA_COMMAND" --version
if "$CONDA_COMMAND" env list | awk '{print $1}' | grep -Fxq "$ENVIRONMENT_NAME"; then
    "$CONDA_COMMAND" env update \
        --name "$ENVIRONMENT_NAME" \
        --file environment.yml \
        --prune
else
    "$CONDA_COMMAND" env create --file environment.yml
fi

if [[ ! -f "$SEED_EMULATOR_ROOT/setup.py" ]]; then
    echo "The pinned seedemu dependency is unavailable from the package index." >&2
    echo "Expected a seed-emulator checkout at: $SEED_EMULATOR_ROOT" >&2
    echo "Set SEED_EMULATOR_CHECKOUT to an equivalent checkout path." >&2
    exit 1
fi

echo "Installing pinned seedemu from $SEED_EMULATOR_ROOT"
"$CONDA_COMMAND" run -n "$ENVIRONMENT_NAME" \
    python -m pip install --editable "$SEED_EMULATOR_ROOT"
"$CONDA_COMMAND" run -n "$ENVIRONMENT_NAME" \
    python -m pip install --editable '.[test]'

echo "Skyseed development environment is ready:"
"$CONDA_COMMAND" run -n "$ENVIRONMENT_NAME" python -m pip --version
"$CONDA_COMMAND" run -n "$ENVIRONMENT_NAME" \
    python scripts/check_dev_environment.py
