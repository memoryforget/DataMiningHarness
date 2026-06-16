#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  run_all_benchmarks_claude.sh \
    --benchmark-root DIR \
    --run-output-root DIR \
    --eval-output-root DIR \
    [--limit-benchmarks N] \
    [--limit-tasks N] \
    [--skip-existing] \
    [--jobs N] \
    [--debug] \
    [--model MODEL] \
    [--claude-bin PATH] \
    [--claude-config-dir DIR] \
    [--claude-extra-arg ARG]... \
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

BENCHMARK_ROOT="$WORKSPACE_ROOT/InifiteEDA_data_lake"
RUN_OUTPUT_ROOT="$WORKSPACE_ROOT/tmp/benchmark_runs_claude"
EVAL_OUTPUT_ROOT="$WORKSPACE_ROOT/tmp/benchmark_evals_claude"
LIMIT_BENCHMARKS=""
LIMIT_TASKS=""
SKIP_EXISTING=0
JOBS=1
DEBUG_MODE=0
MODEL=""
CLAUDE_BIN=""
CLAUDE_CONFIG_DIR=""
MINERU_LOCAL_API_URL=""
TMP_ROOT=""
ACTIVATE_SCRIPT=""
NODE_ACTIVATE_SCRIPT=""
CONDA_ENV_NAME=""
USE_CONDA_ACTIVATE=1
USE_NODE_ACTIVATE=1
CLAUDE_EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --benchmark-root) BENCHMARK_ROOT="${2:?missing value for --benchmark-root}"; shift 2 ;;
    --run-output-root) RUN_OUTPUT_ROOT="${2:?missing value for --run-output-root}"; shift 2 ;;
    --eval-output-root) EVAL_OUTPUT_ROOT="${2:?missing value for --eval-output-root}"; shift 2 ;;
    --limit-benchmarks) LIMIT_BENCHMARKS="${2:?missing value for --limit-benchmarks}"; shift 2 ;;
    --limit-tasks) LIMIT_TASKS="${2:?missing value for --limit-tasks}"; shift 2 ;;
    --skip-existing) SKIP_EXISTING=1; shift ;;
    --jobs) JOBS="${2:?missing value for --jobs}"; shift 2 ;;
    --debug) DEBUG_MODE=1; shift ;;
    --model) MODEL="${2:?missing value for --model}"; shift 2 ;;
    --claude-bin) CLAUDE_BIN="${2:?missing value for --claude-bin}"; shift 2 ;;
    --claude-config-dir) CLAUDE_CONFIG_DIR="${2:?missing value for --claude-config-dir}"; shift 2 ;;
    --claude-extra-arg) CLAUDE_EXTRA_ARGS+=("${2:?missing value for --claude-extra-arg}"); shift 2 ;;
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

if [[ ! -d "$BENCHMARK_ROOT" ]]; then
  echo "Benchmark root does not exist: $BENCHMARK_ROOT" >&2
  exit 1
fi
if [[ -n "$LIMIT_BENCHMARKS" && ! "$LIMIT_BENCHMARKS" =~ ^[1-9][0-9]*$ ]]; then
  echo "--limit-benchmarks must be a positive integer." >&2
  exit 1
fi
if [[ -n "$LIMIT_TASKS" && ! "$LIMIT_TASKS" =~ ^[1-9][0-9]*$ ]]; then
  echo "--limit-tasks must be a positive integer." >&2
  exit 1
fi
if [[ ! "$JOBS" =~ ^[1-9][0-9]*$ ]]; then
  echo "--jobs must be a positive integer." >&2
  exit 1
fi

mkdir -p "$RUN_OUTPUT_ROOT" "$EVAL_OUTPUT_ROOT"

format_duration() {
  local total_seconds="$1"
  local hours minutes seconds
  hours=$((total_seconds / 3600))
  minutes=$(((total_seconds % 3600) / 60))
  seconds=$((total_seconds % 60))
  printf '%02dh:%02dm:%02ds' "$hours" "$minutes" "$seconds"
}

