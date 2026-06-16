#!/usr/bin/env python3

import argparse
import csv
import math
import os
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_STEP_KEYS = {
    "STEP",
    "BASH",
    "INPUT_ARTIFACT",
    "OUTPUT_ARTIFACT",
    "INTERMEDIATE_RESULT",
}

NUMERIC_ABS_TOLERANCE = 1e-9
NUMERIC_REL_TOLERANCE = 1e-9
STRICT_HASH_SUFFIXES = {
    ".py",
    ".sh",
    ".sql",
    ".ipynb",
    ".xlsx",
    ".xls",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".parquet",
    ".pkl",
    ".pickle",
    ".zip",
}


@dataclass
class FileSnapshot:
    exists: bool
    is_dir: bool
    size: int | None
    mtime_ns: int | None
    digest: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--work-root", default=None)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--activate-script", default=None)
    parser.add_argument("--conda-env", default="daagent")
    parser.add_argument("--no-conda-activate", action="store_true")
    parser.add_argument("--judge-api-url", default=None)
    parser.add_argument("--judge-api-key", default=None)
    parser.add_argument("--judge-model", default="gpt-5.4")
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-output-tokens", type=int, default=1000)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def normalize_artifact_field(value: Any, field_name: str) -> list[str]:
    if value == "NONE":
        return []
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{field_name} must not be empty")
        return [value]
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{field_name} list items must be non-empty strings")
            normalized.append(item)
        return normalized
    raise ValueError(f"{field_name} must be a string, list of strings, or 'NONE'")


def ensure_schema(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list):
        raise ValueError("reproduction.json top level must be a list")
    normalized_steps: list[dict[str, Any]] = []
    expected_step = 1
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            raise ValueError("each step must be an object")
        missing = REQUIRED_STEP_KEYS - set(raw_step)
        if missing:
            raise ValueError(f"step is missing required keys: {sorted(missing)}")
        if raw_step["STEP"] != expected_step:
            raise ValueError(f"expected STEP={expected_step}, got {raw_step['STEP']}")
        if not isinstance(raw_step["BASH"], str) or not raw_step["BASH"].strip():
            raise ValueError(f"STEP {expected_step}: BASH must be a non-empty string")
        if not isinstance(raw_step["INTERMEDIATE_RESULT"], str) or not raw_step["INTERMEDIATE_RESULT"].strip():
            raise ValueError(f"STEP {expected_step}: INTERMEDIATE_RESULT must be a non-empty string")
        normalized_steps.append(
            {
                "STEP": expected_step,
                "BASH": raw_step["BASH"],
                "INPUT_ARTIFACT": normalize_artifact_field(raw_step["INPUT_ARTIFACT"], "INPUT_ARTIFACT"),
                "OUTPUT_ARTIFACT": normalize_artifact_field(raw_step["OUTPUT_ARTIFACT"], "OUTPUT_ARTIFACT"),
                "INTERMEDIATE_RESULT": raw_step["INTERMEDIATE_RESULT"],
            }
        )
        expected_step += 1
    return normalized_steps


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dir(path: Path) -> str:
    h = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        rel = child.relative_to(path).as_posix()
        h.update(rel.encode("utf-8"))
        if child.is_dir():
            h.update(b"\0dir\0")
            continue
        h.update(b"\0file\0")
        h.update(sha256_file(child).encode("ascii"))
    return h.hexdigest()


def snapshot_path(path: Path) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(False, False, None, None, None)
    stat = path.stat()
    if path.is_dir():
        return FileSnapshot(True, True, None, stat.st_mtime_ns, sha256_dir(path))
    return FileSnapshot(True, False, stat.st_size, stat.st_mtime_ns, sha256_file(path))


def candidate_to_workspace_path(candidate_dir: Path, workspace_dir: Path, artifact_path: str) -> Path | None:
    path = Path(artifact_path)
    if path.is_absolute():
        try:
            rel = path.relative_to(candidate_dir)
        except ValueError:
            return None
        return workspace_dir / rel
    return workspace_dir / path


