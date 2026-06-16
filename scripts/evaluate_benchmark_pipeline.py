#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_JUDGE_API_URL = "http://123.129.219.111:3000/v1"
DEFAULT_JUDGE_API_KEY = ""
DEFAULT_JUDGE_MODEL = "gpt-5.5"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-json", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--stage1-timeout-seconds", type=int, default=600)
    parser.add_argument("--keep-stage1-workspace", action="store_true")
    parser.add_argument("--stage1-replay-root", default=None)
    parser.add_argument("--judge-api-url", default=DEFAULT_JUDGE_API_URL)
    parser.add_argument("--judge-api-key", default=DEFAULT_JUDGE_API_KEY)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-output-tokens", type=int, default=4000)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def extract_tasks(benchmark_json: Path, limit: int | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    obj = load_json(benchmark_json)
    meta = obj.get("data_lake_metadata")
    stage_1 = obj.get("stage_1_subtask_pool")
    stage_2 = obj.get("stage_2_tasks")
    if not isinstance(meta, dict) or not isinstance(stage_1, list) or not isinstance(stage_2, list):
        raise ValueError("benchmark JSON must contain data_lake_metadata, stage_1_subtask_pool, and stage_2_tasks")
    lake_path = meta.get("lake_path")
    if not isinstance(lake_path, str) or not lake_path.strip():
        raise ValueError("benchmark JSON is missing data_lake_metadata.lake_path")
    domain = Path(lake_path).name

    tasks: list[dict[str, Any]] = []
    for item in stage_2:
        if not isinstance(item, dict):
            continue
        task_id = item.get("task_id")
        if not isinstance(task_id, int):
            continue
        tasks.append(
            {
                "task_id": task_id,
                "query_id": f"{domain}_task_{task_id}",
                "stage_2_task": item,
            }
        )
        if limit is not None and len(tasks) >= limit:
            break

    benchmark_core = {
        "data_lake_metadata": meta,
        "stage_1_subtask_pool": stage_1,
    }
    return benchmark_core, tasks


def find_report(candidate_dir: Path) -> Path | None:
    for name in ("report.md", "insights.md"):
        path = candidate_dir / name
        if path.is_file():
            return path
    return None


def build_single_task_benchmark(benchmark_core: dict[str, Any], stage_2_task: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_lake_metadata": benchmark_core["data_lake_metadata"],
        "stage_1_subtask_pool": benchmark_core["stage_1_subtask_pool"],
        "stage_2_tasks": [stage_2_task],
    }


def count_expected_units(benchmark_core: dict[str, Any], stage_2_task: dict[str, Any]) -> dict[str, int]:
    subtask_rubric_counts: dict[str, int] = {}
    for item in benchmark_core["stage_1_subtask_pool"]:
        if not isinstance(item, dict):
            continue
        subtask_id = item.get("subtask_id")
        rubrics = item.get("rubrics", [])
        if isinstance(subtask_id, str) and isinstance(rubrics, list):
            subtask_rubric_counts[subtask_id] = len(rubrics)

    refs = stage_2_task.get("hidden_subtasks_refs", [])
    if not isinstance(refs, list):
        refs = []

    total_subtasks = 0
    total_rubrics = 0
    for ref in refs:
        if not isinstance(ref, str) or ref not in subtask_rubric_counts:
            continue
        total_subtasks += 1
        total_rubrics += subtask_rubric_counts[ref]

    return {
        "expected_subtasks": total_subtasks,
        "expected_rubrics": total_rubrics,
    }


