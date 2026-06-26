#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${MINERU_API_ENV_FILE:-$SCRIPT_DIR/mineru_api.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Config file not found: $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

curl --fail --silent --show-error "http://127.0.0.1:${PORT}/health"
echo
