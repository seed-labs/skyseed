#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${BUNDLE_DIR}/../.." && pwd)"
SATELLITE_DIR="${REPO_ROOT}/satellite"
CONFIG_DIR="${SATELLITE_DIR}/configs/iteration2_small_scale"
OUTPUT_DIR="${SATELLITE_DIR}/output/iteration2_small_scale"
COMPOSE_FILE="${OUTPUT_DIR}/docker-compose.yml"
SEED_EMULATOR_DIR="/home/seed/seed-emulator-upstream-dev"
SEED_EMULATOR_REMOTE="https://github.com/seed-labs/seed-emulator.git"
SEED_EMULATOR_BRANCH="development2"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

require_file() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    log "OK file: ${path}"
  else
    log "ERROR missing file: ${path}"
    return 1
  fi
}

require_dir() {
  local path="$1"
  if [[ -d "${path}" ]]; then
    log "OK dir: ${path}"
  else
    log "ERROR missing dir: ${path}"
    return 1
  fi
}

optional_generated_file() {
  local path="$1"
  local generator="$2"
  if [[ -f "${path}" ]]; then
    log "OK generated file: ${path}"
  else
    log "INFO generated file not present: ${path}"
    log "INFO generate it with: ${generator}"
  fi
}

optional_generated_dir() {
  local path="$1"
  local generator="$2"
  if [[ -d "${path}" ]]; then
    log "OK generated dir: ${path}"
  else
    log "INFO generated dir not present: ${path}"
    log "INFO generate it with: ${generator}"
  fi
}

log "Satellite Iteration2 host preflight started"
log "repo_root=${REPO_ROOT}"
log "satellite_dir=${SATELLITE_DIR}"
log "bundle_dir=${BUNDLE_DIR}"

log "Checking core files and directories"
require_dir "${SATELLITE_DIR}"
require_dir "${CONFIG_DIR}"
require_file "${CONFIG_DIR}/topology.json"
require_file "${CONFIG_DIR}/link_state.json"
require_file "${SATELLITE_DIR}/apps/iteration2_satellite_experiment.py"
require_file "${SATELLITE_DIR}/apps/build_iteration2_example_topology.py"
optional_generated_dir \
  "${OUTPUT_DIR}" \
  "python3 ${SATELLITE_DIR}/apps/build_iteration2_example_topology.py"
optional_generated_file \
  "${COMPOSE_FILE}" \
  "python3 ${SATELLITE_DIR}/apps/build_iteration2_example_topology.py"

log "Checking Python and local dependency wiring"
env -u PYTHONPATH python3 --version
require_dir "${SEED_EMULATOR_DIR}"
SEED_IMPORT_PATH="$(
  env -u PYTHONPATH python3 - <<'PY'
from pathlib import Path
import seedemu
print(Path(seedemu.__file__).resolve())
PY
)"
log "seedemu=${SEED_IMPORT_PATH}"
if [[ "${SEED_IMPORT_PATH}" != "${SEED_EMULATOR_DIR}/seedemu/"* ]]; then
  log "ERROR seedemu import is not from ${SEED_EMULATOR_DIR}"
  exit 1
fi
SEED_BRANCH="$(git -C "${SEED_EMULATOR_DIR}" branch --show-current)"
SEED_ORIGIN="$(git -C "${SEED_EMULATOR_DIR}" remote get-url origin)"
log "seedemu_origin=${SEED_ORIGIN}"
log "seedemu_branch=${SEED_BRANCH}"
if [[ "${SEED_ORIGIN}" != "${SEED_EMULATOR_REMOTE}" ]]; then
  log "ERROR seedemu origin should be ${SEED_EMULATOR_REMOTE}"
  exit 1
fi
if [[ "${SEED_BRANCH}" != "${SEED_EMULATOR_BRANCH}" ]]; then
  log "ERROR seedemu branch should be ${SEED_EMULATOR_BRANCH}"
  exit 1
fi
env -u PYTHONPATH python3 "${SATELLITE_DIR}/apps/iteration2_satellite_experiment.py" --help >/dev/null
log "OK iteration2 app import smoke"

log "Checking Docker toolchain"
docker --version
docker-compose version

log "Checking current container state"
RUNNING_CONTAINERS="$(docker ps --format '{{.Names}}\t{{.Status}}')"
if [[ -n "${RUNNING_CONTAINERS}" ]]; then
  printf '%s\n' "${RUNNING_CONTAINERS}"
else
  log "INFO no running containers detected"
fi

REQUIRED_NAMES=(
  ut1
  ut2
  sat-a1
  sat-a2
  sat-b1
  gw1
  gw2
  as154-host-new
  as160-host1
)

RUNNING_NAMES="$(docker ps --format '{{.Names}}')"
FOUND_COUNT=0
for name in "${REQUIRED_NAMES[@]}"; do
  if printf '%s\n' "${RUNNING_NAMES}" | grep -qx "${name}"; then
    log "RUNNING ${name}"
    FOUND_COUNT=$((FOUND_COUNT + 1))
  else
    log "NOT-RUNNING ${name}"
  fi
done

log "Summary: ${FOUND_COUNT}/${#REQUIRED_NAMES[@]} key containers running"
log "Host preflight finished successfully"