def run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def main() -> int:
    args = parse_args()
    benchmark_json = Path(args.benchmark_json).resolve()
    report_dir = Path(args.report_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    scripts_dir = Path(__file__).resolve().parent
    stage1_script = scripts_dir / "validate_reproduction_stage1.py"
    stage2_script = scripts_dir / "evaluate_reports_stage2.py"
    stage3_script = scripts_dir / "evaluate_reports_stage3.py"

    if not benchmark_json.is_file():
        print(f"benchmark JSON does not exist: {benchmark_json}", file=sys.stderr)
        return 1
    if not report_dir.is_dir():
        print(f"report directory does not exist: {report_dir}", file=sys.stderr)
        return 1
    if not stage1_script.is_file():
        print(f"stage-1 script not found: {stage1_script}", file=sys.stderr)
        return 1
    if not stage2_script.is_file():
        print(f"stage-2 script not found: {stage2_script}", file=sys.stderr)
        return 1
    if not stage3_script.is_file():
        print(f"stage-3 script not found: {stage3_script}", file=sys.stderr)
        return 1

    benchmark_core, tasks = extract_tasks(benchmark_json, args.limit)
    output_dir.mkdir(parents=True, exist_ok=True)
    stage1_root = output_dir / "stage1"
    stage2_root = output_dir / "stage2"
    stage3_root = output_dir / "stage3"
    temp_root = output_dir / "_tmp"
    stage1_replay_root = Path(args.stage1_replay_root).resolve() if args.stage1_replay_root else None

    task_summaries: list[dict[str, Any]] = []
    stage1_failures: list[dict[str, Any]] = []
    stage2_failures: list[dict[str, Any]] = []
    stage3_failures: list[dict[str, Any]] = []

    for task in tasks:
        query_id = task["query_id"]
        candidate_dir = report_dir / query_id
        report_path = find_report(candidate_dir)
        expected_units = count_expected_units(benchmark_core, task["stage_2_task"])
        task_summary: dict[str, Any] = {
            "task_id": task["task_id"],
            "query_id": query_id,
            "candidate_dir": str(candidate_dir),
            "report_path": str(report_path) if report_path else None,
            **expected_units,
            "stage1": None,
            "stage2": None,
            "stage3": None,
            "final_task_passed": False,
            "final_task_score": 0.0,
        }

        stage1_task_dir = stage1_root / query_id
        stage1_output_json = stage1_task_dir / "stage1_eval.json"
        stage1_work_root = (stage1_replay_root / query_id / "replay") if stage1_replay_root else (stage1_task_dir / "replay")
        stage1_cmd = [
            sys.executable,
            str(stage1_script),
            "--candidate-dir",
            str(candidate_dir),
            "--output-json",
            str(stage1_output_json),
            "--work-root",
            str(stage1_work_root),
            "--timeout-seconds",
            str(args.stage1_timeout_seconds),
            "--judge-api-url",
            args.judge_api_url,
            "--judge-api-key",
            args.judge_api_key,
            "--judge-model",
            args.judge_model,
            "--judge-temperature",
            str(args.judge_temperature),
            "--judge-max-output-tokens",
            str(args.judge_max_output_tokens),
        ]
        if args.keep_stage1_workspace:
            stage1_cmd.append("--keep-workspace")

        if not (args.skip_existing and stage1_output_json.is_file()):
            proc = run_subprocess(stage1_cmd)
            stage1_task_dir.mkdir(parents=True, exist_ok=True)
            (stage1_task_dir / "stage1_stdout.txt").write_text(proc.stdout, encoding="utf-8")
            (stage1_task_dir / "stage1_stderr.txt").write_text(proc.stderr, encoding="utf-8")

        if not stage1_output_json.is_file():
            stage1_failures.append(
                {
                    "task_id": task["task_id"],
                    "query_id": query_id,
                    "error": "stage-1 result file was not created",
                    "stage1_dir": str(stage1_task_dir),
                }
            )
            task_summary["stage1"] = {
                "passed": False,
                "result_path": str(stage1_output_json),
            }
            task_summaries.append(task_summary)
            continue

        stage1_result = load_json(stage1_output_json)
        stage1_passed = bool(stage1_result.get("schema_pass")) and bool(stage1_result.get("execution_pass"))
        task_summary["stage1"] = {
            "passed": stage1_passed,
            "result_path": str(stage1_output_json),
        }

        if not stage1_passed:
            failing_steps = [
                {
                    "step": step.get("step"),
                    "returncode": step.get("returncode"),
                    "missing_outputs": step.get("missing_outputs", []),
                    "missing_candidate_outputs": step.get("missing_candidate_outputs", []),
                    "digest_mismatches": step.get("digest_mismatches", []),
                    "artifact_comparison_errors": step.get("artifact_comparison_errors", {}),
                    "content_validation_errors": step.get("content_validation_errors", {}),
                    "log_file": step.get("log_file"),
                }
                for step in stage1_result.get("steps", [])
                if not step.get("passed")
            ]
            stage1_failures.append(
                {
                    "task_id": task["task_id"],
                    "query_id": query_id,
                    "failing_steps": failing_steps,
                    "result_path": str(stage1_output_json),
                }
            )
            task_summaries.append(task_summary)
            continue

        if report_path is None:
            stage2_failures.append(
                {
                    "task_id": task["task_id"],
                    "query_id": query_id,
                    "error": "stage-2 skipped because report.md or insights.md is missing",
                }
            )
            task_summaries.append(task_summary)
            continue

        single_benchmark_path = temp_root / f"{query_id}_benchmark.json"
        write_json(single_benchmark_path, build_single_task_benchmark(benchmark_core, task["stage_2_task"]))

        stage2_task_dir = stage2_root / query_id
        stage2_cmd = [
            sys.executable,
            str(stage2_script),
            "--benchmark-json",
            str(single_benchmark_path),
            "--report-dir",
            str(report_dir),
            "--output-dir",
            str(stage2_task_dir),
            "--judge-api-url",
            args.judge_api_url,
            "--judge-api-key",
            args.judge_api_key,
            "--judge-model",
            args.judge_model,
            "--temperature",
            str(args.judge_temperature),
            "--max-output-tokens",
            str(args.judge_max_output_tokens),
        ]
        if args.skip_existing:
            stage2_cmd.append("--skip-existing")

        summary_path = stage2_task_dir / "summary.json"
        if not (args.skip_existing and summary_path.is_file()):
            proc = run_subprocess(stage2_cmd)
            stage2_task_dir.mkdir(parents=True, exist_ok=True)
            (stage2_task_dir / "stage2_stdout.txt").write_text(proc.stdout, encoding="utf-8")
            (stage2_task_dir / "stage2_stderr.txt").write_text(proc.stderr, encoding="utf-8")

        if not summary_path.is_file():
            stage2_failures.append(
                {
                    "task_id": task["task_id"],
                    "query_id": query_id,
                    "error": "stage-2 summary.json was not created",
                    "stage2_dir": str(stage2_task_dir),
                }
            )
            task_summary["stage2"] = {
                "evaluated": False,
                "summary_path": str(summary_path),
            }
            task_summaries.append(task_summary)
            continue

        stage2_summary = load_json(summary_path)
        per_task_eval_candidates = [
            stage2_task_dir / "task_evaluation.json",
            stage2_task_dir / query_id / "task_evaluation.json",
        ]
        per_task_eval = next((p for p in per_task_eval_candidates if p.is_file()), None)
        task_passed = False
        task_score = 0.0
        stage3_output_json: Path | None = None
        stage3_summary: dict[str, Any] | None = None
        if per_task_eval is not None:
            per_task_eval_json = load_json(per_task_eval)
            uncertain_count = sum(
                1
                for rubric in per_task_eval_json.get("rubric_results", [])
                if isinstance(rubric, dict) and str(rubric.get("verdict", "")).lower() == "uncertain"
            )
            if uncertain_count:
                stage3_task_dir = stage3_root / query_id
                stage3_output_json = stage3_task_dir / "task_evaluation.json"
                stage3_cmd = [
                    sys.executable,
                    str(stage3_script),
                    "--task-evaluation-json",
                    str(per_task_eval),
                    "--candidate-dir",
                    str(candidate_dir),
                    "--output-json",
                    str(stage3_output_json),
                    "--judge-api-url",
                    args.judge_api_url,
                    "--judge-api-key",
                    args.judge_api_key,
                    "--judge-model",
                    args.judge_model,
                    "--temperature",
                    str(args.judge_temperature),
                    "--max-output-tokens",
                    str(args.judge_max_output_tokens),
                ]
                if not (args.skip_existing and stage3_output_json.is_file()):
                    proc = run_subprocess(stage3_cmd)
                    stage3_task_dir.mkdir(parents=True, exist_ok=True)
                    (stage3_task_dir / "stage3_stdout.txt").write_text(proc.stdout, encoding="utf-8")
                    (stage3_task_dir / "stage3_stderr.txt").write_text(proc.stderr, encoding="utf-8")
                if stage3_output_json.is_file():
                    per_task_eval = stage3_output_json
                    per_task_eval_json = load_json(per_task_eval)
                    stage3_summary = per_task_eval_json.get("stage3_evaluation", {})
                else:
                    stage3_failures.append(
                        {
                            "task_id": task["task_id"],
                            "query_id": query_id,
                            "error": "stage-3 task_evaluation.json was not created",
                            "stage3_dir": str(stage3_task_dir),
                            "uncertain_rubrics": uncertain_count,
                        }
                    )
                    stage3_summary = {
                        "evaluated": False,
                        "uncertain_rubrics": uncertain_count,
                        "result_path": str(stage3_output_json),
                    }
            task_summary_block = per_task_eval_json.get("summary", {})
            task_passed = bool(task_summary_block.get("task_passed"))
            task_score = float(task_summary_block.get("task_score", 0.0))

        task_summary["stage2"] = {
            "evaluated": True,
            "summary_path": str(summary_path),
            "task_evaluation_path": str(per_task_eval) if per_task_eval is not None else None,
            "task_passed": task_passed,
            "task_score": task_score,
        }
        if stage3_summary is not None:
            task_summary["stage3"] = {
                "evaluated": bool(stage3_summary.get("evaluated")),
                "uncertain_rubrics": int(stage3_summary.get("uncertain_rubrics", 0)),
                "adjudicated_rubrics": int(stage3_summary.get("adjudicated_rubrics", 0)),
                "task_evaluation_path": str(stage3_output_json) if stage3_output_json is not None else None,
            }
        task_summary["final_task_passed"] = stage1_passed and task_passed
        task_summary["final_task_score"] = task_score if stage1_passed else 0.0

        if stage2_summary.get("failures"):
            stage2_failures.extend(stage2_summary["failures"])

        task_summaries.append(task_summary)

    total_tasks = len(task_summaries)
    stage1_passed_tasks = sum(1 for x in task_summaries if (x.get("stage1") or {}).get("passed"))
    stage2_evaluated_tasks = sum(1 for x in task_summaries if (x.get("stage2") or {}).get("evaluated"))
    stage3_evaluated_tasks = sum(1 for x in task_summaries if (x.get("stage3") or {}).get("evaluated"))
    final_passed_tasks = sum(1 for x in task_summaries if x.get("final_task_passed"))
    final_task_score_sum = sum(float(x.get("final_task_score", 0.0)) for x in task_summaries)

    total_subtasks = 0
    passed_subtasks = 0
    total_rubrics = 0
    passed_rubrics = 0
    for task_summary in task_summaries:
        task_eval_path = (task_summary.get("stage2") or {}).get("task_evaluation_path")
        if not task_eval_path:
            continue
        task_eval = load_json(Path(task_eval_path))
        summary = task_eval.get("summary", {})
        if (task_summary.get("stage1") or {}).get("passed"):
            passed_subtasks += min(int(summary.get("passed_subtasks", 0)), int(task_summary.get("expected_subtasks", 0)))
            passed_rubrics += min(int(summary.get("passed_rubrics", 0)), int(task_summary.get("expected_rubrics", 0)))

    total_subtasks = sum(int(x.get("expected_subtasks", 0)) for x in task_summaries)
    total_rubrics = sum(int(x.get("expected_rubrics", 0)) for x in task_summaries)

    final_summary = {
        "benchmark_json": str(benchmark_json),
        "report_dir": str(report_dir),
        "output_dir": str(output_dir),
        "judge_config": {
            "api_url": args.judge_api_url,
            "model": args.judge_model,
            "temperature": args.judge_temperature,
            "max_output_tokens": args.judge_max_output_tokens,
        },
        "totals": {
            "tasks_total": total_tasks,
            "stage1_passed_tasks": stage1_passed_tasks,
            "stage2_evaluated_tasks": stage2_evaluated_tasks,
            "stage3_evaluated_tasks": stage3_evaluated_tasks,
            "final_passed_tasks": final_passed_tasks,
            "final_task_score_sum": final_task_score_sum,
            "subtasks_total": total_subtasks,
            "subtasks_passed": passed_subtasks,
            "rubrics_total": total_rubrics,
            "rubrics_passed": passed_rubrics,
        },
        "rates": {
            "stage1_pass_rate": stage1_passed_tasks / total_tasks if total_tasks else 0.0,
            "task_pass_rate": final_passed_tasks / total_tasks if total_tasks else 0.0,
            "task_score_avg": final_task_score_sum / total_tasks if total_tasks else 0.0,
            "subtask_pass_rate": passed_subtasks / total_subtasks if total_subtasks else 0.0,
            "rubric_coverage": passed_rubrics / total_rubrics if total_rubrics else 0.0,
        },
        "tasks": task_summaries,
        "stage1_failures": stage1_failures,
        "stage2_failures": stage2_failures,
        "stage3_failures": stage3_failures,
    }
    write_json(output_dir / "summary.json", final_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
