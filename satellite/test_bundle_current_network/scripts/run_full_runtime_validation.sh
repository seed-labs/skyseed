#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${BUNDLE_DIR}/../.." && pwd)"
SATELLITE_DIR="${REPO_ROOT}/satellite"
OUTPUT_DIR="${SATELLITE_DIR}/output/iteration2_small_scale"
LOG_ROOT="${BUNDLE_DIR}/logs"
RUN_ID="full_runtime_$(date '+%Y%m%d_%H%M%S')"
RUN_DIR="${LOG_ROOT}/${RUN_ID}"

mkdir -p "${RUN_DIR}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

run_capture() {
  local name="$1"
  local outfile="$2"
  shift 2
  log "Running ${name}"
  if "$@" >"${outfile}" 2>&1; then
    log "PASS ${name}"
    return 0
  fi
  log "FAIL ${name} (see ${outfile})"
  return 1
}

run_preclean() {
  printf 'Satellite Iteration2 preclean started at %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  printf 'repo_root=%s\n' "${REPO_ROOT}"
  printf 'output_dir=%s\n' "${OUTPUT_DIR}"

  if [[ -f "${OUTPUT_DIR}/docker-compose.yml" ]]; then
    printf '\n$ cd %q && docker-compose down --remove-orphans -v\n' "${OUTPUT_DIR}"
    (
      cd "${OUTPUT_DIR}"
      docker-compose down --remove-orphans -v
    ) || true
  else
    printf '\nNo existing docker-compose.yml under %s\n' "${OUTPUT_DIR}"
  fi

  for project in iteration2_small_scale iteration2_small_scale_dynamic; do
    printf '\nCleaning compose project label: %s\n' "${project}"
    mapfile -t containers < <(
      docker ps -aq --filter "label=com.docker.compose.project=${project}"
    )
    if [[ ${#containers[@]} -gt 0 ]]; then
      printf '$ docker rm -f %s\n' "${containers[*]}"
      docker rm -f "${containers[@]}" || true
    else
      printf 'No labeled containers found for %s\n' "${project}"
    fi

    mapfile -t networks < <(
      docker network ls -q --filter "label=com.docker.compose.project=${project}"
    )
    if [[ ${#networks[@]} -gt 0 ]]; then
      printf '$ docker network rm %s\n' "${networks[*]}"
      docker network rm "${networks[@]}" || true
    else
      printf 'No labeled networks found for %s\n' "${project}"
    fi

    mapfile -t volumes < <(
      docker volume ls -q --filter "label=com.docker.compose.project=${project}"
    )
    if [[ ${#volumes[@]} -gt 0 ]]; then
      printf '$ docker volume rm %s\n' "${volumes[*]}"
      docker volume rm "${volumes[@]}" || true
    else
      printf 'No labeled volumes found for %s\n' "${project}"
    fi
  done

  printf '\nRemaining project containers after preclean:\n'
  docker ps -a \
    --filter "label=com.docker.compose.project=iteration2_small_scale" \
    --filter "label=com.docker.compose.project=iteration2_small_scale_dynamic" \
    --format '{{.Names}}\t{{.Status}}' || true
  printf '\nSatellite Iteration2 preclean finished at %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
}

prepare_host_mpls() {
  printf 'Host MPLS preparation started at %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  printf 'user=%s\n' "$(whoami)"
  printf '\n$ sudo -n true\n'
  sudo -n true

  for module in mpls_router mpls_iptunnel mpls_gso; do
    printf '\n$ sudo -n modprobe %s\n' "${module}"
    sudo -n modprobe "${module}"
  done

  printf '\n$ sudo -n sysctl -w net.mpls.platform_labels=1048575\n'
  sudo -n sysctl -w net.mpls.platform_labels=1048575

  printf '\nLoaded MPLS modules:\n'
  lsmod | grep '^mpls_' || true
  printf '\nHost MPLS sysctl:\n'
  sysctl net.mpls.platform_labels
  printf '\nHost MPLS route family smoke:\n'
  ip -f mpls route show
  printf '\nHost MPLS preparation finished at %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
}

wait_for_containers() {
  local attempts=30
  local sleep_seconds=2
  local required=(ut1 ut2 sat-a1 sat-a2 sat-a3 sat-b1 sat-b2 gw1 gw2 as154-host-new as160-host1)
  local names missing name
  for ((i=1; i<=attempts; i++)); do
    names="$(docker ps --format '{{.Names}}')"
    missing=()
    for name in "${required[@]}"; do
      if ! printf '%s\n' "${names}" | grep -qx "${name}"; then
        missing+=("${name}")
      fi
    done
    if [[ ${#missing[@]} -eq 0 ]]; then
      return 0
    fi
    log "Waiting for containers (${i}/${attempts}), missing: ${missing[*]}"
    sleep "${sleep_seconds}"
  done
  return 1
}

start_ping_watcher() {
  local target_ip="$1"
  local label="$2"
  local outfile="$3"
  docker exec ut1 sh -lc "
while true; do
  ts=\$(date +%s.%3N)
  out=\$(ping -n -c 1 -W 1 ${target_ip} 2>&1 || true)
  if printf '%s\n' \"\$out\" | grep -q 'bytes from'; then
    status=success
  elif printf '%s\n' \"\$out\" | grep -q 'Network is unreachable'; then
    status=unreachable
  elif printf '%s\n' \"\$out\" | grep -q '100% packet loss'; then
    status=loss
  else
    status=other
  fi
  last_line=\$(printf '%s\n' \"\$out\" | tail -n 1 | tr '\t' ' ')
  printf '%s %s %s %s\n' \"\$ts\" \"${label}\" \"\$status\" \"\$last_line\"
  sleep 0.2
done
" >"${outfile}" 2>&1 &
  echo $!
}

stop_watcher() {
  local pid="${1:-0}"
  if [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}" >/dev/null 2>&1 || true
    wait "${pid}" 2>/dev/null || true
  fi
}

BUILD_LOG="${RUN_DIR}/build_topology.log"
PRECLEAN_LOG="${RUN_DIR}/environment_preclean.log"
HOST_MPLS_LOG="${RUN_DIR}/host_mpls_capability.log"
COMPOSE_UP_LOG="${RUN_DIR}/docker_compose_up.log"
CONTAINER_BASELINE_LOG="${RUN_DIR}/container_baseline.log"
PRE_ROUTE_LOG="${RUN_DIR}/pre_runtime_ut1_route.log"
PRE_PING_AS160_LOG="${RUN_DIR}/pre_runtime_ut1_ping_as160.log"
PRE_PING_AS154_LOG="${RUN_DIR}/pre_runtime_ut1_ping_as154.log"
REAL_STDOUT_JSON="${RUN_DIR}/continuous_real.stdout.json"
REAL_STDERR_LOG="${RUN_DIR}/continuous_real.progress.log"
WATCHER_AS160_LOG="${RUN_DIR}/watcher_ut1_as160.log"
WATCHER_AS154_LOG="${RUN_DIR}/watcher_ut1_as154.log"
POST_ROUTE_LOG="${RUN_DIR}/post_runtime_ut1_route.log"
POST_PING_AS160_LOG="${RUN_DIR}/post_runtime_ut1_ping_as160.log"
SUMMARY_JSON="${RUN_DIR}/full_runtime_summary.json"
COMPOSE_DOWN_LOG="${RUN_DIR}/docker_compose_down.log"
MANIFEST_FILE="${RUN_DIR}/run_manifest.txt"

WATCHER_AS160_PID=""
WATCHER_AS154_PID=""
COMPOSE_STARTED="no"
OVERALL_STATUS="pass"

cleanup() {
  stop_watcher "${WATCHER_AS160_PID:-0}"
  stop_watcher "${WATCHER_AS154_PID:-0}"
  if [[ "${COMPOSE_STARTED}" == "yes" ]]; then
    if docker-compose down >"${COMPOSE_DOWN_LOG}" 2>&1; then
      log "Compose stack stopped"
    else
      log "Compose stack stop failed (see ${COMPOSE_DOWN_LOG})"
      OVERALL_STATUS="fail"
    fi
  fi
  {
    printf 'run_id=%s\n' "${RUN_ID}"
    printf 'run_dir=%s\n' "${RUN_DIR}"
    printf 'overall_status=%s\n' "${OVERALL_STATUS}"
    printf 'continuous_real_json=%s\n' "${REAL_STDOUT_JSON}"
    printf 'continuous_real_progress=%s\n' "${REAL_STDERR_LOG}"
    printf 'summary_json=%s\n' "${SUMMARY_JSON}"
  } >"${MANIFEST_FILE}"
}

mark_failure() {
  OVERALL_STATUS="fail"
}

trap mark_failure ERR
trap cleanup EXIT

run_capture "environment preclean" "${PRECLEAN_LOG}" \
  run_preclean

run_capture "prepare host MPLS" "${HOST_MPLS_LOG}" \
  prepare_host_mpls

run_capture "build iteration2 topology" "${BUILD_LOG}" \
  env -u PYTHONPATH python3 "${SATELLITE_DIR}/apps/build_iteration2_example_topology.py"

cd "${OUTPUT_DIR}"

run_capture "docker-compose up" "${COMPOSE_UP_LOG}" \
  docker-compose up -d
COMPOSE_STARTED="yes"

if ! wait_for_containers; then
  log "Container startup did not complete in time"
  OVERALL_STATUS="fail"
  exit 1
fi

run_capture "container baseline" "${CONTAINER_BASELINE_LOG}" \
  docker ps --format '{{.Names}}\t{{.Status}}'
run_capture "pre-runtime ut1 route" "${PRE_ROUTE_LOG}" \
  docker exec ut1 ip route
run_capture "pre-runtime ut1 ping as160" "${PRE_PING_AS160_LOG}" \
  bash -lc "docker exec ut1 ping -n -c 1 -W 1 10.160.0.72 || true"
run_capture "pre-runtime ut1 ping as154" "${PRE_PING_AS154_LOG}" \
  bash -lc "docker exec ut1 ping -n -c 1 -W 1 10.154.0.129 || true"

WATCHER_AS160_PID="$(start_ping_watcher "10.160.0.72" "ut1_to_as160" "${WATCHER_AS160_LOG}")"
WATCHER_AS154_PID="$(start_ping_watcher "10.154.0.129" "ut1_to_as154" "${WATCHER_AS154_LOG}")"
sleep 1

log "Running continuous real execution across all slices"
if ! env -u PYTHONPATH python3 "${SATELLITE_DIR}/apps/iteration2_satellite_experiment.py" \
  --format json \
  --execute-real \
  --continuous-real \
  --runtime-mode fast-demo \
  --loop-count 1 \
  --interval 2 \
  --max-node-workers 2 \
  --observe-flow ut1:as160-host1 \
  --observe-flow ut1:as154-host-new \
  --stream-progress \
  >"${REAL_STDOUT_JSON}" 2>"${REAL_STDERR_LOG}"; then
  log "Continuous real execution failed"
  OVERALL_STATUS="fail"
  exit 1
fi

sleep 1
stop_watcher "${WATCHER_AS160_PID}"
stop_watcher "${WATCHER_AS154_PID}"

run_capture "post-runtime ut1 route" "${POST_ROUTE_LOG}" \
  docker exec ut1 ip route
run_capture "post-runtime ut1 ping as160" "${POST_PING_AS160_LOG}" \
  bash -lc "docker exec ut1 ping -n -c 1 -W 1 10.160.0.72 || true"

python3 - <<'PY' "${REAL_STDOUT_JSON}" "${WATCHER_AS160_LOG}" "${WATCHER_AS154_LOG}" "${SUMMARY_JSON}"
import json
import sys
from pathlib import Path

real_json = Path(sys.argv[1])
watcher_160 = Path(sys.argv[2])
watcher_154 = Path(sys.argv[3])
summary_json = Path(sys.argv[4])

def load_mixed_stdout_json(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("{"):
            return json.loads("\n".join(lines[index:]))
    raise ValueError(f"no JSON object found in {path}")

report = load_mixed_stdout_json(real_json)
real = dict(report.get("real_execution") or {})
iterations = list(real.get("iterations") or [])

def summarize_watcher(path: Path) -> dict:
    counts = {"success": 0, "loss": 0, "unreachable": 0, "other": 0}
    first_success = None
    first_failure_after_success = None
    saw_success = False
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for line in lines:
        parts = line.split(" ", 3)
        if len(parts) < 3:
            continue
        timestamp = parts[0]
        status = parts[2]
        counts[status] = counts.get(status, 0) + 1
        if status == "success" and first_success is None:
            first_success = timestamp
            saw_success = True
        elif saw_success and status != "success" and first_failure_after_success is None:
            first_failure_after_success = timestamp
    return {
        "sample_count": sum(counts.values()),
        "counts": counts,
        "first_success_timestamp": first_success,
        "first_non_success_after_first_success": first_failure_after_success,
    }

summary = {
    "overall_outcome": dict(real.get("outcome") or {}),
    "timing_summary": dict(real.get("timing_summary") or {}),
    "iteration_count": len(iterations),
    "iterations": [
        {
            "sequence_index": item.get("sequence_index"),
            "from_slice_id": item.get("from_slice_id"),
            "to_slice_id": item.get("to_slice_id"),
            "transition_success": item.get("outcome", {}).get("transition_success"),
            "post_transition_ping_success": item.get("post_transition_connectivity", {}).get("ping_success"),
            "observed_paths": [
                {
                    "selector": obs.get("selector"),
                    "path_string": obs.get("path_string"),
                    "matched": obs.get("matched"),
                }
                for obs in item.get("packet_observation", {}).get("observations", [])
            ],
            "host_wall_seconds": item.get("timing", {}).get("host_mutation_apply_wall_seconds"),
            "node_apply_wall_seconds": item.get("timing", {}).get("node_local_apply_wall_seconds"),
            "node_ping_wall_seconds": item.get("timing", {}).get("node_local_ping_wall_seconds"),
            "pacing_sleep_wall_seconds": item.get("timing", {}).get("pacing_sleep_wall_seconds"),
        }
        for item in iterations
    ],
    "watchers": {
        "ut1_to_as160": summarize_watcher(watcher_160),
        "ut1_to_as154": summarize_watcher(watcher_154),
    },
}

summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2, sort_keys=True))
PY

log "Full runtime validation finished"
