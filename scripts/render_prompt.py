#!/usr/bin/env python3

import argparse

PROMPT_TEMPLATE = """
Read prompt.md and follow it strictly.

Data lake path: {{DATA_LAKE_PATH}}
Query: "{{QUERY}}"
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--data-lake-path", required=True)
    return parser.parse_args()


def render_from_template(template: str, query: str, data_lake_path: str) -> str:
    return template.replace("{{QUERY}}", query).replace("{{DATA_LAKE_PATH}}", data_lake_path)


def main() -> int:
    args = parse_args()
    text = render_from_template(PROMPT_TEMPLATE, args.query, args.data_lake_path)
    if not text.endswith("\n"):
        text += "\n"
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
