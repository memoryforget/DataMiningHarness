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

PID_FILE="$(resolve_path "${PID_FILE:-}")"

if [[ ! -f "$PID_FILE" ]]; then
  echo "PID file not found: $PID_FILE" >&2
  exit 1
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$pid" ]]; then
  echo "PID file is empty: $PID_FILE" >&2
  rm -f "$PID_FILE"
  exit 1
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  echo "Stopped MinerU API PID $pid"
else
  echo "Process $pid is not running"
fi

rm -f "$PID_FILE"
