# Pre-Op Triage Take-Home

## Objective

`triage_submission(...)` in `core.py` evaluates a single pre-op submission package. The implementation uses the OpenAI Agents SDK for document fact extraction on the normal path, deterministic Python extraction for structured data, and deterministic policy code for the final decision.

Your output must match this schema:

- `decision`: `READY | NEEDS_FOLLOW_UP | NOT_CLEARED`
- `issues[]`: category + evidence (`source`, `details`)
- `explanation`

## What Is Provided

- `data/patients_sample_50.jsonl` includes:
  - `case_id`
  - `submission`
  - `expected_output`
- `run_baseline.py` runs your `triage_submission` implementation and writes outputs.
- `run_evals.py` scores outputs against provided `expected_output` and can run determinism checks.

## Completion

Note: this exercise is evaluated on engineering judgment. You may not reach a 100% score, and that is OK! We are looking to understand how you approached the problem and designed a working solution.

## Setup

1. Confirm `uv` is installed.

```bash
uv --version
```

2. Set your OpenAI API key.

```bash
export OPENAI_API_KEY="<your_api_key>"
```

You can also keep the key in `.env` and run commands through `uv`'s built-in env-file support:

```bash
uv run --env-file .env run_baseline.py
```

Offline mode is available for tests and local debugging only. It skips the OpenAI Agents SDK document extraction path and uses deterministic regex fallback extraction:

```bash
TRIAGE_OFFLINE=1 make baseline
```

## Recommended Workflow

1. Implement `triage_submission` in `core.py`.
2. Run baseline outputs:

```bash
make baseline
```

To load environment variables from `.env`, use:

```bash
make baseline-env
```

To run the deterministic offline fallback baseline, use:

```bash
make baseline-offline
```

3. Run eval scoring:

```bash
make evals
```

4. Run determinism check:

```bash
make determinism
```

5. Print score:

```bash
make score
```

6. View the interactive report (TUI):

```bash
make report
```

This opens a terminal UI (`view_report.py`) that shows per-case results side-by-side with oracle expectations. You can browse records, see metric pass/fail status, and inspect submission data. Press `f` on a metric row to filter the case list to failures. Press `q` to quit.

## Tests

Run the unit tests with:

```bash
make test
```

## Outputs

- Baseline outputs: `data/baseline_outputs.jsonl`
- Eval report: `data/eval_report.json`
- Determinism report: `data/determinism_report.json`

## Configurable Variables

- `MODEL` (default `gpt-4.1-mini`)
- `INPUT` (default `data/patients_sample_50.jsonl`)
- `OUTPUT` (default `data/baseline_outputs.jsonl`)
- `REPORT` (default `data/eval_report.json`)
- `DETERMINISM_REPORT` (default `data/determinism_report.json`)
