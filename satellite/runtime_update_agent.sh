#!/bin/sh

# Minimal transient node-local runtime update agent.
# The controller currently injects this script via docker exec stdin, but the
# execution responsibility for ordered command mutation lives inside the node.

set -u

SKYSEED_AGENT_LOCAL_SUPPORTED_PROTOCOL_VERSION="satellite-distributed-runtime-update-protocol-v1"
SKYSEED_AGENT_LOCAL_SUPPORTED_COMMAND_FAMILIES_DEFAULT="ip_mpls_route,ip_route,ip_rule,iptables,sysctl"

skyseed_agent_emit_state() {
  printf "__SKYSEED_AGENT_SCHEMA_VERSION__:%s\n" "${SKYSEED_AGENT_SCHEMA_VERSION:-unknown}"
  printf "__SKYSEED_AGENT_PROTOCOL_VERSION__:%s\n" "${SKYSEED_AGENT_PROTOCOL_VERSION:-unknown}"
  printf "__SKYSEED_AGENT_SUPPORTED_PROTOCOL_VERSION__:%s\n" "$SKYSEED_AGENT_LOCAL_SUPPORTED_PROTOCOL_VERSION"
  printf "__SKYSEED_AGENT_NODE_PLAN_ID__:%s\n" "${SKYSEED_AGENT_NODE_PLAN_ID:-unknown}"
  printf "__SKYSEED_AGENT_EXECUTION_NONCE__:%s\n" "${SKYSEED_AGENT_EXECUTION_NONCE:-unknown}"
  printf "__SKYSEED_AGENT_HEALTH_STATUS__:%s\n" "${SKYSEED_AGENT_HEALTH_STATUS:-shell_agent_running}"
  printf "__SKYSEED_AGENT_ACK__:%s\n" "$SKYSEED_AGENT_ACK_STATUS"
  printf "__SKYSEED_AGENT_APPLY_STATUS__:%s\n" "$SKYSEED_AGENT_APPLY_STATUS"
  printf "__SKYSEED_AGENT_ERROR_CODE__:%s\n" "$SKYSEED_AGENT_ERROR_CODE"
  printf "__SKYSEED_AGENT_ERROR_CATEGORY__:%s\n" "$SKYSEED_AGENT_ERROR_CATEGORY"
  printf "__SKYSEED_AGENT_ERROR_MESSAGE__:%s\n" "$SKYSEED_AGENT_ERROR_MESSAGE"
  printf "__SKYSEED_AGENT_VALIDATION_STATUS__:%s\n" "$SKYSEED_AGENT_VALIDATION_STATUS"
  printf "__SKYSEED_AGENT_REPLAY_MODE__:%s\n" "${SKYSEED_AGENT_REPLAY_DETECTION_MODE:-unknown}"
  printf "__SKYSEED_AGENT_REPLAY_VISIBLE_NODE_PLAN_ID__:%s\n" "${SKYSEED_AGENT_NODE_PLAN_ID:-unknown}"
  printf "__SKYSEED_AGENT_REPLAY_VISIBLE_EXECUTION_NONCE__:%s\n" "${SKYSEED_AGENT_EXECUTION_NONCE:-unknown}"
  if [ "$SKYSEED_AGENT_MALFORMED_PAYLOAD" -eq 1 ]; then
    printf "__SKYSEED_AGENT_MALFORMED_PAYLOAD__:1\n"
  fi
  if [ -n "$SKYSEED_AGENT_UNSUPPORTED_PROTOCOL_VERSION" ]; then
    printf "__SKYSEED_AGENT_UNSUPPORTED_PROTOCOL_VERSION__:%s\n" "$SKYSEED_AGENT_UNSUPPORTED_PROTOCOL_VERSION"
  fi
  if [ -n "$SKYSEED_AGENT_FAILED_COMMAND_INDEX" ]; then
    printf "__SKYSEED_AGENT_FAILED_COMMAND_INDEX__:%s\n" "$SKYSEED_AGENT_FAILED_COMMAND_INDEX"
  fi
  if [ -n "$SKYSEED_AGENT_FAILED_COMMAND_FAMILY" ]; then
    printf "__SKYSEED_AGENT_FAILED_COMMAND_FAMILY__:%s\n" "$SKYSEED_AGENT_FAILED_COMMAND_FAMILY"
  fi
  if [ -n "$SKYSEED_AGENT_FAILED_LOGICAL_KIND" ]; then
    printf "__SKYSEED_AGENT_FAILED_LOGICAL_KIND__:%s\n" "$SKYSEED_AGENT_FAILED_LOGICAL_KIND"
  fi
  for field in $SKYSEED_AGENT_MISSING_REQUIRED_FIELDS; do
    printf "__SKYSEED_AGENT_MISSING_FIELD__:%s\n" "$field"
  done
  for family in $SKYSEED_AGENT_UNSUPPORTED_COMMAND_FAMILIES; do
    printf "__SKYSEED_AGENT_UNSUPPORTED_COMMAND_FAMILY__:%s\n" "$family"
  done
  for validation_error in $SKYSEED_AGENT_VALIDATION_ERRORS; do
    printf "__SKYSEED_AGENT_VALIDATION_ERROR__:%s\n" "$validation_error"
  done
}

