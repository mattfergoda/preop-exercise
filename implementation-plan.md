# Pre-Op Scheduling Triage Implementation Plan

## Goal

Implement the design in `design-doc.md` without changing the public entrypoint: `triage_submission(...)` in `core.py`.

The implementation must use the OpenAI Agents SDK on the normal path, run the document extraction agent on every case, and reserve deterministic fallback extraction for explicit offline mode or agent failure.

## Target Behavior

Normal behavior:

- `triage_submission(...)` validates the submission.
- Structured facts are extracted with deterministic Python code.
- The OpenAI Agents SDK document extraction agent runs on every case.
- If `OPENAI_API_KEY` is missing, the system fails fast with a clear error.
- Deterministic policy code produces the final `TriageOutput`.

Offline behavior:

- Enabled only when `TRIAGE_OFFLINE=1` is set.
- Skips the OpenAI agent.
- Uses deterministic regex document extraction.
- Preserves the same final policy and evidence generation path.

Failure fallback behavior:

- If the agent raises, times out, or returns invalid structured facts, log or annotate internally and fall back to regex document extraction.
- Do not return a partial or agent-authored final decision.
- Final output must still validate as `TriageOutput`.

## Dependency Changes

Update script dependencies to include the OpenAI Agents SDK.

Files to update:

- `run_baseline.py`
- `run_evals.py` only if determinism mode imports a path that needs the SDK at runtime
- `Makefile` test target

Expected dependency additions:

```python
# /// script
# dependencies = [
#   "openai>=2.0.0",
#   "openai-agents",
#   "pydantic>=2.8.0",
# ]
# ///
```

Update `make test` to install `openai-agents` with `uv run --with`.

Do not add `python-dotenv`. Use `uv run --env-file .env ...` for `.env` support.

## Proposed File Layout

Keep the repo flat and minimal.

Add these files:

- `facts.py`
- `structured_extraction.py`
- `document_extraction.py`
- `policy.py`

Update these files:

- `core.py`
- `tests/test_triage_submission.py`
- `README.md`
- `Makefile`

Do not create a full package directory unless implementation complexity later clearly requires it.

## Pydantic Fact Models

Create `facts.py` for intermediate models shared across extraction and policy.

Keep final public output models in `core.py` unless moving them becomes necessary.

Recommended models:

```python
from pydantic import BaseModel, Field

class SourceRef(BaseModel):
    source: str
    details: str

class DatedSource(BaseModel):
    source: str
    date: str | None = None
    raw_date: str | None = None
    details: str = ""

class LabFact(BaseModel):
    code: str
    source: str
    effective_at: str
    days_before_procedure: int | None = None

class VitalFact(BaseModel):
    type: str
    source: str
    date: str
    systolic: float | int | None = None
    diastolic: float | int | None = None
    value_f: float | int | None = None

class MedicationFact(BaseModel):
    name: str
    source: str
    active: bool | None = None
    is_anticoagulant: bool = False

class StructuredFacts(BaseModel):
    procedure_date: str | None = None
    procedure_risk: str | None = None
    latest_cbc: LabFact | None = None
    latest_cmp: LabFact | None = None
    latest_blood_pressure: VitalFact | None = None
    latest_temperature: VitalFact | None = None
    anticoagulants: list[MedicationFact] = Field(default_factory=list)
    unknown_anticoagulants: list[MedicationFact] = Field(default_factory=list)

class DocumentFinding(BaseModel):
    found: bool = False
    clear: bool = False
    source: str | None = None
    document_date: str | None = None
    excerpt: str | None = None
    reason: str = ""

class DocumentFacts(BaseModel):
    history_and_physical: DocumentFinding = Field(default_factory=DocumentFinding)
    signed_consent: DocumentFinding = Field(default_factory=DocumentFinding)
    anticoagulation_plan: DocumentFinding = Field(default_factory=DocumentFinding)
```

`DocumentFacts` is the `output_type` for the OpenAI agent.

## Structured Extraction

Create `structured_extraction.py`.

Public function:

```python
def extract_structured_facts(submission: PatientSubmission) -> StructuredFacts:
    ...
```

Implementation requirements:

- Parse dates with standard library `datetime`.
- Treat ISO datetimes and ISO dates as valid.
- Use calendar-day comparison against `procedure.procedure_date`.
- Preserve original date strings in details.
- Treat windows as inclusive: `<= 30` days and `<= 14` days.
- Only consider labs with parseable `effective_at` and a relevant CBC/CMP code or display.
- For CBC, accept `CBC`, `LAB-CBC`, and displays containing `Complete Blood Count` or `CBC`.
- For CMP, accept `CMP`, `LAB-CMP`, and displays containing `Comprehensive Metabolic Panel` or `CMP`.
- Pick the most recent valid lab for each required lab type before checking the time window.
- Pick the latest valid `blood_pressure` vital by date.
- Pick the latest valid `temperature` vital by date.
- Identify anticoagulants by a fixed lowercase set: `apixaban`, `warfarin`, `rivaroxaban`, `dabigatran`, `edoxaban`, `heparin`, `enoxaparin`.
- `active is True` creates an active anticoagulant fact.
- `active is None` on an anticoagulant creates an unknown anticoagulant fact.
- `active is False` does not require anticoagulation planning.

