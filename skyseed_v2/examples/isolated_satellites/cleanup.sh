#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"
COMPOSE_FILE="$OUTPUT_DIR/docker-compose.yml"
PROJECT_NAME="skyseed_v2_isolated_satellites"

if [[ -f "$COMPOSE_FILE" ]]; then
    docker compose --parallel 16 -p "$PROJECT_NAME" -f "$COMPOSE_FILE" \
        down --remove-orphans
fi
if [[ -d "$OUTPUT_DIR" ]]; then
    find "$OUTPUT_DIR" -depth -mindepth 1 -delete
    rmdir "$OUTPUT_DIR"
fi
