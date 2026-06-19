#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  run_opencode_locally.sh \
    --queries-json BENCHMARK_JSON \
    --output-dir DIR \
    [--limit N] \
    [--skip-existing] \
    [--jobs N] \
    [--debug] \
    [--model MODEL] \
    [--opencode-bin PATH] \
    [--opencode-config-dir DIR] \
    [--opencode-extra-arg ARG]... \
    [--mineru-local-api-url URL] \
    [--tmp-root DIR] \
    [--activate-script FILE] \
    [--node-activate-script FILE] \
    [--conda-env NAME] \
    [--no-conda-activate] \
    [--no-node-activate]
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_DIR="$SCRIPT_DIR"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_ACTIVATE_SCRIPT="$WORKSPACE_ROOT/activate_my_base.sh"
DEFAULT_NODE_ACTIVATE_SCRIPT="$WORKSPACE_ROOT/activate_my_node.sh"
DEFAULT_OPENCODE_BIN="opencode"
DEFAULT_OPENCODE_CONFIG_DIR="$WORKSPACE_ROOT/.opencode"

QUERIES_JSON=""
OUTPUT_DIR=""
MODEL=""
OPENCODE_BIN="$DEFAULT_OPENCODE_BIN"
OPENCODE_CONFIG_DIR="$DEFAULT_OPENCODE_CONFIG_DIR"
MINERU_LOCAL_API_URL=""
LIMIT=""
SKIP_EXISTING=0
JOBS=1
DEBUG_MODE=0
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)_$$"
TMP_ROOT="$WORKSPACE_ROOT/tmp/opencode-local-batch_$RUN_STAMP"
ACTIVATE_SCRIPT="$DEFAULT_ACTIVATE_SCRIPT"
NODE_ACTIVATE_SCRIPT="$DEFAULT_NODE_ACTIVATE_SCRIPT"
CONDA_ENV_NAME="daagent"
USE_CONDA_ACTIVATE=1
USE_NODE_ACTIVATE=1
FAIL_LOG=""
OPENCODE_EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --queries-json) QUERIES_JSON="${2:?missing value for --queries-json}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:?missing value for --output-dir}"; shift 2 ;;
    --limit) LIMIT="${2:?missing value for --limit}"; shift 2 ;;
    --skip-existing) SKIP_EXISTING=1; shift ;;
    --jobs) JOBS="${2:?missing value for --jobs}"; shift 2 ;;
    --debug) DEBUG_MODE=1; shift ;;
    --model) MODEL="${2:?missing value for --model}"; shift 2 ;;
    --opencode-bin) OPENCODE_BIN="${2:?missing value for --opencode-bin}"; shift 2 ;;
    --opencode-config-dir) OPENCODE_CONFIG_DIR="${2:?missing value for --opencode-config-dir}"; shift 2 ;;
    --opencode-extra-arg) OPENCODE_EXTRA_ARGS+=("${2:?missing value for --opencode-extra-arg}"); shift 2 ;;
    --mineru-local-api-url) MINERU_LOCAL_API_URL="${2:?missing value for --mineru-local-api-url}"; shift 2 ;;
    --tmp-root) TMP_ROOT="${2:?missing value for --tmp-root}"; shift 2 ;;
    --activate-script) ACTIVATE_SCRIPT="${2:?missing value for --activate-script}"; shift 2 ;;
    --node-activate-script) NODE_ACTIVATE_SCRIPT="${2:?missing value for --node-activate-script}"; shift 2 ;;
    --conda-env) CONDA_ENV_NAME="${2:?missing value for --conda-env}"; shift 2 ;;
    --no-conda-activate) USE_CONDA_ACTIVATE=0; shift ;;
    --no-node-activate) USE_NODE_ACTIVATE=0; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ -z "$QUERIES_JSON" || -z "$OUTPUT_DIR" ]]; then
  echo "Missing required arguments." >&2
  usage >&2
  exit 1
fi

if [[ ! -f "$HARNESS_DIR/prompt.md" ]]; then
  echo "Harness file not found: $HARNESS_DIR/prompt.md" >&2
  exit 1
