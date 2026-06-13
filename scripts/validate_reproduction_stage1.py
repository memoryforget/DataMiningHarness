#!/usr/bin/env python3

import argparse
import os
import hashlib
import json
import shutil
import subprocess
import sys
import time
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


def snapshot_path(path: Path) -> FileSnapshot:
    if not path.exists():
        return FileSnapshot(False, False, None, None, None)
    stat = path.stat()
    if path.is_dir():
        return FileSnapshot(True, True, None, stat.st_mtime_ns, None)
    return FileSnapshot(True, False, stat.st_size, stat.st_mtime_ns, sha256_file(path))


def was_created_or_changed(before: FileSnapshot, after: FileSnapshot) -> bool:
    if not after.exists:
        return False
    if not before.exists:
        return True
    if before.is_dir or after.is_dir:
        return before.mtime_ns != after.mtime_ns
    return (
        before.size != after.size
        or before.mtime_ns != after.mtime_ns
        or before.digest != after.digest
    )


def candidate_to_workspace_path(candidate_dir: Path, workspace_dir: Path, artifact_path: str) -> Path | None:
    path = Path(artifact_path)
    if path.is_absolute():
        try:
            rel = path.relative_to(candidate_dir)
        except ValueError:
            return None
        return workspace_dir / rel
    return workspace_dir / path


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


def main() -> int:
    args = parse_args()
    candidate_dir = Path(args.candidate_dir).resolve()
    output_json = Path(args.output_json).resolve()
    reproduction_path = candidate_dir / "artifacts" / "reproduction.json"

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

    work_root = Path(args.work_root).resolve() if args.work_root else candidate_dir.parent / f"{candidate_dir.name}_stage1_replay"
    workspace_dir = work_root / "workspace"
    logs_dir = work_root / "logs"

    if work_root.exists():
        shutil.rmtree(work_root)
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True)

    overall_pass = True

    for step in steps:
        step_no = step["STEP"]
        step_log = logs_dir / f"step_{step_no}.log"
        copied_inputs = copy_candidate_inputs(candidate_dir, workspace_dir, step["INPUT_ARTIFACT"])

        output_snapshots_before: dict[str, FileSnapshot] = {}
        output_paths: dict[str, Path] = {}
        for output_artifact in step["OUTPUT_ARTIFACT"]:
            path = candidate_to_workspace_path(candidate_dir, workspace_dir, output_artifact)
            if path is None:
                continue
            output_paths[output_artifact] = path
            output_snapshots_before[output_artifact] = snapshot_path(path)

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
            proc = subprocess.run(
                step["BASH"],
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
        unchanged_outputs: list[str] = []
        for output_artifact, path in output_paths.items():
            after = snapshot_path(path)
            before = output_snapshots_before[output_artifact]
            changed = was_created_or_changed(before, after)
            output_checks.append(
                {
                    "artifact": output_artifact,
                    "path": str(path),
                    "exists_after": after.exists,
                    "changed": changed,
                }
            )
            if not after.exists:
                missing_outputs.append(output_artifact)
            elif not changed:
                unchanged_outputs.append(output_artifact)

        step_pass = proc.returncode == 0 and not missing_outputs and not unchanged_outputs
        if not step_pass:
            overall_pass = False

        result["steps"].append(
            {
                "step": step_no,
                "bash": step["BASH"],
                "copied_input_artifacts": copied_inputs,
                "returncode": proc.returncode,
                "timed_out": timed_out,
                "duration_seconds": round(duration_seconds, 3),
                "log_file": str(step_log),
                "missing_outputs": missing_outputs,
                "unchanged_outputs": unchanged_outputs,
                "output_checks": output_checks,
                "passed": step_pass,
            }
        )

        if not step_pass:
            break

    result["execution_pass"] = overall_pass
    result["workspace"] = str(workspace_dir)
    result["logs_dir"] = str(logs_dir)

    write_result(output_json, result)

    if not args.keep_workspace and overall_pass:
        shutil.rmtree(work_root)

    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