Do not infer missing `procedure_date` or `procedure_risk` from document text. The sample oracle treats missing structured fields as `MISSING_REQUIRED_DATA` even when document text references a target date.

## Document Extraction Agent

Create `document_extraction.py`.

Public functions:

```python
def extract_document_facts(
    submission: PatientSubmission,
    *,
    model: str,
    offline: bool,
) -> DocumentFacts:
    ...

def extract_document_facts_with_regex(submission: PatientSubmission) -> DocumentFacts:
    ...
```

Normal path implementation:

```python
from agents import Agent, ModelSettings, Runner

agent = Agent(
    name="Cadence Pre-Op Document Extraction Agent",
    instructions=DOCUMENT_EXTRACTION_INSTRUCTIONS,
    model=model,
    model_settings=ModelSettings(temperature=0),
    output_type=DocumentFacts,
)
result = Runner.run_sync(agent, input_payload)
facts = DocumentFacts.model_validate(result.final_output)
```

If `ModelSettings(temperature=0)` is not supported by the installed SDK/model combination, omit model settings rather than blocking implementation.

Fail-fast behavior:

- If `offline` is false and `OPENAI_API_KEY` is missing, raise `RuntimeError` with a message that says to set `OPENAI_API_KEY` or use `TRIAGE_OFFLINE=1`.
- If `offline` is true, do not import or call the Agents SDK.
- If the agent call fails after key validation, catch the exception and use regex fallback.

Input payload to the agent:

- Include `procedure.procedure_date` and `procedure.procedure_risk` for context only.
- Include each document with `index`, `type`, `date`, `author`, and `text`.
- Include active anticoagulant medication names and source indexes.
- Do not send unnecessary patient identifiers if avoidable.

Agent instruction requirements:

- Use only the Cadence policy concepts supplied in the prompt.
- Extract document facts only.
- Do not decide `READY`, `NEEDS_FOLLOW_UP`, or `NOT_CLEARED`.
- Do not emit issue categories.
- Return exact `documents[i]` source paths.
- Return exact excerpts from document text when possible.
- For H&P, identify the best clearly valid H&P document and include its date.
- For consent, `clear=True` only when the document clearly says signed, signature on file, signed consent scanned, electronically signed, or equivalent.
- For consent, `clear=False` when text says unsigned, awaiting signature, pending, or only counseling without a signature.
- For anticoagulation plan, `clear=True` only when the document clearly describes before-procedure and after-procedure medication management.
- For anticoagulation plan, `clear=False` when text says pending, follow up, final plan pending, no hold/resume guidance, not yet documented, or ambiguous.

Calibration requirement:

- Add a regression test or eval inspection checkpoint for `case_00002`.
- In that case, `documents[0].type` is `History & Phsyical` and its text looks H&P-like, but the oracle expects H&P outside the window from `documents[1]`.
- The implementation should not blindly accept every text mention of `H and P` when the document type is malformed or ambiguous.
- Prefer authoritative document type signals and use text as supporting evidence, then validate against sample eval failures.

## Regex Document Fallback

Implement regex fallback in `document_extraction.py`.

Fallback requirements:

- It is not the primary production path.
- It should be deterministic and conservative.
- It should return the same `DocumentFacts` shape as the agent.

H&P fallback guidance:

- Accept strong type patterns containing `H&P`, `H and P`, `History and Physical`, `History & Physical`, `Hist & Phys`, `H/P`, or `H+P`.
- Accept generic clearance/evaluation types only when text strongly confirms history and physical exam completion.
- Be conservative with malformed type strings.

Consent fallback guidance:

- First reject unsigned/pending text with patterns such as `unsigned`, `awaiting signature`, `pending signature`, `not signed`.
- Then accept signed text with patterns such as `signed`, `signature on file`, `signed consent scanned`, `electronically signed`, `consent obtained and signed`.
- Require either consent-like type or consent-like text.

Anticoagulation fallback guidance:

- First reject incomplete language such as `pending`, `follow up`, `not yet documented`, `to be finalized`, `no clear hold/resume`, `no clear guidance`.
- Accept clear plans only when text includes an anticoagulant reference plus both pre-op and post-op management language, such as hold/stop before and resume/restart after.
- Do not treat a document title alone as a clear plan.

