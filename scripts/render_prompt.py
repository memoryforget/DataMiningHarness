#!/usr/bin/env python3

import argparse
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS_DIR = SCRIPT_DIR.parent
PROMPT_TEMPLATE_PATH = HARNESS_DIR / "prompt.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--data-lake-path", required=True)
    return parser.parse_args()


def render_from_template(template: str, query: str, data_lake_path: str) -> str:
    return (
        template.replace("{{analysis_query}}", query)
        .replace("{{data_lake_path}}", data_lake_path)
    )


def main() -> int:
    args = parse_args()
    text = render_from_template(
        PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8"),
        args.query,
        args.data_lake_path,
    )
    if not text.endswith("\n"):
        text += "\n"
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
