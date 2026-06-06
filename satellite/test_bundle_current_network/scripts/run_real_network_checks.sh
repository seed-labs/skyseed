#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${BUNDLE_DIR}/../.." && pwd)"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

run_step() {
  local description="$1"
  shift
  log "STEP ${description}"
  printf '$'
  printf ' %q' "$@"
  printf '\n'
  if "$@"; then
    log "OK ${description}"
    return 0
  fi
  log "ERROR ${description}"
  return 1
}

REQUIRED_CONTAINERS=(
  ut1
  sat-a1
  sat-a2
  gw1
  gw2
  as154-host-new
  as160-host1
)

RUNNING_NAMES="$(docker ps --format '{{.Names}}')"
MISSING=()
for name in "${REQUIRED_CONTAINERS[@]}"; do
  if ! printf '%s\n' "${RUNNING_NAMES}" | grep -qx "${name}"; then
    MISSING+=("${name}")
  fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  log "SKIPPED real network checks because required containers are not all running"
  log "Missing containers: ${MISSING[*]}"
  log "Hint: if you want dataplane checks, bring up the iteration2 compose stack first."
  exit 0
fi

FAILURES=0

run_step "ut1 route table" docker exec ut1 ip route || FAILURES=$((FAILURES + 1))
run_step "ut1 ping as160-host1" docker exec ut1 ping -n -c 3 -W 1 10.160.0.72 || FAILURES=$((FAILURES + 1))
run_step "ut1 ping as154-host-new" docker exec ut1 ping -n -c 3 -W 1 10.154.0.129 || FAILURES=$((FAILURES + 1))
run_step "sat-a1 link-a1-a2 counters" docker exec sat-a1 sh -lc "ip -s link show dev link-a1-a2 | sed -n '1,6p'" || FAILURES=$((FAILURES + 1))
run_step "sat-a2 MPLS LFIB" docker exec sat-a2 ip -f mpls route show || FAILURES=$((FAILURES + 1))
run_step "gw1 MPLS LFIB" docker exec gw1 ip -f mpls route show || FAILURES=$((FAILURES + 1))
run_step "gw2 MPLS LFIB" docker exec gw2 ip -f mpls route show || FAILURES=$((FAILURES + 1))
run_step "gw2 ix101 counters" docker exec gw2 sh -lc "ip -s link show dev ix101 | sed -n '1,6p'" || FAILURES=$((FAILURES + 1))

if [[ ${FAILURES} -gt 0 ]]; then
  log "Real network checks finished with ${FAILURES} failed step(s)"
  exit 1
fi

log "Real network checks finished successfully"