## Policy Evaluation

Create `policy.py`.

Public function:

```python
def evaluate_policy(
    submission: PatientSubmission,
    structured: StructuredFacts,
    documents: DocumentFacts,
) -> TriageOutput:
    ...
```

Policy implementation requirements:

- Collect issues first.
- Decide final status after all checks.
- Emit all applicable issues.
- Use stable issue order.
- Use `TriageIssue` and `TriageIssueEvidence` from `core.py`.

Recommended stable issue order:

```text
1. Missing procedure date
2. Missing procedure risk
3. Required documentation issues
4. Required testing issues
5. Unknown anticoagulant active status
6. Anticoagulation management issues
7. Missing latest blood pressure
8. Missing latest temperature
9. Acute safety exclusions
```

Decision precedence:

```python
if any(issue.category == "ACUTE_SAFETY_EXCLUSION" for issue in issues):
    decision = "NOT_CLEARED"
elif issues:
    decision = "NEEDS_FOLLOW_UP"
else:
    decision = "READY"
```

Explanation formatting:

```text
All required documentation, testing, anticoagulation planning, and safety checks are satisfied.
```

Use the above exact explanation for ready cases.

For issue cases, use:

```text
CATEGORY: Description | CATEGORY: Description
```

## Evidence Details

Evidence must be concrete enough for `run_evals.py` grounding checks.

Missing structured field examples:

```json
{"source": "procedure.procedure_date", "details": "procedure.procedure_date is null"}
```

Stale H&P example:

```text
H&P date 2026-01-30 vs procedure_date 2026-03-03 (32 days prior; must be within 30)
```

Unsigned consent example:

```text
Consent document text does not clearly indicate signed consent: Consent documented but unsigned; awaiting patient signature.
```

Missing CBC example:

```text
No CBC result with valid effective_at found for procedure_risk LOW
```

Stale CBC example:

```text
CBC effective_at 2026-02-19T09:10:00Z vs procedure_date 2026-03-11 (20 days prior; must be within 14)
```

Anticoagulation example:

```text
Active anticoagulant medication present (medications[1]) but no clear perioperative plan document found
```

Acute BP example:

```text
latest BP systolic=184, diastolic=111; threshold systolic>=180 or diastolic>=110
```

Acute temperature example:

```text
latest temperature value_f=101.0; threshold is > 100.4
```

## Updating `core.py`

Keep existing public schemas in `core.py`.

Replace the current naive `triage_submission(...)` implementation with orchestration code:

```python
def triage_submission(
    submission: dict[str, object] | PatientSubmission,
    *,
    model: str,
) -> TriageOutput:
    if isinstance(submission, PatientSubmission):
        patient_submission = submission
    else:
        patient_submission = PatientSubmission.model_validate(submission)

    offline = os.getenv("TRIAGE_OFFLINE") == "1"
    structured = extract_structured_facts(patient_submission)
    documents = extract_document_facts(patient_submission, model=model, offline=offline)
    return evaluate_policy(patient_submission, structured, documents)
```

Keep `build_user_prompt(...)`, `triage_output_json_schema(...)`, and `BASELINE_SYSTEM_PROMPT` only if tests or scripts still use them. If unused after refactor, remove only after tests are updated.

Lazy-import the Agents SDK inside `document_extraction.py` so offline mode and pure unit tests do not require importing agent runtime unless needed.

## Updating `run_baseline.py`

Update inline dependencies to include `openai-agents`.

Do not catch and hide missing API key as a generic model error if possible. The current row-level exception capture can remain, but the error string should be clear enough to tell the user to set `OPENAI_API_KEY` or `TRIAGE_OFFLINE=1`.

No CLI change is required for model selection because `--model` already exists.

## Updating `run_evals.py`

Normal eval mode requires `OPENAI_API_KEY` because it creates OpenAI eval runs.

Determinism mode also calls `triage_submission(...)`, so it requires the key unless `TRIAGE_OFFLINE=1` is explicitly set.

No local-only eval flag is required for this implementation pass.

Update inline dependencies if importing the refactored core path requires `openai-agents` in determinism mode.

## Updating `Makefile`

Update `test` dependencies to include `openai-agents`.

Add optional helper targets:

```make
baseline-env:
	uv run --env-file .env run_baseline.py \
		--input $(INPUT) \
		--output $(OUTPUT) \
		--model $(MODEL)

baseline-offline:
	TRIAGE_OFFLINE=1 uv run run_baseline.py \
		--input $(INPUT) \
		--output $(OUTPUT) \
		--model $(MODEL)
```

Do not change the existing `baseline`, `evals`, `determinism`, or `all` targets unless needed.

