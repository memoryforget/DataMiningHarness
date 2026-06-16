#!/usr/bin/env python3
"""Summarize per-task run times for a benchmark output directory."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

START_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+start\s+(?P<task>\S+)")
END_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?:done|fail)\s+(?P<task>\S+)")


def parse_ts(value: str) -> int | None:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return None


def read_int(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value >= 0 else None


def parse_run_log(path: Path, task_id: str) -> dict[str, Any] | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    start_ts = None
    end_ts = None
    for line in lines:
        start_match = START_RE.match(line)
        if start_match and start_match.group("task") == task_id:
            start_ts = parse_ts(start_match.group("ts"))
            continue
        end_match = END_RE.match(line)
        if end_match and end_match.group("task") == task_id:
            end_ts = parse_ts(end_match.group("ts"))

    if start_ts is None or end_ts is None or end_ts < start_ts:
        return None
    return {
        "task_id": task_id,
        "run_time_seconds": end_ts - start_ts,
        "start_epoch_seconds": start_ts,
        "end_epoch_seconds": end_ts,
        "source": "run_log",
    }


def task_summary(task_dir: Path) -> dict[str, Any] | None:
    task_id = task_dir.name
    seconds = read_int(task_dir / "run_time_seconds.txt")
    if seconds is not None:
        summary = {
            "task_id": task_id,
            "run_time_seconds": seconds,
            "source": "run_time_seconds_txt",
        }
        summary_path = task_dir / "run_time_summary.json"
        try:
            saved = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                summary.update(saved)
                summary["source"] = "run_time_seconds_txt"
        except (OSError, json.JSONDecodeError):
            pass
        return summary
    return parse_run_log(task_dir / "run.log", task_id)


def summarize(output_dir: Path) -> dict[str, Any]:
    tasks = []
    missing = []
    for child in sorted(output_dir.iterdir() if output_dir.exists() else []):
        if not child.is_dir():
            continue
        summary = task_summary(child)
        if summary is None:
            missing.append(child.name)
            continue
        tasks.append(summary)

    total = sum(int(task.get("run_time_seconds") or 0) for task in tasks)
    payload = {
        "output_dir": str(output_dir),
        "task_count_with_time": len(tasks),
        "total_run_time_seconds": total,
        "tasks": tasks,
        "missing_task_times": missing,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_only_time_seconds.txt").write_text(f"{total}\n", encoding="utf-8")
    (output_dir / "run_time_summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    payload = summarize(args.output_dir)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "total_run_time_seconds": payload["total_run_time_seconds"],
        "task_count_with_time": payload["task_count_with_time"],
        "missing_task_times": payload["missing_task_times"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