fi
if [[ ! -f "$QUERIES_JSON" ]]; then
  echo "Queries JSON does not exist: $QUERIES_JSON" >&2
  exit 1
fi
if [[ ! -d "$OPENCODE_CONFIG_DIR" ]]; then
  echo "OpenCode config directory does not exist: $OPENCODE_CONFIG_DIR" >&2
  exit 1
fi
if [[ ! -f "$OPENCODE_CONFIG_DIR/opencode.json" ]]; then
  echo "OpenCode config not found: $OPENCODE_CONFIG_DIR/opencode.json" >&2
  exit 1
fi
if [[ -n "$LIMIT" && ! "$LIMIT" =~ ^[0-9]+$ ]]; then
  echo "--limit must be a non-negative integer." >&2
  exit 1
fi
if [[ ! "$JOBS" =~ ^[1-9][0-9]*$ ]]; then
  echo "--jobs must be a positive integer." >&2
  exit 1
fi
if [[ $USE_CONDA_ACTIVATE -eq 1 && ! -f "$ACTIVATE_SCRIPT" ]]; then
  echo "Activate script does not exist: $ACTIVATE_SCRIPT" >&2
  exit 1
fi
if [[ $USE_NODE_ACTIVATE -eq 1 && ! -f "$NODE_ACTIVATE_SCRIPT" ]]; then
  echo "Node activate script does not exist: $NODE_ACTIVATE_SCRIPT" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required for batch mode." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR" "$TMP_ROOT"
FAIL_LOG="$TMP_ROOT/failures_$(date +%s)_$$.log"
: > "$FAIL_LOG"
trap 'rm -f "$FAIL_LOG" >/dev/null 2>&1 || true' EXIT

render_prompt() {
  local query_text="$1"
  local data_lake_path="$2"
  local -a render_args=(
    python3 "$HARNESS_DIR/scripts/render_prompt.py"
    --data-lake-path "$data_lake_path"
    --query "$query_text"
  )
  "${render_args[@]}"
}