build_generator_args() {
  local -a args=()
  if [[ -n "$LIMIT_TASKS" ]]; then
    args+=(--limit "$LIMIT_TASKS")
  fi
  if [[ $SKIP_EXISTING -eq 1 ]]; then
    args+=(--skip-existing)
  fi
  args+=(--jobs "$JOBS")
  if [[ $DEBUG_MODE -eq 1 ]]; then
    args+=(--debug)
  fi
  if [[ -n "$MODEL" ]]; then
    args+=(--model "$MODEL")
  fi
  if [[ -n "$CLAUDE_BIN" ]]; then
    args+=(--claude-bin "$CLAUDE_BIN")
  fi
  if [[ -n "$CLAUDE_CONFIG_DIR" ]]; then
    args+=(--claude-config-dir "$CLAUDE_CONFIG_DIR")
  fi
  if [[ ${#CLAUDE_EXTRA_ARGS[@]} -gt 0 ]]; then
    local extra_arg
    for extra_arg in "${CLAUDE_EXTRA_ARGS[@]}"; do
      args+=(--claude-extra-arg "$extra_arg")
    done
  fi
  if [[ -n "$MINERU_LOCAL_API_URL" ]]; then
    args+=(--mineru-local-api-url "$MINERU_LOCAL_API_URL")
  fi
  if [[ -n "$TMP_ROOT" ]]; then
    args+=(--tmp-root "$TMP_ROOT")
  fi
  if [[ -n "$ACTIVATE_SCRIPT" ]]; then
    args+=(--activate-script "$ACTIVATE_SCRIPT")
  fi
  if [[ -n "$NODE_ACTIVATE_SCRIPT" ]]; then
    args+=(--node-activate-script "$NODE_ACTIVATE_SCRIPT")
  fi
  if [[ -n "$CONDA_ENV_NAME" ]]; then
    args+=(--conda-env "$CONDA_ENV_NAME")
  fi
  if [[ $USE_CONDA_ACTIVATE -eq 0 ]]; then
    args+=(--no-conda-activate)
  fi
  if [[ $USE_NODE_ACTIVATE -eq 0 ]]; then
    args+=(--no-node-activate)
  fi
  printf '%s\n' "${args[@]}"
}

run_one_benchmark() {
  local benchmark_json="$1"
  local benchmark_name domain run_output_dir eval_output_dir
  local run_elapsed_seconds

  benchmark_name="$(basename "$benchmark_json")"
  domain="${benchmark_name#benchmark_}"
  domain="${domain%.json}"
  run_output_dir="$RUN_OUTPUT_ROOT/$domain"
  eval_output_dir="$EVAL_OUTPUT_ROOT/$domain"

  mkdir -p "$run_output_dir" "$eval_output_dir"

  echo "[$domain] generating candidate reports with Claude Code" >&2
  mapfile -t generator_args < <(build_generator_args)
  "$HARNESS_DIR/run_claude_code_locally.sh" \
    --queries-json "$benchmark_json" \
    --output-dir "$run_output_dir" \
    "${generator_args[@]}"
  python3 "$HARNESS_DIR/scripts/summarize_run_times.py" \
    --output-dir "$run_output_dir" >/dev/null 2>&1 || true
  run_elapsed_seconds="$(cat "$run_output_dir/run_only_time_seconds.txt" 2>/dev/null || printf '0')"
  printf '[%s] cumulative task run time cost: %ss (%s)\n' \
    "$domain" \
    "$run_elapsed_seconds" \
    "$(format_duration "$run_elapsed_seconds")" >&2

  echo "[$domain] evaluating reports" >&2
  local -a eval_args=(
    "$HARNESS_DIR/scripts/evaluate_benchmark_pipeline.py"
    --benchmark-json "$benchmark_json"
    --report-dir "$run_output_dir"
    --output-dir "$eval_output_dir"
  )
  if [[ -n "$LIMIT_TASKS" ]]; then
    eval_args+=(--limit "$LIMIT_TASKS")
  fi
  if [[ $SKIP_EXISTING -eq 1 ]]; then
    eval_args+=(--skip-existing)
  fi
  if [[ -n "$TMP_ROOT" ]]; then
    eval_args+=(--stage1-replay-root "$TMP_ROOT")
  fi
  python3 "${eval_args[@]}"
}

count=0
while IFS= read -r benchmark_json; do
  [[ -n "$benchmark_json" ]] || continue
  run_one_benchmark "$benchmark_json"
  count=$((count + 1))
  if [[ -n "$LIMIT_BENCHMARKS" && "$count" -ge "$LIMIT_BENCHMARKS" ]]; then
    break
  fi
done < <(find "$BENCHMARK_ROOT" -maxdepth 1 -type f -name 'benchmark_*.json' | sort)

if [[ $count -eq 0 ]]; then
  echo "No benchmark_*.json files found under $BENCHMARK_ROOT" >&2
  exit 1
fi
