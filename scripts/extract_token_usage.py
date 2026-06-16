#!/usr/bin/env python3
"""Extract per-task token usage from local agent state."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


TASK_RE = re.compile(r"^(?P<lake>.+)_task_(?P<num>\d+)$")


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def codex_usage(run_root: Path) -> dict[str, Any]:
    source_files = sorted(run_root.glob("codex-home/sessions/**/*.jsonl"))
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
        "billable_tokens_estimate_no_cached_input": 0,
    }
    sessions = []
    usage_records = 0

    for source_file in source_files:
        last_total = None
        records = 0
        for obj in read_jsonl(source_file):
            if obj.get("type") != "event_msg":
                continue
            payload = obj.get("payload") or {}
            if payload.get("type") != "token_count":
                continue
            total = (payload.get("info") or {}).get("total_token_usage")
            if isinstance(total, dict):
                last_total = total
                records += 1
        if last_total is None:
            continue

        tokens = {
            "input_tokens": int(last_total.get("input_tokens") or 0),
            "cached_input_tokens": int(last_total.get("cached_input_tokens") or 0),
            "output_tokens": int(last_total.get("output_tokens") or 0),
            "reasoning_output_tokens": int(last_total.get("reasoning_output_tokens") or 0),
            "total_tokens": int(last_total.get("total_tokens") or 0),
        }
        tokens["billable_tokens_estimate_no_cached_input"] = (
            max(tokens["input_tokens"] - tokens["cached_input_tokens"], 0)
            + tokens["output_tokens"]
            + tokens["reasoning_output_tokens"]
        )
        for key, value in tokens.items():
            totals[key] += value
        usage_records += records
        sessions.append(
            {
                "source_file": str(source_file),
                "token_count_records": records,
                "tokens": tokens,
            }
        )

    return {
        "tool": "codex",
        "source_files": [str(path) for path in source_files],
        "usage_records": usage_records,
        "sessions": sessions,
        "tokens": totals,
    }


def claude_usage(run_root: Path) -> dict[str, Any]:
    source_files = sorted(run_root.glob("claude-home/.claude/projects/**/*.jsonl"))
    totals = {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "total_including_cache_read": 0,
        "billable_tokens_estimate_no_cache_read": 0,
    }
    models: dict[str, int] = {}
    usage_records = 0

    for source_file in source_files:
        for obj in read_jsonl(source_file):
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            usage_records += 1
            model = message.get("model")
            if model:
                models[model] = models.get(model, 0) + 1

            input_tokens = int(usage.get("input_tokens") or 0)
            cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
            cache_read = int(usage.get("cache_read_input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            totals["input_tokens"] += input_tokens
            totals["cache_creation_input_tokens"] += cache_creation
            totals["cache_read_input_tokens"] += cache_read
            totals["output_tokens"] += output_tokens
            totals["total_including_cache_read"] += (
                input_tokens + cache_creation + cache_read + output_tokens
            )
            totals["billable_tokens_estimate_no_cache_read"] += (
                input_tokens + cache_creation + output_tokens
            )

    return {
        "tool": "claude-code",
        "source_files": [str(path) for path in source_files],
        "usage_records": usage_records,
        "models": models,
        "tokens": totals,
    }


def opencode_usage(run_root: Path) -> dict[str, Any]:
    db_candidates = [
        run_root / "opencode-home/.local/share/opencode/opencode.db",
        run_root / ".local/share/opencode/opencode.db",
    ]
    db_path = next((path for path in db_candidates if path.exists()), db_candidates[0])
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_including_cache": 0,
        "billable_tokens_estimate_no_cache_read": 0,
    }
    sessions = []
    cost = 0.0

    if db_path.exists():
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """
                select id, title, model, cost, tokens_input, tokens_output,
                       tokens_reasoning, tokens_cache_read, tokens_cache_write
                from session
                """
            ).fetchall()
        finally:
            con.close()

        for row in rows:
            session = dict(row)
            sessions.append(session)
            cost += float(row["cost"] or 0)
            totals["input_tokens"] += int(row["tokens_input"] or 0)
            totals["output_tokens"] += int(row["tokens_output"] or 0)
            totals["reasoning_tokens"] += int(row["tokens_reasoning"] or 0)
            totals["cache_read_tokens"] += int(row["tokens_cache_read"] or 0)
            totals["cache_write_tokens"] += int(row["tokens_cache_write"] or 0)

    totals["total_including_cache"] = sum(
        totals[key]
        for key in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        )
    )
    totals["billable_tokens_estimate_no_cache_read"] = (
        totals["input_tokens"]
        + totals["output_tokens"]
        + totals["reasoning_tokens"]
        + totals["cache_write_tokens"]
    )

    return {
        "tool": "opencode",
        "source_db": str(db_path),
        "source_db_candidates": [str(path) for path in db_candidates],
        "sessions": sessions,
        "tokens": totals,
        "cost": cost,
    }


def extract_task(tool: str, run_root: Path, output_dir: Path) -> dict[str, Any]:
    extractors = {
        "codex": codex_usage,
        "claude-code": claude_usage,
        "opencode": opencode_usage,
    }
    usage = extractors[tool](run_root)
    write_json(output_dir / "token_usage.json", usage)
    return usage


def summarize_output(output_dir: Path) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    totals: dict[str, float] = {}
    for token_file in sorted(output_dir.glob("*/token_usage.json")):
        task_id = token_file.parent.name
        try:
            usage = json.loads(token_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tasks[task_id] = usage
        for key, value in (usage.get("tokens") or {}).items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            totals["cost"] = totals.get("cost", 0) + cost

    summary = {
        "output_dir": str(output_dir),
        "task_count": len(tasks),
        "totals": totals,
        "tasks": tasks,
    }
    write_json(output_dir / "token_usage_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    task_parser = subparsers.add_parser("task")
    task_parser.add_argument("--tool", required=True, choices=["codex", "claude-code", "opencode"])
    task_parser.add_argument("--run-root", required=True, type=Path)
    task_parser.add_argument("--output-dir", required=True, type=Path)

    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--output-dir", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "task":
        usage = extract_task(args.tool, args.run_root, args.output_dir)
        print(json.dumps({"output_dir": str(args.output_dir), "tokens": usage.get("tokens", {})}))
    else:
        summary = summarize_output(args.output_dir)
        print(json.dumps({"output_dir": str(args.output_dir), "totals": summary["totals"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