def candidate_artifact_path(candidate_dir: Path, artifact_path: str) -> Path | None:
    path = Path(artifact_path)
    if path.is_absolute():
        try:
            path.relative_to(candidate_dir)
        except ValueError:
            return None
        return path
    return candidate_dir / path


def normalize_artifact_key(candidate_dir: Path, artifact_path: str) -> str | None:
    path = Path(artifact_path)
    if path.is_absolute():
        try:
            return path.relative_to(candidate_dir).as_posix()
        except ValueError:
            return None
    return path.as_posix()


def validate_artifact_content(path: Path) -> list[str]:
    errors: list[str] = []
    if path.is_dir():
        if not any(path.iterdir()):
            errors.append("directory output is empty")
        return errors
    if path.stat().st_size == 0:
        errors.append("file output is empty")
        return errors

    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            load_json(path)
        elif suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh, delimiter=delimiter)
                header = next(reader, None)
                if not header or not any(cell.strip() for cell in header):
                    errors.append(f"{suffix} output has no non-empty header row")
        elif suffix in {".md", ".txt", ".log"}:
            if not path.read_text(encoding="utf-8", errors="replace").strip():
                errors.append(f"{suffix} output has no non-whitespace text")
    except Exception as exc:
        errors.append(f"{suffix or 'file'} output failed basic validation: {exc}")
    return errors


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numbers_close(left: float, right: float) -> bool:
    return math.isclose(
        float(left),
        float(right),
        rel_tol=NUMERIC_REL_TOLERANCE,
        abs_tol=NUMERIC_ABS_TOLERANCE,
    )


def compare_json_values(left: Any, right: Any, path: str, errors: list[str]) -> None:
    if is_number(left) and is_number(right):
        if not numbers_close(float(left), float(right)):
            errors.append(f"{path}: numeric mismatch {left!r} != {right!r}")
        return
    if type(left) is not type(right):
        errors.append(f"{path}: type mismatch {type(left).__name__} != {type(right).__name__}")
        return
    if isinstance(left, dict):
        left_keys = set(left)
        right_keys = set(right)
        if left_keys != right_keys:
            missing = sorted(left_keys - right_keys)
            extra = sorted(right_keys - left_keys)
            if missing:
                errors.append(f"{path}: missing keys in replay {missing[:10]}")
            if extra:
                errors.append(f"{path}: extra keys in replay {extra[:10]}")
            return
        for key in sorted(left_keys):
            compare_json_values(left[key], right[key], f"{path}.{key}", errors)
        return
    if isinstance(left, list):
        if len(left) != len(right):
            errors.append(f"{path}: list length mismatch {len(left)} != {len(right)}")
            return
        for idx, (left_item, right_item) in enumerate(zip(left, right)):
            compare_json_values(left_item, right_item, f"{path}[{idx}]", errors)
        return
    if left != right:
        errors.append(f"{path}: value mismatch {left!r} != {right!r}")