skyseed_agent_append_word() {
  list_name="$1"
  value="$2"
  eval "current_value=\${$list_name}"
  if [ -z "$current_value" ]; then
    eval "$list_name=\$value"
  else
    eval "$list_name=\$current_value \$value"
  fi
}

skyseed_agent_mark_validation_failure() {
  error_code="$1"
  error_category="$2"
  error_message="$3"
  validation_status="$4"
  SKYSEED_AGENT_ERROR_CODE="$error_code"
  SKYSEED_AGENT_ERROR_CATEGORY="$error_category"
  SKYSEED_AGENT_ERROR_MESSAGE="$error_message"
  SKYSEED_AGENT_VALIDATION_STATUS="$validation_status"
  SKYSEED_AGENT_ACK_STATUS="acknowledged_error"
  SKYSEED_AGENT_APPLY_STATUS="not_started"
  SKYSEED_AGENT_HEALTH_STATUS="shell_agent_validation_failed"
}

skyseed_agent_validate_required_field() {
  field_name="$1"
  eval "field_value=\${$field_name-}"
  if [ -z "$field_value" ]; then
    SKYSEED_AGENT_MALFORMED_PAYLOAD=1
    skyseed_agent_append_word SKYSEED_AGENT_MISSING_REQUIRED_FIELDS "$field_name"
    skyseed_agent_append_word SKYSEED_AGENT_VALIDATION_ERRORS "missing_required_field"
  fi
}

skyseed_agent_validate_integer_field() {
  field_name="$1"
  eval "field_value=\${$field_name-}"
  case "$field_value" in
    ""|*[!0-9]*)
      SKYSEED_AGENT_MALFORMED_PAYLOAD=1
      skyseed_agent_append_word SKYSEED_AGENT_VALIDATION_ERRORS "${field_name}_not_integer"
      ;;
  esac
}

skyseed_agent_validate_contract() {
  for field_name in \
    SKYSEED_AGENT_PROTOCOL_VERSION \
    SKYSEED_AGENT_SCHEMA_VERSION \
    SKYSEED_AGENT_RESPONSE_ENVELOPE_SCHEMA_VERSION \
    SKYSEED_AGENT_NODE_PLAN_ID \
    SKYSEED_AGENT_PLAN_ID \
    SKYSEED_AGENT_NODE_ID \
    SKYSEED_AGENT_NODE_TYPE \
    SKYSEED_AGENT_PHASE \
    SKYSEED_AGENT_GENERATION \
    SKYSEED_AGENT_SEQUENCE \
    SKYSEED_AGENT_COMMAND_COUNT \
    SKYSEED_AGENT_COMMAND_INDEXES \
    SKYSEED_AGENT_EXPECTED_ACK_MODE \
    SKYSEED_AGENT_EXECUTION_NONCE \
    SKYSEED_AGENT_IDEMPOTENCY_SCOPE \
    SKYSEED_AGENT_REPLAY_DETECTION_MODE
  do
    skyseed_agent_validate_required_field "$field_name"
  done
  skyseed_agent_validate_integer_field SKYSEED_AGENT_GENERATION
  skyseed_agent_validate_integer_field SKYSEED_AGENT_SEQUENCE
  skyseed_agent_validate_integer_field SKYSEED_AGENT_COMMAND_COUNT
  if [ "$SKYSEED_AGENT_MALFORMED_PAYLOAD" -eq 1 ]; then
    skyseed_agent_mark_validation_failure \
      "malformed_payload" \
      "payload" \
      "required local-agent input fields are missing or malformed" \
      "failed"
    exit 64
  fi
  if [ "${SKYSEED_AGENT_PROTOCOL_VERSION:-}" != "$SKYSEED_AGENT_LOCAL_SUPPORTED_PROTOCOL_VERSION" ]; then
    SKYSEED_AGENT_UNSUPPORTED_PROTOCOL_VERSION="${SKYSEED_AGENT_PROTOCOL_VERSION:-unknown}"
    skyseed_agent_append_word SKYSEED_AGENT_VALIDATION_ERRORS "unsupported_protocol_version"
    skyseed_agent_mark_validation_failure \
      "unsupported_protocol_version" \
      "protocol" \
      "protocol version is not supported by the local agent" \
      "rejected"
    exit 65
  fi
  SKYSEED_AGENT_VALIDATION_STATUS="passed"
}

