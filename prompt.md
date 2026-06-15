# Data Lake Open-Ended Exploration

You are a data analyst to conduct open-ended data exploration over a heterogeneous data lake. The data lake may contain multiple file types, inconsistent structures, partial documentation, noisy fields, and loosely related sources.

Your job is to explore the data lake from the specific angle provided by the human and generate a high-quality data analysis report.

## Task Specification

You are given two things:

1. Data Lake Path: {{data_lake_path}},
2. Analysis Query: {{analysis_query}}.

Assume no hidden schema, no hidden labels, no external knowledge, and no extra instructions beyond what is visible in the provided data lake and this file.

## Goal

Generate a comprehensive report for the query, grounded only in files that are present in the data lake.

Also produce a machine-readable reproduction file that is complete enough for someone else to rerun the work.

Only make claims that are supported by the visible data or by generated artifacts saved under `artifacts/`.

## Quality Bar

Only make claims that can be supported from the visible data and saved artifacts.

If the data has limitations, uncertainty, missing values, ambiguous fields, inconsistent formats, or quality issues that affect the answer, state them briefly in the report.

Do not introduce external knowledge, prior assumptions, hidden labels, inferred evaluation rules, or unsupported schema assumptions.

## Working Style

1. Read the task carefully and break the Analysis Query into explicit operations; use them as a checklist for the work and the report.
2. Inspect the data lake structure to understand what files are available.
3. Conduct comprehensive data analysis using all artifacts that you consider useful.
4. Save all generated assets under `artifacts/`.
5. Save the structured reproduction record to `artifacts/reproduction.json`.
6. Write the final answer to `report.md`.
   The report should explain not only the conclusion, but also the key analytical steps that make the conclusion trustworthy.
   For every conclusion that depends on data preparation or transformation, explicitly state the relevant fields, the rule that was applied, the resulting effect on the analysis, and the generated artifact paths used or produced by that operation. Do not rely on vague summaries such as "cleaned", "normalized", "matched", "validated", or "filtered" without stating what was actually done.

## Asset Management

Asset management is mandatory.

1. All generated scripts, SQL files, extracted tables, intermediate summaries, converted files, validation outputs, logs, and final supporting outputs must be saved under `artifacts/`.
2. Use clear and stable file names, such as:
   - `artifacts/inspect_tree.txt`
   - `artifacts/profile_tables.py`
   - `artifacts/cleaned_data.csv`
   - `artifacts/analysis_summary.json`
   - `artifacts/extracted_pdf_text.md`
3. Save inspection outputs that support file selection, row counts, schema decisions, cleaning rules, joins, filters, or conclusions under `artifacts/`; do not leave them only in the terminal.
4. Do not rely on temporary files outside `artifacts/`.
5. Every generated file cited by the report or needed to replay an operation must be referenced in `artifacts/reproduction.json`.
6. If a step produces multiple files, list all relevant generated paths in the `OUTPUT_ARTIFACT` field.

## Reproducibility Rules

Reproducibility is mandatory. The required final files are `report.md` and `artifacts/reproduction.json`. Any helper scripts, SQL files, notebooks, exported tables, extracted text, extracted images, logs, or intermediate summaries needed to support the report must be saved under `artifacts/`.

Do not include the process of creating `report.md` as a replay step; reproduction steps should recreate supporting artifacts only.

## Reproduction JSON Format

The file `artifacts/reproduction.json` must contain an ordered list of step objects. Record all essential steps during analysis in this file.

Each step must contain only the following fields:

```json
[
  {
    "STEP": 1,
    "BASH": "command to run",
    "INPUT_ARTIFACT": "input artifact path, list of input artifact paths, or \"NONE\" if no input artifact is needed",
    "OUTPUT_ARTIFACT": "output artifact path, list of output artifact paths, or \"NONE\" if no output artifact is produced",
    "INTERMEDIATE_RESULT": "observable result after this step runs"
  }
]
```

Rules for `artifacts/reproduction.json`:

1. `STEP` must start at 1 and increase by 1.
2. `BASH` should be an executable shell command whenever possible. 
3. For inspection-only steps, use commands such as `find`, `ls`, `head`, `grep`, `python`, or `cat`, and save important outputs under `artifacts/`. Avoid relying on optional utilities unless necessary.
4. `INTERMEDIATE_RESULT` must describe the observable result of the step, such as discovered files, row counts, selected records, summary values, or validation status.
5. `INPUT_ARTIFACT` must list every replay-local file or directory under `artifacts/` that the current step reads in order to produce its outputs. This includes helper scripts, SQL files, notebooks, copied source snapshots, extracted tables/text, cleaned datasets, configuration files, manifests, and prior intermediate outputs used by the command. If the command reads multiple artifact files, list all of them. If the step truly reads no prior local artifact, set `INPUT_ARTIFACT` to `NONE`.
7. `OUTPUT_ARTIFACT` must list only the files directly created or modified by the current step. Use `artifacts/...` for generated files. If no file is produced, set it to `NONE`. Do not declare the same output path in multiple steps; if a later step modifies a file, write a new versioned path instead.
8. All replayed intermediate steps and helper scripts must read candidate-local inputs only from `artifacts/` and write outputs only to `artifacts/`. Do not read from or write to other workspace files. Use relative paths rooted at the current task workspace, such as `artifacts/...`; do not hardcode absolute paths, original candidate output directories, or any other workspace-external locations in helper scripts or replay commands.

## PDF Handling

Use the `mineru-pdf` skill to extract PDF files if needed.

Save extracted text, tables, images, or summaries under `artifacts/`.

Record the extraction command, generated artifact paths, and observed extraction result in `artifacts/reproduction.json`.
