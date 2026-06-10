#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries-json", required=True)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.queries_json)
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    metadata = payload.get("data_lake_metadata")
    stage_2_tasks = payload.get("stage_2_tasks")
    if not isinstance(metadata, dict) or not isinstance(stage_2_tasks, list):
        print(
            "benchmark JSON must contain data_lake_metadata and stage_2_tasks",
            file=sys.stderr,
        )
        return 1

    lake_path = metadata.get("lake_path")
    if not isinstance(lake_path, str) or not lake_path.strip():
        print("benchmark JSON is missing data_lake_metadata.lake_path", file=sys.stderr)
        return 1

    domain = Path(lake_path).name
    emitted = 0
    for item in stage_2_tasks:
        if not isinstance(item, dict):
            continue

        task_id = item.get("task_id")
        prompt = item.get("agent_facing_prompt")
        if task_id is None or not isinstance(prompt, str) or not prompt.strip():
            continue

        query_id = f"{domain}_task_{task_id}"
        clean_text = prompt.replace("\t", " ").replace("\n", " ")
        clean_lake_path = lake_path.replace("\t", " ").replace("\n", " ")
        sys.stdout.write(f"{query_id}\t{clean_lake_path}\t{clean_text}\n")
        emitted += 1
        if args.limit is not None and emitted >= args.limit:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
