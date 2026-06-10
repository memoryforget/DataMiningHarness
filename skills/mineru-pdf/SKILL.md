---
name: mineru-pdf
description: Use this skill when PDF evidence matters. It must call the local mineru wrapper, produce markdown plus extracted assets, and use those artifacts for reproducible evidence collection.
---

# `mineru-pdf`

Use this skill when PDF files matter and default PDF reading is not reliable enough.

## When to use

Trigger this skill when at least one of these is true:

1. The task depends on PDF evidence.
2. Native PDF extraction loses structure, tables, or figure references.
3. You need a reproducible markdown artifact and extracted image paths for the final trace.

## What this skill gives you

The bundled local-wrapper script runs the local mineru wrapper and returns a JSON summary with:

1. `markdown_path`
2. `extract_dir`
3. `backend`
4. `warnings`
5. `error`

The markdown file is the primary reading surface. If the markdown references extracted figures, inspect those image files with the host agent's image tool.

## Run it

From the repository root:

```bash
python scripts/mineru.py --pdf /absolute/path/to/file.pdf
```

Allowed variant:

```bash
python scripts/mineru.py --pdf /absolute/path/to/file.pdf --output-dir ./mineru_runs
```

## Workflow

1. Parse the target PDF by invoking `python scripts/mineru.py --pdf ...` from this repository.
2. Do not bypass this wrapper with direct `mineru` CLI calls.
3. Open the returned markdown file.
4. Use `grep` on the markdown to find entities, metrics, sections, or keywords.
5. If the markdown points to extracted figures, inspect the image files directly.
6. In the final reproduction trace, cite the markdown path and any relevant image paths.

## Rules

1. PDF-dependent tasks must go through `scripts/mineru.py`; do not read the PDF with any other primary extraction path unless the wrapper fails first.
2. Do not override the backend to `api`; keep the wrapper on the local backend path.
3. Do not rely on a single snippet; read enough local context to verify the claim.
4. Do not fabricate PDF evidence when parsing fails.
5. If parsing fails, report the failure and continue with other evidence if possible.
6. Prefer markdown text as the default source, and use extracted images only when they add evidence the text does not preserve.
7. If `MINERU_LOCAL_API_URL` is set, expect the wrapper to route local parsing through that API endpoint; do not replace it with a different endpoint unless explicitly instructed.
