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

ACTIVATE_BASE_SH="$(resolve_path "${ACTIVATE_BASE_SH:-}")"
MODEL_DIR="$(resolve_path "${MODEL_DIR:-}")"
RUNTIME_ROOT="$(resolve_path "${RUNTIME_ROOT:-}")"

source "$ACTIVATE_BASE_SH"
conda activate "$CONDA_ENV_NAME"

export CUDA_VISIBLE_DEVICES
export VLLM_USE_V1
export MINERU_MODEL_SOURCE
export HOME="$RUNTIME_ROOT"
mkdir -p "$HOME"

TOOLS_CONFIG_PATH="$HOME/$TOOLS_CONFIG_NAME"
cat > "$TOOLS_CONFIG_PATH" <<JSON
{
  "models-dir": {
    "$MODEL_TYPE": "$MODEL_DIR"
  }
}
JSON

export MINERU_TOOLS_CONFIG_JSON="$TOOLS_CONFIG_NAME"

echo "Starting MinerU API on http://$HOST:$PORT"
echo "Model dir: $MODEL_DIR"
exec mineru-api --host "$HOST" --port "$PORT" --enable-vlm-preload "$ENABLE_VLM_PRELOAD"
