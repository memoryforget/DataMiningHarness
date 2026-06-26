#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${MINERU_API_ENV_FILE:-$SCRIPT_DIR/mineru_api.env}"

resolve_path() {
  local raw="$1"
  if [[ -z "$raw" ]]; then
    return 0
  fi
  if [[ "$raw" = /* ]]; then
    printf '%s
' "$raw"
  else
    python3 -c 'import os,sys; print(os.path.abspath(os.path.join(sys.argv[1], sys.argv[2])))' "$SCRIPT_DIR" "$raw"
  fi
}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Config file not found: $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

LOG_FILE="$(resolve_path "${LOG_FILE:-}")"
PID_FILE="$(resolve_path "${PID_FILE:-}")"

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$PID_FILE")"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "MinerU API already running with PID $existing_pid" >&2
    exit 0
  fi
  rm -f "$PID_FILE"
fi

nohup "$SCRIPT_DIR/start_mineru_api.sh" > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Started MinerU API in background. PID=$(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
