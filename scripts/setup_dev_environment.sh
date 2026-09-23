#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENVIRONMENT_NAME="skyseed_dev"
# An explicit prefix isolates release checks from the ordinary development env.
if [[ -n "${SKYSEED_ENV_PREFIX:-}" ]]; then
    ENVIRONMENT_ARGS=(--prefix "$SKYSEED_ENV_PREFIX")
else
    ENVIRONMENT_ARGS=(--name "$ENVIRONMENT_NAME")
fi
DEFAULT_SEED_EMULATOR_ROOT="$(cd -- "$PROJECT_ROOT/.." && pwd)/seed-emulator"
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

if [[ ! -f "$SEED_EMULATOR_ROOT/setup.py" ]]; then
    echo "Required SEED ExtensionNode checkout not found: $SEED_EMULATOR_ROOT" >&2
    echo "Clone https://github.com/EMLionHeart/seed-emulator (development)." >&2
    echo "Set SEED_EMULATOR_CHECKOUT to that checkout; see docs/user-guides/getting-started.md." >&2
    exit 1
fi
export SEED_EMULATOR_CHECKOUT="$SEED_EMULATOR_ROOT"

"$CONDA_COMMAND" --version
if { [[ -n "${SKYSEED_ENV_PREFIX:-}" ]] && [[ -f "$SKYSEED_ENV_PREFIX/conda-meta/history" ]]; } ||
   { [[ -z "${SKYSEED_ENV_PREFIX:-}" ]] && "$CONDA_COMMAND" env list | awk '{print $1}' | grep -Fxq "$ENVIRONMENT_NAME"; }; then
    "$CONDA_COMMAND" env update "${ENVIRONMENT_ARGS[@]}" --file environment.yml --prune
else
    "$CONDA_COMMAND" env create "${ENVIRONMENT_ARGS[@]}" --file environment.yml
fi

echo "Installing required SEED fork from $SEED_EMULATOR_ROOT"
"$CONDA_COMMAND" run "${ENVIRONMENT_ARGS[@]}" \
    python -m pip install --editable "$SEED_EMULATOR_ROOT"
"$CONDA_COMMAND" run "${ENVIRONMENT_ARGS[@]}" \
    python -m pip install --editable '.[test]'

echo "SkySEED development environment is ready: ${SKYSEED_ENV_PREFIX:-$ENVIRONMENT_NAME}"
"$CONDA_COMMAND" run "${ENVIRONMENT_ARGS[@]}" python -m pip --version
"$CONDA_COMMAND" run "${ENVIRONMENT_ARGS[@]}" python scripts/check_dev_environment.py