run_query_locally() {
  local query_id="$1"
  local prompt_text="$2"
  local task_output_dir="$OUTPUT_DIR/$query_id"
  local log_file="$task_output_dir/run.log"
  local debug_log_file="$task_output_dir/debug.log"
  local status_file="$task_output_dir/exit_code.txt"
  local prompt_file="$task_output_dir/prompt.txt"
  local insights_file="$task_output_dir/report.md"
  local run_root="$TMP_ROOT/$query_id"
  local workspace="$run_root/workspace"
  local opencode_home="$workspace"
  local xdg_config_home="$workspace/.config"
  local xdg_cache_home="$workspace/.cache"
  local xdg_data_home="$workspace/.local/share"
  local opencode_config="$workspace/.opencode"
  local opencode_skills="$opencode_config/skills"
  local env_info_file="$task_output_dir/env_info.txt"
  local task_start_ts=0
  local task_end_ts=0
  local task_elapsed_seconds=0
  local exit_code=0
  local -a cmd=(
    "$OPENCODE_BIN" run "$prompt_text"
    --dangerously-skip-permissions
    --print-logs
    --log-level DEBUG
  )

  if [[ -n "$MODEL" ]]; then
    cmd+=(-m "$MODEL")
  fi
  if [[ ${#OPENCODE_EXTRA_ARGS[@]} -gt 0 ]]; then
    cmd+=("${OPENCODE_EXTRA_ARGS[@]}")
  fi

  mkdir -p "$task_output_dir"
  rm -rf "$run_root"
  mkdir -p \
    "$workspace/artifacts" \
    "$opencode_config" \
    "$opencode_skills" \
    "$opencode_home" \
    "$xdg_config_home" \
    "$xdg_cache_home" \
    "$xdg_data_home"
  cp -a "$HARNESS_DIR/skills/mineru-pdf" "$opencode_skills/mineru-pdf"
  cp "$OPENCODE_CONFIG_DIR/opencode.json" "$opencode_config/opencode.json"

  : > "$log_file"
  printf '%s\n' "$prompt_text" > "$prompt_file"
  task_start_ts="$(date +%s)"
  printf '[%s] start %s\n' "$(date -u +%FT%TZ)" "$query_id" | tee -a "$log_file" >&2

  set +e
  (
    set -- # Clear positional parameters to avoid passing them to sourced scripts
    export MINERU_LOCAL_API_URL="$MINERU_LOCAL_API_URL"
    export TASK_OUTPUT_DIR="$task_output_dir"
    export IS_SANDBOX=1
    export HOME="$opencode_home"
    export XDG_CONFIG_HOME="$xdg_config_home"
    export XDG_CACHE_HOME="$xdg_cache_home"
    export XDG_DATA_HOME="$xdg_data_home"
    if [[ $USE_CONDA_ACTIVATE -eq 1 ]]; then
      source "$ACTIVATE_SCRIPT"
      conda activate "$CONDA_ENV_NAME"
    fi
    if [[ $USE_NODE_ACTIVATE -eq 1 ]]; then
      source "$NODE_ACTIVATE_SCRIPT"
    fi
    if ! command -v "$OPENCODE_BIN" >/dev/null 2>&1; then
      echo "opencode is not available after environment activation" >&2
      exit 127
    fi
    {
      echo "python=$(command -v python || true)"
      python -c 'import sys; print("python_executable=" + sys.executable)' 2>/dev/null || true
      echo "pip=$(command -v pip || true)"
      pip --version 2>/dev/null || true
      echo "opencode=$(command -v "$OPENCODE_BIN" || true)"
      echo "conda_env=${CONDA_DEFAULT_ENV:-}"
      echo "home=$HOME"
      echo "xdg_config_home=$XDG_CONFIG_HOME"
      echo "xdg_cache_home=$XDG_CACHE_HOME"
      echo "xdg_data_home=$XDG_DATA_HOME"
      echo "opencode_config_dir=$opencode_config"
      echo "opencode_config=$opencode_config/opencode.json"
      echo "node_activate_script=$NODE_ACTIVATE_SCRIPT"
      echo "mineru_local_api_url=$MINERU_LOCAL_API_URL"
    } > "$env_info_file"
    if [[ "$DEBUG_MODE" == "1" ]]; then
      echo "[debug] whoami=$(whoami)"
      echo "[debug] pwd=$workspace"
      echo "[debug] MINERU_LOCAL_API_URL=$MINERU_LOCAL_API_URL"
      echo "[debug] ACTIVATE_SCRIPT=$ACTIVATE_SCRIPT"
      echo "[debug] CONDA_ENV_NAME=$CONDA_ENV_NAME"
      echo "[debug] CONDA_DEFAULT_ENV=${CONDA_DEFAULT_ENV:-}"
      echo "[debug] HOME=$HOME"
      echo "[debug] XDG_CONFIG_HOME=$XDG_CONFIG_HOME"
      echo "[debug] XDG_CACHE_HOME=$XDG_CACHE_HOME"
      echo "[debug] XDG_DATA_HOME=$XDG_DATA_HOME"
      echo "[debug] NODE_ACTIVATE_SCRIPT=$NODE_ACTIVATE_SCRIPT"
      echo "[debug] workspace_before"
      find "$workspace" -maxdepth 3 -type f | sort
      echo "[debug] cmd=${cmd[*]}"
    fi
    cd "$workspace"
    "${cmd[@]}" < /dev/null 2>"$debug_log_file"
    if [[ "$DEBUG_MODE" == "1" ]]; then
      echo "[debug] workspace_after"
      find "$workspace" -maxdepth 3 -type f | sort
    fi
  ) >>"$log_file" 2>&1
  exit_code=$?
  set -e
  task_end_ts="$(date +%s)"
  task_elapsed_seconds=$((task_end_ts - task_start_ts))
  printf '%s\n' "$task_elapsed_seconds" > "$task_output_dir/run_time_seconds.txt"
  cat > "$task_output_dir/run_time_summary.json" <<EOF
{
  "task_id": "$query_id",
  "run_time_seconds": $task_elapsed_seconds,
  "start_epoch_seconds": $task_start_ts,
  "end_epoch_seconds": $task_end_ts
}
EOF

  if [[ -f "$workspace/report.md" ]]; then
    cp -f "$workspace/report.md" "$insights_file"
  fi
  if [[ -d "$workspace/artifacts" ]]; then
    rm -rf "$task_output_dir/artifacts"
    cp -a "$workspace/artifacts" "$task_output_dir/artifacts"
  fi
  if [[ -d "$workspace/.opencode/skills" ]]; then
    rm -rf "$task_output_dir/.opencode"
    mkdir -p "$task_output_dir/.opencode"
    cp -a "$workspace/.opencode/skills" "$task_output_dir/.opencode/skills"
  fi
  if [[ -d "$workspace/mineru_runs" ]]; then
    rm -rf "$task_output_dir/mineru_runs"
    cp -a "$workspace/mineru_runs" "$task_output_dir/mineru_runs"
  fi
  python3 "$HARNESS_DIR/scripts/extract_token_usage.py" task \
    --tool opencode \
    --run-root "$run_root" \
    --output-dir "$task_output_dir" >>"$log_file" 2>&1 || true

  if [[ $exit_code -eq 0 && ( ! -f "$insights_file" || ! -s "$insights_file" ) ]]; then
    echo "Expected output missing: $insights_file" >> "$log_file"
    exit_code=101
  fi

  printf '%s\n' "$exit_code" > "$status_file"
  if [[ $exit_code -ne 0 ]]; then
    printf '%s\n' "$query_id" >> "$FAIL_LOG"
    printf '[%s] fail %s exit=%s\n' "$(date -u +%FT%TZ)" "$query_id" "$exit_code" | tee -a "$log_file" >&2
    return "$exit_code"
  fi

  printf '[%s] done %s time=%ss\n' "$(date -u +%FT%TZ)" "$query_id" "$task_elapsed_seconds" | tee -a "$log_file" >&2
}

wait_for_slot() {
  while [[ $(jobs -rp | wc -l) -ge $JOBS ]]; do
    wait -n || true
  done
}

run_batch() {
  local query_id=""
  local query_text=""
  local prompt_text=""
  local data_lake_path=""
  local seen=0
  local submitted=0
  local -a helper_args=(
    python3 "$HARNESS_DIR/scripts/extract_formatted_queries.py"
    --queries-json "$QUERIES_JSON"
  )

  if [[ -n "$LIMIT" ]]; then
    helper_args+=(--limit "$LIMIT")
  fi

  while IFS=$'\t' read -r query_id data_lake_path query_text; do
    [[ -n "$query_id" ]] || continue
    seen=$((seen + 1))

    if [[ ! -d "$data_lake_path" ]]; then
      echo "Data lake path does not exist for query $query_id: $data_lake_path" >&2
      printf '%s\n' "$query_id" >> "$FAIL_LOG"
      continue
    fi

    if [[ $SKIP_EXISTING -eq 1 && -f "$OUTPUT_DIR/$query_id/report.md" ]]; then
      echo "[$query_id] skipping because report.md already exists." >&2
      continue
    fi

    prompt_text="$(render_prompt "$query_text" "$data_lake_path")"
    wait_for_slot
    run_query_locally "$query_id" "$prompt_text" &
    submitted=$((submitted + 1))
  done < <("${helper_args[@]}")

  if [[ $seen -eq 0 ]]; then
    echo "No benchmark stage_2_tasks matched the current filters." >&2
    exit 1
  fi

  if [[ $submitted -eq 0 ]]; then
    echo "All matched queries were skipped or unavailable; no new runs submitted." >&2
  fi

  wait

  python3 "$HARNESS_DIR/scripts/extract_token_usage.py" summary \
    --output-dir "$OUTPUT_DIR" >/dev/null 2>&1 || true

  if [[ -s "$FAIL_LOG" ]]; then
    echo "Some queries failed:" >&2
    cat "$FAIL_LOG" >&2
    exit 1
  fi
}

run_batch