skyseed_agent_emit_abort_marker() {
  failed_index="$1"
  skipped_indexes=""
  found_current=0
  old_ifs="$IFS"
  IFS=","
  for command_index in ${SKYSEED_AGENT_COMMAND_INDEXES:-}; do
    if [ "$found_current" -eq 1 ]; then
      if [ -z "$skipped_indexes" ]; then
        skipped_indexes="$command_index"
      else
        skipped_indexes="$skipped_indexes,$command_index"
      fi
      continue
    fi
    if [ "$command_index" = "$failed_index" ]; then
      found_current=1
    fi
  done
  IFS="$old_ifs"
  printf "__SKYSEED_AGENT_APPLY_ABORT__:%s:%s\n" "$failed_index" "$skipped_indexes"
}

skyseed_agent_validate_command_family() {
  idx="$1"
  command_family="$2"
  logical_kind="$3"
  stage="$4"
  case ",${SKYSEED_AGENT_SUPPORTED_COMMAND_FAMILIES:-$SKYSEED_AGENT_LOCAL_SUPPORTED_COMMAND_FAMILIES_DEFAULT}," in
    *,"$command_family",*)
      return 0
      ;;
  esac
  skyseed_agent_append_word SKYSEED_AGENT_UNSUPPORTED_COMMAND_FAMILIES "$command_family"
  skyseed_agent_append_word SKYSEED_AGENT_VALIDATION_ERRORS "unsupported_command_family"
  SKYSEED_AGENT_FAILED_COMMAND_INDEX="$idx"
  SKYSEED_AGENT_FAILED_COMMAND_FAMILY="$command_family"
  SKYSEED_AGENT_FAILED_LOGICAL_KIND="$logical_kind"
  SKYSEED_AGENT_ERROR_CODE="unsupported_command"
  SKYSEED_AGENT_ERROR_CATEGORY="capability"
  SKYSEED_AGENT_ERROR_MESSAGE="$stage"
  SKYSEED_AGENT_VALIDATION_STATUS="rejected"
  SKYSEED_AGENT_ACK_STATUS="acknowledged_error"
  if [ "$SKYSEED_AGENT_EXECUTED_COMMAND_COUNT" -gt 0 ]; then
    SKYSEED_AGENT_APPLY_STATUS="partially_applied"
  else
    SKYSEED_AGENT_APPLY_STATUS="not_started"
  fi
  SKYSEED_AGENT_HEALTH_STATUS="shell_agent_validation_failed"
  skyseed_agent_emit_abort_marker "$idx"
  exit 65
}

skyseed_agent_on_exit() {
  rc="$1"
  if [ "$SKYSEED_AGENT_STARTED" -eq 1 ]; then
    if [ "$SKYSEED_AGENT_APPLY_STATUS" = "in_progress" ]; then
      if [ -n "$SKYSEED_AGENT_FAILED_COMMAND_INDEX" ]; then
        SKYSEED_AGENT_ACK_STATUS="acknowledged_error"
        if [ "$SKYSEED_AGENT_ERROR_CODE" = "command_failed" ]; then
          SKYSEED_AGENT_APPLY_STATUS="partially_applied"
        elif [ "$SKYSEED_AGENT_EXECUTED_COMMAND_COUNT" -gt 0 ]; then
          SKYSEED_AGENT_APPLY_STATUS="partially_applied"
        else
          SKYSEED_AGENT_APPLY_STATUS="not_started"
        fi
      elif [ "$rc" -ne 0 ]; then
        SKYSEED_AGENT_ACK_STATUS="acknowledged_error"
        SKYSEED_AGENT_APPLY_STATUS="transport_error"
        SKYSEED_AGENT_ERROR_CODE="transport_error"
        SKYSEED_AGENT_ERROR_CATEGORY="transport"
        SKYSEED_AGENT_ERROR_MESSAGE="node-local shell agent exited non-zero"
      else
        SKYSEED_AGENT_ACK_STATUS="acknowledged_success"
        SKYSEED_AGENT_APPLY_STATUS="applied"
        SKYSEED_AGENT_HEALTH_STATUS="shell_agent_completed"
      fi
    fi
  fi
  skyseed_agent_emit_state
}

trap 'skyseed_agent_on_exit "$?"' EXIT

