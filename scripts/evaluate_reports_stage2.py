#!/usr/bin/env python3

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Rubric:
    dimension: str
    criteria: str


@dataclass
class Subtask:
    subtask_id: str
    name: str
    rubrics: list[Rubric]


@dataclass
class Task:
    task_id: int
    query_id: str
    domain: str
    lake_path: str
    prompt: str
    subtasks: list[Subtask]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-json", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--judge-api-url", required=True)
    parser.add_argument("--judge-api-key", required=True)
    parser.add_argument("--judge-model", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-output-tokens", type=int, default=4000)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_tasks(benchmark_json: Path, limit: int | None) -> list[Task]:
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

    subtask_map: dict[str, Subtask] = {}
    for item in stage_1:
        if not isinstance(item, dict):
            continue
        subtask_id = item.get("subtask_id")
        name = item.get("name")
        rubrics_raw = item.get("rubrics", [])
        if not isinstance(subtask_id, str) or not isinstance(name, str) or not isinstance(rubrics_raw, list):
            continue
        rubrics: list[Rubric] = []
        for rubric in rubrics_raw:
            if not isinstance(rubric, dict):
                continue
            dimension = rubric.get("dimension")
            criteria = rubric.get("criteria")
            if isinstance(dimension, str) and isinstance(criteria, str):
                rubrics.append(Rubric(dimension=dimension, criteria=criteria))
        subtask_map[subtask_id] = Subtask(
            subtask_id=subtask_id,
            name=name,
            rubrics=rubrics,
        )

    tasks: list[Task] = []
    for item in stage_2:
        if not isinstance(item, dict):
            continue
        task_id = item.get("task_id")
        prompt = item.get("agent_facing_prompt")
        refs = item.get("hidden_subtasks_refs", [])
        if not isinstance(task_id, int) or not isinstance(prompt, str) or not isinstance(refs, list):
            continue
        subtasks = [subtask_map[ref] for ref in refs if ref in subtask_map]
        tasks.append(
            Task(
                task_id=task_id,
                query_id=f"{domain}_task_{task_id}",
                domain=domain,
                lake_path=lake_path,
                prompt=prompt,
                subtasks=subtasks,
            )
        )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


def find_report(candidate_dir: Path) -> Path | None:
    for name in ("report.md", "insights.md"):
        path = candidate_dir / name
        if path.is_file():
            return path
    return None


def build_task_payload(task: Task) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "query_id": task.query_id,
        "domain": task.domain,
        "data_lake_path": task.lake_path,
        "agent_facing_prompt": task.prompt,
        "subtasks": [
            {
                "subtask_id": subtask.subtask_id,
                "name": subtask.name,
                "rubrics": [
                    {
                        "rubric_index": idx,
                        "dimension": rubric.dimension,
                        "criteria": rubric.criteria,
                    }
                    for idx, rubric in enumerate(subtask.rubrics, start=1)
                ],
            }
            for subtask in task.subtasks
        ],
    }


def build_judge_prompt(task: Task, report_text: str) -> str:
    task_payload = build_task_payload(task)
    schema = {
        "task_id": task.task_id,
        "query_id": task.query_id,
        "rubric_results": [
            {
                "subtask_id": "string",
                "rubric_index": 1,
                "dimension": "string",
                "criteria": "string",
                "passed": True,
                "verdict": "pass|fail|uncertain",
                "confidence": "high|medium|low",
                "evidence": ["short quotations or paraphrases from the report"],
                "reason": "short justification for the decision",
                "requested_artifacts": [
                    {
                        "path": "artifacts/example.csv",
                        "purpose": "why this report-cited artifact would clarify an uncertain rubric",
                    }
                ],
            }
        ],
        "overall_notes": ["optional notes"],
    }
    return f"""You are a strict report-only benchmark judge.

Evaluate whether the candidate report satisfies each rubric using only the report text.
Do not use external knowledge, inspect files, or infer analysis steps that are not supported by the report.

Return one of three verdicts for each rubric:
- pass: the report gives enough concrete evidence to decide that the requirement was satisfied.
- fail: the report contradicts the requirement, omits a central required operation, or only gives vague unsupported claims.
- uncertain: the report makes a relevant claim or cites an `artifacts/...` output, but the report text alone is not enough to decide.

Concrete evidence can include fields used, rules or operations applied, output paths, resulting counts, examples, or a clear not-applicable explanation for data-dependent edge cases. Do not require every illustrative example in a rubric when the report describes a general rule covering that class. Do require evidence for explicitly required fields, checks, outputs, or metrics.

Use `verdict: "uncertain"` for cases that need artifact inspection rather than guessing. For uncertain rubrics, set `passed` to false and list only report-cited `artifacts/...` paths in `requested_artifacts`. Do not use uncertain when the report contains no relevant claim or no artifact path that could resolve the issue; mark those cases as failed.

Return strict JSON only. No markdown fences. Return exactly one rubric_results item for every rubric in Task metadata.

Required output schema:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Task metadata:
{json.dumps(task_payload, ensure_ascii=False, indent=2)}

Candidate report:
```markdown
{report_text}
```
"""