def compare_json_files(candidate_path: Path, replay_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        candidate_obj = load_json(candidate_path)
        replay_obj = load_json(replay_path)
    except Exception as exc:
        return [f"json parse failed: {exc}"]
    compare_json_values(candidate_obj, replay_obj, "$", errors)
    return errors


def parse_float_cell(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def is_floatish_cell(value: str) -> bool:
    text = value.strip().lower()
    return "." in text or "e" in text


def compare_csv_files(candidate_path: Path, replay_path: Path, delimiter: str) -> list[str]:
    errors: list[str] = []
    try:
        with candidate_path.open("r", encoding="utf-8", newline="") as left_fh:
            candidate_rows = list(csv.reader(left_fh, delimiter=delimiter))
        with replay_path.open("r", encoding="utf-8", newline="") as right_fh:
            replay_rows = list(csv.reader(right_fh, delimiter=delimiter))
    except Exception as exc:
        return [f"table parse failed: {exc}"]

    if len(candidate_rows) != len(replay_rows):
        errors.append(f"row count mismatch {len(candidate_rows)} != {len(replay_rows)}")
        return errors
    if not candidate_rows:
        return errors
    if [cell.strip() for cell in candidate_rows[0]] != [cell.strip() for cell in replay_rows[0]]:
        errors.append("header mismatch")
        return errors

    for row_idx, (candidate_row, replay_row) in enumerate(zip(candidate_rows[1:], replay_rows[1:]), start=2):
        if len(candidate_row) != len(replay_row):
            errors.append(f"row {row_idx}: column count mismatch {len(candidate_row)} != {len(replay_row)}")
            if len(errors) >= 20:
                return errors
            continue
        for col_idx, (candidate_cell, replay_cell) in enumerate(zip(candidate_row, replay_row), start=1):
            left = candidate_cell.strip()
            right = replay_cell.strip()
            left_num = parse_float_cell(left)
            right_num = parse_float_cell(right)
            if (
                left_num is not None
                and right_num is not None
                and (is_floatish_cell(left) or is_floatish_cell(right))
            ):
                if not numbers_close(left_num, right_num):
                    errors.append(f"row {row_idx} col {col_idx}: numeric mismatch {left!r} != {right!r}")
            elif left != right:
                errors.append(f"row {row_idx} col {col_idx}: value mismatch {left!r} != {right!r}")
            if len(errors) >= 20:
                return errors
    return errors


def normalize_text_for_compare(text: str) -> str:
    normalized_lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.rstrip()
        line = re.sub(r"(?<!:)/{2,}", "/", line)
        normalized_lines.append(line)
    while normalized_lines and normalized_lines[-1] == "":
        normalized_lines.pop()
    return "\n".join(normalized_lines)


def compare_text_files(candidate_path: Path, replay_path: Path) -> list[str]:
    try:
        candidate_text = candidate_path.read_text(encoding="utf-8", errors="replace")
        replay_text = replay_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return [f"text read failed: {exc}"]
    if normalize_text_for_compare(candidate_text) != normalize_text_for_compare(replay_text):
        return ["normalized text mismatch"]
    return []


def read_text_for_grader(path: Path, max_chars: int = 20000) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    if truncated:
        return text[:max_chars], True
    return text, False


def build_text_equivalence_prompt(artifact_label: str, candidate_text: str, replay_text: str) -> str:
    schema = {
        "equivalent": True,
        "confidence": "high|medium|low",
        "reason": "short explanation",
    }
    return f"""You are a strict artifact equivalence grader.

Decide whether the two text artifacts are semantically equivalent for benchmark replay.
Pass only if the same facts, records, metrics, fields, and conclusions are preserved.
Ignore harmless differences in whitespace, path prefixes, formatting, and tiny floating-point rounding.
Fail if records, counts, values, fields, or substantive text differ. If unsure, fail.

Return strict JSON only using this schema:
{json.dumps(schema, ensure_ascii=False)}

Artifact path: {artifact_label}

Candidate artifact:
```text
{candidate_text}
```

Replay artifact:
```text
{replay_text}
```
"""


def post_json(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
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
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc}") from exc
    return json.loads(raw)


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


def run_text_equivalence_judge(
    candidate_path: Path,
    replay_path: Path,
    artifact_label: str,
    judge_api_url: str | None,
    judge_api_key: str | None,
    judge_model: str,
    judge_temperature: float,
    judge_max_output_tokens: int,
) -> dict[str, Any]:
    if not judge_api_url or not judge_api_key:
        return {
            "attempted": False,
            "equivalent": False,
            "error": "judge API URL or key was not provided",
        }
    try:
        candidate_text, candidate_truncated = read_text_for_grader(candidate_path)
        replay_text, replay_truncated = read_text_for_grader(replay_path)
        prompt = build_text_equivalence_prompt(artifact_label, candidate_text, replay_text)
        payload = {
            "model": judge_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": judge_temperature,
            "max_tokens": judge_max_output_tokens,
        }
        response = post_json(judge_api_url.rstrip("/") + "/chat/completions", judge_api_key, payload)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("judge response missing choices")
        message = choices[0].get("message", {})
        raw_output = message.get("content")
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise ValueError("judge response missing message content")
        judge_result = parse_judge_json(raw_output)
        equivalent = bool(judge_result.get("equivalent"))
        return {
            "attempted": True,
            "equivalent": equivalent,
            "confidence": str(judge_result.get("confidence", "unknown")),
            "reason": str(judge_result.get("reason", "")),
            "candidate_truncated": candidate_truncated,
            "replay_truncated": replay_truncated,
            "raw_output": raw_output,
        }
    except Exception as exc:
        return {
            "attempted": True,
            "equivalent": False,
            "error": str(exc),
        }


def compare_artifact_files(
    candidate_path: Path,
    replay_path: Path,
    *,
    artifact_label: str | None = None,
    judge_api_url: str | None = None,
    judge_api_key: str | None = None,
    judge_model: str = "gpt-5.4",
    judge_temperature: float = 0.0,
    judge_max_output_tokens: int = 1000,
) -> dict[str, Any]:
    suffix = candidate_path.suffix.lower()
    if suffix != replay_path.suffix.lower():
        return {
            "matched": False,
            "method": "suffix",
            "errors": [f"suffix mismatch {candidate_path.suffix!r} != {replay_path.suffix!r}"],
        }
    if suffix == ".json":
        errors = compare_json_files(candidate_path, replay_path)
        return {"matched": not errors, "method": "json_tolerant", "errors": errors[:20]}
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        errors = compare_csv_files(candidate_path, replay_path, delimiter)
        return {"matched": not errors, "method": f"{suffix[1:]}_tolerant", "errors": errors[:20]}
    if suffix in {".txt", ".log", ".md"}:
        errors = compare_text_files(candidate_path, replay_path)
        result: dict[str, Any] = {"matched": not errors, "method": "text_normalized", "errors": errors[:20]}
        if errors:
            semantic_result = run_text_equivalence_judge(
                candidate_path,
                replay_path,
                artifact_label or candidate_path.name,
                judge_api_url,
                judge_api_key,
                judge_model,
                judge_temperature,
                judge_max_output_tokens,
            )
            result["semantic_text_judge"] = semantic_result
            if semantic_result.get("equivalent") is True:
                result["matched"] = True
                result["method"] = "text_semantic_judge"
                result["errors"] = []
        return result

    matched = sha256_file(candidate_path) == sha256_file(replay_path)
    return {
        "matched": matched,
        "method": "strict_hash" if suffix in STRICT_HASH_SUFFIXES else "strict_hash_default",
        "errors": [] if matched else ["sha256 mismatch"],
    }


def compare_artifact_paths(
    candidate_path: Path,
    replay_path: Path,
    *,
    artifact_label: str | None = None,
    judge_api_url: str | None = None,
    judge_api_key: str | None = None,
    judge_model: str = "gpt-5.4",
    judge_temperature: float = 0.0,
    judge_max_output_tokens: int = 1000,
) -> dict[str, Any]:
    if not candidate_path.exists() or not replay_path.exists():
        return {"matched": False, "method": "existence", "errors": ["candidate or replay path is missing"]}
    if candidate_path.is_dir() != replay_path.is_dir():
        return {"matched": False, "method": "path_type", "errors": ["file/directory type mismatch"]}
    if candidate_path.is_dir():
        candidate_files = sorted(path.relative_to(candidate_path).as_posix() for path in candidate_path.rglob("*") if path.is_file())
        replay_files = sorted(path.relative_to(replay_path).as_posix() for path in replay_path.rglob("*") if path.is_file())
        if candidate_files != replay_files:
            missing = sorted(set(candidate_files) - set(replay_files))
            extra = sorted(set(replay_files) - set(candidate_files))
            errors = []
            if missing:
                errors.append(f"missing files in replay {missing[:10]}")
            if extra:
                errors.append(f"extra files in replay {extra[:10]}")
            return {"matched": False, "method": "directory_listing", "errors": errors}
        errors: list[str] = []
        methods: set[str] = set()
        semantic_text_judges: dict[str, Any] = {}
        for rel in candidate_files:
            child_result = compare_artifact_files(
                candidate_path / rel,
                replay_path / rel,
                artifact_label=f"{artifact_label or candidate_path.name}/{rel}",
                judge_api_url=judge_api_url,
                judge_api_key=judge_api_key,
                judge_model=judge_model,
                judge_temperature=judge_temperature,
                judge_max_output_tokens=judge_max_output_tokens,
            )
            methods.add(str(child_result["method"]))
            if "semantic_text_judge" in child_result:
                semantic_text_judges[rel] = child_result["semantic_text_judge"]
            if not child_result["matched"]:
                errors.append(f"{rel}: {'; '.join(child_result['errors'])}")
                if len(errors) >= 20:
                    break
        result = {"matched": not errors, "method": "directory_adaptive:" + ",".join(sorted(methods)), "errors": errors}
        if semantic_text_judges:
            result["semantic_text_judges"] = semantic_text_judges
        return result
    return compare_artifact_files(
        candidate_path,
        replay_path,
        artifact_label=artifact_label,
        judge_api_url=judge_api_url,
        judge_api_key=judge_api_key,
        judge_model=judge_model,
        judge_temperature=judge_temperature,
        judge_max_output_tokens=judge_max_output_tokens,
    )


def is_within(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def copy_candidate_inputs(candidate_dir: Path, workspace_dir: Path, input_artifacts: list[str]) -> list[str]:
    copied: list[str] = []
    candidate_dir = candidate_dir.resolve()
    workspace_dir = workspace_dir.resolve()
    for artifact in input_artifacts:
        raw_src = Path(artifact)
        src = raw_src if raw_src.is_absolute() else (candidate_dir / raw_src)
        try:
            src = src.resolve(strict=False)
        except OSError:
            continue
        if not is_within(candidate_dir, src):
            continue
        if not src.exists():
            continue
        rel = src.relative_to(candidate_dir)
        dst = workspace_dir / rel
        try:
            dst_resolved = dst.resolve(strict=False)
        except OSError:
            continue
        if not is_within(workspace_dir, dst_resolved):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        copied.append(str(rel))
    return copied


def write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def default_activate_script() -> Path | None:
    workspace_root = Path(__file__).resolve().parents[2]
    candidate = workspace_root / "activate_my_base.sh"
    return candidate if candidate.is_file() else None


def build_replay_command(raw_command: str, activate_script: Path | None, conda_env: str | None, use_conda: bool) -> str:
    setup_parts: list[str] = []
    if use_conda and activate_script is not None:
        setup_parts.append(f"source {shell_quote(str(activate_script))}")
        if conda_env:
            setup_parts.append(f"conda activate {shell_quote(conda_env)}")
    if not setup_parts:
        return raw_command
    return " && ".join(setup_parts + [raw_command])


def default_tmp_root() -> Path:
    workspace_root = Path(__file__).resolve().parents[2]
    value = os.environ.get("TMP_ROOT")
    if value:
        return Path(value).resolve()
    return workspace_root / "tmp"


def local_batch_replay_root(candidate_dir: Path, output_json: Path) -> Path:
    digest = hashlib.sha1(str(output_json).encode("utf-8")).hexdigest()[:10]
    return default_tmp_root() / f"stage1-local-batch_{candidate_dir.name}_{digest}"


def main() -> int:
    args = parse_args()
    candidate_dir = Path(args.candidate_dir).resolve()
    output_json = Path(args.output_json).resolve()
    reproduction_path = candidate_dir / "artifacts" / "reproduction.json"
    activate_script = Path(args.activate_script).resolve() if args.activate_script else default_activate_script()
    use_conda_activate = not args.no_conda_activate and activate_script is not None

    if not candidate_dir.is_dir():
        print(f"candidate directory does not exist: {candidate_dir}", file=sys.stderr)
        return 1
    if not reproduction_path.is_file():
        print(f"reproduction.json not found: {reproduction_path}", file=sys.stderr)
        return 1

    result: dict[str, Any] = {
        "candidate_dir": str(candidate_dir),
        "reproduction_json": str(reproduction_path),
        "schema_pass": False,
        "execution_pass": False,
        "steps": [],
    }

    try:
        steps = ensure_schema(load_json(reproduction_path))
    except Exception as exc:
        result["error"] = f"schema validation failed: {exc}"
        write_result(output_json, result)
        return 1

    result["schema_pass"] = True

    declared_outputs: set[str] = set()
    invalid_declared_outputs: list[str] = []
    output_counts: dict[str, int] = {}
    output_last_step: dict[str, int] = {}
    for step in steps:
        for output_artifact in step["OUTPUT_ARTIFACT"]:
            key = normalize_artifact_key(candidate_dir, output_artifact)
            if key == "artifacts":
                continue
            if key is None or not key.startswith("artifacts/"):
                invalid_declared_outputs.append(output_artifact)
                continue
            declared_outputs.add(key)
            output_counts[key] = output_counts.get(key, 0) + 1
            output_last_step[key] = step["STEP"]

    duplicate_outputs = sorted(path for path, count in output_counts.items() if count > 1)
    result["artifact_declaration_check"] = {
        "declared_outputs": sorted(declared_outputs),
        "invalid_declared_outputs": invalid_declared_outputs,
        "duplicate_outputs": duplicate_outputs,
        "duplicate_output_policy": "allowed_last_writer_wins",
        "passed": not invalid_declared_outputs,
    }
    if not result["artifact_declaration_check"]["passed"]:
        result["error"] = "artifact declaration validation failed"
        write_result(output_json, result)
        return 1

    if args.work_root:
        work_root = Path(args.work_root).resolve()
        replay_root = work_root
        workspace_dir = work_root
        logs_dir = output_json.parent / "replay_logs"
    else:
        work_root = candidate_dir.parent / f"{candidate_dir.name}_stage1_replay"
        replay_root = local_batch_replay_root(candidate_dir, output_json)
        workspace_dir = replay_root / candidate_dir.name / "workspace"
        logs_dir = work_root / "logs"

    if work_root.exists():
        shutil.rmtree(work_root)
    if not args.work_root and replay_root.exists():
        shutil.rmtree(replay_root)
    if logs_dir.exists():
        shutil.rmtree(logs_dir)
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True)

    overall_pass = True

    for step in steps:
        step_no = step["STEP"]
        step_log = logs_dir / f"step_{step_no}.log"
        copied_inputs = copy_candidate_inputs(candidate_dir, workspace_dir, step["INPUT_ARTIFACT"])

        output_paths: dict[str, Path] = {}
        candidate_output_snapshots: dict[str, FileSnapshot] = {}
        invalid_output_paths: list[str] = []
        skipped_duplicate_outputs: list[str] = []
        for output_artifact in step["OUTPUT_ARTIFACT"]:
            key = normalize_artifact_key(candidate_dir, output_artifact)
            if key is not None and output_last_step.get(key) != step_no:
                skipped_duplicate_outputs.append(output_artifact)
                continue
            path = candidate_to_workspace_path(candidate_dir, workspace_dir, output_artifact)
            candidate_path = candidate_artifact_path(candidate_dir, output_artifact)
            if path is None or candidate_path is None:
                invalid_output_paths.append(output_artifact)
                continue
            try:
                path_resolved = path.resolve(strict=False)
            except OSError:
                invalid_output_paths.append(output_artifact)
                continue
            if not is_within(workspace_dir, path_resolved):
                invalid_output_paths.append(output_artifact)
                continue
            output_paths[output_artifact] = path
            candidate_output_snapshots[output_artifact] = snapshot_path(candidate_path)

        started_at = time.time()
        env = os.environ.copy()
        python_bin_dir = Path(sys.executable).resolve().parent
        codex_bin_dir = Path.home() / ".npm-global" / "bin"
        rg_path = (
            Path.home()
            / ".npm-global"
            / "lib"
            / "node_modules"
            / "@openai"
            / "codex"
            / "node_modules"
            / "@openai"
            / "codex-linux-x64"
            / "vendor"
            / "x86_64-unknown-linux-musl"
            / "path"
            / "rg"
        )
        path_entries = [str(python_bin_dir), str(codex_bin_dir)]
        if rg_path.is_file():
            path_entries.append(str(rg_path.parent))
        existing_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(path_entries + ([existing_path] if existing_path else []))
        try:
            replay_command = build_replay_command(
                step["BASH"],
                activate_script,
                args.conda_env,
                use_conda_activate,
            )
            proc = subprocess.run(
                replay_command,
                shell=True,
                cwd=workspace_dir,
                executable="/bin/bash",
                text=True,
                capture_output=True,
                timeout=args.timeout_seconds,
                env=env,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            proc = subprocess.CompletedProcess(
                args=step["BASH"],
                returncode=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + f"\nTimed out after {args.timeout_seconds} seconds.",
            )
        duration_seconds = time.time() - started_at

        step_log.write_text(
            (proc.stdout or "")
            + ("\n" if proc.stdout and not proc.stdout.endswith("\n") else "")
            + (proc.stderr or ""),
            encoding="utf-8",
        )

        output_checks: list[dict[str, Any]] = []
        missing_outputs: list[str] = []
        missing_candidate_outputs: list[str] = []
        digest_mismatches: list[str] = []
        artifact_comparison_errors: dict[str, list[str]] = {}
        content_validation_errors: dict[str, list[str]] = {}
        for output_artifact, path in output_paths.items():
            after = snapshot_path(path)
            expected = candidate_output_snapshots[output_artifact]
            content_errors = validate_artifact_content(path) if after.exists else []
            candidate_path = candidate_artifact_path(candidate_dir, output_artifact)
            comparison = (
                compare_artifact_paths(
                    candidate_path,
                    path,
                    artifact_label=normalize_artifact_key(candidate_dir, output_artifact) or output_artifact,
                    judge_api_url=args.judge_api_url,
                    judge_api_key=args.judge_api_key,
                    judge_model=args.judge_model,
                    judge_temperature=args.judge_temperature,
                    judge_max_output_tokens=args.judge_max_output_tokens,
                )
                if after.exists and expected.exists and candidate_path is not None
                else {"matched": False, "method": "not_compared", "errors": []}
            )
            output_checks.append(
                {
                    "artifact": output_artifact,
                    "path": str(path),
                    "exists_after": after.exists,
                    "candidate_exists": expected.exists,
                    "digest_match": comparison["matched"],
                    "comparison_method": comparison["method"],
                    "comparison_errors": comparison["errors"],
                    "semantic_text_judge": comparison.get("semantic_text_judge"),
                    "semantic_text_judges": comparison.get("semantic_text_judges"),
                    "content_errors": content_errors,
                }
            )
            if not after.exists:
                missing_outputs.append(output_artifact)
            elif not expected.exists:
                missing_candidate_outputs.append(output_artifact)
            elif not comparison["matched"]:
                digest_mismatches.append(output_artifact)
                artifact_comparison_errors[output_artifact] = comparison["errors"]
            if content_errors:
                content_validation_errors[output_artifact] = content_errors

        step_pass = (
            proc.returncode == 0
            and not invalid_output_paths
            and not missing_outputs
            and not missing_candidate_outputs
            and not digest_mismatches
            and not content_validation_errors
        )
        if not step_pass:
            overall_pass = False

        result["steps"].append(
            {
                "step": step_no,
                "bash": step["BASH"],
                "replay_command": replay_command if "replay_command" in locals() else step["BASH"],
                "activate_script": str(activate_script) if use_conda_activate and activate_script is not None else None,
                "conda_env": args.conda_env if use_conda_activate else None,
                "copied_input_artifacts": copied_inputs,
                "returncode": proc.returncode,
                "timed_out": timed_out,
                "duration_seconds": round(duration_seconds, 3),
                "log_file": str(step_log),
                "invalid_output_paths": invalid_output_paths,
                "skipped_duplicate_outputs": skipped_duplicate_outputs,
                "missing_outputs": missing_outputs,
                "missing_candidate_outputs": missing_candidate_outputs,
                "digest_mismatches": digest_mismatches,
                "artifact_comparison_errors": artifact_comparison_errors,
                "content_validation_errors": content_validation_errors,
                "output_checks": output_checks,
                "passed": step_pass,
            }
        )

        if not step_pass:
            break

    result["execution_pass"] = overall_pass
    result["work_root"] = str(work_root)
    result["replay_root"] = str(replay_root)
    result["workspace"] = str(workspace_dir)
    result["logs_dir"] = str(logs_dir)

    write_result(output_json, result)

    if not args.keep_workspace and overall_pass:
        if work_root.exists():
            shutil.rmtree(work_root)
        if replay_root != work_root and replay_root.exists():
            shutil.rmtree(replay_root)

    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