## Updating `README.md`

Add setup notes:

```bash
export OPENAI_API_KEY="<your_api_key>"
```

Add `.env` option:

```bash
uv run --env-file .env run_baseline.py
```

Add offline mode explanation:

```bash
TRIAGE_OFFLINE=1 make baseline
```

Clarify that offline mode uses deterministic fallback extraction and is intended for tests/local debugging, not the primary agentic submission path.

## Testing Plan

Replace or rewrite `tests/test_triage_submission.py` so tests no longer assert the old single Responses API call shape.

Test groups:

- `structured_extraction` unit tests
- `document_extraction` regex fallback tests
- `policy` unit tests
- `triage_submission` orchestration tests

Required test cases:

- ready case returns `READY` and no issues
- missing `procedure.procedure_date` produces `MISSING_REQUIRED_DATA`
- missing `procedure.procedure_risk` produces `MISSING_REQUIRED_DATA`
- stale H&P produces `REQUIRED_DOCUMENTATION`
- missing consent produces `REQUIRED_DOCUMENTATION`
- unsigned consent produces `REQUIRED_DOCUMENTATION`
- low/moderate CBC missing produces `REQUIRED_TESTING`
- high-risk CMP missing produces `REQUIRED_TESTING`
- stale high-risk CBC/CMP produces `REQUIRED_TESTING`
- latest BP with systolic >= 180 or diastolic >= 110 produces `NOT_CLEARED`
- latest temperature > 100.4 produces `NOT_CLEARED`
- active anticoagulant without clear plan produces `ANTICOAGULATION_MANAGEMENT`
- anticoagulant with `active=None` produces `MISSING_REQUIRED_DATA`
- missing vitals produce missing latest BP and missing latest temperature issues
- missing `OPENAI_API_KEY` fails fast when `TRIAGE_OFFLINE` is not set
- `TRIAGE_OFFLINE=1` does not require `OPENAI_API_KEY`
- invalid agent output falls back to regex extraction

Sample regression tests:

- Use representative sample rows from `data/patients_sample_50.jsonl` for `case_00000`, `case_00002`, `case_00003`, `case_00004`, `case_00005`, `case_00008`, `case_00012`, and `case_00014`.
- Assert decision and issue categories match each row's `expected_output`.
- Do not assert exact full prose unless needed for evidence grounding stability.

Mocking strategy:

- Unit tests should not call the real OpenAI API.
- Monkeypatch `document_extraction.extract_document_facts` or the internal agent runner to return `DocumentFacts`.
- Test the fail-fast API-key behavior separately.
- Test regex fallback directly with `TRIAGE_OFFLINE=1`.

## Verification Commands

Run unit tests:

```bash
make test
```

Run agentic baseline with exported key:

```bash
make baseline
```

Run agentic baseline with `.env`:

```bash
uv run --env-file .env run_baseline.py --input data/patients_sample_50.jsonl --output data/baseline_outputs.jsonl --model gpt-4.1-mini
```

Run eval scoring:

```bash
make evals
```

Run determinism:

```bash
make determinism
```

Print score:

```bash
make score
```

Run offline baseline for debugging only:

```bash
TRIAGE_OFFLINE=1 make baseline
```

## Implementation Sequence

Recommended sequence for the coding agent:

1. Add `facts.py` with intermediate Pydantic models.
2. Add `structured_extraction.py` and unit tests.
3. Add conservative regex fallback in `document_extraction.py` and unit tests.
4. Add OpenAI Agents SDK extraction path in `document_extraction.py` with lazy import and fail-fast key validation.
5. Add `policy.py` with deterministic issue generation and unit tests.
6. Replace `core.py` `triage_submission(...)` with orchestration.
7. Update tests to mock the agent path and validate orchestration.
8. Update script dependencies and `Makefile`.
9. Update `README.md` with agentic, `.env`, and offline instructions.
10. Run `make test`.
11. Run `make baseline` with API key.
12. Run `make evals`, `make score`, and inspect failures.
13. Tune document extraction instructions and evidence formatting based on eval failures.
14. Run `make determinism`.

## Acceptance Criteria

The implementation is complete when:

- `triage_submission(...)` returns `TriageOutput` without making the old single final-answer LLM call.
- The OpenAI Agents SDK extraction path runs by default on every case.
- Missing `OPENAI_API_KEY` fails fast unless `TRIAGE_OFFLINE=1` is set.
- Offline mode produces valid outputs through regex fallback.
- Unit tests pass.
- Baseline generation completes with an API key.
- Eval scoring completes with an API key.
- Determinism check passes at the decision/schema level and preferably exact-output level.
- Generated issues include grounded source paths and concrete values or excerpts.