skyseed_agent_run_cmd() {
  idx="$1"
  allow_failure="$2"
  command_family="$3"
  logical_kind="$4"
  idempotency_key="$5"
  stage="$6"
  shift 6
  if [ "${1:-}" = "--" ]; then
    shift
  fi
  skyseed_agent_validate_command_family "$idx" "$command_family" "$logical_kind" "$stage"
  SKYSEED_AGENT_LAST_STARTED_COMMAND_INDEX="$idx"
  printf "__SKYSEED_AGENT_CMD_START__:%s:%s:%s\n" "$idx" "$command_family" "$logical_kind"
  printf "__SKYSEED_BATCH_CMD_START__:%s\n" "$idx"
  "$@"
  rc="$?"
  SKYSEED_AGENT_EXECUTED_COMMAND_COUNT=$((SKYSEED_AGENT_EXECUTED_COMMAND_COUNT + 1))
  printf "__SKYSEED_AGENT_CMD_EXIT__:%s:%s\n" "$idx" "$rc"
  printf "__SKYSEED_BATCH_CMD_EXIT__:%s:%s\n" "$idx" "$rc"
  if [ "$rc" -ne 0 ] && [ "$allow_failure" -ne 1 ]; then
    SKYSEED_AGENT_FAILED_COMMAND_INDEX="$idx"
    SKYSEED_AGENT_FAILED_COMMAND_FAMILY="$command_family"
    SKYSEED_AGENT_FAILED_LOGICAL_KIND="$logical_kind"
    SKYSEED_AGENT_ERROR_CODE="command_failed"
    SKYSEED_AGENT_ERROR_CATEGORY="execution"
    SKYSEED_AGENT_ERROR_MESSAGE="$stage"
    SKYSEED_AGENT_HEALTH_STATUS="shell_agent_command_failed"
    skyseed_agent_emit_abort_marker "$idx"
    printf "__SKYSEED_BATCH_FAIL__:%s:%s\n" "$idx" "$rc"
    exit "$rc"
  fi
  if [ "$rc" -ne 0 ]; then
    SKYSEED_AGENT_LAST_ALLOWED_FAILURE_INDEX="$idx"
  fi
}

skyseed_agent_begin_apply() {
  SKYSEED_AGENT_STARTED=1
  SKYSEED_AGENT_HEALTH_STATUS="shell_agent_validating"
  SKYSEED_AGENT_ACK_STATUS="awaiting_transport_completion"
  SKYSEED_AGENT_APPLY_STATUS="in_progress"
  SKYSEED_AGENT_ERROR_CODE="none"
  SKYSEED_AGENT_ERROR_CATEGORY="none"
  SKYSEED_AGENT_ERROR_MESSAGE=""
  SKYSEED_AGENT_VALIDATION_STATUS="not_run"
  skyseed_agent_validate_contract
  SKYSEED_AGENT_HEALTH_STATUS="shell_agent_running"
  skyseed_agent_emit_state
}

skyseed_agent_end_apply() {
  printf "__SKYSEED_BATCH_OK__\n"
}

skyseed_agent_require_apply_action() {
  action="${1:-apply-node-plan}"
  if [ "$action" = "apply-node-plan" ] || [ "$action" = "--apply-node-plan" ]; then
    return 0
  fi
  SKYSEED_AGENT_STARTED=1
  SKYSEED_AGENT_HEALTH_STATUS="shell_agent_rejected_action"
  SKYSEED_AGENT_ACK_STATUS="acknowledged_error"
  SKYSEED_AGENT_APPLY_STATUS="not_started"
  SKYSEED_AGENT_ERROR_CODE="unsupported_command"
  SKYSEED_AGENT_ERROR_CATEGORY="capability"
  SKYSEED_AGENT_ERROR_MESSAGE="unsupported local-agent action"
  SKYSEED_AGENT_VALIDATION_STATUS="rejected"
  exit 64
}

SKYSEED_AGENT_STARTED=0
SKYSEED_AGENT_ACK_STATUS="pending"
SKYSEED_AGENT_APPLY_STATUS="not_started"
SKYSEED_AGENT_ERROR_CODE="none"
SKYSEED_AGENT_ERROR_CATEGORY="none"
SKYSEED_AGENT_ERROR_MESSAGE=""
SKYSEED_AGENT_FAILED_COMMAND_INDEX=""
SKYSEED_AGENT_FAILED_COMMAND_FAMILY=""
SKYSEED_AGENT_FAILED_LOGICAL_KIND=""
SKYSEED_AGENT_HEALTH_STATUS="shell_agent_ready"
SKYSEED_AGENT_VALIDATION_STATUS="not_run"
SKYSEED_AGENT_MISSING_REQUIRED_FIELDS=""
SKYSEED_AGENT_UNSUPPORTED_COMMAND_FAMILIES=""
SKYSEED_AGENT_VALIDATION_ERRORS=""
SKYSEED_AGENT_UNSUPPORTED_PROTOCOL_VERSION=""
SKYSEED_AGENT_MALFORMED_PAYLOAD=0
SKYSEED_AGENT_EXECUTED_COMMAND_COUNT=0
SKYSEED_AGENT_LAST_STARTED_COMMAND_INDEX=""
SKYSEED_AGENT_LAST_ALLOWED_FAILURE_INDEX=""

skyseed_agent_require_apply_action "${1:-apply-node-plan}"
