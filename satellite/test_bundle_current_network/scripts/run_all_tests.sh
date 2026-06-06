#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_ROOT="${BUNDLE_DIR}/logs"
RUN_ID="$(date '+%Y%m%d_%H%M%S')"
RUN_DIR="${LOG_ROOT}/${RUN_ID}"

mkdir -p "${RUN_DIR}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

run_and_capture() {
  local name="$1"
  shift
  local output_file="$1"
  shift
  log "Running ${name}"
  if "$@" >"${output_file}" 2>&1; then
    log "PASS ${name}"
    return 0
  fi
  log "FAIL ${name}"
  return 1
}

HOST_PREFLIGHT_LOG="${RUN_DIR}/host_preflight.log"
OFFLINE_STDOUT_LOG="${RUN_DIR}/offline_validation.stdout.log"
OFFLINE_SUMMARY_JSON="${RUN_DIR}/offline_validation_summary.json"
REAL_NETWORK_LOG="${RUN_DIR}/real_network_checks.log"
MANIFEST_FILE="${RUN_DIR}/run_manifest.txt"

STATUS_PREFLIGHT="pass"
STATUS_OFFLINE="pass"
STATUS_REAL="pass"
OVERALL_STATUS="pass"

run_and_capture \
  "host preflight" \
  "${HOST_PREFLIGHT_LOG}" \
  bash "${SCRIPT_DIR}/run_host_preflight.sh" || STATUS_PREFLIGHT="fail"

run_and_capture \
  "offline validation" \
  "${OFFLINE_STDOUT_LOG}" \
  python3 "${SCRIPT_DIR}/run_offline_validation.py" \
    --summary-out "${OFFLINE_SUMMARY_JSON}" || STATUS_OFFLINE="fail"

run_and_capture \
  "real network checks" \
  "${REAL_NETWORK_LOG}" \
  bash "${SCRIPT_DIR}/run_real_network_checks.sh" || STATUS_REAL="fail"

if [[ "${STATUS_PREFLIGHT}" != "pass" || "${STATUS_OFFLINE}" != "pass" || "${STATUS_REAL}" != "pass" ]]; then
  OVERALL_STATUS="fail"
fi

{
  printf 'run_id=%s\n' "${RUN_ID}"
  printf 'run_dir=%s\n' "${RUN_DIR}"
  printf 'host_preflight=%s\n' "${STATUS_PREFLIGHT}"
  printf 'offline_validation=%s\n' "${STATUS_OFFLINE}"
  printf 'real_network_checks=%s\n' "${STATUS_REAL}"
  printf 'overall_status=%s\n' "${OVERALL_STATUS}"
} >"${MANIFEST_FILE}"

log "Logs written to ${RUN_DIR}"
log "Manifest: ${MANIFEST_FILE}"

if [[ "${OVERALL_STATUS}" != "pass" ]]; then
  exit 1
fi

exit 0