def post_json(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                last_error = RuntimeError(f"non-JSON response on attempt {attempt}: {raw[:200]!r}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"request failed: {exc}")
        if attempt < 3:
            time.sleep(5 * attempt)
    raise RuntimeError(str(last_error) if last_error else "request failed")


def run_judge(prompt: str, api_url: str, api_key: str, model: str, temperature: float, max_output_tokens: int) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
    }
    response = post_json(api_url.rstrip("/") + "/chat/completions", api_key, payload)
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("judge response missing choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("judge response missing message content")
    return content, response


def parse_judge_json(raw_output: str) -> dict[str, Any]:
    text = raw_output.strip()
    if not text:
        raise ValueError("judge returned empty output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def normalize_judge_result(task: Task, judge_result: dict[str, Any]) -> dict[str, Any]:
    rubric_lookup: dict[tuple[str, int], dict[str, str]] = {}
    for subtask in task.subtasks:
        for idx, rubric in enumerate(subtask.rubrics, start=1):
            rubric_lookup[(subtask.subtask_id, idx)] = {
                "dimension": rubric.dimension,
                "criteria": rubric.criteria,
                "subtask_name": subtask.name,
            }

    raw_rubric_results = judge_result.get("rubric_results")
    if not isinstance(raw_rubric_results, list):
        raise ValueError("judge output missing rubric_results list")

    rubric_results: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, int]] = set()
    for item in raw_rubric_results:
        if not isinstance(item, dict):
            continue
        subtask_id = item.get("subtask_id")
        rubric_index = item.get("rubric_index")
        passed = item.get("passed")
        if not isinstance(subtask_id, str) or not isinstance(rubric_index, int) or not isinstance(passed, bool):
            continue
        key = (subtask_id, rubric_index)
        if key not in rubric_lookup:
            continue
        seen_keys.add(key)
        meta = rubric_lookup[key]
        evidence = item.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = []
        requested_artifacts = item.get("requested_artifacts", [])
        if not isinstance(requested_artifacts, list):
            requested_artifacts = []
        normalized_requested_artifacts: list[dict[str, str]] = []
        for artifact_item in requested_artifacts:
            if isinstance(artifact_item, str):
                normalized_requested_artifacts.append({"path": artifact_item, "purpose": ""})
            elif isinstance(artifact_item, dict):
                path = artifact_item.get("path")
                if isinstance(path, str) and path.strip():
                    normalized_requested_artifacts.append(
                        {
                            "path": path,
                            "purpose": str(artifact_item.get("purpose", "")),
                        }
                    )
        verdict = str(item.get("verdict", "pass" if passed else "fail")).lower()
        if verdict not in {"pass", "fail", "uncertain"}:
            verdict = "pass" if passed else "fail"
        if verdict == "uncertain":
            passed = False
        rubric_results.append(
            {
                "subtask_id": subtask_id,
                "subtask_name": meta["subtask_name"],
                "rubric_index": rubric_index,
                "dimension": meta["dimension"],
                "criteria": meta["criteria"],
                "passed": passed,
                "verdict": verdict,
                "confidence": str(item.get("confidence", "unknown")),
                "evidence": [str(x) for x in evidence],
                "reason": str(item.get("reason", "")),
                "requested_artifacts": normalized_requested_artifacts,
            }
        )

    for key, meta in rubric_lookup.items():
        if key in seen_keys:
            continue
        rubric_results.append(
            {
                "subtask_id": key[0],
                "subtask_name": meta["subtask_name"],
                "rubric_index": key[1],
                "dimension": meta["dimension"],
                "criteria": meta["criteria"],
                "passed": False,
                "verdict": "fail",
                "confidence": "missing",
                "evidence": [],
                "reason": "Judge output did not provide a decision for this rubric.",
                "requested_artifacts": [],
            }
        )

    rubric_results.sort(key=lambda x: (x["subtask_id"], x["rubric_index"]))

    subtask_results: list[dict[str, Any]] = []
    for subtask in task.subtasks:
        group = [r for r in rubric_results if r["subtask_id"] == subtask.subtask_id]
        passed_rubrics = sum(1 for r in group if r["passed"])
        failed_rubrics = [r for r in group if not r["passed"]]
        subtask_results.append(
            {
                "subtask_id": subtask.subtask_id,
                "subtask_name": subtask.name,
                "passed": bool(group) and passed_rubrics == len(group),
                "passed_rubrics": passed_rubrics,
                "total_rubrics": len(group),
                "failed_rubric_indexes": [r["rubric_index"] for r in failed_rubrics],
                "failed_rubrics": failed_rubrics,
            }
        )

    total_rubrics = len(rubric_results)
    passed_rubrics = sum(1 for r in rubric_results if r["passed"])
    total_subtasks = len(subtask_results)
    passed_subtasks = sum(1 for s in subtask_results if s["passed"])
    task_score = passed_subtasks / total_subtasks if total_subtasks else 0.0
    task_passed = total_subtasks > 0 and passed_subtasks == total_subtasks

    return {
        "task_id": task.task_id,
        "query_id": task.query_id,
        "domain": task.domain,
        "data_lake_path": task.lake_path,
        "agent_facing_prompt": task.prompt,
        "overall_notes": judge_result.get("overall_notes", []),
        "rubric_results": rubric_results,
        "subtask_results": subtask_results,
        "summary": {
            "passed_rubrics": passed_rubrics,
            "total_rubrics": total_rubrics,
            "rubric_coverage": passed_rubrics / total_rubrics if total_rubrics else 0.0,
            "passed_subtasks": passed_subtasks,
            "total_subtasks": total_subtasks,
            "subtask_pass_rate": passed_subtasks / total_subtasks if total_subtasks else 0.0,
            "task_score": task_score,
            "task_passed": task_passed,
        },
    }


def aggregate_results(task_results: list[dict[str, Any]], tasks: list[Task]) -> dict[str, Any]:
    result_by_query_id = {r["query_id"]: r for r in task_results}
    total_tasks = len(tasks)
    passed_tasks = 0
    total_task_score = 0.0
    total_subtasks = sum(len(task.subtasks) for task in tasks)
    total_rubrics = sum(len(subtask.rubrics) for task in tasks for subtask in task.subtasks)
    passed_subtasks = 0
    passed_rubrics = 0

    for task in tasks:
        result = result_by_query_id.get(task.query_id)
        if result is None:
            continue
        summary = result["summary"]
        passed_tasks += 1 if summary["task_passed"] else 0
        total_task_score += float(summary.get("task_score", 0.0))
        passed_subtasks += min(int(summary.get("passed_subtasks", 0)), len(task.subtasks))
        expected_rubrics = sum(len(subtask.rubrics) for subtask in task.subtasks)
        passed_rubrics += min(int(summary.get("passed_rubrics", 0)), expected_rubrics)

    failed_tasks = []
    for task in tasks:
        result = result_by_query_id.get(task.query_id)
        if result is None:
            failed_tasks.append(
                {
                    "task_id": task.task_id,
                    "query_id": task.query_id,
                    "failed_subtasks": [
                        {
                            "subtask_id": subtask.subtask_id,
                            "subtask_name": subtask.name,
                            "failed_rubric_indexes": list(range(1, len(subtask.rubrics) + 1)),
                            "failed_rubric_count": len(subtask.rubrics),
                        }
                        for subtask in task.subtasks
                    ],
                }
            )
            continue
        if result["summary"]["task_passed"]:
            continue
        failed_subtasks = [s for s in result["subtask_results"] if not s["passed"]]
        failed_tasks.append(
            {
                "task_id": result["task_id"],
                "query_id": result["query_id"],
                "failed_subtasks": [
                    {
                        "subtask_id": s["subtask_id"],
                        "subtask_name": s["subtask_name"],
                        "failed_rubric_indexes": s["failed_rubric_indexes"],
                        "failed_rubric_count": len(s["failed_rubrics"]),
                    }
                    for s in failed_subtasks
                ],
            }
        )

    return {
        "totals": {
            "tasks_total": total_tasks,
            "tasks_passed": passed_tasks,
            "task_score_sum": total_task_score,
            "subtasks_total": total_subtasks,
            "subtasks_passed": passed_subtasks,
            "rubrics_total": total_rubrics,
            "rubrics_passed": passed_rubrics,
        },
        "rates": {
            "task_pass_rate": passed_tasks / total_tasks if total_tasks else 0.0,
            "task_score_avg": total_task_score / total_tasks if total_tasks else 0.0,
            "subtask_pass_rate": passed_subtasks / total_subtasks if total_subtasks else 0.0,
            "rubric_coverage": passed_rubrics / total_rubrics if total_rubrics else 0.0,
        },
        "failed_tasks": failed_tasks,
    }


def main() -> int:
    args = parse_args()
    benchmark_json = Path(args.benchmark_json).resolve()
    report_dir = Path(args.report_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not benchmark_json.is_file():
        print(f"benchmark JSON does not exist: {benchmark_json}", file=sys.stderr)
        return 1
    if not report_dir.is_dir():
        print(f"report directory does not exist: {report_dir}", file=sys.stderr)
        return 1

    tasks = load_tasks(benchmark_json, args.limit)
    output_dir.mkdir(parents=True, exist_ok=True)
    single_task_mode = len(tasks) == 1

    task_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for task in tasks:
        candidate_dir = report_dir / task.query_id
        report_path = find_report(candidate_dir)
        task_output_dir = output_dir if single_task_mode else (output_dir / task.query_id)
        prompt_path = task_output_dir / "judge_prompt.md"
        raw_output_path = task_output_dir / "judge_raw_output.txt"
        api_response_path = task_output_dir / "judge_api_response.json"
        result_path = task_output_dir / "task_evaluation.json"

        if args.skip_existing and result_path.is_file():
            task_results.append(load_json(result_path))
            continue

        if report_path is None:
            failures.append(
                {
                    "task_id": task.task_id,
                    "query_id": task.query_id,
                    "stage": "report_lookup",
                    "error": f"report.md or insights.md not found under {candidate_dir}",
                }
            )
            continue

        report_text = read_text(report_path)
        judge_prompt = build_judge_prompt(task, report_text)
        task_output_dir.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(judge_prompt, encoding="utf-8")

        if args.prepare_only:
            continue

        try:
            raw_output, api_response = run_judge(
                judge_prompt,
                args.judge_api_url,
                args.judge_api_key,
                args.judge_model,
                args.temperature,
                args.max_output_tokens,
            )
        except Exception as exc:
            failures.append(
                {
                    "task_id": task.task_id,
                    "query_id": task.query_id,
                    "stage": "judge_execution",
                    "error": str(exc),
                    "judge_prompt_path": str(prompt_path),
                }
            )
            continue

        raw_output_path.write_text(raw_output, encoding="utf-8")
        write_json(api_response_path, api_response)

        try:
            judge_result = parse_judge_json(raw_output)
            normalized = normalize_judge_result(task, judge_result)
        except Exception as exc:
            failures.append(
                {
                    "task_id": task.task_id,
                    "query_id": task.query_id,
                    "stage": "judge_parsing",
                    "error": str(exc),
                    "judge_prompt_path": str(prompt_path),
                    "raw_output_file": str(raw_output_path),
                    "api_response_file": str(api_response_path),
                }
            )
            continue

        normalized["report_path"] = str(report_path)
        normalized["judge_prompt_path"] = str(prompt_path)
        normalized["judge_raw_output_path"] = str(raw_output_path)
        normalized["judge_api_response_path"] = str(api_response_path)
        write_json(result_path, normalized)
        task_results.append(normalized)

    summary = {
        "benchmark_json": str(benchmark_json),
        "report_dir": str(report_dir),
        "output_dir": str(output_dir),
        "prepare_only": args.prepare_only,
        "judge_api_url": args.judge_api_url,
        "judge_model": args.judge_model,
        "evaluated_tasks": len(task_results),
        "task_results": [
            {
                "task_id": r["task_id"],
                "query_id": r["query_id"],
                "result_path": str((output_dir / "task_evaluation.json") if single_task_mode else (output_dir / r["query_id"] / "task_evaluation.json")),
                "task_passed": r["summary"]["task_passed"],
                "task_score": r["summary"]["task_score"],
                "passed_subtasks": r["summary"]["passed_subtasks"],
                "total_subtasks": r["summary"]["total_subtasks"],
                "passed_rubrics": r["summary"]["passed_rubrics"],
                "total_rubrics": r["summary"]["total_rubrics"],
            }
            for r in task_results
        ],
        "aggregate": aggregate_results(task_results, tasks),
        "failures": failures,
    }
    write_json(output_dir / "summary.json", summary)

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
